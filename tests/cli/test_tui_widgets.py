"""Textual App 交互测试。"""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from textual.containers import VerticalScroll
from textual.widgets import Button, Input, Markdown, Static

import codo.cli.tui.widgets as tui_widgets
from codo.cli.tui import TextualChatApp, UIBridge
from codo.cli.interactive_dialogs import prompt_permission_dialog
from codo.commands.base import Command
from codo.services.tools.permission_checker import create_default_permission_context
from codo.session.storage import SessionStorage
from codo.types.permissions import PermissionMode, PermissionRuleSource
from codo.cli.tui.widgets import (
    AgentStreamCardWidget,
    AssistantMessageWidget,
    HeaderPanel,
    InfoMessageWidget,
    InputPanel,
    MessageColumn,
    SidebarRosterCardWidget,
    UserMessageWidget,
)
from codo.tools.receipts import ProposedFileChange

def _chat_input(app: TextualChatApp) -> Any:
    return app.query_one("#chat-input")

def _set_chat_input_value(app: TextualChatApp, value: str) -> Any:
    widget = _chat_input(app)
    if hasattr(widget, "value"):
        widget.value = value
    else:
        widget.load_text(value)
    return widget

def _get_chat_input_value(app: TextualChatApp) -> str:
    widget = _chat_input(app)
    if hasattr(widget, "value"):
        return widget.value
    return widget.text

class StreamingEngine:
    def __init__(self, cwd: str = "."):
        self.session_id = "session-main"
        self.cwd = cwd
        self.model = "claude-test"
        self.execution_context = {
            "permission_context": create_default_permission_context("."),
            "options": {"app_state": {"todos": {}}},
        }
        self.interrupted = False

    async def submit_message_stream(self, prompt: str):
        yield {"type": "stream_request_start"}
        yield {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}}
        yield {"type": "text_delta", "index": 0, "delta": {"text": f"Echo: {prompt}"}}
        yield {"type": "message_stop"}

    def get_context_stats(self):
        return {
            "token_count": 12,
            "context_window": 200000,
            "effective_context_window": 180000,
            "remaining_tokens": 179988,
            "model_visible_message_count": 2,
            "session_message_count": 2,
        }

    def reset_interrupt_state(self):
        self.interrupted = False

    def interrupt(self):
        self.interrupted = True

    async def compact(self, instruction=None):
        return None

class PermissionFlowEngine(StreamingEngine):
    async def submit_message_stream(self, prompt: str):
        yield {"type": "stream_request_start"}
        yield {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}}
        yield {"type": "text_delta", "index": 0, "delta": {"text": "Preparing tool...\n"}}
        choice = await prompt_permission_dialog(
            tool_name="Write",
            tool_info="Write: app.py",
            message="Need approval",
        )
        yield {"type": "text_delta", "index": 0, "delta": {"text": f"permission={choice}"}}
        yield {"type": "message_stop"}

class RuntimeQuestionFlowEngine(StreamingEngine):
    def __init__(self, cwd: str = "."):
        super().__init__(cwd=cwd)
        self._pending_request_id: str | None = None
        self._answer_future: asyncio.Future[dict[str, str] | None] | None = None

    async def submit_message_stream(self, prompt: str):
        yield {"type": "stream_request_start"}
        yield {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}}
        yield {"type": "text_delta", "index": 0, "delta": {"text": "需要你确认一个问题。\n"}}
        yield {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "tool_use", "id": "tool-question-1", "name": "AskUserQuestion"},
        }
        yield {
            "type": "tool_started",
            "tool_use_id": "tool-question-1",
            "tool_name": "AskUserQuestion",
            "status": "running",
            "input_preview": '{"questions":[{"header":"语言","question":"使用哪种语言?","options":[{"label":"Python","description":"保留当前栈"},{"label":"Go","description":"切到静态编译"}]}]}',
        }

        loop = asyncio.get_running_loop()
        self._pending_request_id = "req-question-runtime-1"
        self._answer_future = loop.create_future()
        yield {
            "type": "interaction_requested",
            "request": {
                "request_id": self._pending_request_id,
                "kind": "question",
                "label": "语言",
                "questions": [
                    {
                        "question_id": "question-1",
                        "header": "语言",
                        "question": "使用哪种语言?",
                        "options": [
                            {"value": "Python", "label": "Python", "description": "保留当前栈"},
                            {"value": "Go", "label": "Go", "description": "切到静态编译"},
                        ],
                        "multi_select": False,
                    }
                ],
            },
        }

        answers = await self._answer_future
        yield {
            "type": "interaction_resolved",
            "request_id": self._pending_request_id,
            "data": answers,
        }
        yield {
            "type": "tool_result",
            "tool_use_id": "tool-question-1",
            "content": f"已收到答案: {answers}",
            "receipt": {
                "kind": "generic",
                "summary": "已记录你的回答",
                "body": f"已收到答案: {answers}",
            },
            "is_error": False,
            "status": "completed",
        }
        yield {"type": "message_stop"}

    def resolve_interaction(self, request_id: str, data: dict[str, str] | None) -> None:
        if request_id != self._pending_request_id or self._answer_future is None:
            return
        if not self._answer_future.done():
            self._answer_future.set_result(data)

@pytest.mark.asyncio
async def test_app_shows_user_message_after_submit():
    """提交后，用户输入应立即显示在消息流中。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        _set_chat_input_value(app, "hello textual")
        await pilot.press("enter")
        await pilot.pause()

        users = list(app.query(UserMessageWidget))
        assert len(users) == 1
        assert users[0].message.content == "hello textual"

@pytest.mark.asyncio
async def test_assistant_uses_plain_preview_while_streaming_and_markdown_after_completion():
    """流式阶段应使用轻量预览，结束后再切回 Markdown，避免每个 token 重渲染。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        bridge.begin_user_turn("stream please")
        bridge.apply_stream_event({"type": "stream_request_start"})
        bridge.apply_stream_event({"type": "content_block_start", "index": 0, "content_block": {"type": "text"}})
        bridge.apply_stream_event(
            {
                "type": "text_delta",
                "index": 0,
                "delta": {"text": "Streaming **markdown** preview"},
            }
        )
        await pilot.pause()

        assistant = list(app.query(AssistantMessageWidget))[-1]
        preview = assistant.query_one(".assistant-stream-preview", Static)
        markdown = assistant.query_one(".assistant-markdown", Markdown)

        assert preview.display is True
        assert "Streaming **markdown** preview" in str(preview.render())
        assert markdown.display is False

        bridge.apply_stream_event({"type": "message_stop"})
        await pilot.pause()

        assert preview.display is False
        assert markdown.display is True

@pytest.mark.asyncio
async def test_completed_plain_text_keeps_static_preview_without_markdown_swap():
    """普通文本完成后应保持轻量静态预览，避免 turn 结束时整块切换成 Markdown。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        bridge.begin_user_turn("plain please")
        bridge.apply_stream_event({"type": "stream_request_start"})
        bridge.apply_stream_event({"type": "content_block_start", "index": 0, "content_block": {"type": "text"}})
        bridge.apply_stream_event({"type": "text_delta", "delta": {"text": "这是一段普通文本，没有 markdown 语法。"}})
        await pilot.pause()

        assistant = list(app.query(AssistantMessageWidget))[-1]
        preview = assistant.query_one(".assistant-stream-preview", Static)
        markdown = assistant.query_one(".assistant-markdown", Markdown)

        bridge.apply_stream_event({"type": "message_stop"})
        await pilot.pause()

        assert preview.display is True
        assert "普通文本" in str(preview.render())
        assert markdown.display is False

@pytest.mark.asyncio
async def test_assistant_stream_preview_batches_rapid_deltas(monkeypatch):
    """流式正文应批量刷新，避免每个 delta 都触发整段重绘。"""
    clock = {"now": 0.0}
    monkeypatch.setattr(tui_widgets, "_monotonic", lambda: clock["now"])

    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        bridge.begin_user_turn("batch stream")
        bridge.apply_stream_event({"type": "stream_request_start"})
        bridge.apply_stream_event({"type": "text_delta", "delta": {"text": "A"}})
        await pilot.pause()

        assistant = list(app.query(AssistantMessageWidget))[-1]
        preview = assistant.query_one(".assistant-stream-preview", Static)
        assert str(preview.render()) == "A"

        bridge.apply_stream_event({"type": "text_delta", "delta": {"text": "B"}})
        await pilot.pause()
        assert str(preview.render()) == "A"

        clock["now"] = 0.2
        bridge.apply_stream_event({"type": "text_delta", "delta": {"text": "C"}})
        await pilot.pause()
        assert str(preview.render()) == "ABC"

@pytest.mark.asyncio
async def test_assistant_stream_preview_flushes_buffer_after_throttle_window(monkeypatch):
    """即使新的 delta 暂停下来，流式预览也应在节流窗口后自动补刷，不要一直卡在旧内容。"""
    clock = {"now": 0.0}
    monkeypatch.setattr(tui_widgets, "_monotonic", lambda: clock["now"])

    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        bridge.begin_user_turn("buffer flush")
        bridge.apply_stream_event({"type": "stream_request_start"})
        bridge.apply_stream_event({"type": "text_delta", "delta": {"text": "A"}})
        await pilot.pause()

        assistant = list(app.query(AssistantMessageWidget))[-1]
        preview = assistant.query_one(".assistant-stream-preview", Static)
        assert str(preview.render()) == "A"

        bridge.apply_stream_event({"type": "text_delta", "delta": {"text": "B"}})
        await pilot.pause()
        assert str(preview.render()) == "A"

        clock["now"] = 0.2
        await asyncio.sleep(0.2)
        await pilot.pause()
        assert str(preview.render()) == "AB"

@pytest.mark.asyncio
async def test_user_message_keeps_compact_height_when_rendered():
    """用户消息应保持紧凑行高，不能撑满整个聊天区导致被滚出可视区。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test(size=(80, 24)) as pilot:
        bridge.begin_user_turn("hello compact user row")
        await pilot.pause()

        users = list(app.query(UserMessageWidget))
        assert len(users) == 1
        assert users[0].size.height <= 3

