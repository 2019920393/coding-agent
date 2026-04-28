"""Integration tests for enhanced team collaboration system."""

import pytest
import asyncio
from unittest.mock import AsyncMock, patch

from codo.team import (
    SubAgentContext,
    prepare_fresh_context,
    prepare_fork_context,
    should_use_fork_mode,
    BackgroundTask,
    TaskStatus,
    get_task_manager,
)
from codo.team.enhanced_agent import run_subagent_with_mode
from codo.tools.agent_tool.types import AgentToolInput

class FakeRuntimeController:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def emit_runtime_event(self, event_type: str, **payload):
        self.events.append((event_type, payload))

class TestSubAgentContext:
    """Test sub-agent context management."""

    def test_prepare_fresh_context(self):
        """Test preparing fresh sub-agent context."""
        ctx = prepare_fresh_context(
            agent_type="Explore",
            system_prompt="You are an explorer",
            tools=[],
            model="claude-haiku-4-5",
            is_background=False,
        )

        assert ctx.mode == "fresh"
        assert ctx.agent_type == "Explore"
        assert ctx.system_prompt == "You are an explorer"
        assert ctx.model == "claude-haiku-4-5"
        assert ctx.is_background is False
        assert ctx.agent_id is not None
        assert ctx.metadata["created_from"] == "fresh"

    def test_prepare_fork_context(self):
        """Test preparing forked sub-agent context."""
        parent_ctx = {
            "system_prompt": "Parent prompt",
            "model": "claude-sonnet-4",
            "agent_id": "parent_123",
        }

        ctx = prepare_fork_context(
            parent_context=parent_ctx,
            tools=[],
            is_background=True,
        )

        assert ctx.mode == "fork"
        assert ctx.agent_type == "forked"
        assert "Parent prompt" in ctx.system_prompt
        assert "FORK MODE RESTRICTIONS" in ctx.system_prompt
        assert ctx.model == "claude-sonnet-4"
        assert ctx.is_background is True
        assert ctx.parent_context == parent_ctx
        assert ctx.metadata["created_from"] == "fork"
        assert ctx.metadata["parent_agent_id"] == "parent_123"

    def test_should_use_fork_mode_with_subagent_type(self):
        """Test fork mode decision with specific agent type."""
        parent_ctx = {"agent_id": "parent"}

        # Specific agent type -> fresh mode
        assert should_use_fork_mode("Explore", parent_ctx) is False
        assert should_use_fork_mode("Plan", parent_ctx) is False

    def test_should_use_fork_mode_without_subagent_type(self):
        """Test fork mode decision without specific agent type."""
        parent_ctx = {"agent_id": "parent"}

        # No specific type -> fork mode
        assert should_use_fork_mode(None, parent_ctx) is True

    def test_should_use_fork_mode_no_parent(self):
        """Test fork mode decision without parent context."""
        # No parent -> must use fresh
        assert should_use_fork_mode(None, None) is False

class TestBackgroundTasks:
    """Test background task execution."""

    @pytest.mark.asyncio
    async def test_create_task(self):
        """Test creating a background task."""
        manager = get_task_manager()

        task = manager.create_task(
            agent_id="agent_123",
            description="Test task",
        )

        assert task.task_id is not None
        assert task.agent_id == "agent_123"
        assert task.description == "Test task"
        assert task.status == TaskStatus.PENDING

    @pytest.mark.asyncio
    async def test_run_task_success(self):
        """Test running a successful background task."""
        manager = get_task_manager()

        task = manager.create_task(
            agent_id="agent_123",
            description="Success task",
        )

        async def work():
            await asyncio.sleep(0.1)
            return "Task completed"

        await manager.run_task(task, work())

        # Wait for completion
        completed = await manager.wait_for_task(task.task_id, timeout=1.0)

        assert completed is not None
        assert completed.status == TaskStatus.COMPLETED
        assert completed.result == "Task completed"
        assert completed.error is None

    @pytest.mark.asyncio
    async def test_run_task_failure(self):
        """Test running a failing background task."""
        manager = get_task_manager()

        task = manager.create_task(
            agent_id="agent_123",
            description="Failing task",
        )

        async def work():
            await asyncio.sleep(0.1)
            raise ValueError("Task failed")

        await manager.run_task(task, work())

        # Wait for completion
        completed = await manager.wait_for_task(task.task_id, timeout=1.0)

        assert completed is not None
        assert completed.status == TaskStatus.FAILED
        assert "Task failed" in completed.error

    @pytest.mark.asyncio
    async def test_cancel_task(self):
        """Test cancelling a running task."""
        manager = get_task_manager()

        task = manager.create_task(
            agent_id="agent_123",
            description="Long task",
        )

        async def work():
            await asyncio.sleep(10.0)
            return "Should not complete"

        await manager.run_task(task, work())

        # Give it time to start
        await asyncio.sleep(0.1)

        # Cancel it
        cancelled = await manager.cancel_task(task.task_id)
        assert cancelled is True

        # Wait for cancellation
        await asyncio.sleep(0.2)

        task_status = manager.get_task(task.task_id)
        assert task_status.status == TaskStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_get_running_tasks(self):
        """Test getting running tasks."""
        manager = get_task_manager()

        task1 = manager.create_task("agent_1", "Task 1")
        task2 = manager.create_task("agent_2", "Task 2")

        async def work():
            await asyncio.sleep(1.0)

        await manager.run_task(task1, work())
        await manager.run_task(task2, work())

        # Give tasks time to start
        await asyncio.sleep(0.1)

        running = manager.get_running_tasks()
        assert len(running) >= 2

        # Cleanup
        await manager.cancel_task(task1.task_id)
        await manager.cancel_task(task2.task_id)

    @pytest.mark.asyncio
    async def test_notification_callback(self):
        """Test task completion notification."""
        manager = get_task_manager()

        notifications = []

        async def callback(task: BackgroundTask):
            notifications.append(task.task_id)

        manager.register_notification_callback(callback)

        task = manager.create_task("agent_123", "Notify task")

        async def work():
            await asyncio.sleep(0.1)
            return "Done"

        await manager.run_task(task, work())
        await manager.wait_for_task(task.task_id, timeout=1.0)

        # Give notification time to fire
        await asyncio.sleep(0.1)

        assert task.task_id in notifications

    @pytest.mark.asyncio
    async def test_status_callback_receives_running_and_completed_updates(self):
        """状态回调应收到运行中与完成两次更新。"""
        manager = get_task_manager()

        statuses = []

        async def callback(task: BackgroundTask):
            statuses.append(task.status)

        manager.register_status_callback(callback)
        try:
            task = manager.create_task("agent_456", "Status task")

            async def work():
                await asyncio.sleep(0.1)
                return "Done"

            await manager.run_task(task, work())
            await manager.wait_for_task(task.task_id, timeout=1.0)
            await asyncio.sleep(0.1)
        finally:
            manager.unregister_status_callback(callback)

        assert TaskStatus.RUNNING in statuses
        assert TaskStatus.COMPLETED in statuses

