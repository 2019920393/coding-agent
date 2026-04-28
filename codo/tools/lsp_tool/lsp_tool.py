"""
LSPTool - Language Server Protocol 工具

提供代码智能功能：跳转定义、查找引用、悬停信息、符号搜索等
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional, Any, Dict

from lsprotocol.types import (
    Position,
    TextDocumentIdentifier,
    DefinitionParams,
    ReferenceParams,
    ReferenceContext,
    HoverParams,
    DocumentSymbolParams,
    WorkspaceSymbolParams,
    ImplementationParams,
    CallHierarchyPrepareParams,
    CallHierarchyIncomingCallsParams,
    CallHierarchyOutgoingCallsParams,
)

from codo.tools.base import Tool, ToolUseContext
from codo.tools.types import ValidationResult
from codo.types.permissions import PermissionAllowDecision, create_allow_decision
from codo.services.lsp import LSPServerManager
from codo.services.lsp.types import LSP_METHOD_MAP, LSPOperationType

from .types import LSPToolInput, LSPToolOutput
from .formatters import format_result
from .symbol_context import extract_symbol_at_position

logger = logging.getLogger(__name__)

# 文件大小限制：10MB
MAX_FILE_SIZE = 10 * 1024 * 1024

class LSPTool(Tool[LSPToolInput, LSPToolOutput, None]):
    """LSP 工具

    提供代码智能功能：
    - goToDefinition: 跳转到定义
    - findReferences: 查找所有引用
    - hover: 获取悬停信息
    - documentSymbol: 获取文档符号
    - workspaceSymbol: 工作区符号搜索
    - goToImplementation: 跳转到实现
    - prepareCallHierarchy: 准备调用层级
    - incomingCalls: 查找调用该函数的所有函数
    - outgoingCalls: 查找该函数调用的所有函数
    """

    def __init__(self):
        self.name = "LSP"
        self._manager: Optional[LSPServerManager] = None
        self._manager_lock = asyncio.Lock()

    @property
    def input_schema(self) -> type[LSPToolInput]:
        """返回输入 schema"""
        return LSPToolInput

    @property
    def output_schema(self) -> type[LSPToolOutput]:
        """返回输出 schema"""
        return LSPToolOutput

    async def description(self, input_data: LSPToolInput, options: dict) -> str:
        """返回工具描述"""
        from .prompt import DESCRIPTION
        return DESCRIPTION

    async def prompt(self, options: dict) -> str:
        """返回系统提示"""
        from .prompt import PROMPT
        return PROMPT

    def map_tool_result_to_tool_result_block_param(
        self, content: LSPToolOutput, tool_use_id: str
    ):
        """将工具结果转换为 API 格式"""
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content.formatted_result,
        }

    async def _get_manager(self, cwd: str) -> LSPServerManager:
        """获取或创建 LSP 服务器管理器

        Args:
            cwd: 工作目录

        Returns:
            LSP 服务器管理器
        """
        async with self._manager_lock:
            if self._manager is None:
                self._manager = LSPServerManager()
                await self._manager.initialize(cwd=cwd)
            return self._manager

    async def validate_input(
        self, input_data: LSPToolInput, context: ToolUseContext
    ) -> ValidationResult:
        """验证输入

        Args:
            input_data: 输入数据
            context: 工具使用上下文

        Returns:
            验证结果
        """
        # 获取工作目录
        cwd = context.get("cwd") or context.get_options().get("cwd", ".")

        # 解析文件路径
        file_path = Path(input_data.file_path)

        # 检查是否为绝对路径
        if not file_path.is_absolute():
            file_path = Path(cwd) / file_path

        # 检查文件是否存在
        if not file_path.exists():
            return ValidationResult(
                result=False,
                message=f"File does not exist: {input_data.file_path}",
            )

        # 检查是否为文件（非目录）
        if not file_path.is_file():
            return ValidationResult(
                result=False,
                message=f"Path is not a file: {input_data.file_path}",
            )

        # 检查文件大小
        file_size = file_path.stat().st_size
        if file_size > MAX_FILE_SIZE:
            return ValidationResult(
                result=False,
                message=f"File too large: {file_size} bytes (max {MAX_FILE_SIZE})",
            )

        # 检查 UNC 路径（Windows 安全）
        if str(file_path).startswith("\\\\"):
            return ValidationResult(
                result=False,
                message="UNC paths are not supported for security reasons",
            )

        # 检查位置范围
        if input_data.line < 1 or input_data.character < 1:
            return ValidationResult(
                result=False,
                message="Line and character must be >= 1",
            )

        # workspaceSymbol 需要 query 参数
        if input_data.operation == "workspaceSymbol" and not input_data.query:
            return ValidationResult(
                result=False,
                message="workspaceSymbol operation requires 'query' parameter",
            )

        return ValidationResult(result=True)

    async def check_permissions(
        self, input_data: LSPToolInput, context: ToolUseContext
    ) -> PermissionAllowDecision:
        """检查权限

        LSP 是只读操作，直接返回 ALLOW（对齐 Tool 基类的 PermissionResult 接口）

        Args:
            input_data: 输入数据
            context: 工具使用上下文

        Returns:
            PermissionResult（ALLOW）
        """
        return create_allow_decision()

    def _get_method_and_params(
        self, input_data: LSPToolInput, file_path: str
    ) -> tuple[str, Any]:
        """获取 LSP 方法和参数

        Args:
            input_data: 输入数据
            file_path: 文件路径（绝对路径）

        Returns:
            (方法名, 参数)
        """
        # 转换为 file:// URI
        uri = Path(file_path).as_uri()

        # 转换为 0-based 位置
        position = Position(
            line=input_data.line - 1,
            character=input_data.character - 1,
        )

        text_document = TextDocumentIdentifier(uri=uri)

        # 根据操作类型构建参数
        if input_data.operation == "goToDefinition":
            method = LSP_METHOD_MAP[LSPOperationType.GO_TO_DEFINITION]
            params = DefinitionParams(
                text_document=text_document,
                position=position,
            )

        elif input_data.operation == "findReferences":
            method = LSP_METHOD_MAP[LSPOperationType.FIND_REFERENCES]
            params = ReferenceParams(
                text_document=text_document,
                position=position,
                context=ReferenceContext(include_declaration=True),
            )

        elif input_data.operation == "hover":
            method = LSP_METHOD_MAP[LSPOperationType.HOVER]
            params = HoverParams(
                text_document=text_document,
                position=position,
            )

        elif input_data.operation == "documentSymbol":
            method = LSP_METHOD_MAP[LSPOperationType.DOCUMENT_SYMBOL]
            params = DocumentSymbolParams(
                text_document=text_document,
            )

        elif input_data.operation == "workspaceSymbol":
            method = LSP_METHOD_MAP[LSPOperationType.WORKSPACE_SYMBOL]
            params = WorkspaceSymbolParams(
                query=input_data.query or "",
            )

        elif input_data.operation == "goToImplementation":
            method = LSP_METHOD_MAP[LSPOperationType.GO_TO_IMPLEMENTATION]
            params = ImplementationParams(
                text_document=text_document,
                position=position,
            )

        elif input_data.operation == "prepareCallHierarchy":
            method = LSP_METHOD_MAP[LSPOperationType.PREPARE_CALL_HIERARCHY]
            params = CallHierarchyPrepareParams(
                text_document=text_document,
                position=position,
            )

        elif input_data.operation == "incomingCalls":
            method = LSP_METHOD_MAP[LSPOperationType.INCOMING_CALLS]
            # 注意：incomingCalls 需要先调用 prepareCallHierarchy 获取 item
            # 这里我们先调用 prepareCallHierarchy，然后使用第一个结果
            params = CallHierarchyPrepareParams(
                text_document=text_document,
                position=position,
            )

        elif input_data.operation == "outgoingCalls":
            method = LSP_METHOD_MAP[LSPOperationType.OUTGOING_CALLS]
            # 同上
            params = CallHierarchyPrepareParams(
                text_document=text_document,
                position=position,
            )

        else:
            raise ValueError(f"Unknown operation: {input_data.operation}")

        return method, params

    async def call(
        self,
        input_data: LSPToolInput,
        context: ToolUseContext,
        can_use_tool: Any = None,
        parent_message: Any = None,
        on_progress: Optional[Any] = None,
    ) -> "ToolResult":
        """执行 LSP 操作

        对齐执行器的 5 参数调用约定：
        call(input, context, can_use_tool, parent_message, on_progress)

        Args:
            input_data: 输入数据
            context: 工具使用上下文
            can_use_tool: 权限检查回调（LSP 不使用）
            parent_message: 父消息（LSP 不使用）
            on_progress: 进度回调（LSP 不使用）

        Returns:
            ToolResult 包含 LSPToolOutput
        """
        from codo.tools.types import ToolResult as TR
        # 获取工作目录
        cwd = context.get("cwd") or context.get_options().get("cwd", ".")

        # 解析文件路径
        file_path = Path(input_data.file_path)
        if not file_path.is_absolute():
            file_path = Path(cwd) / file_path
        file_path = file_path.resolve()

        # 获取 LSP 管理器
        manager = await self._get_manager(cwd)

        # 读取文件内容
        content = file_path.read_text(encoding="utf-8")

        # 打开文件（如果尚未打开）
        if not manager.is_file_open(str(file_path)):
            await manager.open_file(str(file_path), content, cwd=cwd)

        # 获取方法和参数
        method, params = self._get_method_and_params(input_data, str(file_path))

        # 处理 incomingCalls 和 outgoingCalls（需要两步）
        if input_data.operation in ["incomingCalls", "outgoingCalls"]:
            # 第一步：prepareCallHierarchy
            prepare_result = await manager.send_request(
                str(file_path), method, params, cwd=cwd
            )

            if not prepare_result or len(prepare_result) == 0:
                return TR(data=LSPToolOutput(
                    operation=input_data.operation,
                    file_path=str(file_path),
                    result="No call hierarchy item found at this position",
                    result_count=0,
                    file_count=0,
                ))

            # 第二步：incomingCalls 或 outgoingCalls
            item = prepare_result[0]
            if input_data.operation == "incomingCalls":
                method = LSP_METHOD_MAP[LSPOperationType.INCOMING_CALLS]
                params = CallHierarchyIncomingCallsParams(item=item)
            else:
                method = LSP_METHOD_MAP[LSPOperationType.OUTGOING_CALLS]
                params = CallHierarchyOutgoingCallsParams(item=item)

        # 发送请求
        result = await manager.send_request(
            str(file_path), method, params, cwd=cwd
        )

        # 提取符号名称（用于显示）
        symbol_name = None
        try:
            symbol_name = extract_symbol_at_position(
                content, input_data.line, input_data.character
            )
        except Exception as e:
            logger.debug(f"Failed to extract symbol: {e}")

        # 格式化结果
        formatted_result, result_count, file_count = format_result(
            input_data.operation,
            result,
            cwd,
        )

        return TR(data=LSPToolOutput(
            operation=input_data.operation,
            file_path=str(file_path),
            result=formatted_result,
            result_count=result_count,
            file_count=file_count,
            symbol_name=symbol_name,
        ))

    def is_concurrency_safe(self, input_data: Optional[LSPToolInput] = None) -> bool:
        """是否并发安全

        Returns:
            True（只读操作）
        """
        return True

    def is_read_only(self, input_data: Optional[LSPToolInput] = None) -> bool:
        """是否只读

        Returns:
            True
        """
        return True

    async def cleanup(self):
        """清理资源"""
        if self._manager:
            await self._manager.shutdown()
            self._manager = None
