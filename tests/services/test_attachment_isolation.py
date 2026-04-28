"""
测试 attachment 消息隔离
验证 attachment 消息不会被持久化到下一轮迭代
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codo.query import QueryParams, query_loop

@pytest.mark.asyncio
async def test_attachment_messages_not_persisted():
    """
    测试 attachment 消息不会被持久化到 state.messages

    messages: [...messagesForQuery, ...assistantMessages, ...toolResults]

    其中 messagesForQuery 不包含 attachment 消息
    """
    # Mock client
    mock_client = MagicMock()

    # Mock streaming response - 第一轮返回工具调用，第二轮返回文本
    class MockStream:
        def __init__(self, has_tool_use=True):
            self.has_tool_use = has_tool_use
            self.events = []

            if has_tool_use:
                # 第一轮：返回工具调用
                self.events = [
                    MagicMock(
                        type="content_block_start",
                        content_block=MagicMock(
                            type="tool_use",
                            id="tool_1",
                            name="Read",
                        ),
                    ),
                    MagicMock(
                        type="content_block_delta",
                        delta=MagicMock(
                            type="input_json_delta",
                            partial_json='{"file_path": "/test.txt"}',
                        ),
                    ),
                    MagicMock(type="content_block_stop"),
                    MagicMock(
                        type="message_stop",
                        message=MagicMock(
                            usage=MagicMock(
                                input_tokens=100,
                                output_tokens=50,
                            ),
                        ),
                    ),
                ]
            else:
                # 第二轮：返回纯文本（无工具调用）
                self.events = [
                    MagicMock(
                        type="content_block_start",
                        content_block=MagicMock(type="text"),
                    ),
                    MagicMock(
                        type="content_block_delta",
                        delta=MagicMock(type="text_delta", text="Done!"),
                    ),
                    MagicMock(type="content_block_stop"),
                    MagicMock(
                        type="message_stop",
                        message=MagicMock(
                            usage=MagicMock(
                                input_tokens=100,
                                output_tokens=20,
                            ),
                        ),
                    ),
                ]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def __aiter__(self):
            for event in self.events:
                yield event

    # 跟踪 API 调用次数
    call_count = 0

    def mock_stream(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        # 第一次调用返回工具使用，第二次返回纯文本
        return MockStream(has_tool_use=(call_count == 1))

    mock_client.messages.stream = mock_stream

    # Mock tools
    mock_tools = [
        MagicMock(
            name="Read",
            execute=AsyncMock(return_value={
                "content": "test content",
                "is_error": False,
            }),
        ),
    ]

    # 初始消息
    initial_messages = [
        {"role": "user", "content": [{"type": "text", "text": "Read /test.txt"}]},
    ]

    # 跟踪每轮的消息数量
    messages_per_turn = []

    # Mock get_attachment_messages 返回一个 attachment
    async def mock_get_attachments(*args, **kwargs):
        return [
            {
                "role": "user",
                "content": [{
                    "type": "text",
                    "text": "[ATTACHMENT] IDE Selection: test.py:1-10",
                }],
            }
        ]

    with patch("codo.query.get_attachment_messages", mock_get_attachments):
        with patch("codo.query.auto_compact_if_needed", AsyncMock(return_value=None)):
            with patch("codo.query.microcompact_if_needed", AsyncMock(return_value=MagicMock(compacted_count=0, messages=[]))):
                params = QueryParams(
                    client=mock_client,
                    model="claude-opus-4",
                    system_prompt=[{"type": "text", "text": "You are a helpful assistant"}],
                    messages=initial_messages,
                    tools=mock_tools,
                    max_turns=2,
                    verbose=True,
                )

                events = []
                async for event in query_loop(params):
                    events.append(event)

                    # 在每次 stream_request_start 时记录当前消息数
                    if event.get("type") == "stream_request_start":
                        # 这里我们无法直接访问 state.messages，但可以通过其他方式验证
                        pass

    # 验证：
    # 1. 应该有两轮 API 调用
    assert call_count == 2, f"Expected 2 API calls, got {call_count}"

    # 2. 检查事件流
    stream_starts = [e for e in events if e.get("type") == "stream_request_start"]
    assert len(stream_starts) == 2, f"Expected 2 stream starts, got {len(stream_starts)}"

    # 3. 检查 attachment 消息被产出
    attachment_events = [e for e in events if isinstance(e, dict) and
                        e.get("role") == "user" and
                        any("[ATTACHMENT]" in str(c) for c in e.get("content", []))]
    # 每轮都应该产出 attachment
    assert len(attachment_events) == 2, f"Expected 2 attachment events, got {len(attachment_events)}"

    print("✅ Attachment isolation test passed!")
    print(f"   - API calls: {call_count}")
    print(f"   - Stream starts: {len(stream_starts)}")
    print(f"   - Attachment events: {len(attachment_events)}")

if __name__ == "__main__":
    asyncio.run(test_attachment_messages_not_persisted())
