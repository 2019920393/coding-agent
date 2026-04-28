"""
Compact service - Context compression for long conversations.
"""

from codo.services.compact.compact import (
    AutoCompactState,
    CompactResult,
    auto_compact_if_needed,
    calculate_token_warning_state,
    compact_conversation,
    force_compact,
    force_compact_conversation,
)
from codo.services.compact.microcompact import (
    reset_compacted_cache,
)
from codo.services.compact.prompt import (
    format_compact_summary,
    get_compact_prompt,
    get_compact_prompt_for_partial,
    get_compact_user_summary_message,
)

__all__ = [
    "AutoCompactState",
    "CompactResult",
    "auto_compact_if_needed",
    "calculate_token_warning_state",
    "compact_conversation",
    "force_compact",
    "force_compact_conversation",
    "format_compact_summary",
    "get_compact_prompt",
    "get_compact_prompt_for_partial",
    "get_compact_user_summary_message",
    "reset_compacted_cache",
]
