"""
GlobTool - 文件名模式匹配工具

[Workflow]
1. 解析 glob 模式
2. 搜索匹配的文件
3. 按修改时间排序
4. 返回结果（限制 100 个文件）
"""

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from codo.constants import GLOB_MAX_FILES, GLOB_MAX_RESULT_CHARS

from ...utils.path import expandPath, toRelativePath
from ..base import Tool
from ..types import ToolResult, ValidationResult


class GlobToolInput(BaseModel):
    """Glob 工具输入模型，描述匹配模式和可选搜索目录。"""
    pattern: str = Field(description="要匹配文件的 glob 模式")
    path: str | None = Field(default=None, description="要搜索的目录。如果未指定，将使用当前工作目录")

class GlobToolOutput(BaseModel):
    """Glob 工具输出模型，返回匹配文件、数量、耗时和截断状态。"""
    durationMs: int
    numFiles: int
    filenames: list[str]
    truncated: bool

class GlobTool(Tool[GlobToolInput, GlobToolOutput, None]):
    """文件名模式匹配工具，用于按 glob 规则查找工作区文件。"""
    def __init__(self):
        """初始化 GlobTool，设置工具名称和最大结果大小。"""
        self.name = "Glob"
        self.max_result_size_chars = GLOB_MAX_RESULT_CHARS

    @property
    def input_schema(self) -> type[GlobToolInput]:
        """返回输入 schema 类 GlobToolInput。"""
        return GlobToolInput

    @property
    def output_schema(self) -> type[GlobToolOutput]:
        """返回输出 schema 类 GlobToolOutput。"""
        return GlobToolOutput

    async def description(self, input_data: GlobToolInput, options: dict) -> str:
        """返回工具简短描述。"""
        return "使用 glob 模式查找文件"

    async def prompt(self, options: dict) -> str:
        """返回系统提示词中的工具描述。"""
        return "使用 glob 模式查找文件"

    def map_tool_result_to_tool_result_block_param(self, content: GlobToolOutput, tool_use_id: str):
        """
        将工具结果转换为 API tool_result 消息块格式。

        返回:
            dict: 如 {"type": "tool_result", "tool_use_id": "...", "content": "Found 5 files"}
        """
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": f"Found {content.numFiles} files"
        }

    def user_facing_name(self) -> str:
        """返回用户可见的工具名称（中文）。"""
        return "查找文件"

    def is_concurrency_safe(self, input_data: GlobToolInput) -> bool:
        """文件搜索是并发安全的，返回 True。"""
        return True

    def is_read_only(self, input_data: GlobToolInput) -> bool:
        """文件搜索是只读操作，返回 True。"""
        return True

    async def validate_input(self, input_data: GlobToolInput, context: dict[str, Any]) -> ValidationResult:
        """验证 glob 模式不能为空。"""
        if not input_data.pattern:
            return ValidationResult(result=False, message='模式不能为空')
        return ValidationResult(result=True)

    async def call(
        self,
        input_data: GlobToolInput,
        context: dict[str, Any],
        can_use_tool: Callable,
        parent_message: Any,
        on_progress: Callable | None = None
    ) -> ToolResult[GlobToolOutput]:
        """
        执行 glob 文件搜索。

        [Workflow]
        1. 确定搜索路径（input_data.path 或当前工作目录）
        2. 使用 pathlib.Path.glob() 搜索匹配文件
        3. 按修改时间降序排列
        4. 截断到最多 100 个文件
        5. 转换为相对路径并返回

        返回:
            ToolResult[GlobToolOutput]: 包含文件列表、数量、耗时和是否截断
        """
        import time
        start_time = time.time()

        try:
            # 确定搜索路径。
            # 工作流：
            workspace_cwd = str(context.get("cwd") or os.getcwd())
            search_path = (
                expandPath(input_data.path, cwd=workspace_cwd)
                if input_data.path
                else workspace_cwd
            )

            # 使用 pathlib 进行 glob 搜索
            base = Path(search_path)
            matches = [path for path in base.glob(input_data.pattern) if not path.is_symlink()]

            # 按修改时间排序（最新的在前）
            matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)

            # 限制结果数量
            max_files = GLOB_MAX_FILES
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
