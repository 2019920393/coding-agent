import json
from uuid import uuid4

from codo.query_engine import QueryEngine
from codo.session.storage import SessionStorage, get_session_file_path

def test_session_storage_writes_event_log_and_snapshot(tmp_path):
    storage = SessionStorage(session_id=str(uuid4()), cwd=str(tmp_path))

    storage.record_messages(
        [
            {
                "role": "user",
                "content": "hello",
                "uuid": "msg-1",
                "type": "user",
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "hi"}],
                "uuid": "msg-2",
                "type": "assistant",
            },
        ]
    )
    storage.record_runtime_event({"type": "interaction_requested", "request": {"request_id": "req-1"}})
    storage.record_runtime_event(
        {
            "type": "todo_updated",
            "key": storage.session_id,
            "items": [
                {
                    "content": "Inspect logs",
                    "status": "in_progress",
                    "activeForm": "Inspecting logs",
                }
            ],
        }
    )
    storage.record_runtime_event({"type": "interrupt_ack", "checkpoint_id": "cp-1"})

    events = storage.load_events()
    snapshot = storage.load_snapshot()

    assert [event.event_type for event in events[:2]] == ["message_recorded", "message_recorded"]
    assert events[2].event_type == "interaction_requested"
    assert snapshot is not None
    assert snapshot.messages[0]["uuid"] == "msg-1"
    assert snapshot.messages[1]["uuid"] == "msg-2"
    assert snapshot.runtime_state["app_state"]["todos"][storage.session_id][0]["content"] == "Inspect logs"
    assert snapshot.runtime_state["retry_checkpoint_id"] == "cp-1"

