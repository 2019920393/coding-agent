"""
MCP 工具包装器模块
"""

from .mcp_tool import (
    MCPToolInput,
    MCPToolOutput,
    MCPToolBase,
    create_mcp_tool_instance,
    create_mcp_tool_class,
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
