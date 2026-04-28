"""Tests for Streaming Tool Executor system."""

import pytest
import asyncio
from pathlib import Path
from unittest.mock import Mock, AsyncMock

from codo.services.tools.streaming_executor import (
    StreamingToolExecutor,
    ToolStatus,
    TrackedTool,
    ToolUpdate,
)
from codo.services.tools.error_handler import (
    ErrorHandler,
    ErrorSeverity,
    RollbackManager,
)
from codo.services.tools.progress_reporter import (
    ProgressReporter,
    ProgressType,
)
from codo.cli.tui.interaction_types import InteractionRequest
from codo.tools.base import ToolUseContext
from codo.tools.types import ToolResult, ValidationResult
from codo.types.permissions import create_passthrough_result
from codo.services.tools.permission_checker import create_default_permission_context
from codo.types.permissions import PermissionMode

class FakeInteractionBroker:
    """Minimal broker used by executor runtime interaction tests."""

    def __init__(self):
        self.requests: list[InteractionRequest] = []
        self._futures: dict[str, asyncio.Future[object]] = {}

    async def request(self, request: InteractionRequest):
        loop = asyncio.get_running_loop()
        future: asyncio.Future[object] = loop.create_future()
        self.requests.append(request)
        self._futures[request.request_id] = future
        return await future

    def resolve(self, request_id: str, data: object) -> None:
        self._futures[request_id].set_result(data)

class MockTool:
    """Mock tool for testing."""

    def __init__(self, name: str, is_safe: bool = True, should_fail: bool = False):
        self.name = name
        self.is_concurrency_safe = is_safe
        self.should_fail = should_fail
        self.call_count = 0

    def input_schema(self, **kwargs):
        """Mock input schema."""
        return kwargs

    async def call(self, input_data, context):
        """Mock call method."""
        self.call_count += 1
        await asyncio.sleep(0.1)  # Simulate work

        if self.should_fail:
            raise ValueError(f"{self.name} failed")

        return Mock(data=f"{self.name} result", error=None)

class ReceiptTool:
    """Mock tool that returns a structured receipt."""

    def __init__(self):
        self.name = "Bash"

    @staticmethod
    def is_concurrency_safe(_input_data):
        return False

    class input_schema:
        def __init__(self, **kwargs):
            self.command = kwargs["command"]

    async def call(self, input_data, context, *args):
        from codo.tools.receipts import AuditLogEvent, CommandReceipt
        from codo.tools.types import ToolResult

        return ToolResult(
            data=None,
            receipt=CommandReceipt(
                kind="command",
                summary="Ran pytest",
                command=input_data.command,
                exit_code=0,
                stdout="ok",
            ),
            audit_events=[
                AuditLogEvent(
                    event_id="evt-1",
                    agent_id="assistant",
                    source="tool",
                    message="Command finished",
                    created_at=0.0,
                    metadata={"command": input_data.command},
                )
            ],
        )

class ContextAwareExecutorTool:
    """验证 StreamingToolExecutor 内部传递的是 ToolUseContext。"""

    def __init__(self):
        self.name = "Read"
        self.seen_context_types: list[tuple[str, type]] = []

    @staticmethod
    def is_concurrency_safe(_input_data):
        return True

    class input_schema:
        def __init__(self, **kwargs):
            self.path = kwargs.get("path", "README.md")

    async def validate_input(self, input_data, context):
        self.seen_context_types.append(("validate", type(context)))
        return ValidationResult(result=True)

    async def check_permissions(self, input_data, context):
        self.seen_context_types.append(("permission", type(context)))
        return create_passthrough_result()

    async def call(self, input_data, context, *args):
        self.seen_context_types.append(("call", type(context)))
        return ToolResult(data="ok", error=None)

class StagedChangeTool:
    """Mock tool that returns a staged file change requiring review."""

    def __init__(self, target_path: Path):
        self.name = "Write"
        self.target_path = target_path

    @staticmethod
    def is_concurrency_safe(_input_data):
        return False

    class input_schema:
        def __init__(self, **kwargs):
            self.file_path = kwargs["file_path"]
            self.content = kwargs["content"]

    async def call(self, input_data, context, *args):
        from codo.tools.receipts import DiffReceipt, ProposedFileChange
        from codo.tools.types import ToolResult

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

