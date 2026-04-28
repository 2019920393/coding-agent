"""
工具注册表模块。

本模块是内置工具的统一来源（single source of truth），用于保证：
1. 运行时工具池与提示词中的工具列表一致；
2. API 请求组装时使用的工具与运行时一致；
3. 新增工具时只需维护一处，降低列表漂移风险。
"""

from typing import List
from codo.tools.base import Tool
from codo.tools import BUILTIN_TOOLS

def get_all_tools() -> List[Tool]:
    """
    获取所有可用内置工具。

    [Workflow]
    1. 从内置工具常量中读取当前可用工具；
    2. 返回一个浅拷贝，避免调用方误改全局列表；
    3. 由上层模块（QueryEngine/PromptBuilder/API 组装）复用同一来源。

    设计目的：
    - 统一工具来源，防止“提示词有工具但运行时不可用”的对齐问题。
    """
    # 返回副本而不是原对象，避免调用方 append/pop 影响全局工具注册状态。
    return list(BUILTIN_TOOLS)

def find_tool_by_name(tools: List[Tool], name: str) -> Tool | None:
    """
    按名称查找工具实例。

    [Workflow]
    1. 顺序遍历工具列表；
    2. 比较工具名是否与目标名一致；
    3. 命中即返回工具对象，未命中返回 None。

    说明：
    - 与参考实现中的 findToolByName 语义保持一致。
    """
    # 逐个工具遍历，确保在保持原顺序语义下进行匹配。
    for tool in tools:
        # 命中后立即返回，减少无效遍历并保持“首个匹配优先”行为。
        if tool.name == name:
            return tool
    # 未找到匹配工具时返回 None，由调用方决定降级策略（报错/忽略/回退）。
    return None
