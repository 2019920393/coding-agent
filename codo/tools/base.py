"""
工具系统基类定义

[Workflow]
本模块定义了工具系统的核心基类：
1. Tool - 工具抽象基类，定义所有工具必须实现的接口
2. build_tool - 工具构建装饰器，用于创建具体工具实例

工具定义模式：
```python
@build_tool(
    name="Bash",
    max_result_size_chars=30000,
    input_schema=BashInput,
    output_schema=BashOutput,
)
class BashTool(Tool[BashInput, BashOutput, BashProgress]):
    async def call(self, args, context, can_use_tool, parent_message, on_progress):
        return ToolResult(data=output)

    async def description(self, input, options):
        return "执行 shell 命令"

    async def prompt(self, options):
        return "系统提示..."

    def map_tool_result_to_tool_result_block_param(self, content, tool_use_id):
        return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
```
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, Generic, TypeVar

from anthropic.types import ToolResultBlockParam
from pydantic import BaseModel

from codo.types.permissions import PermissionResult, create_passthrough_result

from .receipts import ToolReceipt
from .types import InputT, OutputT, ProgressT, ToolCallProgress, ToolResult, ValidationResult


class ToolUseContext(dict[str, Any]):
    def __init__(
        self,
        *,
        options: dict[str, Any] | None = None,
        abort_controller: Any = None,
        messages: list[dict[str, Any]] | None = None,
        **values: Any,
    ) -> None:
        super().__init__(values)
        self["options"] = options if options is not None else {}
        self["abort_controller"] = abort_controller
        self["messages"] = messages if messages is not None else []
        self._source: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, context: dict[str, Any]) -> "ToolUseContext":
        wrapped = cls(
            options=context.get("options") if isinstance(context.get("options"), dict) else {},
            abort_controller=context.get("abort_controller"),
            messages=context.get("messages") if isinstance(context.get("messages"), list) else [],
            **{
                key: value
                for key, value in context.items()
                if key not in {"options", "abort_controller", "messages"}
            },
        )
        wrapped._source = context
        return wrapped

    def sync_to_source(self) -> None:
        if self._source is not None:
            self._source.clear()
            self._source.update(self)

    def get_options(self) -> dict[str, Any]:
        return self["options"]

    @property
    def options(self) -> dict[str, Any]:
        return self["options"]

    @options.setter
    def options(self, value: dict[str, Any]) -> None:
        self["options"] = value

    @property
    def abort_controller(self) -> Any:
        return self.get("abort_controller")

    @abort_controller.setter
    def abort_controller(self, value: Any) -> None:
        self["abort_controller"] = value

    @property
    def messages(self) -> list[dict[str, Any]]:
        return self["messages"]

    @messages.setter
    def messages(self, value: list[dict[str, Any]]) -> None:
        self["messages"] = value

# ============================================================================
# ============================================================================
# 工具基类
# ============================================================================

class Tool(ABC, Generic[InputT, OutputT, ProgressT]):
    """
    工具抽象基类

    所有工具必须继承此类并实现必需的抽象方法。

    泛型参数：
        InputT: 输入类型（Pydantic BaseModel）
        OutputT: 输出类型
        ProgressT: 进度类型（Pydantic BaseModel）

    必需属性（由 build_tool 装饰器设置）：
        name: 工具名称
        max_result_size_chars: 结果最大字符数（超过则持久化到磁盘）

    可选属性：
        aliases: 工具别名列表（用于向后兼容）
        search_hint: 搜索提示（用于工具搜索功能）
        strict: 是否启用严格模式
        should_defer: 是否延迟加载
        always_load: 是否总是加载（即使启用了工具搜索）
        is_mcp: 是否为 MCP 工具
        mcp_info: MCP 工具信息（server_name, tool_name）
    """

    # 必需属性（由装饰器设置）
    name: str
    max_result_size_chars: int

    # 可选属性
    aliases: list[str] | None = None
    search_hint: str | None = None
    strict: bool = False
    should_defer: bool = False
    always_load: bool = False
    is_mcp: bool = False
    mcp_info: dict[str, str] | None = None

    # ========================================================================
    # Schema 定义（必需实现）
    # ========================================================================

    @property
    @abstractmethod
    def input_schema(self) -> type[InputT]: # 代表类本身
        """
        输入 schema（Pydantic 模型类）

        Returns:
            Pydantic BaseModel 类
        """
        pass

    @property
    def output_schema(self) -> type[OutputT] | None:
        """
        输出 schema（可选）

        Returns:
            输出类型或 None
        """
        return None

    # ========================================================================
    # 核心方法（必需实现）
    # ========================================================================

    @abstractmethod
    async def call(
        self,
        args: InputT,
        context: dict[str, Any],
        can_use_tool: Callable,
        parent_message: Any,
        on_progress: ToolCallProgress | None = None,
    ) -> ToolResult[OutputT]:
        """
        执行工具

        Args:
            args: 工具输入参数（已验证）
            context: 工具使用上下文
            can_use_tool: 权限检查回调函数
            parent_message: 父消息（AssistantMessage）
            on_progress: 进度回调函数（可选）

        Returns:
            工具执行结果
        """
        pass

    @abstractmethod
    async def description(
        self,
        input: InputT,
        options: dict[str, Any],
    ) -> str:
        """
        工具描述

        Args:
            input: 工具输入参数
            options: 配置选项

        Returns:
            工具描述字符串
        """
        pass

    @abstractmethod
    async def prompt(self, options: dict[str, Any]) -> str:
        """
        系统提示

        Args:
            options: 配置选项（包含 tools, agents 等）

        Returns:
            系统提示字符串
        """
        pass

    @abstractmethod
    def map_tool_result_to_tool_result_block_param(
        self,
        content: OutputT,
        tool_use_id: str,
    ) -> ToolResultBlockParam:
        """
        将工具结果转换为 API 格式

        Args:
            content: 工具输出内容
            tool_use_id: 工具使用 ID

        Returns:
            ToolResultBlockParam
        """
        pass

    # ========================================================================
    # 默认实现的方法（可覆盖）
    # ========================================================================

    def is_enabled(self) -> bool:
        """
        工具是否启用

        Returns:
            True 表示启用，False 表示禁用
        """
        return True

    def is_concurrency_safe(self, input: InputT) -> bool:
        """
        是否并发安全

        默认返回 False（fail-closed），假设工具不是并发安全的。
        只读工具应该覆盖此方法返回 True。

        Args:
            input: 工具输入参数

        Returns:
            True 表示可以并发执行，False 表示必须串行执行
        """
        return False

    def is_read_only(self, input: InputT) -> bool:
        """
        是否只读操作

        默认返回 False（fail-closed），假设工具有写操作。
        只读工具应该覆盖此方法返回 True。

        Args:
            input: 工具输入参数

        Returns:
            True 表示只读，False 表示有写操作
        """
        return False

    def is_destructive(self, input: InputT) -> bool:
        """
        是否破坏性操作

        默认返回 False。破坏性操作（删除、覆盖、发送）应该覆盖此方法。

        Args:
            input: 工具输入参数

        Returns:
            True 表示破坏性操作，False 表示非破坏性
        """
        return False

    def requires_permission(self, input: InputT) -> bool:
        """
        是否需要权限检查

        默认返回 True（fail-closed），假设工具需要权限检查。
        完全安全的工具可以覆盖此方法返回 False。

        Args:
            input: 工具输入参数

        Returns:
            True 表示需要权限检查，False 表示不需要
        """
        return True

    async def check_permissions(
        self,
        input: InputT,
        context: dict[str, Any],
    ) -> PermissionResult:
        """
        权限检查

        默认返回 ALLOW，交给通用权限系统处理。
        工具特定的权限逻辑应该覆盖此方法。

        Args:
            input: 工具输入参数
            context: 工具使用上下文

        Returns:
            权限检查结果
        """
        return create_passthrough_result()

    async def validate_input(
        self,
        input: InputT,
        context: dict[str, Any],
    ) -> ValidationResult:
        """
        输入验证

        默认返回通过。工具特定的验证逻辑应该覆盖此方法。

        Args:
            input: 工具输入参数
            context: 工具使用上下文

        Returns:
            验证结果
        """
        return ValidationResult(result=True)

    def to_auto_classifier_input(self, input: InputT) -> Any:
        """
        转换为自动分类器输入

        默认返回空字符串（跳过分类器）。
        安全相关的工具应该覆盖此方法返回紧凑的表示。

        Args:
            input: 工具输入参数

        Returns:
            分类器输入（字符串或对象）
        """
        return ""

    def user_facing_name(self, input: InputT | None = None) -> str:
        """
        用户可见名称

        默认返回工具名称。工具可以覆盖此方法提供更友好的名称。

        Args:
            input: 工具输入参数（可选）

        Returns:
            用户可见的工具名称
        """
        return self.name

    def get_tool_use_summary(self, input: InputT | None = None) -> str | None:
        """
        工具使用摘要

        用于在紧凑视图中显示工具使用的简短摘要。

        Args:
            input: 工具输入参数（可选）

        Returns:
            摘要字符串，或 None 表示不显示
        """
        return None

    def get_activity_description(self, input: InputT | None = None) -> str | None:
        """
        活动描述

        用于在 spinner 中显示当前活动的描述。
        例如："Reading src/foo.ts", "Running bun test"

        Args:
            input: 工具输入参数（可选）

        Returns:
            活动描述字符串，或 None 表示使用默认描述
        """
        return None

    def get_path(self, input: InputT) -> str | None:
        """
        获取文件路径

        用于文件操作工具返回操作的文件路径。

        Args:
            input: 工具输入参数

        Returns:
            文件路径，或 None 表示不是文件操作
        """
        return None

    def get_context_modifier(
        self,
        input: InputT,
        result: ToolResult[OutputT],
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        """
        获取上下文修改器

        工具可以返回上下文修改器来改变后续工具的执行环境。
        例如，cd 命令可以返回新的工作目录。

        Args:
            input: 工具输入参数
            result: 工具执行结果
            context: 当前执行上下文

        Returns:
            上下文修改器字典，或 None 表示不修改上下文
        """
        return None

    def build_default_receipt(
        self,
        result: ToolResult[OutputT],
        input: InputT | None = None,
    ) -> ToolReceipt | None:
        """
        返回工具默认的结构化收据。

        当前主链路优先尊重工具显式返回的 result.receipt。
        后续若需要按工具类型自动派生 receipt，可在子类覆盖这里。
        """
        return result.receipt

    async def prepare_permission_matcher(
        self,
        input: InputT,
    ) -> Callable[[str], bool]:
        """
        准备权限匹配器

        用于钩子 if 条件的模式匹配。
        默认返回总是返回 False 的匹配器（不匹配任何模式）。

        Args:
            input: 工具输入参数

        Returns:
            匹配器函数（接受模式字符串，返回是否匹配）
        """
        return lambda pattern: False

    async def execute(
        self,
        input: InputT,
        context: dict[str, Any],
    ) -> ToolResult[OutputT]:
        """
        执行工具（简化版本，用于编排系统）

        这是 call() 方法的简化包装器，用于编排系统调用。
        编排系统不需要完整的 call() 参数（can_use_tool, parent_message, on_progress）。

        Args:
            input: 工具输入参数（字典或 Pydantic 模型）
            context: 执行上下文字典

        Returns:
            工具执行结果
        """
        if isinstance(input, dict):
            input = self.input_schema(**input)

        tool_context = context if isinstance(context, ToolUseContext) else ToolUseContext.from_dict(context)
        try:
            return await self.call(
                input,
                tool_context,
                lambda: True,
                None,
                None,
            )
        finally:
            tool_context.sync_to_source()

# ============================================================================
# 工具集合类型
# ============================================================================

# 工具列表类型
Tools = list[Tool]

# ============================================================================
# 工具构建装饰器
# ============================================================================

T = TypeVar('T', bound=Tool)

def build_tool(
    name: str,
    max_result_size_chars: int,
    input_schema: type[BaseModel],
    output_schema: type | None = None,
    *,
    aliases: list[str] | None = None,
    search_hint: str | None = None,
    strict: bool = False,
    # 可选覆盖的方法
    is_enabled: Callable[[], bool] | None = None,
    is_concurrency_safe: Callable[[Any], bool] | None = None,
    is_read_only: Callable[[Any], bool] | None = None,
    is_destructive: Callable[[Any], bool] | None = None,
    to_auto_classifier_input: Callable[[Any], Any] | None = None,
    user_facing_name: Callable[[Any | None], str] | None = None,
) -> Callable[[type[T]], type[T]]:
    """
    工具构建装饰器

    用于创建工具类，自动设置元数据和默认方法。

    Args:
        name: 工具名称
        max_result_size_chars: 结果最大字符数
        input_schema: 输入 schema（Pydantic 模型类）
        output_schema: 输出 schema（可选）
        aliases: 工具别名列表（可选）
        search_hint: 搜索提示（可选）
        strict: 是否启用严格模式（可选）
        is_enabled: 覆盖 is_enabled 方法（可选）
        is_concurrency_safe: 覆盖 is_concurrency_safe 方法（可选）
        is_read_only: 覆盖 is_read_only 方法（可选）
        is_destructive: 覆盖 is_destructive 方法（可选）
        to_auto_classifier_input: 覆盖 to_auto_classifier_input 方法（可选）
        user_facing_name: 覆盖 user_facing_name 方法（可选）

    Returns:
        装饰器函数

    Example:
        ```python
        @build_tool(
            name="Bash",
            max_result_size_chars=30000,
            input_schema=BashInput,
            output_schema=BashOutput,
        )
        class BashTool(Tool[BashInput, BashOutput, BashProgress]):
            async def call(self, args, context, can_use_tool, parent_message, on_progress):
                return ToolResult(data=output)

            async def description(self, input, options):
                return "执行 shell 命令"

            async def prompt(self, options):
                return "系统提示..."

            def map_tool_result_to_tool_result_block_param(self, content, tool_use_id):
                return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
        ```
    """
    def decorator(cls: type[T]) -> type[T]:
        """
        实际的类装饰器，将元数据注入工具类并创建带 schema 属性的子类。

        [Workflow]
        1. 将 name、max_result_size_chars、aliases 等元数据设置为类属性
        2. 创建 ToolWithSchema 子类，通过 @property 实现 input_schema/output_schema 抽象属性
        3. 复制类名和模块信息，保持调试信息正确
        4. 若提供了可选覆盖函数（is_enabled 等），用 lambda 包装后注入子类

        参数:
            cls: 被装饰的工具类（继承自 Tool）

        返回:
            ToolWithSchema: 注入了 schema 属性和元数据的新子类
        """
        # 设置类属性
        cls.name = name
        cls.max_result_size_chars = max_result_size_chars
        cls.aliases = aliases
        cls.search_hint = search_hint
        cls.strict = strict

        # 捕获外部变量到局部作用域
        _input_schema = input_schema
        _output_schema = output_schema

        # 创建一个新的类，覆盖 input_schema 和 output_schema 属性
        # 这样可以正确实现抽象属性
        class ToolWithSchema(cls):  # type: ignore
            """build_tool 动态生成的工具子类，用于注入输入和输出 schema。"""

            @property
            def input_schema(self) -> type[BaseModel]:
                """返回工具的输入 schema 类（由 build_tool 装饰器注入）。"""
                return _input_schema

            if _output_schema is not None:
                @property
                def output_schema(self) -> type | None:
                    """返回工具的输出 schema 类（由 build_tool 装饰器注入，可选）。"""
                    return _output_schema

        # 复制类名和模块信息
        ToolWithSchema.__name__ = cls.__name__
        ToolWithSchema.__qualname__ = cls.__qualname__
        ToolWithSchema.__module__ = cls.__module__

        # 覆盖可选方法（如果提供）
        if is_enabled is not None:
            ToolWithSchema.is_enabled = lambda self: is_enabled()

        if is_concurrency_safe is not None:
            ToolWithSchema.is_concurrency_safe = lambda self, input: is_concurrency_safe(input)

        if is_read_only is not None:
            ToolWithSchema.is_read_only = lambda self, input: is_read_only(input)

        if is_destructive is not None:
            ToolWithSchema.is_destructive = lambda self, input: is_destructive(input)

        if to_auto_classifier_input is not None:
            ToolWithSchema.to_auto_classifier_input = lambda self, input: to_auto_classifier_input(input)

        if user_facing_name is not None:
            ToolWithSchema.user_facing_name = lambda self, input=None: user_facing_name(input)

        return ToolWithSchema  # type: ignore

    return decorator
