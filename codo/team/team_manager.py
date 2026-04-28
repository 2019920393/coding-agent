"""Team manager for coordinating multiple agents."""

import time
import uuid
from typing import Optional
from .message_types import Message, MessageType
from .mailbox import Mailbox

class TeamManager:
    """Manages a team of collaborating agents."""

    def __init__(self):
        """Initialize the team manager."""
        self.mailbox = Mailbox()
        self._agents: dict[str, dict] = {}
        self._leader_id: Optional[str] = None

    def register_agent(
        self,
        agent_id: str,
        role: str,
        capabilities: Optional[list[str]] = None
    ) -> None:
        """
        Register an agent with the team.

        Args:
            agent_id: Unique agent identifier
            role: Agent role (e.g., "leader", "worker")
            capabilities: List of agent capabilities
        """
        self._agents[agent_id] = {
            "id": agent_id,
            "role": role,
            "capabilities": capabilities or [],
            "status": "idle",
        }

        if role == "leader":
            self._leader_id = agent_id

    def unregister_agent(self, agent_id: str) -> None:
        """
        Unregister an agent from the team.

        Args:
            agent_id: Agent identifier to unregister
        """
        if agent_id in self._agents:
            del self._agents[agent_id]

        if self._leader_id == agent_id:
            self._leader_id = None

    def get_agent(self, agent_id: str) -> Optional[dict]:
        """
        Get agent information.

        Args:
            agent_id: Agent identifier

        Returns:
            Agent info dict or None if not found
        """
        return self._agents.get(agent_id)

    def get_all_agents(self) -> list[dict]:
        """
        Get all registered agents.

        Returns:
            List of agent info dicts
        """
        return list(self._agents.values())

    def get_leader_id(self) -> Optional[str]:
        """
        Get the leader agent ID.

        Returns:
            Leader agent ID or None
        """
        return self._leader_id

    async def send_message(
        self,
        from_agent: str,
        to_agent: str,
        message_type: MessageType,
        content: str,
        metadata: Optional[dict] = None,
        parent_id: Optional[str] = None
    ) -> Message:
        """
        Send a message between agents.

        Args:
            from_agent: Sender agent ID
            to_agent: Recipient agent ID
            message_type: Type of message
            content: Message content
            metadata: Optional metadata
            parent_id: Optional parent message ID

        Returns:
            The created message
        """
        message = Message(
            id=str(uuid.uuid4()),
            type=message_type,
            from_agent=from_agent,
            to_agent=to_agent,
            content=content,
            metadata=metadata or {},
            timestamp=time.time(),
            parent_id=parent_id,
        )

        await self.mailbox.send(message)
        return message

    async def receive_message(
        self,
        agent_id: str,
        timeout: Optional[float] = None
    ) -> Optional[Message]:
        """
        Receive a message for an agent.

        Args:
            agent_id: Agent ID to receive for
            timeout: Optional timeout in seconds

        Returns:
            Next message or None
        """
        return await self.mailbox.receive(agent_id, timeout)

    async def receive_all_messages(self, agent_id: str) -> list[Message]:
        """
        Receive all pending messages for an agent.

        Args:
            agent_id: Agent ID to receive for

        Returns:
            List of all pending messages
        """
        return await self.mailbox.receive_all(agent_id)

    def update_agent_status(self, agent_id: str, status: str) -> None:
        """
        Update an agent's status.

        Args:
            agent_id: Agent identifier
            status: New status (e.g., "idle", "busy", "error")
        """
        if agent_id in self._agents:
            self._agents[agent_id]["status"] = status

_global_team_manager: Optional[TeamManager] = None

def get_team_manager() -> TeamManager:
    global _global_team_manager
    if _global_team_manager is None:
        _global_team_manager = TeamManager()
    return _global_team_manager
