from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

SCRIPT_PATH = Path(__file__).resolve()
WORKBENCH_ROOT = SCRIPT_PATH.parent.parent
REPO_ROOT = WORKBENCH_ROOT.parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

load_dotenv(REPO_ROOT / ".env")
load_dotenv(WORKBENCH_ROOT / ".env")

_log_path = Path(tempfile.gettempdir()) / "codo_bridge.log"
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.FileHandler(_log_path, mode="w", encoding="utf-8")],
    force=True,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("anthropic").setLevel(logging.INFO)
logging.getLogger(__name__).info("bridge logging -> %s", _log_path)

from codo.query_engine import QueryEngine  # noqa: E402
from codo.query import Terminal  # noqa: E402
from codo.types.permissions import PermissionMode  # noqa: E402

PROTOCOL_STDOUT = sys.stdout
sys.stdout = sys.stderr

DEFAULT_EVENT_TIMEOUT_SECONDS = 90.0
DEFAULT_MANUAL_INTERACTION_ENABLED = False
DEFAULT_TITLE_GENERATION_TIMEOUT_SECONDS = 12.0
CONTROL_HOST = "127.0.0.1"
BYTES_PER_MEBIBYTE = 1024 * 1024
MAX_CONTROL_COMMAND_BYTES = 80 * BYTES_PER_MEBIBYTE


@dataclass
class BridgeState:
    """Python bridge 的运行时状态。"""

    engine: Optional[QueryEngine] = None
    workspace_path: Optional[str] = None
    session_id: Optional[str] = None
    active_turn_id: Optional[str] = None
    active_task: Optional[asyncio.Task[None]] = None
    content_blocks: dict[int, "ActiveContentBlock"] = field(default_factory=dict)
    auto_resolved_interaction_ids: set[str] = field(default_factory=set)
    emitted_session_title: Optional[str] = None
    manual_interaction_enabled: bool = DEFAULT_MANUAL_INTERACTION_ENABLED


@dataclass
class ActiveContentBlock:
    """
    当前轮次里已经开始的内容块。

    工作流：
    1. `content_block_start` 到达时记录 block index、类型和工具 ID。
    2. `input_json_delta` 到达时按 index 找回工具块并累积 JSON。
    3. 前端就能看到工具入参是如何流式生成的，而不是只看到最后摘要。
    """

    index: int
    block_type: str
    tool_use_id: Optional[str]
    tool_name: Optional[str]
    input_json: str = ""


