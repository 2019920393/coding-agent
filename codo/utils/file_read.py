"""
文件读取辅助模块

提供文件读取相关的辅助功能，包括编码检测、内容读取、特殊文件处理等。

[Workflow]
1. readFileSyncWithMetadata(): 读取文件并返回元数据
2. detectEncoding(): 检测文件编码
3. readFileWithOffset(): 读取文件的指定范围
4. readPdfFile(): 读取 PDF 文件
5. readImageFile(): 读取图片文件（返回 base64）
"""

import os
import base64
from typing import Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timezone

@dataclass
class FileReadResult:
    """文件读取结果"""
    content: str  # 文件内容
    encoding: str  # 文件编码
    size: int  # 文件大小（字节）
    mtime: datetime  # 修改时间
    lineCount: int  # 行数
    isBinary: bool  # 是否为二进制文件

def detectEncoding(filepath: str, sample_size: int = 8192) -> str:
    """
    检测文件编码

    策略：
    1. 尝试 UTF-8
    2. 尝试 UTF-16LE（Windows）
    3. 使用 chardet 检测（如果可用）
    4. 回退到 latin-1

    Args:
        filepath: 文件路径
        sample_size: 采样大小（字节）

    Returns:
        编码名称
    """
    with open(filepath, 'rb') as f:
        sample = f.read(sample_size)

    # 尝试 UTF-8
    try:
        sample.decode('utf-8')
        return 'utf-8'
    except UnicodeDecodeError:
        pass

    # 尝试 UTF-16LE（Windows 常见）
    try:
        sample.decode('utf-16le')
        return 'utf-16le'
    except UnicodeDecodeError:
        pass

    # 尝试使用 chardet
    try:
        import chardet
        result = chardet.detect(sample)
        if result['encoding'] and result['confidence'] > 0.7:
            return result['encoding']
    except ImportError:
        pass

    # 回退到 latin-1（总是成功）
    return 'latin-1'

def readFileSyncWithMetadata(filepath: str) -> FileReadResult:
    """
    读取文件并返回元数据

    Args:
        filepath: 文件路径

    Returns:
        FileReadResult 对象

    Raises:
        FileNotFoundError: 文件不存在
        PermissionError: 无权限读取
        IsADirectoryError: 路径是目录
    """
    from .fs_operations import getFsImplementation

    fs = getFsImplementation()

    # 检查文件是否存在
    if not fs.exists(filepath):
        raise FileNotFoundError(f'文件不存在: {filepath}')

    # 检查是否为目录
    if fs.isDir(filepath):
        raise IsADirectoryError(f'路径是目录: {filepath}')

    # 获取文件元数据
    size = fs.getFileSize(filepath)
    mtime = fs.getModificationTime(filepath)

    # 检测是否为二进制文件
    is_binary = fs.isBinaryFile(filepath)

    if is_binary:
        # 二进制文件返回空内容
        return FileReadResult(
            content='',
            encoding='binary',
            size=size,
            mtime=mtime,
            lineCount=0,
            isBinary=True
        )

    # 检测编码
    encoding = detectEncoding(filepath)

    # 读取文件内容
    try:
        content = fs.readFile(filepath, encoding=encoding)
    except UnicodeDecodeError:
        # 编码检测失败，回退到 latin-1
        encoding = 'latin-1'
        content = fs.readFile(filepath, encoding=encoding)

    # 统计行数
    line_count = content.count('\n') + (1 if content and not content.endswith('\n') else 0)

    return FileReadResult(
        content=content,
        encoding=encoding,
        size=size,
        mtime=mtime,
        lineCount=line_count,
        isBinary=False
    )

def readFileWithOffset(
    filepath: str,
    offset: int = 0,
    limit: Optional[int] = None
) -> Tuple[str, int]:
    """
    读取文件的指定范围（按行）

    Args:
        filepath: 文件路径
        offset: 起始行号（从 0 开始）
        limit: 读取行数（None 表示读取到文件末尾）

    Returns:
        (内容, 总行数)

    Examples:
        >>> content, total = readFileWithOffset("test.txt", offset=10, limit=20)
        >>> # 读取第 10-29 行（共 20 行）
    """
    result = readFileSyncWithMetadata(filepath)

    if result.isBinary:
        raise ValueError(f'无法读取二进制文件: {filepath}')

    lines = result.content.splitlines(keepends=True)
    total_lines = len(lines)

    # 应用 offset 和 limit
    start = offset
    end = start + limit if limit is not None else total_lines

    selected_lines = lines[start:end]
    content = ''.join(selected_lines)

    return content, total_lines

