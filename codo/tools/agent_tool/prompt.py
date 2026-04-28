"""
AgentTool 常量和 prompt 模板

- constants.ts — AGENT_TOOL_NAME, LEGACY_AGENT_TOOL_NAME
- prompt.ts — getPrompt() 生成 AgentTool 的 prompt
"""

from typing import List
from .agents import AgentDefinition, get_builtin_agents

# 工具名称常量

AGENT_TOOL_NAME = "Agent"
LEGACY_AGENT_TOOL_NAME = "Task"

# 子代理最大对话轮数（防止无限循环）
MAX_AGENT_TURNS = 30

# 默认子代理类型（未指定时使用）
DEFAULT_AGENT_TYPE = "Explore"

# AgentTool 描述
DESCRIPTION = "Launch a new agent to handle a specific task, optionally in the background"

def get_agent_tool_prompt(agents: List[AgentDefinition] = None) -> str:
    """
    生成 AgentTool 的系统 prompt

    简化版本：只包含代理类型列表和使用说明

    Args:
        agents: 可用的代理定义列表。如果为 None，使用内置代理。

    Returns:
        AgentTool 的 prompt 文本
    """
    if agents is None:
        agents = list(get_builtin_agents().values())

    # 构建代理类型描述
    agent_descriptions = []
    for agent in agents:
        agent_descriptions.append(
            f"- **{agent.agent_type}**: {agent.when_to_use}"
        )
    agent_list = "\n".join(agent_descriptions)

    return f"""Launch a new agent that has its own conversation context and tools to work on a specific task. Use agents to delegate work that can be done independently.

## Available Agent Types

{agent_list}

## When to Use Agents

- Use **Explore** when you need to search the codebase, find files, or understand code structure
- Use **Plan** when you need to design an implementation approach before making changes

## How Agents Work

1. Each agent runs in its own conversation with its own set of tools
2. Agents are read-only — they cannot modify files
3. Results are returned to you as text
4. Agents are efficient for parallel research tasks
5. Long-running independent research can be started in the background so the main conversation can continue

## Parameters

- `description`: A short 3-5 word label (e.g., "Find auth middleware")
- `prompt`: Detailed instructions for the agent
- `subagent_type`: Which agent type to use (default: Explore)
- `run_in_background`: Set to true when the work is independent and you don't need the result immediately; completion will appear later as a notification"""
