"""Runtime data shared by core services and desktop UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypedDict

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject = dict[str, JsonValue]
InteractionData = str | None


class ThinkingConfig(TypedDict):
    type: Literal["enabled"]
    budget_tokens: int


class CheckpointMetadata(TypedDict, total=False):
    messages_count: int
    turn_count: int
    phase: str
    reason: str
    active_tool_ids: list[str]
    pending_interaction: JsonObject | None


class ExecutionOptions(TypedDict, total=False):
    api_client: object
    model: str
    tools: list[object]
    system_prompt: str
    normalize_question_mark: bool
    app_state: JsonObject


class ExecutionContext(TypedDict, total=False):
    cwd: str
    session_id: str
    permission_context: object
    abort_controller: object
    runtime_controller: object
    interaction_broker: object
    phase_tracker: object
    queued_commands: list[str]
    ide_selection: JsonObject | None
    mode: str
    options: ExecutionOptions


@dataclass(frozen=True)
class InteractionOption:
    value: str
    label: str
    description: str = ""
    preview: str = ""


@dataclass(frozen=True)
class InteractionQuestion:
    question_id: str
    header: str
    question: str
    options: list[InteractionOption] = field(default_factory=list)
    multi_select: bool = False


@dataclass(frozen=True)
class InteractionRequest:
    request_id: str
    kind: Literal["permission", "question", "diff_review"]
    label: str = ""
    tool_name: str = ""
    tool_info: str = ""
    message: str = ""
    questions: list[InteractionQuestion] = field(default_factory=list)
    options: list[InteractionOption] = field(default_factory=list)
    initial_value: str | None = None
    validation_rules: JsonObject = field(default_factory=dict)
    payload: JsonObject = field(default_factory=dict)
