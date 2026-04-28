"""Textual 内联交互组件。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Input, Static

from .bridge import UIBridge
from .interaction_types import InteractionQuestion, InteractionRequest

def _option_label(option: Any) -> str:
    return str(getattr(option, "label", "")).strip()

def _option_description(option: Any) -> str:
    return str(getattr(option, "description", "")).strip()

def _parse_answer(raw_value: str, options: List[Any], multi_select: bool) -> tuple[bool, Optional[str]]:
    raw = (raw_value or "").strip().replace("，", ",").replace("：", ":")
    if not raw:
        return False, "请输入编号、标签，或 o:自定义答案"
    if raw in ("0", "skip", "SKIP"):
        return True, None

    if multi_select:
        parts = [part.strip() for part in raw.split(",") if part.strip()]
        picks: List[str] = []
        seen = set()
        for part in parts:
            lower = part.lower()
            if lower in ("0", "skip"):
                return True, None
            if lower.startswith("o:"):
                custom = part[2:].strip()
                if not custom:
                    return False, "自定义答案不能为空"
                if custom not in seen:
                    seen.add(custom)
                    picks.append(custom)
                continue
            if part.isdigit():
                index = int(part)
                if index < 1 or index > len(options):
                    return False, f"选项超出范围: {index}"
                label = _option_label(options[index - 1])
                if label and label not in seen:
                    seen.add(label)
                    picks.append(label)
                continue
            matched = next((_option_label(option) for option in options if _option_label(option) == part), "")
            if not matched:
                return False, f"无效输入: {part}"
            if matched not in seen:
                seen.add(matched)
                picks.append(matched)
        if not picks:
            return False, "请至少选择一个有效选项"
        return True, ", ".join(picks)

    lower = raw.lower()
    if lower.startswith("o:"):
        custom = raw[2:].strip()
        if not custom:
            return False, "自定义答案不能为空"
        return True, custom
    if raw.isdigit():
        index = int(raw)
        if index < 1 or index > len(options):
            return False, f"选项超出范围: {index}"
        label = _option_label(options[index - 1])
        if not label:
            return False, "选项内容为空"
        return True, label
    matched = next((_option_label(option) for option in options if _option_label(option) == raw), "")
    if not matched:
        return False, "请输入编号、标签，或 o:自定义答案"
    return True, matched

def _preview_diff(diff_text: str, max_lines: int = 8) -> str:
    lines = (diff_text or "").splitlines()
    if len(lines) <= max_lines:
        return diff_text
    preview = "\n".join(lines[:max_lines]).rstrip()
    return f"{preview}\n... （还有 {len(lines) - max_lines} 行）"

class DiffFullViewScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("y", "accept", show=False),
        Binding("n", "reject", show=False),
        Binding("escape", "close", show=False),
    ]

    def __init__(
        self,
        interaction: InteractionRequest,
        on_resolve: Any,
        on_cancel: Any,
    ) -> None:
        super().__init__(id="diff-full-screen")
        self.interaction = interaction
        self._on_resolve = on_resolve
        self._on_cancel = on_cancel

    def compose(self) -> ComposeResult:
        payload = dict(self.interaction.payload or {})
        with Vertical(id="diff-full-shell"):
            yield Static(self.interaction.label or "审阅变更", classes="diff-full-title")
            yield Static(str(payload.get("path", "")), classes="diff-full-path")
            with Horizontal(id="diff-full-columns"):
                with VerticalScroll(classes="diff-full-pane diff-full-pane-before"):
                    yield Static(str(payload.get("original_content", "") or "(空)"), id="diff-full-before")
                with VerticalScroll(classes="diff-full-pane diff-full-pane-after"):
                    yield Static(str(payload.get("new_content", "") or "(空)"), id="diff-full-after")
            yield Static("[Y] 接受   [N] 拒绝   [Esc] 关闭", classes="diff-full-actions")

    def action_accept(self) -> None:
        self._on_resolve(self.interaction.request_id, "accept")
        self.dismiss(None)

    def action_reject(self) -> None:
        self._on_resolve(self.interaction.request_id, "reject")
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)

class PermissionRequestWidget(Vertical):
    can_focus = True
    selected_index = reactive(0)

    def __init__(
        self,
        bridge: UIBridge,
        interaction: InteractionRequest,
        on_resolve: Any,
        on_cancel: Any,
    ) -> None:
        super().__init__(id="selection-panel", classes="inline-interaction permission-interaction permission-bar")
        self.bridge = bridge
        self.interaction = interaction
        self._on_resolve = on_resolve
        self._on_cancel = on_cancel
        self._options_view: Optional[Static] = None
        self._mode_view: Optional[Static] = None
        self.options = [
            ("allow_once", "[Y] 本次允许"),
            ("allow_always", "[A] 本会话始终允许"),
            ("deny", "[N] 拒绝"),
            ("abort", "[X] 中止"),
        ]

    def compose(self) -> ComposeResult:
        yield Static(f"等待权限 · {self.interaction.tool_name}", classes="dialog-title")
        detail = self.interaction.message or self.interaction.tool_info
        if detail:
            yield Static(detail, classes="dialog-subtle permission-detail")
        with Horizontal(classes="dialog-action-row permission-action-row"):
            yield Button("本次允许", id="permission-allow-once", classes="dialog-action-button")
            yield Button("本会话始终允许", id="permission-allow-always", classes="dialog-action-button")
        with Horizontal(classes="dialog-action-row permission-action-row"):
            yield Button("拒绝", id="permission-deny", classes="dialog-action-button dialog-action-button-muted")
            yield Button("中止", id="permission-abort", classes="dialog-action-button dialog-action-button-muted")
        yield Static("", classes="dialog-option-list permission-options-inline")
        with Horizontal(classes="dialog-action-row permission-mode-row"):
            yield Button("后续直通", id="permission-mode-bypass", classes="dialog-chip-button")
            yield Button("严格询问", id="permission-mode-strict", classes="dialog-chip-button")
        yield Static("", classes="dialog-subtle permission-mode-inline")

    def on_mount(self) -> None:
        self.focus()
        self.call_after_refresh(self._refresh_view)

    def focus_default(self) -> None:
        self.focus()

    def watch_selected_index(self, _: int) -> None:
        self._refresh_view()

    def _refresh_view(self) -> None:
        if not self.is_mounted:
            return
        if self._options_view is None:
            try:
                self._options_view = self.query_one(".dialog-option-list", Static)
            except NoMatches:
                return
        if self._mode_view is None:
            try:
                self._mode_view = self.query_one(".permission-mode-inline", Static)
            except NoMatches:
                return
        segments = []
        for index, (_, label) in enumerate(self.options):
            if index == self.selected_index:
                segments.append(f"› {label} ‹")
            else:
                segments.append(label)
        self._options_view.update("   ".join(segments))
        state = self.bridge.get_permission_mode_state()
        self._mode_view.update(
            f"模式 {state['display_label']} · 会话规则 {state['session_allow_rule_count']} · [B] 后续工具直通 · [S] 严格询问"
        )

    def handle_interaction_key(self, key: str) -> bool:
        if key == "up":
            self.selected_index = (self.selected_index - 1) % len(self.options)
            return True
        if key == "down":
            self.selected_index = (self.selected_index + 1) % len(self.options)
            return True
        if key == "enter":
            self._on_resolve(self.interaction.request_id, self.options[self.selected_index][0])
            return True
        if key == "escape":
            self._on_cancel(self.interaction.request_id)
            return True
        if key in {"y", "a", "n", "x"}:
            mapping = {
                "y": "allow_once",
                "a": "allow_always",
                "n": "deny",
                "x": "abort",
            }
            self._on_resolve(self.interaction.request_id, mapping[key])
            return True
        if key == "b":
            self.bridge.set_permission_mode("bypass", confirm=True, source="interaction")
            self._refresh_view()
            return True
        if key == "s":
            self.bridge.set_permission_mode("ask", strict=True, source="interaction")
            self._refresh_view()
            return True
        return False

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "permission-allow-once":
            self._on_resolve(self.interaction.request_id, "allow_once")
        elif button_id == "permission-allow-always":
            self._on_resolve(self.interaction.request_id, "allow_always")
        elif button_id == "permission-deny":
            self._on_resolve(self.interaction.request_id, "deny")
        elif button_id == "permission-abort":
            self._on_resolve(self.interaction.request_id, "abort")
        elif button_id == "permission-mode-bypass":
            self.bridge.set_permission_mode("bypass", confirm=True, source="interaction")
            self._refresh_view()
        elif button_id == "permission-mode-strict":
            self.bridge.set_permission_mode("ask", strict=True, source="interaction")
            self._refresh_view()
        else:
            return
        event.stop()

class QuestionRequestWidget(Vertical):
    MAX_RENDERED_OPTIONS = 24

    selected_index = reactive(0)
    question_index = reactive(0)
    feedback = reactive("")

    def __init__(
        self,
        interaction: InteractionRequest,
        on_resolve: Any,
        on_cancel: Any,
    ) -> None:
        super().__init__(id="selection-panel", classes="inline-interaction question-interaction bottom-sheet")
        self.interaction = interaction
        self._on_resolve = on_resolve
        self._on_cancel = on_cancel
        self.answers: Dict[str, str] = {}
        self._progress_view: Optional[Static] = None
        self._header_view: Optional[Static] = None
        self._body_view: Optional[Static] = None
        self._option_list: Optional[VerticalScroll] = None
        self._option_detail_view: Optional[Static] = None
        self._feedback_view: Optional[Static] = None
        self._input_widget: Optional[Input] = None

    @property
    def current_question(self) -> InteractionQuestion:
        return self.interaction.questions[self.question_index]

    def compose(self) -> ComposeResult:
        yield Static("", id="question-progress", classes="dialog-subtle")
        yield Static("", id="question-header", classes="dialog-title")
        yield Static("", id="question-body", classes="dialog-body")
        with VerticalScroll(id="question-option-list", classes="question-option-list-scroll"):
            for index in range(1, self.MAX_RENDERED_OPTIONS + 1):
                yield Button("", id=f"question-option-{index}", classes="dialog-chip-button question-option-button")
        yield Static("", id="question-option-detail", classes="dialog-subtle question-option-detail")
        yield Input(placeholder="回车选择高亮项，或输入 1 / 1,2 / o:自定义", id="question-input")
        with Horizontal(classes="dialog-action-row question-submit-row"):
            yield Button("提交", id="question-submit", classes="dialog-action-button")
            yield Button("取消", id="question-cancel", classes="dialog-action-button dialog-action-button-muted")
        yield Static("", id="question-feedback", classes="dialog-error")

    def on_mount(self) -> None:
        self.call_after_refresh(self._render_question)
        self.call_after_refresh(self.focus_default)

    def focus_default(self) -> None:
        input_widget = self._get_input_widget()
        if input_widget is not None:
            input_widget.focus()

    def watch_selected_index(self, _: int) -> None:
        self._render_option_buttons()
        self._render_option_detail()
        self.call_after_refresh(self._ensure_selected_option_visible)

    def watch_feedback(self, value: str) -> None:
        feedback_view = self._get_feedback_view()
        if feedback_view is not None:
            feedback_view.update(value)

    def watch_question_index(self, _: int) -> None:
        self.selected_index = 0
        self.feedback = ""
        self._render_question()

    def _render_question(self) -> None:
        if not self.is_mounted:
            return
        question = self.current_question
        total = len(self.interaction.questions)
        progress_view = self._get_progress_view()
        header_view = self._get_header_view()
        body_view = self._get_body_view()
        input_widget = self._get_input_widget()
        if progress_view is None or header_view is None or body_view is None or input_widget is None:
            return
        progress_view.update(f"[{self.question_index + 1}/{total}]")
        header_view.update(question.header)
        body_view.update(question.question)
        input_widget.value = ""
        self._render_option_buttons()
        self._render_option_detail()
        self.call_after_refresh(self._ensure_selected_option_visible)

    def _render_option_buttons(self) -> None:
        if not self.is_mounted:
            return
        buttons = list(self.query(".question-option-button"))
        options = list(self.current_question.options)
        for index, button in enumerate(buttons, 1):
            if index <= len(options):
                option = options[index - 1]
                button.display = True
                prefix = "▶ " if index - 1 == self.selected_index else ""
                button.label = f"{prefix}[{index}] {_option_label(option)}"
                if index - 1 == self.selected_index:
                    button.add_class("-selected")
                else:
                    button.remove_class("-selected")
            else:
                button.display = False
                button.label = ""
                button.remove_class("-selected")

    def _render_option_detail(self) -> None:
        if not self.is_mounted:
            return
        detail_view = self._get_option_detail_view()
        options = list(self.current_question.options)
        if detail_view is None or not options:
            return
        description = _option_description(options[self.selected_index])
        detail_view.update(description or "可用上下键切换，回车确认；也可直接输入编号或 o:自定义。")

    def _get_progress_view(self) -> Optional[Static]:
        if self._progress_view is None:
            try:
                self._progress_view = self.query_one("#question-progress", Static)
            except NoMatches:
                return None
        return self._progress_view

    def _get_header_view(self) -> Optional[Static]:
        if self._header_view is None:
            try:
                self._header_view = self.query_one("#question-header", Static)
            except NoMatches:
                return None
        return self._header_view

    def _get_body_view(self) -> Optional[Static]:
        if self._body_view is None:
            try:
                self._body_view = self.query_one("#question-body", Static)
            except NoMatches:
                return None
        return self._body_view

    def _get_option_list(self) -> Optional[VerticalScroll]:
        if self._option_list is None:
            try:
                self._option_list = self.query_one("#question-option-list", VerticalScroll)
            except NoMatches:
                return None
        return self._option_list

    def _get_option_detail_view(self) -> Optional[Static]:
        if self._option_detail_view is None:
            try:
                self._option_detail_view = self.query_one("#question-option-detail", Static)
            except NoMatches:
                return None
        return self._option_detail_view

    def _get_feedback_view(self) -> Optional[Static]:
        if self._feedback_view is None:
            try:
                self._feedback_view = self.query_one("#question-feedback", Static)
            except NoMatches:
                return None
        return self._feedback_view

    def _get_input_widget(self) -> Optional[Input]:
        if self._input_widget is None:
            try:
                self._input_widget = self.query_one("#question-input", Input)
            except NoMatches:
                return None
        return self._input_widget

    def _scroll_option_list_to(self, top_row: int) -> None:
        option_list = self._get_option_list()
        if option_list is None:
            return
        option_list.scroll_to(
            y=top_row,
            animate=False,
            force=True,
            immediate=True,
        )

    def _ensure_selected_option_visible(self) -> None:
        option_list = self._get_option_list()
        if option_list is None:
            return
        visible_buttons = [button for button in self.query(".question-option-button") if button.display]
        if not visible_buttons or self.selected_index >= len(visible_buttons):
            return

        selected = visible_buttons[self.selected_index]
        viewport_top = option_list.region.y
        viewport_bottom = option_list.region.y + option_list.region.height
        row_top = selected.region.y
        row_bottom = selected.region.y + selected.region.height
        current_scroll = int(option_list.scroll_y)

        target_scroll = current_scroll
        if row_bottom > viewport_bottom:
            target_scroll += row_bottom - viewport_bottom
        elif row_top < viewport_top:
            target_scroll = max(0, current_scroll - (viewport_top - row_top))

        if target_scroll != current_scroll:
            self._scroll_option_list_to(target_scroll)

    def handle_interaction_key(self, key: str) -> bool:
        options = self.current_question.options
        if key == "up" and options:
            self.selected_index = (self.selected_index - 1) % len(options)
            return True
        if key == "down" and options:
            self.selected_index = (self.selected_index + 1) % len(options)
            return True
        if key == "escape":
            self._on_cancel(self.interaction.request_id)
            return True
        if key == "enter":
            input_widget = self._get_input_widget()
            self._submit_raw(input_widget.value.strip() if input_widget is not None else "")
            return True
        if key.isdigit() and key != "0":
            self._submit_raw(key)
            return True
        return False

    @on(Input.Submitted, "#question-input")
    def on_question_submitted(self, event: Input.Submitted) -> None:
        self._submit_raw(event.value.strip())

    @on(Input.Changed, "#question-input")
    def on_question_changed(self, event: Input.Changed) -> None:
        """
        支持数字键 1-9 的即时确认。

        当问题是单选时，用户在输入框里直接敲 1/2/3 应该立刻完成选择，
        不必再额外按 Enter。多选和自定义输入仍然走显式提交。
        """
        value = event.value.strip()
        question = self.current_question
        if question.multi_select:
            return
        if not value.isdigit():
            return
        if len(value) != 1:
            return
        self._submit_raw(value)

    def _submit_raw(self, raw: str) -> None:
        question = self.current_question
        raw_value = raw or str(self.selected_index + 1)
        ok, parsed = _parse_answer(raw_value, question.options, question.multi_select)
        if not ok:
            self.feedback = str(parsed)
            return
        if parsed is None:
            self._on_cancel(self.interaction.request_id)
            return

        self.answers[question.question] = parsed
        if self.question_index >= len(self.interaction.questions) - 1:
            self._on_resolve(self.interaction.request_id, dict(self.answers))
            return

        self.question_index += 1
        input_widget = self._get_input_widget()
        if input_widget is not None:
            input_widget.focus()

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "question-submit":
            input_widget = self._get_input_widget()
            self._submit_raw(input_widget.value.strip() if input_widget is not None else "")
            event.stop()
            return
        if button_id == "question-cancel":
            self._on_cancel(self.interaction.request_id)
            event.stop()
            return
        if button_id.startswith("question-option-"):
            try:
                index = int(button_id.rsplit("-", 1)[1])
            except ValueError:
                return
            if self.current_question.multi_select:
                input_widget = self._get_input_widget()
                if input_widget is None:
                    return
                current = [part.strip() for part in input_widget.value.split(",") if part.strip()]
                token = str(index)
                if token in current:
                    current.remove(token)
                else:
                    current.append(token)
                input_widget.value = ",".join(current)
                input_widget.focus()
            else:
                self._submit_raw(str(index))
            event.stop()

class DiffReviewWidget(Vertical):
    can_focus = True

    def __init__(
        self,
        interaction: InteractionRequest,
        on_resolve: Any,
        on_cancel: Any,
    ) -> None:
        super().__init__(id="selection-panel", classes="inline-interaction diff-review-interaction bottom-sheet")
        self.interaction = interaction
        self._on_resolve = on_resolve
        self._on_cancel = on_cancel

    def compose(self) -> ComposeResult:
        yield Static(self.interaction.label or "审阅变更", classes="dialog-title")
        yield Static(self.interaction.message or "", classes="dialog-subtle")
        yield Static(str(self.interaction.payload.get("path", "")), classes="dialog-body diff-review-path")
        yield Static("", classes="dialog-option-list diff-review-preview")
        with Horizontal(classes="diff-review-columns"):
            yield Static("", classes="diff-pane diff-pane-before")
            yield Static("", classes="diff-pane diff-pane-after")
        with Horizontal(classes="dialog-action-row diff-review-button-row"):
            yield Button("接受", id="diff-accept", classes="dialog-action-button")
            yield Button("拒绝", id="diff-reject", classes="dialog-action-button dialog-action-button-muted")
            yield Button("全屏查看", id="diff-full-view", classes="dialog-chip-button")
            yield Button("取消", id="diff-cancel", classes="dialog-chip-button")
        yield Static("", classes="dialog-subtle diff-review-actions")

    def on_mount(self) -> None:
        self.focus()
        self.call_after_refresh(self._refresh_view)

    def focus_default(self) -> None:
        self.focus()

    def _refresh_view(self) -> None:
        if not self.is_mounted:
            return
        payload = dict(self.interaction.payload or {})
        preview = self.query_one(".diff-review-preview", Static)
        columns = self.query_one(".diff-review-columns", Horizontal)
        before = self.query_one(".diff-pane-before", Static)
        after = self.query_one(".diff-pane-after", Static)
        actions = self.query_one(".diff-review-actions", Static)
        full_view_button = self.query_one("#diff-full-view", Button)
        diff_text = str(payload.get("diff_text", "") or "")
        original_content = str(payload.get("original_content", "") or "")
        new_content = str(payload.get("new_content", "") or "")

        preview.display = True
        preview.update(_preview_diff(diff_text))

        has_side_by_side = bool(original_content or new_content)
        columns.display = False
        full_view_button.display = has_side_by_side
        before.update(original_content or "(空)")
        after.update(new_content or "(空)")
        actions.update(
            "[Y] 接受   [N] 拒绝   [V] 全屏查看   [Esc] 取消"
            if has_side_by_side
            else "[Y] 接受   [N] 拒绝   [Esc] 取消"
        )

    def handle_interaction_key(self, key: str) -> bool:
        if key == "escape":
            self._on_cancel(self.interaction.request_id)
            return True
        if key == "v":
            if self.interaction.payload.get("original_content") or self.interaction.payload.get("new_content"):
                self.app.push_screen(
                    DiffFullViewScreen(
                        self.interaction,
                        on_resolve=self._on_resolve,
                        on_cancel=self._on_cancel,
                    )
                )
            return True
        if key in {"y", "enter"}:
            self._on_resolve(self.interaction.request_id, "accept")
            return True
        if key == "n":
            self._on_resolve(self.interaction.request_id, "reject")
            return True
        return False

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "diff-accept":
            self._on_resolve(self.interaction.request_id, "accept")
        elif button_id == "diff-reject":
            self._on_resolve(self.interaction.request_id, "reject")
        elif button_id == "diff-cancel":
            self._on_cancel(self.interaction.request_id)
        elif button_id == "diff-full-view":
            self.handle_interaction_key("v")
        else:
            return
        event.stop()

class InteractionHost(Widget):
    """输入区上方的原位交互宿主。"""

    def __init__(self, bridge: UIBridge, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.bridge = bridge

    def compose(self) -> ComposeResult:
        yield Vertical(id="interaction-container")

    async def update_interaction(self, interaction: Optional[InteractionRequest]) -> None:
        container = self.query_one("#interaction-container", Vertical)
        if interaction is None:
            self.display = False
            for child in list(container.children):
                await child.remove()
            return

        self.display = True
        current = next(iter(container.children), None)
        if current is not None and getattr(current, "interaction", None) is not None:
            current_interaction = getattr(current, "interaction")
            if (
                current_interaction.request_id == interaction.request_id
                and current_interaction.kind == interaction.kind
            ):
                return

        for child in list(container.children):
            await child.remove()

        if interaction.kind == "permission":
            await container.mount(
                PermissionRequestWidget(
                    self.bridge,
                    interaction,
                    on_resolve=self.bridge.resolve_interaction,
                    on_cancel=self.bridge.cancel_interaction,
                )
            )
        elif interaction.kind == "question":
            await container.mount(
                QuestionRequestWidget(
                    interaction,
                    on_resolve=self.bridge.resolve_interaction,
                    on_cancel=self.bridge.cancel_interaction,
                )
            )
        elif interaction.kind == "diff_review":
            await container.mount(
                DiffReviewWidget(
                    interaction,
                    on_resolve=self.bridge.resolve_interaction,
                    on_cancel=self.bridge.cancel_interaction,
                )
            )

    def focus_interaction(self) -> None:
        current = next(iter(self.query("#interaction-container > .inline-interaction")), None)
        if current is not None:
            focus_default = getattr(current, "focus_default", None)
            if focus_default is not None:
                focus_default()
            else:
                current.focus()

    def dispatch_interaction_key(self, key: str) -> bool:
        normalized_key = key.lower() if len(key) == 1 else key
        current = next(iter(self.query("#interaction-container > .inline-interaction")), None)
        if current is None:
            return False
        handler = getattr(current, "handle_interaction_key", None)
        if handler is None:
            return False
        return bool(handler(normalized_key))