def test_query_engine_restore_session_rehydrates_runtime_todos(tmp_path):
    session_id = str(uuid4())
    storage = SessionStorage(session_id=session_id, cwd=str(tmp_path))

    storage.record_messages(
        [
            {
                "role": "user",
                "content": "hello",
                "uuid": "msg-1",
                "type": "user",
            }
        ]
    )
    storage.record_runtime_event(
        {
            "type": "todo_updated",
            "key": session_id,
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

    engine = QueryEngine(
        client=object(),
        cwd=str(tmp_path),
        session_id=session_id,
        enable_persistence=True,
    )

    assert engine.restore_session() is True
    todos = engine.execution_context["options"]["app_state"]["todos"][session_id]
    assert todos[0]["content"] == "Run tests"
    assert todos[1]["status"] == "pending"

def test_session_storage_tracks_pending_interaction_in_runtime_state(tmp_path):
    storage = SessionStorage(session_id=str(uuid4()), cwd=str(tmp_path))

    storage.record_runtime_event(
        {
            "type": "interaction_requested",
            "request": {
                "request_id": "req-persist-1",
                "kind": "permission",
                "label": "Permission review",
                "message": "Allow writing files?",
                "options": [
                    {"value": "allow_once", "label": "Allow once"},
                    {"value": "deny", "label": "Deny"},
                ],
            },
        }
    )

    runtime_state = storage.load_runtime_state()

    assert runtime_state["pending_interaction"]["request_id"] == "req-persist-1"
    assert runtime_state["pending_interaction"]["kind"] == "permission"

    storage.record_runtime_event(
        {
            "type": "interaction_resolved",
            "request_id": "req-persist-1",
            "data": "allow_once",
        }
    )

    resolved_state = storage.load_runtime_state()

    assert resolved_state["pending_interaction"] is None

def test_session_storage_load_messages_prefers_snapshot(tmp_path):
    storage = SessionStorage(session_id=str(uuid4()), cwd=str(tmp_path))

    storage.record_messages(
        [
            {
                "role": "user",
                "content": "from snapshot",
                "uuid": "msg-1",
                "type": "user",
            }
        ]
    )

    restored = storage.load_messages()

    assert len(restored) == 1
    assert restored[0]["content"] == "from snapshot"

def test_session_storage_load_messages_skips_malformed_snapshot_entries(tmp_path):
    session_id = str(uuid4())
    storage = SessionStorage(session_id=session_id, cwd=str(tmp_path))
    storage.snapshot_file.parent.mkdir(parents=True, exist_ok=True)
    storage.snapshot_file.write_text(
        json.dumps(
            {
                "session_id": session_id,
                "messages": [
                    {
                        "role": "user",
                        "type": "user",
                        "uuid": "msg-1",
                        "content": "still here",
                        "timestamp": "2024-01-01T00:00:00",
                    },
                    "bad-message-entry",
                    {
                        "role": "assistant",
                        "type": "assistant",
                        "uuid": "msg-2",
                        "content": [{"type": "text", "text": "kept reply"}],
                        "timestamp": "2024-01-01T00:00:01",
                    },
                    {
                        "role": "assistant",
                        "type": "assistant",
                        "content": "missing uuid should be ignored",
                    },
                ],
                "runtime_state": {},
                "metadata": {"session_id": session_id, "custom_title": "Recovered"},
                "updated_at": "2024-01-01T00:00:02",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    restored = storage.load_messages()

    assert [message["uuid"] for message in restored] == ["msg-1", "msg-2"]
    assert restored[1]["content"][0]["text"] == "kept reply"
    assert storage.current_title == "Recovered"

def test_session_storage_load_messages_keeps_valid_event_records_when_one_is_malformed(tmp_path):
    session_id = str(uuid4())
    storage = SessionStorage(session_id=session_id, cwd=str(tmp_path))
    storage.event_log_file.parent.mkdir(parents=True, exist_ok=True)
    storage.event_log_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event_id": "evt-1",
                        "session_id": session_id,
                        "event_type": "message_recorded",
                        "payload": {
                            "message": {
                                "role": "user",
                                "type": "user",
                                "uuid": "msg-1",
                                "content": "keep me",
                                "timestamp": "2024-01-01T00:00:00",
                            }
                        },
                        "created_at": "2024-01-01T00:00:00",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "event_id": "evt-2",
                        "session_id": session_id,
                        "event_type": "message_recorded",
                        "payload": {
                            "message": {
                                "role": "assistant",
                                "type": "assistant",
                                "content": {"type": "text", "text": "missing uuid"},
                            }
                        },
                        "created_at": "2024-01-01T00:00:01",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "event_id": "evt-3",
                        "session_id": session_id,
                        "event_type": "metadata_updated",
                        "payload": {"custom_title": "Event recovery"},
                        "created_at": "2024-01-01T00:00:02",
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    if storage.snapshot_file.exists():
        storage.snapshot_file.unlink()

    restored = storage.load_messages()

    assert len(restored) == 1
    assert restored[0]["uuid"] == "msg-1"
    assert restored[0]["content"] == "keep me"
    assert storage.current_title == "Event recovery"

def test_session_storage_rebuilds_messages_from_event_log_when_transcript_is_missing(tmp_path):
    storage = SessionStorage(session_id=str(uuid4()), cwd=str(tmp_path))

    storage.record_messages(
        [
            {
                "role": "user",
                "content": "recover me",
                "uuid": "msg-1",
                "type": "user",
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "recovered"}],
                "uuid": "msg-2",
                "type": "assistant",
            },
        ]
    )
    storage.save_custom_title("Recovered Title")

    if storage.session_file and storage.session_file.exists():
        storage.session_file.unlink()
    if storage.snapshot_file.exists():
        storage.snapshot_file.unlink()

    restored = storage.load_messages()

    assert len(restored) == 2
    assert restored[0]["content"] == "recover me"
    assert restored[1]["content"][0]["text"] == "recovered"
    assert storage.current_title == "Recovered Title"

def test_session_storage_migrates_legacy_transcript_to_event_log_and_snapshot(tmp_path):
    session_id = str(uuid4())
    storage = SessionStorage(session_id=session_id, cwd=str(tmp_path))
    transcript_path = get_session_file_path(session_id, str(tmp_path))
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "custom-title",
                        "sessionId": session_id,
                        "customTitle": "Legacy Session",
                        "timestamp": "2024-01-01T00:00:00",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "type": "agent-name",
                        "sessionId": session_id,
                        "agentName": "Planner",
                        "timestamp": "2024-01-01T00:00:01",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "type": "agent-color",
                        "sessionId": session_id,
                        "agentColor": "#7aa2f7",
                        "timestamp": "2024-01-01T00:00:02",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "type": "mode",
                        "sessionId": session_id,
                        "mode": "coordinator",
                        "timestamp": "2024-01-01T00:00:03",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "type": "last-prompt",
                        "sessionId": session_id,
                        "lastPrompt": "continue",
                        "timestamp": "2024-01-01T00:00:04",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "type": "user",
                        "role": "user",
                        "uuid": "msg-1",
                        "content": "legacy hello",
                        "timestamp": "2024-01-01T00:00:05",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "role": "assistant",
                        "uuid": "msg-2",
                        "parent_uuid": "msg-1",
                        "content": [
                            {"type": "text", "text": "legacy reply"},
                            {
                                "type": "tool_use",
                                "name": "TodoWrite",
                                "input": {
                                    "todos": [
                                        {
                                            "content": "Migrate session",
                                            "status": "in_progress",
                                            "activeForm": "Migrating session",
                                        },
                                        {
                                            "content": "Verify restore",
                                            "status": "pending",
                                            "activeForm": "Verifying restore",
                                        },
                                    ]
                                },
                            },
                        ],
                        "timestamp": "2024-01-01T00:00:06",
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    restored = storage.load_messages()
    runtime_state = storage.load_runtime_state()
    events = storage.load_events()
    snapshot = storage.load_snapshot()

    assert [message["uuid"] for message in restored] == ["msg-1", "msg-2"]
    assert transcript_path.exists()
    assert storage.event_log_file.exists()
    assert storage.snapshot_file.exists()
    assert snapshot is not None
    assert snapshot.messages[1]["content"][0]["text"] == "legacy reply"
    assert storage.current_title == "Legacy Session"
    assert storage.current_agent_name == "Planner"
    assert storage.current_agent_color == "#7aa2f7"
    assert storage.current_mode == "coordinator"
    assert storage.current_last_prompt == "continue"
    assert [event.event_type for event in events[:7]] == [
        "metadata_updated",
        "metadata_updated",
        "metadata_updated",
        "metadata_updated",
        "metadata_updated",
        "message_recorded",
        "message_recorded",
    ]
    assert events[-1].event_type == "todo_updated"
    assert runtime_state["app_state"]["todos"][session_id][0]["content"] == "Migrate session"
    assert runtime_state["app_state"]["todos"][session_id][1]["status"] == "pending"

def test_query_engine_restore_session_migrates_legacy_transcript_runtime_state(tmp_path):
    session_id = str(uuid4())
    transcript_path = get_session_file_path(session_id, str(tmp_path))
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "user",
                        "role": "user",
                        "uuid": "msg-1",
                        "content": "resume legacy",
                        "timestamp": "2024-01-01T00:00:00",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "role": "assistant",
                        "uuid": "msg-2",
                        "parent_uuid": "msg-1",
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "TodoWrite",
                                "input": {
                                    "todos": [
                                        {
                                            "content": "Recovered todo",
                                            "status": "in_progress",
                                            "activeForm": "Recovering todo",
                                        }
                                    ]
                                },
                            }
                        ],
                        "timestamp": "2024-01-01T00:00:01",
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    engine = QueryEngine(
        client=object(),
        cwd=str(tmp_path),
        session_id=session_id,
        enable_persistence=True,
    )

    assert engine.restore_session() is True
    assert engine.messages[0]["uuid"] == "msg-1"
    assert engine.messages[1]["uuid"] == "msg-2"
    assert engine.execution_context["options"]["app_state"]["todos"][session_id][0]["content"] == "Recovered todo"
