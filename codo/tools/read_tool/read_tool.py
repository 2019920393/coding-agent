"""
ReadTool 实现

从本地文件系统读取文件。

[Workflow]
1. 验证输入参数（文件路径、offset、limit）
2. 检查文件是否存在
3. 检查权限（如果配置了权限系统）
4. 检查去重（避免重复读取未修改的文件）
5. 读取文件内容（支持特殊文件：PDF、图片、Notebook）
6. 返回结果
"""

import os
from typing import Optional, Callable, Dict, Any
from datetime import datetime
from ..base import Tool, ToolUseContext
from ..types import ToolResult, ValidationResult, ToolCallProgress
from .types import ReadToolInput, ReadToolOutput
from .prompt import (
    READ_TOOL_NAME,
    DESCRIPTION,
    get_user_facing_name,
    get_tool_use_summary,
    get_activity_description
)
from ...utils.path import expandPath, isUncPath
from ...utils.file_read import (
    readFileSyncWithMetadata,
    readFileWithOffset,
    readPdfFile,
    readImageFile,
    readNotebookFile
)
from ...utils.fs_operations import getFsImplementation

# 文件读取状态跟踪（用于去重）
_read_file_state: Dict[str, datetime] = {}

class ReadTool(Tool[ReadToolInput, ReadToolOutput, None]):
    """文件读取工具"""

    def __init__(self):
        self.name = READ_TOOL_NAME
        self.max_result_size_chars = float('inf')  # Infinity - 防止读取结果被持久化（避免循环）

    @property
    def input_schema(self) -> type[ReadToolInput]:
        return ReadToolInput

    @property
    def output_schema(self) -> type[ReadToolOutput]:
        return ReadToolOutput

    async def description(self, input_data: ReadToolInput, options: dict) -> str:
        """
        返回工具描述（简短，用于 API schema）

        [Workflow]
        直接返回简短描述字符串
        """
        return "Read a file from the local filesystem."

    async def prompt(self, options: dict) -> str:
        """
        生成工具描述（用于 ?? API 系统提示词）

        [Workflow]
        1. 构建基础描述（读取文件能力说明）
        2. 添加路径要求（绝对路径）
        3. 添加行数限制说明
        4. 添加 offset/limit 说明
        5. 添加行号格式说明
        6. 添加特殊文件支持（图片、PDF、Notebook）
        7. 添加目录限制说明
        """
        return (
            "Reads a file from the local filesystem. You can access any file directly "
            "by using this tool.\n"
            "Assume this tool is able to read all files on the machine. If the User "
            "provides a path to a file assume that path is valid. It is okay to read "
            "a file that does not exist; an error will be returned.\n\n"
            "Usage:\n"
            "- The file_path parameter must be an absolute path, not a relative path\n"
            "- By default, it reads up to 2000 lines starting from the beginning of the file\n"
            "- You can optionally specify a line offset and limit (especially handy for "
            "long files), but it's recommended to read the whole file by not providing "
            "these parameters\n"
            "- Results are returned using cat -n format, with line numbers starting at 1\n"
            "- This tool allows the agent to read images (eg PNG, JPG, etc). When reading "
            "an image file the contents are presented visually because the agent is a "
            "multimodal LLM.\n"
            "- This tool can read PDF files (.pdf). For large PDFs (more than 10 pages), "
            "you MUST provide the pages parameter to read specific page ranges (e.g., "
            "pages: \"1-5\"). Reading a large PDF without the pages parameter will fail. "
            "Maximum 20 pages per request.\n"
            "- This tool can read Jupyter notebooks (.ipynb files) and returns all cells "
            "with their outputs, combining code, text, and visualizations.\n"
            "- This tool can only read files, not directories. To read a directory, use "
            "an ls command via the Bash tool.\n"
            "- You will regularly be asked to read screenshots. If the user provides a "
            "path to a screenshot, ALWAYS use this tool to view the file at the path. "
            "This tool will work with all temporary file paths.\n"
            "- If you read a file that exists but has empty contents you will receive a "
            "system reminder warning in place of file contents."
        )

    def map_tool_result_to_tool_result_block_param(self, content: ReadToolOutput, tool_use_id: str):
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content.content
        }

    def user_facing_name(self) -> str:
        """返回用户可见的工具名称"""
        return get_user_facing_name()

    def get_tool_use_summary(self, input_data: ReadToolInput) -> str:
        """返回工具使用摘要"""
        return get_tool_use_summary(input_data.model_dump())

    def get_activity_description(self, input_data: ReadToolInput) -> str:
        """返回活动描述"""
        return get_activity_description(input_data.model_dump())

    def is_concurrency_safe(self, input_data: ReadToolInput) -> bool:
        """文件读取是并发安全的"""
        return True

    def is_read_only(self, input_data: ReadToolInput) -> bool:
        """文件读取是只读操作"""
        return True

    async def validate_input(self, input_data: ReadToolInput, context: ToolUseContext) -> ValidationResult:
        """
        验证输入参数

        检查：
        1. 文件路径是否为绝对路径
        2. 文件是否存在
        3. 是否为目录
        4. UNC 路径安全检查
        5. 设备文件检查
        6. offset/limit 参数合理性
        """
        file_path = input_data.file_path

        # 检查是否为绝对路径
        if not os.path.isabs(file_path):
            return ValidationResult(
                result=False,
                message=f'文件路径必须是绝对路径: {file_path}',
            )

        # UNC 路径安全检查（Windows）
        if isUncPath(file_path):
            return ValidationResult(
                result=False,
                message='出于安全考虑，不支持 UNC 路径（可能触发 NTLM 认证）',
            )

        # 扩展路径
        abs_path = expandPath(file_path)

        fs = getFsImplementation()

        # 检查文件是否存在
        if not fs.exists(abs_path):
            return ValidationResult(
                result=False,
                message=f'文件不存在: {file_path}',
            )

        # 检查是否为目录
        if fs.isDir(abs_path):
            return ValidationResult(
                result=False,
                message=f'路径是目录，不是文件: {file_path}。使用 Bash 工具的 ls 命令读取目录',
            )

        # 检查是否为设备文件
        if fs.isDeviceFile(abs_path):
            return ValidationResult(
                result=False,
                message=f'不允许读取设备文件: {file_path}',
            )

        # 检查 offset/limit 参数
        if input_data.offset is not None and input_data.offset < 0:
            return ValidationResult(
                result=False,
                message='offset 不能为负数',
            )

        if input_data.limit is not None and input_data.limit <= 0:
            return ValidationResult(
                result=False,
                message='limit 必须大于 0',
            )

        # TODO: 权限检查（未来实现）

        return ValidationResult(
            result=True,
        )

    async def call(
        self,
        input_data: ReadToolInput,
        context: ToolUseContext,
        can_use_tool: Callable,
        parent_message: Any,
        on_progress: Optional[Callable[[ToolCallProgress], None]] = None
    ) -> ToolResult[ReadToolOutput]:
        """
        读取文件

        Args:
            input_data: 输入参数
            context: 工具使用上下文
            on_progress: 进度回调（文件读取不使用）

        Returns:
            工具执行结果
        """
        file_path = expandPath(input_data.file_path)
        fs = getFsImplementation()

        try:
            # 获取文件扩展名
            _, ext = os.path.splitext(file_path)
            ext = ext.lower()

            # 检查去重（如果文件未修改，可能跳过读取）
            current_mtime = fs.getModificationTime(file_path)
            last_read_mtime = _read_file_state.get(file_path)

            # 特殊文件处理
            if ext == '.pdf':
                # PDF 文件
                content = readPdfFile(file_path, input_data.pages)
                size = fs.getFileSize(file_path)
                line_count = content.count('\n') + 1
                encoding = 'pdf'
                is_binary = False

            elif ext in ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp']:
                # 图片文件
                base64_data = readImageFile(file_path)
                content = f'[图片文件: {os.path.basename(file_path)}]\nBase64: {base64_data[:100]}...'
                size = fs.getFileSize(file_path)
                line_count = 1
                encoding = 'binary'
                is_binary = True

            elif ext == '.ipynb':
                # Jupyter Notebook
                content = readNotebookFile(file_path)
                size = fs.getFileSize(file_path)
                line_count = content.count('\n') + 1
                encoding = 'utf-8'
                is_binary = False

            else:
                # 普通文本文件
                if input_data.offset is not None or input_data.limit is not None:
                    # 部分读取
                    content, total_lines = readFileWithOffset(
                        file_path,
                        offset=input_data.offset or 0,
                        limit=input_data.limit
                    )
                    line_count = total_lines
                    is_partial = True
                else:
                    # 完整读取
                    result = readFileSyncWithMetadata(file_path)
                    content = result.content
                    line_count = result.lineCount
                    is_partial = False

                    if result.isBinary:
                        return ToolResult(
                            error='无法读取二进制文件。如果这是图片，工具会自动处理；如果是其他二进制文件，请使用适当的工具'
                        )

                size = fs.getFileSize(file_path)
                encoding = 'utf-8'  # 简化处理
                is_binary = False

            # 更新读取状态
            _read_file_state[file_path] = current_mtime

            # 格式化内容（添加行号）
            if not is_binary and ext not in ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp']:
                lines = content.splitlines()
                numbered_lines = [f"{i+1}\t{line}" for i, line in enumerate(lines)]
                formatted_content = '\n'.join(numbered_lines)
            else:
                formatted_content = content

            return ToolResult(
                data=ReadToolOutput(
                    content=formatted_content,
                    filePath=file_path,
                    size=size,
                    mtime=current_mtime.isoformat(),
                    lineCount=line_count,
                    encoding=encoding,
                    isBinary=is_binary,
                    isPartial=input_data.offset is not None or input_data.limit is not None
                )
            )

        except FileNotFoundError:
            return ToolResult(
                error=f'文件不存在: {file_path}'
            )
        except PermissionError:
            return ToolResult(
                error=f'无权限读取文件: {file_path}'
            )
        except Exception as e:
            return ToolResult(
                error=f'读取文件失败: {str(e)}'
            )

# 创建工具实例
read_tool = ReadTool()
