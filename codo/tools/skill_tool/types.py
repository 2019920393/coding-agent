"""SkillTool 类型定义"""
from dataclasses import dataclass, field
from typing import Optional, List
from pydantic import BaseModel, Field

@dataclass(frozen=True) #实例创建后，不能修改任何属性，也不能新增属性；强行修改会直接抛出 FrozenInstanceError 异常。
class SkillDefinition:
    """已加载 skill 的运行时定义。"""

    name: str
    prompt: str
    description: str = ""
    allowed_tools: List[str] = field(default_factory=list)
    model: Optional[str] = None
    user_invocable: bool = True #用户是否可见  有些内部不可见的但是能被别的skill触发调用 设计意图是这样的
    source_path: str = ""

class SkillInput(BaseModel):
    """Skill 输入参数"""
    skill: str = Field(description='The skill name. E.g., "commit", "review-pr", or "pdf"')
    args: Optional[str] = Field(default=None, description="Optional arguments for the skill")

class SkillOutputInline(BaseModel):
    """Skill 输出结果（inline 模式）"""
    success: bool = Field(description="Whether the skill was executed successfully")
    commandName: str = Field(description="The name of the executed skill")  # 就是skill名字
    allowedTools: Optional[List[str]] = Field(default=None, description="Tools allowed by this skill")
    model: Optional[str] = Field(default=None, description="Model to use for this skill")
    prompt: Optional[str] = Field(default=None, description="Resolved skill prompt content")
    description: Optional[str] = Field(default=None, description="Short description of the skill")
    sourcePath: Optional[str] = Field(default=None, description="Where the skill was loaded from")
    status: Optional[str] = Field(default="inline", description="Execution status")
# 在独立子agent使用
class SkillOutputForked(BaseModel):
    """Skill 输出结果（forked 模式）"""
    success: bool = Field(description="Whether the skill was executed successfully")
    commandName: str = Field(description="The name of the executed skill")
    status: str = Field(default="forked", description="Execution status")
    agentId: str = Field(description="The agent ID for forked execution")
    result: str = Field(description="The execution result")
