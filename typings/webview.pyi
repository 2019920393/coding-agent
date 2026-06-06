from collections.abc import Callable
from typing import Any

class Event:
    def __iadd__(self, handler: Callable[[], object]) -> Event: ...

class WindowEvents:
    loaded: Event

class Window:
    events: WindowEvents
    def evaluate_js(self, script: str) -> Any: ...

def create_window(
    title: str,
    url: str,
    js_api: object | None = None,
    width: int = ...,
    height: int = ...,
    min_size: tuple[int, int] | None = ...,
    resizable: bool = ...,
    frameless: bool = ...,
    easy_drag: bool = ...,
    background_color: str | None = ...,
) -> Window: ...

def start(*, debug: bool = ..., icon: str | None = ...) -> None: ...
