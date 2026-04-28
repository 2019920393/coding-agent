"""
错误处理和回滚机制

"""

import logging
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

class ErrorSeverity(str, Enum):
    """错误严重程度"""
    WARNING = "warning"      # 警告，不影响继续执行
    ERROR = "error"          # 错误，影响当前工具但不影响其他工具
    CRITICAL = "critical"    # 严重错误，需要中止所有并发工具

@dataclass
class ToolError:
    """
    工具错误

    Attributes:
        tool_use_id: 工具调用 ID
        tool_name: 工具名称
        error_type: 错误类型
        error_message: 错误消息
        severity: 错误严重程度
        stack_trace: 堆栈跟踪
        context: 错误上下文
        timestamp: 时间戳
    """
    tool_use_id: str
    tool_name: str
    error_type: str
    error_message: str
    severity: ErrorSeverity = ErrorSeverity.ERROR
    stack_trace: Optional[str] = None
    context: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=lambda: __import__('time').time())

@dataclass
class RollbackAction:
    """
    回滚操作

    Attributes:
        action_id: 操作 ID
        description: 操作描述
        rollback_fn: 回滚函数
        executed: 是否已执行
    """
    action_id: str
    description: str
    rollback_fn: Callable[[], None]
    executed: bool = False

class ErrorHandler:
    """
    错误处理器

    管理工具执行期间的错误处理，支持：
    - 错误分类和严重程度判断
    - Sibling abort（并发工具取消）
    - 错误恢复策略
    - 错误日志和报告
    """

    def __init__(self):
        """初始化错误处理器"""
        self._errors: List[ToolError] = []
        self._error_callbacks: List[Callable[[ToolError], None]] = []

    def register_error_callback(
        self,
        callback: Callable[[ToolError], None]
    ) -> None:
        """
        注册错误回调

        Args:
            callback: 错误回调函数
        """
        self._error_callbacks.append(callback)

    def handle_error(
        self,
        tool_use_id: str,
        tool_name: str,
        error: Exception,
        context: Optional[Dict[str, Any]] = None
    ) -> ToolError:
        """
        处理工具错误

        Args:
            tool_use_id: 工具调用 ID
            tool_name: 工具名称
            error: 异常对象
            context: 错误上下文

        Returns:
            ToolError 对象
        """
        # 确定错误严重程度
        severity = self._determine_severity(tool_name, error)

        # 创建错误对象
        tool_error = ToolError(
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            error_type=type(error).__name__,
            error_message=str(error),
            severity=severity,
            stack_trace=self._get_stack_trace(error),
            context=context or {},
        )

        # 记录错误
        self._errors.append(tool_error)
        logger.error(
            f"Tool error: {tool_name} ({tool_use_id}), "
            f"severity={severity}, error={tool_error.error_message}"
        )

        # 调用回调
        for callback in self._error_callbacks:
            try:
                callback(tool_error)
            except Exception as e:
                logger.error(f"Error callback failed: {e}")

        return tool_error

    def _determine_severity(
        self,
        tool_name: str,
        error: Exception
    ) -> ErrorSeverity:
        """
        确定错误严重程度

        Args:
            tool_name: 工具名称
            error: 异常对象

        Returns:
            错误严重程度
        """
        # Bash 工具错误通常是 CRITICAL（触发 sibling abort）
        if tool_name == "Bash":
            return ErrorSeverity.CRITICAL

        # 权限错误
        if "permission" in str(error).lower():
            return ErrorSeverity.ERROR

        # 文件不存在等常见错误
        if isinstance(error, (FileNotFoundError, ValueError)):
            return ErrorSeverity.ERROR

        # 默认为 ERROR
        return ErrorSeverity.ERROR

    def _get_stack_trace(self, error: Exception) -> Optional[str]:
        """
        获取堆栈跟踪

        Args:
            error: 异常对象

        Returns:
            堆栈跟踪字符串
        """
        import traceback
        return "".join(traceback.format_exception(type(error), error, error.__traceback__))

    def should_abort_siblings(self, tool_error: ToolError) -> bool:
        """
        判断是否应该中止并发工具

        Args:
            tool_error: 工具错误

        Returns:
            是否应该中止
        """
        return tool_error.severity == ErrorSeverity.CRITICAL

    def get_errors(
        self,
        tool_use_id: Optional[str] = None,
        severity: Optional[ErrorSeverity] = None
    ) -> List[ToolError]:
        """
        获取错误列表

        Args:
            tool_use_id: 过滤特定工具，None 表示所有
            severity: 过滤特定严重程度，None 表示所有

        Returns:
            错误列表
        """
        errors = self._errors

        if tool_use_id:
            errors = [e for e in errors if e.tool_use_id == tool_use_id]

        if severity:
            errors = [e for e in errors if e.severity == severity]

        return errors

    def has_critical_errors(self) -> bool:
        """
        检查是否有严重错误

        Returns:
            是否有严重错误
        """
        return any(e.severity == ErrorSeverity.CRITICAL for e in self._errors)

    def clear_errors(self, tool_use_id: Optional[str] = None) -> None:
        """
        清除错误记录

        Args:
            tool_use_id: 工具调用 ID，None 表示清除所有
        """
        if tool_use_id:
            self._errors = [e for e in self._errors if e.tool_use_id != tool_use_id]
        else:
            self._errors.clear()