class WorkbenchAiBridgeApp:
    """
    负责把 QueryEngine 接到 Electron workbench。

    工作流：
    1. stdout 只输出 JSON 行事件，保证前端能稳定接收流式回复。
    2. 本地 TCP 控制端口只接收 submit/cancel/resolve 命令。
    3. 根据命令创建或复用 QueryEngine，并转发 query() 事件。
    """

    def __init__(self) -> None:
        self.state = BridgeState()
        self.command_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.control_server: Optional[asyncio.AbstractServer] = None
        self.control_port: Optional[int] = None
        self.api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        self.base_url = os.getenv("ANTHROPIC_BASE_URL", "").strip() or None
        self.model = os.getenv("CODO_MODEL", "").strip() or "claude-opus-4-5"
        self.event_timeout_seconds = parse_float_env(
            "CODO_AI_EVENT_TIMEOUT_SECONDS",
            DEFAULT_EVENT_TIMEOUT_SECONDS,
        )
        self.state.manual_interaction_enabled = parse_bool_env(
            "CODO_WORKBENCH_MANUAL_INTERACTION_ENABLED",
            DEFAULT_MANUAL_INTERACTION_ENABLED,
        )
        self.title_generation_timeout_seconds = parse_float_env(
            "CODO_WORKBENCH_TITLE_GENERATION_TIMEOUT_SECONDS",
            DEFAULT_TITLE_GENERATION_TIMEOUT_SECONDS,
        )

    async def run(self) -> None:
        """
        启动 bridge 主循环。

        工作流：
        1. 校验 API key，启动只监听本机的 TCP 控制端口。
        2. 通过 bridge-ready 把控制端口告诉 Electron。
        3. TCP handler 只把命令放入队列，dispatcher 顺序处理控制命令。
        4. AI turn 作为独立任务运行，流式事件只通过 stdout 输出。
        """
        if not self.api_key:
            self.emit_event(
                {
                    "kind": "bridge-error",
                    "message": "缺少 ANTHROPIC_API_KEY，AI bridge 无法启动。",
                }
            )
            return

        self.control_server = await asyncio.start_server(
            self.handle_control_connection,
            host=CONTROL_HOST,
            port=0,
            limit=MAX_CONTROL_COMMAND_BYTES + 1,
        )
        self.control_port = get_control_server_port(self.control_server)
        self.emit_bridge_ready(workspace_path=None, session_id=None)

        try:
            while True:
                command = await self.command_queue.get()
                await self.handle_command(command)
        finally:
            await self.close_control_server()

    async def handle_control_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """
        接收 Electron 主进程发来的控制命令。

        工作流：
        1. 每条命令使用一行 JSON，便于 Electron 打开短连接后立即关闭。
        2. TCP handler 不直接执行业务，只把合法命令放入 command_queue。
        3. 这样 resolve-interaction 不会打断 stdout 流式事件。
        """
        try:
            while True:
                try:
                    line = await reader.readline()
                except ValueError:
                    self.emit_bridge_error(
                        "AI 控制命令过大，已拒绝。请压缩图片或减少一次发送的图片数量。"
                    )
                    return

                if line == b"":
                    return

                if len(line) > MAX_CONTROL_COMMAND_BYTES:
                    self.emit_bridge_error("AI 控制命令过大，已拒绝。")
                    continue

                try:
                    decoded_line = line.decode("utf-8")
                except UnicodeDecodeError:
                    self.emit_bridge_error("AI 控制命令必须使用 UTF-8 编码。")
                    continue

                command = self.parse_command(decoded_line)
                if command is None:
                    self.emit_bridge_error("AI 控制命令不是合法 JSON 对象。")
                    continue

                await self.command_queue.put(command)
        finally:
            writer.close()
            await writer.wait_closed()

    async def close_control_server(self) -> None:
        """关闭本地 TCP 控制端口。"""
        if self.control_server is None:
            return

        self.control_server.close()
        await self.control_server.wait_closed()
        self.control_server = None
        self.control_port = None

    async def handle_command(self, command: dict[str, Any]) -> None:
        """处理 Electron 主进程发来的命令。"""
        command_type = str(command.get("type", "") or "")

        if command_type == "submit":
            request = command.get("request")
            if not isinstance(request, dict):
                self.emit_error_event(
                    turn_id="",
                    message="AI submit 请求必须是对象。",
                    recoverable=False,
                )
                return

            turn_id = extract_submit_turn_id(request)
            if turn_id is None:
                self.emit_error_event(
                    turn_id="",
                    message="turnId 不能为空。",
                    recoverable=False,
                )
                return

            if self.has_active_turn():
                self.emit_error_event(
                    turn_id=turn_id,
                    message="当前 AI 轮次仍在进行中，请先停止后再发送新消息。",
                    recoverable=False,
                )
                return

            self.start_turn(turn_id, request)
            return

        if command_type == "cancel":
            turn_id = str(command.get("turnId", "") or "")
            self.cancel_turn(turn_id)
            return

        if command_type == "resolve-interaction":
            request = command.get("request")
            if not isinstance(request, dict):
                self.emit_error_event(
                    turn_id=self.state.active_turn_id or "",
                    message="交互响应请求必须是对象。",
                    recoverable=True,
                )
                return
            self.resolve_interaction(request)
            return

        if command_type == "reset":
            manual_interaction_enabled = self.state.manual_interaction_enabled
            if self.state.active_task is not None and not self.state.active_task.done():
                self.state.active_task.cancel()
            self.state = BridgeState(
                manual_interaction_enabled=manual_interaction_enabled
            )
            self.emit_bridge_ready(workspace_path=None, session_id=None)
            return

        self.emit_error_event(
            turn_id="",
            message=f"未知命令：{command_type}",
            recoverable=False,
        )

    def has_active_turn(self) -> bool:
        """判断 bridge 是否仍有未结束的 AI 轮次。"""
        return self.state.active_task is not None and not self.state.active_task.done()

    def start_turn(self, turn_id: str, request: dict[str, Any]) -> None:
        """
        记录并启动一个 AI 轮次。

        工作流：
        1. 先写入 active_turn_id，再创建 asyncio task。
        2. 这样用户立刻点击停止时，cancel 也能准确找到这一轮。
        3. 每轮开始前清空工具输入、自动授权等上轮临时状态。
        """
        self.state.active_turn_id = turn_id
        self.state.content_blocks.clear()
        self.state.auto_resolved_interaction_ids.clear()
        self.state.active_task = self.create_turn_task(request)

    def cancel_turn(self, turn_id: str) -> None:
        """
        中断指定 AI 轮次。

        工作流：
        1. 只允许取消当前 active_turn_id，避免旧 cancel 误杀新一轮对话。
        2. 命中当前轮次时先 interrupt QueryEngine，再取消 asyncio task。
        3. bridge 立即发送 interrupt-ack，前端不用等待底层 runtime 完全退出。
        """
        active_turn_id = self.state.active_turn_id
        active_task = self.state.active_task

        if active_turn_id != turn_id:
            self.emit_interrupt_ack_event(
                turn_id=turn_id,
                reason="turn_not_active",
                turn_count=0,
            )
            return

        if self.state.engine is not None:
            self.state.engine.interrupt()

        if active_task is not None and not active_task.done():
            active_task.cancel()

        self.emit_interrupt_ack_event(
            turn_id=turn_id,
            reason="user_interrupted",
            turn_count=0,
        )
        self.clear_active_turn(turn_id)

    def clear_active_turn(self, turn_id: str) -> None:
        """
        清理当前轮次的 bridge 临时状态。

        工作流：
        1. 只清理匹配 turn_id 的状态，防止旧任务 finally 覆盖新任务。
        2. 工具 content block 和自动授权记录只属于当前轮次。
        3. QueryEngine 本身保留，下一轮可以继续复用会话上下文。
        """
        if self.state.active_turn_id != turn_id:
            return

        self.state.content_blocks.clear()
        self.state.auto_resolved_interaction_ids.clear()
        self.state.active_turn_id = None
        self.state.active_task = None

    def clear_active_task(self, task: asyncio.Task[None]) -> None:
        """
        按 task 身份清理状态。

        工作流：
        1. done callback 只处理仍然归属于当前 state 的 task。
        2. 如果用户已经开启新一轮，旧 task 的回调不能清掉新状态。
        """
        if self.state.active_task is not task:
            return

        turn_id = self.state.active_turn_id
        if turn_id is None:
            self.state.active_task = None
            return

        self.clear_active_turn(turn_id)

    def create_turn_task(self, request: dict[str, Any]) -> asyncio.Task[None]:
        """
        创建 AI 轮次任务。

        工作流：
        1. submit 创建独立任务，让 dispatcher 可以继续处理 resolve/cancel/reset。
        2. run_turn 内部负责发送流式事件、错误事件和清理 active 状态。
        3. done callback 只处理没有被 run_turn 捕获的异常，避免任务静默失败。
        """
        task = asyncio.create_task(self.run_turn(request))
        task.add_done_callback(self.handle_turn_task_done)
        return task

    def handle_turn_task_done(self, task: asyncio.Task[None]) -> None:
        """回收 AI 轮次任务异常，避免后台任务失败后 UI 永远停在运行态。"""
        if self.state.active_task is not task:
            return

        if task.cancelled():
            self.clear_active_task(task)
            return

        exception = task.exception()
        if exception is None:
            self.clear_active_task(task)
            return

        self.emit_error_event(
            turn_id=self.state.active_turn_id or "",
            message=str(exception),
            recoverable=False,
        )
        self.clear_active_task(task)

    def resolve_interaction(self, request: dict[str, Any]) -> None:
        """
        把 workbench UI 的交互答案回传给 QueryEngine。

        工作流：
        1. Renderer 传入 requestId 和 data。
        2. Python bridge 不解释业务含义，只校验 requestId 存在。
        3. QueryEngine.resolve_interaction 唤醒等待中的工具执行流程。
        """
        request_id = str(request.get("requestId", "") or "")
        if not request_id:
            self.emit_error_event(
                turn_id=self.state.active_turn_id or "",
                message="交互 requestId 不能为空。",
                recoverable=True,
            )
            return

        if self.state.engine is None:
            self.emit_error_event(
                turn_id=self.state.active_turn_id or "",
                message="当前没有可接收交互答案的 AI 引擎。",
                recoverable=True,
            )
            return

        self.state.engine.resolve_interaction(request_id, request.get("data"))

    async def run_turn(self, request: dict[str, Any]) -> None:
        """
        执行单轮对话并输出流式事件。

        工作流：
        1. 校验 workspace 和 prompt。
        2. 按 workspacePath 创建或复用 QueryEngine。
        3. 逐条转发 query() 事件给 stdout。
        4. 完成后清理 active 状态。
        """
        turn_id = ""
        try:
            turn_id = str(request.get("turnId", "") or "")
            workspace_path = str(request.get("workspacePath", "") or "").strip()
            workspace_name = str(request.get("workspaceName", "") or "").strip()
            prompt = str(request.get("prompt", "") or "").strip()
            images = request.get("images") or []
            session_id = normalize_session_id(request.get("sessionId"))
            manual_interaction_enabled = parse_permission_mode(
                request.get("permissionMode"),
                self.state.manual_interaction_enabled,
            )

            if not turn_id:
                self.emit_error_event(
                    turn_id="",
                    message="turnId 不能为空。",
                    recoverable=False,
                )
                return

            if not workspace_path:
                self.emit_error_event(
                    turn_id=turn_id,
                    message="请先选择工作区，再发起 AI 对话。",
                    recoverable=False,
                )
                return

            if not prompt:
                self.emit_error_event(
                    turn_id=turn_id,
                    message="prompt 不能为空。",
                    recoverable=False,
                )
                return

            self.state.active_turn_id = turn_id
            self.state.manual_interaction_enabled = manual_interaction_enabled
            self.state.content_blocks.clear()
            self.state.auto_resolved_interaction_ids.clear()
            self.emit_runtime_status(
                turn_id=turn_id,
                phase="prepare_turn",
                status_message="正在初始化 Codo QueryEngine...",
            )
            engine = self.ensure_engine(
                workspace_path,
                session_id,
                manual_interaction_enabled,
            )

            if engine is None:
                self.emit_error_event(
                    turn_id=turn_id,
                    message="AI 引擎初始化失败。",
                    recoverable=False,
                )
                return

            # 上一轮如果被用户中断，QueryEngine 的 abort_controller 还停在 aborted 状态。
            # 这里复用同一个引擎前必须显式 reset，否则新一轮开局就会立刻被旧的中断标记打断。
            engine.reset_interrupt_state()

            context_prompt = self.build_context_prompt(
                request,
                workspace_name,
                workspace_path,
                prompt,
                images,
            )

            self.emit_runtime_status(
                turn_id=turn_id,
                phase="stream_assistant",
                status_message="AI 正在启动本轮对话...",
            )
            terminal_event_emitted = False
            fatal_error_emitted = False
            async for event in self.iter_query_events_with_timeout(
                turn_id,
                engine,
                context_prompt,
            ):
                if isinstance(event, Terminal):
                    terminal_event_emitted = True
                elif is_fatal_error_event(event):
                    fatal_error_emitted = True
                self.forward_query_event(turn_id, event)

            if not fatal_error_emitted:
                if not terminal_event_emitted:
                    self.emit_completed_event(
                        turn_id=turn_id,
                        reason="completed",
                        turn_count=0,
                    )

                self.clear_active_turn(turn_id)
                await self.ensure_session_title(engine)
        except asyncio.CancelledError:
            if self.state.active_turn_id == turn_id:
                self.emit_interrupt_ack_event(
                    turn_id=turn_id,
                    reason="task_cancelled",
                    turn_count=0,
                )
            raise
        except Exception as exc:  # noqa: BLE001
            self.emit_error_event(
                turn_id=turn_id,
                message=str(exc),
                recoverable=False,
            )
        finally:
            self.clear_active_turn(turn_id)

    async def iter_query_events_with_timeout(
        self,
        turn_id: str,
        engine: QueryEngine,
        context_prompt: str | list[dict[str, Any]],
    ):
        """
        带超时保护地消费 QueryEngine 事件流。

        工作流：
        1. 调用 codo 的 `submit_message_stream()` 获取异步迭代器。
        2. 每次等待下一个事件时设置超时。
        3. 如果长时间没有任何 runtime 事件，抛出明确错误，让前端结束占位。
        """
        iterator = engine.submit_message_stream(context_prompt).__aiter__()
        while True:
            try:
                event = await asyncio.wait_for(
                    iterator.__anext__(),
                    timeout=self.event_timeout_seconds,
                )
            except StopAsyncIteration:
                return
            except asyncio.TimeoutError as exc:
                raise TimeoutError(
                    "等待 Codo runtime 事件超时。可能卡在 MCP 刷新、模型 API 请求、"
                    f"网络代理或模型名配置。当前超时：{self.event_timeout_seconds:.0f}s，"
                    f"model={self.model}，base_url={self.base_url or 'Anthropic default'}。"
                ) from exc
            yield event

    def ensure_engine(
        self,
        workspace_path: str,
        session_id: Optional[str],
        manual_interaction_enabled: bool,
    ) -> Optional[QueryEngine]:
        """
        按 workspacePath 和 sessionId 创建或复用 QueryEngine。

        工作流：
        1. workspace 和 sessionId 都一致时复用旧引擎。
        2. 复用旧引擎时仍重新应用本轮权限模式，保证 UI 开关生效。
        3. 传入 sessionId 时创建引擎后恢复磁盘历史。
        4. 未传 sessionId 时创建新会话，后续 bridge-ready 会把新 ID 回传前端。
        """
        if (
            self.state.engine is not None
            and self.state.workspace_path == workspace_path
            and (session_id is None or self.state.session_id == session_id)
        ):
            self.configure_permission_mode(
                self.state.engine,
                manual_interaction_enabled,
            )
            return self.state.engine

        self.state.workspace_path = workspace_path
        self.state.session_id = session_id
        self.state.emitted_session_title = None
        self.state.engine = QueryEngine(
            api_key=self.api_key,
            cwd=workspace_path,
            verbose=False,
            model=self.model,
            enable_persistence=True,
            base_url=self.base_url,
            session_id=session_id,
        )
        if session_id is not None:
            self.state.engine.restore_session()
        self.configure_permission_mode(
            self.state.engine,
            manual_interaction_enabled,
        )
        self.state.session_id = self.state.engine.session_id
        self.emit_existing_session_title(self.state.engine)

        self.emit_bridge_ready(
            workspace_path=workspace_path,
            session_id=self.state.engine.session_id,
        )
        return self.state.engine

    async def ensure_session_title(self, engine: QueryEngine) -> None:
        """
        确保 Workbench 会话在结束后有可读标题。

        工作流：
        1. 优先复用 SessionStorage 已保存的标题。
        2. 没有标题时调用 codo 主体的 generate_and_save_title，不自己造标题逻辑。
        3. 生成成功后推送 session-title-updated，让右侧历史按钮立即更新。
        """
        existing_title = self.get_engine_session_title(engine)
        if existing_title is not None:
            self.emit_session_title(engine, existing_title)
            return

        session_storage = engine.session_storage
        messages_snapshot = list(engine.messages)
        if session_storage is None or not messages_snapshot:
            return

        try:
            from codo.session.title import generate_and_save_title

            title = await asyncio.wait_for(
                generate_and_save_title(
                    client=engine.client,
                    model=engine.model,
                    messages=messages_snapshot,
                    session_storage=session_storage,
                ),
                timeout=self.title_generation_timeout_seconds,
            )
        except Exception:
            return

        if title is not None and title.strip():
            self.emit_session_title(engine, title.strip())

    def emit_existing_session_title(self, engine: QueryEngine) -> None:
        """恢复历史会话后，如果已有标题，立即同步给前端。"""
        existing_title = self.get_engine_session_title(engine)
        if existing_title is not None:
            self.emit_session_title(engine, existing_title)

    def get_engine_session_title(self, engine: QueryEngine) -> Optional[str]:
        """从 QueryEngine 的 SessionStorage 读取当前标题。"""
        session_storage = engine.session_storage
        if session_storage is None:
            return None

        title = getattr(session_storage, "current_title", None)
        if isinstance(title, str) and title.strip():
            return title.strip()

        info = session_storage.get_session_info()
        return (
            to_non_empty_string(info.get("user_title"))
            or to_non_empty_string(info.get("ai_title"))
        )

    def emit_session_title(self, engine: QueryEngine, title: str) -> None:
        """把会话标题更新推送给 Renderer。"""
        if self.state.emitted_session_title == title:
            return

        self.state.emitted_session_title = title
        self.emit_event(
            {
                "kind": "session-title-updated",
                "workspacePath": engine.cwd,
                "sessionId": engine.session_id,
                "title": title,
            }
        )

    def configure_permission_mode(
        self,
        engine: QueryEngine,
        manual_interaction_enabled: bool,
    ) -> None:
        """
        配置 workbench 的工具授权模式。

        工作流：
        1. 读取本轮请求传入的权限模式。
        2. 手动：保留 codo 默认权限模式，工具调用会等待人工确认。
        3. 自动：切到 bypassPermissions，避免右侧 UI 卡在 wait_interaction。

        说明：
        bypassPermissions 只绕过普通工具授权；codo 的路径安全检查仍会执行。
        """
        permission_context = engine.execution_context.get("permission_context")
        if permission_context is None:
            return

        mode = (
            PermissionMode.DEFAULT
            if manual_interaction_enabled
            else PermissionMode.BYPASS_PERMISSIONS
        )
        permission_context.mode = mode
        options = engine.execution_context.setdefault("options", {})
        options["permission_mode"] = mode.value

    def build_context_prompt(
        self,
        request: dict[str, Any],
        workspace_name: str,
        workspace_path: str,
        prompt: str,
        images: list[dict[str, Any]],
    ) -> str | list[dict[str, Any]]:
        """
        把工作台上下文合并进用户 prompt。

        工作流：
        1. 只拼接必要上下文，不把整个文件树塞进 prompt。
        2. 让模型知道当前 workspace、打开的文件和选中的文件。
        3. 保持 prompt 简洁，避免无意义的上下文噪音。
        4. 支持图片附件，返回多模态内容数组。
        """
        active_file_path = request.get("activeFilePath")
        selected_path = request.get("selectedPath")
        open_file_paths = request.get("openFilePaths") or []
        open_file_lines = "\n".join(f"- {item}" for item in open_file_paths if isinstance(item, str))

        text_content = (
            "【工作台上下文】\n"
            f"工作区名称：{workspace_name}\n"
            f"工作区路径：{workspace_path}\n"
            f"当前文件：{active_file_path or '无'}\n"
            f"当前选中项：{selected_path or '无'}\n"
            f"已打开文件：\n{open_file_lines or '- 无'}\n\n"
            "【用户请求】\n"
            f"{prompt}"
        )

        # 如果没有图片，返回纯文本
        if not images:
            return text_content

        # 有图片时，返回多模态内容数组
        content_parts: list[dict[str, Any]] = [{"type": "text", "text": text_content}]

        for image in images:
            if isinstance(image, dict) and "base64" in image and "mimeType" in image:
                content_parts.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image["mimeType"],
                        "data": image["base64"],
                    },
                })

        return content_parts

    def forward_query_event(self, turn_id: str, event: Any) -> None:
        """
        把 QueryEngine 的事件转换成前端协议事件。

        工作流：
        1. 先识别 codo runtime 的原始事件类型。
        2. 不改写 codo 主体状态，只把已有事实映射为 workbench 可展示的数据。
        3. 对工具输入流、agent 流和 todo 状态做结构化转发。
        """
        if isinstance(event, Terminal):
            self.emit_completed_event(
                turn_id=turn_id,
                reason=event.reason,
                turn_count=int(event.metadata.get("turn_count", 0) or 0)
                if isinstance(event.metadata, dict)
                else 0,
            )
            return

        if not isinstance(event, dict):
            return

        event_type = str(event.get("type", "") or "")
        if event_type == "turn_started":
            self.emit_event(
                {
                    "kind": "turn-started",
                    "turnId": turn_id,
                    "turnCount": int(event.get("turn_count", 0) or 0),
                    "messagesCount": int(event.get("messages_count", 0) or 0),
                }
            )
            return

        if event_type == "turn_completed":
            metadata = event.get("metadata", {})
            self.emit_event(
                {
                    "kind": "turn-completed",
                    "turnId": turn_id,
                    "reason": str(event.get("reason", "") or ""),
                    "turnCount": int(event.get("turn_count", 0) or 0),
                    "messageCount": extract_message_count(metadata),
                }
            )
            return

        if event_type == "status_changed":
            phase = str(event.get("phase", "") or "")
            pending_interaction = event.get("pending_interaction")
            self.emit_event(
                {
                    "kind": "status-changed",
                    "turnId": turn_id,
                    "phase": normalize_runtime_phase(phase),
                    "statusMessage": map_phase_to_status_message(phase),
                    "turnCount": int(event.get("turn_count", 0) or 0),
                    "checkpointId": to_nullable_string(event.get("checkpoint_id")),
                    "activeToolIds": to_string_list(event.get("active_tool_ids")),
                    "activeAgentId": to_nullable_string(event.get("active_agent_id")),
                    "pendingInteraction": normalize_status_pending_interaction(
                        pending_interaction,
                        self.state.manual_interaction_enabled,
                    ),
                    "interruptReason": to_nullable_string(event.get("interrupt_reason")),
                    "resumeTarget": to_nullable_string(event.get("resume_target")),
                    "metadata": normalize_runtime_metadata(event.get("metadata")),
                }
            )
            return

        if event_type == "interaction_requested":
            pending_interaction = event.get("request")
            auto_resolved = self.auto_resolve_pending_interaction(pending_interaction)
            self.emit_event(
                {
                    "kind": "status-changed",
                    "turnId": turn_id,
                    "phase": "wait_interaction",
                    "statusMessage": "AI 正在等待交互处理...",
                    "turnCount": int(event.get("turn_count", 0) or 0),
                    "checkpointId": None,
                    "activeToolIds": [],
                    "activeAgentId": None,
                    "pendingInteraction": None
                    if auto_resolved
                    else normalize_pending_interaction(pending_interaction),
                    "interruptReason": None,
                    "resumeTarget": None,
                    "metadata": {
                        "summary": "interaction_requested",
                        "reason": None,
                        "messageCount": None,
                        "toolCount": None,
                        "contentBlockCount": None,
                    },
                }
            )
            return

        if event_type == "interaction_resolved":
            self.emit_event(
                {
                    "kind": "status-changed",
                    "turnId": turn_id,
                    "phase": "apply_interaction_result",
                    "statusMessage": "AI 正在应用交互结果...",
                    "turnCount": int(event.get("turn_count", 0) or 0),
                    "checkpointId": None,
                    "activeToolIds": [],
                    "activeAgentId": None,
                    "pendingInteraction": None,
                    "interruptReason": None,
                    "resumeTarget": None,
                    "metadata": {
                        "summary": "interaction_resolved",
                        "reason": None,
                        "messageCount": None,
                        "toolCount": None,
                        "contentBlockCount": None,
                    },
                }
            )
            return

        if event_type == "stream_request_start":
            self.emit_event({"kind": "stream-started", "turnId": turn_id})
            return

        if event_type == "compact":
            result = event.get("result", {})
            self.emit_event(
                {
                    "kind": "compact",
                    "turnId": turn_id,
                    "preTokens": extract_int(result, "pre_tokens"),
                    "postTokens": extract_int(result, "post_tokens"),
                }
            )
            return

        if event_type == "content_block_start":
            block_info = normalize_content_block(event)
            self.state.content_blocks[block_info.index] = block_info
            self.emit_event(
                {
                    "kind": "content-block-started",
                    "turnId": turn_id,
                    "index": block_info.index,
                    "blockType": normalize_content_block_type(block_info.block_type),
                    "toolUseId": block_info.tool_use_id,
                    "toolName": block_info.tool_name,
                }
            )
            return

        if event_type == "content_block_stop":
            self.emit_event(
                {
                    "kind": "content-block-stopped",
                    "turnId": turn_id,
                    "index": int(event.get("index", 0) or 0),
                }
            )
            return

        if event_type == "text_delta":
            delta = event.get("delta", {})
            self.emit_event(
                {
                    "kind": "text-delta",
                    "turnId": turn_id,
                    "index": to_nullable_int(event.get("index")),
                    "delta": str(delta.get("text", "") if isinstance(delta, dict) else ""),
                }
            )
            return

        if event_type == "thinking_delta":
            delta = event.get("delta", {})
            self.emit_event(
                {
                    "kind": "thinking-delta",
                    "turnId": turn_id,
                    "index": to_nullable_int(event.get("index")),
                    "delta": str(delta.get("thinking", "") if isinstance(delta, dict) else ""),
                }
            )
            return

        if event_type == "input_json_delta":
            index = int(event.get("index", 0) or 0)
            delta = event.get("delta", {})
            partial_json = str(
                delta.get("partial_json", "") if isinstance(delta, dict) else ""
            )
            block_info = self.state.content_blocks.get(index)
            accumulated_json = partial_json
            tool_use_id: Optional[str] = None
            tool_name: Optional[str] = None
            if block_info is not None:
                block_info.input_json += partial_json
                accumulated_json = block_info.input_json
                tool_use_id = block_info.tool_use_id
                tool_name = block_info.tool_name
            self.emit_event(
                {
                    "kind": "tool-input-delta",
                    "turnId": turn_id,
                    "index": index,
                    "toolUseId": tool_use_id,
                    "toolName": tool_name,
                    "partialJson": partial_json,
                    "accumulatedJson": accumulated_json,
                }
            )
            return

        if event_type == "tool_started":
            self.emit_event(
                {
                    "kind": "tool-started",
                    "turnId": turn_id,
                    "toolUseId": str(event.get("tool_use_id", "") or ""),
                    "toolName": str(event.get("tool_name", "") or ""),
                    "inputPreview": str(event.get("input_preview", "") or ""),
                    "status": "running",
                }
            )
            return

        if event_type == "tool_progress":
            self.emit_event(
                {
                    "kind": "tool-progress",
                    "turnId": turn_id,
                    "toolUseId": str(event.get("tool_use_id", "") or ""),
                    "toolName": str(event.get("tool_name", "") or ""),
                    "progress": str(event.get("progress", "") or ""),
                }
            )
            return

        if event_type == "tool_completed":
            self.emit_event(
                {
                    "kind": "tool-completed",
                    "turnId": turn_id,
                    "tool": normalize_tool_summary(event, None),
                }
            )
            return

        if event_type == "tool_result":
            tool_use_id = str(event.get("tool_use_id", "") or "")
            self.emit_event(
                {
                    "kind": "tool-result",
                    "turnId": turn_id,
                    "tool": normalize_tool_summary(
                        event,
                        self.find_tool_name(tool_use_id),
                    ),
                    "isError": bool(event.get("is_error", False)),
                }
            )
            return

        if event_type == "todo_updated":
            self.emit_event(
                {
                    "kind": "todo-updated",
                    "turnId": turn_id,
                    "key": str(event.get("key", "") or ""),
                    "items": normalize_todo_items(event.get("items", [])),
                    "toolUseId": str(event.get("tool_use_id", "") or ""),
                }
            )
            return

        if event_type == "agent_started":
            self.emit_event(
                {
                    "kind": "agent-started",
                    "turnId": turn_id,
                    "agent": normalize_agent_summary(event),
                }
            )
            return

        if event_type == "agent_delta":
            self.emit_event(
                {
                    "kind": "agent-delta",
                    "turnId": turn_id,
                    "agentId": str(event.get("agent_id", "") or ""),
                    "taskId": to_nullable_string(event.get("task_id")),
                    "contentDelta": str(event.get("content_delta", "") or ""),
                    "thinkingDelta": str(event.get("thinking_delta", "") or ""),
                }
            )
            return

        if event_type == "agent_tool_started":
            self.emit_event(
                {
                    "kind": "agent-tool-started",
                    "turnId": turn_id,
                    "agentId": str(event.get("agent_id", "") or ""),
                    "taskId": to_nullable_string(event.get("task_id")),
                    "toolUseId": str(event.get("tool_use_id", "") or ""),
                    "toolName": str(event.get("tool_name", "") or ""),
                    "inputPreview": str(event.get("input_preview", "") or ""),
                }
            )
            return

        if event_type == "agent_tool_completed":
            self.emit_event(
                {
                    "kind": "agent-tool-completed",
                    "turnId": turn_id,
                    "agentId": str(event.get("agent_id", "") or ""),
                    "taskId": to_nullable_string(event.get("task_id")),
                    "tool": normalize_tool_summary(event, None),
                }
            )
            return

        if event_type == "agent_completed":
            self.emit_event(
                {
                    "kind": "agent-completed",
                    "turnId": turn_id,
                    "agentId": str(event.get("agent_id", "") or ""),
                    "taskId": to_nullable_string(event.get("task_id")),
                    "result": str(event.get("result", "") or ""),
                    "status": "completed",
                    "totalTokens": int(event.get("total_tokens", 0) or 0),
                }
            )
            return

        if event_type == "agent_error":
            self.emit_event(
                {
                    "kind": "agent-error",
                    "turnId": turn_id,
                    "agentId": str(event.get("agent_id", "") or ""),
                    "taskId": to_nullable_string(event.get("task_id")),
                    "error": str(event.get("error", "") or ""),
                    "status": "error",
                }
            )
            return

        if event_type == "message_stop":
            self.emit_event(
                {
                    "kind": "message-stop",
                    "turnId": turn_id,
                }
            )
            return

        if event_type == "interrupt_ack":
            self.emit_event(
                {
                    "kind": "interrupt-ack",
                    "turnId": turn_id,
                    "reason": str(event.get("reason", "") or ""),
                    "turnCount": int(event.get("turn_count", 0) or 0),
                }
            )
            return

        if event_type == "error":
            self.emit_event(
                {
                    "kind": "error",
                    "turnId": turn_id,
                    "message": str(event.get("error", "") or ""),
                    "errorType": to_nullable_string(event.get("error_type")),
                    "category": to_nullable_string(event.get("category")),
                    "recoverable": bool(event.get("recoverable", False)),
                    "retryAttempt": to_nullable_int(event.get("retry_attempt")),
                    "maxRetries": to_nullable_int(event.get("max_retries")),
                }
            )
            return

    def emit_runtime_status(self, turn_id: str, phase: str, status_message: str) -> None:
        """
        在 QueryEngine 真正产出事件前，先给前端一个可见状态。

        工作流：
        1. submit 命令进入 Python bridge 后立即调用。
        2. QueryEngine 初始化或主循环启动慢时，前端不会误以为没有响应。
        3. 这不是 codo 主体状态，只是 bridge 层的连接状态。
        """
        self.emit_event(
            {
                "kind": "status-changed",
                "turnId": turn_id,
                "phase": normalize_runtime_phase(phase),
                "statusMessage": status_message,
                "turnCount": 0,
                "checkpointId": None,
                "activeToolIds": [],
                "activeAgentId": None,
                "pendingInteraction": None,
                "interruptReason": None,
                "resumeTarget": None,
                "metadata": {
                    "summary": status_message,
                    "reason": None,
                    "messageCount": None,
                    "toolCount": None,
                    "contentBlockCount": None,
                },
            }
        )

    def emit_completed_event(self, turn_id: str, reason: str, turn_count: int) -> None:
        """
        发出前端可识别的 AI 轮次终态。

        工作流：
        1. codo 主体通常会用 Terminal 表示整轮结束。
        2. 某些流只会正常结束迭代，不再额外产出 Terminal。
        3. bridge 需要兜底补一个 completed，避免 Workbench 一直停在“回复中”。
        """
        self.emit_event(
            {
                "kind": "completed",
                "turnId": turn_id,
                "reason": reason,
                "turnCount": turn_count,
            }
        )

    def emit_interrupt_ack_event(self, turn_id: str, reason: str, turn_count: int) -> None:
        """
        立即通知前端当前轮次已被用户中断。

        工作流：
        1. cancel 命令进入 bridge 后先调用 QueryEngine.interrupt()。
        2. bridge 主动发 interrupt-ack，让 Workbench 立即释放输入框。
        3. 后续 runtime 若还有迟到事件，前端会按 turnId 忽略。
        """
        self.emit_event(
            {
                "kind": "interrupt-ack",
                "turnId": turn_id,
                "reason": reason,
                "turnCount": turn_count,
            }
        )

    def auto_resolve_pending_interaction(self, value: Any) -> bool:
        """
        在 workbench 关闭人工交互时自动处理 runtime pending interaction。

        工作流：
        1. 仅在 interaction_requested 之后生效，避免 Future 未注册时丢答案。
        2. permission 自动选择 allow_once，避免 Glob/Read/Grep 卡住。
        3. diff_review 自动 accept，让编辑类工具继续提交 staged change。
        4. question/unknown 不自动处理，必须交给 UI。
        """
        if self.state.manual_interaction_enabled or not isinstance(value, dict):
            return False

        request_id = str(value.get("request_id", value.get("requestId", "")) or "")
        if not request_id or request_id in self.state.auto_resolved_interaction_ids:
            return request_id in self.state.auto_resolved_interaction_ids

        if self.state.engine is None:
            return False

        response = build_auto_interaction_response(value)
        if response is None:
            return False

        self.state.auto_resolved_interaction_ids.add(request_id)
        self.state.engine.resolve_interaction(request_id, response)
        return True

    def find_tool_name(self, tool_use_id: str) -> Optional[str]:
        """根据 tool_use_id 从当前内容块里找工具名称。"""
        if not tool_use_id:
            return None
        for block in self.state.content_blocks.values():
            if block.tool_use_id == tool_use_id:
                return block.tool_name
        return None

    def emit_bridge_ready(
        self,
        workspace_path: Optional[str],
        session_id: Optional[str],
    ) -> None:
        """
        通知 Electron bridge 已准备好接收控制命令。

        工作流：
        1. Python 启动 TCP 控制端口后立即发送一次。
        2. 工作区或会话切换后再次发送，保持 sessionId 同步。
        3. Electron 只用 controlPort 发命令，不再写 stdin。
        """
        self.emit_event(
            {
                "kind": "bridge-ready",
                "workspacePath": workspace_path,
                "sessionId": session_id,
                "controlPort": self.control_port,
            }
        )

    def emit_bridge_error(self, message: str) -> None:
        """输出 bridge 级别错误，不绑定具体 AI 轮次。"""
        self.emit_event(
            {
                "kind": "bridge-error",
                "message": message,
            }
        )

    def emit_error_event(self, turn_id: str, message: str, recoverable: bool) -> None:
        """输出错误事件到前端。"""
        self.emit_event(
            {
                "kind": "error",
                "turnId": turn_id,
                "message": message,
                "errorType": None,
                "category": None,
                "recoverable": recoverable,
                "retryAttempt": None,
                "maxRetries": None,
            }
        )

    def emit_event(self, payload: dict[str, Any]) -> None:
        """向 stdout 输出一条 JSON 事件。"""
        PROTOCOL_STDOUT.write(json.dumps(payload, ensure_ascii=False) + "\n")
        PROTOCOL_STDOUT.flush()

    def parse_command(self, line: str) -> Optional[dict[str, Any]]:
        """解析主进程下发的命令。"""
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            return None

        return value if isinstance(value, dict) else None


