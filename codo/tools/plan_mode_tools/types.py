"""PlanMode 工具类型定义"""

from pydantic import BaseModel, Field


class AllowedPrompt(BaseModel):
    """允许的提示权限"""
    tool: str = Field(description="The tool this prompt applies to (e.g., 'Bash')")
    prompt: str = Field(description="Semantic description of the action")

class EnterPlanModeInput(BaseModel):
    """EnterPlanMode 输入参数（无参数）"""
    pass

class EnterPlanModeOutput(BaseModel):
    """EnterPlanMode 输出结果"""
    message: str = Field(description="Confirmation that plan mode was entered")

class ExitPlanModeInput(BaseModel):
    """ExitPlanMode 输入参数"""
    allowedPrompts: list[AllowedPrompt] | None = Field(
        default=None,
        description="Prompt-based permissions needed to implement the plan"
    )
    plan: str | None = Field(default=None, description="Plan content (injected from disk)")
    planFilePath: str | None = Field(default=None, description="Plan file path (injected)")

class ExitPlanModeOutput(BaseModel):
    """ExitPlanMode 输出结果"""
    plan: str | None = Field(description="The approved plan content")
    isAgent: bool = Field(description="Whether this is an agent context")
    filePath: str | None = Field(default=None, description="Plan file path")
    planWasEdited: bool | None = Field(default=None, description="Whether plan was edited by user")
