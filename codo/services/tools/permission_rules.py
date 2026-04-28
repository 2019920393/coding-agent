"""
权限规则管理

[简化说明]
简化为个人使用场景，移除复杂的 MCP 规则匹配。
"""

from typing import Any, List, Dict, Optional
import re

from codo.types.permissions import (
    PermissionRule,
    PermissionRuleSource,
    PermissionRuleValue,
    PermissionBehavior,
    ToolPermissionContext,
)
from codo.tools.base import Tool

# ============================================================================
# 规则解析（Rule Parsing）
# ============================================================================

def parse_permission_rule_value(rule_string: str) -> PermissionRuleValue:
    """
    解析权限规则字符串为 PermissionRuleValue

    参考：src/utils/permissions/permissionRuleParser.ts:permissionRuleValueFromString

    [Workflow]
    1. 检查规则字符串格式
    2. 如果包含括号，提取工具名称和规则内容
    3. 否则，只提取工具名称
    4. 返回 PermissionRuleValue 对象

    格式示例：
    - "Bash" → PermissionRuleValue(tool_name="Bash", rule_content=None)
    - "Bash(prefix:npm)" → PermissionRuleValue(tool_name="Bash", rule_content="prefix:npm")
    - "Write" → PermissionRuleValue(tool_name="Write", rule_content=None)

    Args:
        rule_string: 规则字符串

    Returns:
        PermissionRuleValue 对象
    """
    # 匹配格式：ToolName 或 ToolName(content)
    match = re.match(r'^([^(]+)(?:\(([^)]+)\))?$', rule_string.strip())

    # 如果格式不匹配，抛出异常
    if not match:
        raise ValueError(f"无效的权限规则格式: {rule_string}")

    # 提取工具名称
    tool_name = match.group(1).strip()
    # 提取规则内容（如果有）
    rule_content = match.group(2).strip() if match.group(2) else None

    # 返回规则值对象
    return PermissionRuleValue(
        tool_name=tool_name,
        rule_content=rule_content,
    )

def format_permission_rule_value(rule_value: PermissionRuleValue) -> str:
    """
    格式化 PermissionRuleValue 为字符串

    参考：src/utils/permissions/permissionRuleParser.ts:permissionRuleValueToString

    [Workflow]
    1. 如果有 rule_content，返回 "ToolName(content)"
    2. 否则，返回 "ToolName"

    Args:
        rule_value: PermissionRuleValue 对象

    Returns:
        规则字符串
    """
    # 如果有规则内容，格式化为 "ToolName(content)"
    if rule_value.rule_content:
        return f"{rule_value.tool_name}({rule_value.rule_content})"
    # 否则只返回工具名称
    return rule_value.tool_name

# ============================================================================
# 规则匹配（Rule Matching）
# ============================================================================

def tool_matches_rule(tool: Tool, rule: PermissionRule) -> bool:
    """
    检查工具是否匹配规则

    参考：src/utils/permissions/permissions.ts:238-269 (toolMatchesRule)

    [Workflow]
    1. 规则必须没有内容才能匹配整个工具
    2. 检查工具名称是否匹配规则的工具名称
    3. 返回匹配结果

    注意：
    - 规则 "Bash" 匹配工具 "Bash"
    - 规则 "Bash(prefix:npm)" 不匹配工具 "Bash"（因为有内容）
    - 简化版不支持 MCP 工具匹配

    Args:
        tool: Tool 对象
        rule: PermissionRule 对象

    Returns:
        是否匹配
    """
    # 规则必须没有内容才能匹配整个工具
    if rule.rule_value.rule_content is not None:
        return False

    # 直接工具名称匹配
    return rule.rule_value.tool_name == tool.name

# ============================================================================
# 规则查询（Rule Queries）
# ============================================================================

