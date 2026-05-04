"""Textual UI 入口。

本模块作为 CLI TUI 层的公开接口，统一导出三个核心符号：
- TextualChatApp: Textual 主应用，负责渲染整个 UI
- UIBridge: 引擎事件聚合器，将 QueryEngine 状态转换为 UI 快照
- get_active_app: 获取当前活动的 Textual App 实例（用于工具层回调）
"""

from .app import TextualChatApp
from .bridge import UIBridge
from .runtime import get_active_app

__all__ = ["TextualChatApp", "UIBridge", "get_active_app"]

