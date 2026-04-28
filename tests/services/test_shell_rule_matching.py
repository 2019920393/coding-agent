"""
Shell 规则匹配测试

测试 shell_rule_matching.py 中的所有核心功能：
- permission_rule_extract_prefix: legacy :* 前缀提取
- has_wildcards: 通配符检测
- match_wildcard_pattern: 通配符模式匹配
- parse_permission_rule: 规则解析
- matches_rule_content: 集成到 permission_rules 的内容匹配
"""

import pytest

from codo.services.tools.shell_rule_matching import (
    permission_rule_extract_prefix,
    has_wildcards,
    match_wildcard_pattern,
    parse_permission_rule,
    suggestion_for_exact_command,
    suggestion_for_prefix,
)
from codo.services.tools.permission_rules import matches_rule_content

# ============================================================================
# permission_rule_extract_prefix 测试
# ============================================================================

class TestPermissionRuleExtractPrefix:
    """测试 legacy :* 前缀提取"""

    def test_npm_prefix(self):
        """npm:* → npm"""
        assert permission_rule_extract_prefix("npm:*") == "npm"

    def test_git_prefix(self):
        """git:* → git"""
        assert permission_rule_extract_prefix("git:*") == "git"

    def test_complex_prefix(self):
        """npm install:* → npm install"""
        assert permission_rule_extract_prefix("npm install:*") == "npm install"

    def test_no_prefix_plain(self):
        """npm → None"""
        assert permission_rule_extract_prefix("npm") is None

    def test_no_prefix_wildcard(self):
        """git * → None（这是通配符，不是 legacy 语法）"""
        assert permission_rule_extract_prefix("git *") is None

    def test_no_prefix_star_in_middle(self):
        """git * push → None"""
        assert permission_rule_extract_prefix("git * push") is None

    def test_empty_string(self):
        """空字符串 → None"""
        assert permission_rule_extract_prefix("") is None

# ============================================================================
# has_wildcards 测试
# ============================================================================

class TestHasWildcards:
    """测试通配符检测"""

    def test_simple_wildcard(self):
        """git * → True"""
        assert has_wildcards("git *") is True

    def test_star_at_end(self):
        """npm run * → True"""
        assert has_wildcards("npm run *") is True

    def test_star_in_middle(self):
        """git * push → True"""
        assert has_wildcards("git * push") is True

    def test_legacy_prefix_not_wildcard(self):
        """npm:* → False（legacy 语法）"""
        assert has_wildcards("npm:*") is False

    def test_no_star(self):
        """git push → False"""
        assert has_wildcards("git push") is False

    def test_escaped_star(self):
        r"""git \* → False（转义的星号）"""
        assert has_wildcards("git \\*") is False

    def test_escaped_backslash_then_star(self):
        r"""git \\* → True（转义的反斜杠后跟未转义的星号）"""
        assert has_wildcards("git \\\\*") is True

    def test_empty_string(self):
        """空字符串 → False"""
        assert has_wildcards("") is False

    def test_just_star(self):
        """* → True"""
        assert has_wildcards("*") is True

# ============================================================================
# match_wildcard_pattern 测试
# ============================================================================

class TestMatchWildcardPattern:
    """测试通配符模式匹配"""

    def test_simple_wildcard(self):
        """git * 匹配 git push"""
        assert match_wildcard_pattern("git *", "git push") is True

    def test_wildcard_matches_base_command(self):
        """git * 也匹配 git（尾部空格+通配符可选）"""
        assert match_wildcard_pattern("git *", "git") is True

    def test_wildcard_matches_long_args(self):
        """git * 匹配 git push origin main"""
        assert match_wildcard_pattern("git *", "git push origin main") is True

    def test_wildcard_no_match(self):
        """git * 不匹配 npm install"""
        assert match_wildcard_pattern("git *", "npm install") is False

    def test_exact_match_no_wildcard(self):
        """ls -la 精确匹配"""
        assert match_wildcard_pattern("ls -la", "ls -la") is True

    def test_exact_no_match(self):
        """ls -la 不匹配 ls"""
        assert match_wildcard_pattern("ls -la", "ls") is False

    def test_escaped_star_literal(self):
        r"""echo \* 匹配 echo *（字面量星号）"""
        assert match_wildcard_pattern("echo \\*", "echo *") is True

    def test_escaped_star_no_wildcard(self):
        r"""echo \* 不匹配 echo hello"""
        assert match_wildcard_pattern("echo \\*", "echo hello") is False

    def test_escaped_backslash(self):
        r"""echo \\ 匹配 echo \（字面量反斜杠）"""
        assert match_wildcard_pattern("echo \\\\", "echo \\") is True

    def test_multiple_wildcards(self):
        """* run * 匹配 npm run build"""
        assert match_wildcard_pattern("* run *", "npm run build") is True

    def test_multiple_wildcards_no_optional_tail(self):
        """* run * 不匹配 npm run（多通配符不启用尾部可选）"""
        assert match_wildcard_pattern("* run *", "npm run") is False

    def test_case_sensitive_default(self):
        """默认区分大小写"""
        assert match_wildcard_pattern("Git *", "git push") is False

    def test_case_insensitive(self):
        """忽略大小写模式"""
        assert match_wildcard_pattern("Git *", "git push", case_insensitive=True) is True

    def test_pattern_with_special_chars(self):
        """包含正则特殊字符的模式"""
        assert match_wildcard_pattern("echo (hello)", "echo (hello)") is True

    def test_pattern_with_dots(self):
        """包含点号的模式"""
        assert match_wildcard_pattern("cat file.txt", "cat file.txt") is True
        assert match_wildcard_pattern("cat file.txt", "cat filextxt") is False

    def test_whitespace_trimmed(self):
        """模式首尾空白被去除"""
        assert match_wildcard_pattern("  git *  ", "git push") is True

    def test_star_only(self):
        """* 匹配任何命令"""
        assert match_wildcard_pattern("*", "anything goes here") is True

    def test_star_only_empty(self):
        """* 匹配空字符串"""
        assert match_wildcard_pattern("*", "") is True

