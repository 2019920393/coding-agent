from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Optional, Union

@dataclass
class CommandReceipt:
    kind: Literal["command"]
    summary: str
    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""

@dataclass
class DiffReceipt:
    kind: Literal["diff"]
    summary: str
    path: str
    diff_text: str
    change_id: Optional[str] = None

@dataclass
class GenericReceipt:
    kind: Literal["generic"]
    summary: str
    body: str = ""

@dataclass
class AgentReceipt:
    kind: Literal["agent"]
    summary: str
    agent_id: str = ""
    agent_type: str = ""
    mode: str = ""
    task_id: Optional[str] = None
    background: bool = False
    status: str = "completed"
    result_preview: str = ""
    total_tokens: int = 0

@dataclass
class ProposedFileChange:
    change_id: str
    path: str
    original_content: str
    new_content: str
    diff_text: str
    source_tool: str
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass
class AuditLogEvent:
    event_id: str
    agent_id: str
    source: str
    message: str
    created_at: float
    metadata: dict[str, Any] = field(default_factory=dict)

ToolReceipt = Union[CommandReceipt, DiffReceipt, GenericReceipt, AgentReceipt]

def receipt_to_dict(receipt: ToolReceipt) -> dict[str, Any]:
    return asdict(receipt)

def render_receipt_for_model(receipt: ToolReceipt, tool_use_id: str) -> dict[str, Any]:
    if receipt.kind == "command":
        content = (
            f"{receipt.summary}\n"
            f"$ {receipt.command}\n"
            f"exit_code={receipt.exit_code}\n"
            f"{receipt.stdout}".strip()
        )
        if receipt.stderr:
            content = f"{content}\n<stderr>\n{receipt.stderr}\n</stderr>"
    elif receipt.kind == "diff":
        content = f"{receipt.summary}\n{receipt.path}\n{receipt.diff_text}"
    elif receipt.kind == "agent":
        lines = [receipt.summary]
        if receipt.agent_type or receipt.status:
            lines.append(
                " · ".join(
                    value
                    for value in [receipt.agent_type, receipt.mode, receipt.status]
                    if value
                )
            )
        if receipt.task_id:
            lines.append(f"task_id={receipt.task_id}")
        if receipt.result_preview:
            lines.append(receipt.result_preview)
        content = "\n".join(line for line in lines if line).strip()
    else:
        content = f"{receipt.summary}\n{receipt.body}".strip()

    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
    }
