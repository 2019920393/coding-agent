"""memory 文件的增删改查辅助方法。"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import yaml

from codo.services.memory.paths import ENTRYPOINT_NAME, get_memory_file_path
from codo.services.memory.types import MemoryFile, MemoryFrontmatter, MemoryIndexEntry

FRONTMATTER_REGEX = re.compile(r"^---\s*\n([\s\S]*?)---\s*\n?", re.MULTILINE)

class MemoryManager:
    """管理指定 memory 目录下的记忆文档。"""

    def __init__(self, memory_dir: str):
        self.memory_dir = memory_dir
        self.index_path = os.path.join(memory_dir, ENTRYPOINT_NAME)
        Path(memory_dir).mkdir(parents=True, exist_ok=True)

    def create_memory(
        self,
        name: str,
        description: str,
        memory_type: str,
        content: str,
        topic: Optional[str] = None,
    ) -> str:
        topic = topic or name
        file_path = get_memory_file_path(self.memory_dir, memory_type, topic)
        payload = {
            "name": name,
            "description": description,
            "type": memory_type,
        }
        with open(file_path, "w", encoding="utf-8") as handle:
            handle.write("---\n")
            handle.write(
                yaml.dump(payload, allow_unicode=True, default_flow_style=False)
            )
            handle.write("---\n\n")
            handle.write(content)

        self._add_to_index(name, os.path.basename(file_path), description)
        return file_path

    def read_memory(self, file_path: str) -> Optional[MemoryFile]:
        if not os.path.exists(file_path):
            return None

        try:
            raw_content = Path(file_path).read_text(encoding="utf-8")
            match = FRONTMATTER_REGEX.match(raw_content)
            if not match:
                return None

            frontmatter_str = match.group(1)
            content = raw_content[match.end() :].strip()
            frontmatter_data = yaml.safe_load(frontmatter_str) or {}
            if not frontmatter_data.get("name"):
                frontmatter_data["name"] = Path(file_path).stem.replace("_", " ")
            memory = MemoryFrontmatter(**frontmatter_data)
            mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
            return MemoryFile(
                path=file_path,
                frontmatter=memory,
                content=content,
                mtime=mtime,
            )
        except Exception:
            return None

    def update_memory(
        self,
        file_path: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        content: Optional[str] = None,
    ) -> bool:
        memory = self.read_memory(file_path)
        if memory is None:
            return False

        payload = {
            "name": name or memory.frontmatter.name or Path(file_path).stem.replace("_", " "),
            "description": description or memory.frontmatter.description,
            "type": memory.frontmatter.type,
        }
        body = content if content is not None else memory.content

        try:
            with open(file_path, "w", encoding="utf-8") as handle:
                handle.write("---\n")
                handle.write(
                    yaml.dump(payload, allow_unicode=True, default_flow_style=False)
                )
                handle.write("---\n\n")
                handle.write(body)
            if name or description:
                self._update_index(
                    os.path.basename(file_path),
                    payload["name"],
                    payload["description"],
                )
            return True
        except Exception:
            return False

    def delete_memory(self, file_path: str) -> bool:
        if not os.path.exists(file_path):
            return False

        try:
            os.remove(file_path)
            self._remove_from_index(os.path.basename(file_path))
            return True
        except Exception:
            return False

    def _add_to_index(self, title: str, filename: str, hook: str) -> None:
        entries = self._read_index()
        entries.append(MemoryIndexEntry(title=title, filename=filename, hook=hook))
        self._write_index(entries)

    def _update_index(self, filename: str, title: str, hook: str) -> None:
        entries = self._read_index()
        for entry in entries:
            if entry.filename == filename:
                entry.title = title
                entry.hook = hook
                break
        self._write_index(entries)

    def _remove_from_index(self, filename: str) -> None:
        entries = [entry for entry in self._read_index() if entry.filename != filename]
        self._write_index(entries)

    def _read_index(self) -> List[MemoryIndexEntry]:
        if not os.path.exists(self.index_path):
            return []

        pattern = r"- \[([^\]]+)\]\(([^\)]+)\) — (.+)"
        entries: List[MemoryIndexEntry] = []
        try:
            content = Path(self.index_path).read_text(encoding="utf-8")
            for match in re.finditer(pattern, content):
                entries.append(
                    MemoryIndexEntry(
                        title=match.group(1),
                        filename=match.group(2),
                        hook=match.group(3),
                    )
                )
        except Exception:
            return []
        return entries

    def _write_index(self, entries: List[MemoryIndexEntry]) -> None:
        with open(self.index_path, "w", encoding="utf-8") as handle:
            handle.write("# auto memory\n\n")
            handle.write("You have a persistent, file-based memory system.\n\n")
            handle.write("## MEMORY.md\n\n")
            for entry in entries:
                handle.write(f"- [{entry.title}]({entry.filename}) — {entry.hook}\n")