class TestEnhancedAgentIntegration:
    """Test enhanced agent with fresh/fork modes."""

    @pytest.mark.asyncio
    async def test_fresh_mode_selection(self):
        """Test that fresh mode is selected for specific agent types."""
        from codo.team.subagent_context import should_use_fork_mode

        parent_ctx = {"agent_id": "parent"}

        # Specific agent type should use fresh
        assert should_use_fork_mode("Explore", parent_ctx) is False

    @pytest.mark.asyncio
    async def test_fork_mode_selection(self):
        """Test that fork mode is selected when no agent type specified."""
        from codo.team.subagent_context import should_use_fork_mode

        parent_ctx = {"agent_id": "parent"}

        # No agent type should use fork
        assert should_use_fork_mode(None, parent_ctx) is True

    def test_fork_restrictions_in_prompt(self):
        """Test that fork mode adds restrictions to system prompt."""
        parent_ctx = {
            "system_prompt": "Original prompt",
            "model": "claude-sonnet-4",
        }

        ctx = prepare_fork_context(parent_ctx, [], False)

        assert "Original prompt" in ctx.system_prompt
        assert "FORK MODE RESTRICTIONS" in ctx.system_prompt
        assert "NOT fork additional sub-agents" in ctx.system_prompt

    def test_fresh_mode_specialization(self):
        """Test that fresh mode creates specialized agents."""
        ctx = prepare_fresh_context(
            agent_type="Explore",
            system_prompt="Explore the codebase",
            tools=[],
            model="claude-haiku-4-5",
        )

        assert ctx.metadata["specialization"] == "Explore"
        assert ctx.agent_type == "Explore"

    @pytest.mark.asyncio
    async def test_foreground_subagent_emits_runtime_events(self):
        runtime = FakeRuntimeController()
        args = AgentToolInput(
            description="Search repository",
            prompt="Find auth flow",
            subagent_type="Explore",
        )
        context = {
            "api_client": AsyncMock(),
            "tools": [],
            "model": "claude-test",
            "cwd": "/tmp",
            "runtime_controller": runtime,
        }

        async def fake_run_sub_agent(**kwargs):
            event_callback = kwargs["event_callback"]
            await event_callback("agent_delta", {"content_delta": "Inspecting files", "status": "thinking"})
            return "Found auth middleware", {"total": 42, "input": 20, "output": 22}

        with patch("codo.tools.agent_tool.agent_tool._run_sub_agent", new=AsyncMock(side_effect=fake_run_sub_agent)):
            result = await run_subagent_with_mode(args=args, context=context, run_in_background=False)

        assert result["result"] == "Found auth middleware"
        event_types = [event_type for event_type, _ in runtime.events]
        assert "agent_started" in event_types
        assert "agent_delta" in event_types
        assert "agent_completed" in event_types

    @pytest.mark.asyncio
    async def test_background_subagent_emits_runtime_events(self):
        runtime = FakeRuntimeController()
        args = AgentToolInput(
            description="Search repository",
            prompt="Find auth flow",
            subagent_type="Explore",
            run_in_background=True,
        )
        context = {
            "api_client": AsyncMock(),
            "tools": [],
            "model": "claude-test",
            "cwd": "/tmp",
            "runtime_controller": runtime,
        }
        task_manager = get_task_manager()
        task_manager._tasks.clear()
        task_manager._running_tasks.clear()

        async def fake_run_sub_agent(**kwargs):
            event_callback = kwargs["event_callback"]
            await event_callback("agent_delta", {"content_delta": "Inspecting files", "status": "thinking"})
            return "Found auth middleware", {"total": 42, "input": 20, "output": 22}

        with patch("codo.tools.agent_tool.agent_tool._run_sub_agent", new=AsyncMock(side_effect=fake_run_sub_agent)):
            result = await run_subagent_with_mode(args=args, context=context, run_in_background=True)
            await task_manager.wait_for_task(result["task_id"], timeout=1.0)
            await asyncio.sleep(0.05)

        event_types = [event_type for event_type, _ in runtime.events]
        assert result["is_background"] is True
        assert "agent_started" in event_types
        assert "agent_delta" in event_types
        assert "agent_completed" in event_types
