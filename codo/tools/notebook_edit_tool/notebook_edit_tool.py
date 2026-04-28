"""
NotebookEditTool - Jupyter Notebook 单元格编辑工具

[Workflow]
1. 验证输入参数（路径、扩展名、edit_mode、cell_id 等）
2. 读取 notebook JSON 文件
3. 根据 cell_id 定位目标 cell（支持 ID 和 "cell-N" 格式）
4. 根据 edit_mode 执行操作（replace / insert / delete）
5. 写回文件（indent=1）
6. 返回操作结果
"""

import os
import json
import re
import random
import string
from typing import Optional, Callable, Any, Literal

from pydantic import BaseModel, Field

from ..base import Tool, ToolUseContext
from ..types import ToolResult, ValidationResult, ToolCallProgress
from ...utils.path import expandPath
from .prompt import NOTEBOOK_EDIT_TOOL_NAME, DESCRIPTION, PROMPT

# ============================================================================
# 辅助函数
# ============================================================================

def parse_cell_id(cell_id: str) -> Optional[int]:
    """
    解析 "cell-N" 格式的 cell ID，返回 N（0-indexed）

    [Workflow]
    1. 使用正则匹配 "cell-N" 格式
    2. 如果匹配成功，返回解析后的整数索引
    3. 如果不匹配或解析失败，返回 None

    Args:
        cell_id: cell ID 字符串，可能是 "cell-0"、"cell-1" 等格式

    Returns:
        解析后的整数索引，或 None（如果不是 "cell-N" 格式）
    """
    # 使用正则匹配 "cell-数字" 格式
    match = re.match(r'^cell-(\d+)$', cell_id)
    if match and match.group(1):
        # 提取数字部分并转换为整数
        index = int(match.group(1))
        return index
    # 不匹配 "cell-N" 格式，返回 None
    return None

def _generate_cell_id() -> str:
    """
    生成随机 cell ID

    [Workflow]
    1. 从字母和数字中随机选取 13 个字符
    2. 拼接为字符串返回

    Returns:
        随机生成的 cell ID 字符串
    """
    # 使用字母和数字生成 13 位随机 ID（对齐 JS 的 toString(36).substring(2,15)）
    chars = string.ascii_lowercase + string.digits
    return ''.join(random.choice(chars) for _ in range(13))

# ============================================================================
# 输入 Schema
# ============================================================================

class NotebookEditToolInput(BaseModel):
    """
    NotebookEditTool 输入参数

    [Workflow]
    定义 notebook 编辑操作所需的全部参数
    """
    # notebook 文件的绝对路径（必须是绝对路径，不是相对路径）
    notebook_path: str = Field(
        description="The absolute path to the Jupyter notebook file to edit (must be absolute, not relative)"
    )
    # cell ID（可选）：用于定位要编辑的 cell，支持实际 ID 或 "cell-N" 格式
    # insert 模式下，新 cell 会插入到此 ID 对应 cell 之后；未指定时插入到开头
    cell_id: Optional[str] = Field(
        default=None,
        description=(
            "The ID of the cell to edit. When inserting a new cell, the new cell "
            "will be inserted after the cell with this ID, or at the beginning if not specified."
        )
    )
    # 新的 cell 内容
    new_source: str = Field(
        description="The new source for the cell"
    )
    # cell 类型（可选）：code 或 markdown。insert 模式下必须指定
    cell_type: Optional[Literal["code", "markdown"]] = Field(
        default=None,
        description=(
            "The type of the cell (code or markdown). If not specified, it defaults "
            "to the current cell type. If using edit_mode=insert, this is required."
        )
    )
    # 编辑模式（可选）：replace / insert / delete，默认 replace
    edit_mode: Optional[Literal["replace", "insert", "delete"]] = Field(
        default=None,
        description="The type of edit to make (replace, insert, delete). Defaults to replace."
    )

# ============================================================================
# 输出 Schema
# ============================================================================

class NotebookEditToolOutput(BaseModel):
    """
    NotebookEditTool 输出结果

    [Workflow]
    包含操作结果的全部字段，用于返回给调用方
    """
    # 写入 cell 的新源代码
    new_source: str = Field(description="The new source code that was written to the cell")
    # 被编辑的 cell ID
    cell_id: Optional[str] = Field(default=None, description="The ID of the cell that was edited")
    # cell 类型
    cell_type: str = Field(description="The type of the cell")
    # notebook 的编程语言
    language: str = Field(description="The programming language of the notebook")
    # 使用的编辑模式
    edit_mode: str = Field(description="The edit mode that was used")
    # 错误信息（操作失败时）
    error: Optional[str] = Field(default=None, description="Error message if the operation failed")
    # notebook 文件路径（用于归因追踪）
    notebook_path: str = Field(description="The path to the notebook file")
    # 修改前的原始 notebook 内容
    original_file: str = Field(description="The original notebook content before modification")
    # 修改后的 notebook 内容
    updated_file: str = Field(description="The updated notebook content after modification")

