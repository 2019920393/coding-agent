"""
工具模式转换

将工具定义转换为 ?? API 格式的 JSON Schema。

参考：src/utils/api.ts - toolToAPISchema()
简化：移除工具模式缓存、defer_loading、Beta 头管理
保留：基础 JSON Schema 转换、工具描述生成
"""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaValue
from pydantic_core import core_schema
from codo.tools.base import Tool

class PydanticToJsonSchemaConverter:
    """
    Pydantic 模型转 JSON Schema 转换器

    [Workflow]
    将 Pydantic 模型转换为 ?? API 兼容的 JSON Schema
    """

    @staticmethod
    def convert(model: type[BaseModel]) -> Dict[str, Any]:
        """
        转换 Pydantic 模型为 JSON Schema

        [Workflow]
        1. 使用 Pydantic 的 model_json_schema() 生成基础 schema
        2. 移除不必要的字段（$defs、title 等）
        3. 确保符合 ?? API 要求

        Args:
            model: Pydantic 模型类

        Returns:
            JSON Schema 字典
        """
        # 生成基础 JSON Schema
        schema = model.model_json_schema()

        # 移除顶层的 title（?? API 不需要）
        schema.pop("title", None)

        # 移除顶层的 description（?? API input_schema 不允许顶层 description）
        schema.pop("description", None)

        # 移除 $defs（如果存在且为空）
        if "$defs" in schema and not schema["$defs"]:
            schema.pop("$defs")

        # 确保有 type 字段
        if "type" not in schema:
            schema["type"] = "object"

        # 确保有 properties 字段
        if "properties" not in schema:
            schema["properties"] = {}

        return schema

async def tool_to_api_schema(tool: Tool, options: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    将工具转换为 ?? API 格式的 schema

    [Workflow]
    1. 获取工具名称
    2. 生成工具描述（调用 tool.prompt()）
    3. 转换输入 schema（Pydantic → JSON Schema）
    4. 组装为 ?? API 格式
    5. 添加缓存控制

    Args:
        tool: 工具实例
        options: 传递给 tool.prompt() 的选项（包含 tools, agents 等）

    Returns:
        ?? API 格式的工具 schema
    """
    # 获取工具名称
    name = tool.name

    # 生成工具描述
    if options is None:
        options = {}
    description = await tool.prompt(options)

    # 转换输入 schema
    if tool.input_schema:
        input_schema = PydanticToJsonSchemaConverter.convert(tool.input_schema)
    else:
        # 如果没有输入 schema，使用空对象
        input_schema = {
            "type": "object",
            "properties": {},
        }

    # 组装为 ?? API 格式
    schema = {
        "name": name,
        "description": description,
        "input_schema": input_schema,
    }

    # 添加缓存控制（最后一个工具添加缓存标记）
    # 注意：这里简化处理，实际应该在 assembler 中统一添加
    # schema["cache_control"] = {"type": "ephemeral"}

    return schema

async def tools_to_api_schemas(
    tools: List[Tool],
    agents: Optional[List[Any]] = None,
) -> List[Dict[str, Any]]:
    """
    将工具列表转换为 ?? API 格式的 schema 列表

    [Workflow]
    1. 遍历所有工具
    2. 转换每个工具为 API schema
    3. 在最后一个工具上添加缓存控制标记

    Args:
        tools: 工具列表
        agents: 可用的 agent 定义列表（传递给 AgentTool.prompt()）

    Returns:
        ?? API 格式的工具 schema 列表
    """
    if not tools:
        return []

    # 构建 options 对象
    options = {
        "tools": tools,
        "agents": agents or [],
    }

    # 转换所有工具
    schemas = []
    for tool in tools:
        schema = await tool_to_api_schema(tool, options)
        schemas.append(schema)

    # 在最后一个工具上添加缓存控制标记
    if schemas:
        schemas[-1]["cache_control"] = {"type": "ephemeral"}

    return schemas

def format_tool_list_for_prompt(tools: List[Tool]) -> str:
    """
    格式化工具列表为提示词文本（用于调试）

    [Workflow]
    1. 遍历所有工具
    2. 格式化为可读的文本列表

    Args:
        tools: 工具列表

    Returns:
        格式化的工具列表文本
    """
    if not tools:
        return "No tools available."

    lines = ["Available tools:"]
    for i, tool in enumerate(tools, 1):
        lines.append(f"{i}. {tool.name} - {tool.description}")

    return "\n".join(lines)
