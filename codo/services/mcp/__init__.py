"""
MCP (Model Context Protocol) 工具系统

[Workflow]
1. 配置管理：读取和管理 MCP 服务器配置
2. 客户端连接：连接到 MCP 服务器（stdio/SSE/HTTP 等）
3. 工具发现：列出服务器提供的工具
4. 工具调用：执行 MCP 工具并返回结果
5. 资源访问：读取 MCP 服务器提供的资源
"""

from .config import MCPConfig, MCPServerConfig, MCPConfigManager
from .client import MCPClientManager
from .types import MCPTransportType, MCPServerConnection, MCPToolInfo, MCPResourceInfo

__all__ = [
    "MCPConfig",
    "MCPServerConfig",
    "MCPConfigManager",
    "MCPClientManager",
    "MCPTransportType",
    "MCPServerConnection",
    "MCPToolInfo",
    "MCPResourceInfo",
]