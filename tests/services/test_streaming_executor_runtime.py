import asyncio
from pathlib import Path

import pytest

from codo.cli.tui.interaction_types import InteractionOption, InteractionQuestion, InteractionRequest
from codo.services.tools.permission_checker import create_default_permission_context
from codo.services.tools.streaming_executor import StreamingToolExecutor, ToolStatus
from codo.tools.ask_user_question_tool.ask_user_question_tool import AskUserQuestionTool
from codo.tools.receipts import DiffReceipt, ProposedFileChange
from codo.tools.todo_write_tool import TodoWriteTool
from codo.tools.types import ToolResult
from codo.types.permissions import PermissionAskDecision

class FakeInteractionBroker:
    def __init__(self) -> None:
        self.requests: list[InteractionRequest] = []
        self._futures: dict[str, asyncio.Future[object]] = {}

    async def request(self, request: InteractionRequest) -> object:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[object] = loop.create_future()
        self.requests.append(request)
        self._futures[request.request_id] = future
        return await future

    def resolve(self, request_id: str, data: object) -> None:
        future = self._futures[request_id]
        future.set_result(data)

async def _wait_for_request(
    broker: FakeInteractionBroker,
    *,
    timeout: float = 1.0,
) -> InteractionRequest:
    async def _poll() -> InteractionRequest:
        while not broker.requests:
            await asyncio.sleep(0.01)
        return broker.requests[0]

    return await asyncio.wait_for(_poll(), timeout=timeout)

class PhaseTrackerSpy:
    def __init__(self) -> None:
        self.transitions: list[dict[str, object]] = []

    async def transition(self, phase: str, **kwargs) -> None:
        self.transitions.append({"phase": phase, **kwargs})

class RuntimeControllerSpy:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    async def emit_runtime_event(self, event_type: str, **payload) -> None:
        self.events.append((event_type, payload))

class PermissionTool:
    name = "Bash"
    is_concurrency_safe = False

    class input_schema:
        def __init__(self, **kwargs):
            self.command = kwargs["command"]

    async def check_permissions(self, input_data, context):
        from codo.types.permissions import create_passthrough_result

        return create_passthrough_result()

    async def call(self, input_data, context, *args):
        return ToolResult(
            data=None,
            receipt=None,
        )

class QuestionTool:
    name = "AskUserQuestion"
    is_concurrency_safe = False

    class input_schema:
        def __init__(self, **kwargs):
            self.questions = kwargs["questions"]
            self.answers = None

    async def check_permissions(self, input_data, context):
        from codo.types.permissions import create_passthrough_result

        return create_passthrough_result()

    async def call(self, input_data, context, *args):
        return ToolResult(
            data={"answers": input_data.answers},
        )

class StagedChangeTool:
    name = "Write"
    is_concurrency_safe = False

    def __init__(self, target_path: Path):
        self.target_path = target_path

    class input_schema:
        def __init__(self, **kwargs):
            self.file_path = kwargs["file_path"]
            self.content = kwargs["content"]

    async def check_permissions(self, input_data, context):
        from codo.types.permissions import create_passthrough_result

        return create_passthrough_result()

    async def call(self, input_data, context, *args):
        diff_text = "@@ -0,0 +1 @@\n+hello"
        change = ProposedFileChange(
            change_id="chg_1",
            path=str(self.target_path),
            original_content="",
            new_content=input_data.content,
            diff_text=diff_text,
            source_tool="Write",
        )
        return ToolResult(
            data=None,
            receipt=DiffReceipt(
                kind="diff",
                summary=f"Prepared create for {self.target_path}",
                path=str(self.target_path),
                diff_text=diff_text,
                change_id=change.change_id,
            ),
            staged_changes=[change],
        )

@pytest.mark.asyncio
async def test_executor_emits_permission_interaction_instead_of_calling_ui(monkeypatch):
    broker = FakeInteractionBroker()
    tool = PermissionTool()

    async def fake_has_permissions_to_use_tool(*_args, **_kwargs):
        return PermissionAskDecision(message="Need approval")

    monkeypatch.setattr(
        "codo.services.tools.permission_checker.has_permissions_to_use_tool",
        fake_has_permissions_to_use_tool,
    )

    executor = StreamingToolExecutor(
        [tool],
        {
            "cwd": ".",
            "permission_context": create_default_permission_context("."),
            "interaction_broker": broker,
        },
    )

    executor.add_tool(
        {"id": "tool-1", "name": "Bash", "input": {"command": "pytest"}},
        {"role": "assistant", "content": []},
    )

    tracked = executor.tools[0]
    request = await _wait_for_request(broker)
    assert tracked.status == ToolStatus.WAITING_INTERACTION
    assert request.kind == "permission"
    assert request.tool_name == "Bash"

    broker.resolve(request.request_id, "allow_once")

    results = [result async for result in executor.get_remaining_results()]
    assert tracked.status == ToolStatus.YIELDED
    assert results[0].status == "completed"

@pytest.mark.asyncio
async def test_executor_transitions_runtime_phase_around_permission_interaction(monkeypatch):
    broker = FakeInteractionBroker()
    tracker = PhaseTrackerSpy()
    tool = PermissionTool()

    async def fake_has_permissions_to_use_tool(*_args, **_kwargs):
        return PermissionAskDecision(message="Need approval")

    monkeypatch.setattr(
        "codo.services.tools.permission_checker.has_permissions_to_use_tool",
        fake_has_permissions_to_use_tool,
    )

    executor = StreamingToolExecutor(
        [tool],
        {
            "cwd": ".",
            "permission_context": create_default_permission_context("."),
            "interaction_broker": broker,
            "phase_tracker": tracker,
        },
    )

    executor.add_tool(
        {"id": "tool-1", "name": "Bash", "input": {"command": "pytest"}},
        {"role": "assistant", "content": []},
    )

    await _wait_for_request(broker)
    assert tracker.transitions
    assert tracker.transitions[0]["phase"] == "wait_interaction"
    assert tracker.transitions[0]["resume_target"] == "tool-1"

    broker.resolve(broker.requests[0].request_id, "allow_once")

    _ = [result async for result in executor.get_remaining_results()]

    phases = [entry["phase"] for entry in tracker.transitions]
    assert "apply_interaction_result" in phases
    assert phases[-1] == "execute_tools"

