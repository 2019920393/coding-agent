"""Legacy shim for the canonical Read tool."""

from codo.tools.read_tool import ReadTool, ReadToolInput, ReadToolOutput, read_tool

FileReadTool = ReadTool

__all__ = [
    "ReadTool",
    "ReadToolInput",
    "ReadToolOutput",
    "read_tool",
    "FileReadTool",
]
