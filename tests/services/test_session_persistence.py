"""
测试会话持久化功能

"""

import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

from codo.session.storage import SessionStorage, SessionManager

@pytest.fixture
def temp_session_dir(monkeypatch):
    """创建临时会话目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(
            "codo.session.storage.get_sessions_dir",
            lambda cwd: Path(tmpdir),
        )
        yield tmpdir

@pytest.mark.asyncio
async def test_record_and_load_messages(temp_session_dir):
    """测试消息记录和加载"""
    session_id = str(uuid4())
    cwd = "/test/path"

    storage = SessionStorage(session_id, cwd)

    # 记录消息
    message1 = {
        "type": "user",
        "role": "user",
        "content": "Hello",
        "uuid": str(uuid4()),
    }
    message2 = {
        "type": "assistant",
        "role": "assistant",
        "content": "Hi there!",
        "uuid": str(uuid4()),
    }

    await storage.record_message(message1)
    await storage.record_message(message2)

    # 加载消息
    loaded = storage.load_messages()

    assert len(loaded) == 2
    assert loaded[0]["content"] == "Hello"
    assert loaded[1]["content"] == "Hi there!"

@pytest.mark.asyncio
async def test_session_metadata(temp_session_dir):
    """测试会话元数据保存"""
    session_id = str(uuid4())
    cwd = "/test/path"

    storage = SessionStorage(session_id, cwd)

    # 保存标题
    storage.save_title("Test Session", source="user")

    # 保存标签
    storage.save_tag("important")

    # 保存摘要
    storage.save_summary("This is a test session")

    # 验证文件内容
    with open(storage.session_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    assert len(lines) == 3

    # 解析并验证
    title_entry = json.loads(lines[0])
    assert title_entry["type"] == "custom-title"
    assert title_entry["title"] == "Test Session"
    assert title_entry["source"] == "user"

    tag_entry = json.loads(lines[1])
    assert tag_entry["type"] == "tag"
    assert tag_entry["tag"] == "important"

    summary_entry = json.loads(lines[2])
    assert summary_entry["type"] == "summary"
    assert summary_entry["summary"] == "This is a test session"

@pytest.mark.asyncio
async def test_session_info(temp_session_dir):
    """测试会话信息获取"""
    session_id = str(uuid4())
    cwd = "/test/path"

    storage = SessionStorage(session_id, cwd)

    # 记录一些消息
    for i in range(5):
        await storage.record_message({
            "type": "user",
            "content": f"Message {i}",
            "uuid": str(uuid4()),
        })

    # 获取会话信息
    info = storage.get_session_info()

    assert info["session_id"] == session_id
    assert info["exists"] is True
    assert info["message_count"] == 5
    assert info["file_size"] > 0

def test_list_sessions(temp_session_dir, monkeypatch):
    """测试会话列表"""
    # Mock get_sessions_dir 返回临时目录
    from codo.session import storage as session_module
    monkeypatch.setattr(session_module, "get_sessions_dir", lambda cwd: Path(temp_session_dir))

    # 创建多个会话
    session_ids = [str(uuid4()) for _ in range(3)]

    for sid in session_ids:
        storage = SessionStorage(sid, "/test/path")
        # 需要 mock storage 的 session_file 路径
        storage.session_file = Path(temp_session_dir) / f"{sid}.jsonl"
        # 创建空文件
        storage.session_file.touch()

    # 列出会话
    sessions = SessionManager.list_sessions()

    assert len(sessions) == 3
    assert all(s["exists"] for s in sessions)

def test_delete_session(temp_session_dir):
    """测试会话删除"""
    session_id = str(uuid4())
    cwd = "/test/path"

    storage = SessionStorage(session_id, cwd)

    # 创建会话文件
    storage.materialize_session_file()
    assert storage.session_file is not None
    storage.session_file.touch()
    assert storage.session_file.exists()

    # 删除会话
    storage.delete_session()
    assert not storage.session_file.exists()

@pytest.mark.asyncio
async def test_ai_title_vs_user_title(temp_session_dir):
    """测试 AI 标题和用户标题的区分"""
    session_id = str(uuid4())
    cwd = "/test/path"

    storage = SessionStorage(session_id, cwd)

    # 保存 AI 标题
    storage.save_title("AI Generated Title", source="ai")

    # 保存用户标题
    storage.save_title("User Custom Title", source="user")

    # 验证文件内容
    with open(storage.session_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    assert len(lines) == 2

    ai_entry = json.loads(lines[0])
    assert ai_entry["type"] == "ai-title"
    assert ai_entry["title"] == "AI Generated Title"

    user_entry = json.loads(lines[1])
    assert user_entry["type"] == "custom-title"
    assert user_entry["title"] == "User Custom Title"

@pytest.mark.asyncio
async def test_message_chain_with_parent_uuid(temp_session_dir):
    """测试消息链（parent_uuid）"""
    session_id = str(uuid4())
    cwd = "/test/path"

    storage = SessionStorage(session_id, cwd)

    # 记录父消息
    parent_uuid = str(uuid4())
    parent_message = {
        "type": "user",
        "content": "Parent message",
        "uuid": parent_uuid,
    }
    await storage.record_message(parent_message)

    # 记录子消息
    child_message = {
        "type": "assistant",
        "content": "Child message",
        "uuid": str(uuid4()),
    }
    await storage.record_message(child_message, parent_uuid=parent_uuid)

    # 验证文件内容
    with open(storage.session_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    assert len(lines) == 2

    child_entry = json.loads(lines[1])
    assert child_entry["parent_uuid"] == parent_uuid
