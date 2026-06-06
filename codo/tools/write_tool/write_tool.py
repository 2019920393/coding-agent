"""
WriteTool - 文件写入工具（创建或完全覆盖）

[Workflow]
1. 检查文件是否存在（区分 create/update）
2. 生成 diff（如果是更新）
3. 写入文件
"""

import os
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from codo.constants import EDIT_MAX_RESULT_CHARS

from ...utils.diff import generateUnifiedDiff
from ...utils.fs_operations import getFsImplementation
from ...utils.path import expandPath
from ..base import Tool
from ..receipts import DiffReceipt, ProposedFileChange
from ..types import ToolResult, ValidationResult


class WriteToolInput(BaseModel):
    """Write 工具输入模型，描述目标绝对路径和完整文件内容。"""
    file_path: str = Field(description="要写入的文件的绝对路径（必须是绝对路径，不是相对路径）")
    content: str = Field(description="要写入文件的内容")

class WriteToolOutput(BaseModel):
    """Write 工具输出模型，返回写入类型、文件路径、内容和可选 diff。"""
    type: str  # 'create' | 'update'
    filePath: str
    content: str
    diff: str | None = None

class WriteTool(Tool[WriteToolInput, WriteToolOutput, None]):
    """文件写入工具，用于创建新文件或完整覆盖已有文件。"""
    def __init__(self):
        """初始化 WriteTool，设置工具名称和最大结果大小。"""
        self.name = "Write"
        self.max_result_size_chars = EDIT_MAX_RESULT_CHARS

    @property
    def input_schema(self) -> type[WriteToolInput]:
        """返回输入 schema 类 WriteToolInput。"""
        return WriteToolInput

    @property
    def output_schema(self) -> type[WriteToolOutput]:
        """返回输出 schema 类 WriteToolOutput。"""
        return WriteToolOutput

    async def description(self, input_data: WriteToolInput, options: dict) -> str:
        """
        返回工具描述（简短，用于 API schema）

        [Workflow]
        直接返回简短描述字符串
        """
        return "Write a file to the local filesystem."

    async def prompt(self, options: dict) -> str:
        """
        生成工具描述（用于模型 API 系统提示词）

        [Workflow]
        1. 构建基础描述
        2. 添加覆盖警告
        3. 添加 Read-before-Write 要求
        4. 添加 Edit 工具优先说明
        5. 添加文档文件限制
        """
        return (
            "Writes a file to the local filesystem.\n\n"
            "Usage:\n"
            "- This tool will overwrite the existing file if there is one at the provided path.\n"
            "- If this is an existing file, you MUST use the Read tool first to read the "
            "file's contents. This tool will fail if you did not read the file first.\n"
            "- Prefer the Edit tool for modifying existing files \u2014 it only sends the diff. "
            "Only use this tool to create new files or for complete rewrites.\n"
            "- NEVER create documentation files (*.md) or README files unless explicitly "
            "requested by the User.\n"
            "- Only use emojis if the user explicitly requests it."
        )

    def map_tool_result_to_tool_result_block_param(self, content: WriteToolOutput, tool_use_id: str):
        """
        将工具结果转换为 API tool_result 消息块格式。

        返回:
            dict: 如 {"type": "tool_result", "tool_use_id": "...", "content": "create: /path/to/file.py"}
        """
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": f"{content.type}: {content.filePath}"
        }

    def user_facing_name(self) -> str:
        """返回用户可见的工具名称（中文）。"""
        return "写入文件"

    def is_concurrency_safe(self, input_data: WriteToolInput) -> bool:
        """文件写入不是并发安全的，返回 False。"""
        return False

    def is_read_only(self, input_data: WriteToolInput) -> bool:
        """文件写入不是只读操作，返回 False。"""
        return False

    async def validate_input(self, input_data: WriteToolInput, context: dict[str, Any]) -> ValidationResult:
        """验证文件路径必须是绝对路径。"""
        if not os.path.isabs(input_data.file_path):
            return ValidationResult(result=False, message='文件路径必须是绝对路径')
        return ValidationResult(result=True)

    async def call(
        self,
        input_data: WriteToolInput,
        context: dict[str, Any],
        can_use_tool: Callable,
        parent_message: Any,
        on_progress: Callable | None = None
    ) -> ToolResult[WriteToolOutput]:
        """
        写入文件（创建或完全覆盖）。

        [Workflow]
        1. 检查文件是否已存在（区分 create/update）
        2. 若已存在，读取原始内容生成 unified diff
        3. 构建 ProposedFileChange（待审阅变更）
        4. 返回 ToolResult（含 DiffReceipt 和 staged_changes）

        返回:
            ToolResult[WriteToolOutput]: 包含操作类型（create/update）、文件路径和 diff
        """
        file_path = expandPath(input_data.file_path)
        fs = getFsImplementation()

        try:
            # 检查文件是否存在
            is_new = not fs.exists(file_path)

            diff = None
            original = ""
            if not is_new:
                # 读取原始内容生成 diff
                original = fs.readFile(file_path)
                diff = generateUnifiedDiff(original, input_data.content, file_path, file_path)
            else:
                diff = generateUnifiedDiff("", input_data.content, file_path, file_path)

            change = ProposedFileChange(
                change_id=f"chg_{uuid4().hex[:12]}",
                path=file_path,
                original_content=original,
                new_content=input_data.content,
                diff_text=diff or "",
                source_tool=self.name,
            )

            return ToolResult(
                data=WriteToolOutput(
                    type='create' if is_new else 'update',
                    filePath=file_path,
                    content=input_data.content,
                    diff=diff
                ),
                receipt=DiffReceipt(
                    kind="diff",
                    summary=f"Prepared {'create' if is_new else 'update'} for {file_path}",
                    path=file_path,
                    diff_text=diff or "",
                    change_id=change.change_id,
                ),
                staged_changes=[change],
            )

        except Exception as e:
            return ToolResult(error=f'写入文件失败: {str(e)}')

write_tool = WriteTool()
