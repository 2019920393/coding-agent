"""
配置管理模块

[Workflow]
1. 管理用户目录（~/.codo/）
2. 读写全局配置（~/.codo/settings.json）
3. 读写项目配置（{cwd}/.codo/settings.json）
4. 配置合并（全局 → 项目 → 环境变量）

简化：去除多用户/OAuth/analytics/自动更新等功能，
保留核心配置项：model、autoCompact、memory、permissions 等
"""

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

# 模块级日志记录器，用于记录配置读写过程中的警告和错误
logger = logging.getLogger(__name__)

# ============================================================================
# 目录管理
# ============================================================================

def get_user_dir() -> Path:
    """
    获取用户数据目录（~/.codo/）

    [Workflow]
    返回 ~/.codo/ 路径，如果不存在则创建
    """
    # 使用 Path.home() 获取当前用户的 home 目录，拼接 .codo 子目录
    return Path.home() / ".codo"

def get_sessions_dir() -> Path:
    """
    获取会话存储目录（~/.codo/sessions/）

    [Workflow]
    返回会话文件存储路径，用于持久化 JSONL 会话记录
    """
    # 在用户目录下创建 sessions 子目录
    return get_user_dir() / "sessions"

def get_memory_dir() -> Path:
    """
    获取记忆存储目录（~/.codo/memory/）

    [Workflow]
    返回记忆文件存储路径，用于持久化 agent 记忆内容
    """
    # 在用户目录下创建 memory 子目录
    return get_user_dir() / "memory"

def get_config_file() -> Path:
    """
    获取全局配置文件路径（~/.codo/settings.json）

    [Workflow]
    返回全局配置文件路径。
    """

    return get_user_dir() / "settings.json"

def get_project_config_file(cwd: str) -> Path:
    """
    获取项目配置文件路径（{cwd}/.codo/settings.json）

    [Workflow]
    1. 接收工作目录路径
    2. 拼接 .codo/settings.json 子路径
    3. 返回项目级配置文件路径

    Args:
        cwd: 项目工作目录

    Returns:
        项目配置文件路径
    """
    # 在项目目录下创建 .codo/settings.json 路径
    return Path(cwd) / ".codo" / "settings.json"

def ensure_user_dirs():
    """
    确保用户目录存在

    [Workflow]
    创建 ~/.codo/、~/.codo/sessions/、~/.codo/memory/ 目录
    如果目录已存在则跳过（exist_ok=True）
    """
    # 创建主用户目录，exist_ok=True 避免目录已存在时报错
    get_user_dir().mkdir(exist_ok=True)
    # 创建会话存储子目录
    get_sessions_dir().mkdir(exist_ok=True)
    # 创建记忆存储子目录
    get_memory_dir().mkdir(exist_ok=True)

# ============================================================================
# 全局配置数据类

# ============================================================================

@dataclass
class GlobalConfig:
    """
    全局配置

    [Workflow]
    包含所有用户级别的配置项，存储在 ~/.codo/settings.json
    字段命名使用 Python snake_case 风格，序列化时保持一致
    """
    # ---- 模型配置 ----
    # 默认模型名称，None 表示使用代码内置默认值（如 claude-sonnet-4-20250514）
    model: Optional[str] = None

    # ---- 功能开关 ----
    # 是否启用自动压缩
    auto_compact_enabled: bool = True
    # 是否启用详细输出（调试模式）
    verbose: bool = False

    # ---- 语言偏好 ----
    # 语言偏好，如 "zh-CN"、"English"，None 表示跟随系统
    language: Optional[str] = None

    # ---- 主题 ----
    # 终端主题，支持 dark/light
    theme: str = "dark"

    # ---- 记忆系统 ----
    # 是否启用记忆系统
    memory_enabled: bool = True

    # ---- 权限 ----
    # 是否已接受绕过权限模式的确认
    bypass_permissions_mode_accepted: bool = False

    # ---- 环境变量 ----
    # 额外注入的环境变量字典，会在工具执行时合并到进程环境
    env: Dict[str, str] = field(default_factory=dict)

    # ---- 自定义 API 配置 ----
    # 自定义 API base URL，用于代理或私有部署场景
    api_base_url: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """
        转换为字典（用于 JSON 序列化）

        [Workflow]
        1. 使用 dataclasses.asdict() 将 dataclass 转为字典
        2. 过滤掉值为 None 的字段，减少配置文件体积
        3. 返回干净的字典

        Returns:
            过滤 None 值后的配置字典
        """
        # asdict() 递归地将 dataclass 转换为字典（包括嵌套结构）
        d = asdict(self)
        # 过滤掉 None 值，避免配置文件中出现大量 null 字段
        return {k: v for k, v in d.items() if v is not None}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GlobalConfig":
        """
        从字典创建配置对象

        [Workflow]
        1. 获取 dataclass 所有已知字段名
        2. 从输入字典中只提取已知字段（忽略未知字段，向前兼容）
        3. 用过滤后的字典实例化 GlobalConfig

        Args:
            data: 从 JSON 文件读取的配置字典

        Returns:
            GlobalConfig 对象
        """
        # 获取所有已定义的字段名集合，用于过滤未知字段
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        # 只保留已知字段，忽略未来版本可能添加的新字段（向前兼容）
        filtered = {k: v for k, v in data.items() if k in known_fields}
        # 用过滤后的字典解包实例化
        return cls(**filtered)

