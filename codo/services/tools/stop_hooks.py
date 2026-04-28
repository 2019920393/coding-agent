"""
Stop Hooks 执行

[Workflow]
1. 在对话结束时（model 返回无工具调用的最终响应后）执行
2. 加载 Stop 类型的 hooks 配置
3. 执行所有 Stop hooks
4. 返回是否阻止继续执行

简化版实现：
- 只支持 command 类型的 Stop hooks
- 不支持 TeammateIdle/TaskCompleted hooks（多用户功能）
- 不支持 extractMemories/autoDream（Ant-only 功能）
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from codo.services.tools.hooks import execute_hook, aggregate_hook_results, AggregatedHookResult
from codo.services.tools.hooks_loader import get_hooks_for_event
from codo.types.hooks import PostToolUseHookInput, HookConfig

logger = logging.getLogger(__name__)

async def execute_stop_hooks(
    cwd: str,
    messages: List[Dict[str, Any]],
) -> AggregatedHookResult:
    """
    执行 Stop hooks

    [Workflow]
    1. 加载 Stop 类型的 hooks 配置
    2. 如果没有 Stop hooks，直接返回空结果
    3. 并发执行所有 Stop hooks
    4. 聚合结果并返回

    Args:
        cwd: 当前工作目录
        messages: 当前对话消息列表（用于传递给 hook）

    Returns:
        AggregatedHookResult: 聚合后的 hook 结果
    """
    # 加载 Stop hooks 配置
    stop_hooks = get_hooks_for_event(cwd, "Stop")

    if not stop_hooks:
        # 没有 Stop hooks，直接返回空结果
        return AggregatedHookResult()

    logger.debug(f"[stop_hooks] 执行 {len(stop_hooks)} 个 Stop hooks")

    # 构建 hook 输入（Stop hooks 使用 PostToolUse 格式，但 tool_name 为 "Stop"）

    last_assistant_content = ""
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            content = msg.get("content", "")
            if isinstance(content, str):
                last_assistant_content = content
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        last_assistant_content = block.get("text", "")
                        break
            break

    # 创建 hook 输入（使用 PostToolUseHookInput 格式，tool_name 为 "Stop"）
    hook_input = PostToolUseHookInput(
        tool_name="Stop",
        tool_input={},
        tool_response=last_assistant_content,
        tool_use_id="stop",
        cwd=cwd,
        hook_event_name="PostToolUse",  # Stop hooks 使用 PostToolUse 格式
    )

    # 并发执行所有 Stop hooks
    tasks = [execute_hook(hook, hook_input) for hook in stop_hooks]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 过滤掉异常结果
    valid_results = []
    for result in results:
        if isinstance(result, Exception):
            logger.error(f"[stop_hooks] Stop hook 执行异常: {result}")
        else:
            valid_results.append(result)

    # 聚合结果
    aggregated = aggregate_hook_results(valid_results)

    if aggregated.prevent_continuation:
        logger.info(
            f"[stop_hooks] Stop hook 阻止继续执行: {aggregated.stop_reason}"
        )

    return aggregated

async def handle_stop_hooks(
    cwd: str,
    messages: List[Dict[str, Any]],
) -> bool:
    """
    处理 Stop hooks 并返回是否应该继续

    [Workflow]
    1. 执行 Stop hooks
    2. 如果有 hook 阻止继续，返回 False
    3. 否则返回 True

    Args:
        cwd: 当前工作目录
        messages: 当前对话消息列表

    Returns:
        bool: True 表示可以继续，False 表示应该停止
    """
    try:
        result = await execute_stop_hooks(cwd, messages)
        return not result.prevent_continuation
    except Exception as e:
        # Stop hooks 是 best-effort，失败不影响主流程
        logger.error(f"[stop_hooks] Stop hooks 执行失败: {e}")
        return True
