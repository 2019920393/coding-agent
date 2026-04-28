"""
relevance.py 单元测试

测试 find_relevant_memories() 及其辅助函数的正确性。
覆盖：关键词提取、相关性评分、文件过滤、边界情况。
"""

import os
import tempfile
import shutil
from pathlib import Path
from typing import Set

import pytest

from codo.services.memory.relevance import (
    _extract_keywords,
    _score_memory,
    find_relevant_memories,
    RelevantMemory,
    MAX_RELEVANT_MEMORIES,
)
from codo.services.memory.scan import MemoryHeader

# ============================================================================
# 辅助函数：创建临时 memory 文件
# ============================================================================

def _make_memory_file(
    memory_dir: str,
    filename: str,
    description: str = "",
    memory_type: str = "project_fact",
    content: str = "test content",
) -> str:
    """在临时目录中创建一个带 frontmatter 的 .md 文件，返回绝对路径。"""
    filepath = Path(memory_dir) / filename
    frontmatter = f"---\ndescription: {description}\ntype: {memory_type}\n---\n\n{content}\n"
    filepath.write_text(frontmatter, encoding="utf-8")
    return str(filepath)

# ============================================================================
# Fixture
# ============================================================================

@pytest.fixture
def temp_memory_dir():
    """创建临时 memory 目录，测试结束后自动清理。"""
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)

# ============================================================================
# _extract_keywords 测试
# ============================================================================

class TestExtractKeywords:
    """测试关键词提取函数"""

    def test_基本提取(self):
        """普通英文文本应提取出有效关键词"""
        keywords = _extract_keywords("python testing framework")
        # python、testing、framework 均不在停用词中，应被提取
        assert "python" in keywords
        assert "testing" in keywords
        assert "framework" in keywords

    def test_停用词过滤(self):
        """停用词应被过滤掉"""
        keywords = _extract_keywords("the file is not found")
        # the、file、not 均在停用词中，应被过滤
        assert "the" not in keywords
        assert "file" not in keywords
        assert "not" not in keywords

    def test_短词过滤(self):
        """长度小于 3 的词应被过滤"""
        keywords = _extract_keywords("a to be or")
        # 所有词长度 <= 2，应全部被过滤
        assert len(keywords) == 0

    def test_空文本(self):
        """空文本应返回空集合"""
        assert _extract_keywords("") == set()
        assert _extract_keywords(None) == set()  # type: ignore

    def test_大小写不敏感(self):
        """关键词应统一转为小写"""
        keywords = _extract_keywords("Python TESTING Framework")
        assert "python" in keywords
        assert "testing" in keywords
        assert "framework" in keywords

    def test_下划线词(self):
        """含下划线的标识符应被正确提取"""
        keywords = _extract_keywords("memory_manager scan_files")
        assert "memory_manager" in keywords
        assert "scan_files" in keywords

    def test_中文文本(self):
        """中文文本无法被正则提取，应返回空集合"""
        keywords = _extract_keywords("用户偏好设置")
        # 中文字符不匹配 [a-zA-Z0-9_]{3,}，结果为空
        assert len(keywords) == 0

# ============================================================================
# _score_memory 测试
# ============================================================================

class TestScoreMemory:
    """测试记忆文件相关性评分函数"""

    def _make_header(
        self,
        filename: str,
        description: str = "",
        memory_type: str = "",
        filepath: str = "/tmp/test.md",
        mtime: float = 0.0,
    ) -> MemoryHeader:
        """创建测试用 MemoryHeader"""
        return MemoryHeader(
            filename=filename,
            filepath=filepath,
            mtime=mtime,
            description=description,
            memory_type=memory_type,
        )

    def test_无交集返回零(self):
        """查询关键词与文件无交集时，分数应为 0"""
        header = self._make_header("database_schema.md", "database table structure")
        score = _score_memory(header, {"python", "testing", "pytest"})
        assert score == 0.0

    def test_有交集返回正分(self):
        """查询关键词与文件有交集时，分数应大于 0"""
        header = self._make_header("python_style.md", "python coding style preferences")
        score = _score_memory(header, {"python", "style"})
        assert score > 0.0

    def test_文件名权重高于描述(self):
        """文件名命中的权重（2x）应高于描述命中（1x）"""
        # 文件名命中：文件名含 "python"，描述不含
        header_filename = self._make_header("python.md", "general coding tips")
        # 描述命中：文件名不含 "python"，描述含
        header_desc = self._make_header("general.md", "python coding tips")

        query_kw = {"python"}
        score_filename = _score_memory(header_filename, query_kw)
        score_desc = _score_memory(header_desc, query_kw)

        # 文件名命中分数应高于描述命中（2x vs 1x 权重）
        assert score_filename > score_desc

    def test_空查询关键词返回零(self):
        """查询关键词为空时，分数应为 0"""
        header = self._make_header("python_style.md", "python coding style")
        score = _score_memory(header, set())
        assert score == 0.0

    def test_空描述不报错(self):
        """description 为 None 时不应抛出异常"""
        header = self._make_header("test.md", description=None, memory_type=None)  # type: ignore
        score = _score_memory(header, {"test"})
        # 只有文件名有关键词，分数应 >= 0
        assert score >= 0.0

