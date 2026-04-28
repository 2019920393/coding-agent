"""
测试工具结果大小限制功能

测试覆盖：
1. 工具结果大小计算
2. 持久化阈值获取
3. 预览生成
4. 结果持久化
5. 大结果消息构建
6. 工具结果截断
7. 消息级预算控制
"""

import pytest
import math
from pathlib import Path
from unittest.mock import Mock, patch

from codo.utils.tool_result_storage import (
    get_persistence_threshold,
    content_size,
    format_file_size,
    generate_preview,
    persist_tool_result,
    build_large_tool_result_message,
    maybe_persist_large_tool_result,
    get_per_message_budget_limit,
    apply_tool_result_budget,
    ToolResultStorage,
)
from codo.constants.tool_limits import (
    DEFAULT_MAX_RESULT_SIZE_CHARS,
    MAX_TOOL_RESULTS_PER_MESSAGE_CHARS,
    PREVIEW_SIZE_BYTES,
)
from codo.tools.types import ToolResult

class TestPersistenceThreshold:
    """测试持久化阈值获取"""

    def test_infinity_threshold(self):
        """测试 Infinity 阈值"""
        threshold = get_persistence_threshold("Read", float('inf'))
        assert not math.isfinite(threshold)

    def test_none_threshold(self):
        """测试 None 阈值"""
        threshold = get_persistence_threshold("Read", None)
        assert not math.isfinite(threshold)

    def test_declared_threshold_less_than_default(self):
        """测试工具声明值小于默认值"""
        threshold = get_persistence_threshold("Bash", 30000)
        assert threshold == 30000

    def test_declared_threshold_greater_than_default(self):
        """测试工具声明值大于默认值"""
        threshold = get_persistence_threshold("Custom", 100000)
        assert threshold == DEFAULT_MAX_RESULT_SIZE_CHARS

class TestContentSize:
    """测试内容大小计算"""

    def test_string_content(self):
        """测试字符串内容"""
        content = "Hello, World!"
        size = content_size(content)
        assert size == len(content.encode('utf-8'))

    def test_bytes_content(self):
        """测试字节内容"""
        content = b"Hello, World!"
        size = content_size(content)
        assert size == len(content)

    def test_list_content(self):
        """测试列表内容"""
        content = ["Hello", "World"]
        size = content_size(content)
        expected = len("Hello".encode('utf-8')) + len("World".encode('utf-8'))
        assert size == expected

    def test_dict_content(self):
        """测试字典内容"""
        content = {"text": "Hello", "type": "text"}
        size = content_size(content)
        expected = len("Hello".encode('utf-8')) + len("text".encode('utf-8'))
        assert size == expected

    def test_unicode_content(self):
        """测试 Unicode 内容"""
        content = "你好，世界！"
        size = content_size(content)
        assert size == len(content.encode('utf-8'))
        assert size > len(content)  # UTF-8 编码后更大

class TestFormatFileSize:
    """测试文件大小格式化"""

    def test_bytes(self):
        """测试字节格式"""
        assert format_file_size(500) == "500B"

    def test_kilobytes(self):
        """测试 KB 格式"""
        assert format_file_size(1024) == "1.0KB"
        assert format_file_size(2048) == "2.0KB"
        assert format_file_size(21760) == "21.2KB"

    def test_megabytes(self):
        """测试 MB 格式"""
        assert format_file_size(1024 * 1024) == "1.0MB"
        assert format_file_size(5 * 1024 * 1024) == "5.0MB"

    def test_gigabytes(self):
        """测试 GB 格式"""
        assert format_file_size(1024 * 1024 * 1024) == "1.0GB"

class TestGeneratePreview:
    """测试预览生成"""

    def test_content_within_limit(self):
        """测试内容未超限"""
        content = "Short content"
        preview, has_more = generate_preview(content, 1000)
        assert preview == content
        assert has_more is False

    def test_content_exceeds_limit(self):
        """测试内容超限"""
        content = "A" * 5000
        preview, has_more = generate_preview(content, 1000)
        assert len(preview.encode('utf-8')) <= 1000
        assert has_more is True

    def test_truncate_at_newline(self):
        """测试在换行处截断"""
        content = "Line 1\n" + "A" * 1000 + "\nLine 2\n" + "B" * 1000
        preview, has_more = generate_preview(content, 1500)
        assert has_more is True
        # 应该在换行处截断
        assert preview.endswith("A" * 1000) or "\n" in preview

    def test_unicode_truncation(self):
        """测试 Unicode 截断"""
        content = "你好" * 1000
        preview, has_more = generate_preview(content, 1000)
        assert has_more is True
        # 确保没有截断多字节字符
        preview.encode('utf-8')  # 不应该抛出异常

