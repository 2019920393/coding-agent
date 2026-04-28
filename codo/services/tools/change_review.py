"""Textual-only staged change review helpers."""

from codo.cli.tui.runtime import get_active_app
from codo.tools.receipts import ProposedFileChange

async def request_change_review(change: ProposedFileChange) -> str:
    app = get_active_app()
    if app is None or not hasattr(app, "request_change_review"):
        raise RuntimeError("Textual app is required for staged change review")
    return await app.request_change_review(change)
