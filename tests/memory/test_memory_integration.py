"""Memory System 集成测试"""
import pytest
import tempfile
import shutil
from pathlib import Path

from codo.services.memory import MemoryManager, get_auto_memory_path
from codo.services.prompt.context import get_user_context, clear_context_cache

class TestMemoryIntegration:
    """Memory System 集成测试"""

    def test_memory_loaded_in_user_context(self):
        """测试记忆加载到用户上下文"""
        # 创建临时目录作为 cwd
        with tempfile.TemporaryDirectory() as temp_dir:
            # 清空缓存
            clear_context_cache()

            # 创建记忆
            memory_dir = get_auto_memory_path(temp_dir)
            manager = MemoryManager(memory_dir)

            manager.create_memory(
                name="Test Memory",
                description="Test integration",
                memory_type="user",
                content="This is a test memory for integration."
            )

            # 获取用户上下文
            context = get_user_context(temp_dir)

            # 验证记忆已加载
            assert "autoMemory" in context
            assert "Test Memory" in context["autoMemory"]
            assert "auto memory" in context["autoMemory"]

    def test_memory_context_cached(self):
        """测试记忆上下文缓存"""
        with tempfile.TemporaryDirectory() as temp_dir:
            clear_context_cache()

            # 第一次加载
            context1 = get_user_context(temp_dir)

            # 第二次加载（应该从缓存）
            context2 = get_user_context(temp_dir)

            # 应该是同一个对象
            assert context1 is context2

    def test_memory_with_multiple_types(self):
        """测试多种类型的记忆"""
        with tempfile.TemporaryDirectory() as temp_dir:
            clear_context_cache()

            memory_dir = get_auto_memory_path(temp_dir)
            manager = MemoryManager(memory_dir)

            # 创建不同类型的记忆
            manager.create_memory(
                name="User Info",
                description="User details",
                memory_type="user",
                content="User is a senior engineer."
            )

            manager.create_memory(
                name="Feedback",
                description="Code style feedback",
                memory_type="feedback",
                content="Use type hints in Python."
            )

            manager.create_memory(
                name="Project Info",
                description="Project deadline",
                memory_type="project",
                content="Release on 2026-05-01."
            )

            # 获取上下文
            context = get_user_context(temp_dir)

            # 验证所有记忆都在索引中
            assert "autoMemory" in context
            memory_content = context["autoMemory"]
            assert "User Info" in memory_content
            assert "Feedback" in memory_content
            assert "Project Info" in memory_content
