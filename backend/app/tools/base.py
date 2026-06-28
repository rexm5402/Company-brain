"""Tool contract shared by every agent tool.

Each tool carries an explicit JSON schema (per CLAUDE.md: prefer explicit tool
schemas over loosely-typed dicts) and a single `run` entrypoint.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.audit.recorder import ToolResult


class Tool(ABC):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema for the arguments object

    @abstractmethod
    def run(self, **kwargs: Any) -> ToolResult:
        ...

    def schema(self) -> dict[str, Any]:
        """Provider-neutral schema; LLMClient adapts it per provider."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }
