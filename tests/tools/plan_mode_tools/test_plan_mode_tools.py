"""PlanMode 工具单元测试"""
import pytest
from codo.tools.plan_mode_tools import (
    EnterPlanModeTool,
    ExitPlanModeTool,
    EnterPlanModeInput,
    ExitPlanModeInput,
)
from codo.tools.base import ToolUseContext

@pytest.fixture
def enter_tool():
    """创建 EnterPlanModeTool 实例"""
    return EnterPlanModeTool()

@pytest.fixture
def exit_tool():
    """创建 ExitPlanModeTool 实例"""
    return ExitPlanModeTool()

@pytest.fixture
def context():
    """创建测试上下文"""
    return ToolUseContext(
        options={
            "cwd": "/test",
            "session_id": "test_session",
            "app_state": {}
        },
        abort_controller=None,
        messages=[]
    )

class TestEnterPlanModeTool:
    """EnterPlanModeTool 测试套件"""

    @pytest.mark.asyncio
    async def test_validate_success(self, enter_tool, context):
        """测试验证成功"""
        input_data = EnterPlanModeInput()
        result = await enter_tool.validate_input(input_data, context)
        assert result.result is True

    @pytest.mark.asyncio
    async def test_validate_in_agent_context(self, enter_tool, context):
        """测试在 agent 上下文中验证失败"""
        context.options["agent_id"] = "test_agent"
        input_data = EnterPlanModeInput()
        result = await enter_tool.validate_input(input_data, context)
        assert result.result is False
        assert "agent contexts" in result.message

    @pytest.mark.asyncio
    async def test_call_success(self, enter_tool, context):
        """测试调用成功"""
        input_data = EnterPlanModeInput()
        result = await enter_tool.call(input_data, context, None, None, None)

        assert result.data is not None
        assert "Entered plan mode" in result.data.message
        assert context.options["app_state"]["plan_mode"] is True
        assert "plan_file_path" in context.options["app_state"]

    def test_tool_properties(self, enter_tool):
        """测试工具属性"""
        assert enter_tool.name == "EnterPlanMode"
        assert enter_tool.is_read_only() is True
        assert enter_tool.is_concurrency_safe() is True

class TestExitPlanModeTool:
    """ExitPlanModeTool 测试套件"""

    @pytest.mark.asyncio
    async def test_validate_not_in_plan_mode(self, exit_tool, context):
        """测试不在计划模式中验证失败"""
        input_data = ExitPlanModeInput()
        result = await exit_tool.validate_input(input_data, context)
        assert result.result is False
        assert "not in plan mode" in result.message

    @pytest.mark.asyncio
    async def test_validate_in_plan_mode(self, exit_tool, context):
        """测试在计划模式中验证成功"""
        context.options["app_state"]["plan_mode"] = True
        input_data = ExitPlanModeInput()
        result = await exit_tool.validate_input(input_data, context)
        assert result.result is True

    @pytest.mark.asyncio
    async def test_call_with_plan(self, exit_tool, context):
        """测试带计划的调用"""
        context.options["app_state"]["plan_mode"] = True
        context.options["app_state"]["plan_file_path"] = "/test/plan.md"

        input_data = ExitPlanModeInput(plan="# My Plan\n\nImplement feature X")
        result = await exit_tool.call(input_data, context, None, None, None)

        assert result.data is not None
        assert result.data.plan == "# My Plan\n\nImplement feature X"
        assert result.data.isAgent is False
        assert context.options["app_state"]["plan_mode"] is False

    def test_tool_properties(self, exit_tool):
        """测试工具属性"""
        assert exit_tool.name == "ExitPlanMode"
        assert exit_tool.is_read_only() is False
        assert exit_tool.is_concurrency_safe() is True
        assert exit_tool.requires_user_interaction() is True
