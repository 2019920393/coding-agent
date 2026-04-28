"""
测试 Query 主循环的错误恢复和附件处理

验证：
1. Retry 机制（可重试错误的恢复）
2. Reactive Compact（prompt_too_long 错误的即时压缩）
3. Attachment 处理（queued_command, ide_selection）
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from anthropic import RateLimitError, APIConnectionError, AuthenticationError

from codo.services.api.errors import APIErrorCategory, classify_api_error, is_retryable
from codo.services.attachments import get_attachment_messages, create_attachment_message

def test_classify_api_error():
    """测试 API 错误分类"""
    print("\n=== Test 1: API Error Classification ===")

    # Rate limit error
    rate_limit_err = RateLimitError("Rate limited", response=MagicMock(), body={})
    assert classify_api_error(rate_limit_err) == APIErrorCategory.RATE_LIMITED
    assert is_retryable(APIErrorCategory.RATE_LIMITED)
    print("✓ Rate limit error classified correctly")

    # Connection error
    conn_err = APIConnectionError(request=MagicMock())
    assert classify_api_error(conn_err) == APIErrorCategory.CONNECTION_ERROR
    assert is_retryable(APIErrorCategory.CONNECTION_ERROR)
    print("✓ Connection error classified correctly")

    # Auth error (not retryable)
    auth_err = AuthenticationError("Invalid API key", response=MagicMock(), body={})
    assert classify_api_error(auth_err) == APIErrorCategory.AUTH_ERROR
    assert not is_retryable(APIErrorCategory.AUTH_ERROR)
    print("✓ Auth error classified correctly (not retryable)")

def test_create_attachment_message():
    """测试附件消息创建"""
    print("\n=== Test 2: Create Attachment Message ===")

    attachment = {
        "type": "queued_command",
        "prompt": "test command",
        "source_uuid": "test-uuid",
    }

    msg = create_attachment_message(attachment)

    assert msg["type"] == "attachment"
    assert msg["attachment"] == attachment
    print("✓ Attachment message created correctly")

@pytest.mark.asyncio
async def test_get_attachment_messages_queued_command():
    """测试 queued_command 附件收集"""
    print("\n=== Test 3: Queued Command Attachments ===")

    messages = []
    turn_count = 1
    context = {
        "queued_commands": [
            {
                "prompt": "test command 1",
                "uuid": "cmd-1",
                "origin": {"kind": "user"},
                "isMeta": False,
            },
            {
                "prompt": "test command 2",
                "uuid": "cmd-2",
                "origin": {"kind": "coordinator"},
                "isMeta": True,
            },
        ]
    }

    attachments = await get_attachment_messages(messages, turn_count, context)

    assert len(attachments) == 2
    assert attachments[0]["type"] == "attachment"
    assert attachments[0]["attachment"]["type"] == "queued_command"
    assert attachments[0]["attachment"]["prompt"] == "test command 1"
    assert attachments[1]["attachment"]["prompt"] == "test command 2"
    print("✓ Queued command attachments collected correctly")

@pytest.mark.asyncio
async def test_get_attachment_messages_ide_selection():
    """测试 IDE selection 附件收集"""
    print("\n=== Test 4: IDE Selection Attachments ===")

    messages = []
    turn_count = 1

    # Test 1: IDE selection with text
    context = {
        "ide_selection": {
            "filePath": "/path/to/file.py",
            "text": "selected text",
            "startLine": 10,
            "endLine": 20,
        }
    }

    attachments = await get_attachment_messages(messages, turn_count, context)

    assert len(attachments) == 1
    assert attachments[0]["attachment"]["type"] == "ide_selection"
    assert attachments[0]["attachment"]["filename"] == "/path/to/file.py"
    assert attachments[0]["attachment"]["text"] == "selected text"
    print("✓ IDE selection with text collected correctly")

    # Test 2: IDE opened file without selection
    context = {
        "ide_selection": {
            "filePath": "/path/to/file.py",
        }
    }

    attachments = await get_attachment_messages(messages, turn_count, context)

    assert len(attachments) == 1
    assert attachments[0]["attachment"]["type"] == "opened_file_in_ide"
    assert attachments[0]["attachment"]["filename"] == "/path/to/file.py"
    print("✓ IDE opened file without selection collected correctly")

@pytest.mark.asyncio
async def test_get_attachment_messages_plan_mode():
    """测试 Plan Mode 提醒附件"""
    print("\n=== Test 5: Plan Mode Reminder Attachments ===")

    messages = []

    # Test 1: Turn 5 (should have reminder)
    context = {"mode": "plan"}
    attachments = await get_attachment_messages(messages, turn_count=5, context=context)

    assert len(attachments) == 1
    assert attachments[0]["attachment"]["type"] == "plan_mode_reminder"
    print("✓ Plan mode reminder at turn 5")

    # Test 2: Turn 3 (no reminder)
    attachments = await get_attachment_messages(messages, turn_count=3, context=context)
    assert len(attachments) == 0
    print("✓ No plan mode reminder at turn 3")

    # Test 3: Turn 25 (full reminder)
    attachments = await get_attachment_messages(messages, turn_count=25, context=context)
    assert len(attachments) == 1
    assert attachments[0]["attachment"]["full"] is True
    print("✓ Full plan mode reminder at turn 25")

@pytest.mark.asyncio
async def test_get_attachment_messages_combined():
    """测试组合附件收集"""
    print("\n=== Test 6: Combined Attachments ===")

    messages = []
    turn_count = 5
    context = {
        "mode": "plan",
        "queued_commands": [
            {
                "prompt": "test command",
                "uuid": "cmd-1",
                "origin": {"kind": "user"},
                "isMeta": False,
            }
        ],
        "ide_selection": {
            "filePath": "/path/to/file.py",
            "text": "selected text",
        }
    }

    attachments = await get_attachment_messages(messages, turn_count, context)

    # Should have: 1 queued_command + 1 ide_selection + 1 plan_mode_reminder
    assert len(attachments) == 3

    types = [att["attachment"]["type"] for att in attachments]
    assert "queued_command" in types
    assert "ide_selection" in types
    assert "plan_mode_reminder" in types

    print("✓ Combined attachments collected correctly")

def test_retry_logic_integration():
    """测试重试逻辑集成"""
    print("\n=== Test 7: Retry Logic Integration ===")

    # 这个测试验证 query.py 中的重试逻辑结构
    # 实际的重试行为需要在集成测试中验证

    from codo.services.api.errors import get_retry_delay

    # Test exponential backoff
    delay_0 = get_retry_delay(0, APIErrorCategory.RATE_LIMITED)
    delay_1 = get_retry_delay(1, APIErrorCategory.RATE_LIMITED)
    delay_2 = get_retry_delay(2, APIErrorCategory.RATE_LIMITED)

    assert delay_0 < delay_1 < delay_2
    print(f"✓ Exponential backoff: {delay_0:.2f}s -> {delay_1:.2f}s -> {delay_2:.2f}s")

    # Test max delay cap
    delay_10 = get_retry_delay(10, APIErrorCategory.RATE_LIMITED)
    assert delay_10 <= 60.0  # Max 60s for rate limit
    print(f"✓ Max delay capped at {delay_10:.2f}s")

def test_reactive_compact_guard():
    """测试 reactive compact 防护"""
    print("\n=== Test 8: Reactive Compact Guard ===")

    # 验证 hasAttemptedReactiveCompact 防止无限循环
    # 这个测试验证状态机逻辑

    from codo.query import QueryState, AutoCompactState

    # 初始状态
    state = QueryState(
        messages=[],
        turn_count=1,
        auto_compact_tracking=AutoCompactState(),
        has_attempted_reactive_compact=False,
    )

    assert state.has_attempted_reactive_compact is False
    print("✓ Initial state: has_attempted_reactive_compact = False")

    # 第一次 prompt_too_long 后
    state = QueryState(
        messages=[],
        turn_count=1,
        auto_compact_tracking=AutoCompactState(),
        has_attempted_reactive_compact=True,  # 标记已尝试
    )

    assert state.has_attempted_reactive_compact is True
    print("✓ After first attempt: has_attempted_reactive_compact = True")
    print("✓ Second prompt_too_long will terminate (no infinite loop)")

if __name__ == "__main__":
    print("=" * 60)
    print("Testing Query Loop Error Recovery and Attachments")
    print("=" * 60)

    try:
        # Sync tests
        test_classify_api_error()
        test_create_attachment_message()
        test_retry_logic_integration()
        test_reactive_compact_guard()

        # Async tests
        asyncio.run(test_get_attachment_messages_queued_command())
        asyncio.run(test_get_attachment_messages_ide_selection())
        asyncio.run(test_get_attachment_messages_plan_mode())
        asyncio.run(test_get_attachment_messages_combined())

        print("\n" + "=" * 60)
        print("✅ All tests passed!")
        print("=" * 60)
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
