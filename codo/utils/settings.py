"""
设置管理模块

[Workflow]
1. 提供统一的设置读写接口
2. 支持全局设置和项目设置
3. 支持设置合并

简化：去除 policySettings、flagSettings、localSettings 等复杂来源，
保留核心的全局设置和项目设置两层结构
"""

import logging
from typing import Any, Dict, Optional

# 从 config 模块导入底层配置读写函数和数据类
from codo.utils.config import (
    get_global_config,
    save_global_config,
    get_project_config,
    save_project_config,
    get_merged_config,
    GlobalConfig,
)

# 模块级日志记录器
logger = logging.getLogger(__name__)

def get_settings(cwd: str = "") -> Dict[str, Any]:
    """
    获取合并后的设置

    [Workflow]
    1. 调用 get_merged_config() 加载全局设置
    2. 加载项目设置（如果 cwd 非空）
    3. 合并并返回最终设置字典

    Args:
        cwd: 项目工作目录（可选，为空时只返回全局设置）

    Returns:
        合并后的设置字典（优先级：环境变量 > 项目 > 全局）
    """
    # 直接委托给 get_merged_config，它已实现完整的三层合并逻辑
    return get_merged_config(cwd)

def update_global_setting(key: str, value: Any) -> bool:
    """
    更新全局设置中的单个配置项

    [Workflow]
    1. 加载当前全局配置对象
    2. 检查字段是否存在于 GlobalConfig 中（防止写入未知字段）
    3. 使用 setattr 更新指定字段
    4. 保存更新后的配置到文件

    Args:
        key: 配置键名（使用 Python snake_case 命名，如 auto_compact_enabled）
        value: 要设置的配置值

    Returns:
        True 表示更新成功，False 表示字段不存在或保存失败
    """
    # 加载当前全局配置，获取最新状态
    config = get_global_config()

    # 检查字段是否在 GlobalConfig 中定义，防止写入未知字段
    if not hasattr(config, key):
        # 记录警告：尝试设置未知配置项
        logger.warning(f"未知的配置项: {key}")
        return False  # 返回 False 表示操作失败

    # 使用 setattr 动态更新字段值
    setattr(config, key, value)

    # 将更新后的配置保存到 ~/.codo/settings.json
    return save_global_config(config)

def update_project_setting(cwd: str, key: str, value: Any) -> bool:
    """
    更新项目设置中的单个配置项

    [Workflow]
    1. 构造单键值字典
    2. 调用 save_project_config() 合并并保存
    （save_project_config 内部会先加载现有配置再合并）

    Args:
        cwd: 项目工作目录
        key: 配置键名
        value: 要设置的配置值

    Returns:
        True 表示更新成功，False 表示保存失败
    """
    # 构造单键值字典，委托给 save_project_config 处理合并逻辑
    return save_project_config(cwd, {key: value})