def get_control_server_port(server: asyncio.AbstractServer) -> int:
    """
    读取 TCP 控制端口。

    工作流：
    1. asyncio.start_server(port=0) 会让系统分配一个空闲端口。
    2. 这里从 server.sockets 里取出真实端口。
    3. 端口作为 bridge-ready.controlPort 发给 Electron。
    """
    sockets = server.sockets
    if not sockets:
        raise RuntimeError("AI 控制端口启动失败：没有监听 socket。")

    socket_name = sockets[0].getsockname()
    if not isinstance(socket_name, tuple) or len(socket_name) < 2:
        raise RuntimeError("AI 控制端口启动失败：无法读取 socket 地址。")

    port = socket_name[1]
    if not isinstance(port, int):
        raise RuntimeError("AI 控制端口启动失败：端口不是整数。")

    return port


def normalize_content_block(value: dict[str, Any]) -> ActiveContentBlock:
    """
    读取 codo 的 content_block_start 事件。

    Anthropic SDK 的 content_block 不是普通 dict，所以这里用 getattr 读取
    type/id/name。这样前端能把后续 input_json_delta 归到正确工具。
    """
    block = value.get("content_block")
    index = int(value.get("index", 0) or 0)
    block_type = str(getattr(block, "type", "") or "unknown")
    tool_use_id = to_nullable_string(getattr(block, "id", None))
    tool_name = to_nullable_string(getattr(block, "name", None))
    return ActiveContentBlock(
        index=index,
        block_type=block_type,
        tool_use_id=tool_use_id,
        tool_name=tool_name,
    )


