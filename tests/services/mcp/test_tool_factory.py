"""
测试 MCP 工具工厂

验证 MCP 工具的创建、命名和调用逻辑
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from codo.services.mcp.tool_factory import (
    build_mcp_tool_name,
    create_mcp_tool,
    fetch_mcp_tools,
    fetch_all_mcp_tools,
    _extract_mcp_result_content,
)
from codo.services.mcp.types import MCPToolInfo
from codo.tools.base import ToolUseContext

def test_build_mcp_tool_name():
    """测试 MCP 工具名称构建"""
    # 基本情况
    assert build_mcp_tool_name("server", "tool") == "server__tool"

    # 带下划线的工具名
    assert build_mcp_tool_name("my_server", "my_tool") == "my_server__my_tool"

    # 特殊字符
    assert build_mcp_tool_name("server-1", "tool.name") == "server-1__tool.name"

def test_extract_mcp_result_content_string():
    """测试从字符串提取内容"""
    result = "simple string"
    assert _extract_mcp_result_content(result) == "simple string"

def test_extract_mcp_result_content_object_with_content():
    """测试从对象提取 content 字段"""
    # 模拟对象
    result = MagicMock()
    result.content = "content from object"

    assert _extract_mcp_result_content(result) == "content from object"

def test_extract_mcp_result_content_list():
    """测试从内容列表提取"""
    # 模拟对象列表
    item1 = MagicMock()
    item1.text = "part 1"

    item2 = MagicMock()
    item2.text = "part 2"

    result = MagicMock()
    result.content = [item1, item2]

    assert _extract_mcp_result_content(result) == "part 1\npart 2"

def test_extract_mcp_result_content_dict():
    """测试从字典提取内容"""
    result = {"content": "content from dict"}
    assert _extract_mcp_result_content(result) == "content from dict"

    result = {"content": ["item1", "item2"]}
    assert _extract_mcp_result_content(result) == "item1\nitem2"

@pytest.mark.asyncio
async def test_create_mcp_tool():
    """测试创建 MCP 工具实例"""
    # Mock MCP 客户端管理器
    mcp_client = AsyncMock()
    mcp_client.call_tool = AsyncMock(return_value=MagicMock(content="tool result"))

    # 创建工具信息
    tool_info = MCPToolInfo(
        name="test_tool",
        description="Test tool description",
        input_schema={},
        server_name="test_server"
    )

    # 创建工具
    tool = await create_mcp_tool(mcp_client, "test_server", tool_info)

    # 验证工具属性
    assert tool.name == "test_server__test_tool"
    assert await tool.description() == "Test tool description"
    assert tool.mcp_info["server_name"] == "test_server"
    assert tool.mcp_info["tool_name"] == "test_tool"
    assert tool.mcp_info["is_mcp"] is True

@pytest.mark.asyncio
async def test_create_mcp_tool_call():
    """测试 MCP 工具调用"""
    # Mock MCP 客户端管理器
    mcp_client = AsyncMock()

    # Mock 工具调用结果
    mock_result = MagicMock()
    mock_result.content = "tool execution result"
    mcp_client.call_tool = AsyncMock(return_value=mock_result)

    # 创建工具信息
    tool_info = MCPToolInfo(
        name="test_tool",
        description="Test tool",
        input_schema={},
        server_name="test_server"
    )

    # 创建工具
    tool = await create_mcp_tool(mcp_client, "test_server", tool_info)

    # 调用工具
    from codo.tools.mcp_tool import MCPToolInput
    input_data = MCPToolInput()
    context = ToolUseContext(
        options={},
        abort_controller=None,
        messages=[]
    )

    result = await tool.call(input_data, context)

    # 验证调用
    assert result.data.content == "tool execution result"
    assert result.mcp_meta["server"] == "test_server"
    assert result.mcp_meta["tool"] == "test_tool"
    assert result.mcp_meta["is_mcp"] is True

    # 验证 MCP 客户端被正确调用
    mcp_client.call_tool.assert_called_once_with(
        server_name="test_server",
        tool_name="test_tool",
        arguments={}
    )

@pytest.mark.asyncio
async def test_create_mcp_tool_call_error():
    """测试 MCP 工具调用失败"""
    # Mock MCP 客户端管理器
    mcp_client = AsyncMock()
    mcp_client.call_tool = AsyncMock(side_effect=Exception("Connection failed"))

    # 创建工具信息
    tool_info = MCPToolInfo(
        name="test_tool",
        description="Test tool",
        input_schema={},
        server_name="test_server"
    )

    # 创建工具
    tool = await create_mcp_tool(mcp_client, "test_server", tool_info)

    # 调用工具
    from codo.tools.mcp_tool import MCPToolInput
    input_data = MCPToolInput()
    context = ToolUseContext(
        options={},
        abort_controller=None,
        messages=[]
    )

    result = await tool.call(input_data, context)

    # 验证错误处理
    assert result.error is not None
    assert "Connection failed" in result.error
    assert result.mcp_meta["is_mcp"] is True

@pytest.mark.asyncio
async def test_fetch_mcp_tools():
    """测试从 MCP 服务器获取工具列表"""
    # Mock MCP 客户端管理器
    mcp_client = AsyncMock()

    # Mock 工具列表
    tool_infos = [
        MCPToolInfo(
            name="tool1",
            description="Tool 1",
            input_schema={},
            server_name="test_server"
        ),
        MCPToolInfo(
            name="tool2",
            description="Tool 2",
            input_schema={},
            server_name="test_server"
        ),
    ]
    mcp_client.list_tools = AsyncMock(return_value=tool_infos)

    # 获取工具
    tools = await fetch_mcp_tools(mcp_client, "test_server")

    # 验证结果
    assert len(tools) == 2
    assert tools[0].name == "test_server__tool1"
    assert tools[1].name == "test_server__tool2"

    # 验证客户端被调用
    mcp_client.list_tools.assert_called_once_with("test_server")

@pytest.mark.asyncio
async def test_fetch_mcp_tools_error():
    """测试获取工具失败时返回空列表"""
    # Mock MCP 客户端管理器
    mcp_client = AsyncMock()
    mcp_client.list_tools = AsyncMock(side_effect=Exception("Server not connected"))

    # 获取工具（应该返回空列表而不是抛出异常）
    tools = await fetch_mcp_tools(mcp_client, "test_server")

    # 验证返回空列表
    assert tools == []

@pytest.mark.asyncio
async def test_fetch_all_mcp_tools():
    """测试从所有已连接服务器获取工具"""
    # Mock MCP 客户端管理器
    mcp_client = AsyncMock()

    # Mock 连接状态
    from codo.services.mcp.types import MCPServerConnection
    connections = {
        "server1": MCPServerConnection(
            name="server1",
            transport="stdio",
            connected=True,
            tools_count=2,
            resources_count=0
        ),
        "server2": MCPServerConnection(
            name="server2",
            transport="stdio",
            connected=True,
            tools_count=1,
            resources_count=0
        ),
        "server3": MCPServerConnection(
            name="server3",
            transport="stdio",
            connected=False,  # 未连接
            tools_count=0,
            resources_count=0
        ),
    }
    mcp_client.list_connections = MagicMock(return_value=connections)

    # Mock list_tools 返回
    async def mock_list_tools(server_name):
        if server_name == "server1":
            return [
                MCPToolInfo(name="tool1", description="", input_schema={}, server_name=server_name),
                MCPToolInfo(name="tool2", description="", input_schema={}, server_name=server_name),
            ]
        elif server_name == "server2":
            return [
                MCPToolInfo(name="tool3", description="", input_schema={}, server_name=server_name),
            ]
        return []

    mcp_client.list_tools = AsyncMock(side_effect=mock_list_tools)

    # 获取所有工具
    tools = await fetch_all_mcp_tools(mcp_client)

    # 验证结果
    assert len(tools) == 3
    assert tools[0].name == "server1__tool1"
    assert tools[1].name == "server1__tool2"
    assert tools[2].name == "server2__tool3"

    # 验证只调用了已连接的服务器
    assert mcp_client.list_tools.call_count == 2
