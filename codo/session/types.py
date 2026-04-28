"""
会话数据结构定义

定义会话持久化所需的数据结构。
"""

from dataclasses import dataclass
from typing import Literal, Optional, Dict, Any, List, Union
from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime
from uuid import UUID

# ============================================================================
# 会话状态枚举
# ============================================================================

SessionState = Literal["idle", "running", "requires_action"]

# ============================================================================
# Transcript Entry 类型
# ============================================================================

class TranscriptMessage(BaseModel):
    """Transcript 消息（user/assistant）"""
    model_config = ConfigDict(extra="allow")

    type: Literal["user", "assistant"]
    uuid: str
    parent_uuid: Optional[str] = None
    content: Union[str, List[Dict[str, Any]]]
    timestamp: Optional[str] = None
    model: Optional[str] = None

class CustomTitleEntry(BaseModel):
    """自定义标题条目"""
    type: Literal["custom-title"] = "custom-title"
    custom_title: str
    session_id: str
    timestamp: Optional[str] = None

class TagEntry(BaseModel):
    """标签条目"""
    type: Literal["tag"] = "tag"
    tag: str
    session_id: str
    timestamp: Optional[str] = None

class AgentNameEntry(BaseModel):
    """代理名称条目"""
    type: Literal["agent-name"] = "agent-name"
    agent_name: str
    session_id: str
    timestamp: Optional[str] = None

class AgentColorEntry(BaseModel):
    """代理颜色条目"""
    type: Literal["agent-color"] = "agent-color"
    agent_color: str
    session_id: str
    timestamp: Optional[str] = None

class ModeEntry(BaseModel):
    """模式条目"""
    type: Literal["mode"] = "mode"
    mode: str
    session_id: str
    timestamp: Optional[str] = None

class LastPromptEntry(BaseModel):
    """最后提示词条目"""
    type: Literal["last-prompt"] = "last-prompt"
    last_prompt: str
    session_id: str
    timestamp: Optional[str] = None

class ContentReplacementEntry(BaseModel):
    """内容替换条目（用于工具结果截断记录）"""
    type: Literal["content-replacement"] = "content-replacement"
    replacements: List[Dict[str, Any]]
    agent_id: Optional[str] = None
    session_id: str
    timestamp: Optional[str] = None

# 所有 Entry 类型的联合
TranscriptEntry = Union[
    TranscriptMessage,
    CustomTitleEntry,
    TagEntry,
    AgentNameEntry,
    AgentColorEntry,
    ModeEntry,
    LastPromptEntry,
    ContentReplacementEntry,
]

# ============================================================================
# 会话元数据
# ============================================================================

class SessionMetadata(BaseModel):
    """会话元数据"""
    session_id: str
    custom_title: Optional[str] = None
    tag: Optional[str] = None
    agent_name: Optional[str] = None
    agent_color: Optional[str] = None
    mode: Optional[str] = None
    last_prompt: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

# ============================================================================
# 会话加载结果
# ============================================================================

class LoadedSession(BaseModel):
    """加载的会话数据"""
    model_config = ConfigDict(extra="allow")

    session_id: str
    messages: List[TranscriptMessage]
    metadata: SessionMetadata
    leaf_uuids: List[str] = Field(default_factory=list)
    content_replacements: List[Dict[str, Any]] = Field(default_factory=list)

# ============================================================================
# 会话外部元数据（用于 UI/API）
# ============================================================================

class SessionExternalMetadata(BaseModel):
    """会话外部元数据（用于 UI/API 层）"""
    model_config = ConfigDict(extra="allow")

    permission_mode: Optional[str] = None
    model: Optional[str] = None
    task_summary: Optional[str] = None

# ============================================================================
# 会话列表/恢复元数据
# ============================================================================

@dataclass
class SessionInfo:
    """
    会话元数据结构

    用于会话列表展示、查询和恢复。
    """

    session_id: str
    summary: str
    last_modified: float
    file_size: int
    custom_title: Optional[str] = None
    first_prompt: Optional[str] = None
    cwd: Optional[str] = None
    created_at: Optional[float] = None

    def __post_init__(self) -> None:
        """确保 summary 总有可用值。"""
        if not self.summary:
            if self.custom_title:
                self.summary = self.custom_title
            elif self.first_prompt:
                self.summary = self.first_prompt[:50] + ("..." if len(self.first_prompt) > 50 else "")
            else:
                self.summary = "Untitled"

# ============================================================================
# Runtime Event Log + Snapshot
# ============================================================================

class SessionEvent(BaseModel):
    """Append-only runtime event entry."""

    model_config = ConfigDict(extra="allow")

    event_id: str
    session_id: str
    event_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: str
    agent_id: Optional[str] = None

class SessionSnapshot(BaseModel):
    """Materialized session snapshot derived from event log."""

    model_config = ConfigDict(extra="allow")

    session_id: str
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    runtime_state: Dict[str, Any] = Field(default_factory=dict)
    metadata: SessionMetadata
    last_event_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
