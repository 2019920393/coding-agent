"""
MCP 配置管理

[Workflow]
1. 读取配置文件（支持多个作用域：local/user/project）
2. 解析服务器配置
3. 保存和更新配置
4. 验证配置有效性
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field, field_validator, ConfigDict

from .types import MCPTransportType

class MCPServerConfig(BaseModel):
    """MCP 服务器配置"""

    command: str = Field(..., description="启动命令（stdio 模式）")
    args: List[str] = Field(default_factory=list, description="命令参数")
    env: Dict[str, str] = Field(default_factory=dict, description="环境变量")
    transport: MCPTransportType = Field(default=MCPTransportType.STDIO, description="传输类型")
    url: Optional[str] = Field(default=None, description="服务器 URL（SSE/HTTP/WS 模式）")
    disabled: bool = Field(default=False, description="是否禁用")

    @field_validator("command")
    @classmethod
    def validate_command(cls, v):
        """验证命令不为空"""
        if not v or not v.strip():
            raise ValueError("command 不能为空")
        return v.strip()

class MCPConfig(BaseModel):
    """MCP 配置"""

    model_config = ConfigDict(populate_by_name=True)

    mcpServers: Dict[str, MCPServerConfig] = Field(
        default_factory=dict,
        description="MCP 服务器配置字典"
    )

class MCPConfigManager:
    """
    MCP 配置管理器

    [Workflow]
    1. 从配置文件加载 MCP 服务器配置
    2. 支持多个配置作用域（优先级：local > user > project）
    3. 提供配置的读取、保存、更新接口
    """

    def __init__(self, cwd: str):
        """
        初始化配置管理器

        Args:
            cwd: 当前工作目录
        """
        self.cwd = Path(cwd)
        self._config: Optional[MCPConfig] = None

    def get_config_paths(self) -> List[Path]:
        """
        获取配置文件路径列表（按优先级排序）

        Returns:
            配置文件路径列表
        """
        paths = []

        # 1. Local 配置（当前目录）
        local_config = self.cwd / ".codo" / "mcp.json"
        if local_config.exists():
            paths.append(local_config)

        # 2. User 配置（用户主目录）
        user_config = Path.home() / ".codo" / "mcp.json"
        if user_config.exists():
            paths.append(user_config)

        # 3. Project 配置（项目根目录，通过 .git 判断）
        project_root = self._find_project_root()
        if project_root:
            project_config = project_root / ".codo" / "mcp.json"
            if project_config.exists():
                paths.append(project_config)

        return paths

    def _find_project_root(self) -> Optional[Path]:
        """
        查找项目根目录（包含 .git 的目录）

        Returns:
            项目根目录路径，如果未找到返回 None
        """
        current = self.cwd
        while current != current.parent:
            if (current / ".git").exists():
                return current
            current = current.parent
        return None

    def load_config(self) -> MCPConfig:
        """
        加载 MCP 配置（合并多个作用域）

        Returns:
            合并后的 MCP 配置
        """
        if self._config is not None:
            return self._config

        merged_servers: Dict[str, MCPServerConfig] = {}

        # 按优先级反向加载（低优先级先加载，高优先级覆盖）
        config_paths = list(reversed(self.get_config_paths()))

        for config_path in config_paths:
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    config = MCPConfig(**data)
                    # 合并服务器配置
                    merged_servers.update(config.mcpServers)
            except Exception as e:
                # 配置文件解析失败，跳过
                print(f"警告：无法加载配置文件 {config_path}: {e}")
                continue

        self._config = MCPConfig(mcpServers=merged_servers)
        return self._config

    def get_server_config(self, name: str) -> Optional[MCPServerConfig]:
        """
        获取指定服务器的配置

        Args:
            name: 服务器名称

        Returns:
            服务器配置，如果不存在返回 None
        """
        config = self.load_config()
        return config.mcpServers.get(name)

    def list_servers(self) -> Dict[str, MCPServerConfig]:
        """
        列出所有服务器配置

        Returns:
            服务器配置字典
        """
        config = self.load_config()
        return config.mcpServers

    def save_config(self, config: MCPConfig, scope: str = "user") -> None:
        """
        保存配置到指定作用域

        Args:
            config: MCP 配置
            scope: 作用域（local/user/project）
        """
        if scope == "local":
            config_path = self.cwd / ".codo" / "mcp.json"
        elif scope == "user":
            config_path = Path.home() / ".codo" / "mcp.json"
        elif scope == "project":
            project_root = self._find_project_root()
            if not project_root:
                raise ValueError("未找到项目根目录")
            config_path = project_root / ".codo" / "mcp.json"
        else:
            raise ValueError(f"无效的作用域: {scope}")

        # 确保目录存在
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # 保存配置
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config.model_dump(), f, indent=2, ensure_ascii=False)

        # 清除缓存
        self._config = None

    def add_server(
        self,
        name: str,
        command: str,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        scope: str = "user"
    ) -> None:
        """
        添加 MCP 服务器配置

        Args:
            name: 服务器名称
            command: 启动命令
            args: 命令参数
            env: 环境变量
            scope: 保存作用域
        """
        config = self.load_config()

        server_config = MCPServerConfig(
            command=command,
            args=args or [],
            env=env or {}
        )

        config.mcpServers[name] = server_config
        self.save_config(config, scope)

    def remove_server(self, name: str, scope: str = "user") -> None:
        """
        移除 MCP 服务器配置

        Args:
            name: 服务器名称
            scope: 作用域
        """
        config = self.load_config()

        if name in config.mcpServers:
            del config.mcpServers[name]
            self.save_config(config, scope)
