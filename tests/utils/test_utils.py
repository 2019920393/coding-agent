"""
测试工具辅助模块
"""

import pytest
import os
import tempfile
from pathlib import Path

from codo.utils.fs_operations import getFsImplementation
from codo.utils.path import expandPath, toRelativePath, isUncPath, isSubPath
from codo.utils.diff import generateUnifiedDiff, countLinesChanged, detectLineEnding
from codo.utils.file_read import readFileSyncWithMetadata, detectEncoding

class TestFsOperations:
    """测试文件系统操作"""

    def test_read_write_file(self, tmp_path):
        """测试文件读写"""
        fs = getFsImplementation()

        test_file = tmp_path / "test.txt"
        content = "Hello, World!\nLine 2"

        # 写入文件
        fs.writeFile(str(test_file), content)

        # 读取文件
        read_content = fs.readFile(str(test_file))

        assert read_content == content

    def test_file_exists(self, tmp_path):
        """测试文件存在检查"""
        fs = getFsImplementation()

        test_file = tmp_path / "test.txt"

        # 文件不存在
        assert not fs.exists(str(test_file))

        # 创建文件
        fs.writeFile(str(test_file), "test")

        # 文件存在
        assert fs.exists(str(test_file))

    def test_is_binary_file(self, tmp_path):
        """测试二进制文件检测"""
        fs = getFsImplementation()

        # 文本文件
        text_file = tmp_path / "text.txt"
        fs.writeFile(str(text_file), "Hello, World!")
        assert not fs.isBinaryFile(str(text_file))

        # 二进制文件（包含 NULL 字节）
        binary_file = tmp_path / "binary.bin"
        fs.writeFileBytes(str(binary_file), b'\x00\x01\x02\x03')
        assert fs.isBinaryFile(str(binary_file))

class TestPath:
    """测试路径处理"""

    def test_expand_path(self):
        """测试路径扩展"""
        # 绝对路径保持不变（使用当前系统的绝对路径格式）
        if os.name == 'nt':
            abs_path = "C:\\Users\\test.txt"
        else:
            abs_path = "/home/user/test.txt"

        expanded = expandPath(abs_path)
        assert os.path.isabs(expanded)

        # 相对路径转换为绝对路径
        rel_path = "test.txt"
        expanded = expandPath(rel_path)
        assert os.path.isabs(expanded)

    def test_to_relative_path(self, tmp_path):
        """测试相对路径转换"""
        base = str(tmp_path)
        file_path = str(tmp_path / "subdir" / "test.txt")

        rel_path = toRelativePath(file_path, base)
        assert not rel_path.startswith('..')
        assert 'test.txt' in rel_path

    def test_is_unc_path(self):
        """测试 UNC 路径检测"""
        assert isUncPath("\\\\server\\share\\file.txt")
        assert isUncPath("//server/share/file.txt")
        assert not isUncPath("C:\\Users\\test.txt")
        assert not isUncPath("/home/user/test.txt")

    def test_is_sub_path(self, tmp_path):
        """测试子路径检查"""
        parent = str(tmp_path)
        child = str(tmp_path / "subdir" / "file.txt")
        other = "/other/path/file.txt"

        assert isSubPath(child, parent)
        assert not isSubPath(other, parent)

class TestDiff:
    """测试 diff 生成"""

    def test_generate_unified_diff(self):
        """测试 unified diff 生成"""
        original = "line1\nline2\nline3"
        modified = "line1\nmodified\nline3"

        diff = generateUnifiedDiff(original, modified)

        assert '--- original' in diff
        assert '+++ modified' in diff
        assert '-line2' in diff
        assert '+modified' in diff

    def test_count_lines_changed(self):
        """测试变更行数统计"""
        original = "line1\nline2\nline3"
        modified = "line1\nmodified\nline3\nline4"

        added, deleted = countLinesChanged(original, modified)

        assert added == 2  # modified + line4
        assert deleted == 1  # line2

    def test_detect_line_ending(self):
        """测试行尾符检测"""
        assert detectLineEnding("line1\r\nline2\r\n") == '\r\n'
        assert detectLineEnding("line1\nline2\n") == '\n'
        assert detectLineEnding("line1\rline2\r") == '\r'

class TestFileRead:
    """测试文件读取"""

    def test_read_file_with_metadata(self, tmp_path):
        """测试带元数据的文件读取"""
        test_file = tmp_path / "test.txt"
        content = "line1\nline2\nline3"
        test_file.write_text(content)

        result = readFileSyncWithMetadata(str(test_file))

        assert result.content == content
        assert result.lineCount == 3
        assert not result.isBinary
        assert result.encoding in ['utf-8', 'latin-1']

    def test_detect_encoding(self, tmp_path):
        """测试编码检测"""
        # UTF-8 文件
        utf8_file = tmp_path / "utf8.txt"
        utf8_file.write_text("Hello, 世界!", encoding='utf-8')

        encoding = detectEncoding(str(utf8_file))
        assert encoding == 'utf-8'

if __name__ == '__main__':
    pytest.main([__file__, '-v'])
