#!/usr/bin/env python3
"""
测试会话元数据功能

验证：
1. 元数据记录功能
2. 元数据提取功能
"""

import asyncio
import json
import tempfile
from pathlib import Path
import sys
from unittest.mock import patch

# 添加 Codo_new 到路径
sys.path.insert(0, str(Path(__file__).parent / "Codo_new"))

from codo.session.storage import SessionStorage
from codo.session.restore import extract_metadata_from_transcript

def test_metadata_recording():
    """测试元数据记录功能"""
    print("测试 1: 元数据记录功能")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Mock get_sessions_dir 返回临时目录
        with patch('codo.session.storage.get_sessions_dir', return_value=tmpdir_path):
            session = SessionStorage(
                session_id="test-session",
                cwd="/test/path"
            )

        # 记录各种元数据
        session.save_title("Test Session Title", source="user")
        session.save_tag("bug-fix")
        session.save_tag("feature")
        session.save_summary("Fixed authentication bug", leaf_uuid="msg-123")
        session.save_agent_name("code-reviewer")
        session.save_agent_color("#FF5733")
        session.save_agent_setting("explore")
        session.save_mode("plan")
        session.save_pr_link(
            pr_number=42,
            pr_url="https://github.com/user/repo/pull/42",
            pr_repository="user/repo"
        )
        session.save_worktree_state({"path": "/tmp/worktree", "branch": "feature-x"})
        session_file = session.session_file

        # 读取文件验证
        with open(session_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        assert len(lines) == 10, f"Expected 10 metadata entries, got {len(lines)}"

        # 验证每条记录
        records = [json.loads(line) for line in lines]

        # custom-title
        assert records[0]["type"] == "custom-title"
        assert records[0]["customTitle"] == "Test Session Title"
        assert records[0]["source"] == "user"

        # tags
        assert records[1]["type"] == "tag"
        assert records[1]["tag"] == "bug-fix"
        assert records[2]["type"] == "tag"
        assert records[2]["tag"] == "feature"

        # summary
        assert records[3]["type"] == "summary"
        assert records[3]["summary"] == "Fixed authentication bug"
        assert records[3]["leafUuid"] == "msg-123"

        # agent-name
        assert records[4]["type"] == "agent-name"
        assert records[4]["agentName"] == "code-reviewer"

        # agent-color
        assert records[5]["type"] == "agent-color"
        assert records[5]["agentColor"] == "#FF5733"

        # agent-setting
        assert records[6]["type"] == "agent-setting"
        assert records[6]["agentSetting"] == "explore"

        # mode
        assert records[7]["type"] == "mode"
        assert records[7]["mode"] == "plan"

        # pr-link
        assert records[8]["type"] == "pr-link"
        assert records[8]["prNumber"] == 42
        assert records[8]["prUrl"] == "https://github.com/user/repo/pull/42"
        assert records[8]["prRepository"] == "user/repo"

        # worktree-state
        assert records[9]["type"] == "worktree-state"
        assert records[9]["worktreeSession"]["path"] == "/tmp/worktree"
        assert records[9]["worktreeSession"]["branch"] == "feature-x"

        print("✓ 所有元数据记录正确")

def test_metadata_extraction():
    """测试元数据提取功能"""
    print("\n测试 2: 元数据提取功能")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        with patch('codo.session.storage.get_sessions_dir', return_value=tmpdir_path):
            session = SessionStorage(
                session_id="test-session",
                cwd="/test/path"
            )

        # 记录元数据
        session.save_title("Session 1", source="user")
        session.save_title("Session 1 Updated", source="ai")
        session.save_tag("tag1")
        session.save_tag("tag2")  # 会覆盖 tag1
        session.save_summary("Summary 1", leaf_uuid="uuid-1")
        session.save_summary("Summary 2", leaf_uuid="uuid-2")
        session.save_agent_name("agent-1")
        session.save_agent_color("#123456")
        session.save_agent_setting("explore")
        session.save_mode("plan")
        session.save_pr_link(100, "https://github.com/test/repo/pull/100", "test/repo")
        session.save_worktree_state({"path": "/tmp/wt", "branch": "main"})
        session_file = session.session_file

        # 读取并提取元数据
        with open(session_file, "r", encoding="utf-8") as f:
            records = [json.loads(line) for line in f]

        metadata = extract_metadata_from_transcript(records)

        # 验证提取结果
        # custom_titles 按 sessionId 索引，同一个 session 的多次 save_title 会覆盖
        # 但是 source="user" 和 source="ai" 会生成不同的 type（custom-title 和 ai-title）
        # 所以实际上只有 custom-title 类型会被提取到 custom_titles 中
        assert len(metadata["custom_titles"]) == 1
        assert metadata["custom_titles"]["test-session"] == "Session 1"

        # tags 按 sessionId 索引，同一个 session 的多次 save_tag 会覆盖
        assert len(metadata["tags"]) == 1
        assert metadata["tags"]["test-session"] == "tag2"  # 最后一个 tag

        assert len(metadata["summaries"]) == 2
        assert metadata["summaries"]["uuid-1"] == "Summary 1"
        assert metadata["summaries"]["uuid-2"] == "Summary 2"

        assert len(metadata["agent_names"]) == 1
        assert "agent-1" in metadata["agent_names"].values()

        assert len(metadata["agent_colors"]) == 1
        assert "#123456" in metadata["agent_colors"].values()

        assert len(metadata["agent_settings"]) == 1
        assert "explore" in metadata["agent_settings"].values()

        assert len(metadata["modes"]) == 1
        assert "plan" in metadata["modes"].values()

        assert len(metadata["pr_numbers"]) == 1
        assert 100 in metadata["pr_numbers"].values()

        assert len(metadata["pr_urls"]) == 1
        assert "https://github.com/test/repo/pull/100" in metadata["pr_urls"].values()

        assert len(metadata["pr_repositories"]) == 1
        assert "test/repo" in metadata["pr_repositories"].values()

        assert len(metadata["worktree_states"]) == 1
        worktree = list(metadata["worktree_states"].values())[0]
        assert worktree["path"] == "/tmp/wt"
        assert worktree["branch"] == "main"

        print("✓ 元数据提取正确")

def test_multiple_sessions():
    """测试多会话元数据隔离"""
    print("\n测试 3: 多会话元数据隔离")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        with patch('codo.session.storage.get_sessions_dir', return_value=tmpdir_path):
            # 会话 1
            session1 = SessionStorage(
                session_id="session-1",
                cwd="/test/path"
            )
            session1.save_title("Session 1 Title", source="user")
            session1.save_tag("session1-tag")
            session1_file = session1.session_file

            # 会话 2
            session2 = SessionStorage(
                session_id="session-2",
                cwd="/test/path"
            )
            session2.save_title("Session 2 Title", source="user")
            session2.save_tag("session2-tag")
            session2_file = session2.session_file

        # 验证两个会话有独立的文件
        assert session1_file != session2_file
        assert session1_file.exists()
        assert session2_file.exists()

        # 读取会话 1 并验证
        with open(session1_file, "r", encoding="utf-8") as f:
            records1 = [json.loads(line) for line in f]

        assert len(records1) == 2
        assert records1[0]["sessionId"] == "session-1"
        assert records1[1]["sessionId"] == "session-1"

        # 读取会话 2 并验证
        with open(session2_file, "r", encoding="utf-8") as f:
            records2 = [json.loads(line) for line in f]

        assert len(records2) == 2
        assert records2[0]["sessionId"] == "session-2"
        assert records2[1]["sessionId"] == "session-2"

        print("✓ 多会话元数据隔离正确")

def test_worktree_null_state():
    """测试 worktree 空状态"""
    print("\n测试 4: Worktree 空状态")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        with patch('codo.session.storage.get_sessions_dir', return_value=tmpdir_path):
            session = SessionStorage(
                session_id="test-session",
                cwd="/test/path"
            )

        # 保存空状态
        session.save_worktree_state(None)
        session_file = session.session_file

        # 读取并提取
        with open(session_file, "r", encoding="utf-8") as f:
            records = [json.loads(line) for line in f]

        metadata = extract_metadata_from_transcript(records)

        # 验证空状态
        assert len(metadata["worktree_states"]) == 1
        worktree = list(metadata["worktree_states"].values())[0]
        assert worktree is None

        print("✓ Worktree 空状态处理正确")

async def main():
    """运行所有测试"""
    print("=" * 60)
    print("会话元数据功能测试")
    print("=" * 60)

    try:
        test_metadata_recording()
        test_metadata_extraction()
        test_multiple_sessions()
        test_worktree_null_state()

        print("\n" + "=" * 60)
        print("✓ 所有测试通过！")
        print("=" * 60)

    except AssertionError as e:
        print(f"\n✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ 测试错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
