"""
User input history management

Manages user input history for:
- Up/Down arrow navigation
- Ctrl+R search
- Cross-session history

Storage format:
- Location: ~/.codo/history.jsonl
- Format: JSONL (one JSON object per line)
- Each line contains: display, timestamp, project, session_id, pasted_contents
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, AsyncGenerator
from uuid import uuid4

from codo.utils.config import get_user_dir

MAX_HISTORY_ITEMS = 100
MAX_PASTED_CONTENT_LENGTH = 1024

@dataclass
class PastedContent:
    """Pasted content (text or image)"""
    id: int
    type: str  # 'text' or 'image'
    content: str
    media_type: Optional[str] = None
    filename: Optional[str] = None

@dataclass
class StoredPastedContent:
    """Stored paste content - either inline or hash reference"""
    id: int
    type: str
    content: Optional[str] = None  # Inline content for small pastes
    content_hash: Optional[str] = None  # Hash reference for large pastes
    media_type: Optional[str] = None
    filename: Optional[str] = None

@dataclass
class HistoryEntry:
    """History entry for display"""
    display: str
    pasted_contents: Dict[int, PastedContent]

@dataclass
class LogEntry:
    """Internal log entry format"""
    display: str
    pasted_contents: Dict[int, StoredPastedContent]
    timestamp: float
    project: str
    session_id: Optional[str] = None

class InputHistory:
    """
    Manages user input history across sessions.

    参考：src/history.ts
    核心功能：
    1. 全局历史文件 - ~/.codo/history.jsonl（对齐 history.ts:115）
    2. Up/Down 箭头导航 - getHistory()（对齐 history.ts:190-217）
    3. Ctrl+R 搜索 - getTimestampedHistory()（对齐 history.ts:162-180）
    4. 当前会话优先 - 当前会话的条目优先显示（对齐 history.ts:201-206）
    5. 项目隔离 - 只显示当前项目的历史（对齐 history.ts:191-199）
    """

    def __init__(self, project_root: str, session_id: str, history_file: Optional[Path] = None):
        self.project_root = project_root
        self.session_id = session_id
        self._history_file = history_file  # Allow override for testing
        self._pending_entries: List[LogEntry] = []
        self._last_added_entry: Optional[LogEntry] = None
        self._skipped_timestamps: set = set()
        self._ensure_history_file()

    @property
    def history_file(self) -> Path:
        """Get history file path (dynamic to support testing)"""
        if self._history_file:
            return self._history_file
        return get_user_dir() / "history.jsonl"

    def _ensure_history_file(self):
        """Ensure history file exists"""
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.history_file.exists():
            self.history_file.touch(mode=0o600)

    def add_to_history(self, command: str, pasted_contents: Optional[Dict[int, PastedContent]] = None):
        """
        Add command to history.

        [Workflow]
        1. 创建 LogEntry
        2. 添加到 pending buffer
        3. 异步刷新到磁盘

        Args:
            command: User input command
            pasted_contents: Optional pasted content references
        """
        if not command.strip():
            return

        # Convert pasted contents to stored format
        stored_pasted_contents = {}
        if pasted_contents:
            for id_num, content in pasted_contents.items():
                # For now, store all content inline (no hash store yet)
                stored_pasted_contents[id_num] = StoredPastedContent(
                    id=content.id,
                    type=content.type,
                    content=content.content if content.type == 'text' else None,
                    media_type=content.media_type,
                    filename=content.filename
                )

        log_entry = LogEntry(
            display=command,
            pasted_contents=stored_pasted_contents,
            timestamp=datetime.now(timezone.utc).timestamp(),
            project=self.project_root,
            session_id=self.session_id
        )

        self._pending_entries.append(log_entry)
        self._last_added_entry = log_entry
        self._flush_history()

    def _flush_history(self):
        """
        Flush pending entries to disk.

        """
        if not self._pending_entries:
            return

        try:
            with open(self.history_file, 'a', encoding='utf-8') as f:
                for entry in self._pending_entries:
                    # Convert to dict for JSON serialization
                    entry_dict = {
                        'display': entry.display,
                        'pastedContents': {
                            str(k): {
                                'id': v.id,
                                'type': v.type,
                                'content': v.content,
                                'contentHash': v.content_hash,
                                'mediaType': v.media_type,
                                'filename': v.filename
                            }
                            for k, v in entry.pasted_contents.items()
                        },
                        'timestamp': entry.timestamp,
                        'project': entry.project,
                        'sessionId': entry.session_id
                    }
                    f.write(json.dumps(entry_dict) + '\n')

            self._pending_entries.clear()
        except Exception as e:
            # Log error but don't crash
            print(f"Failed to write history: {e}")

    def get_history(self, max_items: int = MAX_HISTORY_ITEMS) -> List[HistoryEntry]:
        """
        Get history entries for current project, with current session first.

        [Workflow]
        1. 读取 pending entries（未刷新到磁盘的）
        2. 读取磁盘文件（倒序）
        3. 当前会话的条目优先
        4. 过滤当前项目
        5. 去重和限制数量

        Returns:
            List of HistoryEntry objects, newest first
        """
        current_session_entries = []
        other_session_entries = []

        # First, add pending entries (newest first)
        for entry in reversed(self._pending_entries):
            if entry.timestamp in self._skipped_timestamps:
                continue
            if entry.project == self.project_root:
                current_session_entries.append(self._log_entry_to_history_entry(entry))

        # Then read from disk (newest first)
        if self.history_file.exists():
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    lines = f.readlines()

                # Read in reverse order (newest first)
                for line in reversed(lines):
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        entry_dict = json.loads(line)
                        entry = self._dict_to_log_entry(entry_dict)

                        # Skip if marked as skipped
                        if entry.timestamp in self._skipped_timestamps:
                            continue

                        # Filter by project
                        if entry.project != self.project_root:
                            continue

                        # Separate by session
                        history_entry = self._log_entry_to_history_entry(entry)
                        if entry.session_id == self.session_id:
                            current_session_entries.append(history_entry)
                        else:
                            other_session_entries.append(history_entry)

                        # Stop if we have enough
                        if len(current_session_entries) + len(other_session_entries) >= max_items:
                            break

                    except (json.JSONDecodeError, KeyError):
                        continue

            except Exception as e:
                print(f"Failed to read history: {e}")

        # Combine: current session first, then other sessions
        result = current_session_entries + other_session_entries
        return result[:max_items]

    def search_history(self, query: str, max_items: int = MAX_HISTORY_ITEMS) -> List[tuple[HistoryEntry, float]]:
        """
        Search history with fuzzy matching (for Ctrl+R).

        [Workflow]
        1. 读取当前项目的所有历史
        2. 按 display 文本去重
        3. 模糊匹配查询字符串
        4. 返回匹配结果和时间戳

        Args:
            query: Search query string
            max_items: Maximum number of results

        Returns:
            List of (HistoryEntry, timestamp) tuples, newest first
        """
        seen = set()
        results = []
        query_lower = query.lower()

        # Search pending entries first
        for entry in reversed(self._pending_entries):
            if entry.project != self.project_root:
                continue
            if entry.display in seen:
                continue
            if query_lower in entry.display.lower():
                seen.add(entry.display)
                results.append((
                    self._log_entry_to_history_entry(entry),
                    entry.timestamp
                ))

        # Search disk entries
        if self.history_file.exists():
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    lines = f.readlines()

                for line in reversed(lines):
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        entry_dict = json.loads(line)
                        entry = self._dict_to_log_entry(entry_dict)

                        if entry.project != self.project_root:
                            continue
                        if entry.display in seen:
                            continue
                        if query_lower in entry.display.lower():
                            seen.add(entry.display)
                            results.append((
                                self._log_entry_to_history_entry(entry),
                                entry.timestamp
                            ))

                        if len(results) >= max_items:
                            break

                    except (json.JSONDecodeError, KeyError):
                        continue

            except Exception as e:
                print(f"Failed to search history: {e}")

        return results[:max_items]

    def remove_last_from_history(self):
        """
        Remove the last added entry from history.

        用途：当用户按 Esc 中断时，撤销最后一次输入的历史记录

        [Workflow]
        1. 如果在 pending buffer 中，直接删除
        2. 如果已刷新到磁盘，添加到 skip set
        """
        if not self._last_added_entry:
            return

        entry = self._last_added_entry
        self._last_added_entry = None

        # Try to remove from pending buffer
        if entry in self._pending_entries:
            self._pending_entries.remove(entry)
        else:
            # Already flushed, add to skip set
            self._skipped_timestamps.add(entry.timestamp)

    def clear_pending_entries(self):
        """
        Clear pending entries without flushing.

        """
        self._pending_entries.clear()
        self._last_added_entry = None
        self._skipped_timestamps.clear()

    def _dict_to_log_entry(self, entry_dict: Dict[str, Any]) -> LogEntry:
        """Convert dict to LogEntry"""
        pasted_contents = {}
        for id_str, content_dict in entry_dict.get('pastedContents', {}).items():
            pasted_contents[int(id_str)] = StoredPastedContent(
                id=content_dict['id'],
                type=content_dict['type'],
                content=content_dict.get('content'),
                content_hash=content_dict.get('contentHash'),
                media_type=content_dict.get('mediaType'),
                filename=content_dict.get('filename')
            )

        return LogEntry(
            display=entry_dict['display'],
            pasted_contents=pasted_contents,
            timestamp=entry_dict['timestamp'],
            project=entry_dict['project'],
            session_id=entry_dict.get('sessionId')
        )

    def _log_entry_to_history_entry(self, entry: LogEntry) -> HistoryEntry:
        """Convert LogEntry to HistoryEntry"""
        pasted_contents = {}
        for id_num, stored in entry.pasted_contents.items():
            if stored.content:
                pasted_contents[id_num] = PastedContent(
                    id=stored.id,
                    type=stored.type,
                    content=stored.content,
                    media_type=stored.media_type,
                    filename=stored.filename
                )

        return HistoryEntry(
            display=entry.display,
            pasted_contents=pasted_contents
        )

def format_pasted_text_ref(id_num: int, num_lines: int) -> str:
    """
    Format pasted text reference.

    """
    if num_lines == 0:
        return f"[Pasted text #{id_num}]"
    return f"[Pasted text #{id_num} +{num_lines} lines]"

def format_image_ref(id_num: int) -> str:
    """
    Format image reference.

    """
    return f"[Image #{id_num}]"

def expand_pasted_text_refs(input_text: str, pasted_contents: Dict[int, PastedContent]) -> str:
    """
    Replace [Pasted text #N] placeholders with actual content.

    Args:
        input_text: Input text with placeholders
        pasted_contents: Map of paste ID to content

    Returns:
        Expanded text with placeholders replaced
    """
    import re

    # Find all references: [Pasted text #N] or [Pasted text #N +X lines]
    pattern = r'\[Pasted text #(\d+)(?:\s+\+\d+\s+lines)?\]'

    def replace_ref(match):
        paste_id = int(match.group(1))
        content = pasted_contents.get(paste_id)
        if content and content.type == 'text':
            return content.content
        return match.group(0)  # Keep original if not found

    return re.sub(pattern, replace_ref, input_text)