def normalize_content_block_type(value: str) -> str:
    """把 SDK block 类型收敛到前端协议允许的几种。"""
    if value in {"text", "thinking", "tool_use"}:
        return value
    return "unknown"


def normalize_runtime_phase(value: str) -> str:
    """把 QueryState.phase 收敛到前端已知阶段。"""
    allowed = {
        "idle",
        "submitted",
        "ready",
        "prepare_turn",
        "stream_assistant",
        "dispatch_tools",
        "execute_tools",
        "wait_interaction",
        "apply_interaction_result",
        "collect_tool_results",
        "compact",
        "stop_hooks",
        "complete",
        "error",
        "interrupted",
    }
    return value if value in allowed else "error"


def normalize_runtime_metadata(value: Any) -> dict[str, Any]:
    """把 status_changed.metadata 转成固定结构，避免前端接收任意字典。"""
    metadata = value if isinstance(value, dict) else {}
    return {
        "summary": build_metadata_summary(metadata),
        "reason": to_nullable_string(metadata.get("reason")),
        "messageCount": extract_first_int(metadata, ["message_count", "messages"]),
        "toolCount": extract_first_int(metadata, ["tool_count"]),
        "contentBlockCount": extract_first_int(metadata, ["content_blocks"]),
    }


def build_metadata_summary(metadata: dict[str, Any]) -> str:
    """把常见 runtime metadata 压成一行给执行流显示。"""
    parts: list[str] = []
    reason = to_nullable_string(metadata.get("reason"))
    if reason:
        parts.append(f"reason={reason}")
    message_count = extract_first_int(metadata, ["message_count", "messages"])
    if message_count is not None:
        parts.append(f"messages={message_count}")
    tool_count = extract_first_int(metadata, ["tool_count"])
    if tool_count is not None:
        parts.append(f"tools={tool_count}")
    return " · ".join(parts)


