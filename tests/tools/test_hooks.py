"""
测试 Hook 系统

测试场景：
1. PreToolUse Hook - 权限决策和输入修改
2. PostToolUse Hook - 输出处理
3. PostToolUseFailure Hook - 错误恢复
4. Hook 结果聚合 - 多个 Hook 的决策聚合
5. Hook 超时处理
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
import pytest

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from codo.types.hooks import (
    HookConfig,
    HookResult,
    PreToolUseHookInput,
    PostToolUseHookInput,
    PostToolUseFailureHookInput,
)
from codo.services.tools.hooks import (
    execute_hook,
    parse_hook_output,
    aggregate_hook_results,
    run_pre_tool_use_hooks,
    run_post_tool_use_hooks,
    run_post_tool_use_failure_hooks,
)

# ============================================================================
# 测试 Hook 输出解析
# ============================================================================

def test_parse_hook_output_allow():
    """测试解析 allow 权限决策"""
    output_json = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": "测试允许",
        }
    }

    result = parse_hook_output(output_json, "PreToolUse")

    assert result.outcome == "success"
    assert result.permission_behavior == "allow"
    assert result.permission_decision_reason == "测试允许"

def test_parse_hook_output_deny():
    """测试解析 deny 权限决策"""
    output_json = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "测试拒绝",
        }
    }

    result = parse_hook_output(output_json, "PreToolUse")

    assert result.outcome == "success"
    assert result.permission_behavior == "deny"
    assert result.permission_decision_reason == "测试拒绝"

def test_parse_hook_output_updated_input():
    """测试解析输入修改"""
    output_json = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "updatedInput": {"file_path": "/modified/path.txt"},
        }
    }

    result = parse_hook_output(output_json, "PreToolUse")

    assert result.outcome == "success"
    assert result.updated_input == {"file_path": "/modified/path.txt"}

def test_parse_hook_output_prevent_continuation():
    """测试解析阻止继续执行"""
    output_json = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "preventContinuation": True,
            "stopReason": "测试停止",
        }
    }

    result = parse_hook_output(output_json, "PostToolUse")

    assert result.outcome == "success"
    assert result.prevent_continuation is True
    assert result.stop_reason == "测试停止"

def test_parse_hook_output_retry():
    """测试解析重试标志"""
    output_json = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUseFailure",
            "retry": True,
        }
    }

    result = parse_hook_output(output_json, "PostToolUseFailure")

    assert result.outcome == "success"
    assert result.retry is True

# ============================================================================
# 测试 Hook 结果聚合
# ============================================================================

def test_aggregate_hook_results_deny_priority():
    """测试 deny 优先级最高"""
    results = [
        HookResult(outcome="success", permission_behavior="allow"),
        HookResult(outcome="success", permission_behavior="deny", permission_decision_reason="拒绝原因"),
        HookResult(outcome="success", permission_behavior="ask"),
    ]

    aggregated = aggregate_hook_results(results)

    assert aggregated.permission_behavior == "deny"
    assert aggregated.permission_decision_reason == "拒绝原因"

def test_aggregate_hook_results_ask_priority():
    """测试 ask 优先级高于 allow"""
    results = [
        HookResult(outcome="success", permission_behavior="allow"),
        HookResult(outcome="success", permission_behavior="ask", permission_decision_reason="询问原因"),
    ]

    aggregated = aggregate_hook_results(results)

    assert aggregated.permission_behavior == "ask"
    assert aggregated.permission_decision_reason == "询问原因"

def test_aggregate_hook_results_allow():
    """测试只有 allow 时的聚合"""
    results = [
        HookResult(outcome="success", permission_behavior="allow", permission_decision_reason="允许原因"),
    ]

    aggregated = aggregate_hook_results(results)

    assert aggregated.permission_behavior == "allow"
    assert aggregated.permission_decision_reason == "允许原因"

def test_aggregate_hook_results_updated_input():
    """测试输入修改合并"""
    results = [
        HookResult(outcome="success", updated_input={"key1": "value1"}),
        HookResult(outcome="success", updated_input={"key2": "value2"}),
        HookResult(outcome="success", updated_input={"key1": "overridden"}),
    ]

    aggregated = aggregate_hook_results(results)

    assert aggregated.updated_input == {"key1": "overridden", "key2": "value2"}

def test_aggregate_hook_results_additional_contexts():
    """测试额外上下文收集"""
    results = [
        HookResult(outcome="success", additional_context="上下文1"),
        HookResult(outcome="success", additional_context="上下文2"),
    ]

    aggregated = aggregate_hook_results(results)

    assert len(aggregated.additional_contexts) == 2
    assert "上下文1" in aggregated.additional_contexts
    assert "上下文2" in aggregated.additional_contexts

# ============================================================================
# 测试 Hook 执行（需要创建测试脚本）
# ============================================================================

@pytest.mark.asyncio
async def test_execute_hook_allow():
    """测试执行返回 allow 的 Hook"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建测试 Hook 脚本
        hook_script = os.path.join(tmpdir, "hook_allow.py")
        with open(hook_script, "w") as f:
            f.write("""
import sys
import json

# 读取输入
input_data = json.loads(sys.stdin.read())

# 返回 allow 决策
output = {
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "permissionDecisionReason": "Hook 允许执行"
    }
}

print(json.dumps(output))
""")

        # 配置 Hook
        hook_config = HookConfig(
            command=f"python {hook_script}",
            event="PreToolUse",
        )

        # 准备输入
        hook_input = PreToolUseHookInput(
            tool_name="test_tool",
            tool_input={"arg": "value"},
            tool_use_id="test_id",
            cwd=tmpdir,
        )

        # 执行 Hook
        result = await execute_hook(hook_config, hook_input)

        # 验证结果
        assert result.outcome == "success"
        assert result.permission_behavior == "allow"
        assert result.permission_decision_reason == "Hook 允许执行"

