"""
真实 API 端到端集成测试

使用真实 API 测试完整的工具调用流程。
覆盖：
1. 工具 schema 是否正确传给 API（模型能否正确调用工具）
2. Read/Write/Edit/Bash 工具执行
3. Agent 工具调用
4. 流式事件处理
5. 错误恢复

运行方式：
    cd Codo_new
    python -m pytest tests/integration/test_real_api.py -v -s
"""

import asyncio
import os
import sys
import tempfile
import shutil
from pathlib import Path
from typing import Any, Dict, List, AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
from dotenv import load_dotenv

# 加载 .env
load_dotenv()

API_KEY = os.getenv("ANTHROPIC_API_KEY")
BASE_URL = os.getenv("ANTHROPIC_BASE_URL")

if not API_KEY:
    pytest.skip("ANTHROPIC_API_KEY 未设置", allow_module_level=True)

# ============================================================================
# 辅助函数：收集所有流式事件
# ============================================================================

async def collect_stream_events(stream: AsyncGenerator) -> List[Dict[str, Any]]:
    """收集所有流式事件，返回事件列表"""
    events = []
    async for event in stream:
        events.append(event)
    return events

async def run_query_and_collect(
    prompt: str,
    cwd: str,
    model: str = "claude-haiku-4-5-20251001",  # 用 Haiku 节省费用
) -> Dict[str, Any]:
    """
    运行一次查询，收集所有事件，返回结果摘要
    """
    from codo.query_engine import QueryEngine
    from codo.query import Terminal
    from codo.types.permissions import PermissionMode

    engine = QueryEngine(
        api_key=API_KEY,
        cwd=cwd,
        model=model,
        enable_persistence=False,
        base_url=BASE_URL,
    )
    # 真实 API 集成测试聚焦工具链路，不覆盖 Textual 权限交互。
    # 在无活动 UI 的测试环境里显式启用 bypassPermissions，避免把
    # “缺少交互宿主”误判成工具执行失败。
    engine.execution_context["permission_context"].mode = PermissionMode.BYPASS_PERMISSIONS

    events = []
    terminal = None
    errors = []
    text_parts = []
    tool_calls = []
    tool_results = []

    with patch(
        "codo.services.tools.change_review.request_change_review",
        AsyncMock(return_value="accept"),
    ):
        async for event in engine.submit_message_stream(prompt):
            if isinstance(event, Terminal):
                terminal = event
                break

            events.append(event)
            event_type = event.get("type") if isinstance(event, dict) else None

            if event_type == "text_delta":
                delta = event.get("delta", {})
                text_parts.append(delta.get("text", ""))

            elif event_type == "content_block_start":
                block = event.get("content_block")
                if block and hasattr(block, "type") and block.type == "tool_use":
                    tool_calls.append({
                        "id": block.id,
                        "name": block.name,
                        "input": {},
                    })

            elif event_type == "tool_result":
                tool_results.append({
                    "tool_use_id": event.get("tool_use_id"),
                    "content": event.get("content", ""),
                    "is_error": event.get("is_error", False),
                })

            elif event_type == "error":
                errors.append(event.get("error", ""))

    return {
        "terminal_reason": terminal.reason if terminal else None,
        "text": "".join(text_parts),
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "errors": errors,
        "event_count": len(events),
    }

# ============================================================================
# 测试 1：基础文本对话（无工具）
# ============================================================================

class TestBasicConversation:
    """测试基础对话，不涉及工具"""

    @pytest.mark.asyncio
    async def test_simple_text_response(self, tmp_path):
        """模型应该能正常回复文本"""
        result = await run_query_and_collect(
            "用一句话回答：1+1等于几？",
            str(tmp_path),
        )

        print(f"\n[text_response] terminal={result['terminal_reason']}, text={result['text'][:100]}")

        assert result["terminal_reason"] == "completed", \
            f"期望 completed，实际 {result['terminal_reason']}"
        assert len(result["text"]) > 0, "模型没有返回文本"
        assert len(result["errors"]) == 0, f"有错误: {result['errors']}"

# ============================================================================
# 测试 2：Read 工具调用
# ============================================================================

