"""
MCP 客户端管理

[Workflow]
1. 连接到 MCP 服务器（stdio/SSE/HTTP 等）
2. 管理客户端生命周期（连接、断开、重连）
3. 列出可用工具和资源
4. 执行工具调用
5. 读取资源内容
"""

from contextlib import AsyncExitStack
from typing import Dict, Any, Optional, List

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import MCPConfigManager
from .types import MCPServerConnection, MCPToolInfo, MCPResourceInfo, MCPTransportType

class MCPClientManager:
    """
    MCP 客户端管理器

    [Workflow]
    1. 根据配置连接到 MCP 服务器
    2. 管理多个客户端会话
    3. 提供工具调用和资源访问接口
    """

    def __init__(self, config_manager: MCPConfigManager):
        """
        初始化客户端管理器

        Args:
            config_manager: 配置管理器
        """
        self.config_manager = config_manager
        self._sessions: Dict[str, ClientSession] = {}
        self._session_stacks: Dict[str, AsyncExitStack] = {}
        self._connections: Dict[str, MCPServerConnection] = {}

    async def connect(self, server_name: str) -> bool:
        """
        连接到 MCP 服务器

        Args:
            server_name: 服务器名称

        Returns:
            是否连接成功
        """
        # 如果已连接，直接返回
        if server_name in self._sessions:
            return True

        # 获取服务器配置
        server_config = self.config_manager.get_server_config(server_name)
        if not server_config:
            raise ValueError(f"未找到服务器配置: {server_name}")

        if server_config.disabled:
            raise ValueError(f"服务器已禁用: {server_name}")

        try:
            # 目前只支持 stdio 传输
            if server_config.transport != MCPTransportType.STDIO:
                raise NotImplementedError(f"暂不支持传输类型: {server_config.transport}")

            # 创建 stdio 客户端
            server_params = StdioServerParameters(
                command=server_config.command,
                args=server_config.args,
                env=server_config.env or None
            )

            stack = AsyncExitStack()
            try:
                read, write = await stack.enter_async_context(stdio_client(server_params))
                session = ClientSession(read, write)

                # 初始化会话
                await session.initialize()

                # 获取服务器能力
                capabilities = session.get_server_capabilities()

                # 列出工具和资源
                tools_count = 0
                resources_count = 0

                if capabilities and capabilities.tools:
                    tools_result = await session.list_tools()
                    tools_count = len(tools_result.tools) if tools_result else 0

                if capabilities and capabilities.resources:
                    resources_result = await session.list_resources()
                    resources_count = len(resources_result.resources) if resources_result else 0

                # 保存会话
                self._sessions[server_name] = session
                self._session_stacks[server_name] = stack

                # 更新连接状态
                self._connections[server_name] = MCPServerConnection(
                    name=server_name,
                    transport=server_config.transport,
                    connected=True,
                    tools_count=tools_count,
                    resources_count=resources_count
                )

                return True
            except Exception:
                await stack.aclose()
                raise

        except Exception as e:
            # 连接失败，记录错误
            self._connections[server_name] = MCPServerConnection(
                name=server_name,
                transport=server_config.transport,
                connected=False,
                error=str(e)
            )
            return False

    async def disconnect(self, server_name: str) -> None:
        """
        断开与 MCP 服务器的连接

        Args:
            server_name: 服务器名称
        """
        if server_name in self._sessions:
            del self._sessions[server_name]

        stack = self._session_stacks.pop(server_name, None)
        if stack is not None:
            await stack.aclose()

        if server_name in self._connections:
            del self._connections[server_name]

    async def list_tools(self, server_name: str) -> List[MCPToolInfo]:
        """
        列出服务器提供的工具

        Args:
            server_name: 服务器名称

        Returns:
            工具信息列表
        """
        session = self._sessions.get(server_name)
        if not session:
            raise ValueError(f"未连接到服务器: {server_name}")

        try:
            result = await session.list_tools()
            tools = []

            if result and result.tools:
                for tool in result.tools:
                    tools.append(MCPToolInfo(
                        name=tool.name,
                        description=tool.description,
                        input_schema=tool.inputSchema if hasattr(tool, 'inputSchema') else {},
                        server_name=server_name
                    ))

            return tools

        except Exception as e:
            raise RuntimeError(f"列出工具失败: {e}")

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: Dict[str, Any]
    ) -> Any:
        """
        调用 MCP 工具

        Args:
            server_name: 服务器名称
            tool_name: 工具名称
            arguments: 工具参数

        Returns:
            工具执行结果
        """
        session = self._sessions.get(server_name)
        if not session:
            raise ValueError(f"未连接到服务器: {server_name}")

        try:
            result = await session.call_tool(tool_name, arguments)
            return result

        except Exception as e:
            raise RuntimeError(f"调用工具失败: {e}")

    async def list_resources(self, server_name: str) -> List[MCPResourceInfo]:
        """
        列出服务器提供的资源

        Args:
            server_name: 服务器名称

        Returns:
            资源信息列表
        """
        session = self._sessions.get(server_name)
        if not session:
            raise ValueError(f"未连接到服务器: {server_name}")

        try:
            result = await session.list_resources()
            resources = []

            if result and result.resources:
                for resource in result.resources:
                    resources.append(MCPResourceInfo(
                        uri=resource.uri,
                        name=resource.name,
                        description=resource.description if hasattr(resource, 'description') else None,
                        mime_type=resource.mimeType if hasattr(resource, 'mimeType') else None,
                        server_name=server_name
                    ))

            return resources

        except Exception as e:
            raise RuntimeError(f"列出资源失败: {e}")

    async def read_resource(self, server_name: str, uri: str) -> Any:
        """
        读取资源内容

        Args:
            server_name: 服务器名称
            uri: 资源 URI

        Returns:
            资源内容
        """
        session = self._sessions.get(server_name)
        if not session:
            raise ValueError(f"未连接到服务器: {server_name}")

        try:
            result = await session.read_resource(uri)
            return result

        except Exception as e:
            raise RuntimeError(f"读取资源失败: {e}")

    def get_connection_status(self, server_name: str) -> Optional[MCPServerConnection]:
        """
        获取服务器连接状态

        Args:
            server_name: 服务器名称

        Returns:
            连接状态，如果未连接返回 None
        """
        return self._connections.get(server_name)

    def list_connections(self) -> List[MCPServerConnection]:
        """
        列出所有连接状态

        Returns:
            连接状态列表
        """
        return list(self._connections.values())

    async def disconnect_all(self) -> None:
        """断开所有连接"""
        server_names = list(self._sessions.keys())
        for server_name in server_names:
            await self.disconnect(server_name)
