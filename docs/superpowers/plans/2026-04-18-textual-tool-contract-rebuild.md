# Textual Tool Contract Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the legacy string-first, direct-write tool pipeline with a single structured tool-result contract that powers Textual interactions, staged file review, tool summaries, Todo/Team state, and model-facing `tool_result` blocks from one source of truth.

**Architecture:** The runtime keeps one canonical tool contract in `codo/tools/types.py` plus typed receipt models in `codo/tools/receipts.py`. Tools return structured receipts, staged file changes, and audit events instead of pre-rendered strings or immediate writes. `StreamingToolExecutor`, `query.py`, `UIBridge`, and the Textual widgets consume those objects and derive their own views centrally, while the LLM-facing `tool_result` block is serialized once from the same canonical receipt.

**Tech Stack:** Python 3, asyncio, Pydantic, Textual, pytest

---

## File Map

### New files
- `codo/tools/receipts.py`: typed receipt objects, staged file change objects, audit events, and serializer helpers for model-facing `tool_result` blocks.
- `codo/services/tools/execution_manager.py`: apply or reject staged filesystem changes after UI approval.
- `codo/services/tools/change_review.py`: Textual-only async bridge for diff review interactions.
- `codo/cli/tui/interaction_types.py`: canonical `InteractionRequest` / `InteractionOption` / validation schema for inline Textual interactions.
- `codo/team/execution_logger.py`: in-memory per-agent audit log storage for the deep-audit drawer.
- `tests/tools/test_tool_result_contract.py`: contract-level tests for receipts, staged changes, and serializer output.
- `tests/tools/test_staged_file_changes.py`: tests for `Write` / `Edit` staging and `ExecutionManager`.
- `tests/test_query_tool_receipts.py`: tests that `query.py` emits structured tool-result events.

### Modified files
- `codo/tools/types.py`: extend `ToolResult` with `receipt`, `staged_changes`, and `audit_events`.
- `codo/tools/base.py`: expose receipt helpers in the active tool base and keep the active tool protocol explicit.
- `codo/tools/write_tool/write_tool.py`: stop writing immediately; return a staged change + diff receipt.
- `codo/tools/edit_tool/edit_tool.py`: stop writing immediately; return a staged change + diff receipt.
- `codo/services/tools/streaming_executor.py`: emit structured `ToolUpdate` objects and request review for staged changes.
- `codo/query.py`: forward `receipt`, `staged_changes`, and audit metadata as first-class events.
- `codo/cli/tui/bridge.py`: consume structured tool events, own `pending_interactions`, expose richer snapshots, and subscribe to Team observers.
- `codo/cli/tui/app.py`: render inline review/permission/question components from canonical interaction requests.
- `codo/cli/tui/dialogs.py`: render inline approval strips and selection cards for interaction requests.
- `codo/cli/tui/styles.tcss`: style review strips, diff receipts, audit drawer, and remove stale modal-only assumptions.
- `codo/cli/interactive_dialogs.py`: remain Textual-only, but proxy through the new interaction request schema.
- `codo/services/tools/permission_prompt.py`: build structured permission requests instead of ad hoc strings.
- `codo/team/background_tasks.py`: add optional `current_action`.
- `codo/team/mailbox.py`: expose mailbox listeners for UI observation.
- `codo/team/team_manager.py`: forward mailbox events and keep agent state observable.
- `codo/tools_registry.py`: import the active tool base only.
- `codo/services/prompt/tools.py`: stop importing `codo.tool.Tool`.
- `codo/services/tools/orchestration.py`: stop importing legacy tool types.
- `codo/services/tools/permission_checker.py`: stop importing legacy tool types.
- `codo/services/tools/permission_rules.py`: stop importing legacy tool types.
- `tests/services/test_streaming_executor.py`: assert structured `ToolUpdate` output.
- `tests/tools/test_tools.py`: update `Write` / `Edit` expectations from “immediate write” to “staged then applied”.
- `tests/cli/test_tui_bridge.py`: assert request-id based interactions and receipt-driven snapshots.
- `tests/cli/test_tui_dialogs.py`: assert diff review controls and inline resolution.

