"""
LSP 结果格式化器

将 LSP 响应格式化为人类可读的文本
"""

import os
from typing import Any, Optional, List, Union
from pathlib import Path

from lsprotocol.types import (
    Location,
    LocationLink,
    SymbolInformation,
    DocumentSymbol,
    Hover,
    CallHierarchyIncomingCall,
    CallHierarchyOutgoingCall,
    MarkupContent,
    MarkupKind,
    Range,
)

def _uri_to_path(uri: str) -> str:
    """将 file:// URI 转换为文件路径

    Args:
        uri: file:// URI

    Returns:
        文件路径
    """
    if uri.startswith("file://"):
        # 移除 file:// 前缀
        path = uri[7:]
        # Windows 路径处理
        if len(path) > 2 and path[0] == "/" and path[2] == ":":
            path = path[1:]  # 移除开头的 /
        return path
    return uri

def _format_location(location: Union[Location, LocationLink], cwd: str) -> str:
    """格式化位置信息

    Args:
        location: 位置或位置链接
        cwd: 当前工作目录

    Returns:
        格式化的位置字符串
    """
    if isinstance(location, LocationLink):
        uri = location.target_uri
        range_obj = location.target_selection_range
    else:
        uri = location.uri
        range_obj = location.range

    # 转换 URI 到路径
    file_path = _uri_to_path(uri)

    # 转换为相对路径
    try:
        rel_path = os.path.relpath(file_path, cwd)
    except ValueError:
        rel_path = file_path

    # 格式化位置（1-based）
    line = range_obj.start.line + 1
    char = range_obj.start.character + 1

    return f"{rel_path}:{line}:{char}"

def _format_range(range_obj: Range) -> str:
    """格式化范围

    Args:
        range_obj: 范围对象

    Returns:
        格式化的范围字符串
    """
    start_line = range_obj.start.line + 1
    start_char = range_obj.start.character + 1
    end_line = range_obj.end.line + 1
    end_char = range_obj.end.character + 1

    if start_line == end_line:
        return f"line {start_line}, chars {start_char}-{end_char}"
    else:
        return f"lines {start_line}:{start_char} - {end_line}:{end_char}"

def format_definition_result(
    result: Union[Location, List[Location], List[LocationLink], None],
    cwd: str,
) -> tuple[str, int, int]:
    """格式化定义结果

    Args:
        result: LSP 定义结果
        cwd: 当前工作目录

    Returns:
        (格式化文本, 结果数量, 文件数量)
    """
    if result is None:
        return "No definition found", 0, 0

    # 规范化为列表
    locations = [result] if not isinstance(result, list) else result

    if len(locations) == 0:
        return "No definition found", 0, 0

    # 格式化每个位置
    lines = []
    files = set()

    for loc in locations:
        formatted = _format_location(loc, cwd)
        lines.append(f"  {formatted}")

        # 提取文件路径
        if isinstance(loc, LocationLink):
            uri = loc.target_uri
        else:
            uri = loc.uri
        files.add(_uri_to_path(uri))

    result_text = "Definition found:\n" + "\n".join(lines)
    return result_text, len(locations), len(files)

def format_references_result(
    result: Optional[List[Location]],
    cwd: str,
) -> tuple[str, int, int]:
    """格式化引用结果

    Args:
        result: LSP 引用结果
        cwd: 当前工作目录

    Returns:
        (格式化文本, 结果数量, 文件数量)
    """
    if result is None or len(result) == 0:
        return "No references found", 0, 0

    # 按文件分组
    by_file: dict[str, List[Location]] = {}
    for loc in result:
        file_path = _uri_to_path(loc.uri)
        if file_path not in by_file:
            by_file[file_path] = []
        by_file[file_path].append(loc)

    # 格式化
    lines = [f"Found {len(result)} reference(s) in {len(by_file)} file(s):\n"]

    for file_path, locations in sorted(by_file.items()):
        # 相对路径
        try:
            rel_path = os.path.relpath(file_path, cwd)
        except ValueError:
            rel_path = file_path

        lines.append(f"\n{rel_path}:")
        for loc in sorted(locations, key=lambda l: (l.range.start.line, l.range.start.character)):
            line = loc.range.start.line + 1
            char = loc.range.start.character + 1
            lines.append(f"  Line {line}:{char}")

    return "\n".join(lines), len(result), len(by_file)

def format_hover_result(
    result: Optional[Hover],
    cwd: str,
) -> tuple[str, int, int]:
    """格式化悬停结果

    Args:
        result: LSP 悬停结果
        cwd: 当前工作目录

    Returns:
        (格式化文本, 结果数量, 文件数量)
    """
    if result is None:
        return "No hover information available", 0, 0

    # 提取内容
    contents = result.contents

    if isinstance(contents, str):
        text = contents
    elif isinstance(contents, MarkupContent):
        text = contents.value
    elif isinstance(contents, list):
        parts = []
        for item in contents:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, MarkupContent):
                parts.append(item.value)
            elif hasattr(item, 'value'):
                parts.append(item.value)
        text = "\n\n".join(parts)
    else:
        text = str(contents)

    # 限制长度
    max_length = 1000
    if len(text) > max_length:
        text = text[:max_length] + "\n... (truncated)"

    return f"Hover information:\n{text}", 1, 0

