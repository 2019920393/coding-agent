"""Mailbox system for agent message delivery."""

import asyncio
from collections import defaultdict
from typing import Awaitable, Callable, Optional
from .message_types import Message

class Mailbox:
    """Manages message delivery between agents."""

    def __init__(self):
        """Initialize the mailbox system."""
        self._messages: dict[str, list[Message]] = defaultdict(list)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._listeners: list[Callable[[Message], Awaitable[None]]] = []

    def register_listener(self, listener: Callable[[Message], Awaitable[None]]) -> None:
        self._listeners.append(listener)

    def unregister_listener(self, listener: Callable[[Message], Awaitable[None]]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    async def send(self, message: Message) -> None:
        """
        Send a message to an agent's mailbox.

        Args:
            message: The message to send
        """
        async with self._locks[message.to_agent]:
            self._messages[message.to_agent].append(message)
        for listener in list(self._listeners):
            await listener(message)

    async def receive(self, agent_id: str, timeout: Optional[float] = None) -> Optional[Message]:
        """
        Receive the next message for an agent.

        Args:
            agent_id: The agent ID to receive messages for
            timeout: Optional timeout in seconds

        Returns:
            The next message, or None if timeout expires
        """
        async with self._locks[agent_id]:
            if self._messages[agent_id]:
                return self._messages[agent_id].pop(0)

        if timeout:
            await asyncio.sleep(timeout)

        return None

    async def receive_all(self, agent_id: str) -> list[Message]:
        """
        Receive all pending messages for an agent.

        Args:
            agent_id: The agent ID to receive messages for

        Returns:
            List of all pending messages
        """
        async with self._locks[agent_id]:
            messages = self._messages[agent_id].copy()
            self._messages[agent_id].clear()
            return messages

    def has_messages(self, agent_id: str) -> bool:
        """
        Check if an agent has pending messages.

        Args:
            agent_id: The agent ID to check

        Returns:
            True if there are pending messages
        """
        return bool(self._messages.get(agent_id))

    def get_message_count(self, agent_id: str) -> int:
        """
        Get the number of pending messages for an agent.

        Args:
            agent_id: The agent ID to check

        Returns:
            Number of pending messages
        """
        return len(self._messages.get(agent_id, []))
