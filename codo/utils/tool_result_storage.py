"""
工具结果持久化和大小限制处理

这个模块负责：
1. 检测工具结果是否超过大小限制
2. 将超限结果持久化到磁盘
3. 生成预览消息返回给模型
4. 控制单消息中所有工具结果的聚合大小

[Workflow]
1. 工具执行完成后，检查结果大小
2. 如果超过阈值，持久化到文件
3. 生成包含预览的替换消息
4. 在消息级别控制所有工具结果的总预算

[Key Concepts]
- 持久化阈值：由工具声明的 maxResultSizeChars 和全局默认值决定
- 预览生成：保留头部内容，尽量在换行处截断
- 消息级预算：防止多个并行工具结果总和过大
"""

import os
import math
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime

from codo.constants.tool_limits import (
    DEFAULT_MAX_RESULT_SIZE_CHARS,
    MAX_TOOL_RESULT_BYTES,
    MAX_TOOL_RESULTS_PER_MESSAGE_CHARS,
    PREVIEW_SIZE_BYTES,
    PERSISTED_OUTPUT_TAG,
    PERSISTED_OUTPUT_CLOSING_TAG,
)

def get_persistence_threshold(
    tool_name: str,
    declared_max_result_size_chars: Optional[int],
) -> int:
    """
    获取工具结果持久化阈值

    [Workflow]
    1. 如果工具声明的是 Infinity，直接返回 Infinity
    2. 否则返回 min(工具声明值, 全局默认值)

    Args:
        tool_name: 工具名称
        declared_max_result_size_chars: 工具声明的最大结果大小

    Returns:
        实际生效的持久化阈值
    """
    # 如果工具声明的是 Infinity（Python 中用 float('inf') 或 math.inf）
    if declared_max_result_size_chars is None or not math.isfinite(declared_max_result_size_chars):
        return float('inf')

    # TODO: 未来可以添加 GrowthBook 覆盖逻辑
    # 目前直接返回 min(工具声明值, 全局默认值)
    return min(declared_max_result_size_chars, DEFAULT_MAX_RESULT_SIZE_CHARS)

def content_size(content: Any) -> int:
    """
    计算内容大小（字节数）

    Args:
        content: 内容（字符串或其他类型）

    Returns:
        内容的字节大小
    """
    if isinstance(content, str):
        return len(content.encode('utf-8'))
    elif isinstance(content, bytes):
        return len(content)
    elif isinstance(content, list):
        # 如果是列表（例如 tool_result 的 content 数组），递归计算
        return sum(content_size(item) for item in content)
    elif isinstance(content, dict):
        # 如果是字典，计算所有值的大小
        return sum(content_size(v) for v in content.values())
    else:
        # 其他类型，转换为字符串后计算
        return len(str(content).encode('utf-8'))

def format_file_size(size_bytes: int) -> str:
    """
    格式化文件大小为人类可读格式

    Args:
        size_bytes: 字节数

    Returns:
        格式化后的字符串（例如 "21.2KB"）
    """
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f}KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f}MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f}GB"

def generate_preview(content: str, max_bytes: int = PREVIEW_SIZE_BYTES) -> Tuple[str, bool]:
    """
    生成内容预览

    [Strategy]
    - 保留头部内容
    - 尽量在换行处截断，避免切断中间行
    - 如果没有合适的换行位置，直接在 max_bytes 处截断

    Args:
        content: 原始内容
        max_bytes: 最大预览字节数

    Returns:
        (预览内容, 是否还有更多内容)
    """
    content_bytes = content.encode('utf-8')

    if len(content_bytes) <= max_bytes:
        return (content, False)

    # 截取前 max_bytes 字节
    truncated_bytes = content_bytes[:max_bytes]

    # 尝试解码（可能在多字节字符中间截断）
    try:
        truncated = truncated_bytes.decode('utf-8')
    except UnicodeDecodeError:
        # 如果解码失败，向前回退直到找到有效的 UTF-8 边界
        for i in range(1, 4):
            try:
                truncated = content_bytes[:max_bytes - i].decode('utf-8')
                break
            except UnicodeDecodeError:
                continue
        else:
            # 如果还是失败，使用 errors='ignore'
            truncated = content_bytes[:max_bytes].decode('utf-8', errors='ignore')

    # 尝试在换行处截断
    last_newline = truncated.rfind('\n')
    if last_newline > max_bytes * 0.5:
        # 如果最后一个换行位置在后半部分，使用它
        truncated = truncated[:last_newline]

    return (truncated, True)