# ============================================================================
# NotebookEditTool 工具类
# ============================================================================

class NotebookEditTool(Tool[NotebookEditToolInput, NotebookEditToolOutput, None]):
    """
    Jupyter Notebook 单元格编辑工具

    [Workflow]
    1. validate_input: 验证路径、扩展名、edit_mode、cell_id 等
    2. call: 读取 notebook → 定位 cell → 执行编辑 → 写回文件
    """

    def __init__(self):
        """初始化工具，设置名称和结果大小限制"""
        # 工具名称
        self.name = NOTEBOOK_EDIT_TOOL_NAME
        # 结果最大字符数
        self.max_result_size_chars = 100000

    @property
    def input_schema(self) -> type[NotebookEditToolInput]:
        """返回输入 schema 类"""
        return NotebookEditToolInput

    @property
    def output_schema(self) -> type[NotebookEditToolOutput]:
        """返回输出 schema 类"""
        return NotebookEditToolOutput

    async def description(self, input_data: NotebookEditToolInput, options: dict) -> str:
        """
        返回工具描述（简短，用于 API schema）

        [Workflow]
        直接返回 DESCRIPTION 常量
        """
        return DESCRIPTION

    async def prompt(self, options: dict) -> str:
        """
        返回工具提示（详细，用于系统提示词）

        [Workflow]
        直接返回 PROMPT 常量
        """
        return PROMPT

    def map_tool_result_to_tool_result_block_param(
        self,
        content: NotebookEditToolOutput,
        tool_use_id: str
    ):
        """
        将工具结果转换为 API 格式

        [Workflow]
        1. 如果有错误，返回错误结果（is_error=True）
        2. 根据 edit_mode 返回不同的成功消息
        """
        # 如果有错误，返回错误结果
        if content.error:
            return {
                "tool_use_id": tool_use_id,
                "type": "tool_result",
                "content": content.error,
                "is_error": True,
            }
        # 根据 edit_mode 返回不同的成功消息
        if content.edit_mode == "replace":
            return {
                "tool_use_id": tool_use_id,
                "type": "tool_result",
                "content": f"Updated cell {content.cell_id} with {content.new_source}",
            }
        elif content.edit_mode == "insert":
            return {
                "tool_use_id": tool_use_id,
                "type": "tool_result",
                "content": f"Inserted cell {content.cell_id} with {content.new_source}",
            }
        elif content.edit_mode == "delete":
            return {
                "tool_use_id": tool_use_id,
                "type": "tool_result",
                "content": f"Deleted cell {content.cell_id}",
            }
        else:
            # 未知的 edit_mode（理论上不会到达这里）
            return {
                "tool_use_id": tool_use_id,
                "type": "tool_result",
                "content": "Unknown edit mode",
            }

    def user_facing_name(self) -> str:
        """
        返回用户可见的工具名称

        """
        return "编辑 Notebook"

    def is_concurrency_safe(self, input_data: NotebookEditToolInput) -> bool:
        """
        是否并发安全

        Notebook 编辑涉及文件写入，不是并发安全的
        """
        return False

    def is_read_only(self, input_data: NotebookEditToolInput) -> bool:
        """
        是否只读操作

        Notebook 编辑是写操作
        """
        return False

    def get_tool_use_summary(self, input_data: Optional[NotebookEditToolInput] = None) -> Optional[str]:
        """
        获取工具使用摘要

        [Workflow]
        返回 notebook 路径的文件名部分
        """
        if input_data is None:
            return None
        # 提取文件名作为摘要
        return os.path.basename(input_data.notebook_path)

    def get_activity_description(self, input_data: Optional[NotebookEditToolInput] = None) -> Optional[str]:
        """
        获取活动描述（用于进度显示）

        [Workflow]
        返回 "Editing notebook <文件名>" 格式的描述
        """
        summary = self.get_tool_use_summary(input_data)
        if summary:
            return f"Editing notebook {summary}"
        return "Editing notebook"

    def get_path(self, input_data: NotebookEditToolInput) -> Optional[str]:
        """
        获取操作的文件路径

        """
        return input_data.notebook_path

    async def validate_input(
        self,
        input_data: NotebookEditToolInput,
        context: ToolUseContext
    ) -> ValidationResult:
        """
        验证输入参数

        validateInput() 逻辑

        [Workflow]
        1. 解析完整路径（支持相对路径转绝对路径）
        2. 检查 .ipynb 扩展名
        3. 检查 edit_mode 有效性
        4. insert 模式必须指定 cell_type
        5. 检查文件存在
        6. 解析 JSON 验证 notebook 格式
        7. 检查 cell_id 存在（支持 "cell-N" 格式）
        """
        # 获取编辑模式，默认为 replace
        edit_mode = input_data.edit_mode or "replace"

        # 解析完整路径
        if os.path.isabs(input_data.notebook_path):
            full_path = input_data.notebook_path
        else:
            full_path = os.path.abspath(input_data.notebook_path)

        # 安全检查：跳过 UNC 路径（防止 NTLM 凭据泄露）
        if full_path.startswith('\\\\') or full_path.startswith('//'):
            return ValidationResult(result=True)

        # 检查 .ipynb 扩展名
        _, ext = os.path.splitext(full_path)
        if ext != '.ipynb':
            return ValidationResult(
                result=False,
                message=(
                    'File must be a Jupyter notebook (.ipynb file). '
                    'For editing other file types, use the FileEdit tool.'
                ),
            )

        # 检查 edit_mode 有效性
        if edit_mode not in ('replace', 'insert', 'delete'):
            return ValidationResult(
                result=False,
                message='Edit mode must be replace, insert, or delete.',
            )

        # insert 模式必须指定 cell_type
        if edit_mode == 'insert' and not input_data.cell_type:
            return ValidationResult(
                result=False,
                message='Cell type is required when using edit_mode=insert.',
            )

        # 检查文件是否存在
        if not os.path.exists(full_path):
            return ValidationResult(
                result=False,
                message='Notebook file does not exist.',
            )

        # 读取文件内容并验证 JSON 格式
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception:
            return ValidationResult(
                result=False,
                message='Notebook file does not exist.',
            )

        # 解析 JSON
        try:
            notebook = json.loads(content)
        except (json.JSONDecodeError, ValueError):
            return ValidationResult(
                result=False,
                message='Notebook is not valid JSON.',
            )

        # 检查 cell_id
        cell_id = input_data.cell_id
        if not cell_id:
            # 没有指定 cell_id 时，非 insert 模式必须指定
            if edit_mode != 'insert':
                return ValidationResult(
                    result=False,
                    message='Cell ID must be specified when not inserting a new cell.',
                )
        else:
            # 获取 cells 列表
            cells = notebook.get('cells', [])

            # 首先尝试通过实际 ID 查找 cell
            cell_index = -1
            for i, cell in enumerate(cells):
                if cell.get('id') == cell_id:
                    cell_index = i
                    break

            if cell_index == -1:
                # 如果未找到，尝试解析 "cell-N" 格式的数字索引
                parsed_cell_index = parse_cell_id(cell_id)
                if parsed_cell_index is not None:
                    # 检查索引是否在有效范围内
                    if parsed_cell_index < 0 or parsed_cell_index >= len(cells):
                        return ValidationResult(
                            result=False,
                            message=f'Cell with index {parsed_cell_index} does not exist in notebook.',
                        )
                else:
                    # 既不是有效 ID 也不是 "cell-N" 格式
                    return ValidationResult(
                        result=False,
                        message=f'Cell with ID "{cell_id}" not found in notebook.',
                    )

        # 所有验证通过
        return ValidationResult(result=True)

    async def call(
        self,
        input_data: NotebookEditToolInput,
        context: ToolUseContext,
        can_use_tool: Callable,
        parent_message: Any,
        on_progress: Optional[Callable[[ToolCallProgress], None]] = None
    ) -> ToolResult[NotebookEditToolOutput]:
        """
        执行 Notebook 编辑操作

        call() 逻辑

        [Workflow]
        1. 解析完整路径
        2. 读取 notebook JSON
        3. 根据 cell_id 定位 cell 索引（支持 ID 和 "cell-N" 格式）
        4. 根据 edit_mode 执行操作：
           - replace: 修改 cell source，重置 execution_count 和 outputs
           - insert: 在指定位置插入新 cell
           - delete: 删除指定 cell
        5. 写回文件（indent=1）
        6. 返回操作结果
        """
        # 解析完整路径
        if os.path.isabs(input_data.notebook_path):
            full_path = input_data.notebook_path
        else:
            full_path = os.path.abspath(input_data.notebook_path)

        # 提取输入参数
        new_source = input_data.new_source
        cell_id = input_data.cell_id
        cell_type = input_data.cell_type
        original_edit_mode = input_data.edit_mode

        try:
            # 读取 notebook 文件内容
            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # 解析 JSON（不使用缓存，因为后续会修改 notebook 对象）
            try:
                notebook = json.loads(content)
            except (json.JSONDecodeError, ValueError):
                # JSON 解析失败，返回错误结果
                return ToolResult(
                    data=NotebookEditToolOutput(
                        new_source=new_source,
                        cell_type=cell_type or 'code',
                        language='python',
                        edit_mode='replace',
                        error='Notebook is not valid JSON.',
                        cell_id=cell_id,
                        notebook_path=full_path,
                        original_file='',
                        updated_file='',
                    )
                )

            # 获取 cells 列表
            cells = notebook.get('cells', [])

            # 根据 cell_id 定位 cell 索引
            cell_index = None
            if not cell_id:
                # 没有指定 cell_id，默认插入到开头（索引 0）
                cell_index = 0
            else:
                # 首先尝试通过实际 ID 查找 cell
                cell_index = -1
                for i, cell in enumerate(cells):
                    if cell.get('id') == cell_id:
                        cell_index = i
                        break

                # 如果未找到，尝试解析 "cell-N" 格式
                if cell_index == -1:
                    parsed_index = parse_cell_id(cell_id)
                    if parsed_index is not None:
                        cell_index = parsed_index
                    else:
                        cell_index = -1

                # insert 模式下，插入到指定 cell 之后（索引 +1）
                if original_edit_mode == 'insert':
                    cell_index += 1

            # 确定实际的编辑模式（可能从 replace 转换为 insert）
            edit_mode = original_edit_mode or 'replace'

            # 如果 replace 模式的索引等于 cells 长度（超出末尾），转换为 insert
            if edit_mode == 'replace' and cell_index == len(cells):
                edit_mode = 'insert'
                # 如果没有指定 cell_type，默认为 code
                if not cell_type:
                    cell_type = 'code'

            # 获取 notebook 的编程语言
            metadata = notebook.get('metadata', {})
            language_info = metadata.get('language_info', {})
            language = language_info.get('name', 'python')

            # 确定是否需要生成 cell ID（nbformat >= 4.5）
            nbformat = notebook.get('nbformat', 0)
            nbformat_minor = notebook.get('nbformat_minor', 0)
            new_cell_id = None
            if nbformat > 4 or (nbformat == 4 and nbformat_minor >= 5):
                if edit_mode == 'insert':
                    # insert 模式：生成新的随机 cell ID
                    new_cell_id = _generate_cell_id()
                elif cell_id is not None:
                    # replace/delete 模式：保留原始 cell ID
                    new_cell_id = cell_id

            # 根据 edit_mode 执行操作
            if edit_mode == 'delete':
                # 删除模式：移除指定索引的 cell
                cells.pop(cell_index)

            elif edit_mode == 'insert':
                # 插入模式：在指定位置插入新 cell
                if cell_type == 'markdown':
                    # 创建 markdown 类型的新 cell
                    new_cell = {
                        'cell_type': 'markdown',
                        'id': new_cell_id,
                        'source': new_source,
                        'metadata': {},
                    }
                else:
                    # 创建 code 类型的新 cell（包含 execution_count 和 outputs）
                    new_cell = {
                        'cell_type': 'code',
                        'id': new_cell_id,
                        'source': new_source,
                        'metadata': {},
                        'execution_count': None,
                        'outputs': [],
                    }
                # 在指定位置插入新 cell
                cells.insert(cell_index, new_cell)

            else:
                # 替换模式：修改目标 cell 的 source
                target_cell = cells[cell_index]
                # 更新 cell 的源代码
                target_cell['source'] = new_source
                # 如果是 code 类型，重置 execution_count 和 outputs
                if target_cell.get('cell_type') == 'code':
                    target_cell['execution_count'] = None
                    target_cell['outputs'] = []
                # 如果指定了 cell_type 且与当前不同，更新类型
                if cell_type and cell_type != target_cell.get('cell_type'):
                    target_cell['cell_type'] = cell_type

            # 更新 notebook 的 cells 列表
            notebook['cells'] = cells

            updated_content = json.dumps(notebook, indent=1, ensure_ascii=False)

            # 写回文件
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(updated_content)

            # 构建返回数据
            data = NotebookEditToolOutput(
                new_source=new_source,
                cell_type=cell_type or 'code',
                language=language,
                edit_mode=edit_mode or 'replace',
                cell_id=new_cell_id or None,
                error='',
                notebook_path=full_path,
                original_file=content,
                updated_file=updated_content,
            )

            return ToolResult(data=data)

        except Exception as e:
            # 捕获所有异常，返回错误结果
            error_message = str(e) if str(e) else 'Unknown error occurred while editing notebook'
            data = NotebookEditToolOutput(
                new_source=new_source,
                cell_type=cell_type or 'code',
                language='python',
                edit_mode='replace',
                error=error_message,
                cell_id=cell_id,
                notebook_path=full_path,
                original_file='',
                updated_file='',
            )
            return ToolResult(data=data)

# 创建工具实例（供工具注册表使用）
notebook_edit_tool = NotebookEditTool()
