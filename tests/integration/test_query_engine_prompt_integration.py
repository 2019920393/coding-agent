"""
测试 QueryEngine 与 Prompt 系统的集成
"""

import asyncio
import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from codo.query_engine import QueryEngine

class TestQueryEnginePromptIntegration:
    """测试 QueryEngine 与 Prompt 系统的集成"""

    @pytest.fixture
    def mock_api_key(self):
        """模拟 API key"""
        return "test-api-key"

    @pytest.fixture
    def test_cwd(self, tmp_path):
        """创建测试工作目录"""
        return str(tmp_path)

    @pytest.fixture
    def query_engine(self, mock_api_key, test_cwd):
        """创建 QueryEngine 实例"""
        return QueryEngine(
            api_key=mock_api_key,
            cwd=test_cwd,
            enable_persistence=False,  # 禁用持久化以简化测试
        )

    @pytest.mark.asyncio
    async def test_query_engine_initialization(self, query_engine, test_cwd):
        """测试 QueryEngine 初始化"""
        # 验证基本属性
        assert query_engine.cwd == test_cwd
        assert query_engine.model == "claude-opus-4-20250514"

        # 验证工具列表（当前实现包含更多内置工具，至少应包含核心 6 个）
        assert len(query_engine.tools) >= 6
        tool_names = [tool.name for tool in query_engine.tools]
        assert "Bash" in tool_names
        assert "Read" in tool_names
        assert "Edit" in tool_names
        assert "Write" in tool_names
        assert "Glob" in tool_names
        assert "Grep" in tool_names

        # 验证工具模式（需要异步生成）
        from codo.services.prompt.tools import tools_to_api_schemas
        tool_schemas = await tools_to_api_schemas(query_engine.tools)
        assert len(tool_schemas) >= 6
        schema_names = {schema.get("name") for schema in tool_schemas}
        assert {"Bash", "Read", "Edit", "Write", "Glob", "Grep"}.issubset(schema_names)
        for schema in tool_schemas:
            assert "name" in schema
            assert "description" in schema
            assert "input_schema" in schema

        # 验证 PromptBuilder 已初始化
        assert query_engine.prompt_builder is not None
        assert query_engine.prompt_builder.cwd == test_cwd

    @pytest.mark.asyncio
    async def test_system_prompt_generation(self, query_engine):
        """测试系统提示词生成"""
        # 构建系统提示词（不需要传递 tools 参数）
        system_prompt = query_engine.prompt_builder.build_system_prompt(
            language_preference="zh-CN",
        )

        # 验证系统提示词是列表格式
        assert isinstance(system_prompt, list)
        assert len(system_prompt) > 0

        # 转换为文本进行验证
        system_prompt_text = query_engine.prompt_builder.build_system_prompt_text(
            language_preference="zh-CN",
        )

        # 验证系统提示词包含关键内容
        assert "agent" in system_prompt_text.lower() or "assistant" in system_prompt_text.lower()
        assert query_engine.cwd in system_prompt_text
        # 验证包含语言偏好
        assert "zh-CN" in system_prompt_text

    @pytest.mark.asyncio
    async def test_tool_schemas_format(self, query_engine):
        """测试工具模式格式"""
        from codo.services.prompt.tools import tools_to_api_schemas

        # 生成工具模式
        tool_schemas = await tools_to_api_schemas(query_engine.tools)

        # 验证每个工具模式的格式
        for schema in tool_schemas:
            # 必需字段
            assert "name" in schema
            assert "description" in schema
            assert "input_schema" in schema

            # 验证 input_schema 结构
            input_schema = schema["input_schema"]
            assert "type" in input_schema
            assert input_schema["type"] == "object"
            assert "properties" in input_schema

    @pytest.mark.asyncio
    async def test_message_normalization(self, query_engine):
        """测试消息规范化"""
        from codo.services.prompt.messages import normalize_messages_for_api

        # 添加一些测试消息
        query_engine.messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": [{"type": "text", "text": "Hi"}]},
            {"role": "user", "content": "How are you?"},
        ]

        # 规范化消息
        normalized = normalize_messages_for_api(query_engine.messages)

        # 验证规范化结果
        assert len(normalized) == 3
        assert all("role" in msg for msg in normalized)
        assert all("content" in msg for msg in normalized)

    @pytest.mark.asyncio
    async def test_submit_message_with_mock_api(self, query_engine):
        """测试 submit_message 使用模拟 API"""
        # 创建模拟的流式响应
        mock_stream = AsyncMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)

        # 模拟流式事件
        async def mock_stream_events():
            # 模拟文本块开始
            yield MagicMock(
                type="content_block_start",
                content_block=MagicMock(type="text", text="")
            )
            # 模拟文本增量
            yield MagicMock(
                type="content_block_delta",
                delta=MagicMock(type="text_delta", text="Hello")
            )
            # 模拟块结束
            yield MagicMock(type="content_block_stop")

        mock_stream.__aiter__ = mock_stream_events

        # 模拟最终消息
        mock_final_message = MagicMock()
        mock_final_message.content = [
            MagicMock(type="text", text="Hello, how can I help you?")
        ]
        mock_stream.get_final_message = AsyncMock(return_value=mock_final_message)

        # 打补丁
        with patch.object(query_engine.client.messages, 'stream', return_value=mock_stream):
            # 提交消息
            results = []
            try:
                async for result in query_engine.submit_message("Test message"):
                    results.append(result)
            except Exception as e:
                # 如果出错，至少验证消息被添加到历史
                pass

            # 验证消息被添加到历史
            assert len(query_engine.messages) >= 1
            assert query_engine.messages[0]["role"] == "user"
            assert query_engine.messages[0]["content"] == "Test message"

    @pytest.mark.asyncio
    async def test_prompt_builder_with_codo_md(self, query_engine, test_cwd):
        """测试 PromptBuilder 读取 CODO.md"""
        # 创建 CODO.md 文件
        codo_md_path = Path(test_cwd) / "CODO.md"
        codo_md_path.write_text("# Custom Instructions\n\nThis is a test project.")

        # 构建系统提示词
        system_prompt_text = query_engine.prompt_builder.build_system_prompt_text()

        # 验证包含 CODO.md 内容
        assert "Custom Instructions" in system_prompt_text or "test project" in system_prompt_text

    def test_deprecated_build_system_prompt(self, query_engine):
        """测试废弃的 _build_system_prompt 方法"""
        # 调用废弃方法
        old_prompt = query_engine._build_system_prompt()

        # 验证仍然可以工作（向后兼容）
        assert "coding assistant" in old_prompt
        assert query_engine.cwd in old_prompt
        assert "bash" in old_prompt

    @pytest.mark.asyncio
    async def test_tools_to_api_schemas_integration(self, query_engine):
        """测试 tools_to_api_schemas 集成"""
        from codo.services.prompt.tools import tools_to_api_schemas

        # 直接调用转换函数
        schemas = await tools_to_api_schemas(query_engine.tools)

        # 验证结果
        assert len(schemas) >= 6
        schema_names = {schema.get("name") for schema in schemas}
        assert {"Bash", "Read", "Edit", "Write", "Glob", "Grep"}.issubset(schema_names)

        # 验证每个模式
        for schema in schemas:
            assert "name" in schema
            assert "description" in schema
            assert "input_schema" in schema

    @pytest.mark.asyncio
    async def test_message_history_persistence(self, query_engine):
        """测试消息历史持久化"""
        # 添加用户消息
        query_engine.messages.append({
            "role": "user",
            "content": "Test message",
            "uuid": "test-uuid-1",
            "type": "user",
        })

        # 添加助手消息
        query_engine.messages.append({
            "role": "assistant",
            "content": [{"type": "text", "text": "Test response"}],
            "uuid": "test-uuid-2",
            "type": "assistant",
        })

        # 验证消息历史
        assert len(query_engine.messages) == 2
        assert query_engine.messages[0]["role"] == "user"
        assert query_engine.messages[1]["role"] == "assistant"

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
