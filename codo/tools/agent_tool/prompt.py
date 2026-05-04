"""
AgentTool 常量和 prompt 模板

- constants.ts — AGENT_TOOL_NAME
- prompt.ts — getPrompt() 生成 AgentTool 的 prompt
"""

from typing import List
from .agents import AgentDefinition, get_builtin_agents

# 工具名称常量

AGENT_TOOL_NAME = "Agent"


# 子代理最大对话轮数（防止无限循环）
MAX_AGENT_TURNS = 30

# 默认子代理类型（未指定时使用）
DEFAULT_AGENT_TYPE = "Explore"

# AgentTool 描述
DESCRIPTION = "Launch a new agent to handle a specific task, optionally in the background"

def get_agent_tool_prompt(agents: List[AgentDefinition] = None) -> str:
    """
    生成 AgentTool 的系统 prompt

    根据传入的 agent 定义动态生成描述，支持内置和自定义 agent。

    Args:
        agents: 可用的代理定义列表。如果为 None，使用内置代理。

    Returns:
        AgentTool 的 prompt 文本
    """
    if agents is None:
        agents = list(get_builtin_agents().values())

    # 构建代理类型描述（动态）
    agent_descriptions = []
    for agent in agents:
        agent_descriptions.append(
            f"- **{agent.agent_type}**: {agent.when_to_use}"
        )
    agent_list = "\n".join(agent_descriptions)

    # 动态判断是否所有 agent 都是只读
    all_read_only = all(a.is_read_only for a in agents) if agents else True
    if all_read_only:
        read_only_note = "2. Agents are read-only — they cannot modify files"
    else:
        read_only_note = "2. Some agents are read-only, others can modify files — check the agent type description above"

    return f"""Launch a new agent that has its own conversation context and tools to work on a specific task. Use agents to delegate work that can be done independently.

## Available Agent Types

{agent_list}

## How Agents Work

1. Each agent runs in its own conversation with its own set of tools
{read_only_note}
3. Results are returned to you as text
4. Agents are efficient for parallel research tasks
5. Long-running independent research can be started in the background so the main conversation can continue

## Parameters

- `description`: A short 3-5 word label (e.g., "Find auth middleware")
- `prompt`: Detailed instructions for the agent
- `subagent_type`: Which agent type to use (default: Explore)
- `run_in_background`: Set to true when the work is independent and you don't need the result immediately; completion will appear later as a notification"""