@pytest.mark.asyncio
async def test_help_command_renders_info_message_in_chat():
    """/help 应插入信息卡片，而不是作为普通 prompt 发送。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        _set_chat_input_value(app, "/help")
        await pilot.press("enter")
        await pilot.pause()

        info_cards = list(app.query(InfoMessageWidget))
        assert info_cards
        assert "/help" in info_cards[-1].message.content
        assert not list(app.query(UserMessageWidget))

@pytest.mark.asyncio
async def test_help_command_works_when_typing_keys_like_a_user():
    """/help 在真实键入场景下也应产生信息卡片。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        await pilot.click("#chat-input")
        await pilot.press("/", "h", "e", "l", "p", "enter")
        await pilot.pause()

        info_cards = list(app.query(InfoMessageWidget))
        assert info_cards
        assert "/help" in info_cards[-1].message.content

@pytest.mark.asyncio
async def test_enter_on_partial_slash_command_completes_before_execution():
    """部分 slash 命令第一次回车应补全，第二次回车才执行。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        _set_chat_input_value(app, "/h")
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        assert _get_chat_input_value(app) == "/help "
        assert not list(app.query(InfoMessageWidget))

        await pilot.press("enter")
        await pilot.pause()

        info_cards = list(app.query(InfoMessageWidget))
        assert info_cards
        assert "/help" in info_cards[-1].message.content

@pytest.mark.asyncio
async def test_tab_completes_slash_command_from_overlay():
    """命令浮层可通过 Tab 把当前高亮命令补全回输入框。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        _set_chat_input_value(app, "/co")
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()

        assert _get_chat_input_value(app) == "/compact "

@pytest.mark.asyncio
async def test_clicking_command_suggestion_completes_input():
    """命令浮层的条目应可直接点击补全，避免只能依赖键盘。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        _set_chat_input_value(app, "/h")
        await pilot.pause()

        await pilot.click(".command-suggestion-row")
        await pilot.pause()

        assert _get_chat_input_value(app) == "/help "

@pytest.mark.asyncio
async def test_partial_permissions_command_completes_to_permissions():
    """权限命令应能像其他 slash 命令一样，从部分输入补全到完整命令。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        _set_chat_input_value(app, "/p")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert _get_chat_input_value(app) == "/permissions "

@pytest.mark.asyncio
async def test_exact_permissions_command_first_enter_moves_into_argument_stage():
    """带结构化参数的命令，第一次回车应补空格进入参数阶段，而不是直接执行。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        _set_chat_input_value(app, "/permissions")
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        assert _get_chat_input_value(app) == "/permissions "
        assert not list(app.query(InfoMessageWidget))

@pytest.mark.asyncio
async def test_command_menu_switches_to_argument_options_after_exact_command():
    """命令名补全后，浮层应切换到 argument_spec 选项，而不是继续显示命令列表。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        _set_chat_input_value(app, "/permissions ")
        await pilot.pause()

        names = [str(widget.render()) for widget in app.query(".command-suggestion-name")]

        assert "show" in names
        assert "ask --strict" in names
        assert "bypass confirm" in names
        assert "/permissions" not in names

@pytest.mark.asyncio
async def test_sessions_command_overlay_shows_workspace_history_suggestions(tmp_path):
    """`/sessions ` 参数浮层应直接展示当前工作区历史会话，而不是只停在命令层。"""
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    first = SessionStorage("session-a", str(project_dir))
    first.save_title("修复登录流程", source="user")
    first.record_messages(
        [
            {"role": "user", "type": "user", "uuid": "u-1", "content": "fix login bug"},
        ]
    )
    second = SessionStorage("session-b", str(project_dir))
    second.record_messages(
        [
            {"role": "user", "type": "user", "uuid": "u-2", "content": "ship release"},
        ]
    )

    bridge = UIBridge(StreamingEngine(cwd=str(project_dir)))
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        _set_chat_input_value(app, "/sessions ")
        await pilot.pause()

        names = [str(widget.render()) for widget in app.query(".command-suggestion-name")]
        descriptions = [str(widget.render()) for widget in app.query(".command-suggestion-desc")]

        assert "修复登录流程" in names
        assert any("session-a" in item for item in descriptions)
        assert any("ship release" in item for item in descriptions)
        assert "/sessions" not in names

@pytest.mark.asyncio
async def test_initial_session_query_prefills_sessions_command():
    """命令行带来的恢复查询应在 UI 内预填成 `/sessions <query>`，而不是弹前置选择器。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge, initial_session_query="recent")

    async with app.run_test() as pilot:
        await pilot.pause()

        assert _get_chat_input_value(app) == "/sessions recent"

@pytest.mark.asyncio
async def test_enter_on_partial_argument_completes_selected_argument_before_execution():
    """参数阶段第一次回车应补全当前高亮参数，第二次才执行。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        _set_chat_input_value(app, "/permissions bypass c")
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        assert _get_chat_input_value(app) == "/permissions bypass confirm"
        assert not list(app.query(InfoMessageWidget))

@pytest.mark.asyncio
async def test_permission_interaction_renders_inline_and_does_not_use_modal_wait(monkeypatch):
    """权限交互应以内联组件呈现，而不是通过 push_screen_wait 阻塞。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async def _unexpected_modal(*args, **kwargs):
        raise AssertionError("push_screen_wait should not be used")

    monkeypatch.setattr(app, "push_screen_wait", _unexpected_modal)

    async with app.run_test() as pilot:
        request_task = asyncio.create_task(
            app.request_permission(
                tool_name="Bash",
                tool_info="$ ls -la",
                message="Need approval",
            )
        )
        await pilot.pause()

        assert app.query_one("#interaction-host").display is True
        assert app.query_one(".dialog-title") is not None
        assert app.query_one("#interaction-host").size.height > 0

        await pilot.press("y")
        await pilot.pause()

        assert await request_task == "allow_once"
        assert app.query_one("#interaction-host").display is False

@pytest.mark.asyncio
async def test_permission_interaction_stays_inside_input_panel():
    """权限交互应像 slash 菜单一样贴在输入区内部，而不是另起大块面板。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test(size=(100, 28)) as pilot:
        request_task = asyncio.create_task(
            app.request_permission(
                tool_name="Write",
                tool_info="write app.py",
                message="Need approval",
            )
        )
        await pilot.pause()

        host = app.query_one("#interaction-host")
        panel = app.query_one("#selection-panel")
        input_panel = app.query_one("#input-panel")
        input_shell = app.query_one(".input-shell")

        assert host.display is True
        assert host.region.y >= input_panel.region.y
        assert panel.region.y >= input_panel.region.y
        assert panel.region.y + panel.region.height <= input_shell.region.y
        assert input_shell.region.y - (panel.region.y + panel.region.height) <= 2

        await pilot.press("y")
        await pilot.pause()
        assert await request_task == "allow_once"

