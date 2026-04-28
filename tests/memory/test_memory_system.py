"""Memory System 单元测试"""
import pytest
import tempfile
import shutil
from pathlib import Path

from codo.services.memory import (
    MemoryManager,
    MemoryScanner,
    MemoryLoader,
    get_auto_memory_path,
    sanitize_filename,
)

@pytest.fixture
def temp_memory_dir():
    """创建临时记忆目录"""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir)

class TestMemoryManager:
    """MemoryManager 测试套件"""

    def test_create_memory(self, temp_memory_dir):
        """测试创建记忆"""
        manager = MemoryManager(temp_memory_dir)

        file_path = manager.create_memory(
            name="User Role",
            description="User is a senior Go engineer",
            memory_type="user",
            content="The user has 10+ years of Go experience.",
            topic="role"
        )

        assert Path(file_path).exists()
        assert "user_role.md" in file_path

    def test_read_memory(self, temp_memory_dir):
        """测试读取记忆"""
        manager = MemoryManager(temp_memory_dir)

        file_path = manager.create_memory(
            name="Test Memory",
            description="Test description",
            memory_type="feedback",
            content="Test content"
        )

        memory = manager.read_memory(file_path)

        assert memory is not None
        assert memory.frontmatter.name == "Test Memory"
        assert memory.frontmatter.type == "feedback"
        assert memory.content == "Test content"

    def test_update_memory(self, temp_memory_dir):
        """测试更新记忆"""
        manager = MemoryManager(temp_memory_dir)

        file_path = manager.create_memory(
            name="Original",
            description="Original description",
            memory_type="project",
            content="Original content"
        )

        success = manager.update_memory(
            file_path,
            name="Updated",
            content="Updated content"
        )

        assert success

        memory = manager.read_memory(file_path)
        assert memory.frontmatter.name == "Updated"
        assert memory.content == "Updated content"

    def test_delete_memory(self, temp_memory_dir):
        """测试删除记忆"""
        manager = MemoryManager(temp_memory_dir)

        file_path = manager.create_memory(
            name="To Delete",
            description="Will be deleted",
            memory_type="reference",
            content="Delete me"
        )

        assert Path(file_path).exists()

        success = manager.delete_memory(file_path)

        assert success
        assert not Path(file_path).exists()

class TestMemoryScanner:
    """MemoryScanner 测试套件"""

    def test_scan_memory_files(self, temp_memory_dir):
        """测试扫描记忆文件"""
        manager = MemoryManager(temp_memory_dir)
        scanner = MemoryScanner(temp_memory_dir)

        # 创建几个记忆文件
        manager.create_memory("Memory 1", "First memory", "user", "Content 1")
        manager.create_memory("Memory 2", "Second memory", "feedback", "Content 2")
        manager.create_memory("Memory 3", "Third memory", "project", "Content 3")

        headers = scanner.scan_memory_files()

        assert len(headers) == 3
        assert all(h.name in ["Memory 1", "Memory 2", "Memory 3"] for h in headers)

    def test_format_memory_manifest(self, temp_memory_dir):
        """测试格式化记忆清单"""
        manager = MemoryManager(temp_memory_dir)
        scanner = MemoryScanner(temp_memory_dir)

        manager.create_memory("Test", "Test memory", "user", "Content")

        headers = scanner.scan_memory_files()
        manifest = scanner.format_memory_manifest(headers)

        assert "[user]" in manifest
        assert "Test memory" in manifest

class TestMemoryLoader:
    """MemoryLoader 测试套件"""

    def test_load_memory_index(self, temp_memory_dir):
        """测试加载记忆索引"""
        manager = MemoryManager(temp_memory_dir)
        loader = MemoryLoader(temp_memory_dir)

        # 创建记忆（会自动创建索引）
        manager.create_memory("Test", "Test memory", "user", "Content")

        index_content = loader.load_memory_index()

        assert index_content is not None
        assert "auto memory" in index_content
        assert "Test" in index_content

    def test_build_memory_context(self, temp_memory_dir):
        """测试构建记忆上下文"""
        manager = MemoryManager(temp_memory_dir)
        loader = MemoryLoader(temp_memory_dir)

        manager.create_memory("User Info", "User details", "user", "Content")

        context = loader.build_memory_context()

        assert "auto memory" in context
        assert "Types of memory" in context
        assert "User Info" in context

class TestPaths:
    """路径函数测试套件"""

    def test_sanitize_filename(self):
        """测试文件名清理"""
        assert sanitize_filename("User Role") == "user_role"
        assert sanitize_filename("Test-Memory") == "test-memory"
        assert sanitize_filename("Special!@#$%Chars") == "specialchars"

    def test_get_auto_memory_path(self):
        """测试获取自动记忆路径"""
        path = get_auto_memory_path("/home/user/project")

        assert "memory" in path
        assert Path(path).exists()
