"""Message types for team communication."""

from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field

class MessageType(str, Enum):
    """Types of messages in the team system."""

    TASK_ASSIGNMENT = "task_assignment"
    TASK_RESULT = "task_result"
    QUESTION = "question"
    ANSWER = "answer"
    STATUS_UPDATE = "status_update"
    ERROR = "error"

class Message(BaseModel):
    """A message between agents in the team."""

    model_config = ConfigDict(use_enum_values=True)

    id: str = Field(description="Unique message ID")
    type: MessageType = Field(description="Type of message")
    from_agent: str = Field(description="Sender agent ID or name")
    to_agent: str = Field(description="Recipient agent ID or name")
    content: str = Field(description="Message content")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    timestamp: float = Field(description="Unix timestamp when message was created")
    parent_id: Optional[str] = Field(default=None, description="ID of parent message if this is a reply")
