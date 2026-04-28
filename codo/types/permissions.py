"""
权限系统类型定义

[简化说明]
- 移除 auto 模式（AI分类器）
- 移除 acceptEdits、dontAsk、plan 模式
- 简化规则来源为 3 个（userSettings、projectSettings、session）
- 保留核心的 allow/deny/ask 三种行为
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Any, List
from typing_extensions import Literal

# ============================================================================
# 权限模式（Permission Modes）
# ============================================================================

class PermissionMode(str, Enum):
    """
    权限模式枚举

    参考：src/types/permissions.ts:16-29
    简化：只保留 default 和 bypassPermissions 两种模式
    """
    DEFAULT = "default"  # 每次工具使用都询问用户
    BYPASS_PERMISSIONS = "bypassPermissions"  # 绕过所有权限检查（除安全检查外）

# ============================================================================
# 权限行为（Permission Behaviors）
# ============================================================================

PermissionBehavior = Literal["allow", "deny", "ask"]
"""
权限行为类型
- allow: 允许执行
- deny: 拒绝执行
- ask: 询问用户

参考：src/types/permissions.ts:44
"""

# ============================================================================
# 权限规则（Permission Rules）
# ============================================================================

class PermissionRuleSource(str, Enum):
    """
    权限规则来源

    优先级（从高到低）：
    1. projectSettings - 项目级设置（.codo/settings.json）
    2. userSettings - 用户级设置（~/.codo/settings.json）
    3. session - 会话级设置（运行时）
    """
    PROJECT_SETTINGS = "projectSettings"  # 项目级设置（最高优先级）
    USER_SETTINGS = "userSettings"  # 用户级设置
    SESSION = "session"  # 会话级设置（最低优先级）

@dataclass
class PermissionRuleValue:
    """
    权限规则值

    格式示例：
    - "Bash" - 整个工具
    - "Bash(prefix:npm)" - 特定前缀
    - "Write" - 文件写入工具
    """
    tool_name: str  # 工具名称
    rule_content: Optional[str] = None  # 可选内容（如 "prefix:npm"）

@dataclass
class PermissionRule:
    """
    权限规则

    参考：src/types/permissions.ts:75-79

    包含规则来源、行为和值
    """
    source: PermissionRuleSource  # 规则来源
    rule_behavior: PermissionBehavior  # 规则行为（allow/deny/ask）
    rule_value: PermissionRuleValue  # 规则值

# ============================================================================
# 权限决策（Permission Decisions）
# ============================================================================

@dataclass
class PermissionDecisionReason:
    """
    权限决策原因

    参考：src/types/permissions.ts:271-324
    简化：只保留 rule、mode、safetyCheck、other 四种类型
    """
    type: Literal["rule", "mode", "safetyCheck", "other"]

    # type=rule 时使用
    rule: Optional[PermissionRule] = None

    # type=mode 时使用
    mode: Optional[PermissionMode] = None

    # type=safetyCheck 或 other 时使用
    reason: Optional[str] = None

@dataclass
class PermissionAllowDecision:
    """
    权限允许决策

    参考：src/types/permissions.ts:174-184
    """
    behavior: Literal["allow"] = "allow"
    updated_input: Optional[Dict[str, Any]] = None  # 更新后的输入（可选）
    user_modified: bool = False  # 用户是否修改了输入
    decision_reason: Optional[PermissionDecisionReason] = None  # 决策原因

@dataclass
class PermissionAskDecision:
    """
    权限询问决策

    参考：src/types/permissions.ts:199-226
    简化：移除 suggestions、metadata、pendingClassifierCheck 等字段
    """
    behavior: Literal["ask"] = "ask"
    message: str = ""  # 询问消息
    updated_input: Optional[Dict[str, Any]] = None  # 更新后的输入（可选）
    decision_reason: Optional[PermissionDecisionReason] = None  # 决策原因
    blocked_path: Optional[str] = None  # 被阻止的路径（用于文件操作）

@dataclass
class PermissionDenyDecision:
    """
    权限拒绝决策

    参考：src/types/permissions.ts:232-236
    """
    behavior: Literal["deny"] = "deny"
    message: str = ""  # 拒绝消息
    decision_reason: Optional[PermissionDecisionReason] = None  # 决策原因

# 权限决策联合类型
PermissionDecision = PermissionAllowDecision | PermissionAskDecision | PermissionDenyDecision

@dataclass
class PermissionResult:
    """
    权限结果（包含 passthrough 选项）

    参考：src/types/permissions.ts:251-266
    简化：移除 suggestions、pendingClassifierCheck 等字段

    passthrough 表示工具没有做出决策，需要继续检查
    """
    behavior: Literal["allow", "ask", "deny", "passthrough"]
    message: Optional[str] = None
    updated_input: Optional[Dict[str, Any]] = None
    decision_reason: Optional[PermissionDecisionReason] = None
    blocked_path: Optional[str] = None

# ============================================================================
# 工具权限上下文（Tool Permission Context）
# ============================================================================

@dataclass
class ToolPermissionContext:
    """
    工具权限上下文

    参考：src/types/permissions.ts:427-441
    简化：移除 additionalWorkingDirectories、strippedDangerousRules 等字段

    包含权限检查所需的所有上下文信息
    """
    mode: PermissionMode  # 权限模式

    # 权限规则（按来源分组）
    always_allow_rules: Dict[PermissionRuleSource, List[str]]  # 允许规则
    always_deny_rules: Dict[PermissionRuleSource, List[str]]  # 拒绝规则
    always_ask_rules: Dict[PermissionRuleSource, List[str]]  # 询问规则

    # 工作目录
    cwd: str  # 当前工作目录

    # 标志
    is_bypass_permissions_mode_available: bool = True  # 是否可用 bypassPermissions 模式

# ============================================================================
# 辅助函数
# ============================================================================

def create_allow_decision(
    updated_input: Optional[Dict[str, Any]] = None,
    reason: Optional[PermissionDecisionReason] = None,
) -> PermissionAllowDecision:
    """
    创建允许决策

    [Workflow]
    1. 创建 PermissionAllowDecision 对象
    2. 设置 updated_input 和 decision_reason
    3. 返回决策对象
    """
    return PermissionAllowDecision(
        behavior="allow",
        updated_input=updated_input,
        decision_reason=reason,
    )

def create_ask_decision(
    message: str,
    updated_input: Optional[Dict[str, Any]] = None,
    reason: Optional[PermissionDecisionReason] = None,
    blocked_path: Optional[str] = None,
) -> PermissionAskDecision:
    """
    创建询问决策

    [Workflow]
    1. 创建 PermissionAskDecision 对象
    2. 设置 message、updated_input、decision_reason、blocked_path
    3. 返回决策对象
    """
    return PermissionAskDecision(
        behavior="ask",
        message=message,
        updated_input=updated_input,
        decision_reason=reason,
        blocked_path=blocked_path,
    )

def create_deny_decision(
    message: str,
    reason: Optional[PermissionDecisionReason] = None,
) -> PermissionDenyDecision:
    """
    创建拒绝决策

    [Workflow]
    1. 创建 PermissionDenyDecision 对象
    2. 设置 message 和 decision_reason
    3. 返回决策对象
    """
    return PermissionDenyDecision(
        behavior="deny",
        message=message,
        decision_reason=reason,
    )

def create_passthrough_result(
    message: Optional[str] = None,
) -> PermissionResult:
    """
    创建 passthrough 结果

    [Workflow]
    1. 创建 PermissionResult 对象
    2. 设置 behavior 为 "passthrough"
    3. 返回结果对象

    passthrough 表示工具没有做出决策，需要继续检查
    """
    return PermissionResult(
        behavior="passthrough",
        message=message,
    )
