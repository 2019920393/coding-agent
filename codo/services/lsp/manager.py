"""
LSP 服务器管理器

管理多个 LSP 服务器实例，根据文件扩展名路由请求
"""

import asyncio
import logging
from typing import Optional, Dict, List, Any
from pathlib import Path

from .client import LSPClient
from .types import LSPServerConfig, DEFAULT_LSP_SERVERS

logger = logging.getLogger(__name__)

class LSPServerManager:
    """LSP 服务器管理器

    管理多个 LSP 服务器实例，根据文件扩展名路由请求
    """

    def __init__(self, server_configs: Optional[List[LSPServerConfig]] = None):
        """初始化管理器

        Args:
            server_configs: 服务器配置列表，默认使用 DEFAULT_LSP_SERVERS
        """
        self._server_configs = server_configs or DEFAULT_LSP_SERVERS
        self._servers: Dict[str, LSPClient] = {}
        self._extension_map: Dict[str, List[str]] = {}
        self._opened_files: Dict[str, str] = {}  # file_path -> server_name
        self._initialized = False

    async def initialize(self, cwd: Optional[str] = None) -> None:
        """初始化管理器

        Args:
            cwd: 工作目录
        """
        if self._initialized:
            return

        # 构建扩展名 -> 服务器映射
        for config in self._server_configs:
            if config.disabled:
                continue

            # 映射文件扩展名到服务器
            for ext in config.file_extensions:
                normalized_ext = ext.lower()
                if normalized_ext not in self._extension_map:
                    self._extension_map[normalized_ext] = []
                self._extension_map[normalized_ext].append(config.name)

            # 创建服务器实例（延迟启动）
            client = LSPClient(
                server_config=config,
                on_crash=lambda e: self._handle_server_crash(config.name, e),
            )
            self._servers[config.name] = client

        self._initialized = True
        logger.info(f"LSP manager initialized with {len(self._servers)} servers")

    def _handle_server_crash(self, server_name: str, error: Exception):
        """处理服务器崩溃

        Args:
            server_name: 服务器名称
            error: 错误信息
        """
        logger.error(f"LSP server {server_name} crashed: {error}")

        # 清理崩溃服务器的状态
        if server_name in self._servers:
            # 移除该服务器打开的所有文件
            files_to_remove = [
                file_path
                for file_path, srv_name in self._opened_files.items()
                if srv_name == server_name
            ]
            for file_path in files_to_remove:
                del self._opened_files[file_path]

    async def shutdown(self) -> None:
        """关闭所有服务器"""
        if not self._initialized:
            return

        # 停止所有服务器
        stop_tasks = []
        for server_name, client in self._servers.items():
            if client.is_initialized:
                stop_tasks.append(self._stop_server(server_name, client))

        if stop_tasks:
            results = await asyncio.gather(*stop_tasks, return_exceptions=True)

            # 记录错误
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    server_name = list(self._servers.keys())[i]
                    logger.error(f"Failed to stop LSP server {server_name}: {result}")

        # 清理状态
        self._servers.clear()
        self._extension_map.clear()
        self._opened_files.clear()
        self._initialized = False

        logger.info("LSP manager shut down")

    async def _stop_server(self, server_name: str, client: LSPClient) -> None:
        """停止单个服务器

        Args:
            server_name: 服务器名称
            client: 客户端实例
        """
        try:
            await client.stop()
        except Exception as e:
            logger.error(f"Error stopping server {server_name}: {e}")
            raise

    def get_server_for_file(self, file_path: str) -> Optional[LSPClient]:
        """获取文件对应的 LSP 服务器

        Args:
            file_path: 文件路径

        Returns:
            LSP 客户端，如果没有对应服务器则返回 None
        """
        ext = Path(file_path).suffix.lower()
        server_names = self._extension_map.get(ext, [])

        if not server_names:
            return None

        # 使用第一个服务器（可以后续添加优先级）
        server_name = server_names[0]
        return self._servers.get(server_name)

    def get_language_id_for_file(self, file_path: str) -> Optional[str]:
        """获取文件的语言 ID

        Args:
            file_path: 文件路径

        Returns:
            语言 ID，如果没有对应服务器则返回 None
        """
        ext = Path(file_path).suffix.lower()
        server_names = self._extension_map.get(ext, [])

        if not server_names:
            return None

        server_name = server_names[0]
        client = self._servers.get(server_name)

        if not client:
            return None

        # 从配置中查找语言 ID
        for lang_id in client.server_config.language_ids:
            return lang_id

        return None

    async def ensure_server_started(
        self, file_path: str, cwd: Optional[str] = None
    ) -> Optional[LSPClient]:
        """确保文件对应的服务器已启动

        Args:
            file_path: 文件路径
            cwd: 工作目录

        Returns:
            LSP 客户端，如果没有对应服务器则返回 None
        """
        client = self.get_server_for_file(file_path)

        if not client:
            return None

        # 如果服务器未启动，启动它
        if not client.is_initialized:
            try:
                await client.start(cwd=cwd)
                await client.initialize(
                    workspace_folders=[cwd] if cwd else None
                )
                logger.info(f"Started LSP server {client.server_config.name}")
            except Exception as e:
                logger.error(
                    f"Failed to start LSP server {client.server_config.name}: {e}"
                )
                return None

        return client

    async def send_request(
        self,
        file_path: str,
        method: str,
        params: Any,
        cwd: Optional[str] = None,
    ) -> Optional[Any]:
        """发送请求到文件对应的 LSP 服务器

        Args:
            file_path: 文件路径
            method: LSP 方法名
            params: 请求参数
            cwd: 工作目录

        Returns:
            响应结果，如果没有对应服务器则返回 None
        """
        client = await self.ensure_server_started(file_path, cwd=cwd)

        if not client:
            return None

        try:
            result = await client.send_request(method, params)
            return result
        except Exception as e:
            logger.error(f"LSP request {method} failed for {file_path}: {e}")
            return None

    async def open_file(
        self, file_path: str, content: str, cwd: Optional[str] = None
    ) -> None:
        """打开文件

        Args:
            file_path: 文件路径
            content: 文件内容
            cwd: 工作目录
        """
        # 检查文件是否已打开
        if file_path in self._opened_files:
            return

        client = await self.ensure_server_started(file_path, cwd=cwd)

        if not client:
            return

        language_id = self.get_language_id_for_file(file_path)

        if not language_id:
            return

        try:
            await client.open_file(file_path, content, language_id)
            self._opened_files[file_path] = client.server_config.name
        except Exception as e:
            logger.error(f"Failed to open file {file_path}: {e}")

    async def close_file(self, file_path: str) -> None:
        """关闭文件

        Args:
            file_path: 文件路径
        """
        server_name = self._opened_files.get(file_path)

        if not server_name:
            return

        client = self._servers.get(server_name)

        if not client:
            return

        try:
            await client.close_file(file_path)
            del self._opened_files[file_path]
        except Exception as e:
            logger.error(f"Failed to close file {file_path}: {e}")

    def is_file_open(self, file_path: str) -> bool:
        """检查文件是否已打开

        Args:
            file_path: 文件路径

        Returns:
            是否已打开
        """
        return file_path in self._opened_files

    def get_all_servers(self) -> Dict[str, LSPClient]:
        """获取所有服务器实例

        Returns:
            服务器名称 -> 客户端映射
        """
        return self._servers.copy()

    def get_supported_extensions(self) -> List[str]:
        """获取所有支持的文件扩展名

        Returns:
            扩展名列表
        """
        return list(self._extension_map.keys())
