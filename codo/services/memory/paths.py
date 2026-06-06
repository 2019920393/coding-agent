"""
memory 路径管理。

memory 目录结构（用户级，跨项目共享）：
  ~/.codo/memory/
    MEMORY.md          # 索引文件（会被加载进系统提示）
    topic_file.md      # 带 frontmatter 的独立记忆文件

历史上曾按 cwd 切到 `~/.codo/projects/<sanitized-cwd>/memory/`，
后来发现跨项目偏好（如喜欢的语言）写到项目里跨不过来，统一改为用户级。
"""

import os
from pathlib import Path

from codo.utils.config import get_user_dir


def get_memory_base_dir() -> Path:
    """获取记忆存储根目录，并兼容远程环境下的覆盖配置。"""
    override = os.environ.get("CODO_REMOTE_MEMORY_DIR")
    if override:
        return Path(override).expanduser()
    return get_user_dir()


def get_memory_dir() -> Path:
    """返回用户级 memory 目录（`~/.codo/memory/`）。"""
    return get_memory_base_dir() / "memory"


def ensure_memory_dir() -> Path:
    """确保 memory 目录存在，并返回其路径。"""
    memory_dir = get_memory_dir()
    memory_dir.mkdir(parents=True, exist_ok=True)
    return memory_dir


def get_memory_index_path() -> Path:
    """获取 `MEMORY.md` 索引文件路径。"""
    return get_memory_dir() / ENTRYPOINT_NAME


def is_memory_path(filepath: str) -> bool:
    """检查某个文件路径是否位于 memory 目录内部。"""
    try:
        memory_dir = get_memory_dir()
        filepath_resolved = Path(filepath).resolve()
        memory_dir_resolved = memory_dir.resolve()
        return str(filepath_resolved).startswith(str(memory_dir_resolved))
    except Exception:
        return False


# 入口文件与截断相关常量
ENTRYPOINT_NAME = "MEMORY.md"
MAX_ENTRYPOINT_LINES = 200
MAX_ENTRYPOINT_BYTES = 25_000