### Files to delete after import migration passes
- `codo/tool.py`
- `codo/types/tools.py`

---

### Task 1: Canonical Tool Result Contract

**Files:**
- Create: `codo/tools/receipts.py`
- Modify: `codo/tools/types.py`
- Modify: `codo/tools/base.py`
- Test: `tests/tools/test_tool_result_contract.py`

- [ ] **Step 1: Write the failing contract tests**

```python
from codo.tools.receipts import CommandReceipt, DiffReceipt, ProposedFileChange, render_receipt_for_model
from codo.tools.types import ToolResult

def test_tool_result_keeps_structured_receipt_and_staged_changes():
    receipt = DiffReceipt(
        kind="diff",
        summary="Update app.py",
        path="C:/tmp/app.py",
        diff_text="@@ -1 +1 @@\n-old\n+new",
        change_id="chg_1",
    )
    change = ProposedFileChange(
        change_id="chg_1",
        path="C:/tmp/app.py",
        original_content="old",
        new_content="new",
        diff_text="@@ -1 +1 @@\n-old\n+new",
        source_tool="Edit",
    )

    result = ToolResult(data=None, receipt=receipt, staged_changes=[change])

    assert result.receipt == receipt
    assert result.staged_changes[0].path.endswith("app.py")

def test_render_receipt_for_model_builds_single_tool_result_block():
    receipt = CommandReceipt(
        kind="command",
        summary="Ran pytest",
        command="pytest -q",
        exit_code=1,
        stdout="collected 3 items",
        stderr="1 failed",
    )

    block = render_receipt_for_model(receipt, tool_use_id="tool-1")

    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "tool-1"
    assert "pytest -q" in block["content"]
    assert "1 failed" in block["content"]
```

- [ ] **Step 2: Run the new tests to verify the contract is missing**

Run: `pytest C:\Users\tzm\Desktop\test\Codo_new\tests\tools\test_tool_result_contract.py -v`
Expected: FAIL with import errors for `codo.tools.receipts` or missing `ToolResult` fields such as `receipt`.

- [ ] **Step 3: Add the canonical receipt and staged-change types**

```python
# codo/tools/receipts.py
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

ToolReceipt = Union[CommandReceipt, DiffReceipt, GenericReceipt]

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
    else:
        content = f"{receipt.summary}\n{receipt.body}".strip()

    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
    }
```

```python
# codo/tools/types.py
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Generic, Optional

from .receipts import AuditLogEvent, ProposedFileChange, ToolReceipt

@dataclass
class ToolResult(Generic[OutputT]):
    data: Optional[OutputT] = None
    error: Optional[str] = None
    receipt: Optional[ToolReceipt] = None
    staged_changes: list[ProposedFileChange] = field(default_factory=list)
    audit_events: list[AuditLogEvent] = field(default_factory=list)
    new_messages: Optional[list] = None
    context_modifier: Optional[Callable] = None
    mcp_meta: Optional[Dict[str, Any]] = None
```

```python
# codo/tools/base.py
from .receipts import ToolReceipt

class Tool(ABC, Generic[InputT, OutputT, ProgressT]):
    ...
    def build_default_receipt(
        self,
        result: ToolResult[OutputT],
        input: Optional[InputT] = None,
    ) -> Optional[ToolReceipt]:
        return result.receipt
```

- [ ] **Step 4: Run the contract tests again**

Run: `pytest C:\Users\tzm\Desktop\test\Codo_new\tests\tools\test_tool_result_contract.py -v`
Expected: PASS

- [ ] **Step 5: Commit the contract layer**

```bash
git -C C:\Users\tzm\Desktop\test\Codo_new add \
  codo/tools/receipts.py \
  codo/tools/types.py \
  codo/tools/base.py \
  tests/tools/test_tool_result_contract.py
git -C C:\Users\tzm\Desktop\test\Codo_new commit -m "refactor: add canonical tool receipt contract"
```

### Task 2: Stream Structured Tool Updates Through Executor and Query

**Files:**
- Modify: `codo/services/tools/streaming_executor.py`
- Modify: `codo/query.py`
- Modify: `tests/services/test_streaming_executor.py`
- Test: `tests/test_query_tool_receipts.py`

