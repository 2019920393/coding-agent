"""Textual 交互弹层入口测试。"""

from unittest.mock import AsyncMock

import pytest

from codo.cli import interactive_dialogs
from codo.tools.receipts import ProposedFileChange
from codo.services.tools.change_review import request_change_review

@pytest.mark.asyncio
async def test_prompt_permission_dialog_prefers_active_textual_app(monkeypatch):
    """存在活动 Textual App 时，不应回退到 builtins.input。"""
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
    """存在活动 Textual App 时，应委托给 UI 卡片收集答案。"""
    app = AsyncMock()
    app.request_questions.return_value = {"Question?": "Answer"}
    monkeypatch.setattr(interactive_dialogs, "get_active_app", lambda: app)

    questions = [SimpleQuestion("Question?")]
    result = await interactive_dialogs.collect_user_answers_dialog(questions)

    assert result == {"Question?": "Answer"}
    app.request_questions.assert_awaited_once_with(questions)

class SimpleQuestion:
    def __init__(self, question: str):
        self.question = question

@pytest.mark.asyncio
async def test_request_change_review_prefers_active_textual_app(monkeypatch):
    """存在活动 Textual App 时，diff review 也应委托给原位 UI。"""
    app = AsyncMock()
    app.request_change_review.return_value = "accept"
    monkeypatch.setattr("codo.services.tools.change_review.get_active_app", lambda: app)

    change = ProposedFileChange(
        change_id="chg_1",
        path="C:/tmp/app.py",
        original_content="old",
        new_content="new",
        diff_text="@@ -1 +1 @@\n-old\n+new",
        source_tool="Edit",
    )

    result = await request_change_review(change)

    assert result == "accept"
    app.request_change_review.assert_awaited_once_with(change)
