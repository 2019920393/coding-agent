"""Textual 运行时注册表。"""

from __future__ import annotations

from typing import Any, Optional
import weakref

_ACTIVE_APP: Optional[weakref.ReferenceType[Any]] = None

def set_active_app(app: Any) -> None:
    """登记当前活动的 Textual App。"""
    global _ACTIVE_APP
    _ACTIVE_APP = weakref.ref(app)

def clear_active_app(app: Any | None = None) -> None:
    """清理活动 App 引用。"""
    global _ACTIVE_APP
    if _ACTIVE_APP is None:
        return
    current = _ACTIVE_APP()
    if app is None or current is app:
        _ACTIVE_APP = None

def get_active_app() -> Any | None:
    """获取当前活动 App。"""
    if _ACTIVE_APP is None:
        return None
    return _ACTIVE_APP()
