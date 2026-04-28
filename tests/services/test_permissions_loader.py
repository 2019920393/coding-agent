"""
权限规则加载器测试

测试 permissions_loader.py 中的所有核心功能：
- _get_settings_file_path: 设置文件路径获取
- _load_settings_json: JSON 文件加载
- _settings_json_to_rules: JSON 到规则转换
- load_permission_rules_from_source: 单来源加载
- load_all_permission_rules: 全来源加载
- build_permission_context_from_disk: 构建权限上下文
- add_permission_rules_to_settings: 添加规则
- delete_permission_rule_from_settings: 删除规则
"""

import json
import os
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch

from codo.types.permissions import (
    PermissionMode,
    PermissionRule,
    PermissionRuleSource,
    PermissionRuleValue,
)
from codo.services.tools.permissions_loader import (
    _get_settings_file_path,
    _load_settings_json,
    _settings_json_to_rules,
    load_permission_rules_from_source,
    load_all_permission_rules,
    build_permission_context_from_disk,
    add_permission_rules_to_settings,
    delete_permission_rule_from_settings,
)

# ============================================================================
# _get_settings_file_path 测试
# ============================================================================

class TestGetSettingsFilePath:
    """测试设置文件路径获取"""

    def test_project_settings_with_cwd(self):
        """项目设置：{cwd}/.codo/settings.json"""
        result = _get_settings_file_path(PermissionRuleSource.PROJECT_SETTINGS, "/home/user/project")
        assert result == os.path.join("/home/user/project", ".codo", "settings.json")

    def test_project_settings_without_cwd(self):
        """项目设置无 cwd → None"""
        result = _get_settings_file_path(PermissionRuleSource.PROJECT_SETTINGS, "")
        assert result is None

    def test_user_settings(self):
        """用户设置：~/.codo/settings.json"""
        result = _get_settings_file_path(PermissionRuleSource.USER_SETTINGS)
        expected = os.path.join(str(Path.home()), ".codo", "settings.json")
        assert result == expected

    def test_session_returns_none(self):
        """SESSION 来源 → None"""
        result = _get_settings_file_path(PermissionRuleSource.SESSION)
        assert result is None

# ============================================================================
# _load_settings_json 测试
# ============================================================================

class TestLoadSettingsJson:
    """测试 JSON 文件加载"""

    def test_load_valid_json(self, tmp_path):
        """加载有效的 JSON 文件"""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text('{"permissions": {"allow": ["Bash"]}}', encoding='utf-8')
        result = _load_settings_json(str(settings_file))
        assert result == {"permissions": {"allow": ["Bash"]}}

    def test_load_empty_file(self, tmp_path):
        """加载空文件 → 空字典"""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text('', encoding='utf-8')
        result = _load_settings_json(str(settings_file))
        assert result == {}

    def test_load_nonexistent_file(self):
        """加载不存在的文件 → None"""
        result = _load_settings_json("/nonexistent/path/settings.json")
        assert result is None

    def test_load_invalid_json(self, tmp_path):
        """加载无效 JSON → None"""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text('{invalid json}', encoding='utf-8')
        result = _load_settings_json(str(settings_file))
        assert result is None

    def test_load_non_dict_json(self, tmp_path):
        """加载非字典 JSON → None"""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text('["not", "a", "dict"]', encoding='utf-8')
        result = _load_settings_json(str(settings_file))
        assert result is None

    def test_load_whitespace_only(self, tmp_path):
        """加载只有空白的文件 → 空字典"""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text('   \n  ', encoding='utf-8')
        result = _load_settings_json(str(settings_file))
        assert result == {}

# ============================================================================
# _settings_json_to_rules 测试
# ============================================================================

