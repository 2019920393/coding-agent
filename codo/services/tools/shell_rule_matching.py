"""
Shell 规则匹配工具

[Workflow]
1. 解析权限规则字符串为 exact/prefix/wildcard 三种类型
2. 支持 legacy :* 前缀语法（向后兼容）
3. 支持通配符模式匹配（* 匹配任意字符序列）
4. 支持转义序列（\\* 匹配字面量星号，\\\\ 匹配字面量反斜杠）
5. 提供权限建议生成函数
"""

import re
import logging
from typing import Dict, Optional, List, Any, Union

logger = logging.getLogger(__name__)

# ============================================================================
# 模块级常量（Null-byte 哨兵占位符）
# ============================================================================

# 转义星号的占位符，使用 null-byte 包裹确保不会与正常文本冲突

ESCAPED_STAR_PLACEHOLDER = '\x00ESCAPED_STAR\x00'

# 转义反斜杠的占位符

ESCAPED_BACKSLASH_PLACEHOLDER = '\x00ESCAPED_BACKSLASH\x00'

# 预编译占位符的正则表达式，避免每次匹配时重新编译
ESCAPED_STAR_PLACEHOLDER_RE = re.compile(re.escape(ESCAPED_STAR_PLACEHOLDER))
ESCAPED_BACKSLASH_PLACEHOLDER_RE = re.compile(re.escape(ESCAPED_BACKSLASH_PLACEHOLDER))

# ============================================================================
# ShellPermissionRule 类型定义
# ============================================================================

# 使用 TypedDict 风格的字典表示解析后的规则

# type: "exact" | "prefix" | "wildcard"
# exact: {"type": "exact", "command": str}
# prefix: {"type": "prefix", "prefix": str}
# wildcard: {"type": "wildcard", "pattern": str}
ShellPermissionRule = Dict[str, str]

# ============================================================================
# 前缀提取（Legacy :* 语法）
# ============================================================================

def permission_rule_extract_prefix(permission_rule: str) -> Optional[str]:
    """
    从 legacy :* 语法中提取前缀

    [Workflow]
    1. 使用正则匹配 "xxx:*" 格式
    2. 如果匹配，返回 "xxx" 部分
    3. 否则返回 None

    示例：
    - "npm:*" → "npm"
    - "git:*" → "git"
    - "npm" → None
    - "git *" → None（这是通配符，不是 legacy 语法）

    Args:
        permission_rule: 权限规则字符串

    Returns:
        前缀字符串，或 None
    """
    # 匹配 "任意字符:*" 格式，:* 必须在末尾
    match = re.match(r'^(.+):\*$', permission_rule)
    # 如果匹配成功，返回捕获组（前缀部分）
    if match:
        return match.group(1)
    # 不匹配则返回 None
    return None

# ============================================================================
# 通配符检测
# ============================================================================

def has_wildcards(pattern: str) -> bool:
    """
    检查模式是否包含未转义的通配符（不包括 legacy :* 语法）

    [Workflow]
    1. 如果以 :* 结尾，视为 legacy 前缀语法，返回 False
    2. 遍历每个字符，查找未转义的 *
    3. 通过计算前面的反斜杠数量判断是否转义
    4. 偶数个反斜杠（包括 0）= 未转义 → 返回 True

    Args:
        pattern: 模式字符串

    Returns:
        是否包含未转义的通配符
    """
    # 如果以 :* 结尾，是 legacy 前缀语法，不是通配符
    if pattern.endswith(':*'):
        return False

    # 遍历每个字符，查找未转义的 *
    for i in range(len(pattern)):
        if pattern[i] == '*':
            # 计算该星号前面连续的反斜杠数量
            backslash_count = 0
            j = i - 1
            # 向前遍历，统计连续反斜杠
            while j >= 0 and pattern[j] == '\\':
                backslash_count += 1
                j -= 1
            # 偶数个反斜杠（包括 0 个）意味着星号未被转义
            if backslash_count % 2 == 0:
                return True

    # 没有找到未转义的通配符
    return False

# ============================================================================
# 通配符模式匹配
# ============================================================================

