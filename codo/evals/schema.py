"""Evaluation result schemas."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TaskTag = Literal["bug-fix", "feature", "refactor", "read-only", "multi-file"]


@dataclass
class ModuleCaseResult:
    module: str
    case_id: str
    passed: bool
    latency_ms: float
    error_message: str | None


@dataclass
class EvalTask:
    id: str
    prompt: str
    tags: list[TaskTag]
    success_criteria: list[str]
    max_turns: int
    workspace_fixture: str
    inject_errors: list[str]


@dataclass
class EvalResult:
    task_id: str
    passed: bool
    turns_used: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    tool_calls: int
    tool_errors: int
    tool_retries: int
    human_interventions: int
    side_effect_files: list[str]
    error_recovery_triggered: bool
    error_recovery_succeeded: bool
    duration_seconds: float
