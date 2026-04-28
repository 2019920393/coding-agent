"""
上下文统计口径测试
"""

from codo.query_engine import QueryEngine

def _build_tool_result_message(tool_use_id: str, payload: str) -> dict:
    """构造最小可识别的 tool_result 消息。"""
    return {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": payload,
            }
        ],
        "type": "user",
    }

def test_get_token_usage_uses_runtime_microcompact_view():
    """
    get_token_usage 应使用运行时（microcompact 后）口径估算 token。

    构造 6 条 tool_result，按照 keep_recent=5 规则应仅压缩最旧 1 条。
    """
    engine = QueryEngine(
        api_key="test-key",
        cwd="/tmp",
        enable_persistence=False,
    )

    messages = []
    for idx in range(6):
        messages.append(
            _build_tool_result_message(
                tool_use_id=f"tool-{idx}",
                payload="x" * 600,
            )
        )
    engine.messages = messages

    usage = engine.get_token_usage()

    assert "token_count" in usage
    assert "percent_used" in usage
    assert usage.get("token_count_source") == "runtime_after_microcompact"
    assert usage.get("runtime_microcompact_compacted_count") == 1
    assert usage.get("token_count", 0) < usage.get("model_visible_token_count", 0)

def test_get_context_stats_without_persistence_has_zero_session_count():
    """未启用持久化时，会话存档消息数应为 0。"""
    engine = QueryEngine(
        api_key="test-key",
        cwd="/tmp",
        enable_persistence=False,
    )
    engine.messages = [{"role": "user", "content": "hello", "type": "user"}]

    stats = engine.get_context_stats()

    assert stats["session_message_count"] == 0
    assert stats["model_visible_message_count"] >= 1
    assert stats["runtime_message_count"] >= 1
