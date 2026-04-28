"""Textual slash 命令功能层测试。"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from codo.commands import find_command
from codo.cli.tui import TextualChatApp, UIBridge
from codo.services.mcp.config import MCPServerConfig
from codo.services.mcp.types import MCPResourceInfo, MCPServerConnection, MCPToolInfo
from codo.services.tools.permission_checker import create_default_permission_context
from codo.session.storage import (
    SessionStorage,
    get_session_event_log_path,
    get_session_file_path,
    get_session_snapshot_path,
)
from codo.types.permissions import PermissionMode, PermissionRuleSource
from codo.utils.config import get_global_config
from codo.utils.config import save_project_config

class DummyMCPConfigManager:
    def __init__(self, servers: dict[str, MCPServerConfig] | None = None) -> None:
        self._servers = servers or {}

    def list_servers(self) -> dict[str, MCPServerConfig]:
        return self._servers

class DummyMCPClientManager:
    def __init__(
        self,
        tools: dict[str, list[MCPToolInfo]] | None = None,
        resources: dict[str, list[MCPResourceInfo]] | None = None,
    ) -> None:
        self._tools = tools or {}
        self._resources = resources or {}
        self._connections: dict[str, MCPServerConnection] = {}

    async def connect(self, server_name: str) -> bool:
        self._connections[server_name] = MCPServerConnection(
            name=server_name,
            transport="stdio",
            connected=True,
            tools_count=len(self._tools.get(server_name, [])),
            resources_count=len(self._resources.get(server_name, [])),
        )
        return True

    async def disconnect(self, server_name: str) -> None:
        self._connections.pop(server_name, None)

    def list_connections(self) -> list[MCPServerConnection]:
        return list(self._connections.values())

    async def list_tools(self, server_name: str) -> list[MCPToolInfo]:
        return list(self._tools.get(server_name, []))

    async def list_resources(self, server_name: str) -> list[MCPResourceInfo]:
        return list(self._resources.get(server_name, []))

class CommandEngine:
    def __init__(self, cwd: str) -> None:
        self.cwd = cwd
        self.session_id = "session-main"
        self.model = "claude-test"
        self.turn_count = 1
        self.enable_persistence = True
        self.refresh_calls = 0
        self.messages = [
            {"role": "user", "content": "hello", "type": "user", "uuid": "user-1"},
            {"role": "assistant", "content": "world", "type": "assistant", "uuid": "assistant-1"},
        ]
        self.execution_context = {
            "permission_context": create_default_permission_context(cwd),
            "options": {"app_state": {"todos": {}}},
        }
        self.session_storage = SessionStorage(self.session_id, cwd)
        self.token_budget = SimpleNamespace(model=self.model)
        self.mcp_config_manager = DummyMCPConfigManager(
            {
                "filesystem": MCPServerConfig(command="fs-server", args=["--stdio"]),
            }
        )
        self.mcp_client_manager = DummyMCPClientManager(
            tools={
                "filesystem": [
                    MCPToolInfo(
                        name="read_file",
                        description="Read files",
                        input_schema={},
                        server_name="filesystem",
                    )
                ]
            },
            resources={
                "filesystem": [
                    MCPResourceInfo(
                        uri="file:///README.md",
                        name="README",
                        description="Repository readme",
                        mime_type="text/markdown",
                        server_name="filesystem",
                    )
                ]
            },
        )

    def get_context_stats(self) -> dict[str, int]:
        return {
            "token_count": 12,
            "context_window": 200000,
            "remaining_tokens": 199988,
            "model_visible_message_count": 2,
            "session_message_count": len(self.messages),
        }

    def reset_interrupt_state(self) -> None:
        return None

    def interrupt(self) -> None:
        return None

    async def compact(self, instruction=None):
        return SimpleNamespace(pre_compact_token_count=300, post_compact_token_count=120)

    async def refresh_mcp_tools(self) -> int:
        self.refresh_calls += 1
        return sum(
            len(self.mcp_client_manager._tools.get(connection.name, []))
            for connection in self.mcp_client_manager.list_connections()
        )

    def refresh_skills(self) -> int:
        return 0

    def restore_session(self) -> bool:
        loaded_messages = self.session_storage.load_messages()
        self.messages = loaded_messages
        return bool(loaded_messages)

@pytest.fixture
def sandbox_home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home

@pytest.fixture
def app_factory():
    bridges: list[UIBridge] = []

    def _build(engine: CommandEngine) -> tuple[TextualChatApp, UIBridge]:
        bridge = UIBridge(engine)
        bridges.append(bridge)
        return TextualChatApp(bridge=bridge), bridge

    yield _build

    for bridge in bridges:
        bridge.close()

def _info_messages(bridge: UIBridge) -> list[str]:
    return [message.content for message in bridge.get_snapshot().messages if message.role == "info"]

def _toast_messages(bridge: UIBridge) -> list[str]:
    return [toast.message for toast in bridge.get_snapshot().toasts]

def test_permissions_command_is_registered_with_aliases():
    command = find_command("permissions")

    assert command is not None
    assert find_command("perm") is command
    assert find_command("p") is command

def test_focus_command_stays_registered_and_audit_command_is_removed():
    assert find_command("focus") is not None
    assert find_command("audit") is None

@pytest.mark.asyncio
async def test_clear_command_clears_ui_and_engine_history(sandbox_home, tmp_path, app_factory):
    engine = CommandEngine(str(tmp_path / "project"))
    app, bridge = app_factory(engine)

    await app._execute_command(find_command("clear"), "")

    snapshot = bridge.get_snapshot()
    assert snapshot.messages == []
    assert engine.messages == []
    assert any("会话已清空" in item for item in _toast_messages(bridge))

@pytest.mark.asyncio
async def test_model_command_reports_and_switches_model(sandbox_home, tmp_path, app_factory):
    engine = CommandEngine(str(tmp_path / "project"))
    app, bridge = app_factory(engine)

    await app._execute_command(find_command("model"), "")
    await app._execute_command(find_command("model"), "claude-sonnet-4")

    toasts = _toast_messages(bridge)
    assert any("当前模型：claude-test" in item for item in toasts)
    assert any("已切换模型到 claude-sonnet-4" in item for item in toasts)
    assert engine.model == "claude-sonnet-4"

@pytest.mark.asyncio
async def test_project_skill_is_exposed_as_prompt_command(sandbox_home, tmp_path, app_factory):
    project_dir = tmp_path / "project"
    skill_dir = project_dir / ".codo" / "skills"
    skill_dir.mkdir(parents=True)
    (skill_dir / "review.md").write_text(
        "---\ndescription: Review helper\n---\n\nInspect the patch carefully.\n",
        encoding="utf-8",
    )

    engine = CommandEngine(str(project_dir))
    app, _ = app_factory(engine)

    command = app._resolve_exact_command("review")

    assert command is not None
    assert command.type.value == "prompt"
    assert any(item.name == "review" for item in app._commands)

@pytest.mark.asyncio
async def test_prompt_skill_command_queues_hidden_runtime_prompt(sandbox_home, tmp_path, app_factory, monkeypatch):
    project_dir = tmp_path / "project"
    skill_dir = project_dir / ".codo" / "skills"
    skill_dir.mkdir(parents=True)
    (skill_dir / "review.md").write_text(
        "---\ndescription: Review helper\n---\n\nInspect the patch carefully.\n",
        encoding="utf-8",
    )

    engine = CommandEngine(str(project_dir))
    app, _ = app_factory(engine)
    submit_mock = AsyncMock()
    monkeypatch.setattr(app, "_submit_prompt", submit_mock)

    command = app._resolve_exact_command("review")
    assert command is not None

    await app._execute_command(command, "src/app.py")

    queued = engine.execution_context.get("queued_commands", [])
    assert len(queued) == 1
    assert "<command-name>/review</command-name>" in queued[0]["prompt"]
    assert "src/app.py" in queued[0]["prompt"]
    submit_mock.assert_awaited_once_with("/review src/app.py")

@pytest.mark.asyncio
async def test_permissions_show_reports_current_runtime_mode(sandbox_home, tmp_path, app_factory):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    engine = CommandEngine(str(project_dir))
    app, bridge = app_factory(engine)

    await app._execute_command(find_command("permissions"), "")

    info = _info_messages(bridge)[-1]
    assert "权限模式：询问" in info
    assert "作用域：当前会话" in info
    assert "会话放行规则：0" in info

@pytest.mark.asyncio
async def test_permissions_bypass_requires_explicit_confirm_first_time(sandbox_home, tmp_path, app_factory):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    engine = CommandEngine(str(project_dir))
    app, bridge = app_factory(engine)

    await app._execute_command(find_command("permissions"), "bypass")

    permission_context = engine.execution_context["permission_context"]
    assert permission_context.mode == PermissionMode.DEFAULT
    assert any("/permissions bypass confirm" in item for item in _toast_messages(bridge))

@pytest.mark.asyncio
async def test_permissions_bypass_confirm_switches_mode_and_persists_acceptance(sandbox_home, tmp_path, app_factory):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    engine = CommandEngine(str(project_dir))
    app, bridge = app_factory(engine)

    await app._execute_command(find_command("permissions"), "bypass confirm")

    permission_context = engine.execution_context["permission_context"]
    assert permission_context.mode == PermissionMode.BYPASS_PERMISSIONS
    assert any("权限模式已切换为：直通" in item for item in _toast_messages(bridge))
    assert get_global_config().bypass_permissions_mode_accepted is True

@pytest.mark.asyncio
async def test_permissions_ask_strict_clears_session_allow_rules(sandbox_home, tmp_path, app_factory):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    engine = CommandEngine(str(project_dir))
    permission_context = engine.execution_context["permission_context"]
    permission_context.mode = PermissionMode.BYPASS_PERMISSIONS
    permission_context.always_allow_rules[PermissionRuleSource.SESSION] = ["Bash", "Write"]
    app, bridge = app_factory(engine)

    await app._execute_command(find_command("permissions"), "ask --strict")

    assert permission_context.mode == PermissionMode.DEFAULT
    assert permission_context.always_allow_rules[PermissionRuleSource.SESSION] == []
    assert any("清除了 2 条会话放行规则" in item for item in _toast_messages(bridge))

@pytest.mark.asyncio
async def test_focus_command_switches_sidebar_modes(sandbox_home, tmp_path, app_factory):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    engine = CommandEngine(str(project_dir))
    engine.execution_context["options"]["app_state"]["todos"]["agent-1"] = [
        {
            "content": "Inspect runtime events",
            "status": "in_progress",
            "activeForm": "Inspecting runtime events",
        }
    ]
    app, bridge = app_factory(engine)

    await app._execute_command(find_command("focus"), "global")
    assert bridge.get_snapshot().sidebar_mode == "global"
    assert bridge.get_snapshot().auto_follow is False

    await app._execute_command(find_command("focus"), "agent-1")
    assert bridge.get_snapshot().sidebar_mode == "agent:agent-1"
    assert bridge.get_snapshot().auto_follow is False

    await app._execute_command(find_command("focus"), "auto")
    assert bridge.get_snapshot().sidebar_mode == "auto"
    assert bridge.get_snapshot().auto_follow is True

@pytest.mark.asyncio
async def test_focus_command_show_uses_human_friendly_copy(sandbox_home, tmp_path, app_factory):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    engine = CommandEngine(str(project_dir))
    app, bridge = app_factory(engine)

    await app._execute_command(find_command("focus"), "")

    info = _info_messages(bridge)[-1]
    assert "侧栏视角：自动跟随" in info
    assert "自动跟随：开启" in info
    assert "sidebar_mode" not in info
    assert "auto_follow" not in info

@pytest.mark.asyncio
async def test_status_command_uses_human_friendly_sidebar_copy(sandbox_home, tmp_path, app_factory):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    engine = CommandEngine(str(project_dir))
    app, bridge = app_factory(engine)

    await app._execute_command(find_command("status"), "")

    info = _info_messages(bridge)[-1]
    assert "侧栏视角：" in info
    assert "自动跟随：" in info
    assert "侧栏：auto" not in info

@pytest.mark.asyncio
async def test_version_command_renders_version_card(sandbox_home, tmp_path, app_factory):
    engine = CommandEngine(str(tmp_path / "project"))
    app, bridge = app_factory(engine)

    await app._execute_command(find_command("version"), "")

    assert any("Codo" in item and "0.1.0" in item for item in _info_messages(bridge))

@pytest.mark.asyncio
async def test_sessions_command_lists_current_runtime_sessions(sandbox_home, tmp_path, app_factory):
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    first = SessionStorage("session-a", str(project_dir))
    first.record_messages(
        [
            {"role": "user", "type": "user", "uuid": "u-1", "content": "fix login bug"},
            {"role": "assistant", "type": "assistant", "uuid": "a-1", "content": "working on it"},
        ]
    )
    second = SessionStorage("session-b", str(project_dir))
    second.record_messages(
        [
            {"role": "user", "type": "user", "uuid": "u-2", "content": "ship release"},
        ]
    )

    engine = CommandEngine(str(project_dir))
    app, bridge = app_factory(engine)

    await app._execute_command(find_command("sessions"), "")

    info = _info_messages(bridge)[-1]
    assert "当前工作区历史会话" in info
    assert "session-a" in info
    assert "session-b" in info
    assert "fix login bug" in info or "ship release" in info

@pytest.mark.asyncio
async def test_sessions_command_switches_session_and_reloads_history(sandbox_home, tmp_path, app_factory):
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    target = SessionStorage("session-a", str(project_dir))
    target.save_title("修复登录流程", source="user")
    target.record_messages(
        [
            {"role": "user", "type": "user", "uuid": "u-1", "content": "fix login bug"},
            {"role": "assistant", "type": "assistant", "uuid": "a-1", "content": "working on it"},
        ]
    )

    engine = CommandEngine(str(project_dir))
    app, bridge = app_factory(engine)

    await app._execute_command(find_command("sessions"), "session-a")

    assert engine.session_id == "session-a"
    assert engine.session_storage.session_id == "session-a"
    assert engine.execution_context["session_id"] == "session-a"
    assert [message["role"] for message in engine.messages] == ["user", "assistant"]
    assert engine.messages[0]["content"] == "fix login bug"

    snapshot = bridge.get_snapshot()
    assert [message.role for message in snapshot.messages] == ["user", "assistant"]
    assert snapshot.messages[0].content == "fix login bug"
    assert snapshot.status.session_title == "修复登录流程"
    assert any("已恢复会话" in item and "修复登录流程" in item for item in _toast_messages(bridge))

@pytest.mark.asyncio
async def test_sessions_command_removes_empty_fresh_session_artifacts_when_switching(
    sandbox_home,
    tmp_path,
    app_factory,
):
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    target = SessionStorage("session-a", str(project_dir))
    target.record_messages(
        [
            {"role": "user", "type": "user", "uuid": "u-1", "content": "ship release"},
            {"role": "assistant", "type": "assistant", "uuid": "a-1", "content": "done"},
        ]
    )

    engine = CommandEngine(str(project_dir))
    engine.messages = []
    engine.session_storage.save_last_prompt("/sessions session-a")

    session_file = get_session_file_path(engine.session_id, str(project_dir))
    event_file = get_session_event_log_path(engine.session_id, str(project_dir))
    snapshot_file = get_session_snapshot_path(engine.session_id, str(project_dir))
    assert event_file.exists()
    assert snapshot_file.exists()

    app, bridge = app_factory(engine)

    await app._execute_command(find_command("sessions"), "session-a")

    assert not session_file.exists()
    assert not event_file.exists()
    assert not snapshot_file.exists()
    assert any("已清理空白会话" in item for item in _toast_messages(bridge))

@pytest.mark.asyncio
async def test_export_command_writes_transcript_file(sandbox_home, tmp_path, app_factory):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    engine = CommandEngine(str(project_dir))
    app, bridge = app_factory(engine)

    await app._execute_command(find_command("export"), "conversation.md")

    export_path = project_dir / "conversation.md"
    assert export_path.exists()
    content = export_path.read_text(encoding="utf-8")
    assert "hello" in content
    assert "world" in content
    assert any("已导出会话到" in item for item in _toast_messages(bridge))

@pytest.mark.asyncio
async def test_config_command_renders_merged_config(sandbox_home, tmp_path, app_factory):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    save_project_config(str(project_dir), {"theme": "light", "language": "zh-CN"})

    engine = CommandEngine(str(project_dir))
    app, bridge = app_factory(engine)

    await app._execute_command(find_command("config"), "")

    info = _info_messages(bridge)[-1]
    assert '"theme": "light"' in info
    assert '"language": "zh-CN"' in info

@pytest.mark.asyncio
async def test_diff_command_renders_repo_changes(sandbox_home, tmp_path, app_factory):
    if shutil.which("git") is None:
        pytest.skip("git is not available")

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    subprocess.run(["git", "init"], cwd=project_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=project_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=project_dir, check=True)
    tracked = project_dir / "tracked.txt"
    tracked.write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=project_dir, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=project_dir, check=True, capture_output=True)
    tracked.write_text("hello world\n", encoding="utf-8")

    engine = CommandEngine(str(project_dir))
    app, bridge = app_factory(engine)

    await app._execute_command(find_command("diff"), "")

    info = _info_messages(bridge)[-1]
    assert "tracked.txt" in info
    assert "hello world" in info

@pytest.mark.asyncio
async def test_memory_commands_list_view_delete_and_index(sandbox_home, tmp_path, app_factory):
    from codo.services.memory import MemoryManager, ensure_memory_dir

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    memory_dir = ensure_memory_dir(str(project_dir))
    manager = MemoryManager(str(memory_dir))
    file_path = Path(
        manager.create_memory(
            name="Release Plan",
            description="Notes for launch",
            memory_type="project",
            content="Ship on Friday",
            topic="release_plan",
        )
    )

    engine = CommandEngine(str(project_dir))
    app, bridge = app_factory(engine)

    await app._execute_command(find_command("memory"), "list")
    await app._execute_command(find_command("memory"), f"view {file_path.name}")
    await app._execute_command(find_command("memory"), "index")
    await app._execute_command(find_command("memory"), f"delete {file_path.name}")

    infos = _info_messages(bridge)
    assert any("release_plan" in item for item in infos)
    assert any("Ship on Friday" in item for item in infos)
    assert any("Notes for launch" in item for item in infos)
    assert not file_path.exists()
    assert any("已删除记忆" in item for item in _toast_messages(bridge))

@pytest.mark.asyncio
async def test_mcp_commands_connect_list_tools_resources_and_disconnect(sandbox_home, tmp_path, app_factory):
    engine = CommandEngine(str(tmp_path / "project"))
    app, bridge = app_factory(engine)

    await app._execute_command(find_command("mcp-list"), "")
    await app._execute_command(find_command("mcp-connect"), "filesystem")
    await app._execute_command(find_command("mcp-tools"), "")
    await app._execute_command(find_command("mcp-resources"), "filesystem")
    await app._execute_command(find_command("mcp-disconnect"), "filesystem")

    infos = _info_messages(bridge)
    toasts = _toast_messages(bridge)
    assert any("filesystem" in item for item in infos)
    assert any("read_file" in item for item in infos)
    assert any("README" in item for item in infos)
    assert any("已连接 MCP 服务器" in item for item in toasts)
    assert any("已断开 MCP 服务器" in item for item in toasts)
    assert engine.refresh_calls >= 2

@pytest.mark.asyncio
async def test_doctor_command_renders_runtime_diagnostics(sandbox_home, tmp_path, app_factory):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    engine = CommandEngine(str(project_dir))
    app, bridge = app_factory(engine)

    await app._execute_command(find_command("doctor"), "")

    payload = json.loads(_info_messages(bridge)[-1])
    assert payload["session_id"] == "session-main"
    assert payload["cwd"] == str(project_dir)
    assert payload["model"] == "claude-test"
