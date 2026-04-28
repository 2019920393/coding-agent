"""
LSP 客户端实现

基于 pygls 的 LSP 客户端，用于与 LSP 服务器通信
"""

import asyncio
import logging
from typing import Optional, Dict, Any, Callable, List
from pathlib import Path
import subprocess

from lsprotocol.types import (
    InitializeParams,
    InitializeResult,
    InitializedParams,
    ClientCapabilities,
    TextDocumentItem,
    DidOpenTextDocumentParams,
    DidCloseTextDocumentParams,
    TextDocumentIdentifier,
    Position,
    WorkspaceFolder,
)
from pygls.client import JsonRPCClient
from pygls.protocol import JsonRPCProtocol

from .types import LSPServerConfig, LSPRequest, LSPResponse

logger = logging.getLogger(__name__)

class LSPClient:
    """LSP 客户端

    管理与单个 LSP 服务器的通信
    """

    def __init__(
        self,
        server_config: LSPServerConfig,
        on_crash: Optional[Callable[[Exception], None]] = None,
    ):
        """初始化 LSP 客户端

        Args:
            server_config: 服务器配置
            on_crash: 服务器崩溃回调
        """
        self.server_config = server_config
        self.on_crash = on_crash

        # 状态
        self._process: Optional[subprocess.Popen] = None
        self._client: Optional[JsonRPCClient] = None
        self._protocol: Optional[JsonRPCProtocol] = None
        self._capabilities: Optional[Dict[str, Any]] = None
        self._is_initialized = False
        self._is_stopping = False
        self._start_failed = False
        self._start_error: Optional[Exception] = None

        # 已打开的文件
        self._opened_files: Dict[str, int] = {}  # file_path -> version

        # 待处理的通知和请求处理器
        self._pending_notification_handlers: List[tuple[str, Callable]] = []
        self._pending_request_handlers: List[tuple[str, Callable]] = []

    @property
    def capabilities(self) -> Optional[Dict[str, Any]]:
        """服务器能力"""
        return self._capabilities

    @property
    def is_initialized(self) -> bool:
        """是否已初始化"""
        return self._is_initialized

    @property
    def opened_files(self) -> Dict[str, int]:
        """已打开的文件"""
        return self._opened_files.copy()

    def _check_start_failed(self):
        """检查启动是否失败"""
        if self._start_failed:
            error = self._start_error or Exception(
                f"LSP server {self.server_config.name} failed to start"
            )
            raise error

    async def start(self, cwd: Optional[str] = None) -> None:
        """启动 LSP 服务器

        Args:
            cwd: 工作目录

        Raises:
            Exception: 启动失败
        """
        try:
            # 构建命令
            command = [self.server_config.command] + self.server_config.args

            # 准备环境变量
            env = dict(self.server_config.env or {})

            # 启动进程
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=cwd,
                # Windows 下隐藏控制台窗口
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
            )

            # 等待进程启动
            await asyncio.sleep(0.1)

            # 检查进程是否启动成功
            if self._process.poll() is not None:
                raise Exception(f"LSP server {self.server_config.name} exited immediately")

            # 创建 JSON-RPC 客户端
            self._protocol = JsonRPCProtocol()
            self._client = JsonRPCClient(protocol=self._protocol)

            # 连接到进程的 stdin/stdout
            self._client.start_io(self._process.stdout, self._process.stdin)

            # 监听 stderr
            if self._process.stderr:
                asyncio.create_task(self._monitor_stderr())

            # 监听进程退出
            asyncio.create_task(self._monitor_process())

            # 注册待处理的处理器
            for method, handler in self._pending_notification_handlers:
                self._protocol.fm.feature(method)(handler)
            self._pending_notification_handlers.clear()

            for method, handler in self._pending_request_handlers:
                self._protocol.fm.feature(method)(handler)
            self._pending_request_handlers.clear()

            logger.info(f"LSP server {self.server_config.name} started")

        except Exception as e:
            self._start_failed = True
            self._start_error = e
            logger.error(f"Failed to start LSP server {self.server_config.name}: {e}")
            raise

    async def _monitor_stderr(self):
        """监听服务器 stderr 输出"""
        if not self._process or not self._process.stderr:
            return

        try:
            while True:
                line = await asyncio.get_event_loop().run_in_executor(
                    None, self._process.stderr.readline
                )
                if not line:
                    break

                output = line.decode('utf-8', errors='ignore').strip()
                if output:
                    logger.debug(f"[LSP SERVER {self.server_config.name}] {output}")
        except Exception as e:
            logger.error(f"Error monitoring stderr: {e}")

    async def _monitor_process(self):
        """监听进程退出"""
        if not self._process:
            return

        try:
            returncode = await asyncio.get_event_loop().run_in_executor(
                None, self._process.wait
            )

            if not self._is_stopping and returncode != 0:
                error = Exception(
                    f"LSP server {self.server_config.name} crashed with exit code {returncode}"
                )
                logger.error(str(error))

                if self.on_crash:
                    self.on_crash(error)
        except Exception as e:
            logger.error(f"Error monitoring process: {e}")

    async def initialize(
        self,
        workspace_folders: Optional[List[str]] = None,
        initialization_options: Optional[Dict[str, Any]] = None,
    ) -> InitializeResult:
        """初始化 LSP 服务器

        Args:
            workspace_folders: 工作区文件夹
            initialization_options: 初始化选项

        Returns:
            初始化结果
        """
        self._check_start_failed()

        if not self._client:
            raise Exception("LSP client not started")

        # 准备工作区文件夹
        folders = workspace_folders or self.server_config.workspace_folders or []
        workspace_folders_param = [
            WorkspaceFolder(uri=f"file://{Path(f).as_posix()}", name=Path(f).name)
            for f in folders
        ] if folders else None

        # 准备初始化参数
        params = InitializeParams(
            process_id=None,
            root_uri=workspace_folders_param[0].uri if workspace_folders_param else None,
            capabilities=ClientCapabilities(),
            workspace_folders=workspace_folders_param,
            initialization_options=initialization_options or self.server_config.initialization_options,
        )

        # 发送初始化请求
        result = await self.send_request("initialize", params)

        # 保存服务器能力
        if result and hasattr(result, 'capabilities'):
            self._capabilities = result.capabilities

        # 发送 initialized 通知
        await self.send_notification("initialized", InitializedParams())

        self._is_initialized = True
        logger.info(f"LSP server {self.server_config.name} initialized")

        return result

    async def send_request(self, method: str, params: Any) -> Any:
        """发送请求

        Args:
            method: 方法名
            params: 参数

        Returns:
            响应结果
        """
        self._check_start_failed()

        if not self._client:
            raise Exception("LSP client not started")

        try:
            result = await self._client.protocol.send_request_async(method, params)
            return result
        except Exception as e:
            logger.error(f"LSP request {method} failed: {e}")
            raise

    async def send_notification(self, method: str, params: Any) -> None:
        """发送通知

        Args:
            method: 方法名
            params: 参数
        """
        self._check_start_failed()

        if not self._client:
            raise Exception("LSP client not started")

        try:
            self._client.protocol.notify(method, params)
        except Exception as e:
            logger.error(f"LSP notification {method} failed: {e}")
            raise

    def on_notification(self, method: str, handler: Callable[[Any], None]) -> None:
        """注册通知处理器

        Args:
            method: 方法名
            handler: 处理器
        """
        if self._protocol:
            self._protocol.fm.feature(method)(handler)
        else:
            self._pending_notification_handlers.append((method, handler))

    def on_request(self, method: str, handler: Callable[[Any], Any]) -> None:
        """注册请求处理器

        Args:
            method: 方法名
            handler: 处理器
        """
        if self._protocol:
            self._protocol.fm.feature(method)(handler)
        else:
            self._pending_request_handlers.append((method, handler))

    async def open_file(self, file_path: str, content: str, language_id: str) -> None:
        """打开文件

        Args:
            file_path: 文件路径
            content: 文件内容
            language_id: 语言 ID
        """
        if file_path in self._opened_files:
            return

        uri = f"file://{Path(file_path).as_posix()}"
        version = 1

        params = DidOpenTextDocumentParams(
            text_document=TextDocumentItem(
                uri=uri,
                language_id=language_id,
                version=version,
                text=content,
            )
        )

        await self.send_notification("textDocument/didOpen", params)
        self._opened_files[file_path] = version

        logger.debug(f"Opened file: {file_path}")

    async def close_file(self, file_path: str) -> None:
        """关闭文件

        Args:
            file_path: 文件路径
        """
        if file_path not in self._opened_files:
            return

        uri = f"file://{Path(file_path).as_posix()}"

        params = DidCloseTextDocumentParams(
            text_document=TextDocumentIdentifier(uri=uri)
        )

        await self.send_notification("textDocument/didClose", params)
        del self._opened_files[file_path]

        logger.debug(f"Closed file: {file_path}")

    async def stop(self) -> None:
        """停止 LSP 服务器"""
        self._is_stopping = True

        try:
            # 发送 shutdown 请求
            if self._client and self._is_initialized:
                try:
                    await self.send_request("shutdown", None)
                    await self.send_notification("exit", None)
                except Exception as e:
                    logger.warning(f"Error during shutdown: {e}")

            # 关闭客户端
            if self._client:
                self._client.stop()

            # 终止进程
            if self._process:
                try:
                    self._process.terminate()
                    await asyncio.wait_for(
                        asyncio.get_event_loop().run_in_executor(None, self._process.wait),
                        timeout=5.0
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"LSP server {self.server_config.name} did not terminate, killing")
                    self._process.kill()
                except Exception as e:
                    logger.error(f"Error terminating process: {e}")

            logger.info(f"LSP server {self.server_config.name} stopped")

        finally:
            self._is_stopping = False
            self._is_initialized = False
            self._capabilities = None
            self._opened_files.clear()