def get_all_rules_by_behavior(
    context: ToolPermissionContext,
    behavior: PermissionBehavior,
) -> List[PermissionRule]:
    """
    获取指定行为的所有规则

    参考：src/utils/permissions/permissions.ts:122-132 (getAllowRules)

    [Workflow]
    1. 根据 behavior 选择规则字典
    2. 遍历所有规则来源
    3. 解析规则字符串为 PermissionRule 对象
    4. 返回规则列表

    Args:
        context: 工具权限上下文
        behavior: 权限行为（allow/deny/ask）

    Returns:
        PermissionRule 列表
    """
    # 根据行为选择对应的规则字典
    if behavior == "allow":
        rules_dict = context.always_allow_rules if hasattr(context, 'always_allow_rules') else context.get('always_allow_rules', {})
    elif behavior == "deny":
        rules_dict = context.always_deny_rules if hasattr(context, 'always_deny_rules') else context.get('always_deny_rules', {})
    elif behavior == "ask":
        rules_dict = context.always_ask_rules if hasattr(context, 'always_ask_rules') else context.get('always_ask_rules', {})
    else:
        # 未知行为，返回空列表
        return []

    # 遍历所有规则来源，按优先级排序（PROJECT > USER > SESSION）
    rules: List[PermissionRule] = []
    for source in [
        PermissionRuleSource.PROJECT_SETTINGS,
        PermissionRuleSource.USER_SETTINGS,
        PermissionRuleSource.SESSION,
    ]:
        # 获取该来源的规则字符串列表
        rule_strings = rules_dict.get(source, [])
        for rule_string in rule_strings:
            try:
                # 解析规则字符串为规则值对象
                rule_value = parse_permission_rule_value(rule_string)
                # 创建完整的规则对象并添加到列表
                rules.append(PermissionRule(
                    source=source,
                    rule_behavior=behavior,
                    rule_value=rule_value,
                ))
            except ValueError:
                # 忽略无效的规则字符串，继续处理下一个
                continue

    return rules

def get_allow_rules(context: ToolPermissionContext) -> List[PermissionRule]:
    """
    获取所有允许规则

    参考：src/utils/permissions/permissions.ts:122-132

    Args:
        context: 工具权限上下文

    Returns:
        允许规则列表
    """
    return get_all_rules_by_behavior(context, "allow")

def get_deny_rules(context: ToolPermissionContext) -> List[PermissionRule]:
    """
    获取所有拒绝规则

    参考：src/utils/permissions/permissions.ts:213-221

    Args:
        context: 工具权限上下文

    Returns:
        拒绝规则列表
    """
    return get_all_rules_by_behavior(context, "deny")

def get_ask_rules(context: ToolPermissionContext) -> List[PermissionRule]:
    """
    获取所有询问规则

    参考：src/utils/permissions/permissions.ts:223-231

    Args:
        context: 工具权限上下文

    Returns:
        询问规则列表
    """
    return get_all_rules_by_behavior(context, "ask")

def tool_always_allowed_rule(
    context: ToolPermissionContext,
    tool: Tool,
) -> Optional[PermissionRule]:
    """
    检查工具是否在允许规则列表中

    参考：src/utils/permissions/permissions.ts:275-282

    [Workflow]
    1. 获取所有允许规则
    2. 遍历规则，检查是否匹配工具
    3. 返回第一个匹配的规则，如果没有则返回 None

    Args:
        context: 工具权限上下文
        tool: Tool 对象

    Returns:
        匹配的 PermissionRule，如果没有则返回 None
    """
    # 获取所有允许规则
    allow_rules = get_allow_rules(context)
    # 遍历规则，查找匹配的规则
    for rule in allow_rules:
        if tool_matches_rule(tool, rule):
            # 找到匹配的规则，立即返回
            return rule
    # 没有找到匹配的规则
    return None

def get_deny_rule_for_tool(
    context: ToolPermissionContext,
    tool: Tool,
) -> Optional[PermissionRule]:
    """
    检查工具是否在拒绝规则列表中

    参考：src/utils/permissions/permissions.ts:287-292

    [Workflow]
    1. 获取所有拒绝规则
    2. 遍历规则，检查是否匹配工具
    3. 返回第一个匹配的规则，如果没有则返回 None

    Args:
        context: 工具权限上下文
        tool: Tool 对象

    Returns:
        匹配的 PermissionRule，如果没有则返回 None
    """
    # 获取所有拒绝规则
    deny_rules = get_deny_rules(context)
    # 遍历规则，查找匹配的规则
    for rule in deny_rules:
        if tool_matches_rule(tool, rule):
            # 找到匹配的规则，立即返回
            return rule
    # 没有找到匹配的规则
    return None

def get_ask_rule_for_tool(
    context: ToolPermissionContext,
    tool: Tool,
) -> Optional[PermissionRule]:
    """
    检查工具是否在询问规则列表中

    参考：src/utils/permissions/permissions.ts:297-302

    [Workflow]
    1. 获取所有询问规则
    2. 遍历规则，检查是否匹配工具
    3. 返回第一个匹配的规则，如果没有则返回 None

    Args:
        context: 工具权限上下文
        tool: Tool 对象

    Returns:
        匹配的 PermissionRule，如果没有则返回 None
    """
    # 获取所有询问规则
    ask_rules = get_ask_rules(context)
    # 遍历规则，查找匹配的规则
    for rule in ask_rules:
        if tool_matches_rule(tool, rule):
            # 找到匹配的规则，立即返回
            return rule
    # 没有找到匹配的规则
    return None