class TestReadTool:
    """测试 Read 工具"""

    @pytest.mark.asyncio
    async def test_read_file(self, tmp_path):
        """模型应该能正确调用 Read 工具读取文件"""
        # 创建测试文件
        test_file = tmp_path / "hello.txt"
        test_file.write_text("Hello, World! This is a test file.", encoding="utf-8")

        result = await run_query_and_collect(
            f"读取文件 {test_file} 的内容，告诉我里面写了什么",
            str(tmp_path),
        )

        print(f"\n[read_tool] terminal={result['terminal_reason']}")
        print(f"  tool_calls: {[t['name'] for t in result['tool_calls']]}")
        print(f"  errors: {result['errors']}")
        print(f"  text: {result['text'][:200]}")

        assert result["terminal_reason"] == "completed", \
            f"期望 completed，实际 {result['terminal_reason']}"
        assert len(result["errors"]) == 0, f"有错误: {result['errors']}"

        # 验证 Read 工具被调用
        read_calls = [t for t in result["tool_calls"] if t["name"] == "Read"]
        assert len(read_calls) > 0, "模型没有调用 Read 工具"

        # 验证工具结果不是错误
        for tr in result["tool_results"]:
            assert not tr["is_error"], f"Read 工具返回错误: {tr['content']}"

        # 验证模型回复包含文件内容
        assert "Hello" in result["text"] or "hello" in result["text"].lower(), \
            f"模型回复没有包含文件内容: {result['text']}"

# ============================================================================
# 测试 3：Write 工具调用
# ============================================================================

class TestWriteTool:
    """测试 Write 工具"""

    @pytest.mark.asyncio
    async def test_write_file(self, tmp_path):
        """模型应该能正确调用 Write 工具创建文件"""
        target_file = tmp_path / "output.txt"

        result = await run_query_and_collect(
            f"创建文件 {target_file}，内容为：'This file was created by Codo.'",
            str(tmp_path),
        )

        print(f"\n[write_tool] terminal={result['terminal_reason']}")
        print(f"  tool_calls: {[t['name'] for t in result['tool_calls']]}")
        print(f"  errors: {result['errors']}")

        assert result["terminal_reason"] == "completed", \
            f"期望 completed，实际 {result['terminal_reason']}"
        assert len(result["errors"]) == 0, f"有错误: {result['errors']}"

        # 验证 Write 工具被调用
        write_calls = [t for t in result["tool_calls"] if t["name"] == "Write"]
        assert len(write_calls) > 0, "模型没有调用 Write 工具"

        # 验证文件被创建
        assert target_file.exists(), f"文件没有被创建: {target_file}"
        content = target_file.read_text(encoding="utf-8")
        assert "Codo" in content or "created" in content.lower(), \
            f"文件内容不符合预期: {content}"

# ============================================================================
# 测试 4：Bash 工具调用
# ============================================================================

class TestBashTool:
    """测试 Bash 工具"""

    @pytest.mark.asyncio
    async def test_bash_command(self, tmp_path):
        """模型应该能正确调用 Bash 工具执行命令"""
        result = await run_query_and_collect(
            "运行命令 `echo hello_from_bash` 并告诉我输出结果",
            str(tmp_path),
        )

        print(f"\n[bash_tool] terminal={result['terminal_reason']}")
        print(f"  tool_calls: {[t['name'] for t in result['tool_calls']]}")
        print(f"  errors: {result['errors']}")
        print(f"  text: {result['text'][:200]}")

        assert result["terminal_reason"] == "completed", \
            f"期望 completed，实际 {result['terminal_reason']}"
        assert len(result["errors"]) == 0, f"有错误: {result['errors']}"

        # 验证 Bash 工具被调用
        bash_calls = [t for t in result["tool_calls"] if t["name"] == "Bash"]
        assert len(bash_calls) > 0, "模型没有调用 Bash 工具"

# ============================================================================
# 测试 5：Agent 工具调用（最关键）
# ============================================================================

class TestAgentTool:
    """测试 Agent 工具"""

    @pytest.mark.asyncio
    async def test_explore_agent(self, tmp_path):
        """模型应该能正确调用 Explore agent"""
        # 创建一些测试文件
        (tmp_path / "main.py").write_text("def main():\n    print('hello')\n", encoding="utf-8")
        (tmp_path / "utils.py").write_text("def helper():\n    return 42\n", encoding="utf-8")

        result = await run_query_and_collect(
            "用 Explore agent 扫描当前目录，告诉我有哪些 Python 文件",
            str(tmp_path),
        )

        print(f"\n[agent_tool] terminal={result['terminal_reason']}")
        print(f"  tool_calls: {[t['name'] for t in result['tool_calls']]}")
        print(f"  errors: {result['errors']}")
        print(f"  text: {result['text'][:300]}")

        assert result["terminal_reason"] == "completed", \
            f"期望 completed，实际 {result['terminal_reason']}"
        assert len(result["errors"]) == 0, f"有错误: {result['errors']}"

        # 验证 Agent 工具被调用
        agent_calls = [t for t in result["tool_calls"] if t["name"] == "Agent"]
        assert len(agent_calls) > 0, "模型没有调用 Agent 工具"

