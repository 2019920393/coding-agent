"""WebFetchTool 工具函数"""
import time
from typing import Optional, Tuple
from urllib.parse import urlparse, urlunparse
import httpx

from .constants import (
    MAX_URL_LENGTH,
    FETCH_TIMEOUT_MS,
    MAX_HTTP_CONTENT_LENGTH,
    MAX_REDIRECTS,
    USER_AGENT,
    ACCEPT_HEADER,
    MAX_MARKDOWN_LENGTH,
    CACHE_TTL_MS,
)

# 简单的内存缓存 {url: (content, timestamp, metadata)}
_cache = {}

def validate_url(url: str) -> Tuple[bool, Optional[str], str]:
    """验证 URL 格式

    Returns:
        (is_valid, error_message, normalized_url)
    """
    # 检查长度
    if len(url) > MAX_URL_LENGTH:
        return False, f"URL too long (max {MAX_URL_LENGTH} characters)", url

    try:
        parsed = urlparse(url)

        # 检查是否有凭据
        if parsed.username or parsed.password:
            return False, "URLs with credentials are not allowed", url

        # 检查主机名
        hostname = parsed.hostname
        if not hostname or len(hostname.split('.')) < 2:
            return False, "Invalid hostname", url

        # 自动升级 http 到 https
        if parsed.scheme == 'http':
            parsed = parsed._replace(scheme='https')
            normalized_url = urlunparse(parsed)
            return True, None, normalized_url

        if parsed.scheme != 'https':
            return False, "Only HTTP/HTTPS URLs are supported", url

        return True, None, url
    except Exception as e:
        return False, f"Invalid URL: {str(e)}", url

def is_same_origin_redirect(from_url: str, to_url: str) -> bool:
    """检查重定向是否同源（协议、端口、凭据必须匹配）"""
    try:
        from_parsed = urlparse(from_url)
        to_parsed = urlparse(to_url)

        # 检查协议
        if from_parsed.scheme != to_parsed.scheme:
            return False

        # 检查端口
        from_port = from_parsed.port or (443 if from_parsed.scheme == 'https' else 80)
        to_port = to_parsed.port or (443 if to_parsed.scheme == 'https' else 80)
        if from_port != to_port:
            return False

        # 检查凭据
        if from_parsed.username != to_parsed.username or from_parsed.password != to_parsed.password:
            return False

        return True
    except Exception:
        return False

async def fetch_url_with_redirects(url: str, timeout: int = 60) -> Tuple[str, int, str, int, str]:
    """抓取 URL 内容，手动处理重定向

    Returns:
        (content, status_code, status_text, bytes, final_url)
    """
    current_url = url
    redirect_count = 0

    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=timeout,
        limits=httpx.Limits(max_connections=10)
    ) as client:
        while redirect_count < MAX_REDIRECTS:
            response = await client.get(
                current_url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": ACCEPT_HEADER,
                }
            )

            # 检查是否是重定向
            if response.status_code in (301, 302, 303, 307, 308):
                redirect_url = response.headers.get("Location")
                if not redirect_url:
                    raise ValueError("Redirect without Location header")

                # 处理相对 URL
                if not redirect_url.startswith(('http://', 'https://')):
                    from urllib.parse import urljoin
                    redirect_url = urljoin(current_url, redirect_url)

                # 检查同源
                if not is_same_origin_redirect(current_url, redirect_url):
                    raise ValueError(f"Cross-origin redirect not allowed: {current_url} -> {redirect_url}")

                current_url = redirect_url
                redirect_count += 1
                continue

            # 非重定向响应
            if response.status_code >= 400:
                raise ValueError(f"HTTP {response.status_code}: {response.reason_phrase}")

            # 检查内容长度
            content = response.text
            content_bytes = len(content.encode('utf-8'))
            if content_bytes > MAX_HTTP_CONTENT_LENGTH:
                raise ValueError(f"Content too large: {content_bytes} bytes (max {MAX_HTTP_CONTENT_LENGTH})")

            return (
                content,
                response.status_code,
                response.reason_phrase,
                content_bytes,
                str(response.url)
            )

        raise ValueError(f"Too many redirects (max {MAX_REDIRECTS})")

def get_cached_fetch(url: str) -> Optional[Tuple[str, dict]]:
    """获取缓存的抓取结果

    Returns:
        (content, metadata) or None
    """
    if url not in _cache:
        return None

    content, timestamp, metadata = _cache[url]

    # 检查 TTL
    if (time.time() * 1000 - timestamp) > CACHE_TTL_MS:
        del _cache[url]
        return None

    return content, metadata

def set_cached_fetch(url: str, content: str, metadata: dict):
    """设置缓存的抓取结果"""
    _cache[url] = (content, time.time() * 1000, metadata)

async def convert_html_to_markdown(html: str, api_client) -> str:
    """使用 Haiku 模型将 HTML 转换为 Markdown

    Args:
        html: HTML 内容
        api_client: API 客户端

    Returns:
        Markdown 内容
    """
    # 截断到最大长度
    truncated = html[:MAX_MARKDOWN_LENGTH]

    # 使用 Haiku 模型转换
    response = await api_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": f"Convert this HTML to clean Markdown. Preserve structure, links, and formatting.\n\n{truncated}"
        }]
    )

    return response.content[0].text

async def process_content_with_prompt(content: str, prompt: str, api_client) -> str:
    """使用 Haiku 模型处理内容

    Args:
        content: 内容
        prompt: 用户提示词
        api_client: API 客户端

    Returns:
        处理后的结果
    """
    # 截断到最大长度
    truncated = content[:MAX_MARKDOWN_LENGTH]

    # 使用 Haiku 模型处理
    response = await api_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": f"{prompt}\n\nContent:\n{truncated}"
        }]
    )

    return response.content[0].text
