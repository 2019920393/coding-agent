"""
QueryEngine - 对话核心引擎

[重构说明]
- 提取独立的 query() 主循环到 codo/query.py
- QueryEngine 作为高层封装，负责：
  1. 管理会话状态
  2. 初始化工具和上下文
  3. 调用 query() 主循环
  4. 处理会话持久化

[Workflow]
1. 初始化客户端、工具池、会话存储和执行上下文
2. 在 submit_message_stream 中组装 QueryParams 并调用 query()
3. 接收流式事件并同步回写会话状态
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Dict, List, Optional, Any
from uuid import uuid4

from anthropic import Anthropic, AsyncAnthropic
from anthropic.types import Message, MessageStreamEvent

from codo.tools_registry import get_all_tools
from codo.session import SessionStorage
# 导入新的 query 主循环
from codo.query import query, QueryParams, Terminal
# 导入 Prompt 系统
from codo.services.prompt.builder import PromptBuilder
from codo.services.prompt.tools import tools_to_api_schemas
from codo.services.prompt.messages import normalize_messages_for_api
# 兼容旧测试桩：保留 run_tools_batch 模块符号
from codo.services.tools.orchestration import run_tools_batch  # noqa: F401
# 导入 MCP 工具系统
from codo.services.mcp import MCPClientManager, MCPConfigManager
from codo.services.mcp.tool_factory import fetch_all_mcp_tools
# 导入 Compact 和 Token 系统
from codo.services.compact import (
    AutoCompactState,
    CompactResult,
    auto_compact_if_needed,
    compact_conversation,
)
from codo.services.token_estimation import (
    TokenBudget,
    estimate_messages_tokens,
)
from codo.services.compact.microcompact import preview_microcompact
# 导入 API 错误处理
from codo.services.api.errors import (
    classify_api_error,
    format_api_error,
    is_prompt_too_long_error,
    is_retryable,
    APIErrorCategory,
    with_retry,
)
# 导入 Memory 提取系统
from codo.services.memory.extract import (
    extract_memories,
    MemoryExtractionState,
)
from codo.tools.skill_tool import skill_tool
# 导入 AbortController
from codo.utils.abort_controller import AbortController, AbortedError, get_abort_message
from codo.runtime_protocol import QueryRuntimeController, RuntimeCheckpoint, RuntimeCommand, RuntimeEvent

logger = logging.getLogger(__name__)

class QueryEngine:
    """
    管理单个会话的完整生命周期与状态流转。

    每个会话实例对应一个 QueryEngine，避免跨会话状态串扰。

    [Workflow]
    1. 由构造函数建立运行时依赖和上下文
    2. 通过 submit_message_stream 驱动对话主循环
    3. 在 cleanup 或析构阶段执行资源收尾
    """

    @classmethod
    async def from_session_id(
        cls,
        session_id: str,
        api_key: str,
        cwd: str,
        verbose: bool = False,
        model: str = "claude-opus-4-20250514",
    ) -> "QueryEngine":
        """
        从现有会话 ID 创建 QueryEngine 并恢复历史

        [Workflow]
        1. 创建 QueryEngine 实例
        2. 自动恢复会话历史
        3. 返回实例

        参数:
            session_id: 会话 ID
            api_key: API 密钥
            cwd: 工作目录
            verbose: 是否输出详细日志
            model: 模型名称

        返回:
            QueryEngine: 已恢复历史的实例
        """
        engine = cls(
            api_key=api_key,
            cwd=cwd,
            verbose=verbose,
            model=model,
            session_id=session_id,
            enable_persistence=True,
        )

        # 恢复会话历史
        engine.restore_session()

        return engine

    def __init__(
        self,
        api_key: Optional[str] = None,
        cwd: str = ".",
        verbose: bool = False,
        model: str = "claude-opus-4-20250514",
        session_id: Optional[str] = None,
        enable_persistence: bool = True,
        initial_messages: Optional[List[Dict[str, Any]]] = None,
        base_url: Optional[str] = None,
        max_turns: Optional[int] = None,
        thinking_config: Optional[Dict[str, Any]] = None,
        client: Optional[Any] = None,
    ):
        """
        初始化 QueryEngine 运行环境

        [Workflow]
        1. 创建模型客户端与会话基础状态
        2. 建立工具执行上下文与持久化能力
        3. 初始化工具池、Token 预算与记忆提取状态
        """
        self.api_key = api_key or ""
        self.cwd = cwd
        self.verbose = verbose
        self.model = model
        self.max_turns = max_turns

        # 扩展思考配置

        self.thinking_config = thinking_config  # None 表示禁用，字典表示启用并携带预算

        # 客户端
        if client is not None:
            self.client = client
        else:
            if not api_key:
                raise ValueError("api_key is required when client is not provided")
            client_kwargs = {"api_key": api_key}
            if base_url:
                client_kwargs["base_url"] = base_url
            self.client = AsyncAnthropic(**client_kwargs)

        # 会话状态
        self.session_id = session_id or str(uuid4())
        self.messages: List[Dict[str, Any]] = initial_messages or []
        self.enable_persistence = enable_persistence

        # 轮次计数（从 1 开始，而非 0）

        self.turn_count = 1

        # 用户中断控制器

        self.abort_controller = AbortController()
        self._runtime_controller: Optional[QueryRuntimeController] = None
        self._runtime_pump_task: Optional[asyncio.Task[Any]] = None
        self._runtime_command_task: Optional[asyncio.Task[Any]] = None
        self._archived_checkpoints: dict[str, RuntimeCheckpoint] = {}

        # 执行上下文（用于工具编排）
        from codo.services.tools.permission_checker import create_default_permission_context

        self.execution_context = {
            "cwd": cwd,
            "session_id": self.session_id,
            "permission_context": create_default_permission_context(cwd),
            "abort_controller": self.abort_controller,  # 传递 AbortController 到工具执行层
            "options": {
                "api_client": self.client,
                "model": self.model,
                "cwd": cwd,
                "tools": [],  # 稍后在 refresh_mcp_tools 中填充
                # AskUserQuestion 在运行时启用宽松问号规范化，降低模型格式波动导致的失败率
                "normalize_question_mark": True,
            },
        }

        # 会话存储
        if enable_persistence:
            self.session_storage = SessionStorage(self.session_id, cwd)
        else:
            self.session_storage = None

        # MCP 客户端管理器
        self.mcp_config_manager = MCPConfigManager(cwd)
        self.mcp_client_manager = MCPClientManager(self.mcp_config_manager)

        # Token 预算与自动压缩状态

        self.token_budget = TokenBudget(model)
        self.auto_compact_state = AutoCompactState()

        # 记忆提取状态

        self.memory_extraction_state = MemoryExtractionState()

        # 内置工具（统一从 registry 获取，避免多处手写列表漂移）
        self.builtin_tools = get_all_tools()

        # MCP 工具（动态加载）
        self.mcp_tools: List[Any] = []

        # 合并工具池（内置工具 + MCP 工具）
        self.tools = self.builtin_tools + self.mcp_tools

        # 同步 execution_context 中的工具列表
        self.execution_context["options"]["tools"] = self.tools

        # API 工具模式定义（将在 submit_message 中异步生成）
        self.tool_schemas = None

        # 提示词构建器（用于生成系统提示词）
        self.prompt_builder = PromptBuilder(cwd=cwd)
        self._skill_catalog_signature: tuple[Any, ...] = ()
        self.refresh_skills()

    def refresh_skills(self) -> int:
        """重新加载当前工作目录可见的 skill，并在目录变更时失效工具 schema。"""
        loaded = skill_tool.load_all_skills(self.cwd)
        signature = tuple(
            (
                item.name,
                item.description,
                item.prompt,
                tuple(item.allowed_tools),
                item.model,
                item.user_invocable,
                item.source_path,
            )
            for item in skill_tool.list_skills()
        )
        if signature != self._skill_catalog_signature:
            self._skill_catalog_signature = signature
            self.tool_schemas = None
        return loaded

    async def refresh_mcp_tools(self) -> int:
        """
        刷新 MCP 工具列表

        [Workflow]
        1. 从所有已连接的 MCP 服务器获取工具
        2. 更新 self.mcp_tools
        3. 重新合并工具池（内置工具 + MCP 工具）
        4. 重新生成工具模式定义（如果已经生成过）
        5. 返回 MCP 工具数量

        返回:
            int: MCP 工具数量
        """
        # 获取所有 MCP 工具
        self.mcp_tools = await fetch_all_mcp_tools(self.mcp_client_manager)

        # 重新合并工具池
        self.tools = self.builtin_tools + self.mcp_tools

        # 更新 execution_context 中的工具列表（供 AgentTool 使用）
        self.execution_context["options"]["tools"] = self.tools

        # 如果已经生成过工具模式定义，则按最新工具池重新生成
        if self.tool_schemas is not None:
            self.tool_schemas = await tools_to_api_schemas(self.tools)

        return len(self.mcp_tools)

    def restore_session(self) -> bool:
        """
        从磁盘恢复会话

        [Workflow]
        1. 检查是否启用持久化
        2. 加载会话消息历史
        3. 恢复到 self.messages
        4. 返回是否成功恢复

        返回:
            bool: 是否成功恢复会话
        """
        if not self.enable_persistence or not self.session_storage:
            return False

        try:
            # 加载消息历史
            loaded_messages = self.session_storage.load_messages()

            if loaded_messages:
                self.messages = loaded_messages
                try:
                    runtime_state = self.session_storage.load_runtime_state()
                except Exception:
                    runtime_state = {}
                self._restore_runtime_state(runtime_state)
                if self.verbose:
                    print(f"[会话恢复] 已加载 {len(loaded_messages)} 条消息")
                return True
            else:
                if self.verbose:
                    print("[会话恢复] 未找到历史消息，开始新会话")
                return False

        except Exception as e:
            if self.verbose:
                print(f"[会话恢复] 恢复失败: {e}")
            return False

    def _restore_runtime_state(self, runtime_state: Dict[str, Any]) -> None:
        if not isinstance(runtime_state, dict):
            return

        options = self.execution_context.setdefault("options", {})
        if not isinstance(options, dict):
            options = {}
            self.execution_context["options"] = options

        restored_app_state = runtime_state.get("app_state")
        if isinstance(restored_app_state, dict):
            app_state = dict(options.get("app_state", {}) or {})
            todos = restored_app_state.get("todos")
            if isinstance(todos, dict):
                app_state["todos"] = {
                    str(key): [dict(item) for item in value if isinstance(item, dict)]
                    for key, value in todos.items()
                    if isinstance(value, list)
                }
            options["app_state"] = app_state

        permission_mode = runtime_state.get("permission_mode")
        if permission_mode:
            try:
                from codo.types.permissions import PermissionMode

                permission_context = self.execution_context.get("permission_context")
                if permission_context is not None:
                    permission_context.mode = PermissionMode(str(permission_mode))
            except Exception:
                pass

    async def submit_message_stream(
        self,
        prompt: str,
        checkpoint_id: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        提交消息并产出流式事件（用于 StreamManager）

        [Workflow]
        1. 检查是否已中断并按需提前返回错误事件
        2. 刷新工具池、补充用户消息、构建系统提示词
        3. 组装 QueryParams 并进入 query() 主循环
        4. 转发事件并在终止时同步会话历史

        [重构说明]
        - 现在调用独立的 query() 主循环
        - QueryEngine 负责准备参数和处理结果
        - query() 负责核心循环逻辑

        产出事件格式：
        - {"type": "text_delta", "delta": {"text": "..."}}
        - {"type": "tool_result", "tool_use_id": "...", "content": "...", "is_error": False}
        - {"type": "content_block_start", "index": 0, "content_block": {...}}
        - {"type": "content_block_stop", "index": 0}
        - {"type": "message_stop"}
        - {"type": "compact", "result": {...}}
        - {"type": "error", "error": "..."}
        """

        if checkpoint_id:
            self._apply_checkpoint_restore(checkpoint_id)

        # 检查是否已中断
        if self.abort_controller.is_aborted():
            yield {
                "type": "error",
                "error": get_abort_message(self.abort_controller.get_reason()),
                "error_type": "user_interrupted",
            }
            return

        self.refresh_skills()

        # 刷新 MCP 工具（处理运行中新增连接的 MCP 服务端）
        await self.refresh_mcp_tools()

        # 添加用户消息（仅当 prompt 非空时）
        if prompt:
            user_message = {
                "role": "user",
                "content": prompt,
                "uuid": str(uuid4()),
                "type": "user",
            }
            self.messages.append(user_message)

            # 持久化用户消息
            if self.session_storage:
                self.session_storage.save_last_prompt(prompt)
                self.session_storage.record_messages([user_message])

        # 生成工具模式（首次请求时按当前工具池懒加载）
        if self.tool_schemas is None:
            self.tool_schemas = await tools_to_api_schemas(self.tools)

        # 构建系统提示词
        system_prompt_blocks = self.prompt_builder.build_system_prompt(
            language_preference="zh-CN",
        )
        # 提取文本（query() 期望字符串）
        system_prompt = "\n\n".join(block["text"] for block in system_prompt_blocks)

        # 把 system_prompt 和 model 同步到 execution_context，供 fork 模式的子代理继承
        self.execution_context["options"]["system_prompt"] = system_prompt
        self.execution_context["options"]["model"] = self.model

        # 调试输出（用于观察提示词和上下文规模）
        if self.verbose:
            token_count = estimate_messages_tokens(normalize_messages_for_api(self.messages))
            print(f"[DEBUG] Model: {self.model}")
            print(f"[DEBUG] System prompt length: {len(system_prompt)}")
            print(f"[DEBUG] Messages count: {len(self.messages)}")
            print(f"[DEBUG] Tools count: {len(self.tool_schemas)}")
            print(f"[DEBUG] Estimated tokens: {token_count}")

        # ====================================================================
        # 调用新的 query() 主循环
        # ====================================================================
        from codo.query import QueryParams, Terminal

        runtime_controller = QueryRuntimeController()
        self._runtime_controller = runtime_controller
        runtime_execution_context = dict(self.execution_context)
        runtime_execution_context["interaction_broker"] = runtime_controller
        runtime_execution_context["runtime_controller"] = runtime_controller

        query_params = QueryParams(
            client=self.client,
            model=self.model,
            system_prompt=system_prompt,
            messages=self.messages.copy(),  # 传递副本
            tools=self.tools,
            tool_schemas=self.tool_schemas,
            execution_context=runtime_execution_context,
            cwd=self.cwd,
            session_id=self.session_id,
            max_turns=self.max_turns,
            enable_persistence=self.enable_persistence,
            session_storage=self.session_storage,
            memory_extraction_state=self.memory_extraction_state,
            verbose=self.verbose,
            thinking_config=self.thinking_config,
        )

        async def _pump_query_events() -> None:
            try:
                async for event in query(query_params):
                    await runtime_controller.emit(event)
            except asyncio.CancelledError:
                await runtime_controller.emit(
                    RuntimeEvent(
                        type="error",
                        payload={
                            "error": get_abort_message(self.abort_controller.get_reason()),
                            "error_type": "user_interrupted",
                            "recoverable": False,
                        },
                    )
                )
                raise
            except Exception as exc:
                await runtime_controller.emit(
                    RuntimeEvent(
                        type="error",
                        payload={
                            "error": str(exc),
                            "error_type": "runtime_error",
                            "recoverable": False,
                        },
                    )
                )
            finally:
                await runtime_controller.finish()

        async def _pump_runtime_commands() -> None:
            while True:
                command = await runtime_controller.next_command()
                if command is QueryRuntimeController._COMMAND_SENTINEL:
                    return
                if not isinstance(command, RuntimeCommand):
                    continue

                if command.type == "interrupt":
                    self.abort_controller.abort("interrupt")
                    if self._runtime_pump_task is not None and not self._runtime_pump_task.done():
                        self._runtime_pump_task.cancel()
                elif command.type == "resolve_interaction":
                    request_id = str(command.payload.get("request_id", "") or "")
                    runtime_controller.resolve_interaction(request_id, command.payload.get("data"))
                elif command.type == "cancel_interaction":
                    request_id = str(command.payload.get("request_id", "") or "")
                    runtime_controller.cancel_interaction(request_id)
                elif command.type == "retry_checkpoint":
                    checkpoint_ref = command.payload.get("checkpoint_id")
                    if checkpoint_ref:
                        checkpoint = self._lookup_checkpoint(str(checkpoint_ref))
                        if checkpoint is not None:
                            self._restore_from_checkpoint(checkpoint)
                            await runtime_controller.emit_runtime_event(
                                "checkpoint_restored",
                                checkpoint_id=checkpoint.checkpoint_id,
                                phase=checkpoint.phase,
                            )
                elif command.type == "switch_sidebar_focus":
                    await runtime_controller.emit_runtime_event(
                        "sidebar_focus_changed",
                        sidebar_mode=str(command.payload.get("sidebar_mode", "") or "auto"),
                        auto_follow=bool(command.payload.get("auto_follow", True)),
                        source=str(command.payload.get("source", "ui") or "ui"),
                    )

        pump_task = asyncio.create_task(_pump_query_events())
        command_task = asyncio.create_task(_pump_runtime_commands())
        self._runtime_pump_task = pump_task
        self._runtime_command_task = command_task

        terminal = None
        try:
            while True:
                event = await runtime_controller.next_event()
                if event is QueryRuntimeController._SENTINEL:
                    break

                if isinstance(event, RuntimeEvent):
                    legacy_event = event.as_legacy_event()
                    if self.session_storage:
                        self.session_storage.record_runtime_event(legacy_event)
                    yield legacy_event
                    continue

                if isinstance(event, Terminal):
                    terminal = event
                    if self.session_storage:
                        self.session_storage.record_runtime_event(
                            {
                                "type": "turn_completed",
                                "reason": event.reason,
                                "metadata": dict(event.metadata or {}),
                            }
                        )
                    # 同步消息历史回 QueryEngine
                    # 原因：query() 内部会推进消息状态，这里需要回写到引擎实例
                    # 当前策略：直接从 session_storage 重新加载，保证与持久化一致
                    if self.session_storage:
                        self.messages = self.session_storage.load_messages()
                    elif isinstance(event.metadata, dict) and event.metadata.get("messages") is not None:
                        self.messages = event.metadata.get("messages")
                    yield event
                    break

                # 在调用方提前中断消费（例如遇到 error 事件就 break）之前，
                # 先把关键状态同步回引擎实例，避免 turn_count 丢失。
                if (
                    isinstance(event, dict)
                    and event.get("type") == "error"
                    and event.get("error_type") == "max_turns_reached"
                ):
                    event_turn_count = event.get("turn_count")
                    if isinstance(event_turn_count, int) and event_turn_count > 0:
                        self.turn_count = event_turn_count - 1
                if self.session_storage and isinstance(event, dict):
                    self.session_storage.record_runtime_event(event)
                yield event
        finally:
            try:
                await pump_task
            except asyncio.CancelledError:
                pass
            try:
                await command_task
            except asyncio.CancelledError:
                pass
            self._archived_checkpoints.update(runtime_controller.export_checkpoints())
            self._runtime_pump_task = None
            self._runtime_command_task = None
            self._runtime_controller = None

        # 处理终止结果
        if terminal:
            # 同步 query 循环中的轮次计数
            if isinstance(terminal.metadata, dict):
                self.turn_count = terminal.metadata.get("turn_count", self.turn_count)

            if terminal.reason == "max_turns":
                # maxTurns 已在 query() 中处理
                pass
            elif terminal.reason == "completed":
                # 正常完成后，触发会话标题自动生成（fire-and-forget）

                # 只在没有用户自定义标题时生成
                if (
                    self.session_storage
                    and not getattr(self.session_storage, "current_title", None)
                    and self.messages
                ):
                    try:
                        from codo.session.title import generate_and_save_title
                        # fire-and-forget：不等待标题生成完成
                        asyncio.create_task(
                            generate_and_save_title(
                                client=self.client,
                                model=self.model,
                                messages=self.messages,
                                session_storage=self.session_storage,
                            )
                        )
                    except RuntimeError:
                        # 无事件循环时跳过（如在同步上下文中）
                        pass
            elif terminal.reason in ("aborted", "error", "prompt_too_long"):
                # 错误已在 query() 中产出
                pass

    async def compact(
        self,
        custom_instructions: str = None,
    ) -> CompactResult:
        """
        手动执行 compact（对应 /compact 命令）

        [Workflow]
        1. 构建系统提示词并规范化当前消息
        2. 调用 compact_conversation 执行压缩
        3. 回写消息历史并持久化 compact 边界
        4. 更新自动压缩状态并返回结果

        参数:
            custom_instructions: 自定义压缩指令

        返回:
            CompactResult: 压缩结果对象
        """
        system_prompt = self.prompt_builder.build_system_prompt(
            language_preference="zh-CN",
        )

        normalized = normalize_messages_for_api(self.messages)
        if not normalized:
            raise ValueError("No messages to compact.")

        result = await compact_conversation(
            client=self.client,
            model=self.model,
            system_prompt=system_prompt,
            messages=normalized,
            custom_instructions=custom_instructions,
            suppress_follow_up=False,
            transcript_path=self._get_transcript_path(),
        )

        # 替换消息历史
        self.messages = result.new_messages

        # 持久化 compact 标记
        if self.session_storage:
            self.session_storage.record_messages([{
                "type": "compact_boundary",
                "uuid": str(uuid4()),
                "pre_compact_tokens": result.pre_compact_token_count,
                "post_compact_tokens": result.post_compact_token_count,
            }])

        self.auto_compact_state.record_success()
        return result

    def get_token_usage(self) -> Dict[str, Any]:
        """
        获取当前 token 使用统计

        [Workflow]
        1. 调用统一上下文统计入口（运行时口径）
        2. 返回兼容旧调用方的 token 字段
        3. 附带额外统计字段供 UI 直接展示

        返回:
            Dict[str, Any]: Token 使用统计字典
        """
        return self.get_context_stats()

    def get_context_stats(self) -> Dict[str, Any]:
        """
        获取上下文统计（统一口径）。

        [Workflow]
        1. 统计会话存档中的总消息数（session jsonl 口径）
        2. 统计当前分支模型可见消息数（normalize 后）
        3. 预览 microcompact 后运行时消息并估算 token
        4. 返回 token 预算信息和多口径计数

        说明:
            - 这里不执行真实 auto-compact，避免在查看统计时触发压缩副作用。
            - token_count 统一采用“运行时消息（microcompact 后）”估算值。
        """
        # 口径 1：会话存档总消息数（包含历史分支）
        session_message_count = 0
        if self.session_storage:
            try:
                session_info = self.session_storage.get_session_info()
                session_message_count = int(session_info.get("message_count", 0))
            except Exception:
                session_message_count = 0

        # 口径 2：当前分支模型可见消息（标准化后）
        normalized_messages = normalize_messages_for_api(self.messages)
        model_visible_message_count = len(normalized_messages)
        model_visible_token_count = estimate_messages_tokens(normalized_messages)

        # 口径 3：运行时消息（对齐 query_loop 的 microcompact 阶段）
        runtime_messages = self.messages
        runtime_compacted_count = 0
        try:
            preview = preview_microcompact(
                messages=self.messages,
                context={"session_id": self.session_id},
            )
            runtime_messages = preview.messages
            runtime_compacted_count = preview.compacted_count
        except Exception:
            runtime_messages = self.messages
            runtime_compacted_count = 0

        runtime_normalized_messages = normalize_messages_for_api(runtime_messages)
        runtime_message_count = len(runtime_normalized_messages)
        runtime_token_count = estimate_messages_tokens(runtime_normalized_messages)

        usage = self.token_budget.get_usage_stats(runtime_token_count)
        effective_window = int(usage.get("effective_context_window", 0) or 0)
        usage.update(
            {
                "session_message_count": session_message_count,
                "model_visible_message_count": model_visible_message_count,
                "model_visible_token_count": model_visible_token_count,
                "runtime_message_count": runtime_message_count,
                "runtime_microcompact_compacted_count": runtime_compacted_count,
                "token_count_source": "runtime_after_microcompact",
                "remaining_tokens": max(0, effective_window - runtime_token_count),
            }
        )
        return usage

    def _get_transcript_path(self) -> Optional[str]:
        """
        获取会话记录文件路径

        [Workflow]
        1. 检查是否启用了会话存储
        2. 如果启用则返回会话文件绝对路径
        3. 否则返回 None
        """
        if self.session_storage:
            return str(self.session_storage.session_file)
        return None

    def interrupt(self):
        """
        触发用户中断（Ctrl+C）

        [Workflow]
        1. 调用 abort_controller.abort('interrupt')
        2. 中断信号传播到所有工具执行层
        3. 正在执行的工具会收到中断信号并优雅退出
        """
        self.send_control(RuntimeCommand(type="interrupt"))

    def send_control(self, command: RuntimeCommand | Dict[str, Any]) -> None:
        """发送运行时控制命令到当前活动会话。"""
        if isinstance(command, dict):
            command = RuntimeCommand(
                type=str(command.get("type", "")),
                payload={key: value for key, value in command.items() if key != "type"},
            )
        if self._runtime_controller is not None:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._runtime_controller.send_command(command))
                return
            except RuntimeError:
                pass

        if command.type == "interrupt":
            self.abort_controller.abort("interrupt")
            if self._runtime_pump_task is not None and not self._runtime_pump_task.done():
                self._runtime_pump_task.cancel()
        elif command.type == "resolve_interaction":
            request_id = str(command.payload.get("request_id", "") or "")
            controller = self._runtime_controller
            if controller is not None:
                controller.resolve_interaction(request_id, command.payload.get("data"))
        elif command.type == "cancel_interaction":
            request_id = str(command.payload.get("request_id", "") or "")
            controller = self._runtime_controller
            if controller is not None:
                controller.cancel_interaction(request_id)
        elif command.type == "retry_checkpoint":
            checkpoint_ref = command.payload.get("checkpoint_id")
            if checkpoint_ref:
                checkpoint = self._lookup_checkpoint(str(checkpoint_ref))
                if checkpoint is not None:
                    self._restore_from_checkpoint(checkpoint)
        elif command.type == "switch_sidebar_focus":
            return

    def resolve_interaction(self, request_id: str, data: Any) -> None:
        """完成当前运行时交互请求。"""
        self.send_control(
            RuntimeCommand(
                type="resolve_interaction",
                payload={"request_id": request_id, "data": data},
            )
        )

    def retry_checkpoint(self, checkpoint_id: str) -> Optional[RuntimeCheckpoint]:
        """恢复到指定 checkpoint 对应的运行时快照。"""
        checkpoint = self._lookup_checkpoint(checkpoint_id)
        if checkpoint is None:
            return None
        self._restore_from_checkpoint(checkpoint)
        self.send_control(
            RuntimeCommand(
                type="retry_checkpoint",
                payload={"checkpoint_id": checkpoint_id},
            )
        )
        return checkpoint

    def reset_interrupt_state(self):
        """
        重置中断状态，允许下一次新查询继续执行。

        [Workflow]
        1. 检查当前 abort_controller 是否已中断
        2. 若已中断则创建新的 AbortController
        3. 同步更新 execution_context 中的 abort_controller 引用
        """
        if not self.abort_controller.is_aborted():
            return
        self.abort_controller = AbortController()
        self.execution_context["abort_controller"] = self.abort_controller

    def _lookup_checkpoint(self, checkpoint_id: str) -> Optional[RuntimeCheckpoint]:
        if self._runtime_controller is not None:
            checkpoint = self._runtime_controller.get_checkpoint(checkpoint_id)
            if checkpoint is not None:
                return checkpoint
        return self._archived_checkpoints.get(checkpoint_id)

    def _restore_from_checkpoint(self, checkpoint: RuntimeCheckpoint) -> None:
        metadata = dict(checkpoint.metadata or {})
        messages = metadata.get("messages_state")
        if not isinstance(messages, list):
            messages = metadata.get("messages")
        if isinstance(messages, list):
            self.messages = [dict(message) if isinstance(message, dict) else message for message in messages]
        self.turn_count = max(1, int(checkpoint.turn_count))
        self.reset_interrupt_state()

    def _apply_checkpoint_restore(self, checkpoint_id: str) -> Optional[RuntimeCheckpoint]:
        checkpoint = self._lookup_checkpoint(checkpoint_id)
        if checkpoint is None:
            return None
        self._restore_from_checkpoint(checkpoint)
        return checkpoint

    def _fire_memory_extraction(self):
        """
        以后台任务方式触发记忆提取（发起后不等待）。

        [Workflow]
        1. 当前实现仅保留兼容入口
        2. 实际记忆提取逻辑已迁移到 query.py
        3. 记录调试日志提示调用方迁移
        """
        logger.debug("[memory] _fire_memory_extraction is deprecated, use query.py instead")

    async def _run_memory_extraction(self):
        """
        执行记忆提取协程（兼容保留）。

        [Workflow]
        1. 当前实现仅保留兼容入口
        2. 实际执行应调用 query.py 中的新实现
        3. 输出调试日志避免静默调用
        """
        logger.debug("[memory] _run_memory_extraction is deprecated, use query.py instead")

    async def submit_message(
        self,
        prompt: str,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        提交消息并流式返回结果（兼容旧接口）。

        [Workflow]
        1. 记录废弃告警
        2. 直接代理到 submit_message_stream()
        3. 逐条透传事件给调用方

        注意：此方法已废弃，请使用 submit_message_stream()。

        产出事件格式：
        - {"type": "assistant", "content": [...]}
        - {"type": "tool_result", "tool_use_id": "...", "content": "..."}
        - {"type": "error", "error": "..."}
        """
        logger.warning("submit_message() is deprecated, use submit_message_stream() instead")
        async for event in self.submit_message_stream(prompt):
            yield event

    async def _execute_tool(self, tool_use: Dict[str, Any]) -> str:
        """
        执行工具并返回结果（兼容占位实现）。

        [Workflow]
        1. 保留旧接口签名避免调用方报错
        2. 返回固定废弃提示，引导迁移到新执行链路
        3. 不再承担真实工具执行职责

        注意：此方法已被 query.py 中的 StreamingToolExecutor 替代，保留用于向后兼容。
        新代码路径：QueryEngine.submit_message_stream() -> query() -> StreamingToolExecutor
        """
        return "Deprecated: use query() with StreamingToolExecutor instead"

    def _save_session(self):
        """
        保存会话状态到磁盘

        [Workflow]
        1. 检查是否启用持久化
        2. 如果启用，确保所有消息已保存
        3. 保存会话元数据（如果需要）

        注意：
        - 消息已通过 record_message() 实时保存到 JSONL
        - 此方法主要用于同步收尾和元数据保存
        """
        if not self.enable_persistence or not self.session_storage:
            return

        # 会话元数据已通过 record_message 实时保存
        # 这里可以添加额外的元数据保存逻辑（如会话标题、标签等）
        # 当前实现：消息已实时保存，无需额外操作
        pass

    def save_title(self, title: str, source: str = "user"):
        """
        保存会话标题

        [Workflow]
        1. 检查会话存储是否可用
        2. 将标题与来源写入会话元数据

        参数:
            title: 会话标题
            source: 来源（"user" 或 "ai"）
        """
        if self.session_storage:
            self.session_storage.save_title(title, source)

    async def set_session_title(self, title: str):
        """
        兼容旧接口：异步设置会话标题。

        历史调用链（如 REPL 命令）使用 `set_session_title`，内部复用 `save_title`。
        """
        self.save_title(title, source="user")

    def save_tag(self, tag: str):
        """
        保存会话标签

        [Workflow]
        1. 检查会话存储是否可用
        2. 将标签写入会话元数据

        参数:
            tag: 标签名称
        """
        if self.session_storage:
            self.session_storage.save_tag(tag)

    def save_summary(self, summary: str):
        """
        保存会话摘要

        [Workflow]
        1. 检查会话存储是否可用
        2. 将摘要写入会话元数据

        参数:
            summary: 会话摘要
        """
        if self.session_storage:
            self.session_storage.save_summary(summary)

    def cleanup(self):
        """
        清理会话资源

        [Workflow]
        1. 保存会话状态
        2. 清理临时资源
        3. 关闭连接

        注意：
        - 应在会话结束时调用
        - 确保所有数据已持久化
        """
        # 保存会话状态
        self._save_session()

        # 清理其他资源（如果有）
        # 当前实现：无需额外清理

    def __del__(self):
        """
        析构函数：确保会话状态被保存

        [Workflow]
        1. 在对象销毁阶段尝试保存会话
        2. 若保存失败则吞掉异常，避免影响进程退出
        """
        try:
            self._save_session()
        except Exception:
            # 析构函数中忽略异常，避免影响程序退出
            pass