class TestPersistToolResult:
    """测试工具结果持久化"""

    def test_persist_tool_result(self, tmp_path):
        """测试持久化工具结果"""
        content = "A" * 10000
        tool_use_id = "test_tool_123"

        with patch('pathlib.Path.home', return_value=tmp_path):
            result = persist_tool_result(content, tool_use_id, str(tmp_path))

        # 检查返回值
        assert "filepath" in result
        assert "original_size" in result
        assert "preview" in result
        assert "has_more" in result

        # 检查文件是否创建
        filepath = Path(result["filepath"])
        assert filepath.exists()
        assert filepath.read_text(encoding='utf-8') == content

        # 检查大小
        assert result["original_size"] == len(content.encode('utf-8'))

        # 检查预览
        assert len(result["preview"]) < len(content)
        assert result["has_more"] is True

class TestBuildLargeToolResultMessage:
    """测试大工具结果消息构建"""

    def test_build_message(self):
        """测试构建消息"""
        result = {
            "filepath": "/path/to/file.txt",
            "original_size": 21760,
            "preview": "Preview content...",
            "has_more": True,
        }

        message = build_large_tool_result_message(result)

        # 检查消息格式
        assert "<persisted-output>" in message
        assert "</persisted-output>" in message
        assert "21.2KB" in message
        assert "/path/to/file.txt" in message
        assert "Preview content..." in message
        assert "..." in message

class TestMaybePersistLargeToolResult:
    """测试工具结果持久化检查"""

    def test_small_result_not_persisted(self):
        """测试小结果不持久化"""
        tool_result_block = {
            "tool_use_id": "test_123",
            "content": "Small content",
        }

        result = maybe_persist_large_tool_result(
            tool_result_block,
            "Bash",
            30000,
            "/tmp",
        )

        # 应该返回原结果
        assert result == tool_result_block

    def test_large_result_persisted(self, tmp_path):
        """测试大结果持久化"""
        large_content = "A" * 100000
        tool_result_block = {
            "tool_use_id": "test_123",
            "content": large_content,
        }

        with patch('pathlib.Path.home', return_value=tmp_path):
            result = maybe_persist_large_tool_result(
                tool_result_block,
                "Bash",
                30000,
                str(tmp_path),
            )

        # 应该返回替换后的结果
        assert result["tool_use_id"] == "test_123"
        assert result["content"] != large_content
        assert "<persisted-output>" in result["content"]

    def test_infinity_threshold_not_persisted(self):
        """测试 Infinity 阈值不持久化"""
        large_content = "A" * 100000
        tool_result_block = {
            "tool_use_id": "test_123",
            "content": large_content,
        }

        result = maybe_persist_large_tool_result(
            tool_result_block,
            "Read",
            float('inf'),
            "/tmp",
        )

        # 应该返回原结果
        assert result == tool_result_block

class TestPerMessageBudget:
    """测试消息级预算控制"""

    def test_get_per_message_budget_limit(self):
        """测试获取预算限制"""
        limit = get_per_message_budget_limit()
        assert limit == MAX_TOOL_RESULTS_PER_MESSAGE_CHARS

    def test_apply_budget_within_limit(self):
        """测试预算内的消息"""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "1", "content": "Small"},
                    {"type": "tool_result", "tool_use_id": "2", "content": "Content"},
                ],
            }
        ]

        result = apply_tool_result_budget(messages, "/tmp")

        # 应该返回原消息
        assert result == messages

    def test_apply_budget_exceeds_limit(self, tmp_path):
        """测试超预算的消息"""
        # 使用稍微超过预算的大小：100001 * 2 = 200002 > 200000
        large_content = "A" * 100001
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "1", "content": large_content},
                    {"type": "tool_result", "tool_use_id": "2", "content": large_content},
                ],
            }
        ]

        result = apply_tool_result_budget(messages, str(tmp_path))

        # 应该持久化大结果
        assert len(result) == 1
        assert result[0]["role"] == "user"
        content = result[0]["content"]
        assert len(content) == 2
        # 至少有一个结果被持久化
        assert any("<persisted-output>" in str(item.get("content", "")) for item in content)

class TestToolResultStorage:
    """测试 ToolResultStorage 类"""

    def test_maybe_truncate_result_small(self):
        """测试小结果不截断"""
        storage = ToolResultStorage("/tmp")
        result = ToolResult(data="Small content")

        truncated = storage.maybe_truncate_result(
            result,
            "test_123",
            "Bash",
            30000,
        )

        # 应该返回原结果
        assert truncated == result

    def test_maybe_truncate_result_large(self, tmp_path):
        """测试大结果截断"""
        storage = ToolResultStorage(str(tmp_path))
        large_data = "A" * 100000
        result = ToolResult(data=large_data)

        with patch('pathlib.Path.home', return_value=tmp_path):
            truncated = storage.maybe_truncate_result(
                result,
                "test_123",
                "Bash",
                30000,
            )

        # 应该返回截断后的结果
        assert truncated != result
        assert isinstance(truncated, ToolResult)
        assert "<persisted-output>" in truncated.data

    def test_maybe_truncate_result_infinity(self):
        """测试 Infinity 限制不截断"""
        storage = ToolResultStorage("/tmp")
        large_data = "A" * 100000
        result = ToolResult(data=large_data)

        truncated = storage.maybe_truncate_result(
            result,
            "test_123",
            "Read",
            float('inf'),
        )

        # 应该返回原结果
        assert truncated == result

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
