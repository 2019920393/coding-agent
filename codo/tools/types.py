"""
工具系统类型定义

[Workflow]
本模块定义了工具系统的核心类型，包括：
- 泛型类型变量（InputT, OutputT, ProgressT）
- 工具结果类型（ToolResult）
- 验证结果类型（ValidationResult）
- 进度类型（ToolProgress）

这些类型被工具基类和具体工具实现使用。
"""

from typing import TypeVar, Generic, Optional, Dict, Any, Callable, Awaitable
from dataclasses import dataclass, field
from pydantic import BaseModel

from .receipts import AuditLogEvent, ProposedFileChange, ToolReceipt

# ============================================================================
# 泛型类型变量
# ============================================================================

# 工具输入类型（必须是 Pydantic BaseModel）
InputT = TypeVar('InputT', bound=BaseModel)

# 工具输出类型（任意类型）
OutputT = TypeVar('OutputT')

# 工具进度类型（必须是 Pydantic BaseModel）
ProgressT = TypeVar('ProgressT', bound=BaseModel)

# ============================================================================
# 工具结果类型
# ============================================================================

@dataclass
class ToolResult(Generic[OutputT]):
    """
    工具执行结果

    Attributes:
        data: 工具输出数据（成功时）
        error: 错误消息（失败时）
        new_messages: 可选的新消息列表（用于工具生成额外消息）
        context_modifier: 可选的上下文修改函数（仅对非并发安全工具有效）
        mcp_meta: MCP 协议元数据（用于 MCP 工具）
    """
    data: Optional[OutputT] = None
    error: Optional[str] = None
    receipt: Optional[ToolReceipt] = None  #用于UI展示和日志
    staged_changes: list[ProposedFileChange] = field(default_factory=list)
    audit_events: list[AuditLogEvent] = field(default_factory=list)
    new_messages: Optional[list] = None   # 工具可以向对话中注入新消息
    context_modifier: Optional[Callable] = None
    mcp_meta: Optional[Dict[str, Any]] = None

# ============================================================================
# 验证结果类型
# ============================================================================

@dataclass
class ValidationResult:
    """
    输入验证结果

    Attributes:
        result: 验证是否通过
        message: 验证失败时的错误消息
        error_code: 验证失败时的错误代码
    """
    result: bool
    message: Optional[str] = None
    error_code: Optional[int] = None

# ============================================================================
# 进度类型
# ============================================================================

class ToolProgress(BaseModel, Generic[ProgressT]):
    """
    工具执行进度

    Attributes:
        tool_use_id: 工具使用 ID
        data: 进度数据
    """
    tool_use_id: str
    data: ProgressT

# 工具调用进度回调类型（不使用泛型，因为 Callable 不支持泛型参数）
ToolCallProgress = Callable[[ToolProgress], Awaitable[None]]
