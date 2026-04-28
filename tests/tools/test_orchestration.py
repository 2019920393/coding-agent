"""
测试工具编排系统

测试场景：
1. 批处理分区 - 验证工具调用正确分组
2. 并发执行 - 验证并发安全工具并行执行
3. 串行执行 - 验证非并发安全工具串行执行
4. 上下文修改 - 验证上下文修改器正确应用
5. 混合执行 - 验证混合场景的调度
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
import pytest

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from codo.services.tools.orchestration import (
    partition_tool_calls,
    run_tools_batch,
    execute_single_tool
)
from codo.types.orchestration import ToolExecutionTask, ExecutionStatus
from codo.types.permissions import PermissionMode, ToolPermissionContext, PermissionRuleSource

def test_partition_tool_calls():
    """测试批处理分区"""
    print("\n=== 测试1: 批处理分区 ===")

    # 场景1: 连续的并发安全工具
    tool_calls = [
        {"id": "1", "name": "Read", "input": {"file_path": "test1.py"}},
        {"id": "2", "name": "Read", "input": {"file_path": "test2.py"}},
        {"id": "3", "name": "Grep", "input": {"pattern": "test"}},
    ]

    context = {"cwd": os.getcwd()}
    batches = partition_tool_calls(tool_calls, context)

    print(f"输入: 3个并发安全工具 (file_read, file_read, grep)")
    print(f"输出: {len(batches)} 个批次")

    assert len(batches) == 1, f"应该合并为1个批次，实际: {len(batches)}"
    assert batches[0].is_concurrency_safe, "批次应该是并发安全的"
    assert batches[0].size == 3, f"批次大小应该是3，实际: {batches[0].size}"

    print("✓ 连续并发安全工具正确合并为一个批次")

    # 场景2: 混合工具
    tool_calls = [
        {"id": "1", "name": "Read", "input": {"file_path": "test1.py"}},
        {"id": "2", "name": "Read", "input": {"file_path": "test2.py"}},
        {"id": "3", "name": "Bash", "input": {"command": "ls"}},
        {"id": "4", "name": "Grep", "input": {"pattern": "test"}},
    ]

    batches = partition_tool_calls(tool_calls, context)

    print(f"\n输入: 混合工具 (file_read, file_read, bash, grep)")
    print(f"输出: {len(batches)} 个批次")

    assert len(batches) == 3, f"应该分为3个批次，实际: {len(batches)}"
    assert batches[0].is_concurrency_safe and batches[0].size == 2, "第1批次: 2个并发安全工具"
    assert not batches[1].is_concurrency_safe and batches[1].size == 1, "第2批次: 1个非并发安全工具"
    assert batches[2].is_concurrency_safe and batches[2].size == 1, "第3批次: 1个并发安全工具"

    print("✓ 混合工具正确分为3个批次")
    print("  批次1: [file_read, file_read] (并发)")
    print("  批次2: [bash] (串行)")
    print("  批次3: [grep] (串行)")

@pytest.mark.asyncio
async def test_concurrent_execution():
    """测试并发执行"""
    print("\n=== 测试2: 并发执行 ===")

    # 创建临时测试文件
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建3个测试文件
        for i in range(1, 4):
            file_path = os.path.join(tmpdir, f"test{i}.txt")
            with open(file_path, "w") as f:
                f.write(f"Content of test{i}")

        # 并发读取3个文件
        tool_calls = [
            {"id": "1", "name": "Read", "input": {"file_path": f"test1.txt"}},
            {"id": "2", "name": "Read", "input": {"file_path": f"test2.txt"}},
            {"id": "3", "name": "Read", "input": {"file_path": f"test3.txt"}},
        ]

        context = {
            "cwd": tmpdir,
            "permission_context": ToolPermissionContext(
                mode=PermissionMode.BYPASS_PERMISSIONS,
                always_allow_rules={},
                always_deny_rules={},
                always_ask_rules={},
                cwd=tmpdir,
            )
        }

        import time
        start_time = time.time()

        result = await run_tools_batch(tool_calls, context)

        end_time = time.time()
        duration = end_time - start_time

        print(f"执行3个文件读取")
        print(f"总耗时: {duration:.3f}秒")
        print(f"完成任务: {result.completed_tasks}/{result.total_tasks}")
        print(f"失败任务: {result.failed_tasks}")

        assert result.total_tasks == 3, "应该有3个任务"
        assert result.completed_tasks == 3, "应该完成3个任务"
        assert result.failed_tasks == 0, "不应该有失败任务"

        # 验证所有任务都成功
        for batch in result.batches:
            for task in batch.tasks:
                assert task.status == ExecutionStatus.COMPLETED, f"任务 {task.tool_use_id} 应该完成"
                assert task.result is not None, f"任务 {task.tool_use_id} 应该有结果"

        print("✓ 并发执行成功")

@pytest.mark.asyncio
async def test_serial_execution():
    """测试串行执行"""
    print("\n=== 测试3: 串行执行 ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        # 串行执行bash命令
        tool_calls = [
            {"id": "1", "name": "Bash", "input": {"command": "echo 'test1'"}},
            {"id": "2", "name": "Bash", "input": {"command": "echo 'test2'"}},
        ]

        context = {
            "cwd": tmpdir,
            "permission_context": ToolPermissionContext(
                mode=PermissionMode.BYPASS_PERMISSIONS,
                always_allow_rules={},
                always_deny_rules={},
                always_ask_rules={},
                cwd=tmpdir,
            )
        }

        result = await run_tools_batch(tool_calls, context)

        print(f"执行2个bash命令")
        print(f"完成任务: {result.completed_tasks}/{result.total_tasks}")

        assert result.total_tasks == 2, "应该有2个任务"
        assert result.completed_tasks == 2, "应该完成2个任务"

        # 验证串行执行（每个bash命令单独批次）
        assert len(result.batches) == 2, "应该有2个批次（串行）"

        print("✓ 串行执行成功")

@pytest.mark.asyncio
async def test_context_modifier():
    """测试上下文修改器"""
    print("\n=== 测试4: 上下文修改器 ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建子目录
        subdir = os.path.join(tmpdir, "subdir")
        os.makedirs(subdir)

        # 创建测试文件
        test_file = os.path.join(subdir, "test.txt")
        with open(test_file, "w") as f:
            f.write("test content")

        # 执行 cd 命令，然后读取文件
        tool_calls = [
            {"id": "1", "name": "Bash", "input": {"command": f"cd {subdir}"}},
            {"id": "2", "name": "Read", "input": {"file_path": "test.txt"}},
        ]

        context = {
            "cwd": tmpdir,
            "permission_context": ToolPermissionContext(
                mode=PermissionMode.BYPASS_PERMISSIONS,
                always_allow_rules={},
                always_deny_rules={},
                always_ask_rules={},
                cwd=tmpdir,
            )
        }

        result = await run_tools_batch(tool_calls, context)

        print(f"执行 cd + file_read")
        print(f"完成任务: {result.completed_tasks}/{result.total_tasks}")
        print(f"上下文修改器数量: {len(result.context_modifiers)}")

        # 验证上下文修改器
        assert len(result.context_modifiers) > 0, "应该有上下文修改器"

        # 应用修改器
        updated_context = context.copy()
        for modifier in result.context_modifiers:
            updated_context = modifier.apply(updated_context)

        print(f"原始工作目录: {context['cwd']}")
        print(f"修改后工作目录: {updated_context['cwd']}")

        assert updated_context['cwd'] == subdir, "工作目录应该被修改"

        print("✓ 上下文修改器工作正常")

@pytest.mark.asyncio
async def test_mixed_execution():
    """测试混合执行"""
    print("\n=== 测试5: 混合执行 ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建测试文件
        for i in range(1, 3):
            file_path = os.path.join(tmpdir, f"test{i}.txt")
            with open(file_path, "w") as f:
                f.write(f"Content {i}")

        # 混合执行：并发读取 + bash命令 + 搜索
        tool_calls = [
            {"id": "1", "name": "Read", "input": {"file_path": "test1.txt"}},
            {"id": "2", "name": "Read", "input": {"file_path": "test2.txt"}},
            {"id": "3", "name": "Bash", "input": {"command": "ls"}},
            {"id": "4", "name": "Glob", "input": {"pattern": "*.txt"}},
        ]

        context = {
            "cwd": tmpdir,
            "permission_context": ToolPermissionContext(
                mode=PermissionMode.BYPASS_PERMISSIONS,
                always_allow_rules={},
                always_deny_rules={},
                always_ask_rules={},
                cwd=tmpdir,
            )
        }

        import time
        start_time = time.time()

        result = await run_tools_batch(tool_calls, context)

        end_time = time.time()
        duration = end_time - start_time

        print(f"执行混合工具调用")
        print(f"总耗时: {duration:.3f}秒")
        print(f"批次数: {len(result.batches)}")
        print(f"完成任务: {result.completed_tasks}/{result.total_tasks}")

        # 验证批次分区
        assert len(result.batches) == 3, "应该分为3个批次"
        assert result.batches[0].is_concurrency_safe, "第1批次应该并发"
        assert not result.batches[1].is_concurrency_safe, "第2批次应该串行"
        assert result.batches[2].is_concurrency_safe, "第3批次应该并发"

        print("✓ 混合执行成功")
        print(f"  批次1: {result.batches[0].size} 个并发任务")
        print(f"  批次2: {result.batches[1].size} 个串行任务")
        print(f"  批次3: {result.batches[2].size} 个并发任务")

@pytest.mark.asyncio
async def test_performance_comparison():
    """测试性能对比"""
    print("\n=== 测试6: 性能对比 ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建10个测试文件
        for i in range(1, 11):
            file_path = os.path.join(tmpdir, f"test{i}.txt")
            with open(file_path, "w") as f:
                f.write(f"Content {i}" * 100)

        tool_calls = [
            {"id": str(i), "name": "Read", "input": {"file_path": f"test{i}.txt"}}
            for i in range(1, 11)
        ]

        context = {
            "cwd": tmpdir,
            "permission_context": ToolPermissionContext(
                mode=PermissionMode.BYPASS_PERMISSIONS,
                always_allow_rules={},
                always_deny_rules={},
                always_ask_rules={},
                cwd=tmpdir,
            )
        }

        # 并发执行
        import time
        start_time = time.time()
        result = await run_tools_batch(tool_calls, context)
        concurrent_duration = time.time() - start_time

        print(f"并发执行10个文件读取: {concurrent_duration:.3f}秒")
        print(f"完成任务: {result.completed_tasks}/{result.total_tasks}")

        # 估算串行执行时间（假设每个任务平均耗时）
        avg_task_duration = result.total_duration / result.total_tasks
        estimated_serial_duration = avg_task_duration * result.total_tasks

        print(f"估算串行执行时间: {estimated_serial_duration:.3f}秒")
        print(f"性能提升: {estimated_serial_duration / concurrent_duration:.1f}x")

        assert result.completed_tasks == 10, "应该完成10个任务"

        print("✓ 性能测试完成")

async def main():
    """运行所有测试"""
    print("=" * 60)
    print("工具编排系统测试")
    print("=" * 60)

    try:
        # 测试1: 批处理分区
        test_partition_tool_calls()

        # 测试2: 并发执行
        await test_concurrent_execution()

        # 测试3: 串行执行
        await test_serial_execution()

        # 测试4: 上下文修改器
        await test_context_modifier()

        # 测试5: 混合执行
        await test_mixed_execution()

        # 测试6: 性能对比
        await test_performance_comparison()

        print("\n" + "=" * 60)
        print("✓ 所有测试通过！")
        print("=" * 60)

    except AssertionError as e:
        print(f"\n✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ 测试错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
