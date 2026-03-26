"""Shared test fakes for n1 client protocol objects."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


def is_bootstrap_step_artifact(path: str | None) -> bool:
    return bool(path) and Path(path).name.startswith("step-00")


@dataclass
class FakeFunction:
    name: str
    arguments: str


@dataclass
class FakeToolCall:
    id: str
    function: FakeFunction


@dataclass
class FakeMessage:
    role: str = "assistant"
    tool_calls: list[FakeToolCall] | None = None
    content: str | None = None

    def model_dump(self, exclude_none: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls is not None:
            payload["tool_calls"] = [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                    },
                }
                for tool_call in self.tool_calls
            ]
        if exclude_none:
            return {key: value for key, value in payload.items() if value is not None}
        return payload


class FakeN1Client:
    def __init__(self, responses: list[FakeMessage]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def create(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> FakeMessage:
        self.calls.append({"messages": messages, "tools": tools or []})
        return self.responses.pop(0)

    def trim_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return messages

    async def close(self) -> None:
        return None
