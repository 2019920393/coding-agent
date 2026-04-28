"""
核心流程：
将完整对话内容 + 精简提示词发送给模型
模型生成总结内容（<analysis> + <summary>）
将所有消息替换为：[compact_boundary, summary_message]
可选择性重新注入关键上下文（CODO.md、近期文件）
这是长对话场景下最为核心的服务 —— 缺少该服务，
上下文窗口将会被占满，智能代理将无法正常使用
"""

import asyncio
import inspect
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from anthropic import AsyncAnthropic

from codo.services.compact.prompt import (
    format_compact_summary,
    get_compact_prompt,
    get_compact_user_summary_message,
)
from codo.services.token_estimation import (
    TokenBudget,
    estimate_messages_tokens,
    estimate_token_count,
)

logger = logging.getLogger(__name__)

# Max streaming retries for compact
MAX_COMPACT_RETRIES = 2

# Max tokens for compact summary output
COMPACT_MAX_OUTPUT_TOKENS = 20_000

class CompactResult:
    """压缩操作的结果封装

    用于返回压缩后的消息列表和统计信息，供调用方：
    1. 用 new_messages 替换旧消息继续对话
    2. 通过 pre/post token count 评估压缩效果
    3. 保存 summary 用于调试或展示
    """

    def __init__(
        self,
        summary: str,                          # 模型生成的对话摘要文本（从 <summary> 标签提取）
        new_messages: List[Dict[str, Any]],    # 压缩后的消息列表（通常是 [compact_boundary, summary_message]）
        pre_compact_token_count: int = 0,      # 压缩前的 token 数量
        post_compact_token_count: int = 0,     # 压缩后的 token 数量（通常降低 60-80%）
    ):
        self.summary = summary
        self.new_messages = new_messages
        self.pre_compact_token_count = pre_compact_token_count
        self.post_compact_token_count = post_compact_token_count

class AutoCompactState:
    """
    跟踪 auto-compact 的跨轮次状态

    [Workflow]
    1. 每轮次递增 turn_counter
    2. compact 成功时重置计数器
    3. compact 失败时递增 consecutive_failures
    4. 连续失败超过阈值时触发 circuit breaker（停止尝试）
    """

    # 连续失败阈值（超过此值停止尝试）

    MAX_CONSECUTIVE_FAILURES = 3

    def __init__(self):
        # 是否已经执行过 compact
        self.compacted = False
        # 轮次计数器（自上次 compact 以来的轮次数）
        self.turn_counter = 0
        # 轮次唯一 ID（用于分析和调试）
        self.turn_id = ""
        # 连续失败次数（用于 circuit breaker）
        self.consecutive_failures = 0

    @property
    def circuit_breaker_tripped(self) -> bool:
        """检查 circuit breaker 是否已触发（连续失败次数达到阈值）"""
        return self.consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES

    def record_success(self):
        """记录 compact 成功，重置所有计数器"""
        self.compacted = True
        self.consecutive_failures = 0
        self.turn_counter = 0
        self.turn_id = ""  # 重置轮次 ID

    def record_failure(self):
        """
        记录 compact 失败，递增连续失败计数

        [Workflow]
        1. 递增 consecutive_failures
        2. 检查是否触发 circuit breaker
        3. 触发时输出警告日志
        """
        self.consecutive_failures += 1
        if self.circuit_breaker_tripped:
            logger.warning(
                "Auto-compact circuit breaker tripped after "
                f"{self.consecutive_failures} consecutive failures"
            )

    def increment_turn(self):
        """递增轮次计数器"""
        self.turn_counter += 1

# ============================================================================
# Token 警告状态计算

# ============================================================================
# 注意：所有阈值常量已统一定义在 TokenBudget 类中，避免重复定义

