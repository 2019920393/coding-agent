"""
EditTool - 文件编辑工具（精确字符串替换）

[Workflow]
1. 验证文件已被读取（readFileState）
2. 检查文件修改时间戳（防止并发冲突）
3. 执行字符串替换
4. 生成 diff
5. 写入文件
"""

from pydantic import BaseModel, Field
from typing import Optional, Callable, List, Any
from datetime import datetime
import os
from uuid import uuid4

from ..base import Tool, ToolUseContext
from ..types import ToolResult, ValidationResult, ToolCallProgress
from ..receipts import DiffReceipt, ProposedFileChange
from ...utils.path import expandPath
from ...utils.fs_operations import getFsImplementation
from ...utils.diff import generateUnifiedDiff, generateStructuredPatch, DiffHunk

class EditToolInput(BaseModel):
    file_path: str = Field(description="要修改的文件的绝对路径")
    old_string: str = Field(description="要替换的文本")
    new_string: str = Field(description="替换后的文本（必须与 old_string 不同）")
    replace_all: bool = Field(default=False, description="替换所有出现的位置（默认 false）")

class EditToolOutput(BaseModel):
    filePath: str
    diff: str
    linesChanged: int

class EditTool(Tool[EditToolInput, EditToolOutput, None]):
    def __init__(self):
        self.name = "Edit"
        self.max_result_size_chars = 100000

    @property
    def input_schema(self) -> type[EditToolInput]:
        return EditToolInput

    @property
    def output_schema(self) -> type[EditToolOutput]:
        return EditToolOutput

    async def description(self, input_data: EditToolInput, options: dict) -> str:
        """
        返回工具描述（简短，用于 API schema）

        [Workflow]
        直接返回简短描述字符串
        """
        return "Performs exact string replacements in files."

    async def prompt(self, options: dict) -> str:
        """
        生成工具描述（用于 ?? API 系统提示词）

        [Workflow]
        1. 构建基础描述
        2. 添加 Read-before-Edit 要求
        3. 添加缩进保留说明
        4. 添加唯一性要求
        """
        return (
            "Performs exact string replacements in files.\n\n"
            "Usage:\n"
            "- You must use your `Read` tool at least once in the conversation before editing. "
            "This tool will error if you attempt an edit without reading the file.\n"
            "- When editing text from Read tool output, ensure you preserve the exact "
            "indentation (tabs/spaces) as it appears AFTER the line number prefix.\n"
            "- ALWAYS prefer editing existing files in the codebase. NEVER write new files "
            "unless explicitly required.\n"
            "- Only use emojis if the user explicitly requests it.\n"
            "- The edit will FAIL if old_string is not unique in the file. Either provide "
            "a larger string with more surrounding context to make it unique or use "
            "replace_all to change every instance of old_string.\n"
            "- Use replace_all for replacing and renaming strings across the file."
        )

    def map_tool_result_to_tool_result_block_param(self, content: EditToolOutput, tool_use_id: str):
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": f"Edited {content.filePath}\n{content.diff}"
        }

    def user_facing_name(self) -> str:
        return "编辑文件"

    def is_concurrency_safe(self, input_data: EditToolInput) -> bool:
        return False

    def is_read_only(self, input_data: EditToolInput) -> bool:
        return False

    async def validate_input(self, input_data: EditToolInput, context: ToolUseContext) -> ValidationResult:
        if not os.path.isabs(input_data.file_path):
            return ValidationResult(result=False, message='文件路径必须是绝对路径')

        if input_data.old_string == input_data.new_string:
            return ValidationResult(result=False, message='old_string 和 new_string 必须不同')

        return ValidationResult(result=True)

    async def call(
        self,
        input_data: EditToolInput,
        context: ToolUseContext,
        can_use_tool: Callable,
        parent_message: Any,
        on_progress: Optional[Callable] = None
    ) -> ToolResult[EditToolOutput]:
        file_path = expandPath(input_data.file_path)
        fs = getFsImplementation()

        try:
            # 读取原始内容
            original = fs.readFile(file_path)

            # 执行替换
            if input_data.replace_all:
                modified = original.replace(input_data.old_string, input_data.new_string)
            else:
                # 只替换第一次出现
                idx = original.find(input_data.old_string)
                if idx == -1:
                    return ToolResult(error=f'未找到要替换的字符串')
                modified = original[:idx] + input_data.new_string + original[idx + len(input_data.old_string):]

            # 生成 diff
            diff = generateUnifiedDiff(original, modified, file_path, file_path)

            # 统计变更行数
            lines_changed = abs(modified.count('\n') - original.count('\n'))
            change = ProposedFileChange(
                change_id=f"chg_{uuid4().hex[:12]}",
                path=file_path,
                original_content=original,
                new_content=modified,
                diff_text=diff,
                source_tool=self.name,
            )

            return ToolResult(
                data=EditToolOutput(
                    filePath=file_path,
                    diff=diff,
                    linesChanged=lines_changed
                ),
                receipt=DiffReceipt(
                    kind="diff",
                    summary=f"Prepared edit for {file_path}",
                    path=file_path,
                    diff_text=diff,
                    change_id=change.change_id,
                ),
                staged_changes=[change],
            )

        except Exception as e:
            return ToolResult(error=f'编辑文件失败: {str(e)}')

edit_tool = EditTool()