class TestStreamingToolExecutor:
    """Test StreamingToolExecutor."""

    @pytest.mark.asyncio
    async def test_add_tool_concurrent_safe(self):
        """Test adding concurrent-safe tools."""
        tool1 = MockTool("Read", is_safe=True)
        tool2 = MockTool("Grep", is_safe=True)
        tools = [tool1, tool2]

        executor = StreamingToolExecutor(tools, {}, max_concurrency=10)

        # Add first tool
        executor.add_tool(
            {"id": "tool1", "name": "Read", "input": {}},
            {"role": "assistant", "content": []}
        )

        # Add second tool
        executor.add_tool(
            {"id": "tool2", "name": "Grep", "input": {}},
            {"role": "assistant", "content": []}
        )

        # Wait for execution
        await asyncio.sleep(0.3)

        # Both should execute concurrently
        assert tool1.call_count == 1
        assert tool2.call_count == 1

    @pytest.mark.asyncio
    async def test_add_tool_non_concurrent(self):
        """Test adding non-concurrent-safe tools."""
        tool1 = MockTool("Edit", is_safe=False)
        tool2 = MockTool("Write", is_safe=False)
        tools = [tool1, tool2]

        executor = StreamingToolExecutor(tools, {}, max_concurrency=10)

        # Add first tool
        executor.add_tool(
            {"id": "tool1", "name": "Edit", "input": {}},
            {"role": "assistant", "content": []}
        )

        # Add second tool
        executor.add_tool(
            {"id": "tool2", "name": "Write", "input": {}},
            {"role": "assistant", "content": []}
        )

        # Wait for execution
        await asyncio.sleep(0.5)

        # Both should execute, but sequentially
        assert tool1.call_count == 1
        assert tool2.call_count == 1

    @pytest.mark.asyncio
    async def test_tool_not_found(self):
        """Test handling tool not found."""
        executor = StreamingToolExecutor([], {}, max_concurrency=10)

        executor.add_tool(
            {"id": "tool1", "name": "NonExistent", "input": {}},
            {"role": "assistant", "content": []}
        )

        # Should create error result immediately
        assert len(executor.tools) == 1
        assert executor.tools[0].status == ToolStatus.COMPLETED
        assert executor.tools[0].results[0]["content"][0]["is_error"] is True

    @pytest.mark.asyncio
    async def test_get_completed_results(self):
        """Test getting completed results."""
        tool = MockTool("Read", is_safe=True)
        executor = StreamingToolExecutor([tool], {}, max_concurrency=10)

        executor.add_tool(
            {"id": "tool1", "name": "Read", "input": {}},
            {"role": "assistant", "content": []}
        )

        # Wait for completion
        await asyncio.sleep(0.2)

        # Get results
        results = executor.get_completed_results()

        assert len(results) == 1
        assert results[0].tool_use_id == "tool1"
        assert results[0].status == "completed"

    @pytest.mark.asyncio
    async def test_executor_coerces_raw_context_to_tool_use_context(self):
        """流式执行链里的 validate / permission / call 都应收到 ToolUseContext。"""
        tool = ContextAwareExecutorTool()
        executor = StreamingToolExecutor(
            [tool],
            {
                "cwd": ".",
                "permission_context": create_default_permission_context(".", mode=PermissionMode.BYPASS_PERMISSIONS),
            },
            max_concurrency=10,
        )

        executor.add_tool(
            {"id": "tool1", "name": "Read", "input": {"path": "README.md"}},
            {"role": "assistant", "content": []},
        )

        await asyncio.sleep(0.3)

        assert tool.seen_context_types == [
            ("validate", ToolUseContext),
            ("permission", ToolUseContext),
            ("call", ToolUseContext),
        ]

    @pytest.mark.asyncio
    async def test_sibling_abort_on_bash_error(self):
        """Test sibling abort when Bash tool errors."""
        bash_tool = MockTool("Bash", is_safe=False, should_fail=True)
        read_tool = MockTool("Read", is_safe=True)
        tools = [bash_tool, read_tool]

        executor = StreamingToolExecutor(tools, {}, max_concurrency=10)

        # Add Bash tool (will fail)
        executor.add_tool(
            {"id": "bash1", "name": "Bash", "input": {}},
            {"role": "assistant", "content": []}
        )

        # Add concurrent Read tool
        executor.add_tool(
            {"id": "read1", "name": "Read", "input": {}},
            {"role": "assistant", "content": []}
        )

        # Wait for execution
        await asyncio.sleep(0.3)

        # Bash should have errored
        assert executor.has_errored is True

        # Read tool should be aborted
        read_tracked = next(t for t in executor.tools if t.id == "read1")
        assert read_tracked.status == ToolStatus.COMPLETED
        assert "Cancelled" in read_tracked.results[0]["content"][0]["content"]

    @pytest.mark.asyncio
    async def test_get_remaining_results(self):
        """Test getting remaining results."""
        tool = MockTool("Read", is_safe=True)
        executor = StreamingToolExecutor([tool], {}, max_concurrency=10)

        executor.add_tool(
            {"id": "tool1", "name": "Read", "input": {}},
            {"role": "assistant", "content": []}
        )

        # Get remaining results
        results = []
        async for result in executor.get_remaining_results():
            results.append(result)

        assert len(results) == 1
        assert results[0].tool_use_id == "tool1"

    @pytest.mark.asyncio
    async def test_discard(self):
        """Test discarding executor."""
        tool = MockTool("Read", is_safe=True)
        executor = StreamingToolExecutor([tool], {}, max_concurrency=10)

        executor.add_tool(
            {"id": "tool1", "name": "Read", "input": {}},
            {"role": "assistant", "content": []}
        )

        # Discard immediately
        executor.discard()

        # Should not return results
        results = executor.get_completed_results()
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_executor_returns_structured_receipt(self):
        """Executor 应保留结构化 receipt，而不是只返回字符串。"""
        executor = StreamingToolExecutor([ReceiptTool()], {}, max_concurrency=1)

        executor.add_tool(
            {"id": "tool-1", "name": "Bash", "input": {"command": "pytest -q"}},
            {"role": "assistant", "content": []},
        )

        results = [result async for result in executor.get_remaining_results()]

        assert len(results) == 1
        assert results[0].receipt.kind == "command"
        assert results[0].message["content"][0]["content"].startswith("Ran pytest")

    @pytest.mark.asyncio
    async def test_executor_embeds_receipt_metadata_into_tool_result_message(self):
        """持久化用 tool_result 消息应带上 receipt 和 audit_events 元数据。"""
        executor = StreamingToolExecutor([ReceiptTool()], {}, max_concurrency=1)

        executor.add_tool(
            {"id": "tool-1", "name": "Bash", "input": {"command": "pytest -q"}},
            {"role": "assistant", "content": []},
        )

        results = [result async for result in executor.get_remaining_results()]

        block = results[0].message["content"][0]
        assert block["receipt"]["kind"] == "command"
        assert block["audit_events"][0]["message"] == "Command finished"

    @pytest.mark.asyncio
    async def test_executor_reviews_and_applies_staged_change_before_emitting_result(self, tmp_path):
        """staged change 应在 accept 后才真正落盘，并输出 applied 收据。"""
        target = tmp_path / "note.txt"
        broker = FakeInteractionBroker()

        executor = StreamingToolExecutor(
            [StagedChangeTool(target)],
            {"interaction_broker": broker},
            max_concurrency=1,
        )

        executor.add_tool(
            {
                "id": "tool-1",
                "name": "Write",
                "input": {"file_path": str(target), "content": "hello"},
            },
            {"role": "assistant", "content": []},
        )

        while not broker.requests:
            await asyncio.sleep(0.01)
        broker.resolve(broker.requests[0].request_id, "accept")

        results = [result async for result in executor.get_remaining_results()]

        assert target.read_text(encoding="utf-8") == "hello"
        assert results[0].receipt.summary == f"Applied changes to {target}"
        assert results[0].message["content"][0]["content"].startswith("Applied changes to")

    @pytest.mark.asyncio
    async def test_executor_rejects_staged_change_without_writing_file(self, tmp_path):
        """reject staged change 时不应写文件，且输出 rejected 收据。"""
        target = tmp_path / "note.txt"
        broker = FakeInteractionBroker()

        executor = StreamingToolExecutor(
            [StagedChangeTool(target)],
            {"interaction_broker": broker},
            max_concurrency=1,
        )

        executor.add_tool(
            {
                "id": "tool-1",
                "name": "Write",
                "input": {"file_path": str(target), "content": "hello"},
            },
            {"role": "assistant", "content": []},
        )

        while not broker.requests:
            await asyncio.sleep(0.01)
        broker.resolve(broker.requests[0].request_id, "reject")

        results = [result async for result in executor.get_remaining_results()]

        assert not target.exists()
        assert results[0].receipt.summary == f"Rejected changes to {target}"
        assert results[0].message["content"][0]["content"].startswith("Rejected changes to")