# ============================================================================
# 配置读写函数
# ============================================================================

def _load_json_file(file_path: Path) -> Optional[Dict[str, Any]]:
    """
    加载 JSON 配置文件（内部辅助函数）

    [Workflow]
    1. 检查文件是否存在，不存在返回 None
    2. 读取文件内容并去除首尾空白
    3. 空文件返回空字典
    4. 解析 JSON，失败时记录警告并返回 None

    Args:
        file_path: 配置文件路径

    Returns:
        配置字典，文件不存在返回 None，解析失败返回 None
    """
    # 文件不存在时直接返回 None，调用方会使用默认值
    if not file_path.exists():
        return None

    try:
        # 读取文件内容，使用 UTF-8 编码支持中文配置值
        content = file_path.read_text(encoding="utf-8").strip()
        # 空文件视为空配置，返回空字典而非 None
        if not content:
            return {}
        # 解析 JSON 字符串为 Python 字典
        data = json.loads(content)
        # 确保解析结果是字典类型（防止 JSON 数组等非法格式）
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError) as e:
        # JSON 解析失败或文件读取失败时记录警告，不抛出异常
        logger.warning(f"加载配置文件失败 {file_path}: {e}")
        return None

def _save_json_file(file_path: Path, data: Dict[str, Any]) -> bool:
    """
    保存 JSON 配置文件（内部辅助函数）

    [Workflow]
    1. 确保父目录存在（parents=True 递归创建）
    2. 将字典序列化为格式化 JSON（indent=2）
    3. 写入文件，使用 UTF-8 编码支持中文

    Args:
        file_path: 配置文件路径
        data: 要保存的数据字典

    Returns:
        True 表示保存成功，False 表示失败
    """
    try:
        # 确保父目录存在，parents=True 支持多级目录创建
        file_path.parent.mkdir(parents=True, exist_ok=True)
        # 序列化为 JSON：indent=2 美化格式，ensure_ascii=False 支持中文字符
        file_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return True  # 写入成功
    except OSError as e:
        # 文件系统错误（权限不足、磁盘满等）时记录错误日志
        logger.error(f"保存配置文件失败 {file_path}: {e}")
        return False  # 写入失败

def get_global_config() -> GlobalConfig:
    """
    获取全局配置

    [Workflow]
    1. 调用 _load_json_file() 加载 ~/.codo/settings.json
    2. 如果文件不存在（返回 None），返回默认配置对象
    3. 调用 GlobalConfig.from_dict() 解析配置

    Returns:
        GlobalConfig 对象（文件不存在时返回默认值）
    """
    # 加载全局配置文件，返回 None 表示文件不存在
    data = _load_json_file(get_config_file())
    if data is None:
        # 文件不存在时返回全默认值的配置对象
        return GlobalConfig()
    # 从字典解析配置，忽略未知字段
    return GlobalConfig.from_dict(data)

