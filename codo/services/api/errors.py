"""
API 错误分类和重试逻辑

将 API 错误分类并实现指数退避重试。
关键错误类别：
- overloaded (529): 临时性错误，使用退避重试
- rate_limit (429): 临时性错误，使用退避重试 + 响应头延迟
- prompt_too_long: 需要压缩，仅重试无法解决
- auth_error (401/403): 致命错误，不可重试
- connection_error: 临时性错误，可重试
- server_error (500+): 临时性错误，使用退避重试
"""

import asyncio
import logging
import re
from enum import Enum
from typing import Any, Optional

from anthropic import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)

logger = logging.getLogger(__name__)

class APIErrorCategory(Enum):
    """API 错误分类"""
    OVERLOADED = "overloaded"           # 529 - 服务器过载
    RATE_LIMITED = "rate_limited"       # 429 - 速率限制
    PROMPT_TOO_LONG = "prompt_too_long" # 400 - 提示词超过上下文窗口
    AUTH_ERROR = "auth_error"           # 401/403 - 认证失败
    CONNECTION_ERROR = "connection"      # 网络连接问题
    TIMEOUT = "timeout"                 # 请求超时
    SERVER_ERROR = "server_error"       # 500+ - 内部服务器错误
    BAD_REQUEST = "bad_request"         # 400 - 其他错误请求
    UNKNOWN = "unknown"                 # 未分类

# 指数退避的基础延迟
BASE_DELAY_MS = 500

# 不同错误类别的最大重试次数
MAX_RETRIES = 5
MAX_529_RETRIES = 3

# 提示词过长的匹配模式
PROMPT_TOO_LONG_PATTERN = re.compile(
    r"prompt is too long[^0-9]*(\d+)\s*tokens?\s*>\s*(\d+)",
    re.IGNORECASE,
)

def classify_api_error(error: Exception) -> APIErrorCategory:
    """
    将 API 错误分类到某个类别

    - categorizeRetryableAPIError()
    """
    if isinstance(error, APITimeoutError):
        return APIErrorCategory.TIMEOUT

    if isinstance(error, APIConnectionError):
        return APIErrorCategory.CONNECTION_ERROR

    if isinstance(error, AuthenticationError):
        return APIErrorCategory.AUTH_ERROR

    if isinstance(error, PermissionDeniedError):
        return APIErrorCategory.AUTH_ERROR

    if isinstance(error, RateLimitError):
        return APIErrorCategory.RATE_LIMITED

    if isinstance(error, BadRequestError):
        # 检查是否是 prompt_too_long
        error_msg = str(error)
        if is_prompt_too_long_error(error_msg):
            return APIErrorCategory.PROMPT_TOO_LONG
        return APIErrorCategory.BAD_REQUEST

    if isinstance(error, InternalServerError):
        return APIErrorCategory.SERVER_ERROR

    if isinstance(error, APIStatusError):
        status = error.status_code
        if status == 529:
            return APIErrorCategory.OVERLOADED
        if status == 429:
            return APIErrorCategory.RATE_LIMITED
        if status in (401, 403):
            return APIErrorCategory.AUTH_ERROR
        if status >= 500:
            return APIErrorCategory.SERVER_ERROR
        if status == 400:
            error_msg = str(error)
            if is_prompt_too_long_error(error_msg):
                return APIErrorCategory.PROMPT_TOO_LONG
            return APIErrorCategory.BAD_REQUEST

    if isinstance(error, APIError):
        return APIErrorCategory.SERVER_ERROR

    return APIErrorCategory.UNKNOWN

def is_retryable(category: APIErrorCategory) -> bool:
    """检查错误类别是否可重试"""
    return category in (
        APIErrorCategory.OVERLOADED,
        APIErrorCategory.RATE_LIMITED,
        APIErrorCategory.CONNECTION_ERROR,
        APIErrorCategory.TIMEOUT,
        APIErrorCategory.SERVER_ERROR,
    )

def is_prompt_too_long_error(error_msg: str) -> bool:
    """
    检查错误消息是否表示提示词过长

    - isPromptTooLongMessage()
    """
    lower = error_msg.lower()
    return (
        "prompt is too long" in lower
        or "prompt_too_long" in lower
        or "content_length_exceeds" in lower
        or "context length" in lower
    )

