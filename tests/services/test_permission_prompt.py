"""
Tests for permission prompt UI and orchestration integration.

Covers:
- format_tool_info: formatting for all tool types
- prompt_permission: Desktop async wrapper
- apply_session_allow_rule: session rule persistence
- orchestration ask→interactive integration
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codo.services.tools.permission_prompt import (
    PermissionChoice,
    apply_session_allow_rule,
    format_tool_info,
)
from codo.types.runtime import InteractionRequest


class _PermissionBroker:
    def __init__(self, choice: PermissionChoice) -> None:
        self.choice = choice
        self.request_payload: InteractionRequest | None = None

    async def request(self, request: InteractionRequest) -> str:
        self.request_payload = request
        return self.choice.value


class TestFormatToolInfo:
    def test_bash_with_description(self):
        result = format_tool_info("Bash", {"command": "ls -la", "description": "List files"})
        assert "List files" in result
        assert "$ ls -la" in result

    def test_bash_without_description(self):
        result = format_tool_info("Bash", {"command": "pwd"})
        assert "$ pwd" in result
        assert "Bash:" in result

    def test_write(self):
        result = format_tool_info("Write", {
            "file_path": "/tmp/test.py",
            "content": "print('hello')\nprint('world')",
        })
        assert "/tmp/test.py" in result
        assert "2 lines" in result

    def test_write_long_content_truncated(self):
        content = "x" * 300
        result = format_tool_info("Write", {
            "file_path": "/tmp/big.py",
            "content": content,
        })
        assert "..." in result

    def test_edit(self):
        result = format_tool_info("Edit", {
            "file_path": "/tmp/test.py",
            "old_string": "old code",
            "new_string": "new code",
        })
        assert "/tmp/test.py" in result
        assert "- old code" in result
        assert "+ new code" in result

    def test_edit_long_strings_truncated(self):
        old = "a" * 100
        new = "b" * 100
        result = format_tool_info("Edit", {
            "file_path": "/tmp/test.py",
            "old_string": old,
            "new_string": new,
        })
        assert "..." in result

    def test_read(self):
        result = format_tool_info("Read", {"file_path": "/tmp/test.py"})
        assert "Read: /tmp/test.py" == result

    def test_glob(self):
        result = format_tool_info("Glob", {"pattern": "**/*.py"})
        assert "Glob: **/*.py" == result

    def test_grep(self):
        result = format_tool_info("Grep", {"pattern": "TODO", "path": "src/"})
        assert "Grep: /TODO/ in src/" == result

    def test_grep_default_path(self):
        result = format_tool_info("Grep", {"pattern": "TODO"})
        assert "Grep: /TODO/ in ." == result

    def test_generic_tool(self):
        result = format_tool_info("CustomTool", {"key": "value"})
        assert "CustomTool:" in result
        assert "value" in result

    def test_generic_tool_long_json_truncated(self):
        result = format_tool_info("CustomTool", {"data": "x" * 300})
        assert "..." in result

    def test_generic_tool_non_serializable(self):
        class Unserializable:
            pass

        result = format_tool_info("CustomTool", {"obj": Unserializable()})
        assert "not serializable" in result

class TestApplySessionAllowRule:
    def test_adds_rule(self):
        from codo.types.permissions import PermissionRuleSource

        ctx = MagicMock()
        ctx.always_allow_rules = {}

        apply_session_allow_rule(ctx, "Bash")

        assert PermissionRuleSource.SESSION in ctx.always_allow_rules
        assert "Bash" in ctx.always_allow_rules[PermissionRuleSource.SESSION]

    def test_does_not_duplicate(self):
        from codo.types.permissions import PermissionRuleSource

        ctx = MagicMock()
        ctx.always_allow_rules = {
            PermissionRuleSource.SESSION: ["Bash"],
        }

        apply_session_allow_rule(ctx, "Bash")

        assert ctx.always_allow_rules[PermissionRuleSource.SESSION].count("Bash") == 1

    def test_appends_to_existing(self):
        from codo.types.permissions import PermissionRuleSource

        ctx = MagicMock()
        ctx.always_allow_rules = {
            PermissionRuleSource.SESSION: ["Read"],
        }

        apply_session_allow_rule(ctx, "Bash")

        rules = ctx.always_allow_rules[PermissionRuleSource.SESSION]
        assert "Read" in rules
        assert "Bash" in rules

class TestPermissionChoice:
    def test_values(self):
        assert PermissionChoice.ALLOW_ONCE == "allow_once"
        assert PermissionChoice.ALLOW_ALWAYS == "allow_always"
        assert PermissionChoice.DENY == "deny"
        assert PermissionChoice.ABORT == "abort"

    def test_is_str_enum(self):
        assert isinstance(PermissionChoice.ALLOW_ONCE, str)

class TestOrchestrationPermissionIntegration:
    @pytest.mark.asyncio
    async def test_ask_allow_once_proceeds(self):
        from codo.services.tools.orchestration import execute_single_tool
        from codo.types.orchestration import ExecutionStatus, ToolExecutionTask

        task = ToolExecutionTask(
            tool_use_id="test-1",
            tool_name="Bash",
            tool_input={"command": "echo hello"},
            is_concurrency_safe=False,
        )

        mock_tool = MagicMock()
        mock_tool.name = "Bash"
        mock_tool.requires_permission.return_value = True
        mock_tool.execute = AsyncMock(return_value=MagicMock(data="hello"))
        mock_tool.get_context_modifier.return_value = None

        mock_ask_decision = MagicMock()
        mock_ask_decision.behavior = "ask"
        mock_ask_decision.message = "Needs approval"

        broker = _PermissionBroker(PermissionChoice.ALLOW_ONCE)
        with patch("codo.services.tools.orchestration.find_tool_by_name", return_value=mock_tool), \
             patch("codo.services.tools.permission_checker.has_permissions_to_use_tool", new_callable=AsyncMock, return_value=mock_ask_decision):
            await execute_single_tool(task, {"cwd": "/tmp", "interaction_broker": broker})

        assert task.status == ExecutionStatus.COMPLETED
        assert broker.request_payload is not None

    @pytest.mark.asyncio
    async def test_ask_deny_raises_permission_error(self):
        from codo.services.tools.orchestration import execute_single_tool
        from codo.types.orchestration import ExecutionStatus, ToolExecutionTask

        task = ToolExecutionTask(
            tool_use_id="test-2",
            tool_name="Bash",
            tool_input={"command": "rm -rf /"},
            is_concurrency_safe=False,
        )

        mock_tool = MagicMock()
        mock_tool.name = "Bash"
        mock_tool.requires_permission.return_value = True

        mock_ask_decision = MagicMock()
        mock_ask_decision.behavior = "ask"
        mock_ask_decision.message = "Dangerous!"

        broker = _PermissionBroker(PermissionChoice.DENY)
        with patch("codo.services.tools.orchestration.find_tool_by_name", return_value=mock_tool), \
             patch("codo.services.tools.permission_checker.has_permissions_to_use_tool", new_callable=AsyncMock, return_value=mock_ask_decision):
            await execute_single_tool(task, {"cwd": "/tmp", "interaction_broker": broker})

        assert task.status == ExecutionStatus.FAILED
        assert isinstance(task.error, PermissionError)

    @pytest.mark.asyncio
    async def test_ask_abort_raises_keyboard_interrupt(self):
        from codo.services.tools.orchestration import execute_single_tool
        from codo.types.orchestration import ToolExecutionTask

        task = ToolExecutionTask(
            tool_use_id="test-3",
            tool_name="Bash",
            tool_input={"command": "rm -rf /"},
            is_concurrency_safe=False,
        )

        mock_tool = MagicMock()
        mock_tool.name = "Bash"
        mock_tool.requires_permission.return_value = True

        mock_ask_decision = MagicMock()
        mock_ask_decision.behavior = "ask"
        mock_ask_decision.message = "Dangerous!"

        broker = _PermissionBroker(PermissionChoice.ABORT)
        with patch("codo.services.tools.orchestration.find_tool_by_name", return_value=mock_tool), \
             patch("codo.services.tools.permission_checker.has_permissions_to_use_tool", new_callable=AsyncMock, return_value=mock_ask_decision):
            with pytest.raises(KeyboardInterrupt):
                await execute_single_tool(task, {"cwd": "/tmp", "interaction_broker": broker})

    @pytest.mark.asyncio
    async def test_ask_allow_always_adds_session_rule(self):
        from codo.services.tools.orchestration import execute_single_tool
        from codo.types.orchestration import ExecutionStatus, ToolExecutionTask
        from codo.types.permissions import PermissionRuleSource

        perm_ctx = MagicMock()
        perm_ctx.always_allow_rules = {}

        task = ToolExecutionTask(
            tool_use_id="test-4",
            tool_name="Read",
            tool_input={"file_path": "/tmp/test.txt"},
            is_concurrency_safe=True,
        )

        mock_tool = MagicMock()
        mock_tool.name = "Read"
        mock_tool.requires_permission.return_value = True
        mock_tool.execute = AsyncMock(return_value=MagicMock(data="content"))
        mock_tool.get_context_modifier.return_value = None

        mock_ask_decision = MagicMock()
        mock_ask_decision.behavior = "ask"
        mock_ask_decision.message = "Read permission"

        broker = _PermissionBroker(PermissionChoice.ALLOW_ALWAYS)
        with patch("codo.services.tools.orchestration.find_tool_by_name", return_value=mock_tool), \
             patch("codo.services.tools.permission_checker.has_permissions_to_use_tool", new_callable=AsyncMock, return_value=mock_ask_decision):
            await execute_single_tool(
                task,
                {"cwd": "/tmp", "permission_context": perm_ctx, "interaction_broker": broker},
            )

        assert task.status == ExecutionStatus.COMPLETED
        assert PermissionRuleSource.SESSION in perm_ctx.always_allow_rules
        assert "Read" in perm_ctx.always_allow_rules[PermissionRuleSource.SESSION]