@pytest.mark.asyncio
async def test_permission_resolution_in_stream_keeps_app_responsive():
    """真实流式权限确认后应继续输出，并且界面还能继续下一轮交互。"""
    bridge = UIBridge(PermissionFlowEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test(size=(100, 28)) as pilot:
        _set_chat_input_value(app, "needs approval")
        await pilot.press("enter")
        await pilot.pause()

        assert app.query_one("#interaction-host").display is True
        await pilot.press("y")
        await pilot.pause()
        await pilot.pause()

        assistants = [message for message in bridge.messages if message.role == "assistant"]
        assert assistants
        assert "permission=allow_once" in assistants[-1].content
        assert app.query_one("#interaction-host").display is False
        assert app.focused is _chat_input(app)

        _set_chat_input_value(app, "second turn")
        await pilot.press("enter")
        await pilot.pause()

        users = list(app.query(UserMessageWidget))
        assert len(users) >= 2

@pytest.mark.asyncio
async def test_permission_interaction_supports_allow_always_shortcut():
    """权限交互应支持 allow_always 快捷键。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        request_task = asyncio.create_task(
            app.request_permission(
                tool_name="Write",
                tool_info="write app.py",
                message="Need approval",
            )
        )
        await pilot.pause()

        await pilot.press("a")
        await pilot.pause()

        assert await request_task == "allow_always"
        assert app.query_one("#interaction-host").display is False

@pytest.mark.asyncio
async def test_permission_interaction_supports_clickable_actions():
    """权限交互不应只靠快捷键，用户应能直接点击操作按钮。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        request_task = asyncio.create_task(
            app.request_permission(
                tool_name="Write",
                tool_info="write app.py",
                message="Need approval",
            )
        )
        await pilot.pause()

        await pilot.click("#permission-allow-once")
        await pilot.pause()

        assert await request_task == "allow_once"
        assert app.query_one("#interaction-host").display is False

@pytest.mark.asyncio
async def test_permission_interaction_can_switch_to_bypass_mode_inline(tmp_path, monkeypatch):
    """权限条内应可直接切到 bypass，并立即反映到 header。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    engine = StreamingEngine()
    bridge = UIBridge(engine)
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        request_task = asyncio.create_task(
            app.request_permission(
                tool_name="Write",
                tool_info="write app.py",
                message="Need approval",
            )
        )
        await pilot.pause()

        await pilot.press("b")
        await pilot.pause()

        permission_context = engine.execution_context["permission_context"]
        assert permission_context.mode == PermissionMode.BYPASS_PERMISSIONS
        assert app.query_one("#interaction-host").display is True
        assert "权限 直通" in str(app.query_one("#header-line-2", Static).render())

        await pilot.press("y")
        await pilot.pause()
        assert await request_task == "allow_once"

@pytest.mark.asyncio
async def test_permission_interaction_can_switch_to_ask_strict_inline(tmp_path, monkeypatch):
    """权限条内应可直接切回 ask strict，并清空 session allow 规则。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    engine = StreamingEngine()
    permission_context = engine.execution_context["permission_context"]
    permission_context.mode = PermissionMode.BYPASS_PERMISSIONS
    permission_context.always_allow_rules[PermissionRuleSource.SESSION] = ["Write", "Bash"]
    bridge = UIBridge(engine)
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        request_task = asyncio.create_task(
            app.request_permission(
                tool_name="Write",
                tool_info="write app.py",
                message="Need approval",
            )
        )
        await pilot.pause()

        await pilot.press("s")
        await pilot.pause()

        assert permission_context.mode == PermissionMode.DEFAULT
        assert permission_context.always_allow_rules[PermissionRuleSource.SESSION] == []
        assert app.query_one("#interaction-host").display is True
        assert "权限 询问" in str(app.query_one("#header-line-2", Static).render())

        await pilot.press("y")
        await pilot.pause()
        assert await request_task == "allow_once"

@pytest.mark.asyncio
async def test_permission_interaction_can_mount_twice_sequentially():
    """权限组件连续挂载两次也不应因子节点时序问题崩溃。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        first_request = asyncio.create_task(
            app.request_permission(
                tool_name="Write",
                tool_info="write app.py",
                message="Need approval",
            )
        )
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert await first_request == "allow_once"

        second_request = asyncio.create_task(
            app.request_permission(
                tool_name="Bash",
                tool_info="$ ls -la",
                message="Need approval again",
            )
        )
        await pilot.pause()
        assert app.query_one("#interaction-host").display is True
        await pilot.press("n")
        await pilot.pause()
        assert await second_request == "deny"
        assert app.query_one("#interaction-host").display is False

@pytest.mark.asyncio
async def test_permission_resolution_restores_focus_to_chat_input():
    """权限确认结束后，焦点应回到聊天输入框，避免界面看起来像死掉。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        request_task = asyncio.create_task(
            app.request_permission(
                tool_name="Write",
                tool_info="write app.py",
                message="Need approval",
            )
        )
        await pilot.pause()

        await pilot.press("y")
        await pilot.pause()

        assert await request_task == "allow_once"
        assert app.focused is _chat_input(app)

@pytest.mark.asyncio
async def test_permission_interaction_is_not_refocused_on_housekeeping_ticks():
    """同一个权限请求展示后，housekeeping 不应重复触发 focus，避免条目闪烁。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        request_task = asyncio.create_task(
            app.request_permission(
                tool_name="Write",
                tool_info="write app.py",
                message="Need approval",
            )
        )
        await pilot.pause()

        host = app.query_one("#interaction-host")
        focus_calls: list[int] = []
        original_focus = host.focus_interaction

        def _tracked_focus() -> None:
            focus_calls.append(1)
            original_focus()

        host.focus_interaction = _tracked_focus  # type: ignore[method-assign]

        await app._tick_housekeeping()
        await app._tick_housekeeping()

        assert not focus_calls

        await pilot.press("y")
        await pilot.pause()
        assert await request_task == "allow_once"

@pytest.mark.asyncio
async def test_running_tool_card_stays_compact_during_live_execution():
    """运行中的工具卡片应保持紧凑，不应占用大面积消息区。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test(size=(120, 40)) as pilot:
        bridge.begin_user_turn("glob it")
        bridge.apply_stream_event({"type": "stream_request_start"})
        bridge.apply_stream_event(
            {
                "type": "tool_started",
                "tool_use_id": "tool-1",
                "tool_name": "Glob",
                "input_preview": '{"pattern":"*"}',
                "status": "running",
            }
        )
        await pilot.pause()

        card = app.query_one(".tool-call-card")
        assert card.size.height <= 3

@pytest.mark.asyncio
async def test_live_tool_cards_show_human_action_summaries_instead_of_raw_json():
    """LIVE 工具卡片应显示动作描述，而不是原始 input JSON。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test(size=(120, 36)) as pilot:
        bridge.begin_user_turn("inspect files")
        bridge.apply_stream_event({"type": "stream_request_start"})
        bridge.apply_stream_event(
            {
                "type": "tool_started",
                "tool_use_id": "tool-glob",
                "tool_name": "Glob",
                "input_preview": "{'path': '新建文件夹', 'pattern': '*'}",
                "status": "running",
            }
        )
        bridge.apply_stream_event(
            {
                "type": "tool_started",
                "tool_use_id": "tool-read",
                "tool_name": "Read",
                "input_preview": "{'file_path': 'C:\\\\Users\\\\tzm\\\\Desktop\\\\test\\\\test\\\\index.html'}",
                "status": "running",
            }
        )
        await pilot.pause()

        inline_texts = [
            str(widget.render())
            for widget in app.query(".tool-call-inline")
            if widget.display
        ]

        assert any("扫描 新建文件夹，匹配 *" in text for text in inline_texts)
        assert any("读取 index.html" in text for text in inline_texts)
        assert not any("{'path':" in text or "{'file_path':" in text for text in inline_texts)

@pytest.mark.asyncio
async def test_tool_card_updates_in_place_without_remounting():
    """同一个工具在 progress/result 更新时应原位刷新，避免闪烁。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        bridge.begin_user_turn("run ls")
        bridge.apply_stream_event({"type": "stream_request_start"})
        bridge.apply_stream_event(
            {
                "type": "tool_started",
                "tool_use_id": "tool-1",
                "tool_name": "Bash",
                "input_preview": '{"command":"ls"}',
                "status": "running",
            }
        )
        await pilot.pause()

        first = app.query_one(".tool-call-card")

        bridge.apply_stream_event(
            {
                "type": "tool_progress",
                "tool_use_id": "tool-1",
                "tool_name": "Bash",
                "progress": "still running",
            }
        )
        await pilot.pause()

        second = app.query_one(".tool-call-card")
        assert second is first

        bridge.apply_stream_event(
            {
                "type": "tool_result",
                "tool_use_id": "tool-1",
                "content": "Listed files",
                "receipt": {
                    "kind": "command",
                    "summary": "Listed files",
                    "command": "ls",
                    "exit_code": 0,
                    "stdout": "a.txt\nb.txt",
                    "stderr": "",
                },
                "status": "completed",
                "is_error": False,
            }
        )
        await pilot.pause()

        third = app.query_one(".tool-call-card")
        assert third is first

@pytest.mark.asyncio
async def test_housekeeping_does_not_rebuild_existing_tool_receipt_widgets():
    """housekeeping tick 只能更新外层 chrome，不能重建已完成工具的 receipt 子树。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        bridge.begin_user_turn("run ls")
        bridge.apply_stream_event({"type": "stream_request_start"})
        bridge.apply_stream_event(
            {
                "type": "tool_result",
                "tool_use_id": "tool-1",
                "content": "Listed repository files",
                "receipt": {
                    "kind": "command",
                    "summary": "Listed repository files",
                    "command": "ls -la",
                    "exit_code": 0,
                    "stdout": "a.txt\nb.txt",
                    "stderr": "",
                },
                "audit_events": [],
                "is_error": False,
                "status": "completed",
            }
        )
        bridge.apply_stream_event({"type": "message_stop"})
        await pilot.pause()

        receipt = app.query_one(".receipt-command")
        await app._tick_housekeeping()
        await pilot.pause()
        assert app.query_one(".receipt-command") is receipt

@pytest.mark.asyncio
async def test_generic_tool_result_keeps_compact_summary_without_duplicate_box():
    """普通工具完成后应只保留摘要，不再额外挂一个大号 generic receipt 框。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test(size=(120, 36)) as pilot:
        bridge.begin_user_turn("glob it")
        bridge.apply_stream_event({"type": "stream_request_start"})
        bridge.apply_stream_event(
            {
                "type": "tool_started",
                "tool_use_id": "tool-1",
                "tool_name": "Glob",
                "input_preview": '{"pattern":"**/*"}',
                "status": "running",
            }
        )
        bridge.apply_stream_event(
            {
                "type": "tool_result",
                "tool_use_id": "tool-1",
                "content": "durationMs=1 numFiles=4 filenames=['script.js','style.css'] truncated=False",
                "receipt": {
                    "kind": "generic",
                    "summary": "Matched 4 files",
                    "body": "durationMs=1 numFiles=4 filenames=['script.js','style.css'] truncated=False",
                },
                "audit_events": [],
                "is_error": False,
                "status": "completed",
            }
        )
        bridge.apply_stream_event({"type": "message_stop"})
        await pilot.pause()

        card = app.query_one(".tool-call-card")
        assert card.size.height <= 3
        assert not list(app.query(".receipt-generic"))
        visible_summaries = [widget for widget in app.query(".tool-call-summary") if widget.display]
        assert not visible_summaries

@pytest.mark.asyncio
async def test_completed_read_card_shows_filename_summary_not_file_body():
    """Read 完成后主摘要应是文件级短句，而不是文件正文。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test(size=(120, 36)) as pilot:
        bridge.begin_user_turn("read css")
        bridge.apply_stream_event({"type": "stream_request_start"})
        bridge.apply_stream_event(
            {
                "type": "tool_started",
                "tool_use_id": "tool-read",
                "tool_name": "Read",
                "input_preview": "{'file_path': 'C:\\\\Users\\\\tzm\\\\Desktop\\\\test\\\\test\\\\style.css'}",
                "status": "running",
            }
        )
        bridge.apply_stream_event(
            {
                "type": "tool_result",
                "tool_use_id": "tool-read",
                "content": "1\t* {\n2\t  margin: 0;\n3\t  padding: 0;\n4\t}\n",
                "receipt": {
                    "kind": "generic",
                    "summary": "1\t* {\n2\t  margin: 0;\n3\t  padding: 0;\n4\t}\n",
                    "body": "1\t* {\n2\t  margin: 0;\n3\t  padding: 0;\n4\t}\n",
                },
                "audit_events": [],
                "is_error": False,
                "status": "completed",
            }
        )
        bridge.apply_stream_event({"type": "message_stop"})
        await pilot.pause()

        inline_texts = [
            str(widget.render())
            for widget in app.query(".tool-call-inline")
            if widget.display
        ]

        assert any("已读取 style.css" in text for text in inline_texts)
        assert not any("margin: 0" in text or "padding: 0" in text for text in inline_texts)
        assert app.query_one(".tool-call-card").size.height <= 3

@pytest.mark.asyncio
async def test_completed_glob_card_shows_match_count_summary_not_dump():
    """Glob 完成后主摘要应是匹配数量，而不是 duration/filenames dump。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test(size=(120, 36)) as pilot:
        bridge.begin_user_turn("glob it")
        bridge.apply_stream_event({"type": "stream_request_start"})
        bridge.apply_stream_event(
            {
                "type": "tool_started",
                "tool_use_id": "tool-glob",
                "tool_name": "Glob",
                "input_preview": "{'path': '新建文件夹', 'pattern': '*'}",
                "status": "running",
            }
        )
        bridge.apply_stream_event(
            {
                "type": "tool_result",
                "tool_use_id": "tool-glob",
                "content": "durationMs=1 numFiles=4 filenames=['script.js','style.css','index.html'] truncated=False",
                "receipt": {
                    "kind": "generic",
                    "summary": "durationMs=1 numFiles=4 filenames=['script.js','style.css','index.html'] truncated=False",
                    "body": "durationMs=1 numFiles=4 filenames=['script.js','style.css','index.html'] truncated=False",
                },
                "audit_events": [],
                "is_error": False,
                "status": "completed",
            }
        )
        bridge.apply_stream_event({"type": "message_stop"})
        await pilot.pause()

        inline_texts = [
            str(widget.render())
            for widget in app.query(".tool-call-inline")
            if widget.display
        ]

        assert any("匹配到 4 个文件" in text for text in inline_texts)
        assert not any("durationMs=1" in text or "filenames=[" in text for text in inline_texts)
        assert app.query_one(".tool-call-card").size.height <= 3

class _QuestionOption:
    def __init__(self, label: str, description: str = ""):
        self.label = label
        self.description = description
        self.preview = ""

class _Question:
    def __init__(self, header: str, question: str, options, multi_select: bool = False):
        self.header = header
        self.question = question
        self.options = options
        self.multiSelect = multi_select

@pytest.mark.asyncio
async def test_question_interaction_collects_answer_inline():
    """问答交互应以内联卡片呈现，并把答案回传给 bridge future。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)
    questions = [
        _Question(
            header="Language",
            question="Which language?",
            options=[_QuestionOption("Python"), _QuestionOption("Go")],
        )
    ]

    async with app.run_test() as pilot:
        request_task = asyncio.create_task(app.request_questions(questions))
        await pilot.pause()

        assert app.query_one("#interaction-host").display is True
        assert str(app.query_one("#question-header", Static).render()) == "Language"

        await pilot.press("1")
        await pilot.pause()

        assert await request_task == {"Which language?": "Python"}
        assert app.query_one("#interaction-host").display is False

@pytest.mark.asyncio
async def test_question_interaction_supports_clickable_single_choice_option():
    """单选问题应支持直接点击选项完成，而不是必须手输编号。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)
    questions = [
        _Question(
            header="Language",
            question="Which language?",
            options=[_QuestionOption("Python"), _QuestionOption("Go")],
        )
    ]

    async with app.run_test() as pilot:
        request_task = asyncio.create_task(app.request_questions(questions))
        await pilot.pause()

        await pilot.click("#question-option-1")
        await pilot.pause()

        assert await request_task == {"Which language?": "Python"}
        assert app.query_one("#interaction-host").display is False

