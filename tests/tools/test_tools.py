"""
测试核心工具
"""

import pytest
import os
import tempfile
from pathlib import Path
from unittest.mock import Mock

from codo.tools.bash_tool import bash_tool, BashToolInput
from codo.tools.read_tool import read_tool, ReadToolInput
from codo.tools.edit_tool import edit_tool, EditToolInput
from codo.tools.write_tool import write_tool, WriteToolInput
from codo.tools.glob_tool import glob_tool, GlobToolInput
from codo.tools.grep_tool import grep_tool, GrepToolInput
from codo.tools.base import ToolUseContext
from codo.services.tools.execution_manager import ExecutionManager
from codo.team import get_task_manager

# 创建模拟的上下文和回调
def create_mock_context():
    """创建模拟的工具使用上下文"""
    return ToolUseContext(
        options={},
        abort_controller=None,
        messages=[]
    )

def mock_can_use_tool():
    """模拟权限检查回调"""
    return True

class TestBashTool:
    """测试 BashTool"""

    @pytest.mark.asyncio
    async def test_simple_command(self):
        """测试简单命令执行"""
        input_data = BashToolInput(command="echo Hello")
        context = create_mock_context()

        result = await bash_tool.call(input_data, context, mock_can_use_tool, None)

        assert result.data is not None
        assert "Hello" in result.data.stdout
        assert result.data.exitCode == 0

    @pytest.mark.asyncio
    async def test_command_with_error(self):
        """测试错误命令"""
        input_data = BashToolInput(command="nonexistent_command_xyz")
        context = create_mock_context()

        result = await bash_tool.call(input_data, context, mock_can_use_tool, None)

        assert result.data is not None
        assert result.data.exitCode != 0

    @pytest.mark.asyncio
    async def test_timeout(self):
        """测试超时"""
        # 使用短超时测试（Windows 兼容命令）
        import sys
        if sys.platform == 'win32':
            # Windows: 使用 ping 命令模拟延迟
            command = "ping -n 6 127.0.0.1"  # 大约 5 秒
        else:
            command = "sleep 5"

        input_data = BashToolInput(
            command=command,
            timeout=100  # 100ms
        )
        context = create_mock_context()

        result = await bash_tool.call(input_data, context, mock_can_use_tool, None)

        assert result.data is not None
        assert result.data.timedOut

    @pytest.mark.asyncio
    async def test_run_in_background_returns_task_and_completes(self, tmp_path):
        """后台执行应立即返回任务信息，并由任务管理器完成收尾。"""
        input_data = BashToolInput(
            command="echo Hello Background",
            run_in_background=True,
            description="后台执行 echo",
        )
        context = create_mock_context()
        context["cwd"] = str(tmp_path)

        result = await bash_tool.call(input_data, context, mock_can_use_tool, None)

        assert result.data is not None
        assert result.data.background is True
        assert result.data.taskId
        assert result.data.status == "running"

        block = bash_tool.map_tool_result_to_tool_result_block_param(result.data, "tool-1")
        assert block["content"] == f"Background task started: {result.data.taskId}"

        completed = await get_task_manager().wait_for_task(result.data.taskId, timeout=2.0)

        assert completed is not None
        assert completed.result is not None
        assert completed.result["exitCode"] == 0
        assert "Hello Background" in completed.result["stdout"]

class TestReadTool:
    """测试 ReadTool"""

    @pytest.mark.asyncio
    async def test_read_text_file(self, tmp_path):
        """测试读取文本文件"""
        test_file = tmp_path / "test.txt"
        content = "line1\nline2\nline3"
        test_file.write_text(content)

        input_data = ReadToolInput(file_path=str(test_file))
        context = create_mock_context()

        result = await read_tool.call(input_data, context, mock_can_use_tool, None)

        assert result.data is not None
        assert "line1" in result.data.content
        assert result.data.lineCount == 3
        assert not result.data.isBinary

    @pytest.mark.asyncio
    async def test_read_nonexistent_file(self):
        """测试读取不存在的文件"""
        input_data = ReadToolInput(file_path="/nonexistent/file.txt")
        context = create_mock_context()

        result = await read_tool.call(input_data, context, mock_can_use_tool, None)

        assert result.error is not None

    @pytest.mark.asyncio
    async def test_read_with_offset_limit(self, tmp_path):
        """测试部分读取"""
        test_file = tmp_path / "test.txt"
        content = "\n".join([f"line{i}" for i in range(1, 101)])
        test_file.write_text(content)

        input_data = ReadToolInput(
            file_path=str(test_file),
            offset=10,
            limit=5
        )
        context = create_mock_context()

        result = await read_tool.call(input_data, context, mock_can_use_tool, None)

        assert result.data is not None
        assert result.data.isPartial
        assert "line11" in result.data.content  # offset=10 means starting from line 11

