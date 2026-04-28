"""
会话导出功能

[Workflow]
1. 从消息列表中提取对话内容
2. 格式化为 Markdown 或纯文本
3. 写入指定文件或返回内容字符串

支持的导出格式：
- Markdown (.md) — 带格式的对话记录
- 纯文本 (.txt) — 无格式的对话记录
- JSON (.json) — 原始消息数据
"""

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

def format_timestamp(dt: Optional[datetime] = None) -> str:
    """
    格式化时间戳为文件名安全的字符串

    [Workflow]
    1. 获取当前时间（如果未提供）
    2. 格式化为 YYYY-MM-DD-HHmmss 格式

    Args:
        dt: 日期时间对象，默认为当前时间

    Returns:
        格式化的时间戳字符串
    """
    if dt is None:
        dt = datetime.now()

    # 格式化为 YYYY-MM-DD-HHmmss
    return dt.strftime("%Y-%m-%d-%H%M%S")

def extract_first_prompt(messages: List[Dict[str, Any]]) -> str:
    """
    从消息列表中提取第一条用户消息

    [Workflow]
    1. 找到第一条 role == "user" 的消息
    2. 提取文本内容
    3. 只取第一行，限制长度为 50 字符

    Args:
        messages: 消息列表

    Returns:
        第一条用户消息的文本（最多 50 字符）
    """
    # 找到第一条用户消息
    first_user_msg = None
    for msg in messages:
        if msg.get("role") == "user":
            first_user_msg = msg
            break

    if not first_user_msg:
        return ""

    # 提取文本内容
    content = first_user_msg.get("content", "")
    result = ""

    if isinstance(content, str):
        result = content.strip()
    elif isinstance(content, list):
        # 从 content blocks 中提取第一个文本块
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                result = block.get("text", "").strip()
                break

    # 只取第一行
    result = result.split("\n")[0] if result else ""

    # 限制长度为 50 字符
    if len(result) > 50:
        result = result[:49] + "…"

    return result

def sanitize_filename(text: str) -> str:
    """
    将文本转换为安全的文件名

    [Workflow]
    1. 转换为小写
    2. 移除特殊字符（只保留字母、数字、空格、连字符）
    3. 将空格替换为连字符
    4. 合并多个连字符
    5. 移除首尾连字符

    Args:
        text: 原始文本

    Returns:
        安全的文件名字符串
    """
    # 转换为小写
    result = text.lower()
    # 移除特殊字符（只保留字母、数字、空格、连字符）
    result = re.sub(r'[^a-z0-9\s-]', '', result)
    # 将空格替换为连字符
    result = re.sub(r'\s+', '-', result)
    # 合并多个连字符
    result = re.sub(r'-+', '-', result)
    # 移除首尾连字符
    result = result.strip('-')
    return result

def generate_default_filename(
    messages: List[Dict[str, Any]],
    extension: str = ".txt",
) -> str:
    """
    生成默认导出文件名

    [Workflow]
    1. 提取第一条用户消息
    2. 如果有内容，生成 {timestamp}-{sanitized_prompt}{ext} 格式
    3. 否则生成 conversation-{timestamp}{ext} 格式

    Args:
        messages: 消息列表
        extension: 文件扩展名（默认 .txt）

    Returns:
        默认文件名字符串
    """
    timestamp = format_timestamp()
    first_prompt = extract_first_prompt(messages)

    if first_prompt:
        sanitized = sanitize_filename(first_prompt)
        if sanitized:
            return f"{timestamp}-{sanitized}{extension}"

    return f"conversation-{timestamp}{extension}"

