"""
hooks_loader.py 单元测试

测试覆盖：
- _get_settings_file_path: 设置文件路径获取
- _load_settings_json: JSON 文件加载
- _parse_hook_matcher: matcher 配置解析
- load_hooks_from_settings: 完整 hooks 配置加载
- get_hooks_for_event: 按事件和工具过滤
"""

import json
import os
import tempfile
import shutil
from pathlib import Path

import pytest

from codo.services.tools.hooks_loader import (
    _get_settings_file_path,
    _load_settings_json,
    _parse_hook_matcher,
    load_hooks_from_settings,
    get_hooks_for_event,
    SUPPORTED_HOOK_EVENTS,
)
from codo.types.hooks import HookConfig

# ============================================================================
# Fixture
# ============================================================================

@pytest.fixture
def temp_project_dir():
    """创建临时项目目录，测试结束后自动清理"""
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)

def _create_settings(project_dir: str, hooks_config: dict) -> str:
    """在临时目录中创建 .codo/settings.json"""
    codo_dir = os.path.join(project_dir, ".codo")
    os.makedirs(codo_dir, exist_ok=True)
    settings_path = os.path.join(codo_dir, "settings.json")
    with open(settings_path, 'w', encoding='utf-8') as f:
        json.dump({"hooks": hooks_config}, f)
    return settings_path

# ============================================================================
# _get_settings_file_path 测试
# ============================================================================

class TestGetSettingsFilePath:
    """测试设置文件路径获取"""

    def test_存在时返回路径(self, temp_project_dir):
        """设置文件存在时应返回路径"""
        _create_settings(temp_project_dir, {})
        result = _get_settings_file_path(temp_project_dir)
        assert result is not None
        assert result.endswith("settings.json")

    def test_不存在时返回None(self, temp_project_dir):
        """设置文件不存在时应返回 None"""
        result = _get_settings_file_path(temp_project_dir)
        assert result is None

    def test_不存在目录返回None(self):
        """不存在的目录应返回 None"""
        result = _get_settings_file_path("/nonexistent/path/xyz")
        assert result is None

# ============================================================================
# _load_settings_json 测试
# ============================================================================

class TestLoadSettingsJson:
    """测试 JSON 文件加载"""

    def test_加载有效JSON(self, tmp_path):
        """有效 JSON 文件应正确加载"""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text('{"hooks": {"PreToolUse": []}}', encoding='utf-8')
        result = _load_settings_json(str(settings_file))
        assert result == {"hooks": {"PreToolUse": []}}

    def test_不存在文件返回None(self):
        """不存在的文件应返回 None"""
        result = _load_settings_json("/nonexistent/settings.json")
        assert result is None

    def test_空文件返回空字典(self, tmp_path):
        """空文件应返回空字典"""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text('', encoding='utf-8')
        result = _load_settings_json(str(settings_file))
        assert result == {}

    def test_无效JSON返回None(self, tmp_path):
        """无效 JSON 应返回 None"""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text('{invalid}', encoding='utf-8')
        result = _load_settings_json(str(settings_file))
        assert result is None

# ============================================================================
# _parse_hook_matcher 测试
# ============================================================================

class TestParseHookMatcher:
    """测试 matcher 配置解析"""

    def test_基本command_hook(self):
        """基本 command hook 应正确解析"""
        matcher_config = {
            "matcher": "Bash",
            "hooks": [
                {"type": "command", "command": "echo 'before bash'"}
            ]
        }
        result = _parse_hook_matcher(matcher_config, "PreToolUse")
        assert len(result) == 1
        assert result[0].command == "echo 'before bash'"
        assert result[0].tool_name == "Bash"
        assert result[0].event == "PreToolUse"

    def test_无matcher匹配所有工具(self):
        """没有 matcher 时应匹配所有工具（tool_name=None）"""
        matcher_config = {
            "hooks": [
                {"type": "command", "command": "echo 'all tools'"}
            ]
        }
        result = _parse_hook_matcher(matcher_config, "PostToolUse")
        assert len(result) == 1
        assert result[0].tool_name is None

    def test_跳过非command类型(self):
        """非 command 类型的 hook 应被跳过"""
        matcher_config = {
            "hooks": [
                {"type": "prompt", "prompt": "verify this"},
                {"type": "command", "command": "echo 'ok'"},
            ]
        }
        result = _parse_hook_matcher(matcher_config, "PreToolUse")
        # 只有 command 类型被处理
        assert len(result) == 1
        assert result[0].command == "echo 'ok'"

    def test_自定义超时(self):
        """自定义超时应被正确转换（秒→毫秒）"""
        matcher_config = {
            "hooks": [
                {"type": "command", "command": "echo 'test'", "timeout": 30}
            ]
        }
        result = _parse_hook_matcher(matcher_config, "PreToolUse")
        assert len(result) == 1
        assert result[0].timeout == 30000  # 30秒 = 30000毫秒

    def test_空hooks数组(self):
        """空 hooks 数组应返回空列表"""
        matcher_config = {"matcher": "Bash", "hooks": []}
        result = _parse_hook_matcher(matcher_config, "PreToolUse")
        assert result == []

    def test_无command字段跳过(self):
        """没有 command 字段的 hook 应被跳过"""
        matcher_config = {
            "hooks": [
                {"type": "command"}  # 缺少 command 字段
            ]
        }
        result = _parse_hook_matcher(matcher_config, "PreToolUse")
        assert result == []

