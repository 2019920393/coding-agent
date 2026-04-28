"""
Tests for permission prompt UI and orchestration integration.

Covers:
- format_tool_info: formatting for all tool types
- prompt_permission: Textual-only async wrapper
- apply_session_allow_rule: session rule persistence
- orchestration ask→interactive integration
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from codo.services.tools.permission_prompt import (
    PermissionChoice,
    format_tool_info,
    prompt_permission,
    apply_session_allow_rule,
)

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

class TestPromptPermissionAsync:
    @pytest.mark.asyncio
    @patch(
        "codo.cli.interactive_dialogs.prompt_permission_dialog",
        new_callable=AsyncMock,
        return_value="allow_once",
    )
    async def test_prompt_permission_maps_allow_once(self, mock_dialog):
        result = await prompt_permission("Bash", {"command": "ls"})
        assert result == PermissionChoice.ALLOW_ONCE
        mock_dialog.assert_awaited_once()

    @pytest.mark.asyncio
    @patch(
        "codo.cli.interactive_dialogs.prompt_permission_dialog",
        new_callable=AsyncMock,
        return_value="deny",
    )
    async def test_prompt_permission_maps_deny(self, mock_dialog):
        result = await prompt_permission("Bash", {"command": "rm -rf /"}, message="danger")
        assert result == PermissionChoice.DENY

    @pytest.mark.asyncio
    @patch(
        "codo.cli.interactive_dialogs.prompt_permission_dialog",
        new_callable=AsyncMock,
        side_effect=RuntimeError("Textual app is required"),
    )
    async def test_prompt_permission_propagates_when_textual_missing(self, mock_dialog):
        with pytest.raises(RuntimeError, match="Textual app is required"):
            await prompt_permission("Bash", {"command": "ls"})

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
        from codo.types.orchestration import ToolExecutionTask, ExecutionStatus

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

        with patch("codo.services.tools.orchestration.find_tool_by_name", return_value=mock_tool), \
             patch("codo.services.tools.permission_checker.has_permissions_to_use_tool", new_callable=AsyncMock, return_value=mock_ask_decision), \
             patch("codo.services.tools.permission_prompt.prompt_permission", new_callable=AsyncMock, return_value=PermissionChoice.ALLOW_ONCE):
            await execute_single_tool(task, {"cwd": "/tmp"})

        assert task.status == ExecutionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_ask_deny_raises_permission_error(self):
        from codo.services.tools.orchestration import execute_single_tool
        from codo.types.orchestration import ToolExecutionTask, ExecutionStatus

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

        with patch("codo.services.tools.orchestration.find_tool_by_name", return_value=mock_tool), \
             patch("codo.services.tools.permission_checker.has_permissions_to_use_tool", new_callable=AsyncMock, return_value=mock_ask_decision), \
             patch("codo.services.tools.permission_prompt.prompt_permission", new_callable=AsyncMock, return_value=PermissionChoice.DENY):
            await execute_single_tool(task, {"cwd": "/tmp"})

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

        with patch("codo.services.tools.orchestration.find_tool_by_name", return_value=mock_tool), \
             patch("codo.services.tools.permission_checker.has_permissions_to_use_tool", new_callable=AsyncMock, return_value=mock_ask_decision), \
             patch("codo.services.tools.permission_prompt.prompt_permission", new_callable=AsyncMock, return_value=PermissionChoice.ABORT):
            with pytest.raises(KeyboardInterrupt):
                await execute_single_tool(task, {"cwd": "/tmp"})

    @pytest.mark.asyncio
    async def test_ask_allow_always_adds_session_rule(self):
        from codo.services.tools.orchestration import execute_single_tool
        from codo.types.orchestration import ToolExecutionTask, ExecutionStatus
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

        with patch("codo.services.tools.orchestration.find_tool_by_name", return_value=mock_tool), \
             patch("codo.services.tools.permission_checker.has_permissions_to_use_tool", new_callable=AsyncMock, return_value=mock_ask_decision), \
             patch("codo.services.tools.permission_prompt.prompt_permission", new_callable=AsyncMock, return_value=PermissionChoice.ALLOW_ALWAYS):
            await execute_single_tool(
                task,
                {"cwd": "/tmp", "permission_context": perm_ctx},
            )

        assert task.status == ExecutionStatus.COMPLETED
        assert PermissionRuleSource.SESSION in perm_ctx.always_allow_rules
        assert "Read" in perm_ctx.always_allow_rules[PermissionRuleSource.SESSION]
