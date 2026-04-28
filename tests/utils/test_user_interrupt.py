"""
用户中断处理测试

"""

import asyncio
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codo.query_engine import QueryEngine
from codo.utils.abort_controller import AbortController, AbortedError, get_abort_message

class TestAbortController:
    """AbortController 核心功能测试"""

    def test_initial_state(self):
        """初始状态应该未中断"""
        controller = AbortController()
        assert controller.is_aborted() is False
        assert controller.get_reason() is None

    def test_abort_interrupt(self):
        """interrupt 原因中断"""
        controller = AbortController()
        callback_called = False
        callback_reason = None

        def callback(reason):
            nonlocal callback_called, callback_reason
            callback_called = True
            callback_reason = reason

        controller.on_abort(callback)
        controller.abort("interrupt")

        assert controller.is_aborted() is True
        assert controller.get_reason() == "interrupt"
        assert callback_called is True
        assert callback_reason == "interrupt"

    def test_abort_abort(self):
        """abort 原因中断"""
        controller = AbortController()
        controller.abort("abort")

        assert controller.is_aborted() is True
        assert controller.get_reason() == "abort"

    def test_abort_idempotent(self):
        """重复 abort 应该是幂等的"""
        controller = AbortController()
        controller.abort("interrupt")
        controller.abort("abort")  # 不应该改变 reason

        assert controller.get_reason() == "interrupt"

    def test_callback_immediate_if_already_aborted(self):
        """如果已中断，注册回调时应立即调用"""
        controller = AbortController()
        controller.abort("interrupt")

        callback_called = False
        callback_reason = None

        def callback(reason):
            nonlocal callback_called, callback_reason
            callback_called = True
            callback_reason = reason

        unregister = controller.on_abort(callback)

        assert callback_called is True
        assert callback_reason == "interrupt"

    def test_unregister_callback(self):
        """取消注册回调"""
        controller = AbortController()
        callback_called = False

        def callback(reason):
            nonlocal callback_called
            callback_called = True

        unregister = controller.on_abort(callback)
        unregister()
        controller.abort("interrupt")

        assert callback_called is False

    def test_create_child_propagation(self):
        """父中断应传播到子"""
        parent = AbortController()
        child = parent.create_child()

        parent.abort("interrupt")

        assert child.is_aborted() is True
        assert child.get_reason() == "interrupt"

    def test_create_child_already_aborted(self):
        """如果父已中断，子应立即中断"""
        parent = AbortController()
        parent.abort("abort")

        child = parent.create_child()

        assert child.is_aborted() is True
        assert child.get_reason() == "abort"

    @pytest.mark.asyncio
    async def test_check_aborted_raises(self):
        """check_aborted 应抛出 AbortedError"""
        controller = AbortController()
        controller.abort("interrupt")

        with pytest.raises(AbortedError) as exc_info:
            await controller.check_aborted()

        assert exc_info.value.reason == "interrupt"

class TestAbortMessages:
    """中断消息测试"""

    def test_get_abort_message_interrupt(self):
        assert get_abort_message("interrupt") == "User interrupted"

    def test_get_abort_message_abort(self):
        assert get_abort_message("abort") == "Operation cancelled"

    def test_get_abort_message_none(self):
        assert get_abort_message(None) == "Operation aborted"

class TestQueryEngineInterrupt:
    """QueryEngine 中断集成测试"""

    def test_query_engine_has_abort_controller(self):
        engine = QueryEngine(
            api_key="test-key",
            cwd="/tmp/test",
            model="claude-3-5-sonnet-20241022",
            verbose=False,
            enable_persistence=False,
        )

        assert hasattr(engine, 'abort_controller')
        assert isinstance(engine.abort_controller, AbortController)
        assert engine.execution_context.get('abort_controller') is engine.abort_controller

    def test_interrupt_method(self):
        engine = QueryEngine(
            api_key="test-key",
            cwd="/tmp/test",
            model="claude-3-5-sonnet-20241022",
            verbose=False,
            enable_persistence=False,
        )

        engine.interrupt()

        assert engine.abort_controller.is_aborted() is True
        assert engine.abort_controller.get_reason() == "interrupt"

    def test_reset_interrupt_state(self):
        engine = QueryEngine(
            api_key="test-key",
            cwd="/tmp/test",
            model="claude-3-5-sonnet-20241022",
            verbose=False,
            enable_persistence=False,
        )

        old_controller = engine.abort_controller
        engine.interrupt()
        assert engine.abort_controller.is_aborted() is True

        engine.reset_interrupt_state()

        assert engine.abort_controller is not old_controller
        assert engine.abort_controller.is_aborted() is False
        assert engine.execution_context.get("abort_controller") is engine.abort_controller

    @pytest.mark.asyncio
    async def test_submit_message_stream_aborted(self):
        engine = QueryEngine(
            api_key="test-key",
            cwd="/tmp/test",
            model="claude-3-5-sonnet-20241022",
            verbose=False,
            enable_persistence=False,
        )

        engine.interrupt()

        events = []
        async for event in engine.submit_message_stream("test prompt"):
            events.append(event)

        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["error_type"] == "user_interrupted"
        assert events[0]["error"] == "User interrupted"
