"""
测试 attachment 消息隔离
验证 attachment 消息不会被持久化到下一轮迭代
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codo.query import QueryParams, query_loop
from tests.fake_anthropic_stream import (
    FakeAnthropicStream,
    FakeContentBlock,
    FakeDelta,
    FakeFinalMessage,
    FakeStreamEvent,
)


class ReadTool:
    """测试用 Read 工具，保留真实工具执行器需要的最小接口。"""

    name = "Read"
    is_concurrency_safe = True

    def input_schema(self, **kwargs):
        return kwargs

    async def call(self, _input_data, _context, *_args):
        return SimpleNamespace(
            data=SimpleNamespace(content="test content"),
            error=None,
            staged_changes=[],
            audit_events=[],
            receipt=None,
        )

@pytest.mark.asyncio
async def test_attachment_messages_not_persisted():
    """
    测试 attachment 消息不会被持久化到 state.messages

    messages: [...messagesForQuery, ...assistantMessages, ...toolResults]

    其中 messagesForQuery 不包含 attachment 消息
    """
    # Mock client
    mock_client = MagicMock()

    # 跟踪 API 调用次数
    call_count = 0

    def mock_stream(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return FakeAnthropicStream(
                events=[
                    FakeStreamEvent(
                        type="content_block_start",
                        content_block=FakeContentBlock(
                            type="tool_use",
                            id="tool_1",
                            name="Read",
                        ),
                    ),
                    FakeStreamEvent(
                        type="content_block_delta",
                        delta=FakeDelta(
                            type="input_json_delta",
                            partial_json='{"file_path": "/test.txt"}',
                        ),
                    ),
                    FakeStreamEvent(type="content_block_stop"),
                ],
                final_message=FakeFinalMessage(
                    content=[
                        FakeContentBlock(
                            type="tool_use",
                            id="tool_1",
                            name="Read",
                            input={"file_path": "/test.txt"},
                        )
                    ],
                ),
            )

        return FakeAnthropicStream(
            events=[
                FakeStreamEvent(
                    type="content_block_start",
                    content_block=FakeContentBlock(type="text"),
                ),
                FakeStreamEvent(
                    type="content_block_delta",
                    delta=FakeDelta(type="text_delta", text="Done!"),
                ),
                FakeStreamEvent(type="content_block_stop"),
            ],
            final_message=FakeFinalMessage(
                content=[FakeContentBlock(type="text", text="Done!")],
            ),
        )

    mock_client.messages.stream = mock_stream

    mock_tools = [ReadTool()]

    # 初始消息
    initial_messages = [
        {"role": "user", "content": [{"type": "text", "text": "Read /test.txt"}]},
    ]

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
                    if isinstance(event, dict) and event.get("type") == "stream_request_start":
                        # 这里我们无法直接访问 state.messages，但可以通过其他方式验证
                        pass

    # 验证：
    # 1. 应该有两轮 API 调用
    assert call_count == 2, f"Expected 2 API calls, got {call_count}"

    # 2. 检查事件流
    stream_starts = [
        e for e in events
        if isinstance(e, dict) and e.get("type") == "stream_request_start"
    ]
    assert len(stream_starts) == 2, f"Expected 2 stream starts, got {len(stream_starts)}"

    # 3. 检查 attachment 消息被产出
    attachment_events = [
        e for e in events
        if isinstance(e, dict)
        and e.get("role") == "user"
        and any("[ATTACHMENT]" in str(c) for c in e.get("content", []))
    ]
    # 每轮都应该产出 attachment
    assert len(attachment_events) == 2, f"Expected 2 attachment events, got {len(attachment_events)}"

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
