"""
会话数据结构定义

定义会话持久化所需的数据结构。
"""

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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
    parent_uuid: str | None = None
    content: str | list[dict[str, Any]]
    timestamp: str | None = None
    model: str | None = None

class CustomTitleEntry(BaseModel):
    """自定义标题条目"""
    type: Literal["custom-title"] = "custom-title"
    custom_title: str
    session_id: str
    timestamp: str | None = None

class TagEntry(BaseModel):
    """标签条目"""
    type: Literal["tag"] = "tag"
    tag: str
    session_id: str
    timestamp: str | None = None

class AgentNameEntry(BaseModel):
    """代理名称条目"""
    type: Literal["agent-name"] = "agent-name"
    agent_name: str
    session_id: str
    timestamp: str | None = None

class AgentColorEntry(BaseModel):
    """代理颜色条目"""
    type: Literal["agent-color"] = "agent-color"
    agent_color: str
    session_id: str
    timestamp: str | None = None

class ModeEntry(BaseModel):
    """模式条目"""
    type: Literal["mode"] = "mode"
    mode: str
    session_id: str
    timestamp: str | None = None

class LastPromptEntry(BaseModel):
    """最后提示词条目"""
    type: Literal["last-prompt"] = "last-prompt"
    last_prompt: str
    session_id: str
    timestamp: str | None = None

class ContentReplacementEntry(BaseModel):
    """内容替换条目（用于工具结果截断记录）"""
    type: Literal["content-replacement"] = "content-replacement"
    replacements: list[dict[str, Any]]
    agent_id: str | None = None
    session_id: str
    timestamp: str | None = None

# 所有 Entry 类型的联合
TranscriptEntry = (
    TranscriptMessage
    | CustomTitleEntry
    | TagEntry
    | AgentNameEntry
    | AgentColorEntry
    | ModeEntry
    | LastPromptEntry
    | ContentReplacementEntry
)

# ============================================================================
# 会话元数据
# ============================================================================

class SessionMetadata(BaseModel):
    """会话元数据"""
    session_id: str
    custom_title: str | None = None
    tag: str | None = None
    agent_name: str | None = None
    agent_color: str | None = None
    mode: str | None = None
    last_prompt: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

# ============================================================================
# 会话加载结果
# ============================================================================

class LoadedSession(BaseModel):
    """加载的会话数据"""
    model_config = ConfigDict(extra="allow")

    session_id: str
    messages: list[TranscriptMessage]
    metadata: SessionMetadata
    leaf_uuids: list[str] = Field(default_factory=list)
    content_replacements: list[dict[str, Any]] = Field(default_factory=list)
 #记录被截断/替换的内容。
# ============================================================================
# 会话外部元数据（用于 UI/API）
# ============================================================================

class SessionExternalMetadata(BaseModel):
    """会话外部元数据（用于 UI/API 层）"""
    model_config = ConfigDict(extra="allow")

    permission_mode: str | None = None
    model: str | None = None
    task_summary: str | None = None

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
    custom_title: str | None = None
    first_prompt: str | None = None
    cwd: str | None = None
    created_at: float | None = None

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
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    agent_id: str | None = None

class SessionSnapshot(BaseModel):
    """Materialized session snapshot derived from event log."""

    model_config = ConfigDict(extra="allow")

    session_id: str
    messages: list[dict[str, Any]] = Field(default_factory=list)
    runtime_state: dict[str, Any] = Field(default_factory=dict)
    metadata: SessionMetadata
    last_event_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