def format_document_symbol_result(
    result: Union[List[DocumentSymbol], List[SymbolInformation], None],
    cwd: str,
) -> tuple[str, int, int]:
    """格式化文档符号结果

    Args:
        result: LSP 文档符号结果
        cwd: 当前工作目录

    Returns:
        (格式化文本, 结果数量, 文件数量)
    """
    if result is None or len(result) == 0:
        return "No symbols found in document", 0, 0

    lines = [f"Found {len(result)} symbol(s):\n"]

    def format_document_symbol(symbol: DocumentSymbol, indent: int = 0):
        """递归格式化 DocumentSymbol"""
        prefix = "  " * indent
        line = symbol.range.start.line + 1
        kind = symbol.kind.name if hasattr(symbol.kind, 'name') else str(symbol.kind)
        lines.append(f"{prefix}{symbol.name} ({kind}) - Line {line}")

        # 递归处理子符号
        if hasattr(symbol, 'children') and symbol.children:
            for child in symbol.children:
                format_document_symbol(child, indent + 1)

    for item in result:
        if isinstance(item, DocumentSymbol):
            format_document_symbol(item)
        elif isinstance(item, SymbolInformation):
            line = item.location.range.start.line + 1
            kind = item.kind.name if hasattr(item.kind, 'name') else str(item.kind)
            container = f" in {item.container_name}" if item.container_name else ""
            lines.append(f"  {item.name} ({kind}) - Line {line}{container}")

    return "\n".join(lines), len(result), 1

def format_workspace_symbol_result(
    result: Optional[List[SymbolInformation]],
    cwd: str,
) -> tuple[str, int, int]:
    """格式化工作区符号结果

    Args:
        result: LSP 工作区符号结果
        cwd: 当前工作目录

    Returns:
        (格式化文本, 结果数量, 文件数量)
    """
    if result is None or len(result) == 0:
        return "No symbols found in workspace", 0, 0

    # 按文件分组
    by_file: dict[str, List[SymbolInformation]] = {}
    for symbol in result:
        file_path = _uri_to_path(symbol.location.uri)
        if file_path not in by_file:
            by_file[file_path] = []
        by_file[file_path].append(symbol)

    lines = [f"Found {len(result)} symbol(s) in {len(by_file)} file(s):\n"]

    for file_path, symbols in sorted(by_file.items()):
        try:
            rel_path = os.path.relpath(file_path, cwd)
        except ValueError:
            rel_path = file_path

        lines.append(f"\n{rel_path}:")
        for symbol in symbols:
            line = symbol.location.range.start.line + 1
            kind = symbol.kind.name if hasattr(symbol.kind, 'name') else str(symbol.kind)
            lines.append(f"  {symbol.name} ({kind}) - Line {line}")

    return "\n".join(lines), len(result), len(by_file)

def format_call_hierarchy_result(
    result: Union[List[CallHierarchyIncomingCall], List[CallHierarchyOutgoingCall], None],
    operation: str,
    cwd: str,
) -> tuple[str, int, int]:
    """格式化调用层级结果

    Args:
        result: LSP 调用层级结果
        operation: 操作类型
        cwd: 当前工作目录

    Returns:
        (格式化文本, 结果数量, 文件数量)
    """
    if result is None or len(result) == 0:
        return f"No {operation} found", 0, 0

    lines = [f"Found {len(result)} call(s):\n"]
    files = set()

    for call in result:
        if isinstance(call, CallHierarchyIncomingCall):
            item = call.from_
        else:
            item = call.to

        # 提取位置信息
        uri = item.uri
        file_path = _uri_to_path(uri)
        files.add(file_path)

        try:
            rel_path = os.path.relpath(file_path, cwd)
        except ValueError:
            rel_path = file_path

        line = item.range.start.line + 1
        kind = item.kind.name if hasattr(item.kind, 'name') else str(item.kind)

        lines.append(f"  {item.name} ({kind}) - {rel_path}:{line}")

    return "\n".join(lines), len(result), len(files)

def format_result(
    operation: str,
    result: Any,
    cwd: str,
) -> tuple[str, int, int]:
    """格式化 LSP 结果

    Args:
        operation: 操作类型
        result: LSP 结果
        cwd: 当前工作目录

    Returns:
        (格式化文本, 结果数量, 文件数量)
    """
    if operation == "goToDefinition":
        return format_definition_result(result, cwd)
    elif operation == "findReferences":
        return format_references_result(result, cwd)
    elif operation == "hover":
        return format_hover_result(result, cwd)
    elif operation == "documentSymbol":
        return format_document_symbol_result(result, cwd)
    elif operation == "workspaceSymbol":
        return format_workspace_symbol_result(result, cwd)
    elif operation == "goToImplementation":
        return format_definition_result(result, cwd)  # 格式相同
    elif operation in ["incomingCalls", "outgoingCalls"]:
        return format_call_hierarchy_result(result, operation, cwd)
    else:
        return f"Unknown operation: {operation}", 0, 0
