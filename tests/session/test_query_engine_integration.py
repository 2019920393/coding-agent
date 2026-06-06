"""
测试 QueryEngine 与 SessionStorage 的集成

验证会话持久化在各个关键触发点是否正常工作。
"""

from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from codo.query_engine import QueryEngine
from codo.session.storage import SessionStorage
from tests.fake_anthropic_stream import (
    FakeAnthropicStream,
    FakeContentBlock,
    FakeDelta,
    FakeFinalMessage,
    FakeStreamEvent,
)


@pytest.fixture
def mock_session_storage():
    """Mock SessionStorage"""
    storage = Mock(spec=SessionStorage)
    storage.record_messages = Mock()
    storage.save_session = Mock()
    storage.load_messages = Mock(return_value=[])
    storage.current_title = "已有标题"
    return storage

@pytest.fixture
def mock_client():
    """Mock client"""
    client = Mock()
    return client

@pytest.fixture
def query_engine(mock_client, mock_session_storage):
    """创建 QueryEngine 实例"""
    engine = QueryEngine(
        client=mock_client,
        model="claude-opus-4",
        session_id=str(uuid4()),
        cwd="/test/path",
    )
    engine.session_storage = mock_session_storage
    return engine

class TestSessionPersistenceIntegration:
    """测试会话持久化集成"""

    @pytest.mark.asyncio
    async def test_assistant_message_saved(self, query_engine, mock_session_storage):
        """测试 assistant 消息是否被保存"""
        mock_stream = FakeAnthropicStream(
            events=[
                FakeStreamEvent(
                    type="content_block_start",
                    index=0,
                    content_block=FakeContentBlock(type="text"),
                ),
                FakeStreamEvent(
                    type="content_block_delta",
                    delta=FakeDelta(type="text_delta", text="Hello"),
                ),
                FakeStreamEvent(type="content_block_stop", index=0),
            ],
            final_message=FakeFinalMessage(
                content=[FakeContentBlock(type="text", text="Hello")],
            ),
        )

        query_engine.client.messages.stream = Mock(return_value=mock_stream)

        # 执行查询
        events = []
        async for event in query_engine.submit_message_stream("test"):
            events.append(event)

        # 验证 session_storage.record_messages 被调用
        assert mock_session_storage.record_messages.called

        # 验证保存的消息包含 assistant 消息
        call_args = mock_session_storage.record_messages.call_args_list
        saved_messages = []
        for call in call_args:
            saved_messages.extend(call[0][0])

        assert any(msg.get("role") == "assistant" for msg in saved_messages)

    @pytest.mark.asyncio
    async def test_max_turns_error_saved(self, query_engine, mock_session_storage):
        """测试 maxTurns 错误消息是否被保存"""
        # 设置 maxTurns 限制
        query_engine.max_turns = 1
        query_engine.turn_count = 0

        mock_stream = FakeAnthropicStream(
            events=[
                FakeStreamEvent(
                    type="content_block_start",
                    index=0,
                    content_block=FakeContentBlock(
                        type="tool_use",
                        id="tool_1",
                        name="test_tool",
                    ),
                ),
                FakeStreamEvent(type="content_block_stop", index=0),
            ],
            final_message=FakeFinalMessage(
                content=[
                    FakeContentBlock(
                        type="tool_use",
                        id="tool_1",
                        name="test_tool",
                    )
                ],
            ),
        )

        query_engine.client.messages.stream = Mock(return_value=mock_stream)

        # Mock 工具执行
        with patch("codo.query_engine.run_tools_batch") as mock_run_tools:
            mock_run_tools.return_value = AsyncMock(
                batches=[],
                context_modifiers=[],
            )

            # 执行查询
            events = []
            async for event in query_engine.submit_message_stream("test"):
                events.append(event)

        # 验证 maxTurns 错误被保存
        call_args = mock_session_storage.record_messages.call_args_list
        saved_messages = []
        for call in call_args:
            saved_messages.extend(call[0][0])

        # 检查是否有 maxTurns 错误消息
        max_turns_messages = [
            msg for msg in saved_messages
            if msg.get("role") == "user" and
            any(att.get("type") == "max_turns_reached"
                for att in msg.get("attachments", []))
        ]

        assert len(max_turns_messages) > 0

    @pytest.mark.asyncio
    async def test_tool_results_saved(self, query_engine, mock_session_storage):
        """测试工具执行结果是否被保存"""
        # 限制为单轮，避免 tool_use follow-up 进入无限循环
        query_engine.max_turns = 1

        mock_stream = FakeAnthropicStream(
            events=[
                FakeStreamEvent(
                    type="content_block_start",
                    index=0,
                    content_block=FakeContentBlock(
                        type="tool_use",
                        id="tool_1",
                        name="test_tool",
                    ),
                ),
                FakeStreamEvent(type="content_block_stop", index=0),
            ],
            final_message=FakeFinalMessage(
                content=[
                    FakeContentBlock(
                        type="tool_use",
                        id="tool_1",
                        name="test_tool",
                    )
                ],
            ),
        )

        query_engine.client.messages.stream = Mock(return_value=mock_stream)

        # Mock 工具执行结果
        mock_task = Mock()
        mock_task.tool_use_id = "tool_1"
        mock_task.error = None
        mock_task.result = Mock(data="test result")

        mock_batch = Mock()
        mock_batch.tasks = [mock_task]

        with patch("codo.query_engine.run_tools_batch") as mock_run_tools:
            mock_run_tools.return_value = AsyncMock(
                batches=[mock_batch],
                context_modifiers=[],
            )

            # 执行查询（走真实 submit_message_stream 逻辑）
            events = []
            async for event in query_engine.submit_message_stream("test"):
                events.append(event)

        # 验证工具结果被保存
        call_args = mock_session_storage.record_messages.call_args_list
        saved_messages = []
        for call in call_args:
            saved_messages.extend(call[0][0])

        # 检查是否有 tool_result 消息
        tool_result_messages = [
            msg for msg in saved_messages
            if msg.get("role") == "user" and
            isinstance(msg.get("content"), list) and
            any(
                isinstance(block, dict) and block.get("type") == "tool_result"
                for block in msg.get("content", [])
            )
        ]

        assert len(tool_result_messages) > 0

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
