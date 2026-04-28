import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from codo.cli.tui.interaction_types import InteractionOption, InteractionQuestion, InteractionRequest
from codo.query import Terminal
from codo.query_engine import QueryEngine

class DummyClient:
    pass

@pytest.mark.asyncio
async def test_query_engine_surfaces_interaction_events_before_waiting(monkeypatch):
    async def fake_query(params):
        yield {"type": "stream_request_start"}
        broker = params.execution_context["interaction_broker"]
        answer = await broker.request(
            InteractionRequest(
                request_id="req-1",
                kind="question",
                label="Need answer",
                questions=[
                    InteractionQuestion(
                        question_id="q-1",
                        header="Mode",
                        question="Which mode?",
                        options=[
                            InteractionOption(value="safe", label="Safe"),
                            InteractionOption(value="fast", label="Fast"),
                        ],
                    )
                ],
            )
        )
        yield {
            "type": "tool_result",
            "tool_use_id": "tool-1",
            "content": str(answer),
            "is_error": False,
            "status": "completed",
        }
        yield Terminal(reason="completed", metadata={"messages": [], "turn_count": 1})

    monkeypatch.setattr("codo.query_engine.query", fake_query)

    engine = QueryEngine(
        client=DummyClient(),
        cwd=".",
        model="claude-test",
        enable_persistence=False,
    )
    engine.refresh_mcp_tools = lambda: asyncio.sleep(0)
    engine.prompt_builder.build_system_prompt = lambda language_preference="zh-CN": [{"text": "system"}]

    stream = engine.submit_message_stream("hello")

    first = await stream.__anext__()
    assert first["type"] == "stream_request_start"

    second = await stream.__anext__()
    assert second["type"] == "interaction_requested"
    assert second["request"]["request_id"] == "req-1"
    assert second["request"]["kind"] == "question"

    engine.resolve_interaction("req-1", {"Which mode?": "safe"})

    third = await stream.__anext__()
    assert third["type"] == "interaction_resolved"

    fourth = await stream.__anext__()
    assert fourth["type"] == "tool_result"
    assert "safe" in fourth["content"]

    terminal = await stream.__anext__()
    assert isinstance(terminal, Terminal)
    assert terminal.reason == "completed"

@pytest.mark.asyncio
async def test_query_engine_interrupt_cancels_active_runtime(monkeypatch):
    async def fake_query(params):
        yield {"type": "stream_request_start"}
        await asyncio.sleep(60)

    monkeypatch.setattr("codo.query_engine.query", fake_query)

    engine = QueryEngine(
        client=DummyClient(),
        cwd=".",
        model="claude-test",
        enable_persistence=False,
    )
    engine.refresh_mcp_tools = lambda: asyncio.sleep(0)
    engine.prompt_builder.build_system_prompt = lambda language_preference="zh-CN": [{"text": "system"}]

    stream = engine.submit_message_stream("hello")
    first = await stream.__anext__()
    assert first["type"] == "stream_request_start"

    engine.interrupt()

    second = await stream.__anext__()
    assert second["type"] == "error"
    assert second["error_type"] == "user_interrupted"

@pytest.mark.asyncio
async def test_query_engine_emits_runtime_phase_events_for_real_query(monkeypatch):
    mock_client = AsyncMock()
    mock_stream = AsyncMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=None)

    async def mock_stream_events():
        yield Mock(type="content_block_start", content_block=Mock(type="text", text=""))
        yield Mock(type="content_block_delta", delta=Mock(type="text_delta", text="Hello"))
        yield Mock(type="content_block_stop")

    mock_stream.__aiter__ = mock_stream_events
    mock_stream.get_final_message = AsyncMock(
        return_value=Mock(content=[Mock(type="text", text="Hello")], stop_reason=None)
    )
    mock_client.messages.stream = Mock(return_value=mock_stream)

    async def always_continue(*_args, **_kwargs):
        return True

    monkeypatch.setattr("codo.services.tools.stop_hooks.handle_stop_hooks", always_continue)

    engine = QueryEngine(
        client=mock_client,
        cwd=".",
        model="claude-test",
        enable_persistence=False,
    )
    engine.refresh_mcp_tools = lambda: asyncio.sleep(0)
    engine.prompt_builder.build_system_prompt = lambda language_preference="zh-CN": [{"text": "system"}]
    engine.builtin_tools = []
    engine.mcp_tools = []
    engine.tools = []
    engine.tool_schemas = []
    engine.execution_context["options"]["tools"] = []
    engine.memory_extraction_state = None

    events = []
    async for event in engine.submit_message_stream("hello"):
        events.append(event)
        if isinstance(event, Terminal):
            break

    event_types = [event["type"] for event in events if isinstance(event, dict)]
    assert "turn_started" in event_types
    assert "status_changed" in event_types

    phases = [
        event["phase"]
        for event in events
        if isinstance(event, dict) and event.get("type") == "status_changed"
    ]
    assert "prepare_turn" in phases
    assert "stream_assistant" in phases
    assert "stop_hooks" in phases
    assert "complete" in phases

