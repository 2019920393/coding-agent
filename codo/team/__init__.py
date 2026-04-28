"""Team collaboration system for multi-agent coordination."""

from .message_types import Message, MessageType
from .mailbox import Mailbox
from .team_manager import TeamManager, get_team_manager
from .subagent_context import (
    SubAgentContext,
    prepare_fresh_context,
    prepare_fork_context,
    should_use_fork_mode,
    clone_context_for_isolation,
)
from .background_tasks import (
    BackgroundTask,
    TaskStatus,
    BackgroundTaskManager,
    get_task_manager,
)

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
