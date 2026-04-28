"""
Tests for token estimation module.
"""

import pytest
from codo.services.token_estimation import (
    estimate_token_count,
    estimate_content_tokens,
    estimate_messages_tokens,
    get_context_window,
    get_max_output_tokens,
    TokenBudget,
)

class TestEstimateTokenCount:
    def test_empty_string(self):
        assert estimate_token_count("") == 0

    def test_short_string(self):
        result = estimate_token_count("hello")
        assert result >= 1

    def test_long_string(self):
        text = "hello world " * 100
        result = estimate_token_count(text)
        # ~1200 chars / 3.5 ≈ 342
        assert 200 < result < 500

    def test_chinese_text(self):
        text = "你好世界" * 10
        result = estimate_token_count(text)
        assert result > 0

class TestEstimateContentTokens:
    def test_none(self):
        assert estimate_content_tokens(None) == 0

    def test_string(self):
        result = estimate_content_tokens("hello world")
        assert result > 0

    def test_text_block(self):
        blocks = [{"type": "text", "text": "hello world"}]
        result = estimate_content_tokens(blocks)
        assert result > 0

    def test_tool_use_block(self):
        blocks = [
            {
                "type": "tool_use",
                "name": "Bash",
                "input": {"command": "ls -la"},
            }
        ]
        result = estimate_content_tokens(blocks)
        assert result > 0

    def test_tool_result_block(self):
        blocks = [
            {
                "type": "tool_result",
                "tool_use_id": "123",
                "content": "file1.py\nfile2.py",
            }
        ]
        result = estimate_content_tokens(blocks)
        assert result > 0

    def test_image_block(self):
        blocks = [{"type": "image", "source": {"type": "base64", "data": "..."}}]
        result = estimate_content_tokens(blocks)
        # Images use fixed 1600 tokens
        assert result == 1600

    def test_mixed_blocks(self):
        blocks = [
            {"type": "text", "text": "Here's the result:"},
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
        ]
        result = estimate_content_tokens(blocks)
        assert result > 0

class TestEstimateMessagesTokens:
    def test_empty(self):
        assert estimate_messages_tokens([]) == 0

    def test_single_message(self):
        messages = [{"role": "user", "content": "hello"}]
        result = estimate_messages_tokens(messages)
        # 4 (overhead) + token estimate for "hello"
        assert result > 4

    def test_multi_messages(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
            {"role": "user", "content": "how are you?"},
        ]
        result = estimate_messages_tokens(messages)
        assert result > 12  # At least 4 per message overhead

class TestGetContextWindow:
    def test_known_model(self):
        assert get_context_window("claude-opus-4-20250514") == 200_000

    def test_unknown_model(self):
        result = get_context_window("unknown-model")
        assert result == 200_000  # default

class TestTokenBudget:
    def test_init(self):
        budget = TokenBudget("claude-opus-4-20250514")
        assert budget.model == "claude-opus-4-20250514"
        assert budget.context_window == 200_000

    def test_effective_context_window(self):
        budget = TokenBudget("claude-opus-4-20250514")
        # Should be context_window - max(max_output, 20000)
        assert budget.effective_context_window < budget.context_window
        assert budget.effective_context_window > 0

    def test_auto_compact_threshold(self):
        budget = TokenBudget("claude-opus-4-20250514")
        # Should be effective_context_window - buffer
        assert budget.auto_compact_threshold < budget.effective_context_window
        assert budget.auto_compact_threshold > 0

    def test_should_auto_compact(self):
        budget = TokenBudget("claude-opus-4-20250514")
        # Below threshold
        assert not budget.should_auto_compact(1000)
        # Above threshold
        assert budget.should_auto_compact(budget.auto_compact_threshold + 1)

    def test_is_at_blocking_limit(self):
        budget = TokenBudget("claude-opus-4-20250514")
        assert not budget.is_at_blocking_limit(1000)
        assert budget.is_at_blocking_limit(budget.blocking_limit + 1)

    def test_get_usage_stats(self):
        budget = TokenBudget("claude-opus-4-20250514")
        stats = budget.get_usage_stats(50_000)
        assert "token_count" in stats
        assert "percent_used" in stats
        assert "percent_left" in stats
        assert stats["token_count"] == 50_000
        assert stats["percent_used"] + stats["percent_left"] == 100
