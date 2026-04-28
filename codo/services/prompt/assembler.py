"""
API 请求组装器

组装完整的 ?? API 请求参数。

参考：src/services/api/query.ts - queryModel()
简化：移除 Beta 头管理、工具搜索、复杂的缓存策略
保留：基础请求参数组装、系统提示词、消息历史、工具列表
"""

from typing import List, Dict, Any, Optional
from codo.services.prompt.builder import PromptBuilder
from codo.services.prompt.messages import (
    normalize_messages_for_api,
    ensure_alternating_messages,
    add_cache_breakpoints,
)
from codo.services.prompt.tools import tools_to_api_schemas
from codo.tools_registry import get_all_tools

class APIRequestAssembler:
    """
    API 请求组装器

    [Workflow]
    1. 构建系统提示词
    2. 规范化消息历史
    3. 转换工具列表
    4. 组装完整的 API 请求参数
    """

    def __init__(
        self,
        cwd: str,
        model: str = "claude-opus-4-6",
        max_tokens: int = 8192,
        temperature: Optional[float] = None,
    ):
        """
        初始化 API 请求组装器

        Args:
            cwd: 当前工作目录
            model: 模型 ID
            max_tokens: 最大输出 token 数
            temperature: 温度参数
        """
        self.cwd = cwd
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.prompt_builder = PromptBuilder(cwd, self._get_model_display_name(model))

    def _get_model_display_name(self, model_id: str) -> str:
        """
        获取模型显示名称

        [Workflow]
        将模型 ID 转换为显示名称

        Args:
            model_id: 模型 ID

        Returns:
            模型显示名称
        """
        model_map = {
            "claude-opus-4-6": "Opus 4.6",
            "claude-sonnet-4-6": "Sonnet 4.6",
            "claude-haiku-4-5-20251001": "Haiku 4.5",
        }
        return model_map.get(model_id, model_id)

    async def assemble_request(
        self,
        messages: List[Dict[str, Any]],
        language_preference: Optional[str] = None,
        custom_system_prompt: Optional[str] = None,
        enable_caching: bool = True,
    ) -> Dict[str, Any]:
        """
        组装完整的 API 请求参数

        [Workflow]
        1. 构建系统提示词
        2. 规范化消息历史
        3. 添加缓存断点
        4. 转换工具列表
        5. 组装请求参数

        Args:
            messages: 消息历史
            language_preference: 语言偏好
            custom_system_prompt: 自定义系统提示词
            enable_caching: 是否启用缓存

        Returns:
            ?? API 请求参数
        """
        # 1. 构建系统提示词
        system_prompt = self.prompt_builder.build_system_prompt(
            language_preference=language_preference,
            custom_system_prompt=custom_system_prompt,
        )

        # 2. 规范化消息历史
        normalized_messages = normalize_messages_for_api(messages)
        alternating_messages = ensure_alternating_messages(normalized_messages)

        # 3. 添加缓存断点
        cached_messages = add_cache_breakpoints(
            alternating_messages,
            enable_caching=enable_caching,
        )

        # 4. 转换工具列表
        tools = get_all_tools()

        # 获取可用的 agent 定义
        from codo.tools.agent_tool.agents import get_builtin_agents
        agents = list(get_builtin_agents().values())

        tool_schemas = await tools_to_api_schemas(tools, agents)

        # 5. 组装请求参数
        params = {
            "model": self.model,
            "messages": cached_messages,
            "system": system_prompt,
            "tools": tool_schemas,
            "max_tokens": self.max_tokens,
        }

        # 添加可选参数
        if self.temperature is not None:
            params["temperature"] = self.temperature

        return params

    async def assemble_request_simple(
        self,
        user_message: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        组装简单的 API 请求（单条用户消息）

        [Workflow]
        1. 构建消息列表（历史 + 新消息）
        2. 调用 assemble_request

        Args:
            user_message: 用户消息
            conversation_history: 对话历史

        Returns:
            ?? API 请求参数
        """
        # 构建消息列表
        messages = []

        # 添加历史消息
        if conversation_history:
            messages.extend(conversation_history)

        # 添加新用户消息
        messages.append({
            "role": "user",
            "content": user_message,
        })

        # 组装请求
        return await self.assemble_request(messages)

async def assemble_api_request(
    cwd: str,
    messages: List[Dict[str, Any]],
    model: str = "claude-opus-4-6",
    max_tokens: int = 8192,
    temperature: Optional[float] = None,
    language_preference: Optional[str] = None,
    custom_system_prompt: Optional[str] = None,
    enable_caching: bool = True,
) -> Dict[str, Any]:
    """
    组装 API 请求（便捷函数）

    [Workflow]
    1. 创建 APIRequestAssembler 实例
    2. 组装请求

    Args:
        cwd: 当前工作目录
        messages: 消息历史
        model: 模型 ID
        max_tokens: 最大输出 token 数
        temperature: 温度参数
        language_preference: 语言偏好
        custom_system_prompt: 自定义系统提示词
        enable_caching: 是否启用缓存

    Returns:
        ?? API 请求参数
    """
    assembler = APIRequestAssembler(
        cwd=cwd,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
    )

    return await assembler.assemble_request(
        messages=messages,
        language_preference=language_preference,
        custom_system_prompt=custom_system_prompt,
        enable_caching=enable_caching,
    )
