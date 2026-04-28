"""PlanMode 工具函数"""
import os
import random
from pathlib import Path
from typing import Optional

# 简单的词库用于生成 slug
ADJECTIVES = [
    "happy", "clever", "brave", "swift", "bright", "calm", "eager", "gentle",
    "jolly", "kind", "lively", "merry", "noble", "proud", "quiet", "wise"
]

NOUNS = [
    "fox", "bear", "wolf", "eagle", "lion", "tiger", "hawk", "owl",
    "deer", "rabbit", "falcon", "dragon", "phoenix", "unicorn", "griffin"
]

def generate_word_slug() -> str:
    """生成随机词组 slug"""
    adj1 = random.choice(ADJECTIVES)
    adj2 = random.choice(ADJECTIVES)
    noun = random.choice(NOUNS)
    return f"{adj1}-{adj2}-{noun}"

def get_plans_directory() -> str:
    """获取计划文件目录"""
    # 默认使用 ~/.codo/plans
    home = Path.home()
    codo_dir = home / ".codo"
    plans_dir = codo_dir / "plans"

    # 确保目录存在
    plans_dir.mkdir(parents=True, exist_ok=True)

    return str(plans_dir)

def get_plan_file_path(session_id: str, agent_id: Optional[str] = None) -> str:
    """获取计划文件路径

    Args:
        session_id: 会话 ID
        agent_id: 可选的代理 ID

    Returns:
        计划文件的完整路径
    """
    plans_dir = get_plans_directory()

    # 生成唯一的 slug
    slug = generate_word_slug()

    # 主会话: {slug}.md
    # 子代理: {slug}-agent-{agentId}.md
    if agent_id:
        filename = f"{slug}-agent-{agent_id}.md"
    else:
        filename = f"{slug}.md"

    return os.path.join(plans_dir, filename)

def read_plan_file(file_path: str) -> Optional[str]:
    """读取计划文件

    Args:
        file_path: 计划文件路径

    Returns:
        计划内容，如果文件不存在返回 None
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return None
    except Exception:
        return None

def write_plan_file(file_path: str, content: str) -> bool:
    """写入计划文件

    Args:
        file_path: 计划文件路径
        content: 计划内容

    Returns:
        是否写入成功
    """
    try:
        # 确保目录存在
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return True
    except Exception:
        return False