# ============================================================================
# find_relevant_memories 测试
# ============================================================================

class TestFindRelevantMemories:
    """测试主函数 find_relevant_memories"""

    def test_空目录返回空列表(self, temp_memory_dir):
        """memory 目录为空时应返回空列表"""
        result = find_relevant_memories("python testing", temp_memory_dir)
        assert result == []

    def test_不存在的目录返回空列表(self):
        """不存在的目录应返回空列表"""
        result = find_relevant_memories("python testing", "/nonexistent/path/xyz")
        assert result == []

    def test_找到相关文件(self, temp_memory_dir):
        """有相关文件时应返回匹配结果"""
        _make_memory_file(
            temp_memory_dir,
            "python_preferences.md",
            description="python coding style and preferences",
        )
        result = find_relevant_memories("python style preferences", temp_memory_dir)
        assert len(result) >= 1
        # 返回的应该是 RelevantMemory 实例
        assert isinstance(result[0], RelevantMemory)
        assert "python_preferences.md" in result[0].path

    def test_过滤已展示文件(self, temp_memory_dir):
        """already_surfaced 中的文件应被过滤掉"""
        path = _make_memory_file(
            temp_memory_dir,
            "python_preferences.md",
            description="python coding style",
        )
        # 将该文件标记为已展示
        result = find_relevant_memories(
            "python style",
            temp_memory_dir,
            already_surfaced={path},
        )
        # 已展示的文件不应出现在结果中
        assert all(r.path != path for r in result)

    def test_最多返回5个(self, temp_memory_dir):
        """结果数量不应超过 MAX_RELEVANT_MEMORIES（5）"""
        # 创建 8 个相关文件
        for i in range(8):
            _make_memory_file(
                temp_memory_dir,
                f"python_topic_{i}.md",
                description=f"python topic number {i}",
            )
        result = find_relevant_memories("python topic", temp_memory_dir)
        assert len(result) <= MAX_RELEVANT_MEMORIES

    def test_自定义max_results(self, temp_memory_dir):
        """max_results 参数应被正确应用"""
        for i in range(6):
            _make_memory_file(
                temp_memory_dir,
                f"python_item_{i}.md",
                description=f"python item {i}",
            )
        result = find_relevant_memories("python item", temp_memory_dir, max_results=2)
        assert len(result) <= 2

    def test_无关键词退化为mtime排序(self, temp_memory_dir):
        """查询无有效关键词时，应退化为按 mtime 返回最新文件"""
        _make_memory_file(temp_memory_dir, "file_a.md", description="some content")
        _make_memory_file(temp_memory_dir, "file_b.md", description="other content")
        # 查询只有停用词，无有效关键词
        result = find_relevant_memories("the a to", temp_memory_dir)
        # 应返回文件（退化模式），不应为空
        assert len(result) >= 1

    def test_mtime_ms字段为毫秒(self, temp_memory_dir):
        """返回的 mtime_ms 应为毫秒级时间戳"""
        _make_memory_file(
            temp_memory_dir,
            "python_guide.md",
            description="python guide",
        )
        result = find_relevant_memories("python guide", temp_memory_dir)
        assert len(result) >= 1
        # mtime_ms 应大于 0，且为毫秒级（> 1e12 表示 2001 年后的时间戳）
        assert result[0].mtime_ms > 1_000_000_000_000  # 毫秒级时间戳

    def test_排除MEMORY_md(self, temp_memory_dir):
        """MEMORY.md 文件应被排除在结果之外"""
        # 创建 MEMORY.md（索引文件，不应被返回）
        memory_index = Path(temp_memory_dir) / "MEMORY.md"
        memory_index.write_text("# Memory Index\n- [test](test.md)\n", encoding="utf-8")
        # 创建普通 memory 文件
        _make_memory_file(temp_memory_dir, "test.md", description="test memory")

        result = find_relevant_memories("test memory", temp_memory_dir)
        # 结果中不应包含 MEMORY.md
        assert all("MEMORY.md" not in r.path for r in result)

    def test_无相关文件返回空列表(self, temp_memory_dir):
        """查询与所有文件均无关键词交集时，应返回空列表"""
        _make_memory_file(
            temp_memory_dir,
            "database_schema.md",
            description="database table structure sql",
        )
        # 查询关键词与文件完全无关
        result = find_relevant_memories("python testing pytest", temp_memory_dir)
        assert result == []

    def test_already_surfaced默认为空集合(self, temp_memory_dir):
        """不传 already_surfaced 时应正常工作（默认空集合）"""
        _make_memory_file(
            temp_memory_dir,
            "python_style.md",
            description="python style guide",
        )
        # 不传 already_surfaced，不应抛出异常
        result = find_relevant_memories("python style", temp_memory_dir)
        assert isinstance(result, list)

