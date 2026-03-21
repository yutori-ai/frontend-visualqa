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


async def _overlay_dom_state(page: Any) -> dict[str, Any]:
    return await page.evaluate(
        """() => {
            const read = (id) => {
                const element = document.getElementById(id);
                if (!element) {
                    return { present: false, display: null, text: null };
                }
                const style = window.getComputedStyle(element);
                return {
                    present: true,
                    display: style.display,
                    text: (element.textContent || "").trim(),
                };
            };
            return {
                persistent: read("__n1PersistentRoot"),
                transient: read("__n1TransientRoot"),
                chip: read("__n1StatusChip"),
            };
        }"""
    )


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
                            arguments=json.dumps({"status": "passed", "finding": "The modal title reads Edit Task."}),
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
    assert [item.status for item in result.results] == ["passed"]
    assert "Visible dialog title matched" in result.results[0].finding
    assert result.results[0].trace.actions == ["left_click([419, 348])"]
    assert result.results[0].trace.steps_taken == 1
    assert all(Path(path).exists() for path in result.results[0].trace.screenshot_paths)
    assert result.results[0].proof is not None
    assert result.results[0].proof.step == 1
    assert result.results[0].proof.text is None
    assert Path(result.results[0].proof.screenshot_path).exists()
    assert result.results[0].proof.after_action == "left_click([419, 348])"
    assert result.results[0].page.url == f"{example_server}/test_page.html"


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
                                    "status": "passed",
                                    "finding": "The Show Save Confirmation button is visible without scrolling.",
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
    assert [item.status for item in result.results] == ["failed"]
    assert "No visible button label matched" in result.results[0].finding


@pytest.mark.asyncio
async def test_live_runner_headed_overlay_hides_restores_and_cleans_up(
    example_server: str,
    tmp_path: Path,
) -> None:
    overlay_path = PACKAGE_ROOT / "src/frontend_visualqa/overlay.py"
    if not overlay_path.exists():
        pytest.skip("headed overlay implementation is not present in this partial worktree")
    from frontend_visualqa.overlay import OverlayController

    browser_manager = BrowserManager(headless=False, settle_delay_seconds=0)
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
                            arguments=json.dumps(
                                {"status": "passed", "finding": "The modal title reads Edit Task."}
                            ),
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

    lifecycle_samples: list[dict[str, Any]] = []
    original_claim_started = OverlayController.claim_started
    original_claim_ended = OverlayController.claim_ended
    original_before_screenshot = OverlayController.before_screenshot
    original_after_screenshot = OverlayController.after_screenshot

    async def _record_state(controller: Any) -> dict[str, Any]:
        page = getattr(controller, "_page", None)
        if page is None:
            page = getattr(controller, "page", None)
        assert page is not None, "OverlayController must expose a page reference"
        return await _overlay_dom_state(page)

    async def instrumented_claim_started(self: Any) -> None:
        error: Exception | None = None
        try:
            await original_claim_started(self)
        except Exception as exc:  # pragma: no cover - surfaced through assertions below
            error = exc
        finally:
            lifecycle_samples.append(
                {"phase": "after_claim_started", "state": await _record_state(self), "error": error}
            )

    async def instrumented_claim_ended(self: Any) -> None:
        error: Exception | None = None
        try:
            await original_claim_ended(self)
        except Exception as exc:  # pragma: no cover - surfaced through assertions below
            error = exc
        finally:
            lifecycle_samples.append(
                {"phase": "after_claim_ended", "state": await _record_state(self), "error": error}
            )

    async def instrumented_before_screenshot(self: Any) -> None:
        error: Exception | None = None
        try:
            await original_before_screenshot(self)
        except Exception as exc:  # pragma: no cover - surfaced through assertions below
            error = exc
        finally:
            lifecycle_samples.append(
                {"phase": "before_screenshot", "state": await _record_state(self), "error": error}
            )

    async def instrumented_after_screenshot(self: Any) -> None:
        error: Exception | None = None
        try:
            await original_after_screenshot(self)
        except Exception as exc:  # pragma: no cover - surfaced through assertions below
            error = exc
        finally:
            lifecycle_samples.append(
                {"phase": "after_screenshot", "state": await _record_state(self), "error": error}
            )

    OverlayController.claim_started = instrumented_claim_started
    OverlayController.claim_ended = instrumented_claim_ended
    OverlayController.before_screenshot = instrumented_before_screenshot
    OverlayController.after_screenshot = instrumented_after_screenshot

    try:
        result = await runner.run(
            url=f"{example_server}/test_page.html",
            claims=["The modal title reads 'Edit Task'"],
            viewport=ViewportConfig(width=1280, height=800, device_scale_factor=1),
            navigation_hint="Click the first task row to open the task modal before judging the claim.",
            visualize=True,
        )
    except Exception as exc:
        message = str(exc).lower()
        if "browser" in message and ("launch" in message or "display" in message or "headed" in message):
            pytest.skip(f"headed browser unavailable in this environment: {exc}")
        raise
    finally:
        OverlayController.claim_started = original_claim_started
        OverlayController.claim_ended = original_claim_ended
        OverlayController.before_screenshot = original_before_screenshot
        OverlayController.after_screenshot = original_after_screenshot
        await runner.close()

    assert result.overall_status == "completed"
    assert [item.status for item in result.results] == ["passed"]
    assert "Visible dialog title matched" in result.results[0].finding
    assert result.results[0].trace.actions == ["left_click([419, 348])"]
    assert result.results[0].trace.steps_taken == 1
    assert all(Path(path).exists() for path in result.results[0].trace.screenshot_paths)

    before_samples = [sample for sample in lifecycle_samples if sample["phase"] == "before_screenshot"]
    after_samples = [sample for sample in lifecycle_samples if sample["phase"] == "after_screenshot"]
    started_samples = [sample for sample in lifecycle_samples if sample["phase"] == "after_claim_started"]
    ended_samples = [sample for sample in lifecycle_samples if sample["phase"] == "after_claim_ended"]

    assert started_samples, "overlay claim_started should inject persistent and transient roots"
    assert ended_samples, "overlay claim_ended should clean up overlay roots"
    assert len(before_samples) >= 2, "expected a screenshot before the first response and after the action"
    assert len(after_samples) >= 2, "expected a restore step after each screenshot"

    for sample in before_samples:
        state = sample["state"]
        assert sample["error"] is None
        assert state["persistent"]["present"] is True
        assert state["persistent"]["display"] == "none"
        assert state["transient"]["present"] is True
        assert state["transient"]["display"] == "none"

    for sample in after_samples:
        state = sample["state"]
        assert sample["error"] is None
        assert state["persistent"]["present"] is True
        assert state["persistent"]["display"] != "none"
        assert state["transient"]["present"] is True
        assert state["transient"]["display"] == "none"

    started_state = started_samples[0]["state"]
    assert started_samples[0]["error"] is None
    assert started_state["persistent"]["present"] is True
    assert started_state["persistent"]["display"] != "none"
    assert started_state["chip"]["present"] is True
    assert started_state["chip"]["text"].upper() == "ANALYZING"

    ended_state = ended_samples[0]["state"]
    assert ended_samples[0]["error"] is None
    assert ended_state["persistent"]["present"] is False
    assert ended_state["transient"]["present"] is False
    assert ended_state["chip"]["present"] is False