def normalize_pending_interaction(value: Any) -> Optional[dict[str, Any]]:
    """把权限确认、diff review、用户问题转成前端协议结构。"""
    if not isinstance(value, dict):
        return None
    request_id = str(value.get("request_id", value.get("requestId", "")) or "")
    return {
        "requestId": request_id,
        "kind": str(value.get("kind", "") or ""),
        "label": str(value.get("label", "") or ""),
        "toolName": str(value.get("tool_name", value.get("toolName", "")) or ""),
        "toolInfo": str(value.get("tool_info", value.get("toolInfo", "")) or ""),
        "message": str(value.get("message", "") or ""),
        "questions": normalize_interaction_questions(value.get("questions")),
        "options": normalize_interaction_options(value.get("options")),
        "initialValue": to_nullable_string(
            value.get("initial_value", value.get("initialValue"))
        ),
        "payload": normalize_string_dict(value.get("payload")),
    }


def normalize_status_pending_interaction(
    value: Any,
    manual_interaction_enabled: bool,
) -> Optional[dict[str, Any]]:
    """
    规范化 status_changed 阶段的 pending interaction。

    工作流：
    1. 手动模式：直接展示 pending 卡片，用户可以看到 AI 正在等什么。
    2. 自动模式：permission/diff_review 会在 interaction_requested 后自动处理，这里不展示。
    3. 自动模式：question 无法可靠推断答案，仍展示给用户处理。
    """
    if not isinstance(value, dict):
        return None

    if not manual_interaction_enabled and build_auto_interaction_response(value) is not None:
        return None

    return normalize_pending_interaction(value)


