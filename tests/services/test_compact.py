"""
Tests for compact service modules.
"""

import pytest
from codo.services.compact.prompt import (
    get_compact_prompt,
    format_compact_summary,
    get_compact_user_summary_message,
)
from codo.services.compact.compact import (
    AutoCompactState,
    _strip_images_from_messages,
    _ensure_alternating,
    _stream_compact_summary,
)

class TestCompactPrompt:
    def test_get_compact_prompt_basic(self):
        prompt = get_compact_prompt()
        assert "CRITICAL" in prompt
        assert "TEXT ONLY" in prompt
        assert "summary" in prompt.lower()
        assert "analysis" in prompt.lower()
        assert "REMINDER" in prompt

    def test_get_compact_prompt_with_instructions(self):
        prompt = get_compact_prompt("Focus on Python code changes")
        assert "Focus on Python code changes" in prompt
        assert "Additional Instructions" in prompt

    def test_get_compact_prompt_empty_instructions(self):
        prompt = get_compact_prompt("")
        assert "Additional Instructions" not in prompt

    def test_format_compact_summary_with_analysis(self):
        raw = """<analysis>
This is the analysis section that should be stripped.
</analysis>

<summary>
1. Primary Request: Build a CLI tool
2. Key Concepts: Python, asyncio
</summary>"""
        formatted = format_compact_summary(raw)
        assert "analysis section" not in formatted
        assert "Summary:" in formatted
        assert "Primary Request" in formatted

    def test_format_compact_summary_no_tags(self):
        raw = "Just a plain summary text."
        formatted = format_compact_summary(raw)
        assert formatted == "Just a plain summary text."

    def test_format_compact_summary_only_summary(self):
        raw = "<summary>The summary content.</summary>"
        formatted = format_compact_summary(raw)
        assert "Summary:" in formatted
        assert "The summary content." in formatted

    def test_get_compact_user_summary_message(self):
        msg = get_compact_user_summary_message("Test summary")
        assert "continued from a previous conversation" in msg
        assert "Test summary" in msg

    def test_get_compact_user_summary_message_with_transcript(self):
        msg = get_compact_user_summary_message(
            "Test summary",
            transcript_path="/path/to/transcript.jsonl",
        )
        assert "/path/to/transcript.jsonl" in msg

    def test_get_compact_user_summary_message_suppress_follow_up(self):
        msg = get_compact_user_summary_message(
            "Test summary",
            suppress_follow_up=True,
        )
        assert "without asking" in msg
        assert "Pick up the last task" in msg

class TestAutoCompactState:
    def test_initial_state(self):
        state = AutoCompactState()
        assert not state.compacted
        assert state.turn_counter == 0
        assert state.consecutive_failures == 0
        assert not state.circuit_breaker_tripped

    def test_record_success(self):
        state = AutoCompactState()
        state.consecutive_failures = 2
        state.record_success()
        assert state.compacted
        assert state.consecutive_failures == 0
        assert state.turn_counter == 0

    def test_record_failure(self):
        state = AutoCompactState()
        state.record_failure()
        assert state.consecutive_failures == 1
        assert not state.circuit_breaker_tripped

    def test_circuit_breaker(self):
        state = AutoCompactState()
        for _ in range(3):
            state.record_failure()
        assert state.circuit_breaker_tripped

    def test_increment_turn(self):
        state = AutoCompactState()
        state.increment_turn()
        assert state.turn_counter == 1
        state.increment_turn()
        assert state.turn_counter == 2

class TestStripImages:
    def test_no_images(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        result = _strip_images_from_messages(messages)
        assert result == messages

    def test_strip_image_block(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Look at this:"},
                    {"type": "image", "source": {"type": "base64", "data": "..."}},
                ],
            }
        ]
        result = _strip_images_from_messages(messages)
        assert len(result) == 1
        content = result[0]["content"]
        assert len(content) == 2
        assert content[1] == {"type": "text", "text": "[image]"}

    def test_strip_document_block(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "document", "source": {"type": "base64", "data": "..."}},
                ],
            }
        ]
        result = _strip_images_from_messages(messages)
        assert result[0]["content"][0] == {"type": "text", "text": "[document]"}

    def test_string_content_unchanged(self):
        messages = [{"role": "user", "content": "just text"}]
        result = _strip_images_from_messages(messages)
        assert result == messages

class TestEnsureAlternating:
    def test_empty(self):
        assert _ensure_alternating([]) == []

    def test_already_alternating(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        result = _ensure_alternating(messages)
        assert len(result) == 2

    def test_merge_consecutive_user(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "are you there?"},
        ]
        result = _ensure_alternating(messages)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert "hello" in result[0]["content"]
        assert "are you there?" in result[0]["content"]

    def test_merge_consecutive_list_content(self):
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "a"}]},
            {"role": "user", "content": [{"type": "text", "text": "b"}]},
        ]
        result = _ensure_alternating(messages)
        assert len(result) == 1
        assert len(result[0]["content"]) == 2

    def test_mixed_string_and_list(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "user", "content": [{"type": "text", "text": "world"}]},
        ]
        result = _ensure_alternating(messages)
        assert len(result) == 1

class _FakeCompactStream:
    def __init__(self, chunks):
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    @property
    def text_stream(self):
        async def _iterate():
            for chunk in self._chunks:
                yield chunk

        return _iterate()

class _FakeCompactMessagesAPI:
    def __init__(self):
        self.calls = []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeCompactStream(["第一段摘要", "，第二段摘要"])

class _FakeCompactClient:
    def __init__(self):
        self.messages = _FakeCompactMessagesAPI()

@pytest.mark.asyncio
async def test_stream_compact_summary_uses_streaming_api():
    client = _FakeCompactClient()

    summary = await _stream_compact_summary(
        client=client,
        model="claude-test",
        system_prompt="system",
        messages=[{"role": "user", "content": "compact please"}],
    )

    assert summary == "第一段摘要，第二段摘要"
    assert len(client.messages.calls) == 1
    assert client.messages.calls[0]["model"] == "claude-test"
