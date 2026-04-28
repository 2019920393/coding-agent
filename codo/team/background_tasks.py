"""Background task execution system for sub-agents.

- 后台任务通知机制
- 任务状态管理
- 结果回流
"""

import asyncio
import time
import uuid
from typing import Optional, Dict, Any, Callable, Awaitable
from dataclasses import dataclass, field
from enum import Enum

class TaskStatus(str, Enum):
    """Background task status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

@dataclass
class BackgroundTask:
    """Background task metadata.

    Attributes:
        task_id: Unique task identifier
        agent_id: Agent ID running this task
        description: Task description
        status: Current status
        created_at: Creation timestamp
        started_at: Start timestamp
        completed_at: Completion timestamp
        result: Task result (when completed)
        error: Error message (when failed)
        output_file: Path to output file
    """
    task_id: str
    agent_id: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Optional[Any] = None
    error: Optional[str] = None
    output_file: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    current_action: Optional[str] = None

class BackgroundTaskManager:
    """Manages background task execution and notifications."""

    def __init__(self):
        """Initialize the background task manager."""
        self._tasks: Dict[str, BackgroundTask] = {}
        self._running_tasks: Dict[str, asyncio.Task] = {}
        self._notification_callbacks: list[Callable[[BackgroundTask], Awaitable[None]]] = []
        self._status_callbacks: list[Callable[[BackgroundTask], Awaitable[None]]] = []

    def create_task(
        self,
        agent_id: str,
        description: str,
        output_file: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> BackgroundTask:
        """
        Create a new background task.

        Args:
            agent_id: Agent ID
            description: Task description
            output_file: Optional output file path

        Returns:
            Created BackgroundTask
        """
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        task = BackgroundTask(
            task_id=task_id,
            agent_id=agent_id,
            description=description,
            output_file=output_file,
            metadata=metadata or {},
        )
        self._tasks[task_id] = task
        return task

    async def run_task(
        self,
        task: BackgroundTask,
        coro: Awaitable[Any],
    ) -> None:
        """
        Run a background task.

        Args:
            task: BackgroundTask to run
            coro: Coroutine to execute
        """
        task.status = TaskStatus.RUNNING
        task.started_at = time.time()

        async_task = asyncio.create_task(self._execute_task(task, coro))
        self._running_tasks[task.task_id] = async_task
        await self._notify_status_update(task)

    async def _execute_task(
        self,
        task: BackgroundTask,
        coro: Awaitable[Any],
    ) -> None:
        """
        Execute a background task and handle completion.

        Args:
            task: BackgroundTask
            coro: Coroutine to execute
        """
        try:
            result = await coro
            task.result = result
            task.status = TaskStatus.COMPLETED
            task.completed_at = time.time()
        except asyncio.CancelledError:
            task.status = TaskStatus.CANCELLED
            task.completed_at = time.time()
        except Exception as e:
            task.error = str(e)
            task.status = TaskStatus.FAILED
            task.completed_at = time.time()
        finally:
            # Remove from running tasks
            self._running_tasks.pop(task.task_id, None)

            # Notify status change first so UIs can refresh task cards in place
            await self._notify_status_update(task)

            # Notify completion
            await self._notify_task_completion(task)

    async def _notify_status_update(self, task: BackgroundTask) -> None:
        """
        Notify registered listeners whenever task status changes.

        Args:
            task: Task with latest status
        """
        for callback in list(self._status_callbacks):
            try:
                await callback(task)
            except Exception as e:
                print(f"Error in task status callback: {e}")

    async def _notify_task_completion(self, task: BackgroundTask) -> None:
        """
        Notify all registered callbacks about task completion.

        Args:
            task: Completed task
        """
        for callback in self._notification_callbacks:
            try:
                await callback(task)
            except Exception as e:
                # Log but don't fail on notification errors
                print(f"Error in task notification callback: {e}")

    def register_notification_callback(
        self,
        callback: Callable[[BackgroundTask], Awaitable[None]]
    ) -> None:
        """
        Register a callback for task completion notifications.

        Args:
            callback: Async callback function
        """
        self._notification_callbacks.append(callback)

    def unregister_notification_callback(
        self,
        callback: Callable[[BackgroundTask], Awaitable[None]]
    ) -> None:
        """Unregister a completion notification callback."""
        if callback in self._notification_callbacks:
            self._notification_callbacks.remove(callback)

    def register_status_callback(
        self,
        callback: Callable[[BackgroundTask], Awaitable[None]]
    ) -> None:
        """
        Register a callback for task lifecycle status updates.

        Args:
            callback: Async callback function
        """
        self._status_callbacks.append(callback)

    def unregister_status_callback(
        self,
        callback: Callable[[BackgroundTask], Awaitable[None]]
    ) -> None:
        """Unregister a task lifecycle status callback."""
        if callback in self._status_callbacks:
            self._status_callbacks.remove(callback)

    def get_task(self, task_id: str) -> Optional[BackgroundTask]:
        """
        Get task by ID.

        Args:
            task_id: Task identifier

        Returns:
            BackgroundTask or None
        """
        return self._tasks.get(task_id)

    def get_all_tasks(self) -> list[BackgroundTask]:
        """
        Get all tasks.

        Returns:
            List of all tasks
        """
        return list(self._tasks.values())

    def get_running_tasks(self) -> list[BackgroundTask]:
        """
        Get all running tasks.

        Returns:
            List of running tasks
        """
        return [
            task for task in self._tasks.values()
            if task.status == TaskStatus.RUNNING
        ]

    async def update_task_action(self, task_id: str, current_action: str) -> bool:
        """Update a task's current action and notify listeners."""
        task = self._tasks.get(task_id)
        if task is None:
            return False
        task.current_action = current_action
        await self._notify_status_update(task)
        return True

    async def cancel_task(self, task_id: str) -> bool:
        """
        Cancel a running task.

        Args:
            task_id: Task identifier

        Returns:
            True if cancelled, False if not found or not running
        """
        async_task = self._running_tasks.get(task_id)
        if not async_task:
            return False

        async_task.cancel()
        return True

    async def wait_for_task(
        self,
        task_id: str,
        timeout: Optional[float] = None
    ) -> Optional[BackgroundTask]:
        """
        Wait for a task to complete.

        Args:
            task_id: Task identifier
            timeout: Optional timeout in seconds

        Returns:
            Completed task or None if timeout
        """
        task = self._tasks.get(task_id)
        if not task:
            return None

        # Already completed
        if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            return task

        # Wait for completion
        async_task = self._running_tasks.get(task_id)
        if not async_task:
            return task

        try:
            await asyncio.wait_for(async_task, timeout=timeout)
        except asyncio.TimeoutError:
            return None

        return task

# Global background task manager instance
_global_task_manager: Optional[BackgroundTaskManager] = None

def get_task_manager() -> BackgroundTaskManager:
    """
    Get the global background task manager.

    Returns:
        BackgroundTaskManager instance
    """
    global _global_task_manager
    if _global_task_manager is None:
        _global_task_manager = BackgroundTaskManager()
    return _global_task_manager