class RollbackManager:
    """
    回滚管理器

    管理工具执行的回滚操作，支持：
    - 注册回滚操作
    - 执行回滚
    - 回滚历史
    """

    def __init__(self):
        """初始化回滚管理器"""
        self._actions: Dict[str, List[RollbackAction]] = {}

    def register_rollback(
        self,
        tool_use_id: str,
        action_id: str,
        description: str,
        rollback_fn: Callable[[], None]
    ) -> None:
        """
        注册回滚操作

        Args:
            tool_use_id: 工具调用 ID
            action_id: 操作 ID
            description: 操作描述
            rollback_fn: 回滚函数
        """
        if tool_use_id not in self._actions:
            self._actions[tool_use_id] = []

        action = RollbackAction(
            action_id=action_id,
            description=description,
            rollback_fn=rollback_fn,
        )

        self._actions[tool_use_id].append(action)
        logger.debug(f"Registered rollback: {tool_use_id}/{action_id}")

    def rollback(self, tool_use_id: str) -> int:
        """
        执行回滚

        Args:
            tool_use_id: 工具调用 ID

        Returns:
            执行的回滚操作数量
        """
        actions = self._actions.get(tool_use_id, [])
        count = 0

        # 逆序执行回滚（后进先出）
        for action in reversed(actions):
            if not action.executed:
                try:
                    logger.info(f"Rolling back: {action.description}")
                    action.rollback_fn()
                    action.executed = True
                    count += 1
                except Exception as e:
                    logger.error(f"Rollback failed: {action.description}, error={e}")

        return count

    def clear_rollbacks(self, tool_use_id: str) -> None:
        """
        清除回滚操作

        Args:
            tool_use_id: 工具调用 ID
        """
        self._actions.pop(tool_use_id, None)

    def get_rollback_actions(self, tool_use_id: str) -> List[RollbackAction]:
        """
        获取回滚操作列表

        Args:
            tool_use_id: 工具调用 ID

        Returns:
            回滚操作列表
        """
        return self._actions.get(tool_use_id, [])

# 全局实例
_global_error_handler: Optional[ErrorHandler] = None
_global_rollback_manager: Optional[RollbackManager] = None

def get_error_handler() -> ErrorHandler:
    """获取全局错误处理器"""
    global _global_error_handler
    if _global_error_handler is None:
        _global_error_handler = ErrorHandler()
    return _global_error_handler

def get_rollback_manager() -> RollbackManager:
    """获取全局回滚管理器"""
    global _global_rollback_manager
    if _global_rollback_manager is None:
        _global_rollback_manager = RollbackManager()
    return _global_rollback_manager
