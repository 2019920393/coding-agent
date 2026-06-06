"""
AgentTool 工具函数

- agentToolUtils.ts filterToolsForAgent() (line 70-100)
- agentToolUtils.ts resolveAgentTools() (line 102-180)
- constants/tools.ts ALL_AGENT_DISALLOWED_TOOLS
"""


from .agents import AgentDefinition

# 所有子代理都不能使用的工具

ALL_AGENT_DISALLOWED_TOOLS: set[str] = {
    "Agent",  # 防止子代理递归生成子代理
}


def filter_tools_for_agent(tools: list, agent_def: AgentDefinition) -> list:
    """
    根据代理定义过滤可用工具列表。

    [Workflow]
    1. 合并 agent_def.disallowed_tools 和全局黑名单 ALL_AGENT_DISALLOWED_TOOLS
    2. MCP 工具（名称以 "mcp__" 开头）始终允许，跳过过滤
    3. 黑名单中的工具直接排除
    4. 若 agent_def.tools 不为 None，只保留白名单中的工具

    参数:
        tools: 全量工具列表
        agent_def: 代理定义，包含 tools（白名单）和 disallowed_tools（黑名单）

    返回:
        list: 过滤后的工具列表，如 [bash_tool, read_tool, grep_tool]
    """
    disallowed = set(agent_def.disallowed_tools) | ALL_AGENT_DISALLOWED_TOOLS

    result = []
    for tool in tools:
        tool_name = tool.name if hasattr(tool, 'name') else str(tool)

        # MCP 工具总是允许
        if tool_name.startswith("mcp__"):
            result.append(tool)
            continue

        # 黑名单过滤
        if tool_name in disallowed:
            continue

        # 白名单过滤：如果 agent_def 指定了 tools，只保留白名单里的
        if agent_def.tools is not None and tool_name not in agent_def.tools:
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