class TestSettingsJsonToRules:
    """测试 JSON 到规则转换"""

    def test_valid_permissions(self):
        """有效的 permissions 字段"""
        data = {
            "permissions": {
                "allow": ["Bash", "Read"],
                "deny": ["Write"],
                "ask": ["Bash(prefix:npm)"],
            }
        }
        rules = _settings_json_to_rules(data, PermissionRuleSource.PROJECT_SETTINGS)
        assert len(rules) == 4
        # 检查 allow 规则
        assert rules[0].rule_behavior == "allow"
        assert rules[0].rule_value.tool_name == "Bash"
        assert rules[1].rule_behavior == "allow"
        assert rules[1].rule_value.tool_name == "Read"
        # 检查 deny 规则
        assert rules[2].rule_behavior == "deny"
        assert rules[2].rule_value.tool_name == "Write"
        # 检查 ask 规则
        assert rules[3].rule_behavior == "ask"
        assert rules[3].rule_value.tool_name == "Bash"
        assert rules[3].rule_value.rule_content == "prefix:npm"

    def test_none_data(self):
        """None 数据 → 空列表"""
        rules = _settings_json_to_rules(None, PermissionRuleSource.USER_SETTINGS)
        assert rules == []

    def test_no_permissions_key(self):
        """没有 permissions 键 → 空列表"""
        rules = _settings_json_to_rules({"other": "data"}, PermissionRuleSource.USER_SETTINGS)
        assert rules == []

    def test_permissions_not_dict(self):
        """permissions 不是字典 → 空列表"""
        rules = _settings_json_to_rules({"permissions": "not_dict"}, PermissionRuleSource.USER_SETTINGS)
        assert rules == []

    def test_behavior_not_list(self):
        """行为值不是列表 → 跳过"""
        data = {"permissions": {"allow": "not_a_list"}}
        rules = _settings_json_to_rules(data, PermissionRuleSource.USER_SETTINGS)
        assert rules == []

    def test_non_string_rule(self):
        """非字符串规则 → 跳过"""
        data = {"permissions": {"allow": ["Bash", 123, None, "Read"]}}
        rules = _settings_json_to_rules(data, PermissionRuleSource.USER_SETTINGS)
        assert len(rules) == 2
        assert rules[0].rule_value.tool_name == "Bash"
        assert rules[1].rule_value.tool_name == "Read"

    def test_source_preserved(self):
        """来源信息被正确保留"""
        data = {"permissions": {"allow": ["Bash"]}}
        rules = _settings_json_to_rules(data, PermissionRuleSource.SESSION)
        assert rules[0].source == PermissionRuleSource.SESSION

# ============================================================================
# load_permission_rules_from_source 测试
# ============================================================================

class TestLoadPermissionRulesFromSource:
    """测试单来源加载"""

    def test_load_from_project(self, tmp_path):
        """从项目设置加载"""
        codo_dir = tmp_path / ".codo"
        codo_dir.mkdir()
        settings_file = codo_dir / "settings.json"
        settings_file.write_text(json.dumps({
            "permissions": {"allow": ["Bash", "Read"]}
        }), encoding='utf-8')

        rules = load_permission_rules_from_source(
            PermissionRuleSource.PROJECT_SETTINGS, str(tmp_path)
        )
        assert len(rules) == 2
        assert all(r.source == PermissionRuleSource.PROJECT_SETTINGS for r in rules)

    def test_load_session_returns_empty(self):
        """SESSION 来源返回空列表"""
        rules = load_permission_rules_from_source(PermissionRuleSource.SESSION)
        assert rules == []

    def test_load_nonexistent_project(self):
        """不存在的项目目录返回空列表"""
        rules = load_permission_rules_from_source(
            PermissionRuleSource.PROJECT_SETTINGS, "/nonexistent/path"
        )
        assert rules == []

# ============================================================================
# load_all_permission_rules 测试
# ============================================================================

class TestLoadAllPermissionRules:
    """测试全来源加载"""

    def test_merges_project_and_user(self, tmp_path):
        """合并项目和用户设置"""
        # 创建项目设置
        codo_dir = tmp_path / ".codo"
        codo_dir.mkdir()
        settings_file = codo_dir / "settings.json"
        settings_file.write_text(json.dumps({
            "permissions": {"allow": ["Bash"]}
        }), encoding='utf-8')

        # Mock 用户设置路径指向临时目录
        user_settings = tmp_path / "user_settings.json"
        user_settings.write_text(json.dumps({
            "permissions": {"deny": ["Write"]}
        }), encoding='utf-8')

        with patch(
            "codo.services.tools.permissions_loader._get_settings_file_path"
        ) as mock_path:
            def side_effect(source, cwd=""):
                if source == PermissionRuleSource.PROJECT_SETTINGS:
                    return str(settings_file)
                elif source == PermissionRuleSource.USER_SETTINGS:
                    return str(user_settings)
                return None

            mock_path.side_effect = side_effect
            rules = load_all_permission_rules(str(tmp_path))

        assert len(rules) == 2
        assert rules[0].rule_value.tool_name == "Bash"
        assert rules[0].source == PermissionRuleSource.PROJECT_SETTINGS
        assert rules[1].rule_value.tool_name == "Write"
        assert rules[1].source == PermissionRuleSource.USER_SETTINGS

