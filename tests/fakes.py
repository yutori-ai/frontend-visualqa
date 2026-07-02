"""Shared test fakes for Navigator client protocol objects."""

from __future__ import annotations

import importlib
import inspect
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import ModuleType
from typing import Any

import pytest

from frontend_visualqa.artifacts import RunArtifacts


def is_bootstrap_step_artifact(path: str | None) -> bool:
    return bool(path) and Path(path).name.startswith("step-00")


class _SilentStaticHandler(SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler that suppresses per-request access logging."""

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return


def serve_static_directory(directory: Path) -> Iterator[str]:
    """Start a background ThreadingHTTPServer over *directory*, yielding its base URL.

    Several test modules each defined their own identical static-file-server fixture
    (an `_SilentStaticHandler` plus start/yield/shutdown boilerplate) to serve
    `examples/` for live browser tests. This is the shared implementation they wrap.
    """
    handler = partial(_SilentStaticHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def import_or_skip(module_path: str) -> ModuleType:
    """Import *module_path*, skipping the test if it is not implemented yet.

    Several test modules were written before their corresponding
    ``frontend_visualqa`` module existed, so each defined its own
    ``_import_X_module`` helper with this same import-or-skip guard. This is
    the shared version they all delegate to now.
    """
    try:
        return importlib.import_module(module_path)
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("frontend_visualqa"):
            pytest.skip(f"{module_path} is not implemented yet")
        raise


def assert_claim_result_payload_shape(result: dict[str, Any]) -> None:
    """Assert a serialized ``ClaimResult`` dict has the expected top-level and nested keys.

    ``test_cli.py``, ``test_mcp_server.py``, and ``test_reporters.py`` each defined an
    identical (or near-identical) ``_assert_claim_result_payload_shape`` helper to check the
    same claim/status/finding/proof/page/trace contract. This is the shared version they all
    delegate to now.
    """
    assert set(result) == {"claim", "status", "finding", "proof", "page", "trace"}

    proof = result["proof"]
    assert proof is not None
    assert set(proof) == {"screenshot_path", "step", "after_action", "text", "text_path"}

    page = result["page"]
    assert isinstance(page, dict)
    assert set(page) == {"url", "viewport"}
    viewport = page["viewport"]
    assert isinstance(viewport, dict)
    assert set(viewport) == {"width", "height", "device_scale_factor"}

    trace = result["trace"]
    assert isinstance(trace, dict)
    assert set(trace) == {"steps_taken", "wrong_page_recovered", "screenshot_paths", "actions", "trace_path"}


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
    choices: list[FakeChoice] = field(default_factory=list)
    parsed_json: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.choices or self.parsed_json is None:
            return
        self.choices = [FakeChoice(message=FakeMessage(content=json.dumps(self.parsed_json)))]


class FakeNavigatorClient:
    def __init__(self, responses: list[FakeMessage | FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def create(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        json_schema: dict[str, Any] | None = None,
        already_trimmed: bool = False,
    ) -> FakeResponse:
        del already_trimmed  # informational; we don't trim in fake
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

    @staticmethod
    def _claim_dir(run: RunArtifacts, claim_index: int) -> Path:
        claim_dir = run.run_dir / f"claim-{claim_index:02d}"
        claim_dir.mkdir(parents=True, exist_ok=True)
        return claim_dir

    def save_screenshot(self, run: RunArtifacts, claim_index: int, label: str, image_bytes: bytes) -> str:
        path = self._claim_dir(run, claim_index) / f"{label}.webp"
        path.write_bytes(image_bytes)
        return str(path)

    def save_rich_trace(self, run: RunArtifacts, claim_index: int, events: list[dict[str, Any]]) -> str:
        path = self._claim_dir(run, claim_index) / "trace.json"
        path.write_text(json.dumps(events))
        return str(path)

    def save_proof_text(self, run: RunArtifacts, claim_index: int, label: str, text: str) -> str:
        path = self._claim_dir(run, claim_index) / f"{label}.txt"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def save_json(self, run: RunArtifacts, relative_path: str, payload: dict[str, Any]) -> str:
        path = run.run_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))
        return str(path)
