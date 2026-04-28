"""
权限规则加载器

[Workflow]
1. 从 settings.json 文件加载权限规则
2. 解析 permissions.allow / permissions.deny / permissions.ask 数组
3. 转换为 PermissionRule 对象列表
4. 支持从多个来源加载（项目设置、用户设置）
5. 支持添加和删除规则
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Any

from codo.types.permissions import (
    PermissionBehavior,
    PermissionRule,
    PermissionRuleSource,
    PermissionRuleValue,
    ToolPermissionContext,
)
from codo.services.tools.permission_rules import (
    parse_permission_rule_value,
    format_permission_rule_value,
)

# 模块级日志记录器
logger = logging.getLogger(__name__)

SUPPORTED_RULE_BEHAVIORS = ["allow", "deny", "ask"]

def _get_settings_file_path(source: PermissionRuleSource, cwd: str = "") -> Optional[str]:
    """
    获取设置文件路径

    [Workflow]
    1. 根据来源确定文件路径
    2. PROJECT_SETTINGS → {cwd}/.codo/settings.json
    3. USER_SETTINGS → ~/.codo/settings.json
    4. SESSION → 无文件（运行时内存）

    Args:
        source: 规则来源
        cwd: 当前工作目录（PROJECT_SETTINGS 需要）

    Returns:
        设置文件路径，或 None（SESSION 来源）
    """
    if source == PermissionRuleSource.PROJECT_SETTINGS:
        # 项目级设置：{cwd}/.codo/settings.json
        if cwd:
            return os.path.join(cwd, ".codo", "settings.json")
        # 没有 cwd 时无法确定项目设置路径
        return None
    elif source == PermissionRuleSource.USER_SETTINGS:
        # 用户级设置：~/.codo/settings.json
        return os.path.join(str(Path.home()), ".codo", "settings.json")
    else:
        # SESSION 来源没有文件，规则存储在运行时内存中
        return None

def _load_settings_json(file_path: str) -> Optional[Dict[str, Any]]:
    """
    加载 settings.json 文件

    [Workflow]
    1. 检查文件是否存在
    2. 读取文件内容
    3. 解析 JSON
    4. 返回解析后的字典，或 None

    Args:
        file_path: 设置文件路径

    Returns:
        解析后的字典，或 None
    """
    # 检查文件是否存在，不存在则返回 None
    if not os.path.exists(file_path):
        return None

    try:
        # 读取文件内容，使用 utf-8 编码
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()

        if not content:
            return {}

        # 解析 JSON 字符串为字典
        data = json.loads(content)

        # 确保返回的是字典类型，非字典类型视为无效
        if isinstance(data, dict):
            return data
        return None

    except (json.JSONDecodeError, OSError) as e:
        # JSON 解析失败或文件读取失败，记录警告并返回 None
        logger.warning(f"加载设置文件失败 {file_path}: {e}")
        return None

def _settings_json_to_rules(
    data: Optional[Dict[str, Any]],
    source: PermissionRuleSource,
) -> List[PermissionRule]:
    """
    将 settings.json 中的 permissions 字段转换为规则列表

    [Workflow]
    1. 检查 data 和 data.permissions 是否存在
    2. 遍历 allow/deny/ask 三种行为
    3. 解析每个规则字符串为 PermissionRule
    4. 返回规则列表

    Args:
        data: settings.json 解析后的字典
        source: 规则来源

    Returns:
        PermissionRule 列表
    """
    # 检查数据有效性：data 为空或不包含 permissions 字段
    if not data or "permissions" not in data:
        return []

    # 获取 permissions 字段
    permissions = data["permissions"]
    # permissions 必须是字典类型
    if not isinstance(permissions, dict):
        return []

    # 初始化规则列表
    rules: List[PermissionRule] = []

    for behavior in SUPPORTED_RULE_BEHAVIORS:
        # 获取该行为对应的规则数组
        behavior_array = permissions.get(behavior)
        # 跳过非列表类型的值
        if not isinstance(behavior_array, list):
            continue

        # 解析每个规则字符串
        for rule_string in behavior_array:
            # 跳过非字符串类型的值
            if not isinstance(rule_string, str):
                continue
            try:
                # 将规则字符串解析为 PermissionRuleValue 对象
                rule_value = parse_permission_rule_value(rule_string)
                # 创建完整的 PermissionRule 并添加到列表
                rules.append(PermissionRule(
                    source=source,
                    rule_behavior=behavior,
                    rule_value=rule_value,
                ))
            except ValueError:
                # 忽略无效的规则字符串，记录调试日志
                logger.debug(f"忽略无效的权限规则: {rule_string}")
                continue

    return rules

def load_permission_rules_from_source(
    source: PermissionRuleSource,
    cwd: str = "",
) -> List[PermissionRule]:
    """
    从指定来源加载权限规则

    [Workflow]
    1. 获取设置文件路径
    2. 加载 settings.json
    3. 解析权限规则
    4. 返回规则列表

    Args:
        source: 规则来源
        cwd: 当前工作目录

    Returns:
        PermissionRule 列表
    """
    # 获取该来源对应的文件路径
    file_path = _get_settings_file_path(source, cwd)
    # 没有文件路径（如 SESSION 来源）则返回空列表
    if not file_path:
        return []

    # 加载设置文件内容
    data = _load_settings_json(file_path)

    # 将 JSON 数据解析为规则列表并返回
    return _settings_json_to_rules(data, source)

def load_all_permission_rules(cwd: str = "") -> List[PermissionRule]:
    """
    从所有来源加载权限规则

    [Workflow]
    1. 从项目设置加载规则
    2. 从用户设置加载规则
    3. 合并并返回

    Args:
        cwd: 当前工作目录

    Returns:
        所有来源的 PermissionRule 列表
    """
    # 初始化规则列表
    rules: List[PermissionRule] = []

    # 从项目设置加载（优先级最高）
    rules.extend(load_permission_rules_from_source(
        PermissionRuleSource.PROJECT_SETTINGS, cwd
    ))

    # 从用户设置加载
    rules.extend(load_permission_rules_from_source(
        PermissionRuleSource.USER_SETTINGS, cwd
    ))

    # SESSION 来源的规则不从磁盘加载，由运行时管理
    return rules

def build_permission_context_from_disk(
    cwd: str,
) -> ToolPermissionContext:
    """
    从磁盘加载权限规则并构建 ToolPermissionContext

    [Workflow]
    1. 加载所有权限规则
    2. 按行为分组（allow/deny/ask）
    3. 按来源分组
    4. 构建 ToolPermissionContext

    Args:
        cwd: 当前工作目录

    Returns:
        ToolPermissionContext 对象
    """
    from codo.types.permissions import PermissionMode

    # 从磁盘加载所有规则
    all_rules = load_all_permission_rules(cwd)

    # 初始化三种行为的规则字典，每种行为按来源分组
    allow_rules: Dict[PermissionRuleSource, List[str]] = {
        PermissionRuleSource.PROJECT_SETTINGS: [],
        PermissionRuleSource.USER_SETTINGS: [],
        PermissionRuleSource.SESSION: [],  # SESSION 初始为空，运行时填充
    }
    deny_rules: Dict[PermissionRuleSource, List[str]] = {
        PermissionRuleSource.PROJECT_SETTINGS: [],
        PermissionRuleSource.USER_SETTINGS: [],
        PermissionRuleSource.SESSION: [],
    }
    ask_rules: Dict[PermissionRuleSource, List[str]] = {
        PermissionRuleSource.PROJECT_SETTINGS: [],
        PermissionRuleSource.USER_SETTINGS: [],
        PermissionRuleSource.SESSION: [],
    }

    # 遍历所有规则，按行为和来源分组
    for rule in all_rules:
        # 将规则值格式化为字符串（如 "Bash" 或 "Bash(prefix:npm)"）
        rule_string = format_permission_rule_value(rule.rule_value)
        # 根据行为类型分配到对应的字典
        if rule.rule_behavior == "allow":
            allow_rules[rule.source].append(rule_string)
        elif rule.rule_behavior == "deny":
            deny_rules[rule.source].append(rule_string)
        elif rule.rule_behavior == "ask":
            ask_rules[rule.source].append(rule_string)

    # 构建并返回 ToolPermissionContext 对象
    return ToolPermissionContext(
        mode=PermissionMode.DEFAULT,
        always_allow_rules=allow_rules,
        always_deny_rules=deny_rules,
        always_ask_rules=ask_rules,
        cwd=cwd,
        is_bypass_permissions_mode_available=True,
    )

def add_permission_rules_to_settings(
    rule_values: List[PermissionRuleValue],
    rule_behavior: PermissionBehavior,
    source: PermissionRuleSource,
    cwd: str = "",
) -> bool:
    """
    将权限规则添加到设置文件

    [Workflow]
    1. 获取设置文件路径
    2. 加载现有设置
    3. 添加新规则（去重）
    4. 写回文件

    Args:
        rule_values: 要添加的规则值列表
        rule_behavior: 规则行为（allow/deny/ask）
        source: 规则来源
        cwd: 当前工作目录

    Returns:
        是否成功
    """
    # 空规则列表直接返回成功
    if not rule_values:
        return True

    # 获取设置文件路径
    file_path = _get_settings_file_path(source, cwd)
    # 无法获取路径（如 SESSION 来源）则返回失败
    if not file_path:
        logger.warning(f"无法获取设置文件路径: source={source}")
        return False

    try:
        # 加载现有设置，如果文件不存在则使用空字典
        data = _load_settings_json(file_path) or {}

        # 确保 permissions 字段存在
        if "permissions" not in data:
            data["permissions"] = {}

        # 获取 permissions 对象
        permissions = data["permissions"]

        # 获取该行为对应的现有规则列表
        existing_rules = permissions.get(rule_behavior, [])

        # 将新规则值格式化为字符串
        new_rule_strings = [
            format_permission_rule_value(rv) for rv in rule_values
        ]

        # 去重：构建现有规则集合，只添加不存在的规则
        existing_set = set(existing_rules)
        rules_to_add = [r for r in new_rule_strings if r not in existing_set]

        # 如果没有新规则需要添加，直接返回成功
        if not rules_to_add:
            return True

        # 将新规则追加到现有列表
        permissions[rule_behavior] = existing_rules + rules_to_add
        data["permissions"] = permissions

        # 确保目标目录存在（如 .codo/ 目录）
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        # 将更新后的设置写回文件
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        logger.info(f"已添加 {len(rules_to_add)} 条权限规则到 {file_path}")
        return True

    except Exception as e:
        # 捕获所有异常，记录错误并返回失败
        logger.error(f"添加权限规则失败: {e}")
        return False

def delete_permission_rule_from_settings(
    rule: PermissionRule,
    cwd: str = "",
) -> bool:
    """
    从设置文件中删除权限规则

    [Workflow]
    1. 获取设置文件路径
    2. 加载现有设置
    3. 查找并删除规则
    4. 写回文件

    Args:
        rule: 要删除的规则
        cwd: 当前工作目录

    Returns:
        是否成功
    """
    # 获取该规则来源对应的文件路径
    file_path = _get_settings_file_path(rule.source, cwd)
    # 无法获取路径则返回失败
    if not file_path:
        return False

    try:
        # 加载现有设置
        data = _load_settings_json(file_path)
        # 设置文件不存在或没有 permissions 字段
        if not data or "permissions" not in data:
            return False

        # 获取 permissions 对象
        permissions = data["permissions"]
        # 获取该行为对应的规则数组
        behavior_array = permissions.get(rule.rule_behavior)
        # 规则数组不存在或为空
        if not behavior_array:
            return False

        # 将要删除的规则格式化为字符串
        rule_string = format_permission_rule_value(rule.rule_value)

        # 检查规则是否存在于数组中
        if rule_string not in behavior_array:
            return False

        # 从数组中移除该规则
        behavior_array.remove(rule_string)
        # 更新 permissions 对象
        permissions[rule.rule_behavior] = behavior_array
        data["permissions"] = permissions

        # 将更新后的设置写回文件
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        logger.info(f"已从 {file_path} 删除权限规则: {rule_string}")
        return True

    except Exception as e:
        # 捕获所有异常，记录错误并返回失败
        logger.error(f"删除权限规则失败: {e}")
        return False
