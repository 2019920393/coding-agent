"""兼容旧接口的扫描器外观层，基于统一后的 memory 存储实现。"""

from __future__ import annotations

import os
from datetime import datetime
from typing import List, Optional

import yaml

from codo.services.memory.manager import FRONTMATTER_REGEX
from codo.services.memory.types import MEMORY_TYPES

class MemoryHeader:
    """兼容扫描器返回的简化记忆头信息。"""

    def __init__(
        self,
        path: str,
        name: str,
        description: str,
        memory_type: str,
        mtime: datetime,
    ) -> None:
        self.path = path
        self.name = name
        self.description = description
        self.type = memory_type
        self.mtime = mtime

class MemoryScanner:
    """兼容旧版类式 API 的记忆扫描器。"""

    def __init__(self, memory_dir: str):
        self.memory_dir = memory_dir

    def scan_memory_files(self, max_files: int = 200) -> List[MemoryHeader]:
        headers: List[MemoryHeader] = []
        for root, _, files in os.walk(self.memory_dir):
            for filename in files:
                if not filename.endswith(".md") or filename == "MEMORY.md":
                    continue
                file_path = os.path.join(root, filename)
                header = self._read_header(file_path)
                if header is not None:
                    headers.append(header)

        headers.sort(key=lambda item: item.mtime, reverse=True)
        return headers[:max_files]

    def _read_header(self, file_path: str) -> Optional[MemoryHeader]:
        try:
            with open(file_path, "r", encoding="utf-8") as handle:
                content = "".join(line for _, line in zip(range(30), handle))
            match = FRONTMATTER_REGEX.match(content)
            if not match:
                return None
            frontmatter = yaml.safe_load(match.group(1)) or {}
            if not all(key in frontmatter for key in ("description", "type")):
                return None
            if frontmatter["type"] not in MEMORY_TYPES:
                return None
            name = frontmatter.get("name") or os.path.splitext(os.path.basename(file_path))[0].replace("_", " ")
            return MemoryHeader(
                path=file_path,
                name=name,
                description=frontmatter["description"],
                memory_type=frontmatter["type"],
                mtime=datetime.fromtimestamp(os.path.getmtime(file_path)),
            )
        except Exception:
            return None

    def format_memory_manifest(self, headers: List[MemoryHeader]) -> str:
        lines = []
        for header in headers:
            filename = os.path.basename(header.path)
            timestamp = header.mtime.strftime("%Y-%m-%d")
            lines.append(
                f"- [{header.type}] {filename} ({timestamp}): {header.description}"
            )
        return "\n".join(lines)
