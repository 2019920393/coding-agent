"""
测试会话恢复功能

验证 SessionStorage.load_messages() 和 QueryEngine.restore_session() 的正确性。
"""

import pytest
import tempfile
import shutil
from pathlib import Path
from uuid import uuid4

from codo.session.storage import SessionStorage, get_session_file_path
from codo.session.types import TranscriptMessage

class TestSessionRestore:
    """测试会话恢复功能"""

    def setup_method(self):
        """每个测试前创建临时目录"""
        self.temp_dir = tempfile.mkdtemp()
        self.session_id = str(uuid4())

    def teardown_method(self):
        """每个测试后清理临时目录"""
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_load_messages_empty_session(self):
        """测试加载空会话"""
        storage = SessionStorage(self.session_id, self.temp_dir)
        messages = storage.load_messages()

        assert messages == []

    def test_load_messages_with_history(self):
        """测试加载有历史的会话"""
        # 创建会话并写入消息
        storage = SessionStorage(self.session_id, self.temp_dir)

        # 写入用户消息
        user_msg = {
            "role": "user",
            "type": "user",
            "uuid": str(uuid4()),
            "parent_uuid": None,
            "content": "Hello",
            "timestamp": "2024-01-01T00:00:00",
        }

        # 写入助手消息
        assistant_msg = {
            "role": "assistant",
            "type": "assistant",
            "uuid": str(uuid4()),
            "parent_uuid": user_msg["uuid"],
            "content": [{"type": "text", "text": "Hi there!"}],
            "timestamp": "2024-01-01T00:00:01",
        }

        storage.record_messages([user_msg, assistant_msg])

        # 创建新的 storage 实例并加载
        storage2 = SessionStorage(self.session_id, self.temp_dir)
        loaded_messages = storage2.load_messages()

        assert len(loaded_messages) == 2
        assert loaded_messages[0]["role"] == "user"
        assert loaded_messages[0]["content"] == "Hello"
        assert loaded_messages[1]["role"] == "assistant"
        assert loaded_messages[1]["content"][0]["text"] == "Hi there!"

    def test_load_messages_with_branching(self):
        """测试加载有分支的会话（选择最新叶子链路）"""
        storage = SessionStorage(self.session_id, self.temp_dir)

        # 创建分支结构：
        # root -> msg1 -> msg2a
        #              -> msg2b (更新)

        root_uuid = str(uuid4())
        msg1_uuid = str(uuid4())
        msg2a_uuid = str(uuid4())
        msg2b_uuid = str(uuid4())

        root_msg = {
            "role": "user",
            "type": "user",
            "uuid": root_uuid,
            "parent_uuid": None,
            "content": "Start",
            "timestamp": "2024-01-01T00:00:00",
        }

        msg1 = {
            "role": "assistant",
            "type": "assistant",
            "uuid": msg1_uuid,
            "parent_uuid": root_uuid,
            "content": [{"type": "text", "text": "Response 1"}],
            "timestamp": "2024-01-01T00:00:01",
        }

        # 第一条链路：root -> msg1 -> msg2a
        msg2a = {
            "role": "user",
            "type": "user",
            "uuid": msg2a_uuid,
            "parent_uuid": msg1_uuid,
            "content": "Branch A",
            "timestamp": "2024-01-01T00:00:02",
        }

        # 第二条链路：root -> msg1 -> msg2b（从 msg1 分支出来）
        msg2b = {
            "role": "user",
            "type": "user",
            "uuid": msg2b_uuid,
            "parent_uuid": msg1_uuid,
            "content": "Branch B",
            "timestamp": "2024-01-01T00:00:03",  # 更新的时间戳
        }

        # 分别记录两条链路（模拟实际使用场景）
        storage.record_messages([root_msg, msg1, msg2a])
        # 从 msg1 分支出新消息，需要指定 parent_uuid
        storage.record_messages([msg2b], parent_uuid=msg1_uuid)

        # 加载会话（应该选择最新的分支 B）
        storage2 = SessionStorage(self.session_id, self.temp_dir)
        loaded_messages = storage2.load_messages()

        # 应该加载：root -> msg1 -> msg2b
        assert len(loaded_messages) == 3
        assert loaded_messages[0]["uuid"] == root_uuid
        assert loaded_messages[1]["uuid"] == msg1_uuid
        assert loaded_messages[2]["uuid"] == msg2b_uuid
        assert loaded_messages[2]["content"] == "Branch B"

    def test_load_messages_with_metadata(self):
        """测试加载会话时恢复元数据"""
        storage = SessionStorage(self.session_id, self.temp_dir)

        # 设置元数据
        storage.save_custom_title("Test Session")
        storage.save_tag("test-tag")
        storage.save_agent_name("TestAgent")
        storage.save_mode("coordinator")

        # 写入消息
        msg = {
            "role": "user",
            "type": "user",
            "uuid": str(uuid4()),
            "parent_uuid": None,
            "content": "Hello",
            "timestamp": "2024-01-01T00:00:00",
        }
        storage.record_messages([msg])

        # 创建新实例并加载
        storage2 = SessionStorage(self.session_id, self.temp_dir)
        loaded_messages = storage2.load_messages()

        # 验证元数据被恢复
        assert storage2.current_title == "Test Session"
        assert storage2.current_tag == "test-tag"
        assert storage2.current_agent_name == "TestAgent"
        assert storage2.current_mode == "coordinator"

    def test_recorded_message_uuids_restored(self):
        """测试加载后 recorded_message_uuids 被正确恢复"""
        storage = SessionStorage(self.session_id, self.temp_dir)

        msg1_uuid = str(uuid4())
        msg2_uuid = str(uuid4())

        msg1 = {
            "role": "user",
            "type": "user",
            "uuid": msg1_uuid,
            "parent_uuid": None,
            "content": "Message 1",
            "timestamp": "2024-01-01T00:00:00",
        }

        msg2 = {
            "role": "assistant",
            "type": "assistant",
            "uuid": msg2_uuid,
            "parent_uuid": msg1_uuid,
            "content": [{"type": "text", "text": "Response"}],
            "timestamp": "2024-01-01T00:00:01",
        }

        storage.record_messages([msg1, msg2])

        # 加载会话
        storage2 = SessionStorage(self.session_id, self.temp_dir)
        loaded_messages = storage2.load_messages()

        # 验证 UUID 集合被恢复
        assert msg1_uuid in storage2.recorded_message_uuids
        assert msg2_uuid in storage2.recorded_message_uuids
        assert len(storage2.recorded_message_uuids) == 2

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