def match_wildcard_pattern(
    pattern: str,
    command: str,
    case_insensitive: bool = False,
) -> bool:
    """
    将命令与通配符模式进行匹配

    [Workflow]
    1. 去除模式首尾空白
    2. 处理转义序列：\\* → 占位符，\\\\ → 占位符
    3. 转义正则特殊字符（除 * 外）
    4. 将未转义的 * 转换为 .* 正则通配符
    5. 将占位符还原为转义后的正则字面量
    6. 特殊处理：尾部 " *" 模式使空格和参数可选
    7. 编译正则并执行全字符串匹配

    转义规则：
    - \\* → 匹配字面量星号字符
    - \\\\ → 匹配字面量反斜杠字符
    - * → 匹配任意字符序列（包括空）

    特殊行为：
    - "git *" 匹配 "git push" 也匹配 "git"（尾部空格+通配符可选）
    - 仅当模式中只有一个未转义的 * 且在尾部时才启用此行为

    Args:
        pattern: 通配符模式
        command: 要匹配的命令
        case_insensitive: 是否忽略大小写

    Returns:
        命令是否匹配模式
    """
    # 去除模式首尾空白
    trimmed_pattern = pattern.strip()

    # 处理转义序列：将 \* 和 \\ 替换为占位符
    processed = ''
    i = 0

    while i < len(trimmed_pattern):
        char = trimmed_pattern[i]

        # 检测转义序列
        if char == '\\' and i + 1 < len(trimmed_pattern):
            next_char = trimmed_pattern[i + 1]
            if next_char == '*':
                # \* → 字面量星号占位符
                processed += ESCAPED_STAR_PLACEHOLDER
                i += 2
                continue
            elif next_char == '\\':
                # \\ → 字面量反斜杠占位符
                processed += ESCAPED_BACKSLASH_PLACEHOLDER
                i += 2
                continue

        # 非转义字符直接追加
        processed += char
        i += 1

    # 转义正则特殊字符（除 * 外），防止模式中的特殊字符被正则引擎解释

    escaped = re.sub(r'([.+?^${}()|[\]\\\'"])', r'\\\1', processed)

    # 将未转义的 * 转换为 .* 正则通配符
    with_wildcards = escaped.replace('*', '.*')

    # 将占位符还原为正则转义后的字面量
    regex_pattern = ESCAPED_STAR_PLACEHOLDER_RE.sub(r'\\*', with_wildcards)
    regex_pattern = ESCAPED_BACKSLASH_PLACEHOLDER_RE.sub(r'\\\\', regex_pattern)

    # 特殊处理：当模式以 " .*" 结尾且只有一个未转义的 * 时，
    # 使尾部的空格和参数可选，这样 "git *" 既匹配 "git push" 也匹配 "git"

    unescaped_star_count = processed.count('*')
    if regex_pattern.endswith(' .*') and unescaped_star_count == 1:
        # 将 " .*" 替换为 "( .*)?"，使尾部空格和参数可选
        regex_pattern = regex_pattern[:-3] + '( .*)?'

    # 构建正则标志

    flags = re.DOTALL
    if case_insensitive:
        flags |= re.IGNORECASE

    # 编译正则表达式，匹配整个字符串
    try:
        regex = re.compile(f'^{regex_pattern}$', flags)
    except re.error as e:
        # 正则编译失败，记录警告并返回 False
        logger.warning(f"通配符模式编译失败: pattern={pattern}, error={e}")
        return False

    # 执行匹配
    return bool(regex.match(command))

# ============================================================================
# 规则解析
# ============================================================================

def parse_permission_rule(permission_rule: str) -> ShellPermissionRule:
    """
    将权限规则字符串解析为结构化的规则对象

    [Workflow]
    1. 首先检查 legacy :* 前缀语法（向后兼容）
    2. 然后检查新的通配符语法（包含 * 但不是 :* 结尾）
    3. 否则视为精确匹配

    解析优先级：
    1. "npm:*" → prefix 类型（legacy 语法）
    2. "git *" → wildcard 类型（通配符语法）
    3. "ls -la" → exact 类型（精确匹配）

    Args:
        permission_rule: 权限规则字符串

    Returns:
        解析后的规则字典，包含 type 和对应的值
    """
    # 首先检查 legacy :* 前缀语法（向后兼容）
    prefix = permission_rule_extract_prefix(permission_rule)
    if prefix is not None:
        return {
            "type": "prefix",
            "prefix": prefix,
        }

    # 检查新的通配符语法（包含未转义的 *）
    if has_wildcards(permission_rule):
        return {
            "type": "wildcard",
            "pattern": permission_rule,
        }

    # 默认为精确匹配
    return {
        "type": "exact",
        "command": permission_rule,
    }

# ============================================================================
# 权限建议生成
# ============================================================================

def suggestion_for_exact_command(
    tool_name: str,
    command: str,
) -> List[Dict[str, Any]]:
    """
    为精确命令匹配生成权限更新建议

    [Workflow]
    1. 创建 addRules 类型的权限更新
    2. 规则内容为完整命令字符串
    3. 行为为 allow
    4. 目标为 localSettings

    Args:
        tool_name: 工具名称
        command: 命令字符串

    Returns:
        权限更新建议列表
    """
    return [
        {
            "type": "addRules",
            "rules": [
                {
                    "toolName": tool_name,
                    "ruleContent": command,
                },
            ],
            "behavior": "allow",
            "destination": "localSettings",
        },
    ]

def suggestion_for_prefix(
    tool_name: str,
    prefix: str,
) -> List[Dict[str, Any]]:
    """
    为前缀匹配生成权限更新建议

    [Workflow]
    1. 创建 addRules 类型的权限更新
    2. 规则内容为 "prefix:*" 格式
    3. 行为为 allow
    4. 目标为 localSettings

    Args:
        tool_name: 工具名称
        prefix: 命令前缀

    Returns:
        权限更新建议列表
    """
    return [
        {
            "type": "addRules",
            "rules": [
                {
                    "toolName": tool_name,
                    "ruleContent": f"{prefix}:*",
                },
            ],
            "behavior": "allow",
            "destination": "localSettings",
        },
    ]
