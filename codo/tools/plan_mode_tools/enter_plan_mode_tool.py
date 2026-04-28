"""EnterPlanModeTool 实现"""
from typing import Dict, Any
from ..base import Tool, ToolUseContext
from ..types import ToolResult, ValidationResult
from codo.types.permissions import PermissionAskDecision, create_ask_decision
from .types import EnterPlanModeInput, EnterPlanModeOutput
from .prompt import ENTER_PLAN_MODE_PROMPT, ENTER_PLAN_MODE_DESCRIPTION
from .constants import ENTER_PLAN_MODE_TOOL_NAME
from .utils import get_plan_file_path

class EnterPlanModeTool(Tool[EnterPlanModeInput, EnterPlanModeOutput, None]):
    """
    进入计划模式工具

    转换到计划模式，允许探索代码库并设计实现方法。
    """

    def __init__(self):
        self.name = ENTER_PLAN_MODE_TOOL_NAME
        self.max_result_size_chars = 10_000

    @property
    def input_schema(self) -> type[EnterPlanModeInput]:
        return EnterPlanModeInput

    @property
    def output_schema(self) -> type[EnterPlanModeOutput]:
        return EnterPlanModeOutput

    async def description(self, input_data: EnterPlanModeInput, options: Dict[str, Any]) -> str:
        return ENTER_PLAN_MODE_DESCRIPTION

    async def prompt(self, options: Dict[str, Any]) -> str:
        return ENTER_PLAN_MODE_PROMPT

    def is_read_only(self, input_data: EnterPlanModeInput = None) -> bool:
        return True

    def is_concurrency_safe(self, input_data: EnterPlanModeInput = None) -> bool:
        return True

    async def validate_input(
        self,
        args: EnterPlanModeInput,
        context: ToolUseContext
    ) -> ValidationResult:
        """验证输入参数"""
        # 检查是否在 agent 上下文中
        options = context.get_options()
        agent_id = options.get("agent_id")
        if agent_id:
            return ValidationResult(
                result=False,
                message="EnterPlanMode tool cannot be used in agent contexts"
            )

        return ValidationResult(result=True)

    async def check_permissions(
        self,
        args: EnterPlanModeInput,
        context: ToolUseContext
    ) -> PermissionAskDecision:
        """检查权限：需要用户确认"""
        return create_ask_decision(
            message="Enter plan mode?",
            updated_input=args.model_dump()
        )

    async def call(
        self,
        args: EnterPlanModeInput,
        context: ToolUseContext,
        can_use_tool,
        parent_message,
        on_progress=None
    ) -> ToolResult[EnterPlanModeOutput]:
        """执行进入计划模式"""
        # 获取 options
        options = context.get_options()

        # 获取会话 ID
        session_id = options.get("session_id", "default")

        # 生成计划文件路径
        plan_file_path = get_plan_file_path(session_id)

        # 更新应用状态为计划模式
        app_state = options.get("app_state", {})
        app_state["plan_mode"] = True
        app_state["plan_file_path"] = plan_file_path
        context["options"] = options

        message = f"""Entered plan mode. You should now focus on exploring the codebase and designing your implementation approach.

Your plan will be saved to: {plan_file_path}

## What to do in plan mode:

1. **Explore**: Use Glob, Grep, and Read tools to understand the codebase
2. **Plan**: Design your implementation approach
3. **Clarify**: Use AskUserQuestion if you need to clarify requirements
4. **Document**: Write your plan to the plan file
5. **Exit**: Call ExitPlanMode when ready for user approval

Remember: In plan mode, you should NOT write code yet. Focus on exploration and planning."""

        return ToolResult(
            data=EnterPlanModeOutput(message=message)
        )

    def map_tool_result_to_tool_result_block_param(
        self,
        content: EnterPlanModeOutput,
        tool_use_id: str
    ) -> Dict[str, Any]:
        """将工具结果映射为 API 响应格式"""
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content.message
        }

# 创建工具实例
enter_plan_mode_tool = EnterPlanModeTool()
