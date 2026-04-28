"""
权限系统测试

测试权限检查器、规则管理和工具集成。
"""

import pytest
import os
from typing import Dict, Any

from codo.tools.base import Tool, ToolUseContext
from codo.tools.types import ToolResult
from codo.types.permissions import (
    PermissionMode,
    PermissionRuleSource,
    PermissionRuleValue,
    PermissionRule,
    ToolPermissionContext,
    create_passthrough_result,
    create_deny_decision,
    create_ask_decision,
)
from codo.services.tools.permission_rules import (
    parse_permission_rule_value,
    format_permission_rule_value,
    tool_matches_rule,
    get_allow_rules,
    get_deny_rules,
    get_ask_rules,
    tool_always_allowed_rule,
    get_deny_rule_for_tool,
    get_ask_rule_for_tool,
    get_rule_by_contents_for_tool,
)
from codo.services.tools.permission_checker import (
    has_permissions_to_use_tool,
    check_path_safety,
    create_default_permission_context,
)

# ============================================================================
# 测试工具类
# ============================================================================

class MockBaseTool(Tool):
    name = "MockBase"

    @property
    def input_schema(self):
        return None

    async def call(self, args, context, can_use_tool, parent_message, on_progress=None):
        return ToolResult(data="mock result")

    async def description(self, input_data, options):
        return "mock tool"

    async def prompt(self, options):
        return "mock tool"

    def map_tool_result_to_tool_result_block_param(self, content, tool_use_id):
        return {"type": "tool_result", "tool_use_id": tool_use_id, "content": str(content)}

class MockReadTool(MockBaseTool):
    """模拟只读工具"""
    name = "MockRead"

    def is_concurrency_safe(self, input_data: Dict[str, Any]) -> bool:
        return True

    def is_read_only(self, input_data: Dict[str, Any]) -> bool:
        return True

class MockWriteTool(MockBaseTool):
    """模拟写入工具"""
    name = "MockWrite"

    def is_concurrency_safe(self, input_data: Dict[str, Any]) -> bool:
        return False

class MockDangerousTool(MockBaseTool):
    """模拟危险工具（自定义权限检查）"""
    name = "MockDangerous"

    async def check_permissions(self, input_data: Dict[str, Any], context: ToolUseContext):
        """检查危险命令"""
        command = input_data.get("command", "")
        if "rm -rf /" in command:
            return create_deny_decision(
                message="Dangerous command detected: rm -rf /",
            )
        if "sudo" in command:
            return create_ask_decision(
                message="This command requires sudo privileges. Do you want to proceed?",
            )
        return create_passthrough_result()

class ContextAwarePermissionTool(MockBaseTool):
    """验证权限检查链收到的是 ToolUseContext 对象。"""

    name = "ContextAwarePermission"

    def __init__(self):
        self.seen_context_types: list[type] = []

    async def check_permissions(self, input_data: Dict[str, Any], context: ToolUseContext):
        self.seen_context_types.append(type(context))
        return create_passthrough_result()

def make_tool_context(permission_context, cwd: str = "/test") -> ToolUseContext:
    return ToolUseContext.from_dict(
        {
            "cwd": cwd,
            "permission_context": permission_context,
        }
    )

# ============================================================================
# 测试规则解析
# ============================================================================

def test_parse_permission_rule_value():
    """测试规则解析"""
    # 测试简单规则
    rule = parse_permission_rule_value("Bash")
    assert rule.tool_name == "Bash"
    assert rule.rule_content is None

    # 测试带内容的规则
    rule = parse_permission_rule_value("Bash(prefix:npm)")
    assert rule.tool_name == "Bash"
    assert rule.rule_content == "prefix:npm"

    # 测试复杂内容
    rule = parse_permission_rule_value("Bash(npm publish:*)")
    assert rule.tool_name == "Bash"
    assert rule.rule_content == "npm publish:*"

def test_format_permission_rule_value():
    """测试规则格式化"""
    # 测试简单规则
    rule = PermissionRuleValue(tool_name="Bash", rule_content=None)
    assert format_permission_rule_value(rule) == "Bash"

    # 测试带内容的规则
    rule = PermissionRuleValue(tool_name="Bash", rule_content="prefix:npm")
    assert format_permission_rule_value(rule) == "Bash(prefix:npm)"

# ============================================================================
# 测试规则匹配
# ============================================================================

def test_tool_matches_rule():
    """测试工具规则匹配"""
    tool = MockReadTool()

    # 测试匹配整个工具
    rule = PermissionRule(
        source=PermissionRuleSource.USER_SETTINGS,
        rule_behavior="allow",
        rule_value=PermissionRuleValue(tool_name="MockRead", rule_content=None)
    )
    assert tool_matches_rule(tool, rule) is True

    # 测试不匹配（有内容）
    rule = PermissionRule(
        source=PermissionRuleSource.USER_SETTINGS,
        rule_behavior="allow",
        rule_value=PermissionRuleValue(tool_name="MockRead", rule_content="prefix:test")
    )
    assert tool_matches_rule(tool, rule) is False

    # 测试不匹配（工具名称不同）
    rule = PermissionRule(
        source=PermissionRuleSource.USER_SETTINGS,
        rule_behavior="allow",
        rule_value=PermissionRuleValue(tool_name="MockWrite", rule_content=None)
    )
    assert tool_matches_rule(tool, rule) is False

