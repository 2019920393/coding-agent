"""
Hook 配置加载器

[Workflow]
1. 从 .codo/settings.json 加载 hooks 配置
2. 解析 hooks 字段（PreToolUse/PostToolUse/PostToolUseFailure/Stop）
3. 转换为 HookConfig 对象列表
4. 支持 matcher 过滤（工具名称匹配）

hooks 配置格式：
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {"type": "command", "command": "echo 'before bash'", "timeout": 5}
        ]
      }
    ],
    "PostToolUse": [...],
    "Stop": [...]
  }
}
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from codo.types.hooks import HookConfig, HookEventName

logger = logging.getLogger(__name__)

# 支持的 hook 事件类型
SUPPORTED_HOOK_EVENTS = [
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",
]

# 默认 hook 超时（毫秒）
DEFAULT_HOOK_TIMEOUT_MS = 10 * 60 * 1000  # 10 分钟

def _get_settings_file_path(cwd: str) -> Optional[str]:
    """
    获取项目设置文件路径

    [Workflow]
    1. 检查 {cwd}/.codo/settings.json
    2. 如果存在则返回路径，否则返回 None

    Args:
        cwd: 当前工作目录

    Returns:
        设置文件路径，或 None
    """
    # 项目级设置文件路径
    project_settings = os.path.join(cwd, ".codo", "settings.json")
    if os.path.exists(project_settings):
        return project_settings
    return None

def _load_settings_json(file_path: str) -> Optional[Dict[str, Any]]:
    """
    加载 settings.json 文件

    [Workflow]
    1. 检查文件是否存在
    2. 读取并解析 JSON
    3. 返回解析后的字典，或 None

    Args:
        file_path: 设置文件路径

    Returns:
        解析后的字典，或 None
    """
    if not os.path.exists(file_path):
        return None

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()

        if not content:
            return {}

        data = json.loads(content)
        return data if isinstance(data, dict) else None

    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"加载设置文件失败 {file_path}: {e}")
        return None

def _parse_hook_matcher(
    matcher_config: Dict[str, Any],
    event: str,
) -> List[HookConfig]:
    """
    解析单个 hook matcher 配置

    [Workflow]
    1. 提取 matcher 字段（工具名称过滤）
    2. 遍历 hooks 数组
    3. 只处理 type == "command" 的 hook（简化版）
    4. 创建 HookConfig 对象

    Args:
        matcher_config: matcher 配置字典
        event: hook 事件类型

    Returns:
        HookConfig 列表
    """
    # 提取 matcher（工具名称过滤，None 表示匹配所有工具）
    matcher = matcher_config.get("matcher")

    # 获取 hooks 数组
    hooks_list = matcher_config.get("hooks", [])
    if not isinstance(hooks_list, list):
        return []

    result = []
    for hook_def in hooks_list:
        if not isinstance(hook_def, dict):
            continue

        # 只处理 command 类型的 hook（简化版，不支持 prompt/agent/http）
        hook_type = hook_def.get("type", "command")
        if hook_type != "command":
            logger.debug(f"[hooks_loader] 跳过非 command 类型的 hook: {hook_type}")
            continue

        # 提取命令
        command = hook_def.get("command", "")
        if not command:
            continue

        # 提取超时（秒 → 毫秒）
        timeout_s = hook_def.get("timeout")
        if timeout_s is not None:
            timeout_ms = int(timeout_s * 1000)
        else:
            timeout_ms = DEFAULT_HOOK_TIMEOUT_MS

        # 创建 HookConfig
        result.append(HookConfig(
            command=command,
            tool_name=matcher,  # None 表示匹配所有工具
            event=event,
            timeout=timeout_ms,
        ))

    return result

def load_hooks_from_settings(cwd: str) -> Dict[str, List[HookConfig]]:
    """
    从设置文件加载 hooks 配置

    [Workflow]
    1. 获取设置文件路径
    2. 加载 settings.json
    3. 提取 hooks 字段
    4. 遍历每个事件类型
    5. 解析每个 matcher 配置
    6. 返回按事件类型分组的 HookConfig 字典

    Args:
        cwd: 当前工作目录

    Returns:
        按事件类型分组的 HookConfig 字典
        格式：{"PreToolUse": [...], "PostToolUse": [...], "Stop": [...]}
    """
    # 初始化结果字典
    result: Dict[str, List[HookConfig]] = {
        event: [] for event in SUPPORTED_HOOK_EVENTS
    }

    # 获取设置文件路径
    file_path = _get_settings_file_path(cwd)
    if not file_path:
        return result

    # 加载设置文件
    data = _load_settings_json(file_path)
    if not data:
        return result

    # 提取 hooks 字段
    hooks_config = data.get("hooks")
    if not isinstance(hooks_config, dict):
        return result

    # 遍历每个事件类型
    for event in SUPPORTED_HOOK_EVENTS:
        matchers = hooks_config.get(event)
        if not isinstance(matchers, list):
            continue

        # 解析每个 matcher 配置
        for matcher_config in matchers:
            if not isinstance(matcher_config, dict):
                continue

            hooks = _parse_hook_matcher(matcher_config, event)
            result[event].extend(hooks)

    # 记录加载结果
    total_hooks = sum(len(v) for v in result.values())
    if total_hooks > 0:
        logger.debug(
            f"[hooks_loader] 从 {file_path} 加载了 {total_hooks} 个 hooks: "
            + ", ".join(f"{k}={len(v)}" for k, v in result.items() if v)
        )

    return result

def get_hooks_for_event(
    cwd: str,
    event: str,
    tool_name: Optional[str] = None,
) -> List[HookConfig]:
    """
    获取指定事件和工具的 hooks

    [Workflow]
    1. 加载所有 hooks 配置
    2. 过滤指定事件的 hooks
    3. 如果指定了 tool_name，进一步过滤匹配的 hooks
    4. 返回过滤后的 HookConfig 列表

    Args:
        cwd: 当前工作目录
        event: hook 事件类型
        tool_name: 工具名称（可选，None 表示不过滤）

    Returns:
        匹配的 HookConfig 列表
    """
    all_hooks = load_hooks_from_settings(cwd)
    event_hooks = all_hooks.get(event, [])

    if tool_name is None:
        return event_hooks

    # 过滤匹配工具名称的 hooks
    return [
        hook for hook in event_hooks
        if hook.tool_name is None or hook.tool_name == tool_name
    ]