- [ ] **Step 1: Write failing executor and query tests**

```python
import pytest
from unittest.mock import Mock

from codo.services.tools.streaming_executor import StreamingToolExecutor
from codo.tools.receipts import CommandReceipt
from codo.tools.types import ToolResult

class ReceiptTool:
    name = "Bash"
    is_concurrency_safe = False

    class input_schema:
        def __init__(self, **kwargs):
            self.command = kwargs["command"]

    async def call(self, input_data, context, *args):
        return ToolResult(
            data=None,
            receipt=CommandReceipt(
                kind="command",
                summary="Ran pytest",
                command=input_data.command,
                exit_code=0,
                stdout="ok",
            ),
        )

@pytest.mark.asyncio
async def test_executor_returns_structured_receipt():
    executor = StreamingToolExecutor([ReceiptTool()], {}, max_concurrency=1)
    executor.add_tool(
        {"id": "tool-1", "name": "Bash", "input": {"command": "pytest -q"}},
        {"role": "assistant", "content": []},
    )

    results = [result async for result in executor.get_remaining_results()]

    assert results[0].receipt.kind == "command"
    assert results[0].message["content"][0]["content"].startswith("Ran pytest")
```

```python
import pytest

from codo.query import QueryParams, query

@pytest.mark.asyncio
async def test_query_emits_receipt_dict(fake_query_params: QueryParams):
    events = [event async for event in query(fake_query_params)]
    tool_result = next(event for event in events if isinstance(event, dict) and event.get("type") == "tool_result")

    assert tool_result["receipt"]["kind"] == "command"
    assert tool_result["content"] == "Ran pytest"
```

- [ ] **Step 2: Run the executor/query tests to observe the current string-only behavior**

Run: `pytest C:\Users\tzm\Desktop\test\Codo_new\tests\services\test_streaming_executor.py C:\Users\tzm\Desktop\test\Codo_new\tests\test_query_tool_receipts.py -v`
Expected: FAIL because `ToolUpdate` has no `receipt` field and `query.py` only yields flattened `content`.

- [ ] **Step 3: Refactor the executor and query pipeline to keep structured updates**

```python
# codo/services/tools/streaming_executor.py
from codo.tools.receipts import GenericReceipt, receipt_to_dict, render_receipt_for_model

@dataclass
class ToolUpdate:
    message: Optional[Dict[str, Any]] = None
    context_modifier: Optional[Callable] = None
    tool_use_id: Optional[str] = None
    receipt: Optional[Any] = None
    staged_changes: List[Any] = field(default_factory=list)
    audit_events: List[Any] = field(default_factory=list)
    content: Optional[str] = None
    is_error: bool = False
    status: Optional[str] = None
    duration: Optional[float] = None

def _build_tool_update(self, tool: TrackedTool, result: Any) -> ToolUpdate:
    receipt = result.receipt or GenericReceipt(
        kind="generic",
        summary=self._extract_tool_content(tool.block["name"], result.data),
        body="",
    )
    message = {
        "role": "user",
        "content": [render_receipt_for_model(receipt, tool.id)],
    }
    return ToolUpdate(
        message=message,
        tool_use_id=tool.id,
        receipt=receipt,
        staged_changes=list(result.staged_changes),
        audit_events=list(result.audit_events),
        content=receipt.summary,
        is_error=result.error is not None,
        status="completed",
        duration=tool.duration,
    )
```

```python
# codo/query.py
for result in completed_results:
    if result.message:
        messages_for_query.append(result.message)

    yield {
        "type": "tool_result",
        "tool_use_id": result.tool_use_id,
        "content": result.content or "",
        "receipt": receipt_to_dict(result.receipt) if result.receipt else None,
        "staged_changes": [change.__dict__ for change in result.staged_changes],
        "audit_events": [event.__dict__ for event in result.audit_events],
        "is_error": result.is_error,
        "status": result.status,
    }
```

- [ ] **Step 4: Run the executor and query tests again**

