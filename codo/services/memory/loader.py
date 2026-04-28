"""兼容旧接口的 memory 加载器外观层，用于注入记忆上下文。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List, Optional

from codo.services.memory.manager import MemoryManager
from codo.services.memory.paths import ENTRYPOINT_NAME, MAX_ENTRYPOINT_BYTES, MAX_ENTRYPOINT_LINES
from codo.services.memory.scanner import MemoryScanner
from codo.services.memory.types import MemoryFile

class MemoryLoader:
    """从指定 memory 目录加载索引和记忆文件。"""

    def __init__(self, memory_dir: str):
        self.memory_dir = memory_dir
        self.manager = MemoryManager(memory_dir)
        self.scanner = MemoryScanner(memory_dir)

    def load_memory_index(self) -> Optional[str]:
        index_path = Path(self.memory_dir) / ENTRYPOINT_NAME
        if not index_path.exists():
            return None

        try:
            content = index_path.read_text(encoding="utf-8")
            lines = content.split("\n")
            if len(lines) > MAX_ENTRYPOINT_LINES:
                content = "\n".join(lines[:MAX_ENTRYPOINT_LINES])
            encoded = content.encode("utf-8")
            if len(encoded) > MAX_ENTRYPOINT_BYTES:
                content = encoded[:MAX_ENTRYPOINT_BYTES].decode(
                    "utf-8", errors="ignore"
                )
            return content
        except Exception:
            return None

    def load_memory_files(self, filenames: List[str]) -> List[MemoryFile]:
        memories: List[MemoryFile] = []
        for filename in filenames:
            path = str(Path(self.memory_dir) / filename)
            memory = self.manager.read_memory(path)
            if memory is not None:
                memories.append(memory)
        return memories

    def build_memory_context(self) -> str:
        index_content = self.load_memory_index()
        if not index_content:
            return ""

        return f"""# auto memory

You have a persistent, file-based memory system at `{self.memory_dir}`. This directory already exists and can be updated directly.

Use it to retain durable collaboration context across sessions: user preferences, confirmed project facts, long-running task context, references, and high-signal feedback.

## Types of memory

Store durable information in topic-based markdown files with frontmatter and keep `MEMORY.md` as the concise index.

## How to save memories

1. Write or update a dedicated markdown file with frontmatter fields `name`, `description`, and `type`.
2. Add or update a matching entry in `MEMORY.md`.

{index_content}
"""

    def get_memory_age_note(self, memory: MemoryFile) -> str:
        days = (datetime.now() - memory.mtime).days
        if days == 0:
            return "This memory is fresh (today)."
        if days == 1:
            return "This memory is 1 day old."
        if days < 7:
            return f"This memory is {days} days old."
        if days < 30:
            weeks = days // 7
            suffix = "s" if weeks > 1 else ""
            return f"This memory is {weeks} week{suffix} old."
        months = days // 30
        suffix = "s" if months > 1 else ""
        return (
            f"This memory is {months} month{suffix} old. "
            "Consider verifying if it's still accurate."
        )
