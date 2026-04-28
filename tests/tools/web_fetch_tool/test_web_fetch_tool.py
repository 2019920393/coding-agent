"""WebFetchTool 单元测试"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from codo.tools.web_fetch_tool import (
    WebFetchTool,
    WebFetchInput,
    WebFetchOutput,
)
from codo.tools.base import ToolUseContext
from codo.types.permissions import (
    PermissionAllowDecision,
    PermissionResult,
    create_allow_decision,
    create_passthrough_result,
)

@pytest.fixture
def tool():
    """创建 WebFetchTool 实例"""
    return WebFetchTool()

@pytest.fixture
def context():
    """创建测试上下文"""
    api_client = MagicMock()
    api_client.messages = MagicMock()
    api_client.messages.create = AsyncMock(return_value=MagicMock(
        content=[MagicMock(text="Processed content")]
    ))

    return ToolUseContext(
        options={
            "cwd": "/test",
            "api_client": api_client,
        },
        abort_controller=None,
        messages=[]
    )

class TestWebFetchTool:
    """WebFetchTool 测试套件"""

    @pytest.mark.asyncio
    async def test_validate_url_success(self, tool, context):
        """测试 URL 验证成功"""
        input_data = WebFetchInput(
            url="https://docs.python.org",
            prompt="Summarize this page"
        )

        result = await tool.validate_input(input_data, context)
        assert result.result is True

    @pytest.mark.asyncio
    async def test_validate_url_too_long(self, tool, context):
        """测试 URL 过长"""
        input_data = WebFetchInput(
            url="https://example.com/" + "a" * 2000,
            prompt="Test"
        )

        result = await tool.validate_input(input_data, context)
        assert result.result is False
        assert "too long" in result.message.lower()

    @pytest.mark.asyncio
    async def test_validate_url_with_credentials(self, tool, context):
        """测试带凭据的 URL"""
        input_data = WebFetchInput(
            url="https://user:pass@example.com",
            prompt="Test"
        )

        result = await tool.validate_input(input_data, context)
        assert result.result is False
        assert "credentials" in result.message.lower()

    @pytest.mark.asyncio
    async def test_validate_empty_prompt(self, tool, context):
        """测试空 prompt"""
        input_data = WebFetchInput(
            url="https://example.com",
            prompt=""
        )

        result = await tool.validate_input(input_data, context)
        assert result.result is False
        assert "empty" in result.message.lower()

    @pytest.mark.asyncio
    async def test_http_upgrade_to_https(self, tool, context):
        """测试 HTTP 自动升级到 HTTPS"""
        from codo.tools.web_fetch_tool.utils import validate_url

        is_valid, error, normalized = validate_url("http://example.com")
        assert is_valid is True
        assert normalized == "https://example.com"

    @pytest.mark.asyncio
    async def test_preapproved_domain(self, tool, context):
        """测试预批准域名"""
        from codo.tools.web_fetch_tool.preapproved import is_preapproved_domain

        assert is_preapproved_domain("https://docs.python.org") is True
        assert is_preapproved_domain("https://docs.anthropic.com") is True
        assert is_preapproved_domain("https://unknown-site.com") is False

    @pytest.mark.asyncio
    async def test_check_permissions_preapproved(self, tool, context):
        """测试预批准域名权限"""
        input_data = WebFetchInput(
            url="https://docs.python.org",
            prompt="Test"
        )

        result = await tool.check_permissions(input_data, context)
        assert isinstance(result, PermissionAllowDecision)
        assert result == create_allow_decision()

    @pytest.mark.asyncio
    async def test_check_permissions_unknown_domain_passthrough(self, tool, context):
        """非预批准域名应透传给 canonical 权限系统。"""
        input_data = WebFetchInput(
            url="https://unknown-site.com",
            prompt="Test"
        )

        result = await tool.check_permissions(input_data, context)

        assert isinstance(result, PermissionResult)
        assert result == create_passthrough_result()

    @pytest.mark.asyncio
    async def test_fetch_with_mock(self, tool, context):
        """测试抓取（使用 mock）"""
        with patch('codo.tools.web_fetch_tool.web_fetch_tool.fetch_url_with_redirects') as mock_fetch, \
             patch('codo.tools.web_fetch_tool.web_fetch_tool.convert_html_to_markdown') as mock_convert, \
             patch('codo.tools.web_fetch_tool.web_fetch_tool.process_content_with_prompt') as mock_process:

            mock_fetch.return_value = (
                "<html><body>Test content</body></html>",
                200,
                "OK",
                100,
                "https://example.com"
            )
            mock_convert.return_value = "Test content"
            mock_process.return_value = "Processed result"

            input_data = WebFetchInput(
                url="https://example.com",
                prompt="Summarize"
            )

            result = await tool.call(input_data, context, None, None, None)

            assert result.data is not None
            assert result.data.code == 200
            assert result.data.url == "https://example.com"
            assert result.data.result == "Processed result"

    @pytest.mark.asyncio
    async def test_cache_functionality(self, tool, context):
        """测试缓存功能"""
        from codo.tools.web_fetch_tool.utils import get_cached_fetch, set_cached_fetch

        url = "https://example.com"
        content = "Test content"
        metadata = {"url": url, "code": 200, "codeText": "OK", "bytes": 100}

        # 设置缓存
        set_cached_fetch(url, content, metadata)

        # 获取缓存
        cached = get_cached_fetch(url)
        assert cached is not None
        assert cached[0] == content
        assert cached[1]["code"] == 200

    def test_tool_properties(self, tool):
        """测试工具属性"""
        assert tool.name == "WebFetch"
        assert tool.is_read_only() is True
        assert tool.is_concurrency_safe() is True
        assert tool.input_schema == WebFetchInput
        assert tool.output_schema == WebFetchOutput

    def test_map_tool_result(self, tool):
        """测试工具结果映射"""
        output = WebFetchOutput(
            result="Test result",
            url="https://example.com",
            code=200,
            codeText="OK",
            bytes=100,
            durationMs=500
        )

        result = tool.map_tool_result_to_tool_result_block_param(output, "test_id")
        assert result["type"] == "tool_result"
        assert result["tool_use_id"] == "test_id"
        assert "200 OK" in result["content"]
        assert "Test result" in result["content"]