@pytest.mark.asyncio
async def test_query_engine_send_control_can_resolve_interaction_via_runtime_command(monkeypatch):
    async def fake_query(params):
        yield {"type": "stream_request_start"}
        broker = params.execution_context["interaction_broker"]
        answer = await broker.request(
            InteractionRequest(
                request_id="req-cmd-1",
                kind="question",
                label="Need answer",
                questions=[
                    InteractionQuestion(
                        question_id="q-1",
                        header="Mode",
                        question="Which mode?",
                        options=[
                            InteractionOption(value="safe", label="Safe"),
                            InteractionOption(value="fast", label="Fast"),
                        ],
                    )
                ],
            )
        )
        yield {
            "type": "tool_result",
            "tool_use_id": "tool-1",
            "content": str(answer),
            "is_error": False,
            "status": "completed",
        }
        yield Terminal(reason="completed", metadata={"messages": [], "turn_count": 1})

    monkeypatch.setattr("codo.query_engine.query", fake_query)

    engine = QueryEngine(
        client=DummyClient(),
        cwd=".",
        model="claude-test",
        enable_persistence=False,
    )
    engine.refresh_mcp_tools = lambda: asyncio.sleep(0)
    engine.prompt_builder.build_system_prompt = lambda language_preference="zh-CN": [{"text": "system"}]

    stream = engine.submit_message_stream("hello")
    assert (await stream.__anext__())["type"] == "stream_request_start"
    interaction = await stream.__anext__()
    assert interaction["type"] == "interaction_requested"

    engine.send_control(
        {
            "type": "resolve_interaction",
            "request_id": "req-cmd-1",
            "data": {"Which mode?": "safe"},
        }
    )

    resolved = await stream.__anext__()
    assert resolved["type"] == "interaction_resolved"
    tool_result = await stream.__anext__()
    assert tool_result["type"] == "tool_result"
    assert "safe" in tool_result["content"]

@pytest.mark.asyncio
async def test_query_engine_persists_last_prompt_for_retry_restore(monkeypatch, tmp_path):
    async def fake_query(_params):
        yield {"type": "stream_request_start"}
        yield Terminal(reason="completed", metadata={"messages": [], "turn_count": 1})

    monkeypatch.setattr("codo.query_engine.query", fake_query)

    engine = QueryEngine(
        client=DummyClient(),
        cwd=str(tmp_path),
        model="claude-test",
        enable_persistence=True,
    )
    engine.refresh_mcp_tools = lambda: asyncio.sleep(0)
    engine.prompt_builder.build_system_prompt = lambda language_preference="zh-CN": [{"text": "system"}]

    events = []
    async for event in engine.submit_message_stream("persist this prompt"):
        events.append(event)
        if isinstance(event, Terminal):
            break

    assert any(isinstance(event, dict) and event.get("type") == "stream_request_start" for event in events)
    assert engine.session_storage is not None
    assert engine.session_storage.current_last_prompt == "persist this prompt"

@pytest.mark.asyncio
async def test_query_engine_send_control_supports_sidebar_runtime_command_only(monkeypatch):
    async def fake_query(params):
        yield {"type": "stream_request_start"}
        await asyncio.sleep(0.05)
        yield Terminal(reason="completed", metadata={"messages": [], "turn_count": 1})

    monkeypatch.setattr("codo.query_engine.query", fake_query)

    engine = QueryEngine(
        client=DummyClient(),
        cwd=".",
        model="claude-test",
        enable_persistence=False,
    )
    engine.refresh_mcp_tools = lambda: asyncio.sleep(0)
    engine.prompt_builder.build_system_prompt = lambda language_preference="zh-CN": [{"text": "system"}]

    stream = engine.submit_message_stream("hello")
    first = await stream.__anext__()
    assert first["type"] == "stream_request_start"

    engine.send_control(
        {
            "type": "switch_sidebar_focus",
            "sidebar_mode": "agent:agent-7",
            "auto_follow": False,
        }
    )
    engine.send_control(
        {
            "type": "open_audit_view",
            "visible": True,
            "agent_id": "agent-7",
            "follow_focus": True,
        }
    )

    events = []
    async for item in stream:
        events.append(item)
        if isinstance(item, Terminal):
            break

    event_types = [item["type"] for item in events if isinstance(item, dict)]
    assert "sidebar_focus_changed" in event_types
    assert "audit_view_changed" not in event_types

@pytest.mark.asyncio
async def test_query_engine_retry_checkpoint_restores_messages_from_archived_checkpoint(monkeypatch):
    mock_client = AsyncMock()
    mock_stream = AsyncMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=None)

    async def mock_stream_events():
        yield Mock(type="content_block_start", content_block=Mock(type="text", text=""))
        yield Mock(type="content_block_delta", delta=Mock(type="text_delta", text="Hello"))
        yield Mock(type="content_block_stop")

    mock_stream.__aiter__ = mock_stream_events
    mock_stream.get_final_message = AsyncMock(
        return_value=Mock(content=[Mock(type="text", text="Hello")], stop_reason=None)
    )
    mock_client.messages.stream = Mock(return_value=mock_stream)

    async def always_continue(*_args, **_kwargs):
        return True

    monkeypatch.setattr("codo.services.tools.stop_hooks.handle_stop_hooks", always_continue)

    engine = QueryEngine(
        client=mock_client,
        cwd=".",
        model="claude-test",
        enable_persistence=False,
    )
    engine.refresh_mcp_tools = lambda: asyncio.sleep(0)
    engine.prompt_builder.build_system_prompt = lambda language_preference="zh-CN": [{"text": "system"}]
    engine.builtin_tools = []
    engine.mcp_tools = []
    engine.tools = []
    engine.tool_schemas = []
    engine.execution_context["options"]["tools"] = []
    engine.memory_extraction_state = None

    checkpoint_id = None
    async for event in engine.submit_message_stream("hello"):
        if isinstance(event, dict) and event.get("type") == "status_changed" and event.get("phase") == "stream_assistant":
            checkpoint_id = event.get("checkpoint_id")
        if isinstance(event, Terminal):
            break

    assert checkpoint_id is not None
    engine.messages = [{"role": "user", "content": "mutated", "uuid": "mutated"}]

    checkpoint = engine.retry_checkpoint(str(checkpoint_id))

    assert checkpoint is not None
    assert checkpoint.checkpoint_id == checkpoint_id
    assert engine.messages[0]["content"] == "hello"
