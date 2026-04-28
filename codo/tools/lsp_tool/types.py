"""
LSPTool 类型定义
"""

from typing import Literal, Optional
from pydantic import BaseModel, Field

class LSPToolInput(BaseModel):
    """LSPTool 输入"""

    operation: Literal[
        "goToDefinition",
        "findReferences",
        "hover",
        "documentSymbol",
        "workspaceSymbol",
        "goToImplementation",
        "prepareCallHierarchy",
        "incomingCalls",
        "outgoingCalls",
    ] = Field(..., description="LSP 操作类型")

    file_path: str = Field(..., description="文件路径（绝对路径）")

    line: int = Field(..., description="行号（1-based）", ge=1)

    character: int = Field(..., description="字符位置（1-based）", ge=1)

    query: Optional[str] = Field(
        default=None, description="查询字符串（仅用于 workspaceSymbol）"
    )

class LSPToolOutput(BaseModel):
    """LSPTool 输出"""

    operation: str = Field(..., description="执行的操作")

    file_path: str = Field(..., description="文件路径")

    result: str = Field(..., description="格式化的结果")

    result_count: Optional[int] = Field(default=None, description="结果数量")

    file_count: Optional[int] = Field(default=None, description="涉及的文件数量")

    symbol_name: Optional[str] = Field(default=None, description="符号名称")
