"""Textual 主应用。"""

from __future__ import annotations

from dataclasses import dataclass
import json
import shlex
import subprocess
import sys
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import Button, TextArea

from codo import __version__
from codo.commands import find_command, get_enabled_commands
from codo.commands.base import Command, CommandType
from codo.commands.skills import build_skill_commands, list_skill_summaries
from codo.services.memory import MemoryManager
from codo.services.memory.paths import ensure_memory_dir, get_project_memory_dir
from codo.services.memory.scan import format_memory_manifest, load_memory_index, scan_memory_files
from codo.session.export import export_session, generate_default_filename
from codo.services.token_estimation import TokenBudget
from codo.session.storage import (
    SessionStorage as RuntimeSessionStorage,
    get_session_event_log_path,
    get_session_file_path,
    get_session_snapshot_path,
    get_sessions_dir as get_runtime_sessions_dir,
)
from codo.tools.receipts import ProposedFileChange
from codo.utils.config import get_config_file, get_merged_config, get_project_config_file

from .bridge import UISnapshot, UIBridge
from .dialogs import InteractionHost
from .runtime import clear_active_app, set_active_app
from .widgets import (
    ChatInput,
    CommandMenuWidget,
    CommandSuggestionRowWidget,
    CommandSuggestionView,
    HeaderPanel,
    InputPanel,
    MessageColumn,
    SidebarPanel,
    ToastHost,
)

class BridgeUpdated(Message):
    pass

@dataclass
class SlashSuggestion:
    kind: str
    command: Command
    completion: str
    name: str
    description: str
    hint: str = ""