Run: `pytest C:\Users\tzm\Desktop\test\Codo_new\tests\services\test_streaming_executor.py C:\Users\tzm\Desktop\test\Codo_new\tests\test_query_tool_receipts.py -v`
Expected: PASS

- [ ] **Step 5: Commit the structured streaming pipeline**

```bash
git -C C:\Users\tzm\Desktop\test\Codo_new add \
  codo/services/tools/streaming_executor.py \
  codo/query.py \
  tests/services/test_streaming_executor.py \
  tests/test_query_tool_receipts.py
git -C C:\Users\tzm\Desktop\test\Codo_new commit -m "refactor: stream typed tool receipts through query"
```

### Task 3: Convert Write and Edit to Two-Phase Commit

**Files:**
- Create: `codo/services/tools/execution_manager.py`
- Modify: `codo/tools/write_tool/write_tool.py`
- Modify: `codo/tools/edit_tool/edit_tool.py`
- Modify: `tests/tools/test_tools.py`
- Test: `tests/tools/test_staged_file_changes.py`

- [ ] **Step 1: Write failing tests that prove file mutations must be staged**

```python
import pytest

from codo.services.tools.execution_manager import ExecutionManager
from codo.tools.edit_tool import EditToolInput, edit_tool
from codo.tools.write_tool import WriteToolInput, write_tool

@pytest.mark.asyncio
async def test_write_tool_returns_staged_change_before_apply(tmp_path):
    target = tmp_path / "note.txt"
    result = await write_tool.call(
        WriteToolInput(file_path=str(target), content="hello"),
        {"messages": [], "options": {}},
        lambda *args, **kwargs: True,
        None,
    )

    assert result.staged_changes
    assert not target.exists()

    manager = ExecutionManager()
    receipt = await manager.apply_staged_change(result.staged_changes[0])

    assert receipt.kind == "diff"
    assert target.read_text() == "hello"

@pytest.mark.asyncio
async def test_edit_tool_returns_staged_change_before_apply(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("before world")

    result = await edit_tool.call(
        EditToolInput(
            file_path=str(target),
            old_string="world",
            new_string="after",
            replace_all=False,
        ),
        {"messages": [], "options": {}},
        lambda *args, **kwargs: True,
        None,
    )

    assert target.read_text() == "before world"
    assert result.staged_changes[0].new_content == "before after"
```

- [ ] **Step 2: Run the staging tests to confirm `Write` and `Edit` still write immediately**

Run: `pytest C:\Users\tzm\Desktop\test\Codo_new\tests\tools\test_staged_file_changes.py C:\Users\tzm\Desktop\test\Codo_new\tests\tools\test_tools.py -v`
Expected: FAIL because `Write` / `Edit` currently call `fs.writeFile(...)` inside the tool.

- [ ] **Step 3: Introduce `ExecutionManager` and change file tools to emit staged changes**

```python
# codo/services/tools/execution_manager.py
from codo.tools.receipts import DiffReceipt, ProposedFileChange
from codo.utils.fs_operations import getFsImplementation

class ExecutionManager:
    async def apply_staged_change(self, change: ProposedFileChange) -> DiffReceipt:
        fs = getFsImplementation()
        fs.writeFile(change.path, change.new_content)
        return DiffReceipt(
            kind="diff",
            summary=f"Applied changes to {change.path}",
            path=change.path,
            diff_text=change.diff_text,
            change_id=change.change_id,
        )

    async def reject_staged_change(self, change: ProposedFileChange) -> DiffReceipt:
        return DiffReceipt(
            kind="diff",
            summary=f"Rejected changes to {change.path}",
            path=change.path,
            diff_text=change.diff_text,
            change_id=change.change_id,
        )
```

```python
# codo/tools/write_tool/write_tool.py
from uuid import uuid4

from ..receipts import DiffReceipt, ProposedFileChange

change = ProposedFileChange(
    change_id=f"chg_{uuid4().hex[:12]}",
    path=file_path,
    original_content="" if is_new else original,
    new_content=input_data.content,
    diff_text=diff or generateUnifiedDiff("", input_data.content, file_path, file_path),
    source_tool=self.name,
)

return ToolResult(
    data=WriteToolOutput(
        type="create" if is_new else "update",
        filePath=file_path,
        content=input_data.content,
        diff=change.diff_text,
    ),
    receipt=DiffReceipt(
        kind="diff",
        summary=f"Prepared {'create' if is_new else 'update'} for {file_path}",
        path=file_path,
        diff_text=change.diff_text,
        change_id=change.change_id,
    ),
    staged_changes=[change],
)
```