# ============================================================================
# parse_permission_rule 测试
# ============================================================================

class TestParsePermissionRule:
    """测试规则解析"""

    def test_exact_match(self):
        """ls -la → exact 类型"""
        result = parse_permission_rule("ls -la")
        assert result["type"] == "exact"
        assert result["command"] == "ls -la"

    def test_legacy_prefix(self):
        """npm:* → prefix 类型"""
        result = parse_permission_rule("npm:*")
        assert result["type"] == "prefix"
        assert result["prefix"] == "npm"

    def test_wildcard(self):
        """git * → wildcard 类型"""
        result = parse_permission_rule("git *")
        assert result["type"] == "wildcard"
        assert result["pattern"] == "git *"

    def test_complex_prefix(self):
        """npm install:* → prefix 类型"""
        result = parse_permission_rule("npm install:*")
        assert result["type"] == "prefix"
        assert result["prefix"] == "npm install"

    def test_escaped_star_is_exact(self):
        r"""echo \* → exact 类型（转义的星号不是通配符）"""
        result = parse_permission_rule("echo \\*")
        assert result["type"] == "exact"
        assert result["command"] == "echo \\*"

# ============================================================================
# matches_rule_content 集成测试
# ============================================================================

class TestMatchesRuleContent:
    """测试 permission_rules.py 中的 matches_rule_content"""

    def test_bash_exact_match(self):
        """Bash 精确匹配"""
        assert matches_rule_content("Bash", "ls -la", {"command": "ls -la"}) is True

    def test_bash_exact_no_match(self):
        """Bash 精确不匹配"""
        assert matches_rule_content("Bash", "ls -la", {"command": "ls"}) is False

    def test_bash_prefix_match(self):
        """Bash legacy 前缀匹配"""
        assert matches_rule_content("Bash", "npm:*", {"command": "npm install"}) is True

    def test_bash_prefix_exact_match(self):
        """Bash legacy 前缀精确匹配"""
        assert matches_rule_content("Bash", "npm:*", {"command": "npm"}) is True

    def test_bash_prefix_no_match(self):
        """Bash legacy 前缀不匹配"""
        assert matches_rule_content("Bash", "npm:*", {"command": "git push"}) is False

    def test_bash_wildcard_match(self):
        """Bash 通配符匹配"""
        assert matches_rule_content("Bash", "git *", {"command": "git push"}) is True

    def test_bash_wildcard_base_match(self):
        """Bash 通配符匹配基础命令"""
        assert matches_rule_content("Bash", "git *", {"command": "git"}) is True

    def test_powershell_exact_match(self):
        """PowerShell 精确匹配"""
        assert matches_rule_content("PowerShell", "Get-Process", {"command": "Get-Process"}) is True

    def test_other_tool_returns_false(self):
        """非 Shell 工具返回 False"""
        assert matches_rule_content("Write", "some_content", {"file_path": "/tmp/test"}) is False

    def test_empty_command(self):
        """空命令"""
        assert matches_rule_content("Bash", "ls", {"command": ""}) is False

    def test_missing_command_key(self):
        """缺少 command 键"""
        assert matches_rule_content("Bash", "ls", {}) is False

# ============================================================================
# 权限建议生成测试
# ============================================================================

class TestSuggestions:
    """测试权限建议生成"""

    def test_suggestion_for_exact_command(self):
        """精确命令建议"""
        result = suggestion_for_exact_command("Bash", "ls -la")
        assert len(result) == 1
        assert result[0]["type"] == "addRules"
        assert result[0]["behavior"] == "allow"
        assert result[0]["rules"][0]["toolName"] == "Bash"
        assert result[0]["rules"][0]["ruleContent"] == "ls -la"

    def test_suggestion_for_prefix(self):
        """前缀建议"""
        result = suggestion_for_prefix("Bash", "npm")
        assert len(result) == 1
        assert result[0]["type"] == "addRules"
        assert result[0]["rules"][0]["ruleContent"] == "npm:*"

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
