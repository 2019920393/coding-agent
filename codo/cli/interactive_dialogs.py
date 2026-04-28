"""交互式对话组件（Textual-only 入口）。"""

from typing import Any, Dict, List, Optional

from codo.cli.tui.runtime import get_active_app

async def prompt_permission_dialog(
    tool_name: str,
    tool_info: str,
    message: str = "",
) -> Optional[str]:
    """权限确认交互，仅支持活动中的 Textual App。"""
    app = get_active_app()
    if app is not None and hasattr(app, "request_permission"):
        return await app.request_permission(
            tool_name=tool_name,
            tool_info=tool_info,
            message=message,
        )

    raise RuntimeError("Textual app is required for permission dialogs")

async def collect_user_answers_dialog(questions: List[Any]) -> Optional[Dict[str, str]]:
    """AskUserQuestion 交互，仅支持活动中的 Textual App。"""
    app = get_active_app()
    if app is not None and hasattr(app, "request_questions"):
        return await app.request_questions(questions)

    raise RuntimeError("Textual app is required for question dialogs")