class TestErrorHandler:
    """Test ErrorHandler."""

    def test_handle_error(self):
        """Test handling errors."""
        handler = ErrorHandler()

        error = ValueError("Test error")
        tool_error = handler.handle_error(
            tool_use_id="tool1",
            tool_name="Read",
            error=error,
        )

        assert tool_error.tool_use_id == "tool1"
        assert tool_error.tool_name == "Read"
        assert tool_error.error_type == "ValueError"
        assert tool_error.severity == ErrorSeverity.ERROR

    def test_bash_error_is_critical(self):
        """Test that Bash errors are critical."""
        handler = ErrorHandler()

        error = RuntimeError("Command failed")
        tool_error = handler.handle_error(
            tool_use_id="bash1",
            tool_name="Bash",
            error=error,
        )

        assert tool_error.severity == ErrorSeverity.CRITICAL
        assert handler.should_abort_siblings(tool_error) is True

    def test_error_callback(self):
        """Test error callbacks."""
        handler = ErrorHandler()
        callback_called = []

        def callback(error):
            callback_called.append(error)

        handler.register_error_callback(callback)

        error = ValueError("Test")
        handler.handle_error("tool1", "Read", error)

        assert len(callback_called) == 1
        assert callback_called[0].tool_use_id == "tool1"

    def test_get_errors(self):
        """Test getting errors."""
        handler = ErrorHandler()

        handler.handle_error("tool1", "Read", ValueError("Error 1"))
        handler.handle_error("tool2", "Write", ValueError("Error 2"))

        all_errors = handler.get_errors()
        assert len(all_errors) == 2

        tool1_errors = handler.get_errors(tool_use_id="tool1")
        assert len(tool1_errors) == 1
        assert tool1_errors[0].tool_use_id == "tool1"

