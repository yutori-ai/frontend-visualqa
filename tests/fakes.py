"""Shared test fakes for Navigator client protocol objects."""

from __future__ import annotations

import importlib
import inspect
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import partial
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import ModuleType
from typing import Any

import pytest

from frontend_visualqa.artifacts import RunArtifacts
from frontend_visualqa.schemas import ClaimResult, ViewportConfig


def is_bootstrap_step_artifact(path: str | None) -> bool:
    return bool(path) and Path(path).name.startswith("step-00")


class _SilentStaticHandler(SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler that suppresses per-request access logging."""

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return


def serve_http(handler: type[BaseHTTPRequestHandler] | partial) -> Iterator[str]:
    """Start a background ThreadingHTTPServer with *handler*, yielding its base URL.

    Several test modules each defined their own identical start/yield/shutdown/join/close
    boilerplate for a one-off `ThreadingHTTPServer`, differing only in the request handler
    (a static-file handler, a cookie-setting handler, etc). This is the shared lifecycle they
    all delegate to now.
    """
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def serve_static_directory(directory: Path) -> Iterator[str]:
    """Start a background ThreadingHTTPServer over *directory*, yielding its base URL.

    Several test modules each defined their own identical static-file-server fixture
    (an `_SilentStaticHandler` plus start/yield/shutdown boilerplate) to serve
    `examples/` for live browser tests. This is the shared implementation they wrap.
    """
    handler = partial(_SilentStaticHandler, directory=str(directory))
    yield from serve_http(handler)


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


_EMPTY_CLAIM_TRACE: dict[str, Any] = {
    "steps_taken": 0,
    "wrong_page_recovered": False,
    "screenshot_paths": [],
    "actions": [],
    "trace_path": None,
}


def make_claim_result(
    *,
    claim: str,
    status: str,
    finding: str,
    url: str,
    viewport: ViewportConfig,
    proof: dict[str, Any] | None = None,
    trace: dict[str, Any] | None = None,
) -> ClaimResult:
    """Build a ``ClaimResult``, defaulting to the no-proof/empty-trace shape most fixtures use.

    ``test_reporters.py`` independently hand-built this same no-proof/empty-trace ``ClaimResult``
    skeleton (or a full-proof variant with an explicit ``proof``/``trace``) at every call site,
    differing only in claim/status/finding/url. This is the shared constructor they delegate to
    now — the write-side mirror of `assert_claim_result_payload_shape`, which already consolidated
    the read/assert side.
    """
    return ClaimResult(
        claim=claim,
        status=status,
        finding=finding,
        proof=proof,
        page={"url": url, "viewport": viewport},
        trace=trace if trace is not None else dict(_EMPTY_CLAIM_TRACE),
    )


def instantiate_with_supported_kwargs(factory: Any, **candidates: Any) -> Any:
    signature = inspect.signature(factory)
    kwargs = {
        name: value
        for name, value in candidates.items()
        if name in signature.parameters
        and signature.parameters[name].kind in {inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
    }
    return factory(**kwargs)


def instantiate_with_aliased_attrs(factory: Any, aliases: dict[str, Any], **extra_candidates: Any) -> Any:
    """Instantiate *factory* via `instantiate_with_supported_kwargs`, then force-set every entry
    of *aliases* as an attribute on the built instance.

    `test_runner.py`'s ``_build_runner`` and `test_claim_verifier.py`'s ``_build_claim_verifier``
    each construct a not-yet-settled class using several aliased constructor-parameter names for
    the same fake dependency (e.g. both ``browser`` and ``browser_manager``), then force-set every
    alias as an attribute afterward so tests work regardless of which name the real constructor
    ultimately accepts. This is the shared version they delegate to.
    """
    instance = instantiate_with_supported_kwargs(factory, **aliases, **extra_candidates)
    for name, value in aliases.items():
        setattr(instance, name, value)
    return instance


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


def tool_call_message(name: str, arguments: str, *, call_id: str = "tool-1") -> FakeMessage:
    """Build a FakeMessage wrapping a single tool call, the shape most navigator-response fixtures need.

    `test_claim_verifier.py` and `test_live_runner.py` each hand-built this identical
    ``FakeMessage(tool_calls=[FakeToolCall(id=..., function=FakeFunction(name=..., arguments=...))])``
    skeleton at dozens of call sites, differing only in the tool name/arguments/id. This is the
    shared constructor they delegate to now.
    """
    return FakeMessage(tool_calls=[FakeToolCall(id=call_id, function=FakeFunction(name=name, arguments=arguments))])


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


class RecordingFakeOverlay:
    """Fake ``OverlayController`` that appends each lifecycle call to an external list.

    ``test_claim_verifier.py`` had five call sites that each defined an identical nested
    ``FakeOverlay`` class (differing only in the name of the closure-captured events list) to
    monkeypatch ``_create_overlay_controller``. This is the shared version they delegate to now.
    """

    def __init__(self, events: list[Any]) -> None:
        self._events = events

    async def claim_started(self) -> None:
        self._events.append("claim_started")

    async def set_status(self, label: str) -> None:
        self._events.append(("set_status", label))

    async def show_thought(self, text: str) -> None:
        self._events.append(("show_thought", text))

    async def clear_thought(self) -> None:
        self._events.append("clear_thought")

    async def before_screenshot(self) -> None:
        self._events.append("before_screenshot")

    async def after_screenshot(self) -> None:
        self._events.append("after_screenshot")

    async def claim_ended(self) -> None:
        self._events.append("claim_ended")


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
