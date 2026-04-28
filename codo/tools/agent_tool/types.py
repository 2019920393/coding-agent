"""
AgentTool 输入/输出类型定义

- AgentTool.tsx baseInputSchema (line 82-88)
- agentToolUtils.ts agentToolResultSchema (line 227-260)
"""

from pydantic import BaseModel, Field
from typing import Optional

class AgentToolInput(BaseModel):
    """AgentTool 输入 schema

    - description: 3-5 word task description
    - prompt: 子代理的任务提示
    - subagent_type: 代理类型 (Explore / Plan)
    """
    description: str = Field(
        description="A short (3-5 word) description of the task"
    )
    prompt: str = Field(
        description="The task for the agent to perform"
    )
    subagent_type: Optional[str] = Field(
        default=None,
        description="The type of specialized agent to use for this task"
    )
    run_in_background: Optional[bool] = Field(
        default=False,
        description="Whether to run this agent in the background and continue the main conversation immediately"
    )

class AgentToolOutput(BaseModel):
    """AgentTool 输出 schema

    - result: 子代理最终文本输出
    - total_tokens / input_tokens / output_tokens: token 使用统计
    """
    result: str = Field(description="The agent's final text output")
    total_tokens: int = Field(default=0)
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    background: bool = Field(default=False)
    task_id: Optional[str] = Field(default=None)
    status: Optional[str] = Field(default=None)