# ============================================================================
# build_permission_context_from_disk 测试
# ============================================================================

class TestBuildPermissionContextFromDisk:
    """测试构建权限上下文"""

    def test_builds_context(self, tmp_path):
        """从磁盘构建完整上下文"""
        codo_dir = tmp_path / ".codo"
        codo_dir.mkdir()
        settings_file = codo_dir / "settings.json"
        settings_file.write_text(json.dumps({
            "permissions": {
                "allow": ["Bash", "Read"],
                "deny": ["Write"],
                "ask": ["Bash(prefix:npm)"],
            }
        }), encoding='utf-8')

        # Mock 用户设置为空
        with patch(
            "codo.services.tools.permissions_loader._get_settings_file_path"
        ) as mock_path:
            def side_effect(source, cwd=""):
                if source == PermissionRuleSource.PROJECT_SETTINGS:
                    return str(settings_file)
                return None

            mock_path.side_effect = side_effect
            context = build_permission_context_from_disk(str(tmp_path))

        assert context.mode == PermissionMode.DEFAULT
        assert "Bash" in context.always_allow_rules[PermissionRuleSource.PROJECT_SETTINGS]
        assert "Read" in context.always_allow_rules[PermissionRuleSource.PROJECT_SETTINGS]
        assert "Write" in context.always_deny_rules[PermissionRuleSource.PROJECT_SETTINGS]
        assert "Bash(prefix:npm)" in context.always_ask_rules[PermissionRuleSource.PROJECT_SETTINGS]
        # SESSION 初始为空
        assert context.always_allow_rules[PermissionRuleSource.SESSION] == []

    def test_empty_disk_returns_empty_context(self):
        """磁盘无设置文件时返回空上下文"""
        context = build_permission_context_from_disk("/nonexistent/path")
        assert context.mode == PermissionMode.DEFAULT
        assert context.always_allow_rules[PermissionRuleSource.PROJECT_SETTINGS] == []
        assert context.always_allow_rules[PermissionRuleSource.USER_SETTINGS] == []

# ============================================================================
# add_permission_rules_to_settings 测试
# ============================================================================

class TestAddPermissionRulesToSettings:
    """测试添加规则到设置文件"""

    def test_add_to_new_file(self, tmp_path):
        """添加规则到新文件"""
        with patch(
            "codo.services.tools.permissions_loader._get_settings_file_path",
            return_value=str(tmp_path / ".codo" / "settings.json"),
        ):
            result = add_permission_rules_to_settings(
                rule_values=[PermissionRuleValue(tool_name="Bash")],
                rule_behavior="allow",
                source=PermissionRuleSource.PROJECT_SETTINGS,
                cwd=str(tmp_path),
            )

        assert result is True
        # 验证文件内容
        with open(tmp_path / ".codo" / "settings.json", 'r') as f:
            data = json.load(f)
        assert "Bash" in data["permissions"]["allow"]

    def test_add_to_existing_file(self, tmp_path):
        """添加规则到已有文件"""
        codo_dir = tmp_path / ".codo"
        codo_dir.mkdir()
        settings_file = codo_dir / "settings.json"
        settings_file.write_text(json.dumps({
            "permissions": {"allow": ["Read"]}
        }), encoding='utf-8')

        with patch(
            "codo.services.tools.permissions_loader._get_settings_file_path",
            return_value=str(settings_file),
        ):
            result = add_permission_rules_to_settings(
                rule_values=[PermissionRuleValue(tool_name="Bash")],
                rule_behavior="allow",
                source=PermissionRuleSource.PROJECT_SETTINGS,
                cwd=str(tmp_path),
            )

        assert result is True
        with open(settings_file, 'r') as f:
            data = json.load(f)
        assert "Read" in data["permissions"]["allow"]
        assert "Bash" in data["permissions"]["allow"]

    def test_no_duplicates(self, tmp_path):
        """不添加重复规则"""
        codo_dir = tmp_path / ".codo"
        codo_dir.mkdir()
        settings_file = codo_dir / "settings.json"
        settings_file.write_text(json.dumps({
            "permissions": {"allow": ["Bash"]}
        }), encoding='utf-8')

        with patch(
            "codo.services.tools.permissions_loader._get_settings_file_path",
            return_value=str(settings_file),
        ):
            result = add_permission_rules_to_settings(
                rule_values=[PermissionRuleValue(tool_name="Bash")],
                rule_behavior="allow",
                source=PermissionRuleSource.PROJECT_SETTINGS,
                cwd=str(tmp_path),
            )

        assert result is True
        with open(settings_file, 'r') as f:
            data = json.load(f)
        assert data["permissions"]["allow"].count("Bash") == 1

    def test_empty_rule_values(self):
        """空规则列表直接返回成功"""
        result = add_permission_rules_to_settings(
            rule_values=[],
            rule_behavior="allow",
            source=PermissionRuleSource.PROJECT_SETTINGS,
        )
        assert result is True

    def test_session_source_returns_false(self):
        """SESSION 来源返回失败"""
        result = add_permission_rules_to_settings(
            rule_values=[PermissionRuleValue(tool_name="Bash")],
            rule_behavior="allow",
            source=PermissionRuleSource.SESSION,
        )
        assert result is False

