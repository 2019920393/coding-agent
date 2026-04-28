"""
进度报告系统

"""

import time
import logging
from typing import Dict, Any, Optional, Callable, List
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

class ProgressType(str, Enum):
    """进度类型"""
    STARTED = "started"
    PROGRESS = "progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

@dataclass
class ProgressUpdate:
    """
    进度更新

    Attributes:
        tool_use_id: 工具调用 ID
        tool_name: 工具名称
        progress_type: 进度类型
        message: 进度消息
        percentage: 完成百分比 (0-100)
        timestamp: 时间戳
        metadata: 额外元数据
    """
    tool_use_id: str
    tool_name: str
    progress_type: ProgressType
    message: str
    percentage: Optional[float] = None
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

class ProgressReporter:
    """
    进度报告器

    管理工具执行期间的进度报告，支持：
    - 报告工具开始/进行中/完成状态
    - 百分比进度
    - 自定义进度消息
    - 进度回调
    """

    def __init__(self):
        """初始化进度报告器"""
        self._callbacks: List[Callable[[ProgressUpdate], None]] = []
        self._progress_history: Dict[str, List[ProgressUpdate]] = {}

    def register_callback(self, callback: Callable[[ProgressUpdate], None]) -> None:
        """
        注册进度回调

        Args:
            callback: 进度回调函数
        """
        self._callbacks.append(callback)

    def report_started(
        self,
        tool_use_id: str,
        tool_name: str,
        message: Optional[str] = None
    ) -> None:
        """
        报告工具开始执行

        Args:
            tool_use_id: 工具调用 ID
            tool_name: 工具名称
            message: 自定义消息
        """
        update = ProgressUpdate(
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            progress_type=ProgressType.STARTED,
            message=message or f"Starting {tool_name}",
            percentage=0.0,
        )
        self._emit_progress(update)

    def report_progress(
        self,
        tool_use_id: str,
        tool_name: str,
        message: str,
        percentage: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        报告工具执行进度

        Args:
            tool_use_id: 工具调用 ID
            tool_name: 工具名称
            message: 进度消息
            percentage: 完成百分比
            metadata: 额外元数据
        """
        update = ProgressUpdate(
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            progress_type=ProgressType.PROGRESS,
            message=message,
            percentage=percentage,
            metadata=metadata or {},
        )
        self._emit_progress(update)

    def report_completed(
        self,
        tool_use_id: str,
        tool_name: str,
        message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        报告工具完成

        Args:
            tool_use_id: 工具调用 ID
            tool_name: 工具名称
            message: 完成消息
            metadata: 额外元数据
        """
        update = ProgressUpdate(
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            progress_type=ProgressType.COMPLETED,
            message=message or f"{tool_name} completed",
            percentage=100.0,
            metadata=metadata or {},
        )
        self._emit_progress(update)

    def report_failed(
        self,
        tool_use_id: str,
        tool_name: str,
        error: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        报告工具失败

        Args:
            tool_use_id: 工具调用 ID
            tool_name: 工具名称
            error: 错误消息
            metadata: 额外元数据
        """
        update = ProgressUpdate(
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            progress_type=ProgressType.FAILED,
            message=f"{tool_name} failed: {error}",
            metadata=metadata or {},
        )
        self._emit_progress(update)

    def report_cancelled(
        self,
        tool_use_id: str,
        tool_name: str,
        reason: Optional[str] = None
    ) -> None:
        """
        报告工具取消

        Args:
            tool_use_id: 工具调用 ID
            tool_name: 工具名称
            reason: 取消原因
        """
        update = ProgressUpdate(
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            progress_type=ProgressType.CANCELLED,
            message=reason or f"{tool_name} cancelled",
        )
        self._emit_progress(update)

    def _emit_progress(self, update: ProgressUpdate) -> None:
        """
        发送进度更新

        Args:
            update: 进度更新
        """
        # 记录历史
        if update.tool_use_id not in self._progress_history:
            self._progress_history[update.tool_use_id] = []
        self._progress_history[update.tool_use_id].append(update)

        # 调用回调
        for callback in self._callbacks:
            try:
                callback(update)
            except Exception as e:
                logger.error(f"Progress callback error: {e}")

    def get_progress_history(self, tool_use_id: str) -> List[ProgressUpdate]:
        """
        获取工具的进度历史

        Args:
            tool_use_id: 工具调用 ID

        Returns:
            进度更新列表
        """
        return self._progress_history.get(tool_use_id, [])

    def get_latest_progress(self, tool_use_id: str) -> Optional[ProgressUpdate]:
        """
        获取工具的最新进度

        Args:
            tool_use_id: 工具调用 ID

        Returns:
            最新进度更新
        """
        history = self._progress_history.get(tool_use_id, [])
        return history[-1] if history else None

    def clear_history(self, tool_use_id: Optional[str] = None) -> None:
        """
        清除进度历史

        Args:
            tool_use_id: 工具调用 ID，如果为 None 则清除所有
        """
        if tool_use_id:
            self._progress_history.pop(tool_use_id, None)
        else:
            self._progress_history.clear()

# 全局进度报告器实例
_global_reporter: Optional[ProgressReporter] = None

def get_progress_reporter() -> ProgressReporter:
    """
    获取全局进度报告器

    Returns:
        ProgressReporter 实例
    """
    global _global_reporter
    if _global_reporter is None:
        _global_reporter = ProgressReporter()
    return _global_reporter
