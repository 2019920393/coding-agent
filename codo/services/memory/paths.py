"""
memory 路径与目录管理。

memory 目录结构：
  ~/.codo/projects/<sanitized-cwd>/memory/
    MEMORY.md          # 索引文件（会被加载进系统提示）
    topic_file.md      # 带 frontmatter 的独立记忆文件
"""

import os
import re
from pathlib import Path

from codo.utils.config import get_user_dir

def get_memory_base_dir() -> Path:
    """获取记忆存储根目录，并兼容远程环境下的覆盖配置。"""
    override = os.environ.get("CODO_REMOTE_MEMORY_DIR")
    if override:
        return Path(override).expanduser()
    return get_user_dir()

def get_projects_dir() -> Path:
    """获取项目目录（`~/.codo/projects/`）。"""
    return get_memory_base_dir() / "projects"

def sanitize_path_for_dir(path: str) -> str:
    """
    将文件系统路径清洗为安全的目录名。

    会把分隔符和特殊字符替换为短横线。
    """
    sanitized = re.sub(r"[\\/:*?\"<>|]", "-", path)
    sanitized = re.sub(r"-+", "-", sanitized)
    sanitized = sanitized.strip("-")
    return sanitized

def get_project_memory_dir(cwd: str) -> Path:
    """
    获取指定项目对应的 memory 目录。

    Returns:
        `~/.codo/projects/<sanitized-cwd>/memory/` 路径
    """
    sanitized = sanitize_path_for_dir(cwd)
    return get_projects_dir() / sanitized / "memory"

def ensure_memory_dir(cwd: str) -> Path:
    """确保 memory 目录存在，并返回其路径。"""
    memory_dir = get_project_memory_dir(cwd)
    memory_dir.mkdir(parents=True, exist_ok=True)
    return memory_dir

def get_memory_index_path(cwd: str) -> Path:
    """获取 `MEMORY.md` 索引文件路径。"""
    return get_project_memory_dir(cwd) / "MEMORY.md"

def get_auto_memory_path(project_root: str) -> str:
    """兼容旧接口的别名，返回项目级 memory 目录。"""
    return str(ensure_memory_dir(project_root))

def sanitize_filename(name: str) -> str:
    """将主题名称清洗为安全的文件名片段。"""
    cleaned = name.lower().replace(" ", "_")
    return "".join(char for char in cleaned if char.isalnum() or char in "_-")

def get_memory_file_path(memory_dir: str, memory_type: str, topic: str) -> str:
    """为指定主题生成 memory 目录内的文件路径。"""
    filename = f"{memory_type}_{sanitize_filename(topic)}.md"
    return str(Path(memory_dir) / filename)

def is_memory_path(filepath: str, cwd: str) -> bool:
    """检查某个文件路径是否位于 memory 目录内部。"""
    try:
        memory_dir = get_project_memory_dir(cwd)
        filepath_resolved = Path(filepath).resolve()
        memory_dir_resolved = memory_dir.resolve()
        return str(filepath_resolved).startswith(str(memory_dir_resolved))
    except Exception:
        return False

# 入口文件与截断相关常量
ENTRYPOINT_NAME = "MEMORY.md"
MAX_ENTRYPOINT_LINES = 200
MAX_ENTRYPOINT_BYTES = 25_000
