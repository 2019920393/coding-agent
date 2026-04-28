"""
Diff 生成工具模块

提供统一的 diff 生成功能，支持多种格式。

[Workflow]
1. generateUnifiedDiff(): 生成 unified diff 格式
2. generateStructuredPatch(): 生成结构化 patch（hunks）
3. countLinesChanged(): 统计变更行数
4. getPatchForDisplay(): 获取用于显示的 patch
"""

import difflib
from typing import List, Optional, Tuple
from dataclasses import dataclass

@dataclass
class DiffHunk:
    """Diff hunk 数据结构"""
    oldStart: int  # 旧文件起始行号
    oldLines: int  # 旧文件行数
    newStart: int  # 新文件起始行号
    newLines: int  # 新文件行数
    lines: List[str]  # diff 行内容（包含 +/-/ 前缀）

@dataclass
class StructuredPatch:
    """结构化 patch 数据结构"""
    hunks: List[DiffHunk]
    oldFileName: str
    newFileName: str

def generateUnifiedDiff(
    original: str,
    modified: str,
    fromfile: str = 'original',
    tofile: str = 'modified',
    lineterm: str = '\n',
    context_lines: int = 3
) -> str:
    """
    生成 unified diff 格式

    Args:
        original: 原始内容
        modified: 修改后内容
        fromfile: 原始文件名
        tofile: 修改后文件名
        lineterm: 行终止符
        context_lines: 上下文行数

    Returns:
        unified diff 字符串

    Examples:
        >>> original = "line1\\nline2\\nline3"
        >>> modified = "line1\\nmodified\\nline3"
        >>> diff = generateUnifiedDiff(original, modified)
        >>> print(diff)
        --- original
        +++ modified
        @@ -1,3 +1,3 @@
         line1
        -line2
        +modified
         line3
    """
    original_lines = original.splitlines(keepends=True)
    modified_lines = modified.splitlines(keepends=True)

    diff_lines = difflib.unified_diff(
        original_lines,
        modified_lines,
        fromfile=fromfile,
        tofile=tofile,
        lineterm=lineterm,
        n=context_lines
    )

    return ''.join(diff_lines)

def generateStructuredPatch(
    original: str,
    modified: str,
    fromfile: str = 'original',
    tofile: str = 'modified',
    context_lines: int = 3
) -> StructuredPatch:
    """
    生成结构化 patch（hunks）

    Args:
        original: 原始内容
        modified: 修改后内容
        fromfile: 原始文件名
        tofile: 修改后文件名
        context_lines: 上下文行数

    Returns:
        StructuredPatch 对象
    """
    original_lines = original.splitlines(keepends=False)
    modified_lines = modified.splitlines(keepends=False)

    # 使用 difflib 生成 unified diff
    diff_lines = list(difflib.unified_diff(
        original_lines,
        modified_lines,
        fromfile=fromfile,
        tofile=tofile,
        lineterm='',
        n=context_lines
    ))

    hunks: List[DiffHunk] = []
    current_hunk_lines: List[str] = []
    current_hunk_header: Optional[Tuple[int, int, int, int]] = None

    for line in diff_lines:
        # 跳过文件头
        if line.startswith('---') or line.startswith('+++'):
            continue

        # 解析 hunk 头
        if line.startswith('@@'):
            # 保存上一个 hunk
            if current_hunk_header and current_hunk_lines:
                hunks.append(DiffHunk(
                    oldStart=current_hunk_header[0],
                    oldLines=current_hunk_header[1],
                    newStart=current_hunk_header[2],
                    newLines=current_hunk_header[3],
                    lines=current_hunk_lines
                ))

            # 解析新 hunk 头：@@ -oldStart,oldLines +newStart,newLines @@
            parts = line.split()
            old_range = parts[1].lstrip('-').split(',')
            new_range = parts[2].lstrip('+').split(',')

            old_start = int(old_range[0])
            old_lines = int(old_range[1]) if len(old_range) > 1 else 1
            new_start = int(new_range[0])
            new_lines = int(new_range[1]) if len(new_range) > 1 else 1

            current_hunk_header = (old_start, old_lines, new_start, new_lines)
            current_hunk_lines = []
        else:
            # 添加 hunk 内容行
            current_hunk_lines.append(line)

    # 保存最后一个 hunk
    if current_hunk_header and current_hunk_lines:
        hunks.append(DiffHunk(
            oldStart=current_hunk_header[0],
            oldLines=current_hunk_header[1],
            newStart=current_hunk_header[2],
            newLines=current_hunk_header[3],
            lines=current_hunk_lines
        ))

    return StructuredPatch(
        hunks=hunks,
        oldFileName=fromfile,
        newFileName=tofile
    )

def countLinesChanged(original: str, modified: str) -> Tuple[int, int]:
    """
    统计变更行数

    Args:
        original: 原始内容
        modified: 修改后内容

    Returns:
        (添加行数, 删除行数)

    Examples:
        >>> original = "line1\\nline2\\nline3"
        >>> modified = "line1\\nmodified\\nline3\\nline4"
        >>> countLinesChanged(original, modified)
        (2, 1)
    """
    original_lines = original.splitlines(keepends=False)
    modified_lines = modified.splitlines(keepends=False)

    diff = difflib.unified_diff(original_lines, modified_lines, lineterm='')

    added = 0
    deleted = 0

    for line in diff:
        if line.startswith('+') and not line.startswith('+++'):
            added += 1
        elif line.startswith('-') and not line.startswith('---'):
            deleted += 1

    return added, deleted

def getPatchForDisplay(
    original: str,
    modified: str,
    fromfile: str = 'original',
    tofile: str = 'modified',
    max_lines: int = 100
) -> str:
    """
    获取用于显示的 patch（可能截断）

    Args:
        original: 原始内容
        modified: 修改后内容
        fromfile: 原始文件名
        tofile: 修改后文件名
        max_lines: 最大显示行数

    Returns:
        patch 字符串（可能包含截断提示）
    """
    diff = generateUnifiedDiff(original, modified, fromfile, tofile)
    lines = diff.splitlines()

    if len(lines) <= max_lines:
        return diff

    # 截断并添加提示
    truncated_lines = lines[:max_lines]
    truncated_lines.append(f'\n... (截断，共 {len(lines)} 行，仅显示前 {max_lines} 行)')

    return '\n'.join(truncated_lines)

def areLinesIdentical(line1: str, line2: str, ignore_whitespace: bool = False) -> bool:
    """
    比较两行是否相同

    Args:
        line1: 第一行
        line2: 第二行
        ignore_whitespace: 是否忽略空白字符

    Returns:
        是否相同
    """
    if ignore_whitespace:
        return line1.strip() == line2.strip()
    return line1 == line2

def detectLineEnding(content: str) -> str:
    """
    检测文件的行尾符

    Args:
        content: 文件内容

    Returns:
        行尾符（'\\r\\n', '\\n', '\\r'）

    Examples:
        >>> detectLineEnding("line1\\r\\nline2\\r\\n")
        '\\r\\n'
        >>> detectLineEnding("line1\\nline2\\n")
        '\\n'
    """
    if '\r\n' in content:
        return '\r\n'
    elif '\n' in content:
        return '\n'
    elif '\r' in content:
        return '\r'
    else:
        # 默认使用系统行尾符
        import os
        return os.linesep
