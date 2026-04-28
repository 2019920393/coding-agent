"""
Prompt 系统测试

测试 Prompt 装配系统的各个组件。
"""

import pytest
import asyncio
import os
import tempfile
from pathlib import Path

from codo.services.prompt.context import ContextProvider, get_context_for_cwd
from codo.services.prompt.builder import PromptBuilder, build_system_prompt_for_cwd
from codo.services.prompt.tools import tool_to_api_schema, tools_to_api_schemas
from codo.services.prompt.messages import (
    normalize_messages_for_api,
    ensure_alternating_messages,
    add_cache_breakpoints,
    create_user_message,
    create_assistant_message,
)
from codo.services.prompt.assembler import APIRequestAssembler, assemble_api_request
from codo.tools_registry import get_all_tools

class TestContextProvider:
    """测试上下文提供者"""

    def test_is_git_repository_false(self, tmp_path):
        """测试非 Git 仓库检测"""
        provider = ContextProvider(str(tmp_path))
        assert provider.is_git_repository() is False

    def test_get_git_status_none(self, tmp_path):
        """测试非 Git 仓库返回 None"""
        provider = ContextProvider(str(tmp_path))
        assert provider.get_git_status() is None

    def test_read_codo_md_none(self, tmp_path):
        """测试不存在的 CODO.md"""
        provider = ContextProvider(str(tmp_path))
        assert provider.read_codo_md() is None

    def test_read_codo_md_exists(self, tmp_path):
        """测试读取 CODO.md"""
        # 创建 CODO.md
        codo_md = tmp_path / "CODO.md"
        codo_md.write_text("Test project instructions")

        provider = ContextProvider(str(tmp_path))
        content = provider.read_codo_md()
        assert content == "Test project instructions"

    def test_get_current_date(self, tmp_path):
        """测试获取当前日期"""
        provider = ContextProvider(str(tmp_path))
        date = provider.get_current_date()
        assert len(date) == 10  # YYYY-MM-DD
        assert date.count("-") == 2

    def test_get_user_context(self, tmp_path):
        """测试获取用户上下文"""
        # 创建 CODO.md
        codo_md = tmp_path / "CODO.md"
        codo_md.write_text("Test instructions")

        provider = ContextProvider(str(tmp_path))
        context = provider.get_user_context()

        assert context is not None
        assert "Test instructions" in context
        assert "Current Date" in context

class TestPromptBuilder:
    """测试 Prompt 构建器"""

    def test_get_enabled_tools(self, tmp_path):
        """测试获取启用的工具"""
        builder = PromptBuilder(str(tmp_path))
        tools = builder.get_enabled_tools()

        assert isinstance(tools, set)
        assert len(tools) > 0
        assert "Bash" in tools  # 新工具系统使用大写名称
        assert "Read" in tools

    def test_build_system_prompt(self, tmp_path):
        """测试构建系统提示词"""
        builder = PromptBuilder(str(tmp_path))
        prompt = builder.build_system_prompt()

        assert isinstance(prompt, list)
        assert len(prompt) > 0
        assert prompt[0]["type"] == "text"
        assert "cache_control" in prompt[0]

    def test_build_system_prompt_with_language(self, tmp_path):
        """测试带语言偏好的系统提示词"""
        builder = PromptBuilder(str(tmp_path))
        prompt = builder.build_system_prompt(language_preference="Chinese")

        text = prompt[0]["text"]
        assert "Chinese" in text

    def test_build_system_prompt_with_custom(self, tmp_path):
        """测试自定义系统提示词"""
        builder = PromptBuilder(str(tmp_path))
        custom = "Custom system prompt"
        prompt = builder.build_system_prompt(custom_system_prompt=custom)

        assert len(prompt) == 1
        assert prompt[0]["text"] == custom

    def test_build_system_prompt_text(self, tmp_path):
        """测试构建系统提示词文本"""
        builder = PromptBuilder(str(tmp_path))
        text = builder.build_system_prompt_text()

        assert isinstance(text, str)
        assert len(text) > 0
        assert "interactive agent" in text.lower()

class TestToolsConversion:
    """测试工具转换"""

    @pytest.mark.asyncio
    async def test_tool_to_api_schema(self):
        """测试单个工具转换"""
        tools = get_all_tools()
        bash_tool = next(t for t in tools if t.name == "Bash")  # 新工具系统使用大写名称

        schema = await tool_to_api_schema(bash_tool)

        assert schema["name"] == "Bash"
        assert "description" in schema
        assert "input_schema" in schema
        assert schema["input_schema"]["type"] == "object"

    @pytest.mark.asyncio
    async def test_tools_to_api_schemas(self):
        """测试工具列表转换"""
        tools = get_all_tools()
        schemas = await tools_to_api_schemas(tools)

        assert isinstance(schemas, list)
        assert len(schemas) == len(tools)

        # 检查最后一个工具有缓存控制
        assert "cache_control" in schemas[-1]

    @pytest.mark.asyncio
    async def test_tools_to_api_schemas_empty(self):
        """测试空工具列表"""
        schemas = await tools_to_api_schemas([])
        assert schemas == []

