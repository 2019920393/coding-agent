"""
MCP 类型定义

[Workflow]
定义 MCP 系统使用的核心类型和枚举
"""

from enum import Enum
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field

class MCPTransportType(str, Enum):
    """MCP 传输类型"""
    STDIO = "stdio"  # 标准输入输出
    SSE = "sse"      # 服务器发送事件
    HTTP = "http"    # HTTP 请求
    WEBSOCKET = "ws" # WebSocket

class MCPServerConnection(BaseModel):
    """MCP 服务器连接状态"""
    name: str = Field(..., description="服务器名称")
    transport: MCPTransportType = Field(..., description="传输类型")
    connected: bool = Field(default=False, description="是否已连接")
    tools_count: int = Field(default=0, description="可用工具数量")
    resources_count: int = Field(default=0, description="可用资源数量")
    error: Optional[str] = Field(default=None, description="错误信息")

class MCPToolInfo(BaseModel):
    """MCP 工具信息"""
    name: str = Field(..., description="工具名称")
    description: Optional[str] = Field(default=None, description="工具描述")
    input_schema: Dict[str, Any] = Field(default_factory=dict, description="输入参数 schema")
    server_name: str = Field(..., description="所属服务器名称")

class MCPResourceInfo(BaseModel):
    """MCP 资源信息"""
    uri: str = Field(..., description="资源 URI")
    name: str = Field(..., description="资源名称")
    description: Optional[str] = Field(default=None, description="资源描述")
    mime_type: Optional[str] = Field(default=None, description="MIME 类型")
    server_name: str = Field(..., description="所属服务器名称")