@pytest.mark.asyncio
async def test_question_interaction_supports_multi_select_submission():
    """多选问题应支持输入 1,2 后回车提交。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)
    questions = [
        _Question(
            header="Features",
            question="Which features?",
            options=[_QuestionOption("Todo"), _QuestionOption("Team"), _QuestionOption("Diff")],
            multi_select=True,
        )
    ]

    async with app.run_test() as pilot:
        request_task = asyncio.create_task(app.request_questions(questions))
        await pilot.pause()

        input_widget = app.query_one("#question-input", Input)
        input_widget.value = "1,2"
        await pilot.press("enter")
        await pilot.pause()

        assert await request_task == {"Which features?": "Todo, Team"}
        assert app.query_one("#interaction-host").display is False

@pytest.mark.asyncio
async def test_question_interaction_scrolls_to_keep_lower_options_visible():
    """问题选项较多时，选项区应可滚动，并跟随当前高亮项。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)
    questions = [
        _Question(
            header="Language",
            question="Which language?",
            options=[
                _QuestionOption("Python", "保留当前栈"),
                _QuestionOption("Go", "切到静态编译"),
                _QuestionOption("Rust", "强调内存安全"),
                _QuestionOption("TypeScript", "前后端统一"),
                _QuestionOption("Java", "企业项目常见"),
                _QuestionOption("C#", "桌面和服务端都可用"),
            ],
        )
    ]

    async with app.run_test(size=(90, 20)) as pilot:
        request_task = asyncio.create_task(app.request_questions(questions))
        await pilot.pause()

        option_list = app.query_one("#question-option-list", VerticalScroll)
        assert option_list.max_scroll_y > 0
        assert option_list.scroll_y == 0

        for _ in range(4):
            await pilot.press("down")
            await pilot.pause()

        assert option_list.scroll_y > 0

        await pilot.press("enter")
        await pilot.pause()

        assert await request_task == {"Which language?": "Java"}
        assert app.query_one("#interaction-host").display is False