@pytest.mark.asyncio
async def test_execute_hook_deny():
    """测试执行返回 deny 的 Hook"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建测试 Hook 脚本
        hook_script = os.path.join(tmpdir, "hook_deny.py")
        with open(hook_script, "w") as f:
            f.write("""
import sys
import json

input_data = json.loads(sys.stdin.read())

output = {
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "Hook 拒绝执行"
    }
}

print(json.dumps(output))
""")

        hook_config = HookConfig(
            command=f"python {hook_script}",
            event="PreToolUse",
        )

        hook_input = PreToolUseHookInput(
            tool_name="test_tool",
            tool_input={"arg": "value"},
            tool_use_id="test_id",
            cwd=tmpdir,
        )

        result = await execute_hook(hook_config, hook_input)

        assert result.outcome == "success"
        assert result.permission_behavior == "deny"
        assert result.permission_decision_reason == "Hook 拒绝执行"

@pytest.mark.asyncio
async def test_execute_hook_updated_input():
    """测试 Hook 修改输入"""
    with tempfile.TemporaryDirectory() as tmpdir:
        hook_script = os.path.join(tmpdir, "hook_update.py")
        with open(hook_script, "w") as f:
            f.write("""
import sys
import json

input_data = json.loads(sys.stdin.read())

output = {
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "updatedInput": {"file_path": "/modified/path.txt"}
    }
}

