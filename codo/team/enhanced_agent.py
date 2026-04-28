"""Enhanced AgentTool with fresh/fork and background execution support.

This module extends the base AgentTool with:
- Fresh vs Fork mode selection
- Background task execution
- Context isolation and inheritance
"""

from typing import Optional, Dict, Any
import logging

from codo.tools.agent_tool.types import AgentToolInput
from codo.tools.agent_tool.agents import find_agent_by_type
from codo.team import (
    prepare_fresh_context,
    prepare_fork_context,
    should_use_fork_mode,
    get_task_manager,
    TaskStatus,
)

logger = logging.getLogger(__name__)

async def _emit_agent_event(runtime_controller: Any, event_type: str, **payload: Any) -> None:
    if runtime_controller is None or not hasattr(runtime_controller, "emit_runtime_event"):
        return
    await runtime_controller.emit_runtime_event(event_type, **payload)

async def run_subagent_with_mode(
    args: AgentToolInput,
    context: Dict[str, Any],
    run_in_background: bool = False,
) -> Dict[str, Any]:
    """
    Run a sub-agent with fresh/fork mode selection.

    This is the enhanced entry point that decides between fresh and fork modes,
    and handles background execution.

    Args:
        args: AgentToolInput with description, prompt, subagent_type
        context: Parent agent context
        run_in_background: Whether to run in background

    Returns:
        Result dict with:
        - result: Final text output (or task info if background)
        - mode: "fresh" or "fork"
        - agent_id: Sub-agent identifier
        - task_id: Background task ID (if background)
    """
    # Step 1: Decide fresh vs fork
    use_fork = should_use_fork_mode(args.subagent_type, context)
    mode = "fork" if use_fork else "fresh"

    logger.info(f"[SubAgent] Mode: {mode}, Background: {run_in_background}")

    # Step 2: Prepare context
    tools = context.get("tools", [])

    if use_fork:
        subagent_ctx = prepare_fork_context(
            parent_context=context,
            tools=tools,
            is_background=run_in_background,
        )
    else:
        # Fresh mode - need agent definition
        agent_type = args.subagent_type or "general-purpose"
        agent_def = find_agent_by_type(agent_type)

        if not agent_def:
            return {
                "error": f"Agent type '{agent_type}' not found",
                "mode": mode,
            }

        # Filter tools for agent
        from codo.tools.agent_tool.utils import filter_tools_for_agent
        agent_tools = filter_tools_for_agent(tools, agent_def)

        model = agent_def.model or context.get("model", "claude-sonnet-4-20250514")

        subagent_ctx = prepare_fresh_context(
            agent_type=agent_type,
            system_prompt=agent_def.system_prompt,
            tools=agent_tools,
            model=model,
            is_background=run_in_background,
        )

    # Step 3: Execute (foreground or background)
    if run_in_background:
        return await _run_background_subagent(args, subagent_ctx, context)
    else:
        return await _run_foreground_subagent(args, subagent_ctx, context)