@pytest.mark.asyncio
async def test_executor_emits_question_interaction_and_applies_answers():
    broker = FakeInteractionBroker()
    tool = QuestionTool()

    executor = StreamingToolExecutor(
        [tool],
        {
            "cwd": ".",
            "permission_context": create_default_permission_context("."),
            "interaction_broker": broker,
        },
    )

    executor.add_tool(
        {
            "id": "tool-1",
            "name": "AskUserQuestion",
            "input": {
                "questions": [
                    {
                        "header": "Mode",
                        "question": "Which mode?",
                        "options": [
                            {"label": "Safe", "description": "Keep it safe"},
                            {"label": "Fast", "description": "Go faster"},
                        ],
                    }
                ]
            },
        },
        {"role": "assistant", "content": []},
    )

    tracked = executor.tools[0]
    request = await _wait_for_request(broker)
    assert tracked.status == ToolStatus.WAITING_INTERACTION
    assert request.kind == "question"
    assert request.questions[0].question == "Which mode?"

    broker.resolve(request.request_id, {"Which mode?": "Safe"})

    results = [result async for result in executor.get_remaining_results()]
    assert results[0].status == "completed"
    assert tracked.status == ToolStatus.YIELDED

@pytest.mark.asyncio
async def test_executor_uses_real_ask_user_question_tool_without_aborting():
    broker = FakeInteractionBroker()
    tool = AskUserQuestionTool()

    executor = StreamingToolExecutor(
        [tool],
        {
            "cwd": ".",
            "permission_context": create_default_permission_context("."),
            "interaction_broker": broker,
        },
    )

    executor.add_tool(
        {
            "id": "tool-real-question-1",
            "name": "AskUserQuestion",
            "input": {
                "questions": [
                    {
                        "header": "Mode",
                        "question": "Which mode?",
                        "options": [
                            {"label": "Safe", "description": "Keep it safe"},
                            {"label": "Fast", "description": "Move faster"},
                        ],
                    }
                ]
            },
        },
        {"role": "assistant", "content": []},
    )

    tracked = executor.tools[0]
    request = await _wait_for_request(broker)
    assert tracked.status == ToolStatus.WAITING_INTERACTION
    assert request.kind == "question"
    assert request.questions[0].question == "Which mode?"

    broker.resolve(request.request_id, {"Which mode?": "Safe"})

    results = [result async for result in executor.get_remaining_results()]
    assert tracked.status == ToolStatus.YIELDED
    assert results[0].is_error is False
    assert "Safe" in (results[0].content or "")

@pytest.mark.asyncio
async def test_executor_emits_diff_review_interaction_for_staged_changes(tmp_path: Path):
    broker = FakeInteractionBroker()
    tool = StagedChangeTool(tmp_path / "app.py")

    executor = StreamingToolExecutor(
        [tool],
        {
            "cwd": str(tmp_path),
            "permission_context": create_default_permission_context(str(tmp_path)),
            "interaction_broker": broker,
        },
    )

    executor.add_tool(
        {
            "id": "tool-1",
            "name": "Write",
            "input": {
                "file_path": str(tmp_path / "app.py"),
                "content": "hello",
            },
        },
        {"role": "assistant", "content": []},
    )

    tracked = executor.tools[0]
    request = await _wait_for_request(broker)
    assert tracked.status == ToolStatus.WAITING_INTERACTION
    assert request.kind == "diff_review"
    assert request.payload["path"] == str(tmp_path / "app.py")

    broker.resolve(request.request_id, "accept")

    results = [result async for result in executor.get_remaining_results()]
    assert tracked.status == ToolStatus.YIELDED
    assert results[0].receipt.kind == "diff"
    assert "Applied changes" in results[0].receipt.summary

@pytest.mark.asyncio
async def test_executor_emits_global_todo_updated_for_session_scope(monkeypatch):
    runtime = RuntimeControllerSpy()
    tool = TodoWriteTool()
    todos = [
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

    async def fake_has_permissions_to_use_tool(*_args, **_kwargs):
        from codo.types.permissions import create_passthrough_result

        return create_passthrough_result()

    monkeypatch.setattr(
        "codo.services.tools.permission_checker.has_permissions_to_use_tool",
        fake_has_permissions_to_use_tool,
    )

    executor = StreamingToolExecutor(
        [tool],
        {
            "cwd": ".",
            "session_id": "session-main",
            "permission_context": create_default_permission_context("."),
            "runtime_controller": runtime,
            "options": {
                "app_state": {"todos": {}},
            },
        },
    )

    executor.add_tool(
        {
            "id": "tool-todo-1",
            "name": "TodoWrite",
            "input": {"todos": todos},
        },
        {"role": "assistant", "content": []},
    )

    _ = [result async for result in executor.get_remaining_results()]

    todo_events = [payload for event_type, payload in runtime.events if event_type == "todo_updated"]
    assert len(todo_events) == 1
    assert todo_events[0]["key"] == "session-main"
    assert todo_events[0]["items"] == todos
