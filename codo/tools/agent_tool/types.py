"""
AgentTool 输入/输出类型定义

- AgentTool.tsx baseInputSchema (line 82-88)
- agentToolUtils.ts agentToolResultSchema (line 227-260)
"""


from pydantic import BaseModel, Field


class AgentToolInput(BaseModel):
    """AgentTool 输入 schema

    - description: 3-5 word task description
    - prompt: 子代理的任务提示
    - subagent_type: 代理类型 (Explore / Plan)
    """
    description: str = Field(
        description="A short (3-5 word) description of the task"
    )# 这个是给UI显示的一个简短的标签
    prompt: str = Field(
        description="The task for the agent to perform"
    )# 这个主要是实际的工作内容 给子agent的工作内容
    subagent_type: str | None = Field(  # Optional[str]可选不是必须传值
        default=None,
        description="The type of specialized agent to use for this task"
    )
    run_in_background: bool | None = Field(
        default=False,
        description="Whether to run this agent in the background and continue the main conversation immediately"
    ) # 让agent 后台执行 主agent可以继续执行

class AgentToolOutput(BaseModel):
    """AgentTool 输出 schema

    - result: 子代理最终文本输出
    - total_tokens / input_tokens / output_tokens: token 使用统计
    """
    result: str = Field(description="The agent's final text output") #子agent完成的最终文本
    total_tokens: int = Field(default=0) #三种类型token消耗
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    background: bool = Field(default=False) #这三个字段后台agent才有意义
    task_id: str | None = Field(default=None)  #关联后台任务的  标记任务完成  用户可以取消任务  后台发出的是所有事件通过id路由到正确的任务面板
    status: str | None = Field(default=None)