# ============================================================================
# load_hooks_from_settings 测试
# ============================================================================

class TestLoadHooksFromSettings:
    """测试完整 hooks 配置加载"""

    def test_加载PreToolUse_hooks(self, temp_project_dir):
        """应正确加载 PreToolUse hooks"""
        _create_settings(temp_project_dir, {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": "echo 'pre bash'"}]
                }
            ]
        })
        result = load_hooks_from_settings(temp_project_dir)
        assert len(result["PreToolUse"]) == 1
        assert result["PreToolUse"][0].command == "echo 'pre bash'"
        assert result["PreToolUse"][0].tool_name == "Bash"

    def test_加载多个事件类型(self, temp_project_dir):
        """应正确加载多个事件类型的 hooks"""
        _create_settings(temp_project_dir, {
            "PreToolUse": [
                {"hooks": [{"type": "command", "command": "echo 'pre'"}]}
            ],
            "PostToolUse": [
                {"hooks": [{"type": "command", "command": "echo 'post'"}]}
            ],
            "Stop": [
                {"hooks": [{"type": "command", "command": "echo 'stop'"}]}
            ],
        })
        result = load_hooks_from_settings(temp_project_dir)
        assert len(result["PreToolUse"]) == 1
        assert len(result["PostToolUse"]) == 1
        assert len(result["Stop"]) == 1

    def test_无设置文件返回空字典(self, temp_project_dir):
        """没有设置文件时应返回空字典"""
        result = load_hooks_from_settings(temp_project_dir)
        for event in SUPPORTED_HOOK_EVENTS:
            assert result[event] == []

    def test_无hooks字段返回空字典(self, temp_project_dir):
        """设置文件中没有 hooks 字段时应返回空字典"""
        codo_dir = os.path.join(temp_project_dir, ".codo")
        os.makedirs(codo_dir, exist_ok=True)
        settings_path = os.path.join(codo_dir, "settings.json")
        with open(settings_path, 'w') as f:
            json.dump({"other_setting": "value"}, f)

        result = load_hooks_from_settings(temp_project_dir)
        for event in SUPPORTED_HOOK_EVENTS:
            assert result[event] == []

    def test_多个matcher(self, temp_project_dir):
        """多个 matcher 应全部被加载"""
        _create_settings(temp_project_dir, {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": "echo 'bash hook'"}]
                },
                {
                    "matcher": "Write",
                    "hooks": [{"type": "command", "command": "echo 'write hook'"}]
                },
            ]
        })
        result = load_hooks_from_settings(temp_project_dir)
        assert len(result["PreToolUse"]) == 2
        tool_names = {h.tool_name for h in result["PreToolUse"]}
        assert "Bash" in tool_names
        assert "Write" in tool_names

# ============================================================================
# get_hooks_for_event 测试
# ============================================================================

class TestGetHooksForEvent:
    """测试按事件和工具过滤"""

    def test_按事件过滤(self, temp_project_dir):
        """应只返回指定事件的 hooks"""
        _create_settings(temp_project_dir, {
            "PreToolUse": [
                {"hooks": [{"type": "command", "command": "echo 'pre'"}]}
            ],
            "PostToolUse": [
                {"hooks": [{"type": "command", "command": "echo 'post'"}]}
            ],
        })
        pre_hooks = get_hooks_for_event(temp_project_dir, "PreToolUse")
        post_hooks = get_hooks_for_event(temp_project_dir, "PostToolUse")
        assert len(pre_hooks) == 1
        assert len(post_hooks) == 1
        assert pre_hooks[0].command == "echo 'pre'"
        assert post_hooks[0].command == "echo 'post'"

    def test_按工具名称过滤(self, temp_project_dir):
        """应只返回匹配工具名称的 hooks"""
        _create_settings(temp_project_dir, {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": "echo 'bash'"}]
                },
                {
                    "matcher": "Write",
                    "hooks": [{"type": "command", "command": "echo 'write'"}]
                },
                {
                    # 无 matcher，匹配所有工具
                    "hooks": [{"type": "command", "command": "echo 'all'"}]
                },
            ]
        })
        bash_hooks = get_hooks_for_event(temp_project_dir, "PreToolUse", "Bash")
        # 应返回 Bash 专属 hook + 无 matcher 的 hook
        assert len(bash_hooks) == 2
        commands = {h.command for h in bash_hooks}
        assert "echo 'bash'" in commands
        assert "echo 'all'" in commands
        assert "echo 'write'" not in commands

    def test_无匹配返回空列表(self, temp_project_dir):
        """没有匹配的 hooks 时应返回空列表"""
        _create_settings(temp_project_dir, {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": "echo 'bash'"}]
                },
            ]
        })
        # 查询 Write 工具的 hooks，但只有 Bash 的 hook
        write_hooks = get_hooks_for_event(temp_project_dir, "PreToolUse", "Write")
        assert write_hooks == []

    def test_Stop_hooks(self, temp_project_dir):
        """应正确加载 Stop hooks"""
        _create_settings(temp_project_dir, {
            "Stop": [
                {"hooks": [{"type": "command", "command": "echo 'session ended'"}]}
            ]
        })
        stop_hooks = get_hooks_for_event(temp_project_dir, "Stop")
        assert len(stop_hooks) == 1
        assert stop_hooks[0].command == "echo 'session ended'"
