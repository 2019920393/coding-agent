"""UIBridge 状态聚合测试。"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from codo.cli.tui.bridge import UIBridge
from codo.cli.tui.interaction_types import InteractionOption, InteractionRequest
from codo.services.tools.permission_checker import create_default_permission_context
from codo.session.storage import SessionStorage
from codo.team import TaskStatus, get_task_manager, get_team_manager
from codo.team.message_types import MessageType
from codo.types.permissions import PermissionMode, PermissionRuleSource

class DummyEngine:
    def __init__(self):
        self.session_id = "session-main"
        self.model = "claude-test"
        self.turn_count = 1
        self.messages = []
        self.execution_context = {
            "permission_context": create_default_permission_context("."),
            "options": {
                "app_state": {
                    "todos": {
                        "session-main": [
                            {
                                "content": "Inspect logs",
                                "status": "completed",
                                "activeForm": "Inspecting logs",
                            },
                            {
                                "content": "Run tests",
                                "status": "in_progress",
                                "activeForm": "Running tests",
                            },
                            {
                                "content": "Ship fix",
                                "status": "pending",
                                "activeForm": "Shipping fix",
                            },
                        ]
                    }
                }
            }
        }

    def get_context_stats(self):
        return {
            "token_count": 321,
            "context_window": 200000,
            "remaining_tokens": 199679,
            "model_visible_message_count": 4,
            "session_message_count": 4,
        }

    def reset_interrupt_state(self) -> None:
        return None

class RetryEngine(DummyEngine):
    def __init__(self):
        super().__init__()
        self.retry_calls: list[str] = []
        self.stream_calls: list[str] = []

    def retry_checkpoint(self, checkpoint_id: str):
        self.retry_calls.append(checkpoint_id)
        self.messages = [
            {"role": "user", "content": "Recovered prompt", "type": "user", "uuid": "user-restored"},
            {"role": "assistant", "content": "Recovered assistant", "type": "assistant", "uuid": "assistant-restored"},
        ]
        return SimpleNamespace(checkpoint_id=checkpoint_id)

    async def submit_message_stream(self, prompt: str):
        self.stream_calls.append(prompt)
        yield {"type": "stream_request_start"}
        yield {"type": "message_stop"}

class CountingEngine(DummyEngine):
    def __init__(self):
        super().__init__()
        self.context_stats_calls = 0

    def get_context_stats(self):
        self.context_stats_calls += 1
        return super().get_context_stats()

def test_bridge_tracks_streaming_message_thinking_and_tool_results():
    """bridge 应把 query 流事件转成当前 assistant 消息快照。"""
    engine = DummyEngine()
    bridge = UIBridge(engine=engine)

    bridge.begin_user_turn("Please fix it")
    bridge.apply_stream_event({"type": "stream_request_start"})
    bridge.apply_stream_event(
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": SimpleNamespace(type="thinking"),
        }
    )
    bridge.apply_stream_event(
        {
            "type": "thinking_delta",
            "index": 0,
            "delta": {"thinking": "Need to inspect the failure."},
        }
    )
    bridge.apply_stream_event(
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": SimpleNamespace(type="text"),
        }
    )
    bridge.apply_stream_event(
        {
            "type": "text_delta",
            "index": 1,
            "delta": {"text": "I found the issue."},
        }
    )
    bridge.apply_stream_event(
        {
            "type": "content_block_start",
            "index": 2,
            "content_block": SimpleNamespace(type="tool_use", id="tool-1", name="TodoWrite"),
        }
    )
    bridge.apply_stream_event(
        {
            "type": "input_json_delta",
            "index": 2,
            "delta": {"partial_json": '{"todos": ['},
        }
    )
    bridge.apply_stream_event(
        {
            "type": "tool_result",
            "tool_use_id": "tool-1",
            "content": "Todos updated",
            "is_error": False,
            "status": "completed",
        }
    )
    bridge.apply_stream_event({"type": "message_stop"})

    snapshot = bridge.get_snapshot()

    assert snapshot.is_generating is False
    assert len(snapshot.messages) == 2
    assistant = snapshot.messages[-1]
    assert assistant.role == "assistant"
    assert assistant.content == "I found the issue."
    assert assistant.thinking == "Need to inspect the failure."
    assert assistant.thinking_collapsed is True
    assert assistant.tool_calls[0].name == "TodoWrite"
    assert assistant.tool_calls[0].result == "Todos updated"
    assert snapshot.global_todos.active.content == "Run tests"
    assert snapshot.status.token_count == 321

@pytest.mark.asyncio
async def test_bridge_coalesces_high_frequency_stream_deltas():
    """高频 token 不应每个都触发一次整屏 notify。"""
    engine = DummyEngine()
    bridge = UIBridge(engine=engine)
    snapshots = []

    bridge.subscribe(lambda snapshot: snapshots.append(snapshot))
    bridge.begin_user_turn("Please stream")
    bridge.apply_stream_event({"type": "stream_request_start"})
    bridge.apply_stream_event(
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": SimpleNamespace(type="text"),
        }
    )
    snapshots.clear()

    for token in ("A", "B", "C", "D", "E", "F"):
        bridge.apply_stream_event(
            {
                "type": "text_delta",
                "index": 0,
                "delta": {"text": token},
            }
        )

    assert snapshots == []

    await asyncio.sleep(0.08)

    assert 1 <= len(snapshots) <= 2
    assert snapshots[-1].messages[-1].content == "ABCDEF"

def test_bridge_restores_structured_tool_results_into_assistant_message():
    """历史消息中的 tool_result 应恢复到 assistant 卡片，而不是变成独立 user 噪音。"""
    engine = DummyEngine()
    engine.messages = [
        {"role": "user", "content": "Show files"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "tool-1", "name": "Bash", "input": {"command": "ls -la"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tool-1",
                    "content": "Listed repository files",
                    "receipt": {
                        "kind": "command",
                        "summary": "Listed repository files",
                        "command": "ls -la",
                        "exit_code": 0,
                        "stdout": "a.txt\nb.txt",
                        "stderr": "",
                    },
                    "audit_events": [
                        {
                            "event_id": "evt-1",
                            "agent_id": "assistant",
                            "source": "tool",
                            "message": "Command completed",
                            "created_at": 0.0,
                            "metadata": {"cwd": "/tmp"},
                        }
                    ],
                    "is_error": False,
                }
            ],
        },
    ]

    bridge = UIBridge(engine=engine)
    snapshot = bridge.get_snapshot()

    assert len(snapshot.messages) == 2
    assistant = snapshot.messages[-1]
    assert assistant.role == "assistant"
    assert assistant.tool_calls[0].receipt["kind"] == "command"
    assert assistant.tool_calls[0].audit_events[0]["message"] == "Command completed"

def test_bridge_restores_agent_receipt_into_nested_child_snapshot():
    engine = DummyEngine()
    engine.messages = [
        {"role": "user", "content": "Delegate search"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "tool-agent", "name": "Agent", "input": {"prompt": "Search repo"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tool-agent",
                    "content": "Background task started: task_123",
                    "receipt": {
                        "kind": "agent",
                        "summary": "Spawned Explore agent",
                        "agent_id": "agent_42",
                        "agent_type": "Explore",
                        "mode": "fresh",
                        "task_id": "task_123",
                        "background": True,
                        "status": "running",
                        "result_preview": "Searching the repository",
                        "total_tokens": 77,
                    },
                    "audit_events": [],
                    "is_error": False,
                }
            ],
        },
    ]

    bridge = UIBridge(engine=engine)
    snapshot = bridge.get_snapshot()

    assistant = snapshot.messages[-1]
    assert assistant.agent_children
    child = assistant.agent_children[0]
    assert child.agent_id == "agent_42"
    assert child.content == "Searching the repository"

def test_bridge_updates_todo_summary_from_todo_updated_event():
    engine = DummyEngine()
    bridge = UIBridge(engine=engine)

    engine.execution_context["options"]["app_state"]["todos"]["session-main"] = [
        {
            "content": "Wire runtime command",
            "status": "in_progress",
            "activeForm": "Wiring runtime command",
        },
        {
            "content": "Render audit drawer",
            "status": "pending",
            "activeForm": "Rendering audit drawer",
        },
    ]

    bridge.begin_user_turn("update todos")
    bridge.apply_stream_event({"type": "stream_request_start"})
    bridge.apply_stream_event(
        {
            "type": "todo_updated",
            "key": "session-main",
            "items": engine.execution_context["options"]["app_state"]["todos"]["session-main"],
        }
    )

    snapshot = bridge.get_snapshot()
    assert snapshot.global_todos.active.content == "Wire runtime command"
    assert snapshot.messages[-1].todo_summary is not None
    assert snapshot.messages[-1].todo_summary.items[0].content == "Wire runtime command"

def test_bridge_reload_from_engine_keeps_previous_messages_when_new_history_hydration_breaks(monkeypatch):
    engine = DummyEngine()
    engine.messages = [
        {"role": "user", "content": "stable history", "type": "user", "uuid": "user-stable"},
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "stable reply"}],
            "type": "assistant",
            "uuid": "assistant-stable",
        },
    ]
    bridge = UIBridge(engine=engine)

    previous = [message.content for message in bridge.get_snapshot().messages]

    def _boom() -> None:
        raise RuntimeError("bad history payload")

    monkeypatch.setattr(bridge, "_hydrate_existing_messages", _boom)
    bridge.reload_from_engine()

    snapshot = bridge.get_snapshot()
    assert [message.content for message in snapshot.messages] == previous
    assert any("已保留上一份可用内容" in toast.message for toast in snapshot.toasts)

def test_bridge_reload_from_engine_keeps_previous_messages_when_new_history_is_all_invalid():
    engine = DummyEngine()
    engine.messages = [
        {"role": "user", "content": "stable history", "type": "user", "uuid": "user-stable"},
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "stable reply"}],
            "type": "assistant",
            "uuid": "assistant-stable",
        },
    ]
    bridge = UIBridge(engine=engine)

    previous = [message.content for message in bridge.get_snapshot().messages]
    engine.messages = [
        {"content": "missing role"},
        {"role": "system", "content": "unsupported"},
    ]

    bridge.reload_from_engine()

    snapshot = bridge.get_snapshot()
    assert [message.content for message in snapshot.messages] == previous
    assert any("已保留上一份可用内容" in toast.message for toast in snapshot.toasts)

def test_bridge_syncs_todo_updated_items_back_into_app_state():
    """todo_updated 事件即使先于 app_state 同步到达，也应驱动侧栏更新。"""
    engine = DummyEngine()
    engine.execution_context["options"]["app_state"]["todos"] = {}
    bridge = UIBridge(engine=engine)

    items = [
        {
            "content": "Create weather app",
            "status": "in_progress",
            "activeForm": "Creating weather app",
        },
        {
            "content": "Add city input",
            "status": "pending",
            "activeForm": "Adding city input",
        },
    ]

    bridge.begin_user_turn("create todo")
    bridge.apply_stream_event({"type": "stream_request_start"})
    bridge.apply_stream_event(
        {
            "type": "todo_updated",
            "key": "session-main",
            "items": items,
        }
    )

    snapshot = bridge.get_snapshot()
    assert snapshot.global_todos.total_count == 2
    assert snapshot.global_todos.active is not None
    assert snapshot.global_todos.active.content == "Create weather app"
    assert engine.execution_context["options"]["app_state"]["todos"]["session-main"] == items

def test_bridge_marks_interrupted_message_and_keeps_retry_target():
    """用户中断后，当前 assistant 消息应带中断标记。"""
    engine = DummyEngine()
    bridge = UIBridge(engine=engine)

    bridge.begin_user_turn("hello")
    bridge.apply_stream_event({"type": "stream_request_start"})
    bridge.apply_stream_event(
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": SimpleNamespace(type="text"),
        }
    )
    bridge.apply_stream_event(
        {
            "type": "text_delta",
            "index": 0,
            "delta": {"text": "Partial answer"},
        }
    )
    bridge.apply_stream_event(
        {
            "type": "error",
            "error": "User interrupted",
            "error_type": "user_interrupted",
        }
    )

    snapshot = bridge.get_snapshot()
    assistant = snapshot.messages[-1]
    assert assistant.interrupted is True
    assert snapshot.last_retry_prompt == "hello"

def test_bridge_uses_runtime_phase_to_refine_header_status():
    engine = DummyEngine()
    bridge = UIBridge(engine=engine)

    bridge.begin_user_turn("run tools")
    bridge.apply_stream_event({"type": "stream_request_start"})
    bridge.apply_stream_event(
        {
            "type": "status_changed",
            "phase": "execute_tools",
            "checkpoint_id": "chk_tools_1",
        }
    )

    status = bridge.get_snapshot().status
    assert status.top_status == "🟢 处理中"
    assert status.sub_status == "执行工具中"

def test_bridge_does_not_emit_query_finished_toast_for_terminal_errors():
    engine = DummyEngine()
    bridge = UIBridge(engine=engine)

    bridge.finish_terminal(SimpleNamespace(reason="api_error"))

    assert bridge.get_snapshot().toasts == []

def test_bridge_humanizes_rate_limit_error_message_for_users():
    engine = DummyEngine()
    bridge = UIBridge(engine=engine)

    bridge.begin_user_turn("retry later")
    bridge.apply_stream_event({"type": "stream_request_start"})
    bridge.apply_stream_event(
        {
            "type": "error",
            "error": "exceeded retry limit, last status: 429 Too Many Requests, request id: 4fa7f73e-f6af-4f63-a5cd-57793667b9a7",
        }
    )

    snapshot = bridge.get_snapshot()
    error_message = snapshot.messages[-1].content
    assert "请求过于频繁" in error_message
    assert "稍等片刻后重试" in error_message
    assert "请求 ID: 4fa7f73e-f6af-4f63-a5cd-57793667b9a7" in error_message
    assert "Too Many Requests" not in error_message

@pytest.mark.asyncio
async def test_bridge_retry_last_turn_prefers_runtime_checkpoint_restore():
    engine = RetryEngine()
    bridge = UIBridge(engine=engine)

    bridge.begin_user_turn("retry me")
    bridge.apply_stream_event({"type": "stream_request_start"})
    bridge.apply_stream_event(
        {
            "type": "status_changed",
            "phase": "execute_tools",
            "checkpoint_id": "chk_retry_1",
        }
    )
    bridge.apply_stream_event(
        {
            "type": "error",
            "error": "User interrupted",
            "error_type": "user_interrupted",
        }
    )

    await bridge.retry_last_turn()

    assert engine.retry_calls == ["chk_retry_1"]
    assert engine.stream_calls == [""]
    assert len([message for message in bridge.messages if message.role == "user"]) == 1

def test_bridge_activates_runtime_interaction_from_stream_event():
    engine = DummyEngine()
    bridge = UIBridge(engine=engine)

    bridge.apply_stream_event(
        {
            "type": "interaction_requested",
            "request": {
                "request_id": "req-1",
                "kind": "question",
                "label": "Need your choice",
                "questions": [
                    {
                        "question_id": "q-1",
                        "header": "Mode",
                        "question": "Which mode?",
                        "options": [
                            {"value": "safe", "label": "Safe"},
                            {"value": "fast", "label": "Fast"},
                        ],
                    }
                ],
            },
        }
    )

    snapshot = bridge.get_snapshot()
    assert snapshot.interaction is not None
    assert snapshot.interaction.request_id == "req-1"
    assert snapshot.interaction.questions[0].question == "Which mode?"

def test_bridge_consumes_runtime_tool_lifecycle_and_interaction_resolution():
    engine = DummyEngine()
    bridge = UIBridge(engine=engine)

    bridge.begin_user_turn("run tests")
    bridge.apply_stream_event({"type": "stream_request_start"})
    bridge.apply_stream_event(
        {
            "type": "tool_started",
            "tool_use_id": "tool-1",
            "tool_name": "Bash",
            "input_preview": '{"command":"pytest"}',
            "status": "running",
        }
    )
    bridge.apply_stream_event(
        {
            "type": "tool_progress",
            "tool_use_id": "tool-1",
            "tool_name": "Bash",
            "progress": "Collecting tests",
        }
    )
    bridge.apply_stream_event(
        {
            "type": "interaction_requested",
            "request": {
                "request_id": "req-2",
                "kind": "permission",
                "label": "Awaiting approval",
            },
        }
    )
    bridge.apply_stream_event(
        {
            "type": "interaction_resolved",
            "request_id": "req-2",
        }
    )
    bridge.apply_stream_event(
        {
            "type": "tool_completed",
            "tool_use_id": "tool-1",
            "tool_name": "Bash",
            "status": "completed",
            "content": "Pytest finished",
            "receipt": {"kind": "command", "summary": "Pytest finished"},
        }
    )

    snapshot = bridge.get_snapshot()
    assistant = snapshot.messages[-1]
    assert assistant.tool_calls[0].name == "Bash"
    assert assistant.tool_calls[0].status == "completed"
    assert assistant.tool_calls[0].result == "Pytest finished"
    assert assistant.tool_calls[0].receipt["kind"] == "command"
    assert snapshot.interaction is None

def test_bridge_tracks_nested_agent_runtime_stream_inside_assistant_message():
    engine = DummyEngine()
    bridge = UIBridge(engine=engine)

    bridge.begin_user_turn("delegate this task")
    bridge.apply_stream_event({"type": "stream_request_start"})
    bridge.apply_stream_event(
        {
            "type": "agent_started",
            "agent_id": "agent_42",
            "label": "Explore > Search repository",
            "agent_type": "Explore",
            "mode": "fresh",
            "background": False,
            "status": "running",
        }
    )
    bridge.apply_stream_event(
        {
            "type": "agent_delta",
            "agent_id": "agent_42",
            "content_delta": "Looking through the src tree",
            "status": "thinking",
        }
    )
    bridge.apply_stream_event(
        {
            "type": "agent_tool_started",
            "agent_id": "agent_42",
            "tool_use_id": "child-tool-1",
            "tool_name": "Glob",
            "input_preview": '{"pattern":"**/*.py"}',
        }
    )
    bridge.apply_stream_event(
        {
            "type": "agent_tool_completed",
            "agent_id": "agent_42",
            "tool_use_id": "child-tool-1",
            "content": "Matched 12 files",
            "status": "completed",
        }
    )
    bridge.apply_stream_event(
        {
            "type": "agent_completed",
            "agent_id": "agent_42",
            "result": "Found the root cause in middleware.py",
            "status": "completed",
            "total_tokens": 88,
        }
    )
    bridge.apply_stream_event({"type": "message_stop"})

    snapshot = bridge.get_snapshot()
    assistant = snapshot.messages[-1]

    assert assistant.role == "assistant"
    assert assistant.agent_children
    child = assistant.agent_children[0]
    assert child.agent_id == "agent_42"
    assert child.label == "Explore > Search repository"
    assert child.content.endswith("Found the root cause in middleware.py")
    assert child.status == "completed"
    assert child.tool_calls[0].name == "Glob"
    assert child.tool_calls[0].result == "Matched 12 files"

def test_bridge_tracks_nested_agent_tool_receipts_and_audit_events():
    engine = DummyEngine()
    bridge = UIBridge(engine=engine)

    bridge.begin_user_turn("delegate with structured child tool output")
    bridge.apply_stream_event({"type": "stream_request_start"})
    bridge.apply_stream_event(
        {
            "type": "agent_started",
            "agent_id": "agent_77",
            "label": "Review > Patch settings",
            "status": "running",
        }
    )
    bridge.apply_stream_event(
        {
            "type": "agent_tool_started",
            "agent_id": "agent_77",
            "tool_use_id": "child-tool-77",
            "tool_name": "Bash",
            "input_preview": '{"command":"pytest -q"}',
        }
    )
    bridge.apply_stream_event(
        {
            "type": "agent_tool_completed",
            "agent_id": "agent_77",
            "tool_use_id": "child-tool-77",
            "tool_name": "Bash",
            "content": "Pytest finished",
            "status": "completed",
            "receipt": {
                "kind": "command",
                "summary": "Pytest finished",
                "command": "pytest -q",
                "exit_code": 0,
                "stdout": "12 passed",
                "stderr": "",
            },
            "audit_events": [
                {
                    "event_id": "audit-child-1",
                    "agent_id": "agent_77",
                    "source": "tool",
                    "message": "Child tool completed",
                    "created_at": 0.0,
                    "metadata": {"cwd": "/tmp/project"},
                }
            ],
        }
    )

    snapshot = bridge.get_snapshot()
    child = snapshot.messages[-1].agent_children[0]

    assert child.tool_calls[0].receipt["kind"] == "command"
    assert child.tool_calls[0].audit_events[0]["message"] == "Child tool completed"

@pytest.mark.asyncio
async def test_bridge_derives_thinking_waiting_and_history_snapshots():
    """bridge 应能恢复历史消息，并从事件推导 Thinking / Waiting 状态。"""
    engine = DummyEngine()
    engine.messages = [
        {"role": "user", "content": "Previous prompt", "type": "user"},
        {
            "role": "assistant",
            "type": "assistant",
            "content": [
                {"type": "thinking", "thinking": "Need to inspect state."},
                {"type": "text", "text": "Recovered answer"},
            ],
        },
    ]

    bridge = UIBridge(engine=engine)
    restored = bridge.get_snapshot()

    assert len(restored.messages) == 2
    assert restored.messages[0].content == "Previous prompt"
    assert restored.messages[1].thinking == "Need to inspect state."
    assert restored.messages[1].content == "Recovered answer"

    bridge.apply_stream_event({"type": "stream_request_start"})
    bridge.apply_stream_event(
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": SimpleNamespace(type="thinking"),
        }
    )
    bridge.apply_stream_event(
        {
            "type": "thinking_delta",
            "index": 0,
            "delta": {"thinking": "Still thinking"},
        }
    )
    thinking_status = bridge.get_snapshot().status
    assert thinking_status.top_status == "🔵 思考中"

    wait_task = asyncio.create_task(
        bridge.request_interaction(
            InteractionRequest(
                request_id="req_wait_1",
                kind="question",
                label="Need input",
            )
        )
    )
    await asyncio.sleep(0)
    waiting_status = bridge.get_snapshot().status
    assert waiting_status.top_status == "🟡 等待输入"
    assert waiting_status.sub_status == "Need input"
    bridge.cancel_interaction("req_wait_1")
    await wait_task

@pytest.mark.asyncio
async def test_bridge_uses_future_backed_permission_interaction():
    """bridge 应通过 pending future 驱动权限交互，而不是由 App 模态阻塞。"""
    engine = DummyEngine()
    bridge = UIBridge(engine=engine)

    request_task = asyncio.create_task(
        bridge.request_permission(
            tool_name="Bash",
            tool_info="$ ls -la",
            message="Need approval",
        )
    )
    await asyncio.sleep(0)

    snapshot = bridge.get_snapshot()
    assert snapshot.interaction is not None
    assert snapshot.interaction.kind == "permission"
    assert snapshot.status.top_status == "🟡 等待输入"

    bridge.resolve_interaction(snapshot.interaction.request_id, "allow_once")
    result = await request_task

    assert result == "allow_once"
    assert bridge.get_snapshot().interaction is None

@pytest.mark.asyncio
async def test_bridge_uses_future_backed_question_interaction():
    """bridge 应支持问题卡片通过 resolve_interaction 完成异步闭环。"""
    engine = DummyEngine()
    bridge = UIBridge(engine=engine)

    question = SimpleNamespace(
        header="Mode",
        question="Which mode?",
        multiSelect=False,
        options=[
            SimpleNamespace(label="Fast", description="Quick path"),
            SimpleNamespace(label="Safe", description="Careful path"),
        ],
    )

    request_task = asyncio.create_task(bridge.request_questions([question]))
    await asyncio.sleep(0)

    snapshot = bridge.get_snapshot()
    assert snapshot.interaction is not None
    assert snapshot.interaction.kind == "question"
    assert snapshot.interaction.questions[0].header == "Mode"

    bridge.resolve_interaction(snapshot.interaction.request_id, {"Which mode?": "Safe"})
    result = await request_task

    assert result == {"Which mode?": "Safe"}
    assert bridge.get_snapshot().interaction is None

def test_bridge_snapshot_no_longer_exposes_audit_panel():
    engine = DummyEngine()
    bridge = UIBridge(engine=engine)

    snapshot = bridge.get_snapshot()
    assert not hasattr(snapshot, "audit_panel")
    assert not hasattr(bridge, "open_audit_view")

def test_bridge_caches_context_stats_between_live_snapshot_reads():
    engine = CountingEngine()
    bridge = UIBridge(engine=engine)

    bridge.begin_user_turn("cache stats")
    bridge.apply_stream_event({"type": "stream_request_start"}, notify=False)
    bridge.apply_stream_event({"type": "text_delta", "delta": {"text": "streaming content"}}, notify=False)

    baseline_calls = engine.context_stats_calls
    bridge.get_snapshot()
    bridge.get_snapshot()
    assert engine.context_stats_calls == baseline_calls + 1

    bridge.apply_stream_event({"type": "message_stop"}, notify=False)
    bridge.get_snapshot()
    assert engine.context_stats_calls == baseline_calls + 2

@pytest.mark.asyncio
async def test_bridge_resolves_diff_review_by_request_id():
    """bridge 应统一通过 request_interaction 驱动 diff_review。"""
    engine = DummyEngine()
    bridge = UIBridge(engine=engine)

    request = InteractionRequest(
        request_id="req_diff_1",
        kind="diff_review",
        label="Review app.py",
        message="Apply these changes?",
        options=[
            InteractionOption(value="accept", label="Accept"),
            InteractionOption(value="reject", label="Reject"),
        ],
        payload={"path": "C:/tmp/app.py"},
    )

    pending = asyncio.create_task(bridge.request_interaction(request))
    await asyncio.sleep(0)

    snapshot = bridge.get_snapshot()
    assert snapshot.interaction is not None
    assert snapshot.interaction.request_id == "req_diff_1"
    assert snapshot.interaction.kind == "diff_review"

    bridge.resolve_interaction("req_diff_1", "accept")
    result = await pending

    assert result == "accept"
    assert bridge.get_snapshot().interaction is None

@pytest.mark.asyncio
async def test_bridge_tracks_mailbox_events_and_current_action_for_agent_status():
    """bridge 应观察 mailbox 并更新 agent 当前动作。"""
    engine = DummyEngine()
    bridge = UIBridge(engine=engine)

    team_manager = get_team_manager()
    task_manager = get_task_manager()

    team_manager._agents.clear()
    team_manager._leader_id = None
    team_manager.mailbox._messages.clear()
    task_manager._tasks.clear()
    task_manager._running_tasks.clear()

    team_manager.register_agent("leader", "leader")
    team_manager.register_agent("agent-1", "worker")

    task = task_manager.create_task(agent_id="agent-1", description="Investigate bug")
    task.status = TaskStatus.RUNNING
    task.current_action = "Patch middleware.py"
    await bridge._on_task_status_update(task)

    await team_manager.send_message(
        from_agent="leader",
        to_agent="agent-1",
        message_type=MessageType.TASK_ASSIGNMENT,
        content="Patch the failing test",
    )

    snapshot = bridge.get_snapshot()
    agent = next(item for item in snapshot.agents if item.agent_id == "agent-1")

    assert agent.current_task == "Patch middleware.py"

@pytest.mark.asyncio
async def test_bridge_restores_persisted_runtime_state_for_todos_agents_and_retry(tmp_path):
    engine = RetryEngine()
    engine.execution_context["options"]["app_state"] = {"todos": {}}
    storage = SessionStorage(engine.session_id, str(tmp_path))
    storage.record_messages(
        [
            {
                "role": "user",
                "content": "Restore session",
                "uuid": "user-1",
                "type": "user",
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Recovered assistant"}],
                "uuid": "assistant-1",
                "type": "assistant",
            },
        ]
    )
    storage.record_runtime_event(
        {
            "type": "todo_updated",
            "key": engine.session_id,
            "items": [
                {
                    "content": "Run tests",
                    "status": "in_progress",
                    "activeForm": "Running tests",
                },
                {
                    "content": "Ship fix",
                    "status": "pending",
                    "activeForm": "Shipping fix",
                },
            ],
        }
    )
    storage.record_runtime_event(
        {
            "type": "agent_started",
            "agent_id": "agent-restore",
            "label": "Review > Inspect logs",
            "status": "running",
            "content": "Inspect logs",
        }
    )
    storage.record_runtime_event(
        {
            "type": "agent_delta",
            "agent_id": "agent-restore",
            "content_delta": "\nPatch middleware.py",
            "status": "active",
        }
    )
    storage.record_runtime_event({"type": "interrupt_ack", "checkpoint_id": "cp-restore"})
    storage.save_snapshot()

    engine.session_storage = storage
    engine.messages = storage.load_messages()

    bridge = UIBridge(engine=engine)
    snapshot = bridge.get_snapshot()
    assistant = next(message for message in snapshot.messages if message.role == "assistant")

    assert snapshot.global_todos.active.content == "Run tests"
    assert assistant.agent_children
    assert assistant.agent_children[0].agent_id == "agent-restore"
    assert "Patch middleware.py" in assistant.agent_children[0].content

    await bridge.retry_last_turn()

    assert engine.retry_calls == ["cp-restore"]

def test_bridge_restores_waiting_interaction_as_retryable_recovery_state(tmp_path):
    engine = RetryEngine()
    storage = SessionStorage(engine.session_id, str(tmp_path))
    storage.record_messages(
        [
            {
                "role": "user",
                "content": "Need approval",
                "uuid": "user-wait-1",
                "type": "user",
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "I need permission before editing files."}],
                "uuid": "assistant-wait-1",
                "type": "assistant",
            },
        ]
    )
    storage.save_last_prompt("Need approval")
    storage.record_runtime_event(
        {
            "type": "status_changed",
            "phase": "wait_interaction",
            "checkpoint_id": "cp-wait-1",
        }
    )
    storage.record_runtime_event(
        {
            "type": "interaction_requested",
            "request": {
                "request_id": "req-wait-1",
                "kind": "permission",
                "label": "Permission review",
                "message": "Allow editing files?",
                "options": [
                    {"value": "allow_once", "label": "Allow once"},
                    {"value": "deny", "label": "Deny"},
                ],
            },
        }
    )

    engine.session_storage = storage
    engine.messages = storage.load_messages()

    bridge = UIBridge(engine=engine)
    snapshot = bridge.get_snapshot()
    assistant = next(message for message in snapshot.messages if message.role == "assistant")

    assert snapshot.interaction is None
    assert snapshot.last_retry_prompt == "Need approval"
    assert assistant.interrupted is True
    assert assistant.completed is True
    assert "可以点击重试继续。" in assistant.content
    assert snapshot.status.top_status == "⏸ 已中断"

def test_bridge_auto_follow_waits_before_switching_agents_on_normal_progress():
    engine = DummyEngine()
    engine.execution_context["options"]["app_state"] = {"todos": {}}
    bridge = UIBridge(engine=engine)

    bridge.apply_stream_event(
        {
            "type": "agent_started",
            "agent_id": "agent-1",
            "label": "Worker > Task one",
            "status": "running",
            "content": "Task one",
        }
    )
    first_snapshot = bridge.get_snapshot()
    assert first_snapshot.active_entity_label == "Worker"
    assert "Task one" in first_snapshot.active_task_snippet

    bridge.apply_stream_event(
        {
            "type": "agent_started",
            "agent_id": "agent-2",
            "label": "Worker > Task two",
            "status": "running",
            "content": "Task two",
        }
    )
    held_snapshot = bridge.get_snapshot()
    assert held_snapshot.active_entity_label == "Worker"
    assert "Task one" in held_snapshot.active_task_snippet

    bridge._last_visible_agent_at -= 2
    switched_snapshot = bridge.get_snapshot()
    assert switched_snapshot.active_entity_label == "Worker"
    assert "Task two" in switched_snapshot.active_task_snippet

    bridge.apply_stream_event(
        {
            "type": "agent_error",
            "agent_id": "agent-1",
            "error": "boom",
        }
    )
    urgent_snapshot = bridge.get_snapshot()
    assert urgent_snapshot.active_entity_label == "Worker"
    assert "boom" in urgent_snapshot.active_task_snippet

def test_bridge_can_switch_permission_modes_and_clear_session_rules(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    engine = DummyEngine()
    bridge = UIBridge(engine=engine)
    permission_context = engine.execution_context["permission_context"]
    permission_context.always_allow_rules[PermissionRuleSource.SESSION] = ["Bash", "Write"]

    bypass = bridge.set_permission_mode("bypass", confirm=True)
    assert bypass["success"] is True
    assert permission_context.mode == PermissionMode.BYPASS_PERMISSIONS
    assert bridge.get_snapshot().status.permission_mode == "直通"

    ask = bridge.set_permission_mode("ask", strict=True)
    assert ask["success"] is True
    assert permission_context.mode == PermissionMode.DEFAULT
    assert permission_context.always_allow_rules[PermissionRuleSource.SESSION] == []
    assert bridge.get_snapshot().status.permission_mode == "询问"
