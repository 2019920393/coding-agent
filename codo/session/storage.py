"""
会话存储管理器

负责会话的持久化和加载：
1. JSONL 格式追加写入
2. 消息链记录
3. 元数据管理
4. 会话文件加载和解析
"""

import json
import os
import re
from pathlib import Path
from typing import Optional, List, Dict, Any, Set, Tuple
from datetime import datetime
from uuid import uuid4

from codo.session.types import (
    TranscriptEntry,
    TranscriptMessage,
    SessionMetadata,
    LoadedSession,
    SessionEvent,
    SessionSnapshot,
    CustomTitleEntry,
    TagEntry,
    AgentNameEntry,
    AgentColorEntry,
    ModeEntry,
    LastPromptEntry,
    ContentReplacementEntry,
)

_RUNTIME_REPLAY_EVENT_TYPES = {
    "tool_started",
    "tool_progress",
    "tool_completed",
    "tool_result",
    "todo_updated",
    "agent_started",
    "agent_delta",
    "agent_tool_started",
    "agent_tool_completed",
    "agent_completed",
    "agent_error",
    "status_changed",
    "checkpoint_restored",
    "interrupt_ack",
    "turn_completed",
}

_RETRYABLE_RUNTIME_PHASES = {
    "prepare_turn",
    "stream_assistant",
    "collect_tool_calls",
    "execute_tools",
    "wait_interaction",
    "apply_interaction_result",
    "stop_hooks",
    "compact",
}

_SNAPSHOT_REFRESH_EVENT_TYPES = {
    "interaction_requested",
    "interaction_resolved",
    "tool_completed",
    "tool_result",
    "todo_updated",
    "agent_started",
    "agent_delta",
    "agent_tool_started",
    "agent_tool_completed",
    "agent_completed",
    "agent_error",
    "status_changed",
    "checkpoint_restored",
    "interrupt_ack",
    "turn_completed",
    "permission_mode_changed",
}

# ============================================================================
# 路径管理
# ============================================================================

def get_sessions_dir(cwd: str) -> Path:
    """
    获取会话存储目录

    Args:
        cwd: 当前工作目录

    Returns:
        会话存储目录路径
    """
    # 使用 ~/.codo/sessions/<sanitized-cwd>/ 作为存储目录
    home = Path.home()
    codo_dir = home / ".codo" / "sessions"

    # 清理 cwd 路径作为子目录名
    sanitized = cwd.replace(":", "").replace("\\", "_").replace("/", "_")
    session_dir = codo_dir / sanitized

    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir



def get_session_file_path(session_id: str, cwd: str) -> Path:
    """
    获取会话文件路径

    Args:
        session_id: 会话 ID
        cwd: 当前工作目录

    Returns:
        会话文件路径（JSONL 格式）
    """
    sessions_dir = get_sessions_dir(cwd)
    return sessions_dir / f"{session_id}.jsonl"


# 新版append-onlu事件日志
def get_session_event_log_path(session_id: str, cwd: str) -> Path:
    sessions_dir = get_sessions_dir(cwd)
    return sessions_dir / f"{session_id}.events.jsonl"
#事件日志的物化快照（加速加载）
def get_session_snapshot_path(session_id: str, cwd: str) -> Path:
    sessions_dir = get_sessions_dir(cwd)
    return sessions_dir / f"{session_id}.snapshot.json"