# ============================================================================
# delete_permission_rule_from_settings 测试
# ============================================================================

class TestDeletePermissionRuleFromSettings:
    """测试从设置文件删除规则"""

    def test_delete_existing_rule(self, tmp_path):
        """删除已存在的规则"""
        codo_dir = tmp_path / ".codo"
        codo_dir.mkdir()
        settings_file = codo_dir / "settings.json"
        settings_file.write_text(json.dumps({
            "permissions": {"allow": ["Bash", "Read"]}
        }), encoding='utf-8')

        rule = PermissionRule(
            source=PermissionRuleSource.PROJECT_SETTINGS,
            rule_behavior="allow",
            rule_value=PermissionRuleValue(tool_name="Bash"),
        )

        with patch(
            "codo.services.tools.permissions_loader._get_settings_file_path",
            return_value=str(settings_file),
        ):
            result = delete_permission_rule_from_settings(rule, str(tmp_path))

        assert result is True
        with open(settings_file, 'r') as f:
            data = json.load(f)
        assert "Bash" not in data["permissions"]["allow"]
        assert "Read" in data["permissions"]["allow"]

    def test_delete_nonexistent_rule(self, tmp_path):
        """删除不存在的规则 → False"""
        codo_dir = tmp_path / ".codo"
        codo_dir.mkdir()
        settings_file = codo_dir / "settings.json"
        settings_file.write_text(json.dumps({
            "permissions": {"allow": ["Read"]}
        }), encoding='utf-8')

        rule = PermissionRule(
            source=PermissionRuleSource.PROJECT_SETTINGS,
            rule_behavior="allow",
            rule_value=PermissionRuleValue(tool_name="Bash"),
        )

        with patch(
            "codo.services.tools.permissions_loader._get_settings_file_path",
            return_value=str(settings_file),
        ):
            result = delete_permission_rule_from_settings(rule, str(tmp_path))

        assert result is False

    def test_delete_from_nonexistent_file(self):
        """从不存在的文件删除 → False"""
        rule = PermissionRule(
            source=PermissionRuleSource.PROJECT_SETTINGS,
            rule_behavior="allow",
            rule_value=PermissionRuleValue(tool_name="Bash"),
        )

        with patch(
            "codo.services.tools.permissions_loader._get_settings_file_path",
            return_value="/nonexistent/settings.json",
        ):
            result = delete_permission_rule_from_settings(rule)

        assert result is False

    def test_delete_session_rule_returns_false(self):
        """SESSION 来源规则无法删除"""
        rule = PermissionRule(
            source=PermissionRuleSource.SESSION,
            rule_behavior="allow",
            rule_value=PermissionRuleValue(tool_name="Bash"),
        )
        result = delete_permission_rule_from_settings(rule)
        assert result is False

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