class TestMessagesNormalization:
    """测试消息规范化"""

    def test_normalize_messages_basic(self):
        """测试基本消息规范化"""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]

        normalized = normalize_messages_for_api(messages)

        assert len(normalized) == 2
        assert normalized[0]["role"] == "user"
        assert normalized[1]["role"] == "assistant"

    def test_normalize_messages_filter_virtual(self):
        """测试过滤虚拟消息"""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi", "is_virtual": True},
            {"role": "user", "content": "How are you?"},
        ]

        normalized = normalize_messages_for_api(messages)

        assert len(normalized) == 2
        assert normalized[0]["content"] == "Hello"
        assert normalized[1]["content"] == "How are you?"

    def test_normalize_attachment_message_for_model(self):
        """attachment 消息应被转成模型可见的 user 内容。"""
        messages = [
            {"role": "user", "content": "请处理这个命令"},
            {
                "type": "attachment",
                "attachment": {
                    "type": "queued_command",
                    "prompt": "<command-name>/review</command-name>\nInspect the patch.",
                    "origin": {"kind": "slash_command", "name": "review"},
                },
            },
        ]

        normalized = normalize_messages_for_api(messages)

        assert len(normalized) == 1
        assert normalized[0]["role"] == "user"
        assert "/review" in normalized[0]["content"]
        assert "Inspect the patch." in normalized[0]["content"]

    def test_normalize_memory_attachment_for_model(self):
        """memory attachment 应被注入模型上下文。"""
        messages = [
            {
                "type": "attachment",
                "attachment": {
                    "type": "memory",
                    "path": "/tmp/memory/user.md",
                    "content": "User prefers concise answers.",
                },
            },
        ]

        normalized = normalize_messages_for_api(messages)

        assert len(normalized) == 1
        assert normalized[0]["role"] == "user"
        assert "User prefers concise answers." in normalized[0]["content"]
        assert "/tmp/memory/user.md" in normalized[0]["content"]

    def test_ensure_alternating_messages(self):
        """测试确保消息交替"""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "user", "content": "World"},
            {"role": "assistant", "content": "Hi"},
        ]

        alternating = ensure_alternating_messages(messages)

        assert len(alternating) == 2
        assert alternating[0]["role"] == "user"
        assert "Hello" in alternating[0]["content"]
        assert "World" in alternating[0]["content"]

    def test_add_cache_breakpoints(self):
        """测试添加缓存断点"""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
            {"role": "user", "content": "How are you?"},
        ]

        cached = add_cache_breakpoints(messages, enable_caching=True)

        # 检查最后一条 user 消息有缓存控制
        last_user_msg = cached[-1]
        assert last_user_msg["role"] == "user"
        assert isinstance(last_user_msg["content"], list)
        assert "cache_control" in last_user_msg["content"][0]

    def test_create_user_message(self):
        """测试创建用户消息"""
        msg = create_user_message("Hello")

        assert msg["role"] == "user"
        assert msg["content"] == "Hello"

    def test_create_assistant_message(self):
        """测试创建助手消息"""
        msg = create_assistant_message("Hi there")

        assert msg["role"] == "assistant"
        assert msg["content"] == "Hi there"

class TestAPIRequestAssembler:
    """测试 API 请求组装器"""

    @pytest.mark.asyncio
    async def test_assemble_request_basic(self, tmp_path):
        """测试基本请求组装"""
        assembler = APIRequestAssembler(str(tmp_path))

        messages = [
            {"role": "user", "content": "Hello"},
        ]

        params = await assembler.assemble_request(messages)

        assert "model" in params
        assert "messages" in params
        assert "system" in params
        assert "tools" in params
        assert "max_tokens" in params

    @pytest.mark.asyncio
    async def test_assemble_request_with_temperature(self, tmp_path):
        """测试带温度参数的请求组装"""
        assembler = APIRequestAssembler(str(tmp_path), temperature=0.7)

        messages = [
            {"role": "user", "content": "Hello"},
        ]

        params = await assembler.assemble_request(messages)

        assert params["temperature"] == 0.7

    @pytest.mark.asyncio
    async def test_assemble_request_simple(self, tmp_path):
        """测试简单请求组装"""
        assembler = APIRequestAssembler(str(tmp_path))

        params = await assembler.assemble_request_simple("Hello")

        assert len(params["messages"]) == 1
        assert params["messages"][0]["role"] == "user"
        # 内容可能被转换为内容块格式（带缓存控制）
        content = params["messages"][0]["content"]
        if isinstance(content, str):
            assert content == "Hello"
        elif isinstance(content, list):
            assert content[0]["text"] == "Hello"

    @pytest.mark.asyncio
    async def test_assemble_request_with_history(self, tmp_path):
        """测试带历史的请求组装"""
        assembler = APIRequestAssembler(str(tmp_path))

        history = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
        ]

        params = await assembler.assemble_request_simple(
            "How are you?",
            conversation_history=history,
        )

        assert len(params["messages"]) == 3

    @pytest.mark.asyncio
    async def test_assemble_api_request_function(self, tmp_path):
        """测试便捷函数"""
        messages = [
            {"role": "user", "content": "Hello"},
        ]

        params = await assemble_api_request(
            cwd=str(tmp_path),
            messages=messages,
        )

        assert "model" in params
        assert "messages" in params
        assert "system" in params
        assert "tools" in params

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
