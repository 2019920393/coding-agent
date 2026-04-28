import pytest

from codo.services.tools.execution_manager import ExecutionManager
from codo.tools.edit_tool import EditToolInput, edit_tool
from codo.tools.write_tool import WriteToolInput, write_tool

@pytest.mark.asyncio
async def test_write_tool_returns_staged_change_before_apply(tmp_path):
    target = tmp_path / "note.txt"

    result = await write_tool.call(
        WriteToolInput(file_path=str(target), content="hello"),
        {"messages": [], "options": {}},
        lambda *args, **kwargs: True,
        None,
    )

    assert result.staged_changes
    assert not target.exists()

    manager = ExecutionManager()
    receipt = await manager.apply_staged_change(result.staged_changes[0])

    assert receipt.kind == "diff"
    assert target.read_text() == "hello"

@pytest.mark.asyncio
async def test_edit_tool_returns_staged_change_before_apply(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("before world")

    result = await edit_tool.call(
        EditToolInput(
            file_path=str(target),
            old_string="world",
            new_string="after",
            replace_all=False,
        ),
        {"messages": [], "options": {}},
        lambda *args, **kwargs: True,
        None,
    )

    assert result.staged_changes
    assert target.read_text() == "before world"
    assert result.staged_changes[0].new_content == "before after"
