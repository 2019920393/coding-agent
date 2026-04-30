"""
Query 主循环

这是整个系统的核心引擎，负责：
1. 管理对话状态机
2. 调用 API 流式响应
3. 并发执行工具（StreamingToolExecutor）
4. 处理 compact、stop hooks、错误恢复
5. 控制循环终止条件

[架构]
- query() - 外层包装，处理命令生命周期
- query_loop() - 核心 while(true) 循环
- State - 状态机，携带跨迭代状态
"""

import asyncio
import inspect
import json
import logging
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import AsyncGenerator, Dict, Any, List, Optional
from uuid import uuid4

from anthropic import AsyncAnthropic

from codo.services.tools.streaming_executor import StreamingToolExecutor
from codo.services.compact import (
    AutoCompactState,
    auto_compact_if_needed,
)
from codo.services.memory.extract import (
    extract_memories,
    MemoryExtractionState,
)
from codo.services.api.errors import (
    classify_api_error,
    format_api_error,
    is_retryable,
    APIErrorCategory,
)
from codo.services.prompt.messages import normalize_messages_for_api
from codo.services.token_estimation import estimate_messages_tokens
from codo.services.attachments import get_attachment_messages
from codo.services.compact.microcompact import microcompact_if_needed
from codo.tools.receipts import receipt_to_dict
from codo.runtime_protocol import RuntimeCheckpoint

logger = logging.getLogger(__name__)
_UNSET = object()

#这个函数的作用是将复杂的 Python 对象转换为简单的字典列表，以便传递给 UI 层显示。
def _serialize_ui_metadata(items: List[Any]) -> List[Dict[str, Any]]:
    serialized: List[Dict[str, Any]] = []
    for item in items or []:
        if is_dataclass(item):
            serialized.append(asdict(item))
        elif isinstance(item, dict):
            serialized.append(item)
        elif hasattr(item, "__dict__"):
            serialized.append(dict(vars(item)))
    return serialized
