"""
工具错误处理模块

本模块负责工具执行错误的分类、格式化和处理。
"""

import traceback
from typing import Optional, Any
from enum import Enum

class ToolErrorType(Enum):
    """
    工具错误类型

    """
    ABORT = "AbortError"              # 用户中止
    VALIDATION = "ValidationError"     # 输入验证错误
    PERMISSION = "PermissionError"     # 权限错误
    FILE_NOT_FOUND = "FileNotFoundError"  # 文件不存在
    PERMISSION_DENIED = "PermissionDenied"  # 权限拒绝
    TIMEOUT = "TimeoutError"          # 超时
    NETWORK = "NetworkError"          # 网络错误
    UNKNOWN = "Error"                 # 未知错误

def classify_tool_error(error: Exception) -> str:
    """
    分类工具错误

    [Workflow]
    1. 检查错误类型
    2. 返回错误分类字符串

    Args:
        error: 异常对象

    Returns:
        str: 错误分类

    Examples:
        >>> classify_tool_error(FileNotFoundError("file.txt"))
        'FileNotFoundError'
        >>> classify_tool_error(PermissionError("access denied"))
        'PermissionDenied'
    """
    # 检查特定错误类型
    if isinstance(error, KeyboardInterrupt):
        return ToolErrorType.ABORT.value
    elif isinstance(error, FileNotFoundError):
        return ToolErrorType.FILE_NOT_FOUND.value
    elif isinstance(error, PermissionError):
        return ToolErrorType.PERMISSION_DENIED.value
    elif isinstance(error, TimeoutError):
        return ToolErrorType.TIMEOUT.value
    elif isinstance(error, (ConnectionError, OSError)):
        return ToolErrorType.NETWORK.value
    elif hasattr(error, '__class__'):
        # 返回错误类名
        return error.__class__.__name__
    else:
        return ToolErrorType.UNKNOWN.value

def format_tool_error(error: Exception, include_traceback: bool = False) -> str:
    """
    格式化工具错误

    [Workflow]
    1. 检查错误类型
    2. 提取错误信息
    3. 格式化为用户友好的字符串
    4. 如果超过10000字符，截断中间部分

    Args:
        error: 异常对象
        include_traceback: 是否包含堆栈跟踪

    Returns:
        str: 格式化的错误信息

    Examples:
        >>> format_tool_error(FileNotFoundError("file.txt not found"))
        'FileNotFoundError: file.txt not found'
    """
    # 步骤 1: 检查中止错误
    if isinstance(error, KeyboardInterrupt):
        return "Tool execution was interrupted by user"

    # 步骤 2: 提取错误信息
    error_parts = []

    # 添加错误类型和消息
    error_type = error.__class__.__name__
    error_message = str(error)

    if error_message:
        error_parts.append(f"{error_type}: {error_message}")
    else:
        error_parts.append(error_type)

    # 添加堆栈跟踪（如果需要）
    if include_traceback:
        tb = traceback.format_exception(type(error), error, error.__traceback__)
        error_parts.append('\n'.join(tb))

    # 步骤 3: 合并错误信息
    full_message = '\n'.join(error_parts).strip()

    if not full_message:
        full_message = "Command failed with no output"

    # 步骤 4: 截断长消息（超过10000字符）

    if len(full_message) <= 10000:
        return full_message

    # 截断中间部分，保留前5000和后5000字符
    half_length = 5000
    start = full_message[:half_length]
    end = full_message[-half_length:]
    truncated_length = len(full_message) - 10000

    return f"{start}\n\n... [{truncated_length} characters truncated] ...\n\n{end}"

def format_tool_result_error(
    tool_name: str,
    error: Exception,
    input_data: Optional[Any] = None
) -> str:
    """
    格式化工具结果错误

    [Workflow]
    1. 格式化错误信息
    2. 添加工具名称和输入信息
    3. 返回完整的错误消息

    Args:
        tool_name: 工具名称
        error: 异常对象
        input_data: 输入数据（可选）

    Returns:
        str: 格式化的错误消息
    """
    # 格式化基本错误
    error_message = format_tool_error(error)

    # 添加工具名称
    lines = [f"Tool '{tool_name}' failed:"]
    lines.append(error_message)

    # 添加输入信息（如果提供）
    if input_data is not None:
        lines.append(f"\nInput: {input_data}")

    return '\n'.join(lines)

def is_retriable_error(error: Exception) -> bool:
    """
    判断错误是否可重试

    [Workflow]
    1. 检查错误类型
    2. 返回是否可重试

    Args:
        error: 异常对象

    Returns:
        bool: 是否可重试

    Examples:
        >>> is_retriable_error(TimeoutError())
        True
        >>> is_retriable_error(FileNotFoundError())
        False
    """
    # 可重试的错误类型
    retriable_types = (
        TimeoutError,
        ConnectionError,
        OSError,
    )

    return isinstance(error, retriable_types)

def get_error_severity(error: Exception) -> str:
    """
    获取错误严重程度

    [Workflow]
    1. 根据错误类型判断严重程度
    2. 返回严重程度字符串

    Args:
        error: 异常对象

    Returns:
        str: 严重程度 ('low', 'medium', 'high', 'critical')
    """
    # 低严重程度：可恢复的错误
    if isinstance(error, (TimeoutError, ConnectionError)):
        return 'low'

    # 中等严重程度：常见错误
    if isinstance(error, (FileNotFoundError, ValueError, TypeError)):
        return 'medium'

    # 高严重程度：权限和系统错误
    if isinstance(error, (PermissionError, OSError)):
        return 'high'

    # 严重：中止和未知错误
    if isinstance(error, (KeyboardInterrupt, SystemExit)):
        return 'critical'

    # 默认：中等
    return 'medium'

class ToolExecutionError(Exception):
    """
    工具执行错误基类

    [Workflow]
    用于包装工具执行过程中的错误，提供额外的上下文信息。
    """

    def __init__(
        self,
        message: str,
        tool_name: str,
        error_type: str,
        original_error: Optional[Exception] = None
    ):
        super().__init__(message)
        self.tool_name = tool_name
        self.error_type = error_type
        self.original_error = original_error

    def __str__(self) -> str:
        return f"[{self.tool_name}] {self.error_type}: {super().__str__()}"

class ToolValidationError(ToolExecutionError):
    """
    工具验证错误

    用于输入验证失败的情况。
    """

    def __init__(self, message: str, tool_name: str, original_error: Optional[Exception] = None):
        super().__init__(message, tool_name, "ValidationError", original_error)

class ToolPermissionError(ToolExecutionError):
    """
    工具权限错误

    用于权限检查失败的情况。
    """

    def __init__(self, message: str, tool_name: str, original_error: Optional[Exception] = None):
        super().__init__(message, tool_name, "PermissionError", original_error)

class ToolTimeoutError(ToolExecutionError):
    """
    工具超时错误

    用于工具执行超时的情况。
    """

    def __init__(self, message: str, tool_name: str, original_error: Optional[Exception] = None):
        super().__init__(message, tool_name, "TimeoutError", original_error)