def persist_tool_result(
    content: str,
    tool_use_id: str,
    cwd: str,
) -> Dict[str, Any]:
    """
    持久化工具结果到磁盘

    [Workflow]
    1. 创建持久化目录（~/.codo/tool_results/）
    2. 生成文件名（基于 tool_use_id 和时间戳）
    3. 写入完整内容到文件
    4. 生成预览
    5. 返回持久化结果信息

    Args:
        content: 工具结果内容
        tool_use_id: 工具使用 ID
        cwd: 当前工作目录

    Returns:
        持久化结果信息字典
    """
    # 创建持久化目录
    tool_results_dir = Path.home() / ".codo" / "tool_results"
    tool_results_dir.mkdir(parents=True, exist_ok=True)

    # 生成文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{tool_use_id}_{timestamp}.txt"
    filepath = tool_results_dir / filename

    # 写入文件
    filepath.write_text(content, encoding='utf-8')

    # 计算大小
    original_size = len(content.encode('utf-8'))

    # 生成预览
    preview, has_more = generate_preview(content, PREVIEW_SIZE_BYTES)

    return {
        "filepath": str(filepath),
        "original_size": original_size,
        "preview": preview,
        "has_more": has_more,
    }

def build_large_tool_result_message(result: Dict[str, Any]) -> str:
    """
    构建大工具结果的替换消息

    [Format]
    <persisted-output>
    Output too large (21.2KB). Full output saved to: /path/to/file

    Preview (first 2KB):
    <preview content>
    ...
    </persisted-output>

    Args:
        result: persist_tool_result() 返回的结果信息

    Returns:
        格式化的替换消息
    """
    message = f"{PERSISTED_OUTPUT_TAG}\n"
    message += f"Output too large ({format_file_size(result['original_size'])}). "
    message += f"Full output saved to: {result['filepath']}\n\n"
    message += f"Preview (first {format_file_size(PREVIEW_SIZE_BYTES)}):\n"
    message += result['preview']
    if result['has_more']:
        message += '\n...\n'
    else:
        message += '\n'
    message += PERSISTED_OUTPUT_CLOSING_TAG

    return message

def maybe_persist_large_tool_result(
    tool_result_block: Dict[str, Any],
    tool_name: str,
    declared_max_result_size_chars: Optional[int],
    cwd: str,
) -> Dict[str, Any]:
    """
    检查工具结果大小，如果超限则持久化

    [Workflow]
    1. 获取持久化阈值
    2. 计算结果大小
    3. 如果未超限，直接返回原结果
    4. 如果超限，持久化到文件并返回替换消息

    Args:
        tool_result_block: 工具结果块（包含 tool_use_id 和 content）
        tool_name: 工具名称
        declared_max_result_size_chars: 工具声明的最大结果大小
        cwd: 当前工作目录

    Returns:
        原结果或替换后的结果
    """
    # 获取持久化阈值
    threshold = get_persistence_threshold(tool_name, declared_max_result_size_chars)

    # 如果阈值是 Infinity，不持久化
    if not math.isfinite(threshold):
        return tool_result_block

    # 计算结果大小
    content = tool_result_block.get("content", "")
    size = content_size(content)

    # 如果未超限，直接返回
    if size <= threshold:
        return tool_result_block

    # 超限，持久化到文件
    # 将 content 转换为字符串（如果是列表或其他类型）
    if isinstance(content, list):
        # 如果是列表，提取所有文本内容
        content_str = "\n".join(
            item.get("text", str(item)) if isinstance(item, dict) else str(item)
            for item in content
        )
    else:
        content_str = str(content)

    result = persist_tool_result(
        content_str,
        tool_result_block.get("tool_use_id", "unknown"),
        cwd,
    )

    # 构建替换消息
    message = build_large_tool_result_message(result)

    # 返回替换后的结果块
    return {
        **tool_result_block,
        "content": message,
    }

def get_per_message_budget_limit() -> int:
    """
    获取单消息工具结果聚合预算限制

    Returns:
        单消息聚合预算（字符数）
    """
    # TODO: 未来可以添加 GrowthBook 覆盖逻辑
    return MAX_TOOL_RESULTS_PER_MESSAGE_CHARS