def messages_to_markdown(messages: List[Dict[str, Any]]) -> str:
    """
    将消息列表转换为 Markdown 格式

    [Workflow]
    1. 遍历消息列表
    2. 根据 role 添加对应的 Markdown 标题
    3. 提取并格式化文本内容
    4. 跳过工具调用和系统消息

    Args:
        messages: 消息列表

    Returns:
        Markdown 格式的对话文本
    """
    lines = []
    lines.append("# 对话记录\n")
    lines.append(f"导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append("---\n")

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        # 跳过非对话消息
        if role not in ("user", "assistant"):
            continue

        # 跳过 meta 消息
        if msg.get("isMeta", False):
            continue

        # 添加角色标题
        if role == "user":
            lines.append("\n## 用户\n")
        else:
            lines.append("\n## 助手\n")

        # 提取文本内容
        if isinstance(content, str):
            lines.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    block_type = block.get("type", "")
                    if block_type == "text":
                        lines.append(block.get("text", ""))
                    elif block_type == "tool_use":
                        # 工具调用：显示工具名和输入摘要
                        tool_name = block.get("name", "unknown")
                        tool_input = block.get("input", {})
                        # 只显示简短摘要
                        input_summary = str(tool_input)[:100]
                        if len(str(tool_input)) > 100:
                            input_summary += "..."
                        lines.append(f"\n*[工具调用: {tool_name}({input_summary})]*\n")
                    elif block_type == "tool_result":
                        # 工具结果：显示简短摘要
                        result_content = block.get("content", "")
                        if isinstance(result_content, str):
                            summary = result_content[:200]
                            if len(result_content) > 200:
                                summary += "..."
                            lines.append(f"\n*[工具结果: {summary}]*\n")

        lines.append("\n")

    return "\n".join(lines)

def messages_to_plain_text(messages: List[Dict[str, Any]]) -> str:
    """
    将消息列表转换为纯文本格式

    [Workflow]
    1. 遍历消息列表
    2. 根据 role 添加前缀
    3. 提取纯文本内容（跳过工具调用）

    Args:
        messages: 消息列表

    Returns:
        纯文本格式的对话文本
    """
    lines = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        # 跳过非对话消息
        if role not in ("user", "assistant"):
            continue

        # 跳过 meta 消息
        if msg.get("isMeta", False):
            continue

        # 角色前缀
        prefix = "Human: " if role == "user" else "Assistant: "

        # 提取文本内容
        text_parts = []
        if isinstance(content, str):
            text_parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    if text:
                        text_parts.append(text)

        if text_parts:
            text = "\n".join(text_parts)
            lines.append(f"{prefix}{text}")
            lines.append("")  # 空行分隔

    return "\n".join(lines)

def export_session(
    messages: List[Dict[str, Any]],
    output_path: str,
    format: str = "txt",
) -> str:
    """
    导出会话到文件

    [Workflow]
    1. 根据格式选择转换函数
    2. 转换消息为目标格式
    3. 写入文件
    4. 返回文件路径

    Args:
        messages: 消息列表
        output_path: 输出文件路径
        format: 导出格式（"txt"、"md"、"json"）

    Returns:
        写入的文件路径

    Raises:
        ValueError: 不支持的格式
        IOError: 文件写入失败
    """
    # 根据格式选择转换函数
    if format == "md" or output_path.endswith(".md"):
        content = messages_to_markdown(messages)
    elif format == "json" or output_path.endswith(".json"):
        # JSON 格式：导出原始消息数据（过滤掉内部字段）
        export_messages = []
        for msg in messages:
            if msg.get("role") in ("user", "assistant"):
                export_msg = {
                    "role": msg["role"],
                    "content": msg.get("content", ""),
                }
                if msg.get("uuid"):
                    export_msg["uuid"] = msg["uuid"]
                export_messages.append(export_msg)
        content = json.dumps(
            {"messages": export_messages, "exported_at": datetime.now().isoformat()},
            ensure_ascii=False,
            indent=2,
        )
    else:
        # 默认纯文本格式
        content = messages_to_plain_text(messages)

    # 确保目录存在
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    # 写入文件
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    logger.info(f"[session_export] 已导出会话到: {output_path}")
    return output_path

def export_session_to_string(
    messages: List[Dict[str, Any]],
    format: str = "txt",
) -> str:
    """
    将会话导出为字符串（不写入文件）

    [Workflow]
    1. 根据格式选择转换函数
    2. 返回转换后的字符串

    Args:
        messages: 消息列表
        format: 导出格式（"txt"、"md"、"json"）

    Returns:
        导出内容字符串
    """
    if format == "md":
        return messages_to_markdown(messages)
    elif format == "json":
        export_messages = []
        for msg in messages:
            if msg.get("role") in ("user", "assistant"):
                export_msg = {
                    "role": msg["role"],
                    "content": msg.get("content", ""),
                }
                export_messages.append(export_msg)
        return json.dumps(
            {"messages": export_messages, "exported_at": datetime.now().isoformat()},
            ensure_ascii=False,
            indent=2,
        )
    else:
        return messages_to_plain_text(messages)
