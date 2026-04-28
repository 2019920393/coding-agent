"""
权限检查器

[简化说明]
实现核心权限检查逻辑。简化为个人使用场景，移除 AI 分类器和复杂的钩子系统。
"""

import logging
from typing import Dict, Any, Optional
import os

# 模块级日志记录器
logger = logging.getLogger(__name__)

from codo.tools.base import Tool, ToolUseContext
from codo.types.permissions import (
    PermissionDecision,
    PermissionAllowDecision,
    PermissionAskDecision,
    PermissionDenyDecision,
    PermissionResult,
    PermissionDecisionReason,
    PermissionMode,
    ToolPermissionContext,
    create_allow_decision,
    create_ask_decision,
    create_deny_decision,
    create_passthrough_result,
)
from codo.services.tools.permission_rules import (
    get_deny_rule_for_tool,
    get_ask_rule_for_tool,
    tool_always_allowed_rule,
    create_permission_request_message,
    format_permission_rule_value,
    get_permission_rule_source_display_name,
)

# ============================================================================
# 安全检查（Safety Checks）
# ============================================================================

def check_path_safety(file_path: str, cwd: str) -> Optional[PermissionAskDecision]:
    """
    检查文件路径安全性

    参考：src/utils/permissions/permissions.ts:700-750 (安全检查部分)

    [Workflow]
    1. 检查路径是否在敏感目录中（.git/, .codo/）
    2. 检查路径是否是配置文件（.env, settings.json 等）
    3. 如果是敏感路径，返回 ask 决策
    4. 否则，返回 None（通过检查）

    敏感路径：
    - .git/ - Git 仓库目录
    - .codo/ - Codo 配置目录
    - .env, .env.* - 环境变量文件
    - settings.json - 配置文件

    Args:
        file_path: 文件路径
        cwd: 当前工作目录

    Returns:
        如果路径不安全，返回 PermissionAskDecision；否则返回 None
    """
    # 规范化路径
    if not os.path.isabs(file_path):
        file_path = os.path.abspath(os.path.join(cwd, file_path))

    # 检查敏感目录
    sensitive_dirs = ['.git', '.codo']
    for sensitive_dir in sensitive_dirs:
        if f"{os.sep}{sensitive_dir}{os.sep}" in file_path or file_path.endswith(os.sep + sensitive_dir):
            reason = PermissionDecisionReason(
                type="safetyCheck",
                reason=f"访问敏感目录: {sensitive_dir}",
            )
            return create_ask_decision(
                message=f"此操作将访问敏感目录 ({sensitive_dir})。是否继续？",
                reason=reason,
                blocked_path=file_path,
            )

    # 检查敏感文件
    basename = os.path.basename(file_path)
    sensitive_files = ['.env', 'settings.json', 'config.json']
    if basename in sensitive_files or basename.startswith('.env.'):
        reason = PermissionDecisionReason(
            type="safetyCheck",
            reason=f"访问敏感文件: {basename}",
        )
        return create_ask_decision(
            message=f"此操作将访问敏感文件 ({basename})。是否继续？",
            reason=reason,
            blocked_path=file_path,
        )

    return None

# ============================================================================
# 核心权限检查（Core Permission Checking）
# ============================================================================

