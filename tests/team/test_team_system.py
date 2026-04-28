"""Tests for team collaboration system."""

import pytest
import asyncio

from codo.team import Message, MessageType, Mailbox, TeamManager

class TestMessageTypes:
    """Test message type definitions."""

    def test_message_creation(self):
        """Test creating a message."""
        message = Message(
            id="msg1",
            type=MessageType.TASK_ASSIGNMENT,
            from_agent="leader",
            to_agent="worker1",
            content="Do task X",
            timestamp=1234567890.0,
        )

        assert message.id == "msg1"
        assert message.type == MessageType.TASK_ASSIGNMENT
        assert message.from_agent == "leader"
        assert message.to_agent == "worker1"
        assert message.content == "Do task X"

    def test_message_with_metadata(self):
        """Test message with metadata."""
        message = Message(
            id="msg2",
            type=MessageType.QUESTION,
            from_agent="worker1",
            to_agent="leader",
            content="How should I proceed?",
            metadata={"priority": "high", "task_id": "task123"},
            timestamp=1234567890.0,
        )

        assert message.metadata["priority"] == "high"
        assert message.metadata["task_id"] == "task123"

class TestMailbox:
    """Test mailbox message delivery."""

    @pytest.mark.asyncio
    async def test_send_and_receive(self):
        """Test sending and receiving messages."""
        mailbox = Mailbox()

        message = Message(
            id="msg1",
            type=MessageType.TASK_ASSIGNMENT,
            from_agent="leader",
            to_agent="worker1",
            content="Task",
            timestamp=1234567890.0,
        )

        await mailbox.send(message)
        received = await mailbox.receive("worker1")

        assert received is not None
        assert received.id == "msg1"
        assert received.content == "Task"

    @pytest.mark.asyncio
    async def test_receive_empty(self):
        """Test receiving from empty mailbox."""
        mailbox = Mailbox()
        received = await mailbox.receive("worker1", timeout=0.1)

        assert received is None

    @pytest.mark.asyncio
    async def test_receive_all(self):
        """Test receiving all messages."""
        mailbox = Mailbox()

        for i in range(3):
            message = Message(
                id=f"msg{i}",
                type=MessageType.TASK_ASSIGNMENT,
                from_agent="leader",
                to_agent="worker1",
                content=f"Task {i}",
                timestamp=1234567890.0 + i,
            )
            await mailbox.send(message)

        messages = await mailbox.receive_all("worker1")
        assert len(messages) == 3
        assert messages[0].id == "msg0"
        assert messages[2].id == "msg2"

    def test_has_messages(self):
        """Test checking for pending messages."""
        mailbox = Mailbox()
        assert not mailbox.has_messages("worker1")

    def test_get_message_count(self):
        """Test getting message count."""
        mailbox = Mailbox()
        assert mailbox.get_message_count("worker1") == 0

class TestTeamManager:
    """Test team manager."""

    def test_register_agent(self):
        """Test registering an agent."""
        manager = TeamManager()
        manager.register_agent("worker1", "worker", ["coding", "testing"])

        agent = manager.get_agent("worker1")
        assert agent is not None
        assert agent["id"] == "worker1"
        assert agent["role"] == "worker"
        assert "coding" in agent["capabilities"]

    def test_register_leader(self):
        """Test registering a leader."""
        manager = TeamManager()
        manager.register_agent("leader1", "leader")

        assert manager.get_leader_id() == "leader1"

    def test_unregister_agent(self):
        """Test unregistering an agent."""
        manager = TeamManager()
        manager.register_agent("worker1", "worker")
        manager.unregister_agent("worker1")

        agent = manager.get_agent("worker1")
        assert agent is None

    def test_get_all_agents(self):
        """Test getting all agents."""
        manager = TeamManager()
        manager.register_agent("leader1", "leader")
        manager.register_agent("worker1", "worker")
        manager.register_agent("worker2", "worker")

        agents = manager.get_all_agents()
        assert len(agents) == 3

    @pytest.mark.asyncio
    async def test_send_message(self):
        """Test sending a message through manager."""
        manager = TeamManager()
        manager.register_agent("leader1", "leader")
        manager.register_agent("worker1", "worker")

        message = await manager.send_message(
            from_agent="leader1",
            to_agent="worker1",
            message_type=MessageType.TASK_ASSIGNMENT,
            content="Do task X",
        )

        assert message.from_agent == "leader1"
        assert message.to_agent == "worker1"
        assert message.type == MessageType.TASK_ASSIGNMENT

    @pytest.mark.asyncio
    async def test_receive_message(self):
        """Test receiving a message through manager."""
        manager = TeamManager()
        manager.register_agent("leader1", "leader")
        manager.register_agent("worker1", "worker")

        await manager.send_message(
            from_agent="leader1",
            to_agent="worker1",
            message_type=MessageType.TASK_ASSIGNMENT,
            content="Do task X",
        )

        received = await manager.receive_message("worker1")
        assert received is not None
        assert received.content == "Do task X"

    def test_update_agent_status(self):
        """Test updating agent status."""
        manager = TeamManager()
        manager.register_agent("worker1", "worker")
        manager.update_agent_status("worker1", "busy")

        agent = manager.get_agent("worker1")
        assert agent["status"] == "busy"
