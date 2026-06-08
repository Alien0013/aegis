"""Normalized internal data types shared across the harness.

These are provider-agnostic. Each provider transport converts to/from these.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


def new_id(prefix: str = "id") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class ToolCall:
    """A model's request to invoke a tool."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json_args(cls, id: str, name: str, raw_args: str) -> "ToolCall":
        try:
            args = json.loads(raw_args) if raw_args else {}
        except json.JSONDecodeError:
            args = {"__raw__": raw_args}
        return cls(id=id, name=name, arguments=args if isinstance(args, dict) else {"value": args})

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass
class Message:
    """One turn in the conversation, normalized across providers."""

    role: str
    content: str = ""
    # assistant -> requested tool calls
    tool_calls: list[ToolCall] = field(default_factory=list)
    # role == "tool"
    tool_call_id: str | None = None
    name: str | None = None  # tool name for tool messages
    # optional reasoning / thinking text (kept out of provider wire unless supported)
    reasoning: str = ""
    # optional image references (file paths or data URLs)
    images: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.name:
            d["name"] = self.name
        if self.reasoning:
            d["reasoning"] = self.reasoning
        if self.images:
            d["images"] = self.images
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Message":
        return cls(
            role=d["role"],
            content=d.get("content", "") or "",
            tool_calls=[
                ToolCall(id=tc["id"], name=tc["name"], arguments=tc.get("arguments", {}))
                for tc in d.get("tool_calls", [])
            ],
            tool_call_id=d.get("tool_call_id"),
            name=d.get("name"),
            reasoning=d.get("reasoning", "") or "",
            images=d.get("images", []) or [],
        )

    @staticmethod
    def user(text: str, images: list[str] | None = None) -> "Message":
        return Message(role="user", content=text, images=images or [])

    @staticmethod
    def system(text: str) -> "Message":
        return Message(role="system", content=text)

    @staticmethod
    def assistant(text: str = "", tool_calls: list[ToolCall] | None = None) -> "Message":
        return Message(role="assistant", content=text, tool_calls=tool_calls or [])

    @staticmethod
    def tool(tool_call_id: str, name: str, content: str) -> "Message":
        return Message(role="tool", content=content, tool_call_id=tool_call_id, name=name)


# A JSON-schema tool definition: {"name", "description", "parameters"}
ToolSchema = dict[str, Any]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0     # tokens served from the prompt cache (cheap)
    cache_write: int = 0    # tokens written to the prompt cache

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read += other.cache_read
        self.cache_write += other.cache_write


@dataclass
class LLMResponse:
    """Normalized model reply."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    reasoning: str = ""
    usage: Usage = field(default_factory=Usage)
    raw: Any = None

    def to_message(self) -> Message:
        return Message(
            role="assistant",
            content=self.text,
            tool_calls=list(self.tool_calls),
            reasoning=self.reasoning,
        )
