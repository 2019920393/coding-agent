"""
StreamingToolExecutor - 流式工具执行器

核心功能：
- 在 API 流式响应期间并发执行工具
- 维护工具状态机：queued → executing → completed → yielded
- 强制并发规则：concurrent-safe 工具并行，unsafe 工具独占
- 增量返回结果：get_completed_results() 在流式期间返回
- 错误处理：sibling abort（Bash 错误取消并行工具）、synthetic errors
"""

import asyncio
import time
import logging
import os
from enum import Enum
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Dict, List, Optional, Any, Callable, AsyncGenerator

from codo.tools.receipts import CommandReceipt, GenericReceipt, receipt_to_dict, render_receipt_for_model
from codo.tools.base import ToolUseContext

logger = logging.getLogger(__name__)

def _serialize_ui_metadata(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_serialize_ui_metadata(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize_ui_metadata(item) for key, item in value.items()}
    return value

# ============================================================================
# 数据结构
# ============================================================================

class ToolStatus(str, Enum):
    """工具执行状态"""
    QUEUED = "queued"          # 已添加到队列，等待执行
    EXECUTING = "executing"    # 正在执行
    WAITING_INTERACTION = "waiting_interaction"  # 正在等待 UI 交互结果
    COMPLETED = "completed"    # 已完成（成功或失败）
    CANCELLED = "cancelled"    # 被取消
    FAILED = "failed"          # 执行失败
    INTERRUPTED = "interrupted"  # 被用户中断
    YIELDED = "yielded"        # 结果已返回给调用者

@dataclass
class TrackedTool:
    """跟踪单个工具的执行状态

    """
    id: str                                          # tool_use_id
    block: Dict[str, Any]                           # tool_use block
    assistant_message: Dict[str, Any]               # 包含此 tool_use 的 assistant message
    status: ToolStatus                              # 当前状态
    is_concurrency_safe: bool                       # 是否并发安全
    promise: Optional[asyncio.Task] = None          # 执行任务
    results: List[Dict[str, Any]] = field(default_factory=list)  # 工具结果消息
    pending_progress: List[Dict[str, Any]] = field(default_factory=list)  # 待返回的进度消息
    context_modifiers: List[Callable] = field(default_factory=list)  # Context 修改器
    duration: Optional[float] = None                # 执行时长（秒）
    start_time: Optional[float] = None              # 开始时间
    receipt: Optional[Any] = None                   # 结构化收据
    staged_changes: List[Any] = field(default_factory=list)
    audit_events: List[Any] = field(default_factory=list)
    result_summary: Optional[str] = None

@dataclass
class ToolUpdate:
    """工具执行更新

    用于从 StreamingToolExecutor 返回结果
    """
    message: Optional[Dict[str, Any]] = None        # 完整的消息对象
    context_modifier: Optional[Callable] = None     # Context 修改器
    tool_use_id: Optional[str] = None               # tool_use_id
    receipt: Optional[Any] = None                   # 结构化收据
    staged_changes: List[Any] = field(default_factory=list)
    audit_events: List[Any] = field(default_factory=list)
    content: Optional[str] = None                   # 结果内容
    is_error: bool = False                          # 是否是错误
    status: Optional[str] = None                    # 状态
    duration: Optional[float] = None                # 执行时长

# ============================================================================
# StreamingToolExecutor
# ============================================================================

class StreamingToolExecutor:
    """
    流式工具执行器

    在 API 流式响应期间并发执行工具，增量返回结果。

    核心特性：
    - 工具在 tool_use 块到达时立即开始执行
    - Concurrent-safe 工具可以并行执行
    - Non-concurrent 工具获得独占访问
    - 结果按添加顺序返回
    - Bash 错误触发 sibling abort（取消并行工具）
    """

    def __init__(
        self,
        tools: List[Any],
        context: Dict[str, Any] | ToolUseContext,
        max_concurrency: int = 10
    ):
        """
        初始化流式工具执行器

        Args:
            tools: 可用工具列表
            context: 执行上下文
            max_concurrency: 最大并发数
        """
        self.tools_registry = tools
        self.context = ToolUseContext.coerce(context)
        self.max_concurrency = max_concurrency

        # 跟踪的工具
        self.tools: List[TrackedTool] = []

        # 错误状态（用于 sibling abort）
        self.has_errored = False
        self.errored_tool_description = ""
        self.sibling_abort_event = asyncio.Event()

        # 丢弃标志（streaming fallback）
        self.discarded = False

        # 进度信号
        self._progress_available = asyncio.Event()

        logger.debug(f"StreamingToolExecutor initialized with {len(tools)} tools, max_concurrency={max_concurrency}")

    def add_tool(
        self,
        block: Dict[str, Any],
        assistant_message: Dict[str, Any]
    ) -> None:
        """
        添加工具到执行队列并尝试启动（立即执行版本）

        注意：此方法会立即尝试启动工具执行。
        如果工具 input 尚未完整（如在流式传输期间），请使用 register_tool() 代替。

        Args:
            block: tool_use block (包含 id, name, input)
            assistant_message: 包含此 tool_use 的 assistant message
        """
        # 注册工具
        self.register_tool(block, assistant_message)
        # 立即尝试启动执行（仅在有事件循环时）
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._process_queue())
        except RuntimeError:
            # 没有运行中的事件循环（如在同步测试中），跳过立即启动
            pass

    def register_tool(
        self,
        block: Dict[str, Any],
        assistant_message: Dict[str, Any]
    ) -> None:
        """
        仅注册工具到队列，不立即启动执行

        用于流式传输期间：工具在 content_block_start 时注册，
        等 final_message 拿到完整 input 后再统一调用 _process_queue() 启动。

        Args:
            block: tool_use block (包含 id, name, input)
            assistant_message: 包含此 tool_use 的 assistant message
        """
        logger.debug(f"Registering tool: {block.get('name')} (id={block.get('id')})")

        # 查找工具定义
        tool_def = self._find_tool(block["name"])
        if not tool_def:
            # 工具不存在，创建错误结果
            logger.warning(f"Tool not found: {block['name']}")
            tracked = TrackedTool(
                id=block["id"],
                block=block,
                assistant_message=assistant_message,
                status=ToolStatus.COMPLETED,
                is_concurrency_safe=False,
                results=[self._create_tool_not_found_error(block)]
            )
            self.tools.append(tracked)
            return

        # 检查并发安全性（此时 input 可能还是空 {}，但并发安全性通常基于工具类型）
        is_safe = self._check_concurrency_safety(tool_def, block.get("input", {}))

        # 创建跟踪工具，状态为 QUEUED（等待 input 回填后再启动）
        tracked = TrackedTool(
            id=block["id"],
            block=block,
            assistant_message=assistant_message,
            status=ToolStatus.QUEUED,
            is_concurrency_safe=is_safe,
        )

        self.tools.append(tracked)
        logger.debug(f"Tool registered (queued): {block['name']}, concurrency_safe={is_safe}")

    async def _process_queue(self) -> None:
        """
        处理队列，在并发允许时启动工具

        """
        for tool in self.tools:
            if tool.status != ToolStatus.QUEUED:
                continue

            if self._can_execute_tool(tool):
                await self._start_tool_execution(tool)
            elif not tool.is_concurrency_safe:
                # Non-concurrent 工具被阻塞 - 停止处理
                logger.debug(f"Queue processing stopped at non-concurrent tool: {tool.block['name']}")
                break

    def _can_execute_tool(self, tool: TrackedTool) -> bool:
        """
        检查工具是否可以执行

        并发规则：
        - 没有工具在执行 → 总是可以启动
        - 工具是 concurrent-safe 且所有执行中的工具都是 concurrent-safe → 可以启动
        - 否则 → 不能启动

        Args:
            tool: 要检查的工具

        Returns:
            是否可以执行
        """
        executing = [
            t for t in self.tools
            if t.status in {ToolStatus.EXECUTING, ToolStatus.WAITING_INTERACTION}
        ]

        # 没有工具在执行 - 总是可以启动
        if not executing:
            return True

        # 工具是 concurrent-safe 且所有执行中的工具都是 concurrent-safe
        if tool.is_concurrency_safe and all(t.is_concurrency_safe for t in executing):
            # 检查并发数限制
            if len(executing) < self.max_concurrency:
                return True

        return False

    async def _start_tool_execution(self, tool: TrackedTool) -> None:
        """
        启动工具执行

        Args:
            tool: 要执行的工具
        """
        tool.status = ToolStatus.EXECUTING
        tool.start_time = time.time()
        logger.debug(f"Starting tool execution: {tool.block['name']} (id={tool.id})")
        await self._emit_runtime_event(
            "tool_started",
            tool_use_id=tool.id,
            tool_name=tool.block.get("name", "Tool"),
            input_preview=self._build_tool_activity_summary(
                tool.block.get("name", "Tool"),
                tool.block.get("input", {}) or {},
            ),
            status="running",
        )

        tool.promise = asyncio.create_task(self._execute_tool_with_abort(tool))

        # 完成后继续处理队列
        def on_done(task):
            asyncio.create_task(self._process_queue())

        tool.promise.add_done_callback(on_done)

    async def _execute_tool_with_abort(self, tool: TrackedTool) -> None:
        """
        执行工具，监控 sibling abort

        Args:
            tool: 要执行的工具
        """
        # 检查是否已经应该中止
        if self._should_abort(tool):
            reason = self._get_abort_reason()
            logger.debug(f"Tool aborted before execution: {tool.block['name']}, reason={reason}")
            tool.results = [self._create_synthetic_error(tool, reason)]
            tool.status = ToolStatus.COMPLETED
            tool.duration = 0
            await self._emit_runtime_event(
                "tool_completed",
                tool_use_id=tool.id,
                tool_name=tool.block.get("name", "Tool"),
                status="interrupted" if reason == "user_interrupted" else "cancelled",
                content=reason,
            )
            return

        # 执行工具，同时监控 abort 事件
        execute_task = asyncio.create_task(self._execute_tool(tool))
        abort_task = asyncio.create_task(self.sibling_abort_event.wait())

        done, pending = await asyncio.wait(
            [execute_task, abort_task],
            return_when=asyncio.FIRST_COMPLETED
        )

        # 取消未完成的任务
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # 如果是 abort 触发的完成，生成 synthetic error
        if abort_task in done and tool.status != ToolStatus.COMPLETED:
            logger.debug(f"Tool aborted during execution: {tool.block['name']}")
            tool.results = [self._create_synthetic_error(tool, "sibling_error")]
            tool.status = ToolStatus.COMPLETED
            tool.duration = time.time() - tool.start_time if tool.start_time else 0
            await self._emit_runtime_event(
                "tool_completed",
                tool_use_id=tool.id,
                tool_name=tool.block.get("name", "Tool"),
                status="cancelled",
                content="sibling_error",
            )

    async def _execute_tool(self, tool: TrackedTool) -> None:
        """
        执行单个工具

        Args:
            tool: 要执行的工具
        """
        try:
            # 检查 abort 状态
            if self.sibling_abort_event.is_set():
                logger.debug(f"Tool execution cancelled due to sibling abort: {tool.block['name']}")
                tool.results = [self._create_synthetic_error(tool, "sibling_error")]
                tool.status = ToolStatus.COMPLETED
                tool.duration = time.time() - tool.start_time if tool.start_time else 0
                return

            # 查找工具实例
            tool_instance = self._find_tool(tool.block["name"])
            if not tool_instance:
                raise ValueError(f"Tool not found: {tool.block['name']}")

            # 解析输入（Pydantic schema 验证）
            try:
                tool_input = tool_instance.input_schema(**tool.block.get("input", {}))
            except Exception as e:
                # Pydantic 验证失败，返回友好错误给模型
                error_message = f"Invalid tool input: {e}"
                logger.warning(f"Tool input schema validation failed: {tool.block['name']}, error={error_message}")
                tool.results = [self._format_tool_error(tool, ValueError(error_message))]
                tool.status = ToolStatus.COMPLETED
                tool.duration = time.time() - tool.start_time if tool.start_time else 0
                return

            # 自定义输入验证
            if hasattr(tool_instance, "validate_input"):
                validation_result = await tool_instance.validate_input(tool_input, self.context)
                if not validation_result.result:
                    error_message = validation_result.message or "Input validation failed"
                    logger.warning(f"Tool input validation failed: {tool.block['name']}, error={error_message}")
                    tool.results = [self._format_tool_error(tool, ValueError(error_message))]
                    tool.status = ToolStatus.COMPLETED
                    tool.duration = time.time() - tool.start_time if tool.start_time else 0
                    return

            # ================================================================
            # 权限检查
            # 在 validate_input 之后、call() 之前执行
            # ================================================================
            permission_decision = await self._check_tool_permission(
                tool_instance, tool_input, tool
            )

            if permission_decision == "deny":
                # 权限被拒绝，返回错误给模型
                tool.results = [self._format_tool_error(
                    tool, PermissionError(f"Permission denied for {tool.block['name']}")
                )]
                tool.status = ToolStatus.COMPLETED
                tool.duration = time.time() - tool.start_time if tool.start_time else 0
                return

            if permission_decision == "abort":
                # 用户选择中止整个查询
                logger.info(f"[Permission] User aborted query during {tool.block['name']} permission check")
                tool.results = [self._format_tool_error(
                    tool, PermissionError("User aborted the query")
                )]
                tool.status = ToolStatus.COMPLETED
                tool.duration = time.time() - tool.start_time if tool.start_time else 0
                # 触发 sibling abort，停止其他并行工具
                self.sibling_abort_event.set()
                return

            # permission_decision == "allow"，继续执行

            # 执行工具
            logger.debug(f"Executing tool: {tool.block['name']}")

            # 进度回调
            def on_progress(progress):
                # 将进度消息添加到 pending_progress
                # progress 可能是字典或其他类型，统一处理
                data = progress.get("data") if isinstance(progress, dict) else progress
                tool.pending_progress.append({
                    "type": "progress",
                    "data": data,
                })
                self._progress_available.set()
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(
                        self._emit_runtime_event(
                            "tool_progress",
                            tool_use_id=tool.id,
                            tool_name=tool.block.get("name", "Tool"),
                            progress=str(data or ""),
                        )
                    )
                except RuntimeError:
                    pass

            # 调用工具
            try:
                result = await tool_instance.call(
                    tool_input,
                    self.context,
                    self.context.get("can_use_tool"),  # 权限检查回调
                    tool.assistant_message,             # 父消息
                    on_progress,                        # 进度回调
                )
            except TypeError:
                # 兼容简化工具签名：call(input, context)
                result = await tool_instance.call(tool_input, self.context)

            # 检查是否是错误
            is_error = result.error is not None
            tool.staged_changes = list(getattr(result, "staged_changes", []) or [])
            tool.audit_events = list(getattr(result, "audit_events", []) or [])
            if not is_error and tool.staged_changes:
                tool.receipt = await self._finalize_staged_changes(tool, tool.staged_changes)
            else:
                tool.receipt = self._build_tool_receipt(tool.block["name"], result)
            tool.result_summary = (
                getattr(tool.receipt, "summary", None)
                if tool.receipt is not None
                else self._extract_tool_content(tool.block["name"], result.data)
            )

            # 处理 Bash 错误（sibling abort）

            # Bash 命令有隐式依赖链（如 mkdir 失败 → 后续命令无意义），所以 Bash 错误取消并行工具
            if is_error and tool.block["name"] == "Bash":
                logger.debug(
                    f"Bash tool errored (error={result.error!r}), triggering sibling abort"
                )
                self.has_errored = True
                self.errored_tool_description = self._get_tool_description(tool)
                self.sibling_abort_event.set()

            # 存储结果
            tool.results = [self._format_tool_result(tool, result)]
            tool.status = ToolStatus.COMPLETED
            tool.duration = time.time() - tool.start_time if tool.start_time else 0

            logger.debug(f"Tool completed: {tool.block['name']}, duration={tool.duration:.2f}s, error={is_error}")
            await self._emit_runtime_event(
                "tool_completed",
                tool_use_id=tool.id,
                tool_name=tool.block.get("name", "Tool"),
                status="error" if is_error else "completed",
                content=tool.result_summary or "",
                receipt=receipt_to_dict(tool.receipt) if tool.receipt is not None else None,
                audit_events=_serialize_ui_metadata(tool.audit_events),
            )
            if tool.block.get("name") == "TodoWrite":
                options = self.context.get_options()
                app_state = options.get("app_state", {}) if isinstance(options, dict) else {}
                todos = app_state.get("todos", {}) if isinstance(app_state, dict) else {}
                todo_key = str(
                    options.get("agent_id")
                    or self.context.get("agent_id")
                    or options.get("session_id")
                    or self.context.get("session_id")
                    or ""
                )
                if todo_key:
                    await self._emit_runtime_event(
                        "todo_updated",
                        key=todo_key,
                        items=_serialize_ui_metadata(todos.get(todo_key, [])),
                        tool_use_id=tool.id,
                    )

            # Context modifiers（仅 non-concurrent 工具）
            # 暂不实现

        except Exception as e:
            # 处理错误
            logger.error(f"Tool execution failed: {tool.block['name']}, error={str(e)}")

            if tool.block["name"] == "Bash":
                self.has_errored = True
                self.errored_tool_description = self._get_tool_description(tool)
                self.sibling_abort_event.set()

            tool.results = [self._format_tool_error(tool, e)]
            tool.status = ToolStatus.COMPLETED
            tool.duration = time.time() - tool.start_time if tool.start_time else 0
            await self._emit_runtime_event(
                "tool_completed",
                tool_use_id=tool.id,
                tool_name=tool.block.get("name", "Tool"),
                status="error",
                content=str(e),
            )

    def get_completed_results(self) -> List[ToolUpdate]:
        """
        获取已完成的结果（非阻塞）

        规则：
        - 立即返回进度消息
        - 按添加顺序返回已完成的工具
        - 在第一个未完成的 non-concurrent 工具处停止

        Returns:
            已完成的工具更新列表
        """
        if self.discarded:
            return []

        results = []

        for tool in self.tools:
            # 立即返回进度消息
            while tool.pending_progress:
                results.append(ToolUpdate(message=tool.pending_progress.pop(0)))

            if tool.status == ToolStatus.YIELDED:
                continue

            if tool.status == ToolStatus.COMPLETED and tool.results:
                tool.status = ToolStatus.YIELDED

                for message in tool.results:
                    content = message["content"][0]["content"]
                    is_error = message["content"][0].get("is_error", False)

                    results.append(ToolUpdate(
                        message=message,
                        tool_use_id=tool.id,
                        receipt=tool.receipt,
                        staged_changes=list(tool.staged_changes),
                        audit_events=list(tool.audit_events),
                        content=tool.result_summary or content,
                        is_error=is_error,
                        status="completed",
                        duration=tool.duration,
                    ))

                logger.debug(f"Yielded result for tool: {tool.block['name']}")

            # 在第一个未完成的 non-concurrent 工具处停止
            elif tool.status in {ToolStatus.EXECUTING, ToolStatus.WAITING_INTERACTION} and not tool.is_concurrency_safe:
                logger.debug(f"Stopped yielding at executing non-concurrent tool: {tool.block['name']}")
                break

        return results

    async def get_remaining_results(self) -> AsyncGenerator[ToolUpdate, None]:
        """
        等待剩余工具完成并返回结果

        Yields:
            工具更新
        """
        if self.discarded:
            return

        logger.debug("Getting remaining results...")

        while self._has_unfinished_tools():
            # 处理队列
            await self._process_queue()

            # 返回已完成的结果
            for result in self.get_completed_results():
                yield result

            # 等待下一个完成
            if self._has_executing_tools() and not self._has_completed_unyielded():
                executing_tasks = [
                    t.promise for t in self.tools
                    if t.status in {ToolStatus.EXECUTING, ToolStatus.WAITING_INTERACTION} and t.promise
                ]

                if executing_tasks:
                    # 等待任意任务完成
                    await asyncio.wait(
                        executing_tasks,
                        return_when=asyncio.FIRST_COMPLETED
                    )

        # 最终返回
        for result in self.get_completed_results():
            yield result

        logger.debug("All results yielded")

    def discard(self) -> None:
        """
        标记执行器为已丢弃（streaming fallback）

        """
        logger.warning("StreamingToolExecutor discarded (streaming fallback)")
        self.discarded = True

        # 取消所有执行中的任务
        for tool in self.tools:
            if tool.promise and not tool.promise.done():
                tool.promise.cancel()

    # ========== 辅助方法 ==========

    async def _emit_runtime_event(self, event_type: str, **payload: Any) -> None:
        runtime_controller = self.context.get("runtime_controller")
        if runtime_controller is None:
            return
        emit = getattr(runtime_controller, "emit_runtime_event", None)
        if callable(emit):
            await emit(event_type, **payload)

    async def _transition_phase(self, phase: str, **kwargs: Any) -> None:
        tracker = self.context.get("phase_tracker")
        if tracker is None:
            return
        transition = getattr(tracker, "transition", None)
        if callable(transition):
            await transition(phase, **kwargs)

    def _active_tool_ids(self) -> List[str]:
        return [
            tool.id
            for tool in self.tools
            if tool.status in {
                ToolStatus.QUEUED,
                ToolStatus.EXECUTING,
                ToolStatus.WAITING_INTERACTION,
            }
        ]

    async def _request_runtime_interaction(
        self,
        tool: TrackedTool,
        request: Any,
        interaction_broker: Any,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Any:
        serialized_request = _serialize_ui_metadata(request)
        tool.status = ToolStatus.WAITING_INTERACTION
        await self._transition_phase(
            "wait_interaction",
            pending_interaction=serialized_request,
            active_tool_ids=self._active_tool_ids(),
            resume_target=tool.id,
            metadata=metadata or {},
        )
        try:
            return await interaction_broker.request(request)
        finally:
            tool.status = ToolStatus.EXECUTING
            await self._transition_phase(
                "apply_interaction_result",
                pending_interaction=serialized_request,
                active_tool_ids=self._active_tool_ids(),
                resume_target=tool.id,
                metadata=metadata or {},
            )
            await self._transition_phase(
                "execute_tools",
                pending_interaction=None,
                active_tool_ids=self._active_tool_ids(),
                resume_target=tool.id,
                metadata={"tool_id": tool.id, **(metadata or {})},
            )

    async def _collect_user_answers(self, tool_input: Any) -> Optional[Dict[str, str]]:
        """
        在终端显示问题并收集用户答案

        [Workflow]
        1. 遍历所有问题
        2. 显示问题标题和选项
        3. 用户输入选项编号或自定义文本
        4. 收集所有答案
        5. 返回 {question_text: answer} 字典

        Args:
            tool_input: AskUserQuestionInput（含 questions 列表）

        Returns:
            答案字典，或 None（用户拒绝）
        """
        questions = getattr(tool_input, "questions", [])
        if not questions:
            return {}
        interaction_broker = self.context.get("interaction_broker")
        if interaction_broker is None:
            raise RuntimeError("interaction_broker is required for AskUserQuestion")

        from codo.cli.tui.interaction_types import InteractionOption, InteractionQuestion, InteractionRequest

        interaction_questions: List[InteractionQuestion] = []
        for index, question in enumerate(questions, 1):
            raw_options = getattr(question, "options", None)
            if raw_options is None and isinstance(question, dict):
                raw_options = question.get("options", [])
            options = []
            for option in list(raw_options or []):
                label = getattr(option, "label", None)
                if label is None and isinstance(option, dict):
                    label = option.get("label", "")
                description = getattr(option, "description", None)
                if description is None and isinstance(option, dict):
                    description = option.get("description", "")
                preview = getattr(option, "preview", None)
                if preview is None and isinstance(option, dict):
                    preview = option.get("preview", "")
                options.append(
                    InteractionOption(
                        value=str(label or ""),
                        label=str(label or ""),
                        description=str(description or ""),
                        preview=str(preview or ""),
                    )
                )

            header = getattr(question, "header", None)
            if header is None and isinstance(question, dict):
                header = question.get("header", f"Question {index}")
            prompt = getattr(question, "question", None)
            if prompt is None and isinstance(question, dict):
                prompt = question.get("question", f"Question {index}?")
            multi_select = bool(getattr(question, "multi_select", False) or getattr(question, "multiSelect", False))
            if isinstance(question, dict):
                multi_select = multi_select or bool(question.get("multi_select", False) or question.get("multiSelect", False))

            interaction_questions.append(
                InteractionQuestion(
                    question_id=f"question-{index}",
                    header=str(header or f"Question {index}"),
                    question=str(prompt or f"Question {index}?"),
                    options=options,
                    multi_select=multi_select,
                )
            )

        request = InteractionRequest(
            request_id=f"req_question_{int(time.time() * 1000)}",
            kind="question",
            label=interaction_questions[0].header if interaction_questions else "Waiting for your answer",
            questions=interaction_questions,
        )
        tracked_tool = next(
            (
                tool
                for tool in self.tools
                if tool.block.get("name") == "AskUserQuestion"
                and tool.status in {ToolStatus.EXECUTING, ToolStatus.WAITING_INTERACTION}
            ),
            None,
        )
        if tracked_tool is None:
            return await interaction_broker.request(request)
        return await self._request_runtime_interaction(
            tracked_tool,
            request,
            interaction_broker,
            metadata={"kind": "question"},
        )

    async def _check_tool_permission(
        self,
        tool_instance: Any,
        tool_input: Any,
        tool: "TrackedTool",
    ) -> str:
        """
        检查工具权限

        [Workflow]
        1. 调用 has_permissions_to_use_tool 获取权限决策
        2. allow → 返回 "allow"
        3. deny → 返回 "deny"
        4. ask → 弹出终端权限提示，根据用户选择返回 "allow"/"deny"/"abort"
           - allow_once → "allow"
           - allow_always → 添加会话级规则，返回 "allow"
           - deny → "deny"
           - abort → "abort"

        Args:
            tool_instance: 工具实例
            tool_input: 已验证的工具输入（Pydantic 模型）
            tool: TrackedTool 对象

        Returns:
            "allow" | "deny" | "abort"
        """
        try:
            from codo.services.tools.permission_checker import has_permissions_to_use_tool
            from codo.types.permissions import PermissionMode

            # 获取权限上下文
            permission_context = self.context.get("permission_context")
            if not permission_context:
                # 没有权限上下文，默认允许（向后兼容）
                logger.debug(f"[Permission] No permission_context, allowing {tool_instance.name}")
                return "allow"

            # 单独保留字典视图，供权限提示文案和工具摘要使用。
            if hasattr(tool_input, "model_dump"):
                input_dict = tool_input.model_dump()
            elif isinstance(tool_input, dict):
                input_dict = tool_input
            else:
                input_dict = {}

            # 调用权限检查
            decision = await has_permissions_to_use_tool(
                tool_instance,
                tool_input,
                self.context,
            )

            behavior = decision.behavior

            if behavior == "allow":
                logger.debug(f"[Permission] Allowed: {tool_instance.name}")
                return "allow"

            elif behavior == "deny":
                logger.info(f"[Permission] Denied: {tool_instance.name} - {getattr(decision, 'message', '')}")
                return "deny"

            elif behavior == "ask":
                if tool_instance.name in {"Write", "Edit"}:
                    logger.debug(
                        "[Permission] Deferring %s approval to staged diff review",
                        tool_instance.name,
                    )
                    return "allow"

                # AskUserQuestion 工具特殊处理：直接显示问题并收集答案

                from codo.tools.ask_user_question_tool.constants import ASK_USER_QUESTION_TOOL_NAME
                if tool_instance.name == ASK_USER_QUESTION_TOOL_NAME:
                    answers = await self._collect_user_answers(tool_input)
                    if answers is None:
                        # 用户拒绝回答
                        logger.info(f"[Permission] User declined to answer questions")
                        return "deny"
                    # 把答案注入到 tool_input
                    tool_input.answers = answers
                    logger.info(f"[Permission] User answered questions: {list(answers.keys())}")
                    return "allow"

                # 普通工具：改为通过 runtime interaction broker 请求 UI 交互
                interaction_broker = self.context.get("interaction_broker")
                if interaction_broker is None:
                    raise RuntimeError("interaction_broker is required for permission prompts")

                from codo.cli.tui.interaction_types import InteractionOption, InteractionRequest
                from codo.services.tools.permission_prompt import (
                    PermissionChoice,
                    apply_session_allow_rule,
                    format_tool_info,
                )

                message = getattr(decision, "message", "")
                choice = await self._request_runtime_interaction(
                    tool,
                    InteractionRequest(
                        request_id=f"req_permission_{tool.id}",
                        kind="permission",
                        label=f"Awaiting approval for {tool_instance.name}",
                        tool_name=tool_instance.name,
                        tool_info=format_tool_info(tool_instance.name, input_dict),
                        message=message,
                        options=[
                            InteractionOption(value="allow_once", label="Allow Once"),
                            InteractionOption(value="allow_always", label="Allow Session"),
                            InteractionOption(value="deny", label="Deny"),
                            InteractionOption(value="abort", label="Abort"),
                        ],
                    ),
                    interaction_broker,
                    metadata={
                        "kind": "permission",
                        "tool_name": tool_instance.name,
                    },
                )

                if choice == PermissionChoice.ALLOW_ONCE:
                    logger.info(f"[Permission] User allowed once: {tool_instance.name}")
                    return "allow"

                elif choice == PermissionChoice.ALLOW_ALWAYS:
                    # 添加会话级允许规则
                    apply_session_allow_rule(permission_context, tool_instance.name)
                    logger.info(f"[Permission] User allowed always (session): {tool_instance.name}")
                    return "allow"

                elif choice == PermissionChoice.DENY:
                    logger.info(f"[Permission] User denied: {tool_instance.name}")
                    return "deny"

                else:  # ABORT
                    logger.info(f"[Permission] User aborted: {tool_instance.name}")
                    return "abort"

            else:
                # 未知决策，默认允许
                logger.warning(f"[Permission] Unknown decision behavior '{behavior}', allowing")
                return "allow"

        except Exception as e:
            # 权限检查失败时中止本次调用，避免在交互异常时误放行高风险工具
            logger.error(
                f"[Permission] Permission check failed for {tool_instance.name}: {e}, aborting tool call"
            )
            return "abort"

    def _find_tool(self, name: str) -> Optional[Any]:
        """根据名称查找工具"""
        for tool in self.tools_registry:
            if tool.name == name:
                return tool
        return None

    def _check_concurrency_safety(self, tool: Any, input_data: Dict) -> bool:
        """检查工具是否并发安全"""
        try:
            if hasattr(tool, 'is_concurrency_safe'):
                if callable(tool.is_concurrency_safe):
                    try:
                        return bool(tool.is_concurrency_safe(input_data))
                    except TypeError:
                        return bool(tool.is_concurrency_safe())
                return tool.is_concurrency_safe
            return False
        except:
            return False

    def _get_tool_description(self, tool: TrackedTool) -> str:
        """获取工具的可读描述"""
        input_data = tool.block.get("input", {})
        summary = (
            input_data.get("command") or
            input_data.get("file_path") or
            input_data.get("pattern") or
            input_data.get("url") or
            ""
        )
        if summary and len(summary) > 40:
            summary = summary[:40] + "…"
        return f"{tool.block['name']}({summary})" if summary else tool.block['name']

    def _should_abort(self, tool: TrackedTool) -> bool:
        """检查工具是否应该中止"""
        # 只有 concurrent-safe 工具会被 sibling abort 影响
        return self.discarded or (self.has_errored and tool.is_concurrency_safe)

    def _get_abort_reason(self) -> str:
        """获取中止原因"""
        if self.discarded:
            return "streaming_fallback"
        if self.has_errored:
            return "sibling_error"
        return "user_interrupted"

    def _create_synthetic_error(
        self,
        tool: TrackedTool,
        reason: str
    ) -> Dict[str, Any]:
        """
        创建 synthetic error 消息

        """
        if reason == "sibling_error":
            desc = self.errored_tool_description
            msg = f"Cancelled: parallel tool call {desc} errored" if desc else "Cancelled: parallel tool call errored"
        elif reason == "user_interrupted":
            msg = "User interrupted tool execution"
        else:
            msg = "Streaming fallback - tool execution discarded"

        return {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool.id,
                "content": f"<tool_use_error>{msg}</tool_use_error>",
                "is_error": True,
                "receipt": None,
                "audit_events": [],
            }],
        }

    def _create_tool_not_found_error(self, block: Dict[str, Any]) -> Dict[str, Any]:
        """创建工具未找到错误"""
        return {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": block["id"],
                "content": f"<tool_use_error>Tool '{block['name']}' not found</tool_use_error>",
                "is_error": True,
                "receipt": None,
                "audit_events": [],
            }],
        }

    def _format_tool_result(self, tool: TrackedTool, result: Any) -> Dict[str, Any]:
        """
        格式化工具结果为消息

        对不同工具的输出做有意义的格式化，而不是直接 str(result.data)
        """
        if result.error:

            content = f"<tool_use_error>Error: {result.error}</tool_use_error>"
            is_error = True
        else:
            if tool.receipt is not None:
                content = render_receipt_for_model(tool.receipt, tool.id)["content"]
            else:
                # 根据工具类型提取有意义的内容
                content = self._extract_tool_content(tool.block["name"], result.data)
            is_error = False

        return {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool.id,
                "content": content,
                "is_error": is_error,
                "receipt": receipt_to_dict(tool.receipt) if tool.receipt is not None else None,
                "audit_events": _serialize_ui_metadata(tool.audit_events),
            }],
        }

    def _build_tool_receipt(self, tool_name: str, result: Any) -> Optional[Any]:
        receipt = getattr(result, "receipt", None)
        if receipt is not None:
            return receipt
        if getattr(result, "error", None):
            return None
        return self._build_default_receipt(tool_name, result.data)

    def _build_tool_activity_summary(self, tool_name: str, input_data: Dict[str, Any]) -> str:
        key = str(tool_name or "").strip().lower()

        if key == "read":
            file_path = str(input_data.get("file_path", "") or input_data.get("filePath", "") or "")
            filename = os.path.basename(file_path.replace("\\", "/")) or file_path or "file"
            partial = input_data.get("offset") is not None or input_data.get("limit") is not None
            return f"Reading {filename}{' (partial)' if partial else ''}"

        if key == "glob":
            pattern = str(input_data.get("pattern", "") or "").strip()
            search_path = str(input_data.get("path", "") or "").strip()
            target = os.path.basename(search_path.replace("\\", "/").rstrip("/")) or search_path or "workspace"
            if pattern:
                return f"Scanning {target} for {pattern}"
            return f"Scanning {target}"

        if key == "grep":
            pattern = str(input_data.get("pattern", "") or "").strip()
            search_path = str(input_data.get("path", "") or "").strip()
            target = os.path.basename(search_path.replace("\\", "/").rstrip("/")) or search_path or "workspace"
            if pattern:
                return f"Searching {target} for {pattern}"
            return f"Searching {target}"

        if key == "bash":
            description = str(input_data.get("description", "") or "").strip()
            command = str(input_data.get("command", "") or "").strip()
            summary = description or command
            if len(summary) > 60:
                summary = summary[:57].rstrip() + "..."
            return f"Running {summary}" if summary else "Running command"

        return str(input_data or "")

    def _build_default_receipt(self, tool_name: str, data: Any) -> Optional[Any]:
        key = str(tool_name or "").strip().lower()

        if key == "bash":
            if data is None:
                return None
            background = bool(getattr(data, "background", False))
            task_id = str(getattr(data, "taskId", "") or "")
            command = str(getattr(data, "command", "") or "")
            exit_code = int(getattr(data, "exitCode", 0) or 0)
            stdout = str(getattr(data, "stdout", "") or "")
            stderr = str(getattr(data, "stderr", "") or "")
            if background and task_id:
                return GenericReceipt(
                    kind="generic",
                    summary=f"Started background task {task_id}",
                    body=command,
                )
            summary = command or "Command finished"
            if len(summary) > 60:
                summary = summary[:57].rstrip() + "..."
            return CommandReceipt(
                kind="command",
                summary=summary,
                command=command,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
            )

        if key == "read":
            file_path = str(getattr(data, "filePath", "") or "")
            filename = os.path.basename(file_path.replace("\\", "/")) or file_path or "file"
            partial = bool(getattr(data, "isPartial", False))
            summary = f"Read {filename}{' (partial)' if partial else ''}"
            return GenericReceipt(
                kind="generic",
                summary=summary,
                body=self._extract_tool_content(tool_name, data),
            )

        if key == "glob":
            num_files = int(getattr(data, "numFiles", 0) or 0)
            truncated = bool(getattr(data, "truncated", False))
            summary = f"Matched {num_files} files" if num_files else "Matched files"
            if truncated:
                summary = f"{summary} (truncated)"
            return GenericReceipt(
                kind="generic",
                summary=summary,
                body=self._extract_tool_content(tool_name, data),
            )

        if key == "grep":
            num_matches = int(getattr(data, "numMatches", 0) or 0)
            truncated = bool(getattr(data, "truncated", False))
            summary = f"Found {num_matches} matches" if num_matches else "Found matches"
            if truncated:
                summary = f"{summary} (truncated)"
            return GenericReceipt(
                kind="generic",
                summary=summary,
                body=self._extract_tool_content(tool_name, data),
            )

        if key == "todowrite":
            return GenericReceipt(
                kind="generic",
                summary="Updated todo list",
                body=self._extract_tool_content(tool_name, data),
            )

        if key == "skill":
            command_name = str(getattr(data, "commandName", "") or tool_name)
            prompt_text = str(getattr(data, "prompt", "") or "").strip()
            description = str(getattr(data, "description", "") or "").strip()
            source_path = str(getattr(data, "sourcePath", "") or "").strip()
            allowed_tools = list(getattr(data, "allowedTools", []) or [])
            model_name = str(getattr(data, "model", "") or "").strip()
            body_parts: list[str] = []
            if description:
                body_parts.append(description)
            if allowed_tools:
                body_parts.append(f"Preferred tools: {', '.join(allowed_tools)}")
            if model_name:
                body_parts.append(f"Preferred model: {model_name}")
            if source_path:
                body_parts.append(f"Source: {source_path}")
            if prompt_text:
                body_parts.append(prompt_text)
            return GenericReceipt(
                kind="generic",
                summary=f"Loaded skill /{command_name}",
                body="\n\n".join(part for part in body_parts if part).strip(),
            )

        summary = self._extract_tool_content(tool_name, data)
        return GenericReceipt(kind="generic", summary=summary, body=summary)

    async def _finalize_staged_changes(self, tool: TrackedTool, staged_changes: List[Any]) -> Optional[Any]:
        """
        审阅并提交 staged changes，返回最终收据。

        这一步会把工具自己的 "Prepared ..." 中间态收据替换成
        "Applied ..." 或 "Rejected ..." 的最终结果，确保 query/model/UI
        看到的是同一份最终状态。
        """
        if not staged_changes:
            return None

        from codo.services.tools.execution_manager import ExecutionManager
        from codo.cli.tui.interaction_types import InteractionOption, InteractionRequest

        manager = ExecutionManager()
        receipts: List[Any] = []
        interaction_broker = self.context.get("interaction_broker")
        if interaction_broker is None:
            raise RuntimeError("interaction_broker is required for staged change review")

        for change in staged_changes:
            decision = await self._request_runtime_interaction(
                tool,
                InteractionRequest(
                    request_id=f"req_diff_{change.change_id}",
                    kind="diff_review",
                    label=f"Review {change.path}",
                    message="Apply these changes?",
                    options=[
                        InteractionOption(value="accept", label="Accept"),
                        InteractionOption(value="reject", label="Reject"),
                    ],
                    payload={
                        "change_id": change.change_id,
                        "path": change.path,
                        "diff_text": change.diff_text,
                        "original_content": change.original_content,
                        "new_content": change.new_content,
                    },
                ),
                interaction_broker,
                metadata={
                    "kind": "diff_review",
                    "change_id": change.change_id,
                    "path": change.path,
                },
            )
            if decision == "accept":
                receipts.append(await manager.apply_staged_change(change))
            else:
                receipts.append(await manager.reject_staged_change(change))

        if len(receipts) == 1:
            return receipts[0]

        summary = "; ".join(
            getattr(receipt, "summary", "") for receipt in receipts if getattr(receipt, "summary", "")
        ).strip()
        body = "\n\n".join(
            f"{getattr(receipt, 'path', '')}\n{getattr(receipt, 'diff_text', '')}".strip()
            for receipt in receipts
        ).strip()
        return GenericReceipt(
            kind="generic",
            summary=summary or "Reviewed staged changes",
            body=body,
        )

    def _extract_tool_content(self, tool_name: str, data: Any) -> str:
        """
        从工具输出中提取有意义的内容字符串

        不同工具有不同的展示格式

        Args:
            tool_name: 工具名称
            data: 工具输出数据（Pydantic 模型或其他类型）

        Returns:
            格式化后的字符串内容
        """
        if data is None:
            return ""

        # 如果是字符串，直接返回
        if isinstance(data, str):
            return data

        # 根据工具名称做专门处理
        if tool_name == "Bash":
            # Bash: 显示 stdout + stderr
            stdout = getattr(data, "stdout", "") or ""
            stderr = getattr(data, "stderr", "") or ""
            exit_code = getattr(data, "exitCode", 0)
            parts = []
            if stdout:
                parts.append(stdout)
            if stderr:
                parts.append(f"<stderr>\n{stderr}\n</stderr>")
            if not parts:
                parts.append(f"(exit code: {exit_code})")
            return "\n".join(parts)

        elif tool_name == "Read":
            # Read: 返回文件内容
            return getattr(data, "content", str(data))

        elif tool_name in ("Write", "Edit"):
            # Write/Edit: 显示简洁的成功消息
            file_path = getattr(data, "filePath", "") or getattr(data, "file_path", "")
            if tool_name == "Write":
                content = getattr(data, "content", "")
                num_lines = content.count("\n") + 1 if content else 0
                return f"Wrote {num_lines} lines to {file_path}"
            else:
                diff = getattr(data, "diff", "")
                return f"Updated {file_path}\n{diff}" if diff else f"Updated {file_path}"

        elif tool_name in ("Glob", "Grep"):
            # Glob/Grep: 返回文件列表或匹配结果
            if hasattr(data, "filenames"):
                files = data.filenames or []
                return "\n".join(files) if files else "(no matches)"
            if hasattr(data, "files"):
                files = data.files or []
                return "\n".join(files) if files else "(no matches)"
            if hasattr(data, "matches"):
                matches = data.matches or []
                return "\n".join(str(m) for m in matches) if matches else "(no matches)"
            return str(data)

        elif tool_name == "Agent":
            # Agent: 返回子代理的结果文本
            return getattr(data, "result", str(data))

        elif tool_name == "TodoWrite":
            # TodoWrite: 返回成功消息
            return (
                "Todos have been modified successfully. "
                "Ensure that you continue to use the todo list to track your progress. "
                "Please proceed with the current tasks if applicable"
            )

        elif tool_name == "Skill":
            prompt_text = str(getattr(data, "prompt", "") or "").strip()
            if prompt_text:
                return prompt_text
            command_name = str(getattr(data, "commandName", "") or "skill")
            return f"Loaded skill /{command_name}"

        # 其他工具：尝试提取 content/result/text 字段，否则用 str()
        for attr in ("content", "result", "text", "output", "message"):
            val = getattr(data, attr, None)
            if val is not None:
                return str(val)

        # 最后兜底：如果是 Pydantic 模型，用 model_dump 转 JSON 字符串
        if hasattr(data, "model_dump"):
            import json
            try:
                return json.dumps(data.model_dump(), ensure_ascii=False, indent=2)
            except Exception:
                pass

        return str(data)

    def _format_tool_error(self, tool: TrackedTool, error: Exception) -> Dict[str, Any]:
        """
        格式化工具错误为消息

        """
        return {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool.id,
                "content": f"<tool_use_error>Error: {str(error)}</tool_use_error>",
                "is_error": True,
                "receipt": None,
                "audit_events": [],
            }],
        }

    def _has_unfinished_tools(self) -> bool:
        """检查是否有未完成的工具"""
        return any(t.status != ToolStatus.YIELDED for t in self.tools)

    def _has_executing_tools(self) -> bool:
        """检查是否有正在执行的工具"""
        return any(
            t.status in {ToolStatus.EXECUTING, ToolStatus.WAITING_INTERACTION}
            for t in self.tools
        )

    def _has_completed_unyielded(self) -> bool:
        """检查是否有已完成但未返回的结果"""
        return any(t.status == ToolStatus.COMPLETED for t in self.tools)
