"""Execution manager for staged filesystem changes."""

import os
import tempfile
from pathlib import Path

from codo.tools.receipts import DiffReceipt, ProposedFileChange


class ExecutionManager:
    """Applies or rejects staged file changes after UI approval."""

    async def apply_staged_change(self, change: ProposedFileChange) -> DiffReceipt:
        """
        应用暂存的文件变更（将 new_content 写入磁盘）。

        参数:
            change: 待应用的文件变更对象

        返回:
            DiffReceipt: 包含操作摘要、文件路径和 diff 的回执
        """
        target = Path(change.path)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(target.parent),
            delete=False,
        ) as temp_file:
            temp_file.write(change.new_content)
            temp_path = temp_file.name
        try:
            os.replace(temp_path, target)
        except OSError:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise
        return DiffReceipt(
            kind="diff",
            summary=f"Applied changes to {change.path}",
            path=change.path,
            diff_text=change.diff_text,
            change_id=change.change_id,
        )

    async def reject_staged_change(self, change: ProposedFileChange) -> DiffReceipt:
        """
        拒绝暂存的文件变更（不写入磁盘，仅返回拒绝回执）。

        参数:
            change: 被拒绝的文件变更对象

        返回:
            DiffReceipt: 包含拒绝摘要、文件路径和 diff 的回执
        """
        return DiffReceipt(
            kind="diff",
            summary=f"Rejected changes to {change.path}",
            path=change.path,
            diff_text=change.diff_text,
            change_id=change.change_id,
        )
