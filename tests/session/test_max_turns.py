"""
测试 maxTurns 限制功能

"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from codo.query_engine import QueryEngine
from codo.services.mcp import MCPClientManager

@pytest.fixture
def query_engine():
    """创建测试用的 QueryEngine 实例"""
    engine = QueryEngine(
        api_key="test-key",
        cwd="/tmp/test",
        model="claude-3-5-sonnet-20241022",
        max_turns=3,  # 设置最大 turn 数为 3
        verbose=False,
        enable_persistence=False,
    )

    return engine

@pytest.mark.asyncio
async def test_max_turns_basic(query_engine):
    """
    测试基本的 maxTurns 限制

    场景：
    1. 设置 maxTurns=3
    2. 模拟 3 次工具调用
    3. 第 4 次应该被阻止并返回错误
    """
    # Mock API 响应 - 每次都返回工具调用
    mock_stream = AsyncMock()
    mock_stream.__aiter__.return_value = [
        MagicMock(type="content_block_start", index=0, content_block=MagicMock(type="tool_use", id="tool_1", name="Read", input={"file_path": "/test"})),
        MagicMock(type="content_block_stop", index=0),
    ]
    mock_stream.get_final_message = AsyncMock(return_value=MagicMock(
        content=[
            MagicMock(type="tool_use", id="tool_1", name="Read", input={"file_path": "/test"})
        ]
    ))

    with patch.object(query_engine.client.messages, 'stream') as mock_messages_stream:
        mock_messages_stream.return_value.__aenter__.return_value = mock_stream

        # Mock 工具执行
        with patch('codo.query_engine.run_tools_batch') as mock_run_tools:
            mock_run_tools.return_value = MagicMock(
                context_modifiers=[],
                batches=[
                    MagicMock(tasks=[
                        MagicMock(
                            tool_use_id="tool_1",
                            error=None,
                            result=MagicMock(data="test result"),
                            status=MagicMock(value="completed"),
                            duration=0.1,
                        )
                    ])
                ]
            )

            events = []
            async for event in query_engine.submit_message_stream("test prompt"):
                events.append(event)

                # 如果收到错误事件，检查是否是 max_turns_reached
                if event.get("type") == "error":
                    assert event.get("error_type") == "max_turns_reached"
                    assert event.get("max_turns") == 3
                    assert "Reached maximum number of turns" in event.get("error", "")
                    break

    # 验证收到了 max_turns_reached 错误
    error_events = [e for e in events if e.get("type") == "error"]
    assert len(error_events) > 0, "应该收到 max_turns_reached 错误"

@pytest.mark.asyncio
async def test_max_turns_none_allows_unlimited(query_engine):
    """
    测试 maxTurns=None 时允许无限次工具调用

    场景：
    1. 设置 maxTurns=None（默认）
    2. 模拟多次工具调用
    3. 不应该被限制
    """
    # 重新创建没有 maxTurns 限制的 engine
    engine = QueryEngine(
        api_key="test-key",
        cwd="/tmp/test",
        model="claude-3-5-sonnet-20241022",
        max_turns=None,  # 无限制
        verbose=False,
        enable_persistence=False,
    )

    # 模拟 5 次工具调用后停止
    call_count = 0
    max_calls = 5

    def create_mock_stream():
        nonlocal call_count
        call_count += 1

        # 前 5 次返回工具调用，第 6 次返回纯文本
        if call_count <= max_calls:
            mock_stream = AsyncMock()
            mock_stream.__aiter__.return_value = [
                MagicMock(type="content_block_start", index=0, content_block=MagicMock(type="tool_use", id=f"tool_{call_count}", name="Read", input={"file_path": "/test"})),
                MagicMock(type="content_block_stop", index=0),
            ]
            mock_stream.get_final_message = AsyncMock(return_value=MagicMock(
                content=[
                    MagicMock(type="tool_use", id=f"tool_{call_count}", name="Read", input={"file_path": "/test"})
                ]
            ))
        else:
            # 最后返回纯文本响应
            mock_stream = AsyncMock()
            mock_stream.__aiter__.return_value = [
                MagicMock(type="content_block_start", index=0, content_block=MagicMock(type="text", text="Done")),
                MagicMock(type="content_block_delta", delta=MagicMock(type="text_delta", text="Done")),
                MagicMock(type="content_block_stop", index=0),
            ]
            mock_stream.get_final_message = AsyncMock(return_value=MagicMock(
                content=[
                    MagicMock(type="text", text="Done")
                ]
            ))

        return mock_stream

    with patch.object(engine.client.messages, 'stream') as mock_messages_stream:
        mock_messages_stream.return_value.__aenter__.side_effect = lambda: create_mock_stream()

        # Mock 工具执行
        with patch('codo.query_engine.run_tools_batch') as mock_run_tools:
            mock_run_tools.return_value = MagicMock(
                context_modifiers=[],
                batches=[
                    MagicMock(tasks=[
                        MagicMock(
                            tool_use_id="tool_1",
                            error=None,
                            result=MagicMock(data="test result"),
                            status=MagicMock(value="completed"),
                            duration=0.1,
                        )
                    ])
                ]
            )

            events = []
            async for event in engine.submit_message_stream("test prompt"):
                events.append(event)

    # 验证没有收到 max_turns_reached 错误
    error_events = [e for e in events if e.get("type") == "error" and e.get("error_type") == "max_turns_reached"]
    assert len(error_events) == 0, "maxTurns=None 时不应该有限制"

    # 验证至少执行了 5 次工具调用
    tool_result_events = [e for e in events if e.get("type") == "tool_result"]
    assert len(tool_result_events) >= max_calls, f"应该至少执行 {max_calls} 次工具调用"

@pytest.mark.asyncio
async def test_turn_count_starts_at_one():
    """
    测试 turnCount 从 1 开始（不是 0）

    """
    engine = QueryEngine(
        api_key="test-key",
        cwd="/tmp/test",
        model="claude-3-5-sonnet-20241022",
        max_turns=2,  # 设置为 2，这样第一次工具调用后就会触发限制
        verbose=False,
        enable_persistence=False,
    )

    # 验证初始值
    assert engine.turn_count == 1, "turnCount 应该从 1 开始"

    # Mock API 响应 - 返回工具调用
    mock_stream = AsyncMock()
    mock_stream.__aiter__.return_value = [
        MagicMock(type="content_block_start", index=0, content_block=MagicMock(type="tool_use", id="tool_1", name="Read", input={"file_path": "/test"})),
        MagicMock(type="content_block_stop", index=0),
    ]
    mock_stream.get_final_message = AsyncMock(return_value=MagicMock(
        content=[
            MagicMock(type="tool_use", id="tool_1", name="Read", input={"file_path": "/test"})
        ]
    ))

    with patch.object(engine.client.messages, 'stream') as mock_messages_stream:
        mock_messages_stream.return_value.__aenter__.return_value = mock_stream

        # Mock 工具执行
        with patch('codo.query_engine.run_tools_batch') as mock_run_tools:
            mock_run_tools.return_value = MagicMock(
                context_modifiers=[],
                batches=[
                    MagicMock(tasks=[
                        MagicMock(
                            tool_use_id="tool_1",
                            error=None,
                            result=MagicMock(data="test result"),
                            status=MagicMock(value="completed"),
                            duration=0.1,
                        )
                    ])
                ]
            )

            events = []
            async for event in engine.submit_message_stream("test prompt"):
                events.append(event)
                if event.get("type") == "error":
                    break

    # 验证 turnCount 递增到 2
    assert engine.turn_count == 2, "执行一次工具后 turnCount 应该是 2"

    # 验证收到了 max_turns_reached 错误（因为 nextTurnCount=3 > maxTurns=2）
    error_events = [e for e in events if e.get("type") == "error" and e.get("error_type") == "max_turns_reached"]
    assert len(error_events) > 0, "应该收到 max_turns_reached 错误"

@pytest.mark.asyncio
async def test_max_turns_check_uses_greater_than():
    """
    测试 maxTurns 检查使用 > 而不是 >=

    """
    engine = QueryEngine(
        api_key="test-key",
        cwd="/tmp/test",
        model="claude-3-5-sonnet-20241022",
        max_turns=3,
        verbose=False,
        enable_persistence=False,
    )

    # 模拟恰好 3 次工具调用
    call_count = 0

    def create_mock_stream():
        nonlocal call_count
        call_count += 1

        # 前 3 次返回工具调用，第 4 次应该被阻止
        if call_count <= 3:
            mock_stream = AsyncMock()
            mock_stream.__aiter__.return_value = [
                MagicMock(type="content_block_start", index=0, content_block=MagicMock(type="tool_use", id=f"tool_{call_count}", name="Read", input={"file_path": "/test"})),
                MagicMock(type="content_block_stop", index=0),
            ]
            mock_stream.get_final_message = AsyncMock(return_value=MagicMock(
                content=[
                    MagicMock(type="tool_use", id=f"tool_{call_count}", name="Read", input={"file_path": "/test"})
                ]
            ))
        else:
            # 不应该到达这里
            raise AssertionError("不应该有第 4 次 API 调用")

        return mock_stream

    with patch.object(engine.client.messages, 'stream') as mock_messages_stream:
        mock_messages_stream.return_value.__aenter__.side_effect = lambda: create_mock_stream()

        # Mock 工具执行
        with patch('codo.query_engine.run_tools_batch') as mock_run_tools:
            mock_run_tools.return_value = MagicMock(
                context_modifiers=[],
                batches=[
                    MagicMock(tasks=[
                        MagicMock(
                            tool_use_id="tool_1",
                            error=None,
                            result=MagicMock(data="test result"),
                            status=MagicMock(value="completed"),
                            duration=0.1,
                        )
                    ])
                ]
            )

            events = []
            async for event in engine.submit_message_stream("test prompt"):
                events.append(event)

    # 验证恰好执行了 3 次工具调用
    tool_result_events = [e for e in events if e.get("type") == "tool_result"]
    assert len(tool_result_events) == 3, "应该恰好执行 3 次工具调用"

    # 验证收到了 max_turns_reached 错误
    error_events = [e for e in events if e.get("type") == "error" and e.get("error_type") == "max_turns_reached"]
    assert len(error_events) > 0, "第 3 次工具调用后应该收到 max_turns_reached 错误"

    # 验证最终 turnCount 是 3（1 初始 + 2 次递增，第3次检查时终止）
    assert engine.turn_count == 3, "最终 turnCount 应该是 3"

    # 验证错误事件中的 turn_count 是 4（next_turn_count）
    assert error_events[0].get("turn_count") == 4, "错误事件中的 turn_count 应该是 4"

@pytest.mark.asyncio
async def test_max_turns_error_message_format():
    """
    测试 maxTurns 错误消息的格式

    - 错误类型：max_turns_reached
    - 错误消息："Reached maximum number of turns (N)"
    - 附件包含 maxTurns 和 turnCount
    """
    engine = QueryEngine(
        api_key="test-key",
        cwd="/tmp/test",
        model="claude-3-5-sonnet-20241022",
        max_turns=1,  # 设置为 1，第一次工具调用后就触发
        verbose=False,
        enable_persistence=False,
    )

    # Mock API 响应
    mock_stream = AsyncMock()
    mock_stream.__aiter__.return_value = [
        MagicMock(type="content_block_start", index=0, content_block=MagicMock(type="tool_use", id="tool_1", name="Read", input={"file_path": "/test"})),
        MagicMock(type="content_block_stop", index=0),
    ]
    mock_stream.get_final_message = AsyncMock(return_value=MagicMock(
        content=[
            MagicMock(type="tool_use", id="tool_1", name="Read", input={"file_path": "/test"})
        ]
    ))

    with patch.object(engine.client.messages, 'stream') as mock_messages_stream:
        mock_messages_stream.return_value.__aenter__.return_value = mock_stream

        # Mock 工具执行
        with patch('codo.query_engine.run_tools_batch') as mock_run_tools:
            mock_run_tools.return_value = MagicMock(
                context_modifiers=[],
                batches=[
                    MagicMock(tasks=[
                        MagicMock(
                            tool_use_id="tool_1",
                            error=None,
                            result=MagicMock(data="test result"),
                            status=MagicMock(value="completed"),
                            duration=0.1,
                        )
                    ])
                ]
            )

            events = []
            async for event in engine.submit_message_stream("test prompt"):
                events.append(event)

    # 查找错误事件
    error_events = [e for e in events if e.get("type") == "error"]
    assert len(error_events) > 0, "应该有错误事件"

    error_event = error_events[0]

    # 验证错误类型
    assert error_event.get("error_type") == "max_turns_reached", "错误类型应该是 max_turns_reached"

    # 验证错误消息格式
    assert error_event.get("error") == "Reached maximum number of turns (1)", "错误消息格式不正确"

    # 验证附加信息
    assert error_event.get("max_turns") == 1, "应该包含 max_turns"
    assert error_event.get("turn_count") == 2, "应该包含 turn_count（初始1 + 递增1）"

    # 验证消息历史中也添加了错误消息
    last_message = engine.messages[-1]
    assert last_message["role"] == "user", "最后一条消息应该是 user 角色"
    assert "attachments" in last_message, "应该包含 attachments"
    assert last_message["attachments"][0]["type"] == "max_turns_reached", "attachment 类型应该是 max_turns_reached"
    assert last_message["attachments"][0]["maxTurns"] == 1, "attachment 应该包含 maxTurns"
    assert last_message["attachments"][0]["turnCount"] == 2, "attachment 应该包含 turnCount"
