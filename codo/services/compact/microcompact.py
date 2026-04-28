"""
Microcompact 模块

Microcompact 是一种轻量级压缩，针对特定工具的结果进行压缩：
- 清除旧的工具结果内容（time-based）
- 压缩大型工具结果（Read, Bash, Grep, Glob 等）
- 保留最近的工具结果

简化版实现：
- 实现 time-based microcompact（清除旧内容）
- 跳过 cached microcompact（需要 prompt cache 支持）
"""

import logging
from typing import List, Dict, Any, Optional, Set
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ============================================================================
# 配置常量
# ============================================================================

# 可压缩的工具列表
COMPACTABLE_TOOLS = {
    "Read",
    "Bash",
    "Grep",
    "Glob",
    "WebSearch",
    "WebFetch",
    "Edit",
    "Write",
}

# 清除消息
TIME_BASED_MC_CLEARED_MESSAGE = "[Old tool result content cleared]"

# Time-based microcompact 配置

TIME_BASED_MC_CONFIG = {
    "enabled": True,
    "gap_threshold_minutes": 60,
    "keep_recent": 5,
}

# ============================================================================
# Microcompact 结果类型
# ============================================================================

class MicrocompactResult:
    """
    Microcompact 结果

    [Workflow]
    1. 保存压缩后的消息列表
    2. 记录压缩的工具结果数量
    3. 记录释放的 token 估算值
    """

    def __init__(
        self,
        messages: List[Dict[str, Any]],
        compacted_count: int = 0,
        tokens_freed: int = 0,
    ):
        # 压缩后的消息列表
        self.messages = messages
        # 被压缩的工具结果数量
        self.compacted_count = compacted_count
        # 释放的 token 估算值
        self.tokens_freed = tokens_freed

# 模块级缓存：已压缩的 tool_use_id 集合
# 避免重复压缩同一个工具结果

_compacted_tool_use_ids: Set[str] = set()

def reset_compacted_cache():
    """
    重置已压缩的 tool_use_id 缓存

    在 compact 操作后调用，因为消息历史已被替换。

    [Workflow]
    1. 清空缓存集合
    2. 下次 microcompact 时重新评估所有工具结果
    """
    global _compacted_tool_use_ids
    _compacted_tool_use_ids = set()

# ============================================================================
# Time-based Microcompact
# ============================================================================

def should_compact_tool_result(
    tool_name: str,
    timestamp: Optional[datetime],
    recent_tool_results: List[str],
) -> bool:
    """
    判断是否应该压缩工具结果

    Args:
        tool_name: 工具名称
        timestamp: 工具结果时间戳
        recent_tool_results: 最近的工具结果 ID 列表

    Returns:
        bool: 是否应该压缩
    """
    # [Workflow] 1. 工具白名单判断 -> 2. 时间戳有效性判断 -> 3. 年龄阈值判断。

    # 不在可压缩白名单时直接保留，避免误压缩关键结果。
    if tool_name not in COMPACTABLE_TOOLS:
        return False

    # 缺失时间戳会失去“新旧”判断依据，保守策略是不压缩。
    if timestamp is None:
        return False

    # 检查是否在最近的结果中
    # TODO: 需要工具结果 ID 来判断
    # 简化版：只根据时间判断

    # 检查年龄：当前时间与工具结果时间戳的差值
    age = datetime.now() - timestamp
    # 使用 gap_threshold_minutes 配置项判断是否超过阈值
    threshold = timedelta(minutes=TIME_BASED_MC_CONFIG["gap_threshold_minutes"])

    # 超过阈值返回 True，触发后续内容清理。
    return age > threshold

def compact_tool_result_content(content: Any) -> str:
    """
    压缩工具结果内容

    Args:
        content: 原始内容

    Returns:
        str: 压缩后的内容
    """
    # 用统一占位文本替换原结果，减少 token 占用并保留“此处曾有结果”的语义。
    return TIME_BASED_MC_CLEARED_MESSAGE