def calculate_token_warning_state(
    token_usage: int,
    model: str,
) -> Dict[str, Any]:
    """
    计算 token 使用量的警告状态

    [Workflow]
    1. 获取模型的有效上下文窗口大小
    2. 计算各级别阈值（auto-compact、警告、错误、阻塞）
    3. 判断当前 token 使用量处于哪个级别
    4. 返回完整的警告状态

    Args:
        token_usage: 当前 token 使用量
        model: 模型名称

    Returns:
        Dict 包含以下字段：
        - percent_left: 剩余百分比
        - is_above_warning_threshold: 是否超过警告阈值
        - is_above_error_threshold: 是否超过错误阈值
        - is_above_auto_compact_threshold: 是否超过 auto-compact 阈值
        - is_at_blocking_limit: 是否达到阻塞限制（必须 compact 才能继续）
    """
    # 获取 token 预算信息
    budget = TokenBudget(model)

    # 有效上下文窗口（已减去输出预留）
    effective_window = budget.effective_context_window

    # auto-compact 阈值 = 有效窗口 - auto-compact 缓冲区
    auto_compact_threshold = effective_window - TokenBudget.AUTOCOMPACT_BUFFER_TOKENS

    # 使用 auto-compact 阈值作为基准（如果启用了 auto-compact）
    threshold = auto_compact_threshold

    # 计算剩余百分比：(基准 - 当前用量) / 基准 * 100
    percent_left = max(0, round(((threshold - token_usage) / threshold) * 100))

    # 警告阈值 = 基准 - 警告缓冲区
    warning_threshold = threshold - TokenBudget.WARNING_THRESHOLD_BUFFER_TOKENS

    # 错误阈值 = 基准 - 错误缓冲区
    error_threshold = threshold - TokenBudget.ERROR_THRESHOLD_BUFFER_TOKENS

    # 阻塞限制 = 有效窗口 - 手动 compact 缓冲区
    # 注意：阻塞限制基于有效窗口而非 auto-compact 阈值
    blocking_limit = effective_window - TokenBudget.MANUAL_COMPACT_BUFFER_TOKENS

    return {
        "percent_left": percent_left,
        "is_above_warning_threshold": token_usage >= warning_threshold,
        "is_above_error_threshold": token_usage >= error_threshold,
        "is_above_auto_compact_threshold": token_usage >= auto_compact_threshold,
        "is_at_blocking_limit": token_usage >= blocking_limit,
        # 额外信息（用于调试）
        "token_usage": token_usage,
        "effective_window": effective_window,
        "auto_compact_threshold": auto_compact_threshold,
        "blocking_limit": blocking_limit,
    }

async def compact_conversation(
    client: AsyncAnthropic,
    model: str,
    system_prompt: str,
    messages: List[Dict[str, Any]],
    custom_instructions: str = None,
    suppress_follow_up: bool = False,
    transcript_path: str = None,
) -> CompactResult:
    """
        通过摘要对对话内容进行精简压缩。
    compactConversation()
    执行流程：
    统计压缩前的令牌数量
    构建精简提示词
    将精简请求追加至消息列表中
    调用模型生成摘要内容
    整理摘要格式并构建新的消息数组
    返回精简结果
    入参：
    client：异步客户端
    model：用于生成摘要的模型编号
    system_prompt：当前系统提示词
    messages：当前对话消息（接口格式）
    custom_instructions：用户自定义的可选精简指令
    suppress_follow_up：压缩完成后是否屏蔽后续追问
    transcript_path：完整对话文稿文件路径（用于参考）
    返回值：
    包含摘要内容与新消息列表的精简结果

    """
    if not messages:
        raise ValueError("消息数量不足，无法压缩.")

    pre_compact_tokens = estimate_messages_tokens(messages)

    # Build the compact prompt
    compact_prompt = get_compact_prompt(custom_instructions)

    # Build messages for the compact request:
    # All existing messages + a user message asking for summary
    compact_messages = _strip_images_from_messages(messages) + [
        {"role": "user", "content": compact_prompt}
    ]

    # Ensure alternating roles
    compact_messages = _ensure_alternating(compact_messages)

    # Call the model
    summary = await _stream_compact_summary(
        client=client,
        model=model,
        system_prompt=system_prompt,
        messages=compact_messages,
    )

    if not summary:
        raise RuntimeError(
            "Failed to generate conversation summary — "
            "response did not contain valid text content"
        )

    # Build the new message array
    summary_user_message = get_compact_user_summary_message(
        summary=summary,
        suppress_follow_up=suppress_follow_up,
        transcript_path=transcript_path,
    )

    new_messages = [
        {
            "role": "user",
            "content": summary_user_message,
            "uuid": str(uuid4()),
            "type": "user",
            "is_compact_summary": True,
        },
    ]

    post_compact_tokens = estimate_messages_tokens(
        [{"role": "user", "content": summary_user_message}]
    )

    return CompactResult(
        summary=summary,
        new_messages=new_messages,
        pre_compact_token_count=pre_compact_tokens,
        post_compact_token_count=post_compact_tokens,
    )

