"""
测试新的 query() 主循环架构

验证：
1. 独立的 query() 函数
2. while(true) 循环（不是递归）
3. QueryState 状态机
4. StreamingToolExecutor 集成
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch

from codo.query import query, QueryParams, QueryState, Terminal
from codo.services.compact import AutoCompactState

class MockTool:
    """Mock tool for testing"""
    def __init__(self, name: str, is_safe: bool = True):
        self.name = name
        self.is_concurrency_safe = is_safe
        self.call_count = 0

    def input_schema(self, **kwargs):
        return kwargs

    async def call(self, input_data, context):
        self.call_count += 1
        await asyncio.sleep(0.05)
        return Mock(data=f"{self.name} result", error=None)

@pytest.mark.asyncio
async def test_query_basic_flow():
    """测试基础对话流程（无工具调用）"""

    # Mock client
    mock_client = AsyncMock()

    # Mock stream context manager
    mock_stream = AsyncMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=None)

    # Mock API response (no tool calls)
    async def mock_stream_events():
        # content_block_start (text)
        yield Mock(
            type="content_block_start",
            content_block=Mock(type="text", text="")
        )
        # content_block_delta (text)
        yield Mock(
            type="content_block_delta",
            delta=Mock(type="text_delta", text="Hello")
        )
        # content_block_stop
        yield Mock(type="content_block_stop")

    mock_stream.__aiter__ = mock_stream_events
    mock_stream.get_final_message = AsyncMock(return_value=Mock(
        content=[Mock(type="text", text="Hello")]
    ))

    # 🔥 修复：正确设置 stream 返回值
    mock_client.messages.stream = Mock(return_value=mock_stream)

    # Create params
    params = QueryParams(
        client=mock_client,
        model="claude-opus-4",
        system_prompt="You are a helpful assistant",
        messages=[{"role": "user", "content": "Hi"}],
        tools=[],
        tool_schemas=[],
        execution_context={},
        cwd="/tmp",
        session_id="test-session",
        max_turns=None,
        enable_persistence=False,
        session_storage=None,
        memory_extraction_state=None,
        verbose=False,
    )

    # Run query
    events = []
    terminal = None
    async for event in query(params):
        if isinstance(event, Terminal):
            terminal = event
            break
        events.append(event)

    # Verify
    assert terminal is not None
    assert terminal.reason == "completed"
    assert len(events) > 0

    # Should have stream_request_start, text_delta, message_stop
    event_types = [e["type"] for e in events]
    assert "stream_request_start" in event_types
    assert "text_delta" in event_types
    assert "message_stop" in event_types

@pytest.mark.asyncio
async def test_query_with_tool_calls():
    """测试工具调用流程"""

    # Mock client
    mock_client = AsyncMock()
    mock_stream = AsyncMock()

    # Mock API response (with tool call)
    mock_stream.__aenter__.return_value = mock_stream
    mock_stream.__aexit__.return_value = None

    # First turn: tool call
    async def mock_stream_events_turn1():
        yield Mock(
            type="content_block_start",
            content_block=Mock(
                type="tool_use",
                id="tool1",
                name="Read",
                input={}
            )
        )
        yield Mock(type="content_block_stop")

    # Second turn: final response
    async def mock_stream_events_turn2():
        yield Mock(
            type="content_block_start",
            content_block=Mock(type="text", text="")
        )
        yield Mock(
            type="content_block_delta",
            delta=Mock(type="text_delta", text="Done")
        )
        yield Mock(type="content_block_stop")

    turn_count = [0]

    async def mock_stream_factory():
        turn_count[0] += 1
        if turn_count[0] == 1:
            mock_stream.__aiter__ = mock_stream_events_turn1
            mock_stream.get_final_message = AsyncMock(return_value=Mock(
                content=[Mock(
                    type="tool_use",
                    id="tool1",
                    name="Read",
                    input={"file_path": "test.txt"}
                )]
            ))
        else:
            mock_stream.__aiter__ = mock_stream_events_turn2
            mock_stream.get_final_message = AsyncMock(return_value=Mock(
                content=[Mock(type="text", text="Done")]
            ))
        return mock_stream

    mock_client.messages.stream.side_effect = mock_stream_factory

    # Create mock tool
    read_tool = MockTool("Read", is_safe=True)

    # Create params
    params = QueryParams(
        client=mock_client,
        model="claude-opus-4",
        system_prompt="You are a helpful assistant",
        messages=[{"role": "user", "content": "Read test.txt"}],
        tools=[read_tool],
        tool_schemas=[{
            "name": "Read",
            "description": "Read a file",
            "input_schema": {"type": "object", "properties": {}}
        }],
        execution_context={"cwd": "/tmp"},
        cwd="/tmp",
        session_id="test-session",
        max_turns=None,
        enable_persistence=False,
        session_storage=None,
        memory_extraction_state=None,
        verbose=False,
    )

    # Run query
    events = []
    terminal = None
    async for event in query(params):
        if isinstance(event, Terminal):
            terminal = event
            break
        events.append(event)

    # Verify
    assert terminal is not None
    assert terminal.reason == "completed"

    # Should have tool_result event
    event_types = [e["type"] for e in events]
    assert "tool_result" in event_types

    # Tool should have been called
    assert read_tool.call_count == 1

@pytest.mark.asyncio
async def test_query_max_turns():
    """测试 maxTurns 限制"""

    # Mock client that always returns tool calls
    mock_client = AsyncMock()
    mock_stream = AsyncMock()

    mock_stream.__aenter__.return_value = mock_stream
    mock_stream.__aexit__.return_value = None

    async def mock_stream_events():
        yield Mock(
            type="content_block_start",
            content_block=Mock(
                type="tool_use",
                id="tool1",
                name="Read",
                input={}
            )
        )
        yield Mock(type="content_block_stop")

    mock_stream.__aiter__ = mock_stream_events
    mock_stream.get_final_message = AsyncMock(return_value=Mock(
        content=[Mock(
            type="tool_use",
            id="tool1",
            name="Read",
            input={"file_path": "test.txt"}
        )]
    ))

    mock_client.messages.stream.return_value = mock_stream

    # Create mock tool
    read_tool = MockTool("Read", is_safe=True)

    # Create params with maxTurns=2
    params = QueryParams(
        client=mock_client,
        model="claude-opus-4",
        system_prompt="You are a helpful assistant",
        messages=[{"role": "user", "content": "Read test.txt"}],
        tools=[read_tool],
        tool_schemas=[{"name": "Read", "input_schema": {}}],
        execution_context={"cwd": "/tmp"},
        cwd="/tmp",
        session_id="test-session",
        max_turns=2,  # Limit to 2 turns
        enable_persistence=False,
        session_storage=None,
        memory_extraction_state=None,
        verbose=False,
    )

    # Run query
    events = []
    terminal = None
    async for event in query(params):
        if isinstance(event, Terminal):
            terminal = event
            break
        events.append(event)

    # Verify
    assert terminal is not None
    assert terminal.reason == "max_turns"

    # Should have error event
    error_events = [e for e in events if e.get("type") == "error"]
    assert len(error_events) > 0
    assert "maximum number of turns" in error_events[0]["error"]

@pytest.mark.asyncio
async def test_query_state_machine():
    """测试 QueryState 状态机"""

    # Create initial state
    state = QueryState(
        messages=[{"role": "user", "content": "Hi"}],
        turn_count=1,
        auto_compact_tracking=AutoCompactState(),
        transition=None,
    )

    # Verify initial state
    assert state.turn_count == 1
    assert state.transition is None
    assert len(state.messages) == 1

    # Simulate state update (like in query_loop continue)
    new_state = QueryState(
        messages=state.messages + [{"role": "assistant", "content": "Hello"}],
        turn_count=state.turn_count + 1,
        auto_compact_tracking=state.auto_compact_tracking,
        transition={"reason": "tool_use", "tool_count": 2},
    )

    # Verify updated state
    assert new_state.turn_count == 2
    assert new_state.transition["reason"] == "tool_use"
    assert len(new_state.messages) == 2

def test_query_state_immutability():
    """测试 QueryState 的不可变性（每次 continue 创建新对象）"""

    state1 = QueryState(
        messages=[{"role": "user", "content": "Hi"}],
        turn_count=1,
    )

    # Create new state (simulating continue)
    state2 = QueryState(
        messages=state1.messages + [{"role": "assistant", "content": "Hello"}],
        turn_count=state1.turn_count + 1,
    )

    # Verify state1 is unchanged
    assert state1.turn_count == 1
    assert len(state1.messages) == 1

    # Verify state2 is new
    assert state2.turn_count == 2
    assert len(state2.messages) == 2

    # Verify they are different objects
    assert state1 is not state2

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
