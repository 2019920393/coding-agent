"""
MCP 工具工厂
[Workflow]
1. 从 MCP 服务器获取工具列表
2. 为每个工具创建 Tool 实例
3. 覆盖工具的 name, description, call 方法
4. 返回工具列表供 QueryEngine 使用
"""

from typing import List, Any

from codo.tools.base import Tool, ToolUseContext
from codo.tools.mcp_tool import (
    MCPToolInput,
    MCPToolOutput,
    create_mcp_tool_instance,
)
from codo.tools.types import ToolResult
from codo.services.mcp.client import MCPClientManager
from codo.services.mcp.types import MCPToolInfo

def build_mcp_tool_name(server_name: str, tool_name: str) -> str:
    """
    构建 MCP 工具的完全限定名称

    格式: "server__toolname"
    使用双下划线避免与工具名称中的单下划线冲突

    Args:
        server_name: MCP 服务器名称
        tool_name: 工具名称

    Returns:
        str: 完全限定名称，如 "filesystem__read_file"
    """
    return f"{server_name}__{tool_name}"

async def create_mcp_tool(
    mcp_client: MCPClientManager,
    server_name: str,
    tool_info: MCPToolInfo
) -> Tool:
    """
    从 MCP 工具信息创建 Tool 实例

    [Workflow]
    1. 构建完全限定名称
    2. 创建工具调用闭包（捕获 mcp_client, server_name, tool_info）
    3. 使用 create_mcp_tool_instance 创建工具
    4. 添加 MCP 元数据
    5. 返回工具实例

    Args:
        mcp_client: MCP 客户端管理器
        server_name: MCP 服务器名称
        tool_info: MCP 工具信息

    Returns:
        Tool: 工具实例
    """
    qualified_name = build_mcp_tool_name(server_name, tool_info.name)

    # 创建工具调用闭包

    async def mcp_call(
        input_data: MCPToolInput,
        context: ToolUseContext,
        on_progress=None
    ) -> ToolResult[MCPToolOutput]:
        """
        调用 MCP 服务器工具

        """
        try:
            # 将 Pydantic 模型转换为字典
            # input_data 可能是 MCPToolInput 实例或字典
            if isinstance(input_data, dict):
                arguments = input_data
            else:
                arguments = input_data.model_dump(exclude_unset=True)

            # 调用 MCP 客户端
            result = await mcp_client.call_tool(
                server_name=server_name,
                tool_name=tool_info.name,
                arguments=arguments
            )

            # 提取内容
            # MCP 工具结果可能是多种格式，需要统一处理
            content = _extract_mcp_result_content(result)

            return ToolResult(
                data=MCPToolOutput(content=content),
                mcp_meta={
                    "server": server_name,
                    "tool": tool_info.name,
                    "is_mcp": True
                }
            )

        except Exception as e:
            # 工具调用失败，返回错误
            return ToolResult(
                error=f"MCP tool call failed: {str(e)}",
                mcp_meta={
                    "server": server_name,
                    "tool": tool_info.name,
                    "is_mcp": True
                }
            )

    # 创建工具实例
    tool = create_mcp_tool_instance(
        name=qualified_name,
        description=tool_info.description or f"MCP tool: {tool_info.name}",
        call_func=mcp_call,
        mcp_info={
            "server_name": server_name,
            "tool_name": tool_info.name,
            "is_mcp": True
        }
    )

    return tool

def _extract_mcp_result_content(result: Any) -> str:
    """
    从 MCP 工具结果中提取内容

    MCP 工具结果可能有多种格式：
    - 字符串
    - 包含 content 字段的对象
    - 包含 content 列表的对象（多个内容块）

    Args:
        result: MCP 工具调用结果

    Returns:
        str: 提取的内容字符串
    """
    # 如果是字符串，直接返回
    if isinstance(result, str):
        return result

    # 如果有 content 属性
    if hasattr(result, 'content'):
        content = result.content

        # content 是列表（多个内容块）
        if isinstance(content, list):
            parts = []
            for item in content:
                if hasattr(item, 'text'):
                    parts.append(str(item.text))
                elif isinstance(item, dict) and 'text' in item:
                    parts.append(str(item['text']))
                else:
                    parts.append(str(item))
            return "\n".join(parts)

        # content 是单个值
        return str(content)

    # 如果是字典
    if isinstance(result, dict):
        if 'content' in result:
            content = result['content']
            if isinstance(content, list):
                return "\n".join(str(c) for c in content)
            return str(content)

    # 其他情况，转换为字符串
    return str(result)

async def fetch_mcp_tools(
    mcp_client: MCPClientManager,
    server_name: str
) -> List[Tool]:
    """
    从 MCP 服务器获取所有工具

    [Workflow]
    1. 调用 mcp_client.list_tools() 获取工具列表
    2. 为每个工具调用 create_mcp_tool() 创建 Tool 实例
    3. 返回工具列表
    4. 如果失败，记录警告但返回空列表（不中断整体流程）

    Args:
        mcp_client: MCP 客户端管理器
        server_name: MCP 服务器名称

    Returns:
        List[Tool]: 工具列表（失败时返回空列表）
    """
    try:
        # 获取工具信息列表
        tool_infos = await mcp_client.list_tools(server_name)

        # 为每个工具创建 Tool 实例
        tools = []
        for tool_info in tool_infos:
            tool = await create_mcp_tool(mcp_client, server_name, tool_info)
            tools.append(tool)

        return tools

    except Exception as e:
        # 记录错误但不中断

        print(f"Warning: Failed to fetch MCP tools from {server_name}: {e}")
        return []

async def fetch_all_mcp_tools(
    mcp_client: MCPClientManager
) -> List[Tool]:
    """
    从所有已连接的 MCP 服务器获取工具

    [Workflow]
    1. 获取所有已连接的服务器列表
    2. 对每个服务器调用 fetch_mcp_tools()
    3. 合并所有工具列表
    4. 返回合并后的工具列表

    Args:
        mcp_client: MCP 客户端管理器

    Returns:
        List[Tool]: 所有 MCP 工具的列表
    """
    all_tools = []

    # 获取所有连接状态
    connections = mcp_client.list_connections()

    # 兼容 list / dict 两种返回结构（测试桩常用 dict）
    if isinstance(connections, dict):
        iterable = connections.values()
    else:
        iterable = connections

    # 遍历每个服务器
    for connection in iterable:
        if connection.connected:
            # 获取该服务器的工具
            server_name = getattr(connection, "server_name", None) or getattr(connection, "name", None)
            if not server_name:
                continue
            tools = await fetch_mcp_tools(mcp_client, server_name)
            all_tools.extend(tools)

    return all_tools