print(json.dumps(output))
""")

        hook_config = HookConfig(
            command=f"python {hook_script}",
            event="PreToolUse",
        )

        hook_input = PreToolUseHookInput(
            tool_name="test_tool",
            tool_input={"file_path": "/original/path.txt"},
            tool_use_id="test_id",
            cwd=tmpdir,
        )

        result = await execute_hook(hook_config, hook_input)

        assert result.outcome == "success"
        assert result.updated_input == {"file_path": "/modified/path.txt"}

@pytest.mark.asyncio
async def test_execute_hook_timeout():
    """测试 Hook 超时"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建一个会超时的 Hook 脚本
        hook_script = os.path.join(tmpdir, "hook_timeout.py")
        with open(hook_script, "w") as f:
            f.write("""
import time
time.sleep(10)  # 睡眠 10 秒
""")

        hook_config = HookConfig(
            command=f"python {hook_script}",
            event="PreToolUse",
            timeout=1000,  # 1 秒超时
        )

        hook_input = PreToolUseHookInput(
            tool_name="test_tool",
            tool_input={},
            tool_use_id="test_id",
            cwd=tmpdir,
        )

        result = await execute_hook(hook_config, hook_input)

        assert result.outcome == "non_blocking_error"
        assert "超时" in result.error_message

# ============================================================================
# 测试 Hook 执行入口
# ============================================================================

@pytest.mark.asyncio
async def test_run_pre_tool_use_hooks():
    """测试运行 PreToolUse Hook"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建两个 Hook 脚本
        hook1_script = os.path.join(tmpdir, "hook1.py")
        with open(hook1_script, "w") as f:
            f.write("""
import sys
import json
input_data = json.loads(sys.stdin.read())
output = {
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "updatedInput": {"key1": "value1"}
    }
}
print(json.dumps(output))
""")

        hook2_script = os.path.join(tmpdir, "hook2.py")
        with open(hook2_script, "w") as f:
            f.write("""
import sys
import json
input_data = json.loads(sys.stdin.read())
output = {
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "updatedInput": {"key2": "value2"}
    }
}
print(json.dumps(output))
""")

        hooks = [
            HookConfig(command=f"python {hook1_script}", event="PreToolUse"),
            HookConfig(command=f"python {hook2_script}", event="PreToolUse"),
        ]

        result = await run_pre_tool_use_hooks(
            tool_name="test_tool",
            tool_input={},
            tool_use_id="test_id",
            cwd=tmpdir,
            hooks=hooks,
        )

        # ask 优先级高于 allow
        assert result.permission_behavior == "ask"
        # 输入修改应该合并
        assert result.updated_input == {"key1": "value1", "key2": "value2"}

@pytest.mark.asyncio
async def test_run_post_tool_use_hooks():
    """测试运行 PostToolUse Hook"""
    with tempfile.TemporaryDirectory() as tmpdir:
        hook_script = os.path.join(tmpdir, "hook.py")
        with open(hook_script, "w") as f:
            f.write("""
import sys
import json
input_data = json.loads(sys.stdin.read())
output = {
    "hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": "Hook 添加的上下文"
    }
}
print(json.dumps(output))
""")

        hooks = [
            HookConfig(command=f"python {hook_script}", event="PostToolUse"),
        ]

        result = await run_post_tool_use_hooks(
            tool_name="test_tool",
            tool_input={},
            tool_response="工具响应",
            tool_use_id="test_id",
            cwd=tmpdir,
            hooks=hooks,
        )

        assert len(result.additional_contexts) == 1
        assert result.additional_contexts[0] == "Hook 添加的上下文"

@pytest.mark.asyncio
async def test_run_post_tool_use_failure_hooks():
    """测试运行 PostToolUseFailure Hook"""
    with tempfile.TemporaryDirectory() as tmpdir:
        hook_script = os.path.join(tmpdir, "hook.py")
        with open(hook_script, "w") as f:
            f.write("""
import sys
import json
input_data = json.loads(sys.stdin.read())
output = {
    "hookSpecificOutput": {
        "hookEventName": "PostToolUseFailure",
        "retry": True
    }
}
print(json.dumps(output))
""")

        hooks = [
            HookConfig(command=f"python {hook_script}", event="PostToolUseFailure"),
        ]

        result = await run_post_tool_use_failure_hooks(
            tool_name="test_tool",
            tool_input={},
            tool_use_id="test_id",
            error="测试错误",
            is_interrupt=False,
            cwd=tmpdir,
            hooks=hooks,
        )

        assert result.retry is True

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