def save_global_config(config: GlobalConfig) -> bool:
    """
    保存全局配置

    [Workflow]
    1. 将 GlobalConfig 对象转换为字典（过滤 None 值）
    2. 保存到 ~/.codo/settings.json

    Args:
        config: 要保存的 GlobalConfig 对象

    Returns:
        True 表示保存成功，False 表示失败
    """
    # 转换为字典后写入文件
    return _save_json_file(get_config_file(), config.to_dict())

def get_project_config(cwd: str) -> Dict[str, Any]:
    """
    获取项目配置

    [Workflow]
    1. 加载 {cwd}/.codo/settings.json
    2. 如果文件不存在，返回空字典（不影响全局配置）

    Args:
        cwd: 项目工作目录

    Returns:
        项目配置字典，文件不存在时返回空字典
    """
    # 加载项目配置文件，None 或空时返回空字典
    data = _load_json_file(get_project_config_file(cwd))
    return data or {}

def save_project_config(cwd: str, config: Dict[str, Any]) -> bool:
    """
    保存项目配置

    [Workflow]
    1. 加载现有项目配置（保留未知字段，向前兼容）
    2. 将新配置合并到现有配置（新值覆盖旧值）
    3. 保存合并后的配置到 {cwd}/.codo/settings.json

    Args:
        cwd: 项目工作目录
        config: 要保存/更新的配置字典

    Returns:
        True 表示保存成功，False 表示失败
    """
    # 先加载现有配置，保留未被更新的字段（向前兼容）
    existing = get_project_config(cwd)
    # 合并：existing 为基础，config 中的新值覆盖旧值
    merged = {**existing, **config}
    # 写入合并后的配置
    return _save_json_file(get_project_config_file(cwd), merged)

def get_merged_config(cwd: str) -> Dict[str, Any]:
    """
    获取合并后的配置（全局 → 项目 → 环境变量）

    [Workflow]
    1. 加载全局配置（~/.codo/settings.json）并转为字典
    2. 加载项目配置（{cwd}/.codo/settings.json）
    3. 项目配置覆盖全局配置（update 操作）
    4. 检查环境变量 CODO_MODEL、CODO_LANGUAGE，覆盖文件配置
    5. 返回最终合并后的配置字典

    Args:
        cwd: 项目工作目录

    Returns:
        合并后的配置字典（优先级：环境变量 > 项目配置 > 全局配置）
    """
    # 步骤 1：从全局配置开始，作为合并基础
    global_config = get_global_config()
    merged = global_config.to_dict()  # 转为字典，过滤了 None 值

    # 步骤 2：项目配置覆盖全局配置（update 会用项目值替换全局值）
    project_config = get_project_config(cwd)
    merged.update(project_config)

    # 步骤 3：环境变量具有最高优先级，覆盖文件配置
    # CODO_MODEL 环境变量指定模型名称
    if os.environ.get("CODO_MODEL"):
        merged["model"] = os.environ["CODO_MODEL"]
    # CODO_LANGUAGE 环境变量指定语言偏好
    if os.environ.get("CODO_LANGUAGE"):
        merged["language"] = os.environ["CODO_LANGUAGE"]

    return merged

def get_effective_model(cwd: str = "") -> Optional[str]:
    """
    获取有效的模型名称（按优先级查找）

    [Workflow]
    1. 检查环境变量 CODO_MODEL（最高优先级）
    2. 检查项目配置中的 model 字段
    3. 检查全局配置中的 model 字段
    4. 返回 None（调用方使用代码内置默认值）

    Args:
        cwd: 项目工作目录（可选，为空时跳过项目配置检查）

    Returns:
        模型名称字符串，或 None（表示使用内置默认值）
    """
    # 优先级 1：环境变量 CODO_MODEL 最高
    if os.environ.get("CODO_MODEL"):
        return os.environ["CODO_MODEL"]

    # 优先级 2：项目配置（仅当 cwd 非空时检查）
    if cwd:
        project_config = get_project_config(cwd)
        # 检查项目配置中是否有非空的 model 字段
        if project_config.get("model"):
            return project_config["model"]

    # 优先级 3：全局配置
    global_config = get_global_config()
    # 返回全局配置的 model（可能为 None，表示使用内置默认值）
    return global_config.model
