"""Textual 运行时注册表。

维护一个全局弱引用，指向当前活动的 Textual App 实例。
工具层（如权限对话框）通过 get_active_app() 回调 UI，
而不需要直接持有 App 引用，避免循环依赖和内存泄漏。
"""

from __future__ import annotations

from typing import Any, Optional
import weakref

_ACTIVE_APP: Optional[weakref.ReferenceType[Any]] = None

def set_active_app(app: Any) -> None:
    """
    登记当前活动的 Textual App。

    在 App.on_mount() 时调用，将 App 实例存入全局弱引用。
    使用弱引用避免阻止 App 被垃圾回收。

    参数:
        app: 当前活动的 TextualChatApp 实例
    """
    global _ACTIVE_APP
    _ACTIVE_APP = weakref.ref(app)

def clear_active_app(app: Any | None = None) -> None:
    """
    清理活动 App 引用。

    在 App.on_unmount() 时调用，防止悬空引用。
    若传入 app 参数，只在当前注册的 App 与传入的相同时才清理。

    参数:
        app: 要清理的 App 实例，None 表示无条件清理
    """
    global _ACTIVE_APP
    if _ACTIVE_APP is None:
        return
    current = _ACTIVE_APP()
    if app is None or current is app:
        _ACTIVE_APP = None

def get_active_app() -> Any | None:
    """
    获取当前活动 App。

    供工具层（如权限对话框、问题对话框）在需要 UI 交互时调用。
    若 App 已被销毁或未注册，返回 None。

    返回:
        TextualChatApp 实例，或 None（App 未启动或已退出）
    """
    if _ACTIVE_APP is None:
        return None
    return _ACTIVE_APP()