@pytest.mark.asyncio
async def test_runtime_question_interaction_renders_inline_and_completes_stream():
    """真实 runtime question 事件应挂到输入区交互宿主，而不是退化成工具错误。"""
    bridge = UIBridge(RuntimeQuestionFlowEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test(size=(100, 28)) as pilot:
        _set_chat_input_value(app, "请继续")
        await pilot.press("enter")
        await pilot.pause()

        assert app.query_one("#interaction-host").display is True
        assert str(app.query_one("#question-header", Static).render()) == "语言"

        await pilot.press("1")
        await pilot.pause()
        await pilot.pause()

        assert app.query_one("#interaction-host").display is False
        assistants = [message for message in bridge.messages if message.role == "assistant"]
        assert assistants
        assert any("已收到答案" in (call.result or "") for call in assistants[-1].tool_calls)

@pytest.mark.asyncio
async def test_diff_review_interaction_accepts_inline():
    """Diff review 应以内联卡片呈现，并支持键盘接受。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)
    change = ProposedFileChange(
        change_id="chg_1",
        path="C:/tmp/app.py",
        original_content="old",
        new_content="new",
        diff_text="@@ -1 +1 @@\n-old\n+new",
        source_tool="Edit",
    )

    async with app.run_test() as pilot:
        request_task = asyncio.create_task(app.request_change_review(change))
        await pilot.pause()

        assert app.query_one("#interaction-host").display is True
        assert "审阅变更" in str(app.query_one(".dialog-title", Static).render())

        await pilot.press("y")
        await pilot.pause()

        assert await request_task == "accept"
        assert app.query_one("#interaction-host").display is False

@pytest.mark.asyncio
async def test_diff_review_interaction_supports_clickable_accept_button():
    """Diff 审阅应支持直接点击接受按钮。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)
    change = ProposedFileChange(
        change_id="chg_click_accept",
        path="C:/tmp/app.py",
        original_content="old",
        new_content="new",
        diff_text="@@ -1 +1 @@\n-old\n+new",
        source_tool="Edit",
    )

    async with app.run_test() as pilot:
        request_task = asyncio.create_task(app.request_change_review(change))
        await pilot.pause()

        await pilot.click("#diff-accept")
        await pilot.pause()

        assert await request_task == "accept"
        assert app.query_one("#interaction-host").display is False

@pytest.mark.asyncio
async def test_diff_review_interaction_rejects_inline():
    """Diff review 应支持键盘拒绝。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)
    change = ProposedFileChange(
        change_id="chg_2",
        path="C:/tmp/app.py",
        original_content="old",
        new_content="new",
        diff_text="@@ -1 +1 @@\n-old\n+new",
        source_tool="Edit",
    )

    async with app.run_test() as pilot:
        request_task = asyncio.create_task(app.request_change_review(change))
        await pilot.pause()

        await pilot.press("n")
        await pilot.pause()

        assert await request_task == "reject"
        assert app.query_one("#interaction-host").display is False

@pytest.mark.asyncio
async def test_diff_review_interaction_supports_full_view_toggle():
    """Diff review 按 V 后应进入全屏 side-by-side 视图。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)
    change = ProposedFileChange(
        change_id="chg_3",
        path="C:/tmp/app.py",
        original_content="old line\nsecond old line",
        new_content="new line\nsecond new line",
        diff_text="@@ -1,2 +1,2 @@\n-old line\n-second old line\n+new line\n+second new line",
        source_tool="Edit",
    )

    async with app.run_test(size=(120, 32)) as pilot:
        request_task = asyncio.create_task(app.request_change_review(change))
        await pilot.pause()

        await pilot.press("v")
        await pilot.pause()

        assert getattr(app.screen, "id", "") == "diff-full-screen"
        assert "old line" in str(app.screen.query_one("#diff-full-before", Static).render())
        assert "new line" in str(app.screen.query_one("#diff-full-after", Static).render())

        await pilot.press("y")
        await pilot.pause()

        assert await request_task == "accept"

@pytest.mark.asyncio
async def test_assistant_long_content_collapses_and_can_expand():
    """长 assistant 正文完成后应先封箱，并允许用户展开查看完整 Markdown。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)
    long_text = "\n".join(f"line {index}" for index in range(1, 55))

    async with app.run_test(size=(100, 24)) as pilot:
        bridge.begin_user_turn("show long answer")
        bridge.apply_stream_event({"type": "stream_request_start"})
        bridge.apply_stream_event({"type": "text_delta", "delta": {"text": long_text}})
        bridge.apply_stream_event({"type": "message_stop"})
        await pilot.pause()

        assistant = list(app.query(AssistantMessageWidget))[-1]
        collapsed = assistant.query_one(".assistant-collapsed-preview", Static)
        markdown = assistant.query_one(".assistant-markdown", Markdown)
        button = assistant.query_one(".assistant-expand-button", Button)

        assert collapsed.display is True
        assert markdown.display is False
        assert button.display is True
        assert "还有" in str(collapsed.render())

        button.press()
        await pilot.pause()

        assert collapsed.display is False
        assert markdown.display is True

@pytest.mark.asyncio
async def test_assistant_card_renders_structured_receipt_widgets():
    """工具结果应渲染为 receipt 组件树，而不是拼接字符串。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        bridge.begin_user_turn("run ls")
        bridge.apply_stream_event({"type": "stream_request_start"})
        bridge.apply_stream_event(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": "tool-1", "name": "Bash"},
            }
        )
        bridge.apply_stream_event(
            {
                "type": "input_json_delta",
                "delta": {"partial_json": '{"command":"ls -la"}'},
            }
        )
        bridge.apply_stream_event(
            {
                "type": "tool_result",
                "tool_use_id": "tool-1",
                "content": "Listed repository files",
                "receipt": {
                    "kind": "command",
                    "summary": "Listed repository files",
                    "command": "ls -la",
                    "exit_code": 0,
                    "stdout": "a.txt\nb.txt",
                    "stderr": "",
                },
                "audit_events": [
                    {
                        "event_id": "evt-1",
                        "agent_id": "assistant",
                        "source": "tool",
                        "message": "Command completed",
                        "created_at": 0.0,
                        "metadata": {"cwd": "/tmp"},
                    }
                ],
                "is_error": False,
                "status": "completed",
            }
        )
        bridge.apply_stream_event({"type": "message_stop"})
        await pilot.pause()

        assert list(app.query(".tool-call-card"))
        assert list(app.query(".receipt-command"))
        assert list(app.query(".tool-card-header"))
        assert list(app.query(".tool-receipt-host"))
        assert list(app.query(".audit-event-card")) == []
        assert list(app.query(".tool-audit-stack")) == []

@pytest.mark.asyncio
async def test_command_receipt_formats_output_without_duplicate_input_block():
    """命令类工具结果应显示结构化输出，不应重复渲染原始 input preview。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        bridge.begin_user_turn("run ls")
        bridge.apply_stream_event({"type": "stream_request_start"})
        bridge.apply_stream_event(
            {
                "type": "tool_started",
                "tool_use_id": "tool-1",
                "tool_name": "Bash",
                "input_preview": '{"command":"ls -la"}',
                "status": "running",
            }
        )
        bridge.apply_stream_event(
            {
                "type": "tool_result",
                "tool_use_id": "tool-1",
                "content": "Listed repository files",
                "receipt": {
                    "kind": "command",
                    "summary": "Listed repository files",
                    "command": "ls -la",
                    "exit_code": 0,
                    "stdout": "a.txt\nb.txt",
                    "stderr": "warning",
                },
                "audit_events": [],
                "is_error": False,
                "status": "completed",
            }
        )
        bridge.apply_stream_event({"type": "message_stop"})
        await pilot.pause()

        visible_input_blocks = [widget for widget in app.query(".tool-call-input") if widget.display]
        assert not visible_input_blocks
        assert list(app.query(".receipt-command-line"))
        assert list(app.query(".receipt-section-label"))
        assert len(list(app.query(".receipt-section-label"))) >= 2
        assert list(app.query(".receipt-terminal"))

@pytest.mark.asyncio
async def test_command_receipt_is_collapsed_by_default_until_user_expands():
    """命令类工具结果默认应折叠成紧凑摘要，展开后才显示详细输出。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test(size=(120, 36)) as pilot:
        bridge.begin_user_turn("run dir docs")
        bridge.apply_stream_event({"type": "stream_request_start"})
        bridge.apply_stream_event(
            {
                "type": "tool_started",
                "tool_use_id": "tool-dir",
                "tool_name": "Bash",
                "input_preview": '{"command":"dir docs"}',
                "status": "running",
            }
        )
        bridge.apply_stream_event(
            {
                "type": "tool_result",
                "tool_use_id": "tool-dir",
                "content": "Listed docs directory",
                "receipt": {
                    "kind": "command",
                    "summary": "Listed docs directory",
                    "command": "dir docs",
                    "exit_code": 0,
                    "stdout": "Directory of docs",
                    "stderr": "",
                },
                "audit_events": [],
                "is_error": False,
                "status": "completed",
            }
        )
        bridge.apply_stream_event({"type": "message_stop"})
        await pilot.pause()

        tool_card = app.query_one(".tool-call-card")
        toggle = tool_card.query_one(".tool-receipt-toggle", Button)
        receipt_host = tool_card.query_one(".tool-receipt-host")

        assert tool_card.has_class("-compact")
        assert tool_card.size.height <= 3
        assert toggle.display is True
        assert "展开" in str(toggle.render())
        assert receipt_host.display is False

        await pilot.click(".tool-receipt-toggle")
        await pilot.pause()

        assert receipt_host.display is True
        assert "收起" in str(toggle.render())

