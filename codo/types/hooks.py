"""
Hook 类型定义

Hook 系统支持在工具执行的不同阶段插入自定义逻辑：
- PreToolUse: 工具执行前（权限决策、输入修改）
- PostToolUse: 工具执行成功后（输出处理）
- PostToolUseFailure: 工具执行失败后（错误恢复）
"""

from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional, List
from codo.types.permissions import PermissionBehavior

# ============================================================================
# Hook 事件类型（Hook Event Types）
# ============================================================================

HookEventName = Literal["PreToolUse", "PostToolUse", "PostToolUseFailure"]

# ============================================================================
# Hook 输入类型（Hook Input Types）
# ============================================================================

@dataclass
class PreToolUseHookInput:
    """
    PreToolUse Hook 输入

    [Workflow]
    在工具执行前调用，用于：
    1. 权限决策（allow/deny/ask）
    2. 修改工具输入
    3. 提供额外上下文
    4. 阻止工具执行
    """
    tool_name: str  # 工具名称
    tool_input: Dict[str, Any]  # 工具输入
    tool_use_id: str  # 工具使用 ID
    cwd: str  # 当前工作目录
    hook_event_name: Literal["PreToolUse"] = "PreToolUse"  # Hook 事件名称

@dataclass
class PostToolUseHookInput:
    """
    PostToolUse Hook 输入

    参考：src/entrypoints/sdk/coreSchemas.ts:436
    简化：移除 session_id, transcript_path, permission_mode

    [Workflow]
    在工具成功执行后调用，用于：
    1. 处理工具输出
    2. 提供额外上下文
    3. 阻止继续执行
    4. 记录或审计工具使用
    """
    tool_name: str  # 工具名称
    tool_input: Dict[str, Any]  # 工具输入
    tool_response: Any  # 工具响应
    tool_use_id: str  # 工具使用 ID
    cwd: str  # 当前工作目录
    hook_event_name: Literal["PostToolUse"] = "PostToolUse"  # Hook 事件名称

@dataclass
class PostToolUseFailureHookInput:
    """
    PostToolUseFailure Hook 输入

    参考：src/entrypoints/sdk/coreSchemas.ts:448
    简化：移除 session_id, transcript_path, permission_mode

    [Workflow]
    在工具执行失败后调用，用于：
    1. 错误恢复和重试
    2. 错误分析和记录
    3. 提供恢复建议
    4. 触发补救措施
    """
    tool_name: str  # 工具名称
    tool_input: Dict[str, Any]  # 工具输入
    tool_use_id: str  # 工具使用 ID
    error: str  # 错误消息
    cwd: str  # 当前工作目录
    hook_event_name: Literal["PostToolUseFailure"] = "PostToolUseFailure"  # Hook 事件名称
    is_interrupt: bool = False  # 是否用户中断（True=用户中断，False=执行错误）

# Hook 输入联合类型
HookInput = PreToolUseHookInput | PostToolUseHookInput | PostToolUseFailureHookInput

# ============================================================================
# Hook 结果类型（Hook Result Types）
# ============================================================================

@dataclass
class HookResult:
    """
    单个 Hook 的执行结果

    参考：src/types/hooks.ts:260-290
    简化：移除 message, systemMessage, blockingError 等复杂字段

    [Workflow]
    Hook 执行后返回的结果，包含：
    1. 执行结果（success/blocking/error/cancelled）
    2. 权限决策（allow/deny/ask/passthrough）
    3. 输入/输出修改
    4. 额外上下文信息
    """
    # 执行结果
    outcome: Literal["success", "blocking", "non_blocking_error", "cancelled"] = "success"

    # 权限决策（仅 PreToolUse Hook）
    permission_behavior: Optional[PermissionBehavior] = None  # allow/deny/ask
    permission_decision_reason: Optional[str] = None  # 权限决策原因

    # 输入/输出修改
    updated_input: Optional[Dict[str, Any]] = None  # 修改后的工具输入（PreToolUse）
    updated_output: Optional[Any] = None  # 修改后的工具输出（PostToolUse）

    # 控制标志
    prevent_continuation: bool = False  # 是否阻止继续执行
    stop_reason: Optional[str] = None  # 停止原因
    retry: bool = False  # 是否重试（PostToolUseFailure）

    # 额外信息
    additional_context: Optional[str] = None  # 额外上下文信息
    error_message: Optional[str] = None  # 错误消息

@dataclass
class AggregatedHookResult:
    """
    聚合后的 Hook 结果

    参考：src/types/hooks.ts:292-302
    简化：移除 message, blockingError 等字段

    [Workflow]
    多个 Hook 结果聚合后的最终结果：
    1. 权限决策聚合（deny > ask > allow）
    2. 输入修改合并（后面的覆盖前面的）
    3. 上下文信息收集
    """
    # 权限决策（聚合后）
    permission_behavior: Optional[PermissionBehavior] = None  # 最终权限决策
    permission_decision_reason: Optional[str] = None  # 决策原因

    # 输入/输出修改（聚合后）
    updated_input: Optional[Dict[str, Any]] = None  # 最终修改后的输入
    updated_output: Optional[Any] = None  # 最终修改后的输出

    # 控制标志
    prevent_continuation: bool = False  # 是否阻止继续
    stop_reason: Optional[str] = None  # 停止原因
    retry: bool = False  # 是否重试

    # 额外信息（收集所有 Hook 的上下文）
    additional_contexts: List[str] = None  # 所有额外上下文

    def __post_init__(self):
        """初始化默认值"""
        if self.additional_contexts is None:
            self.additional_contexts = []

# ============================================================================
# Hook 配置类型（Hook Configuration Types）
# ============================================================================

@dataclass
class HookConfig:
    """
    Hook 配置

    参考：src/types/hooks.ts（简化版）

    定义 Hook 的执行配置：
    1. Hook 命令或脚本路径
    2. 匹配的工具名称（可选，None 表示匹配所有工具）
    3. Hook 事件类型
    """
    command: str  # Hook 命令或脚本路径
    tool_name: Optional[str] = None  # 匹配的工具名称（None=所有工具）
    event: HookEventName = "PreToolUse"  # Hook 事件类型
    timeout: int = 5000  # 超时时间（毫秒），默认 5 秒