async def force_compact(
    client: AsyncAnthropic,
    model: str,
    system_prompt: str,
    messages: List[Dict[str, Any]],
    transcript_path: str = None,
) -> Optional[CompactResult]:
    """
    强制执行 compact（用于 reactive compact 场景）

    当 API 返回 prompt_too_long 错误时调用。
    不检查 token 阈值，直接执行压缩。

    Args:
        client: 异步客户端
        model: 模型 ID
        system_prompt: 当前系统提示词
        messages: 当前对话消息列表
        transcript_path: 会话记录文件路径（可选）

    Returns:
        CompactResult 压缩成功时返回结果，失败返回 None
    """
    try:
        result = await compact_conversation(
            client=client,
            model=model,
            system_prompt=system_prompt,
            messages=messages,
            suppress_follow_up=True,
            transcript_path=transcript_path,
        )
        return result
    except Exception as e:
        logger.error(f"强制 compact 失败: {e}")
        return None

async def force_compact_conversation(
    client: AsyncAnthropic,
    model: str,
    system_prompt: str,
    messages: List[Dict[str, Any]],
    token_budget: TokenBudget,
    tracking: AutoCompactState,
    transcript_path: str = None,
) -> Optional[CompactResult]:
    """
    强制执行 compact，绕过阈值检查（兼容旧接口）。

    用于 reactive compact 场景（PROMPT_TOO_LONG 错误恢复）。

    注意：新代码应使用 force_compact()，此函数保留用于向后兼容。

    Args:
        client: 异步客户端
        model: 模型 ID
        system_prompt: 当前系统提示词
        messages: 当前对话消息列表
        token_budget: Token 预算跟踪器
        tracking: Auto-compact 状态跟踪器
        transcript_path: 会话记录文件路径（可选）

    Returns:
        CompactResult 压缩成功时返回结果，失败返回 None
    """
    logger.info(
        f"强制 compact 触发: {len(messages)} 条消息, "
        f"~{estimate_messages_tokens(messages)} tokens"
    )

    try:
        result = await compact_conversation(
            client=client,
            model=model,
            system_prompt=system_prompt,
            messages=messages,
            suppress_follow_up=True,
            transcript_path=transcript_path,
        )
        tracking.record_success()
        return result

    except Exception as e:
        logger.error(f"强制 compact 失败: {e}")
        tracking.record_failure()
        return None

async def auto_compact_if_needed(
    client: AsyncAnthropic,
    model: str,
    system_prompt: str,
    messages: List[Dict[str, Any]],
    token_budget: TokenBudget,
    tracking: AutoCompactState,
    transcript_path: str = None,
) -> Optional[CompactResult]:
    """
    检查是否需要自动压缩并执行压缩操作。
    autoCompactIfNeeded()
    参数：
    client：异步客户端
    model：模型 ID
    system_prompt：当前系统提示词
    messages：当前对话消息
    token_budget：令牌额度跟踪器
    tracking：自动压缩状态跟踪器
    transcript_path：日志文件路径
    返回值：
    若执行了压缩操作，则返回 CompactResult；否则返回 None
    """
    # Circuit breaker
    if tracking.circuit_breaker_tripped:
        return None

    # Estimate current token usage
    token_count = estimate_messages_tokens(messages)

    if not token_budget.should_auto_compact(token_count):
        return None

    logger.info(
        f"Auto-compact triggered: {token_count} tokens "
        f"(threshold: {token_budget.auto_compact_threshold})"
    )

    try:
        result = await compact_conversation(
            client=client,
            model=model,
            system_prompt=system_prompt,
            messages=messages,
            suppress_follow_up=True,
            transcript_path=transcript_path,
        )
        tracking.record_success()
        return result

    except Exception as e:
        logger.error(f"Auto-compact failed: {e}")
        tracking.record_failure()
        return None