@pytest.mark.asyncio
async def test_tool_status_hides_empty_placeholder_calls_when_real_tool_exists():
    """已有真实工具摘要时，空壳 Tool 占位行不应继续污染工具区。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test(size=(120, 36)) as pilot:
        bridge.begin_user_turn("run bash")
        bridge.apply_stream_event({"type": "stream_request_start"})
        bridge.apply_stream_event(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": "tool-real", "name": "Bash"},
            }
        )
        bridge.apply_stream_event(
            {
                "type": "tool_started",
                "tool_use_id": "tool-real",
                "tool_name": "Bash",
                "input_preview": '{"command":"ls -la"}',
                "status": "running",
            }
        )
        bridge.apply_stream_event(
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "tool_use", "id": "tool-placeholder", "name": "Tool"},
            }
        )
        bridge.apply_stream_event(
            {
                "type": "tool_result",
                "tool_use_id": "tool-real",
                "content": "Listed repository files",
                "receipt": {
                    "kind": "command",
                    "summary": "Listed repository files",
                    "command": "ls -la",
                    "exit_code": 0,
                    "stdout": "a.txt\nb.txt",
                    "stderr": "",
                },
                "audit_events": [],
                "is_error": False,
                "status": "completed",
            }
        )
        bridge.apply_stream_event({"type": "message_stop"})
        await pilot.pause()

        cards = list(app.query(".tool-call-card"))

        assert len(cards) == 1
        assert "Bash" in str(cards[0].query_one(".tool-call-name", Static).render())

@pytest.mark.asyncio
async def test_assistant_card_renders_agent_child_card():
    """Agent 工具结果应渲染为独立 agent 子卡片。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        bridge.begin_user_turn("delegate this")
        bridge.apply_stream_event({"type": "stream_request_start"})
        bridge.apply_stream_event(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": "tool-agent", "name": "Agent"},
            }
        )
        bridge.apply_stream_event(
            {
                "type": "tool_result",
                "tool_use_id": "tool-agent",
                "content": "Background task started: task_123",
                "receipt": {
                    "kind": "agent",
                    "summary": "Spawned Explore agent",
                    "agent_id": "agent_42",
                    "agent_type": "Explore",
                    "mode": "fresh",
                    "task_id": "task_123",
                    "background": True,
                    "status": "running",
                    "result_preview": "Searching the repository",
                    "total_tokens": 77,
                },
                "audit_events": [
                    {
                        "event_id": "evt-agent",
                        "agent_id": "agent_42",
                        "source": "agent",
                        "message": "Agent accepted delegated task",
                        "created_at": 0.0,
                        "metadata": {"mode": "fresh"},
                    }
                ],
                "is_error": False,
                "status": "completed",
            }
        )
        bridge.apply_stream_event({"type": "message_stop"})
        await pilot.pause()

        assert list(app.query(".receipt-agent"))
        assert list(app.query(".agent-child-card"))

@pytest.mark.asyncio
async def test_assistant_card_renders_live_nested_agent_stream():
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        bridge.begin_user_turn("delegate this")
        bridge.apply_stream_event({"type": "stream_request_start"})
        bridge.apply_stream_event(
            {
                "type": "agent_started",
                "agent_id": "agent_live_1",
                "label": "Explore > Search repository",
                "agent_type": "Explore",
                "mode": "fresh",
                "background": False,
                "status": "running",
            }
        )
        bridge.apply_stream_event(
            {
                "type": "agent_delta",
                "agent_id": "agent_live_1",
                "content_delta": "Scanning the source tree",
                "status": "thinking",
            }
        )
        bridge.apply_stream_event(
            {
                "type": "agent_completed",
                "agent_id": "agent_live_1",
                "result": "Located the failing handler",
                "status": "completed",
            }
        )
        bridge.apply_stream_event({"type": "message_stop"})
        await pilot.pause()

        assert list(app.query(".agent-stream-card"))
        assert list(app.query(".agent-stream-title"))

@pytest.mark.asyncio
async def test_command_menu_renders_structured_overlay_rows():
    """命令浮层应使用结构化行，而不是纯字符串拼接。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        _set_chat_input_value(app, "/co")
        await pilot.pause()

        assert list(app.query(".command-suggestion-row"))
        assert list(app.query(".command-suggestion-name"))
        assert list(app.query(".command-suggestion-desc"))
        assert str(app.query_one("#command-menu-hint", Static).render())

@pytest.mark.asyncio
async def test_assistant_message_area_is_not_boxed():
    """AI 正文区应去框化，避免整块卡片把正文包起来。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        bridge.begin_user_turn("hello")
        bridge.apply_stream_event({"type": "stream_request_start"})
        bridge.apply_stream_event({"type": "text_delta", "delta": {"text": "Hello world"}})
        bridge.apply_stream_event({"type": "message_stop"})
        await pilot.pause()

        card = app.query_one(".ai-card")
        assert card.styles.background.a == 0
        assert card.styles.border_left[0] != "solid"

@pytest.mark.asyncio
async def test_command_menu_stays_compact_with_limited_visible_rows():
    """/ 输入时的命令浮层应保持低矮，并通过内部滚动承载完整候选项。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test(size=(100, 28)) as pilot:
        _set_chat_input_value(app, "/")
        await pilot.pause()

        menu_body = app.query_one("#command-menu-body")
        menu_list = app.query_one("#command-menu-list")
        visible_rows = [row for row in app.query(".command-suggestion-row") if row.display]

        assert len(visible_rows) > 5
        assert menu_body.size.height <= 8
        assert getattr(menu_list, "max_scroll_y", 0) > 0

        for _ in range(5):
            await pilot.press("down")
            await pilot.pause()

        assert menu_list.scroll_y > 0

@pytest.mark.asyncio
async def test_command_menu_scrolls_when_rendered_viewport_is_shorter_than_nominal_rows():
    """如果实际可见高度只够 3 行，选中第 4 项时也必须自动跟随。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test(size=(100, 28)) as pilot:
        _set_chat_input_value(app, "/")
        await pilot.pause()

        menu_body = app.query_one("#command-menu-body")
        menu_list = app.query_one("#command-menu-list")
        menu_body.styles.height = 4
        await pilot.pause()

        for _ in range(3):
            await pilot.press("down")
            await pilot.pause()

        selected = app.query_one(".command-suggestion-row.-selected")
        viewport_top = menu_body.region.y + 1
        viewport_bottom = menu_body.region.y + menu_body.region.height

        assert selected.region.y >= viewport_top
        assert selected.region.y + selected.region.height <= viewport_bottom
        assert menu_list.scroll_y > 0

@pytest.mark.asyncio
async def test_command_menu_scroll_uses_list_viewport_instead_of_outer_body_height():
    """选中项可见性应基于候选列表视口，而不是整个浮层外壳。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test(size=(100, 28)) as pilot:
        _set_chat_input_value(app, "/")
        await pilot.pause()

        menu_body = app.query_one("#command-menu-body")
        menu_list = app.query_one("#command-menu-list")
        menu_body.styles.height = 5
        menu_list.styles.height = 3
        await pilot.pause()

        for _ in range(3):
            await pilot.press("down")
            await pilot.pause()

        selected = app.query_one(".command-suggestion-row.-selected")
        viewport_top = menu_list.region.y
        viewport_bottom = menu_list.region.y + menu_list.region.height

        assert selected.region.y >= viewport_top
        assert selected.region.y + selected.region.height <= viewport_bottom
        assert menu_list.scroll_y > 0

@pytest.mark.asyncio
async def test_command_menu_height_stays_stable_when_selected_item_hint_changes():
    """高亮项切换到有/无 hint 的命令时，浮层高度也不应跳动。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)
    app._commands = [
        Command(name="with-hint", description="带 hint 的命令", argument_hint="<参数>"),
        Command(name="without-hint", description="不带 hint 的命令"),
    ]

    async with app.run_test(size=(100, 28)) as pilot:
        _set_chat_input_value(app, "/")
        await pilot.pause()

        menu_body = app.query_one("#command-menu-body")
        first_height = menu_body.size.height

        await pilot.press("down")
        await pilot.pause()

        later_height = menu_body.size.height

        assert first_height == later_height

