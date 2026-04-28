"""
文件系统操作抽象层

提供统一的文件系统接口，支持同步和异步操作。

[Workflow]
1. 同步操作：直接使用标准库（os, pathlib）
2. 异步操作：使用 aiofiles 库
3. 文件元数据：获取大小、修改时间、权限等
4. 安全检查：UNC 路径、设备文件、符号链接等
"""

import os
import stat
from pathlib import Path
from typing import Optional, Tuple
from datetime import datetime, timezone

try:
    import aiofiles
    import aiofiles.os
    AIOFILES_AVAILABLE = True
except ImportError:
    AIOFILES_AVAILABLE = False

class FileSystemOperations:
    """文件系统操作类（单例模式）"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # ==================== 同步操作 ====================

    def exists(self, path: str) -> bool:
        """检查路径是否存在"""
        return os.path.exists(path)

    def isFile(self, path: str) -> bool:
        """检查是否为文件"""
        return os.path.isfile(path)

    def isDir(self, path: str) -> bool:
        """检查是否为目录"""
        return os.path.isdir(path)

    def isSymlink(self, path: str) -> bool:
        """检查是否为符号链接"""
        return os.path.islink(path)

    def readFile(self, path: str, encoding: str = 'utf-8') -> str:
        """
        读取文件内容（文本模式）

        Args:
            path: 文件路径
            encoding: 文件编码

        Returns:
            文件内容
        """
        with open(path, 'r', encoding=encoding) as f:
            return f.read()

    def readFileBytes(self, path: str) -> bytes:
        """
        读取文件内容（二进制模式）

        Args:
            path: 文件路径

        Returns:
            文件内容（字节）
        """
        with open(path, 'rb') as f:
            return f.read()

    def writeFile(self, path: str, content: str, encoding: str = 'utf-8') -> None:
        """
        写入文件内容（文本模式）

        Args:
            path: 文件路径
            content: 文件内容
            encoding: 文件编码
        """
        with open(path, 'w', encoding=encoding) as f:
            f.write(content)

    def writeFileBytes(self, path: str, content: bytes) -> None:
        """
        写入文件内容（二进制模式）

        Args:
            path: 文件路径
            content: 文件内容（字节）
        """
        with open(path, 'wb') as f:
            f.write(content)

    def getFileSize(self, path: str) -> int:
        """
        获取文件大小（字节）

        Args:
            path: 文件路径

        Returns:
            文件大小
        """
        return os.path.getsize(path)

    def getModificationTime(self, path: str) -> datetime:
        """
        获取文件修改时间

        Args:
            path: 文件路径

        Returns:
            修改时间（UTC）
        """
        mtime = os.path.getmtime(path)
        return datetime.fromtimestamp(mtime, tz=timezone.utc)

    def getFileStats(self, path: str) -> os.stat_result:
        """
        获取文件统计信息

        Args:
            path: 文件路径

        Returns:
            stat 结果
        """
        return os.stat(path)

    def mkdir(self, path: str, parents: bool = True, exist_ok: bool = True) -> None:
        """
        创建目录

        Args:
            path: 目录路径
            parents: 是否创建父目录
            exist_ok: 目录已存在时是否报错
        """
        Path(path).mkdir(parents=parents, exist_ok=exist_ok)

    def listDir(self, path: str) -> list[str]:
        """
        列出目录内容

        Args:
            path: 目录路径

        Returns:
            文件/目录名列表
        """
        return os.listdir(path)

    def remove(self, path: str) -> None:
        """
        删除文件

        Args:
            path: 文件路径
        """
        os.remove(path)

    def rmdir(self, path: str) -> None:
        """
        删除空目录

        Args:
            path: 目录路径
        """
        os.rmdir(path)

    # ==================== 异步操作 ====================

    async def readFileAsync(self, path: str, encoding: str = 'utf-8') -> str:
        """
        异步读取文件内容（文本模式）

        Args:
            path: 文件路径
            encoding: 文件编码

        Returns:
            文件内容
        """
        if not AIOFILES_AVAILABLE:
            # 回退到同步操作
            return self.readFile(path, encoding)

        async with aiofiles.open(path, 'r', encoding=encoding) as f:
            return await f.read()

    async def readFileBytesAsync(self, path: str) -> bytes:
        """
        异步读取文件内容（二进制模式）

        Args:
            path: 文件路径

        Returns:
            文件内容（字节）
        """
        if not AIOFILES_AVAILABLE:
            return self.readFileBytes(path)

        async with aiofiles.open(path, 'rb') as f:
            return await f.read()

    async def writeFileAsync(self, path: str, content: str, encoding: str = 'utf-8') -> None:
        """
        异步写入文件内容（文本模式）

        Args:
            path: 文件路径
            content: 文件内容
            encoding: 文件编码
        """
        if not AIOFILES_AVAILABLE:
            self.writeFile(path, content, encoding)
            return

        async with aiofiles.open(path, 'w', encoding=encoding) as f:
            await f.write(content)

    async def writeFileBytesAsync(self, path: str, content: bytes) -> None:
        """
        异步写入文件内容（二进制模式）

        Args:
            path: 文件路径
            content: 文件内容（字节）
        """
        if not AIOFILES_AVAILABLE:
            self.writeFileBytes(path, content)
            return

        async with aiofiles.open(path, 'wb') as f:
            await f.write(content)

    # ==================== 安全检查 ====================

    def isDeviceFile(self, path: str) -> bool:
        """
        检查是否为设备文件（/dev/random, /dev/zero 等）

        Args:
            path: 文件路径

        Returns:
            是否为设备文件
        """
        try:
            st = os.stat(path)
            return stat.S_ISCHR(st.st_mode) or stat.S_ISBLK(st.st_mode)
        except (OSError, ValueError):
            return False

    def isBinaryFile(self, path: str, sample_size: int = 8192) -> bool:
        """
        检测是否为二进制文件

        策略：读取前 N 字节，检查是否包含 NULL 字节或大量非文本字符

        Args:
            path: 文件路径
            sample_size: 采样大小（字节）

        Returns:
            是否为二进制文件
        """
        try:
            with open(path, 'rb') as f:
                chunk = f.read(sample_size)

            # 空文件视为文本文件
            if not chunk:
                return False

            # 包含 NULL 字节则为二进制文件
            if b'\x00' in chunk:
                return True

            # 检查非文本字符比例
            text_chars = bytearray({7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x100)) - {0x7f})
            non_text = sum(1 for byte in chunk if byte not in text_chars)

            # 如果超过 30% 是非文本字符，视为二进制
            return non_text / len(chunk) > 0.3
        except (OSError, ValueError):
            return True  # 无法读取时保守处理

# 全局单例
_fs_instance = FileSystemOperations()

def getFsImplementation() -> FileSystemOperations:
    """
    获取文件系统操作实例（单例）

    Returns:
        FileSystemOperations 实例
    """
    return _fs_instance