def readPdfFile(filepath: str, pages: Optional[str] = None) -> str:
    """
    读取 PDF 文件内容

    Args:
        filepath: PDF 文件路径
        pages: 页码范围（如 "1-5", "3", "10-20"）

    Returns:
        PDF 文本内容

    Raises:
        ImportError: pypdf 未安装
        ValueError: 页码范围无效
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ImportError('读取 PDF 需要安装 pypdf: pip install pypdf')

    reader = PdfReader(filepath)
    total_pages = len(reader.pages)

    # 解析页码范围
    if pages:
        page_numbers = _parsePageRange(pages, total_pages)
    else:
        page_numbers = range(total_pages)

    # 提取文本
    text_parts = []
    for page_num in page_numbers:
        if 0 <= page_num < total_pages:
            page = reader.pages[page_num]
            text_parts.append(page.extract_text())

    return '\n\n'.join(text_parts)

def readImageFile(filepath: str) -> str:
    """
    读取图片文件并返回 base64 编码

    Args:
        filepath: 图片文件路径

    Returns:
        base64 编码的图片数据

    Examples:
        >>> base64_data = readImageFile("image.png")
        >>> # 可用于 <img src="data:image/png;base64,{base64_data}">
    """
    from .fs_operations import getFsImplementation

    fs = getFsImplementation()
    image_bytes = fs.readFileBytes(filepath)
    return base64.b64encode(image_bytes).decode('ascii')

def readNotebookFile(filepath: str) -> str:
    """
    读取 Jupyter Notebook 文件并格式化为文本

    Args:
        filepath: .ipynb 文件路径

    Returns:
        格式化的 notebook 内容

    Raises:
        ImportError: nbformat 未安装
    """
    try:
        import nbformat
    except ImportError:
        raise ImportError('读取 Notebook 需要安装 nbformat: pip install nbformat')

    with open(filepath, 'r', encoding='utf-8') as f:
        nb = nbformat.read(f, as_version=4)

    parts = []
    parts.append(f'# Jupyter Notebook: {os.path.basename(filepath)}')
    parts.append('')

    for idx, cell in enumerate(nb.cells, 1):
        cell_type = cell.cell_type

        if cell_type == 'markdown':
            parts.append(f'## Markdown Cell {idx}')
            parts.append(cell.source)
            parts.append('')
        elif cell_type == 'code':
            parts.append(f'## Code Cell {idx}')
            parts.append('```python')
            parts.append(cell.source)
            parts.append('```')

            # 添加输出
            if cell.get('outputs'):
                parts.append('')
                parts.append('### Output:')
                for output in cell.outputs:
                    if output.output_type == 'stream':
                        parts.append(output.text)
                    elif output.output_type == 'execute_result':
                        if 'text/plain' in output.data:
                            parts.append(output.data['text/plain'])
                    elif output.output_type == 'error':
                        parts.append(f'Error: {output.ename}: {output.evalue}')

            parts.append('')

    return '\n'.join(parts)

def _parsePageRange(pages: str, total_pages: int) -> range:
    """
    解析页码范围字符串

    Args:
        pages: 页码范围（如 "1-5", "3", "10-20"）
        total_pages: 总页数

    Returns:
        页码范围（从 0 开始）

    Raises:
        ValueError: 页码范围无效
    """
    pages = pages.strip()

    if '-' in pages:
        # 范围格式：1-5
        parts = pages.split('-')
        if len(parts) != 2:
            raise ValueError(f'无效的页码范围: {pages}')

        try:
            start = int(parts[0]) - 1  # 转换为 0-based
            end = int(parts[1])
        except ValueError:
            raise ValueError(f'无效的页码范围: {pages}')

        if start < 0 or end > total_pages or start >= end:
            raise ValueError(f'页码范围超出范围: {pages} (总页数: {total_pages})')

        return range(start, end)
    else:
        # 单页格式：3
        try:
            page_num = int(pages) - 1  # 转换为 0-based
        except ValueError:
            raise ValueError(f'无效的页码: {pages}')

        if page_num < 0 or page_num >= total_pages:
            raise ValueError(f'页码超出范围: {pages} (总页数: {total_pages})')

        return range(page_num, page_num + 1)
