"""
Tests for API error classification and retry.
"""

import pytest
from anthropic import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
    InternalServerError,
)

from codo.services.api.errors import (
    classify_api_error,
    is_retryable,
    is_prompt_too_long_error,
    parse_prompt_too_long_tokens,
    get_retry_delay,
    format_api_error,
    APIErrorCategory,
)

class TestClassifyApiError:
    def test_timeout(self):
        err = APITimeoutError(request=None)
        assert classify_api_error(err) == APIErrorCategory.TIMEOUT

    def test_connection_error(self):
        err = APIConnectionError(request=None)
        assert classify_api_error(err) == APIErrorCategory.CONNECTION_ERROR

    def test_auth_error(self):
        err = AuthenticationError(
            message="invalid api key",
            response=_make_response(401),
            body=None,
        )
        assert classify_api_error(err) == APIErrorCategory.AUTH_ERROR

    def test_rate_limit(self):
        err = RateLimitError(
            message="rate limited",
            response=_make_response(429),
            body=None,
        )
        assert classify_api_error(err) == APIErrorCategory.RATE_LIMITED

    def test_server_error(self):
        err = InternalServerError(
            message="internal error",
            response=_make_response(500),
            body=None,
        )
        assert classify_api_error(err) == APIErrorCategory.SERVER_ERROR

    def test_prompt_too_long(self):
        err = BadRequestError(
            message="prompt is too long: 150000 tokens > 128000 maximum",
            response=_make_response(400),
            body=None,
        )
        assert classify_api_error(err) == APIErrorCategory.PROMPT_TOO_LONG

    def test_bad_request_other(self):
        err = BadRequestError(
            message="invalid parameter",
            response=_make_response(400),
            body=None,
        )
        assert classify_api_error(err) == APIErrorCategory.BAD_REQUEST

    def test_unknown_error(self):
        err = ValueError("something weird")
        assert classify_api_error(err) == APIErrorCategory.UNKNOWN

class TestIsRetryable:
    def test_retryable_categories(self):
        assert is_retryable(APIErrorCategory.OVERLOADED)
        assert is_retryable(APIErrorCategory.RATE_LIMITED)
        assert is_retryable(APIErrorCategory.CONNECTION_ERROR)
        assert is_retryable(APIErrorCategory.TIMEOUT)
        assert is_retryable(APIErrorCategory.SERVER_ERROR)

    def test_non_retryable_categories(self):
        assert not is_retryable(APIErrorCategory.AUTH_ERROR)
        assert not is_retryable(APIErrorCategory.PROMPT_TOO_LONG)
        assert not is_retryable(APIErrorCategory.BAD_REQUEST)
        assert not is_retryable(APIErrorCategory.UNKNOWN)

class TestIsPromptTooLong:
    def test_standard_message(self):
        assert is_prompt_too_long_error("prompt is too long: 150000 tokens > 128000 maximum")

    def test_alternative_format(self):
        assert is_prompt_too_long_error("prompt_too_long error occurred")

    def test_chinese_proxy_format(self):
        assert is_prompt_too_long_error("CONTENT_LENGTH_EXCEEDS_THRESHOLD")

    def test_context_length(self):
        assert is_prompt_too_long_error("context length exceeded")

    def test_unrelated_message(self):
        assert not is_prompt_too_long_error("invalid api key")

class TestParsePromptTooLongTokens:
    def test_standard_format(self):
        actual, limit = parse_prompt_too_long_tokens(
            "prompt is too long: 150000 tokens > 128000 maximum"
        )
        assert actual == 150000
        assert limit == 128000

    def test_no_match(self):
        actual, limit = parse_prompt_too_long_tokens("some other error")
        assert actual is None
        assert limit is None

class TestGetRetryDelay:
    def test_first_attempt(self):
        delay = get_retry_delay(0, APIErrorCategory.SERVER_ERROR)
        assert delay > 0
        assert delay <= 15.0

    def test_exponential_growth(self):
        d1 = get_retry_delay(0, APIErrorCategory.SERVER_ERROR)
        d2 = get_retry_delay(1, APIErrorCategory.SERVER_ERROR)
        d3 = get_retry_delay(2, APIErrorCategory.SERVER_ERROR)
        assert d2 > d1
        assert d3 > d2

    def test_rate_limit_longer_delay(self):
        rl_delay = get_retry_delay(1, APIErrorCategory.RATE_LIMITED)
        se_delay = get_retry_delay(1, APIErrorCategory.SERVER_ERROR)
        assert rl_delay >= se_delay

    def test_max_cap(self):
        delay = get_retry_delay(100, APIErrorCategory.SERVER_ERROR)
        assert delay <= 15.0

        delay_rl = get_retry_delay(100, APIErrorCategory.RATE_LIMITED)
        assert delay_rl <= 60.0

class TestFormatApiError:
    def test_prompt_too_long(self):
        err = BadRequestError(
            message="prompt is too long: 150000 tokens > 128000 maximum",
            response=_make_response(400),
            body=None,
        )
        msg = format_api_error(err)
        assert "/compact" in msg
        assert "150,000" in msg

    def test_auth_error(self):
        err = AuthenticationError(
            message="invalid key",
            response=_make_response(401),
            body=None,
        )
        msg = format_api_error(err)
        assert "Authentication" in msg

    def test_rate_limited(self):
        err = RateLimitError(
            message="rate limited",
            response=_make_response(429),
            body=None,
        )
        msg = format_api_error(err)
        assert "Rate limited" in msg

# Helper to create mock response objects
class _MockResponse:
    def __init__(self, status_code):
        self.status_code = status_code
        self.headers = {}
        self.request = None

def _make_response(status_code):
    return _MockResponse(status_code)
