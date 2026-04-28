"""
工具系统基类定义

[Workflow]
本模块定义了工具系统的核心基类和上下文：
1. ToolUseContext - 工具使用上下文，包含执行环境信息
2. Tool - 工具抽象基类，定义所有工具必须实现的接口
3. build_tool - 工具构建装饰器，用于创建具体工具实例

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

from typing import Generic, Optional, Callable, Any, Dict, TypeVar
from abc import ABC, abstractmethod
from pydantic import BaseModel
from anthropic.types import ToolResultBlockParam

from .types import (
    InputT, OutputT, ProgressT,
    ToolResult, ValidationResult,
    ToolCallProgress
)
from .receipts import ToolReceipt
from codo.types.permissions import PermissionResult, create_passthrough_result

# ============================================================================
# 工具使用上下文
# ============================================================================

class ToolUseContext:
    """
    工具使用上下文

    同时支持对象属性访问（.options）和字典访问（.get("options")），
    消除工具实现里的契约漂移问题。

    Attributes:
        options: 配置选项字典（包含 tools, commands, debug 等）
        abort_controller: 中止控制器（用于取消长时间运行的工具）
        messages: 当前对话消息列表
    """

    def __init__(
        self,
        options: Dict[str, Any] = None,
        abort_controller: Any = None,
        messages: list = None,
        # 支持从字典直接构造（兼容 execution_context 字典格式）
        _dict: Dict[str, Any] = None,
    ):
        object.__setattr__(self, "_data", _dict if _dict is not None else {})

        if _dict is None:
            self._data.update(
                {
                    "options": options or {},
                    "abort_controller": abort_controller,
                    "messages": messages or [],
                }
            )

        self.options = self._data.get("options", options or {})
        self.abort_controller = self._data.get("abort_controller", abort_controller)
        self.messages = self._data.get("messages", messages or [])

    def __setattr__(self, key: str, value: Any) -> None:
        object.__setattr__(self, key, value)
        if key.startswith("_"):
            return
        data = self.__dict__.get("_data")
        if isinstance(data, dict):
            data[key] = value

    def __getattr__(self, key: str) -> Any:
        data = self.__dict__.get("_data", {})
        if key in data:
            return data[key]
        raise AttributeError(key)

    # ---- 字典协议：让工具可以用 context.get("key") 访问 ----

    def get(self, key: str, default: Any = None) -> Any:
        """支持 context.get("cwd") 等字典式访问"""
        # 先查 _data，再查对象属性
        if key in self._data:
            return self._data[key]
        return getattr(self, key, default)

    def __getitem__(self, key: str) -> Any:
        """支持 context["cwd"] 访问"""
        if key in self._data:
            return self._data[key]
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    def __contains__(self, key: str) -> bool:
        """支持 "key" in context"""
        return key in self._data or hasattr(self, key)

    def __setitem__(self, key: str, value: Any) -> None:
        """支持 context["key"] = value"""
        self._data[key] = value
        if hasattr(self, key):
            setattr(self, key, value)

    def setdefault(self, key: str, default: Any = None) -> Any:
        """支持 context.setdefault("key", value)"""
        if key not in self._data:
            self[key] = default
        return self._data[key]

    def items(self):
        return self._data.items()

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def to_dict(self) -> Dict[str, Any]:
        """返回底层上下文字典本身，便于需要原始字典的链路复用。"""
        return self._data

    def get_options(self) -> Dict[str, Any]:
        """返回可写的 options 字典，缺失时自动初始化并回写到底层上下文。"""
        options = self.get("options", {})
        if not isinstance(options, dict):
            options = {}
            self["options"] = options
        return options

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ToolUseContext":
        """从字典创建 ToolUseContext（工厂方法）"""
        return cls(_dict=d)

    @classmethod
    def coerce(cls, context: Any) -> "ToolUseContext":
        """
        统一把工具执行链里收到的上下文转换成 ToolUseContext。

        规则：
        - 已经是 ToolUseContext：直接复用
        - 是 dict：原位包装，保持对原始 execution_context 的写回能力
        - 是 None：返回空上下文
        """
        if isinstance(context, cls):
            return context
        if context is None:
            return cls()
        if isinstance(context, dict):
            return cls.from_dict(context)
        raise TypeError(f"Unsupported tool context type: {type(context)!r}")

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
    aliases: Optional[list[str]] = None
    search_hint: Optional[str] = None
    strict: bool = False
    should_defer: bool = False
    always_load: bool = False
    is_mcp: bool = False
    mcp_info: Optional[Dict[str, str]] = None

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
    def output_schema(self) -> Optional[type[OutputT]]:
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
        context: ToolUseContext,
        can_use_tool: Callable,
        parent_message: Any,
        on_progress: Optional[ToolCallProgress] = None,
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
        options: Dict[str, Any],
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
    async def prompt(self, options: Dict[str, Any]) -> str:
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
        context: ToolUseContext,
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
        context: ToolUseContext,
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

    def user_facing_name(self, input: Optional[InputT] = None) -> str:
        """
        用户可见名称

        默认返回工具名称。工具可以覆盖此方法提供更友好的名称。

        Args:
            input: 工具输入参数（可选）

        Returns:
            用户可见的工具名称
        """
        return self.name

    def get_tool_use_summary(self, input: Optional[InputT] = None) -> Optional[str]:
        """
        工具使用摘要

        用于在紧凑视图中显示工具使用的简短摘要。

        Args:
            input: 工具输入参数（可选）

        Returns:
            摘要字符串，或 None 表示不显示
        """
        return None

    def get_activity_description(self, input: Optional[InputT] = None) -> Optional[str]:
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

    def get_path(self, input: InputT) -> Optional[str]:
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
        context: Dict[str, Any] | ToolUseContext,
    ) -> Optional[Dict[str, Any]]:
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
        input: Optional[InputT] = None,
    ) -> Optional[ToolReceipt]:
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
        context: Dict[str, Any],
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
        # 如果 input 是字典，转换为 Pydantic 模型
        if isinstance(input, dict):
            input = self.input_schema(**input)

        tool_context = ToolUseContext.coerce(context)

        # 工具层统一使用 ToolUseContext；如上层传入的是 execution_context 字典，
        # 会原位包装，确保工具内的写操作仍能回写到原始上下文。
        return await self.call(
            input,
            tool_context,
            lambda: True,     # 编排系统已经处理了权限
            None,             # parent_message
            None,             # on_progress
        )

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
    output_schema: Optional[type] = None,
    *,
    aliases: Optional[list[str]] = None,
    search_hint: Optional[str] = None,
    strict: bool = False,
    # 可选覆盖的方法
    is_enabled: Optional[Callable[[], bool]] = None,
    is_concurrency_safe: Optional[Callable[[Any], bool]] = None,
    is_read_only: Optional[Callable[[Any], bool]] = None,
    is_destructive: Optional[Callable[[Any], bool]] = None,
    to_auto_classifier_input: Optional[Callable[[Any], Any]] = None,
    user_facing_name: Optional[Callable[[Optional[Any]], str]] = None,
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
            @property
            def input_schema(self) -> type[BaseModel]:
                return _input_schema

            if _output_schema is not None:
                @property
                def output_schema(self) -> Optional[type]:
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