@pytest.mark.asyncio
async def test_command_menu_stays_in_input_dock_above_input_shell():
    """slash 菜单应固定在输入区内部、贴着输入框上方，而不是跑到聊天区。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test(size=(100, 28)) as pilot:
        bottom = app.query_one("#app-bottom")

        _set_chat_input_value(app, "/he")
        await pilot.pause()

        menu = app.query_one("#command-menu")
        menu_body = app.query_one("#command-menu-body")
        input_shell = app.query_one(".input-shell")
        input_panel = app.query_one("#input-panel")
        assert menu.display is True
        assert menu_body.region.height > 0
        assert menu.region.y >= bottom.region.y
        assert menu_body.region.y >= input_panel.region.y
        assert menu_body.region.x == input_shell.region.x
        assert menu_body.region.height > 0
        assert menu_body.region.y + menu_body.region.height <= input_shell.region.y
        assert input_shell.region.y - (menu_body.region.y + menu_body.region.height) <= 2

@pytest.mark.asyncio
async def test_toast_floats_without_stealing_bottom_layout_space():
    """Toast 应作为右上角浮层出现，不能把底部输入区挤成一条额外布局。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test(size=(100, 28)) as pilot:
        bottom_before = app.query_one("#app-bottom").region

        bridge.add_toast("已中断当前生成", level="warning", duration=30.0)
        bridge.notify(force=True)
        await pilot.pause()

        bottom_after = app.query_one("#app-bottom").region
        host = app.query_one("#toast-host")
        toast = app.query_one(".toast", Static)

        assert host.display is True
        assert bottom_after.y == bottom_before.y
        assert bottom_after.height == bottom_before.height
        assert toast.region.x > app.size.width // 2
        assert toast.region.y <= 3

@pytest.mark.asyncio
async def test_chat_input_is_compact_by_default_and_grows_with_multiline_text():
    """聊天输入框默认应较矮，输入多行后再逐步增高。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test(size=(90, 28)) as pilot:
        input_widget = _chat_input(app)
        await pilot.pause()
        base_height = input_widget.size.height

        _set_chat_input_value(app, "line1\nline2\nline3")
        await pilot.pause()

        assert base_height <= 2
        assert input_widget.size.height > base_height

@pytest.mark.asyncio
async def test_chat_input_caps_growth_and_scrolls_for_very_long_content():
    """输入框长到阈值后应停止继续长高，并进入内部滚动。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test(size=(90, 28)) as pilot:
        input_widget = _chat_input(app)
        _set_chat_input_value(app, "\n".join(f"line {index}" for index in range(1, 18)))
        await pilot.pause()

        assert input_widget.size.height <= 8
        assert getattr(input_widget, "max_scroll_y", 0) > 0

@pytest.mark.asyncio
async def test_input_panel_updates_visual_state_for_generating_and_interaction():
    """输入区外壳应随生成中和交互等待切换状态类。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        bridge.begin_user_turn("hello")
        bridge.apply_stream_event({"type": "stream_request_start"})
        await pilot.pause()

        input_panel = app.query_one(InputPanel)
        assert input_panel.has_class("-generating")
        assert not input_panel.has_class("-interaction")
        assert "正在生成回复" in str(app.query_one("#input-context", Static).render())
        assert "Ctrl+C" in str(app.query_one("#input-ghost", Static).render())

        request_task = asyncio.create_task(
            app.request_permission(
                tool_name="Write",
                tool_info="write app.py",
                message="Need approval",
            )
        )
        await pilot.pause()

        assert input_panel.has_class("-interaction")
        assert "需要你确认" in str(app.query_one("#input-context", Static).render())
        assert "也可用快捷键" in str(app.query_one("#input-ghost", Static).render())

        await pilot.press("y")
        await pilot.pause()
        await request_task

@pytest.mark.asyncio
async def test_input_panel_surfaces_retry_hint_after_user_interrupt():
    """用户中断后，输入区应提示可以重试上一轮，而不是回到普通空闲文案。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        bridge.begin_user_turn("retry me")
        bridge.apply_stream_event({"type": "stream_request_start"})
        bridge.apply_stream_event(
            {
                "type": "error",
                "error": "User interrupted",
                "error_type": "user_interrupted",
            }
        )
        await pilot.pause()

        assert "可重试上一轮" in str(app.query_one("#input-context", Static).render())
        assert "重试" in str(app.query_one("#input-ghost", Static).render())

@pytest.mark.asyncio
async def test_header_status_lamp_tracks_runtime_state():
    """头部状态灯应跟随 Idle / Active / Thinking 状态切换。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        header = app.query_one(HeaderPanel)
        lamp = header.query_one("#header-status-lamp", Static)
        label = header.query_one("#header-status-label", Static)

        assert lamp.has_class("-idle")
        assert "空闲" in str(label.render())

        bridge.begin_user_turn("status test")
        bridge.apply_stream_event({"type": "stream_request_start"})
        await pilot.pause()

        assert lamp.has_class("-active")
        assert "处理中" in str(label.render())

        bridge.apply_stream_event(
            {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}}
        )
        bridge.apply_stream_event({"type": "thinking_delta", "delta": {"thinking": "analyzing"}})
        await pilot.pause()

        assert lamp.has_class("-thinking")
        assert "思考中" in str(label.render())

@pytest.mark.asyncio
async def test_header_renders_session_title():
    """头部第一行应显示会话标题。没有持久化标题时，至少退化到当前会话内容标题。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        bridge.begin_user_turn("修复登录流程")
        await pilot.pause()

        header = app.query_one(HeaderPanel)
        title = header.query_one("#header-session-title", Static)
        assert "修复登录流程" in str(title.render())

@pytest.mark.asyncio
async def test_header_status_lamp_does_not_flicker_between_pulses():
    """状态灯应保持稳定符号，避免每个 housekeeping tick 都闪烁。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        header = app.query_one(HeaderPanel)
        lamp = header.query_one("#header-status-lamp", Static)

        request_task = asyncio.create_task(
            app.request_permission(
                tool_name="Glob",
                tool_info='{"pattern":"*"}',
                message="Need approval",
            )
        )
        await pilot.pause()

        before = str(lamp.render())
        await app._tick_housekeeping()
        after = str(lamp.render())

        assert before == after

        await pilot.press("y")
        await pilot.pause()
        await request_task

@pytest.mark.asyncio
async def test_input_panel_shows_typing_feedback_when_user_types():
    """输入框有内容时，底部输入区应进入 typing 视觉状态。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        input_panel = app.query_one(InputPanel)

        _set_chat_input_value(app, "/he")
        await pilot.pause()

        assert input_panel.has_class("-typing")
        assert app.query_one("#input-prompt", Static).has_class("-typing")
        assert str(app.query_one("#input-prompt", Static).render()) == "Codo >"
        assert str(app.query_one("#input-indicator", Static).render()) == "●"

        _set_chat_input_value(app, "")
        await pilot.pause()

        assert not input_panel.has_class("-typing")
        assert str(app.query_one("#input-prompt", Static).render()) == "Codo >"
        assert str(app.query_one("#input-indicator", Static).render()) == "○"

@pytest.mark.asyncio
async def test_header_uses_effective_window_and_remaining_tokens_in_status_line():
    """header token 行应展示有效窗口和剩余额度，而不是错误的总窗口口径。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        await pilot.pause()

        line = str(app.query_one("#header-line-2", Static).render())
        assert "令牌 12/180000" in line
        assert "剩余 179988" in line

@pytest.mark.asyncio
async def test_sidebar_renders_only_roster_section_without_focus_panel():
    """侧栏只保留协作成员列表，不再渲染最近动态卡片。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        bridge.apply_stream_event(
            {
                "type": "agent_started",
                "agent_id": "agent_sidebar_1",
                "label": "Explore > Search repository",
                "agent_type": "Explore",
                "mode": "fresh",
                "background": False,
                "status": "running",
            }
        )
        await pilot.pause()

        labels = [str(widget.render()) for widget in app.query(".sidebar-section-label")]
        assert "协作成员" in labels
        assert "任务进度" not in labels
        assert list(app.query("#sidebar-focus-card")) == []
        assert list(app.query("#sidebar-total")) == []
        assert list(app.query(".sidebar-roster-card"))
        assert list(app.query(".sidebar-roster-name"))

