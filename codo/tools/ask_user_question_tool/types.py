"""用户提问工具的数据类型定义。"""

from pydantic import BaseModel, Field


class QuestionOption(BaseModel):
    """问题选项"""
    label: str = Field(description="选项展示文本，建议 1-5 个词")
    description: str | None = Field(default=None, description="对该选项含义的可选说明")
    preview: str | None = Field(default=None, description="可选的预览内容，可为 Markdown 或 HTML")

class Question(BaseModel):
    """问题定义"""
    question: str = Field(description="完整的问题文本，必须以问号结尾")
    header: str = Field(description="展示为标签的短标题，最长 12 个字符")
    options: list[QuestionOption] = Field(description="该问题的选项列表，数量为 2-4 个")
    multiSelect: bool = Field(default=False, description="是否允许多选")

class QuestionAnnotation(BaseModel):
    """问题注释"""
    preview: str | None = Field(default=None, description="已选选项对应的预览内容")
    notes: str | None = Field(default=None, description="用户补充说明")

class AskUserQuestionInput(BaseModel):
    """用户提问工具的输入参数。"""
    questions: list[Question] = Field(description="需要向用户提问的问题列表，数量为 1-4 个")
    answers: dict[str, str] | None = Field(default=None, description="用户给出的答案，由交互组件回填")
    annotations: dict[str, QuestionAnnotation] | None = Field(default=None, description="按问题记录的附加注释")
    metadata: dict[str, str] | None = Field(default=None, description="可选的跟踪元数据")

class AskUserQuestionOutput(BaseModel):
    """用户提问工具的输出结果。"""
    questions: list[Question] = Field(description="实际向用户展示过的问题列表")
    answers: dict[str, str] = Field(description="问题文本到答案文本的映射")
    annotations: dict[str, QuestionAnnotation] | None = Field(default=None, description="按问题记录的附加注释")
