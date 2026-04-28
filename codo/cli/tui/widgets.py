"""Textual 组件。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from itertools import zip_longest
import json
import math
import time
from typing import Any, List, Optional

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button, Collapsible, Input, Markdown, Static, TextArea

from .bridge import AgentChildSnapshot, MessageSnapshot, TodoSummarySnapshot, UISnapshot
from .dialogs import InteractionHost

def _monotonic() -> float:
    return time.monotonic()

def _balanced_markdown(markdown: str) -> str:
    text = markdown or ""
    if text.count("```") % 2 == 1:
        return text + "\n```"
    return text

def _looks_like_diff(text: str) -> bool:
    value = (text or "").strip()
    if not value:
        return False
    return value.startswith("--- ") or "\n@@ " in value or "\n+ " in value or "\n- " in value

def _tool_status_icon(status: str, is_error: bool) -> str:
    if is_error or status == "error":
        return "✖"
    if status in ("completed", "success"):
        return "✔"
    if status in ("running", "starting"):
        return "◍"
    return "•"

def _tool_status_label(status: str, is_error: bool) -> str:
    if is_error or status == "error":
        return "失败"
    if status in ("completed", "success"):
        return "完成"
    if status in ("running", "starting"):
        return "运行中"
    return "等待中"

def _tool_body_markdown(tool_name: str, input_preview: str, result: str) -> str:
    sections: List[str] = []
    preview = (input_preview or "").strip()
    if preview:
        sections.append("输入")
        sections.append(f"```json\n{preview}\n```")

    rendered_result = (result or "").strip()
    if rendered_result:
        sections.append("结果")
        if _looks_like_diff(rendered_result):
            sections.append(f"```diff\n{rendered_result}\n```")
        else:
            sections.append(f"```\n{rendered_result}\n```")

    if not sections:
        return f"`{tool_name}` 正在准备中..."
    return "\n\n".join(sections)

def _truncate_text(text: str, limit: int = 600) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."

def _truncate_inline(text: str, limit: int) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"

def _needs_markdown_render(text: str) -> bool:
    value = str(text or "")
    if not value.strip():
        return False
    markdown_patterns = (
        "```",
        "\n#",
        "\n> ",
        "**",
        "__",
        "`",
        "[",
    )
    return any(pattern in value for pattern in markdown_patterns)

def _content_line_count(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + 1

def _should_collapse_content(text: str, viewport_height: int) -> bool:
    if not text:
        return False
    line_limit = max(18, int(math.ceil(max(1, viewport_height) * 1.5)))
    return _content_line_count(text) > line_limit or len(text) > 2600

def _collapsed_content_preview(text: str, viewport_height: int) -> str:
    lines = (text or "").splitlines()
    preview_lines = max(10, min(18, int(math.ceil(max(1, viewport_height) * 0.6))))
    if len(lines) <= preview_lines:
        preview = "\n".join(lines)
    else:
        preview = "\n".join(lines[:preview_lines]).rstrip()
    hidden = max(0, len(lines) - preview_lines)
    if hidden > 0:
        return f"{preview}\n\n... 还有 {hidden} 行"
    if len(text) > 1600:
        return _truncate_text(text, 1600)
    return text

def _todo_summary_signature(summary: Optional[TodoSummarySnapshot]) -> tuple[Any, ...]:
    if summary is None:
        return ()
    return (
        summary.key,
        summary.completed_count,
        summary.total_count,
        summary.hidden_count,
        tuple((item.content, item.status, item.active_form) for item in summary.items),
    )

@dataclass
class CommandSuggestionView:
    index: int
    name: str
    description: str
    hint: str = ""
    selected: bool = False

def _status_kind(top_status: str) -> str:
    lower = (top_status or "").lower()
    if "error" in lower or "错误" in lower or "异常" in lower:
        return "error"
    if "waiting" in lower or "等待" in lower:
        return "waiting"
    if "thinking" in lower or "思考" in lower:
        return "thinking"
    if "active" in lower or "处理" in lower or "运行" in lower:
        return "active"
    return "idle"

def _status_label(top_status: str) -> str:
    value = (top_status or "").strip()
    if not value:
        return "空闲"
    parts = value.split(" ", 1)
    if len(parts) == 2 and not parts[0][0].isalnum():
        return parts[1]
    return value

def _status_lamp_glyph(kind: str, pulse_step: int) -> str:
    if kind == "idle":
        return "◯"
    if kind == "active":
        return "●"
    if kind == "thinking":
        return "◉"
    if kind == "waiting":
        return "◌"
    if kind == "error":
        return "✖"
    return "●"

def _agent_status_text(status: str) -> str:
    mapping = {
        "running": "运行中",
        "completed": "已完成",
        "error": "异常",
        "waiting": "等待中",
        "active": "运行中",
        "thinking": "思考中",
        "idle": "空闲",
    }
    return mapping.get(str(status or "").lower(), str(status or "").strip() or "未知")

def _agent_mode_text(mode: str) -> str:
    mapping = {
        "fresh": "新建",
        "resume": "恢复",
        "background": "后台",
    }
    return mapping.get(str(mode or "").lower(), str(mode or "").strip())

def _agent_type_text(agent_type: str) -> str:
    mapping = {
        "default": "协作代理",
        "worker": "执行代理",
        "explorer": "探索代理",
    }
    normalized = str(agent_type or "").strip().lower()
    if normalized in mapping:
        return mapping[normalized]
    return str(agent_type or "").strip()

def _sidebar_focus_mode_text(top_status: str, sidebar_mode: str, auto_follow: bool) -> str:
    if sidebar_mode == "global":
        return "会话主线"
    if sidebar_mode.startswith("agent:"):
        return "协作成员"
    if auto_follow:
        return "自动跟随"
    return "手动查看"

def _sidebar_focus_title(label: str) -> str:
    text = str(label or "").strip()
    if not text:
        return "等待新的进展"
    if text.lower() == "global" or text == "全局":
        return "会话主线"
    return text

def _sidebar_focus_snippet(snippet: str) -> str:
    text = " ".join(str(snippet or "").split())
    if not text:
        return "新的回复、工具进展和待办更新会显示在这里。"
    return _truncate_text(text, 120)

def _sidebar_agent_status_text(status: str) -> str:
    mapping = {
        "active": "处理中",
        "thinking": "思考中",
        "waiting": "等待中",
        "error": "异常",
        "completed": "已完成",
        "running": "处理中",
        "idle": "空闲",
    }
    return mapping.get(str(status or "").lower(), "空闲")

def _todo_summary_lines(summary: TodoSummarySnapshot) -> List[str]:
    completed = [item for item in summary.items if item.status == "completed"]
    active = next((item for item in summary.items if item.status == "in_progress"), None)
    pending = [item for item in summary.items if item.status == "pending"]

    lines: List[str] = []
    if completed:
        completed_text = "、".join(item.content for item in completed[:2])
        if len(completed) > 2:
            completed_text = f"{completed_text} 等 {len(completed)} 项"
        lines.append(f"已完成：{_truncate_text(completed_text, 72)}")

    if active is not None:
        lines.append(f"进行中：{_truncate_text(active.active_form or active.content, 72)}")
    elif pending:
        lines.append(f"待处理：{_truncate_text(pending[0].content, 72)}")

    if active is not None and pending:
        lines.append(f"接下来：{_truncate_text(pending[0].content, 72)}")
    elif len(pending) > 1:
        lines.append(f"接下来：{_truncate_text(pending[1].content, 72)}")

    if not lines and summary.items:
        lines.append(_truncate_text(summary.items[0].content, 72))
    return lines[:3]

def _is_low_signal_tool_call(call: Any) -> bool:
    name = str(getattr(call, "name", "") or "").strip().lower()
    status = str(getattr(call, "status", "") or "").strip().lower()
    input_preview = str(getattr(call, "input_preview", "") or "").strip()
    result = str(getattr(call, "result", "") or "").strip()
    receipt = getattr(call, "receipt", None)
    audit_events = list(getattr(call, "audit_events", []) or [])
    return (
        name == "tool"
        and status in {"", "starting", "waiting"}
        and not input_preview
        and not result
        and not receipt
        and not audit_events
    )

class ChatInput(TextArea):
    MIN_LINES = 1
    MAX_LINES = 6
    BINDINGS = [
        *TextArea.BINDINGS,
        Binding("enter", "submit", "Submit", show=False),
        Binding("shift+enter", "insert_newline", "New line", show=False),
    ]

    @dataclass
    class Submitted(Message):
        input: "ChatInput"
        value: str

        @property
        def control(self) -> "ChatInput":
            return self.input

    def __init__(self, *, placeholder: str = "", id: str | None = None) -> None:
        super().__init__(
            "",
            id=id,
            placeholder=placeholder,
            soft_wrap=True,
            show_line_numbers=False,
            compact=True,
            highlight_cursor_line=False,
        )
        self.show_horizontal_scrollbar = False
        self.show_vertical_scrollbar = False

    @property
    def value(self) -> str:
        return self.text

    @value.setter
    def value(self, new_value: str) -> None:
        self.load_text(new_value)
        lines = new_value.splitlines() or [""]
        self.move_cursor((len(lines) - 1, len(lines[-1])), record_width=False)

    def on_mount(self) -> None:
        self._refresh_height()

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.action_submit()
            return
        if event.key == "shift+enter":
            event.stop()
            event.prevent_default()
            self.action_insert_newline()
            return
        await super()._on_key(event)

    def _on_resize(self) -> None:
        self._refresh_height()

    def watch_disabled(self, disabled: bool) -> None:
        self.read_only = disabled

    def _wrapped_line_count(self) -> int:
        width = max(1, self.content_region.width or self.size.width or 1)
        total = 0
        for raw_line in self.text.split("\n") or [""]:
            line = raw_line or " "
            total += max(1, math.ceil(len(line) / width))
        return max(1, total)

    def _refresh_height(self) -> None:
        visible_lines = min(self.MAX_LINES, max(self.MIN_LINES, self._wrapped_line_count()))
        self.styles.height = visible_lines
        self.show_vertical_scrollbar = self._wrapped_line_count() > self.MAX_LINES
        if not self.show_vertical_scrollbar:
            self.scroll_y = 0

    def on_text_area_changed(self, _: TextArea.Changed) -> None:
        self._refresh_height()

    def action_submit(self) -> None:
        if self.read_only or self.disabled:
            return
        self.post_message(self.Submitted(self, self.text))

    def action_insert_newline(self) -> None:
        if self.read_only or self.disabled:
            return
        self.insert("\n")

class HeaderPanel(Widget):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._signature: tuple[Any, ...] | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="header-line-1"):
            yield Static("", id="header-model")
            yield Static("", id="header-session-title")
            with Horizontal(id="header-status-group"):
                yield Static("◯", id="header-status-lamp", classes="-idle")
                yield Static("空闲", id="header-status-label")
        yield Static("", id="header-line-2")

    def update_snapshot(self, snapshot: UISnapshot, pulse_step: int = 0) -> None:
        status = snapshot.status
        status_kind = _status_kind(status.top_status)
        lamp_glyph = _status_lamp_glyph(status_kind, pulse_step)
        token_window = status.effective_context_window or status.context_window
        remaining_tokens = status.remaining_tokens or max(0, token_window - status.token_count)
        signature = (
            status.model_name,
            status.session_title,
            status.top_status,
            status.sub_status,
            status.permission_mode,
            status.token_count,
            token_window,
            remaining_tokens,
            status.model_visible_message_count,
            status.session_message_count,
            status_kind,
            lamp_glyph,
        )
        if signature == self._signature:
            return
        self._signature = signature
        self.query_one("#header-model", Static).update(status.model_name)
        self.query_one("#header-session-title", Static).update(status.session_title or "未命名会话")
        lamp = self.query_one("#header-status-lamp", Static)
        label = self.query_one("#header-status-label", Static)
        for class_name in ("-idle", "-active", "-thinking", "-waiting", "-error"):
            lamp.set_class(class_name == f"-{status_kind}", class_name)
            label.set_class(class_name == f"-{status_kind}", class_name)
        lamp.update(lamp_glyph)
        label.update(_status_label(status.top_status))
        self.query_one("#header-line-2", Static).update(
            f"{status.sub_status}   权限 {status.permission_mode}   令牌 {status.token_count}/{token_window}   剩余 {remaining_tokens}   消息 {status.model_visible_message_count}/{status.session_message_count}"
        )

class TodoTimelineWidget(Widget):
    def compose(self) -> ComposeResult:
        with Vertical(classes="todo-summary-shell"):
            with Horizontal(classes="todo-summary-header"):
                yield Static("", classes="todo-summary-dot")
                yield Static("更新待办", classes="todo-summary-title")
                yield Static("", classes="todo-summary-meta")
            yield Static("", classes="todo-summary-list")

    def update_summary(self, summary: Optional[TodoSummarySnapshot]) -> None:
        dot = self.query_one(".todo-summary-dot", Static)
        meta = self.query_one(".todo-summary-meta", Static)
        body = self.query_one(".todo-summary-list", Static)
        if not summary or not summary.items:
            self.display = False
            dot.update("")
            meta.update("")
            body.update("")
            return
        self.display = True
        total = max(summary.total_count, 0)
        completed = max(summary.completed_count, 0)
        dot.update("●")
        meta.update(f"已完成 {completed}/{total}")
        lines = _todo_summary_lines(summary)
        if summary.hidden_count:
            lines.append(f"另有 {summary.hidden_count} 项")
        body.update(Text("\n".join(lines)))

class CommandReceiptWidget(Widget):
    def __init__(self, receipt: dict[str, Any]) -> None:
        super().__init__(classes="receipt-card receipt-command")
        self.receipt = receipt

    def compose(self) -> ComposeResult:
        command = str(self.receipt.get("command", "") or "")
        exit_code = self.receipt.get("exit_code")
        stdout = _truncate_text(str(self.receipt.get("stdout", "") or ""))
        stderr = _truncate_text(str(self.receipt.get("stderr", "") or ""))

        if command:
            yield Static(f"$ {command}", classes="receipt-command-line")
        if exit_code is not None:
            yield Static(f"退出码 {exit_code}", classes="receipt-command-exit")
        if stdout:
            yield Static("标准输出", classes="receipt-section-label")
            yield Static(stdout, classes="receipt-terminal receipt-stdout")
        if stderr:
            yield Static("错误输出", classes="receipt-section-label")
            yield Static(stderr, classes="receipt-terminal receipt-stderr")

class DiffReceiptWidget(Widget):
    def __init__(self, receipt: dict[str, Any]) -> None:
        super().__init__(classes="receipt-card receipt-diff")
        self.receipt = receipt

    def compose(self) -> ComposeResult:
        path = str(self.receipt.get("path", "") or "")
        diff_text = str(self.receipt.get("diff_text", "") or "")

        if path:
            yield Static(path, classes="receipt-path")
        if diff_text:
            yield Static(_truncate_text(diff_text, 2400), classes="receipt-terminal receipt-diff-block")

class GenericReceiptWidget(Widget):
    def __init__(self, receipt: dict[str, Any]) -> None:
        super().__init__(classes="receipt-card receipt-generic")
        self.receipt = receipt

    def compose(self) -> ComposeResult:
        body = str(self.receipt.get("body", "") or "")
        if not body:
            body = str(self.receipt.get("summary", "") or "")
        if body:
            yield Static(_truncate_text(body), classes="receipt-body")

class AgentReceiptWidget(Widget):
    def __init__(self, receipt: dict[str, Any]) -> None:
        super().__init__(classes="receipt-card receipt-agent agent-child-card")
        self.receipt = receipt

    def compose(self) -> ComposeResult:
        summary = str(self.receipt.get("summary", "") or "")
        agent_type = _agent_type_text(str(self.receipt.get("agent_type", "") or ""))
        mode = str(self.receipt.get("mode", "") or "")
        status = str(self.receipt.get("status", "") or "")
        task_id = str(self.receipt.get("task_id", "") or "")
        background = bool(self.receipt.get("background", False))
        total_tokens = int(self.receipt.get("total_tokens", 0) or 0)

        title = agent_type or summary or "协作代理"
        yield Static(title, classes="agent-child-title")

        meta = [part for part in [agent_type, _agent_mode_text(mode), _agent_status_text(status)] if part]
        if background:
            meta.append("后台")
        if meta:
            yield Static(" · ".join(meta), classes="agent-child-meta")
        if task_id:
            yield Static(f"任务 {task_id}", classes="agent-child-task")
        if total_tokens:
            yield Static(f"令牌 {total_tokens}", classes="agent-child-tokens")

class SidebarRosterCardWidget(Widget):
    def __init__(self, index: int, agent: Any) -> None:
        super().__init__(classes="sidebar-roster-card")
        self.index = index
        self.agent = agent

    def compose(self) -> ComposeResult:
        with Horizontal(classes="sidebar-roster-head"):
            yield Static(f"[{self.index}]", classes="sidebar-roster-index")
            yield Static(self.agent.label, classes="sidebar-roster-name")
            yield Static(
                _sidebar_agent_status_text(self.agent.status),
                classes=f"sidebar-roster-status -{_status_kind(self.agent.status)}",
            )

    def update_agent(self, index: int, agent: Any) -> None:
        self.index = index
        self.agent = agent
        self.query_one(".sidebar-roster-index", Static).update(f"[{index}]")
        self.query_one(".sidebar-roster-name", Static).update(agent.label)
        status_widget = self.query_one(".sidebar-roster-status", Static)
        for class_name in ("-idle", "-active", "-thinking", "-waiting", "-error"):
            status_widget.set_class(class_name == f"-{_status_kind(agent.status)}", class_name)
        status_widget.update(_sidebar_agent_status_text(agent.status))

class CommandSuggestionRowWidget(Widget):
    class Selected(Message):
        def __init__(self, index: int) -> None:
            self.index = index
            super().__init__()

    def __init__(self) -> None:
        super().__init__(classes="command-suggestion-row")
        self.item: Optional[CommandSuggestionView] = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="command-suggestion-main"):
            yield Static("", classes="command-suggestion-index")
            yield Static("", classes="command-suggestion-name")
            yield Static("", classes="command-suggestion-desc")

    def update_view(self, item: Optional[CommandSuggestionView]) -> None:
        self.item = item
        self.display = item is not None
        if item is None:
            return
        self.set_class(item.selected, "-selected")
        self.query_one(".command-suggestion-index", Static).update(f"{item.index}.")
        self.query_one(".command-suggestion-name", Static).update(_truncate_inline(item.name, 18))
        self.query_one(".command-suggestion-desc", Static).update(_truncate_inline(item.description, 48))

    def on_click(self, event: events.Click) -> None:
        if self.item is None:
            return
        self.post_message(self.Selected(self.item.index))
        event.stop()

def _build_receipt_payload(receipt: Optional[dict[str, Any]], fallback_body: str = "") -> Optional[dict[str, Any]]:
    data = dict(receipt or {})
    if not data and fallback_body:
        data = {"kind": "generic", "summary": "", "body": fallback_body}
    if not data:
        return None
    kind = str(data.get("kind", "") or "")
    if kind in {"", "generic"}:
        return None
    return data

def _build_receipt_widget(receipt: Optional[dict[str, Any]], fallback_body: str = "") -> Optional[Widget]:
    data = _build_receipt_payload(receipt, fallback_body)
    if not data:
        return None
    kind = str(data.get("kind", "") or "")
    if kind == "command":
        return CommandReceiptWidget(data)
    if kind == "diff":
        return DiffReceiptWidget(data)
    if kind == "agent":
        return AgentReceiptWidget(data)
    return None

class InterruptedToolCardWidget(Widget):
    def __init__(self) -> None:
        super().__init__(classes="tool-call-card tool-interrupted-card")

    def compose(self) -> ComposeResult:
        yield Static("[已被用户中断]", classes="tool-call-heading")
        yield Static("可以在下方直接重试。", classes="tool-call-summary")

class ToolCallCardWidget(Widget):
    def __init__(self, call: Any) -> None:
        super().__init__(classes="tool-call-card")
        self.call = call
        self._receipt_signature: Optional[str] = None
        self._receipt_widget: Optional[Widget] = None
        self._receipt_expanded = False

    def compose(self) -> ComposeResult:
        with Horizontal(classes="tool-card-header"):
            yield Static("", classes="tool-call-heading tool-call-name")
            yield Static("", classes="tool-call-inline")
            yield Button("详情", classes="tool-receipt-toggle")
            yield Static("", classes="tool-call-status")
        yield Static("", classes="tool-call-summary")
        yield Static("", classes="tool-call-input")
        yield Vertical(classes="tool-receipt-host")

    async def on_mount(self) -> None:
        await self.update_from_snapshot(self.call)

    async def update_from_snapshot(self, call: Any) -> None:
        self.call = call
        status = "error" if call.is_error else call.status
        heading = f"{_tool_status_icon(status, call.is_error)} {call.name}"
        summary = str(call.summary or "").strip()
        input_preview = str(call.input_preview or "").strip()
        inline_preview = summary or input_preview
        is_live = status in ("running", "starting")
        rich_receipt_payload = _build_receipt_payload(call.receipt, call.result)
        has_rich_receipt = rich_receipt_payload is not None
        is_compact = (not self._receipt_expanded) and (bool(inline_preview) or has_rich_receipt or is_live)

        self.set_class(is_compact, "-compact")

        name_widget = self.query_one(".tool-call-name", Static)
        inline_widget = self.query_one(".tool-call-inline", Static)
        status_widget = self.query_one(".tool-call-status", Static)
        summary_widget = self.query_one(".tool-call-summary", Static)
        input_widget = self.query_one(".tool-call-input", Static)
        receipt_toggle = self.query_one(".tool-receipt-toggle", Button)
        receipt_host = self.query_one(".tool-receipt-host", Vertical)

        name_widget.update(heading)
        inline_widget.display = bool(inline_preview)
        if inline_widget.display:
            inline_widget.update(_truncate_text(inline_preview, 88 if is_live else 120))

        for class_name in list(status_widget.classes):
            if class_name.startswith("tool-call-status-"):
                status_widget.remove_class(class_name)
        status_widget.add_class(f"tool-call-status-{status}")
        status_widget.update(_tool_status_label(status, call.is_error))

        show_summary = bool(
            self._receipt_expanded
            and summary
            and summary != call.name
            and not has_rich_receipt
            and not is_compact
        )
        summary_widget.display = show_summary
        if show_summary:
            summary_widget.update(summary)

        show_input = bool(
            self._receipt_expanded
            and input_preview
            and input_preview != summary
            and not has_rich_receipt
            and not is_compact
        )
        input_widget.display = show_input
        if show_input:
            input_widget.update(_truncate_text(input_preview, 240))

        receipt_widget = _build_receipt_widget(call.receipt, call.result)
        receipt_signature = None
        if rich_receipt_payload is not None:
            receipt_signature = json.dumps(rich_receipt_payload, ensure_ascii=False, sort_keys=True)
        if receipt_signature != self._receipt_signature:
            if self._receipt_widget is not None and self._receipt_widget.is_mounted:
                await self._receipt_widget.remove()
            self._receipt_widget = None
            if receipt_widget is not None:
                self._receipt_widget = receipt_widget
                await receipt_host.mount(receipt_widget)
                if self._receipt_signature is None:
                    self._receipt_expanded = False
            self._receipt_signature = receipt_signature

        has_receipt = self._receipt_widget is not None
        receipt_toggle.display = has_receipt
        receipt_toggle.label = "收起" if self._receipt_expanded else "展开"
        receipt_toggle.set_class(self._receipt_expanded, "-expanded")
        receipt_host.display = has_receipt and self._receipt_expanded

    @on(Button.Pressed, ".tool-receipt-toggle")
    def on_receipt_toggle_pressed(self, event: Button.Pressed) -> None:
        self._receipt_expanded = not self._receipt_expanded
        receipt_toggle = self.query_one(".tool-receipt-toggle", Button)
        receipt_host = self.query_one(".tool-receipt-host", Vertical)
        receipt_toggle.label = "收起" if self._receipt_expanded else "展开"
        receipt_toggle.set_class(self._receipt_expanded, "-expanded")
        self.set_class(self._receipt_widget is not None and not self._receipt_expanded, "-compact")
        receipt_host.display = self._receipt_widget is not None and self._receipt_expanded
        event.stop()

class ToolStatusAreaWidget(Widget):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._tool_widgets: dict[str, ToolCallCardWidget] = {}
        self._interrupted_widget: Optional[InterruptedToolCardWidget] = None

    def compose(self) -> ComposeResult:
        yield Vertical(classes="tool-status-stack")

    async def update_tool_calls(self, tool_calls: List[Any], interrupted: bool) -> None:
        stack = self.query_one(".tool-status-stack", Vertical)
        visible_calls = [call for call in tool_calls if not _is_low_signal_tool_call(call)]
        active_ids = {call.tool_use_id for call in visible_calls}

        for tool_use_id, widget in list(self._tool_widgets.items()):
            if tool_use_id not in active_ids:
                await widget.remove()
                del self._tool_widgets[tool_use_id]

        for call in visible_calls:
            widget = self._tool_widgets.get(call.tool_use_id)
            if widget is None:
                widget = ToolCallCardWidget(call)
                self._tool_widgets[call.tool_use_id] = widget
                await stack.mount(widget)
            await widget.update_from_snapshot(call)

        if interrupted:
            if self._interrupted_widget is None or not self._interrupted_widget.is_mounted:
                self._interrupted_widget = InterruptedToolCardWidget()
                await stack.mount(self._interrupted_widget)
        elif self._interrupted_widget is not None and self._interrupted_widget.is_mounted:
            await self._interrupted_widget.remove()
            self._interrupted_widget = None

        self.display = bool(visible_calls) or interrupted

class AgentStreamCardWidget(Widget):
    def __init__(self, child: AgentChildSnapshot) -> None:
        super().__init__(classes="agent-stream-card")
        self.child = child
        self._tool_widgets: dict[str, ToolCallCardWidget] = {}
        self._todo_signature: tuple[Any, ...] | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="agent-stream-header"):
            yield Static("", classes="agent-stream-title")
            yield Static("", classes="agent-stream-status")
        yield Static("", classes="agent-stream-meta")
        yield Static("", classes="agent-stream-task")
        yield Static("", classes="agent-stream-thinking")
        yield Static("", classes="agent-stream-content")
        yield Vertical(classes="agent-stream-tools")
        yield TodoTimelineWidget(classes="agent-stream-todo")

    async def on_mount(self) -> None:
        await self.update_from_snapshot(self.child)

    async def update_from_snapshot(self, child: AgentChildSnapshot) -> None:
        self.child = child
        title = child.label or child.agent_id
        title_widget = self.query_one(".agent-stream-title", Static)
        status_widget = self.query_one(".agent-stream-status", Static)
        meta_widget = self.query_one(".agent-stream-meta", Static)
        task_widget = self.query_one(".agent-stream-task", Static)
        thinking_widget = self.query_one(".agent-stream-thinking", Static)
        content_widget = self.query_one(".agent-stream-content", Static)
        tools_host = self.query_one(".agent-stream-tools", Vertical)
        todo_widget = self.query_one(TodoTimelineWidget)

        title_widget.update(title)
        for class_name in list(status_widget.classes):
            if class_name.startswith("agent-stream-status-"):
                status_widget.remove_class(class_name)
        status_widget.add_class(f"agent-stream-status-{child.status}")
        status_widget.update(_agent_status_text(child.status))

        meta = [part for part in [_agent_type_text(child.agent_type), _agent_mode_text(child.mode)] if part]
        if child.background:
            meta.append("后台")
        meta.append(_agent_status_text(child.status))
        meta_text = " · ".join(meta)
        meta_widget.display = bool(meta_text)
        if meta_text:
            meta_widget.update(meta_text)

        task_widget.display = bool(child.task_id)
        if child.task_id:
            task_widget.update(f"任务 {child.task_id}")

        thinking_text = _truncate_text(child.thinking, 240) if child.thinking else ""
        thinking_widget.display = bool(thinking_text)
        if thinking_text:
            thinking_widget.update(thinking_text)

        content_text = _truncate_text(child.content, 320) if child.content else ""
        content_widget.display = bool(content_text)
        if content_text:
            content_widget.update(content_text)

        visible_calls = list(child.tool_calls or [])[-3:]
        visible_tool_ids = {call.tool_use_id for call in visible_calls}
        for tool_use_id, widget in list(self._tool_widgets.items()):
            if tool_use_id not in visible_tool_ids:
                await widget.remove()
                del self._tool_widgets[tool_use_id]

        for index, call in enumerate(visible_calls):
            widget = self._tool_widgets.get(call.tool_use_id)
            if widget is None:
                widget = ToolCallCardWidget(call)
                self._tool_widgets[call.tool_use_id] = widget
                await tools_host.mount(widget)
                await widget.update_from_snapshot(call)
            children = list(tools_host.children)
            if index < len(children) and children[index] is not widget:
                tools_host.move_child(widget, before=children[index])
        tools_host.display = bool(visible_calls)

        todo_signature = _todo_summary_signature(child.todo_summary)
        if todo_signature != self._todo_signature:
            self._todo_signature = todo_signature
            todo_widget.update_summary(child.todo_summary)

class AgentChildrenAreaWidget(Widget):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._child_widgets: dict[str, AgentStreamCardWidget] = {}

    def compose(self) -> ComposeResult:
        yield Vertical(classes="agent-children-stack")

    async def update_children(self, children: List[AgentChildSnapshot]) -> None:
        stack = self.query_one(".agent-children-stack", Vertical)
        visible_ids = {child.agent_id for child in children}
        for agent_id, widget in list(self._child_widgets.items()):
            if agent_id not in visible_ids:
                await widget.remove()
                del self._child_widgets[agent_id]

        self.display = bool(children)
        for index, child in enumerate(children):
            widget = self._child_widgets.get(child.agent_id)
            if widget is None:
                widget = AgentStreamCardWidget(child)
                self._child_widgets[child.agent_id] = widget
                await stack.mount(widget)
            await widget.update_from_snapshot(child)
            mounted = list(stack.children)
            if index < len(mounted) and mounted[index] is not widget:
                stack.move_child(widget, before=mounted[index])

class UserMessageWidget(Widget):
    def __init__(self, message: MessageSnapshot) -> None:
        super().__init__(id=f"message-{message.id}", classes="message-row user-row")
        self.message = message

    def compose(self) -> ComposeResult:
        yield Static(self._render_user_line(self.message.content), classes="user-line")

    @staticmethod
    def _render_user_line(content: str) -> Text:
        line = Text()
        line.append("用户 >", style="bold #7aa2f7")
        line.append(" ")
        line.append(content or "", style="#c0caf5")
        return line

    def update_from_snapshot(self, message: MessageSnapshot) -> None:
        self.message = message
        self.query_one(".user-line", Static).update(self._render_user_line(message.content))

class AssistantMessageWidget(Widget):
    STREAM_PREVIEW_INTERVAL = 1 / 15

    def __init__(self, message: MessageSnapshot) -> None:
        super().__init__(id=f"message-{message.id}", classes="message-row assistant-row")
        self.message = message
        self._content_signature: tuple[str, bool] | None = None
        self._thinking_signature: tuple[str, bool, int] | None = None
        self._tool_signature: tuple[tuple[str, float, str, bool], ...] | None = None
        self._agent_signature: tuple[tuple[str, float, str], ...] | None = None
        self._todo_signature: tuple[Any, ...] | None = None
        self._interrupted_signature: bool | None = None
        self._content_expanded = False
        self._content_collapsible = False
        self._last_stream_preview_render_at = 0.0
        self._rendered_stream_content = ""
        self._pending_stream_flush_handle: Optional[asyncio.Handle] = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="ai-card"):
            with Collapsible(
                Static("", classes="thinking-body"),
                title="思考中...",
                collapsed=True,
                classes="thinking-shell",
            ):
                pass
            with Vertical(classes="assistant-content-area"):
                yield Static("", classes="assistant-stream-preview")
                yield Static("", classes="assistant-collapsed-preview")
                yield Markdown("", classes="assistant-markdown")
                yield Button("展开", classes="assistant-expand-button")
            yield ToolStatusAreaWidget(classes="tool-status-area")
            yield AgentChildrenAreaWidget(classes="agent-children-area")
            yield TodoTimelineWidget(classes="todo-timeline")
            with Horizontal(classes="assistant-actions"):
                yield Button("重试", classes="retry-button")

    async def on_mount(self) -> None:
        await self.update_from_snapshot(self.message)

    def on_unmount(self) -> None:
        self._cancel_stream_preview_flush()

    def _cancel_stream_preview_flush(self) -> None:
        if self._pending_stream_flush_handle is not None:
            self._pending_stream_flush_handle.cancel()
            self._pending_stream_flush_handle = None

    def _schedule_stream_preview_flush(self) -> None:
        if self._pending_stream_flush_handle is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        elapsed = _monotonic() - self._last_stream_preview_render_at
        delay = max(0.0, self.STREAM_PREVIEW_INTERVAL - elapsed)
        self._pending_stream_flush_handle = loop.call_later(delay, self._enqueue_stream_preview_flush)

    def _enqueue_stream_preview_flush(self) -> None:
        self._pending_stream_flush_handle = None
        if not self.is_mounted:
            return
        self.call_after_refresh(self._flush_stream_preview)

    def _flush_stream_preview(self) -> None:
        if not self.is_mounted or self.message.completed:
            return
        content = self.message.content or ""
        if not content or content == self._rendered_stream_content:
            return
        preview = self.query_one(".assistant-stream-preview", Static)
        if not preview.display:
            return
        preview.update(content)
        self._rendered_stream_content = content
        self._last_stream_preview_render_at = _monotonic()
        try:
            self.app.query_one(MessageColumn).maintain_follow_position()
        except Exception:
            pass

    async def update_from_snapshot(self, message: MessageSnapshot) -> None:
        self.message = message
        thinking = self.query_one(Collapsible)
        thinking_body = self.query_one(".thinking-body", Static)
        stream_preview = self.query_one(".assistant-stream-preview", Static)
        collapsed_preview = self.query_one(".assistant-collapsed-preview", Static)
        markdown = self.query_one(Markdown)
        expand_button = self.query_one(".assistant-expand-button", Button)
        tool_status = self.query_one(ToolStatusAreaWidget)
        agent_children = self.query_one(AgentChildrenAreaWidget)
        todo_widget = self.query_one(TodoTimelineWidget)
        retry_button = self.query_one(".retry-button", Button)

        thinking_signature = (message.thinking, message.thinking_collapsed, message.duration_seconds)
        if thinking_signature != self._thinking_signature:
            self._thinking_signature = thinking_signature
            if message.thinking:
                thinking.display = True
                thinking.title = (
                    "思考中..."
                    if not message.completed
                    else f"思考用时 {message.duration_seconds} 秒"
                )
                thinking.collapsed = message.thinking_collapsed
                thinking_body.update(message.thinking)
            else:
                thinking.display = False

        content_signature = (message.content, message.completed)
        content_changed = content_signature != self._content_signature
        if content_changed:
            previous_signature = self._content_signature
            self._content_signature = content_signature
            if previous_signature != content_signature:
                self._content_expanded = False

        if message.completed:
            self._cancel_stream_preview_flush()
            viewport_size = getattr(self.app, "size", None) if self.app is not None else None
            viewport_height = getattr(viewport_size, "height", 24) or 24
            self._content_collapsible = _should_collapse_content(message.content or "", viewport_height)
            should_use_markdown = _needs_markdown_render(message.content or "")
            if self._content_collapsible:
                stream_preview.display = False
                collapsed_preview.display = not self._content_expanded
                collapsed_preview.update(_collapsed_content_preview(message.content or "", viewport_height))
                should_show_markdown = self._content_expanded and bool(message.content)
                had_markdown = markdown.display
                markdown.display = should_show_markdown
                expand_button.display = True
                expand_button.label = "收起" if self._content_expanded else "展开"
                if should_show_markdown and (content_changed or not had_markdown):
                    markdown.update(_balanced_markdown(message.content or ""))
            else:
                collapsed_preview.display = False
                expand_button.display = False
                if should_use_markdown:
                    stream_preview.display = False
                    had_markdown = markdown.display
                    markdown.display = bool(message.content)
                    if message.content and (content_changed or not had_markdown):
                        markdown.update(_balanced_markdown(message.content or ""))
                else:
                    markdown.display = False
                    stream_preview.display = bool(message.content)
                    if message.content and (
                        content_changed
                        or self._rendered_stream_content != message.content
                    ):
                        stream_preview.update(message.content)
        else:
            self._content_collapsible = False
            markdown.display = False
            collapsed_preview.display = False
            expand_button.display = False
            stream_preview.display = bool(message.content)
            if message.content and content_changed:
                render_now = (
                    not self._rendered_stream_content
                    or not stream_preview.display
                    or (_monotonic() - self._last_stream_preview_render_at) >= self.STREAM_PREVIEW_INTERVAL
                )
                if render_now:
                    self._cancel_stream_preview_flush()
                    stream_preview.update(message.content)
                    self._rendered_stream_content = message.content
                    self._last_stream_preview_render_at = _monotonic()
                else:
                    self._schedule_stream_preview_flush()

        tool_signature = tuple(
            (call.tool_use_id, call.updated_at, call.status, bool(call.is_error))
            for call in message.tool_calls
        )
        if tool_signature != self._tool_signature or message.interrupted != self._interrupted_signature:
            self._tool_signature = tool_signature
            self._interrupted_signature = message.interrupted
            await tool_status.update_tool_calls(message.tool_calls, message.interrupted)

        agent_signature = tuple((child.agent_id, child.updated_at, child.status) for child in message.agent_children)
        if agent_signature != self._agent_signature:
            self._agent_signature = agent_signature
            await agent_children.update_children(message.agent_children)

        todo_signature = _todo_summary_signature(message.todo_summary)
        if todo_signature != self._todo_signature:
            self._todo_signature = todo_signature
            todo_widget.update_summary(message.todo_summary)

        if not message.completed and not message.content:
            stream_preview.display = False
        if message.completed and not message.content:
            markdown.display = False
            collapsed_preview.display = False
            expand_button.display = False
        if message.completed:
            self._rendered_stream_content = message.content or ""
        retry_button.display = message.interrupted

    @on(Button.Pressed, ".assistant-expand-button")
    def on_expand_button_pressed(self, event: Button.Pressed) -> None:
        if not self._content_collapsible:
            return
        self._content_expanded = not self._content_expanded
        event.stop()
        self.run_worker(self.update_from_snapshot(self.message), exclusive=False)

class InfoMessageWidget(Widget):
    def __init__(self, message: MessageSnapshot) -> None:
        classes = f"message-row info-row {message.kind}-row"
        super().__init__(id=f"message-{message.id}", classes=classes)
        self.message = message

    def compose(self) -> ComposeResult:
        yield Static(self.message.content, classes="info-card")

    def update_from_snapshot(self, message: MessageSnapshot) -> None:
        self.message = message
        self.query_one(Static).update(message.content)

class MessageColumn(VerticalScroll):
    can_focus = True

    def __init__(self) -> None:
        super().__init__(id="message-column")
        self._message_widgets: dict[str, Widget] = {}
        self._message_versions: dict[str, tuple[float, bool, bool]] = {}
        self._bottom_follow_slack = 0.5
        self._follow_latest = True
        self._programmatic_scroll_depth = 0
        self._follow_refresh_scheduled = False

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        if self._programmatic_scroll_depth:
            return
        bottom_gap = max(0.0, self.max_scroll_y - new_value)
        self._follow_latest = bottom_gap <= self._bottom_follow_slack

    def _schedule_follow_latest(self) -> None:
        if self._follow_refresh_scheduled:
            return
        self._follow_refresh_scheduled = True
        self.call_after_refresh(self._scroll_to_latest)

    def maintain_follow_position(self) -> None:
        if self._follow_latest:
            self._schedule_follow_latest()

    def _scroll_to_latest(self) -> None:
        self._follow_refresh_scheduled = False
        self._programmatic_scroll_depth += 1
        try:
            self.scroll_end(animate=False, immediate=True)
        finally:
            self._programmatic_scroll_depth -= 1
        self._follow_latest = True

    def _restore_manual_scroll(self, previous_scroll_y: float, previous_bottom_gap: float) -> None:
        if previous_bottom_gap <= max(4.0, float(self.size.height)):
            target_scroll = max(0.0, self.max_scroll_y - previous_bottom_gap)
        else:
            target_scroll = min(previous_scroll_y, self.max_scroll_y)
        self._programmatic_scroll_depth += 1
        try:
            self.scroll_to(
                y=target_scroll,
                animate=False,
                force=True,
                immediate=True,
            )
        finally:
            self._programmatic_scroll_depth -= 1

    async def update_messages(self, messages: List[MessageSnapshot]) -> None:
        previous_scroll_y = self.scroll_y
        previous_max_scroll_y = self.max_scroll_y
        previous_bottom_gap = max(0.0, previous_max_scroll_y - previous_scroll_y)
        should_follow_latest = self._follow_latest or previous_bottom_gap <= self._bottom_follow_slack
        seen = set()
        content_changed = False

        for message in messages:
            seen.add(message.id)
            widget = self._message_widgets.get(message.id)
            version = (message.updated_at, message.completed, message.interrupted)
            if widget is None:
                widget = self._create_widget(message)
                self._message_widgets[message.id] = widget
                self._message_versions[message.id] = version
                await self.mount(widget)
                content_changed = True
            elif self._message_versions.get(message.id) != version:
                self._message_versions[message.id] = version
                await self._update_widget(widget, message)
                content_changed = True

        for message_id, widget in list(self._message_widgets.items()):
            if message_id not in seen:
                await widget.remove()
                del self._message_widgets[message_id]
                self._message_versions.pop(message_id, None)
                content_changed = True

        if not content_changed:
            return

        if should_follow_latest:
            self._schedule_follow_latest()
        else:
            self.call_after_refresh(
                self._restore_manual_scroll,
                previous_scroll_y,
                previous_bottom_gap,
            )

    @staticmethod
    def _create_widget(message: MessageSnapshot) -> Widget:
        if message.role == "user":
            return UserMessageWidget(message)
        if message.role == "assistant":
            return AssistantMessageWidget(message)
        return InfoMessageWidget(message)

    @staticmethod
    async def _update_widget(widget: Widget, message: MessageSnapshot) -> None:
        if isinstance(widget, UserMessageWidget):
            widget.update_from_snapshot(message)
        elif isinstance(widget, AssistantMessageWidget):
            await widget.update_from_snapshot(message)
        elif isinstance(widget, InfoMessageWidget):
            widget.update_from_snapshot(message)

class SidebarPanel(Widget):
    can_focus = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._roster_signature: tuple[tuple[str, str, str], ...] | None = None
        self._roster_widgets: dict[str, SidebarRosterCardWidget] = {}

    def compose(self) -> ComposeResult:
        yield Static("协作成员", classes="sidebar-section-label")
        yield Static("这里会显示参与当前会话的代理。", id="sidebar-empty", classes="sidebar-empty")
        yield Vertical(id="sidebar-roster-list", classes="sidebar-roster-list")

    async def update_snapshot(self, snapshot: UISnapshot) -> None:
        roster_list = self.query_one("#sidebar-roster-list", Vertical)
        empty_state = self.query_one("#sidebar-empty", Static)

        roster_signature = tuple((agent.agent_id, agent.status, agent.label) for agent in snapshot.agents[:8])
        if roster_signature == self._roster_signature:
            empty_state.display = not bool(snapshot.agents)
            return

        self._roster_signature = roster_signature
        visible_agents = snapshot.agents[:8]
        empty_state.display = not bool(visible_agents)
        visible_ids = {agent.agent_id for agent in visible_agents}
        for agent_id, widget in list(self._roster_widgets.items()):
            if agent_id not in visible_ids:
                await widget.remove()
                del self._roster_widgets[agent_id]

        for index, agent in enumerate(visible_agents, 1):
            widget = self._roster_widgets.get(agent.agent_id)
            if widget is None:
                widget = SidebarRosterCardWidget(index, agent)
                self._roster_widgets[agent.agent_id] = widget
                await roster_list.mount(widget)
            else:
                widget.update_agent(index, agent)
            mounted = list(roster_list.children)
            if index - 1 < len(mounted) and mounted[index - 1] is not widget:
                roster_list.move_child(widget, before=mounted[index - 1])

class ToastHost(Widget):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._toast_signature: tuple[tuple[str, str, str, float], ...] | None = None

    def compose(self) -> ComposeResult:
        yield Vertical(id="toast-stack")

    async def update_snapshot(self, snapshot: UISnapshot) -> None:
        stack = self.query_one("#toast-stack", Vertical)
        toast_signature = tuple(
            (toast.id, toast.message, toast.level, toast.expires_at) for toast in snapshot.toasts
        )
        if toast_signature == self._toast_signature:
            return
        self._toast_signature = toast_signature
        for child in list(stack.children):
            await child.remove()
        widgets = [Static(toast.message, classes=f"toast toast-{toast.level}") for toast in snapshot.toasts]
        if widgets:
            await stack.mount_all(widgets)
        self.display = bool(widgets)

class CommandMenuWidget(Widget):
    MAX_VISIBLE_ROWS = 4
    MAX_RENDERED_ROWS = 24

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.visible_row_count = 0
        self.has_hint = False
        self._selected_index = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="command-menu-body"):
            with VerticalScroll(id="command-menu-list"):
                for _ in range(self.MAX_RENDERED_ROWS):
                    yield CommandSuggestionRowWidget()
            yield Static("", id="command-menu-hint", classes="command-menu-hint")

    def _scroll_list_to_row(self, top_row: int) -> None:
        list_widget = self.query_one("#command-menu-list", VerticalScroll)
        list_widget.scroll_to(
            y=top_row,
            animate=False,
            force=True,
            immediate=True,
        )

    def _ensure_selected_row_visible(self) -> None:
        selected = next((row for row in self.query(CommandSuggestionRowWidget) if row.has_class("-selected")), None)
        if selected is None:
            return

        list_widget = self.query_one("#command-menu-list", VerticalScroll)
        viewport_top = list_widget.region.y
        viewport_bottom = list_widget.region.y + list_widget.region.height
        row_top = selected.region.y
        row_bottom = selected.region.y + selected.region.height
        current_scroll = int(list_widget.scroll_y)

        target_scroll = current_scroll
        if row_bottom > viewport_bottom:
            target_scroll += row_bottom - viewport_bottom
        elif row_top < viewport_top:
            target_scroll = max(0, current_scroll - (viewport_top - row_top))

        if target_scroll != current_scroll:
            self._scroll_list_to_row(target_scroll)

    def update_suggestions(self, suggestions: List[CommandSuggestionView], hint: str = "") -> None:
        rows = list(self.query(CommandSuggestionRowWidget))
        self.visible_row_count = min(len(suggestions), self.MAX_VISIBLE_ROWS)
        self.has_hint = bool(suggestions)
        self._selected_index = 0
        if suggestions:
            self.display = True
        else:
            self.display = False
        selected_row: Optional[CommandSuggestionRowWidget] = None
        for row, item in zip_longest(rows, suggestions[: len(rows)], fillvalue=None):
            row.update_view(item)
            if item is not None and item.selected:
                selected_row = row
                self._selected_index = max(0, item.index - 1)
        hint_widget = self.query_one("#command-menu-hint", Static)
        hint_widget.display = self.has_hint
        hint_widget.update(_truncate_inline(hint, 48) if hint else " ")
        list_widget = self.query_one("#command-menu-list", VerticalScroll)
        list_widget.styles.height = self.visible_row_count if suggestions else 0
        if selected_row is not None:
            actual_visible_rows = max(1, int(list_widget.region.height or self.visible_row_count or 1))
            if getattr(list_widget.styles, "height", None) is not None:
                try:
                    actual_visible_rows = max(1, min(actual_visible_rows, int(list_widget.styles.height.value)))
                except Exception:
                    pass
            max_top = max(0, len(suggestions) - actual_visible_rows)
            lead_row = 2 if actual_visible_rows > 1 else 1
            top_row = max(0, min(self._selected_index - actual_visible_rows + lead_row, max_top))
            self.call_after_refresh(self._scroll_list_to_row, top_row)
            self.call_after_refresh(self._ensure_selected_row_visible)
        elif not suggestions:
            list_widget.scroll_y = 0

class InputPanel(Widget):
    def __init__(self, *, bridge: Any, id: str | None = None) -> None:
        super().__init__(id=id)
        self.bridge = bridge

    def compose(self) -> ComposeResult:
        with Vertical(classes="input-dock"):
            with Horizontal(classes="input-meta-row"):
                yield Static("准备就绪", id="input-context", classes="input-context")
                yield Static("", id="input-ghost", classes="input-ghost")
            yield InteractionHost(bridge=self.bridge, id="interaction-host")
            yield CommandMenuWidget(id="command-menu")
            with Horizontal(classes="input-shell"):
                yield Static("○", id="input-indicator", classes="input-indicator")
                yield Static("Codo >", id="input-prompt", classes="input-prompt")
                yield ChatInput(placeholder="向 Codo 发送消息...", id="chat-input")

    def update_state(
        self,
        *,
        is_generating: bool,
        has_interaction: bool,
        interaction_label: str = "",
        command_hint: str = "",
        can_retry: bool = False,
        is_typing: bool = False,
        pulse_step: int = 0,
    ) -> None:
        self.set_class(is_generating, "-generating")
        self.set_class(has_interaction, "-interaction")
        self.set_class(not is_generating and not has_interaction, "-idle")
        self.set_class(is_typing and not is_generating and not has_interaction, "-typing")

        context = "准备对话"
        if has_interaction:
            context = f"需要你确认 • {interaction_label or '请先处理上方请求'}"
        elif is_generating:
            context = "正在生成回复"
        elif can_retry:
            context = "可重试上一轮"

        if has_interaction:
            ghost = "可直接点击上方操作，也可用快捷键"
        elif is_generating:
            ghost = "Ctrl+C 停止生成"
        elif can_retry:
            ghost = "点击消息下方“重试”，或继续输入新消息"
        else:
            ghost = command_hint or "输入 / 打开命令 • Enter 发送"
        indicator = self.query_one("#input-indicator", Static)
        prompt = self.query_one("#input-prompt", Static)
        is_live_typing = is_typing and not is_generating and not has_interaction
        prompt.set_class(is_live_typing, "-typing")
        prompt.update("Codo >")
        for class_name in ("-idle", "-typing", "-generating", "-interaction"):
            indicator.set_class(False, class_name)
        if has_interaction:
            indicator.update("◆")
            indicator.set_class(True, "-interaction")
        elif is_generating:
            indicator.update("◍" if pulse_step % 2 else "●")
            indicator.set_class(True, "-generating")
        elif is_live_typing:
            indicator.update("●")
            indicator.set_class(True, "-typing")
        else:
            indicator.update("○")
            indicator.set_class(True, "-idle")
        self.query_one("#input-context", Static).update(context)
        self.query_one("#input-ghost", Static).update(ghost)