async def has_permissions_to_use_tool(
    tool: Tool,
    input_data: Any,
    context: ToolUseContext | Dict[str, Any],
) -> PermissionDecision:
    """
    检查工具使用权限

    [Workflow - 三阶段检查]

    阶段1: 规则检查（Rule-Based Checks）
    1a. 检查整个工具是否被拒绝规则覆盖 → deny
    1b. 检查整个工具是否有询问规则 → ask
    1c. 调用工具特定权限检查 (tool.check_permissions)
        - 如果返回 deny → deny
        - 如果返回 ask → ask
        - 如果返回 passthrough → 继续
    1d. 安全检查（敏感路径、配置文件）→ ask（绕过权限免疫）

    阶段2: 模式检查（Mode-Based Checks）
    2a. bypassPermissions 模式 → allow
    2b. 整个工具允许规则 → allow

    阶段3: 默认行为
    3. 如果没有匹配任何规则，default 模式 → ask

    Args:
        tool: Tool 对象
        input_data: 工具输入数据（dict 或 Pydantic 模型）
        context: 工具使用上下文

    Returns:
        PermissionDecision (allow/ask/deny)
    """
    context = ToolUseContext.coerce(context)
    permission_context = context.get("permission_context")
    if not permission_context:
        raise ValueError("permission_context is required in ToolUseContext")

    if hasattr(input_data, "model_dump"):
        normalized_input_data = input_data.model_dump()
    elif isinstance(input_data, dict):
        normalized_input_data = input_data
    else:
        normalized_input_data = {}

    # ========================================================================
    # 阶段1: 规则检查（Rule-Based Checks）
    # ========================================================================

    # 1a. 检查整个工具是否被拒绝规则覆盖
    deny_rule = get_deny_rule_for_tool(permission_context, tool)
    if deny_rule:
        reason = PermissionDecisionReason(
            type="rule",
            rule=deny_rule,
        )
        rule_string = format_permission_rule_value(deny_rule.rule_value)
        source_string = get_permission_rule_source_display_name(deny_rule.source)
        return create_deny_decision(
            message=f"来自{source_string}的权限规则 '{rule_string}' 拒绝此 {tool.name} 命令",
            reason=reason,
        )

    # 1b. 检查整个工具是否有询问规则
    ask_rule = get_ask_rule_for_tool(permission_context, tool)
    if ask_rule:
        reason = PermissionDecisionReason(
            type="rule",
            rule=ask_rule,
        )
        rule_string = format_permission_rule_value(ask_rule.rule_value)
        source_string = get_permission_rule_source_display_name(ask_rule.source)
        return create_ask_decision(
            message=f"来自{source_string}的权限规则 '{rule_string}' 要求批准此 {tool.name} 命令",
            reason=reason,
        )

    # 1c. 工具特定权限检查
    tool_result = await tool.check_permissions(input_data, context)

    # 工具返回 deny
    if tool_result.behavior == "deny":
        return create_deny_decision(
            message=tool_result.message or f"{tool.name} 拒绝了此操作",
            reason=tool_result.decision_reason,
        )

    # 工具返回 ask
    if tool_result.behavior == "ask":
        return create_ask_decision(
            message=tool_result.message or create_permission_request_message(tool.name),
            updated_input=tool_result.updated_input,
            reason=tool_result.decision_reason,
            blocked_path=tool_result.blocked_path,
        )

    # 工具返回 allow（提前允许）
    if tool_result.behavior == "allow":
        return create_allow_decision(
            updated_input=tool_result.updated_input,
            reason=tool_result.decision_reason,
        )

    # 工具返回 passthrough，继续检查

    # 1d. 安全检查（敏感路径、配置文件）
    # 这个检查绕过权限免疫，即使在 bypassPermissions 模式下也会触发
    if "file_path" in normalized_input_data:
        safety_check = check_path_safety(
            normalized_input_data["file_path"],
            context.get("cwd", "/"),
        )
        if safety_check:
            return safety_check

    # ========================================================================
    # 阶段2: 模式检查（Mode-Based Checks）
    # ========================================================================

    # 2a. bypassPermissions 模式 → 允许
    if permission_context.mode == PermissionMode.BYPASS_PERMISSIONS:
        reason = PermissionDecisionReason(
            type="mode",
            mode=permission_context.mode,
        )
        return create_allow_decision(reason=reason)

    # 2b. 整个工具允许规则 → 允许
    allow_rule = tool_always_allowed_rule(permission_context, tool)
    if allow_rule:
        reason = PermissionDecisionReason(
            type="rule",
            rule=allow_rule,
        )
        return create_allow_decision(reason=reason)

    # ========================================================================
    # 阶段3: 默认行为
    # ========================================================================

    # 3. default 模式 → 询问
    reason = PermissionDecisionReason(
        type="mode",
        mode=permission_context.mode,
    )
    return create_ask_decision(
        message=create_permission_request_message(tool.name),
        reason=reason,
    )

# ============================================================================
# 辅助函数
# ============================================================================

def create_default_permission_context(
    cwd: str,
    mode: PermissionMode = PermissionMode.DEFAULT,
) -> ToolPermissionContext:
    """
    创建默认权限上下文（从磁盘加载规则）

    [Workflow]
    1. 尝试从磁盘加载权限规则
    2. 如果加载失败，使用空规则
    3. 覆盖模式（可能由调用方指定）
    4. 返回 ToolPermissionContext

    Args:
        cwd: 当前工作目录
        mode: 权限模式（默认为 DEFAULT）

    Returns:
        ToolPermissionContext 对象
    """
    from codo.types.permissions import PermissionRuleSource

    try:
        # 尝试从磁盘加载权限规则并构建上下文
        from codo.services.tools.permissions_loader import build_permission_context_from_disk
        context = build_permission_context_from_disk(cwd)
        # 覆盖模式（调用方可能指定了不同的模式）
        context.mode = mode
        return context
    except Exception as e:
        # 加载失败时回退到空规则，确保系统仍可正常运行
        logger.warning(f"从磁盘加载权限规则失败，使用空规则: {e}")
        return ToolPermissionContext(
            mode=mode,
            always_allow_rules={
                PermissionRuleSource.PROJECT_SETTINGS: [],
                PermissionRuleSource.USER_SETTINGS: [],
                PermissionRuleSource.SESSION: [],
            },
            always_deny_rules={
                PermissionRuleSource.PROJECT_SETTINGS: [],
                PermissionRuleSource.USER_SETTINGS: [],
                PermissionRuleSource.SESSION: [],
            },
            always_ask_rules={
                PermissionRuleSource.PROJECT_SETTINGS: [],
                PermissionRuleSource.USER_SETTINGS: [],
                PermissionRuleSource.SESSION: [],
            },
            cwd=cwd,
            is_bypass_permissions_mode_available=True,
        )

def permission_decision_to_string(decision: PermissionDecision) -> str:
    """
    将权限决策转换为字符串（用于日志）

    Args:
        decision: 权限决策

    Returns:
        决策字符串
    """
    # 根据决策类型返回对应的字符串表示
    if isinstance(decision, PermissionAllowDecision):
        return f"允许 (原因: {decision.decision_reason.type if decision.decision_reason else '无'})"
    elif isinstance(decision, PermissionAskDecision):
        return f"询问 (消息: {decision.message})"
    elif isinstance(decision, PermissionDenyDecision):
        return f"拒绝 (消息: {decision.message})"
    return "未知"
