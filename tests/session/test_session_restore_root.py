"""
测试会话状态恢复功能

测试核心功能：
1. TODO 列表恢复
2. Agent 设置恢复
3. 元数据恢复
4. 完整会话恢复流程
"""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from codo.session.storage import SessionStorage
from codo.session.restore import (
    parse_jsonl_transcript,
    extract_messages_from_transcript,
    extract_todos_from_transcript,
    extract_agent_setting_from_transcript,
    extract_metadata_from_transcript,
    load_session_for_resume,
    restore_session_state,
    validate_session_data,
)

def test_extract_todos_from_transcript():
    """
    测试从会话记录中提取 TODO 列表

    验证：
    1. 能够找到最后一个 TodoWrite tool_use
    2. 正确提取 todos 字段
    3. 返回正确的 TODO 列表
    """
    # 创建测试记录
    records = [
        {
            "type": "user",
            "role": "user",
            "content": [{"type": "text", "text": "Create a todo list"}],
            "uuid": str(uuid4()),
        },
        {
            "type": "assistant",
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "name": "TodoWrite",
                    "input": {
                        "todos": [
                            {"content": "Task 1", "status": "pending", "activeForm": "Doing task 1"},
                            {"content": "Task 2", "status": "in_progress", "activeForm": "Doing task 2"},
                        ]
                    },
                }
            ],
            "uuid": str(uuid4()),
        },
        {
            "type": "user",
            "role": "user",
            "content": [{"type": "text", "text": "Update the list"}],
            "uuid": str(uuid4()),
        },
        {
            "type": "assistant",
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "name": "TodoWrite",
                    "input": {
                        "todos": [
                            {"content": "Task 1", "status": "completed", "activeForm": "Doing task 1"},
                            {"content": "Task 2", "status": "in_progress", "activeForm": "Doing task 2"},
                            {"content": "Task 3", "status": "pending", "activeForm": "Doing task 3"},
                        ]
                    },
                }
            ],
            "uuid": str(uuid4()),
        },
    ]

    # 提取 TODO 列表
    todos = extract_todos_from_transcript(records)

    # 验证：应该返回最后一个 TodoWrite 的 todos（3 个任务）
    assert len(todos) == 3
    assert todos[0]["content"] == "Task 1"
    assert todos[0]["status"] == "completed"
    assert todos[1]["content"] == "Task 2"
    assert todos[1]["status"] == "in_progress"
    assert todos[2]["content"] == "Task 3"
    assert todos[2]["status"] == "pending"

    print("✓ TODO 提取测试通过")

def test_extract_agent_setting_from_transcript():
    """
    测试从会话记录中提取 Agent 设置

    验证：
    1. 能够找到最后一个 agent-setting 记录
    2. 正确提取 agentType 字段
    """
    # 创建测试记录
    records = [
        {
            "type": "agent-setting",
            "sessionId": "test-session",
            "agentType": "explore",
            "timestamp": "2024-01-01T00:00:00Z",
        },
        {
            "type": "user",
            "role": "user",
            "content": [{"type": "text", "text": "Hello"}],
            "uuid": str(uuid4()),
        },
        {
            "type": "agent-setting",
            "sessionId": "test-session",
            "agentType": "code-reviewer",
            "timestamp": "2024-01-01T00:01:00Z",
        },
    ]

    # 提取 Agent 设置
    agent_setting = extract_agent_setting_from_transcript(records)

    # 验证：应该返回最后一个 agent-setting 的 agentType
    assert agent_setting == "code-reviewer"

    print("✓ Agent 设置提取测试通过")

def test_extract_metadata_from_transcript():
    """
    测试从会话记录中提取元数据

    验证：
    1. 正确提取各种元数据类型
    2. 按 sessionId 或 leafUuid 索引
    """
    session_id = "test-session"
    leaf_uuid = str(uuid4())

    # 创建测试记录
    records = [
        {
            "type": "custom-title",
            "sessionId": session_id,
            "customTitle": "My Custom Title",
            "source": "user",
        },
        {
            "type": "tag",
            "sessionId": session_id,
            "tag": "bug-fix",
        },
        {
            "type": "summary",
            "sessionId": session_id,
            "summary": "Fixed authentication bug",
            "leafUuid": leaf_uuid,
        },
        {
            "type": "agent-name",
            "sessionId": session_id,
            "agentName": "code-reviewer",
        },
        {
            "type": "agent-color",
            "sessionId": session_id,
            "agentColor": "#FF5733",
        },
        {
            "type": "mode",
            "sessionId": session_id,
            "mode": "plan",
        },
    ]

    # 提取元数据
    metadata = extract_metadata_from_transcript(records)

    # 验证
    assert metadata["custom_titles"][session_id] == "My Custom Title"
    assert metadata["tags"][session_id] == "bug-fix"
    assert metadata["summaries"][leaf_uuid] == "Fixed authentication bug"
    assert metadata["agent_names"][session_id] == "code-reviewer"
    assert metadata["agent_colors"][session_id] == "#FF5733"
    assert metadata["modes"][session_id] == "plan"

    print("✓ 元数据提取测试通过")

