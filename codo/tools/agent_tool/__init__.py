"""
AgentTool 模块

子代理工具 — 生成独立子代理执行搜索、规划等任务。

"""

from .agent_tool import AgentTool, agent_tool
from .agents import (
    BUILTIN_AGENTS,
    EXPLORE_AGENT,
    PLAN_AGENT,
    AgentDefinition,
    find_agent_by_type,
    get_builtin_agents,
)
from .prompt import AGENT_TOOL_NAME
from .types import AgentToolInput, AgentToolOutput
from .utils import extract_final_text, filter_tools_for_agent

__all__ = [
    "AgentTool",
    "agent_tool",
    "AgentToolInput",
    "AgentToolOutput",
    "AgentDefinition",
    "EXPLORE_AGENT",
    "PLAN_AGENT",
    "BUILTIN_AGENTS",
    "get_builtin_agents",
    "find_agent_by_type",
    "AGENT_TOOL_NAME",
    "filter_tools_for_agent",
    "extract_final_text",
]