```python
# codo/tools/edit_tool/edit_tool.py
from uuid import uuid4

from ..receipts import DiffReceipt, ProposedFileChange

change = ProposedFileChange(
    change_id=f"chg_{uuid4().hex[:12]}",
    path=file_path,
    original_content=original,
    new_content=modified,
    diff_text=diff,
    source_tool=self.name,
)

return ToolResult(
    data=EditToolOutput(
        filePath=file_path,
        diff=diff,
        linesChanged=lines_changed,
    ),
    receipt=DiffReceipt(
        kind="diff",
        summary=f"Prepared edit for {file_path}",
        path=file_path,
        diff_text=diff,
        change_id=change.change_id,
    ),
    staged_changes=[change],
)
```

- [ ] **Step 4: Re-run the staging tests**

Run: `pytest C:\Users\tzm\Desktop\test\Codo_new\tests\tools\test_staged_file_changes.py C:\Users\tzm\Desktop\test\Codo_new\tests\tools\test_tools.py -v`
Expected: PASS after `tests/tools/test_tools.py` is updated to apply staged changes before asserting final file contents.

- [ ] **Step 5: Commit the two-phase file mutation layer**

```bash
git -C C:\Users\tzm\Desktop\test\Codo_new add \
  codo/services/tools/execution_manager.py \
  codo/tools/write_tool/write_tool.py \
  codo/tools/edit_tool/edit_tool.py \
  tests/tools/test_staged_file_changes.py \
  tests/tools/test_tools.py
git -C C:\Users\tzm\Desktop\test\Codo_new commit -m "refactor: stage file mutations behind execution manager"
```

### Task 4: Replace Ad Hoc Blocking Interactions With Canonical Async Requests

**Files:**
- Create: `codo/cli/tui/interaction_types.py`
- Create: `codo/services/tools/change_review.py`
- Modify: `codo/cli/tui/bridge.py`
- Modify: `codo/cli/tui/app.py`
- Modify: `codo/cli/tui/dialogs.py`
- Modify: `codo/cli/interactive_dialogs.py`
- Modify: `codo/services/tools/permission_prompt.py`
- Modify: `tests/cli/test_tui_bridge.py`
- Modify: `tests/cli/test_tui_dialogs.py`

- [ ] **Step 1: Write failing bridge/dialog tests for request-id based diff review**

```python
import asyncio

import pytest

from codo.cli.tui.bridge import UIBridge
from codo.cli.tui.interaction_types import InteractionRequest, InteractionOption

@pytest.mark.asyncio
async def test_bridge_resolves_diff_review_by_request_id(dummy_engine):
    bridge = UIBridge(dummy_engine)
    request = InteractionRequest(
        request_id="req_diff_1",
        kind="diff_review",
        label="Review app.py",
        message="Apply these changes?",
        options=[
            InteractionOption(value="accept", label="Accept"),
            InteractionOption(value="reject", label="Reject"),
        ],
        payload={"path": "C:/tmp/app.py"},
    )

    pending = asyncio.create_task(bridge.request_interaction(request))
    await asyncio.sleep(0)

    snapshot = bridge.get_snapshot()
    assert snapshot.interaction.request_id == "req_diff_1"
    assert snapshot.interaction.kind == "diff_review"

    bridge.resolve_interaction("req_diff_1", "accept")
    assert await pending == "accept"
```

- [ ] **Step 2: Run the interaction tests to confirm the bridge still uses request-specific helpers plus `begin_interaction`**

Run: `pytest C:\Users\tzm\Desktop\test\Codo_new\tests\cli\test_tui_bridge.py C:\Users\tzm\Desktop\test\Codo_new\tests\cli\test_tui_dialogs.py -v`
Expected: FAIL because there is no generic `InteractionRequest` path and no `diff_review` kind.