# ============================================================================
# 测试 6：多工具协作（Read + Edit）
# ============================================================================

class TestMultiToolCollaboration:
    """测试多工具协作"""

    @pytest.mark.asyncio
    async def test_read_then_edit(self, tmp_path):
        """模型应该先 Read 再 Edit"""
        # 创建有 bug 的文件
        buggy_file = tmp_path / "buggy.py"
        buggy_file.write_text(
            "def divide(a, b):\n    return a / b  # bug: no zero check\n",
            encoding="utf-8",
        )

        result = await run_query_and_collect(
            f"读取 {buggy_file}，然后修复除零 bug，添加 if b == 0: raise ValueError('Cannot divide by zero')",
            str(tmp_path),
        )

        print(f"\n[multi_tool] terminal={result['terminal_reason']}")
        print(f"  tool_calls: {[t['name'] for t in result['tool_calls']]}")
        print(f"  errors: {result['errors']}")

        assert result["terminal_reason"] == "completed", \
            f"期望 completed，实际 {result['terminal_reason']}"
        assert len(result["errors"]) == 0, f"有错误: {result['errors']}"

        # 验证 Read 和 Edit 都被调用
        tool_names = [t["name"] for t in result["tool_calls"]]
        assert "Read" in tool_names, f"没有调用 Read 工具，实际调用: {tool_names}"
        assert "Edit" in tool_names or "Write" in tool_names, \
            f"没有调用 Edit/Write 工具，实际调用: {tool_names}"

        # 验证文件被修改
        content = buggy_file.read_text(encoding="utf-8")
        assert "ValueError" in content or "zero" in content.lower(), \
            f"文件没有被正确修改: {content}"

# ============================================================================
# 测试 7：错误恢复（工具调用失败后继续）
# ============================================================================

class TestErrorRecovery:
    """测试错误恢复"""

    @pytest.mark.asyncio
    async def test_recover_from_tool_error(self, tmp_path):
        """工具调用失败后，模型应该能继续对话"""
        result = await run_query_and_collect(
            "读取文件 /nonexistent/path/file.txt，如果失败就告诉我文件不存在",
            str(tmp_path),
        )

        print(f"\n[error_recovery] terminal={result['terminal_reason']}")
        print(f"  tool_calls: {[t['name'] for t in result['tool_calls']]}")
        print(f"  tool_results: {result['tool_results']}")
        print(f"  text: {result['text'][:200]}")

        # 即使工具失败，对话应该能完成
        assert result["terminal_reason"] == "completed", \
            f"期望 completed，实际 {result['terminal_reason']}"

        # 模型应该告知文件不存在
        text_lower = result["text"].lower()
        assert any(word in text_lower for word in ["不存在", "not found", "error", "failed", "无法"]), \
            f"模型没有正确处理错误: {result['text']}"

# ============================================================================
# 测试 8：Glob 工具
# ============================================================================

class TestGlobTool:
    """测试 Glob 工具"""

    @pytest.mark.asyncio
    async def test_glob_search(self, tmp_path):
        """模型应该能正确调用 Glob 工具搜索文件"""
        # 创建测试文件
        (tmp_path / "a.py").write_text("# python file a", encoding="utf-8")
        (tmp_path / "b.py").write_text("# python file b", encoding="utf-8")
        (tmp_path / "c.txt").write_text("text file", encoding="utf-8")

        result = await run_query_and_collect(
            f"在 {tmp_path} 目录下搜索所有 .py 文件，列出文件名",
            str(tmp_path),
        )

        print(f"\n[glob_tool] terminal={result['terminal_reason']}")
        print(f"  tool_calls: {[t['name'] for t in result['tool_calls']]}")
        print(f"  errors: {result['errors']}")
        print(f"  text: {result['text'][:200]}")

        assert result["terminal_reason"] == "completed", \
            f"期望 completed，实际 {result['terminal_reason']}"
        assert len(result["errors"]) == 0, f"有错误: {result['errors']}"

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "--tb=short"])
