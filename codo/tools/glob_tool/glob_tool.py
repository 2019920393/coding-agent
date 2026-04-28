"""
GlobTool - 文件名模式匹配工具

[Workflow]
1. 解析 glob 模式
2. 搜索匹配的文件
3. 按修改时间排序
4. 返回结果（限制 100 个文件）
"""

from pydantic import BaseModel, Field
from typing import Optional, Callable, List, Any
import os
from pathlib import Path

from ..base import Tool, ToolUseContext
from ..types import ToolResult, ValidationResult, ToolCallProgress
from ...utils.path import expandPath, toRelativePath

class GlobToolInput(BaseModel):
    pattern: str = Field(description="要匹配文件的 glob 模式")
    path: Optional[str] = Field(default=None, description="要搜索的目录。如果未指定，将使用当前工作目录")

class GlobToolOutput(BaseModel):
    durationMs: int
    numFiles: int
    filenames: List[str]
    truncated: bool

class GlobTool(Tool[GlobToolInput, GlobToolOutput, None]):
    def __init__(self):
        self.name = "Glob"
        self.max_result_size_chars = 100000

    @property
    def input_schema(self) -> type[GlobToolInput]:
        return GlobToolInput

    @property
    def output_schema(self) -> type[GlobToolOutput]:
        return GlobToolOutput

    async def description(self, input_data: GlobToolInput, options: dict) -> str:
        return "使用 glob 模式查找文件"

    async def prompt(self, options: dict) -> str:
        return "使用 glob 模式查找文件"

    def map_tool_result_to_tool_result_block_param(self, content: GlobToolOutput, tool_use_id: str):
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": f"Found {content.numFiles} files"
        }

    def user_facing_name(self) -> str:
        return "查找文件"

    def is_concurrency_safe(self, input_data: GlobToolInput) -> bool:
        return True

    def is_read_only(self, input_data: GlobToolInput) -> bool:
        return True

    async def validate_input(self, input_data: GlobToolInput, context: ToolUseContext) -> ValidationResult:
        if not input_data.pattern:
            return ValidationResult(result=False, message='模式不能为空')
        return ValidationResult(result=True)

    async def call(
        self,
        input_data: GlobToolInput,
        context: ToolUseContext,
        can_use_tool: Callable,
        parent_message: Any,
        on_progress: Optional[Callable] = None
    ) -> ToolResult[GlobToolOutput]:
        import time
        start_time = time.time()

        try:
            # 确定搜索路径
            search_path = expandPath(input_data.path) if input_data.path else os.getcwd()

            # 使用 pathlib 进行 glob 搜索
            base = Path(search_path)
            matches = list(base.glob(input_data.pattern))

            # 按修改时间排序（最新的在前）
            matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)

            # 限制结果数量
            max_files = 100
            truncated = len(matches) > max_files
            matches = matches[:max_files]

            # 转换为相对路径
            filenames = [toRelativePath(str(p), search_path) for p in matches]

            duration_ms = int((time.time() - start_time) * 1000)

            return ToolResult(
                data=GlobToolOutput(
                    durationMs=duration_ms,
                    numFiles=len(filenames),
                    filenames=filenames,
                    truncated=truncated
                )
            )

        except Exception as e:
            return ToolResult(error=f'Glob 搜索失败: {str(e)}')

glob_tool = GlobTool()
