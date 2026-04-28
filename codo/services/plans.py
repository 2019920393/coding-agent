"""
Plan 文件管理模块

本模块负责管理 Plan 文件的创建、读取、保存和路径管理。
"""

import os
import random
from pathlib import Path
from typing import Optional, Dict

from codo.utils.config import get_user_dir

# 全局缓存：session_id -> plan_slug
_plan_slug_cache: Dict[str, str] = {}

# 最大重试次数（避免 slug 冲突）
MAX_SLUG_RETRIES = 10

def generate_word_slug() -> str:
    """
    生成随机单词 slug

    格式：{adjective}-{adjective}-{noun}
    例如：happy-blue-elephant

    Returns:
        str: 随机生成的 slug
    """
    adjectives = [
        "happy", "blue", "quick", "bright", "calm", "eager", "gentle", "kind",
        "lively", "proud", "silly", "witty", "brave", "clever", "fancy", "jolly",
        "mighty", "polite", "shiny", "tender", "vivid", "wise", "zesty", "bold",
        "charming", "daring", "elegant", "fierce", "graceful", "humble", "keen",
        "linked", "wibbling", "valiant", "zealous", "agile", "bouncy", "crisp"
    ]

    nouns = [
        "elephant", "tiger", "dolphin", "eagle", "fox", "giraffe", "hawk", "iguana",
        "jaguar", "koala", "lion", "monkey", "newt", "owl", "panda", "quail",
        "rabbit", "seal", "turtle", "unicorn", "viper", "whale", "xerus", "yak",
        "zebra", "antelope", "bear", "cheetah", "deer", "falcon", "gazelle"
    ]

    adj1 = random.choice(adjectives)
    adj2 = random.choice(adjectives)
    noun = random.choice(nouns)

    return f"{adj1}-{adj2}-{noun}"

def get_plans_directory() -> Path:
    """
    获取 Plans 目录路径

    [Workflow]
    1. 检查 settings.json 中的 plansDirectory 配置
    2. 如果配置了，使用相对于项目根目录的路径
    3. 如果没有配置，使用默认路径：~/.codo/plans
    4. 确保目录存在

    Returns:
        Path: Plans 目录路径
    """
    # TODO: 从 settings.json 读取配置
    # 目前使用默认路径
    plans_path = get_user_dir() / "plans"

    # 确保目录存在
    plans_path.mkdir(parents=True, exist_ok=True)

    return plans_path

def get_plan_slug(session_id: str) -> str:
    """
    获取或生成会话的 plan slug

    [Workflow]
    1. 检查缓存中是否已有 slug
    2. 如果没有，生成新的 slug
    3. 检查文件是否已存在，如果存在则重新生成（最多重试 10 次）
    4. 缓存 slug

    Args:
        session_id: 会话 ID

    Returns:
        str: Plan slug
    """
    # 检查缓存
    if session_id in _plan_slug_cache:
        return _plan_slug_cache[session_id]

    # 生成新的 slug
    plans_dir = get_plans_directory()
    slug = None

    for _ in range(MAX_SLUG_RETRIES):
        slug = generate_word_slug()
        file_path = plans_dir / f"{slug}.md"
        if not file_path.exists():
            break

    # 缓存 slug
    if slug:
        _plan_slug_cache[session_id] = slug

    return slug

def set_plan_slug(session_id: str, slug: str) -> None:
    """
    设置会话的 plan slug（用于恢复会话）

    Args:
        session_id: 会话 ID
        slug: Plan slug
    """
    _plan_slug_cache[session_id] = slug

def clear_plan_slug(session_id: str) -> None:
    """
    清除会话的 plan slug

    Args:
        session_id: 会话 ID
    """
    _plan_slug_cache.pop(session_id, None)

def clear_all_plan_slugs() -> None:
    """
    清除所有会话的 plan slug

    """
    _plan_slug_cache.clear()

def get_plan_file_path(session_id: str, agent_id: Optional[str] = None) -> Path:
    """
    获取会话的 plan 文件路径

    [Workflow]
    1. 获取 plan slug
    2. 如果没有 agent_id，返回主会话的 plan 文件路径：{slug}.md
    3. 如果有 agent_id，返回子 agent 的 plan 文件路径：{slug}-agent-{agent_id}.md

    Args:
        session_id: 会话 ID
        agent_id: Agent ID（可选，用于子 agent）

    Returns:
        Path: Plan 文件路径
    """
    slug = get_plan_slug(session_id)
    plans_dir = get_plans_directory()

    if agent_id is None:
        # 主会话
        return plans_dir / f"{slug}.md"
    else:
        # 子 agent
        return plans_dir / f"{slug}-agent-{agent_id}.md"

def get_plan(session_id: str, agent_id: Optional[str] = None) -> Optional[str]:
    """
    读取会话的 plan 内容

    Args:
        session_id: 会话 ID
        agent_id: Agent ID（可选）

    Returns:
        Optional[str]: Plan 内容，如果文件不存在则返回 None
    """
    file_path = get_plan_file_path(session_id, agent_id)

    try:
        return file_path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"Error reading plan file {file_path}: {e}")
        return None

def save_plan(session_id: str, content: str, agent_id: Optional[str] = None) -> Path:
    """
    保存 plan 内容到文件

    Args:
        session_id: 会话 ID
        content: Plan 内容
        agent_id: Agent ID（可选）

    Returns:
        Path: Plan 文件路径
    """
    file_path = get_plan_file_path(session_id, agent_id)

    try:
        file_path.write_text(content, encoding='utf-8')
        return file_path
    except Exception as e:
        print(f"Error saving plan file {file_path}: {e}")
        raise

def plan_exists(session_id: str, agent_id: Optional[str] = None) -> bool:
    """
    检查 plan 文件是否存在

    Args:
        session_id: 会话 ID
        agent_id: Agent ID（可选）

    Returns:
        bool: 文件是否存在
    """
    file_path = get_plan_file_path(session_id, agent_id)
    return file_path.exists()
