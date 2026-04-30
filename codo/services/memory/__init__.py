"""
memory 抽取服务总入口。

对外提供：
- memory 路径与目录管理（paths）
- 记忆文件扫描与 frontmatter 解析（scan）
- 抽取提示词模板（prompts）
- 核心抽取逻辑，即分析最近消息的后台 agent（extract）
- 相关性匹配，即基于关键词的相关记忆查找（relevance）
"""

from codo.services.memory.paths import (
    get_memory_base_dir,
    get_auto_memory_path,
    get_project_memory_dir,
    ensure_memory_dir,
    get_memory_index_path,
    get_memory_file_path,
    sanitize_filename,
    is_memory_path,
    ENTRYPOINT_NAME,
    MAX_ENTRYPOINT_LINES,
    MAX_ENTRYPOINT_BYTES,
)
from codo.services.memory.scan import (
    MemoryHeader,
    parse_frontmatter,
    scan_memory_files,
    format_memory_manifest,
    load_memory_index,
)
from codo.services.memory.types import MemoryType, MemoryFrontmatter, MemoryFile
from codo.services.memory.manager import MemoryManager
from codo.services.memory.prompts import build_extract_prompt
from codo.services.memory.extract import (
    extract_memories,
    MemoryExtractionState,
)
from codo.services.memory.relevance import (
    RelevantMemory,
    find_relevant_memories,
)
