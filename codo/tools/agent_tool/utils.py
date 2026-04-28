"""
AgentTool 工具函数

- agentToolUtils.ts filterToolsForAgent() (line 70-100)
- agentToolUtils.ts resolveAgentTools() (line 102-180)
- constants/tools.ts ALL_AGENT_DISALLOWED_TOOLS
"""

from typing import List, Optional, Set

from .agents import AgentDefinition

# 所有子代理都不能使用的工具

ALL_AGENT_DISALLOWED_TOOLS: Set[str] = {
    "Agent",  # 防止子代理递归生成子代理
}

def filter_tools_for_agent(
    tools: list,
    agent_def: AgentDefinition,
) -> list:
    """
    过滤子代理可用的工具

    规则：
    1. ALL_AGENT_DISALLOWED_TOOLS 中的工具对所有子代理禁止
    2. agent_def.disallowed_tools 中的工具对该代理禁止
    3. MCP 工具（以 mcp__ 开头）允许通过

    Args:
        tools: 父代理的工具列表
        agent_def: 代理定义

    Returns:
        过滤后的工具列表
    """
    disallowed = set(agent_def.disallowed_tools) | ALL_AGENT_DISALLOWED_TOOLS

    result = []
    for tool in tools:
        tool_name = tool.name if hasattr(tool, 'name') else str(tool)

        # MCP 工具总是允许
        if tool_name.startswith("mcp__"):
            result.append(tool)
            continue

        # 检查是否在禁止列表中
        if tool_name in disallowed:
            continue

        result.append(tool)

    return result

def extract_final_text(messages: list) -> str:
    """
    从子代理消息历史中提取最终文本输出

    提取最后一个 assistant 消息中的所有文本内容。

    Args:
        messages: 子代理的消息历史

    Returns:
        最终文本输出
    """
    # 从后往前找最后一个 assistant 消息
    for msg in reversed(messages):
        role = msg.get("role", "")
        if role != "assistant":
            continue

        content = msg.get("content", [])

        # 如果 content 是字符串
        if isinstance(content, str):
            return content

        # 如果 content 是列表（content blocks）
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif hasattr(block, "type") and block.type == "text":
                    text_parts.append(block.text)
            if text_parts:
                return "\n".join(text_parts)

    return ""
