from __future__ import annotations

import json
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import pytest

from frontend_visualqa.artifacts import ArtifactManager
from frontend_visualqa.browser import BrowserManager
from frontend_visualqa.claim_verifier import ClaimVerifier
from frontend_visualqa.runner import VisualQARunner
from frontend_visualqa.schemas import ViewportConfig


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


class _SilentStaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return


@pytest.fixture()
def example_server() -> str:
    handler = partial(_SilentStaticHandler, directory=str(PACKAGE_ROOT / "examples"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


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


@pytest.mark.asyncio
async def test_live_runner_executes_real_browser_flow_and_passes_modal_claim(
    example_server: str,
    tmp_path: Path,
) -> None:
    browser_manager = BrowserManager(headless=True, settle_delay_seconds=0)
    n1_client = FakeN1Client(
        responses=[
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        id="tool-1",
                        function=FakeFunction(
                            name="left_click",
                            arguments=json.dumps({"coordinates": [328, 435]}),
                        ),
                    )
                ]
            ),
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        id="tool-2",
                        function=FakeFunction(
                            name="record_claim_result",
                            arguments=json.dumps({"status": "pass", "summary": "The modal title reads Edit Task."}),
                        ),
                    )
                ]
            ),
        ]
    )
    artifact_manager = ArtifactManager(tmp_path / "artifacts")
    claim_verifier = ClaimVerifier(
        browser_manager=browser_manager,
        artifact_manager=artifact_manager,
        n1_client=n1_client,
    )
    runner = VisualQARunner(
        browser_manager=browser_manager,
        artifact_manager=artifact_manager,
        n1_client=n1_client,
        claim_verifier=claim_verifier,
    )

    try:
        result = await runner.run(
            url=f"{example_server}/test_page.html",
            claims=["The modal title reads 'Edit Task'"],
            viewport=ViewportConfig(width=1280, height=800, device_scale_factor=1),
            navigation_hint="Click the first task row to open the task modal before judging the claim.",
        )
    finally:
        await runner.close()

    assert result.overall_status == "completed"
    assert [item.status for item in result.results] == ["pass"]
    assert result.results[0].action_trace == ["left_click([419, 348])"]
    assert all(Path(path).exists() for path in result.results[0].screenshots)


@pytest.mark.asyncio
async def test_live_runner_downgrades_false_positive_button_claim_with_grounding(
    example_server: str,
    tmp_path: Path,
) -> None:
    browser_manager = BrowserManager(headless=True, settle_delay_seconds=0)
    n1_client = FakeN1Client(
        responses=[
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        id="tool-1",
                        function=FakeFunction(
                            name="record_claim_result",
                            arguments=json.dumps(
                                {
                                    "status": "pass",
                                    "summary": "The Show Save Confirmation button is visible without scrolling.",
                                }
                            ),
                        ),
                    )
                ]
            )
        ]
    )
    artifact_manager = ArtifactManager(tmp_path / "artifacts")
    claim_verifier = ClaimVerifier(
        browser_manager=browser_manager,
        artifact_manager=artifact_manager,
        n1_client=n1_client,
    )
    runner = VisualQARunner(
        browser_manager=browser_manager,
        artifact_manager=artifact_manager,
        n1_client=n1_client,
        claim_verifier=claim_verifier,
    )

    try:
        result = await runner.run(
            url=f"{example_server}/test_page.html",
            claims=["The Save button is visible without scrolling"],
            viewport=ViewportConfig(width=1280, height=800, device_scale_factor=1),
        )
    finally:
        await runner.close()

    assert result.overall_status == "completed"
    assert [item.status for item in result.results] == ["fail"]
    assert "No visible button label matched" in result.results[0].summary