def normalize_interaction_questions(value: Any) -> list[dict[str, Any]]:
    """把 InteractionQuestion 列表转成前端需要的 camelCase 结构。"""
    if not isinstance(value, list):
        return []

    questions: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        questions.append(
            {
                "questionId": str(
                    item.get("question_id", item.get("questionId", "")) or ""
                ),
                "header": str(item.get("header", "") or ""),
                "question": str(item.get("question", "") or ""),
                "options": normalize_interaction_options(item.get("options")),
                "multiSelect": bool(
                    item.get("multi_select", item.get("multiSelect", False))
                ),
            }
        )
    return questions


def normalize_interaction_options(value: Any) -> list[dict[str, str]]:
    """把 InteractionOption 列表转成前端按钮数据。"""
    if not isinstance(value, list):
        return []

    options: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        options.append(
            {
                "value": str(item.get("value", "") or ""),
                "label": str(item.get("label", "") or ""),
                "description": str(item.get("description", "") or ""),
                "preview": str(item.get("preview", "") or ""),
            }
        )
    return options


def normalize_string_dict(value: Any) -> dict[str, str]:
    """把 payload 收敛为字符串字典，避免前端收到任意嵌套对象。"""
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items() if item is not None}


def build_auto_interaction_response(value: dict[str, Any]) -> Any:
    """
    根据交互类型构造自动响应。

    工作流：
    1. permission：普通工具授权，返回 allow_once。
    2. diff_review：编辑/写入的 staged change 审阅，返回 accept。
    3. 其他交互没有可靠默认答案，返回 None 让 runtime 走取消/拒绝路径。
    """
    interaction_kind = str(value.get("kind", "") or "")
    if interaction_kind == "permission":
        return "allow_once"
    if interaction_kind == "diff_review":
        return "accept"
    return None


