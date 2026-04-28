"""
记忆扫描模块：读取并枚举带 frontmatter 的记忆文件。

"""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from codo.services.memory.paths import (
    ENTRYPOINT_NAME,
    MAX_ENTRYPOINT_BYTES,
    MAX_ENTRYPOINT_LINES,
    get_project_memory_dir,
)

@dataclass
class MemoryHeader:
    """记忆文件的头部信息。"""
    filename: str
    filepath: str
    mtime: float
    description: Optional[str]
    memory_type: Optional[str]

def parse_frontmatter(content: str) -> Dict[str, str]:
    """
    从 Markdown 文件中解析类 YAML 的 frontmatter。

    期望格式：
    ---
    description: 某条描述
    type: feedback
    ---
    """
    if not content.startswith("---"):
        return {}

    end_idx = content.find("---", 3)
    if end_idx == -1:
        return {}

    frontmatter_text = content[3:end_idx].strip()
    result = {}
    for line in frontmatter_text.split("\n"):
        line = line.strip()
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()

    return result

def scan_memory_files(memory_dir: str) -> List[MemoryHeader]:
    """
    扫描 memory 目录中的 `.md` 文件，并读取其 frontmatter。

    返回按 mtime 倒序排列的 `MemoryHeader` 列表（最新优先）。
    """
    memory_path = Path(memory_dir)
    if not memory_path.exists():
        return []

    headers = []
    for md_file in memory_path.rglob("*.md"):
        if md_file.name == ENTRYPOINT_NAME:
            continue

        try:
            # 只读取前 30 行，避免为了解析头部而加载整个大文件。
            with open(md_file, "r", encoding="utf-8") as f:
                lines = []
                for i, line in enumerate(f):
                    if i >= 30:
                        break
                    lines.append(line)
                content = "".join(lines)

            frontmatter = parse_frontmatter(content)
            stat = md_file.stat()

            relative_path = str(md_file.relative_to(memory_path))
            headers.append(MemoryHeader(
                filename=relative_path,
                filepath=str(md_file),
                mtime=stat.st_mtime,
                description=frontmatter.get("description"),
                memory_type=frontmatter.get("type"),
            ))
        except Exception:
            continue

    # 按修改时间倒序排列（最新优先）。
    headers.sort(key=lambda h: h.mtime, reverse=True)

    # 最多返回 200 个文件，避免清单过长。
    return headers[:200]

def format_memory_manifest(headers: List[MemoryHeader]) -> str:
    """
    将记忆文件头信息格式化为清单字符串。

    该清单会预注入抽取提示词，帮助 agent 感知现有记忆。

    """
    if not headers:
        return ""

    lines = []
    for header in headers:
        desc = f" — {header.description}" if header.description else ""
        type_str = f" [{header.memory_type}]" if header.memory_type else ""
        lines.append(f"- {header.filename}{type_str}{desc}")

    return "\n".join(lines)

def load_memory_index(cwd: str) -> Optional[str]:
    """
    加载 `MEMORY.md` 索引文件内容。

    会按当前实现的行数和字节数限制执行截断。
    """
    from codo.services.memory.paths import get_memory_index_path

    index_path = get_memory_index_path(cwd)
    if not index_path.exists():
        return None

    try:
        content = index_path.read_text(encoding="utf-8").strip()
        if not content:
            return None

        # 应用截断规则。
        lines = content.split("\n")
        was_line_truncated = len(lines) > MAX_ENTRYPOINT_LINES
        was_byte_truncated = len(content) > MAX_ENTRYPOINT_BYTES

        if was_line_truncated:
            content = "\n".join(lines[:MAX_ENTRYPOINT_LINES])

        if was_byte_truncated and len(content) > MAX_ENTRYPOINT_BYTES:
            # 尽量在字节上限前的最后一个换行处截断。
            last_newline = content.rfind("\n", 0, MAX_ENTRYPOINT_BYTES)
            if last_newline > 0:
                content = content[:last_newline]
            else:
                content = content[:MAX_ENTRYPOINT_BYTES]

        # 追加截断提示。
        warnings = []
        if was_line_truncated:
            warnings.append(f"line cap ({MAX_ENTRYPOINT_LINES})")
        if was_byte_truncated:
            warnings.append(f"byte cap ({MAX_ENTRYPOINT_BYTES})")
        if warnings:
            content += f"\n\n[Truncated — exceeded {', '.join(warnings)}]"

        return content
    except Exception:
        return None
