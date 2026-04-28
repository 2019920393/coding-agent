"""统一 memory 子系统共享的数据类型定义。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

MemoryType = Literal[
    "user",
    "feedback",
    "project",
    "reference",
    "preference",
    "project_fact",
    "task_context",
]

MEMORY_TYPES = [
    "user",
    "feedback",
    "project",
    "reference",
    "preference",
    "project_fact",
    "task_context",
]

class MemoryFrontmatter(BaseModel):
    """单个记忆文件的 frontmatter 结构。"""

    name: str = Field(default="", description="记忆标题")
    description: str = Field(description="单行摘要")
    type: MemoryType = Field(description="记忆分类")

class MemoryFile(BaseModel):
    """解析后的记忆文档。"""

    path: str = Field(description="文件绝对路径")
    frontmatter: MemoryFrontmatter = Field(description="解析后的 frontmatter")
    content: str = Field(description="去除 frontmatter 后的 Markdown 正文")
    mtime: datetime = Field(description="最后修改时间")

class MemoryIndexEntry(BaseModel):
    """`MEMORY.md` 中的一条索引项。"""

    title: str = Field(description="展示标题")
    filename: str = Field(description="文件名")
    hook: str = Field(description="单行描述")