# ============================================================================
# filter_duplicate_memory_attachments 测试
# ============================================================================

class TestFilterDuplicateMemoryAttachments:
    """测试 filter_duplicate_memory_attachments 函数"""

    def setup_method(self):
        """延迟导入，避免在模块级别触发 yaml 依赖"""
        from codo.services.attachments import filter_duplicate_memory_attachments
        self.filter_fn = filter_duplicate_memory_attachments

    def _make_memory_attachment(self, path: str) -> dict:
        """创建 memory 附件消息"""
        return {
            "type": "attachment",
            "attachment": {
                "type": "memory",
                "path": path,
                "content": "test content",
            },
        }

    def test_空历史不过滤(self):
        """消息历史为空时，所有附件应原样返回"""
        attachments = [self._make_memory_attachment("/path/a.md")]
        result = self.filter_fn(attachments, [])
        assert len(result) == 1

    def test_过滤已注入路径(self):
        """历史中已注入的 memory 路径应被过滤"""
        path = "/memory/python_style.md"
        # 历史消息中已有该 memory
        messages = [self._make_memory_attachment(path)]
        # 候选附件中也有该 memory
        attachments = [self._make_memory_attachment(path)]
        result = self.filter_fn(attachments, messages)
        # 重复的应被过滤掉
        assert len(result) == 0

    def test_保留未注入路径(self):
        """历史中未出现的 memory 路径应被保留"""
        old_path = "/memory/old.md"
        new_path = "/memory/new.md"
        messages = [self._make_memory_attachment(old_path)]
        attachments = [self._make_memory_attachment(new_path)]
        result = self.filter_fn(attachments, messages)
        # 新路径未在历史中，应被保留
        assert len(result) == 1
        assert result[0]["attachment"]["path"] == new_path

    def test_非memory附件不受影响(self):
        """非 memory 类型的附件不应被过滤"""
        path = "/memory/python_style.md"
        messages = [self._make_memory_attachment(path)]
        # 候选中有一个 memory（重复）和一个 ide_selection（非 memory）
        attachments = [
            self._make_memory_attachment(path),  # 重复，应被过滤
            {
                "type": "attachment",
                "attachment": {"type": "ide_selection", "filename": "test.py"},
            },
        ]
        result = self.filter_fn(attachments, messages)
        # memory 被过滤，ide_selection 保留
        assert len(result) == 1
        assert result[0]["attachment"]["type"] == "ide_selection"

    def test_空附件列表(self):
        """候选附件为空时应返回空列表"""
        result = self.filter_fn([], [])
        assert result == []

    def test_历史中无memory附件(self):
        """历史中无 memory 附件时，所有候选附件应原样返回（快速路径）"""
        messages = [{"role": "user", "content": "hello"}]
        attachments = [self._make_memory_attachment("/memory/test.md")]
        result = self.filter_fn(attachments, messages)
        assert len(result) == 1