class TestEditTool:
    """测试 EditTool"""

    @pytest.mark.asyncio
    async def test_simple_edit(self, tmp_path):
        """测试简单编辑"""
        test_file = tmp_path / "test.txt"
        original = "Hello World"
        test_file.write_text(original)

        input_data = EditToolInput(
            file_path=str(test_file),
            old_string="World",
            new_string="Python"
        )
        context = create_mock_context()

        result = await edit_tool.call(input_data, context, mock_can_use_tool, None)

        assert result.data is not None
        assert result.data.diff is not None
        assert result.staged_changes

        # 先不落盘，确认后才应用
        assert test_file.read_text() == original

        manager = ExecutionManager()
        await manager.apply_staged_change(result.staged_changes[0])
        assert test_file.read_text() == "Hello Python"

    @pytest.mark.asyncio
    async def test_replace_all(self, tmp_path):
        """测试替换所有"""
        test_file = tmp_path / "test.txt"
        original = "foo bar foo baz foo"
        test_file.write_text(original)

        input_data = EditToolInput(
            file_path=str(test_file),
            old_string="foo",
            new_string="qux",
            replace_all=True
        )
        context = create_mock_context()

        result = await edit_tool.call(input_data, context, mock_can_use_tool, None)

        assert result.data is not None
        assert result.staged_changes

        assert test_file.read_text() == original

        manager = ExecutionManager()
        await manager.apply_staged_change(result.staged_changes[0])
        assert test_file.read_text() == "qux bar qux baz qux"

class TestWriteTool:
    """测试 WriteTool"""

    @pytest.mark.asyncio
    async def test_create_new_file(self, tmp_path):
        """测试创建新文件"""
        test_file = tmp_path / "new.txt"
        content = "New file content"

        input_data = WriteToolInput(
            file_path=str(test_file),
            content=content
        )
        context = create_mock_context()

        result = await write_tool.call(input_data, context, mock_can_use_tool, None)

        assert result.data is not None
        assert result.data.type == "create"
        assert result.staged_changes
        assert not test_file.exists()

        manager = ExecutionManager()
        await manager.apply_staged_change(result.staged_changes[0])
        assert test_file.exists()
        assert test_file.read_text() == content

    @pytest.mark.asyncio
    async def test_update_existing_file(self, tmp_path):
        """测试更新现有文件"""
        test_file = tmp_path / "existing.txt"
        test_file.write_text("Original content")

        new_content = "Updated content"
        input_data = WriteToolInput(
            file_path=str(test_file),
            content=new_content
        )
        context = create_mock_context()

        result = await write_tool.call(input_data, context, mock_can_use_tool, None)

        assert result.data is not None
        assert result.data.type == "update"
        assert result.data.diff is not None
        assert result.staged_changes
        assert test_file.read_text() == "Original content"

        manager = ExecutionManager()
        await manager.apply_staged_change(result.staged_changes[0])
        assert test_file.read_text() == new_content

class TestGlobTool:
    """测试 GlobTool"""

    @pytest.mark.asyncio
    async def test_glob_pattern(self, tmp_path):
        """测试 glob 模式匹配"""
        # 创建测试文件
        (tmp_path / "test1.txt").write_text("test")
        (tmp_path / "test2.txt").write_text("test")
        (tmp_path / "other.md").write_text("test")

        input_data = GlobToolInput(
            pattern="*.txt",
            path=str(tmp_path)
        )
        context = create_mock_context()

        result = await glob_tool.call(input_data, context, mock_can_use_tool, None)

        assert result.data is not None
        assert result.data.numFiles == 2
        assert any("test1.txt" in f for f in result.data.filenames)
        assert any("test2.txt" in f for f in result.data.filenames)

    @pytest.mark.asyncio
    async def test_recursive_glob(self, tmp_path):
        """测试递归 glob"""
        # 创建嵌套目录结构
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (tmp_path / "root.py").write_text("test")
        (subdir / "nested.py").write_text("test")

        input_data = GlobToolInput(
            pattern="**/*.py",
            path=str(tmp_path)
        )
        context = create_mock_context()

        result = await glob_tool.call(input_data, context, mock_can_use_tool, None)

        assert result.data is not None
        assert result.data.numFiles == 2

class TestGrepTool:
    """测试 GrepTool"""

    @pytest.mark.asyncio
    async def test_grep_search(self, tmp_path):
        """测试内容搜索"""
        # 创建测试文件
        (tmp_path / "file1.txt").write_text("Hello World\nFoo Bar")
        (tmp_path / "file2.txt").write_text("Hello Python\nBaz Qux")

        input_data = GrepToolInput(
            pattern="Hello",
            path=str(tmp_path)
        )
        context = create_mock_context()

        result = await grep_tool.call(input_data, context, mock_can_use_tool, None)

        # 如果 ripgrep 未安装，跳过测试
        if result.error and "ripgrep" in result.error:
            pytest.skip("ripgrep not installed")

        assert result.data is not None
        assert result.data.numMatches >= 2

if __name__ == '__main__':
    pytest.main([__file__, '-v'])
