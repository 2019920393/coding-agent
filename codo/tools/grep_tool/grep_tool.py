"""
GrepTool - 内容搜索工具（基于 ripgrep）

[Workflow]
1. 检查 ripgrep 是否可用
2. 构建 ripgrep 命令
3. 执行搜索
4. 解析输出
5. 返回结果
"""

from pydantic import BaseModel, Field
from typing import Optional, Callable, List, Literal, Any
import subprocess
import os

from ..base import Tool, ToolUseContext
from ..types import ToolResult, ValidationResult, ToolCallProgress
from ...utils.path import expandPath

class GrepToolInput(BaseModel):
    pattern: str = Field(description="要搜索的正则表达式模式")
    path: Optional[str] = Field(default=None, description="要搜索的文件或目录（默认为当前工作目录）")
    glob: Optional[str] = Field(default=None, description="Glob 模式过滤文件（例如 '*.js'、'*.{ts,tsx}'）")
    output_mode: Optional[Literal['content', 'files_with_matches', 'count']] = Field(
        default='files_with_matches',
        description="输出模式"
    )
    case_insensitive: Optional[bool] = Field(default=False, description="不区分大小写搜索")
    head_limit: Optional[int] = Field(default=250, description="限制输出行数")

class GrepToolOutput(BaseModel):
    matches: List[str]
    numMatches: int
    truncated: bool
    durationMs: int

class GrepTool(Tool[GrepToolInput, GrepToolOutput, None]):
    def __init__(self):
        self.name = "Grep"
        self.max_result_size_chars = 20000  # 20K chars - 搜索结果通常较小

    @property
    def input_schema(self) -> type[GrepToolInput]:
        return GrepToolInput

    @property
    def output_schema(self) -> type[GrepToolOutput]:
        return GrepToolOutput

    async def description(self, input_data: GrepToolInput, options: dict) -> str:
        return "使用正则表达式搜索文件内容"

    async def prompt(self, options: dict) -> str:
        return "使用正则表达式搜索文件内容"

    def map_tool_result_to_tool_result_block_param(self, content: GrepToolOutput, tool_use_id: str):
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": f"Found {content.numMatches} matches"
        }

    def user_facing_name(self) -> str:
        return "搜索内容"

    def is_concurrency_safe(self, input_data: GrepToolInput) -> bool:
        return True

    def is_read_only(self, input_data: GrepToolInput) -> bool:
        return True

    async def validate_input(self, input_data: GrepToolInput, context: ToolUseContext) -> ValidationResult:
        if not input_data.pattern:
            return ValidationResult(result=False, message='搜索模式不能为空')
        return ValidationResult(result=True)

    def _check_ripgrep_available(self) -> bool:
        """检查 ripgrep 是否可用"""
        try:
            subprocess.run(['rg', '--version'], capture_output=True, check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    async def call(
        self,
        input_data: GrepToolInput,
        context: ToolUseContext,
        can_use_tool: Callable,
        parent_message: Any,
        on_progress: Optional[Callable] = None
    ) -> ToolResult[GrepToolOutput]:
        import time
        start_time = time.time()

        try:
            # 检查 ripgrep 是否可用
            if not self._check_ripgrep_available():
                return ToolResult(error='ripgrep (rg) 未安装。请安装: https://github.com/BurntSushi/ripgrep')

            # 构建命令
            search_path = expandPath(input_data.path) if input_data.path else os.getcwd()

            cmd = ['rg', input_data.pattern, search_path]

            # 添加选项
            if input_data.case_insensitive:
                cmd.append('-i')

            if input_data.glob:
                cmd.extend(['--glob', input_data.glob])

            if input_data.output_mode == 'files_with_matches':
                cmd.append('-l')
            elif input_data.output_mode == 'count':
                cmd.append('-c')

            # 执行搜索
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )

            # 解析输出
            output_lines = result.stdout.strip().split('\n') if result.stdout.strip() else []

            # 应用 head_limit
            head_limit = input_data.head_limit or 250
            truncated = len(output_lines) > head_limit
            matches = output_lines[:head_limit]

            duration_ms = int((time.time() - start_time) * 1000)

            return ToolResult(
                data=GrepToolOutput(
                    matches=matches,
                    numMatches=len(matches),
                    truncated=truncated,
                    durationMs=duration_ms
                )
            )

        except subprocess.TimeoutExpired:
            return ToolResult(error='搜索超时（30秒）')
        except Exception as e:
            return ToolResult(error=f'搜索失败: {str(e)}')

grep_tool = GrepTool()
