"""
MCP 工具包装器模块
"""

from .mcp_tool import (
    MCPToolBase,
    MCPToolInput,
    MCPToolOutput,
    create_mcp_tool_class,
    create_mcp_tool_instance,
)
from .prompt import DESCRIPTION, PROMPT

__all__ = [
    "MCPToolInput",
    "MCPToolOutput",
    "MCPToolBase",
    "create_mcp_tool_instance",
    "create_mcp_tool_class",
    "DESCRIPTION",
    "PROMPT",
]
