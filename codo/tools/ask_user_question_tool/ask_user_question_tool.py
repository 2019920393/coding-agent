"""用户提问工具实现。"""
import re
from typing import Dict, Any
from ..base import Tool, ToolUseContext
from ..types import ToolResult, ValidationResult
from codo.types.permissions import PermissionAskDecision, create_ask_decision
from .types import AskUserQuestionInput, AskUserQuestionOutput
from .prompt import PROMPT, DESCRIPTION
from .constants import (
    ASK_USER_QUESTION_TOOL_NAME,
    MAX_QUESTIONS,
    MIN_QUESTIONS,
    MAX_OPTIONS,
    MIN_OPTIONS,
    MAX_HEADER_LENGTH,
    MAX_RESULT_SIZE_CHARS,
)

class AskUserQuestionTool(Tool[AskUserQuestionInput, AskUserQuestionOutput, None]):
    """
    用户问答工具

    在执行过程中向用户提问，收集偏好、需求或决策。
    """

    def __init__(self):
        self.name = ASK_USER_QUESTION_TOOL_NAME
        self.max_result_size_chars = MAX_RESULT_SIZE_CHARS

    @property
    def input_schema(self) -> type[AskUserQuestionInput]:
        return AskUserQuestionInput

    @property
    def output_schema(self) -> type[AskUserQuestionOutput]:
        return AskUserQuestionOutput

    async def description(self, input_data: AskUserQuestionInput, options: Dict[str, Any]) -> str:
        return DESCRIPTION

    async def prompt(self, options: Dict[str, Any]) -> str:
        return PROMPT

    def is_read_only(self, input_data: AskUserQuestionInput = None) -> bool:
        return True

    def is_concurrency_safe(self, input_data: AskUserQuestionInput = None) -> bool:
        return True

    def requires_user_interaction(self) -> bool:
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
        context: ToolUseContext
    ) -> ValidationResult:
        """验证输入参数"""
        options = context.get_options()

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

    async def check_permissions(
        self,
        args: AskUserQuestionInput,
        context: ToolUseContext
    ) -> PermissionAskDecision:
        """检查权限：总是需要用户交互"""
        return create_ask_decision(
            message="Answer questions?",
            updated_input=args.model_dump()
        )

    async def call(
        self,
        args: AskUserQuestionInput,
        context: ToolUseContext,
        can_use_tool,
        parent_message,
        on_progress=None
    ) -> ToolResult[AskUserQuestionOutput]:
        """执行用户问答"""
        # 检查是否有答案
        if not args.answers:
            return ToolResult(error="No answers provided by user")

        # 返回结果
        return ToolResult(
            data=AskUserQuestionOutput(
                questions=args.questions,
                answers=args.answers,
                annotations=args.annotations
            )
        )

    def map_tool_result_to_tool_result_block_param(
        self,
        content: AskUserQuestionOutput,
        tool_use_id: str
    ) -> Dict[str, Any]:
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