- [ ] **Step 3: Introduce `InteractionRequest` and route all UI interactions through it**

```python
# codo/cli/tui/interaction_types.py
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

@dataclass
class InteractionOption:
    value: str
    label: str
    description: str = ""
    preview: str = ""

@dataclass
class InteractionRequest:
    request_id: str
    kind: Literal["permission", "question", "diff_review"]
    label: str
    message: str = ""
    options: list[InteractionOption] = field(default_factory=list)
    initial_value: Optional[str] = None
    validation_rules: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
```

```python
# codo/cli/tui/bridge.py
async def request_interaction(self, request: InteractionRequest) -> Any:
    loop = asyncio.get_running_loop()
    future: asyncio.Future[Any] = loop.create_future()
    self._pending_interactions[request.request_id] = future
    self._active_interaction = request
    self.notify()
    try:
        return await future
    finally:
        self._clear_interaction(request.request_id)

async def request_permission(self, tool_name: str, tool_info: str, message: str = "") -> Optional[str]:
    return await self.request_interaction(
        InteractionRequest(
            request_id=str(uuid4()),
            kind="permission",
            label=f"Awaiting approval for {tool_name}",
            message=message,
            payload={"tool_name": tool_name, "tool_info": tool_info},
            options=[
                InteractionOption(value="allow_once", label="Allow Once"),
                InteractionOption(value="allow_always", label="Allow Session"),
                InteractionOption(value="deny", label="Deny"),
                InteractionOption(value="abort", label="Abort"),
            ],
        )
    )
```

```python
# codo/services/tools/change_review.py
from codo.cli.tui.runtime import get_active_app
from codo.tools.receipts import ProposedFileChange

async def request_change_review(change: ProposedFileChange) -> str:
    app = get_active_app()
    if app is None or not hasattr(app, "request_change_review"):
        raise RuntimeError("Textual app is required for staged change review")
    return await app.request_change_review(change)
```

```python
# codo/cli/tui/dialogs.py
def build_inline_interaction(request: InteractionRequest) -> Widget:
    if request.kind == "diff_review":
        return DiffApprovalStrip(request)
    if request.kind == "permission":
        return PermissionStrip(request)
    return QuestionCard(request)
```

- [ ] **Step 4: Re-run the interaction tests**

Run: `pytest C:\Users\tzm\Desktop\test\Codo_new\tests\cli\test_tui_bridge.py C:\Users\tzm\Desktop\test\Codo_new\tests\cli\test_tui_dialogs.py -v`
Expected: PASS

- [ ] **Step 5: Commit the unified interaction schema**

```bash
git -C C:\Users\tzm\Desktop\test\Codo_new add \
  codo/cli/tui/interaction_types.py \
  codo/services/tools/change_review.py \
  codo/cli/tui/bridge.py \
  codo/cli/tui/app.py \
  codo/cli/tui/dialogs.py \
  codo/cli/interactive_dialogs.py \
  codo/services/tools/permission_prompt.py \
  tests/cli/test_tui_bridge.py \
  tests/cli/test_tui_dialogs.py
git -C C:\Users\tzm\Desktop\test\Codo_new commit -m "refactor: unify textual interactions under request ids"
```

### Task 5: Feed Team, Todo, and Audit UI From Observers Instead of Shallow Snapshots

**Files:**
- Create: `codo/team/execution_logger.py`
- Modify: `codo/team/background_tasks.py`
- Modify: `codo/team/mailbox.py`
- Modify: `codo/team/team_manager.py`
- Modify: `codo/cli/tui/bridge.py`
- Modify: `tests/cli/test_tui_bridge.py`

- [ ] **Step 1: Write failing bridge tests for mailbox observation and per-agent audit logs**

