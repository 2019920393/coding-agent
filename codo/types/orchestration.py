"""
工具编排类型定义

定义工具编排系统的核心类型：
- Batch: 批次类型（并发或串行）
- ToolExecutionTask: 工具执行任务
- ContextModifier: 上下文修改器
- ExecutionStatus: 执行状态
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, List
from datetime import datetime

class ExecutionStatus(str, Enum):
    """
    工具执行状态枚举

    状态转换流程：
    QUEUED → EXECUTING → COMPLETED
                      ↓
                   FAILED
    """
    QUEUED = "queued"        # 已加入队列，等待执行
    EXECUTING = "executing"  # 正在执行
    COMPLETED = "completed"  # 执行完成
    FAILED = "failed"        # 执行失败

@dataclass
class ContextModifier:
    """
    上下文修改器

    工具执行后可以返回修改器来改变执行上下文。
    例如：cd 命令修改工作目录，影响后续工具执行。

    Attributes:
        tool_use_id: 工具调用ID
        modify_fn: 修改函数，接收旧上下文返回新上下文
        description: 修改描述（用于日志）
    """
    tool_use_id: str
    modify_fn: Callable[[dict], dict]
    description: str = ""

    def apply(self, context: dict) -> dict:
        """
        应用修改器到上下文

        Args:
            context: 当前上下文

        Returns:
            修改后的上下文
        """
        return self.modify_fn(context)

@dataclass
class ToolExecutionTask:
    """
    工具执行任务

    封装单个工具的执行信息和状态。

    Attributes:
        tool_use_id: 工具调用ID（对应 ?? API 的 tool_use.id）
        tool_name: 工具名称
        tool_input: 工具输入参数
        is_concurrency_safe: 是否并发安全
        status: 执行状态
        result: 执行结果（完成后填充）
        error: 错误信息（失败时填充）
        context_modifier: 上下文修改器（可选）
        start_time: 开始时间
        end_time: 结束时间
    """
    tool_use_id: str
    tool_name: str
    tool_input: dict
    is_concurrency_safe: bool
    status: ExecutionStatus = ExecutionStatus.QUEUED
    result: Optional[Any] = None
    error: Optional[Exception] = None
    context_modifier: Optional[ContextModifier] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

    @property
    def duration(self) -> Optional[float]:
        """
        获取执行耗时（秒）

        Returns:
            耗时秒数，如果未完成返回 None
        """
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return None

    @property
    def is_completed(self) -> bool:
        """是否已完成（成功或失败）"""
        return self.status in (ExecutionStatus.COMPLETED, ExecutionStatus.FAILED)

    @property
    def is_success(self) -> bool:
        """是否执行成功"""
        return self.status == ExecutionStatus.COMPLETED and self.error is None

@dataclass
class Batch:
    """
    工具批次

    将工具调用分组为批次，每个批次内的工具可以并发或串行执行。

    分区规则：
    - 连续的并发安全工具 → 一个批次（并发执行）
    - 非并发安全工具 → 单独批次（串行执行）

    示例：
        输入: [Read(并发), Read(并发), Bash(非并发), Grep(并发)]
        分区: [[Read, Read], [Bash], [Grep]]
        执行: [并发批次] → [串行] → [串行]

    Attributes:
        is_concurrency_safe: 批次是否并发安全
        tasks: 批次内的任务列表
        batch_id: 批次ID（用于日志）
    """
    is_concurrency_safe: bool
    tasks: List[ToolExecutionTask] = field(default_factory=list)
    batch_id: Optional[str] = None

    @property
    def size(self) -> int:
        """批次大小（任务数量）"""
        return len(self.tasks)

    @property
    def is_empty(self) -> bool:
        """批次是否为空"""
        return len(self.tasks) == 0

    @property
    def all_completed(self) -> bool:
        """批次内所有任务是否已完成"""
        return all(task.is_completed for task in self.tasks)

    @property
    def has_failure(self) -> bool:
        """批次内是否有任务失败"""
        return any(task.status == ExecutionStatus.FAILED for task in self.tasks)

    def add_task(self, task: ToolExecutionTask) -> None:
        """
        添加任务到批次

        Args:
            task: 工具执行任务
        """
        self.tasks.append(task)

    def get_context_modifiers(self) -> List[ContextModifier]:
        """
        获取批次内所有上下文修改器

        Returns:
            修改器列表（按任务顺序）
        """
        return [
            task.context_modifier
            for task in self.tasks
            if task.context_modifier is not None
        ]

@dataclass
class OrchestrationResult:
    """
    编排执行结果

    封装整个批次执行的结果信息。

    Attributes:
        batches: 所有批次
        total_tasks: 总任务数
        completed_tasks: 完成任务数
        failed_tasks: 失败任务数
        total_duration: 总耗时（秒）
        context_modifiers: 所有上下文修改器
    """
    batches: List[Batch]
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    total_duration: float
    context_modifiers: List[ContextModifier] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        """
        成功率

        Returns:
            成功率（0.0 - 1.0）
        """
        if self.total_tasks == 0:
            return 1.0
        return (self.completed_tasks - self.failed_tasks) / self.total_tasks

    @property
    def is_all_success(self) -> bool:
        """是否全部成功"""
        return self.failed_tasks == 0 and self.completed_tasks == self.total_tasks
