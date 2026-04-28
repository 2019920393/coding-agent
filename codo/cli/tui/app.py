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
    """Codo Textual UI。"""

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
        yield HeaderPanel(id="app-header")
        with Horizontal(id="app-middle"):
            yield SidebarPanel(id="sidebar")
            yield MessageColumn()
        with Vertical(id="app-bottom"):
            yield InputPanel(bridge=self.bridge, id="input-panel")
        yield ToastHost(id="toast-host")

    async def on_mount(self) -> None:
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
        clear_active_app(self)
        self.bridge.close()

    def on_resize(self, event: events.Resize) -> None:
        self._update_layout_mode(event.size.width)

    def _update_layout_mode(self, width: int) -> None:
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
        self._pending_bridge_snapshot = snapshot
        if self._bridge_update_enqueued:
            return
        self._bridge_update_enqueued = True
        self.post_message(BridgeUpdated())

    async def on_bridge_updated(self, message: BridgeUpdated) -> None:
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
        if self.bridge.has_active_interaction:
            self.bridge.cancel_interaction()
            return
        if self.bridge.is_generating:
            self.bridge.interrupt_generation()
            return
        self.exit()

    def action_cycle_sidebar_backward(self) -> None:
        if self.focused is self.query_one(SidebarPanel):
            self.bridge.cycle_sidebar(-1)

    def action_cycle_sidebar_forward(self) -> None:
        if self.focused is self.query_one(SidebarPanel):
            self.bridge.cycle_sidebar(1)

    def action_toggle_auto_follow(self) -> None:
        if self.focused is self.query_one(SidebarPanel):
            self.bridge.toggle_auto_follow()

    def action_sidebar_global(self) -> None:
        self.bridge.set_sidebar_global()

    def on_key(self, event: events.Key) -> None:
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
        if not self._command_suggestions:
            return
        selected = self._command_suggestions[self._command_index]
        self.query_one("#chat-input", ChatInput).value = selected.completion

    @on(CommandSuggestionRowWidget.Selected)
    def on_command_suggestion_selected(self, event: CommandSuggestionRowWidget.Selected) -> None:
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
        self._refresh_command_menu(event.text_area.text)

    @on(ChatInput.Submitted, "#chat-input")
    async def on_input_submitted(self, event: ChatInput.Submitted) -> None:
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
        self.run_worker(self.bridge.submit_prompt(prompt), name="submit-prompt", exclusive=False)

    def _refresh_command_menu(self, raw_value: str) -> None:
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
        if command.argument_spec is not None and command.argument_spec.placeholder:
            return command.argument_spec.placeholder
        return command.argument_hint or ""

    @staticmethod
    def _command_usage_text(command: Command) -> str:
        hint = TextualChatApp._command_hint_text(command)
        return f"/{command.name} {hint}".rstrip()

    def _build_commands(self) -> List[Command]:
        cwd = str(getattr(self.bridge.engine, "cwd", "."))
        commands = list(get_enabled_commands())
        commands.extend(build_skill_commands(cwd))
        return commands

    def _refresh_available_commands(self) -> None:
        self._commands = self._build_commands()

    @staticmethod
    def _parse_slash_input(raw_value: str) -> tuple[str, str, bool]:
        body = raw_value[1:] if raw_value.startswith("/") else raw_value
        name, separator, remainder = body.partition(" ")
        return name.strip(), remainder, bool(separator)

    def _resolve_exact_command(self, token: str) -> Optional[Command]:
        if not token:
            return None
        return find_command(token, self._commands)

    def _match_argument_suggestions(self, command: Command, query: str) -> List[SlashSuggestion]:
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
        title = (
            str(info.get("user_title", "") or "").strip()
            or str(info.get("ai_title", "") or "").strip()
            or str(info.get("first_prompt", "") or "").strip()
        )
        return title or "未命名会话"

    @staticmethod
    def _session_modified_text(info: Dict[str, Any]) -> str:
        modified = str(info.get("modified", "") or "").strip()
        if not modified:
            return "时间未知"
        return modified[:16].replace("T", " ")

    @staticmethod
    def _sidebar_mode_text(snapshot: UISnapshot) -> str:
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
        if not args.strip():
            return []
        try:
            return shlex.split(args)
        except ValueError:
            return [args.strip()]

    @staticmethod
    def _format_json(data: Any) -> str:
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)

    def _resolve_path_arg(self, raw_path: str) -> Path:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = Path(str(getattr(self.bridge.engine, "cwd", "."))) / candidate
        return candidate

    def _engine_messages_for_export(self) -> List[Dict[str, Any]]:
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
        return [
            get_session_file_path(session_id, cwd),
            get_session_event_log_path(session_id, cwd),
            get_session_snapshot_path(session_id, cwd),
        ]

    def _current_session_is_blank(self) -> bool:
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
        argv = self._split_args(args)
        if not argv:
            self.bridge.add_toast("/mcp-disconnect 需要提供服务器名称", level="warning")
            return
        server_name = argv[0]
        await self.bridge.engine.mcp_client_manager.disconnect(server_name)
        await self.bridge.engine.refresh_mcp_tools()
        self.bridge.add_toast(f"已断开 MCP 服务器：{server_name}", level="info")

    async def _execute_mcp_tools_command(self, args: str) -> None:
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
        cwd = str(getattr(self.bridge.engine, "cwd", "."))
        payload = {
            "global_config_path": str(get_config_file()),
            "project_config_path": str(get_project_config_file(cwd)),
            "merged": get_merged_config(cwd),
        }
        self.bridge.add_info_message(self._format_json(payload), level="info")

    async def _execute_doctor_command(self) -> None:
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
        return await self.bridge.request_permission(tool_name=tool_name, tool_info=tool_info, message=message)

    async def request_questions(self, questions: List[Any]) -> Optional[Dict[str, str]]:
        return await self.bridge.request_questions(questions)

    async def request_change_review(self, change: ProposedFileChange) -> Optional[str]:
        return await self.bridge.request_change_review(change)

    @on(Button.Pressed, ".retry-button")
    async def on_retry_pressed(self, _: Button.Pressed) -> None:
        self.run_worker(self.bridge.retry_last_turn(), name="retry-turn", exclusive=False)
