"""Legacy import paths should resolve to canonical runtime tools."""

from codo.tools.edit_tool import EditTool, edit_tool
from codo.tools.file_tools.edit_tool import EditTool as LegacyEditTool, edit_tool as legacy_edit_tool
from codo.tools.file_tools.read_tool import ReadTool as LegacyReadTool, read_tool as legacy_read_tool
from codo.tools.file_tools.write_tool import WriteTool as LegacyWriteTool, write_tool as legacy_write_tool
from codo.tools.read_tool import ReadTool, read_tool
from codo.tools.write_tool import WriteTool, write_tool

def test_legacy_file_tool_modules_reexport_canonical_runtime_tools():
    assert LegacyReadTool is ReadTool
    assert LegacyWriteTool is WriteTool
    assert LegacyEditTool is EditTool
    assert legacy_read_tool is read_tool
    assert legacy_write_tool is write_tool
    assert legacy_edit_tool is edit_tool
