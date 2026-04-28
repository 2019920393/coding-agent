"""
MCP 配置管理测试
"""

import json
import pytest
from pathlib import Path
from codo.services.mcp.config import MCPConfigManager, MCPServerConfig, MCPConfig
from codo.services.mcp.types import MCPTransportType

@pytest.fixture
def temp_config_dir(tmp_path):
    """创建临时配置目录"""
    config_dir = tmp_path / ".codo"
    config_dir.mkdir()
    return tmp_path

@pytest.fixture
def sample_config():
    """示例配置"""
    return {
        "mcpServers": {
            "test-server": {
                "command": "node",
                "args": ["server.js"],
                "env": {"NODE_ENV": "production"},
                "transport": "stdio"
            }
        }
    }

def test_load_empty_config(temp_config_dir):
    """测试加载空配置"""
    manager = MCPConfigManager(str(temp_config_dir))
    config = manager.load_config()

    assert isinstance(config, MCPConfig)
    assert len(config.mcpServers) == 0

def test_load_config_from_file(temp_config_dir, sample_config):
    """测试从文件加载配置"""
    # 创建配置文件
    config_file = temp_config_dir / ".codo" / "mcp.json"
    with open(config_file, "w") as f:
        json.dump(sample_config, f)

    manager = MCPConfigManager(str(temp_config_dir))
    config = manager.load_config()

    assert len(config.mcpServers) == 1
    assert "test-server" in config.mcpServers

    server = config.mcpServers["test-server"]
    assert server.command == "node"
    assert server.args == ["server.js"]
    assert server.env == {"NODE_ENV": "production"}
    assert server.transport == MCPTransportType.STDIO

def test_get_server_config(temp_config_dir, sample_config):
    """测试获取服务器配置"""
    config_file = temp_config_dir / ".codo" / "mcp.json"
    with open(config_file, "w") as f:
        json.dump(sample_config, f)

    manager = MCPConfigManager(str(temp_config_dir))
    server = manager.get_server_config("test-server")

    assert server is not None
    assert server.command == "node"

    # 测试不存在的服务器
    server = manager.get_server_config("non-existent")
    assert server is None

def test_list_servers(temp_config_dir, sample_config):
    """测试列出所有服务器"""
    config_file = temp_config_dir / ".codo" / "mcp.json"
    with open(config_file, "w") as f:
        json.dump(sample_config, f)

    manager = MCPConfigManager(str(temp_config_dir))
    servers = manager.list_servers()

    assert len(servers) == 1
    assert "test-server" in servers

def test_save_config(temp_config_dir):
    """测试保存配置"""
    manager = MCPConfigManager(str(temp_config_dir))

    config = MCPConfig(
        mcpServers={
            "new-server": MCPServerConfig(
                command="python",
                args=["-m", "server"],
                env={"DEBUG": "1"}
            )
        }
    )

    manager.save_config(config, scope="local")

    # 验证文件已创建
    config_file = temp_config_dir / ".codo" / "mcp.json"
    assert config_file.exists()

    # 重新加载验证
    manager._config = None  # 清除缓存
    loaded_config = manager.load_config()
    assert "new-server" in loaded_config.mcpServers

def test_add_server(temp_config_dir):
    """测试添加服务器"""
    manager = MCPConfigManager(str(temp_config_dir))

    manager.add_server(
        name="my-server",
        command="node",
        args=["index.js"],
        env={"PORT": "3000"},
        scope="local"
    )

    # 验证已添加
    server = manager.get_server_config("my-server")
    assert server is not None
    assert server.command == "node"
    assert server.args == ["index.js"]
    assert server.env == {"PORT": "3000"}

def test_remove_server(temp_config_dir, sample_config):
    """测试移除服务器"""
    config_file = temp_config_dir / ".codo" / "mcp.json"
    with open(config_file, "w") as f:
        json.dump(sample_config, f)

    manager = MCPConfigManager(str(temp_config_dir))

    # 移除服务器
    manager.remove_server("test-server", scope="local")

    # 验证已移除
    server = manager.get_server_config("test-server")
    assert server is None

def test_server_config_validation():
    """测试服务器配置验证"""
    # 有效配置
    config = MCPServerConfig(command="node", args=["server.js"])
    assert config.command == "node"

    # 无效配置（空命令）
    with pytest.raises(ValueError):
        MCPServerConfig(command="", args=[])

    with pytest.raises(ValueError):
        MCPServerConfig(command="   ", args=[])

def test_config_merge_priority(tmp_path):
    """测试配置合并优先级（local > user > project）"""
    # 创建多个配置文件
    local_dir = tmp_path / "local"
    local_dir.mkdir()
    local_config_dir = local_dir / ".codo"
    local_config_dir.mkdir()

    # Local 配置（高优先级）
    local_config = {
        "mcpServers": {
            "server1": {"command": "local-cmd", "args": []},
            "server2": {"command": "local-cmd2", "args": []}
        }
    }
    with open(local_config_dir / "mcp.json", "w") as f:
        json.dump(local_config, f)

    # User 配置（低优先级）
    user_config_dir = Path.home() / ".codo"
    user_config_dir.mkdir(exist_ok=True)
    user_config = {
        "mcpServers": {
            "server1": {"command": "user-cmd", "args": []},
            "server3": {"command": "user-cmd3", "args": []}
        }
    }
    user_config_file = user_config_dir / "mcp.json"

    # 保存原有配置（如果存在）
    original_user_config = None
    if user_config_file.exists():
        with open(user_config_file, "r") as f:
            original_user_config = f.read()

    try:
        with open(user_config_file, "w") as f:
            json.dump(user_config, f)

        manager = MCPConfigManager(str(local_dir))
        config = manager.load_config()

        # server1 应该使用 local 配置（高优先级）
        assert config.mcpServers["server1"].command == "local-cmd"

        # server2 只在 local 中
        assert config.mcpServers["server2"].command == "local-cmd2"

        # server3 只在 user 中
        assert config.mcpServers["server3"].command == "user-cmd3"

    finally:
        # 恢复原有配置
        if original_user_config is not None:
            with open(user_config_file, "w") as f:
                f.write(original_user_config)
        elif user_config_file.exists():
            user_config_file.unlink()
