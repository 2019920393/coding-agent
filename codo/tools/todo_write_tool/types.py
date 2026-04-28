"""TodoWriteTool 的类型定义"""
from enum import Enum
from typing import List
from pydantic import BaseModel, Field

class TodoStatus(str, Enum):
    """任务状态枚举"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"

class TodoItem(BaseModel):
    """单个任务项"""
    content: str = Field(..., min_length=1, description="任务内容（祈使句形式，如 'Run tests'）")
    status: TodoStatus = Field(..., description="任务状态")
    activeForm: str = Field(..., min_length=1, description="任务进行时形式（如 'Running tests'）")

class TodoWriteInput(BaseModel):
    """TodoWriteTool 输入"""
    todos: List[TodoItem] = Field(..., description="更新后的任务列表")

class TodoWriteOutput(BaseModel):
    """TodoWriteTool 输出"""
    oldTodos: List[TodoItem] = Field(..., description="更新前的任务列表")
    newTodos: List[TodoItem] = Field(..., description="更新后的任务列表")
    verificationNudgeNeeded: bool = Field(default=False, description="是否需要验证提醒")
