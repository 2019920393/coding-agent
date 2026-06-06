"""Team collaboration system for multi-agent coordination."""

from .background_tasks import (
    BackgroundTask,
    BackgroundTaskManager,
    TaskStatus,
    get_task_manager,
)
from .mailbox import Mailbox
from .message_types import Message, MessageType
from .subagent_context import (
    SubAgentContext,
    clone_context_for_isolation,
    prepare_fork_context,
    prepare_fresh_context,
    should_use_fork_mode,
)
from .team_manager import TeamManager, get_team_manager

__all__ = [
    "Message",
    "MessageType",
    "Mailbox",
    "TeamManager",
    "get_team_manager",
    "SubAgentContext",
    "prepare_fresh_context",
    "prepare_fork_context",
    "should_use_fork_mode",
    "clone_context_for_isolation",
    "BackgroundTask",
    "TaskStatus",
    "BackgroundTaskManager",
    "get_task_manager",
]