def is_fatal_error_event(value: Any) -> bool:
    """
    判断 QueryEngine 事件是否已经代表不可恢复失败。

    工作流：
    1. bridge 需要在流结束时兜底发 completed。
    2. 但如果最后一个事件已经是不可恢复 error，就不能再追加 completed。
    3. 这里只判断结构化 error 事件，不处理普通 recoverable 重试事件。
    """
    if not isinstance(value, dict):
        return False

    event_type = str(value.get("type", "") or "")
    if event_type != "error":
        return False

    return not bool(value.get("recoverable", False))


def normalize_agent_summary(value: dict[str, Any]) -> dict[str, Any]:
    """把 codo agent runtime event 转成右侧 Agent Team 卡片。"""
    agent_id = str(value.get("agent_id", "") or "")
    agent_type = str(value.get("agent_type", "") or "")
    mode = str(value.get("mode", "") or "")
    label = str(value.get("label", "") or "").strip()
    return {
        "agentId": agent_id,
        "label": label or agent_type or agent_id or "Agent",
        "agentType": agent_type,
        "mode": mode,
        "background": bool(value.get("background", False)),
        "status": normalize_agent_status(value.get("status")),
        "taskId": to_nullable_string(value.get("task_id", value.get("taskId"))),
        "currentAction": str(value.get("current_action", value.get("currentAction", "")) or ""),
        "resultPreview": str(value.get("result", value.get("result_preview", "")) or ""),
        "totalTokens": int(value.get("total_tokens", value.get("totalTokens", 0)) or 0),
    }


def normalize_agent_status(value: Any) -> str:
    """统一 agent 状态。"""
    if value in {"running", "completed", "error"}:
        return str(value)
    return "running"


def normalize_tool_summary(value: dict[str, Any], fallback_tool_name: Optional[str]) -> dict[str, Any]:
    """
    把 QueryEngine 的工具事件整理成前端可读结构。

    工作流：
    1. 优先读取 runtime 事件里的 tool_name。
    2. `tool_result` 事件没有 tool_name 时，用 content block 里记录的名称补齐。
    3. receipt 保持结构化，前端可以展开看命令、diff、agent 等细节。
    """
    receipt = value.get("receipt")
    tool_name = str(value.get("tool_name", "") or fallback_tool_name or "Tool")
    content = str(value.get("content", "") or "")
    summary = extract_receipt_summary(receipt) or content or f"{tool_name} 已完成"
    return {
        "toolUseId": str(value.get("tool_use_id", "") or ""),
        "name": tool_name,
        "status": normalize_tool_status(value.get("status")),
        "summary": summary,
        "detail": build_tool_detail(content, receipt),
        "receipt": normalize_receipt(receipt),
    }


def extract_receipt_summary(value: Any) -> str:
    """从 receipt 中提取一句摘要。"""
    if not isinstance(value, dict):
        return ""
    return str(value.get("summary", "") or "")


def normalize_tool_status(value: Any) -> str:
    """统一 tool 状态到 UI 约定值。"""
    if value in {"running", "completed", "error", "cancelled"}:
        return str(value)
    return "completed"


