"""Canonical Textual interaction request models.

定义 UI 层交互请求的标准数据模型。
这些 dataclass 在 UIBridge、dialogs、permission_checker 之间流转，
是工具层发起 UI 交互的统一数据格式。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

@dataclass
class InteractionOption:
    """
    单个交互选项。

    用于权限对话框的按钮选项，或问题对话框的候选答案。

    示例:
        InteractionOption(
            value="allow_once",
            label="本次允许",
            description="仅允许本次操作，下次仍需确认",
            preview="",
        )
    """
    value: str          # 选项的机器可读值，如 "allow_once"、"deny"
    label: str          # 选项的显示文本，如 "本次允许"
    description: str = ""  # 选项的详细说明，显示在选项下方
    preview: str = ""   # 选项的预览内容（如 diff 预览），可选

@dataclass
class InteractionQuestion:
    """
    单个交互问题（用于多问题对话框）。

    AskUserQuestion 工具可以一次提出多个问题，每个问题对应一个 InteractionQuestion。

    示例:
        InteractionQuestion(
            question_id="q_001",
            header="选择处理方式",
            question="你想怎么处理这个冲突文件？",
            options=[
                InteractionOption(value="overwrite", label="覆盖"),
                InteractionOption(value="skip", label="跳过"),
            ],
            multi_select=False,
        )
    """
    question_id: str    # 问题唯一 ID，用于关联答案
    header: str         # 问题标题（粗体显示），如 "选择处理方式"
    question: str       # 问题正文，如 "你想怎么处理这个冲突文件？"
    options: list[InteractionOption] = field(default_factory=list)  # 候选选项列表
    multi_select: bool = False  # 是否允许多选（True 时用逗号分隔多个选项）

@dataclass
class InteractionRequest:
    """
    完整的交互请求，从工具层发往 UI 层。

    [三种交互类型]
    - "permission": 权限确认（Bash/Write 等工具执行前）
    - "question": 多问题问答（AskUserQuestion 工具）
    - "diff_review": 文件变更审阅（Edit/Write 工具的 diff 预览）

    示例（权限请求）:
        InteractionRequest(
            request_id="perm_a1b2c3",
            kind="permission",
            label="等待权限：Bash",
            tool_name="Bash",
            tool_info="rm -rf /tmp/test_dir",
            message="即将删除临时目录",
            options=[
                InteractionOption(value="allow_once", label="本次允许"),
                InteractionOption(value="deny", label="拒绝"),
            ],
        )
    """
    request_id: str     # 请求唯一 ID（UUID），用于 resolve/cancel 时定位
    kind: Literal["permission", "question", "diff_review"]  # 交互类型
    label: str = ""     # 对话框标题，如 "等待权限：Bash"
    tool_name: str = "" # 发起请求的工具名称，如 "Bash"
    tool_info: str = "" # 工具操作详情，如具体命令或文件路径
    message: str = ""   # 附加说明信息
    questions: list[InteractionQuestion] = field(default_factory=list)  # 问题列表（question 类型专用）
    options: list[InteractionOption] = field(default_factory=list)      # 选项列表（permission 类型专用）
    initial_value: Optional[str] = None  # 输入框初始值（自由输入场景）
    validation_rules: dict[str, Any] = field(default_factory=dict)  # 输入验证规则
    payload: dict[str, Any] = field(default_factory=dict)  # 额外数据（diff_review 时含 diff_text 等）
