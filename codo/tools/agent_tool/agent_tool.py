"""
AgentTool 核心实现

- AgentTool.tsx — call() 方法 (line 239-400+)
- runAgent.ts — runAgent() 子代理对话循环 (line 248-800+)
- forkSubagent.ts — fork 模式（继承父上下文）

支持两种模式：
- fresh 模式（有 subagent_type）：创建专门角色的子代理，有独立系统提示和工具集
- fork 模式（无 subagent_type）：继承父代理上下文，适合通用子任务
"""

import logging
import json
from dataclasses import asdict
from typing import Optional, Callable, Any, Dict, List, Awaitable

from ..base import Tool, ToolUseContext, build_tool
from ..receipts import AgentReceipt, receipt_to_dict
from ..types import ToolResult, ValidationResult, ToolCallProgress
from .types import AgentToolInput, AgentToolOutput
from .prompt import (
    AGENT_TOOL_NAME,
    DESCRIPTION,
    MAX_AGENT_TURNS,
    DEFAULT_AGENT_TYPE,
    get_agent_tool_prompt,
)
from .agents import AgentDefinition, find_agent_by_type, get_builtin_agents
from .utils import filter_tools_for_agent, extract_final_text

logger = logging.getLogger(__name__)

def _serialize_runtime_value(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__") and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, list):
        return [_serialize_runtime_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize_runtime_value(item) for key, item in value.items()}
    return value

@build_tool(
    name=AGENT_TOOL_NAME,
    max_result_size_chars=100_000,
    input_schema=AgentToolInput,
    output_schema=AgentToolOutput,
    aliases=["Task"],
    search_hint="delegate work to a subagent",
    is_read_only=lambda _: True,      # AgentTool 本身不直接修改文件
    is_concurrency_safe=lambda _: False,  # 子代理对话循环不应并发
)
class AgentTool(Tool[AgentToolInput, AgentToolOutput, None]):
    """
    子代理工具

    生成独立的子代理来执行特定任务（搜索、规划等）。
    支持两种模式：
    - fresh 模式：有 subagent_type 时，创建专门角色的子代理
    - fork 模式：无 subagent_type 时，继承父代理上下文

    """

    async def call(
        self,
        args: AgentToolInput,
        context: ToolUseContext,
        can_use_tool: Callable,
        parent_message: Any,
        on_progress: Optional[ToolCallProgress] = None,
    ) -> ToolResult[AgentToolOutput]:
        """
        执行子代理

        [Workflow]
        1. 判断模式：有 subagent_type → fresh 模式；无 → fork 模式
        2. 从 context 获取 API client 和可用工具
        3. 调用 run_subagent_with_mode 执行（接入 team 模块）
        4. 返回结果

        """
        # 从 context 获取 API client 和配置。
        # 运行时链路会统一传 ToolUseContext；这里额外保留对测试里 MagicMock 上下文的兜底。
        tool_context: ToolUseContext | None = None
        if isinstance(context, ToolUseContext):
            tool_context = context
        elif isinstance(context, dict):
            tool_context = ToolUseContext.coerce(context)

        if tool_context is not None:
            options = tool_context.get_options()
        elif isinstance(getattr(context, "options", None), dict):
            options = context.options
        else:
            options = {}
        api_client = options.get("api_client")
        if not api_client:
            return ToolResult(
                error="API client not available in context. Cannot run sub-agent."
            )

        cwd = "."
        maybe_cwd = tool_context.get("cwd", None) if tool_context is not None else None
        if isinstance(maybe_cwd, str) and maybe_cwd:
            cwd = maybe_cwd
        if cwd == ".":
            cwd = options.get("cwd", ".")
        parent_model = options.get("model", "claude-sonnet-4-20250514")

        runtime_controller = None
        interaction_broker = None
        session_id = options.get("session_id")
        permission_context = options.get("permission_context")
        if tool_context is not None:
            runtime_candidate = tool_context.get("runtime_controller")
            if callable(getattr(runtime_candidate, "emit_runtime_event", None)):
                runtime_controller = runtime_candidate
            interaction_candidate = tool_context.get("interaction_broker")
            if callable(getattr(interaction_candidate, "request", None)):
                interaction_broker = interaction_candidate
            permission_candidate = tool_context.get("permission_context")
            if permission_candidate is not None:
                permission_context = permission_candidate
            session_id = tool_context.get("session_id", session_id)

        # 构建子代理执行上下文（传递给 team 模块）
        subagent_context = {
            "api_client": api_client,
            "tools": options.get("tools", []),
            "model": parent_model,
            "cwd": cwd,
            "system_prompt": options.get("system_prompt", ""),
            "agent_id": options.get("agent_id"),
            "runtime_controller": runtime_controller or options.get("runtime_controller"),
            "interaction_broker": interaction_broker or runtime_controller or options.get("interaction_broker"),
            "permission_context": permission_context,
            "session_id": session_id,
        }

        logger.info(
            f"[Agent] Starting sub-agent: {args.description} "
            f"(type={args.subagent_type or 'fork'}, model={parent_model}, "
            f"background={bool(getattr(args, 'run_in_background', False))})"
        )

        try:
            # 接入 team 模块的 run_subagent_with_mode
            from codo.team.enhanced_agent import run_subagent_with_mode

            # 兼容旧行为：未显式指定类型时默认走 Explore（而非 fork）
            effective_args = args
            if not args.subagent_type:
                effective_args = args.model_copy(
                    update={"subagent_type": DEFAULT_AGENT_TYPE}
                )

            result = await run_subagent_with_mode(
                args=effective_args,
                context=subagent_context,
                run_in_background=bool(getattr(effective_args, "run_in_background", False)),
            )

            if "error" in result:
                return ToolResult(error=result["error"])

            result_text = result.get("result", "")
            if result.get("is_background"):
                logger.info(
                    f"[Agent] Sub-agent started in background "
                    f"(mode={result.get('mode', 'unknown')}, task_id={result.get('task_id', '')})"
                )
            else:
                logger.info(
                    f"[Agent] Sub-agent completed (mode={result.get('mode', 'unknown')}). "
                    f"Tokens: {result.get('total_tokens', 0)}"
                )

        except Exception as e:
            logger.error(f"[Agent] Sub-agent failed: {e}")
            return ToolResult(error=f"Sub-agent error: {str(e)}")

        output = AgentToolOutput(
            result=result_text,
            total_tokens=result.get("total_tokens", 0),
            input_tokens=result.get("input_tokens", 0),
            output_tokens=result.get("output_tokens", 0),
            background=bool(result.get("is_background", False)),
            task_id=result.get("task_id"),
            status=result.get("status"),
        )

        agent_type = getattr(effective_args, "subagent_type", None) or DEFAULT_AGENT_TYPE
        receipt = AgentReceipt(
            kind="agent",
            summary=result_text or f"{agent_type} agent finished",
            agent_id=str(result.get("agent_id", "") or ""),
            agent_type=agent_type,
            mode=str(result.get("mode", "") or ""),
            task_id=result.get("task_id"),
            background=bool(result.get("is_background", False)),
            status=str(result.get("status", "completed") or "completed"),
            result_preview=result_text,
            total_tokens=int(result.get("total_tokens", 0) or 0),
        )

        return ToolResult(data=output, receipt=receipt)

    async def description(self, input_data: AgentToolInput, options: dict) -> str:
        return DESCRIPTION

    async def prompt(self, options: dict) -> str:
        """
        生成 AgentTool 的描述

        使用 options 中的 agents 列表动态生成描述
        """
        agents = options.get("agents", None)
        return get_agent_tool_prompt(agents)

    def map_tool_result_to_tool_result_block_param(
        self, content: AgentToolOutput, tool_use_id: str
    ):
        """将子代理结果转换为 API 格式"""
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content.result,
        }

    def user_facing_name(self, input_data=None) -> str:
        if input_data and hasattr(input_data, "subagent_type") and input_data.subagent_type:
            return f"Agent({input_data.subagent_type})"
        return "Agent"

    def get_tool_use_summary(self, input_data=None) -> Optional[str]:
        if input_data and hasattr(input_data, "description"):
            if getattr(input_data, "run_in_background", False):
                return f"{input_data.description} [后台]"
            return input_data.description
        return None

    def get_activity_description(self, input_data=None) -> Optional[str]:
        if input_data and hasattr(input_data, "description"):
            agent_type = getattr(input_data, "subagent_type", None) or DEFAULT_AGENT_TYPE
            if getattr(input_data, "run_in_background", False):
                return f"Running {agent_type} in background: {input_data.description}"
            return f"Running {agent_type}: {input_data.description}"
        return "Running sub-agent"

    def requires_permission(self, input_data) -> bool:
        """AgentTool 不需要额外权限检查（子代理内的工具自己会检查）"""
        return False

