from unittest.mock import AsyncMock

import pytest

from codo.query import QueryParams, Terminal, query
from tests.fake_anthropic_stream import (
    FakeAnthropicStream,
    FakeContentBlock,
    FakeDelta,
    FakeFinalMessage,
    FakeStreamEvent,
)


class ReceiptTool:
    def __init__(self):
        self.name = "Read"
        self.call_count = 0

    @staticmethod
    def is_concurrency_safe(_input_data):
        return True

    class input_schema:
        def __init__(self, **kwargs):
            self.file_path = kwargs["file_path"]

    async def call(self, input_data, context, *args):
        self.call_count += 1
        from codo.tools.receipts import GenericReceipt
        from codo.tools.types import ToolResult

        return ToolResult(
            data="done",
            receipt=GenericReceipt(
                kind="generic",
                summary=f"Read {input_data.file_path}",
                body="file loaded",
            ),
        )

@pytest.mark.asyncio
async def test_query_emits_receipt_dict():
    mock_client = AsyncMock()
    turn_count = [0]

    async def mock_stream_factory(**_kwargs):
        turn_count[0] += 1
        if turn_count[0] == 1:
            return FakeAnthropicStream(
                events=[
                    FakeStreamEvent(
                        type="content_block_start",
                        content_block=FakeContentBlock(
                            type="tool_use",
                            id="tool1",
                            name="Read",
                        ),
                    ),
                    FakeStreamEvent(type="content_block_stop"),
                ],
                final_message=FakeFinalMessage(
                    content=[
                        FakeContentBlock(
                            type="tool_use",
                            id="tool1",
                            name="Read",
                            input={"file_path": "test.txt"},
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
                    delta=FakeDelta(type="text_delta", text="Done"),
                ),
                FakeStreamEvent(type="content_block_stop"),
            ],
            final_message=FakeFinalMessage(
                content=[FakeContentBlock(type="text", text="Done")],
            ),
        )

    mock_client.messages.stream.side_effect = mock_stream_factory

    tool = ReceiptTool()
    params = QueryParams(
        client=mock_client,
        model="claude-opus-4",
        system_prompt="You are a helpful assistant",
        messages=[{"role": "user", "content": "Read test.txt"}],
        tools=[tool],
        tool_schemas=[{"name": "Read", "description": "Read a file", "input_schema": {"type": "object", "properties": {}}}],
        execution_context={"cwd": "/tmp"},
        cwd="/tmp",
        session_id="test-session",
        max_turns=None,
        enable_persistence=False,
        session_storage=None,
        memory_extraction_state=None,
        verbose=False,
    )

    events = []
    terminal = None
    async for event in query(params):
        if isinstance(event, Terminal):
            terminal = event
            break
        events.append(event)

    assert terminal is not None
    tool_result = next(event for event in events if event.get("type") == "tool_result")
    assert tool_result["receipt"]["kind"] == "generic"
    assert tool_result["content"] == "Read test.txt"
