"""测试用 Anthropic stream 替身。

这些对象只模拟 SDK 在 query 主循环里会暴露的字段和异步协议。
测试不再用 dict 伪装 content block，避免事实源重新变模糊。
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class FakeContentBlock:
    """模拟 Anthropic SDK 的 content block 对象。"""

    type: str
    text: str = ""
    thinking: str = ""
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class FakeDelta:
    """模拟 Anthropic SDK 的流式 delta 对象。"""

    type: str
    text: str = ""
    thinking: str = ""
    partial_json: str = ""


@dataclass(slots=True)
class FakeStreamEvent:
    """模拟 Anthropic SDK 的流式事件对象。"""

    type: str
    content_block: FakeContentBlock | None = None
    delta: FakeDelta | None = None
    index: int | None = None
    message: Any | None = None


@dataclass(slots=True)
class FakeFinalMessage:
    """模拟 stream.get_final_message() 返回的最终消息。"""

    content: Sequence[FakeContentBlock]
    stop_reason: str | None = None


@dataclass(slots=True)
class FakeAnthropicStream:
    """模拟 Anthropic messages.stream() 返回的 async context manager。

    工作流：
    1. `async with` 进入后返回自身。
    2. `async for` 顺序吐出预设事件。
    3. `get_final_message()` 返回聚合后的最终消息。
    """

    events: Sequence[FakeStreamEvent]
    final_message: FakeFinalMessage

    async def __aenter__(self) -> "FakeAnthropicStream":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def __aiter__(self) -> AsyncIterator[FakeStreamEvent]:
        return self._iterate_events()

    async def _iterate_events(self) -> AsyncIterator[FakeStreamEvent]:
        for event in self.events:
            yield event

    async def get_final_message(self) -> FakeFinalMessage:
        return self.final_message
