"""
memory 抽取服务总入口。

对外提供：
- memory 路径与目录管理（paths）
- 记忆文件扫描与 frontmatter 解析（scan）
- 抽取提示词模板（prompts）
- 核心抽取逻辑，即分析最近消息的后台 agent（extract）
- 相关性匹配，即基于关键词的相关记忆查找（relevance）
"""

from codo.services.memory.extract import (
    MemoryExtractionState,
    extract_memories,
)
from codo.services.memory.paths import (
    ENTRYPOINT_NAME,
    MAX_ENTRYPOINT_BYTES,
    MAX_ENTRYPOINT_LINES,
    ensure_memory_dir,
    get_memory_base_dir,
    get_memory_dir,
    get_memory_index_path,
    is_memory_path,
)
from codo.services.memory.prompts import build_extract_prompt
from codo.services.memory.relevance import (
    RelevantMemory,
    find_relevant_memories,
)
from codo.services.memory.scan import (
    MemoryHeader,
    format_memory_manifest,
    load_memory_index,
    parse_frontmatter,
    scan_memory_files,
)
from codo.services.memory.types import MemoryFile, MemoryFrontmatter, MemoryType

__all__ = [
    "ENTRYPOINT_NAME",
    "MAX_ENTRYPOINT_BYTES",
    "MAX_ENTRYPOINT_LINES",
    "MemoryExtractionState",
    "MemoryFile",
    "MemoryFrontmatter",
    "MemoryHeader",
    "MemoryType",
    "RelevantMemory",
    "build_extract_prompt",
    "ensure_memory_dir",
    "extract_memories",
    "find_relevant_memories",
    "format_memory_manifest",
    "get_memory_base_dir",
    "get_memory_dir",
    "get_memory_index_path",
    "is_memory_path",
    "load_memory_index",
    "parse_frontmatter",
    "scan_memory_files",
]
