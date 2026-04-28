"""
NotebookEditTool 提示和描述

[Workflow]
1. 定义工具名称常量
2. 定义工具描述（简短，用于 API schema）
3. 定义工具提示（详细，用于系统提示词）
"""

# 工具名称常量
NOTEBOOK_EDIT_TOOL_NAME = "NotebookEdit"

# 工具描述
DESCRIPTION = "Replace the contents of a specific cell in a Jupyter notebook."

# 工具提示
PROMPT = (
    "Completely replaces the contents of a specific cell in a Jupyter notebook "
    "(.ipynb file) with new source. Jupyter notebooks are interactive documents "
    "that combine code, text, and visualizations, commonly used for data analysis "
    "and scientific computing. The notebook_path parameter must be an absolute path, "
    "not a relative path. The cell_number is 0-indexed. Use edit_mode=insert to add "
    "a new cell at the index specified by cell_number. Use edit_mode=delete to delete "
    "the cell at the index specified by cell_number."
)
