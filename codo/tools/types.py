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
    工具执行结果容器。

    [字段说明]
    - data: 成功时的输出数据
    - error: 失败时的错误消息（非 None 表示执行失败）
    - receipt: 结构化回执，用于 UI 展示和日志
    - staged_changes: 待审阅的文件变更列表（需用户确认）
    - audit_events: 审计日志事件列表
    - new_messages: 工具向对话注入的额外消息（如附件）
    - context_modifier: 上下文修改函数（仅对非并发安全工具有效）
    - mcp_meta: MCP 协议元数据

    示例（成功）:
        ToolResult(
            data=BashOutput(stdout="hello", exit_code=0),
            receipt=CommandReceipt(kind="command", summary="已执行 echo hello", ...),
        )

    示例（失败）:
        ToolResult(error="文件不存在: /tmp/test.py")
    """
    data: Optional[OutputT] = None
    error: Optional[str] = None
    receipt: Optional[ToolReceipt] = None  # 用于 UI 展示和日志
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
    输入验证结果。

    示例（通过）:
        ValidationResult(result=True)

    示例（失败）:
        ValidationResult(result=False, message="文件路径不能为空", error_code=400)
    """
    result: bool                      # True 表示验证通过
    message: Optional[str] = None     # 失败时的错误消息
    error_code: Optional[int] = None  # 失败时的错误代码

# ============================================================================
# 进度类型
# ============================================================================

class ToolProgress(BaseModel, Generic[ProgressT]):
    """
    工具执行进度，用于流式进度回调。

    工具在长时间执行时（如 Bash 命令）可以通过 on_progress 回调
    定期发送进度更新，UI 层会实时展示。

    示例:
        ToolProgress(
            tool_use_id="toolu_abc123",
            data=BashProgress(output="正在编译...\n"),
        )
    """
    tool_use_id: str   # 对应的工具调用 ID
    data: ProgressT    # 进度数据（工具自定义类型）

# 工具调用进度回调类型（不使用泛型，因为 Callable 不支持泛型参数）
ToolCallProgress = Callable[[ToolProgress], Awaitable[None]]
"""异步进度回调类型，接收 ToolProgress 对象，无返回值。"""
