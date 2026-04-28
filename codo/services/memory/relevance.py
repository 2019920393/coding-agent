"""
Memory 相关性查找模块

[Workflow]
1. 扫描 memory 目录中的所有 .md 文件（排除 MEMORY.md）
2. 过滤已展示过的文件（already_surfaced）
3. 使用关键词匹配找出相关文件（简化版，不调用 LLM）
4. 返回最多 5 个相关文件的路径和 mtime

本模块使用关键词匹配作为简化实现，避免引入额外 API 调用开销。
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set

from codo.services.memory.scan import scan_memory_files, MemoryHeader
from codo.services.memory.paths import ENTRYPOINT_NAME

# 模块级日志记录器，用于调试和错误追踪
logger = logging.getLogger(__name__)

# 最多返回的相关记忆数量
MAX_RELEVANT_MEMORIES = 5

# 停用词集合：常见英文功能词，对相关性判断无实质贡献，过滤后可提升匹配精度
_STOP_WORDS: Set[str] = {
    # 冠词、系动词、助动词
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'can', 'shall',
    # 介词、连词
    'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by', 'from', 'as',
    'into', 'through', 'during', 'and', 'or', 'but', 'not',
    # 代词、限定词
    'this', 'that', 'these', 'those', 'it', 'its', 'my', 'your', 'his',
    'her', 'our', 'their', 'what', 'which', 'who', 'how', 'when', 'where',
    'why', 'all', 'any', 'both', 'each', 'few', 'more', 'most', 'other',
    'some', 'such', 'than', 'too', 'very',
    # 常见副词、位置词
    'just', 'also', 'then', 'now', 'here', 'there', 'up', 'out', 'if',
    # 编程领域高频但低区分度的词汇
    'about', 'use', 'using', 'used', 'make', 'made', 'get', 'got', 'set',
    'run', 'running', 'add', 'added', 'new', 'old', 'file', 'files',
    'code', 'function', 'class', 'method', 'variable', 'type', 'value',
}

@dataclass
class RelevantMemory:
    """
    相关记忆文件信息

    [Workflow]
    仅作为数据容器，存储文件路径和修改时间，
    供调用方决定是否读取文件内容并注入上下文。
    """
    path: str        # 文件绝对路径（用于后续读取内容）
    mtime_ms: float

def _extract_keywords(text: str) -> Set[str]:
    """
    从文本中提取有效关键词集合

    [Workflow]
    1. 将文本转换为小写，统一大小写
    2. 用正则提取所有长度 >= 3 的字母/数字/下划线词元
    3. 过滤掉停用词集合中的词
    4. 返回剩余词元构成的集合

    Args:
        text: 输入文本（可以是文件名、描述、查询等任意字符串）

    Returns:
        有效关键词集合（小写，已去停用词）
    """
    if not text:
        # 空文本直接返回空集合，避免后续正则操作
        return set()

    # 提取所有由字母、数字、下划线组成且长度 >= 3 的词元
    # 使用 \w 等价于 [a-zA-Z0-9_]，长度限制过滤掉无意义的短词
    words = re.findall(r'[a-zA-Z0-9_]{3,}', text.lower())

    # 过滤停用词，保留具有区分度的内容词
    return {w for w in words if w not in _STOP_WORDS}

def _score_memory(header: MemoryHeader, query_keywords: Set[str]) -> float:
    """
    计算单个记忆文件与查询的关键词相关性分数

    [Workflow]
    1. 从文件名提取关键词（权重 2x，文件名通常是主题摘要）
    2. 从 description 字段提取关键词（权重 1x）
    3. 从 memory_type 字段提取关键词（权重 0.5x，类型词区分度较低）
    4. 计算各字段与查询关键词的交集大小并加权求和
    5. 用总关键词数的平方根归一化，防止长描述文件占优

    Args:
        header: 记忆文件头信息（含文件名、描述、类型）
        query_keywords: 从用户查询中提取的关键词集合

    Returns:
        相关性分数（>= 0.0，越高越相关；0.0 表示无交集）
    """
    if not query_keywords:
        # 查询关键词为空时无法判断相关性，返回 0
        return 0.0

    # 从文件名提取关键词（文件名是主题的高度浓缩，权重最高）
    filename_keywords = _extract_keywords(header.filename)

    # 从描述字段提取关键词（描述提供更多上下文，权重次之）
    desc_keywords = _extract_keywords(header.description or "")

    # 从类型字段提取关键词（类型词如 feedback/preference 区分度较低）
    type_keywords = _extract_keywords(header.memory_type or "")

    # 计算各字段与查询关键词的交集大小
    filename_matches = len(filename_keywords & query_keywords)  # 文件名命中数
    desc_matches = len(desc_keywords & query_keywords)          # 描述命中数
    type_matches = len(type_keywords & query_keywords)          # 类型命中数

    # 加权求和：文件名命中权重 2x，描述 1x，类型 0.5x
    raw_score = filename_matches * 2.0 + desc_matches * 1.0 + type_matches * 0.5

    # 计算该文件的总关键词数，用于归一化
    total_keywords = len(filename_keywords) + len(desc_keywords) + len(type_keywords)

    if total_keywords > 0:
        # 用平方根归一化：既惩罚关键词极少的文件，又避免长描述文件过度占优
        raw_score = raw_score / (total_keywords ** 0.5)

    return raw_score

def find_relevant_memories(
    query: str,
    memory_dir: str,
    already_surfaced: Optional[Set[str]] = None,
    max_results: int = MAX_RELEVANT_MEMORIES,
) -> List[RelevantMemory]:
    """
    查找与用户查询相关的记忆文件列表

    [Workflow]
    1. 扫描 memory 目录，获取所有 .md 文件的头信息（排除 MEMORY.md）
    2. 过滤掉 already_surfaced 集合中已展示过的文件（避免重复注入）
    3. 若无候选文件，直接返回空列表
    4. 从查询文本中提取关键词
    5. 若无有效关键词，退化为按 mtime 返回最新的几个文件
    6. 对每个候选文件计算关键词相关性分数
    7. 过滤掉分数为 0 的文件（完全无关）
    8. 按分数降序排序，截取前 max_results 个
    9. 返回 RelevantMemory 列表

    本实现使用关键词匹配作为简化替代，无需额外 API 调用。

    Args:
        query: 用户查询文本（通常是最后一条用户消息的内容）
        memory_dir: memory 目录的绝对路径
        already_surfaced: 本会话中已展示过的文件路径集合（用于去重）
        max_results: 最多返回的文件数量

    Returns:
        按相关性降序排列的 RelevantMemory 列表（可能为空）
    """
    # 防御式初始化：调用方未传入时默认为空集合
    if already_surfaced is None:
        already_surfaced = set()

    # 步骤 1：扫描 memory 目录，获取所有 .md 文件头信息（已按 mtime 降序排列）
    headers = scan_memory_files(memory_dir)

    # 步骤 2：过滤已展示过的文件，避免在同一会话中重复注入相同记忆
    headers = [h for h in headers if h.filepath not in already_surfaced]

    if not headers:
        # 无候选文件（目录为空或全部已展示），直接返回空列表
        logger.debug(f"[relevance] memory 目录无候选文件: {memory_dir}")
        return []

    # 步骤 3：从查询文本中提取有效关键词
    query_keywords = _extract_keywords(query)

    if not query_keywords:
        # 查询文本无有效关键词（如纯符号、极短词），退化为按 mtime 返回最新文件

        logger.debug("[relevance] 查询无有效关键词，退化为按 mtime 返回")
        return [
            RelevantMemory(
                path=h.filepath,
                mtime_ms=h.mtime * 1000,  # 将 Unix 时间戳（秒）转换为毫秒
            )
            for h in headers[:max_results]
        ]

    # 步骤 4：对每个候选文件计算关键词相关性分数
    scored: List[tuple] = []  # 元素为 (score, header) 的元组列表
    for header in headers:
        score = _score_memory(header, query_keywords)
        if score > 0:
            # 只保留有实质交集的文件，分数为 0 的文件完全无关
            scored.append((score, header))

    if not scored:
        # 所有文件与查询均无关键词交集，返回空列表
        logger.debug(f"[relevance] 无相关记忆文件匹配查询: {query[:50]!r}")
        return []

    # 步骤 5：按分数降序排序，分数相同时保持原有 mtime 顺序（稳定排序）
    scored.sort(key=lambda x: x[0], reverse=True)

    # 步骤 6：截取前 max_results 个，构建返回列表
    result = [
        RelevantMemory(
            path=h.filepath,
            mtime_ms=h.mtime * 1000,
        )
        for _, h in scored[:max_results]
    ]

    logger.debug(
        f"[relevance] 找到 {len(result)} 个相关记忆文件 "
        f"(候选 {len(headers)} 个，查询关键词 {len(query_keywords)} 个)"
    )

    return result
