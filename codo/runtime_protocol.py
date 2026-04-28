"""Canonical runtime protocol for the rebuilt Textual-driven execution flow."""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Dict, Optional

# 把复杂对象（数据类、列表、字典嵌套）转换成纯 JSON 友好的普通字典 / 列表 / 基础类型
def _serialize(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    return value

#核心作用是定义一个「运行时检查点」的数据结构
@dataclass
class RuntimeCheckpoint:
    checkpoint_id: str
    phase: str
    turn_count: int
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass
class RuntimeEvent:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def as_legacy_event(self) -> dict[str, Any]:
        return {"type": self.type, **_serialize(self.payload)}

@dataclass
class RuntimeCommand:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)

class QueryRuntimeController:
    """Bidirectional runtime bridge between query execution and Textual UI."""

    _SENTINEL = object()
    _COMMAND_SENTINEL = object()

    def __init__(self) -> None:
        self._events: asyncio.Queue[Any] = asyncio.Queue()
        self._commands: asyncio.Queue[Any] = asyncio.Queue()
        self._pending_interactions: dict[str, asyncio.Future[Any]] = {}
        self._checkpoints: dict[str, RuntimeCheckpoint] = {}
        self._latest_checkpoint_id: Optional[str] = None

    async def emit(self, event: Any) -> None:
        await self._events.put(event)

    async def emit_runtime_event(self, event_type: str, **payload: Any) -> None:
        await self.emit(RuntimeEvent(type=event_type, payload=payload))

    async def finish(self) -> None:
        await self._events.put(self._SENTINEL)
        await self._commands.put(self._COMMAND_SENTINEL)

    async def next_event(self) -> Any:
        return await self._events.get()

    async def send_command(self, command: RuntimeCommand) -> None:
        await self._commands.put(command)

    async def next_command(self) -> Any:
        return await self._commands.get()

    async def request_interaction(self, request: Any, **payload: Any) -> Any:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        request_id = getattr(request, "request_id", None)
        if not request_id:
            raise ValueError("interaction request_id is required")
        self._pending_interactions[request_id] = future
        await self.emit_runtime_event(
            "interaction_requested",
            request=_serialize(request),
            **payload,
        )
        return await future

    async def request(self, request: Any, **payload: Any) -> Any:
        return await self.request_interaction(request, **payload)

    def resolve_interaction(self, request_id: str, data: Any) -> None:
        self._events.put_nowait(
            RuntimeEvent(
                type="interaction_resolved",
                payload={
                    "request_id": request_id,
                    "data": _serialize(data),
                },
            )
        )
        future = self._pending_interactions.pop(request_id, None)
        if future is not None and not future.done():
            future.set_result(data)

    def cancel_interaction(self, request_id: str) -> None:
        self._events.put_nowait(
            RuntimeEvent(
                type="interaction_resolved",
                payload={
                    "request_id": request_id,
                    "data": None,
                    "cancelled": True,
                },
            )
        )
        future = self._pending_interactions.pop(request_id, None)
        if future is not None and not future.done():
            future.set_result(None)

    def checkpoint(self, checkpoint: RuntimeCheckpoint) -> None:
        self._checkpoints[checkpoint.checkpoint_id] = checkpoint
        self._latest_checkpoint_id = checkpoint.checkpoint_id

    def get_checkpoint(self, checkpoint_id: str) -> Optional[RuntimeCheckpoint]:
        return self._checkpoints.get(checkpoint_id)

    def export_checkpoints(self) -> dict[str, RuntimeCheckpoint]:
        return dict(self._checkpoints)

    @property
    def latest_checkpoint_id(self) -> Optional[str]:
        return self._latest_checkpoint_id
