"""ExitPlanModeTool 实现"""
from typing import Dict, Any
from ..base import Tool, ToolUseContext
from ..types import ToolResult, ValidationResult
from codo.types.permissions import PermissionAskDecision, create_ask_decision
from .types import ExitPlanModeInput, ExitPlanModeOutput
from .prompt import EXIT_PLAN_MODE_PROMPT, EXIT_PLAN_MODE_DESCRIPTION
from .constants import EXIT_PLAN_MODE_TOOL_NAME
from .utils import read_plan_file, write_plan_file

class ExitPlanModeTool(Tool[ExitPlanModeInput, ExitPlanModeOutput, None]):
    """
    退出计划模式工具

    退出计划模式并请求用户批准计划。
    """

    def __init__(self):
        self.name = EXIT_PLAN_MODE_TOOL_NAME
        self.max_result_size_chars = 100_000

    @property
    def input_schema(self) -> type[ExitPlanModeInput]:
        return ExitPlanModeInput

    @property
    def output_schema(self) -> type[ExitPlanModeOutput]:
        return ExitPlanModeOutput

    async def description(self, input_data: ExitPlanModeInput, options: Dict[str, Any]) -> str:
        return EXIT_PLAN_MODE_DESCRIPTION

    async def prompt(self, options: Dict[str, Any]) -> str:
        return EXIT_PLAN_MODE_PROMPT

    def is_read_only(self, input_data: ExitPlanModeInput = None) -> bool:
        return False

    def is_concurrency_safe(self, input_data: ExitPlanModeInput = None) -> bool:
        return True

    def requires_user_interaction(self) -> bool:
        return True

    async def validate_input(
        self,
        args: ExitPlanModeInput,
        context: ToolUseContext
    ) -> ValidationResult:
        """验证输入参数"""
        # 检查是否在计划模式中
        options = context.get_options()
        app_state = options.get("app_state", {})
        if not app_state.get("plan_mode"):
            return ValidationResult(
                result=False,
                message="You are not in plan mode. Use EnterPlanMode first."
            )

        return ValidationResult(result=True)

    async def check_permissions(
        self,
        args: ExitPlanModeInput,
        context: ToolUseContext
    ) -> PermissionAskDecision:
        """检查权限：需要用户确认"""
        return create_ask_decision(
            message="Exit plan mode and approve plan?",
            updated_input=args.model_dump()
        )

    async def call(
        self,
        args: ExitPlanModeInput,
        context: ToolUseContext,
        can_use_tool,
        parent_message,
        on_progress=None
    ) -> ToolResult[ExitPlanModeOutput]:
        """执行退出计划模式"""
        # 获取 options
        options = context.get_options()
        app_state = options.get("app_state", {})
        plan_file_path = app_state.get("plan_file_path")
        agent_id = options.get("agent_id")

        # 获取计划内容（从输入或磁盘）
        input_plan = args.plan
        plan = input_plan

        if not plan and plan_file_path:
            plan = read_plan_file(plan_file_path)

        # 如果计划被编辑，同步到磁盘
        plan_was_edited = False
        if input_plan and plan_file_path and input_plan != read_plan_file(plan_file_path):
            write_plan_file(plan_file_path, input_plan)
            plan_was_edited = True

        # 退出计划模式
        app_state["plan_mode"] = False
        context["options"] = options

        return ToolResult(
            data=ExitPlanModeOutput(
                plan=plan,
                isAgent=bool(agent_id),
                filePath=plan_file_path,
                planWasEdited=plan_was_edited if plan_was_edited else None
            )
        )

    def map_tool_result_to_tool_result_block_param(
        self,
        content: ExitPlanModeOutput,
        tool_use_id: str
    ) -> Dict[str, Any]:
        """将工具结果映射为 API 响应格式"""
        # 代理模式
        if content.isAgent:
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": 'User has approved the plan. There is nothing else needed from you now. Please respond with "ok"'
            }

        # 空计划
        if not content.plan or not content.plan.strip():
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": "User has approved exiting plan mode. You can now proceed."
            }

        # 标准情况
        edited_note = " (edited by user)" if content.planWasEdited else ""
        message = f"""User has approved your plan. You can now start coding to implement it.

Your plan has been saved to: {content.filePath}

## Approved Plan{edited_note}:
{content.plan}

Remember to:
1. Follow the plan you outlined
2. Test your implementation
3. Handle edge cases
4. Write clean, maintainable code"""

        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": message
        }

# 创建工具实例
exit_plan_mode_tool = ExitPlanModeTool()