def parse_prompt_too_long_tokens(error_msg: str) -> tuple:
    """
    从提示词过长错误中解析实际/限制的 token 数量

    返回 (actual_tokens, limit_tokens) 或 (None, None)
    """
    match = PROMPT_TOO_LONG_PATTERN.search(error_msg)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None, None

def get_retry_delay(attempt: int, category: APIErrorCategory) -> float:
    """
    使用指数退避计算重试延迟

    - getRetryDelay()

    返回延迟秒数
    """
    base_delay_s = BASE_DELAY_MS / 1000.0

    if category == APIErrorCategory.RATE_LIMITED:
        # 速率限制使用更长的延迟
        return min(base_delay_s * (2 ** attempt), 60.0)

    if category == APIErrorCategory.OVERLOADED:
        # 过载使用中等延迟
        return min(base_delay_s * (2 ** attempt), 30.0)

    # 默认指数退避
    return min(base_delay_s * (2 ** attempt), 15.0)

def format_api_error(error: Exception) -> str:
    """
    格式化 API 错误以显示给用户
    """
    category = classify_api_error(error)

    if category == APIErrorCategory.PROMPT_TOO_LONG:
        actual, limit = parse_prompt_too_long_tokens(str(error))
        if actual and limit:
            return (
                f"提示词过长 ({actual:,} tokens > {limit:,} 最大值)。"
                "使用 /compact 压缩对话。"
            )
        return "提示词过长。使用 /compact 压缩对话。"

    if category == APIErrorCategory.AUTH_ERROR:
        return f"认证错误: {error}。请检查你的 API 密钥。"

    if category == APIErrorCategory.RATE_LIMITED:
        return "API 速率限制。自动重试中..."

    if category == APIErrorCategory.OVERLOADED:
        return "API 过载。自动重试中..."

    if category == APIErrorCategory.CONNECTION_ERROR:
        return f"连接错误: {error}。请检查你的网络。"

    if category == APIErrorCategory.TIMEOUT:
        return "请求超时。重试中..."

    return f"API 错误: {error}"

async def with_retry(operation, max_retries: int = MAX_RETRIES) -> Any:
    """
    使用重试逻辑执行异步操作

    - withRetry()

    简化为个人使用：
    - 无流式回退
    - 无快速模式切换
    - 无持久重试模式
    - 简单的指数退避

    Args:
        operation: 要执行的异步可调用对象
        max_retries: 最大重试次数

    Returns:
        操作的结果

    Raises:
        如果所有重试都失败则抛出最后一个错误，或立即抛出不可重试的错误
    """
    last_error = None
    consecutive_529 = 0

    for attempt in range(max_retries + 1):
        try:
            return await operation()

        except Exception as e:
            last_error = e
            category = classify_api_error(e)

            # 不可重试的错误：立即抛出
            if not is_retryable(category):
                logger.error(f"不可重试的 API 错误 ({category.value}): {e}")
                raise

            # 529 熔断器
            if category == APIErrorCategory.OVERLOADED:
                consecutive_529 += 1
                if consecutive_529 > MAX_529_RETRIES:
                    logger.error(
                        f"529 错误过多 ({consecutive_529})，放弃重试"
                    )
                    raise
            else:
                consecutive_529 = 0

            # 最后一次尝试 — 抛出
            if attempt >= max_retries:
                logger.error(
                    f"所有 {max_retries} 次重试已用尽，错误类型 {category.value}: {e}"
                )
                raise

            # 计算退避延迟并等待
            delay = get_retry_delay(attempt, category)

            # 尊重 retry-after header（如果有）
            if isinstance(e, APIStatusError) and hasattr(e, 'response'):
                retry_after = e.response.headers.get('retry-after')
                if retry_after:
                    try:
                        header_delay = float(retry_after)
                        delay = max(delay, header_delay)
                    except (ValueError, TypeError):
                        pass

            logger.info(
                f"重试中 ({attempt + 1}/{max_retries})，"
                f"{category.value} 错误，延迟={delay:.1f}秒"
            )
            await asyncio.sleep(delay)

    raise last_error