class TextualChatApp(App[None]):
    """
    Codo Textual UI 主应用。

    [核心职责]
    1. 渲染聊天界面（消息列表、输入框、侧边栏、状态栏）
    2. 处理用户输入（消息提交、斜杠命令、快捷键）
    3. 通过 UIBridge 订阅引擎事件，实时更新 UI
    4. 管理交互对话框（权限确认、问题问答、diff 审阅）

    [布局结构]
    ┌─ HeaderPanel ──────────────────────────┐
    │ 模型名 · 状态 · token 用量 · 权限模式  │
    ├─ SidebarPanel ─┬─ MessageColumn ───────┤
    │ 代理列表       │ 消息列表              │
    │ TODO 进度      │ 工具调用卡片          │
    │ 活动状态       │ 思考过程              │
    ├────────────────┴───────────────────────┤
    │ InteractionHost（交互对话框区域）       │
    │ InputPanel（输入框 + 命令菜单）         │
    └─ ToastHost（通知区域）─────────────────┘
    """

    CSS_PATH = "styles.tcss"
    BINDINGS = [
        Binding("tab", "cycle_container_focus", show=False),
        Binding("ctrl+c", "interrupt_or_exit", show=False),
        Binding("[", "cycle_sidebar_backward", show=False),
        Binding("]", "cycle_sidebar_forward", show=False),
        Binding("a", "toggle_auto_follow", show=False),
        Binding("0", "sidebar_global", show=False),
        Binding("alt+1", "sidebar_global", show=False),
    ]

    def __init__(
        self,
        bridge: UIBridge,
        initial_prompt: Optional[str] = None,
        initial_session_query: str = "",
    ) -> None:
        """
        初始化 Textual 聊天应用。

        参数:
            bridge: UI 桥接对象，连接引擎与 UI 层
                例：UIBridge 实例，提供 submit_prompt()、get_snapshot()、subscribe() 等方法
            initial_prompt: 启动时自动提交的初始 prompt 字符串，如 "帮我写一个排序函数"
            initial_session_query: 启动时预填充到输入框的会话查询字符串，如 "abc123"
        """
        super().__init__()
        self.bridge = bridge
        self.initial_prompt = initial_prompt
        self.initial_session_query = initial_session_query
        self._last_snapshot = bridge.get_snapshot()
        self._commands = self._build_commands()
        self._command_suggestions: List[SlashSuggestion] = []
        self._command_index = 0
        self._command_hint = ""
        self._pulse_step = 0
        self._applied_interaction_id: Optional[str] = None
        self._pending_bridge_snapshot: Optional[UISnapshot] = None
        self._bridge_update_enqueued = False
        self._bridge_apply_in_progress = False
        self.bridge.subscribe(self._relay_bridge_update)

    def compose(self) -> ComposeResult:
        """
        组合主布局，依次挂载 HeaderPanel、侧边栏+消息列、底部输入区和 ToastHost。

        布局结构：
            HeaderPanel（顶部状态栏）
            Horizontal（中部）
                SidebarPanel（左侧边栏）
                MessageColumn（消息列表）
            Vertical（底部）
                InputPanel（输入框 + 命令菜单）
            ToastHost（通知浮层）
        """
        yield HeaderPanel(id="app-header")
        with Horizontal(id="app-middle"):
            yield SidebarPanel(id="sidebar")
            yield MessageColumn()
        with Vertical(id="app-bottom"):
            yield InputPanel(bridge=self.bridge, id="input-panel")
        yield ToastHost(id="toast-host")

    async def on_mount(self) -> None:
        """
        挂载时初始化 App：注册活跃 App、设置定时器、应用初始快照并处理初始 prompt。

        [Workflow]
        1. 注册当前 App 为活跃 App（供工具层回调使用）
        2. 设置 0.5 秒定时器执行 _tick_housekeeping
        3. 根据窗口宽度更新布局模式
        4. 应用初始 UISnapshot 到所有 UI 组件
        5. 聚焦输入框
        6. 若有 initial_prompt：填充并自动提交
        7. 若有 initial_session_query：预填充 /sessions 命令到输入框
        """
        set_active_app(self)
        self.set_interval(0.5, self._tick_housekeeping)
        self._update_layout_mode(self.size.width)
        await self._apply_snapshot(self._last_snapshot)
        chat_input = self.query_one("#chat-input", ChatInput)
        chat_input.focus()
        if self.initial_prompt:
            chat_input.value = self.initial_prompt
            await self._submit_prompt(self.initial_prompt)
        elif self.initial_session_query:
            prefill = f"/sessions {self.initial_session_query}".rstrip()
            chat_input.value = prefill
            self._refresh_command_menu(prefill)

    async def on_unmount(self) -> None:
        """卸载时清理活跃 App 引用并关闭 bridge 连接。"""
        clear_active_app(self)
        self.bridge.close()

    def on_resize(self, event: events.Resize) -> None:
        """响应窗口大小变化，根据新宽度更新布局模式。"""
        self._update_layout_mode(event.size.width)

    def _update_layout_mode(self, width: int) -> None:
        """
        根据终端宽度切换布局模式。

        [Workflow]
        1. 宽度 < 90：添加 -hide-sidebar 类（隐藏侧边栏）
        2. 宽度 < 50：添加 -narrow-cards 类（窄卡片模式）
        3. 宽度 >= 90：动态调整侧边栏宽度（最小 20，最大 30，约为总宽 1/4）
        4. 宽度 >= 120：进一步收窄侧边栏（约为总宽 1/5）
        5. 宽度 < 90 且焦点在侧边栏：将焦点转移到输入框

        参数:
            width: 当前终端宽度（字符列数），如 120
        """
        self.set_class(width < 90, "-hide-sidebar")
        self.set_class(width < 50, "-narrow-cards")
        sidebar = self.query_one(SidebarPanel)
        if width >= 90:
            sidebar.styles.width = max(20, min(30, width // 4))
        if width >= 120:
            sidebar.styles.width = max(20, min(28, width // 5))
        if width < 90 and self.focused is sidebar:
            self.query_one("#chat-input", ChatInput).focus()

    def _relay_bridge_update(self, snapshot: UISnapshot) -> None:
        """
        接收 bridge 快照更新，入队处理以避免重复触发。

        [Workflow]
        1. 将最新快照存入 _pending_bridge_snapshot（覆盖旧值，只保留最新）
        2. 若已有更新入队（_bridge_update_enqueued=True），直接返回（防止重复消息）
        3. 否则标记入队并 post_message(BridgeUpdated())

        参数:
            snapshot: bridge 推送的最新 UISnapshot 快照
        """
        self._pending_bridge_snapshot = snapshot
        if self._bridge_update_enqueued:
            return
        self._bridge_update_enqueued = True
        self.post_message(BridgeUpdated())

    async def on_bridge_updated(self, message: BridgeUpdated) -> None:
        """
        处理 BridgeUpdated 消息，循环消费所有待处理快照。

        [Workflow]
        1. 若已有 apply 在进行中，直接返回（防止并发）
        2. 标记 _bridge_apply_in_progress = True
        3. 循环取出 _pending_bridge_snapshot 并调用 _apply_snapshot
        4. 完成后清除标志；若期间又有新快照入队，再次 post_message 触发下一轮
        """
        if self._bridge_apply_in_progress:
            return

        self._bridge_apply_in_progress = True
        try:
            while self._pending_bridge_snapshot is not None:
                snapshot = self._pending_bridge_snapshot
                self._pending_bridge_snapshot = None
                self._last_snapshot = snapshot
                try:
                    await self._apply_snapshot(snapshot)
                except NoMatches:
                    return
        finally:
            self._bridge_apply_in_progress = False
            self._bridge_update_enqueued = False
            if self._pending_bridge_snapshot is not None:
                self._bridge_update_enqueued = True
                self.post_message(BridgeUpdated())

    async def _apply_snapshot(self, snapshot: UISnapshot) -> None:
        """
        将 UISnapshot 应用到所有 UI 组件。

        [Workflow]
        1. 更新 HeaderPanel（状态栏、模型名、token 用量）
        2. 更新 MessageColumn（消息列表）
        3. 更新 InteractionHost（挂载/卸载交互 Widget）
        4. 若有新交互请求，聚焦交互 Widget
        5. 更新输入框只读状态和 placeholder
        6. 若交互结束，将焦点归还输入框
        7. 刷新输入面板状态
        8. 更新 SidebarPanel 和 ToastHost
        9. 维持消息列表的自动跟随位置

        参数:
            snapshot: 最新的 UISnapshot 快照
                例：UISnapshot(messages=[...], is_generating=True,
                               interaction=None, status=StatusInfo(...))
        """
        self.query_one(HeaderPanel).update_snapshot(snapshot, pulse_step=self._pulse_step)
        message_column = self.query_one(MessageColumn)
        await message_column.update_messages(snapshot.messages)
        host = self.query_one(InteractionHost)
        had_interaction = host.display
        interaction_id = snapshot.interaction.request_id if snapshot.interaction is not None else None
        await host.update_interaction(snapshot.interaction)
        if interaction_id is not None and interaction_id != self._applied_interaction_id:
            host.focus_interaction()

        chat_input = self.query_one("#chat-input", ChatInput)
        chat_input.read_only = snapshot.is_generating or snapshot.interaction is not None
        if snapshot.is_generating:
            chat_input.placeholder = "[Ctrl+C] 停止生成..."
        elif snapshot.interaction is not None:
            chat_input.placeholder = "请先处理上方交互..."
        else:
            chat_input.placeholder = "向 Codo 发送消息..."
        if had_interaction and snapshot.interaction is None:
            chat_input.focus()
        self._applied_interaction_id = interaction_id
        self._refresh_input_panel_state()
        await self.query_one(SidebarPanel).update_snapshot(snapshot)
        await self.query_one(ToastHost).update_snapshot(snapshot)
        message_column.maintain_follow_position()

    async def _tick_housekeeping(self) -> None:
        """
        定时维护任务（每 0.5 秒执行一次）。

        [Workflow]
        1. 递增 _pulse_step（用于动画脉冲效果）
        2. 清理过期 Toast 通知
        3. 获取最新快照并更新 HeaderPanel
        4. 更新 ToastHost
        5. 刷新输入面板状态
        """
        self._pulse_step += 1
        self.bridge.prune_toasts()
        try:
            snapshot = self.bridge.get_snapshot()
            self._last_snapshot = snapshot
            self.query_one(HeaderPanel).update_snapshot(snapshot, pulse_step=self._pulse_step)
            await self.query_one(ToastHost).update_snapshot(snapshot)
            self._refresh_input_panel_state()
        except NoMatches:
            return

    def action_cycle_container_focus(self) -> None:
        """
        循环切换焦点（Tab 键）：输入框 → 消息列 → 侧边栏 → 输入框。
        """
        input_widget = self.query_one("#chat-input", ChatInput)
        chat = self.query_one(MessageColumn)
        sidebar = self.query_one(SidebarPanel)
        order = [input_widget, chat, sidebar]
        focused = self.focused
        try:
            index = order.index(focused)  # type: ignore[arg-type]
        except ValueError:
            index = -1
        order[(index + 1) % len(order)].focus()

    async def action_interrupt_or_exit(self) -> None:
        """
        Ctrl+C 中断或退出：有活跃交互时取消交互，生成中时中断生成，否则退出 App。
        """
        if self.bridge.has_active_interaction:
            self.bridge.cancel_interaction()
            return
        if self.bridge.is_generating:
            self.bridge.interrupt_generation()
            return
        self.exit()

    def action_cycle_sidebar_backward(self) -> None:
        """侧边栏向后循环（[ 键）：仅当焦点在侧边栏时有效，调用 bridge.cycle_sidebar(-1)。"""
        if self.focused is self.query_one(SidebarPanel):
            self.bridge.cycle_sidebar(-1)

    def action_cycle_sidebar_forward(self) -> None:
        """侧边栏向前循环（] 键）：仅当焦点在侧边栏时有效，调用 bridge.cycle_sidebar(1)。"""
        if self.focused is self.query_one(SidebarPanel):
            self.bridge.cycle_sidebar(1)

    def action_toggle_auto_follow(self) -> None:
        """切换自动跟随（a 键）：仅当焦点在侧边栏时有效，调用 bridge.toggle_auto_follow()。"""
        if self.focused is self.query_one(SidebarPanel):
            self.bridge.toggle_auto_follow()

    def action_sidebar_global(self) -> None:
        """切换到全局视图（0 键 / Alt+1）：调用 bridge.set_sidebar_global()。"""
        self.bridge.set_sidebar_global()

    def on_key(self, event: events.Key) -> None:
        """
        全局键盘事件处理。

        [Workflow]
        1. 若当前屏幕不是默认屏幕（如模态对话框），直接返回
        2. 优先将按键分发给 InteractionHost（交互 Widget 消费则阻止默认行为）
        3. 若焦点在侧边栏，数字键 1-9 切换对应 Agent
        4. 若焦点在输入框，将按键传给命令菜单导航处理
        """
        if getattr(self.screen, "id", "_default") != "_default":
            return

        if self.query_one(InteractionHost).dispatch_interaction_key(event.key):
            event.prevent_default()
            event.stop()
            return

        if self.focused is self.query_one(SidebarPanel):
            if event.key.isdigit() and event.key != "0":
                self.bridge.select_agent(int(event.key) - 1)
                event.stop()
                return

        if self.focused is self.query_one("#chat-input", ChatInput):
            if self._handle_command_menu_key(event):
                return

    def _handle_command_menu_key(self, event: events.Key) -> bool:
        """
        处理命令菜单的键盘导航。

        [Workflow]
        1. 若无命令建议列表，直接返回 False
        2. down 键：_command_index 向后循环
        3. up 键：_command_index 向前循环
        4. tab 键：应用当前选中命令的补全
        5. 其他按键：返回 False（不消费）
        6. 刷新命令菜单显示，阻止事件默认行为

        参数:
            event: Textual 键盘事件对象

        返回:
            bool: True 表示已处理，False 表示未处理
        """
        if not self._command_suggestions:
            return False

        key = event.key
        if key == "down":
            self._command_index = (self._command_index + 1) % len(self._command_suggestions)
        elif key == "up":
            self._command_index = (self._command_index - 1) % len(self._command_suggestions)
        elif key == "tab":
            self._apply_selected_command_completion()
        else:
            return False

        self._refresh_command_menu(self.query_one("#chat-input", ChatInput).value)
        event.prevent_default()
        event.stop()
        return True

    def _apply_selected_command_completion(self) -> None:
        """将当前选中命令建议的 completion 字符串填充到输入框。"""
        if not self._command_suggestions:
            return
        selected = self._command_suggestions[self._command_index]
        self.query_one("#chat-input", ChatInput).value = selected.completion

    @on(CommandSuggestionRowWidget.Selected)
    def on_command_suggestion_selected(self, event: CommandSuggestionRowWidget.Selected) -> None:
        """
        处理命令建议行被点击选中的事件。

        [Workflow]
        1. 若无建议列表，直接返回
        2. 将 event.index 转换为 _command_index（0-based）
        3. 应用选中命令的补全到输入框
        4. 刷新命令菜单并将焦点归还输入框
        """
        if not self._command_suggestions:
            return
        selected_index = max(0, min(event.index - 1, len(self._command_suggestions) - 1))
        self._command_index = selected_index
        self._apply_selected_command_completion()
        chat_input = self.query_one("#chat-input", ChatInput)
        self._refresh_command_menu(chat_input.value)
        chat_input.focus()
        event.stop()

    @on(TextArea.Changed, "#chat-input")
    def on_input_changed(self, event: TextArea.Changed) -> None:
        """输入内容变化时刷新命令建议菜单（仅当输入以 / 开头时触发建议）。"""
        self._refresh_command_menu(event.text_area.text)

    @on(ChatInput.Submitted, "#chat-input")
    async def on_input_submitted(self, event: ChatInput.Submitted) -> None:
        """
        处理输入框提交事件（按 Enter）。

        [Workflow]
        1. 去除首尾空格，若为空直接返回
        2. 若以 / 开头，尝试作为命令处理：
           - "completed"：补全命令名，刷新菜单后返回
           - "executed"：清空输入框和菜单后返回
        3. 否则清空输入框并提交 prompt 给 bridge
        """
        raw_value = event.value
        value = raw_value.strip()
        if not value:
            return
        command_value = raw_value.lstrip()
        if command_value.startswith("/"):
            outcome = await self._handle_command_submit(command_value)
            if outcome == "completed":
                self._refresh_command_menu(event.input.value)
                return
            if outcome == "executed":
                event.input.value = ""
                self._refresh_command_menu("")
                return
        event.input.value = ""
        self._refresh_command_menu("")
        await self._submit_prompt(value)

    async def _submit_prompt(self, prompt: str) -> None:
        """
        提交 prompt 给 bridge，以 Worker 方式异步执行（不阻塞 UI）。

        参数:
            prompt: 用户输入的消息文本，如 "帮我写一个快速排序"
        """
        self.run_worker(self.bridge.submit_prompt(prompt), name="submit-prompt", exclusive=False)

    def _refresh_command_menu(self, raw_value: str) -> None:
        """
        根据输入内容刷新命令建议菜单。

        [Workflow]
        1. 若输入不以 / 开头：清空建议列表和菜单，刷新输入面板状态后返回
        2. 调用 _build_slash_suggestions 构建建议列表
        3. 若 _command_index 超出范围，重置为 0
        4. 将建议列表转换为 CommandSuggestionView 列表（含 selected 标记）
        5. 提取当前选中建议的 hint 文本
        6. 更新 CommandMenuWidget 显示

        参数:
            raw_value: 输入框当前文本，如 "/ses"、"/sessions abc"
        """
        menu = self.query_one(CommandMenuWidget)
        if not raw_value.startswith("/"):
            self._command_suggestions = []
            self._command_index = 0
            self._command_hint = ""
            menu.update_suggestions([])
            self._refresh_input_panel_state()
            return
        self._command_suggestions = self._build_slash_suggestions(raw_value)
        if self._command_index >= len(self._command_suggestions):
            self._command_index = 0

        display: List[CommandSuggestionView] = []
        for offset, suggestion in enumerate(self._command_suggestions, 1):
            display.append(
                CommandSuggestionView(
                    index=offset,
                    name=suggestion.name,
                    description=suggestion.description,
                    hint=suggestion.hint,
                    selected=offset == self._command_index + 1,
                )
            )
        hint_text = ""
        if self._command_suggestions:
            selected = self._command_suggestions[self._command_index]
            hint_text = selected.hint
            self._command_hint = f"提示：{hint_text}" if hint_text else "Tab 补全"
        else:
            self._command_hint = ""
        menu.update_suggestions(display, hint=hint_text)
        self._refresh_input_panel_state()

    @staticmethod
    def _command_hint_text(command: Command) -> str:
        """
        获取命令的提示文本（参数占位符或 argument_hint）。

        返回:
            str: 提示文本，如 "<session-id>" 或 "reload"，无提示时返回 ""
        """
        if command.argument_spec is not None and command.argument_spec.placeholder:
            return command.argument_spec.placeholder
        return command.argument_hint or ""

    @staticmethod
    def _command_usage_text(command: Command) -> str:
        """
        获取命令的完整用法文本（命令名 + 参数提示）。

        返回:
            str: 用法文本，如 "/sessions <session-id>" 或 "/clear"
        """
        hint = TextualChatApp._command_hint_text(command)
        return f"/{command.name} {hint}".rstrip()

    def _build_commands(self) -> List[Command]:
        """
        构建可用命令列表（内置命令 + 当前工作区的 skill 命令）。

        返回:
            List[Command]: 命令列表，例如：
                [Command(name="help", ...), Command(name="clear", ...),
                 Command(name="my-skill", type=CommandType.PROMPT, ...)]
        """
        cwd = str(getattr(self.bridge.engine, "cwd", "."))
        commands = list(get_enabled_commands())
        commands.extend(build_skill_commands(cwd))
        return commands

    def _refresh_available_commands(self) -> None:
        """重新扫描并刷新可用命令列表（内置 + skill），更新 _commands 缓存。"""
        self._commands = self._build_commands()

    @staticmethod
    def _parse_slash_input(raw_value: str) -> tuple[str, str, bool]:
        """
        解析斜杠命令输入，拆分为命令名、参数和是否有分隔符。

        参数:
            raw_value: 原始输入字符串，如 "/sessions abc123" 或 "/clear"

        返回:
            tuple[str, str, bool]: (命令名, 参数字符串, 是否有空格分隔符)
            例：("/sessions abc123") → ("sessions", "abc123", True)
            例：("/clear") → ("clear", "", False)
        """
        body = raw_value[1:] if raw_value.startswith("/") else raw_value
        name, separator, remainder = body.partition(" ")
        return name.strip(), remainder, bool(separator)

    def _resolve_exact_command(self, token: str) -> Optional[Command]:
        """
        按名称精确查找命令（支持别名）。

        参数:
            token: 命令名称字符串，如 "sessions"、"help"

        返回:
            Optional[Command]: 找到的命令对象，未找到时返回 None
        """
        if not token:
            return None
        return find_command(token, self._commands)

    def _match_argument_suggestions(self, command: Command, query: str) -> List[SlashSuggestion]:
        """
        匹配命令参数建议列表。

        [Workflow]
        1. sessions 命令：委托给 _build_session_argument_suggestions
        2. 其他命令：检查 argument_spec.kind == "select" 且有 options
        3. 遍历 options，按前缀匹配（score=1.0）和模糊匹配（score>=0.35）分组
        4. 前缀匹配优先，模糊匹配按分数降序排列
        5. 去重后返回

        参数:
            command: 已精确匹配的命令对象
            query: 用户输入的参数片段，如 "py" 匹配 "python"

        返回:
            List[SlashSuggestion]: 参数建议列表，每项包含 completion、name、description 等
                例：[SlashSuggestion(kind="argument", name="Python",
                                     completion="/model claude-opus-4", ...)]
        """
        if command.name == "sessions":
            return self._build_session_argument_suggestions(query)

        spec = command.argument_spec
        if spec is None or spec.kind != "select" or not spec.options:
            return []

        lower = query.strip().lower()
        prefix: List[tuple[float, SlashSuggestion]] = []
        fuzzy: List[tuple[float, SlashSuggestion]] = []

        for option in spec.options:
            option_value = option.value.strip()
            option_label = option.label.strip() or option_value
            option_names = [option_value.lower(), option_label.lower()]
            suggestion = SlashSuggestion(
                kind="argument",
                command=command,
                completion=f"/{command.name} {option_value}".rstrip(),
                name=option_label,
                description=option.description,
                hint=spec.placeholder,
            )

            if not lower:
                prefix.append((0.0, suggestion))
                continue

            if any(name.startswith(lower) for name in option_names):
                exact_bonus = 0.1 if any(name == lower for name in option_names) else 0.0
                prefix.append((1.0 + exact_bonus, suggestion))
                continue

            score = max(SequenceMatcher(None, lower, name).ratio() for name in option_names)
            if score >= 0.35:
                fuzzy.append((score, suggestion))

        prefix.sort(key=lambda item: item[0], reverse=True)
        ordered: List[SlashSuggestion] = [suggestion for _, suggestion in prefix]
        fuzzy.sort(key=lambda item: item[0], reverse=True)
        ordered.extend([suggestion for _, suggestion in fuzzy])

        deduped: List[SlashSuggestion] = []
        seen = set()
        for suggestion in ordered:
            key = suggestion.completion
            if key in seen:
                continue
            seen.add(key)
            deduped.append(suggestion)
        return deduped

    @staticmethod
    def _session_title(info: Dict[str, Any]) -> str:
        """
        从会话信息字典中提取标题（优先 user_title，其次 ai_title，再次 first_prompt）。

        参数:
            info: 会话信息字典，例：{"user_title": "排序算法", "session_id": "abc123", ...}

        返回:
            str: 会话标题，如 "排序算法"；无标题时返回 "未命名会话"
        """
        title = (
            str(info.get("user_title", "") or "").strip()
            or str(info.get("ai_title", "") or "").strip()
            or str(info.get("first_prompt", "") or "").strip()
        )
        return title or "未命名会话"

    @staticmethod
    def _session_modified_text(info: Dict[str, Any]) -> str:
        """
        格式化会话最后修改时间为可读字符串。

        参数:
            info: 会话信息字典，例：{"modified": "2024-01-15T10:30:00", ...}

        返回:
            str: 格式化时间，如 "2024-01-15 10:30"；无时间时返回 "时间未知"
        """
        modified = str(info.get("modified", "") or "").strip()
        if not modified:
            return "时间未知"
        return modified[:16].replace("T", " ")

    @staticmethod
    def _sidebar_mode_text(snapshot: UISnapshot) -> str:
        """
        将侧边栏模式转换为可读文本。

        参数:
            snapshot: 当前 UISnapshot，包含 sidebar_mode 和 active_entity_label

        返回:
            str: 可读模式文本，如 "自动跟随"、"当前会话"、"协作成员 · Agent-1"
        """
        mode = str(snapshot.sidebar_mode or "").strip()
        if mode == "auto":
            return "自动跟随"
        if mode == "global":
            return "当前会话"
        if mode.startswith("agent:"):
            label = str(snapshot.active_entity_label or "").strip() or mode.split(":", 1)[1]
            return f"协作成员 · {label}"
        return mode or "当前会话"

    def _build_session_argument_suggestions(self, query: str) -> List[SlashSuggestion]:
        """
        构建 /sessions 命令的参数建议列表（基于当前工作区历史会话）。

        [Workflow]
        1. 获取最多 50 个历史会话
        2. 对每个会话构建 SlashSuggestion（completion="/sessions <id>"）
        3. 按精确匹配（score=2.0）、前缀匹配（score=1.0）、模糊匹配（score>=0.35）分组
        4. 按分数降序排列后返回

        参数:
            query: 用户输入的搜索词，如 "abc" 或 "排序"

        返回:
            List[SlashSuggestion]: 会话建议列表，例：
                [SlashSuggestion(kind="argument", name="排序算法",
                                 completion="/sessions abc123",
                                 hint="5 条消息 · 2024-01-15 10:30")]
        """
        sessions = self._list_runtime_sessions()
        lower = query.strip().lower()
        prefix: List[tuple[float, SlashSuggestion]] = []
        fuzzy: List[tuple[float, SlashSuggestion]] = []

        for info in sessions[:50]:
            session_id = str(info.get("session_id", "") or "").strip()
            if not session_id:
                continue
            title = self._session_title(info)
            preview = str(info.get("first_prompt", "") or "").strip()
            message_count = int(info.get("message_count") or 0)
            modified = self._session_modified_text(info)
            searchable = [session_id.lower(), title.lower(), preview.lower()]
            preview_text = preview or "无首条消息摘要"
            suggestion = SlashSuggestion(
                kind="argument",
                command=self._resolve_exact_command("sessions") or self._commands[0],
                completion=f"/sessions {session_id}",
                name=title,
                description=f"{session_id} · {preview_text}",
                hint=f"{message_count} 条消息 · {modified}",
            )

            if not lower:
                prefix.append((0.0, suggestion))
                continue

            if any(name == lower for name in searchable):
                prefix.append((2.0, suggestion))
                continue

            if any(name.startswith(lower) for name in searchable):
                prefix.append((1.0, suggestion))
                continue

            score = max(SequenceMatcher(None, lower, name).ratio() for name in searchable if name)
            if score >= 0.35:
                fuzzy.append((score, suggestion))

        prefix.sort(key=lambda item: item[0], reverse=True)
        fuzzy.sort(key=lambda item: item[0], reverse=True)
        return [suggestion for _, suggestion in prefix] + [suggestion for _, suggestion in fuzzy]

    def _build_slash_suggestions(self, raw_value: str) -> List[SlashSuggestion]:
        """
        构建斜杠命令建议列表。

        [Workflow]
        1. 解析输入为 (命令名, 参数片段, 是否有分隔符)
        2. 若已精确匹配命令且有分隔符：尝试构建参数建议
        3. 若参数建议为空且命令有 select 类型参数：返回空列表（等待输入）
        4. 否则：对命令名进行模糊匹配，返回命令级建议

        参数:
            raw_value: 输入框当前文本，如 "/ses"、"/sessions "、"/sessions abc"

        返回:
            List[SlashSuggestion]: 建议列表，kind 为 "command" 或 "argument"
        """
        command_token, args_fragment, has_separator = self._parse_slash_input(raw_value)
        command = self._resolve_exact_command(command_token)
        if command is not None and has_separator:
            argument_suggestions = self._match_argument_suggestions(command, args_fragment)
            if argument_suggestions or command.name == "sessions":
                return argument_suggestions
            if command.argument_spec is not None and command.argument_spec.kind == "select":
                return []
        return [
            SlashSuggestion(
                kind="command",
                command=item,
                completion=f"/{item.name} ",
                name=f"/{item.name}",
                description=item.description,
                hint=self._command_hint_text(item),
            )
            for item in self._match_commands(command_token)
        ]

    def _refresh_input_panel_state(self) -> None:
        """
        刷新输入面板状态（只读、placeholder、命令提示、重试按钮等）。

        从 _last_snapshot 读取当前状态，调用 InputPanel.update_state() 更新显示。
        """
        snapshot = self._last_snapshot
        interaction = snapshot.interaction
        input_widget = self.query_one("#chat-input", ChatInput)
        self.query_one(InputPanel).update_state(
            is_generating=snapshot.is_generating,
            has_interaction=interaction is not None,
            interaction_label=interaction.label if interaction is not None else "",
            command_hint=self._command_hint,
            can_retry=bool(snapshot.last_retry_prompt and not snapshot.is_generating and interaction is None),
            is_typing=bool(input_widget.value.strip()),
            pulse_step=self._pulse_step,
        )

    def _match_commands(self, query: str) -> List[Command]:
        """
        模糊匹配命令列表。

        [Workflow]
        1. 若 query 为空，返回全部命令
        2. 前缀匹配（命令名或别名以 query 开头）
        3. 首字母缩写匹配（如 "mc" 匹配 "mcp-connect"）
        4. 模糊匹配（SequenceMatcher ratio >= 0.35）
        5. 去重后按 前缀 > 缩写 > 模糊 顺序返回

        参数:
            query: 命令名搜索词，如 "ses"、"mc"

        返回:
            List[Command]: 匹配的命令列表，例：[Command(name="sessions", ...)]
        """
        commands = list(self._commands)
        if not query:
            return commands

        lower = query.lower()
        prefix: List[Command] = []
        acronym: List[Command] = []
        fuzzy: List[tuple[float, Command]] = []

        for command in commands:
            names = [command.name, *command.aliases]
            if any(name.startswith(lower) for name in names):
                prefix.append(command)
                continue

            abbreviation = "".join(part[0] for part in command.name.split("-") if part)
            if lower == abbreviation:
                acronym.append(command)
                continue

            score = max(SequenceMatcher(None, lower, name).ratio() for name in names)
            if score >= 0.35:
                fuzzy.append((score, command))

        fuzzy.sort(key=lambda item: item[0], reverse=True)
        ordered = prefix + acronym + [command for _, command in fuzzy]

        deduped: List[Command] = []
        seen = set()
        for command in ordered:
            if command.name in seen:
                continue
            seen.add(command.name)
            deduped.append(command)
        return deduped

    async def _handle_command_submit(self, raw_value: str) -> Literal["completed", "executed"]:
        """
        处理命令提交：决定是补全命令名/参数还是直接执行命令。

        [Workflow]
        1. 解析输入为 (命令名, 参数, 是否有分隔符)
        2. 若命令未找到但有建议：应用补全，返回 "completed"
        3. 若命令未找到且无建议：显示警告 Toast，返回 "executed"
        4. 若有建议且当前选中项需要补全（参数建议/命令名不完整）：应用补全，返回 "completed"
        5. 否则执行命令，返回 "executed"

        参数:
            raw_value: 输入框原始文本，如 "/ses"、"/sessions abc123"

        返回:
            Literal["completed", "executed"]:
                "completed" 表示仅补全了输入，"executed" 表示命令已执行
        """
        name, raw_args, has_separator = self._parse_slash_input(raw_value)
        args = raw_args.strip()
        command = self._resolve_exact_command(name)

        if command is None and self._command_suggestions:
            self._apply_selected_command_completion()
            return "completed"

        if command is None:
            self.bridge.add_toast(f"未知命令：/{name}", level="warning")
            return "executed"

        if self._command_suggestions:
            selected = self._command_suggestions[self._command_index]
            should_complete = False
            if selected.kind == "argument":
                should_complete = raw_value != selected.completion
            elif command.name != name:
                should_complete = True
            elif command.argument_spec is not None and not has_separator and raw_value != selected.completion:
                should_complete = True

            if should_complete:
                self.query_one("#chat-input", ChatInput).value = selected.completion
                return "completed"

        await self._execute_command(command, args)
        return "executed"

    @staticmethod
    def _split_args(args: str) -> List[str]:
        """
        使用 shlex 分割命令参数字符串，支持引号包裹的参数。

        参数:
            args: 参数字符串，如 'file.py "my dir/file"'

        返回:
            List[str]: 分割后的参数列表，例：["file.py", "my dir/file"]
            shlex 解析失败时返回 [args.strip()]
        """
        if not args.strip():
            return []
        try:
            return shlex.split(args)
        except ValueError:
            return [args.strip()]

    @staticmethod
    def _format_json(data: Any) -> str:
        """
        将数据格式化为缩进 JSON 字符串（中文不转义，非序列化类型转为字符串）。

        参数:
            data: 任意可序列化数据，如 dict、list、str

        返回:
            str: 格式化后的 JSON 字符串，例：'{\n  "key": "值"\n}'
        """
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)

    def _resolve_path_arg(self, raw_path: str) -> Path:
        """
        解析路径参数，将相对路径转换为基于引擎 cwd 的绝对路径。

        参数:
            raw_path: 用户输入的路径字符串，如 "output.md" 或 "/tmp/export.json"

        返回:
            Path: 解析后的绝对路径，例：Path("/home/user/project/output.md")
        """
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = Path(str(getattr(self.bridge.engine, "cwd", "."))) / candidate
        return candidate

    def _engine_messages_for_export(self) -> List[Dict[str, Any]]:
        """
        获取用于导出的消息列表。

        [Workflow]
        1. 优先从 engine.messages 获取原始消息列表
        2. 若 engine.messages 为空，从 UISnapshot.messages 中提取 user/assistant 角色消息

        返回:
            List[Dict[str, Any]]: 消息列表，例：
                [{"role": "user", "content": "帮我写排序"},
                 {"role": "assistant", "content": "好的，以下是..."}]
        """
        raw_messages = list(getattr(self.bridge.engine, "messages", []) or [])
        if raw_messages:
            return [dict(message) if isinstance(message, dict) else message for message in raw_messages]

        messages: List[Dict[str, Any]] = []
        for snapshot in self.bridge.get_snapshot().messages:
            if snapshot.role not in ("user", "assistant"):
                continue
            messages.append({"role": snapshot.role, "content": snapshot.content})
        return messages

    def _list_runtime_sessions(self) -> List[Dict[str, Any]]:
        """
        列出当前工作区的所有运行时会话（按修改时间降序）。

        [Workflow]
        1. 获取 sessions 目录路径
        2. 遍历所有 .jsonl 文件（排除 .events.jsonl）
        3. 通过 RuntimeSessionStorage 读取每个会话的 info
        4. 按 modified 字段降序排列

        返回:
            List[Dict[str, Any]]: 会话信息列表，例：
                [{"session_id": "abc123", "user_title": "排序算法",
                  "message_count": 5, "modified": "2024-01-15T10:30:00", ...}]
        """
        cwd = str(getattr(self.bridge.engine, "cwd", "."))
        sessions_dir = get_runtime_sessions_dir(cwd)
        directory = Path(sessions_dir)
        if not directory.exists():
            return []

        sessions: List[Dict[str, Any]] = []
        for session_file in directory.glob("*.jsonl"):
            if session_file.name.endswith(".events.jsonl"):
                continue
            session_id = session_file.stem
            storage = RuntimeSessionStorage(session_id, cwd)
            info = storage.get_session_info()
            if info.get("exists"):
                sessions.append(info)

        sessions.sort(key=lambda item: item.get("modified") or "", reverse=True)
        return sessions

    @staticmethod
    def _session_artifact_paths(session_id: str, cwd: str) -> List[Path]:
        """
        获取会话相关的所有文件路径列表（主文件、事件日志、快照）。

        参数:
            session_id: 会话 ID，如 "abc123"
            cwd: 工作区目录路径，如 "/home/user/project"

        返回:
            List[Path]: 三个文件路径，例：
                [Path("~/.codo/sessions/abc123.jsonl"),
                 Path("~/.codo/sessions/abc123.events.jsonl"),
                 Path("~/.codo/sessions/abc123.snapshot.json")]
        """
        return [
            get_session_file_path(session_id, cwd),
            get_session_event_log_path(session_id, cwd),
            get_session_snapshot_path(session_id, cwd),
        ]

    def _current_session_is_blank(self) -> bool:
        """
        判断当前会话是否为空白（无消息、无标题、无首条 prompt）。

        [Workflow]
        1. 检查 engine.messages 是否为空
        2. 检查 session_storage.get_session_info() 中的 message_count、标题、first_prompt

        返回:
            bool: True 表示当前会话为空白，可安全清理
        """
        engine = self.bridge.engine
        if list(getattr(engine, "messages", []) or []):
            return False

        session_storage = getattr(engine, "session_storage", None)
        if session_storage is None:
            return True

        get_info = getattr(session_storage, "get_session_info", None)
        if not callable(get_info):
            return True

        try:
            info = dict(get_info() or {})
        except Exception:
            return False

        if int(info.get("message_count") or 0) > 0:
            return False
        if str(info.get("user_title", "") or "").strip():
            return False
        if str(info.get("ai_title", "") or "").strip():
            return False
        if str(info.get("first_prompt", "") or "").strip():
            return False
        return True

    def _remove_session_artifacts(self, session_id: str) -> bool:
        """
        删除指定会话的所有相关文件（主文件、事件日志、快照）。

        参数:
            session_id: 要删除的会话 ID，如 "abc123"

        返回:
            bool: True 表示至少删除了一个文件，False 表示无文件被删除
        """
        cwd = str(getattr(self.bridge.engine, "cwd", "."))
        removed = False
        for path in self._session_artifact_paths(session_id, cwd):
            try:
                if path.exists():
                    path.unlink()
                    removed = True
            except Exception:
                continue
        return removed

    async def _switch_runtime_session(self, session_id: str) -> bool:
        """
        切换到指定会话。

        [Workflow]
        1. 若目标会话即为当前会话，提示并返回 True
        2. 查找目标会话信息，不存在则提示并返回 False
        3. 更新 engine.session_id 和 session_storage
        4. 调用 engine.restore_session() 或直接加载消息
        5. 通知 bridge 重新加载，显示成功 Toast
        6. 若当前会话为空白，清理其文件

        参数:
            session_id: 目标会话 ID，如 "abc123"

        返回:
            bool: True 表示切换成功，False 表示目标会话不存在
        """
        engine = self.bridge.engine
        current_session_id = str(getattr(engine, "session_id", "") or "")
        cwd = str(getattr(engine, "cwd", "."))
        if session_id == current_session_id:
            self.bridge.add_toast("已经在当前会话中。", level="info")
            return True

        session_info = next(
            (item for item in self._list_runtime_sessions() if str(item.get("session_id", "")) == session_id),
            None,
        )
        if session_info is None:
            self.bridge.add_toast(f"未找到会话：{session_id}", level="warning")
            return False

        should_cleanup_empty_current = self._current_session_is_blank()

        engine.session_id = session_id
        engine.session_storage = RuntimeSessionStorage(session_id, cwd)
        if isinstance(getattr(engine, "execution_context", None), dict):
            engine.execution_context["session_id"] = session_id

        restored = False
        restore_session = getattr(engine, "restore_session", None)
        if callable(restore_session):
            restored = bool(restore_session())
        else:
            try:
                engine.messages = engine.session_storage.load_messages()
                restored = bool(engine.messages)
            except Exception:
                restored = False

        self.bridge.reload_from_engine()
        self._last_snapshot = self.bridge.get_snapshot()

        title = self._session_title(session_info)
        self.bridge.add_toast(f"已恢复会话：{title}（{session_id}）", level="info")

        if should_cleanup_empty_current and current_session_id:
            if self._remove_session_artifacts(current_session_id):
                self.bridge.add_toast(f"已清理空白会话：{current_session_id}", level="info")

        self._last_snapshot = self.bridge.get_snapshot()
        self.bridge.notify()
        return restored or True

    def _resolve_memory_file(self, raw_name: str) -> Optional[Path]:
        """
        解析记忆文件路径（支持绝对路径、相对于 memory_dir 的路径和文件名匹配）。

        [Workflow]
        1. 若 raw_name 是绝对路径且存在，直接返回
        2. 尝试 memory_dir / raw_name
        3. 扫描 memory_dir 中所有文件，按 filename、path.name、path.stem 匹配

        参数:
            raw_name: 用户输入的文件名或路径，如 "project.md" 或 "/abs/path/file.md"

        返回:
            Optional[Path]: 找到的文件路径，未找到时返回 None
        """
        cwd = str(getattr(self.bridge.engine, "cwd", "."))
        memory_dir = get_project_memory_dir(cwd)
        direct = Path(raw_name)
        if direct.is_absolute() and direct.exists():
            return direct

        candidate = memory_dir / raw_name
        if candidate.exists():
            return candidate

        for header in scan_memory_files(str(memory_dir)):
            header_path = Path(header.filepath)
            if raw_name in {header.filename, header_path.name, header_path.stem}:
                return header_path

        return None

    async def _execute_sessions_command(self, args: str = "") -> None:
        """
        执行 /sessions 命令：列出或切换历史会话。

        [Workflow]
        1. 获取历史会话列表，若为空则提示
        2. 若有 args：尝试精确 ID 匹配 → 单一模糊匹配 → 多匹配时缩小范围
        3. 无 args：列出最多 20 个会话（当前会话标记 *）

        参数:
            args: 可选的会话 ID 或搜索词，如 "abc123" 或 "排序"
        """
        sessions = self._list_runtime_sessions()
        if not sessions:
            self.bridge.add_info_message("当前工作区还没有历史会话。", level="info")
            return

        query = (args or "").strip()
        current_session_id = str(getattr(self.bridge.engine, "session_id", "") or "")

        if query:
            exact_id = next(
                (item for item in sessions if str(item.get("session_id", "") or "").strip().lower() == query.lower()),
                None,
            )
            if exact_id is not None:
                await self._switch_runtime_session(str(exact_id.get("session_id", "")))
                return

            matches = [
                item
                for item in sessions
                if query.lower() in str(item.get("session_id", "") or "").lower()
                or query.lower() in self._session_title(item).lower()
                or query.lower() in str(item.get("first_prompt", "") or "").lower()
            ]
            if len(matches) == 1:
                await self._switch_runtime_session(str(matches[0].get("session_id", "")))
                return
            if not matches:
                self.bridge.add_toast(f"没有找到匹配的会话：{query}", level="warning")
                return
            sessions = matches

        lines = [
            "当前工作区历史会话",
            f"工作区：{getattr(self.bridge.engine, 'cwd', '.')}",
        ]
        for info in sessions[:20]:
            marker = "*" if info.get("session_id") == current_session_id else " "
            title = self._session_title(info)
            modified = self._session_modified_text(info)
            message_count = int(info.get("message_count") or 0)
            lines.append(f"{marker} {info.get('session_id')}  {title}  （{message_count} 条消息，{modified}）")

        if query and len(sessions) > 1:
            lines.append("")
            lines.append("匹配到多个会话，请继续输入更精确的 ID 或标题。")

        self.bridge.add_info_message("\n".join(lines), level="info")

    async def _execute_memory_command(self, args: str) -> None:
        """
        执行 /memory 命令：管理记忆文件（list/view/delete/index）。

        [Workflow]
        1. 无参数：显示用法提示
        2. list：列出所有记忆文件（类型、文件名、日期、描述）
        3. index：显示记忆索引内容
        4. view <file>：显示指定记忆文件的内容
        5. delete <file>：删除指定记忆文件

        参数:
            args: 子命令和参数，如 "list"、"view project.md"、"delete old.md"
        """
        cwd = str(getattr(self.bridge.engine, "cwd", "."))
        memory_dir = ensure_memory_dir(cwd)
        manager = MemoryManager(str(memory_dir))
        argv = self._split_args(args)
        if not argv:
            self.bridge.add_info_message(
                "/memory list | view <file> | delete <file> | index",
                level="info",
            )
            return

        subcommand = argv[0].lower()
        if subcommand == "list":
            headers = scan_memory_files(str(memory_dir))
            if not headers:
                self.bridge.add_info_message("当前没有记忆文件。", level="info")
                return
            lines = ["记忆文件"]
            for header in headers[:50]:
                stamp = datetime.fromtimestamp(header.mtime).strftime("%Y-%m-%d")
                type_label = f"[{header.memory_type}]" if header.memory_type else "[未知]"
                description = f" - {header.description}" if header.description else ""
                lines.append(f"- {type_label} {header.filename} ({stamp}){description}")
            self.bridge.add_info_message("\n".join(lines), level="info")
            return

        if subcommand == "index":
            index_body = load_memory_index(cwd)
            if not index_body:
                headers = scan_memory_files(str(memory_dir))
                index_body = format_memory_manifest(headers) or "当前没有可用的记忆索引。"
            self.bridge.add_info_message(index_body, level="info")
            return

        if len(argv) < 2:
            self.bridge.add_toast(f"/memory {subcommand} 需要提供文件名", level="warning")
            return

        target = self._resolve_memory_file(argv[1])
        if target is None:
            self.bridge.add_toast(f"未找到记忆文件：{argv[1]}", level="warning")
            return

        if subcommand == "view":
            memory = manager.read_memory(str(target))
            if memory is None:
                self.bridge.add_toast(f"无法读取记忆文件：{argv[1]}", level="warning")
                return
            lines = [
                f"{memory.frontmatter.name} [{memory.frontmatter.type}]",
                memory.frontmatter.description,
                "",
                memory.content,
            ]
            self.bridge.add_info_message("\n".join(lines).strip(), level="info")
            return

        if subcommand == "delete":
            if not manager.delete_memory(str(target)):
                self.bridge.add_toast(f"删除记忆失败：{argv[1]}", level="warning")
                return
            self.bridge.add_toast(f"已删除记忆：{target.name}", level="info")
            return

        self.bridge.add_toast(f"未知 /memory 子命令：{subcommand}", level="warning")

    @staticmethod
    def _connection_map(connections: Any) -> Dict[str, Any]:
        """
        将 MCP 连接列表或字典转换为以服务器名称为键的映射字典。

        参数:
            connections: 连接列表或字典，每个连接对象有 name 或 server_name 属性

        返回:
            Dict[str, Any]: 名称到连接对象的映射，例：
                {"my-server": <MCPConnection name="my-server" connected=True>}
        """
        if isinstance(connections, dict):
            iterable = connections.values()
        else:
            iterable = connections or []
        mapped: Dict[str, Any] = {}
        for connection in iterable:
            name = getattr(connection, "name", None) or getattr(connection, "server_name", None)
            if name:
                mapped[str(name)] = connection
        return mapped

    async def _execute_mcp_list_command(self) -> None:
        """
        执行 /mcp-list 命令：列出所有已配置的 MCP 服务器及其连接状态。

        输出格式示例：
            已配置的 MCP 服务器
            - my-server [stdio] 已连接 :: python server.py
            - other-server [sse] 未连接 :: http://localhost:8080
        """
        servers = dict(getattr(self.bridge.engine.mcp_config_manager, "list_servers")() or {})
        if not servers:
            self.bridge.add_info_message("当前没有配置 MCP 服务器。", level="info")
            return

        connections = self._connection_map(self.bridge.engine.mcp_client_manager.list_connections())
        lines = ["已配置的 MCP 服务器"]
        for name, config in servers.items():
            connection = connections.get(name)
            if connection and getattr(connection, "connected", False):
                status = "已连接"
            elif connection and getattr(connection, "error", None):
                status = f"错误：{getattr(connection, 'error')}"
            else:
                status = "未连接"
            args = " ".join(getattr(config, "args", []) or [])
            command = getattr(config, "command", "")
            transport = getattr(config, "transport", "stdio")
            lines.append(f"- {name} [{transport}] {status} :: {command} {args}".rstrip())

        self.bridge.add_info_message("\n".join(lines), level="info")

    async def _execute_mcp_connect_command(self, args: str) -> None:
        """
        执行 /mcp-connect 命令：连接指定 MCP 服务器并刷新工具列表。

        参数:
            args: 服务器名称，如 "my-server"
        """
        argv = self._split_args(args)
        if not argv:
            self.bridge.add_toast("/mcp-connect 需要提供服务器名称", level="warning")
            return
        server_name = argv[0]
        connected = await self.bridge.engine.mcp_client_manager.connect(server_name)
        if not connected:
            self.bridge.add_toast(f"连接 MCP 服务器失败：{server_name}", level="warning")
            return
        tool_count = await self.bridge.engine.refresh_mcp_tools()
        self.bridge.add_toast(
            f"已连接 MCP 服务器：{server_name}（载入 {tool_count} 个工具）",
            level="info",
        )

    async def _execute_mcp_disconnect_command(self, args: str) -> None:
        """
        执行 /mcp-disconnect 命令：断开指定 MCP 服务器连接并刷新工具列表。

        参数:
            args: 服务器名称，如 "my-server"
        """
        argv = self._split_args(args)
        if not argv:
            self.bridge.add_toast("/mcp-disconnect 需要提供服务器名称", level="warning")
            return
        server_name = argv[0]
        await self.bridge.engine.mcp_client_manager.disconnect(server_name)
        await self.bridge.engine.refresh_mcp_tools()
        self.bridge.add_toast(f"已断开 MCP 服务器：{server_name}", level="info")

    async def _execute_mcp_tools_command(self, args: str) -> None:
        """
        执行 /mcp-tools 命令：列出指定或所有已连接 MCP 服务器的工具。

        参数:
            args: 可选的服务器名称，如 "my-server"；为空时列出所有已连接服务器的工具
        """
        argv = self._split_args(args)
        connection_map = self._connection_map(self.bridge.engine.mcp_client_manager.list_connections())
        server_names = [argv[0]] if argv else list(connection_map.keys())
        if not server_names:
            self.bridge.add_info_message("当前没有已连接的 MCP 服务器。", level="info")
            return

        lines = ["MCP 工具"]
        for server_name in server_names:
            tools = await self.bridge.engine.mcp_client_manager.list_tools(server_name)
            if not tools:
                lines.append(f"- {server_name}: 无工具")
                continue
            lines.append(f"- {server_name}")
            for tool in tools:
                description = f" - {tool.description}" if getattr(tool, "description", None) else ""
                lines.append(f"  {tool.name}{description}")

        self.bridge.add_info_message("\n".join(lines), level="info")

    async def _execute_mcp_resources_command(self, args: str) -> None:
        """
        执行 /mcp-resources 命令：列出指定或所有已连接 MCP 服务器的资源。

        参数:
            args: 可选的服务器名称，如 "my-server"；为空时列出所有已连接服务器的资源
        """
        argv = self._split_args(args)
        connection_map = self._connection_map(self.bridge.engine.mcp_client_manager.list_connections())
        server_names = [argv[0]] if argv else list(connection_map.keys())
        if not server_names:
            self.bridge.add_info_message("当前没有已连接的 MCP 服务器。", level="info")
            return

        lines = ["MCP 资源"]
        for server_name in server_names:
            resources = await self.bridge.engine.mcp_client_manager.list_resources(server_name)
            if not resources:
                lines.append(f"- {server_name}: 无资源")
                continue
            lines.append(f"- {server_name}")
            for resource in resources:
                description = f" - {resource.description}" if getattr(resource, "description", None) else ""
                lines.append(f"  {resource.name} ({resource.uri}){description}")

        self.bridge.add_info_message("\n".join(lines), level="info")

    async def _execute_export_command(self, args: str) -> None:
        """
        执行 /export 命令：将当前会话消息导出为文件。

        [Workflow]
        1. 获取用于导出的消息列表
        2. 若有路径参数，解析为绝对路径；否则自动生成默认文件名
        3. 根据文件后缀确定导出格式（md/json/txt）
        4. 调用 export_session 写入文件并显示成功 Toast

        参数:
            args: 可选的输出路径，如 "output.md" 或 "/tmp/session.json"
        """
        messages = self._engine_messages_for_export()
        argv = self._split_args(args)
        if argv:
            output_path = self._resolve_path_arg(argv[0])
        else:
            output_path = Path(str(getattr(self.bridge.engine, "cwd", "."))) / generate_default_filename(messages, ".md")

        suffix = output_path.suffix.lower().lstrip(".")
        export_format = suffix if suffix in {"txt", "md", "json"} else "txt"
        if not output_path.suffix:
            output_path = output_path.with_suffix(".txt")
        export_session(messages, str(output_path), format=export_format)
        self.bridge.add_toast(f"已导出会话到 {output_path}", level="info")

    async def _execute_diff_command(self) -> None:
        """
        执行 /diff 命令：显示当前 Git 仓库的工作区和暂存区变更。

        [Workflow]
        1. 检查当前目录是否为 Git 仓库
        2. 获取 git status --short 输出
        3. 获取 git diff（工作区未暂存变更）
        4. 获取 git diff --cached（已暂存变更）
        5. 拼接并显示所有变更信息
        """
        cwd = str(getattr(self.bridge.engine, "cwd", "."))
        probe = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if probe.returncode != 0:
            self.bridge.add_info_message(f"当前目录不是 Git 仓库：{cwd}", level="info")
            return

        status_result = subprocess.run(
            ["git", "status", "--short"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        unstaged_result = subprocess.run(
            ["git", "diff", "--no-ext-diff", "--", "."],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        staged_result = subprocess.run(
            ["git", "diff", "--no-ext-diff", "--cached", "--", "."],
            cwd=cwd,
            capture_output=True,
            text=True,
        )

        parts: List[str] = []
        status_text = (status_result.stdout or "").strip()
        if status_text:
            parts.append("状态")
            parts.append(status_text)

        staged_text = (staged_result.stdout or "").strip()
        if staged_text:
            parts.append("已暂存 Diff")
            parts.append(staged_text)

        unstaged_text = (unstaged_result.stdout or "").strip()
        if unstaged_text:
            parts.append("工作区 Diff")
            parts.append(unstaged_text)

        if not parts:
            self.bridge.add_info_message("当前没有未提交改动。", level="info")
            return

        self.bridge.add_info_message("\n\n".join(parts), level="info")

    async def _execute_config_command(self) -> None:
        """
        执行 /config 命令：显示全局配置、项目配置路径及合并后的配置内容（JSON 格式）。
        """
        cwd = str(getattr(self.bridge.engine, "cwd", "."))
        payload = {
            "global_config_path": str(get_config_file()),
            "project_config_path": str(get_project_config_file(cwd)),
            "merged": get_merged_config(cwd),
        }
        self.bridge.add_info_message(self._format_json(payload), level="info")

    async def _execute_doctor_command(self) -> None:
        """
        执行 /doctor 命令：显示当前环境的诊断信息（JSON 格式）。

        输出包含：版本、Python 版本、工作区路径、模型、会话 ID、持久化状态、
        Git 仓库状态、MCP 服务器列表、记忆文件数量、Token 用量等。
        """
        cwd = str(getattr(self.bridge.engine, "cwd", "."))
        stats = self.bridge.engine.get_context_stats()
        connections = self._connection_map(self.bridge.engine.mcp_client_manager.list_connections())
        memory_dir = get_project_memory_dir(cwd)
        memory_files = scan_memory_files(str(memory_dir))
        payload = {
            "version": __version__,
            "python": sys.version.split()[0],
            "cwd": cwd,
            "model": getattr(self.bridge.engine, "model", ""),
            "session_id": getattr(self.bridge.engine, "session_id", ""),
            "persistence_enabled": bool(getattr(self.bridge.engine, "enable_persistence", False)),
            "session_file": str(get_session_file_path(str(getattr(self.bridge.engine, "session_id", "")), cwd)),
            "session_event_log": str(get_session_event_log_path(str(getattr(self.bridge.engine, "session_id", "")), cwd)),
            "session_snapshot": str(get_session_snapshot_path(str(getattr(self.bridge.engine, "session_id", "")), cwd)),
            "git_repository": subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=cwd,
                capture_output=True,
                text=True,
            ).returncode
            == 0,
            "configured_mcp_servers": list(getattr(self.bridge.engine.mcp_config_manager, "list_servers")().keys()),
            "connected_mcp_servers": list(connections.keys()),
            "memory_dir": str(memory_dir),
            "memory_files": len(memory_files),
            "token_count": stats.get("token_count", 0),
            "remaining_tokens": stats.get("remaining_tokens", 0),
        }
        self.bridge.add_info_message(self._format_json(payload), level="info")

    async def _execute_skills_command(self, args: str) -> None:
        """
        执行 /skills 命令：列出或查看 skill 详情，支持 reload 子命令。

        [Workflow]
        1. 若 args 为 "reload"：重新扫描 skill 目录并刷新命令列表
        2. 若有 args（skill 名称）：显示该 skill 的详细信息
        3. 无 args：列出所有可用 skill

        参数:
            args: 可选的 skill 名称或 "reload"，如 "my-skill" 或 "reload"
        """
        cwd = str(getattr(self.bridge.engine, "cwd", "."))
        argv = self._split_args(args)
        if argv and argv[0].lower() == "reload":
            refresh_skills = getattr(self.bridge.engine, "refresh_skills", None)
            if callable(refresh_skills):
                refresh_skills()
            self._refresh_available_commands()
            self.bridge.add_toast("已重新扫描 skill 目录", level="info")
            return

        skills = list_skill_summaries(cwd)
        if not skills:
            self.bridge.add_info_message("当前没有可用 skill。", level="info")
            return

        if argv:
            target = argv[0].lstrip("/").lower()
            match = next((skill for skill in skills if skill.name.lower() == target), None)
            if match is None:
                self.bridge.add_toast(f"未找到 skill：{argv[0]}", level="warning")
                return

            lines = [
                f"/{match.name}",
                match.description or "无描述",
                f"路径：{match.source_path or '(内置)'}",
                f"可直接调用：{'是' if match.user_invocable else '否'}",
            ]
            if match.allowed_tools:
                lines.append(f"推荐工具：{', '.join(match.allowed_tools)}")
            if match.model:
                lines.append(f"推荐模型：{match.model}")
            self.bridge.add_info_message("\n".join(lines), level="info")
            return

        lines = ["可用 skill"]
        for skill in skills:
            description = f" - {skill.description}" if skill.description else ""
            lines.append(f"- /{skill.name}{description}")
        self.bridge.add_info_message("\n".join(lines), level="info")

    async def _enqueue_prompt_command(self, command: Command, args: str) -> bool:
        """
        将 PROMPT 类型命令生成的 prompt 加入执行队列。

        [Workflow]
        1. 调用 command.get_prompt(args, context) 生成 prompt 字符串
        2. 检查 engine.execution_context 是否支持队列注入
        3. 将 prompt 封装为队列项追加到 execution_context["queued_commands"]
        4. 调用 _submit_prompt 触发执行

        参数:
            command: PROMPT 类型的命令对象，需有 get_prompt 方法
            args: 命令参数字符串，如 "src/main.py"

        返回:
            bool: 始终返回 True（表示命令已处理）
        """
        get_prompt = command.get_prompt
        if not callable(get_prompt):
            self.bridge.add_toast(f"/{command.name} 缺少 prompt 处理器", level="warning")
            return True

        prompt = await get_prompt(
            args,
            {
                "app": self,
                "bridge": self.bridge,
                "engine": self.bridge.engine,
                "cwd": str(getattr(self.bridge.engine, "cwd", ".")),
            },
        )
        if not isinstance(prompt, str) or not prompt.strip():
            self.bridge.add_toast(f"/{command.name} 没有生成可执行内容", level="warning")
            return True

        execution_context = getattr(self.bridge.engine, "execution_context", None)
        if not isinstance(execution_context, dict):
            self.bridge.add_toast("当前会话不支持 prompt skill 注入", level="warning")
            return True

        queued = list(execution_context.get("queued_commands", []) or [])
        visible_prompt = f"/{command.name} {args}".rstrip()
        queued.append(
            {
                "prompt": prompt,
                "uuid": str(uuid4()),
                "origin": {"kind": "slash_command", "name": command.name},
                "display": visible_prompt,
                "isMeta": True,
            }
        )
        execution_context["queued_commands"] = queued
        await self._submit_prompt(visible_prompt)
        return True

    async def _execute_permissions_command(self, args: str) -> None:
        """
        执行 /permissions 命令：查看或修改权限模式。

        [Workflow]
        1. 无参数或 "show"：显示当前权限模式状态
        2. "ask" / "default"：切换为询问模式（可选 --strict）
        3. "bypass" / "bypasspermissions"：切换为直通模式（可选 confirm）
        4. 其他：显示用法提示

        参数:
            args: 子命令和选项，如 ""、"show"、"ask --strict"、"bypass confirm"
        """
        argv = [item.lower() for item in self._split_args(args)]
        state = self.bridge.get_permission_mode_state()

        if not argv or argv[0] == "show":
            lines = [
                f"权限模式：{state['display_label']}",
                "作用域：当前会话",
                f"直通模式已确认：{'是' if state['bypass_confirmed'] else '否'}",
                f"会话放行规则：{state['session_allow_rule_count']}",
            ]
            if state["session_allow_rules"]:
                lines.append(f"已放行工具：{', '.join(state['session_allow_rules'])}")
            self.bridge.add_info_message("\n".join(lines), level="info")
            return

        subcommand = argv[0]
        if subcommand in {"ask", "default"}:
            self.bridge.set_permission_mode(
                "ask",
                strict="--strict" in argv[1:],
                source="command",
            )
            return

        if subcommand in {"bypass", "bypasspermissions"}:
            self.bridge.set_permission_mode(
                "bypass",
                confirm="confirm" in argv[1:] or "--confirm" in argv[1:],
                source="command",
            )
            return

        self.bridge.add_toast(
            "用法：/permissions [show|ask [--strict]|bypass [confirm]]",
            level="warning",
        )
        self.bridge.notify()

    async def _execute_focus_command(self, args: str) -> None:
        """
        执行 /focus 命令：查看或切换侧边栏焦点视角。

        无参数时显示当前侧栏视角和自动跟随状态；
        有参数时调用 bridge.set_sidebar_focus(target) 切换到指定视角。

        参数:
            args: 目标视角标识，如 "agent:1" 或 ""（查看当前状态）
        """
        target = (args or "").strip()
        if not target:
            snapshot = self.bridge.get_snapshot()
            self.bridge.add_info_message(
                f"侧栏视角：{self._sidebar_mode_text(snapshot)}\n"
                f"自动跟随：{'开启' if snapshot.auto_follow else '关闭'}",
                level="info",
            )
            return
        self.bridge.set_sidebar_focus(target, source="command")

    async def _execute_command(self, command: Command, args: str) -> bool:
        """
        命令分发器：根据命令名将执行请求路由到对应的处理方法。

        [Workflow]
        1. PROMPT 类型命令：委托给 _enqueue_prompt_command
        2. 内置命令（help/skills/clear/context/status/model/compact 等）：直接处理
        3. MCP 相关命令：委托给对应的 _execute_mcp_* 方法
        4. 会话/记忆/导出/diff/config/permissions/focus/doctor 等：委托给对应方法
        5. 未实现的命令：显示警告 Toast
        6. 执行异常：捕获并显示错误信息

        参数:
            command: 已解析的命令对象
            args: 命令参数字符串

        返回:
            bool: 始终返回 True（表示命令已处理，无论成功与否）
        """
        name = command.name
        try:
            if command.type == CommandType.PROMPT:
                return await self._enqueue_prompt_command(command, args)
            if name == "help":
                lines = [
                    f"{self._command_usage_text(item)} - {item.description}"
                    for item in self._commands
                ]
                self.bridge.add_info_message("\n".join(lines), level="info")
                return True
            if name == "skills":
                await self._execute_skills_command(args)
                return True
            if name == "clear":
                engine = self.bridge.engine
                engine.messages = []
                if hasattr(engine, "turn_count"):
                    engine.turn_count = 1
                if hasattr(engine, "tool_schemas"):
                    engine.tool_schemas = None
                if hasattr(engine, "_archived_checkpoints"):
                    engine._archived_checkpoints = {}
                refresh_skills = getattr(engine, "refresh_skills", None)
                if callable(refresh_skills):
                    refresh_skills()
                self._refresh_available_commands()
                if getattr(engine, "enable_persistence", False):
                    new_session_id = str(uuid4())
                    engine.session_id = new_session_id
                    engine.session_storage = RuntimeSessionStorage(new_session_id, str(getattr(engine, "cwd", ".")))
                    if isinstance(getattr(engine, "execution_context", None), dict):
                        engine.execution_context["session_id"] = new_session_id
                self.bridge.clear_conversation()
                self.bridge.add_toast("会话已清空", level="info")
                return True
            if name == "context":
                stats = self.bridge.engine.get_context_stats()
                text = (
                    f"运行时令牌：{stats.get('token_count', 0)}\n"
                    f"剩余令牌：{stats.get('remaining_tokens', 0)}\n"
                    f"可见消息：{stats.get('model_visible_message_count', 0)}\n"
                    f"会话消息：{stats.get('session_message_count', 0)}"
                )
                self.bridge.add_info_message(text, level="info")
                return True
            if name == "status":
                snapshot = self.bridge.get_snapshot()
                permission_state = self.bridge.get_permission_mode_state()
                text = (
                    f"{snapshot.status.top_status}\n"
                    f"{snapshot.status.sub_status}\n"
                    f"权限：{permission_state['display_label']}\n"
                    f"侧栏视角：{self._sidebar_mode_text(snapshot)}\n"
                    f"自动跟随：{'开启' if snapshot.auto_follow else '关闭'}"
                )
                self.bridge.add_info_message(text, level="info")
                return True
            if name == "model":
                if args.strip():
                    self.bridge.engine.model = args.strip()
                    if hasattr(self.bridge.engine, "token_budget"):
                        self.bridge.engine.token_budget = TokenBudget(self.bridge.engine.model)
                    if isinstance(getattr(self.bridge.engine, "execution_context", None), dict):
                        options = self.bridge.engine.execution_context.setdefault("options", {})
                        options["model"] = self.bridge.engine.model
                    self.bridge.add_toast(f"已切换模型到 {self.bridge.engine.model}", level="info")
                else:
                    self.bridge.add_toast(f"当前模型：{self.bridge.engine.model}", level="info")
                return True
            if name == "compact":
                result = await self.bridge.engine.compact(args or None)
                self.bridge.reload_from_engine()
                if result is not None:
                    self.bridge.add_toast(
                        f"已压缩上下文：{result.pre_compact_token_count} -> {result.post_compact_token_count}",
                        level="info",
                    )
                    self.bridge.notify()
                return True
            if name == "sessions":
                await self._execute_sessions_command(args)
                self._refresh_available_commands()
                return True
            if name == "memory":
                await self._execute_memory_command(args)
                return True
            if name == "mcp-list":
                await self._execute_mcp_list_command()
                return True
            if name == "mcp-connect":
                await self._execute_mcp_connect_command(args)
                return True
            if name == "mcp-disconnect":
                await self._execute_mcp_disconnect_command(args)
                return True
            if name == "mcp-tools":
                await self._execute_mcp_tools_command(args)
                return True
            if name == "mcp-resources":
                await self._execute_mcp_resources_command(args)
                return True
            if name == "version":
                self.bridge.add_info_message(f"Codo {__version__}", level="info")
                return True
            if name == "diff":
                await self._execute_diff_command()
                return True
            if name == "export":
                await self._execute_export_command(args)
                return True
            if name == "config":
                await self._execute_config_command()
                return True
            if name == "permissions":
                await self._execute_permissions_command(args)
                return True
            if name == "focus":
                await self._execute_focus_command(args)
                return True
            if name == "doctor":
                await self._execute_doctor_command()
                return True
            if name == "exit":
                self.exit()
                return True
        except Exception as exc:
            self.bridge.add_info_message(f"/{name} 执行失败\n{exc}", level="error")
            return True

        self.bridge.add_toast(f"未实现的命令处理器：/{name}", level="warning")
        return True

    async def request_permission(self, tool_name: str, tool_info: str, message: str = "") -> Optional[str]:
        """
        代理权限请求到 bridge.request_permission（供工具层调用）。

        参数:
            tool_name: 请求权限的工具名称，如 "bash"
            tool_info: 工具调用详情描述，如 "rm -rf /tmp/test"
            message: 附加说明信息，默认为空

        返回:
            Optional[str]: 用户决策结果，如 "allow_once"、"deny"；取消时返回 None
        """
        return await self.bridge.request_permission(tool_name=tool_name, tool_info=tool_info, message=message)

    async def request_questions(self, questions: List[Any]) -> Optional[Dict[str, str]]:
        """
        代理问题请求到 bridge.request_questions（供工具层调用）。

        参数:
            questions: 问题列表，每项为 InteractionQuestion 对象

        返回:
            Optional[Dict[str, str]]: 用户回答字典，如 {"选择语言": "Python"}；取消时返回 None
        """
        return await self.bridge.request_questions(questions)

    async def request_change_review(self, change: ProposedFileChange) -> Optional[str]:
        """
        代理文件变更审阅请求到 bridge.request_change_review（供工具层调用）。

        参数:
            change: 提议的文件变更对象，包含路径、原始内容和新内容

        返回:
            Optional[str]: 用户决策结果，如 "accept"、"reject"；取消时返回 None
        """
        return await self.bridge.request_change_review(change)

    @on(Button.Pressed, ".retry-button")
    async def on_retry_pressed(self, _: Button.Pressed) -> None:
        """处理重试按钮点击：以 Worker 方式异步执行 bridge.retry_last_turn()。"""
        self.run_worker(self.bridge.retry_last_turn(), name="retry-turn", exclusive=False)
