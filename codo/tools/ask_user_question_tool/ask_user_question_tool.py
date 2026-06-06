import json
import re
from typing import Any
from uuid import uuid4

from codo.types.permissions import PermissionAskDecision, create_ask_decision
from codo.types.runtime import (
    InteractionOption,
    InteractionQuestion,
    InteractionRequest,
)

from ..base import Tool
from ..types import ToolResult, ValidationResult
from .constants import (
    ASK_USER_QUESTION_TOOL_NAME,
    MAX_HEADER_LENGTH,
    MAX_OPTIONS,
    MAX_QUESTIONS,
    MAX_RESULT_SIZE_CHARS,
    MIN_OPTIONS,
    MIN_QUESTIONS,
)
from .prompt import DESCRIPTION, PROMPT
from .types import AskUserQuestionInput, AskUserQuestionOutput


class AskUserQuestionTool(Tool[AskUserQuestionInput, AskUserQuestionOutput, None]):
    """
    用户问答工具

    在执行过程中向用户提问，收集偏好、需求或决策。
    """

    def __init__(self):
        """初始化 AskUserQuestionTool，设置工具名称和最大结果大小。"""
        self.name = ASK_USER_QUESTION_TOOL_NAME
        self.max_result_size_chars = MAX_RESULT_SIZE_CHARS

    @property
    def input_schema(self) -> type[AskUserQuestionInput]:
        """返回输入 schema 类 AskUserQuestionInput。"""
        return AskUserQuestionInput

    @property
    def output_schema(self) -> type[AskUserQuestionOutput]:
        """返回输出 schema 类 AskUserQuestionOutput。"""
        return AskUserQuestionOutput

    async def description(self, input_data: AskUserQuestionInput, options: dict[str, Any]) -> str:
        """返回工具简短描述。"""
        return DESCRIPTION

    async def prompt(self, options: dict[str, Any]) -> str:
        """返回系统提示词中的工具描述。"""
        return PROMPT

    def is_read_only(self, input_data: AskUserQuestionInput = None) -> bool:
        """用户问答是只读操作（不修改文件系统），返回 True。"""
        return True

    def is_concurrency_safe(self, input_data: AskUserQuestionInput = None) -> bool:
        """用户问答是并发安全的，返回 True。"""
        return True

    def requires_user_interaction(self) -> bool:
        """用户问答工具需要用户交互，返回 True。"""
        return True

    @staticmethod
    def _normalize_question_text(question_text: str) -> str:
        """
        规范化问题文本结尾，降低模型输出格式波动带来的校验失败。

        规则：
        1. 兼容中文问号 `？`（统一转为 `?`）；
        2. 若以句号/感叹号等结尾，替换为问号；
        3. 若无结尾标点，自动补 `?`。
        """
        text = (question_text or "").strip()
        if not text:
            return text

        if text.endswith("？"):
            return text[:-1] + "?"

        if text.endswith("?"):
            return text

        if text[-1] in ("。", ".", "！", "!", "；", ";", "，", ",", "：", ":"):
            text = text[:-1].rstrip()

        return f"{text}?"

    async def validate_input(
        self,
        args: AskUserQuestionInput,
        context: dict[str, Any]
    ) -> ValidationResult:
        """验证输入参数"""
        options = context.get("options", {})

        # 默认严格模式（兼容单元测试）；运行时可通过 options 显式开启宽松模式。
        normalize_question_mark = bool(options.get("normalize_question_mark", False))

        # 检查问题数量
        if len(args.questions) < MIN_QUESTIONS or len(args.questions) > MAX_QUESTIONS:
            return ValidationResult(
                result=False,
                message=f"Must have {MIN_QUESTIONS}-{MAX_QUESTIONS} questions (got {len(args.questions)})"
            )

        # 宽松模式下才自动规范化问题结尾，避免严格校验误伤运行时体验
        if normalize_question_mark:
            for question in args.questions:
                question.question = self._normalize_question_text(question.question)

        # 检查问题文本唯一性
        question_texts = [q.question for q in args.questions]
        if len(question_texts) != len(set(question_texts)):
            return ValidationResult(
                result=False,
                message="Question texts must be unique"
            )

        for i, question in enumerate(args.questions):
            if not question.question.strip():
                return ValidationResult(
                    result=False,
                    message=f"Question {i+1}: cannot be empty"
                )

            # 检查问题格式
            if not (
                question.question.strip().endswith('?')
                or question.question.strip().endswith('？')
            ):
                return ValidationResult(
                    result=False,
                    message=f"Question {i+1}: must end with '?'"
                )

            # 检查 header 长度
            if len(question.header) > MAX_HEADER_LENGTH:
                return ValidationResult(
                    result=False,
                    message=f"Question {i+1}: header too long (max {MAX_HEADER_LENGTH} chars)"
                )

            # 检查选项数量
            if len(question.options) < MIN_OPTIONS or len(question.options) > MAX_OPTIONS:
                return ValidationResult(
                    result=False,
                    message=f"Question {i+1}: must have {MIN_OPTIONS}-{MAX_OPTIONS} options"
                )

            # 检查选项标签唯一性
            labels = [opt.label for opt in question.options]
            if len(labels) != len(set(labels)):
                return ValidationResult(
                    result=False,
                    message=f"Question {i+1}: option labels must be unique"
                )

            # 检查预览（仅单选支持）
            has_preview = any(opt.preview for opt in question.options)
            if has_preview and question.multiSelect:
                return ValidationResult(
                    result=False,
                    message=f"Question {i+1}: previews only supported for single-select questions"
                )

            # 验证 HTML 预览
            for j, opt in enumerate(question.options):
                if opt.preview:
                    error = self._validate_html_preview(opt.preview)
                    if error:
                        return ValidationResult(
                            result=False,
                            message=f"Question {i+1}, Option {j+1}: {error}"
                        )

        return ValidationResult(result=True)

    def _validate_html_preview(self, preview: str) -> str | None:
        """验证 HTML 预览内容"""
        # 检查是否包含禁止的标签
        if re.search(r'<\s*(html|body|!doctype)\b', preview, re.IGNORECASE):
            return "preview must be an HTML fragment, not a complete document"

        if re.search(r'<\s*(script|style)\b', preview, re.IGNORECASE):
            return "preview must not contain <script> or <style> tags"

        # 如果看起来像 HTML，检查是否包含有效标签
        if '<' in preview and '>' in preview:
            if not re.search(r'<[a-z][^>]*>', preview, re.IGNORECASE):
                return "preview must contain valid HTML tags"

        return None

    def _build_interaction_request(self, args: AskUserQuestionInput) -> InteractionRequest:
        request_id = (args.metadata or {}).get("request_id") or f"req_question_{uuid4().hex}"
        questions = [
            InteractionQuestion(
                question_id=f"q_{index}",
                header=question.header,
                question=question.question,
                options=[
                    InteractionOption(
                        value=option.label,
                        label=option.label,
                        description=option.description or "",
                        preview=option.preview or "",
                    )
                    for option in question.options
                ],
                multi_select=question.multiSelect,
            )
            for index, question in enumerate(args.questions, start=1)
        ]
        return InteractionRequest(
            request_id=request_id,
            kind="question",
            label="Answer question",
            message="Answer questions to continue.",
            questions=questions,
        )

    def _parse_interaction_response(
        self,
        response: str,
        args: AskUserQuestionInput,
    ) -> dict[str, str] | None:
        try:
            payload = json.loads(response)
        except json.JSONDecodeError:
            payload = response

        if isinstance(payload, dict):
            answers_payload = payload.get("answers") if "answers" in payload else payload
            if isinstance(answers_payload, dict):
                answers: dict[str, str] = {}
                for key, value in answers_payload.items():
                    if isinstance(key, str) and isinstance(value, str):
                        answers[key] = value
                return answers or None

        if isinstance(payload, str) and len(args.questions) == 1:
            return {args.questions[0].question: payload}

        return None

    async def check_permissions(
        self,
        args: AskUserQuestionInput,
        context: dict[str, Any]
    ) -> PermissionAskDecision:
        """检查权限：总是需要用户交互"""
        return create_ask_decision(
            message="Answer questions?",
            updated_input=args.model_dump()
        )

    async def call(
        self,
        args: AskUserQuestionInput,
        context: dict[str, Any],
        can_use_tool,
        parent_message,
        on_progress=None
    ) -> ToolResult[AskUserQuestionOutput]:
        """执行用户问答"""
        answers = args.answers
        if not answers:
            broker = context.get("interaction_broker") or context.get("runtime_controller")
            if broker is None:
                return ToolResult(error="No interaction broker available for user question")

            request = self._build_interaction_request(args)
            if hasattr(broker, "request"):
                response = await broker.request(request)
            elif hasattr(broker, "request_interaction"):
                response = await broker.request_interaction(request)
            else:
                return ToolResult(error="Interaction broker cannot request user questions")
            if response is None:
                return ToolResult(error="User cancelled question interaction")

            answers = self._parse_interaction_response(response, args)
            if not answers:
                return ToolResult(error="Could not parse user question response")

        return ToolResult(
            data=AskUserQuestionOutput(
                questions=args.questions,
                answers=answers,
                annotations=args.annotations
            )
        )

    def map_tool_result_to_tool_result_block_param(
        self,
        content: AskUserQuestionOutput,
        tool_use_id: str
    ) -> dict[str, Any]:
        """将工具结果映射为 API 响应格式"""
        # 格式化答案
        answer_parts = []
        for question_text, answer in content.answers.items():
            parts = [f'"{question_text}"="{answer}"']

            # 添加预览
            if content.annotations and question_text in content.annotations:
                annotation = content.annotations[question_text]
                if annotation.preview:
                    parts.append(f"selected preview:\n{annotation.preview}")
                if annotation.notes:
                    parts.append(f"user notes: {annotation.notes}")

            answer_parts.append(' '.join(parts))

        answers_text = ', '.join(answer_parts)

        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": f"User has answered your questions: {answers_text}. You can now continue with the user's answers in mind."
        }

# 创建工具实例
ask_user_question_tool = AskUserQuestionTool()
