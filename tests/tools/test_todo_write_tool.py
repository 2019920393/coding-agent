"""TodoWriteTool 单元测试"""
import pytest
from codo.tools.todo_write_tool import (
    TodoWriteTool,
    TodoWriteInput,
    TodoWriteOutput,
    TodoItem,
    TodoStatus,
)
from codo.tools.base import ToolUseContext

@pytest.fixture
def tool():
    """创建 TodoWriteTool 实例"""
    return TodoWriteTool()

@pytest.fixture
def context():
    """创建测试上下文"""
    return ToolUseContext(
        options={
            "cwd": "/test",
            "app_state": {"todos": {}},
            "session_id": "test_session",
        },
        abort_controller=None,
        messages=[]
    )

class TestTodoWriteTool:
    """TodoWriteTool 测试套件"""

    @pytest.mark.asyncio
    async def test_create_todo_list(self, tool, context):
        """测试创建任务列表"""
        input_data = TodoWriteInput(
            todos=[
                TodoItem(
                    content="Implement feature A",
                    status=TodoStatus.IN_PROGRESS,
                    activeForm="Implementing feature A"
                ),
                TodoItem(
                    content="Write tests",
                    status=TodoStatus.PENDING,
                    activeForm="Writing tests"
                ),
            ]
        )

        result = await tool.call(input_data, context, None, None, None)

        assert result.data is not None
        assert len(result.data.oldTodos) == 0
        assert len(result.data.newTodos) == 2
        assert result.data.newTodos[0].content == "Implement feature A"
        assert result.data.newTodos[0].status == TodoStatus.IN_PROGRESS
        assert result.data.verificationNudgeNeeded is False

    @pytest.mark.asyncio
    async def test_update_todo_list(self, tool, context):
        """测试更新任务列表"""
        initial_input = TodoWriteInput(
            todos=[
                TodoItem(
                    content="Task 1",
                    status=TodoStatus.IN_PROGRESS,
                    activeForm="Doing task 1"
                ),
            ]
        )
        await tool.call(initial_input, context, None, None, None)

        update_input = TodoWriteInput(
            todos=[
                TodoItem(
                    content="Task 1",
                    status=TodoStatus.COMPLETED,
                    activeForm="Doing task 1"
                ),
                TodoItem(
                    content="Task 2",
                    status=TodoStatus.IN_PROGRESS,
                    activeForm="Doing task 2"
                ),
            ]
        )
        result = await tool.call(update_input, context, None, None, None)

        assert len(result.data.oldTodos) == 1
        assert result.data.oldTodos[0].content == "Task 1"
        assert len(result.data.newTodos) == 2

    @pytest.mark.asyncio
    async def test_complete_all_tasks(self, tool, context):
        """测试完成所有任务"""
        input_data = TodoWriteInput(
            todos=[
                TodoItem(
                    content="Task 1",
                    status=TodoStatus.COMPLETED,
                    activeForm="Doing task 1"
                ),
                TodoItem(
                    content="Task 2",
                    status=TodoStatus.COMPLETED,
                    activeForm="Doing task 2"
                ),
            ]
        )

        await tool.call(input_data, context, None, None, None)

        app_state = context.options["app_state"]
        assert app_state["todos"]["test_session"] == []

    @pytest.mark.asyncio
    async def test_verification_nudge(self, tool, context):
        """测试验证提醒逻辑"""
        input_data = TodoWriteInput(
            todos=[
                TodoItem(content="Task 1", status=TodoStatus.COMPLETED, activeForm="Doing task 1"),
                TodoItem(content="Task 2", status=TodoStatus.COMPLETED, activeForm="Doing task 2"),
                TodoItem(content="Task 3", status=TodoStatus.COMPLETED, activeForm="Doing task 3"),
            ]
        )

        result = await tool.call(input_data, context, None, None, None)
        assert result.data.verificationNudgeNeeded is True

    @pytest.mark.asyncio
    async def test_no_verification_nudge_with_verification_task(self, tool, context):
        """测试有验证任务时不触发提醒"""
        input_data = TodoWriteInput(
            todos=[
                TodoItem(content="Task 1", status=TodoStatus.COMPLETED, activeForm="Doing task 1"),
                TodoItem(content="Task 2", status=TodoStatus.COMPLETED, activeForm="Doing task 2"),
                TodoItem(content="Verify implementation", status=TodoStatus.COMPLETED, activeForm="Verifying implementation"),
            ]
        )

        result = await tool.call(input_data, context, None, None, None)
        assert result.data.verificationNudgeNeeded is False

    @pytest.mark.asyncio
    async def test_validation_empty_list(self, tool, context):
        """测试空任务列表验证"""
        input_data = TodoWriteInput(todos=[])
        validation = await tool.validate_input(input_data, context)
        assert validation.result is False
        assert "empty" in validation.message.lower()

    def test_validation_empty_content(self):
        """测试空内容在 Pydantic 层就失败"""
        with pytest.raises(Exception):
            TodoItem(
                content="",
                status=TodoStatus.IN_PROGRESS,
                activeForm="Doing something"
            )

    @pytest.mark.asyncio
    async def test_validation_multiple_in_progress(self, tool, context):
        """测试多个 in_progress 任务验证"""
        input_data = TodoWriteInput(
            todos=[
                TodoItem(content="Task 1", status=TodoStatus.IN_PROGRESS, activeForm="Doing task 1"),
                TodoItem(content="Task 2", status=TodoStatus.IN_PROGRESS, activeForm="Doing task 2"),
            ]
        )
        validation = await tool.validate_input(input_data, context)
        assert validation.result is False
        assert "ONE task" in validation.message

    @pytest.mark.asyncio
    async def test_validation_no_in_progress(self, tool, context):
        """测试没有 in_progress 任务验证"""
        input_data = TodoWriteInput(
            todos=[
                TodoItem(content="Task 1", status=TodoStatus.PENDING, activeForm="Doing task 1"),
                TodoItem(content="Task 2", status=TodoStatus.PENDING, activeForm="Doing task 2"),
            ]
        )
        validation = await tool.validate_input(input_data, context)
        assert validation.result is False
        assert "ONE task" in validation.message

    @pytest.mark.asyncio
    async def test_validation_all_completed(self, tool, context):
        """测试所有任务完成时的验证"""
        input_data = TodoWriteInput(
            todos=[
                TodoItem(content="Task 1", status=TodoStatus.COMPLETED, activeForm="Doing task 1"),
                TodoItem(content="Task 2", status=TodoStatus.COMPLETED, activeForm="Doing task 2"),
            ]
        )
        validation = await tool.validate_input(input_data, context)
        assert validation.result is True

    def test_map_tool_result(self, tool):
        """测试工具结果映射"""
        output = TodoWriteOutput(
            oldTodos=[],
            newTodos=[
                TodoItem(content="Task 1", status=TodoStatus.IN_PROGRESS, activeForm="Doing task 1"),
            ],
            verificationNudgeNeeded=False
        )
        result = tool.map_tool_result_to_tool_result_block_param(output, "test_id")
        assert result["type"] == "tool_result"
        assert result["tool_use_id"] == "test_id"
        assert "modified successfully" in result["content"]
        assert "NOTE:" not in result["content"]

    def test_map_tool_result_with_nudge(self, tool):
        """测试带验证提醒的工具结果映射"""
        output = TodoWriteOutput(
            oldTodos=[],
            newTodos=[],
            verificationNudgeNeeded=True
        )
        result = tool.map_tool_result_to_tool_result_block_param(output, "test_id")
        assert "NOTE:" in result["content"]
        assert "verification" in result["content"].lower()
