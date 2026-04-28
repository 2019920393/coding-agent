"""Canonical Textual interaction request models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

@dataclass
class InteractionOption:
    value: str
    label: str
    description: str = ""
    preview: str = ""

@dataclass
class InteractionQuestion:
    question_id: str
    header: str
    question: str
    options: list[InteractionOption] = field(default_factory=list)
    multi_select: bool = False

@dataclass
class InteractionRequest:
    request_id: str
    kind: Literal["permission", "question", "diff_review"]
    label: str = ""
    tool_name: str = ""
    tool_info: str = ""
    message: str = ""
    questions: list[InteractionQuestion] = field(default_factory=list)
    options: list[InteractionOption] = field(default_factory=list)
    initial_value: Optional[str] = None
    validation_rules: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
