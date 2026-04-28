"""
Hook 执行服务

参考：src/services/tools/toolHooks.ts, src/utils/hooks.ts
简化：移除分析事件、遥测、复杂的权限规则检查

Hook 系统核心功能：
1. 执行 PreToolUse/PostToolUse/PostToolUseFailure Hook
2. 聚合多个 Hook 的权限决策
3. 处理 Hook 输入/输出修改
4. 超时和错误处理
"""

import asyncio
import json
import subprocess
from typing import List, Optional, Dict, Any, AsyncGenerator
from pathlib import Path

from codo.types.hooks import (
    HookConfig,
    HookInput,
    HookResult,
    AggregatedHookResult,
    PreToolUseHookInput,
    PostToolUseHookInput,
    PostToolUseFailureHookInput,
    HookEventName,
)
from codo.types.permissions import PermissionBehavior

# ============================================================================
# Hook 执行（Hook Execution）
# ============================================================================

async def execute_hook(
    hook_config: HookConfig,
    hook_input: HookInput,
) -> HookResult:
    """
    执行单个 Hook

    参考：src/utils/hooks.ts:1952-2930
    简化：移除进度消息、分析事件、工作区信任检查

    [Workflow]
    1. 准备 Hook 输入 JSON
    2. 执行 Hook 命令（带超时）
    3. 解析 Hook 输出 JSON
    4. 返回 HookResult
    """
    try:
        # 1. 准备 Hook 输入 JSON
        # 将 dataclass 转换为字典
        if isinstance(hook_input, PreToolUseHookInput):
            input_dict = {
                "hook_event_name": hook_input.hook_event_name,
                "tool_name": hook_input.tool_name,
                "tool_input": hook_input.tool_input,
                "tool_use_id": hook_input.tool_use_id,
                "cwd": hook_input.cwd,
            }
        elif isinstance(hook_input, PostToolUseHookInput):
            input_dict = {
                "hook_event_name": hook_input.hook_event_name,
                "tool_name": hook_input.tool_name,
                "tool_input": hook_input.tool_input,
                "tool_response": hook_input.tool_response,
                "tool_use_id": hook_input.tool_use_id,
                "cwd": hook_input.cwd,
            }
        elif isinstance(hook_input, PostToolUseFailureHookInput):
            input_dict = {
                "hook_event_name": hook_input.hook_event_name,
                "tool_name": hook_input.tool_name,
                "tool_input": hook_input.tool_input,
                "tool_use_id": hook_input.tool_use_id,
                "error": hook_input.error,
                "is_interrupt": hook_input.is_interrupt,
                "cwd": hook_input.cwd,
            }
        else:
            raise ValueError(f"未知的 Hook 输入类型: {type(hook_input)}")

        input_json = json.dumps(input_dict)

        # 2. 执行 Hook 命令（带超时）
        # 使用 asyncio.create_subprocess_shell 执行命令
        process = await asyncio.create_subprocess_shell(
            hook_config.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=hook_input.cwd,
        )

        # 发送输入并等待完成（带超时）
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input_json.encode()),
                timeout=hook_config.timeout / 1000.0,  # 转换为秒
            )
        except asyncio.TimeoutError:
            # 超时，终止进程
            process.kill()
            await process.wait()
            return HookResult(
                outcome="non_blocking_error",
                error_message=f"Hook 超时 ({hook_config.timeout}ms)",
            )

        # 3. 解析 Hook 输出 JSON
        if process.returncode != 0:
            # Hook 执行失败
            error_msg = stderr.decode() if stderr else f"Hook 退出码: {process.returncode}"
            return HookResult(
                outcome="non_blocking_error",
                error_message=error_msg,
            )

        # 解析 JSON 输出
        try:
            output_text = stdout.decode().strip()
            if not output_text:
                # 空输出，视为成功但无操作
                return HookResult(outcome="success")

            output_json = json.loads(output_text)
        except json.JSONDecodeError as e:
            return HookResult(
                outcome="non_blocking_error",
                error_message=f"Hook 输出不是有效的 JSON: {e}",
            )

        # 4. 解析 Hook 结果
        return parse_hook_output(output_json, hook_input.hook_event_name)

    except Exception as e:
        # 捕获所有异常，返回错误结果
        return HookResult(
            outcome="non_blocking_error",
            error_message=f"Hook 执行错误: {str(e)}",
        )

