"""
会话恢复模块

本模块负责加载会话文件并恢复对话历史和状态。
"""

import json
from pathlib import Path
from typing import Optional, List, Dict, Any

from codo.session.storage import resolve_session_file_path
from codo.session.query import find_session_by_id, get_last_session
from codo.session.types import SessionInfo

def parse_jsonl_transcript(file_path: str) -> List[Dict[str, Any]]:
    """
    解析 JSONL 格式的会话文件

    [Workflow]
    1. 逐行读取 JSONL 文件
    2. 解析每行为 JSON 对象
    3. 返回记录列表

    Args:
        file_path: 会话文件路径

    Returns:
        List[Dict[str, Any]]: 记录列表

    Raises:
        FileNotFoundError: 文件不存在
        json.JSONDecodeError: JSON 解析错误
    """
    records = []

    # 读取文件
    with open(file_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue

            try:
                # 解析 JSON
                record = json.loads(line)
                records.append(record)
            except json.JSONDecodeError as e:
                # 记录解析错误，但继续处理其他行
                print(f"Warning: Failed to parse line {line_num} in {file_path}: {e}")
                continue

    return records

def extract_messages_from_transcript(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    从会话记录中提取消息

    [Workflow]
    1. 过滤出消息类型的记录（type='user'/'assistant'/'message'）
    2. 提取 role 和 content 字段
    3. 保留 uuid 和 parent_uuid（用于消息链）
    4. 构建消息列表

    Args:
        records: 会话记录列表

    Returns:
        List[Dict[str, Any]]: 消息列表
            每个消息包含：{ "role": "user"|"assistant", "content": [...], "uuid": "...", "parent_uuid": "..." }
    """
    messages = []

    for record in records:
        # 处理消息类型的记录
        record_type = record.get('type')
        if record_type not in ('user', 'assistant', 'message'):
            continue

        # 提取 role 和 content
        role = record.get('role')
        content = record.get('content')

        if not role or not content:
            continue

        # 构建消息对象（保留 uuid 和 parent_uuid）
        message = {
            "role": role,
            "content": content,
            "uuid": record.get('uuid'),
            "parent_uuid": record.get('parent_uuid'),
        }

        messages.append(message)

    return messages

def extract_todos_from_transcript(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    从会话记录中提取 TODO 列表

    [Workflow]
    1. 从后往前遍历记录
    2. 查找最后一个 TodoWrite tool_use
    3. 提取 todos 字段
    4. 返回 TODO 列表

    Args:
        records: 会话记录列表

    Returns:
        List[Dict[str, Any]]: TODO 列表
    """
    # 从后往前查找最后一个 TodoWrite tool_use
    for record in reversed(records):
        # 只处理 assistant 消息
        if record.get('type') not in ('assistant', 'message'):
            continue
        if record.get('role') != 'assistant':
            continue

        # 检查 content 是否包含 TodoWrite tool_use
        content = record.get('content', [])
        if not isinstance(content, list):
            continue

        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'tool_use' and block.get('name') == 'TodoWrite':
                tool_input = block.get('input', {})
                todos = tool_input.get('todos', [])
                return todos if isinstance(todos, list) else []

    return []

def extract_agent_setting_from_transcript(records: List[Dict[str, Any]]) -> Optional[str]:
    """
    从会话记录中提取 agent 设置

    [Workflow]
    1. 从后往前遍历记录
    2. 查找最后一个 agent-setting 记录
    3. 提取 agentType 字段
    4. 返回 agent 类型

    Args:
        records: 会话记录列表

    Returns:
        Optional[str]: agent 类型，如果没有则返回 None
    """
    # 从后往前查找最后一个 agent-setting 记录
    for record in reversed(records):
        if record.get('type') == 'agent-setting':
            agent_type = record.get('agentType')
            if agent_type:
                return agent_type

    return None

def extract_metadata_from_transcript(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    从会话记录中提取完整元数据

    [Workflow]
    1. 遍历所有记录
    2. 提取各种元数据类型：
       - summaries: 会话摘要（按 leafUuid 索引）
       - customTitles: 自定义标题（按 sessionId 索引）
       - tags: 标签（按 sessionId 索引）
       - agentNames: Agent 名称（按 sessionId 索引）
       - agentColors: Agent 颜色（按 sessionId 索引）
       - agentSettings: Agent 设置（按 sessionId 索引）
       - modes: 模式（按 sessionId 索引）
       - prNumbers/prUrls/prRepositories: PR 关联信息
       - worktreeStates: 工作树状态
    3. 返回元数据字典

    Args:
        records: 会话记录列表

    Returns:
        Dict[str, Any]: 元数据字典
            {
                "summaries": Dict[str, str],           # leafUuid -> summary
                "custom_titles": Dict[str, str],       # sessionId -> customTitle
                "tags": Dict[str, str],                # sessionId -> tag
                "agent_names": Dict[str, str],         # sessionId -> agentName
                "agent_colors": Dict[str, str],        # sessionId -> agentColor
                "agent_settings": Dict[str, str],      # sessionId -> agentSetting
                "modes": Dict[str, str],               # sessionId -> mode
                "pr_numbers": Dict[str, int],          # sessionId -> prNumber
                "pr_urls": Dict[str, str],             # sessionId -> prUrl
                "pr_repositories": Dict[str, str],     # sessionId -> prRepository
                "worktree_states": Dict[str, Any],     # sessionId -> worktreeSession
            }
    """
    metadata = {
        "summaries": {},
        "custom_titles": {},
        "tags": {},
        "agent_names": {},
        "agent_colors": {},
        "agent_settings": {},
        "modes": {},
        "pr_numbers": {},
        "pr_urls": {},
        "pr_repositories": {},
        "worktree_states": {},
    }

    for record in records:
        record_type = record.get('type')

        # 提取 summary (按 leafUuid 索引)
        if record_type == 'summary':
            leaf_uuid = record.get('leafUuid')
            summary = record.get('summary')
            if leaf_uuid and summary:
                metadata["summaries"][leaf_uuid] = summary

        # 提取 custom-title (按 sessionId 索引)
        elif record_type == 'custom-title':
            session_id = record.get('sessionId')
            custom_title = record.get('customTitle')
            if session_id and custom_title:
                metadata["custom_titles"][session_id] = custom_title

        # 提取 tag (按 sessionId 索引)
        elif record_type == 'tag':
            session_id = record.get('sessionId')
            tag = record.get('tag')
            if session_id and tag:
                metadata["tags"][session_id] = tag

        # 提取 agent-name (按 sessionId 索引)
        elif record_type == 'agent-name':
            session_id = record.get('sessionId')
            agent_name = record.get('agentName')
            if session_id and agent_name:
                metadata["agent_names"][session_id] = agent_name

        # 提取 agent-color (按 sessionId 索引)
        elif record_type == 'agent-color':
            session_id = record.get('sessionId')
            agent_color = record.get('agentColor')
            if session_id and agent_color:
                metadata["agent_colors"][session_id] = agent_color

        # 提取 agent-setting (按 sessionId 索引)
        elif record_type == 'agent-setting':
            session_id = record.get('sessionId')
            agent_setting = record.get('agentSetting')
            if session_id and agent_setting:
                metadata["agent_settings"][session_id] = agent_setting

        # 提取 mode (按 sessionId 索引)
        elif record_type == 'mode':
            session_id = record.get('sessionId')
            mode = record.get('mode')
            if session_id and mode:
                metadata["modes"][session_id] = mode

        # 提取 worktree-state (按 sessionId 索引)
        elif record_type == 'worktree-state':
            session_id = record.get('sessionId')
            worktree_session = record.get('worktreeSession')
            if session_id:
                metadata["worktree_states"][session_id] = worktree_session

        # 提取 pr-link (按 sessionId 索引)
        elif record_type == 'pr-link':
            session_id = record.get('sessionId')
            pr_number = record.get('prNumber')
            pr_url = record.get('prUrl')
            pr_repository = record.get('prRepository')
            if session_id:
                if pr_number is not None:
                    metadata["pr_numbers"][session_id] = pr_number
                if pr_url:
                    metadata["pr_urls"][session_id] = pr_url
                if pr_repository:
                    metadata["pr_repositories"][session_id] = pr_repository

    return metadata

def load_session_for_resume(
    session_id: Optional[str],
    project_dir: str
) -> Optional[Dict[str, Any]]:
    """
    加载会话用于恢复

    (src/utils/conversationRecovery.ts + src/utils/sessionRestore.ts)

    [Workflow]
    1. 如果 session_id 为 None，查找最近的会话
    2. 如果 session_id 不为 None，查找指定的会话
    3. 加载会话文件（JSONL）
    4. 解析消息历史
    5. 提取状态（todos, agent_setting, metadata）
    6. 返回恢复数据

    Args:
        session_id: 会话 ID（None 表示查找最近的会话）
        project_dir: 项目目录路径

    Returns:
        Optional[Dict[str, Any]]: 恢复数据
            {
                "session_info": SessionInfo,     # 会话元数据
                "messages": List[Dict],          # 消息历史（包含 uuid 和 parent_uuid）
                "todos": List[Dict],             # TODO 列表
                "agent_setting": Optional[str],  # agent 设置
                "metadata": Dict[str, Any],      # 元数据（title, tags, summary）
                "file_path": str                 # 会话文件路径
            }
            如果加载失败则返回 None
    """
    # 步骤 1: 查找会话
    session_info: Optional[SessionInfo] = None

    if session_id is None:
        # 查找最近的会话
        session_info = get_last_session(project_dir)
        if not session_info:
            return None
    else:
        # 查找指定的会话
        session_info = find_session_by_id(session_id, project_dir)
        if not session_info:
            return None

    # 步骤 2: 解析会话文件路径
    file_path = resolve_session_file_path(session_info.session_id, project_dir)
    if not file_path:
        return None

    # 步骤 3: 加载会话文件
    try:
        records = parse_jsonl_transcript(file_path)
    except (FileNotFoundError, OSError) as e:
        print(f"Error: Failed to load session file {file_path}: {e}")
        return None

    # 步骤 4: 提取消息历史（包含 uuid 和 parent_uuid）
    messages = extract_messages_from_transcript(records)

    # 步骤 5: 提取状态
    todos = extract_todos_from_transcript(records)
    agent_setting = extract_agent_setting_from_transcript(records)
    metadata = extract_metadata_from_transcript(records)

    # 步骤 6: 返回恢复数据
    return {
        "session_info": session_info,
        "messages": messages,
        "todos": todos,
        "agent_setting": agent_setting,
        "metadata": metadata,
        "file_path": file_path
    }

def restore_session_state(resume_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    从恢复数据中提取状态

    [Workflow]
    1. 提取 todos
    2. 提取 agent_setting
    3. 提取 metadata
    4. 返回状态字典

    Args:
        resume_data: 恢复数据（来自 load_session_for_resume）

    Returns:
        Dict[str, Any]: 状态字典
            {
                "todos": List[Dict],
                "agent_setting": Optional[str],
                "metadata": Dict[str, Any],
            }
    """
    return {
        "todos": resume_data.get("todos", []),
        "agent_setting": resume_data.get("agent_setting"),
        "metadata": resume_data.get("metadata", {}),
    }

def validate_session_data(data: Optional[Dict[str, Any]]) -> bool:
    """
    验证会话数据是否有效

    [Workflow]
    1. 检查数据是否为 None
    2. 检查必需字段是否存在
    3. 检查消息列表是否为空

    Args:
        data: 会话数据

    Returns:
        bool: 是否有效
    """
    if data is None:
        return False

    # 检查必需字段
    if "session_info" not in data or "messages" not in data or "file_path" not in data:
        return False

    # 检查消息列表是否为空
    if not data["messages"]:
        return False

    return True