# ============================================================================
# 测试规则查询
# ============================================================================

def test_get_rules():
    """测试规则查询"""
    context = ToolPermissionContext(
        mode=PermissionMode.DEFAULT,
        always_allow_rules={
            PermissionRuleSource.USER_SETTINGS: ["MockRead", "MockWrite"],
            PermissionRuleSource.PROJECT_SETTINGS: [],
            PermissionRuleSource.SESSION: [],
        },
        always_deny_rules={
            PermissionRuleSource.USER_SETTINGS: ["MockDangerous"],
            PermissionRuleSource.PROJECT_SETTINGS: [],
            PermissionRuleSource.SESSION: [],
        },
        always_ask_rules={
            PermissionRuleSource.USER_SETTINGS: [],
            PermissionRuleSource.PROJECT_SETTINGS: [],
            PermissionRuleSource.SESSION: [],
        },
        cwd="/test",
    )

    # 测试获取允许规则
    allow_rules = get_allow_rules(context)
    assert len(allow_rules) == 2
    assert allow_rules[0].rule_value.tool_name == "MockRead"
    assert allow_rules[1].rule_value.tool_name == "MockWrite"

    # 测试获取拒绝规则
    deny_rules = get_deny_rules(context)
    assert len(deny_rules) == 1
    assert deny_rules[0].rule_value.tool_name == "MockDangerous"

def test_tool_always_allowed_rule():
    """测试工具允许规则查询"""
    context = ToolPermissionContext(
        mode=PermissionMode.DEFAULT,
        always_allow_rules={
            PermissionRuleSource.USER_SETTINGS: ["MockRead"],
            PermissionRuleSource.PROJECT_SETTINGS: [],
            PermissionRuleSource.SESSION: [],
        },
        always_deny_rules={
            PermissionRuleSource.USER_SETTINGS: [],
            PermissionRuleSource.PROJECT_SETTINGS: [],
            PermissionRuleSource.SESSION: [],
        },
        always_ask_rules={
            PermissionRuleSource.USER_SETTINGS: [],
            PermissionRuleSource.PROJECT_SETTINGS: [],
            PermissionRuleSource.SESSION: [],
        },
        cwd="/test",
    )

    # 测试允许的工具
    tool = MockReadTool()
    rule = tool_always_allowed_rule(context, tool)
    assert rule is not None
    assert rule.rule_value.tool_name == "MockRead"

    # 测试不允许的工具
    tool = MockWriteTool()
    rule = tool_always_allowed_rule(context, tool)
    assert rule is None

# ============================================================================
# 测试安全检查
# ============================================================================

def test_check_path_safety():
    """测试路径安全检查"""
    cwd = "/home/user/project"

    # 测试安全路径
    result = check_path_safety("/home/user/project/file.txt", cwd)
    assert result is None

    # 测试 .git 目录
    result = check_path_safety("/home/user/project/.git/config", cwd)
    assert result is not None
    assert result.behavior == "ask"
    assert ".git" in result.message

    # 测试 .codo 目录
    result = check_path_safety("/home/user/project/.codo/settings.json", cwd)
    assert result is not None
    assert result.behavior == "ask"

    # 旧的 .claude 目录不应再作为 Codo 的敏感配置目录
    result = check_path_safety("/home/user/project/.claude/notes.txt", cwd)
    assert result is None

    # 测试 .env 文件
    result = check_path_safety("/home/user/project/.env", cwd)
    assert result is not None
    assert result.behavior == "ask"

# ============================================================================
# 测试权限检查器
# ============================================================================

@pytest.mark.asyncio
async def test_has_permissions_bypass_mode():
    """测试 bypassPermissions 模式"""
    tool = MockWriteTool()
    input_data = {"file_path": "/test/file.txt", "content": "test"}

    context = make_tool_context(
        ToolPermissionContext(
            mode=PermissionMode.BYPASS_PERMISSIONS,
            always_allow_rules={
                PermissionRuleSource.USER_SETTINGS: [],
                PermissionRuleSource.PROJECT_SETTINGS: [],
                PermissionRuleSource.SESSION: [],
            },
            always_deny_rules={
                PermissionRuleSource.USER_SETTINGS: [],
                PermissionRuleSource.PROJECT_SETTINGS: [],
                PermissionRuleSource.SESSION: [],
            },
            always_ask_rules={
                PermissionRuleSource.USER_SETTINGS: [],
                PermissionRuleSource.PROJECT_SETTINGS: [],
                PermissionRuleSource.SESSION: [],
            },
            cwd="/test",
        )
    )

    decision = await has_permissions_to_use_tool(tool, input_data, context)
    assert decision.behavior == "allow"
    assert decision.decision_reason.type == "mode"

