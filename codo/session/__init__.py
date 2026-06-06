"""
会话管理模块

提供会话持久化和恢复功能。
"""

from codo.session.export import (
    export_session,
    export_session_to_string,
    extract_first_prompt,
    format_timestamp,
    generate_default_filename,
    messages_to_markdown,
    messages_to_plain_text,
    sanitize_filename,
)
from codo.session.query import (
    find_session_by_id,
    get_last_session,
    list_all_sessions,
    load_session_metadata,
    search_sessions_by_title,
    validate_uuid,
)
from codo.session.restore import (
    extract_agent_setting_from_transcript,
    extract_messages_from_transcript,
    extract_metadata_from_transcript,
    extract_todos_from_transcript,
    load_session_for_resume,
    parse_jsonl_transcript,
    restore_session_state,
    validate_session_data,
)
from codo.session.storage import (
    SessionManager,
    SessionStorage,
    build_conversation_chain,
    get_session_file_path,
    get_sessions_dir,
    list_session_files,
    load_session,
    load_session_from_file,
)
from codo.session.title import (
    MAX_CONVERSATION_TEXT,
    SESSION_TITLE_PROMPT,
    extract_conversation_text,
    generate_and_save_title,
    generate_session_title,
)
from codo.session.types import (
    LoadedSession,
    SessionExternalMetadata,
    SessionInfo,
    SessionMetadata,
    SessionState,
    TranscriptMessage,
)

__all__ = [
    # Types
    "SessionState",
    "TranscriptMessage",
    "SessionMetadata",
    "LoadedSession",
    "SessionExternalMetadata",
    "SessionInfo",
    # Storage
    "SessionStorage",
    "SessionManager",
    "load_session",
    "load_session_from_file",
    "build_conversation_chain",
    "get_sessions_dir",
    "get_session_file_path",
    "list_session_files",
    # Query
    "validate_uuid",
    "load_session_metadata",
    "get_last_session",
    "list_all_sessions",
    "search_sessions_by_title",
    "find_session_by_id",
    # Restore
    "parse_jsonl_transcript",
    "extract_messages_from_transcript",
    "extract_todos_from_transcript",
    "extract_agent_setting_from_transcript",
    "extract_metadata_from_transcript",
    "load_session_for_resume",
    "restore_session_state",
    "validate_session_data",
    # Title
    "MAX_CONVERSATION_TEXT",
    "SESSION_TITLE_PROMPT",
    "extract_conversation_text",
    "generate_session_title",
    "generate_and_save_title",
    # Export
    "format_timestamp",
    "extract_first_prompt",
    "sanitize_filename",
    "generate_default_filename",
    "messages_to_markdown",
    "messages_to_plain_text",
    "export_session",
    "export_session_to_string",
]