```python
import pytest

from codo.team import get_team_manager
from codo.team.message_types import MessageType

@pytest.mark.asyncio
async def test_bridge_tracks_mailbox_events_in_agent_audit(dummy_engine):
    bridge = UIBridge(dummy_engine)
    team_manager = get_team_manager()
    team_manager.register_agent("agent-1", "worker")
    team_manager.register_agent("leader", "leader")

    await team_manager.send_message(
        from_agent="leader",
        to_agent="agent-1",
        message_type=MessageType.TASK,
        content="Patch the failing test",
    )

    snapshot = bridge.get_snapshot()
    agent = next(item for item in snapshot.agents if item.agent_id == "agent-1")

    assert agent.status in {"active", "waiting", "thinking", "idle"}
    assert bridge.get_logs("agent-1")[-1].message == "Patch the failing test"
```

- [ ] **Step 2: Run the bridge tests to confirm mailbox traffic is still invisible to the UI**

Run: `pytest C:\Users\tzm\Desktop\test\Codo_new\tests\cli\test_tui_bridge.py -v`
Expected: FAIL because `Mailbox` has no listener support and `UIBridge` has no `get_logs()` data source.

- [ ] **Step 3: Add observer hooks, current action, and execution logging**

```python
# codo/team/background_tasks.py
@dataclass
class BackgroundTask:
    ...
    current_action: Optional[str] = None
```

```python
# codo/team/mailbox.py
class Mailbox:
    def __init__(self):
        ...
        self._listeners: list[Callable[[Message], Awaitable[None]]] = []

    def register_listener(self, listener: Callable[[Message], Awaitable[None]]) -> None:
        self._listeners.append(listener)

    def unregister_listener(self, listener: Callable[[Message], Awaitable[None]]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    async def send(self, message: Message) -> None:
        async with self._locks[message.to_agent]:
            self._messages[message.to_agent].append(message)
        for listener in list(self._listeners):
            await listener(message)
```

```python
# codo/team/execution_logger.py
from collections import defaultdict

class ExecutionLogger:
    def __init__(self) -> None:
        self._events: dict[str, list[AuditLogEvent]] = defaultdict(list)

    def record(self, event: AuditLogEvent) -> None:
        self._events[event.agent_id].append(event)

    def get_logs(self, agent_id: str) -> list[AuditLogEvent]:
        return list(self._events.get(agent_id, []))
```

```python
# codo/cli/tui/bridge.py
self._team_manager = get_team_manager()
self._execution_logger = ExecutionLogger()
self._team_manager.mailbox.register_listener(self._on_mailbox_message)

async def _on_mailbox_message(self, message: Any) -> None:
    event = AuditLogEvent(
        event_id=str(uuid4()),
        agent_id=message.to_agent,
        source="mailbox",
        message=message.content,
        created_at=_now(),
        metadata={"from_agent": message.from_agent, "type": str(message.type)},
    )
    self._execution_logger.record(event)
    self._last_visible_agent_id = message.to_agent
    self.notify()

def get_logs(self, agent_id: str) -> list[AuditLogEvent]:
    return self._execution_logger.get_logs(agent_id)
```

- [ ] **Step 4: Re-run the bridge tests**

Run: `pytest C:\Users\tzm\Desktop\test\Codo_new\tests\cli\test_tui_bridge.py -v`
Expected: PASS

- [ ] **Step 5: Commit the Team observer and audit layer**

```bash
git -C C:\Users\tzm\Desktop\test\Codo_new add \
  codo/team/execution_logger.py \
  codo/team/background_tasks.py \
  codo/team/mailbox.py \
  codo/team/team_manager.py \
  codo/cli/tui/bridge.py \
  tests/cli/test_tui_bridge.py
git -C C:\Users\tzm\Desktop\test\Codo_new commit -m "feat: observe team mailbox and expose audit logs to tui"
```

### Task 6: Remove Legacy Tool Protocol Imports and Verify the Unified Runtime

**Files:**
- Modify: `codo/tools_registry.py`
- Modify: `codo/services/prompt/tools.py`
- Modify: `codo/services/tools/orchestration.py`
- Modify: `codo/services/tools/permission_checker.py`
- Modify: `codo/services/tools/permission_rules.py`
- Delete: `codo/tool.py`
- Delete: `codo/types/tools.py`
- Test: `tests/cli/test_main_textual_entry.py`
- Test: `tests/tools/test_tool_result_contract.py`
- Test: `tests/services/test_streaming_executor.py`
- Test: `tests/tools/test_tools.py`
- Test: `tests/cli/test_tui_bridge.py`

