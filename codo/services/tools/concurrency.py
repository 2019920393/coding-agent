"""
并发控制器

管理工具执行的并发控制，确保：
1. 并发安全工具可以并行执行
2. 非并发安全工具必须独占执行
3. 限制最大并发数

核心类：
- ConcurrencyController: 并发控制器
- ToolExecutionQueue: 执行队列
"""

import asyncio
import os
from typing import List, Optional, Set
from datetime import datetime

from codo.types.orchestration import ToolExecutionTask, ExecutionStatus

def get_max_concurrency() -> int:
    """
    获取最大并发数

    从环境变量 CODO_MAX_TOOL_CONCURRENCY 读取，默认 10。

    Returns:
        最大并发数
    """
    return int(os.environ.get('CODO_MAX_TOOL_CONCURRENCY', '10'))

class ConcurrencyController:
    """
    并发控制器

    管理工具执行的并发控制，实现以下规则：
    1. 并发安全工具可以与其他并发安全工具并行
    2. 非并发安全工具必须独占执行（等待所有其他工具完成）
    3. 限制最大并发数

    Attributes:
        max_concurrency: 最大并发数
        _executing_tasks: 正在执行的任务ID集合
        _executing_unsafe: 是否有非并发安全工具正在执行
    """

    def __init__(self, max_concurrency: Optional[int] = None):
        """
        初始化并发控制器

        Args:
            max_concurrency: 最大并发数，默认从环境变量读取
        """
        self.max_concurrency = max_concurrency or get_max_concurrency()
        self._executing_tasks: Set[str] = set()
        self._executing_unsafe: bool = False
        self._lock = asyncio.Lock()

    async def can_execute(self, task: ToolExecutionTask) -> bool:
        """
        检查任务是否可以执行

        规则：
        1. 如果有非并发安全工具正在执行，其他工具必须等待
        2. 如果任务是非并发安全的，必须等待所有其他工具完成
        3. 并发数不能超过最大限制

        Args:
            task: 工具执行任务

        Returns:
            是否可以执行
        """
        async with self._lock:
            # [Workflow] 规则1: 有非并发安全工具正在执行，其他工具必须等待
            if self._executing_unsafe:
                return False

            # [Workflow] 规则2: 任务是非并发安全的，必须等待所有其他工具完成
            if not task.is_concurrency_safe and len(self._executing_tasks) > 0:
                return False

            # [Workflow] 规则3: 并发数不能超过最大限制
            if len(self._executing_tasks) >= self.max_concurrency:
                return False

            return True

    async def acquire(self, task: ToolExecutionTask) -> None:
        """
        获取执行权限

        等待直到任务可以执行，然后标记为正在执行。

        Args:
            task: 工具执行任务
        """
        # [Workflow] 等待直到可以执行
        while not await self.can_execute(task):
            await asyncio.sleep(0.01)  # 10ms 轮询间隔

        # [Workflow] 标记为正在执行
        async with self._lock:
            self._executing_tasks.add(task.tool_use_id)
            if not task.is_concurrency_safe:
                self._executing_unsafe = True
            task.status = ExecutionStatus.EXECUTING
            task.start_time = datetime.now()

    async def release(self, task: ToolExecutionTask) -> None:
        """
        释放执行权限

        标记任务完成，释放并发槽位。

        Args:
            task: 工具执行任务
        """
        async with self._lock:
            self._executing_tasks.discard(task.tool_use_id)
            if not task.is_concurrency_safe:
                self._executing_unsafe = False
            task.end_time = datetime.now()

    @property
    def active_count(self) -> int:
        """当前正在执行的任务数"""
        return len(self._executing_tasks)

    @property
    def is_full(self) -> bool:
        """是否已达最大并发数"""
        return len(self._executing_tasks) >= self.max_concurrency

    @property
    def is_idle(self) -> bool:
        """是否空闲（没有任务执行）"""
        return len(self._executing_tasks) == 0

class ToolExecutionQueue:
    """
    工具执行队列

    管理工具执行任务的队列，支持：
    1. 添加任务到队列
    2. 获取下一个可执行任务
    3. 标记任务完成
    4. 查询队列状态

    Attributes:
        _tasks: 任务列表
        _controller: 并发控制器
    """

    def __init__(self, max_concurrency: Optional[int] = None):
        """
        初始化执行队列

        Args:
            max_concurrency: 最大并发数
        """
        self._tasks: List[ToolExecutionTask] = []
        self._controller = ConcurrencyController(max_concurrency)

    def add_task(self, task: ToolExecutionTask) -> None:
        """
        添加任务到队列

        Args:
            task: 工具执行任务
        """
        task.status = ExecutionStatus.QUEUED
        self._tasks.append(task)

    def add_tasks(self, tasks: List[ToolExecutionTask]) -> None:
        """
        批量添加任务到队列

        Args:
            tasks: 任务列表
        """
        for task in tasks:
            self.add_task(task)

    async def get_next_executable(self) -> Optional[ToolExecutionTask]:
        """
        获取下一个可执行任务

        按队列顺序查找第一个可以执行的任务。

        Returns:
            可执行任务，如果没有返回 None
        """
        for task in self._tasks:
            if task.status == ExecutionStatus.QUEUED:
                if await self._controller.can_execute(task):
                    return task
        return None

    async def acquire_task(self, task: ToolExecutionTask) -> None:
        """
        获取任务执行权限

        Args:
            task: 工具执行任务
        """
        await self._controller.acquire(task)

    async def release_task(self, task: ToolExecutionTask) -> None:
        """
        释放任务执行权限

        Args:
            task: 工具执行任务
        """
        await self._controller.release(task)

    def mark_completed(self, task: ToolExecutionTask, result: any = None) -> None:
        """
        标记任务完成

        Args:
            task: 工具执行任务
            result: 执行结果
        """
        task.status = ExecutionStatus.COMPLETED
        task.result = result

    def mark_failed(self, task: ToolExecutionTask, error: Exception) -> None:
        """
        标记任务失败

        Args:
            task: 工具执行任务
            error: 错误信息
        """
        task.status = ExecutionStatus.FAILED
        task.error = error

    def get_queued_tasks(self) -> List[ToolExecutionTask]:
        """
        获取所有排队中的任务

        Returns:
            排队任务列表
        """
        return [t for t in self._tasks if t.status == ExecutionStatus.QUEUED]

    def get_executing_tasks(self) -> List[ToolExecutionTask]:
        """
        获取所有正在执行的任务

        Returns:
            执行中任务列表
        """
        return [t for t in self._tasks if t.status == ExecutionStatus.EXECUTING]

    def get_completed_tasks(self) -> List[ToolExecutionTask]:
        """
        获取所有已完成的任务（成功或失败）

        Returns:
            已完成任务列表
        """
        return [t for t in self._tasks if t.is_completed]

    @property
    def all_completed(self) -> bool:
        """是否所有任务都已完成"""
        return all(task.is_completed for task in self._tasks)

    @property
    def has_queued(self) -> bool:
        """是否有排队中的任务"""
        return any(task.status == ExecutionStatus.QUEUED for task in self._tasks)

    @property
    def has_executing(self) -> bool:
        """是否有正在执行的任务"""
        return any(task.status == ExecutionStatus.EXECUTING for task in self._tasks)

    @property
    def total_count(self) -> int:
        """总任务数"""
        return len(self._tasks)

    @property
    def completed_count(self) -> int:
        """已完成任务数"""
        return len(self.get_completed_tasks())

    @property
    def failed_count(self) -> int:
        """失败任务数"""
        return len([t for t in self._tasks if t.status == ExecutionStatus.FAILED])
