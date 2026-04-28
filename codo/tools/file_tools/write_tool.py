"""Legacy shim for the canonical Write tool."""

from codo.tools.write_tool import WriteTool, WriteToolInput, WriteToolOutput, write_tool

FileWriteTool = WriteTool

__all__ = [
    "WriteTool",
    "WriteToolInput",
    "WriteToolOutput",
    "write_tool",
    "FileWriteTool",
]