def parse_hook_output(output_json: Dict[str, Any], event_name: HookEventName) -> HookResult:
    """
    解析 Hook JSON 输出

    参考：src/utils/hooks.ts:489-688
    简化：仅支持 hookSpecificOutput 格式

    [Workflow]
    1. 检查 hookSpecificOutput 字段
    2. 提取权限决策（PreToolUse）
    3. 提取输入/输出修改
    4. 提取控制标志
    5. 返回 HookResult

    支持的输出格式：
    {
      "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow" | "deny" | "ask",
        "permissionDecisionReason": "...",
        "updatedInput": {...},
        "additionalContext": "...",
        "preventContinuation": true/false,
        "stopReason": "...",
        "retry": true/false
      }
    }
    """
    # 检查 hookSpecificOutput 字段
    hook_output = output_json.get("hookSpecificOutput", {})

    # 验证事件名称匹配
    if hook_output.get("hookEventName") != event_name:
        return HookResult(
            outcome="non_blocking_error",
            error_message=f"Hook 事件名称不匹配: 期望 {event_name}, 实际 {hook_output.get('hookEventName')}",
        )

    # 提取字段
    result = HookResult(outcome="success")

    # 权限决策（仅 PreToolUse）
    if event_name == "PreToolUse":
        permission_decision = hook_output.get("permissionDecision")
        if permission_decision in ["allow", "deny", "ask"]:
            result.permission_behavior = permission_decision
            result.permission_decision_reason = hook_output.get("permissionDecisionReason")

    # 输入/输出修改
    if "updatedInput" in hook_output:
        result.updated_input = hook_output["updatedInput"]
    if "updatedOutput" in hook_output:
        result.updated_output = hook_output["updatedOutput"]

    # 控制标志
    if hook_output.get("preventContinuation"):
        result.prevent_continuation = True
        result.stop_reason = hook_output.get("stopReason", "Hook 阻止继续执行")

    if hook_output.get("retry"):
        result.retry = True

    # 额外上下文
    if "additionalContext" in hook_output:
        result.additional_context = hook_output["additionalContext"]

    return result

# ============================================================================
# Hook 结果聚合（Hook Result Aggregation）
# ============================================================================

def aggregate_hook_results(results: List[HookResult]) -> AggregatedHookResult:
    """
    聚合多个 Hook 结果

    参考：src/utils/hooks.ts:2820-2880
    简化：移除复杂的消息处理

    [Workflow]
    1. 聚合权限决策（deny > ask > allow）
    2. 合并输入修改（后面的覆盖前面的）
    3. 收集额外上下文
    4. 检查阻止标志

    权限决策优先级：
    - 如果任何 Hook 返回 deny → 最终决策 = deny
    - 如果有 ask 且无 deny → 最终决策 = ask
    - 如果只有 allow → 最终决策 = allow
    - passthrough 不影响权限决策
    """
    aggregated = AggregatedHookResult()

    # 1. 聚合权限决策
    has_deny = False
    has_ask = False
    has_allow = False
    final_reason = None

    for result in results:
        if result.permission_behavior == "deny":
            has_deny = True
            final_reason = result.permission_decision_reason
        elif result.permission_behavior == "ask":
            has_ask = True
            if not final_reason:
                final_reason = result.permission_decision_reason
        elif result.permission_behavior == "allow":
            has_allow = True
            if not final_reason:
                final_reason = result.permission_decision_reason

    # 应用优先级规则
    if has_deny:
        aggregated.permission_behavior = "deny"
        aggregated.permission_decision_reason = final_reason
    elif has_ask:
        aggregated.permission_behavior = "ask"
        aggregated.permission_decision_reason = final_reason
    elif has_allow:
        aggregated.permission_behavior = "allow"
        aggregated.permission_decision_reason = final_reason

    # 2. 合并输入修改（后面的覆盖前面的）
    for result in results:
        if result.updated_input:
            if aggregated.updated_input is None:
                aggregated.updated_input = {}
            aggregated.updated_input.update(result.updated_input)

        if result.updated_output is not None:
            aggregated.updated_output = result.updated_output

    # 3. 收集额外上下文
    for result in results:
        if result.additional_context:
            aggregated.additional_contexts.append(result.additional_context)

    # 4. 检查阻止标志
    for result in results:
        if result.prevent_continuation:
            aggregated.prevent_continuation = True
            aggregated.stop_reason = result.stop_reason
            break

    # 5. 检查重试标志
    for result in results:
        if result.retry:
            aggregated.retry = True
            break

    return aggregated

