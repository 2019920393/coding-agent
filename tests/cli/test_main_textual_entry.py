"""Textual REPL 入口测试。"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from click.testing import CliRunner

from codo import main
from codo.session.storage import SessionStorage

@pytest.mark.asyncio
async def test_run_repl_delegates_to_textual_launcher(monkeypatch):
    """run_repl 应委托给新的 Textual 启动器。"""
    launcher = AsyncMock()
    monkeypatch.setattr(main, "launch_textual_app", launcher)

    await main.run_repl(
        api_key="test-key",
        verbose=True,
        model="claude-test",
        base_url="https://example.invalid",
        thinking_config={"type": "enabled", "budget_tokens": 2048},
        session_id="session-123",
        initial_prompt="hello",
        initial_session_query="recent",
    )

    launcher.assert_awaited_once()
    kwargs = launcher.await_args.kwargs
    assert kwargs["api_key"] == "test-key"
    assert kwargs["verbose"] is True
    assert kwargs["model"] == "claude-test"
    assert kwargs["base_url"] == "https://example.invalid"
    assert kwargs["thinking_config"] == {"type": "enabled", "budget_tokens": 2048}
    assert kwargs["session_id"] == "session-123"
    assert kwargs["initial_prompt"] == "hello"
    assert kwargs["initial_session_query"] == "recent"

@pytest.mark.asyncio
async def test_run_repl_with_history_forwards_initial_messages(monkeypatch):
    """兼容入口应把历史消息继续交给新的 Textual 启动器。"""
    launcher = AsyncMock()
    monkeypatch.setattr(main, "launch_textual_app", launcher)

    initial_messages = [{"role": "user", "content": "history"}]
    await main.run_repl_with_history(
        api_key="test-key",
        initial_messages=initial_messages,
        verbose=False,
        model="claude-test",
        base_url="https://example.invalid",
    )

    launcher.assert_awaited_once()
    kwargs = launcher.await_args.kwargs
    assert kwargs["initial_messages"] == initial_messages

def test_cli_no_longer_supports_print_mode():
    """CLI 应删除 --print 非交互模式入口。"""
    runner = CliRunner()

    result = runner.invoke(main.cli, ["--print", "hello"])

    assert result.exit_code != 0
    assert "No such option: --print" in result.output

@pytest.fixture
def sandbox_home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home

@pytest.mark.asyncio
async def test_run_continue_session_only_uses_current_working_directory_history(sandbox_home, tmp_path, monkeypatch):
    current_dir = tmp_path / "project-a"
    other_dir = tmp_path / "project-b"
    current_dir.mkdir()
    other_dir.mkdir()

    current_storage = SessionStorage("session-current", str(current_dir))
    current_storage.record_messages(
        [
            {"role": "user", "type": "user", "uuid": "u-current", "content": "current workspace session"},
        ]
    )
    other_storage = SessionStorage("session-other", str(other_dir))
    other_storage.record_messages(
        [
            {"role": "user", "type": "user", "uuid": "u-other", "content": "other workspace session"},
        ]
    )

    run_repl = AsyncMock()
    monkeypatch.setattr(main, "run_repl", run_repl)
    monkeypatch.setattr(main.os, "getcwd", lambda: str(current_dir))

    await main.run_continue_session(api_key="test-key", verbose=False, model="claude-test")

    run_repl.assert_awaited_once()
    assert run_repl.await_args.kwargs["session_id"] == "session-current"

@pytest.mark.asyncio
async def test_run_resume_session_only_matches_runtime_sessions_in_current_working_directory(
    sandbox_home,
    tmp_path,
    monkeypatch,
):
    current_dir = tmp_path / "project-a"
    other_dir = tmp_path / "project-b"
    current_dir.mkdir()
    other_dir.mkdir()

    current_storage = SessionStorage("session-current", str(current_dir))
    current_storage.save_title("修复登录流程", source="user")
    current_storage.record_messages(
        [
            {"role": "user", "type": "user", "uuid": "u-current", "content": "current workspace session"},
        ]
    )
    other_storage = SessionStorage("session-other", str(other_dir))
    other_storage.save_title("修复登录流程", source="user")
    other_storage.record_messages(
        [
            {"role": "user", "type": "user", "uuid": "u-other", "content": "other workspace session"},
        ]
    )

    run_repl = AsyncMock()
    monkeypatch.setattr(main, "run_repl", run_repl)
    monkeypatch.setattr(main.os, "getcwd", lambda: str(current_dir))

    await main.run_resume_session("修复登录流程", api_key="test-key", verbose=False, model="claude-test")

    run_repl.assert_awaited_once()
    assert run_repl.await_args.kwargs["session_id"] == "session-current"

@pytest.mark.asyncio
async def test_run_resume_session_does_not_resume_uuid_from_other_working_directory(
    sandbox_home,
    tmp_path,
    monkeypatch,
):
    current_dir = tmp_path / "project-a"
    other_dir = tmp_path / "project-b"
    current_dir.mkdir()
    other_dir.mkdir()

    other_storage = SessionStorage("12345678-1234-1234-1234-123456789abc", str(other_dir))
    other_storage.record_messages(
        [
            {"role": "user", "type": "user", "uuid": "u-other", "content": "other workspace session"},
        ]
    )

    run_repl = AsyncMock()
    monkeypatch.setattr(main, "run_repl", run_repl)
    monkeypatch.setattr(main.os, "getcwd", lambda: str(current_dir))

    await main.run_resume_session(
        "12345678-1234-1234-1234-123456789abc",
        api_key="test-key",
        verbose=False,
        model="claude-test",
    )

    run_repl.assert_awaited_once()
    assert "session_id" not in run_repl.await_args.kwargs
    assert run_repl.await_args.kwargs["initial_session_query"] == "12345678-1234-1234-1234-123456789abc"
