"""
会话管理模块

提供会话持久化和恢复功能。
"""

from codo.session.types import (
    SessionState,
    TranscriptMessage,
    SessionMetadata,
    LoadedSession,
    SessionExternalMetadata,
    SessionInfo,
)

from codo.session.storage import (
    SessionStorage,
    SessionManager,
    load_session,
    load_session_from_file,
    build_conversation_chain,
    get_sessions_dir,
    get_session_file_path,
    get_project_dir,
    sanitize_path,
    resolve_session_file_path,
    list_session_files,
)
from codo.session.query import (
    validate_uuid,
    load_session_metadata,
    get_last_session,
    list_all_sessions,
    search_sessions_by_title,
    find_session_by_id,
)
from codo.session.restore import (
    parse_jsonl_transcript,
    extract_messages_from_transcript,
    extract_todos_from_transcript,
    extract_agent_setting_from_transcript,
    extract_metadata_from_transcript,
    load_session_for_resume,
    restore_session_state,
    validate_session_data,
)
from codo.session.title import (
    MAX_CONVERSATION_TEXT,
    SESSION_TITLE_PROMPT,
    extract_conversation_text,
    generate_session_title,
    generate_and_save_title,
)
from codo.session.export import (
    format_timestamp,
    extract_first_prompt,
    sanitize_filename,
    generate_default_filename,
    messages_to_markdown,
    messages_to_plain_text,
    export_session,
    export_session_to_string,
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
    "get_project_dir",
    "sanitize_path",
    "resolve_session_file_path",
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
