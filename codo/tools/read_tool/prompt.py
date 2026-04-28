"""
ReadTool 提示和描述
"""

READ_TOOL_NAME = "Read"

DESCRIPTION = """从本地文件系统读取文件。可以直接访问任何文件。

假设此工具能够读取机器上的所有文件。如果用户提供文件路径，假设该路径有效。读取不存在的文件也没关系；会返回错误。

## 使用说明

- file_path 参数必须是绝对路径，而不是相对路径
- 默认情况下，从文件开头读取最多 2000 行
- 可以选择指定行偏移量和限制（特别适用于长文件），但建议通过不提供这些参数来读取整个文件
- 结果使用 cat -n 格式返回，行号从 1 开始
- 此工具可以读取 PDF 文件（.pdf）。对于大型 PDF（超过 10 页），必须提供 pages 参数以读取特定页面范围（例如 pages: "1-5"）。读取大型 PDF 而不使用 pages 参数将失败。每次请求最多 20 页
- 此工具可以读取 Jupyter notebook（.ipynb 文件）并返回所有单元格及其输出，结合代码、文本和可视化
- 此工具只能读取文件，不能读取目录。要读取目录，请通过 Bash 工具使用 ls 命令
- 如果读取存在但内容为空的文件，将收到系统提醒警告代替文件内容
"""

def get_user_facing_name() -> str:
    """获取用户可见的工具名称"""
    return "读取文件"

def get_tool_use_summary(input_data: dict) -> str:
    """
    获取工具使用摘要

    Args:
        input_data: 工具输入数据

    Returns:
        摘要字符串
    """
    file_path = input_data.get('file_path', '')

    # 提取文件名
    import os
    filename = os.path.basename(file_path)

    # 如果有 offset/limit，添加范围信息
    offset = input_data.get('offset')
    limit = input_data.get('limit')

    if offset is not None or limit is not None:
        return f"{filename} (部分)"

    return filename

def get_activity_description(input_data: dict) -> str:
    """
    获取活动描述（用于进度显示）

    Args:
        input_data: 工具输入数据

    Returns:
        活动描述
    """
    summary = get_tool_use_summary(input_data)
    return f"读取: {summary}"