def apply_tool_result_budget(
    messages: List[Dict[str, Any]],
    cwd: str,
    skip_tool_names: Optional[set] = None,
) -> List[Dict[str, Any]]:
    """
    应用工具结果预算控制

    [Workflow]
    1. 遍历所有消息
    2. 对于每个 user message，收集其中的 tool_result
    3. 计算总大小
    4. 如果超过预算，持久化最大的结果
    5. 返回处理后的消息列表

    Args:
        messages: 消息列表
        cwd: 当前工作目录
        skip_tool_names: 跳过的工具名称集合（例如 FileReadTool）

    Returns:
        处理后的消息列表
    """
    skip_tool_names = skip_tool_names or set()
    budget_limit = get_per_message_budget_limit()

    processed_messages = []

    for message in messages:
        if message.get("role") != "user":
            processed_messages.append(message)
            continue

        content = message.get("content", [])
        if not isinstance(content, list):
            processed_messages.append(message)
            continue

        # 收集 tool_result
        tool_results = [
            item for item in content
            if isinstance(item, dict) and item.get("type") == "tool_result"
        ]

        if not tool_results:
            processed_messages.append(message)
            continue

        # 计算总大小
        total_size = sum(content_size(tr.get("content", "")) for tr in tool_results)

        # 如果未超预算，直接添加
        if total_size <= budget_limit:
            processed_messages.append(message)
            continue

        # 超预算，持久化最大的结果
        # 简化实现：持久化所有超过阈值的结果
        new_content = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_result":
                # 简化：直接持久化大结果
                item_size = content_size(item.get("content", ""))
                if item_size > DEFAULT_MAX_RESULT_SIZE_CHARS:
                    # 持久化
                    content_str = str(item.get("content", ""))
                    result = persist_tool_result(
                        content_str,
                        item.get("tool_use_id", "unknown"),
                        cwd,
                    )
                    message_text = build_large_tool_result_message(result)
                    new_content.append({
                        **item,
                        "content": message_text,
                    })
                else:
                    new_content.append(item)
            else:
                new_content.append(item)

        processed_messages.append({
            **message,
            "content": new_content,
        })

    return processed_messages

class ToolResultStorage:
    """
    工具结果存储管理器

    负责检查和截断工具结果。
    """

    def __init__(self, cwd: str):
        """
        初始化存储管理器

        Args:
            cwd: 当前工作目录
        """
        self.cwd = cwd

    def maybe_truncate_result(
        self,
        result: Any,
        tool_use_id: str,
        tool_name: str,
        max_size_chars: int,
    ) -> Any:
        """
        检查结果大小并截断（如果需要）

        [Workflow]
        1. 提取结果内容
        2. 计算大小
        3. 如果超限，持久化并返回替换消息
        4. 否则返回原结果

        Args:
            result: 工具执行结果（ToolResult 对象）
            tool_use_id: 工具使用 ID
            tool_name: 工具名称
            max_size_chars: 最大字符数限制

        Returns:
            原结果或截断后的结果
        """
        # 如果限制是 Infinity，不截断
        if not math.isfinite(max_size_chars):
            return result

        # 提取结果内容
        # ToolResult 对象有 data 属性
        if hasattr(result, 'data'):
            content = result.data
        else:
            content = result

        # 将内容转换为字符串
        if hasattr(content, 'model_dump'):
            # Pydantic 模型
            content_str = str(content.model_dump())
        elif hasattr(content, '__dict__'):
            # 普通对象
            content_str = str(content.__dict__)
        else:
            content_str = str(content)

        # 计算大小
        size = len(content_str.encode('utf-8'))

        # 如果未超限，返回原结果
        threshold = get_persistence_threshold(tool_name, max_size_chars)
        if size <= threshold:
            return result

        # 超限，持久化到文件
        persist_result = persist_tool_result(
            content_str,
            tool_use_id,
            self.cwd,
        )

        # 构建替换消息
        message = build_large_tool_result_message(persist_result)

        # 返回修改后的结果
        # 保持原结果结构，只替换内容
        if hasattr(result, 'data'):
            # 如果是 ToolResult 对象，创建新的对象
            from codo.tools.types import ToolResult
            return ToolResult(
                data=message,
                error=result.error if hasattr(result, 'error') else None,
            )
        else:
            return message

