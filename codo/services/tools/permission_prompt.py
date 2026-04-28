"""Permission prompt UI helpers."""

import json
import logging
from enum import Enum
from typing import Any, Dict

logger = logging.getLogger(__name__)

class PermissionChoice(str, Enum):
    """User's response to a permission prompt."""
    ALLOW_ONCE = "allow_once"
    ALLOW_ALWAYS = "allow_always"
    DENY = "deny"
    ABORT = "abort"

def format_tool_info(tool_name: str, tool_input: Dict[str, Any]) -> str:
    """
    Format tool call information for display.

    Produces a concise summary of what the tool wants to do.

    Args:
        tool_name: Tool name (e.g. "Bash", "Write")
        tool_input: Tool input parameters

    Returns:
        Formatted string for terminal display
    """
    if tool_name == "Bash":
        command = tool_input.get("command", "")
        desc = tool_input.get("description", "")
        if desc:
            return f"Bash: {desc}\n  $ {command}"
        return f"Bash: $ {command}"

    elif tool_name == "Write":
        file_path = tool_input.get("file_path", "")
        content = tool_input.get("content", "")
        preview = content[:200] + "..." if len(content) > 200 else content
        lines = content.count("\n") + 1
        return f"Write: {file_path} ({lines} lines)\n  {preview}"

    elif tool_name == "Edit":
        file_path = tool_input.get("file_path", "")
        old = tool_input.get("old_string", "")
        new = tool_input.get("new_string", "")
        old_preview = old[:80] + "..." if len(old) > 80 else old
        new_preview = new[:80] + "..." if len(new) > 80 else new
        return f"Edit: {file_path}\n  - {old_preview}\n  + {new_preview}"

    elif tool_name == "Read":
        file_path = tool_input.get("file_path", "")
        return f"Read: {file_path}"

    elif tool_name == "Glob":
        pattern = tool_input.get("pattern", "")
        return f"Glob: {pattern}"

    elif tool_name == "Grep":
        pattern = tool_input.get("pattern", "")
        path = tool_input.get("path", ".")
        return f"Grep: /{pattern}/ in {path}"

    else:
        # Generic: show name + compact JSON
        try:
            compact = json.dumps(tool_input, ensure_ascii=False)
            if len(compact) > 200:
                compact = compact[:200] + "..."
            return f"{tool_name}: {compact}"
        except Exception:
            return f"{tool_name}: (input not serializable)"

async def prompt_permission(
    tool_name: str,
    tool_input: Dict[str, Any],
    message: str = "",
) -> PermissionChoice:
    """
    Show a Textual permission prompt and get the user's choice.

    Displays:
    - Tool name and formatted input
    - Permission message (reason why approval is needed)
    - 4 options: allow once / allow always / deny / abort

    Args:
        tool_name: Tool name
        tool_input: Tool input parameters
        message: Permission request message from the checker

    Returns:
        PermissionChoice
    """
    # 兼容 Pydantic 模型 / dataclass / 普通对象
    if hasattr(tool_input, "model_dump"):
        normalized_input = tool_input.model_dump()
    elif isinstance(tool_input, dict):
        normalized_input = tool_input
    elif hasattr(tool_input, "__dict__"):
        normalized_input = dict(vars(tool_input))
    else:
        normalized_input = {}

    from codo.cli.interactive_dialogs import prompt_permission_dialog

    info = format_tool_info(tool_name, normalized_input)
    dialog_result = await prompt_permission_dialog(
        tool_name=tool_name,
        tool_info=info,
        message=message,
    )

    logger.debug("[permission] resolved via Textual dialog")

    if dialog_result == "allow_once":
        return PermissionChoice.ALLOW_ONCE
    if dialog_result == "allow_always":
        return PermissionChoice.ALLOW_ALWAYS
    if dialog_result == "deny":
        return PermissionChoice.DENY
    # None 或显式 abort 都归并为 ABORT
    return PermissionChoice.ABORT

def apply_session_allow_rule(
    permission_context: Any,
    tool_name: str,
) -> None:
    """
    Add a session-level allow rule for a tool.

    Called when user chooses "Allow always (this session)".

    Args:
        permission_context: ToolPermissionContext object
        tool_name: Tool name to allow
    """
    from codo.types.permissions import PermissionRuleSource

    # 兼容对象和字典两种形式的 permission_context
    if hasattr(permission_context, 'always_allow_rules'):
        always_allow = permission_context.always_allow_rules
    else:
        always_allow = permission_context.get('always_allow_rules', {})

    session_rules = always_allow.get(
        PermissionRuleSource.SESSION, []
    )
    if tool_name not in session_rules:
        session_rules.append(tool_name)
        always_allow[PermissionRuleSource.SESSION] = session_rules
        logger.info(f"[permission] added session allow rule: {tool_name}")
