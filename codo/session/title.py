"""
会话标题自动生成

[Workflow]
1. 从对话消息中提取文本内容
2. 调用 LLM（Haiku 或当前模型）生成简洁标题
3. 返回生成的标题字符串

"""

import json
import logging
from typing import Any, Dict, List, Optional

from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)

# 对话文本最大长度
MAX_CONVERSATION_TEXT = 1000

# 标题生成的系统提示词
SESSION_TITLE_PROMPT = """Generate a concise, sentence-case title (3-7 words) that captures the main topic or goal of this coding session. The title should be clear enough that the user recognizes the session in a list. Use sentence case: capitalize only the first word and proper nouns.

Return JSON with a single "title" field.

Good examples:
{"title": "Fix login button on mobile"}
{"title": "Add OAuth authentication"}
{"title": "Debug failing CI tests"}
{"title": "Refactor API client error handling"}

Bad (too vague): {"title": "Code changes"}
Bad (too long): {"title": "Investigate and fix the issue where the login button does not respond on mobile devices"}
Bad (wrong case): {"title": "Fix Login Button On Mobile"}"""

def extract_conversation_text(messages: List[Dict[str, Any]]) -> str:
    """
    从消息列表中提取对话文本

    [Workflow]
    1. 遍历消息列表，只处理 user/assistant 类型
    2. 跳过 meta 消息（isMeta=True）
    3. 提取文本内容并拼接
    4. 截取最后 MAX_CONVERSATION_TEXT 个字符（最近的上下文优先）

    Args:
        messages: 消息列表

    Returns:
        提取的对话文本（最多 MAX_CONVERSATION_TEXT 字符）
    """
    parts = []

    for msg in messages:
        # 只处理 user/assistant 消息
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue

        # 跳过 meta 消息（系统注入的消息）
        if msg.get("isMeta", False):
            continue

        # 提取文本内容
        content = msg.get("content", "")
        if isinstance(content, str):
            # 纯文本内容
            parts.append(content)
        elif isinstance(content, list):
            # content blocks 格式
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    if text:
                        parts.append(text)

    # 拼接所有文本
    full_text = "\n".join(parts)

    # 截取最后 MAX_CONVERSATION_TEXT 个字符（最近的上下文优先）
    if len(full_text) > MAX_CONVERSATION_TEXT:
        return full_text[-MAX_CONVERSATION_TEXT:]

    return full_text

async def generate_session_title(
    client: AsyncAnthropic,
    model: str,
    messages: List[Dict[str, Any]],
) -> Optional[str]:
    """
    使用 LLM 生成会话标题

    [Workflow]
    1. 从消息列表中提取对话文本
    2. 如果文本为空，返回 None
    3. 调用 LLM 生成标题（JSON 格式）
    4. 解析 JSON 提取 title 字段
    5. 返回标题字符串，失败返回 None

    Args:
        client: 异步客户端
        model: 模型名称
        messages: 对话消息列表

    Returns:
        生成的标题字符串，或 None（失败时）
    """
    # 提取对话文本
    conversation_text = extract_conversation_text(messages)
    if not conversation_text.strip():
        # 没有有效文本，无法生成标题
        return None

    try:
        # 调用 LLM 生成标题
        response = await client.messages.create(
            model=model,
            max_tokens=64,  # 标题很短，64 tokens 足够
            system=SESSION_TITLE_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": conversation_text,
                }
            ],
        )

        # 提取响应文本
        text = ""
        for block in response.content:
            if block.type == "text":
                text = block.text
                break

        if not text:
            return None

        # 解析 JSON 提取 title 字段
        try:
            parsed = json.loads(text.strip())
            title = parsed.get("title", "").strip()
            return title if title else None
        except (json.JSONDecodeError, AttributeError):
            # JSON 解析失败，尝试直接使用文本（去掉引号）
            cleaned = text.strip().strip('"').strip("'")
            if cleaned and len(cleaned) <= 100:
                return cleaned
            return None

    except Exception as e:
        # 标题生成是 best-effort，失败不影响主流程
        logger.debug(f"[session_title] 生成标题失败: {e}")
        return None

async def generate_and_save_title(
    client: AsyncAnthropic,
    model: str,
    messages: List[Dict[str, Any]],
    session_storage: Any,
) -> Optional[str]:
    """
    生成会话标题并保存到会话存储

    [Workflow]
    1. 调用 generate_session_title() 生成标题
    2. 如果生成成功，调用 session_storage.save_title() 保存
    3. 返回生成的标题

    Args:
        client: 异步客户端
        model: 模型名称
        messages: 对话消息列表
        session_storage: 会话存储对象（需要有 save_title 方法）

    Returns:
        生成的标题字符串，或 None
    """
    title = await generate_session_title(client, model, messages)

    if title and session_storage:
        try:
            # 保存 AI 生成的标题（source="ai"）
            if hasattr(session_storage, 'save_title'):
                session_storage.save_title(title, source="ai")
                logger.debug(f"[session_title] 已保存 AI 标题: {title!r}")
        except Exception as e:
            logger.debug(f"[session_title] 保存标题失败: {e}")

    return title
