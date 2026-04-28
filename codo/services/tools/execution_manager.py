"""Execution manager for staged filesystem changes."""

from codo.tools.receipts import DiffReceipt, ProposedFileChange
from codo.utils.fs_operations import getFsImplementation

class ExecutionManager:
    """Applies or rejects staged file changes after UI approval."""

    async def apply_staged_change(self, change: ProposedFileChange) -> DiffReceipt:
        fs = getFsImplementation()
        fs.writeFile(change.path, change.new_content)
        return DiffReceipt(
            kind="diff",
            summary=f"Applied changes to {change.path}",
            path=change.path,
            diff_text=change.diff_text,
            change_id=change.change_id,
        )

    async def reject_staged_change(self, change: ProposedFileChange) -> DiffReceipt:
        return DiffReceipt(
            kind="diff",
            summary=f"Rejected changes to {change.path}",
            path=change.path,
            diff_text=change.diff_text,
            change_id=change.change_id,
        )
