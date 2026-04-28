"""
工具编排核心逻辑

实现工具批量执行的编排系统：
1. 批处理分区 - 将工具调用分组为批次
2. 并发/串行调度 - 根据并发安全性决定执行策略
3. 结果聚合 - 收集所有工具执行结果
4. 上下文修改 - 应用工具返回的上下文修改器

核心函数：
- partition_tool_calls: 批处理分区
- run_tools_batch: 批量执行入口
- run_batch_concurrently: 并发执行批次
- run_batch_serially: 串行执行批次
- aggregate_context_modifiers: 聚合上下文修改器
"""

import asyncio
from typing import List, Dict, Any, Optional, AsyncGenerator
from datetime import datetime

from codo.types.orchestration import (
    Batch,
    ToolExecutionTask,
    ContextModifier,
    OrchestrationResult,
    ExecutionStatus
)
from codo.services.tools.concurrency import ToolExecutionQueue
from codo.tools.base import Tool, ToolUseContext
from codo.tools_registry import get_all_tools, find_tool_by_name

def _get_context_options(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    提取上下文 options。
    """
    return ToolUseContext.coerce(context).get_options()

def _resolve_tool_pool(context: Dict[str, Any]) -> List[Tool]:
    """
    统一工具来源：优先使用运行时 context.options.tools，其次回退 registry。

    这样可避免“prompt 声明了工具但执行层找不到”的注册漂移问题。
    """
    options = _get_context_options(context)
    runtime_tools = options.get("tools")
    if isinstance(runtime_tools, list) and runtime_tools:
        return runtime_tools
    return get_all_tools()

def partition_tool_calls(
    tool_calls: List[Dict[str, Any]],
    context: Dict[str, Any]
) -> List[Batch]:
    """
    批处理分区

    将工具调用分组为批次，规则：
    1. 连续的并发安全工具 → 一个批次（并发执行）
    2. 非并发安全工具 → 单独批次（串行执行）

    示例：
        输入: [Read(并发), Read(并发), Bash(非并发), Grep(并发)]
        分区: [[Read, Read], [Bash], [Grep]]
        执行: [并发批次] → [串行] → [串行]

    Args:
        tool_calls: 工具调用列表，每个元素包含 {id, name, input}
        context: 执行上下文

    Returns:
        批次列表
    """
    batches: List[Batch] = []

    tool_pool = _resolve_tool_pool(context)

    for tool_call in tool_calls:
        # [Workflow] 解析工具调用信息
        tool_use_id = tool_call.get('id', '')
        tool_name = tool_call.get('name', '')
        tool_input = tool_call.get('input', {})

        # [Workflow] 查找工具实例
        tool = find_tool_by_name(tool_pool, tool_name)
        if not tool:
            # 工具不存在，创建失败任务
            task = ToolExecutionTask(
                tool_use_id=tool_use_id,
                tool_name=tool_name,
                tool_input=tool_input,
                is_concurrency_safe=False,
                status=ExecutionStatus.FAILED,
                error=Exception(f"Tool not found: {tool_name}")
            )
            # 非并发安全工具，单独批次
            batches.append(Batch(is_concurrency_safe=False, tasks=[task]))
            continue

        # [Workflow] 解析输入参数（字典 → Pydantic 模型）
        try:
            parsed_input = tool.input_schema(**tool_input)
        except Exception as e:
            # 输入验证失败，创建失败任务
            task = ToolExecutionTask(
                tool_use_id=tool_use_id,
                tool_name=tool_name,
                tool_input=tool_input,
                is_concurrency_safe=False,
                status=ExecutionStatus.FAILED,
                error=Exception(f"Invalid input: {str(e)}")
            )
            batches.append(Batch(is_concurrency_safe=False, tasks=[task]))
            continue

        # [Workflow] 判断并发安全性
        is_concurrency_safe = tool.is_concurrency_safe(parsed_input)

        # [Workflow] 创建执行任务
        task = ToolExecutionTask(
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            tool_input=tool_input,
            is_concurrency_safe=is_concurrency_safe
        )

        # [Workflow] 分区逻辑
        if is_concurrency_safe and batches and batches[-1].is_concurrency_safe:
            # 连续的并发安全工具，合并到上一个批次
            batches[-1].add_task(task)
        else:
            # 创建新批次
            batch = Batch(
                is_concurrency_safe=is_concurrency_safe,
                tasks=[task],
                batch_id=f"batch_{len(batches)}"
            )
            batches.append(batch)

    return batches

async def execute_single_tool(
    task: ToolExecutionTask,
    context: Dict[str, Any],
    pre_hooks: Optional[List] = None,
    post_hooks: Optional[List] = None,
    post_failure_hooks: Optional[List] = None
) -> None:
    """
    执行单个工具（带权限检查和 Hook 支持）

    [Workflow]
    1. 检查 AbortController 是否已中断
    2. 运行 PreToolUse Hooks
    3. 查找工具实例
    4. 检查权限（如果需要）
    5. 执行工具
    6. 运行 PostToolUse Hooks（成功）或 PostToolUseFailure Hooks（失败）
    7. 保存结果
    8. 获取上下文修改器

    Args:
        task: 工具执行任务
        context: 执行上下文
        pre_hooks: PreToolUse Hook 列表
        post_hooks: PostToolUse Hook 列表
        post_failure_hooks: PostToolUseFailure Hook 列表
    """
    try:
        tool_context = ToolUseContext.coerce(context)

        # [Workflow] 0. 检查 AbortController 是否已中断
        abort_controller = tool_context.get("abort_controller")
        if abort_controller and abort_controller.is_aborted():
            from codo.utils.abort_controller import get_abort_message
            raise Exception(get_abort_message(abort_controller.get_reason()))

        # [Workflow] 1. 运行 PreToolUse Hooks
        if pre_hooks:
            from codo.services.tools.hooks import run_pre_tool_use_hooks

            hook_decision = await run_pre_tool_use_hooks(
                hooks=pre_hooks,
                tool_name=task.tool_name,
                tool_input=task.tool_input,
                tool_use_id=task.tool_use_id,
                cwd=tool_context.get("cwd", "")
            )

            # 处理 Hook 决策
            if hook_decision:
                if hook_decision.behavior == "deny":
                    raise PermissionError(f"Hook 拒绝执行: {task.tool_name}")

                # 使用 Hook 更新后的输入
                if hook_decision.updated_input:
                    task.tool_input = hook_decision.updated_input

        # [Workflow] 2. 查找工具实例
        tool_pool = _resolve_tool_pool(tool_context)
        tool = find_tool_by_name(tool_pool, task.tool_name)
        if not tool:
            raise Exception(f"工具未找到: {task.tool_name}")

        # [Workflow] 3. 权限检查
        if tool.requires_permission(task.tool_input):
            from codo.services.tools.permission_checker import has_permissions_to_use_tool
            from codo.types.permissions import PermissionAllowDecision

            permission_decision = await has_permissions_to_use_tool(
                tool,
                task.tool_input,
                tool_context
            )

            # 如果权限被拒绝，抛出异常
            if permission_decision.behavior == "deny":
                raise PermissionError(permission_decision.message)

            # 如果需要询问用户，显示交互式权限提示
            # Based on interactiveHandler.ts in reference project
            if permission_decision.behavior == "ask":
                from codo.services.tools.permission_prompt import (
                    prompt_permission,
                    apply_session_allow_rule,
                    PermissionChoice,
                )

                choice = await prompt_permission(
                    tool_name=task.tool_name,
                    tool_input=task.tool_input,
                    message=permission_decision.message,
                )

                if choice == PermissionChoice.ALLOW_ONCE:
                    pass  # proceed to execution
                elif choice == PermissionChoice.ALLOW_ALWAYS:
                    # Add session-level allow rule
                    perm_ctx = tool_context.get("permission_context")
                    if perm_ctx:
                        apply_session_allow_rule(perm_ctx, task.tool_name)
                elif choice == PermissionChoice.DENY:
                    raise PermissionError(
                        f"用户拒绝了 {task.tool_name} 的执行"
                    )
                elif choice == PermissionChoice.ABORT:
                    raise KeyboardInterrupt("用户中止了查询")

            # 如果权限允许，使用更新后的输入（如果有）
            if isinstance(permission_decision, PermissionAllowDecision) and permission_decision.updated_input:
                task.tool_input = permission_decision.updated_input

        # [Workflow] 4. 执行工具
        result = await tool.execute(task.tool_input, tool_context)

        # [Workflow] 4.5. 检查结果大小并截断（如果需要）
        from codo.utils.tool_result_storage import ToolResultStorage

        # 获取工具的 maxResultSizeChars 限制
        raw_max_size = getattr(tool, "max_result_size_chars", float("inf"))
        max_size = raw_max_size if isinstance(raw_max_size, (int, float)) else float("inf")

        # 如果结果超过限制，截断并持久化
        if max_size != float('inf'):
            result_storage = ToolResultStorage(tool_context.get("cwd", ""))
            result = result_storage.maybe_truncate_result(
                result=result,
                tool_use_id=task.tool_use_id,
                tool_name=task.tool_name,
                max_size_chars=max_size
            )

        # [Workflow] 5. 运行 PostToolUse Hooks（成功）
        if post_hooks:
            from codo.services.tools.hooks import run_post_tool_use_hooks

            hook_result = await run_post_tool_use_hooks(
                hooks=post_hooks,
                tool_name=task.tool_name,
                tool_input=task.tool_input,
                tool_response=result,
                tool_use_id=task.tool_use_id,
                cwd=tool_context.get("cwd", "")
            )

            # 处理 Hook 返回的额外上下文
            if hook_result and hook_result.additional_contexts:
                # 可以在这里处理额外上下文，例如添加到结果中
                pass

        # [Workflow] 6. 保存结果
        task.result = result
        task.status = ExecutionStatus.COMPLETED

        # [Workflow] 7. 获取上下文修改器
        modifier = tool.get_context_modifier(task.tool_input, result, tool_context.to_dict())
        if modifier:
            task.context_modifier = ContextModifier(
                tool_use_id=task.tool_use_id,
                modify_fn=modifier,
                description=f"{task.tool_name} 上下文修改"
            )

    except Exception as e:
        # [Workflow] 运行 PostToolUseFailure Hooks（失败）
        if post_failure_hooks:
            from codo.services.tools.hooks import run_post_tool_use_failure_hooks

            try:
                hook_result = await run_post_tool_use_failure_hooks(
                    hooks=post_failure_hooks,
                    tool_name=task.tool_name,
                    tool_input=task.tool_input,
                    tool_use_id=task.tool_use_id,
                    error=str(e),
                    is_interrupt=False,
                    cwd=tool_context.get("cwd", "")
                )

                # 处理 Hook 返回的重试请求
                if hook_result and hook_result.retry:
                    # 可以在这里实现重试逻辑
                    pass
            except Exception as hook_error:
                # Hook 执行失败不应影响原始错误
                print(f"警告: PostToolUseFailure Hook 执行失败: {hook_error}")

        # [Workflow] 记录错误
        task.status = ExecutionStatus.FAILED
        task.error = e

async def run_batch_concurrently(
    batch: Batch,
    context: Dict[str, Any],
    queue: ToolExecutionQueue,
    pre_hooks: Optional[List] = None,
    post_hooks: Optional[List] = None,
    post_failure_hooks: Optional[List] = None
) -> None:
    """
    并发执行批次

    批次内的所有任务并发执行，受并发控制器限制。

    Args:
        batch: 批次
        context: 执行上下文
        queue: 执行队列
        pre_hooks: PreToolUse Hook 列表
        post_hooks: PostToolUse Hook 列表
        post_failure_hooks: PostToolUseFailure Hook 列表
    """
    # [Workflow] 添加所有任务到队列
    queue.add_tasks(batch.tasks)

    # [Workflow] 创建执行任务
    async def execute_with_control(task: ToolExecutionTask) -> None:
        """带并发控制的执行"""
        # 获取执行权限
        await queue.acquire_task(task)
        try:
            # 执行工具（带 Hook 支持）
            await execute_single_tool(task, context, pre_hooks, post_hooks, post_failure_hooks)
        finally:
            # 释放执行权限
            await queue.release_task(task)

    # [Workflow] 并发执行所有任务
    await asyncio.gather(
        *[execute_with_control(task) for task in batch.tasks],
        return_exceptions=True
    )

async def run_batch_serially(
    batch: Batch,
    context: Dict[str, Any],
    queue: ToolExecutionQueue,
    pre_hooks: Optional[List] = None,
    post_hooks: Optional[List] = None,
    post_failure_hooks: Optional[List] = None
) -> None:
    """
    串行执行批次

    批次内的任务逐个执行。

    Args:
        batch: 批次
        context: 执行上下文
        queue: 执行队列
        pre_hooks: PreToolUse Hook 列表
        post_hooks: PostToolUse Hook 列表
        post_failure_hooks: PostToolUseFailure Hook 列表
    """
    for task in batch.tasks:
        # [Workflow] 添加任务到队列
        queue.add_task(task)

        # [Workflow] 获取执行权限
        await queue.acquire_task(task)

        try:
            # [Workflow] 执行工具（带 Hook 支持）
            await execute_single_tool(task, context, pre_hooks, post_hooks, post_failure_hooks)
        finally:
            # [Workflow] 释放执行权限
            await queue.release_task(task)

def aggregate_context_modifiers(
    batches: List[Batch],
    initial_context: Dict[str, Any]
) -> Dict[str, Any]:
    """
    聚合上下文修改器

    按批次顺序应用所有上下文修改器。

    Args:
        batches: 批次列表
        initial_context: 初始上下文

    Returns:
        修改后的上下文
    """
    context = initial_context.copy()

    # [Workflow] 按批次顺序应用修改器
    for batch in batches:
        for modifier in batch.get_context_modifiers():
            try:
                context = modifier.apply(context)
            except Exception as e:
                # 上下文修改失败，记录但不中断
                print(f"警告: 上下文修改器失败: {e}")

    return context

async def run_tools_batch(
    tool_calls: List[Dict[str, Any]],
    context: Dict[str, Any],
    max_concurrency: Optional[int] = None,
    pre_hooks: Optional[List] = None,
    post_hooks: Optional[List] = None,
    post_failure_hooks: Optional[List] = None
) -> OrchestrationResult:
    """
    批量执行工具（主入口）

    执行流程：
    1. 批处理分区 - 将工具调用分组
    2. 逐批次执行 - 并发或串行
    3. 聚合结果 - 收集所有结果和上下文修改

    Args:
        tool_calls: 工具调用列表
        context: 执行上下文
        max_concurrency: 最大并发数
        pre_hooks: PreToolUse Hook 列表
        post_hooks: PostToolUse Hook 列表
        post_failure_hooks: PostToolUseFailure Hook 列表

    Returns:
        编排执行结果
    """
    start_time = datetime.now()

    # [Workflow] 步骤1: 批处理分区
    batches = partition_tool_calls(tool_calls, context)

    # [Workflow] 步骤2: 创建执行队列
    queue = ToolExecutionQueue(max_concurrency)

    # [Workflow] 步骤3: 逐批次执行
    for batch in batches:
        if batch.is_concurrency_safe:
            # 并发执行
            await run_batch_concurrently(batch, context, queue, pre_hooks, post_hooks, post_failure_hooks)
        else:
            # 串行执行
            await run_batch_serially(batch, context, queue, pre_hooks, post_hooks, post_failure_hooks)

    # [Workflow] 步骤4: 聚合上下文修改器
    updated_context = aggregate_context_modifiers(batches, context)

    # [Workflow] 步骤5: 收集所有上下文修改器
    all_modifiers = []
    for batch in batches:
        all_modifiers.extend(batch.get_context_modifiers())

    # [Workflow] 步骤6: 生成结果
    end_time = datetime.now()
    total_duration = (end_time - start_time).total_seconds()

    result = OrchestrationResult(
        batches=batches,
        total_tasks=queue.total_count,
        completed_tasks=queue.completed_count,
        failed_tasks=queue.failed_count,
        total_duration=total_duration,
        context_modifiers=all_modifiers
    )

    return result

async def run_tools_batch_streaming(
    tool_calls: List[Dict[str, Any]],
    context: Dict[str, Any],
    max_concurrency: Optional[int] = None
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    批量执行工具（流式版本）

    实时产出工具执行结果，而不是等待所有工具完成。

    Args:
        tool_calls: 工具调用列表
        context: 执行上下文
        max_concurrency: 最大并发数

    Yields:
        工具执行结果，格式：
        {
            'type': 'tool_result',
            'tool_use_id': str,
            'tool_name': str,
            'result': Any,
            'error': Optional[Exception],
            'status': ExecutionStatus
        }
    """
    # [Workflow] 步骤1: 批处理分区
    batches = partition_tool_calls(tool_calls, context)

    # [Workflow] 步骤2: 创建执行队列
    queue = ToolExecutionQueue(max_concurrency)

    # [Workflow] 步骤3: 逐批次执行并流式产出结果
    for batch in batches:
        if batch.is_concurrency_safe:
            # 并发执行
            await run_batch_concurrently(batch, context, queue)
        else:
            # 串行执行
            await run_batch_serially(batch, context, queue)

        # [Workflow] 产出批次内所有任务的结果
        for task in batch.tasks:
            yield {
                'type': 'tool_result',
                'tool_use_id': task.tool_use_id,
                'tool_name': task.tool_name,
                'result': task.result,
                'error': task.error,
                'status': task.status,
                'duration': task.duration
            }
