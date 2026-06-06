"""
GrepTool - 内容搜索工具（基于 ripgrep）

[Workflow]
1. 检查 ripgrep 是否可用
2. 构建 ripgrep 命令
3. 执行搜索
4. 解析输出
5. 返回结果
"""

import asyncio
import fnmatch
import os
import re
import subprocess
import time
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

from codo.constants import (
    GREP_DEFAULT_HEAD_LIMIT,
    GREP_MAX_OUTPUT_CHARS,
    GREP_TIMEOUT_SECONDS,
)

from ...utils.path import expandPath
from ..base import Tool
from ..types import ToolResult, ValidationResult


class GrepToolInput(BaseModel):
    """Grep 工具输入模型，描述搜索正则、路径、过滤模式和输出方式。"""
    pattern: str = Field(description="要搜索的正则表达式模式")
    path: str | None = Field(default=None, description="要搜索的文件或目录（默认为当前工作目录）")
    glob: str | None = Field(default=None, description="Glob 模式过滤文件（例如 '*.js'、'*.{ts,tsx}'）")
    output_mode: Literal['content', 'files_with_matches', 'count'] | None = Field(
        default='files_with_matches',
        description="输出模式"
    )
    case_insensitive: bool | None = Field(default=False, description="不区分大小写搜索")
    head_limit: int | None = Field(default=GREP_DEFAULT_HEAD_LIMIT, description="限制输出行数")

class GrepToolOutput(BaseModel):
    """Grep 工具输出模型，返回匹配结果、数量、耗时和截断状态。"""
    matches: list[str]
    numMatches: int
    truncated: bool
    durationMs: int