# ============================================================================
# 子代理对话循环
# ============================================================================

async def _run_sub_agent(
    client,
    model: str,
    system_prompt: str,
    tools: list,
    prompt: str,
    cwd: str,
    max_turns: int = MAX_AGENT_TURNS,
    agent_id: Optional[str] = None,
    interaction_broker: Optional[Any] = None,
    permission_context: Optional[Any] = None,
    event_callback: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None,
) -> tuple[str, Dict[str, int]]:
    """
    运行子代理对话循环

    简化版 query loop：
    1. 发送用户 prompt
    2. 获取 assistant 回复
    3. 如果有工具调用，执行工具并收集结果
    4. 循环直到没有工具调用或达到 max_turns

    Args:
        client: AsyncAnthropic 客户端
        model: 模型名称
        system_prompt: 子代理 system prompt
        tools: 可用工具列表
        prompt: 用户 prompt
        cwd: 工作目录
        max_turns: 最大对话轮数

    Returns:
        (最终文本输出, token 使用统计)
    """
    # 生成子代理的工具 schema
    tool_schemas = []
    for tool in tools:
        schema = await _tool_to_schema(tool)
        tool_schemas.append(schema)

    # 初始化消息
    messages: List[Dict[str, Any]] = [
        {"role": "user", "content": prompt}
    ]

    # Token 使用统计
    usage = {"input": 0, "output": 0, "total": 0}

    for turn in range(max_turns):
        # 调用 API
        api_kwargs = {
            "model": model,
            "max_tokens": 8192,
            "system": system_prompt,
            "messages": messages,
        }
        if tool_schemas:
            api_kwargs["tools"] = tool_schemas

        response = await client.messages.create(**api_kwargs)

        # 累计 token 使用
        if hasattr(response, "usage"):
            usage["input"] += response.usage.input_tokens
            usage["output"] += response.usage.output_tokens
            usage["total"] += (
                response.usage.input_tokens + response.usage.output_tokens
            )

        # 将 assistant 回复转为可序列化格式并添加到消息历史
        assistant_content = []
        for block in response.content:
            if block.type == "text":
                assistant_content.append({
                    "type": "text",
                    "text": block.text,
                })
            elif block.type == "tool_use":
                assistant_content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        messages.append({
            "role": "assistant",
            "content": assistant_content,
        })

        text_blocks = [block.text for block in response.content if block.type == "text" and getattr(block, "text", "")]
        if event_callback is not None and text_blocks:
            await event_callback(
                "agent_delta",
                {
                    "content_delta": "\n".join(text_blocks).strip(),
                    "status": "active",
                    "turn": turn + 1,
                },
            )

        # 检查是否有工具调用
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            # 没有工具调用，对话结束
            break

        # 执行工具并收集结果
        from codo.services.tools.streaming_executor import StreamingToolExecutor
        from codo.services.tools.permission_checker import create_default_permission_context

        assistant_message = {
            "role": "assistant",
            "content": assistant_content,
        }
        executor_options: Dict[str, Any] = {
            "app_state": {
                "todos": {agent_id: []} if agent_id else {},
            },
            "agent_id": agent_id,
            "session_id": agent_id or "subagent",
            "api_client": client,
            "tools": tools,
            "normalize_question_mark": True,
        }
        executor_context: Dict[str, Any] = {
            "cwd": cwd,
            "agent_id": agent_id,
            "session_id": agent_id or "subagent",
            "permission_context": permission_context or create_default_permission_context(cwd),
            "interaction_broker": interaction_broker,
            "options": executor_options,
        }
        executor = StreamingToolExecutor(tools, executor_context)

        tool_results = []
        for tool_use in tool_uses:
            if event_callback is not None:
                await event_callback(
                    "agent_tool_started",
                    {
                        "tool_use_id": tool_use.id,
                        "tool_name": tool_use.name,
                        "input_preview": json.dumps(tool_use.input, ensure_ascii=False),
                    },
                )
            executor.register_tool(
                {
                    "id": tool_use.id,
                    "name": tool_use.name,
                    "input": tool_use.input,
                },
                assistant_message,
            )

        await executor._process_queue()
        async for update in executor.get_remaining_results():
            message = update.message or {}
            blocks = list(message.get("content", []) or [])
            block = blocks[0] if blocks else {}
            if not isinstance(block, dict):
                continue
            tool_use_id = str(update.tool_use_id or block.get("tool_use_id", "") or "")
            tool_name = next(
                (tool_use.name for tool_use in tool_uses if tool_use.id == tool_use_id),
                str(block.get("tool_name", "") or "Tool"),
            )
            result_content = str(update.content or block.get("content", "") or "")

            if event_callback is not None and tool_use_id:
                completed_payload = {
                    "tool_use_id": tool_use_id,
                    "tool_name": tool_name,
                    "content": result_content,
                    "status": str(update.status or "completed"),
                    "is_error": bool(update.is_error),
                }
                if update.receipt is not None:
                    completed_payload["receipt"] = receipt_to_dict(update.receipt)
                if update.audit_events:
                    completed_payload["audit_events"] = _serialize_runtime_value(update.audit_events)
                await event_callback("agent_tool_completed", completed_payload)

                if agent_id and tool_name == "TodoWrite":
                    app_state = executor_options.get("app_state", {})
                    todos = app_state.get("todos", {}) if isinstance(app_state, dict) else {}
                    raw_items = todos.get(agent_id)
                    if isinstance(raw_items, list):
                        await event_callback(
                            "todo_updated",
                            {
                                "key": agent_id,
                                "items": [dict(item) for item in raw_items if isinstance(item, dict)],
                                "tool_use_id": tool_use_id,
                            },
                        )

            tool_results.append(block)

        # 添加工具结果到消息
        messages.append({
            "role": "user",
            "content": tool_results,
        })
    else:
        # 达到 max_turns 限制
        logger.warning(
            f"[Agent] Sub-agent reached max turns limit ({max_turns})"
        )

    # 提取最终文本
    final_text = extract_final_text(messages)
    return final_text, usage

