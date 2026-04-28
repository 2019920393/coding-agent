"""
Prompt 构建器

组装完整的系统提示词，集成所有上下文信息。

参考：src/utils/systemPrompt.ts, src/constants/prompts.ts
简化：移除优先级系统、缓存优化、复杂的条件分支
保留：基础提示词构建、上下文注入、工具列表生成
"""

from typing import List, Set, Optional, Dict, Any
from codo.constants.prompts import get_system_prompt
from codo.services.prompt.context import get_system_context, get_user_context
from codo.services.memory.scan import load_memory_index
from codo.tools_registry import get_all_tools

class PromptBuilder:
    """
    Prompt 构建器

    [Workflow]
    1. 收集环境信息（CWD、Git 状态）
    2. 收集用户上下文（CODO.md）
    3. 收集工具列表
    4. 组装系统提示词
    """

    def __init__(self, cwd: str, model: str = "Opus 4.6"):
        """
        初始化 Prompt 构建器

        Args:
            cwd: 当前工作目录
            model: 模型名称
        """
        self.cwd = cwd
        self.model = model

    def get_enabled_tools(self) -> Set[str]:
        """
        获取启用的工具名称集合

        [Workflow]
        1. 从工具注册表获取所有工具
        2. 提取工具名称

        Returns:
            工具名称集合
        """
        tools = get_all_tools()
        return {tool.name for tool in tools}

    def build_system_prompt(
        self,
        language_preference: Optional[str] = None,
        custom_system_prompt: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        构建系统提示词

        [Workflow]
        1. 检查是否有自定义系统提示词（最高优先级）
        2. 获取环境信息
        3. 获取用户上下文
        4. 获取系统上下文
        5. 组装完整的系统提示词
        6. 转换为 ?? API 格式

        Args:
            language_preference: 语言偏好
            custom_system_prompt: 自定义系统提示词（完全替换默认提示词）

        Returns:
            系统提示词块列表（?? API 格式）
        """
        # 如果有自定义系统提示词，直接使用
        if custom_system_prompt:
            return [
                {
                    "type": "text",
                    "text": custom_system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]

        # 获取环境信息
        enabled_tools = self.get_enabled_tools()

        # 获取用户上下文和系统上下文（使用 memoize 缓存的函数）
        user_context_dict = get_user_context(self.cwd)
        system_context_dict = get_system_context(self.cwd)

        # 组合上下文文本（只包含 CODO.md 和日期，不包含 memory_index）
        context_parts = []

        # 添加用户上下文（CODO.md 内容）
        if "codoMd" in user_context_dict:
            context_parts.append(user_context_dict['codoMd'])

        if "currentDate" in user_context_dict:
            context_parts.append(f"Current Date: {user_context_dict['currentDate']}")

        combined_context = "\n\n".join(context_parts) if context_parts else None

        # 获取 Memory 索引（MEMORY.md），作为独立参数传入

        memory_index = load_memory_index(self.cwd)

        # 检查是否是 git 仓库
        is_git = "gitStatus" in system_context_dict

        # 构建系统提示词部分
        sections = get_system_prompt(
            cwd=self.cwd,
            is_git=is_git,
            model=self.model,
            enabled_tools=enabled_tools,
            user_context=combined_context,
            language_preference=language_preference,
            memory_index=memory_index,  # 传入 memory 索引
        )

        # 合并所有部分为单一文本
        full_text = "\n\n".join(sections)

        # 转换为 ?? API 格式
        return [
            {
                "type": "text",
                "text": full_text,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def build_system_prompt_text(
        self,
        language_preference: Optional[str] = None,
        custom_system_prompt: Optional[str] = None,
    ) -> str:
        """
        构建系统提示词文本（用于调试）

        [Workflow]
        1. 构建系统提示词块
        2. 提取文本内容

        Args:
            language_preference: 语言偏好
            custom_system_prompt: 自定义系统提示词

        Returns:
            系统提示词文本
        """
        blocks = self.build_system_prompt(language_preference, custom_system_prompt)
        return "\n\n".join(block["text"] for block in blocks)

def build_system_prompt_for_cwd(
    cwd: str,
    model: str = "Opus 4.6",
    language_preference: Optional[str] = None,
    custom_system_prompt: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    为指定工作目录构建系统提示词

    [Workflow]
    1. 创建 PromptBuilder 实例
    2. 构建系统提示词

    Args:
        cwd: 当前工作目录
        model: 模型名称
        language_preference: 语言偏好
        custom_system_prompt: 自定义系统提示词

    Returns:
        系统提示词块列表（?? API 格式）
    """
    builder = PromptBuilder(cwd, model)
    return builder.build_system_prompt(language_preference, custom_system_prompt)