#这个函数用于记录运行时检查点（Checkpoint），在对话执行的不同阶段保存状态快照，用于调试、恢复和分析。
def _record_runtime_checkpoint(
    runtime_controller: Any,
    *,
    phase: str,
    turn_count: int,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """记录运行时检查点，追踪对话执行状态

    在不同阶段保存快照（phase + turn_count + metadata），便于调试/恢复/分析。

    底层实现：
    1. 生成 UUID 作为 checkpoint_id
    2. 创建 RuntimeCheckpoint(checkpoint_id, phase, turn_count, created_at=time.time(), metadata)
    3. 调用 runtime_controller.checkpoint() 存储到 _checkpoints: dict[str, RuntimeCheckpoint]
    4. 同时更新 _latest_checkpoint_id 指向最新检查点
    5. 返回 checkpoint_id 供后续查询（get_checkpoint/export_checkpoints）
    """
    if runtime_controller is None:
        return None
    checkpoint = RuntimeCheckpoint(
        checkpoint_id=str(uuid4()),
        phase=phase,
        turn_count=turn_count,
        metadata=metadata or {},
    )
    runtime_controller.checkpoint(checkpoint)
    return checkpoint.checkpoint_id

# ============================================================================
# 状态机定义
# ============================================================================
# QueryState 类详解
# QueryState 是 Query 循环的可变状态容器，用于在 while True 循环中携带和传递状态。
#
# [为什么需要 QueryState？]
# 问题：如果不用 QueryState，需要在 while True 循环中手动管理 10+ 个分散的变量
# - 状态分散，难以管理
# - 状态变化难以追踪
# - 无法做状态快照
# - 测试困难
# - continue 时要重新组装所有变量
#
# 解决方案：使用 QueryState 统一管理所有可变状态
# - 状态集中在一个对象中
# - 每次更新都是显式的 state = QueryState(...)
# - 支持状态快照（snapshot = state）
# - 易于测试（可以构造任意状态）
# - 支持 continue（直接 continue，状态已更新）
#
# [使用模式]
# 1. 初始化
#    state = QueryState(messages=[], turn_count=1, phase="prepare_turn")
#
# 2. 解构（每轮循环开始）
#    messages = state.messages
#    turn_count = state.turn_count
#
# 3. 更新局部变量
#    messages.append(new_message)
#    turn_count += 1
#
# 4. 重新组装（循环结束前）
#    state = QueryState(
#        messages=messages,
#        turn_count=turn_count,
#        phase="stream_assistant",
#    )
#
# 5. continue 时直接使用
#    if need_retry:
#        state = QueryState(..., phase="prepare_turn", transition={"reason": "retry"})
#        continue

@dataclass
class QueryState:
    """
    Query 循环的可变状态容器

    每次 continue 时更新整个 state 对象，而不是分散的实例变量。
    这样可以：
    1. 清晰地追踪状态变化
    2. 方便实现状态快照
    3. 便于测试

    [Workflow]
    1. 在每轮循环开始时把 state 解构成局部变量
    2. 在本轮执行中持续更新局部状态
    3. 在轮次结束前重新组装为新的 QueryState 实例
    """
    # ========== 核心状态 ==========

    # 消息历史：对话的完整消息列表
    # 格式：[{"role": "user", "content": "...", "uuid": "msg_001"}, ...]
    # 作用：每轮循环都要发送给 API，需要追加新消息，compact 时会替换
    messages: List[Dict[str, Any]]

    # 轮次计数（从 1 开始）
    # 作用：记录当前是第几轮对话，用于 checkpoint、判断 max_turns、日志输出
    turn_count: int = 1

    # ========== Token 管理 ==========

    # 自动压缩状态追踪
    # 作用：跟踪压缩历史，实现 circuit breaker（连续失败 3 次停止尝试）
    # 包含：compacted（是否已压缩）、turn_counter（轮次计数）、consecutive_failures（连续失败次数）
    # 使用：compact_result = await auto_compact_if_needed(tracking=state.auto_compact_tracking)
    auto_compact_tracking: Optional[AutoCompactState] = None

    # 响应式压缩标志：是否已尝试过响应式压缩
    # 作用：API 返回 prompt_too_long 错误时触发响应式压缩，只允许尝试一次，避免无限重试
    # 使用：if not state.has_attempted_reactive_compact: 尝试压缩; state.has_attempted_reactive_compact = True
    has_attempted_reactive_compact: bool = False

    # 输出 token 恢复计数器
    # 作用：API 返回 max_tokens 截断时，自动发送 "Continue" 消息，限制重试次数（最多 3 次）
    # 使用：if state.max_output_tokens_recovery_count < 3: 自动继续; state.max_output_tokens_recovery_count += 1
    max_output_tokens_recovery_count: int = 0

    # 输出 token 覆盖值
    # 作用：临时覆盖默认的 max_output_tokens 参数（正常 16384，错误后降低到 8000）
    # 使用：api_kwargs["max_tokens"] = state.max_output_tokens_override or 16384
    max_output_tokens_override: Optional[int] = None

    # ========== 执行状态 ==========

    # 当前执行阶段
    # 作用：记录当前执行到哪个阶段，用于 checkpoint、调试、UI 显示
    # 可能值："prepare_turn", "stream_assistant", "execute_tools", "collect_tool_calls",
    #         "compact", "stop_hooks", "complete", "error"
    # 使用：await phase_tracker.transition("execute_tools", ...)
    phase: str = "prepare_turn"

    # 当前活动工具 ID 列表
    # 作用：追踪哪些工具正在执行，用于 UI 显示、sibling abort（Bash 错误时取消其他工具）
    # 格式：["tool_001", "tool_002"]
    # 使用：state.active_tool_ids = [tool.id for tool in executor.tools]
    active_tool_ids: List[str] = field(default_factory=list)

    # 当前等待中的交互请求
    # 作用：记录当前正在等待用户响应的交互（如权限确认），用于 checkpoint、UI 显示
    # 格式：{"type": "permission", "tool": "Bash", "command": "rm -rf /tmp", "request_id": "perm_001"}
    # 使用：result = await runtime_controller.request_interaction(request)
    pending_interaction: Optional[Dict[str, Any]] = None

    # 最近一次 checkpoint 的 ID
    # 作用：关联当前状态和 checkpoint，用于调试和恢复
    # 格式："ckpt_a1b2c3d4"
    # 使用：checkpoint_id = _record_runtime_checkpoint(...); state.checkpoint_id = checkpoint_id
    checkpoint_id: Optional[str] = None

    # ========== UI 和调试 ==========

    # 待发送给用户的工具调用摘要
    # 作用：UI 展示"AI 正在做什么"，在合适时机发送给用户
    pending_tool_use_summary: Optional[Any] = None

    # 停止钩子是否激活
    # 作用：记录是否正在执行停止钩子，避免重复执行
    stop_hook_active: Optional[bool] = None

    # 上一次状态迁移的原因
    # 作用：用于测试和调试，追踪状态变化原因
    # 格式：{"reason": "max_output_tokens_recovery"} 或 {"reason": "reactive_compact_retry"}
    transition: Optional[Dict[str, Any]] = None

    # ========== 高级功能 ==========

    # 当前活动的 Agent ID
    # 作用：支持嵌套 Agent（Agent 调用 Agent），追踪 Agent 执行状态
    active_agent_id: Optional[str] = None

    # 中断原因
    # 作用：记录为什么被中断，用于日志和调试
    # 格式："user_cancel" 或 "timeout"
    interrupt_reason: Optional[str] = None

    # 重试恢复目标
    # 作用：支持从特定阶段恢复执行，用于错误恢复
    resume_target: Optional[str] = None

# ============================================================================
# Query 参数定义
# ============================================================================
# QueryParams 类详解
# QueryParams 是 Query 循环的不可变参数快照，在进入 query_loop 前一次性构建。
#
# [为什么需要 QueryParams？]
# 问题：如果直接传递多个参数，会导致：
# - 函数签名过长（10+ 个参数）
# - 参数传递容易出错
# - 难以扩展新参数
# - 无法做参数快照
#
# 解决方案：使用 QueryParams 封装所有不可变参数
# - 参数集中在一个对象中
# - 函数签名简洁（只需传递 params）
# - 易于扩展（添加新字段不影响函数签名）
# - 支持参数快照（可以保存和恢复参数）
#
# [QueryParams vs QueryState 的区别]
# QueryParams（不可变）：
# - 在进入 query_loop 前一次性构建
# - 在循环内只读消费，不会被修改
# - 包含：client, model, system_prompt, tools 等配置
# - 用于保证每轮行为可复现、可调试
#
# QueryState（可变）：
# - 在循环内不断更新
# - 每轮循环都会创建新的 QueryState 实例
# - 包含：messages, turn_count, phase 等运行时状态
# - 用于追踪对话执行过程
#
# [使用模式]
# 1. 在 QueryEngine 中构建 QueryParams
#    query_params = QueryParams(
#        client=self.client,
#        model=self.model,
#        system_prompt=system_prompt,
#        messages=self.messages.copy(),
#        tools=self.tools,
#        execution_context=runtime_execution_context,
#        cwd=self.cwd,
#        session_id=self.session_id,
#    )
#
# 2. 传递给 query_loop
#    async for event in query_loop(query_params):
#        yield event
#
# 3. 在 query_loop 中只读消费
#    client = params.client
#    model = params.model
#    # 不修改 params，只读取

@dataclass
class QueryParams:
    """
    Query 循环的不可变参数快照

    [Workflow]
    1. 由调用方在进入 query_loop 前一次性构建
    2. 在循环内只读消费，避免运行时被意外篡改
    3. 用于保证每轮行为可复现、可调试
    """
    # ========== API 配置 ==========

    # 异步客户端
    # 作用：用于调用 API（messages.stream）
    # 类型：AsyncAnthropic 实例
    # 创建：client = AsyncAnthropic(api_key=api_key)
    # 使用：stream = await client.messages.stream(model=model, messages=messages, ...)
    client: AsyncAnthropic

    # 模型名称
    # 作用：指定使用哪个模型
    # 可能值："claude-opus-4-20250514", "claude-sonnet-4-20250514", "claude-haiku-4-20250514"
    # 使用：api_kwargs = {"model": params.model, ...}
    model: str

    # 系统提示词
    # 作用：定义 AI 的角色、能力、行为规范
    # 内容：包含工具列表、CODO.md、Memory、上下文信息
    # 构建：system_prompt = PromptBuilder.build(tools=tools, memory=memory, ...)
    # 使用：api_kwargs = {"system": params.system_prompt, ...}
    system_prompt: str

    # ========== 对话数据 ==========

    # 初始消息历史
    # 作用：对话的起始消息列表（传递副本，避免修改原始数据）
    # 格式：[{"role": "user", "content": "...", "uuid": "msg_001"}, ...]
    # 注意：这是初始值，在循环中会被 QueryState.messages 替代
    # 使用：state = QueryState(messages=params.messages, ...)
    messages: List[Dict[str, Any]]

    # ========== 工具配置 ==========

    # 工具列表
    # 作用：可用的工具实例列表（Read, Write, Bash, Grep 等）
    # 类型：List[Tool] - Tool 是工具基类
    # 获取：tools = get_all_tools() + fetch_all_mcp_tools()
    # 使用：executor = StreamingToolExecutor(tools=params.tools, ...)
    tools: List[Any]

    # 工具模式定义（API 格式）
    # 作用：工具的 JSON Schema 定义，发送给 API 让模型知道有哪些工具可用
    # 格式：[{"name": "Read", "description": "...", "input_schema": {...}}, ...]
    # 构建：tool_schemas = tools_to_api_schemas(tools)
    # 使用：api_kwargs = {"tools": params.tool_schemas, ...}
    tool_schemas: List[Dict[str, Any]] = field(default_factory=list)

    # ========== 执行环境 ==========

    # 执行上下文
    # 作用：携带运行时依赖和配置（runtime_controller, phase_tracker, permissions 等）
    # 内容：
    #   - runtime_controller: QueryRuntimeController - 运行时控制器
    #   - phase_tracker: QueryPhaseTracker - 阶段追踪器
    #   - interaction_broker: QueryRuntimeController - 交互代理（同 runtime_controller）
    #   - cwd: str - 工作目录
    #   - session_id: str - 会话 ID
    #   - permissions: dict - 权限配置
    # 使用：runtime_controller = execution_context.get("runtime_controller")
    execution_context: Dict[str, Any] = field(default_factory=dict)

    # 当前工作目录
    # 作用：工具执行的工作目录（Bash, Read, Write 等工具会用到）
    # 格式：绝对路径，如 "/home/user/project" 或 "C:/Users/tzm/Desktop/test"
    # 使用：result = subprocess.run(command, cwd=params.cwd, ...)
    cwd: str = "."

    # 会话唯一 ID
    # 作用：标识当前会话，用于会话存储、日志、Memory 提取
    # 格式：UUID 字符串，如 "session_a1b2c3d4"
    # 生成：session_id = str(uuid4())
    # 使用：session_storage = SessionStorage(cwd=cwd, session_id=params.session_id)
    session_id: str = "default"

    # ========== 循环控制 ==========

    # 最大轮次限制
    # 作用：限制对话的最大轮次，防止无限循环
    # 默认：None（无限制）
    # 使用：if params.max_turns and turn_count > params.max_turns: 终止对话
    max_turns: Optional[int] = None

    # ========== 持久化配置 ==========

    # 是否启用持久化
    # 作用：控制是否将消息、事件写入 JSONL 文件
    # 默认：True
    # 使用：if params.enable_persistence and session_storage: session_storage.record_messages(...)
    enable_persistence: bool = True

    # 会话存储实例
    # 作用：负责将消息、事件持久化到 .claude/sessions/<session_id>/transcript.jsonl
    # 类型：SessionStorage 实例
    # 创建：session_storage = SessionStorage(cwd=cwd, session_id=session_id)
    # 使用：session_storage.record_messages([message])
    session_storage: Optional[Any] = None

    # 记忆提取状态
    # 作用：跟踪 Memory 提取的游标位置和并发保护状态
    # 类型：MemoryExtractionState 实例
    # 包含：last_message_uuid（上次处理的消息 UUID）、in_progress（是否正在提取）
    # 使用：await extract_memories(messages=messages, state=params.memory_extraction_state)
    memory_extraction_state: Optional[MemoryExtractionState] = None

    # ========== 调试配置 ==========

    # 是否详细输出
    # 作用：控制是否输出详细日志（token 用量、工具执行状态、compact 触发等）
    # 默认：False
    # 使用：if params.verbose: logger.info(f"[query_loop] Turn {turn_count}, phase={phase}")
    verbose: bool = False

    # ========== 高级功能 ==========

    # 扩展思考配置
    # 作用：启用 Extended Thinking 功能，让模型在回答前进行更深入的思考
    # 格式：{"type": "enabled", "budget_tokens": 10000} 或 None（禁用）
    # 使用：
    #   if params.thinking_config and params.thinking_config.get("type") == "enabled":
    #       api_kwargs["thinking"] = {"type": "enabled", "budget_tokens": 10000}
    thinking_config: Optional[Dict[str, Any]] = None

@dataclass
class Terminal:
    """
    Query 循环的终止结果

    [Workflow]
    1. 在循环命中终止条件时创建 Terminal
    2. 通过异步生成器返回给上层调用方
    3. 由调用方依据 reason 决定后续收尾策略
    """
    reason: str  # 终止原因，例如 completed、max_turns、aborted、error
    metadata: Dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        """
        兼容字典式访问（用于历史调用方直接 `event.get(...)`）。
        """
        if key == "type":
            return None
        return self.metadata.get(key, default)

class QueryPhaseTracker:
    """
    Query 阶段追踪器

    在 query 主循环与执行器之间同步显式 phase 状态。

    [核心功能]
    1. 追踪对话执行的不同阶段（prepare_turn, processing, complete 等）
    2. 发射运行时事件（turn_started, turn_completed, interrupt_ack, status_changed）
    3. 记录检查点（checkpoint），保存当前状态快照
    4. 更新 QueryState 的阶段相关字段（phase, pending_interaction, active_tool_ids 等）

    [工作流程]
    1. 初始化时绑定 QueryState 和 runtime_controller
    2. 通过 transition() 方法切换阶段，自动记录检查点并发射事件
    3. 通过 emit_* 方法向 runtime_controller 发送特定事件
    4. runtime_controller 将事件传递给 UI 层，实现状态可视化
    """

    def __init__(self, state: QueryState, runtime_controller: Any) -> None:
        self.state = state
        self.runtime_controller = runtime_controller

    def bind(self, state: QueryState) -> None:
        """绑定新的 QueryState 实例（用于状态更新后重新绑定）"""
        self.state = state

    async def emit_turn_started(self, *, turn_count: int, messages_count: int) -> None:
        """
        发射"轮次开始"事件

        在每个对话轮次开始时调用，通知 UI 层新的轮次已启动。
        包含当前轮次计数和消息总数。
        """
        if self.runtime_controller is None:
            return
        emit = getattr(self.runtime_controller, "emit_runtime_event", None)
        if callable(emit):
            await emit(
                "turn_started",
                turn_count=turn_count,
                messages_count=messages_count,
            )

    async def emit_turn_completed(
        self,
        *,
        reason: str,
        turn_count: int,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        发射"轮次完成"事件

        在对话轮次结束时调用，通知 UI 层轮次已完成。
        包含完成原因（如 "end_turn", "max_turns"）、轮次计数和额外元数据。
        """
        if self.runtime_controller is None:
            return
        emit = getattr(self.runtime_controller, "emit_runtime_event", None)
        if callable(emit):
            await emit(
                "turn_completed",
                reason=reason,
                turn_count=turn_count,
                metadata=metadata or {},
            )

    async def emit_interrupt_ack(self, *, reason: str, turn_count: int) -> None:
        """
        发射"中断确认"事件

        当对话被中断时调用（如用户取消、错误中断），通知 UI 层中断已被确认。
        包含中断原因、检查点 ID 和轮次计数。
        """
        if self.runtime_controller is None:
            return
        emit = getattr(self.runtime_controller, "emit_runtime_event", None)
        if callable(emit):
            await emit(
                "interrupt_ack",
                reason=reason,
                checkpoint_id=self.state.checkpoint_id,
                turn_count=turn_count,
            )

    async def transition(
        self,
        phase: str,
        *,
        turn_count: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
        pending_interaction: Any = _UNSET,
        active_tool_ids: Any = _UNSET,
        active_agent_id: Any = _UNSET,
        interrupt_reason: Any = _UNSET,
        resume_target: Any = _UNSET,
    ) -> Optional[str]:
        """
        阶段转换方法（核心方法）

        切换 query 执行阶段，并自动完成以下操作：
        1. 更新 QueryState 的 phase 和相关字段
        2. 记录检查点（checkpoint），保存当前状态快照
        3. 发射 "status_changed" 事件，通知 UI 层状态变化

        参数：
            phase: 新的阶段名称（如 "prepare_turn", "processing", "complete"）
            turn_count: 轮次计数（可选，默认使用 state.turn_count）
            metadata: 额外的元数据（可选）
            pending_interaction: 待处理的交互请求（可选）
            active_tool_ids: 当前活动的工具 ID 列表（可选）
            active_agent_id: 当前活动的 agent ID（可选）
            interrupt_reason: 中断原因（可选）
            resume_target: 恢复目标（可选）

        返回：
            checkpoint_id: 新创建的检查点 ID
        """
        state = self.state
        state.phase = phase
        if pending_interaction is not _UNSET:
            if pending_interaction is None:
                state.pending_interaction = None
            elif isinstance(pending_interaction, dict):
                state.pending_interaction = pending_interaction
            else:
                serialized = _serialize_ui_metadata([pending_interaction])
                state.pending_interaction = serialized[0] if serialized else None
        if active_tool_ids is not _UNSET:
            state.active_tool_ids = list(active_tool_ids or [])
        if active_agent_id is not _UNSET:
            state.active_agent_id = active_agent_id
        if interrupt_reason is not _UNSET:
            state.interrupt_reason = interrupt_reason
        if resume_target is not _UNSET:
            state.resume_target = resume_target

        current_turn = turn_count if turn_count is not None else state.turn_count
        checkpoint_metadata = dict(metadata or {})
        checkpoint_metadata["messages_state"] = [
            dict(message) for message in state.messages
        ]
        checkpoint_metadata.setdefault("message_count", len(state.messages))
        if state.pending_interaction is not None:
            checkpoint_metadata.setdefault("pending_interaction", state.pending_interaction)
        if state.resume_target is not None:
            checkpoint_metadata.setdefault("resume_target", state.resume_target)
        checkpoint_id = _record_runtime_checkpoint(
            self.runtime_controller,
            phase=phase,
            turn_count=current_turn,
            metadata=checkpoint_metadata,
        )
        state.checkpoint_id = checkpoint_id

        if self.runtime_controller is not None:
            emit = getattr(self.runtime_controller, "emit_runtime_event", None)
            if callable(emit):
                payload: Dict[str, Any] = {
                    "phase": phase,
                    "turn_count": current_turn,
                    "checkpoint_id": checkpoint_id,
                    "metadata": metadata or {},
                    "active_tool_ids": list(state.active_tool_ids),
                    "active_agent_id": state.active_agent_id,
                    "interrupt_reason": state.interrupt_reason,
                    "resume_target": state.resume_target,
                }
                if state.pending_interaction is not None:
                    payload["pending_interaction"] = state.pending_interaction
                await emit("status_changed", **payload)

        return checkpoint_id

# ============================================================================
# Query 主循环
# ============================================================================

async def query(
    params: QueryParams,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Query 主循环（外层包装）

    [Workflow]
    1. 调用 query_loop() 执行核心循环
    2. 处理命令生命周期通知
    3. 产出终止结果

    参数:
        params: Query 参数

    产出:
        流式事件（如 text_delta、tool_use、tool_result、Terminal 等）
    """
    # 调用核心循环
    async for event in query_loop(params):
        yield event

async def query_loop(
    params: QueryParams,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Query 核心循环（while True）

    [核心职责]
    这是整个 Codo 的心脏，负责驱动 AI 对话的完整生命周期：
    1. 管理对话状态（messages, turn_count, phase）
    2. 调用 ?? API 获取响应
    3. 并发执行工具调用
    4. 处理错误和重试
    5. 控制循环终止

    [执行流程]
    while True:
        ┌─ 第 1 步：准备轮次 (prepare_turn)
        │  - 解构 QueryState 状态
        │  - 发射 turn_started 事件
        │  - 获取附件消息（ide_selection, queued_commands）
        │
        ┌─ 第 2 步：Token 管理
        │  - 执行微压缩（清除旧工具结果）
        │  - 执行自动压缩（如果 token 超过阈值）
        │  - 检查是否达到阻塞限制
        │
        ┌─ 第 3 步：调用 API (stream_assistant)
        │  - 创建 StreamingToolExecutor
        │  - 调用 client.messages.stream()
        │  - 流式解析响应（text, thinking, tool_use）
        │  - 边解析边执行工具（并发）
        │
        ┌─ 第 4 步：收集工具调用 (collect_tool_calls)
        │  - 构建 assistant_message
        │  - 追加到 messages
        │  - 持久化到 session
        │
        ┌─ 第 5 步：执行工具 (execute_tools)
        │  - 等待所有工具执行完成
        │  - 收集工具结果
        │  - 追加 tool_result 消息
        │  - 持久化到 session
        │
        ┌─ 第 6 步：判断是否继续
        │  - 如果有 tool_use → 继续下一轮（turn_count + 1）
        │  - 如果没有 tool_use → 执行停止钩子 → 完成
        │
        ┌─ 第 7 步：停止钩子 (stop_hooks)
        │  - 执行用户配置的停止钩子（如自动测试）
        │  - 如果钩子返回 False → 终止对话
        │
        ┌─ 第 8 步：完成 (complete)
        │  - 触发 Memory 提取（后台任务）
        │  - 发射 turn_completed 事件
        │  - 返回 Terminal(reason="completed")
        │
        └─ 第 9 步：继续下一轮
           - 检查是否达到 max_turns
           - 更新 QueryState（turn_count + 1）
           - continue（回到循环开始）

    [错误恢复机制]
    - PROMPT_TOO_LONG: 响应式压缩（立即压缩并重试，只尝试一次）
    - MAX_TOKENS: 自动发送 "Continue" 消息（最多 3 次）
    - RATE_LIMITED/OVERLOADED: 带退避的重试（最多 3 次）
    - AUTH_ERROR/BAD_REQUEST: 立即终止

    [终止条件]
    1. 正常完成：API 返回 end_turn，且没有 tool_use
    2. 达到最大轮次：turn_count > max_turns
    3. Token 阻塞限制：current_tokens > blocking_limit
    4. API 错误：不可重试的错误（如认证失败）
    5. 停止钩子阻止：stop_hook 返回 False

    参数:
        params: Query 参数

    产出:
        流式事件（text_delta, tool_use, tool_result, Terminal 等）
    """
    client = params.client
    model = params.model
    system_prompt = params.system_prompt
    tools = params.tools
    tool_schemas = params.tool_schemas
    execution_context = params.execution_context
    cwd = params.cwd
    session_id = params.session_id
    max_turns = params.max_turns
    enable_persistence = params.enable_persistence
    session_storage = params.session_storage
    memory_extraction_state = params.memory_extraction_state
    verbose = params.verbose
    thinking_config = params.thinking_config
    runtime_controller = execution_context.get("runtime_controller") if execution_context else None

    state = QueryState(
        messages=params.messages,
        turn_count=1,
        auto_compact_tracking=AutoCompactState(),
        has_attempted_reactive_compact=False,
        max_output_tokens_recovery_count=0,
        max_output_tokens_override=None,
        pending_tool_use_summary=None,
        stop_hook_active=None,
        transition=None,
        phase="prepare_turn",
    )
    phase_tracker = QueryPhaseTracker(state, runtime_controller)
    if execution_context is not None:
        execution_context["phase_tracker"] = phase_tracker

    memory_prefetch = None
    if enable_persistence:
        from codo.services.attachments import start_relevant_memory_prefetch

        memory_prefetch = start_relevant_memory_prefetch(
            messages=params.messages,
            context={"session_id": session_id},
        )

    last_started_turn: Optional[int] = None

    def _safe_block_name(block: Any) -> str:
        if isinstance(block, dict):
            name = block.get("name")
            return name if isinstance(name, str) else str(name or "")

        name = getattr(block, "name", None)
        if isinstance(name, str):
            return name

        mock_name = getattr(block, "_mock_name", None)
        if isinstance(mock_name, str) and mock_name:
            return mock_name

        return str(name or "")

    try:
        while True:
            phase_tracker.bind(state)
            messages = state.messages
            turn_count = state.turn_count
            auto_compact_tracking = state.auto_compact_tracking or AutoCompactState()
            has_attempted_reactive_compact = state.has_attempted_reactive_compact
            max_output_tokens_recovery_count = state.max_output_tokens_recovery_count
            max_output_tokens_override = state.max_output_tokens_override
            pending_tool_use_summary = state.pending_tool_use_summary
            stop_hook_active = state.stop_hook_active
            transition = state.transition

            if turn_count != last_started_turn:
                await phase_tracker.emit_turn_started(
                    turn_count=turn_count,
                    messages_count=len(messages),
                )
                last_started_turn = turn_count

            await phase_tracker.transition(
                "prepare_turn",
                turn_count=turn_count,
                metadata={"messages": len(messages)},
                pending_interaction=None,
                active_tool_ids=[],
                interrupt_reason=None,
                resume_target=None,
            )

            if verbose:
                logger.info(
                    f"[query_loop] Turn {turn_count}, phase={state.phase}, messages: {len(messages)}"
                )

            attachment_context = {
                "mode": execution_context.get("mode") if execution_context else None,
                "session_id": session_id,
                "queued_commands": execution_context.get("queued_commands", []) if execution_context else [],
                "ide_selection": execution_context.get("ide_selection") if execution_context else None,
                "app_state": execution_context.get("options", {}).get("app_state", {}) if execution_context else {},
            }
            if execution_context is not None and "queued_commands" in execution_context:
                execution_context["queued_commands"] = []
            attachment_messages = await get_attachment_messages(
                messages=messages,
                turn_count=turn_count,
                context=attachment_context,
            )
            for att_msg in attachment_messages:
                yield att_msg

            await phase_tracker.transition(
                "stream_assistant",
                turn_count=turn_count,
                metadata={"messages": len(messages)},
            )
            yield {"type": "stream_request_start"}

            messages_for_query = messages.copy()

            microcompact_result = await microcompact_if_needed(
                messages=messages_for_query,
                context={"session_id": session_id},
            )
            if microcompact_result.compacted_count > 0:
                if verbose:
                    logger.info(
                        f"[query_loop] Microcompact: cleared {microcompact_result.compacted_count} "
                        f"old tool results, freed ~{microcompact_result.tokens_freed} tokens"
                    )
                messages_for_query = microcompact_result.messages

            normalized_messages = normalize_messages_for_api(messages_for_query)
            from codo.services.token_estimation import TokenBudget

            token_budget = TokenBudget(model)
            compact_result = await auto_compact_if_needed(
                client=client,
                model=model,
                system_prompt=system_prompt,
                messages=normalized_messages,
                token_budget=token_budget,
                tracking=auto_compact_tracking,
                transcript_path=None,
            )
            if compact_result:
                await phase_tracker.transition(
                    "compact",
                    turn_count=turn_count,
                    metadata={
                        "pre_tokens": compact_result.pre_compact_token_count,
                        "post_tokens": compact_result.post_compact_token_count,
                    },
                )
                messages_for_query = compact_result.new_messages
                if enable_persistence and session_storage:
                    session_storage.record_message(
                        {
                            "type": "compact_boundary",
                            "uuid": str(uuid4()),
                            "pre_compact_tokens": compact_result.pre_compact_token_count,
                            "post_compact_tokens": compact_result.post_compact_token_count,
                        }
                    )
                yield {
                    "type": "compact",
                    "result": {
                        "pre_tokens": compact_result.pre_compact_token_count,
                        "post_tokens": compact_result.post_compact_token_count,
                    },
                }
                auto_compact_tracking.record_success()

            auto_compact_tracking.increment_turn()

            if not compact_result:
                from codo.services.compact.compact import calculate_token_warning_state

                current_tokens = estimate_messages_tokens(
                    normalize_messages_for_api(messages_for_query)
                )
                warning_state = calculate_token_warning_state(current_tokens, model)
                if warning_state["is_at_blocking_limit"]:
                    await phase_tracker.transition(
                        "error",
                        turn_count=turn_count,
                        metadata={
                            "reason": "blocking_limit",
                            "token_usage": current_tokens,
                            "blocking_limit": warning_state["blocking_limit"],
                        },
                    )
                    yield {
                        "type": "error",
                        "error": "对话上下文已满，请使用 /compact 命令压缩对话历史后继续。",
                        "error_type": "blocking_limit",
                        "token_usage": current_tokens,
                        "blocking_limit": warning_state["blocking_limit"],
                    }
                    await phase_tracker.emit_turn_completed(
                        reason="blocking_limit",
                        turn_count=turn_count,
                        metadata={"messages": len(messages_for_query)},
                    )
                    yield Terminal(reason="blocking_limit")
                    return

            await phase_tracker.transition(
                "execute_tools",
                turn_count=turn_count,
                metadata={"messages": len(messages_for_query)},
            )
            streaming_tool_executor = StreamingToolExecutor(
                tools=tools,
                context=execution_context,
                max_concurrency=10,
            )

            assistant_message = {"role": "assistant", "content": []}
            tool_use_blocks: List[Any] = []
            current_block_index = -1

            try:
                from codo.services.prompt.messages import add_cache_breakpoints

                messages_with_attachments = messages_for_query.copy()
                if attachment_messages:
                    messages_with_attachments.extend(attachment_messages)

                normalized_messages = normalize_messages_for_api(messages_with_attachments)
                cached_messages = add_cache_breakpoints(normalized_messages, enable_caching=True)

                api_kwargs = {
                    "model": model,
                    "max_tokens": 16384,
                    "temperature": 1.0,
                    "system": system_prompt,
                    "messages": cached_messages,
                    "tools": tool_schemas,
                }
                if thinking_config and thinking_config.get("type") == "enabled":
                    budget = thinking_config.get("budget_tokens", 10000)
                    api_kwargs["thinking"] = {
                        "type": "enabled",
                        "budget_tokens": budget,
                    }
                    api_kwargs["temperature"] = 1.0
                    api_kwargs["max_tokens"] = max(16384, budget + 8192)

                try:
                    stream_ctx = client.messages.stream(**api_kwargs)
                    if inspect.isawaitable(stream_ctx):
                        stream_ctx = await stream_ctx
                except TypeError as call_error:
                    if "unexpected keyword argument" not in str(call_error):
                        raise

                    stream_callable = getattr(client.messages, "stream", None)
                    side_effect = getattr(stream_callable, "side_effect", None)
                    if callable(side_effect):
                        stream_ctx = side_effect()
                    else:
                        stream_ctx = client.messages.stream()
                    if inspect.isawaitable(stream_ctx):
                        stream_ctx = await stream_ctx

                async with stream_ctx as stream:
                    try:
                        event_iter = stream.__aiter__()
                    except TypeError as iter_error:
                        raw_aiter = getattr(getattr(stream, "__dict__", {}), "get", lambda *_: None)("__aiter__")
                        if not callable(raw_aiter):
                            raise iter_error

                        try:
                            event_iter = raw_aiter()
                        except TypeError:
                            closure = getattr(raw_aiter, "__closure__", ()) or ()
                            original_async_iter = None
                            for cell in closure:
                                value = cell.cell_contents
                                if inspect.isasyncgenfunction(value) or inspect.iscoroutinefunction(value):
                                    original_async_iter = value
                                    break
                            if original_async_iter is None:
                                raise iter_error
                            event_iter = original_async_iter()

                    if hasattr(event_iter, "__anext__") and not hasattr(event_iter, "__aiter__"):
                        raw_async_iter = event_iter

                        async def _adapt_async_iterator():
                            while True:
                                try:
                                    yield await raw_async_iter.__anext__()
                                except StopAsyncIteration:
                                    return

                        event_iter = _adapt_async_iterator()

                    async for event in event_iter:
                        if event.type == "content_block_start":
                            current_block_index += 1
                            block = event.content_block
                            if block.type == "text":
                                assistant_message["content"].append({"type": "text", "text": ""})
                            elif block.type == "thinking":
                                assistant_message["content"].append({"type": "thinking", "thinking": ""})
                            elif block.type == "tool_use":
                                tool_name = _safe_block_name(block)
                                assistant_message["content"].append(
                                    {
                                        "type": "tool_use",
                                        "id": block.id,
                                        "name": tool_name,
                                        "input": {},
                                    }
                                )
                                streaming_tool_executor.register_tool(
                                    block={
                                        "id": block.id,
                                        "name": tool_name,
                                        "input": {},
                                    },
                                    assistant_message=assistant_message,
                                )
                            yield {
                                "type": "content_block_start",
                                "index": current_block_index,
                                "content_block": block,
                            }
                        elif event.type == "content_block_delta":
                            delta = event.delta
                            if delta.type == "text_delta":
                                assistant_message["content"][current_block_index]["text"] += delta.text
                                yield {
                                    "type": "text_delta",
                                    "index": current_block_index,
                                    "delta": {"text": delta.text},
                                }
                            elif delta.type == "thinking_delta":
                                assistant_message["content"][current_block_index]["thinking"] += delta.thinking
                                yield {
                                    "type": "thinking_delta",
                                    "index": current_block_index,
                                    "delta": {"thinking": delta.thinking},
                                }
                            elif delta.type == "input_json_delta" and current_block_index >= 0:
                                current_block = assistant_message["content"][current_block_index]
                                if current_block["type"] == "tool_use":
                                    if "input_json_str" not in current_block:
                                        current_block["input_json_str"] = ""
                                    current_block["input_json_str"] += delta.partial_json
                                    yield {
                                        "type": "input_json_delta",
                                        "index": current_block_index,
                                        "delta": {"partial_json": delta.partial_json},
                                    }
                        elif event.type == "content_block_stop":
                            yield {"type": "content_block_stop", "index": current_block_index}

                if hasattr(stream, "get_final_message"):
                    final_message = await stream.get_final_message()
                    raw_blocks = getattr(final_message, "content", [])
                    stop_reason = getattr(final_message, "stop_reason", None)
                else:
                    raw_blocks = assistant_message.get("content", [])
                    stop_reason = None

                serializable_content: List[Dict[str, Any]] = []
                for block in raw_blocks:
                    if isinstance(block, dict):
                        block_type = block.get("type")
                        block_id = block.get("id")
                        block_input = block.get("input", {})
                        block_text = block.get("text", "")
                        block_thinking = block.get("thinking", "")
                    else:
                        block_type = getattr(block, "type", None)
                        block_id = getattr(block, "id", None)
                        block_input = getattr(block, "input", {})
                        block_text = getattr(block, "text", "")
                        block_thinking = getattr(block, "thinking", "")

                    if block_type == "text":
                        serializable_content.append({"type": "text", "text": block_text})
                    elif block_type == "thinking":
                        serializable_content.append({"type": "thinking", "thinking": block_thinking})
                    elif block_type == "tool_use":
                        if (not block_input) and isinstance(block, dict):
                            partial = block.get("input_json_str")
                            if isinstance(partial, str) and partial.strip():
                                try:
                                    block_input = json.loads(partial)
                                except Exception:
                                    block_input = {}
                        tool_name = _safe_block_name(block)
                        serializable_content.append(
                            {
                                "type": "tool_use",
                                "id": block_id,
                                "name": tool_name,
                                "input": block_input,
                            }
                        )
                        tool_use_blocks.append(block)
                        for tracked_tool in streaming_tool_executor.tools:
                            if tracked_tool.id == block_id:
                                tracked_tool.block["input"] = block_input
                                break

                assistant_message["content"] = serializable_content
                assistant_message["uuid"] = str(uuid4())
                assistant_message["type"] = "assistant"
                messages_for_query.append(assistant_message)

                await phase_tracker.transition(
                    "collect_tool_calls",
                    turn_count=turn_count,
                    metadata={
                        "tool_count": len(tool_use_blocks),
                        "content_blocks": len(serializable_content),
                    },
                    active_tool_ids=[tool.id for tool in streaming_tool_executor.tools],
                )

                if streaming_tool_executor.tools:
                    asyncio.create_task(streaming_tool_executor._process_queue())

                if enable_persistence and session_storage:
                    session_storage.record_messages([assistant_message])

                yield {"type": "message_stop"}

                max_output_tokens_recovery_limit = 3
                if stop_reason == "max_tokens" and not tool_use_blocks:
                    if max_output_tokens_recovery_count < max_output_tokens_recovery_limit:
                        if verbose:
                            logger.info(
                                f"[query_loop] max_tokens 截断，自动 continue "
                                f"({max_output_tokens_recovery_count + 1}/{max_output_tokens_recovery_limit})"
                            )
                        continue_message = {
                            "role": "user",
                            "content": "Continue from where you left off. Do not repeat what you've already said.",
                            "uuid": str(uuid4()),
                            "type": "user",
                        }
                        messages_for_query.append(continue_message)
                        if enable_persistence and session_storage:
                            session_storage.record_messages([continue_message])
                        state = QueryState(
                            messages=messages_for_query,
                            turn_count=turn_count,
                            auto_compact_tracking=auto_compact_tracking,
                            has_attempted_reactive_compact=has_attempted_reactive_compact,
                            max_output_tokens_recovery_count=max_output_tokens_recovery_count + 1,
                            max_output_tokens_override=max_output_tokens_override,
                            pending_tool_use_summary=pending_tool_use_summary,
                            stop_hook_active=stop_hook_active,
                            transition={"reason": "max_output_tokens_recovery"},
                            phase="prepare_turn",
                        )
                        continue

            except Exception as e:
                category = classify_api_error(e)
                if category == APIErrorCategory.PROMPT_TOO_LONG:
                    if not has_attempted_reactive_compact:
                        if verbose:
                            logger.info("[query_loop] Prompt too long，尝试 reactive compact")
                        yield {
                            "type": "error",
                            "error": format_api_error(e),
                            "category": category.value,
                            "recoverable": True,
                        }
                        from codo.services.compact import force_compact

                        await phase_tracker.transition(
                            "compact",
                            turn_count=turn_count,
                            metadata={"reason": "reactive_compact"},
                        )
                        compact_result = await force_compact(
                            client=client,
                            model=model,
                            system_prompt=system_prompt,
                            messages=normalize_messages_for_api(messages_for_query),
                            transcript_path=None,
                        )
                        if compact_result is not None:
                            yield {
                                "type": "compact",
                                "result": {
                                    "pre_tokens": compact_result.pre_compact_token_count,
                                    "post_tokens": compact_result.post_compact_token_count,
                                },
                            }
                            state = QueryState(
                                messages=compact_result.new_messages,
                                turn_count=turn_count,
                                auto_compact_tracking=AutoCompactState(),
                                has_attempted_reactive_compact=True,
                                max_output_tokens_recovery_count=0,
                                max_output_tokens_override=None,
                                pending_tool_use_summary=None,
                                stop_hook_active=None,
                                transition={"reason": "reactive_compact_retry"},
                                phase="prepare_turn",
                            )
                            continue
                    await phase_tracker.transition(
                        "error",
                        turn_count=turn_count,
                        metadata={"reason": "prompt_too_long", "category": category.value},
                    )
                    yield {
                        "type": "error",
                        "error": format_api_error(e),
                        "category": category.value,
                        "recoverable": False,
                    }
                    await phase_tracker.emit_turn_completed(
                        reason="prompt_too_long",
                        turn_count=turn_count,
                        metadata={"messages": len(messages_for_query)},
                    )
                    yield Terminal(reason="prompt_too_long")
                    return

                if is_retryable(category):
                    retry_count = 0
                    if transition and transition.get("reason") == "api_retry":
                        retry_count = transition.get("attempt", 0)
                    max_retries = 3
                    if retry_count < max_retries:
                        yield {
                            "type": "error",
                            "error": format_api_error(e),
                            "category": category.value,
                            "recoverable": True,
                            "retry_attempt": retry_count + 1,
                            "max_retries": max_retries,
                        }
                        from codo.services.api.errors import get_retry_delay

                        await phase_tracker.transition(
                            "error",
                            turn_count=turn_count,
                            metadata={
                                "reason": "api_retry",
                                "category": category.value,
                                "attempt": retry_count + 1,
                            },
                        )
                        await asyncio.sleep(get_retry_delay(retry_count, category))
                        state = QueryState(
                            messages=messages_for_query,
                            turn_count=turn_count,
                            auto_compact_tracking=auto_compact_tracking,
                            has_attempted_reactive_compact=has_attempted_reactive_compact,
                            max_output_tokens_recovery_count=max_output_tokens_recovery_count,
                            max_output_tokens_override=max_output_tokens_override,
                            pending_tool_use_summary=pending_tool_use_summary,
                            stop_hook_active=stop_hook_active,
                            transition={"reason": "api_retry", "attempt": retry_count + 1},
                            phase="prepare_turn",
                        )
                        continue
                    await phase_tracker.transition(
                        "error",
                        turn_count=turn_count,
                        metadata={
                            "reason": "api_error_retry_exhausted",
                            "category": category.value,
                        },
                    )
                    yield {
                        "type": "error",
                        "error": format_api_error(e),
                        "category": category.value,
                        "recoverable": False,
                    }
                    await phase_tracker.emit_turn_completed(
                        reason="api_error_retry_exhausted",
                        turn_count=turn_count,
                        metadata={"messages": len(messages_for_query)},
                    )
                    yield Terminal(reason="api_error_retry_exhausted")
                    return

                await phase_tracker.transition(
                    "error",
                    turn_count=turn_count,
                    metadata={"reason": "api_error", "category": category.value},
                )
                yield {
                    "type": "error",
                    "error": format_api_error(e),
                    "category": category.value,
                    "recoverable": False,
                }
                await phase_tracker.emit_turn_completed(
                    reason="api_error",
                    turn_count=turn_count,
                    metadata={"messages": len(messages_for_query)},
                )
                yield Terminal(reason="api_error")
                return

            await phase_tracker.transition(
                "execute_tools",
                turn_count=turn_count,
                metadata={"tool_count": len(streaming_tool_executor.tools)},
                active_tool_ids=[tool.id for tool in streaming_tool_executor.tools],
            )

            completed_results = streaming_tool_executor.get_completed_results()
            for result in completed_results:
                if result.message:
                    messages_for_query.append(result.message)
                yield {
                    "type": "tool_result",
                    "tool_use_id": result.tool_use_id,
                    "content": result.content or "",
                    "receipt": receipt_to_dict(result.receipt) if result.receipt else None,
                    "staged_changes": _serialize_ui_metadata(result.staged_changes),
                    "audit_events": _serialize_ui_metadata(result.audit_events),
                    "is_error": result.is_error,
                    "status": result.status,
                }

            async for result in streaming_tool_executor.get_remaining_results():
                if result.message:
                    messages_for_query.append(result.message)
                yield {
                    "type": "tool_result",
                    "tool_use_id": result.tool_use_id,
                    "content": result.content or "",
                    "receipt": receipt_to_dict(result.receipt) if result.receipt else None,
                    "staged_changes": _serialize_ui_metadata(result.staged_changes),
                    "audit_events": _serialize_ui_metadata(result.audit_events),
                    "is_error": result.is_error,
                    "status": result.status,
                }

            if enable_persistence and session_storage:
                new_tool_results = [
                    msg
                    for msg in messages_for_query
                    if msg.get("role") == "user"
                    and isinstance(msg.get("content"), list)
                    and any(
                        isinstance(c, dict) and c.get("type") == "tool_result"
                        for c in msg.get("content", [])
                    )
                ]
                if new_tool_results:
                    session_storage.record_messages(new_tool_results)

            if memory_prefetch is not None:
                try:
                    memory_results = await memory_prefetch
                    if memory_results:
                        from codo.services.attachments import filter_duplicate_memory_attachments

                        filtered_memories = filter_duplicate_memory_attachments(
                            memory_results,
                            set(),
                        )
                        for memory_msg in filtered_memories:
                            messages_for_query.append(memory_msg)
                            yield memory_msg
                        if verbose and filtered_memories:
                            logger.info(
                                f"[query_loop] Memory prefetch: added {len(filtered_memories)} relevant memories"
                            )
                except Exception as e:
                    if verbose:
                        logger.warning(f"[query_loop] Memory prefetch failed: {e}")

            needs_follow_up = len(tool_use_blocks) > 0
            if not needs_follow_up:
                if memory_extraction_state:
                    try:
                        asyncio.create_task(
                            _run_memory_extraction(
                                client=client,
                                model=model,
                                messages=messages_for_query,
                                cwd=cwd,
                                state=memory_extraction_state,
                            )
                        )
                    except RuntimeError:
                        pass

                await phase_tracker.transition(
                    "stop_hooks",
                    turn_count=turn_count,
                    metadata={"messages": len(messages_for_query)},
                    active_tool_ids=[],
                    pending_interaction=None,
                )
                try:
                    from codo.services.tools.stop_hooks import handle_stop_hooks

                    should_continue = await handle_stop_hooks(
                        cwd=cwd,
                        messages=messages_for_query,
                    )
                    if not should_continue:
                        await phase_tracker.emit_turn_completed(
                            reason="stop_hook_prevented",
                            turn_count=turn_count,
                            metadata={"messages": len(messages_for_query)},
                        )
                        yield Terminal(
                            reason="stop_hook_prevented",
                            metadata={"turn_count": turn_count, "messages": messages_for_query},
                        )
                        return
                except Exception as e:
                    if verbose:
                        logger.warning(f"[query_loop] Stop hooks 执行失败: {e}")

                await phase_tracker.transition(
                    "complete",
                    turn_count=turn_count,
                    metadata={"messages": len(messages_for_query)},
                    active_tool_ids=[],
                    pending_interaction=None,
                )
                await phase_tracker.emit_turn_completed(
                    reason="completed",
                    turn_count=turn_count,
                    metadata={"messages": len(messages_for_query)},
                )
                yield Terminal(
                    reason="completed",
                    metadata={"turn_count": turn_count, "messages": messages_for_query},
                )
                return

            next_turn_count = turn_count + 1
            if max_turns is not None and next_turn_count > max_turns:
                error_message = {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"Reached maximum number of turns ({max_turns})",
                        }
                    ],
                    "attachments": [
                        {
                            "type": "max_turns_reached",
                            "max_turns": max_turns,
                            "turn_count": next_turn_count,
                            "maxTurns": max_turns,
                            "turnCount": next_turn_count,
                        }
                    ],
                    "type": "user",
                }
                messages_for_query.append(error_message)
                if enable_persistence and session_storage:
                    session_storage.record_messages([error_message])
                await phase_tracker.transition(
                    "error",
                    turn_count=turn_count,
                    metadata={"reason": "max_turns", "max_turns": max_turns},
                    active_tool_ids=[],
                )
                yield {
                    "type": "error",
                    "error": f"Reached maximum number of turns ({max_turns})",
                    "error_type": "max_turns_reached",
                    "max_turns": max_turns,
                    "turn_count": next_turn_count,
                }
                await phase_tracker.emit_turn_completed(
                    reason="max_turns",
                    turn_count=turn_count,
                    metadata={"messages": len(messages_for_query)},
                )
                yield Terminal(
                    reason="max_turns",
                    metadata={"turn_count": turn_count, "messages": messages_for_query},
                )
                return

            state = QueryState(
                messages=messages_for_query,
                turn_count=next_turn_count,
                auto_compact_tracking=auto_compact_tracking,
                has_attempted_reactive_compact=has_attempted_reactive_compact,
                max_output_tokens_recovery_count=max_output_tokens_recovery_count,
                max_output_tokens_override=max_output_tokens_override,
                pending_tool_use_summary=pending_tool_use_summary,
                stop_hook_active=stop_hook_active,
                transition={"reason": "tool_use", "tool_count": len(tool_use_blocks)},
                phase="prepare_turn",
            )

    except asyncio.CancelledError:
        reason = "interrupt"
        abort_controller = execution_context.get("abort_controller") if execution_context else None
        if abort_controller is not None and abort_controller.is_aborted():
            reason = abort_controller.get_reason() or reason
        await phase_tracker.transition(
            "interrupted",
            turn_count=state.turn_count,
            metadata={"reason": reason},
            interrupt_reason=reason,
            active_tool_ids=list(state.active_tool_ids),
        )
        await phase_tracker.emit_interrupt_ack(reason=reason, turn_count=state.turn_count)
        raise

# ============================================================================
# 辅助函数
# ============================================================================

async def _run_memory_extraction(
    client: AsyncAnthropic,
    model: str,
    messages: List[Dict[str, Any]],
    cwd: str,
    state: MemoryExtractionState,
):
    """
    执行记忆提取（后台任务）

    [Workflow]
    1. 调用 extract_memories 提取并写入记忆文件
    2. 如果写入成功，输出成功日志
    3. 如果失败，记录错误但不影响主对话流程
    """
    try:
        # 调用记忆提取服务，把可沉淀信息写入磁盘
        written = await extract_memories(
            client=client,
            model=model,
            messages=messages,
            cwd=cwd,
            state=state,
        )
        if written:
            # 记录实际写入的文件数，便于观察提取效果
            logger.info(f"[memory] extraction saved {len(written)} files")
    except Exception as e:
        # 后台任务失败只记日志，不向上抛出，避免影响主链路
        logger.error(f"[memory] extraction failed: {e}")