def _run_microcompact(
    messages: List[Dict[str, Any]],
    context: Optional[Dict[str, Any]] = None,
    already_compacted_ids: Optional[Set[str]] = None,
    use_global_cache: bool = True,
) -> MicrocompactResult:
    """
    执行 microcompact 的核心实现（同步）。

    [Workflow]
    1. 按配置检查是否启用 time-based microcompact
    2. 逆序收集最近 keep_recent 个 tool_result（受保护，不压缩）
    3. 正序遍历消息，对旧工具结果执行内容清理
    4. 按需更新模块级缓存（仅 use_global_cache=True）
    5. 返回压缩统计与新消息列表

    Args:
        messages: 消息历史
        context: 执行上下文（预留）
        already_compacted_ids: 外部传入的已压缩 ID 集合
        use_global_cache: 是否读取/写入模块级缓存
    """
    # 如果 time-based microcompact 未启用，直接返回原始消息
    if not TIME_BASED_MC_CONFIG["enabled"]:
        return MicrocompactResult(messages=messages)

    # 引用模块级缓存（仅主链路使用，统计预览场景可关闭）
    global _compacted_tool_use_ids

    compacted_messages = []
    compacted_count = 0
    tokens_freed = 0

    # 收集最近的工具结果（用于保护，不压缩最近的结果）
    recent_tool_results = []
    tool_result_count = 0

    # 第一遍（逆序）：收集最近 N 个工具结果的 ID

    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if block.get("type") == "tool_result":
                        tool_result_count += 1
                        # 保留最近 keep_recent 个工具结果
                        if tool_result_count <= TIME_BASED_MC_CONFIG["keep_recent"]:
                            tool_use_id = block.get("tool_use_id")
                            if tool_use_id:
                                recent_tool_results.append(tool_use_id)

    # 第二遍（正序）：压缩旧的工具结果
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", [])
            if isinstance(content, list):
                new_content = []
                for block in content:
                    if block.get("type") == "tool_result":
                        tool_use_id = block.get("tool_use_id")

                        # 检查是否在最近的结果中（受保护，不压缩）
                        if tool_use_id in recent_tool_results:
                            new_content.append(block)
                        # 检查是否已在缓存中（已压缩过，跳过重复处理）
                        elif (
                            use_global_cache
                            and tool_use_id
                            and tool_use_id in _compacted_tool_use_ids
                        ):
                            new_content.append(block)
                        # 检查外部传入的已压缩 ID 集合
                        elif already_compacted_ids and tool_use_id in already_compacted_ids:
                            new_content.append(block)
                        else:
                            # 需要压缩的工具结果
                            original_content = block.get("content", "")
                            if original_content and original_content != TIME_BASED_MC_CLEARED_MESSAGE:
                                # 估算释放的 token 数（粗略：4 字符 ≈ 1 token）
                                tokens_freed += len(str(original_content)) // 1.5

                                # 创建压缩后的 block，替换内容为清除标记
                                compacted_block = {
                                    **block,
                                    "content": TIME_BASED_MC_CLEARED_MESSAGE,
                                }
                                new_content.append(compacted_block)
                                compacted_count += 1

                                # 将已压缩的 tool_use_id 加入模块级缓存
                                if use_global_cache and tool_use_id:
                                    _compacted_tool_use_ids.add(tool_use_id)
                            else:
                                # 内容为空或已经是清除标记，无需再压缩
                                new_content.append(block)
                    else:
                        new_content.append(block)

                # 更新消息内容
                compacted_msg = {**msg, "content": new_content}
                compacted_messages.append(compacted_msg)
            else:
                compacted_messages.append(msg)
        else:
            compacted_messages.append(msg)

    if compacted_count > 0 and logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            f"[microcompact] Compacted {compacted_count} tool results, "
            f"freed ~{tokens_freed} tokens"
        )

    return MicrocompactResult(
        messages=compacted_messages,
        compacted_count=compacted_count,
        tokens_freed=tokens_freed,
    )

async def microcompact_if_needed(
    messages: List[Dict[str, Any]],
    context: Optional[Dict[str, Any]] = None,
    already_compacted_ids: Optional[Set[str]] = None,
) -> MicrocompactResult:
    """
    执行 microcompact（如果需要）

    [Workflow]
    1. 调用同步核心实现计算压缩结果
    2. 复用全局缓存避免重复压缩同一 tool_result
    3. 返回压缩后的消息与统计信息
    """
    return _run_microcompact(
        messages=messages,
        context=context,
        already_compacted_ids=already_compacted_ids,
        use_global_cache=True,
    )

def preview_microcompact(
    messages: List[Dict[str, Any]],
    context: Optional[Dict[str, Any]] = None,
) -> MicrocompactResult:
    """
    预览 microcompact 结果（无副作用）。

    [Workflow]
    1. 复用 microcompact 核心逻辑计算“运行时会看到的消息”
    2. 不读写模块级缓存，避免影响真实 query 链路
    3. 用于 UI 统计口径（如 /context、token 展示）
    """
    return _run_microcompact(
        messages=messages,
        context=context,
        already_compacted_ids=None,
        use_global_cache=False,
    )

# ============================================================================
# Cached Microcompact（TODO: 需要 prompt cache 支持）
# ============================================================================

def get_pending_cache_edits():
    """
    获取待处理的缓存编辑

    [Workflow]
    1. 查询缓存编辑队列；
    2. 返回待应用的编辑集合；
    3. 当前简化实现直接返回 None。

    Returns:
        None: 简化版不支持
    """
    # 当前实现未接入缓存编辑队列，返回 None 表示“无待处理项”。
    return None

def get_pinned_cache_edits():
    """
    获取已固定的缓存编辑

    [Workflow]
    1. 返回已固定的缓存编辑列表；
    2. 供上层决定是否复用历史缓存；
    3. 当前简化实现返回空列表。

    Returns:
        List: 空列表
    """
    # 当前没有固定缓存编辑，返回空列表保持接口兼容。
    return []
