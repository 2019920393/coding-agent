"""interactive_dialogs Textual-only tests."""

from unittest.mock import AsyncMock

import pytest

from codo.cli import interactive_dialogs

class SimpleQuestion:
    def __init__(self, question: str):
        self.question = question

@pytest.mark.asyncio
async def test_prompt_permission_dialog_prefers_active_textual_app(monkeypatch):
    """存在活动 Textual App 时，应委托给 App 处理权限交互。"""
    app = AsyncMock()
    app.request_permission.return_value = "allow_once"
    monkeypatch.setattr(interactive_dialogs, "get_active_app", lambda: app)

    result = await interactive_dialogs.prompt_permission_dialog(
        tool_name="Bash",
        tool_info="$ ls -la",
        message="Need approval",
    )

    assert result == "allow_once"
    app.request_permission.assert_awaited_once_with(
        tool_name="Bash",
        tool_info="$ ls -la",
        message="Need approval",
    )

@pytest.mark.asyncio
async def test_collect_user_answers_dialog_prefers_active_textual_app(monkeypatch):
    """存在活动 Textual App 时，应委托给 App 处理问答交互。"""
    app = AsyncMock()
    app.request_questions.return_value = {"Question?": "Answer"}
    monkeypatch.setattr(interactive_dialogs, "get_active_app", lambda: app)

    questions = [SimpleQuestion("Question?")]
    result = await interactive_dialogs.collect_user_answers_dialog(questions)

    assert result == {"Question?": "Answer"}
    app.request_questions.assert_awaited_once_with(questions)

@pytest.mark.asyncio
async def test_prompt_permission_dialog_requires_active_textual_app(monkeypatch):
    """没有活动 Textual App 时，应直接报错而不是回退到 input()。"""
    monkeypatch.setattr(interactive_dialogs, "get_active_app", lambda: None)

    with pytest.raises(RuntimeError, match="Textual app is required"):
        await interactive_dialogs.prompt_permission_dialog(
            tool_name="Bash",
            tool_info="$ ls -la",
            message="Need approval",
        )

@pytest.mark.asyncio
async def test_collect_user_answers_dialog_requires_active_textual_app(monkeypatch):
    """没有活动 Textual App 时，问答交互也应直接报错。"""
    monkeypatch.setattr(interactive_dialogs, "get_active_app", lambda: None)

    with pytest.raises(RuntimeError, match="Textual app is required"):
        await interactive_dialogs.collect_user_answers_dialog([SimpleQuestion("Question?")])