# ============================================================================
# Hook 执行入口（Hook Execution Entry Points）
# ============================================================================

async def run_pre_tool_use_hooks(
    tool_name: str,
    tool_input: Dict[str, Any],
    tool_use_id: str,
    cwd: str,
    hooks: List[HookConfig],
) -> AggregatedHookResult:
    """
    执行 PreToolUse Hook

    参考：src/services/tools/toolHooks.ts（简化版）

    [Workflow]
    1. 过滤匹配的 Hook（事件类型 + 工具名称）
    2. 并发执行所有 Hook
    3. 聚合结果
    4. 返回聚合后的权限决策和输入修改
    """
    # 1. 过滤匹配的 Hook
    matching_hooks = [
        hook for hook in hooks
        if hook.event == "PreToolUse" and (hook.tool_name is None or hook.tool_name == tool_name)
    ]

    if not matching_hooks:
        # 没有匹配的 Hook，返回空结果
        return AggregatedHookResult()

    # 2. 准备 Hook 输入
    hook_input = PreToolUseHookInput(
        tool_name=tool_name,
        tool_input=tool_input,
        tool_use_id=tool_use_id,
        cwd=cwd,
    )

    # 3. 并发执行所有 Hook
    tasks = [execute_hook(hook, hook_input) for hook in matching_hooks]
    results = await asyncio.gather(*tasks)

    # 4. 聚合结果
    return aggregate_hook_results(list(results))

async def run_post_tool_use_hooks(
    tool_name: str,
    tool_input: Dict[str, Any],
    tool_response: Any,
    tool_use_id: str,
    cwd: str,
    hooks: List[HookConfig],
) -> AggregatedHookResult:
    """
    执行 PostToolUse Hook

    参考：src/services/tools/toolHooks.ts:39-191

    [Workflow]
    1. 过滤匹配的 Hook
    2. 并发执行所有 Hook
    3. 聚合结果
    4. 返回输出修改和额外上下文
    """
    # 1. 过滤匹配的 Hook
    matching_hooks = [
        hook for hook in hooks
        if hook.event == "PostToolUse" and (hook.tool_name is None or hook.tool_name == tool_name)
    ]

    if not matching_hooks:
        return AggregatedHookResult()

    # 2. 准备 Hook 输入
    hook_input = PostToolUseHookInput(
        tool_name=tool_name,
        tool_input=tool_input,
        tool_response=tool_response,
        tool_use_id=tool_use_id,
        cwd=cwd,
    )

    # 3. 并发执行所有 Hook
    tasks = [execute_hook(hook, hook_input) for hook in matching_hooks]
    results = await asyncio.gather(*tasks)

    # 4. 聚合结果
    return aggregate_hook_results(list(results))

async def run_post_tool_use_failure_hooks(
    tool_name: str,
    tool_input: Dict[str, Any],
    tool_use_id: str,
    error: str,
    is_interrupt: bool,
    cwd: str,
    hooks: List[HookConfig],
) -> AggregatedHookResult:
    """
    执行 PostToolUseFailure Hook

    参考：src/services/tools/toolHooks.ts:193-319

    [Workflow]
    1. 过滤匹配的 Hook
    2. 并发执行所有 Hook
    3. 聚合结果
    4. 返回重试标志和额外上下文
    """
    # 1. 过滤匹配的 Hook
    matching_hooks = [
        hook for hook in hooks
        if hook.event == "PostToolUseFailure" and (hook.tool_name is None or hook.tool_name == tool_name)
    ]

    if not matching_hooks:
        return AggregatedHookResult()

    # 2. 准备 Hook 输入
    hook_input = PostToolUseFailureHookInput(
        tool_name=tool_name,
        tool_input=tool_input,
        tool_use_id=tool_use_id,
        error=error,
        is_interrupt=is_interrupt,
        cwd=cwd,
    )

    # 3. 并发执行所有 Hook
    tasks = [execute_hook(hook, hook_input) for hook in matching_hooks]
    results = await asyncio.gather(*tasks)

    # 4. 聚合结果
    return aggregate_hook_results(list(results))
