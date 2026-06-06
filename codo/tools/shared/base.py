"""
Base Tool class
"""

from abc import ABC, abstractmethod
from typing import Any


class Tool(ABC):
    """Base class for all tools"""

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Tool description"""
        pass

    @abstractmethod
    def get_input_schema(self) -> dict[str, Any]:
        """Get JSON schema for tool input"""
        pass

    @abstractmethod
    async def execute(self, input_data: dict[str, Any], **kwargs) -> str:
        """
        Execute the tool with given input.
        Returns result as string.
        """
        pass

    def to_schema(self) -> dict[str, Any]:
        """Convert to API tool schema"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.get_input_schema(),
        }