async def _run_foreground_subagent(
    args: AgentToolInput,
    subagent_ctx,
    parent_context: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Run sub-agent in foreground (blocking).

    [Workflow]
    1. 从 parent_context 获取 api_client
    2. 调用 _run_sub_agent 执行子代理对话循环
    3. 返回结果字典（含 result, mode, agent_id, token 统计）

    Args:
        args: AgentToolInput
        subagent_ctx: SubAgentContext（含 model, system_prompt, tools）
        parent_context: 父代理上下文（含 api_client, cwd）

    Returns:
        Result dict with final output
    """
    from codo.tools.agent_tool.agent_tool import _run_sub_agent

    # api_client 可能在 parent_context 顶层或 options 子字典里
    api_client = parent_context.get("api_client") or parent_context.get("options", {}).get("api_client")
    cwd = parent_context.get("cwd", ".")
    runtime_controller = parent_context.get("runtime_controller")
    interaction_broker = parent_context.get("interaction_broker") or runtime_controller
    permission_context = parent_context.get("permission_context")

    if not api_client:
        return {
            "error": "API client not available in parent context",
            "mode": subagent_ctx.mode,
            "agent_id": subagent_ctx.agent_id,
        }

    try:
        await _emit_agent_event(
            runtime_controller,
            "agent_started",
            agent_id=subagent_ctx.agent_id,
            label=f"{getattr(subagent_ctx, 'agent_type', 'Agent')} > {args.description}",
            agent_type=getattr(subagent_ctx, "agent_type", "") or "",
            mode=subagent_ctx.mode,
            background=bool(getattr(subagent_ctx, "is_background", False)),
            status="running",
        )

        async def _event_callback(event_type: str, payload: Dict[str, Any]) -> None:
            await _emit_agent_event(
                runtime_controller,
                event_type,
                agent_id=subagent_ctx.agent_id,
                **payload,
            )

        result_text, usage = await _run_sub_agent(
            client=api_client,
            model=subagent_ctx.model,
            system_prompt=subagent_ctx.system_prompt,
            tools=subagent_ctx.tools,
            prompt=args.prompt,
            cwd=cwd,
            agent_id=subagent_ctx.agent_id,
            interaction_broker=interaction_broker,
            permission_context=permission_context,
            event_callback=_event_callback,
        )

        await _emit_agent_event(
            runtime_controller,
            "agent_completed",
            agent_id=subagent_ctx.agent_id,
            result=result_text,
            status="completed",
            total_tokens=usage.get("total", 0),
        )

        return {
            "result": result_text,
            "mode": subagent_ctx.mode,
            "agent_id": subagent_ctx.agent_id,
            "total_tokens": usage.get("total", 0),
            "input_tokens": usage.get("input", 0),
            "output_tokens": usage.get("output", 0),
        }
    except Exception as e:
        logger.error(f"[SubAgent] Foreground execution failed: {e}")
        await _emit_agent_event(
            runtime_controller,
            "agent_error",
            agent_id=subagent_ctx.agent_id,
            error=str(e),
            status="error",
        )
        return {
            "error": str(e),
            "mode": subagent_ctx.mode,
            "agent_id": subagent_ctx.agent_id,
        }

async def _run_background_subagent(
    args: AgentToolInput,
    subagent_ctx,
    parent_context: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Run sub-agent in background (non-blocking).

    Args:
        args: AgentToolInput
        subagent_ctx: SubAgentContext
        parent_context: Parent context

    Returns:
        Result dict with task info (not final result)
    """
    from codo.tools.agent_tool.agent_tool import _run_sub_agent

    task_manager = get_task_manager()
    runtime_controller = parent_context.get("runtime_controller")
    interaction_broker = parent_context.get("interaction_broker") or runtime_controller
    permission_context = parent_context.get("permission_context")

    # Create background task
    task = task_manager.create_task(
        agent_id=subagent_ctx.agent_id,
        description=args.description,
        metadata={
            "kind": "agent",
            "mode": subagent_ctx.mode,
            "agent_type": getattr(subagent_ctx, "agent_type", None),
        },
    )

    logger.info(f"[SubAgent] Created background task: {task.task_id}")
    await _emit_agent_event(
        runtime_controller,
        "agent_started",
        agent_id=subagent_ctx.agent_id,
        label=f"{getattr(subagent_ctx, 'agent_type', 'Agent')} > {args.description}",
        agent_type=getattr(subagent_ctx, "agent_type", "") or "",
        mode=subagent_ctx.mode,
        background=True,
        status="running",
        task_id=task.task_id,
    )

    # Prepare coroutine
    api_client = parent_context.get("api_client")
    cwd = parent_context.get("cwd", ".")

    async def execute():
        async def _event_callback(event_type: str, payload: Dict[str, Any]) -> None:
            current_action = ""
            if event_type == "agent_delta":
                current_action = (
                    str(payload.get("content_delta", "") or "").strip()
                    or str(payload.get("thinking_delta", "") or "").strip()
                )
            elif event_type == "agent_tool_started":
                tool_name = str(payload.get("tool_name", "") or "Tool")
                current_action = f"{tool_name}: started"
            elif event_type == "agent_tool_completed":
                tool_name = str(payload.get("tool_name", "") or "Tool")
                current_action = str(payload.get("content", "") or "").strip() or f"{tool_name}: completed"
            if current_action:
                await task_manager.update_task_action(task.task_id, current_action)
            await _emit_agent_event(
                runtime_controller,
                event_type,
                agent_id=subagent_ctx.agent_id,
                task_id=task.task_id,
                **payload,
            )

        try:
            result_text, usage = await _run_sub_agent(
                client=api_client,
                model=subagent_ctx.model,
                system_prompt=subagent_ctx.system_prompt,
                tools=subagent_ctx.tools,
                prompt=args.prompt,
                cwd=cwd,
                agent_id=subagent_ctx.agent_id,
                interaction_broker=interaction_broker,
                permission_context=permission_context,
                event_callback=_event_callback,
            )
            await _emit_agent_event(
                runtime_controller,
                "agent_completed",
                agent_id=subagent_ctx.agent_id,
                task_id=task.task_id,
                result=result_text,
                status="completed",
                total_tokens=usage.get("total", 0),
            )
            return {
                "result": result_text,
                "mode": subagent_ctx.mode,
                "agent_id": subagent_ctx.agent_id,
                "total_tokens": usage.get("total", 0),
                "input_tokens": usage.get("input", 0),
                "output_tokens": usage.get("output", 0),
            }
        except Exception as e:
            await _emit_agent_event(
                runtime_controller,
                "agent_error",
                agent_id=subagent_ctx.agent_id,
                task_id=task.task_id,
                error=str(e),
                status="error",
            )
            raise

    # Start background execution
    await task_manager.run_task(task, execute())

    # Return task info immediately
    return {
        "result": f"Background task started: {task.task_id}",
        "mode": subagent_ctx.mode,
        "agent_id": subagent_ctx.agent_id,
        "task_id": task.task_id,
        "status": task.status.value,
        "is_background": True,
    }

async def get_background_task_status(task_id: str) -> Optional[Dict[str, Any]]:
    """
    Get status of a background task.

    Args:
        task_id: Task identifier

    Returns:
        Task status dict or None
    """
    task_manager = get_task_manager()
    task = task_manager.get_task(task_id)

    if not task:
        return None

    result = {
        "task_id": task.task_id,
        "agent_id": task.agent_id,
        "description": task.description,
        "status": task.status.value,
        "created_at": task.created_at,
    }

    if task.status == TaskStatus.COMPLETED:
        if isinstance(task.result, dict):
            result.update(
                {
                    "result": task.result.get("result", ""),
                    "total_tokens": task.result.get("total_tokens", 0),
                    "input_tokens": task.result.get("input_tokens", 0),
                    "output_tokens": task.result.get("output_tokens", 0),
                }
            )
        else:
            result["result"] = task.result
        result["completed_at"] = task.completed_at
    elif task.status == TaskStatus.FAILED:
        result["error"] = task.error
        result["completed_at"] = task.completed_at

    return result
