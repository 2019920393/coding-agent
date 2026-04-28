"""
会话查询模块

本模块负责会话的查询、搜索、验证等功能。
"""

import json
import re
from pathlib import Path
from typing import Optional, List

from codo.session.storage import list_session_files
from codo.session.types import SessionInfo

def validate_uuid(value: str) -> bool:
    """
    验证字符串是否为有效的 UUID 格式

    [Workflow]
    1. 使用正则表达式验证 UUID v4 格式
    2. 格式：8-4-4-4-12 个十六进制字符

    Args:
        value: 待验证的字符串

    Returns:
        bool: 是否为有效的 UUID

    Examples:
        >>> validate_uuid("550e8400-e29b-41d4-a716-446655440000")
        True
        >>> validate_uuid("invalid-uuid")
        False
    """
    # UUID v4 正则表达式
    # 格式：xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
    uuid_pattern = re.compile(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        re.IGNORECASE
    )

    return bool(uuid_pattern.match(value))

def load_session_metadata(file_path: str) -> Optional[SessionInfo]:
    """
    加载会话元数据（轻量级读取）

    [Workflow]
    1. 读取会话文件的第一行和最后一行（轻量级）
    2. 从第一行提取：session_id, created_at, first_prompt
    3. 从最后一行提取：custom_title
    4. 从文件属性提取：file_size, last_modified
    5. 构建 SessionInfo 对象

    Args:
        file_path: 会话文件路径

    Returns:
        Optional[SessionInfo]: 会话元数据，如果读取失败则返回 None
    """
    try:
        path = Path(file_path)

        # 获取文件信息
        stat = path.stat()
        file_size = stat.st_size
        last_modified = stat.st_mtime

        # 提取会话 ID（文件名去掉 .jsonl 后缀）
        session_id = path.stem

        # 读取文件内容
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        if not lines:
            return None

        # 解析第一行（获取 created_at 和 first_prompt）
        first_line = json.loads(lines[0])
        created_at = first_line.get('timestamp')
        first_prompt = None

        # 查找第一个用户消息
        for line in lines:
            try:
                record = json.loads(line)
                record_type = record.get('type')
                role = record.get('role') or record_type
                if record_type in ('message', 'user', 'assistant') and role == 'user':
                    # 提取文本内容
                    content = record.get('content', [])
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get('type') == 'text':
                                first_prompt = block.get('text', '')
                                break
                    elif isinstance(content, str):
                        first_prompt = content
                    if first_prompt:
                        break
            except json.JSONDecodeError:
                continue

        # 解析最后一行（获取 custom_title）
        custom_title = None
        for line in reversed(lines):
            try:
                record = json.loads(line)
                record_type = record.get('type')
                if record_type in ('metadata', 'custom-title'):
                    custom_title = (
                        record.get('customTitle')
                        or record.get('custom_title')
                        or record.get('title')
                    )
                    break
            except json.JSONDecodeError:
                continue

        # 构建 summary
        summary = custom_title or (first_prompt[:50] + "..." if first_prompt and len(first_prompt) > 50 else first_prompt) or "Untitled"

        # 构建 SessionInfo
        return SessionInfo(
            session_id=session_id,
            summary=summary,
            last_modified=last_modified,
            file_size=file_size,
            custom_title=custom_title,
            first_prompt=first_prompt,
            cwd=None,  # 从第一行提取（如果需要）
            created_at=created_at
        )

    except (OSError, json.JSONDecodeError, KeyError):
        return None

def get_last_session(project_dir: str) -> Optional[SessionInfo]:
    """
    获取最近修改的会话

    [Workflow]
    1. 列出项目目录下的所有会话文件
    2. 按最后修改时间降序排序
    3. 返回最新的会话元数据

    Args:
        project_dir: 项目目录路径

    Returns:
        Optional[SessionInfo]: 最近的会话元数据，如果没有会话则返回 None
    """
    # 列出所有会话文件
    sessions = list_session_files(project_dir)

    if not sessions:
        return None

    # 按最后修改时间降序排序（最新的在前）
    sessions.sort(key=lambda x: x[3], reverse=True)

    # 获取最新的会话文件路径
    _, file_path, _, _ = sessions[0]

    # 加载会话元数据
    return load_session_metadata(file_path)

def list_all_sessions(project_dir: str) -> List[SessionInfo]:
    """
    列出项目目录下的所有会话

    [Workflow]
    1. 列出项目目录下的所有会话文件
    2. 加载每个会话的元数据
    3. 按最后修改时间降序排序
    4. 返回会话列表

    Args:
        project_dir: 项目目录路径

    Returns:
        List[SessionInfo]: 会话列表（按最后修改时间降序）
    """
    # 列出所有会话文件
    session_files = list_session_files(project_dir)

    sessions = []

    # 加载每个会话的元数据
    for _, file_path, _, _ in session_files:
        metadata = load_session_metadata(file_path)
        if metadata:
            sessions.append(metadata)

    # 按最后修改时间降序排序
    sessions.sort(key=lambda x: x.last_modified, reverse=True)

    return sessions

def search_sessions_by_title(
    title: str,
    project_dir: str,
    exact: bool = True
) -> List[SessionInfo]:
    """
    按标题搜索会话

    [Workflow]
    1. 列出所有会话
    2. 根据 exact 参数进行精确匹配或模糊匹配
    3. 返回匹配的会话列表

    Args:
        title: 搜索标题
        project_dir: 项目目录路径
        exact: 是否精确匹配（True=精确，False=模糊）

    Returns:
        List[SessionInfo]: 匹配的会话列表
    """
    # 列出所有会话
    all_sessions = list_all_sessions(project_dir)

    if not all_sessions:
        return []

    # 搜索匹配的会话
    matches = []

    for session in all_sessions:
        # 检查 custom_title
        if session.custom_title:
            if exact:
                # 精确匹配
                if session.custom_title == title:
                    matches.append(session)
            else:
                # 模糊匹配（不区分大小写）
                if title.lower() in session.custom_title.lower():
                    matches.append(session)

    return matches

def find_session_by_id(session_id: str, project_dir: str) -> Optional[SessionInfo]:
    """
    根据会话 ID 查找会话

    [Workflow]
    1. 验证 session_id 是否为有效的 UUID
    2. 构建会话文件路径
    3. 检查文件是否存在
    4. 加载并返回会话元数据

    Args:
        session_id: 会话 ID（UUID 格式）
        project_dir: 项目目录路径

    Returns:
        Optional[SessionInfo]: 会话元数据，如果不存在则返回 None
    """
    # 验证 UUID 格式
    if not validate_uuid(session_id):
        return None

    # 构建会话文件路径
    file_path = Path(project_dir) / f"{session_id}.jsonl"

    # 检查文件是否存在
    if not file_path.exists():
        return None

    # 加载会话元数据
    return load_session_metadata(str(file_path))