class TestRollbackManager:
    """Test RollbackManager."""

    def test_register_and_rollback(self):
        """Test registering and executing rollback."""
        manager = RollbackManager()
        rollback_executed = []

        def rollback_fn():
            rollback_executed.append("action1")

        manager.register_rollback(
            tool_use_id="tool1",
            action_id="action1",
            description="Test rollback",
            rollback_fn=rollback_fn,
        )

        # Execute rollback
        count = manager.rollback("tool1")

        assert count == 1
        assert "action1" in rollback_executed

    def test_rollback_order(self):
        """Test rollback execution order (LIFO)."""
        manager = RollbackManager()
        order = []

        manager.register_rollback(
            "tool1", "action1", "First", lambda: order.append(1)
        )
        manager.register_rollback(
            "tool1", "action2", "Second", lambda: order.append(2)
        )
        manager.register_rollback(
            "tool1", "action3", "Third", lambda: order.append(3)
        )

        manager.rollback("tool1")

        # Should execute in reverse order
        assert order == [3, 2, 1]

    def test_clear_rollbacks(self):
        """Test clearing rollbacks."""
        manager = RollbackManager()

        manager.register_rollback(
            "tool1", "action1", "Test", lambda: None
        )

        actions = manager.get_rollback_actions("tool1")
        assert len(actions) == 1

        manager.clear_rollbacks("tool1")

        actions = manager.get_rollback_actions("tool1")
        assert len(actions) == 0

class TestProgressReporter:
    """Test ProgressReporter."""

    def test_report_started(self):
        """Test reporting started."""
        reporter = ProgressReporter()
        updates = []

        reporter.register_callback(lambda u: updates.append(u))
        reporter.report_started("tool1", "Read")

        assert len(updates) == 1
        assert updates[0].progress_type == ProgressType.STARTED
        assert updates[0].percentage == 0.0

    def test_report_progress(self):
        """Test reporting progress."""
        reporter = ProgressReporter()
        updates = []

        reporter.register_callback(lambda u: updates.append(u))
        reporter.report_progress("tool1", "Read", "Reading file", percentage=50.0)

        assert len(updates) == 1
        assert updates[0].progress_type == ProgressType.PROGRESS
        assert updates[0].percentage == 50.0

    def test_report_completed(self):
        """Test reporting completed."""
        reporter = ProgressReporter()
        updates = []

        reporter.register_callback(lambda u: updates.append(u))
        reporter.report_completed("tool1", "Read")

        assert len(updates) == 1
        assert updates[0].progress_type == ProgressType.COMPLETED
        assert updates[0].percentage == 100.0

    def test_report_failed(self):
        """Test reporting failed."""
        reporter = ProgressReporter()
        updates = []

        reporter.register_callback(lambda u: updates.append(u))
        reporter.report_failed("tool1", "Read", "File not found")

        assert len(updates) == 1
        assert updates[0].progress_type == ProgressType.FAILED
        assert "File not found" in updates[0].message

    def test_get_progress_history(self):
        """Test getting progress history."""
        reporter = ProgressReporter()

        reporter.report_started("tool1", "Read")
        reporter.report_progress("tool1", "Read", "50%", 50.0)
        reporter.report_completed("tool1", "Read")

        history = reporter.get_progress_history("tool1")
        assert len(history) == 3
        assert history[0].progress_type == ProgressType.STARTED
        assert history[1].progress_type == ProgressType.PROGRESS
        assert history[2].progress_type == ProgressType.COMPLETED

    def test_get_latest_progress(self):
        """Test getting latest progress."""
        reporter = ProgressReporter()

        reporter.report_started("tool1", "Read")
        reporter.report_progress("tool1", "Read", "Working", 50.0)

        latest = reporter.get_latest_progress("tool1")
        assert latest is not None
        assert latest.progress_type == ProgressType.PROGRESS
        assert latest.percentage == 50.0
