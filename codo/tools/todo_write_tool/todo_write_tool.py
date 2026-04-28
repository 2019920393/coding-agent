"""TodoWriteTool 实现"""
from typing import Optional, Dict, Any
from ..base import Tool, ToolUseContext
from ..types import ToolResult, ValidationResult
from .types import TodoWriteInput, TodoWriteOutput, TodoItem, TodoStatus
from .prompt import PROMPT, DESCRIPTION
from .constants import TODO_WRITE_TOOL_NAME

class TodoWriteTool(Tool[TodoWriteInput, TodoWriteOutput, None]):
    """
    任务列表管理工具

    用于创建和管理会话中的结构化任务列表，帮助跟踪进度、组织复杂任务。
    """

    def __init__(self):
        self.name = TODO_WRITE_TOOL_NAME
        self.max_result_size_chars = 100_000

    @property
    def input_schema(self) -> type[TodoWriteInput]:
        return TodoWriteInput

    @property
    def output_schema(self) -> type[TodoWriteOutput]:
        return TodoWriteOutput

    async def description(self, input_data: TodoWriteInput, options: Dict[str, Any]) -> str:
        return DESCRIPTION

    async def prompt(self, options: Dict[str, Any]) -> str:
        return PROMPT

    async def validate_input(
        self,
        args: TodoWriteInput,
        context: ToolUseContext
    ) -> ValidationResult:
        """验证输入参数"""
        # 检查空列表
        if not args.todos:
            return ValidationResult(
                result=False,
                message="Todo list cannot be empty"
            )

        # 检查每个任务的 content 和 activeForm
        for i, todo in enumerate(args.todos):
            if not todo.content.strip():
                return ValidationResult(
                    result=False,
                    message=f"Task {i+1}: content cannot be empty"
                )
            if not todo.activeForm.strip():
                return ValidationResult(
                    result=False,
                    message=f"Task {i+1}: activeForm cannot be empty"
                )

        # 检查是否有且仅有一个 in_progress 任务（如果有未完成的任务）
        in_progress_count = sum(1 for todo in args.todos if todo.status == TodoStatus.IN_PROGRESS)
        all_completed = all(todo.status == TodoStatus.COMPLETED for todo in args.todos)

        if not all_completed and in_progress_count != 1:
            return ValidationResult(
                result=False,
                message=f"Exactly ONE task must be in_progress at any time (found {in_progress_count}). "
                        "Mark the current task as in_progress before starting work."
            )

        return ValidationResult(result=True)

    async def call(
        self,
        args: TodoWriteInput,
        context: ToolUseContext,
        can_use_tool,
        parent_message,
        on_progress=None
    ) -> ToolResult[TodoWriteOutput]:
        """执行任务列表更新"""
        # 验证输入
        if not args.todos:
            raise ValueError("Todo list cannot be empty")

        # 检查每个任务的 content 和 activeForm
        for i, todo in enumerate(args.todos):
            if not todo.content.strip():
                raise ValueError(f"Task {i+1}: content cannot be empty")
            if not todo.activeForm.strip():
                raise ValueError(f"Task {i+1}: activeForm cannot be empty")

        # 检查是否有且仅有一个 in_progress 任务（如果有未完成的任务）
        in_progress_count = sum(1 for todo in args.todos if todo.status == TodoStatus.IN_PROGRESS)
        all_completed = all(todo.status == TodoStatus.COMPLETED for todo in args.todos)

        if not all_completed and in_progress_count != 1:
            raise ValueError(
                f"Exactly ONE task must be in_progress at any time (found {in_progress_count}). "
                "Mark the current task as in_progress before starting work."
            )

        # 从 context 中获取 options
        options = context.get_options()

        # 获取当前会话的 todo key（agent_id 或 session_id）
        agent_id = options.get("agent_id")
        session_id = options.get("session_id")
        if agent_id is None:
            maybe_agent_id = context.get("agent_id")
            if isinstance(maybe_agent_id, str) and maybe_agent_id:
                agent_id = maybe_agent_id
        if not session_id:
            maybe_session_id = context.get("session_id")
            if isinstance(maybe_session_id, str) and maybe_session_id:
                session_id = maybe_session_id
        session_id = session_id or "default"
        todo_key = agent_id if agent_id else session_id

        # 获取 app_state（存储在 options 中）
        app_state = options.get("app_state", {})

        # 获取旧的任务列表
        todos_dict = app_state.get("todos", {})
        old_todos_data = todos_dict.get(todo_key, [])
        old_todos = [TodoItem(**item) if isinstance(item, dict) else item for item in old_todos_data]

        # 检查是否所有任务都已完成
        all_done = all(todo.status == TodoStatus.COMPLETED for todo in args.todos)

        # 如果所有任务都完成，清空任务列表；否则保留
        new_todos = [] if all_done else args.todos

        # 验证提醒逻辑：如果主线程 agent 完成了 3+ 个任务，且没有验证步骤，则提醒
        verification_nudge_needed = False
        if (
            not agent_id  # 主线程（非子 agent）
            and all_done  # 所有任务完成
            and len(args.todos) >= 3  # 至少 3 个任务
            and not any("verif" in todo.content.lower() for todo in args.todos)  # 没有验证步骤
        ):
            verification_nudge_needed = True

        # 更新 app_state（写回 context 的 options）
        new_todos_dict = {**todos_dict, todo_key: [todo.model_dump() for todo in new_todos]}
        new_app_state = {**app_state, "todos": new_todos_dict}
        options["app_state"] = new_app_state
        context["options"] = options

        # 返回结果
        return ToolResult(
            data=TodoWriteOutput(
                oldTodos=old_todos,
                newTodos=args.todos,
                verificationNudgeNeeded=verification_nudge_needed
            )
        )

    def map_tool_result_to_tool_result_block_param(
        self,
        content: TodoWriteOutput,
        tool_use_id: str
    ) -> Dict[str, Any]:
        """将工具结果映射为 API 响应格式"""
        base = (
            "Todos have been modified successfully. "
            "Ensure that you continue to use the todo list to track your progress. "
            "Please proceed with the current tasks if applicable"
        )

        nudge = ""
        if content.verificationNudgeNeeded:
            nudge = (
                "\n\nNOTE: You just closed out 3+ tasks and none of them was a verification step. "
                "Before writing your final summary, consider verifying your work. "
                "You cannot self-assign PARTIAL by listing caveats in your summary — "
                "only proper verification can ensure quality."
            )

        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": base + nudge
        }

# 创建工具实例
todo_write_tool = TodoWriteTool()