async def _stream_compact_summary(
    client: AsyncAnthropic,
    model: str,
    system_prompt: str,
    messages: List[Dict[str, Any]],
) -> Optional[str]:
    """
    从模型中流式传输精简摘要。
    针对瞬时错误进行重试，最多重试 MAX_COMPACT_RETRIES 次。
    """
    last_error = None

    for attempt in range(MAX_COMPACT_RETRIES + 1):
        try:
            stream_ctx = client.messages.stream(
                model=model,
                max_tokens=COMPACT_MAX_OUTPUT_TOKENS,
                system=system_prompt,
                messages=messages,
            )
            if inspect.isawaitable(stream_ctx):
                stream_ctx = await stream_ctx

            async with stream_ctx as stream:
                text_parts: list[str] = []
                text_stream = getattr(stream, "text_stream", None)
                if text_stream is not None:
                    async for text in text_stream:
                        if text:
                            text_parts.append(str(text))

                if text_parts:
                    return "".join(text_parts)

                get_final_text = getattr(stream, "get_final_text", None)
                if callable(get_final_text):
                    final_text = get_final_text()
                    if inspect.isawaitable(final_text):
                        final_text = await final_text
                    if final_text:
                        return str(final_text)

                get_final_message = getattr(stream, "get_final_message", None)
                if callable(get_final_message):
                    final_message = get_final_message()
                    if inspect.isawaitable(final_message):
                        final_message = await final_message
                    content_blocks = getattr(final_message, "content", []) or []
                    for block in content_blocks:
                        if getattr(block, "type", "") == "text":
                            text_parts.append(str(getattr(block, "text", "") or ""))
                    if text_parts:
                        return "".join(text_parts)

                return None

        except Exception as e:
            last_error = e
            logger.warning(
                f"Compact summary attempt {attempt + 1} failed: {e}"
            )
            if attempt < MAX_COMPACT_RETRIES:
                await asyncio.sleep(1.0 * (attempt + 1))

    if last_error:
        raise last_error
    return None

def _strip_images_from_messages(
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
        发送消息前剥离其中的图片模块以实现内容压缩。
        图片会占用精简请求中的令牌额度。
        stripImagesFromMessages()
    """
    result = []
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            result.append(msg)
            continue

        has_image = False
        new_content = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "image":
                    has_image = True
                    new_content.append({"type": "text", "text": "[image]"})
                elif block.get("type") == "document":
                    has_image = True
                    new_content.append({"type": "text", "text": "[document]"})
                elif (
                    block.get("type") == "tool_result"
                    and isinstance(block.get("content"), list)
                ):
                    # Strip images inside tool_result
                    tool_content = []
                    tool_has_image = False
                    for item in block["content"]:
                        if isinstance(item, dict) and item.get("type") in (
                            "image",
                            "document",
                        ):
                            tool_has_image = True
                            tool_content.append(
                                {"type": "text", "text": f"[{item['type']}]"}
                            )
                        else:
                            tool_content.append(item)
                    if tool_has_image:
                        has_image = True
                        new_content.append({**block, "content": tool_content})
                    else:
                        new_content.append(block)
                else:
                    new_content.append(block)
            else:
                new_content.append(block)

        if has_image:
            result.append({**msg, "content": new_content})
        else:
            result.append(msg)

    return result

def _ensure_alternating(
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    确保消息在用户与助手角色之间交替发送。
    如有需要，合并连续的相同角色消息。
    """
    if not messages:
        return []

    result = []
    for msg in messages:
        role = msg.get("role")
        if not role:
            continue

        if result and result[-1]["role"] == role:
            # Same role — merge content
            prev = result[-1]
            prev_content = prev["content"]
            new_content = msg["content"]

            if isinstance(prev_content, str) and isinstance(new_content, str):
                result[-1] = {**prev, "content": f"{prev_content}\n\n{new_content}"}
            elif isinstance(prev_content, list) and isinstance(new_content, list):
                result[-1] = {**prev, "content": prev_content + new_content}
            elif isinstance(prev_content, str) and isinstance(new_content, list):
                result[-1] = {
                    **prev,
                    "content": [{"type": "text", "text": prev_content}] + new_content,
                }
            elif isinstance(prev_content, list) and isinstance(new_content, str):
                result[-1] = {
                    **prev,
                    "content": prev_content + [{"type": "text", "text": new_content}],
                }
        else:
            result.append(msg)

    return result
