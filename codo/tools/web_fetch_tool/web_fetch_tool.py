"""WebFetchTool 实现"""
import time
from typing import Dict, Any
from ..base import Tool, ToolUseContext
from ..types import ToolResult, ValidationResult
from codo.types.permissions import (
    PermissionAllowDecision,
    PermissionResult,
    create_allow_decision,
    create_passthrough_result,
)
from .types import WebFetchInput, WebFetchOutput
from .prompt import PROMPT, DESCRIPTION
from .constants import WEB_FETCH_TOOL_NAME
from .preapproved import is_preapproved_domain
from .utils import (
    validate_url,
    fetch_url_with_redirects,
    get_cached_fetch,
    set_cached_fetch,
    convert_html_to_markdown,
    process_content_with_prompt,
)

class WebFetchTool(Tool[WebFetchInput, WebFetchOutput, None]):
    """
    网页抓取工具

    从 URL 抓取内容并使用 AI 模型处理。
    """

    def __init__(self):
        self.name = WEB_FETCH_TOOL_NAME
        self.max_result_size_chars = 100_000

    @property
    def input_schema(self) -> type[WebFetchInput]:
        return WebFetchInput

    @property
    def output_schema(self) -> type[WebFetchOutput]:
        return WebFetchOutput

    async def description(self, input_data: WebFetchInput, options: Dict[str, Any]) -> str:
        return DESCRIPTION

    async def prompt(self, options: Dict[str, Any]) -> str:
        return PROMPT

    def is_read_only(self, input_data: WebFetchInput = None) -> bool:
        return True

    def is_concurrency_safe(self, input_data: WebFetchInput = None) -> bool:
        return True

    async def check_permissions(
        self,
        args: WebFetchInput,
        context: ToolUseContext
    ) -> PermissionAllowDecision | PermissionResult:
        """检查权限：预批准域名自动允许"""
        if is_preapproved_domain(args.url):
            return create_allow_decision()

        # 其他域名透传到默认权限系统
        return create_passthrough_result()

    async def validate_input(
        self,
        args: WebFetchInput,
        context: ToolUseContext
    ) -> ValidationResult:
        """验证输入参数"""
        # 验证 URL
        is_valid, error_msg, _ = validate_url(args.url)
        if not is_valid:
            return ValidationResult(result=False, message=error_msg)

        # 验证 prompt
        if not args.prompt.strip():
            return ValidationResult(result=False, message="Prompt cannot be empty")

        return ValidationResult(result=True)

    async def call(
        self,
        args: WebFetchInput,
        context: ToolUseContext,
        can_use_tool,
        parent_message,
        on_progress=None
    ) -> ToolResult[WebFetchOutput]:
        """执行网页抓取"""
        start_time = time.time()

        # 验证 URL
        is_valid, error_msg, normalized_url = validate_url(args.url)
        if not is_valid:
            return ToolResult(error=error_msg)

        # 获取 API 客户端
        options = context.get_options()
        api_client = options.get("api_client")
        if not api_client:
            return ToolResult(error="API client not available")

        try:
            # 检查缓存
            cached = get_cached_fetch(normalized_url)
            if cached:
                content, metadata = cached
                # 对缓存内容应用新的 prompt
                result = await process_content_with_prompt(content, args.prompt, api_client)

                duration_ms = int((time.time() - start_time) * 1000)

                return ToolResult(
                    data=WebFetchOutput(
                        result=result,
                        url=metadata["url"],
                        code=metadata["code"],
                        codeText=metadata["codeText"],
                        bytes=metadata["bytes"],
                        durationMs=duration_ms
                    )
                )

            # 抓取 URL
            content, status_code, status_text, content_bytes, final_url = await fetch_url_with_redirects(
                normalized_url,
                timeout=60
            )

            # 检测是否是 HTML 并转换为 Markdown
            if 'text/html' in content[:1000].lower() or '<html' in content[:1000].lower():
                content = await convert_html_to_markdown(content, api_client)

            # 应用用户 prompt
            result = await process_content_with_prompt(content, args.prompt, api_client)

            # 缓存结果
            metadata = {
                "url": final_url,
                "code": status_code,
                "codeText": status_text,
                "bytes": content_bytes,
            }
            set_cached_fetch(normalized_url, content, metadata)

            duration_ms = int((time.time() - start_time) * 1000)

            return ToolResult(
                data=WebFetchOutput(
                    result=result,
                    url=final_url,
                    code=status_code,
                    codeText=status_text,
                    bytes=content_bytes,
                    durationMs=duration_ms
                )
            )

        except Exception as e:
            return ToolResult(error=f"Failed to fetch URL: {str(e)}")

    def map_tool_result_to_tool_result_block_param(
        self,
        content: WebFetchOutput,
        tool_use_id: str
    ) -> Dict[str, Any]:
        """将工具结果映射为 API 响应格式"""
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": f"Successfully fetched {content.url} ({content.code} {content.codeText}, {content.bytes} bytes, {content.durationMs}ms)\n\nResult:\n{content.result}"
        }

# 创建工具实例
web_fetch_tool = WebFetchTool()
