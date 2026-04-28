"""Legacy shim for the canonical Edit tool."""

from codo.tools.edit_tool import EditTool, EditToolInput, EditToolOutput, edit_tool

FileEditTool = EditTool

__all__ = [
    "EditTool",
    "EditToolInput",
    "EditToolOutput",
    "edit_tool",
    "FileEditTool",
]
