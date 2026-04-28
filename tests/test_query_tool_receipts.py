import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from codo.query import QueryParams, Terminal, query

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
    mock_stream = AsyncMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=None)

    async def mock_stream_events_turn1():
        yield Mock(
            type="content_block_start",
            content_block=Mock(type="tool_use", id="tool1", name="Read", input={}),
        )
        yield Mock(type="content_block_stop")

    async def mock_stream_events_turn2():
        yield Mock(type="content_block_start", content_block=Mock(type="text", text=""))
        yield Mock(type="content_block_delta", delta=Mock(type="text_delta", text="Done"))
        yield Mock(type="content_block_stop")

    turn_count = [0]

    async def mock_stream_factory():
        turn_count[0] += 1
        if turn_count[0] == 1:
            mock_stream.__aiter__ = mock_stream_events_turn1
            mock_stream.get_final_message = AsyncMock(
                return_value=Mock(
                    content=[Mock(type="tool_use", id="tool1", name="Read", input={"file_path": "test.txt"})]
                )
            )
        else:
            mock_stream.__aiter__ = mock_stream_events_turn2
            mock_stream.get_final_message = AsyncMock(return_value=Mock(content=[Mock(type="text", text="Done")]))
        return mock_stream

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
