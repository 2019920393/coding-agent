import pytest

from codo.tools.receipts import (
    CommandReceipt,
    DiffReceipt,
    ProposedFileChange,
    render_receipt_for_model,
)
from codo.tools.types import ToolResult

def test_tool_result_keeps_structured_receipt_and_staged_changes():
    receipt = DiffReceipt(
        kind="diff",
        summary="Update app.py",
        path="C:/tmp/app.py",
        diff_text="@@ -1 +1 @@\n-old\n+new",
        change_id="chg_1",
    )
    change = ProposedFileChange(
        change_id="chg_1",
        path="C:/tmp/app.py",
        original_content="old",
        new_content="new",
        diff_text="@@ -1 +1 @@\n-old\n+new",
        source_tool="Edit",
    )

    result = ToolResult(data=None, receipt=receipt, staged_changes=[change])

    assert result.receipt == receipt
    assert result.staged_changes[0].path.endswith("app.py")

def test_render_receipt_for_model_builds_single_tool_result_block():
    receipt = CommandReceipt(
        kind="command",
        summary="Ran pytest",
        command="pytest -q",
        exit_code=1,
        stdout="collected 3 items",
        stderr="1 failed",
    )

    block = render_receipt_for_model(receipt, tool_use_id="tool-1")

    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "tool-1"
    assert "pytest -q" in block["content"]
    assert "1 failed" in block["content"]
