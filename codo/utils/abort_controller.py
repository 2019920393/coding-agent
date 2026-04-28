"""
AbortController - 用户中断处理机制

这个模块提供了一个类似 Web API AbortController 的中断机制，用于：
1. 优雅地处理用户中断（Ctrl+C）
2. 在工具执行链中传播中断信号
3. 区分不同的中断原因（'interrupt' vs 'abort'）
4. 支持父子 AbortController 链式传播

[Workflow]
1. 创建 AbortController 实例
2. 注册中断回调（on_abort）
3. 当用户按 Ctrl+C 时，调用 abort(reason)
4. 所有注册的回调被触发
5. 子 AbortController 自动被中断

[Key Concepts]
- AbortReason: 'interrupt' | 'abort'
  * 'interrupt': 用户中断（Ctrl+C），Bash 工具不杀死进程
  * 'abort': 程序中止，Bash 工具杀死进程
- 父子链：子 AbortController 在父中断时自动中断
- WeakRef: 使用弱引用避免循环引用导致的内存泄漏
"""

import asyncio
import weakref
from typing import Callable, Optional, Literal, Set
from dataclasses import dataclass, field

# 中断原因类型
AbortReason = Literal["interrupt", "abort"]

@dataclass
class AbortController:
    """
    中断控制器

    [Attributes]
    - aborted: 是否已中断
    - reason: 中断原因（'interrupt' | 'abort'）
    - _callbacks: 中断回调列表
    - _children: 子 AbortController 弱引用列表
    - _parent: 父 AbortController 弱引用

    [Methods]
    - abort(reason): 触发中断
    - on_abort(callback): 注册中断回调
    - create_child(): 创建子 AbortController
    - is_aborted(): 检查是否已中断
    """

    aborted: bool = field(default=False, init=False)
    reason: Optional[AbortReason] = field(default=None, init=False)
    _callbacks: Set[Callable[[AbortReason], None]] = field(default_factory=set, init=False)
    _children: list = field(default_factory=list, init=False)  # 使用 list 而非 Set
    _parent: Optional[weakref.ref] = field(default=None, init=False)

    def abort(self, reason: AbortReason = "abort") -> None:
        """
        触发中断

        [Workflow]
        1. 如果已中断，直接返回
        2. 设置 aborted 和 reason
        3. 触发所有注册的回调
        4. 递归中断所有子 AbortController

        Args:
            reason: 中断原因（'interrupt' | 'abort'）
        """
        if self.aborted:
            return

        self.aborted = True
        self.reason = reason

        # 触发所有回调
        for callback in list(self._callbacks):
            try:
                callback(reason)
            except Exception as e:
                # 回调异常不应该阻止其他回调执行
                print(f"Warning: AbortController callback raised exception: {e}")

        # 递归中断所有子 AbortController
        for child_ref in list(self._children):
            child = child_ref()
            if child is not None:
                child.abort(reason)

        # 清理回调和子引用
        self._callbacks.clear()
        self._children.clear()

    def on_abort(self, callback: Callable[[AbortReason], None]) -> Callable[[], None]:
        """
        注册中断回调

        [Workflow]
        1. 如果已中断，立即调用回调
        2. 否则，将回调添加到列表
        3. 返回取消注册函数

        Args:
            callback: 中断回调函数，接收 AbortReason 参数

        Returns:
            取消注册函数
        """
        # 如果已中断，立即调用回调
        if self.aborted:
            try:
                callback(self.reason)
            except Exception as e:
                print(f"Warning: AbortController callback raised exception: {e}")
            return lambda: None

        # 添加回调到列表
        self._callbacks.add(callback)

        # 返回取消注册函数
        def unregister():
            self._callbacks.discard(callback)

        return unregister

    def create_child(self) -> "AbortController":
        """
        创建子 AbortController

        [Workflow]
        1. 创建新的 AbortController
        2. 设置父子关系（使用弱引用）
        3. 如果父已中断，立即中断子
        4. 返回子 AbortController

        Returns:
            子 AbortController
        """
        child = AbortController()
        child._parent = weakref.ref(self)

        # 使用弱引用避免循环引用
        self._children.append(weakref.ref(child))

        # 如果父已中断，立即中断子
        if self.aborted:
            child.abort(self.reason)

        return child

    def is_aborted(self) -> bool:
        """
        检查是否已中断

        Returns:
            是否已中断
        """
        return self.aborted

    def get_reason(self) -> Optional[AbortReason]:
        """
        获取中断原因

        Returns:
            中断原因（'interrupt' | 'abort' | None）
        """
        return self.reason

    async def check_aborted(self) -> None:
        """
        检查是否已中断，如果已中断则抛出异常

        用于在异步操作中定期检查中断状态

        Raises:
            AbortedError: 如果已中断
        """
        if self.aborted:
            raise AbortedError(self.reason)

    def __repr__(self) -> str:
        return f"AbortController(aborted={self.aborted}, reason={self.reason})"

class AbortedError(Exception):
    """
    中断异常

    当 AbortController 被中断时抛出此异常
    """

    def __init__(self, reason: Optional[AbortReason] = None):
        self.reason = reason
        super().__init__(f"Operation aborted: {reason}")

# 中断相关的消息常量

REJECT_MESSAGE = "User interrupted"
"""用户中断消息（用于 'interrupt' 原因）"""

CANCEL_MESSAGE = "Operation cancelled"
"""操作取消消息（用于 'abort' 原因）"""

def get_abort_message(reason: Optional[AbortReason]) -> str:
    """
    根据中断原因获取对应的消息

    Args:
        reason: 中断原因

    Returns:
        中断消息
    """
    if reason == "interrupt":
        return REJECT_MESSAGE
    elif reason == "abort":
        return CANCEL_MESSAGE
    else:
        return "Operation aborted"
