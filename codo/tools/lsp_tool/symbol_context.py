"""
符号上下文提取

从文件内容中提取光标位置的符号名称
"""

import re
from typing import Optional

def extract_symbol_at_position(
    content: str,
    line: int,
    character: int,
) -> Optional[str]:
    """提取光标位置的符号名称

    Args:
        content: 文件内容
        line: 行号（1-based）
        character: 字符位置（1-based）

    Returns:
        符号名称，如果无法提取则返回 None
    """
    lines = content.split("\n")

    # 检查行号是否有效
    if line < 1 or line > len(lines):
        return None

    # 获取目标行（转换为 0-based）
    target_line = lines[line - 1]

    # 检查字符位置是否有效
    if character < 1 or character > len(target_line) + 1:
        return None

    # 转换为 0-based
    char_index = character - 1

    # 如果光标在行尾，向前查找
    if char_index >= len(target_line):
        char_index = len(target_line) - 1

    # 如果光标在空白字符上，向前查找
    while char_index > 0 and target_line[char_index].isspace():
        char_index -= 1

    # 定义符号字符（字母、数字、下划线）
    def is_symbol_char(c: str) -> bool:
        return c.isalnum() or c == "_"

    # 如果当前字符不是符号字符，返回 None
    if not is_symbol_char(target_line[char_index]):
        return None

    # 向前查找符号开始位置
    start = char_index
    while start > 0 and is_symbol_char(target_line[start - 1]):
        start -= 1

    # 向后查找符号结束位置
    end = char_index
    while end < len(target_line) - 1 and is_symbol_char(target_line[end + 1]):
        end += 1

    # 提取符号
    symbol = target_line[start : end + 1]

    # 过滤纯数字
    if symbol.isdigit():
        return None

    return symbol

def extract_symbol_context(
    content: str,
    line: int,
    character: int,
    context_lines: int = 2,
) -> Optional[str]:
    """提取符号及其上下文

    Args:
        content: 文件内容
        line: 行号（1-based）
        character: 字符位置（1-based）
        context_lines: 上下文行数

    Returns:
        符号及其上下文，如果无法提取则返回 None
    """
    lines = content.split("\n")

    # 检查行号是否有效
    if line < 1 or line > len(lines):
        return None

    # 提取符号
    symbol = extract_symbol_at_position(content, line, character)

    if not symbol:
        return None

    # 提取上下文
    start_line = max(1, line - context_lines)
    end_line = min(len(lines), line + context_lines)

    context_lines_list = []
    for i in range(start_line, end_line + 1):
        line_content = lines[i - 1]
        prefix = "→ " if i == line else "  "
        context_lines_list.append(f"{prefix}{i:4d} | {line_content}")

    context = "\n".join(context_lines_list)

    return f"Symbol: {symbol}\n\nContext:\n{context}"
