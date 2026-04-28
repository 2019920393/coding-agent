"""
路径处理工具模块

提供路径规范化、扩展、相对路径转换等功能。

[Workflow]
1. expandPath(): 扩展用户路径和环境变量，转换为绝对路径
2. toRelativePath(): 将绝对路径转换为相对于 cwd 的相对路径
3. isUncPath(): 检测 Windows UNC 路径（安全检查）
"""

import os
from pathlib import Path
from typing import Optional

def expandPath(path: str, cwd: Optional[str] = None) -> str:
    """
    扩展路径为绝对路径

    处理：
    - ~ 扩展为用户主目录
    - 环境变量扩展（$VAR 或 %VAR%）
    - 相对路径转换为绝对路径

    Args:
        path: 输入路径
        cwd: 当前工作目录（默认使用 os.getcwd()）

    Returns:
        规范化的绝对路径

    Examples:
        >>> expandPath("~/test.txt")
        "/home/user/test.txt"
        >>> expandPath("./test.txt", "/home/user/project")
        "/home/user/project/test.txt"
    """
    if not path:
        return cwd or os.getcwd()

    # 扩展用户主目录
    path = os.path.expanduser(path)

    # 扩展环境变量
    path = os.path.expandvars(path)

    # 转换为绝对路径
    if not os.path.isabs(path):
        base = cwd or os.getcwd()
        path = os.path.join(base, path)

    # 规范化路径（解析 .. 和 .）
    path = os.path.normpath(path)

    return path

def toRelativePath(path: str, cwd: Optional[str] = None) -> str:
    """
    将绝对路径转换为相对路径

    Args:
        path: 绝对路径
        cwd: 基准目录（默认使用 os.getcwd()）

    Returns:
        相对路径（如果在 cwd 下），否则返回原路径

    Examples:
        >>> toRelativePath("/home/user/project/test.txt", "/home/user/project")
        "test.txt"
        >>> toRelativePath("/other/path/file.txt", "/home/user/project")
        "/other/path/file.txt"
    """
    base = cwd or os.getcwd()

    try:
        # 尝试计算相对路径
        rel_path = os.path.relpath(path, base)

        # 如果相对路径以 .. 开头，说明不在 cwd 下，返回绝对路径
        if rel_path.startswith('..'):
            return path

        return rel_path
    except ValueError:
        # Windows 上不同驱动器会抛出 ValueError
        return path

def isUncPath(path: str) -> bool:
    """
    检测是否为 Windows UNC 路径

    UNC 路径格式：\\\\server\\share\\path

    安全考虑：UNC 路径可能触发 NTLM 认证，导致凭据泄露

    Args:
        path: 待检测路径

    Returns:
        是否为 UNC 路径

    Examples:
        >>> isUncPath("\\\\\\\\server\\\\share\\\\file.txt")
        True
        >>> isUncPath("C:\\\\Users\\\\test.txt")
        False
    """
    # Windows UNC 路径以 \\\\ 开头
    return path.startswith('\\\\') or path.startswith('//')

def normalizePath(path: str) -> str:
    """
    规范化路径（不扩展为绝对路径）

    处理：
    - 统一路径分隔符
    - 解析 . 和 ..
    - 移除多余的分隔符

    Args:
        path: 输入路径

    Returns:
        规范化的路径
    """
    return os.path.normpath(path)

def ensureDir(path: str) -> None:
    """
    确保目录存在，不存在则创建

    Args:
        path: 目录路径
    """
    Path(path).mkdir(parents=True, exist_ok=True)

def getFileExtension(path: str) -> str:
    """
    获取文件扩展名（小写，不含点）

    Args:
        path: 文件路径

    Returns:
        扩展名（如 "txt", "py"）

    Examples:
        >>> getFileExtension("test.txt")
        "txt"
        >>> getFileExtension("archive.tar.gz")
        "gz"
    """
    _, ext = os.path.splitext(path)
    return ext.lstrip('.').lower()

def isSubPath(child: str, parent: str) -> bool:
    """
    检查 child 是否为 parent 的子路径

    Args:
        child: 子路径
        parent: 父路径

    Returns:
        是否为子路径

    Examples:
        >>> isSubPath("/home/user/project/file.txt", "/home/user/project")
        True
        >>> isSubPath("/home/other/file.txt", "/home/user/project")
        False
    """
    try:
        child_path = Path(child).resolve()
        parent_path = Path(parent).resolve()
        return parent_path in child_path.parents or child_path == parent_path
    except (ValueError, OSError):
        return False