async def _execute_agent_tool(
    tools: list,
    tool_name: str,
    tool_input: dict,
    cwd: str,
    *,
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    执行子代理中的单个工具

    Args:
        tools: 可用工具列表
        tool_name: 工具名称
        tool_input: 工具输入参数
        cwd: 工作目录

    Returns:
        工具执行结果（字符串）
    """
    # 查找工具
    tool = None
    for t in tools:
        if t.name == tool_name:
            tool = t
            break

    if not tool:
        return {
            "content": f"Error: Tool '{tool_name}' not found",
            "status": "error",
            "receipt": None,
            "audit_events": [],
            "todo_items": None,
        }

    try:
        # 使用简化的 execute() 接口，并为子 agent 保留独立的 todo/app_state 命名空间
        options: Dict[str, Any] = {
            "app_state": {
                "todos": {agent_id: []} if agent_id else {},
            },
            "agent_id": agent_id,
            "session_id": agent_id or "subagent",
        }
        context: Dict[str, Any] = {
            "cwd": cwd,
            "agent_id": agent_id,
            "session_id": agent_id or "subagent",
            "options": options,
        }
        result = await tool.execute(tool_input, context)
        todo_items = None
        if tool_name == "TodoWrite" and agent_id:
            app_state = options.get("app_state", {})
            todos = app_state.get("todos", {}) if isinstance(app_state, dict) else {}
            raw_items = todos.get(agent_id)
            if isinstance(raw_items, list):
                todo_items = [
                    dict(item)
                    for item in raw_items
                    if isinstance(item, dict)
                ]

        receipt = None
        receipt_obj = getattr(result, "receipt", None)
        if hasattr(receipt_obj, "__dataclass_fields__") and not isinstance(receipt_obj, type):
            receipt = receipt_to_dict(receipt_obj)
        audit_events = _serialize_runtime_value(list(getattr(result, "audit_events", []) or []))

        # 提取结果文本
        if result and result.data is not None:
            content = str(result.data)
        elif result and result.error:
            content = f"Error: {result.error}"
        else:
            content = str(result)
        return {
            "content": content,
            "status": "error" if getattr(result, "error", None) else "completed",
            "receipt": receipt,
            "audit_events": audit_events if isinstance(audit_events, list) else [],
            "todo_items": todo_items,
        }
    except Exception as e:
        return {
            "content": f"Error executing {tool_name}: {str(e)}",
            "status": "error",
            "receipt": None,
            "audit_events": [],
            "todo_items": None,
        }

async def _tool_to_schema(tool) -> Dict[str, Any]:
    """
    将工具转换为 API schema（简化版）

    Args:
        tool: 工具实例

    Returns:
        ?? API 格式的工具 schema
    """
    name = tool.name
    description = await tool.prompt({})

    # 获取输入 schema
    if hasattr(tool, "input_schema") and tool.input_schema:
        schema = tool.input_schema
        if hasattr(schema, "model_json_schema"):
            input_schema = schema.model_json_schema()
            input_schema.pop("title", None)
            if "$defs" in input_schema and not input_schema["$defs"]:
                input_schema.pop("$defs")
        else:
            input_schema = {"type": "object", "properties": {}}
    else:
        input_schema = {"type": "object", "properties": {}}

    return {
        "name": name,
        "description": description,
        "input_schema": input_schema,
    }

# ============================================================================
# 模块级工具实例
# ============================================================================

agent_tool = AgentTool()