def test_full_session_restore_workflow():
    """
    测试完整的会话恢复流程

    验证：
    1. 创建会话并记录消息、TODO、元数据
    2. 使用 load_session_for_resume() 加载会话
    3. 验证所有状态正确恢复
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        with patch('codo.session.storage.get_sessions_dir', return_value=tmpdir_path):
            # 步骤 1: 创建会话并记录数据
            session_id = "test-restore-session"
            session = SessionStorage(
                session_id=session_id,
                cwd="/test/path"
            )

            # 记录消息
            msg1_uuid = str(uuid4())
            msg2_uuid = str(uuid4())
            messages = [
                {
                    "type": "user",
                    "role": "user",
                    "content": [{"type": "text", "text": "Hello"}],
                    "uuid": msg1_uuid,
                },
                {
                    "type": "assistant",
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Hi there!"},
                        {
                            "type": "tool_use",
                            "name": "TodoWrite",
                            "input": {
                                "todos": [
                                    {"content": "Task 1", "status": "pending", "activeForm": "Doing task 1"},
                                    {"content": "Task 2", "status": "in_progress", "activeForm": "Doing task 2"},
                                ]
                            },
                        },
                    ],
                    "uuid": msg2_uuid,
                },
            ]
            asyncio.run(session.insert_message_chain(messages))

            # 保存元数据
            session.save_title("Test Session", source="user")
            session.save_tag("test")
            session.save_agent_name("code-reviewer")
            session.save_mode("plan")

            # 保存 agent-setting（注意：这里需要直接写入，因为没有专门的方法）
            session.save_metadata("agent-setting", {"agentType": "explore"})

            # 步骤 2: 模拟会话恢复
            # 需要 mock session_query 和 session_storage 模块
            with patch('codo.session.restore.find_session_by_id') as mock_find:
                with patch('codo.session.restore.resolve_session_file_path') as mock_resolve:
                    # Mock 返回值
                    from codo.session.types import SessionInfo
                    import time
                    mock_find.return_value = SessionInfo(
                        session_id=session_id,
                        summary="Test Session",
                        last_modified=time.time(),
                        file_size=1024,
                        custom_title="Test Session",
                        cwd="/test/path",
                    )
                    mock_resolve.return_value = str(session.session_file)

                    # 加载会话
                    resume_data = load_session_for_resume(session_id, "/test/path")

                    # 验证恢复数据
                    assert resume_data is not None
                    assert validate_session_data(resume_data)

                    # 验证消息
                    assert len(resume_data["messages"]) == 2
                    assert resume_data["messages"][0]["uuid"] == msg1_uuid
                    assert resume_data["messages"][1]["uuid"] == msg2_uuid

                    # 验证 TODO 列表
                    assert len(resume_data["todos"]) == 2
                    assert resume_data["todos"][0]["content"] == "Task 1"
                    assert resume_data["todos"][1]["content"] == "Task 2"

                    # 验证 Agent 设置
                    assert resume_data["agent_setting"] == "explore"

                    # 验证元数据
                    metadata = resume_data["metadata"]
                    assert metadata["custom_titles"][session_id] == "Test Session"
                    assert metadata["tags"][session_id] == "test"
                    assert metadata["agent_names"][session_id] == "code-reviewer"
                    assert metadata["modes"][session_id] == "plan"

                    # 步骤 3: 使用 restore_session_state() 提取状态
                    state = restore_session_state(resume_data)
                    assert len(state["todos"]) == 2
                    assert state["agent_setting"] == "explore"
                    assert state["metadata"]["custom_titles"][session_id] == "Test Session"

    print("✓ 完整会话恢复流程测试通过")

def test_empty_session_restore():
    """
    测试空会话恢复

    验证：
    1. 没有 TODO 时返回空列表
    2. 没有 Agent 设置时返回 None
    3. 没有元数据时返回空字典
    """
    # 创建空记录
    records = [
        {
            "type": "user",
            "role": "user",
            "content": [{"type": "text", "text": "Hello"}],
            "uuid": str(uuid4()),
        },
    ]

    # 提取状态
    todos = extract_todos_from_transcript(records)
    agent_setting = extract_agent_setting_from_transcript(records)
    metadata = extract_metadata_from_transcript(records)

    # 验证
    assert todos == []
    assert agent_setting is None
    assert metadata["custom_titles"] == {}
    assert metadata["tags"] == {}

    print("✓ 空会话恢复测试通过")

def test_parse_jsonl_transcript():
    """
    测试 JSONL 文件解析

    验证：
    1. 正确解析 JSONL 格式
    2. 跳过空行
    3. 处理解析错误
    """
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False, encoding='utf-8') as f:
        # 写入测试数据
        f.write('{"type": "user", "content": "Hello"}\n')
        f.write('\n')  # 空行
        f.write('{"type": "assistant", "content": "Hi"}\n')
        f.write('invalid json\n')  # 无效 JSON
        f.write('{"type": "user", "content": "Bye"}\n')
        temp_file = f.name

    try:
        # 解析文件
        records = parse_jsonl_transcript(temp_file)

        # 验证：应该有 3 条有效记录（跳过空行和无效 JSON）
        assert len(records) == 3
        assert records[0]["type"] == "user"
        assert records[1]["type"] == "assistant"
        assert records[2]["type"] == "user"

        print("✓ JSONL 解析测试通过")
    finally:
        # 清理临时文件
        Path(temp_file).unlink()

if __name__ == "__main__":
    print("开始测试会话状态恢复功能...\n")

    test_extract_todos_from_transcript()
    test_extract_agent_setting_from_transcript()
    test_extract_metadata_from_transcript()
    test_parse_jsonl_transcript()
    test_empty_session_restore()
    test_full_session_restore_workflow()

    print("\n✅ 所有会话状态恢复测试通过！")