def normalize_receipt(value: Any) -> Optional[dict[str, Any]]:
    """把工具回执标准化成前端类型。"""
    if not isinstance(value, dict):
        return None

    kind = str(value.get("kind", "") or "")
    if kind == "command":
        return {
            "kind": "command",
            "summary": str(value.get("summary", "") or ""),
            "command": str(value.get("command", "") or ""),
            "cwd": str(value.get("cwd", "") or ""),
            "exitCode": int(value.get("exit_code", value.get("exitCode", 0)) or 0),
            "stdout": str(value.get("stdout", "") or ""),
            "stderr": str(value.get("stderr", "") or ""),
        }

    if kind == "diff":
        return {
            "kind": "diff",
            "summary": str(value.get("summary", "") or ""),
            "path": str(value.get("path", "") or ""),
            "diffText": str(value.get("diff_text", value.get("diffText", "")) or ""),
            "changeId": to_nullable_string(value.get("change_id", value.get("changeId"))),
        }

    if kind == "generic":
        return {
            "kind": "generic",
            "summary": str(value.get("summary", "") or ""),
            "body": str(value.get("body", "") or ""),
            "metadata": normalize_receipt_metadata(value.get("metadata")),
        }

    if kind == "agent":
        return {
            "kind": "agent",
            "summary": str(value.get("summary", "") or ""),
            "agentId": str(value.get("agent_id", value.get("agentId", "")) or ""),
            "agentType": str(value.get("agent_type", value.get("agentType", "")) or ""),
            "mode": str(value.get("mode", "") or ""),
            "taskId": to_nullable_string(value.get("task_id", value.get("taskId"))),
            "background": bool(value.get("background", False)),
            "status": str(value.get("status", "") or ""),
            "resultPreview": str(value.get("result_preview", value.get("resultPreview", "")) or ""),
            "totalTokens": int(value.get("total_tokens", value.get("totalTokens", 0)) or 0),
        }

    return {
        "kind": "unknown",
        "summary": str(value.get("summary", "工具回执") or "工具回执"),
        "body": json.dumps(value, ensure_ascii=False),
    }


def normalize_todo_items(value: Any) -> list[dict[str, Any]]:
    """把 todo 列表标准化成前端结构。"""
    if not isinstance(value, list):
        return []

    items: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        items.append(
            {
                "content": str(item.get("content", "") or ""),
                "activeForm": str(item.get("activeForm", "") or ""),
                "status": normalize_todo_status(item.get("status")),
            }
        )
    return items


def normalize_receipt_metadata(value: Any) -> dict[str, str | int | float | bool | None]:
    """
    把工具回执 metadata 收敛成前端协议允许的标量字典。

    工作流：
    1. Python 工具可以写入 path/count/truncated 等结构化事实。
    2. bridge 只允许字符串、数字、布尔值和 None 通过。
    3. 复杂对象不传给前端，避免 UI 层再做业务猜测。
    """
    if not isinstance(value, dict):
        return {}

    metadata: dict[str, str | int | float | bool | None] = {}
    for key, item in value.items():
        if isinstance(item, (str, int, float, bool)) or item is None:
            metadata[str(key)] = item
    return metadata


def normalize_todo_status(value: Any) -> str:
    """统一 todo 状态。"""
    if value in {"pending", "in_progress", "completed"}:
        return str(value)
    return "pending"


def build_tool_detail(content: Any, receipt: Any) -> str:
    """
    构造工具详情文本。

    工作流：
    1. 有 receipt 时，前端会用结构化 receipt 渲染展开详情。
    2. 此处不再把 content 重复塞进 detail，避免工具卡片展开后出现两份相同输出。
    3. 没有 receipt 的错误或旧事件，才保留 content 作为兜底详情。
    """
    if isinstance(receipt, dict):
        return ""

    if isinstance(content, str) and content.strip():
        return content.strip()

    return ""


def map_phase_to_status_message(phase: str) -> str:
    """把 Query 阶段映射成简洁的界面提示。"""
    mapping = {
        "prepare_turn": "正在准备本轮对话...",
        "stream_assistant": "AI 正在生成回复...",
        "execute_tools": "AI 正在执行工具...",
        "dispatch_tools": "AI 正在汇总工具结果...",
        "wait_interaction": "AI 正在等待交互处理...",
        "apply_interaction_result": "AI 正在应用交互结果...",
        "collect_tool_results": "AI 正在收集工具结果...",
        "compact": "AI 正在压缩上下文...",
        "stop_hooks": "AI 正在收尾...",
        "complete": "AI 轮次已完成。",
        "error": "AI 发生错误。",
        "interrupted": "AI 已被中断。",
    }
    return mapping.get(phase, f"AI 阶段：{phase}")


def to_nullable_string(value: Any) -> Optional[str]:
    """把值统一成可空字符串。"""
    if value is None:
        return None
    return str(value)


def to_non_empty_string(value: Any) -> Optional[str]:
    """把值转成非空字符串，空白内容返回 None。"""
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def normalize_session_id(value: Any) -> Optional[str]:
    """读取前端传入的 sessionId，空字符串视为新会话。"""
    if value is None:
        return None
    session_id = str(value).strip()
    return session_id if session_id else None


def extract_submit_turn_id(request: dict[str, Any]) -> Optional[str]:
    """
    从 submit 请求里读取 turnId。

    工作流：
    1. Electron 已经做过类型校验，但 Python bridge 仍是进程边界，需要再收敛一次。
    2. 空字符串代表非法请求，不能创建没有身份的后台任务。
    3. 返回 None 时调用方会发出终态 error，让前端释放输入状态。
    """
    turn_id = str(request.get("turnId", "") or "").strip()
    return turn_id if turn_id else None


def parse_permission_mode(value: Any, default_manual: bool) -> bool:
    """
    读取前端传入的权限模式。

    工作流：
    1. `manual` 表示工具授权交给右侧交互卡片处理。
    2. `auto` 表示普通工具授权自动放行，避免对话流程被频繁打断。
    3. 非法值使用当前 bridge 默认值；Electron 边界层会先做严格校验。
    """
    if value == "manual":
        return True
    if value == "auto":
        return False
    return default_manual


def to_nullable_int(value: Any) -> Optional[int]:
    """把值统一成可空整数。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def to_string_list(value: Any) -> list[str]:
    """把值统一成字符串列表。"""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def extract_int(value: Any, key: str) -> int:
    """从普通字典里提取整数，失败时返回 0。"""
    if not isinstance(value, dict):
        return 0
    parsed_value = to_nullable_int(value.get(key))
    return parsed_value if parsed_value is not None else 0


def extract_first_int(value: dict[str, Any], keys: list[str]) -> Optional[int]:
    """按优先级从 metadata 里提取第一个整数。"""
    for key in keys:
        parsed_value = to_nullable_int(value.get(key))
        if parsed_value is not None:
            return parsed_value
    return None


def extract_message_count(value: Any) -> Optional[int]:
    """从 turn_completed.metadata 里提取消息数量。"""
    if not isinstance(value, dict):
        return None
    return extract_first_int(value, ["message_count", "messages"])


def parse_float_env(name: str, default: float) -> float:
    """
    读取浮点型环境变量。

    工作流：
    1. 环境变量不存在时使用默认值。
    2. 环境变量无法解析时也使用默认值。
    3. 小于等于 0 的值视为非法，避免关闭超时保护。
    """
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    try:
        parsed_value = float(raw_value)
    except ValueError:
        return default
    return parsed_value if parsed_value > 0 else default


def parse_bool_env(name: str, default: bool) -> bool:
    """
    读取布尔型环境变量。

    工作流：
    1. 未配置时使用默认值。
    2. true/1/yes/on 表示开启。
    3. false/0/no/off 表示关闭。
    """
    raw_value = os.getenv(name, "").strip().lower()
    if not raw_value:
        return default
    if raw_value in {"true", "1", "yes", "on"}:
        return True
    if raw_value in {"false", "0", "no", "off"}:
        return False
    return default


async def main() -> None:
    """脚本入口。"""
    app = WorkbenchAiBridgeApp()
    await app.run()


if __name__ == "__main__":
    asyncio.run(main())
