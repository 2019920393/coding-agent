"""Textual 内联交互组件。

本模块实现三种内联交互 Widget：
- PermissionRequestWidget: 权限确认（允许/拒绝/中止）
- QuestionRequestWidget: 多问题问答（单选/多选/自定义输入）
- DiffReviewWidget: 文件变更审阅（接受/拒绝/全屏查看）

以及 InteractionHost 宿主 Widget，负责在输入区上方动态挂载/卸载交互组件。
"""

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
    """提取选项的 label 文本，返回去空格后的字符串。"""
    return str(getattr(option, "label", "")).strip()

def _option_description(option: Any) -> str:
    """提取选项的 description 文本，返回去空格后的字符串。"""
    return str(getattr(option, "description", "")).strip()

def _parse_answer(raw_value: str, options: List[Any], multi_select: bool) -> tuple[bool, Optional[str]]:
    """
    解析用户在问题对话框中的输入。

    [Workflow]
    1. 规范化输入（去空格，中文逗号/冒号转英文）
    2. 空输入返回错误提示
    3. "0" 或 "skip" 返回跳过（None）
    4. 多选模式：逗号分隔，支持编号/标签/o:自定义
    5. 单选模式：支持编号/标签/o:自定义

    参数:
        raw_value: 用户原始输入，如 "1"、"1,3"、"o:自定义答案"
        options: 可选项列表
        multi_select: 是否多选模式

    返回:
        (bool, str | None): (是否解析成功, 解析结果或错误信息)
        成功时: (True, "选项标签") 或 (True, "标签1, 标签2") 或 (True, None) 表示跳过
        失败时: (False, "错误提示信息")
    """
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
    """
    截取 diff 文本的前 N 行作为预览。

    参数:
        diff_text: 完整 diff 文本
        max_lines: 最大预览行数，默认 8

    返回:
        str: 截取后的 diff 预览，超出部分显示 "... （还有 X 行）"
    """
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
        """
        初始化全屏 diff 查看对话框。

        参数:
            interaction: 当前交互请求对象，包含 diff 内容和路径信息
                例：InteractionRequest(request_id="abc", kind="diff_review",
                                       label="审阅变更", payload={"path": "a.py", ...})
            on_resolve: 用户确认/拒绝时的回调，签名为 (request_id: str, result: str) -> None
            on_cancel: 用户取消时的回调，签名为 (request_id: str) -> None
        """
        super().__init__(id="diff-full-screen")
        self.interaction = interaction
        self._on_resolve = on_resolve
        self._on_cancel = on_cancel

    def compose(self) -> ComposeResult:
        """组合全屏 diff 查看 UI，左右两栏分别显示原始内容和新内容。"""
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
        """接受变更动作：调用 on_resolve 传入 "accept" 并关闭全屏对话框。"""
        self._on_resolve(self.interaction.request_id, "accept")
        self.dismiss(None)

    def action_reject(self) -> None:
        """拒绝变更动作：调用 on_resolve 传入 "reject" 并关闭全屏对话框。"""
        self._on_resolve(self.interaction.request_id, "reject")
        self.dismiss(None)

    def action_close(self) -> None:
        """关闭对话框动作：不做任何决策，直接 dismiss 全屏对话框。"""
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
        """
        初始化权限请求 Widget。

        参数:
            bridge: UI 桥接对象，用于读取/设置权限模式
                例：UIBridge 实例，提供 get_permission_mode_state()、set_permission_mode() 等方法
            interaction: 当前权限交互请求
                例：InteractionRequest(request_id="xyz", kind="permission",
                                       tool_name="bash", message="执行 rm -rf /tmp/test")
            on_resolve: 用户选择后的回调，签名为 (request_id: str, choice: str) -> None
                choice 取值：'allow_once' | 'allow_always' | 'deny' | 'abort'
            on_cancel: 用户取消时的回调，签名为 (request_id: str) -> None
        """
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
        """组合权限请求 UI，包含标题、详情、操作按钮、选项列表和权限模式切换按钮。"""
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
        """挂载时聚焦 Widget 并在首次刷新后更新选项和权限模式显示。"""
        self.focus()
        self.call_after_refresh(self._refresh_view)

    def focus_default(self) -> None:
        """设置默认焦点到当前 Widget 自身。"""
        self.focus()

    def watch_selected_index(self, _: int) -> None:
        """监听 selected_index 变化，自动刷新选项高亮显示。"""
        self._refresh_view()

    def _refresh_view(self) -> None:
        """
        刷新选项列表和权限模式显示。

        [Workflow]
        1. 检查 Widget 是否已挂载，未挂载则直接返回
        2. 懒加载 _options_view 和 _mode_view 引用
        3. 遍历 options，对当前选中项加上 › ‹ 标记，拼接为一行文本
        4. 从 bridge 获取权限模式状态，更新模式显示文本

        bridge.get_permission_mode_state() 返回结构示例：
            {
                "display_label": "严格询问",
                "bypass_confirmed": False,
                "session_allow_rule_count": 2,
                "session_allow_rules": ["bash", "read"]
            }
        """
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
        """
        处理权限请求 Widget 的键盘交互。

        [Workflow]
        1. up/down 键循环切换 selected_index
        2. enter 键按当前选中项调用 on_resolve
        3. escape 键调用 on_cancel
        4. y/a/n/x 快捷键直接映射到对应选项并调用 on_resolve
        5. b 键切换为直通模式，s 键切换为严格询问模式

        参数:
            key: 按键名称，如 "up"、"down"、"enter"、"y"、"b" 等

        返回:
            bool: True 表示已处理该按键，False 表示未处理
        """
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
        """
        处理权限请求 Widget 中的按钮点击事件。

        根据 button_id 分发到对应的权限决策或模式切换逻辑：
        - permission-allow-once: 本次允许
        - permission-allow-always: 本会话始终允许
        - permission-deny: 拒绝
        - permission-abort: 中止
        - permission-mode-bypass: 切换为直通模式
        - permission-mode-strict: 切换为严格询问模式
        """
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
        """
        初始化问题请求 Widget。

        参数:
            interaction: 当前问题交互请求，包含多个 InteractionQuestion
                例：InteractionRequest(request_id="q1", kind="question",
                                       questions=[InteractionQuestion(header="选择语言",
                                                                      question="请选择编程语言",
                                                                      options=[...])])
            on_resolve: 所有问题回答完毕后的回调，签名为 (request_id: str, answers: dict) -> None
                answers 示例：{"请选择编程语言": "Python", "选择框架": "FastAPI"}
            on_cancel: 用户取消时的回调，签名为 (request_id: str) -> None
        """
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
        """返回当前正在回答的问题对象（按 question_index 索引）。"""
        return self.interaction.questions[self.question_index]

    def compose(self) -> ComposeResult:
        """组合问题 UI，包含进度、标题、正文、选项列表、输入框、提交/取消按钮和反馈区。"""
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
        """挂载时渲染当前问题并将焦点设置到输入框。"""
        self.call_after_refresh(self._render_question)
        self.call_after_refresh(self.focus_default)

    def focus_default(self) -> None:
        """设置默认焦点到问题输入框（#question-input）。"""
        input_widget = self._get_input_widget()
        if input_widget is not None:
            input_widget.focus()

    def watch_selected_index(self, _: int) -> None:
        """监听 selected_index 变化，重新渲染选项按钮和详情，并确保选中项可见。"""
        self._render_option_buttons()
        self._render_option_detail()
        self.call_after_refresh(self._ensure_selected_option_visible)

    def watch_feedback(self, value: str) -> None:
        """监听 feedback 文本变化，将错误/提示信息更新到反馈区 Static。"""
        feedback_view = self._get_feedback_view()
        if feedback_view is not None:
            feedback_view.update(value)

    def watch_question_index(self, _: int) -> None:
        """监听 question_index 变化，重置选中索引和反馈，并重新渲染新问题。"""
        self.selected_index = 0
        self.feedback = ""
        self._render_question()

    def _render_question(self) -> None:
        """
        渲染当前问题到各 UI 组件。

        [Workflow]
        1. 检查 Widget 是否已挂载
        2. 懒加载进度、标题、正文、输入框引用
        3. 更新进度文本（如 "[1/3]"）、标题、正文
        4. 清空输入框
        5. 重新渲染选项按钮和选项详情
        6. 确保选中项在视口内可见
        """
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
        """
        渲染选项按钮列表。

        [Workflow]
        1. 遍历预渲染的 MAX_RENDERED_OPTIONS 个按钮
        2. 有对应选项的按钮：显示并设置标签（选中项加 ▶ 前缀和 -selected 样式）
        3. 超出选项数量的按钮：隐藏并清空标签
        """
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
        """渲染当前选中选项的详情描述，若无描述则显示操作提示文本。"""
        if not self.is_mounted:
            return
        detail_view = self._get_option_detail_view()
        options = list(self.current_question.options)
        if detail_view is None or not options:
            return
        description = _option_description(options[self.selected_index])
        detail_view.update(description or "可用上下键切换，回车确认；也可直接输入编号或 o:自定义。")

    def _get_progress_view(self) -> Optional[Static]:
        """懒加载并返回进度显示 Static（#question-progress），未找到时返回 None。"""
        if self._progress_view is None:
            try:
                self._progress_view = self.query_one("#question-progress", Static)
            except NoMatches:
                return None
        return self._progress_view

    def _get_header_view(self) -> Optional[Static]:
        """懒加载并返回标题显示 Static（#question-header），未找到时返回 None。"""
        if self._header_view is None:
            try:
                self._header_view = self.query_one("#question-header", Static)
            except NoMatches:
                return None
        return self._header_view

    def _get_body_view(self) -> Optional[Static]:
        """懒加载并返回正文显示 Static（#question-body），未找到时返回 None。"""
        if self._body_view is None:
            try:
                self._body_view = self.query_one("#question-body", Static)
            except NoMatches:
                return None
        return self._body_view

    def _get_option_list(self) -> Optional[VerticalScroll]:
        """懒加载并返回选项列表滚动容器（#question-option-list），未找到时返回 None。"""
        if self._option_list is None:
            try:
                self._option_list = self.query_one("#question-option-list", VerticalScroll)
            except NoMatches:
                return None
        return self._option_list

    def _get_option_detail_view(self) -> Optional[Static]:
        """懒加载并返回选项详情 Static（#question-option-detail），未找到时返回 None。"""
        if self._option_detail_view is None:
            try:
                self._option_detail_view = self.query_one("#question-option-detail", Static)
            except NoMatches:
                return None
        return self._option_detail_view

    def _get_feedback_view(self) -> Optional[Static]:
        """懒加载并返回反馈/错误提示 Static（#question-feedback），未找到时返回 None。"""
        if self._feedback_view is None:
            try:
                self._feedback_view = self.query_one("#question-feedback", Static)
            except NoMatches:
                return None
        return self._feedback_view

    def _get_input_widget(self) -> Optional[Input]:
        """懒加载并返回问题输入框 Input（#question-input），未找到时返回 None。"""
        if self._input_widget is None:
            try:
                self._input_widget = self.query_one("#question-input", Input)
            except NoMatches:
                return None
        return self._input_widget

    def _scroll_option_list_to(self, top_row: int) -> None:
        """
        将选项列表滚动到指定的垂直像素位置。

        参数:
            top_row: 目标滚动位置（像素），如 42 表示滚动到距顶部 42px 处
        """
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
        """
        确保当前选中的选项按钮在滚动视口内可见。

        [Workflow]
        1. 获取选项列表容器和所有可见按钮
        2. 计算选中按钮的区域与视口区域的关系
        3. 若选中项超出视口底部，向下滚动；若超出顶部，向上滚动
        4. 调用 _scroll_option_list_to 执行滚动
        """
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
        """
        处理问题请求 Widget 的键盘交互。

        [Workflow]
        1. up/down 键循环切换 selected_index（仅当有选项时）
        2. escape 键调用 on_cancel
        3. enter 键提交当前输入框内容（空时使用当前选中项编号）
        4. 数字键 1-9 直接快速提交对应编号

        参数:
            key: 按键名称，如 "up"、"down"、"enter"、"1" 等

        返回:
            bool: True 表示已处理该按键，False 表示未处理
        """
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
        """处理问题输入框的提交事件（按 Enter），将输入值传给 _submit_raw 解析。"""
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
        """
        解析并提交原始输入，推进问题流程。

        [Workflow]
        1. 若 raw 为空，使用当前 selected_index+1 作为默认编号
        2. 调用 _parse_answer 解析输入
        3. 解析失败：将错误信息写入 feedback 并返回
        4. 解析结果为 None（跳过）：调用 on_cancel
        5. 解析成功：记录答案到 self.answers
        6. 若还有下一题：递增 question_index，聚焦输入框
        7. 若已是最后一题：调用 on_resolve 传入全部答案字典

        参数:
            raw: 用户原始输入字符串，如 "1"、"1,3"、"o:自定义"、""（空时用选中项）

        self.answers 示例：
            {"请选择语言": "Python", "选择框架": "FastAPI"}
        """
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
        """
        处理问题请求 Widget 中的按钮点击事件。

        - question-submit: 提交当前输入框内容
        - question-cancel: 取消当前交互
        - question-option-N: 单选时直接提交编号 N；多选时切换输入框中的编号 N
        """
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
        """
        初始化 diff 审阅 Widget。

        参数:
            interaction: 当前 diff 审阅交互请求，payload 包含文件路径和 diff 内容
                例：InteractionRequest(request_id="d1", kind="diff_review",
                                       label="审阅变更",
                                       payload={"path": "src/main.py",
                                                "diff_text": "@@ -1,3 +1,4 @@...",
                                                "original_content": "旧内容",
                                                "new_content": "新内容"})
            on_resolve: 用户决策后的回调，签名为 (request_id: str, result: str) -> None
                result 取值：'accept' | 'reject'
            on_cancel: 用户取消时的回调，签名为 (request_id: str) -> None
        """
        super().__init__(id="selection-panel", classes="inline-interaction diff-review-interaction bottom-sheet")
        self.interaction = interaction
        self._on_resolve = on_resolve
        self._on_cancel = on_cancel

    def compose(self) -> ComposeResult:
        """组合 diff 审阅 UI，包含标题、消息、文件路径、diff 预览、左右对比栏和操作按钮。"""
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
        """挂载时聚焦 Widget 并在首次刷新后更新 diff 预览显示。"""
        self.focus()
        self.call_after_refresh(self._refresh_view)

    def focus_default(self) -> None:
        """设置默认焦点到当前 DiffReviewWidget 自身。"""
        self.focus()

    def _refresh_view(self) -> None:
        """
        刷新 diff 预览和左右对比栏显示。

        [Workflow]
        1. 检查 Widget 是否已挂载
        2. 从 interaction.payload 提取 diff_text、original_content、new_content
        3. 更新 diff 预览区（截取前 8 行）
        4. 若有原始/新内容，显示全屏查看按钮；否则隐藏
        5. 更新操作提示文本（根据是否有左右对比内容决定是否显示 [V] 全屏查看）
        """
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
        """
        处理 diff 审阅 Widget 的键盘交互。

        [Workflow]
        1. escape 键调用 on_cancel
        2. v 键打开全屏 diff 查看（仅当有 original_content 或 new_content 时）
        3. y/enter 键接受变更，调用 on_resolve 传入 "accept"
        4. n 键拒绝变更，调用 on_resolve 传入 "reject"

        参数:
            key: 按键名称，如 "escape"、"v"、"y"、"n"、"enter"

        返回:
            bool: True 表示已处理该按键，False 表示未处理
        """
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
        """
        处理 diff 审阅 Widget 中的按钮点击事件。

        - diff-accept: 接受变更
        - diff-reject: 拒绝变更
        - diff-cancel: 取消审阅
        - diff-full-view: 打开全屏 diff 查看（复用 handle_interaction_key("v")）
        """
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
    """
    输入区上方的原位交互宿主。

    负责根据当前 InteractionRequest 动态挂载/卸载对应的交互 Widget
    （PermissionRequestWidget / QuestionRequestWidget / DiffReviewWidget）。
    """

    def __init__(self, bridge: UIBridge, **kwargs: Any) -> None:
        """
        初始化交互宿主 Widget。

        参数:
            bridge: UI 桥接对象，用于获取 resolve/cancel 回调和权限模式
                例：UIBridge 实例，提供 resolve_interaction()、cancel_interaction() 等方法
            **kwargs: 传递给父类 Widget 的额外参数（如 id、classes 等）
        """
        super().__init__(**kwargs)
        self.bridge = bridge

    def compose(self) -> ComposeResult:
        """组合宿主容器，内部包含一个 id 为 interaction-container 的 Vertical 容器。"""
        yield Vertical(id="interaction-container")

    async def update_interaction(self, interaction: Optional[InteractionRequest]) -> None:
        """
        根据交互请求动态挂载或卸载子 Widget。

        [Workflow]
        1. 若 interaction 为 None：隐藏宿主，移除所有子 Widget
        2. 若 interaction 不为 None：显示宿主
        3. 检查当前子 Widget 是否与新请求相同（request_id + kind 均匹配），相同则跳过
        4. 移除旧子 Widget
        5. 根据 interaction.kind 挂载对应 Widget：
           - "permission" → PermissionRequestWidget
           - "question"   → QuestionRequestWidget
           - "diff_review"→ DiffReviewWidget

        参数:
            interaction: 新的交互请求，None 表示清除当前交互
                例：InteractionRequest(request_id="r1", kind="permission", tool_name="bash")
        """
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
        """
        聚焦当前挂载的交互 Widget。

        优先调用子 Widget 的 focus_default() 方法；若不存在则直接调用 focus()。
        """
        current = next(iter(self.query("#interaction-container > .inline-interaction")), None)
        if current is not None:
            focus_default = getattr(current, "focus_default", None)
            if focus_default is not None:
                focus_default()
            else:
                current.focus()

    def dispatch_interaction_key(self, key: str) -> bool:
        """
        将键盘事件分发给当前挂载的交互 Widget 处理。

        [Workflow]
        1. 单字符按键转为小写（统一大小写处理）
        2. 查找当前 interaction-container 内的 .inline-interaction 子 Widget
        3. 若无子 Widget 或子 Widget 无 handle_interaction_key，返回 False
        4. 调用子 Widget 的 handle_interaction_key(key) 并返回其结果

        参数:
            key: 按键名称，如 "y"、"n"、"enter"、"escape"、"up"、"down"

        返回:
            bool: True 表示按键已被交互 Widget 消费，False 表示未消费
        """
        normalized_key = key.lower() if len(key) == 1 else key
        current = next(iter(self.query("#interaction-container > .inline-interaction")), None)
        if current is None:
            return False
        handler = getattr(current, "handle_interaction_key", None)
        if handler is None:
            return False
        return bool(handler(normalized_key))