class GrepTool(Tool[GrepToolInput, GrepToolOutput, None]):
    """内容搜索工具，基于 ripgrep 在文件中查找正则匹配。"""
    def __init__(self):
        """初始化 GrepTool，设置工具名称和最大结果大小（20K）。"""
        self.name = "Grep"
        self.max_result_size_chars = GREP_MAX_OUTPUT_CHARS

    @property
    def input_schema(self) -> type[GrepToolInput]:
        """返回输入 schema 类 GrepToolInput。"""
        return GrepToolInput

    @property
    def output_schema(self) -> type[GrepToolOutput]:
        """返回输出 schema 类 GrepToolOutput。"""
        return GrepToolOutput

    async def description(self, input_data: GrepToolInput, options: dict) -> str:
        """返回工具简短描述。"""
        return "使用正则表达式搜索文件内容"

    async def prompt(self, options: dict) -> str:
        """返回系统提示词中的工具描述。"""
        return "使用正则表达式搜索文件内容"

    def map_tool_result_to_tool_result_block_param(self, content: GrepToolOutput, tool_use_id: str):
        """
        将工具结果转换为 API tool_result 消息块格式。

        返回:
            dict: 如 {"type": "tool_result", "tool_use_id": "...", "content": "Found 3 matches"}
        """
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": f"Found {content.numMatches} matches"
        }

    def user_facing_name(self) -> str:
        """返回用户可见的工具名称（中文）。"""
        return "搜索内容"

    def is_concurrency_safe(self, input_data: GrepToolInput) -> bool:
        """内容搜索是并发安全的，返回 True。"""
        return True

    def is_read_only(self, input_data: GrepToolInput) -> bool:
        """内容搜索是只读操作，返回 True。"""
        return True

    async def validate_input(self, input_data: GrepToolInput, context: dict[str, Any]) -> ValidationResult:
        """验证搜索模式不能为空。"""
        if not input_data.pattern:
            return ValidationResult(result=False, message='搜索模式不能为空')
        return ValidationResult(result=True)

    def _check_ripgrep_available(self) -> bool:
        """检查 ripgrep (rg) 是否已安装并可用。"""
        try:
            subprocess.run(
                ['rg', '--version'],
                capture_output=True,
                check=True,
                timeout=GREP_TIMEOUT_SECONDS,
            )
            return True
        except (
            subprocess.CalledProcessError,
            FileNotFoundError,
            PermissionError,
            subprocess.TimeoutExpired,
        ):
            return False

    def _run_python_search(
        self,
        input_data: GrepToolInput,
        search_path: str,
    ) -> list[str]:
        flags = re.IGNORECASE if input_data.case_insensitive else 0
        pattern = re.compile(input_data.pattern, flags)
        matched_lines: list[str] = []
        matched_files: set[str] = set()
        counts: dict[str, int] = {}

        files: list[str] = []
        if os.path.isfile(search_path):
            files = [search_path]
        else:
            for root, dirs, filenames in os.walk(search_path, followlinks=False):
                dirs[:] = [name for name in dirs if name not in {".git", "__pycache__", "node_modules"}]
                for filename in filenames:
                    if input_data.glob and not fnmatch.fnmatch(filename, input_data.glob):
                        continue
                    files.append(os.path.join(root, filename))

        for file_path in files:
            try:
                with open(file_path, encoding="utf-8", errors="ignore") as handle:
                    for line_number, line in enumerate(handle, start=1):
                        if pattern.search(line):
                            matched_files.add(file_path)
                            counts[file_path] = counts.get(file_path, 0) + 1
                            if input_data.output_mode == "content":
                                matched_lines.append(f"{file_path}:{line_number}:{line.rstrip()}")
            except OSError:
                continue

        if input_data.output_mode == "content":
            return matched_lines
        if input_data.output_mode == "count":
            return [f"{path}:{count}" for path, count in sorted(counts.items())]
        return sorted(matched_files)

    def _run_ripgrep(self, cmd: list[str]) -> list[str]:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=GREP_TIMEOUT_SECONDS,
        )
        return result.stdout.strip().split('\n') if result.stdout.strip() else []

    async def call(
        self,
        input_data: GrepToolInput,
        context: dict[str, Any],
        can_use_tool: Callable,
        parent_message: Any,
        on_progress: Callable | None = None
    ) -> ToolResult[GrepToolOutput]:
        """
        使用 ripgrep 执行正则表达式内容搜索。

        [Workflow]
        1. 检查 ripgrep 是否可用
        2. 构建 rg 命令（含 pattern、path、glob、output_mode、case_insensitive 等选项）
        3. 执行搜索（超时 30 秒）
        4. 解析输出行，按 head_limit 截断
        5. 返回匹配结果

        返回:
            ToolResult[GrepToolOutput]: 包含匹配列表、数量、耗时和是否截断
        """
        start_time = time.time()

        try:
            workspace_cwd = str(context.get("cwd") or os.getcwd())
            search_path = (
                expandPath(input_data.path, cwd=workspace_cwd)
                if input_data.path
                else workspace_cwd
            )

            if await asyncio.to_thread(self._check_ripgrep_available):
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

                output_lines = await asyncio.to_thread(self._run_ripgrep, cmd)
            else:
                output_lines = await asyncio.to_thread(
                    self._run_python_search,
                    input_data,
                    search_path,
                )

            # 应用 head_limit
            head_limit = input_data.head_limit or GREP_DEFAULT_HEAD_LIMIT
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
            return ToolResult(error=f'搜索超时（{GREP_TIMEOUT_SECONDS}秒）')
        except (PermissionError, OSError):
            try:
                output_lines = await asyncio.to_thread(
                    self._run_python_search,
                    input_data,
                    search_path,
                )
                head_limit = input_data.head_limit or GREP_DEFAULT_HEAD_LIMIT
                matches = output_lines[:head_limit]
                return ToolResult(
                    data=GrepToolOutput(
                        matches=matches,
                        numMatches=len(matches),
                        truncated=len(output_lines) > head_limit,
                        durationMs=int((time.time() - start_time) * 1000),
                    )
                )
            except re.error as exc:
                return ToolResult(error=f'搜索模式无效: {str(exc)}')
        except Exception as e:
            return ToolResult(error=f'搜索失败: {str(e)}')

grep_tool = GrepTool()