@pytest.mark.asyncio
async def test_has_permissions_coerces_raw_dict_to_tool_use_context():
    """权限检查入口即使收到原始 dict，也应统一包装成 ToolUseContext。"""
    tool = ContextAwarePermissionTool()
    raw_context = {
        "cwd": "/test",
        "permission_context": ToolPermissionContext(
            mode=PermissionMode.BYPASS_PERMISSIONS,
            always_allow_rules={
                PermissionRuleSource.USER_SETTINGS: [],
                PermissionRuleSource.PROJECT_SETTINGS: [],
                PermissionRuleSource.SESSION: [],
            },
            always_deny_rules={
                PermissionRuleSource.USER_SETTINGS: [],
                PermissionRuleSource.PROJECT_SETTINGS: [],
                PermissionRuleSource.SESSION: [],
            },
            always_ask_rules={
                PermissionRuleSource.USER_SETTINGS: [],
                PermissionRuleSource.PROJECT_SETTINGS: [],
                PermissionRuleSource.SESSION: [],
            },
            cwd="/test",
        ),
    }

    decision = await has_permissions_to_use_tool(tool, {}, raw_context)

    assert decision.behavior == "allow"
    assert tool.seen_context_types == [ToolUseContext]

@pytest.mark.asyncio
async def test_has_permissions_deny_rule():
    """测试拒绝规则"""
    tool = MockWriteTool()
    input_data = {"file_path": "/test/file.txt", "content": "test"}

    context = make_tool_context(
        ToolPermissionContext(
            mode=PermissionMode.DEFAULT,
            always_allow_rules={
                PermissionRuleSource.USER_SETTINGS: [],
                PermissionRuleSource.PROJECT_SETTINGS: [],
                PermissionRuleSource.SESSION: [],
            },
            always_deny_rules={
                PermissionRuleSource.USER_SETTINGS: ["MockWrite"],
                PermissionRuleSource.PROJECT_SETTINGS: [],
                PermissionRuleSource.SESSION: [],
            },
            always_ask_rules={
                PermissionRuleSource.USER_SETTINGS: [],
                PermissionRuleSource.PROJECT_SETTINGS: [],
                PermissionRuleSource.SESSION: [],
            },
            cwd="/test",
        )
    )

    decision = await has_permissions_to_use_tool(tool, input_data, context)
    assert decision.behavior == "deny"
    assert decision.decision_reason.type == "rule"

@pytest.mark.asyncio
async def test_has_permissions_allow_rule():
    """测试允许规则"""
    tool = MockWriteTool()
    input_data = {"file_path": "/test/file.txt", "content": "test"}

    context = make_tool_context(
        ToolPermissionContext(
            mode=PermissionMode.DEFAULT,
            always_allow_rules={
                PermissionRuleSource.USER_SETTINGS: ["MockWrite"],
                PermissionRuleSource.PROJECT_SETTINGS: [],
                PermissionRuleSource.SESSION: [],
            },
            always_deny_rules={
                PermissionRuleSource.USER_SETTINGS: [],
                PermissionRuleSource.PROJECT_SETTINGS: [],
                PermissionRuleSource.SESSION: [],
            },
            always_ask_rules={
                PermissionRuleSource.USER_SETTINGS: [],
                PermissionRuleSource.PROJECT_SETTINGS: [],
                PermissionRuleSource.SESSION: [],
            },
            cwd="/test",
        )
    )

    decision = await has_permissions_to_use_tool(tool, input_data, context)
    assert decision.behavior == "allow"
    assert decision.decision_reason.type == "rule"

@pytest.mark.asyncio
async def test_has_permissions_tool_deny():
    """测试工具自定义拒绝"""
    tool = MockDangerousTool()
    input_data = {"command": "rm -rf /"}

    context = make_tool_context(create_default_permission_context("/test"))

    decision = await has_permissions_to_use_tool(tool, input_data, context)
    assert decision.behavior == "deny"
    assert "Dangerous command" in decision.message

@pytest.mark.asyncio
async def test_has_permissions_tool_ask():
    """测试工具自定义询问"""
    tool = MockDangerousTool()
    input_data = {"command": "sudo apt-get update"}

    context = make_tool_context(create_default_permission_context("/test"))

    decision = await has_permissions_to_use_tool(tool, input_data, context)
    assert decision.behavior == "ask"
    assert "sudo" in decision.message

@pytest.mark.asyncio
async def test_has_permissions_default_ask():
    """测试默认询问"""
    tool = MockWriteTool()
    input_data = {"file_path": "/test/file.txt", "content": "test"}

    context = make_tool_context(create_default_permission_context("/test"))

    decision = await has_permissions_to_use_tool(tool, input_data, context)
    assert decision.behavior == "ask"
    assert decision.decision_reason.type == "mode"

# ============================================================================
# 运行测试
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
