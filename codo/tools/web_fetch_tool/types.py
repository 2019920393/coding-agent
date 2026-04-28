"""WebFetchTool 类型定义"""
from pydantic import BaseModel, Field

class WebFetchInput(BaseModel):
    """WebFetch 输入参数"""
    url: str = Field(description="The URL to fetch content from")
    prompt: str = Field(description="The prompt to run on the fetched content")

class WebFetchOutput(BaseModel):
    """WebFetch 输出结果"""
    result: str = Field(description="The processed content result")
    url: str = Field(description="Final URL after redirects")
    code: int = Field(description="HTTP status code")
    codeText: str = Field(description="HTTP status text")
    bytes: int = Field(description="Content size in bytes")
    durationMs: int = Field(description="Total fetch duration in milliseconds")
