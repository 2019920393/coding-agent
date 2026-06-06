"""AskUserQuestionTool 单元测试"""
import json

import pytest

from codo.tools.ask_user_question_tool import (
    AskUserQuestionInput,
    AskUserQuestionTool,
    Question,
    QuestionOption,
)


@pytest.fixture
def tool():
    """创建 AskUserQuestionTool 实例"""
    return AskUserQuestionTool()

@pytest.fixture
def context():
    """创建测试上下文"""
    return {"options": {"cwd": "/test"}, "abort_controller": None, "messages": []}


class FakeInteractionBroker:
    def __init__(self, response: str) -> None:
        self.response = response
        self.captured_request = None

    async def request(self, request):
        self.captured_request = request
        return self.response

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
    async def test_validate_question_option_without_description(self, tool, context):
        """测试选项说明缺失时仍可校验通过。"""
        input_data = AskUserQuestionInput(
            questions=[
                Question(
                    question="Which database should we use?",
                    header="Database",
                    options=[
                        QuestionOption(label="PostgreSQL"),
                        QuestionOption(label="MongoDB"),
                    ]
                )
            ]
        )

        result = await tool.validate_input(input_data, context)

        assert result.result is True
        assert input_data.questions[0].options[0].description is None

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
    async def test_call_without_answers_requests_interaction(self, tool, context):
        """测试没有答案时通过 runtime broker 发起交互"""
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
        broker = FakeInteractionBroker(json.dumps({"answers": {"Which database?": "MongoDB"}}))
        context["interaction_broker"] = broker

        result = await tool.call(input_data, context, None, None, None)

        assert result.error is None
        assert result.data is not None
        assert result.data.answers["Which database?"] == "MongoDB"
        assert broker.captured_request is not None
        assert broker.captured_request.kind == "question"

    @pytest.mark.asyncio
    async def test_call_without_answers_requires_broker(self, tool, context):
        """测试没有答案且没有交互 broker 时返回明确错误"""
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
        assert "interaction broker" in result.error

    def test_tool_properties(self, tool):
        """测试工具属性"""
        assert tool.name == "AskUserQuestion"
        assert tool.is_read_only() is True
        assert tool.is_concurrency_safe() is True
        assert tool.requires_user_interaction() is True