def get_rule_by_contents_for_tool(
    context: ToolPermissionContext,
    tool: Tool,
    behavior: PermissionBehavior,
) -> Dict[str, PermissionRule]:
    """
    获取工具的内容特定规则映射

    参考：src/utils/permissions/permissions.ts:349-390

    [Workflow]
    1. 根据 behavior 获取规则列表
    2. 遍历规则，筛选出匹配工具名称且有内容的规则
    3. 构建 {rule_content: rule} 映射
    4. 返回映射字典

    用途：
    - 用于工具特定的权限检查
    - 例如：Bash(prefix:npm) 规则会被映射为 {"prefix:npm": rule}

    Args:
        context: 工具权限上下文
        tool: Tool 对象
        behavior: 权限行为

    Returns:
        {rule_content: PermissionRule} 映射
    """
    rules = get_all_rules_by_behavior(context, behavior)
    rule_by_contents: Dict[str, PermissionRule] = {}

    for rule in rules:
        # 只处理匹配工具名称且有内容的规则
        if (rule.rule_value.tool_name == tool.name and
            rule.rule_value.rule_content is not None and
            rule.rule_behavior == behavior):
            rule_by_contents[rule.rule_value.rule_content] = rule

    return rule_by_contents

# ============================================================================
# Shell 规则内容匹配（Shell Rule Content Matching）
# ============================================================================

def matches_rule_content(
    tool_name: str,
    rule_content: str,
    input_data: Dict[str, Any],
) -> bool:
    """
    检查工具输入是否匹配规则内容

    [Workflow]
    1. 对于 Bash/PowerShell 工具，使用 shell 规则匹配（exact/prefix/wildcard）
    2. 解析规则内容为结构化规则对象
    3. 根据规则类型执行对应的匹配逻辑
    4. 对于其他工具，使用简单字符串匹配

    Args:
        tool_name: 工具名称
        rule_content: 规则内容字符串（如 "npm install" 或 "git *"）
        input_data: 工具输入数据

    Returns:
        是否匹配
    """
    # 延迟导入，避免循环依赖
    from codo.services.tools.shell_rule_matching import (
        parse_permission_rule,
        match_wildcard_pattern,
    )

    # Bash/PowerShell 工具使用 shell 规则匹配
    if tool_name in ("Bash", "PowerShell"):
        # 从输入数据中获取命令字符串
        command = input_data.get("command", "")

        # 将规则内容解析为结构化规则对象
        parsed = parse_permission_rule(rule_content)

        if parsed["type"] == "exact":
            # 精确匹配：命令必须完全等于规则中的命令
            return command == parsed["command"]
        elif parsed["type"] == "prefix":
            # 前缀匹配：命令以前缀开头（后跟空格）或完全等于前缀
            return (
                command.startswith(parsed["prefix"] + " ")
                or command == parsed["prefix"]
            )
        elif parsed["type"] == "wildcard":
            # 通配符匹配：使用正则表达式进行模式匹配
            return match_wildcard_pattern(parsed["pattern"], command)

    # 其他工具：暂不支持内容级匹配，返回 False
    return False

# ============================================================================
# 权限消息生成（Permission Messages）
# ============================================================================

def create_permission_request_message(
    tool_name: str,
    reason: Optional[str] = None,
) -> str:
    """
    创建权限请求消息

    参考：src/utils/permissions/permissions.ts:137-211

    [Workflow]
    1. 如果有 reason，返回带原因的消息
    2. 否则，返回默认消息

    Args:
        tool_name: 工具名称
        reason: 决策原因（可选）

    Returns:
        权限请求消息
    """
    # 如果提供了原因，返回带原因的消息
    if reason:
        return f"需要 {tool_name} 权限: {reason}"
    # 否则返回默认消息
    return f"当前会话请求使用 {tool_name}，但您尚未授予权限。"

def get_permission_rule_source_display_name(source: PermissionRuleSource) -> str:
    """
    获取权限规则来源的显示名称

    参考：src/utils/permissions/permissions.ts:116-120

    Args:
        source: 权限规则来源

    Returns:
        显示名称
    """
    # 根据来源返回对应的显示名称
    if source == PermissionRuleSource.PROJECT_SETTINGS:
        return "项目设置"
    elif source == PermissionRuleSource.USER_SETTINGS:
        return "用户设置"
    elif source == PermissionRuleSource.SESSION:
        return "会话"
    else:
        return "未知来源"
