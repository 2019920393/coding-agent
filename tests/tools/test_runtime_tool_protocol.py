import importlib

import pytest

from codo.tools.base import Tool as RuntimeTool
from codo.tools_registry import get_all_tools

def test_all_registered_tools_use_active_runtime_base():
    tools = get_all_tools()
    assert tools
    assert all(isinstance(tool, RuntimeTool) for tool in tools)

def test_legacy_tool_protocol_modules_are_gone():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("codo.tool")

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("codo.types.tools")

def test_permission_types_live_only_in_canonical_permissions_module():
    import codo.tools as tools_package
    import codo.tools.types as tool_types

    assert not hasattr(tool_types, "PermissionResult")
    assert not hasattr(tool_types, "PermissionBehavior")
    assert not hasattr(tools_package, "PermissionResult")
    assert not hasattr(tools_package, "PermissionBehavior")
