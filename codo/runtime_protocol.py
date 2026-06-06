"""Canonical runtime protocol for desktop-driven execution."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from codo.types.runtime import CheckpointMetadata, InteractionData, JsonObject
from codo.utils.serialize import serialize_to_json


@dataclass
class RuntimeCheckpoint:
    checkpoint_id: str
    phase: str
    turn_count: int
    created_at: float = field(default_factory=time.time)
    metadata: CheckpointMetadata = field(default_factory=dict)

@dataclass
class RuntimeEvent:
    type: str
    payload: JsonObject = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def as_legacy_event(self) -> JsonObject:
        payload = serialize_to_json(self.payload)
        if not isinstance(payload, dict):
            return {"type": self.type}
        return {"type": self.type, **payload}

@dataclass
class RuntimeCommand:
    type: str
    payload: JsonObject = field(default_factory=dict)

class QueryRuntimeController:
    _SENTINEL = object()
    _COMMAND_SENTINEL = object()

    def __init__(self) -> None:
        self._events: asyncio.Queue[RuntimeEvent | RuntimeCommand | object] = asyncio.Queue()
        self._commands: asyncio.Queue[RuntimeEvent | RuntimeCommand | object] = asyncio.Queue()
        self._pending_interactions: dict[str, asyncio.Future[InteractionData]] = {}
        self._checkpoints: dict[str, RuntimeCheckpoint] = {}
        self._latest_checkpoint_id: str | None = None

    async def emit(self, event: RuntimeEvent) -> None:
        await self._events.put(event)

    async def emit_terminal(self, terminal: object) -> None:
        await self._events.put(terminal)

    async def emit_runtime_event(self, event_type: str, **payload: Any) -> None:
        serialized = serialize_to_json(payload)
        if not isinstance(serialized, dict):
            serialized = {}
        await self.emit(RuntimeEvent(type=event_type, payload=serialized))

    async def finish(self) -> None:
        await self._events.put(self._SENTINEL)
        await self._commands.put(self._COMMAND_SENTINEL)

    async def next_event(self) -> RuntimeEvent | RuntimeCommand | object:
        return await self._events.get()

    async def send_command(self, command: RuntimeCommand) -> None:
        await self._commands.put(command)

    async def next_command(self) -> RuntimeEvent | RuntimeCommand | object:
        return await self._commands.get()

    async def request_interaction(self, request: object, **payload: Any) -> InteractionData:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[InteractionData] = loop.create_future()
        request_id = getattr(request, "request_id", None)
        if not request_id:
            raise ValueError("interaction request_id is required")
        self._pending_interactions[request_id] = future
        await self.emit_runtime_event(
            "interaction_requested",
            request=serialize_to_json(request),
            **payload,
        )
        return await future

    async def request(self, request: object, **payload: Any) -> InteractionData:
        return await self.request_interaction(request, **payload)

    def resolve_interaction(self, request_id: str, data: InteractionData) -> None:
        self._events.put_nowait(
            RuntimeEvent(
                type="interaction_resolved",
                payload={
                    "request_id": request_id,
                    "data": data,
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

    def get_checkpoint(self, checkpoint_id: str) -> RuntimeCheckpoint | None:
        return self._checkpoints.get(checkpoint_id)

    def export_checkpoints(self) -> dict[str, RuntimeCheckpoint]:
        return dict(self._checkpoints)

    @property
    def latest_checkpoint_id(self) -> str | None:
        return self._latest_checkpoint_id