@pytest.mark.asyncio
async def test_todo_update_renders_compact_checklist_summary_in_message_flow():
    """Todo 更新应显示成简洁摘要，而不是逐项展开成长清单。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        bridge.begin_user_turn("更新待办")
        bridge.apply_stream_event({"type": "stream_request_start"})
        bridge.apply_stream_event(
            {
                "type": "todo_updated",
                "key": "session-main",
                "items": [
                    {
                        "content": "完成第一个待办任务",
                        "status": "completed",
                        "activeForm": "完成第一个待办任务",
                    },
                    {
                        "content": "完成第二个待办任务",
                        "status": "in_progress",
                        "activeForm": "正在完成第二个待办任务",
                    },
                    {
                        "content": "补充测试",
                        "status": "pending",
                        "activeForm": "补充测试",
                    },
                ],
            }
        )
        await pilot.pause()

        assistant = list(app.query(AssistantMessageWidget))[-1]
        title_text = str(assistant.query_one(".todo-summary-title", Static).render())
        meta_text = str(assistant.query_one(".todo-summary-meta", Static).render())
        list_text = str(assistant.query_one(".todo-summary-list", Static).render())

        assert "更新待办" in title_text
        assert "已完成 1/3" in meta_text
        assert "已完成：完成第一个待办任务" in list_text
        assert "进行中：正在完成第二个待办任务" in list_text
        assert "接下来：补充测试" in list_text
        assert "[x]" not in list_text
        assert "[>]" not in list_text
        assert "[ ]" not in list_text

@pytest.mark.asyncio
async def test_sidebar_roster_card_only_shows_agent_kind_without_task_summary():
    """侧栏 agent 卡片应只保留类型和状态，不再展示长摘要。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test(size=(80, 32)) as pilot:
        bridge.apply_stream_event(
            {
                "type": "agent_started",
                "agent_id": "agent_7301b06f",
                "label": "Explore > 后端 API 设计分析",
                "agent_type": "Explore",
                "status": "running",
                "content": "### 后端 API 设计分析报告\n- 项目处于初期阶段\n- 建议先定义接口\n- 再补实现细节",
            }
        )
        await pilot.pause()

        name_text = str(app.query_one(".sidebar-roster-name", Static).render())
        card = app.query_one(".sidebar-roster-card")

        assert "agent_7301b06f" not in name_text
        assert "Explore" in name_text
        assert list(app.query(".sidebar-roster-task")) == []
        assert card.size.height <= 3

@pytest.mark.asyncio
async def test_sidebar_roster_updates_in_place_during_agent_stream():
    """流式 agent 更新时，侧栏 roster 卡片应原地更新，而不是整卡 remount。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        bridge.apply_stream_event(
            {
                "type": "agent_started",
                "agent_id": "agent_sidebar_live",
                "label": "Worker > Search repo",
                "status": "running",
                "content": "Starting",
            }
        )
        await pilot.pause()

        first_card = app.query(SidebarRosterCardWidget).first()

        bridge.apply_stream_event(
            {
                "type": "agent_delta",
                "agent_id": "agent_sidebar_live",
                "status": "active",
                "content_delta": " more output",
            }
        )
        await pilot.pause()

        second_card = app.query(SidebarRosterCardWidget).first()
        assert second_card is first_card

@pytest.mark.asyncio
async def test_message_column_preserves_manual_scroll_position_when_turn_completes():
    """用户手动滚离底部后，turn 完成不应把消息列滚动位置带跑。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test(size=(90, 20)) as pilot:
        for index in range(8):
            bridge.begin_user_turn(f"user {index}")
            bridge.apply_stream_event({"type": "stream_request_start"})
            bridge.apply_stream_event({"type": "content_block_start", "index": 0, "content_block": {"type": "text"}})
            bridge.apply_stream_event(
                {"type": "text_delta", "delta": {"text": f"这是第 {index} 轮回复，用来撑高消息区域。 " * 4}}
            )
            bridge.apply_stream_event({"type": "message_stop"})
        await pilot.pause()

        bridge.begin_user_turn("final user")
        bridge.apply_stream_event({"type": "stream_request_start"})
        bridge.apply_stream_event({"type": "content_block_start", "index": 0, "content_block": {"type": "text"}})
        bridge.apply_stream_event({"type": "text_delta", "delta": {"text": "这是一段普通文本，没有 markdown。"}})
        await pilot.pause()

        column = app.query_one(MessageColumn)
        target_scroll = max(0, column.max_scroll_y - 6)
        column.scroll_to(y=target_scroll, animate=False, force=True, immediate=True)
        await pilot.pause()
        before = column.scroll_y

        bridge.apply_stream_event({"type": "message_stop"})
        await pilot.pause()

        assert abs(column.scroll_y - before) <= 1

@pytest.mark.asyncio
async def test_message_column_does_not_autoscroll_when_user_is_slightly_above_bottom():
    """用户只要已经离开底部，哪怕只差 1 行，turn 收尾也不应继续抢滚动。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test(size=(90, 20)) as pilot:
        for index in range(8):
            bridge.begin_user_turn(f"user {index}")
            bridge.apply_stream_event({"type": "stream_request_start"})
            bridge.apply_stream_event({"type": "content_block_start", "index": 0, "content_block": {"type": "text"}})
            bridge.apply_stream_event(
                {"type": "text_delta", "delta": {"text": f"这是第 {index} 轮回复，用来撑高消息区域。 " * 4}}
            )
            bridge.apply_stream_event({"type": "message_stop"})
        await pilot.pause()

        bridge.begin_user_turn("final user")
        bridge.apply_stream_event({"type": "stream_request_start"})
        bridge.apply_stream_event({"type": "content_block_start", "index": 0, "content_block": {"type": "text"}})
        bridge.apply_stream_event(
            {
                "type": "text_delta",
                "delta": {"text": "这一轮会在结束时测试自动滚动。\n\n- 第一项\n- 第二项\n- 第三项"},
            }
        )
        await pilot.pause()

        column = app.query_one(MessageColumn)
        near_bottom = max(0, column.max_scroll_y - 1)
        column.scroll_to(y=near_bottom, animate=False, force=True, immediate=True)
        await pilot.pause()
        before = column.scroll_y
        before_gap = max(0, column.max_scroll_y - before)

        bridge.apply_stream_event({"type": "message_stop"})
        await pilot.pause()

        after_gap = max(0, column.max_scroll_y - column.scroll_y)
        assert abs(after_gap - before_gap) <= 1

@pytest.mark.asyncio
async def test_message_column_keeps_following_live_stream_when_user_stays_at_bottom():
    """用户没有手动离开底部时，流式输出和 message_stop 都应持续贴底，不出现最后一跳回弹。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test(size=(90, 20)) as pilot:
        for index in range(6):
            bridge.begin_user_turn(f"user {index}")
            bridge.apply_stream_event({"type": "stream_request_start"})
            bridge.apply_stream_event({"type": "content_block_start", "index": 0, "content_block": {"type": "text"}})
            bridge.apply_stream_event(
                {"type": "text_delta", "delta": {"text": f"这是第 {index} 轮回复，用来撑高消息区域。 " * 5}}
            )
            bridge.apply_stream_event({"type": "message_stop"})
        await pilot.pause()

        bridge.begin_user_turn("follow stream")
        bridge.apply_stream_event({"type": "stream_request_start"})
        bridge.apply_stream_event({"type": "content_block_start", "index": 0, "content_block": {"type": "text"}})
        await pilot.pause()

        column = app.query_one(MessageColumn)
        assert max(0, column.max_scroll_y - column.scroll_y) <= 1

        for part in (
            "### 标题\n\n",
            "- 第一项内容比较长，用来持续拉高消息区域。\n",
            "- 第二项内容比较长，用来持续拉高消息区域。\n",
            "- 第三项内容比较长，用来持续拉高消息区域。\n",
        ):
            bridge.apply_stream_event({"type": "text_delta", "delta": {"text": part}})
            await pilot.pause()
            assert max(0, column.max_scroll_y - column.scroll_y) <= 1

        bridge.apply_stream_event({"type": "message_stop"})
        await pilot.pause()
        assert max(0, column.max_scroll_y - column.scroll_y) <= 1

@pytest.mark.asyncio
async def test_agent_child_card_updates_in_place_during_stream():
    """assistant 卡片内的 agent 子卡片应原地更新，避免流式阶段反复拆装。"""
    bridge = UIBridge(StreamingEngine())
    app = TextualChatApp(bridge=bridge)

    async with app.run_test() as pilot:
        bridge.begin_user_turn("run child agent")
        bridge.apply_stream_event({"type": "stream_request_start"})
        bridge.apply_stream_event(
            {
                "type": "agent_started",
                "agent_id": "agent-child-live",
                "label": "Worker > Explore",
                "status": "running",
                "content": "Booting",
            }
        )
        await pilot.pause()

        first_card = app.query(AgentStreamCardWidget).first()

        bridge.apply_stream_event(
            {
                "type": "agent_delta",
                "agent_id": "agent-child-live",
                "status": "active",
                "thinking_delta": "thinking...",
                "content_delta": " searching",
            }
        )
        await pilot.pause()

        second_card = app.query(AgentStreamCardWidget).first()
        assert second_card is first_card