- [ ] **Step 1: Write a failing regression test that locks the runtime onto the active tool base**

```python
from codo.tools.base import Tool as RuntimeTool
from codo.tools_registry import get_all_tools

def test_all_registered_tools_use_active_runtime_base():
    tools = get_all_tools()
    assert tools
    assert all(isinstance(tool, RuntimeTool) for tool in tools)
```

- [ ] **Step 2: Run import and regression checks to capture legacy references**

Run: `rg -n "from codo\.tool|from codo\.types\.tools" C:\Users\tzm\Desktop\test\Codo_new\codo C:\Users\tzm\Desktop\test\Codo_new\tests`
Expected: output in `codo/tools_registry.py`, `codo/services/prompt/tools.py`, `codo/services/tools/orchestration.py`, `codo/services/tools/permission_checker.py`, and `codo/services/tools/permission_rules.py`.

Run: `pytest C:\Users\tzm\Desktop\test\Codo_new\tests\cli\test_main_textual_entry.py C:\Users\tzm\Desktop\test\Codo_new\tests\cli\test_tui_bridge.py C:\Users\tzm\Desktop\test\Codo_new\tests\services\test_streaming_executor.py C:\Users\tzm\Desktop\test\Codo_new\tests\tools\test_tools.py -v`
Expected: PASS before deletion, then FAIL once `codo/tool.py` and `codo/types/tools.py` are removed if any live import remains.

- [ ] **Step 3: Point all live imports at the active runtime and delete the dead modules**

```python
# codo/tools_registry.py
-from codo.tool import Tool
+from codo.tools.base import Tool
```

```python
# codo/services/prompt/tools.py
-from codo.tool import Tool
+from codo.tools.base import Tool
```

```python
# codo/services/tools/permission_checker.py
-from codo.tool import Tool
-from codo.types.tools import ToolUseContext
+from codo.tools.base import Tool, ToolUseContext
```

```python
# codo/services/tools/permission_rules.py
-from codo.tool import Tool
+from codo.tools.base import Tool
```

```python
# codo/services/tools/orchestration.py
-from codo.tool import Tool
+from codo.tools.base import Tool
```

```bash
git -C C:\Users\tzm\Desktop\test\Codo_new rm codo/tool.py codo/types/tools.py
```

- [ ] **Step 4: Run the full regression suite plus import scan**

Run: `rg -n "from codo\.tool|from codo\.types\.tools" C:\Users\tzm\Desktop\test\Codo_new\codo C:\Users\tzm\Desktop\test\Codo_new\tests`
Expected: no output

Run: `pytest C:\Users\tzm\Desktop\test\Codo_new\tests\cli C:\Users\tzm\Desktop\test\Codo_new\tests\services\test_streaming_executor.py C:\Users\tzm\Desktop\test\Codo_new\tests\tools -v`
Expected: PASS

- [ ] **Step 5: Commit the unified runtime cleanup**

```bash
git -C C:\Users\tzm\Desktop\test\Codo_new add -A
git -C C:\Users\tzm\Desktop\test\Codo_new commit -m "chore: remove legacy tool protocol runtime"
```

## Self-Review

### Spec coverage
- Structured `ToolResult` contract: Task 1
- Structured executor/query events: Task 2
- Staged write/edit two-phase commit: Task 3
- Async request-id based Textual interactions: Task 4
- Team mailbox observation, current action, audit log: Task 5
- Unified runtime and legacy cleanup: Task 6

### Placeholder scan
- No `TODO`, `TBD`, or “implement later” markers remain.
- Every code-changing step includes concrete file paths, code snippets, and commands.

### Type consistency
- Canonical names are `ToolReceipt`, `ProposedFileChange`, `AuditLogEvent`, `InteractionRequest`, and `current_action`.
- `ToolUpdate.receipt` and `query.py` event payloads use the same naming.
- The model-facing serializer is centralized as `render_receipt_for_model`, not duplicated across tools.

Plan complete and saved to `docs/superpowers/plans/2026-04-18-textual-tool-contract-rebuild.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
