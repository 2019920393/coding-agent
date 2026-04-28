"""MCP client manager tests."""

from __future__ import annotations

import pytest

from codo.services.mcp.client import MCPClientManager
from codo.services.mcp.config import MCPServerConfig

class DummyConfigManager:
    def __init__(self) -> None:
        self.config = MCPServerConfig(command="dummy-server", args=["--stdio"])

    def get_server_config(self, name: str) -> MCPServerConfig | None:
        if name == "filesystem":
            return self.config
        return None

class _FakeToolsResult:
    def __init__(self, names: list[str]) -> None:
        self.tools = [type("Tool", (), {"name": name, "description": f"desc:{name}", "inputSchema": {}}) for name in names]

class _FakeResourcesResult:
    def __init__(self, names: list[str]) -> None:
        self.resources = [
            type("Resource", (), {"uri": f"file:///{name}", "name": name, "description": f"desc:{name}", "mimeType": "text/plain"})
            for name in names
        ]

class _FakeCapabilities:
    tools = True
    resources = True

class _State:
    def __init__(self) -> None:
        self.closed = False

class _FakeStdioContext:
    def __init__(self, state: _State) -> None:
        self.state = state

    async def __aenter__(self):
        return object(), object()

    async def __aexit__(self, exc_type, exc, tb):
        self.state.closed = True
        return False

class _FakeClientSession:
    def __init__(self, read, write, state: _State) -> None:
        self._state = state

    async def initialize(self) -> None:
        return None

    def get_server_capabilities(self):
        return _FakeCapabilities()

    async def list_tools(self):
        if self._state.closed:
            raise RuntimeError("connection already closed")
        return _FakeToolsResult(["read_file"])

    async def list_resources(self):
        if self._state.closed:
            raise RuntimeError("connection already closed")
        return _FakeResourcesResult(["README"])

@pytest.mark.asyncio
async def test_connect_keeps_stdio_session_alive_until_disconnect(monkeypatch):
    state = _State()

    def fake_stdio_client(params):
        return _FakeStdioContext(state)

    def fake_client_session(read, write):
        return _FakeClientSession(read, write, state)

    monkeypatch.setattr("codo.services.mcp.client.stdio_client", fake_stdio_client)
    monkeypatch.setattr("codo.services.mcp.client.ClientSession", fake_client_session)

    manager = MCPClientManager(DummyConfigManager())

    connected = await manager.connect("filesystem")

    assert connected is True
    assert state.closed is False

    tools = await manager.list_tools("filesystem")
    resources = await manager.list_resources("filesystem")

    assert [tool.name for tool in tools] == ["read_file"]
    assert [resource.name for resource in resources] == ["README"]

    await manager.disconnect("filesystem")

    assert state.closed is True
