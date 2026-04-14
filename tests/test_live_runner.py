from __future__ import annotations

import contextlib
import json
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

from fakes import FakeChoice, FakeFunction, FakeMessage, FakeNavigatorClient, FakeResponse, FakeToolCall


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



async def _overlay_dom_state(page: Any) -> dict[str, Any]:
    return await page.evaluate(
        """() => {
            const read = (id) => {
                const element = document.getElementById(id);
                if (!element) {
                    return { present: false, display: null, visibility: null, opacity: null, text: null };
                }
                const style = window.getComputedStyle(element);
                return {
                    present: true,
                    display: style.display,
                    visibility: style.visibility,
                    opacity: style.opacity,
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


@contextlib.asynccontextmanager
async def instrumented_overlay_lifecycle(runner, OverlayController, run_kwargs):
    """Instrument OverlayController and run the runner, yielding (result, lifecycle_samples)."""
    lifecycle_samples: list[dict[str, Any]] = []
    originals = {
        "claim_started": OverlayController.claim_started,
        "claim_ended": OverlayController.claim_ended,
        "before_screenshot": OverlayController.before_screenshot,
        "after_screenshot": OverlayController.after_screenshot,
        "set_status": OverlayController.set_status,
        "show_thought": OverlayController.show_thought,
    }

    async def _record_state(controller):
        page = getattr(controller, "_page", None) or getattr(controller, "page", None)
        assert page is not None, "OverlayController must expose a page reference"
        return await _overlay_dom_state(page)

    def _make_instrumented(phase_name, original):
        async def instrumented(self, *args, **kwargs):
            error = None
            try:
                await original(self, *args, **kwargs)
            except Exception as exc:  # pragma: no cover
                error = exc
            finally:
                lifecycle_samples.append(
                    {"phase": phase_name, "state": await _record_state(self), "error": error}
                )
        return instrumented

    OverlayController.claim_started = _make_instrumented("after_claim_started", originals["claim_started"])
    OverlayController.claim_ended = _make_instrumented("after_claim_ended", originals["claim_ended"])
    OverlayController.before_screenshot = _make_instrumented("before_screenshot", originals["before_screenshot"])
    OverlayController.after_screenshot = _make_instrumented("after_screenshot", originals["after_screenshot"])
    OverlayController.set_status = _make_instrumented("set_status", originals["set_status"])
    OverlayController.show_thought = _make_instrumented("show_thought", originals["show_thought"])

    try:
        result = await runner.run(**run_kwargs)
        yield result, lifecycle_samples
    except Exception as exc:
        message = str(exc).lower()
        if "browser" in message and ("launch" in message or "display" in message or "headed" in message):
            pytest.skip(f"headed browser unavailable in this environment: {exc}")
        raise
    finally:
        for name, original in originals.items():
            setattr(OverlayController, name, original)
        await runner.close()


@pytest.mark.asyncio
async def test_live_runner_executes_real_browser_flow_and_passes_modal_claim(
    example_server: str,
    tmp_path: Path,
) -> None:
    browser_manager = BrowserManager(headless=True, settle_delay_seconds=0)
    navigator_client = FakeNavigatorClient(
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
            FakeResponse(parsed_json={"status": "passed", "finding": "The modal title reads Edit Task."}),
        ]
    )
    artifact_manager = ArtifactManager(tmp_path / "artifacts")
    claim_verifier = ClaimVerifier(
        browser_manager=browser_manager,
        artifact_manager=artifact_manager,
        navigator_client=navigator_client,
    )
    runner = VisualQARunner(
        browser_manager=browser_manager,
        artifact_manager=artifact_manager,
        navigator_client=navigator_client,
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
    assert result.results[0].trace.actions == ["left_click([420, 348])"]
    assert result.results[0].trace.steps_taken == 1
    assert all(Path(path).exists() for path in result.results[0].trace.screenshot_paths)
    assert result.results[0].proof is not None
    assert result.results[0].proof.step == 1
    assert result.results[0].proof.text is None
    assert Path(result.results[0].proof.screenshot_path).exists()
    assert result.results[0].proof.after_action == "left_click([420, 348])"
    assert result.results[0].page.url == f"{example_server}/test_page.html"


@pytest.mark.asyncio
async def test_live_runner_downgrades_false_positive_button_claim_with_grounding(
    example_server: str,
    tmp_path: Path,
) -> None:
    browser_manager = BrowserManager(headless=True, settle_delay_seconds=0)
    navigator_client = FakeNavigatorClient(
        responses=[
            FakeResponse(parsed_json={ "status": "passed", "finding": "The Show Save Confirmation button is visible without scrolling.", })
        ]
    )
    artifact_manager = ArtifactManager(tmp_path / "artifacts")
    claim_verifier = ClaimVerifier(
        browser_manager=browser_manager,
        artifact_manager=artifact_manager,
        navigator_client=navigator_client,
    )
    runner = VisualQARunner(
        browser_manager=browser_manager,
        artifact_manager=artifact_manager,
        navigator_client=navigator_client,
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
    navigator_client = FakeNavigatorClient(
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
            FakeResponse(parsed_json={"status": "passed", "finding": "The modal title reads Edit Task."}),
        ]
    )
    artifact_manager = ArtifactManager(tmp_path / "artifacts")
    claim_verifier = ClaimVerifier(
        browser_manager=browser_manager,
        artifact_manager=artifact_manager,
        navigator_client=navigator_client,
    )
    runner = VisualQARunner(
        browser_manager=browser_manager,
        artifact_manager=artifact_manager,
        navigator_client=navigator_client,
        claim_verifier=claim_verifier,
    )

    run_kwargs = dict(
        url=f"{example_server}/test_page.html",
        claims=["The modal title reads 'Edit Task'"],
        viewport=ViewportConfig(width=1280, height=800, device_scale_factor=1),
        navigation_hint="Click the first task row to open the task modal before judging the claim.",
        visualize=True,
    )

    async with instrumented_overlay_lifecycle(runner, OverlayController, run_kwargs) as (result, lifecycle_samples):
        assert result.overall_status == "completed"
        assert [item.status for item in result.results] == ["passed"]
        assert "Visible dialog title matched" in result.results[0].finding
        assert result.results[0].trace.actions == ["left_click([420, 348])"]
        assert result.results[0].trace.steps_taken == 1
        assert all(Path(path).exists() for path in result.results[0].trace.screenshot_paths)

        before_samples = [sample for sample in lifecycle_samples if sample["phase"] == "before_screenshot"]
        after_samples = [sample for sample in lifecycle_samples if sample["phase"] == "after_screenshot"]
        started_samples = [sample for sample in lifecycle_samples if sample["phase"] == "after_claim_started"]
        ended_samples = [sample for sample in lifecycle_samples if sample["phase"] == "after_claim_ended"]

        assert started_samples, "overlay claim_started should inject persistent and transient roots"
        assert ended_samples, "overlay claim_ended should clean up overlay roots"
        # Initial screenshot is taken before overlay injection (no flash),
        # so this one-action flow should produce exactly one post-action pair.
        assert len(before_samples) == 1, "expected exactly one screenshot hide step after the action"
        assert len(after_samples) == 1, "expected exactly one restore step after the action"

        for sample in before_samples:
            state = sample["state"]
            assert sample["error"] is None
            assert state["persistent"]["present"] is True
            assert state["persistent"]["display"] != "none"
            assert state["persistent"]["visibility"] == "hidden"
            assert state["persistent"]["opacity"] == "0"
            assert state["transient"]["present"] is True
            assert state["transient"]["display"] != "none"
            assert state["transient"]["visibility"] == "hidden"
            assert state["transient"]["opacity"] == "0"

        for sample in after_samples:
            state = sample["state"]
            assert sample["error"] is None
            assert state["persistent"]["present"] is True
            assert state["persistent"]["display"] != "none"
            assert state["persistent"]["visibility"] == "visible"
            assert state["persistent"]["opacity"] == "1"
            assert state["transient"]["present"] is True
            assert state["transient"]["display"] != "none"
            assert state["transient"]["visibility"] == "hidden"
            assert state["transient"]["opacity"] == "0"
            # Chip is only present on the same page — navigation actions
            # destroy the persistent root when the new page loads.
            if state["chip"]["present"]:
                assert state["chip"]["text"].upper() == "ANALYZING"

        after_screenshot_index = max(
            index for index, sample in enumerate(lifecycle_samples) if sample["phase"] == "after_screenshot"
        )
        post_capture_status_sample = next(
            sample
            for index, sample in enumerate(lifecycle_samples)
            if index > after_screenshot_index and sample["phase"] == "set_status"
        )
        assert post_capture_status_sample["state"]["chip"]["present"] is True
        assert post_capture_status_sample["state"]["chip"]["text"].upper() == "ANALYZING"

        started_state = started_samples[0]["state"]
        assert started_samples[0]["error"] is None
        assert started_state["persistent"]["present"] is True
        assert started_state["persistent"]["display"] != "none"
        assert started_state["persistent"]["visibility"] == "visible"
        assert started_state["persistent"]["opacity"] == "1"
        assert started_state["chip"]["present"] is True
        assert started_state["chip"]["text"].upper() == "ANALYZING"

        ended_state = ended_samples[0]["state"]
        assert ended_samples[0]["error"] is None
        assert ended_state["persistent"]["present"] is False
        assert ended_state["transient"]["present"] is False
        assert ended_state["chip"]["present"] is False


@pytest.mark.asyncio
async def test_live_runner_headed_overlay_zero_action_path_skips_hide_restore(
    example_server: str,
    tmp_path: Path,
) -> None:
    overlay_path = PACKAGE_ROOT / "src/frontend_visualqa/overlay.py"
    if not overlay_path.exists():
        pytest.skip("headed overlay implementation is not present in this partial worktree")
    from frontend_visualqa.overlay import OverlayController

    browser_manager = BrowserManager(headless=False, settle_delay_seconds=0)
    navigator_client = FakeNavigatorClient(
        responses=[
            FakeResponse(parsed_json={ "status": "passed", "finding": "The page title reads Frontend Visual QA Playground.", })
        ]
    )
    artifact_manager = ArtifactManager(tmp_path / "artifacts")
    claim_verifier = ClaimVerifier(
        browser_manager=browser_manager,
        artifact_manager=artifact_manager,
        navigator_client=navigator_client,
    )
    runner = VisualQARunner(
        browser_manager=browser_manager,
        artifact_manager=artifact_manager,
        navigator_client=navigator_client,
        claim_verifier=claim_verifier,
    )

    run_kwargs = dict(
        url=f"{example_server}/test_page.html",
        claims=["The page title reads 'Frontend Visual QA Playground'"],
        viewport=ViewportConfig(width=1280, height=800, device_scale_factor=1),
        visualize=True,
    )

    async with instrumented_overlay_lifecycle(runner, OverlayController, run_kwargs) as (result, lifecycle_samples):
        assert result.overall_status == "completed"
        assert [item.status for item in result.results] == ["passed"]
        assert result.results[0].trace.actions == []
        assert result.results[0].trace.steps_taken == 0
        assert len(result.results[0].trace.screenshot_paths) == 1
        assert result.results[0].proof is not None
        assert result.results[0].proof.step == 0
        assert result.results[0].proof.after_action is None

        before_samples = [sample for sample in lifecycle_samples if sample["phase"] == "before_screenshot"]
        after_samples = [sample for sample in lifecycle_samples if sample["phase"] == "after_screenshot"]
        started_samples = [sample for sample in lifecycle_samples if sample["phase"] == "after_claim_started"]
        ended_samples = [sample for sample in lifecycle_samples if sample["phase"] == "after_claim_ended"]

        assert started_samples, "overlay claim_started should inject persistent and transient roots"
        assert ended_samples, "overlay claim_ended should clean up overlay roots"
        assert before_samples == []
        assert after_samples == []

        started_state = started_samples[0]["state"]
        assert started_samples[0]["error"] is None
        assert started_state["persistent"]["present"] is True
        assert started_state["persistent"]["visibility"] == "visible"
        assert started_state["persistent"]["opacity"] == "1"
        assert started_state["chip"]["present"] is True
        assert started_state["chip"]["text"].upper() == "ANALYZING"

        ended_state = ended_samples[0]["state"]
        assert ended_samples[0]["error"] is None
        assert ended_state["persistent"]["present"] is False
        assert ended_state["transient"]["present"] is False
        assert ended_state["chip"]["present"] is False
