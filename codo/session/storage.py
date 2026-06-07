"""
会话存储管理器（简化版）

负责会话的持久化和加载：
1. JSONL 格式追加写入
2. 消息链记录
3. 核心元数据管理（标题、标签、模式、最后提示词）
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from codo.session.types import (
    CustomTitleEntry,
    LastPromptEntry,
    LoadedSession,
    ModeEntry,
    SessionMetadata,
    TagEntry,
    TranscriptMessage,
)

logger = logging.getLogger(__name__)


# ============================================================================
# 路径管理
# ============================================================================

def get_sessions_dir(cwd: str) -> Path:
    """获取会话存储目录"""
    home = Path.home()
    codo_dir = home / ".codo" / "sessions"
    sanitized = cwd.replace(":", "").replace("\\", "_").replace("/", "_")
    session_dir = codo_dir / sanitized
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def get_session_file_path(session_id: str, cwd: str) -> Path:
    """获取会话文件路径（JSONL 格式）"""
    sessions_dir = get_sessions_dir(cwd)
    return sessions_dir / f"{session_id}.jsonl"


def list_session_files(project_dir: str) -> list[tuple[str, str, int, float]]:
    """列出项目会话目录内的 JSONL 文件"""
    project_path = Path(project_dir)
    if not project_path.exists() or not project_path.is_dir():
        return []

    sessions: list[tuple[str, str, int, float]] = []
    for file_path in project_path.glob("*.jsonl"):
        # 跳过事件日志文件 (.events.jsonl)
        if file_path.name.endswith(".events.jsonl"):
            continue

        try:
            stat = file_path.stat()
        except OSError:
            continue
        sessions.append((file_path.stem, str(file_path), stat.st_size, stat.st_mtime))
    return sessions


# ============================================================================
# 会话存储管理器
# ============================================================================

class SessionStorage:
    """会话存储管理器"""

    def __init__(self, session_id: str, cwd: str):
        self.session_id = session_id
        self.cwd = cwd
        self.session_file: Path | None = None
        self._file_path = get_session_file_path(session_id, cwd)

        # 元数据缓存
        self.current_title: str | None = None
        self.current_tag: str | None = None
        self.current_mode: str | None = None
        self.current_last_prompt: str | None = None

        # 待写入条目（在文件创建前缓存）
        self.pending_entries: list[Any] = []

        # 已记录的消息 UUID 集合（用于去重）
        self.recorded_message_uuids: set[str] = set()
        self._last_parent_uuid: str | None = None

        self._bootstrap_state()

    def _bootstrap_state(self) -> None:
        """从现有会话恢复状态"""
        if self._file_path.exists():
            self.session_file = self._file_path

        loaded = load_session(self.session_id, self.cwd)
        if loaded is None:
            return

        self.recorded_message_uuids = {message.uuid for message in loaded.messages}
        self.current_title = loaded.metadata.custom_title
        self.current_tag = loaded.metadata.tag
        self.current_mode = loaded.metadata.mode
        self.current_last_prompt = loaded.metadata.last_prompt

        if loaded.messages:
            self._last_parent_uuid = loaded.messages[-1].uuid

    def load_messages(self) -> list[dict[str, Any]]:
        """加载会话消息历史"""
        loaded = load_session(self.session_id, self.cwd)
        if not loaded:
            return []

        restored_messages: list[dict[str, Any]] = []
        self.recorded_message_uuids.clear()

        for msg in loaded.messages:
            msg_dict = msg.model_dump()
            restored_messages.append(msg_dict)
            self.recorded_message_uuids.add(msg.uuid)

        # 同步元数据
        self.current_title = loaded.metadata.custom_title
        self.current_tag = loaded.metadata.tag
        self.current_mode = loaded.metadata.mode
        self.current_last_prompt = loaded.metadata.last_prompt

        return restored_messages

    def should_materialize(self, messages: list[dict[str, Any]]) -> bool:
        """检查是否应该创建会话文件"""
        return any(
            (msg.get("role") or msg.get("type")) in ("user", "assistant")
            for msg in messages
        )

    def materialize_session_file(self) -> None:
        """创建会话文件并写入缓存的元数据"""
        if self.session_file is not None:
            return

        self.session_file = self._file_path
        self._write_metadata()

        # 写入缓存的条目
        if self.pending_entries:
            for entry in self.pending_entries:
                self._append_entry(entry)
            self.pending_entries.clear()

    def _write_metadata(self) -> None:
        """写入元数据到文件"""
        if self.session_file is None:
            return

        if self.current_title:
            self._append_entry(CustomTitleEntry(
                custom_title=self.current_title,
                session_id=self.session_id,
                timestamp=datetime.now().isoformat(),
            ))

        if self.current_tag:
            self._append_entry(TagEntry(
                tag=self.current_tag,
                session_id=self.session_id,
                timestamp=datetime.now().isoformat(),
            ))

        if self.current_mode:
            self._append_entry(ModeEntry(
                mode=self.current_mode,
                session_id=self.session_id,
                timestamp=datetime.now().isoformat(),
            ))

        if self.current_last_prompt:
            self._append_entry(LastPromptEntry(
                last_prompt=self.current_last_prompt,
                session_id=self.session_id,
                timestamp=datetime.now().isoformat(),
            ))

    def _append_entry(self, entry: Any) -> None:
        """追加条目到 JSONL 文件"""
        if self.session_file is None:
            self.pending_entries.append(entry)
            return

        data = entry.model_dump() if hasattr(entry, 'model_dump') else entry
        line = json.dumps(data, ensure_ascii=False) + "\n"

        with open(self.session_file, "a", encoding="utf-8") as f:
            f.write(line)

    def record_messages(
        self,
        messages: list[dict[str, Any]],
        parent_uuid: str | None = None,
    ) -> str | None:
        """记录消息链到会话文件"""
        if self.session_file is None and self.should_materialize(messages):
            self.materialize_session_file()

        if parent_uuid is None:
            parent_uuid = self._last_parent_uuid

        # 过滤出新消息
        new_messages = []
        current_parent = parent_uuid

        for msg in messages:
            role = str(msg.get("role") or msg.get("type") or "").strip().lower()
            if role:
                msg["role"] = role
                msg["type"] = role

            msg_uuid = msg.get("uuid")
            if not msg_uuid:
                msg_uuid = str(uuid4())
                msg["uuid"] = msg_uuid

            if msg_uuid in self.recorded_message_uuids:
                if msg.get("role") in ("user", "assistant"):
                    current_parent = msg_uuid
            else:
                new_messages.append(msg)
                self.recorded_message_uuids.add(msg_uuid)

        if not new_messages:
            self._last_parent_uuid = current_parent
            return current_parent

        # 写入消息链
        last_uuid = self._insert_message_chain(new_messages, current_parent)
        self._last_parent_uuid = last_uuid

        return last_uuid

    def _insert_message_chain(
        self,
        messages: list[dict[str, Any]],
        parent_uuid: str | None,
    ) -> str | None:
        """插入消息链"""
        last_uuid = parent_uuid

        for msg in messages:
            msg["parent_uuid"] = last_uuid
            last_uuid = msg["uuid"]

            if "timestamp" not in msg:
                msg["timestamp"] = datetime.now().isoformat()

            self._append_entry(msg)

        return last_uuid

    def save_title(self, title: str, source: str = "user") -> None:
        """保存会话标题"""
        if self.session_file is None:
            self.materialize_session_file()

        self.current_title = title
        self._append_entry(CustomTitleEntry(
            custom_title=title,
            session_id=self.session_id,
            timestamp=datetime.now().isoformat(),
        ))

    def save_tag(self, tag: str) -> None:
        """保存标签"""
        if self.session_file is None:
            self.materialize_session_file()

        self.current_tag = tag
        self._append_entry(TagEntry(
            tag=tag,
            session_id=self.session_id,
            timestamp=datetime.now().isoformat(),
        ))

    def save_mode(self, mode: str) -> None:
        """保存模式"""
        if self.session_file is None:
            self.materialize_session_file()

        self.current_mode = mode
        self._append_entry(ModeEntry(
            mode=mode,
            session_id=self.session_id,
            timestamp=datetime.now().isoformat(),
        ))

    def save_last_prompt(self, prompt: str) -> None:
        """保存最后提示词"""
        if self.session_file is None:
            self.materialize_session_file()

        self.current_last_prompt = prompt
        self._append_entry(LastPromptEntry(
            last_prompt=prompt,
            session_id=self.session_id,
            timestamp=datetime.now().isoformat(),
        ))

    def get_session_info(self) -> dict[str, Any]:
        """获取会话基本信息"""
        info: dict[str, Any] = {
            "session_id": self.session_id,
            "exists": False,
            "message_count": 0,
            "file_size": 0,
            "created": None,
            "modified": None,
            "user_title": None,
            "first_prompt": None,
        }

        path = self.session_file or self._file_path
        if not path.exists():
            return info

        info["exists"] = True
        stat = path.stat()
        info["file_size"] = stat.st_size
        info["created"] = datetime.fromtimestamp(stat.st_ctime).isoformat()
        info["modified"] = datetime.fromtimestamp(stat.st_mtime).isoformat()

        # 扫描 JSONL 提取消息数和标题
        message_count = 0
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        record_type = record.get("type", "")
                        if record_type in ("user", "assistant"):
                            message_count += 1
                            if record_type == "user" and info["first_prompt"] is None:
                                content = record.get("content", "")
                                if isinstance(content, str) and content.strip():
                                    text = content.strip().split("\n")[0][:50]
                                    if len(content.strip()) > 50:
                                        text += "…"
                                    info["first_prompt"] = text
                        elif record_type == "custom-title":
                            title = record.get("custom_title") or record.get("title")
                            if title:
                                info["user_title"] = title
                    except Exception:
                        continue
        except Exception:
            pass

        info["message_count"] = message_count
        return info

    def delete_session(self) -> None:
        """删除会话文件"""
        if self.session_file and self.session_file.exists():
            self.session_file.unlink()
        elif self._file_path.exists():
            self._file_path.unlink()

    # 兼容层方法
    async def record_message(
        self,
        message: dict[str, Any],
        parent_uuid: str | None = None,
    ) -> str | None:
        """兼容 async 接口"""
        return self.record_messages([message], parent_uuid)

    async def insert_message_chain(
        self,
        messages: list[dict[str, Any]],
        starting_parent_uuid: str | None = None,
    ) -> str | None:
        """兼容 async 接口"""
        return self.record_messages(messages, starting_parent_uuid)

    async def load_metadata(self) -> dict[str, Any]:
        """兼容 async 接口"""
        return {
            "user_title": self.current_title,
            "tag": self.current_tag,
            "mode": self.current_mode,
        }

    def record_runtime_event(self, event: dict[str, Any]) -> None:
        """兼容接口（空操作）"""
        pass


# ============================================================================
# 模块级函数
# ============================================================================

def list_sessions(cwd: str | None = None) -> list[dict[str, Any]]:
    """列出指定工作目录下所有可用会话"""
    target_cwd = cwd or os.getcwd()
    sessions: list[dict[str, Any]] = []
    directory = get_sessions_dir(target_cwd)
    if not directory.exists():
        return []

    for session_file in directory.glob("*.jsonl"):
        # 跳过事件日志文件 (.events.jsonl)
        if session_file.name.endswith(".events.jsonl"):
            continue

        storage = SessionStorage(session_file.stem, target_cwd)
        info = storage.get_session_info()
        if info.get("exists"):
            sessions.append(info)

    sessions.sort(key=lambda item: item.get("modified") or "", reverse=True)
    return sessions


def get_latest_session(cwd: str | None = None) -> str | None:
    """获取最近修改的会话 ID"""
    sessions = list_sessions(cwd)
    if sessions:
        return str(sessions[0].get("session_id") or "")
    return None


def delete_session(session_id: str, cwd: str | None = None) -> None:
    """删除指定会话"""
    SessionStorage(session_id, cwd or os.getcwd()).delete_session()


# ============================================================================
# 会话加载
# ============================================================================

def load_session(session_id: str, cwd: str) -> LoadedSession | None:
    """加载会话"""
    session_file = get_session_file_path(session_id, cwd)
    if not session_file.exists():
        return None
    return load_session_from_file(session_file, session_id)


def load_session_from_file(
    session_file: Path,
    session_id: str,
) -> LoadedSession:
    """从文件加载会话"""
    messages: list[TranscriptMessage] = []
    metadata = SessionMetadata(session_id=session_id)

    with open(session_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            try:
                entry = json.loads(line)
                entry_type = entry.get("type")

                if entry_type in ("user", "assistant"):
                    msg = TranscriptMessage.model_validate(entry)
                    messages.append(msg)

                elif entry_type == "custom-title":
                    metadata.custom_title = entry.get("custom_title") or entry.get("title")

                elif entry_type == "tag":
                    metadata.tag = entry.get("tag")

                elif entry_type == "mode":
                    metadata.mode = entry.get("mode")

                elif entry_type == "last-prompt":
                    metadata.last_prompt = entry.get("last_prompt")

            except Exception as e:
                logger.warning("Failed to parse session entry: %s", e)
                continue

    return LoadedSession(
        session_id=session_id,
        messages=messages,
        metadata=metadata,
    )


# ============================================================================
# 兼容 SessionManager
# ============================================================================

class SessionManager:
    """兼容旧的 SessionManager 静态类"""

    @staticmethod
    def list_sessions(cwd: str | None = None) -> list[dict[str, Any]]:
        return list_sessions(cwd)

    @staticmethod
    def get_latest_session(cwd: str | None = None) -> str | None:
        return get_latest_session(cwd)

    @staticmethod
    def delete_session(session_id: str, cwd: str | None = None) -> None:
        delete_session(session_id, cwd)
