"""
ReadTool 类型定义

定义 ReadTool 的输入输出 schema。
"""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class ReadToolInput(BaseModel):
    """ReadTool 输入参数"""

    file_path: str = Field(
        description="要读取的文件的绝对路径"
    )

    offset: Optional[int] = Field(
        default=0,
        description="起始行号（从 0 开始）。仅在文件过大无法一次读取时提供"
    )

    limit: Optional[int] = Field(
        default=None,
        description="要读取的行数。仅在文件过大无法一次读取时提供"
    )

    pages: Optional[str] = Field(
        default=None,
        description="PDF 文件的页码范围（例如 '1-5'、'3'、'10-20'）。仅适用于 PDF 文件。每次请求最多 20 页"
    )

class ReadToolOutput(BaseModel):
    """ReadTool 输出结果"""

    content: str = Field(
        description="文件内容"
    )

    filePath: str = Field(
        description="文件路径"
    )

    size: int = Field(
        description="文件大小（字节）"
    )

    mtime: str = Field(
        description="文件修改时间（ISO 格式）"
    )

    lineCount: int = Field(
        description="文件总行数"
    )

    encoding: str = Field(
        description="文件编码"
    )

    isBinary: bool = Field(
        description="是否为二进制文件"
    )

    isPartial: bool = Field(
        default=False,
        description="是否为部分读取（使用了 offset/limit）"
    )