def list_session_files(project_dir: str) -> List[Tuple[str, str, int, float]]:
    """列出项目会话目录内的转录 JSONL 文件"""
    project_path = Path(project_dir)
    if not project_path.exists() or not project_path.is_dir():
        return []

    sessions: List[Tuple[str, str, int, float]] = []
    for file_path in project_path.glob("*.jsonl"):
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
    """
    会话存储管理器

    负责：
    - 追加写入 JSONL
    - 缓存元数据
    - 延迟创建会话文件
    - 管理写入队列
    """

    def __init__(self, session_id: str, cwd: str):
        """
        初始化会话存储

        Args:
            session_id: 会话 ID
            cwd: 当前工作目录
        """
        self.session_id = session_id
        self.cwd = cwd
        self.transcript_file: Path = get_session_file_path(session_id, cwd)
        self.session_file: Optional[Path] = None
        self.event_log_file: Path = get_session_event_log_path(session_id, cwd)
        self.snapshot_file: Path = get_session_snapshot_path(session_id, cwd)

        # 元数据缓存
        self.current_title: Optional[str] = None  # 不太理解
        self.current_tag: Optional[str] = None      # 不太理解
        self.current_agent_name: Optional[str] = None   # 不太理解
        self.current_agent_color: Optional[str] = None  # 不太理解
        self.current_mode: Optional[str] = None # 不太理解
        self.current_last_prompt: Optional[str] = None   # 不太理解

        # 待写入条目（在文件创建前缓存）
        self.pending_entries: List[TranscriptEntry] = []

        # 已记录的消息 UUID 集合（用于去重）
        self.recorded_message_uuids: Set[str] = set()
        self._snapshot_messages: List[Dict[str, Any]] = []
        self._last_parent_uuid: Optional[str] = None
        self._bootstrap_existing_transcript_state()

    def _bootstrap_existing_transcript_state(self) -> None:
        """从现有文件恢复已记录的通用唯一识别码及当前文稿路径"""
        transcript_path = self.transcript_file
        if transcript_path.exists():
            self.session_file = transcript_path

        loaded = load_session(self.session_id, self.cwd)
        if loaded is None:
            loaded = load_session_from_events(self.session_id, self.load_events())
        if loaded is None:
            return

        self.recorded_message_uuids = {message.uuid for message in loaded.messages}
        self.current_title = loaded.metadata.custom_title
        self.current_tag = loaded.metadata.tag
        self.current_agent_name = loaded.metadata.agent_name
        self.current_agent_color = loaded.metadata.agent_color
        self.current_mode = loaded.metadata.mode
        self.current_last_prompt = loaded.metadata.last_prompt

        leaf_uuid = None
        if loaded.leaf_uuids:
            leaf_messages = [message for message in loaded.messages if message.uuid in loaded.leaf_uuids]
            if leaf_messages:
                leaf_uuid = max(leaf_messages, key=lambda message: message.timestamp or "").uuid
            else:
                leaf_uuid = loaded.leaf_uuids[-1]
        elif loaded.messages:
            leaf_uuid = loaded.messages[-1].uuid
        self._last_parent_uuid = leaf_uuid

    def _refresh_snapshot_messages_from_transcript(self, leaf_uuid: Optional[str] = None) -> None:
        """根据 transcript 当前叶子链重建 snapshot 消息视图。"""
        loaded = load_session(self.session_id, self.cwd)
        if not loaded:
            loaded = load_session_from_events(self.session_id, self.load_events())
            if not loaded:
                return

        target_leaf_uuid = leaf_uuid
        if not target_leaf_uuid and loaded.leaf_uuids:
            leaf_messages = [
                msg for msg in loaded.messages
                if msg.uuid in loaded.leaf_uuids
            ]
            if leaf_messages:
                latest_leaf = max(
                    leaf_messages,
                    key=lambda msg: msg.timestamp or "",
                )
                target_leaf_uuid = latest_leaf.uuid
            else:
                target_leaf_uuid = loaded.leaf_uuids[-1]

        chain = build_conversation_chain(loaded.messages, target_leaf_uuid) if target_leaf_uuid else loaded.messages
        self._snapshot_messages = [msg.model_dump() for msg in chain]

    def load_messages(self, leaf_uuid: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        加载会话消息历史

        Args:
            leaf_uuid: 指定恢复的叶子节点；为空时自动选择最新叶子链路

        Returns:
            恢复后的消息列表（dict 格式）
        """
        events = self.load_events() if leaf_uuid is None else self.load_events()
        latest_event_id = events[-1].event_id if events else None
        snapshot = self.load_snapshot() if leaf_uuid is None else None
        if (
            snapshot is not None
            and snapshot.messages
            and (latest_event_id is None or snapshot.last_event_id == latest_event_id)
        ):
            self._snapshot_messages = [dict(message) for message in snapshot.messages]
            self.recorded_message_uuids = {
                str(message.get("uuid"))
                for message in self._snapshot_messages
                if isinstance(message, dict) and message.get("uuid")
            }
            self.current_title = snapshot.metadata.custom_title
            self.current_tag = snapshot.metadata.tag
            self.current_agent_name = snapshot.metadata.agent_name
            self.current_agent_color = snapshot.metadata.agent_color
            self.current_mode = snapshot.metadata.mode
            self.current_last_prompt = snapshot.metadata.last_prompt
            if self._snapshot_messages:
                return [dict(message) for message in self._snapshot_messages]

        events_loaded = load_session_from_events(self.session_id, events)
        if events_loaded is not None:
            target_leaf_uuid = leaf_uuid
            if not target_leaf_uuid and events_loaded.leaf_uuids:
                leaf_messages = [
                    msg for msg in events_loaded.messages
                    if msg.uuid in events_loaded.leaf_uuids
                ]
                if leaf_messages:
                    latest_leaf = max(leaf_messages, key=lambda msg: msg.timestamp or "")
                    target_leaf_uuid = latest_leaf.uuid
                else:
                    target_leaf_uuid = events_loaded.leaf_uuids[-1]

            chain = build_conversation_chain(events_loaded.messages, target_leaf_uuid) if target_leaf_uuid else events_loaded.messages
            restored_messages = [msg.model_dump() for msg in chain]
            self._snapshot_messages = [dict(message) for message in restored_messages]
            self.recorded_message_uuids = {msg.uuid for msg in events_loaded.messages}
            self.current_title = events_loaded.metadata.custom_title
            self.current_tag = events_loaded.metadata.tag
            self.current_agent_name = events_loaded.metadata.agent_name
            self.current_agent_color = events_loaded.metadata.agent_color
            self.current_mode = events_loaded.metadata.mode
            self.current_last_prompt = events_loaded.metadata.last_prompt
            return restored_messages

        loaded = load_session(self.session_id, self.cwd)
        if not loaded:
            return []

        target_leaf_uuid = leaf_uuid
        if not target_leaf_uuid and loaded.leaf_uuids:
            # 在叶子节点中选择时间戳最新的
            leaf_messages = [
                msg for msg in loaded.messages
                if msg.uuid in loaded.leaf_uuids
            ]
            if leaf_messages:
                latest_leaf = max(
                    leaf_messages,
                    key=lambda msg: msg.timestamp or "",
                )
                target_leaf_uuid = latest_leaf.uuid
            else:
                target_leaf_uuid = loaded.leaf_uuids[-1]

        if target_leaf_uuid:
            chain = build_conversation_chain(loaded.messages, target_leaf_uuid)
        else:
            chain = loaded.messages

        restored_messages: List[Dict[str, Any]] = []
        self.recorded_message_uuids.clear()

        for msg in chain:
            msg_dict = msg.model_dump()
            restored_messages.append(msg_dict)
            self.recorded_message_uuids.add(msg.uuid)
        self._snapshot_messages = [dict(message) for message in restored_messages]

        self.current_title = loaded.metadata.custom_title
        self.current_tag = loaded.metadata.tag
        self.current_agent_name = loaded.metadata.agent_name
        self.current_agent_color = loaded.metadata.agent_color
        self.current_mode = loaded.metadata.mode
        self.current_last_prompt = loaded.metadata.last_prompt

        return restored_messages

    def should_materialize(self, messages: List[Dict[str, Any]]) -> bool:
        """
        检查是否应该创建会话文件

        Args:
            messages: 消息列表

        Returns:
            是否应该创建文件
        """
        # 只有当有真正的 user/assistant 消息时才创建文件
        return any(
            (msg.get("role") or msg.get("type")) in ("user", "assistant")
            for msg in messages
        )

    def materialize_session_file(self) -> None:
        """
        创建会话文件并写入缓存的元数据

        """
        if self.session_file is not None:
            return

        self.session_file = self.transcript_file

        # 写入元数据
        self._reappend_session_metadata()

        # 写入缓存的条目
        if self.pending_entries:
            for entry in self.pending_entries:
                self._append_entry_to_file(entry)
            self.pending_entries.clear()

    def _ensure_transcript_materialized(self) -> None:
        """为仅含元数据的兼容路径生成实例化副本文件"""
        if self.session_file is None:
            self.materialize_session_file()

    def _reappend_session_metadata(self) -> None:
        """
        重新追加会话元数据到文件

        这样可以确保元数据始终在文件的"尾部窗口"中，
        方便快速读取最新元数据而不需要扫描整个文件。
        """
        if self.session_file is None:
            return

        # 追加各种元数据条目
        if self.current_title:
            self._append_entry_to_file(CustomTitleEntry(
                custom_title=self.current_title,
                session_id=self.session_id,
                timestamp=datetime.now().isoformat(),
            ))

        if self.current_tag:
            self._append_entry_to_file(TagEntry(
                tag=self.current_tag,
                session_id=self.session_id,
                timestamp=datetime.now().isoformat(),
            ))

        if self.current_agent_name:
            self._append_entry_to_file(AgentNameEntry(
                agent_name=self.current_agent_name,
                session_id=self.session_id,
                timestamp=datetime.now().isoformat(),
            ))

        if self.current_agent_color:
            self._append_entry_to_file(AgentColorEntry(
                agent_color=self.current_agent_color,
                session_id=self.session_id,
                timestamp=datetime.now().isoformat(),
            ))

        if self.current_mode:
            self._append_entry_to_file(ModeEntry(
                mode=self.current_mode,
                session_id=self.session_id,
                timestamp=datetime.now().isoformat(),
            ))

        if self.current_last_prompt:
            self._append_entry_to_file(LastPromptEntry(
                last_prompt=self.current_last_prompt,
                session_id=self.session_id,
                timestamp=datetime.now().isoformat(),
            ))

    def _append_entry_to_file(self, entry: Any) -> None:
        """
        追加条目到 JSONL 文件

        Args:
            entry: 要追加的条目
        """
        if self.session_file is None:
            # 文件未创建，缓存条目
            self.pending_entries.append(entry)
            return

        # 序列化为 JSON 并追加换行
        data = entry.model_dump() if hasattr(entry, 'model_dump') else entry
        line = json.dumps(data, ensure_ascii=False) + "\n"

        # 追加写入文件
        with open(self.session_file, "a", encoding="utf-8") as f:
            f.write(line)

    def append_event(
        self,
        event_type: str,
        payload: Dict[str, Any],
        *,
        event_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> SessionEvent:
        """追加运行时事件到 append-only event log。"""
        self.event_log_file.parent.mkdir(parents=True, exist_ok=True)
        event = SessionEvent(
            event_id=event_id or str(uuid4()),
            session_id=self.session_id,
            event_type=event_type,
            payload=payload,
            created_at=created_at or datetime.now().isoformat(),
            agent_id=agent_id,
        )
        with open(self.event_log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event.model_dump(), ensure_ascii=False) + "\n")
        return event

    def record_runtime_event(self, event: Dict[str, Any]) -> Optional[SessionEvent]:
        """记录规范化后的运行时事件，供审计与恢复使用。"""
        if not isinstance(event, dict) or "type" not in event:
            return None
        payload = {key: value for key, value in event.items() if key != "type"}
        agent_id = payload.get("agent_id")
        if agent_id is not None:
            agent_id = str(agent_id or "") or None
        entry = self.append_event(str(event["type"]), payload, agent_id=agent_id)
        if str(event["type"]) in _SNAPSHOT_REFRESH_EVENT_TYPES:
            self.save_snapshot()
        return entry

    def _load_events_from_disk(self) -> List[SessionEvent]:
        events: List[SessionEvent] = []
        if not self.event_log_file.exists():
            return events
        with open(self.event_log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try: #把 JSON 格式的字符串，自动转换成对应的 Pydantic 模型对象
                    events.append(SessionEvent.model_validate_json(line))
                except Exception:
                    continue
        return events

    def load_events(self) -> List[SessionEvent]:
        """加载当前会话的事件日志。"""
        return self._load_events_from_disk()

    def save_snapshot(self) -> SessionSnapshot:
        """把当前消息与元数据物化为 snapshot。"""
        self.snapshot_file.parent.mkdir(parents=True, exist_ok=True)
        events = self.load_events()
        loaded_from_events = load_session_from_events(self.session_id, events)
        runtime_state = build_runtime_state_from_events(events)
        if loaded_from_events is not None:
            target_leaf_uuid = None
            if loaded_from_events.leaf_uuids:
                leaf_messages = [
                    msg for msg in loaded_from_events.messages
                    if msg.uuid in loaded_from_events.leaf_uuids
                ]
                if leaf_messages:
                    latest_leaf = max(leaf_messages, key=lambda msg: msg.timestamp or "")
                    target_leaf_uuid = latest_leaf.uuid
                else:
                    target_leaf_uuid = loaded_from_events.leaf_uuids[-1]
            snapshot_messages = [
                msg.model_dump()
                for msg in (
                    build_conversation_chain(loaded_from_events.messages, target_leaf_uuid)
                    if target_leaf_uuid
                    else loaded_from_events.messages
                )
            ]
            metadata = loaded_from_events.metadata
        else:
            snapshot_messages = self._snapshot_messages
            metadata = SessionMetadata(
                session_id=self.session_id,
                custom_title=self.current_title,
                tag=self.current_tag,
                agent_name=self.current_agent_name,
                agent_color=self.current_agent_color,
                mode=self.current_mode,
                last_prompt=self.current_last_prompt,
                updated_at=datetime.now().isoformat(),
            )

        self._snapshot_messages = [dict(message) for message in snapshot_messages]
        snapshot = SessionSnapshot(
            session_id=self.session_id,
            messages=snapshot_messages,
            runtime_state=runtime_state,
            metadata=metadata,
            last_event_id=events[-1].event_id if events else None,
            updated_at=datetime.now().isoformat(),
        )
        with open(self.snapshot_file, "w", encoding="utf-8") as f:
            json.dump(snapshot.model_dump(), f, ensure_ascii=False, indent=2)
        return snapshot

    def load_snapshot(self) -> Optional[SessionSnapshot]:
        """读取当前 snapshot；不存在或损坏时返回 None。"""
        if not self.snapshot_file.exists():
            return None
        with open(self.snapshot_file, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if isinstance(payload, dict) and isinstance(payload.get("messages"), list):
            payload["messages"] = [msg for msg in payload["messages"] if isinstance(msg, dict) and msg.get("uuid")]
        return SessionSnapshot.model_validate(payload)

    def load_runtime_state(self) -> Dict[str, Any]:
        """加载派生的运行时状态（todo/checkpoint/agent replay 等）。"""
        events = self.load_events()
        latest_event_id = events[-1].event_id if events else None
        snapshot = self.load_snapshot()
        if (
            snapshot is not None
            and (latest_event_id is None or snapshot.last_event_id == latest_event_id)
            and isinstance(snapshot.runtime_state, dict)
            and snapshot.runtime_state
        ):
            return json.loads(json.dumps(snapshot.runtime_state))
        return build_runtime_state_from_events(events)

    async def record_message(
        self,
        message: Dict[str, Any],
        parent_uuid: Optional[str] = None,
    ) -> Optional[str]:
        """Compatibility async wrapper for recording a single message."""
        effective_parent = parent_uuid if parent_uuid is not None else self._last_parent_uuid
        return self.record_messages([message], parent_uuid=effective_parent)

    async def insert_message_chain(
        self,
        messages: List[Dict[str, Any]],
        starting_parent_uuid: Optional[str] = None,
    ) -> Optional[str]:
        """Compatibility async wrapper matching the legacy SessionStorage API."""
        effective_parent = (
            starting_parent_uuid if starting_parent_uuid is not None else self._last_parent_uuid
        )
        return self.record_messages(messages, parent_uuid=effective_parent)

    def record_messages(
        self,
        messages: List[Dict[str, Any]],
        parent_uuid: Optional[str] = None,
    ) -> Optional[str]:
        """
        记录消息链到会话文件

        Args:
            messages: 消息列表
            parent_uuid: 起始父消息 UUID

        Returns:
            最后记录的消息 UUID
        """
        # 检查是否需要创建文件
        if self.session_file is None and self.should_materialize(messages):
            self.materialize_session_file()

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
                # 生成 UUID
                msg_uuid = str(uuid4())
                msg["uuid"] = msg_uuid

            if msg_uuid in self.recorded_message_uuids:
                # 已记录，更新 parent
                if msg.get("role") in ("user", "assistant"):
                    current_parent = msg_uuid
            else:
                # 新消息
                new_messages.append(msg)
                self.recorded_message_uuids.add(msg_uuid)

        if not new_messages:
            self._last_parent_uuid = current_parent
            return current_parent

        # 写入消息链
        last_uuid = self._insert_message_chain(new_messages, current_parent)
        for message in new_messages:
            self.append_event(
                "message_recorded",
                {
                    "message": dict(message),
                    "parent_uuid": message.get("parent_uuid"),
                },
            )
        self._refresh_snapshot_messages_from_transcript(last_uuid)
        self.save_snapshot()
        self._last_parent_uuid = last_uuid

        return last_uuid

    def _insert_message_chain(
        self,
        messages: List[Dict[str, Any]],
        parent_uuid: Optional[str],
    ) -> Optional[str]:
        """
        插入消息链

        Args:
            messages: 消息列表
            parent_uuid: 起始父消息 UUID

        Returns:
            最后一个消息的 UUID
        """
        last_uuid = parent_uuid

        for msg in messages:
            # 设置 parent_uuid
            msg["parent_uuid"] = last_uuid
            last_uuid = msg["uuid"]

            # 添加时间戳
            if "timestamp" not in msg:
                msg["timestamp"] = datetime.now().isoformat()

            # 写入文件
            self._append_entry_to_file(msg)

        return last_uuid

    def save_custom_title(self, title: str) -> None:
        """
        保存自定义标题

        Args:
            title: 标题
        """
        self._ensure_transcript_materialized()
        self.current_title = title
        self._append_entry_to_file(CustomTitleEntry(
            custom_title=title,
            session_id=self.session_id,
            timestamp=datetime.now().isoformat(),
        ))
        self.append_event("metadata_updated", {"custom_title": title})
        self.save_snapshot()

    def save_title(self, title: str, source: str = "user") -> None:
        """
        保存会话标题（兼容 codo/services/session.py 的接口）

        [Workflow]
        source="user" → 保存为自定义标题（custom-title）
        source="ai"   → 保存为 AI 生成标题（ai-title）

        Args:
            title: 标题
            source: 来源（"user" 或 "ai"）
        """
        self._ensure_transcript_materialized()
        self.current_title = title
        metadata_type = "custom-title" if source == "user" else "ai-title"
        entry = {
            "type": metadata_type,
            "custom_title": title,
            "source": source,
            "session_id": self.session_id,
            "timestamp": datetime.now().isoformat(),
        }
        self._append_entry_to_file(entry)
        self.append_event("metadata_updated", {"title": title, "source": source})
        self.save_snapshot()

    def get_session_info(self) -> Dict[str, Any]:
        """
        获取会话基本信息（兼容 codo/services/session.py 的接口）

        [Workflow]
        1. 读取会话文件 stat 信息
        2. 扫描 JSONL 统计消息数和标题
        3. 返回信息字典

        Returns:
            会话信息字典
        """
        info: Dict[str, Any] = {
            "session_id": self.session_id,
            "exists": False,
            "message_count": 0,
            "file_size": 0,
            "created": None,
            "modified": None,
            "user_title": None,
            "ai_title": None,
            "first_prompt": None,
        }

        if self.session_file is None:
            # 尝试从路径推断
            try:
                path = self.transcript_file
            except Exception:
                return info
        else:
            path = self.session_file

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
            with open(path, "r", encoding="utf-8") as f:
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
                            title = record.get("custom_title")
                            if title:
                                info["user_title"] = title
                        elif record_type == "ai-title":
                            title = record.get("custom_title")
                            if title:
                                info["ai_title"] = title
                    except Exception:
                        continue
        except Exception:
            pass

        info["message_count"] = message_count
        return info

    async def load_metadata(self) -> Dict[str, Any]:
        """
        加载会话元数据（兼容 codo/services/session.py 的接口）

        Returns:
            元数据字典
        """
        return {
            "user_title": self.current_title,
            "ai_title": None,
            "tag": self.current_tag,
            "agent_name": self.current_agent_name,
            "agent_color": self.current_agent_color,
            "mode": self.current_mode,
        }

    def save_metadata(self, metadata_type: str, data: Dict[str, Any]) -> None:
        """通用元数据写入。"""
        self._ensure_transcript_materialized()
        entry = {
            "type": metadata_type,
            "session_id": self.session_id,
            "timestamp": datetime.now().isoformat(),
            **data,
        }
        self._append_entry_to_file(entry)
        self.save_snapshot()

    def save_tag(self, tag: str) -> None:
        """
        保存标签

        Args:
            tag: 标签
        """
        self._ensure_transcript_materialized()
        self.current_tag = tag
        self._append_entry_to_file(TagEntry(
            tag=tag,
            session_id=self.session_id,
            timestamp=datetime.now().isoformat(),
        ))
        self.append_event("metadata_updated", {"tag": tag})
        self.save_snapshot()

    def save_agent_name(self, agent_name: str) -> None:
        """
        保存代理名称

        Args:
            agent_name: 代理名称
        """
        self._ensure_transcript_materialized()
        self.current_agent_name = agent_name
        self._append_entry_to_file(AgentNameEntry(
            agent_name=agent_name,
            session_id=self.session_id,
            timestamp=datetime.now().isoformat(),
        ))
        self.append_event("metadata_updated", {"agent_name": agent_name})
        self.save_snapshot()

    def save_agent_color(self, agent_color: str) -> None:
        """Compatibility helper for persisting agent color."""
        self._ensure_transcript_materialized()
        self.current_agent_color = agent_color
        self._append_entry_to_file(AgentColorEntry(
            agent_color=agent_color,
            session_id=self.session_id,
            timestamp=datetime.now().isoformat(),
        ))
        self.append_event("metadata_updated", {"agent_color": agent_color})
        self.save_snapshot()

    def save_agent_setting(self, agent_setting: str) -> None:
        self.save_metadata("agent-setting", {"agent_setting": agent_setting, "agent_type": agent_setting})

    def save_summary(self, summary: str, leaf_uuid: Optional[str] = None) -> None:
        payload: Dict[str, Any] = {"summary": summary}
        if leaf_uuid:
            payload["leaf_uuid"] = leaf_uuid
        self.save_metadata("summary", payload)

    def save_pr_link(self, pr_number: int, pr_url: str, pr_repository: str) -> None:
        self.save_metadata(
            "pr-link",
            {
                "pr_number": pr_number,
                "pr_url": pr_url,
                "pr_repository": pr_repository,
            },
        )

    def save_worktree_state(self, worktree_session: Optional[Dict[str, Any]]) -> None:
        self.save_metadata("worktree-state", {"worktree_session": worktree_session})

    def save_mode(self, mode: str) -> None:
        """
        保存模式

        Args:
            mode: 模式（coordinator/normal）
        """
        self._ensure_transcript_materialized()
        self.current_mode = mode
        self._append_entry_to_file({
            "type": "mode",
            "mode": mode,
            "session_id": self.session_id,
            "timestamp": datetime.now().isoformat(),
        })
        self.append_event("metadata_updated", {"mode": mode})
        self.save_snapshot()

    def save_last_prompt(self, prompt: str) -> None:
        """
        保存最后提示词

        Args:
            prompt: 提示词
        """
        self._ensure_transcript_materialized()
        self.current_last_prompt = prompt
        self._append_entry_to_file(LastPromptEntry(
            last_prompt=prompt,
            session_id=self.session_id,
            timestamp=datetime.now().isoformat(),
        ))
        self.append_event("metadata_updated", {"last_prompt": prompt})
        self.save_snapshot()

    def record_content_replacement(
        self,
        replacements: List[Dict[str, Any]],
        agent_id: Optional[str] = None,
    ) -> None:
        """
        记录内容替换（工具结果截断）

        Args:
            replacements: 替换记录列表
            agent_id: 代理 ID
        """
        self._ensure_transcript_materialized()
        self._append_entry_to_file(ContentReplacementEntry(
            replacements=replacements,
            agent_id=agent_id,
            session_id=self.session_id,
            timestamp=datetime.now().isoformat(),
        ))
        self.append_event(
            "content_replacement",
            {"replacements": replacements},
            agent_id=agent_id,
        )
        self.save_snapshot()

    def flush(self) -> None:
        """
        刷新写入队列

        """
        # Python 的文件写入默认是缓冲的，这里确保元数据被重新追加
        if self.session_file is not None:
            self._reappend_session_metadata()
        self.save_snapshot()

    def get_recorded_messages(self) -> Set[str]:
        """Compatibility accessor for deduplicated transcript message UUIDs."""
        return set(self.recorded_message_uuids)

    def delete_session(self) -> None:
        """Delete transcript, event log, and snapshot files for the session."""
        targets = [
            self.session_file,
            self.transcript_file,
            self.event_log_file,
            self.snapshot_file,
        ]
        seen: Set[Path] = set()
        for path in targets:
            if path is None:
                continue
            resolved = Path(path)
            if resolved in seen:
                continue
            seen.add(resolved)
            if resolved.exists():
                resolved.unlink()

class SessionManager:
    """Compatibility manager for listing and deleting project sessions."""

    @staticmethod
    def list_sessions(cwd: Optional[str] = None) -> List[Dict[str, Any]]:
        target_cwd = cwd or os.getcwd()
        sessions: List[Dict[str, Any]] = []
        directory = get_sessions_dir(target_cwd)
        if not directory.exists():
            return []
        for session_file in directory.glob("*.jsonl"):
            if session_file.name.endswith(".events.jsonl"):
                continue
            storage = SessionStorage(session_file.stem, target_cwd)
            info = storage.get_session_info()
            if info.get("exists"):
                sessions.append(info)
        sessions.sort(key=lambda item: item.get("modified") or "", reverse=True)
        return sessions

    @staticmethod
    def get_latest_session(cwd: Optional[str] = None) -> Optional[str]:
        sessions = SessionManager.list_sessions(cwd)
        if sessions:
            return str(sessions[0].get("session_id") or "")
        return None

    @staticmethod
    def delete_session(session_id: str, cwd: Optional[str] = None) -> None:
        SessionStorage(session_id, cwd or os.getcwd()).delete_session()

# ============================================================================
# 会话加载
# ============================================================================

def load_session(session_id: str, cwd: str) -> Optional[LoadedSession]:
    """
    加载会话

    Args:
        session_id: 会话 ID
        cwd: 当前工作目录

    Returns:
        加载的会话数据，如果文件不存在则返回 None
    """
    session_file = get_session_file_path(session_id, cwd)

    if not session_file.exists():
        return None

    return load_session_from_file(session_file, session_id)

def load_session_from_events(
    session_id: str,
    events: List[SessionEvent],
) -> Optional[LoadedSession]:
    if not events:
        return None

    messages: List[TranscriptMessage] = []
    message_map: Dict[str, TranscriptMessage] = {}
    message_order: List[str] = []
    metadata = SessionMetadata(session_id=session_id)
    leaf_uuids: Set[str] = set()
    content_replacements: List[Dict[str, Any]] = []

    for event in events:
        payload = dict(event.payload or {})
        if event.event_type == "message_recorded":
            raw_message = payload.get("message")
            if not isinstance(raw_message, dict) or not raw_message.get("uuid"):
                continue
            try:
                message = TranscriptMessage.model_validate(raw_message)
            except Exception:
                continue
            if message.uuid not in message_map:
                message_order.append(message.uuid)
            message_map[message.uuid] = message
            if message.parent_uuid:
                leaf_uuids.discard(message.parent_uuid)
            leaf_uuids.add(message.uuid)
        elif event.event_type == "metadata_updated":
            if "custom_title" in payload:
                metadata.custom_title = payload.get("custom_title")
            if "title" in payload and not metadata.custom_title:
                metadata.custom_title = payload.get("title")
            if "tag" in payload:
                metadata.tag = payload.get("tag")
            if "agent_name" in payload:
                metadata.agent_name = payload.get("agent_name")
            if "agent_color" in payload:
                metadata.agent_color = payload.get("agent_color")
            if "mode" in payload:
                metadata.mode = payload.get("mode")
            if "last_prompt" in payload:
                metadata.last_prompt = payload.get("last_prompt")
        elif event.event_type == "content_replacement":
            replacements = payload.get("replacements", [])
            content_replacements.extend(
                dict(item) for item in replacements if isinstance(item, dict)
            )

    messages = [message_map[message_uuid] for message_uuid in message_order]
    return LoadedSession(
        session_id=session_id,
        messages=messages,
        metadata=metadata,
        leaf_uuids=list(leaf_uuids),
        content_replacements=content_replacements,
    )

def build_runtime_state_from_events(events: List[SessionEvent]) -> Dict[str, Any]:
    """从 runtime event log 派生 UI/runtime 恢复所需的状态。"""
    runtime_state: Dict[str, Any] = {
        "app_state": {"todos": {}},
        "replay_events": [],
        "permission_mode": None,
        "last_checkpoint_id": None,
        "retry_checkpoint_id": None,
        "runtime_phase": None,
        "resume_target": None,
        "pending_interaction": None,
    }
    todos = runtime_state["app_state"]["todos"]

    for event in events:
        event_type = str(event.event_type or "")
        payload = dict(event.payload or {})
        if event.agent_id and "agent_id" not in payload:
            payload["agent_id"] = event.agent_id

        if event_type == "todo_updated":
            key = str(payload.get("key", "") or "")
            if key:
                items = payload.get("items", [])
                todos[key] = [dict(item) for item in items if isinstance(item, dict)]
        elif event_type == "permission_mode_changed":
            permission_mode = payload.get("permission_mode") or payload.get("mode") # 这里为什么会有or就是定义的时候没定义好
            if permission_mode is not None:
                runtime_state["permission_mode"] = str(permission_mode)
        elif event_type == "interaction_requested":
            request = payload.get("request")
            if isinstance(request, dict):
                runtime_state["pending_interaction"] = json.loads(json.dumps(request))
        elif event_type == "interaction_resolved":
            request_id = str(payload.get("request_id", "") or "")
            pending = runtime_state.get("pending_interaction")
            if not request_id:
                runtime_state["pending_interaction"] = None
            elif not isinstance(pending, dict):
                runtime_state["pending_interaction"] = None
            elif str(pending.get("request_id", "") or "") == request_id:
                runtime_state["pending_interaction"] = None
        elif event_type == "status_changed":
            phase = str(payload.get("phase", "") or "")
            checkpoint_id = str(payload.get("checkpoint_id", "") or "")
            resume_target = str(payload.get("resume_target", "") or "")
            if phase:
                runtime_state["runtime_phase"] = phase
            runtime_state["resume_target"] = resume_target or None
            if checkpoint_id:
                runtime_state["last_checkpoint_id"] = checkpoint_id
                if phase in _RETRYABLE_RUNTIME_PHASES:
                    runtime_state["retry_checkpoint_id"] = checkpoint_id
        elif event_type == "checkpoint_restored":
            checkpoint_id = str(payload.get("checkpoint_id", "") or "")
            phase = str(payload.get("phase", "") or "")
            if checkpoint_id:
                runtime_state["last_checkpoint_id"] = checkpoint_id
            if phase:
                runtime_state["runtime_phase"] = phase
        elif event_type == "interrupt_ack":
            runtime_state["runtime_phase"] = "interrupted"
            runtime_state["pending_interaction"] = None
            checkpoint_id = str(payload.get("checkpoint_id", "") or "")
            if checkpoint_id:
                runtime_state["last_checkpoint_id"] = checkpoint_id
                runtime_state["retry_checkpoint_id"] = checkpoint_id
        elif event_type == "turn_completed":
            runtime_state["runtime_phase"] = "complete"
            runtime_state["resume_target"] = None
            runtime_state["pending_interaction"] = None

        if event_type in _RUNTIME_REPLAY_EVENT_TYPES:
            runtime_state["replay_events"].append({"type": event_type, **payload})

    return runtime_state

def load_session_from_file(
    session_file: Path,
    session_id: str,
) -> LoadedSession:
    """
    从文件加载会话

    Args:
        session_file: 会话文件路径
        session_id: 会话 ID

    Returns:
        加载的会话数据
    """
    messages: List[TranscriptMessage] = []
    metadata = SessionMetadata(session_id=session_id)
    leaf_uuids: Set[str] = set()
    content_replacements: List[Dict[str, Any]] = []

    # 读取 JSONL 文件
    with open(session_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            try:
                entry = json.loads(line)
                entry_type = entry.get("type")

                # 处理不同类型的条目
                if entry_type in ("user", "assistant"):
                    # Transcript 消息
                    msg = TranscriptMessage.model_validate(entry)
                    messages.append(msg)

                    # 记录叶子节点（没有子节点的消息）
                    if msg.parent_uuid:
                        leaf_uuids.discard(msg.parent_uuid)
                    leaf_uuids.add(msg.uuid)

                elif entry_type == "custom-title":
                    metadata.custom_title = entry.get("custom_title")

                elif entry_type == "tag":
                    metadata.tag = entry.get("tag")

                elif entry_type == "agent-name":
                    metadata.agent_name = entry.get("agent_name")

                elif entry_type == "agent-color":
                    metadata.agent_color = entry.get("agent_color")

                elif entry_type == "mode":
                    metadata.mode = entry.get("mode")

                elif entry_type == "last-prompt":
                    metadata.last_prompt = entry.get("last_prompt")

                elif entry_type == "content-replacement":
                    content_replacements.extend(entry.get("replacements", []))

            except Exception as e:
                # 跳过无法解析的行
                print(f"Warning: Failed to parse session entry: {e}")
                continue

    return LoadedSession(
        session_id=session_id,
        messages=messages,
        metadata=metadata,
        leaf_uuids=list(leaf_uuids),
        content_replacements=content_replacements,
    )

def build_conversation_chain(
    messages: List[TranscriptMessage],
    leaf_uuid: Optional[str],
) -> List[TranscriptMessage]:
    """
    从叶子节点重建对话链

    Args:
        messages: 所有消息列表
        leaf_uuid: 叶子节点 UUID

    Returns:
        按顺序排列的对话链
    """
    if not leaf_uuid:
        return []

    # 构建 UUID -> Message 映射
    message_map = {msg.uuid: msg for msg in messages}

    # 从叶子节点向上追溯
    chain: List[TranscriptMessage] = []
    current_uuid = leaf_uuid

    while current_uuid:
        msg = message_map.get(current_uuid)
        if not msg:
            break

        chain.insert(0, msg)  # 插入到开头
        current_uuid = msg.parent_uuid

    return chain
