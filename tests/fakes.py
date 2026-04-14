"""Shared test fakes for Navigator client protocol objects."""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from frontend_visualqa.artifacts import RunArtifacts


def is_bootstrap_step_artifact(path: str | None) -> bool:
    return bool(path) and Path(path).name.startswith("step-00")


def instantiate_with_supported_kwargs(factory: Any, **candidates: Any) -> Any:
    signature = inspect.signature(factory)
    kwargs = {
        name: value
        for name, value in candidates.items()
        if name in signature.parameters
        and signature.parameters[name].kind in {inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
    }
    return factory(**kwargs)


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


@dataclass
class FakeChoice:
    message: FakeMessage


@dataclass
class FakeResponse:
    choices: list[FakeChoice]
    parsed_json: dict[str, Any] | None = None


class FakeNavigatorClient:
    def __init__(self, responses: list[FakeMessage | FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def create(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> FakeResponse:
        self.calls.append({"messages": messages, "tools": tools or []})
        raw = self.responses.pop(0)
        if isinstance(raw, FakeResponse):
            return raw
        return FakeResponse(choices=[FakeChoice(message=raw)])

    def trim_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return messages

    async def close(self) -> None:
        return None


class FakeArtifactManager:
    def __init__(self, base_dir: Path, run_id: str = "run-test") -> None:
        self.base_dir = base_dir
        self.run = RunArtifacts(run_id=run_id, run_dir=base_dir / run_id)
        self.run.run_dir.mkdir(parents=True, exist_ok=True)

    def create_run(self, prefix: str = "run", run_id: str | None = None) -> RunArtifacts:
        del prefix, run_id
        return self.run

    def save_screenshot(self, run: RunArtifacts, claim_index: int, label: str, image_bytes: bytes) -> str:
        claim_dir = run.run_dir / f"claim-{claim_index:02d}"
        claim_dir.mkdir(parents=True, exist_ok=True)
        path = claim_dir / f"{label}.webp"
        path.write_bytes(image_bytes)
        return str(path)

    def save_rich_trace(self, run: RunArtifacts, claim_index: int, events: list[dict[str, Any]]) -> str:
        claim_dir = run.run_dir / f"claim-{claim_index:02d}"
        claim_dir.mkdir(parents=True, exist_ok=True)
        path = claim_dir / "trace.json"
        path.write_text(json.dumps(events))
        return str(path)

    def save_proof_text(self, run: RunArtifacts, claim_index: int, label: str, text: str) -> str:
        claim_dir = run.run_dir / f"claim-{claim_index:02d}"
        claim_dir.mkdir(parents=True, exist_ok=True)
        path = claim_dir / f"{label}.txt"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def save_json(self, run: RunArtifacts, relative_path: str, payload: dict[str, Any]) -> str:
        path = run.run_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))
        return str(path)
