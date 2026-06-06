"""WebFetchTool 常量定义"""

from importlib.metadata import PackageNotFoundError, version


def _package_version() -> str:
    try:
        return version("codo")
    except PackageNotFoundError:
        return "unknown"

WEB_FETCH_TOOL_NAME = "WebFetch"

# HTTP 配置
MAX_URL_LENGTH = 2000
FETCH_TIMEOUT_MS = 60_000  # 60 秒
MAX_HTTP_CONTENT_LENGTH = 10 * 1024 * 1024  # 10MB
MAX_REDIRECTS = 10
USER_AGENT = f"Codo-User (codo/{_package_version()})"
ACCEPT_HEADER = "text/markdown, text/html, */*"

# 内容处理
MAX_MARKDOWN_LENGTH = 100_000

# 缓存配置
CACHE_TTL_MS = 15 * 60 * 1000  # 15 分钟
