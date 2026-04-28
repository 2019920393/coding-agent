"""
MCP 工具包装器
[Workflow]
1. 定义通用 MCP 工具输入/输出 schema
2. 创建 MCP 工具基类
3. 在运行时为每个 MCP 服务器工具创建子类实例
"""

from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, ConfigDict

from codo.tools.base import Tool, ToolUseContext
from codo.tools.types import ToolResult

class MCPToolInput(BaseModel):
    """
    MCP 工具输入 - 允许任意字段

    使用 Pydantic V2 的 ConfigDict 实现相同效果
    """
    model_config = ConfigDict(extra="allow")#允许这个数据模型接收未在类中定义的额外字段，不会报错。

class MCPToolOutput(BaseModel):
    """MCP 工具输出"""
    content: str = Field(description="工具执行结果")

class MCPToolBase(Tool[MCPToolInput, MCPToolOutput, None]):
    """
    MCP 工具基类

    每个 MCP 服务器工具会创建此类的子类实例
    """

    # 类属性
    name = "mcp"
    max_result_size_chars = 100_000
    input_schema = MCPToolInput
    output_schema = MCPToolOutput

    # MCP 元数据（在子类中设置）
    mcp_server_name: str = ""
    mcp_tool_name: str = ""
    mcp_description: str = ""

    async def description(self) -> str:
        """返回工具描述"""
        return self.mcp_description or f"MCP tool: {self.mcp_tool_name}"

    async def prompt(self, options: dict = None) -> str:
        """返回工具提示词"""
        return self.mcp_description or f"MCP tool: {self.mcp_tool_name}"

    def map_tool_result_to_tool_result_block_param(
        self,
        content: Any,
        tool_use_id: str
    ) -> Dict[str, Any]:
        """
        将工具结果映射为 API 格式

        """
        return {
            "tool_use_id": tool_use_id,
            "type": "tool_result",
            "content": str(content) if content else "",
        }

    async def call(
        self,
        input_data: MCPToolInput,
        context: ToolUseContext,
        on_progress=None
    ) -> ToolResult[MCPToolOutput]:
        """
        默认调用方法 - 应该被子类覆盖
        """
        raise NotImplementedError(
            f"MCP tool {self.name} call method should be overridden"
        )

def create_mcp_tool_class(
    qualified_name: str,
    server_name: str,
    tool_name: str,
    description: str,
    call_func,
) -> type[MCPToolBase]:
    """
    创建 MCP 工具类

    动态创建 MCPToolBase 的子类，设置名称、描述和调用方法

    Args:
        qualified_name: 完全限定名称（如 "server__toolname"）
        server_name: MCP 服务器名称
        tool_name: 工具名称
        description: 工具描述
        call_func: 工具调用函数

    Returns:
        type[MCPToolBase]: 工具类
    """
    # 包装 call_func 为实例方法
    async def call_method(self, input_data, context, on_progress=None):
        return await call_func(input_data, context, on_progress)

    # 创建子类
    tool_class = type(
        f"MCPTool_{qualified_name}",
        (MCPToolBase,),
        {
            "name": qualified_name,
            "mcp_server_name": server_name,
            "mcp_tool_name": tool_name,
            "mcp_description": description,
            "call": call_method,
        }
    )

    return tool_class

def create_mcp_tool_instance(
    name: str,
    description: str,
    call_func,
    input_schema: type[BaseModel] = MCPToolInput,
    output_schema: type[BaseModel] = MCPToolOutput,
    mcp_info: Optional[Dict[str, Any]] = None,
) -> Tool:
    """
    创建 MCP 工具实例

    [Workflow]
    1. 创建 MCPToolBase 子类
    2. 实例化工具
    3. 设置 MCP 元数据
    4. 返回工具实例

    Args:
        name: 工具名称（完全限定名，如 "server__toolname"）
        description: 工具描述
        call_func: 工具调用函数
        input_schema: 输入 schema（默认 MCPToolInput）
        output_schema: 输出 schema（默认 MCPToolOutput）
        mcp_info: MCP 元数据（server_name, tool_name 等）

    Returns:
        Tool: 工具实例
    """
    # 提取服务器名和工具名
    server_name = mcp_info.get("server_name", "") if mcp_info else ""
    tool_name = mcp_info.get("tool_name", "") if mcp_info else ""

    # 创建工具类
    tool_class = create_mcp_tool_class(
        qualified_name=name,
        server_name=server_name,
        tool_name=tool_name,
        description=description,
        call_func=call_func,
    )

    # 实例化工具
    tool = tool_class()

    # 添加 MCP 元数据标记
    if mcp_info:
        tool.mcp_info = mcp_info
    else:
        tool.mcp_info = {"is_mcp": True}

    return tool

