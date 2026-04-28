"""AskUserQuestionTool 单元测试"""
import pytest
from codo.tools.ask_user_question_tool import (
    AskUserQuestionTool,
    AskUserQuestionInput,
    AskUserQuestionOutput,
    Question,
    QuestionOption,
    QuestionAnnotation,
)
from codo.tools.base import ToolUseContext

@pytest.fixture
def tool():
    """创建 AskUserQuestionTool 实例"""
    return AskUserQuestionTool()

@pytest.fixture
def context():
    """创建测试上下文"""
    return ToolUseContext(
        options={"cwd": "/test"},
        abort_controller=None,
        messages=[]
    )

class TestAskUserQuestionTool:
    """AskUserQuestionTool 测试套件"""

    @pytest.mark.asyncio
    async def test_validate_single_question(self, tool, context):
        """测试单个问题验证"""
        input_data = AskUserQuestionInput(
            questions=[
                Question(
                    question="Which database should we use?",
                    header="Database",
                    options=[
                        QuestionOption(label="PostgreSQL", description="Mature and reliable"),
                        QuestionOption(label="MongoDB", description="Flexible schema"),
                    ]
                )
            ]
        )

        result = await tool.validate_input(input_data, context)
        assert result.result is True

    @pytest.mark.asyncio
    async def test_validate_question_without_question_mark(self, tool, context):
        """测试没有问号的问题"""
        input_data = AskUserQuestionInput(
            questions=[
                Question(
                    question="Which database",
                    header="DB",
                    options=[
                        QuestionOption(label="PostgreSQL", description="SQL"),
                        QuestionOption(label="MongoDB", description="NoSQL"),
                    ]
                )
            ]
        )

        result = await tool.validate_input(input_data, context)
        assert result.result is False
        assert "must end with" in result.message

    @pytest.mark.asyncio
    async def test_validate_header_too_long(self, tool, context):
        """测试 header 过长"""
        input_data = AskUserQuestionInput(
            questions=[
                Question(
                    question="Which database?",
                    header="This is a very long header",
                    options=[
                        QuestionOption(label="PostgreSQL", description="SQL"),
                        QuestionOption(label="MongoDB", description="NoSQL"),
                    ]
                )
            ]
        )

        result = await tool.validate_input(input_data, context)
        assert result.result is False
        assert "header too long" in result.message

    @pytest.mark.asyncio
    async def test_call_with_answers(self, tool, context):
        """测试带答案的调用"""
        input_data = AskUserQuestionInput(
            questions=[
                Question(
                    question="Which database?",
                    header="DB",
                    options=[
                        QuestionOption(label="PostgreSQL", description="SQL"),
                        QuestionOption(label="MongoDB", description="NoSQL"),
                    ]
                )
            ],
            answers={"Which database?": "PostgreSQL"}
        )

        result = await tool.call(input_data, context, None, None, None)

        assert result.data is not None
        assert result.data.answers["Which database?"] == "PostgreSQL"

    @pytest.mark.asyncio
    async def test_call_without_answers(self, tool, context):
        """测试没有答案的调用"""
        input_data = AskUserQuestionInput(
            questions=[
                Question(
                    question="Which database?",
                    header="DB",
                    options=[
                        QuestionOption(label="PostgreSQL", description="SQL"),
                        QuestionOption(label="MongoDB", description="NoSQL"),
                    ]
                )
            ]
        )

        result = await tool.call(input_data, context, None, None, None)

        assert result.error is not None
        assert "No answers" in result.error

    def test_tool_properties(self, tool):
        """测试工具属性"""
        assert tool.name == "AskUserQuestion"
        assert tool.is_read_only() is True
        assert tool.is_concurrency_safe() is True
        assert tool.requires_user_interaction() is True
