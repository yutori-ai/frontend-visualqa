from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from frontend_visualqa.artifacts import RunArtifacts
from fakes import FakeArtifactManager, instantiate_with_supported_kwargs, is_bootstrap_step_artifact

from frontend_visualqa.schemas import (
    BrowserConfig,
    BrowserMode,
    BrowserStatusResult,
    ClaimResult,
    ManageBrowserInput,
    VerifyVisualClaimsInput,
    ViewportConfig,
)


def _import_runner_module():
    import importlib

    try:
        return importlib.import_module("frontend_visualqa.runner")
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("frontend_visualqa"):
            pytest.skip("frontend_visualqa.runner is not implemented in this worktree yet")
        raise


@dataclass
class FakePage:
    url: str


@dataclass
class FakeSession:
    session_key: str
    page: FakePage
    viewport: ViewportConfig


class FakeBrowserManager:
    def __init__(self, viewport: ViewportConfig) -> None:
        self.viewport = viewport
        self.sessions: dict[str, FakeSession] = {}
        self.goto_calls: list[tuple[str, str]] = []
        self.restart_calls: list[str] = []
        self.closed_sessions: list[str] = []
        self.closed = False

    async def get_session(
        self,
        session_key: str = "default",
        *,
        viewport: ViewportConfig | None = None,
        reuse_session: bool = True,
    ) -> FakeSession:
        del reuse_session
        desired = viewport or self.viewport
        session = self.sessions.get(session_key)
        if session is None:
            session = FakeSession(session_key=session_key, page=FakePage(url="about:blank"), viewport=desired)
            self.sessions[session_key] = session
        else:
            session.viewport = desired
        return session

    async def goto(self, session: FakeSession, url: str) -> str:
        session.page.url = url
        self.goto_calls.append((session.session_key, url))
        return url

    async def reset_to_url(self, session: FakeSession, url: str) -> str:
        return await self.goto(session, url)

    async def capture_screenshot(self, session: FakeSession) -> bytes:
        del session
        return b"\x89PNGfake"

    async def set_viewport(self, session_key: str, viewport: ViewportConfig) -> FakeSession:
        session = await self.get_session(session_key=session_key, viewport=viewport, reuse_session=True)
        session.viewport = viewport
        return session

    async def restart_session(
        self,
        session_key: str = "default",
        *,
        viewport: ViewportConfig | None = None,
        preserve_url: bool = True,
    ) -> FakeSession:
        del preserve_url
        self.restart_calls.append(session_key)
        self.sessions.pop(session_key, None)
        return await self.get_session(session_key=session_key, viewport=viewport, reuse_session=False)

    async def close_session(self, session_key: str) -> None:
        self.closed_sessions.append(session_key)
        self.sessions.pop(session_key, None)

    async def close(self) -> None:
        self.closed = True
        self.sessions.clear()

    def status(self) -> BrowserStatusResult:
        return BrowserStatusResult(
            browser_running=not self.closed,
            sessions=[
                {
                    "session_key": session.session_key,
                    "browser_open": not self.closed,
                    "current_url": session.page.url,
                    "viewport": session.viewport,
                }
                for session in self.sessions.values()
            ],
        )


class FakeClaimVerifier:
    def __init__(self, results: list[ClaimResult]) -> None:
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []

    async def verify(self, *args: Any, **kwargs: Any) -> ClaimResult:
        if args:
            kwargs["session"] = args[0]
        self.calls.append(kwargs)
        return self.results.pop(0)


def _build_runner(
    module: Any,
    tmp_path: Path,
    verifier_results: list[ClaimResult],
    monkeypatch: pytest.MonkeyPatch,
    *,
    browser_manager: FakeBrowserManager | None = None,
    claim_verifier: Any | None = None,
) -> tuple[Any, FakeBrowserManager, FakeClaimVerifier]:
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)
    browser = browser_manager or FakeBrowserManager(viewport)
    verifier = claim_verifier or FakeClaimVerifier(verifier_results)
    artifacts = FakeArtifactManager(tmp_path, run_id="run-001")

    monkeypatch.setattr(module, "BrowserManager", lambda *args, **kwargs: browser, raising=False)
    monkeypatch.setattr(module, "ClaimVerifier", lambda *args, **kwargs: verifier, raising=False)
    monkeypatch.setattr(module, "ArtifactManager", lambda *args, **kwargs: artifacts, raising=False)
    monkeypatch.setattr(module, "N1Client", lambda *args, **kwargs: object(), raising=False)

    runner = instantiate_with_supported_kwargs(
        module.VisualQARunner,
        browser_manager=browser,
        browser=browser,
        claim_verifier=verifier,
        verifier=verifier,
        artifact_manager=artifacts,
        artifacts=artifacts,
    )

    for attribute_name, value in {
        "browser_manager": browser,
        "browser": browser,
        "claim_verifier": verifier,
        "verifier": verifier,
        "artifact_manager": artifacts,
        "artifacts": artifacts,
    }.items():
        setattr(runner, attribute_name, value)

    async def _skip_preflight(url: str) -> None:
        del url
        return None

    setattr(runner, "_preflight_url", _skip_preflight)

    return runner, browser, verifier


async def _call_run(runner: Any, **kwargs: Any) -> Any:
    signature = inspect.signature(runner.run)
    filtered = {name: value for name, value in kwargs.items() if name in signature.parameters}
    if not filtered and "request" in signature.parameters:
        filtered["request"] = kwargs
    return await runner.run(**filtered)


async def _call_take_screenshot(runner: Any, **kwargs: Any) -> Any:
    signature = inspect.signature(runner.take_screenshot)
    filtered = {name: value for name, value in kwargs.items() if name in signature.parameters}
    return await runner.take_screenshot(**filtered)


async def _call_manage_browser(
    runner: Any,
    *,
    action: str,
    session_key: str = "default",
    viewport: ViewportConfig | None = None,
) -> Any:
    signature = inspect.signature(runner.manage_browser)
    filtered: dict[str, Any] = {}

    if "action" in signature.parameters:
        filtered["action"] = action
    if "session_key" in signature.parameters:
        filtered["session_key"] = session_key
    if "viewport" in signature.parameters:
        filtered["viewport"] = viewport

    input_names = {"request", "input", "payload", "manage_input"}
    target_name = next((name for name in signature.parameters if name in input_names), None)
    if target_name is not None:
        filtered[target_name] = ManageBrowserInput(action=action, session_key=session_key, viewport=viewport)

    return await runner.manage_browser(**filtered)


def _result(name: str, status: str, viewport: ViewportConfig) -> ClaimResult:
    return ClaimResult(
        claim=name,
        status=status,
        finding=f"{name}: {status}",
        proof={
            "screenshot_path": "artifacts/run-001/claim-01/step-01.webp",
            "step": 1,
            "after_action": "read_page()",
            "text": f"{name}: {status}",
            "text_path": "artifacts/run-001/claim-01/step-01.txt",
        },
        page={"url": "http://fixture.local/page", "viewport": viewport},
        trace={
            "steps_taken": 1,
            "wrong_page_recovered": False,
            "screenshot_paths": [
                "artifacts/run-001/claim-01/step-00.webp",
                "artifacts/run-001/claim-01/step-01.webp",
            ],
            "actions": ["read_page()"],
            "trace_path": "artifacts/run-001/claim-01/trace.json",
        },
    )


@pytest.mark.asyncio
async def test_runner_run_aggregates_claim_results_and_resets_between_claims(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _import_runner_module()
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)
    runner, browser, verifier = _build_runner(
        module,
        tmp_path,
        verifier_results=[_result("Claim one", "passed", viewport), _result("Claim two", "failed", viewport)],
        monkeypatch=monkeypatch,
    )

    result = await _call_run(
        runner,
        url="http://fixture.local/page",
        claims=["Claim one", "Claim two"],
        viewport=viewport,
        session_key="qa-session",
        run_name="auth-ci",
        reuse_session=True,
        reset_between_claims=True,
        visualize=True,
        max_steps_per_claim=5,
        navigation_hint="Open the modal if needed.",
    )

    assert result.overall_status == "completed"
    assert result.session_key == "qa-session"
    assert result.run_name == "auth-ci"
    assert [item.status for item in result.results] == ["passed", "failed"]
    assert result.artifacts_dir
    assert result.summary
    assert result.results[0].finding == "Claim one: passed"
    assert result.results[0].page.url == "http://fixture.local/page"
    assert result.results[0].trace.steps_taken == 1
    assert result.results[0].proof.screenshot_path.endswith("step-01.webp")
    assert len([call for call in browser.goto_calls if call == ("qa-session", "http://fixture.local/page")]) >= 2
    assert verifier.calls
    assert verifier.calls[0]["claim"] == "Claim one"
    assert verifier.calls[0]["url"] == "http://fixture.local/page"
    assert verifier.calls[0]["navigation_hint"] == "Open the modal if needed."
    assert verifier.calls[0]["visualize"] is True


@pytest.mark.asyncio
async def test_runner_run_request_reuses_prevalidated_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _import_runner_module()
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)
    runner, browser, verifier = _build_runner(
        module,
        tmp_path,
        verifier_results=[_result("Claim one", "passed", viewport)],
        monkeypatch=monkeypatch,
    )
    request = VerifyVisualClaimsInput(
        url="http://fixture.local/page",
        claims=["Claim one"],
        viewport=viewport,
        session_key="qa-session",
        run_name="modal-check",
        reuse_session=True,
        reset_between_claims=True,
        visualize=True,
        max_steps_per_claim=5,
        navigation_hint="Open the modal if needed.",
    )

    result = await runner.run_request(request)

    assert result.overall_status == "completed"
    assert result.run_name == "modal-check"
    assert [item.status for item in result.results] == ["passed"]
    assert browser.goto_calls[0] == ("qa-session", "http://fixture.local/page")
    assert verifier.calls[0]["navigation_hint"] == "Open the modal if needed."
    assert verifier.calls[0]["visualize"] is True


@pytest.mark.asyncio
async def test_runner_take_screenshot_saves_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _import_runner_module()
    runner, _, _ = _build_runner(
        module,
        tmp_path,
        verifier_results=[],
        monkeypatch=monkeypatch,
    )
    viewport = ViewportConfig(width=768, height=1024, device_scale_factor=1)

    result = await _call_take_screenshot(
        runner,
        url="http://fixture.local/page",
        viewport=viewport,
        session_key="shot-session",
        run_name="mobile-home",
    )

    assert result.final_url == "http://fixture.local/page"
    assert result.run_name == "mobile-home"
    assert result.viewport == viewport
    assert Path(result.screenshot_path).exists()


@pytest.mark.asyncio
async def test_runner_manage_browser_proxies_status_restart_viewport_and_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _import_runner_module()
    runner, browser, _ = _build_runner(
        module,
        tmp_path,
        verifier_results=[],
        monkeypatch=monkeypatch,
    )
    viewport = ViewportConfig(width=390, height=844, device_scale_factor=1)

    await browser.get_session("managed", viewport=ViewportConfig(width=1280, height=800, device_scale_factor=1))
    status = await _call_manage_browser(runner, action="status", session_key="managed")
    assert status.browser_running is True
    assert status.browser_mode == BrowserMode.ephemeral

    resized_status = await _call_manage_browser(runner, action="set_viewport", session_key="managed", viewport=viewport)
    assert resized_status.sessions[0].viewport == viewport

    restarted_status = await _call_manage_browser(runner, action="restart", session_key="managed", viewport=viewport)
    assert "managed" in browser.restart_calls
    assert restarted_status.sessions[0].session_key == "managed"

    closed_status = await _call_manage_browser(runner, action="close", session_key="managed")
    assert "managed" in browser.closed_sessions or browser.closed is True
    assert closed_status.browser_running in {True, False}


@pytest.mark.asyncio
async def test_runner_manage_browser_request_uses_prevalidated_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _import_runner_module()
    runner, browser, _ = _build_runner(
        module,
        tmp_path,
        verifier_results=[],
        monkeypatch=monkeypatch,
    )
    viewport = ViewportConfig(width=390, height=844, device_scale_factor=1)

    result = await runner.manage_browser_request(
        ManageBrowserInput(action="set_viewport", session_key="managed", viewport=viewport)
    )

    assert result.browser_running is True
    assert browser.sessions["managed"].viewport == viewport


class ResetFailingBrowserManager(FakeBrowserManager):
    async def reset_to_url(self, session: FakeSession, url: str) -> str:
        del session, url
        raise RuntimeError("target page crashed during reset")


class ExplodingClaimVerifier:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def verify(self, *args: Any, **kwargs: Any) -> ClaimResult:
        if args:
            kwargs["session"] = args[0]
        self.calls.append(kwargs)
        raise RuntimeError("unexpected verifier crash")


class SlowClaimVerifier:
    def __init__(self, delay_seconds: float, result: ClaimResult) -> None:
        self.delay_seconds = delay_seconds
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def verify(self, *args: Any, **kwargs: Any) -> ClaimResult:
        import asyncio

        if args:
            kwargs["session"] = args[0]
        self.calls.append(kwargs)
        await asyncio.sleep(self.delay_seconds)
        return self.result


class TimeoutClaimVerifier:
    def __init__(self, partial_result: ClaimResult | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.partial_result = partial_result

    async def verify(self, *args: Any, **kwargs: Any) -> ClaimResult:
        if args:
            kwargs["session"] = args[0]
        self.calls.append(kwargs)
        raise TimeoutError("verifier timed out internally")

    def consume_partial_result(self, *, status: str, finding: str) -> ClaimResult | None:
        del status, finding
        return self.partial_result


class RunTimeoutClaimVerifier:
    def __init__(self, partial_result: ClaimResult, delay_seconds: float = 1.0) -> None:
        self.calls: list[dict[str, Any]] = []
        self.partial_result = partial_result
        self.delay_seconds = delay_seconds

    async def verify(self, *args: Any, **kwargs: Any) -> ClaimResult:
        import asyncio

        if args:
            kwargs["session"] = args[0]
        self.calls.append(kwargs)
        await asyncio.sleep(self.delay_seconds)
        return self.partial_result

    def consume_partial_result(self, *, status: str, finding: str) -> ClaimResult | None:
        del status, finding
        return self.partial_result


class PartialExplodingClaimVerifier:
    def __init__(self, partial_result: ClaimResult) -> None:
        self.calls: list[dict[str, Any]] = []
        self.partial_result = partial_result

    async def verify(self, *args: Any, **kwargs: Any) -> ClaimResult:
        if args:
            kwargs["session"] = args[0]
        self.calls.append(kwargs)
        raise RuntimeError("unexpected verifier crash")

    def consume_partial_result(self, *, status: str, finding: str) -> ClaimResult | None:
        del status, finding
        return self.partial_result


class NavigationFailingBrowserManager(FakeBrowserManager):
    async def goto(self, session: FakeSession, url: str) -> str:
        del session, url
        raise RuntimeError("connection refused")


@pytest.mark.asyncio
async def test_runner_marks_claim_not_testable_when_reset_between_claims_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _import_runner_module()
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)
    browser = ResetFailingBrowserManager(viewport)
    runner, _, verifier = _build_runner(
        module,
        tmp_path,
        verifier_results=[_result("Claim one", "passed", viewport)],
        monkeypatch=monkeypatch,
        browser_manager=browser,
    )

    result = await _call_run(
        runner,
        url="http://fixture.local/page",
        claims=["Claim one", "Claim two"],
        viewport=viewport,
        session_key="qa-session",
        reuse_session=True,
        reset_between_claims=True,
        max_steps_per_claim=5,
    )

    assert [item.status for item in result.results] == ["passed", "not_testable"]
    assert "Could not prepare browser state" in result.results[1].finding
    assert len(verifier.calls) == 1


@pytest.mark.asyncio
async def test_runner_marks_claim_not_testable_when_verifier_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _import_runner_module()
    exploding_verifier = ExplodingClaimVerifier()
    runner, _, _ = _build_runner(
        module,
        tmp_path,
        verifier_results=[],
        monkeypatch=monkeypatch,
        claim_verifier=exploding_verifier,
    )
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)

    result = await _call_run(
        runner,
        url="http://fixture.local/page",
        claims=["Claim one"],
        viewport=viewport,
        session_key="qa-session",
        reuse_session=True,
        reset_between_claims=True,
        max_steps_per_claim=5,
    )

    assert [item.status for item in result.results] == ["inconclusive"]
    assert "Verification crashed unexpectedly before returning a verdict" in result.results[0].finding
    assert exploding_verifier.calls


@pytest.mark.asyncio
async def test_runner_take_screenshot_returns_not_testable_result_on_navigation_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _import_runner_module()
    browser = NavigationFailingBrowserManager(ViewportConfig(width=1280, height=800, device_scale_factor=1))
    runner, _, _ = _build_runner(
        module,
        tmp_path,
        verifier_results=[],
        monkeypatch=monkeypatch,
        browser_manager=browser,
    )

    result = await _call_take_screenshot(
        runner,
        url="http://fixture.local/page",
        viewport=ViewportConfig(width=768, height=1024, device_scale_factor=1),
        session_key="shot-session",
    )

    assert result.status == "not_testable"
    assert result.screenshot_path is None
    assert "Could not capture a screenshot" in result.summary


@pytest.mark.asyncio
async def test_runner_marks_claim_inconclusive_when_claim_timeout_expires(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _import_runner_module()
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)
    slow_verifier = SlowClaimVerifier(delay_seconds=0.05, result=_result("Claim one", "passed", viewport))
    runner, _, _ = _build_runner(
        module,
        tmp_path,
        verifier_results=[],
        monkeypatch=monkeypatch,
        claim_verifier=slow_verifier,
    )

    result = await _call_run(
        runner,
        url="http://fixture.local/page",
        claims=["Claim one"],
        viewport=viewport,
        session_key="qa-session",
        reuse_session=True,
        reset_between_claims=True,
        max_steps_per_claim=5,
        claim_timeout_seconds=0.01,
    )

    assert [item.status for item in result.results] == ["inconclusive"]
    assert "Claim verification timed out" in result.results[0].finding


@pytest.mark.asyncio
async def test_runner_handles_timeout_error_when_claim_timeout_is_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _import_runner_module()
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)
    verifier = TimeoutClaimVerifier()
    runner, _, _ = _build_runner(
        module,
        tmp_path,
        verifier_results=[],
        monkeypatch=monkeypatch,
        claim_verifier=verifier,
    )

    result = await _call_run(
        runner,
        url="http://fixture.local/page",
        claims=["Claim one"],
        viewport=viewport,
        session_key="qa-session",
        reuse_session=True,
        reset_between_claims=True,
        max_steps_per_claim=5,
        claim_timeout_seconds=None,
    )

    assert [item.status for item in result.results] == ["inconclusive"]
    assert result.results[0].finding == "Claim verification timed out before a verdict was recorded."


@pytest.mark.asyncio
async def test_runner_uses_partial_claim_result_when_timeout_interrupts_verifier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _import_runner_module()
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)
    partial_result = ClaimResult(
        claim="Claim one",
        status="inconclusive",
        finding="Claim verification timed out after 1s before a verdict was recorded.",
        proof={
            "screenshot_path": "artifacts/run-001/claim-01/step-00.webp",
            "step": 0,
            "after_action": None,
            "text": None,
            "text_path": None,
        },
        page={"url": "http://fixture.local/page", "viewport": viewport},
        trace={
            "steps_taken": 1,
            "wrong_page_recovered": False,
            "screenshot_paths": ["artifacts/run-001/claim-01/step-00.webp"],
            "actions": ["scroll(direction='down', amount=300)"],
            "trace_path": "artifacts/run-001/claim-01/trace.json",
        },
    )
    verifier = RunTimeoutClaimVerifier(partial_result=partial_result)
    runner, _, _ = _build_runner(
        module,
        tmp_path,
        verifier_results=[],
        monkeypatch=monkeypatch,
        claim_verifier=verifier,
    )

    result = await _call_run(
        runner,
        url="http://fixture.local/page",
        claims=["Claim one"],
        viewport=viewport,
        session_key="qa-session",
        reuse_session=True,
        reset_between_claims=True,
        max_steps_per_claim=5,
        claim_timeout_seconds=1.0,
    )

    assert [item.status for item in result.results] == ["inconclusive"]
    assert result.results[0].proof is not None
    assert is_bootstrap_step_artifact(result.results[0].proof.screenshot_path)
    assert result.results[0].trace.steps_taken == 1
    assert result.results[0].trace.actions == ["scroll(direction='down', amount=300)"]


@pytest.mark.asyncio
async def test_runner_uses_partial_claim_result_when_verifier_crashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _import_runner_module()
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)
    partial_result = ClaimResult(
        claim="Claim one",
        status="inconclusive",
        finding="Verification crashed unexpectedly before returning a verdict: unexpected verifier crash",
        proof={
            "screenshot_path": "artifacts/run-001/claim-01/step-01.webp",
            "step": 1,
            "after_action": "read_page()",
            "text": "Visible text included 'Dashboard'.",
            "text_path": "artifacts/run-001/claim-01/step-01.txt",
        },
        page={"url": "http://fixture.local/page", "viewport": viewport},
        trace={
            "steps_taken": 1,
            "wrong_page_recovered": False,
            "screenshot_paths": [
                "artifacts/run-001/claim-01/step-00.webp",
                "artifacts/run-001/claim-01/step-01.webp",
            ],
            "actions": ["read_page()"],
            "trace_path": "artifacts/run-001/claim-01/trace.json",
        },
    )
    verifier = PartialExplodingClaimVerifier(partial_result)
    runner, _, _ = _build_runner(
        module,
        tmp_path,
        verifier_results=[],
        monkeypatch=monkeypatch,
        claim_verifier=verifier,
    )

    result = await _call_run(
        runner,
        url="http://fixture.local/page",
        claims=["Claim one"],
        viewport=viewport,
        session_key="qa-session",
        reuse_session=True,
        reset_between_claims=True,
        max_steps_per_claim=5,
    )

    assert [item.status for item in result.results] == ["inconclusive"]
    assert result.results[0].proof is not None
    assert result.results[0].proof.after_action == "read_page()"
    assert result.results[0].trace.trace_path == "artifacts/run-001/claim-01/trace.json"


@pytest.mark.asyncio
async def test_runner_marks_remaining_claims_inconclusive_when_run_timeout_expires(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _import_runner_module()
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)
    slow_verifier = SlowClaimVerifier(delay_seconds=0.05, result=_result("Claim one", "passed", viewport))
    runner, _, _ = _build_runner(
        module,
        tmp_path,
        verifier_results=[],
        monkeypatch=monkeypatch,
        claim_verifier=slow_verifier,
    )

    result = await _call_run(
        runner,
        url="http://fixture.local/page",
        claims=["Claim one", "Claim two"],
        viewport=viewport,
        session_key="qa-session",
        reuse_session=True,
        reset_between_claims=True,
        max_steps_per_claim=5,
        claim_timeout_seconds=None,
        run_timeout_seconds=0.01,
    )

    assert [item.status for item in result.results] == ["inconclusive", "inconclusive"]
    assert all("Run timed out" in item.finding for item in result.results)


@pytest.mark.asyncio
async def test_runner_preserves_partial_claim_result_when_run_timeout_interrupts_current_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _import_runner_module()
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)
    partial_result = ClaimResult(
        claim="Claim one",
        status="inconclusive",
        finding="Run timed out after 0.01s before this claim could finish.",
        proof={
            "screenshot_path": "artifacts/run-001/claim-01/step-01.webp",
            "step": 1,
            "after_action": "read_page()",
            "text": "Visible text included 'Dashboard'.",
            "text_path": "artifacts/run-001/claim-01/step-01.txt",
        },
        page={"url": "http://fixture.local/page", "viewport": viewport},
        trace={
            "steps_taken": 1,
            "wrong_page_recovered": False,
            "screenshot_paths": [
                "artifacts/run-001/claim-01/step-00.webp",
                "artifacts/run-001/claim-01/step-01.webp",
            ],
            "actions": ["read_page()"],
            "trace_path": "artifacts/run-001/claim-01/trace.json",
        },
    )
    verifier = RunTimeoutClaimVerifier(partial_result=partial_result)
    runner, _, _ = _build_runner(
        module,
        tmp_path,
        verifier_results=[],
        monkeypatch=monkeypatch,
        claim_verifier=verifier,
    )

    result = await _call_run(
        runner,
        url="http://fixture.local/page",
        claims=["Claim one", "Claim two"],
        viewport=viewport,
        session_key="qa-session",
        reuse_session=True,
        reset_between_claims=True,
        max_steps_per_claim=5,
        run_timeout_seconds=0.01,
    )

    assert [item.status for item in result.results] == ["inconclusive", "inconclusive"]
    assert result.results[0].proof is not None
    assert result.results[0].proof.after_action == "read_page()"
    assert result.results[0].trace.steps_taken == 1
    assert result.results[1].proof is None
    assert result.results[1].trace.steps_taken == 0


def test_build_not_testable_run_uses_aggregate_summary() -> None:
    module = _import_runner_module()
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)
    request = VerifyVisualClaimsInput(
        url="http://fixture.local/page",
        claims=["Claim one", "Claim two"],
        viewport=viewport,
        session_key="qa-session",
        run_name="preflight",
    )

    result = module.VisualQARunner._build_not_testable_run(
        request=request,
        run_dir="artifacts/run-001",
        finding="Could not reach the page before opening the browser.",
        started_at=1.0,
        completed_at=2.0,
    )

    assert result.overall_status == "not_testable"
    assert result.run_name == "preflight"
    assert result.summary == "0/2 claims passed. 2 not testable."
    assert all(item.finding == "Could not reach the page before opening the browser." for item in result.results)


def test_runner_passes_browser_config_to_browser_manager(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    module = _import_runner_module()
    captured: dict[str, Any] = {}

    class CapturingBrowserManager(FakeBrowserManager):
        def __init__(self, *, config: BrowserConfig | None = None, **kwargs: Any) -> None:
            del kwargs
            super().__init__(ViewportConfig(width=1280, height=800, device_scale_factor=1))
            captured["config"] = config

    artifacts = FakeArtifactManager(tmp_path, run_id="run-001")
    monkeypatch.setattr(module, "BrowserManager", CapturingBrowserManager, raising=False)
    monkeypatch.setattr(module, "ClaimVerifier", lambda *args, **kwargs: object(), raising=False)
    monkeypatch.setattr(module, "ArtifactManager", lambda *args, **kwargs: artifacts, raising=False)
    monkeypatch.setattr(module, "N1Client", lambda *args, **kwargs: object(), raising=False)

    browser_config = BrowserConfig(
        mode=BrowserMode.persistent,
        user_data_dir=str(tmp_path / "browser-profile"),
        headless=False,
    )
    runner = module.VisualQARunner(browser_config=browser_config)

    assert captured["config"] == browser_config
    assert runner.browser_manager is not None


def test_runner_passes_browser_config_visualize_to_default_claim_verifier(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _import_runner_module()
    captured: dict[str, Any] = {}

    class CapturingBrowserManager(FakeBrowserManager):
        def __init__(self, *, config: BrowserConfig | None = None, **kwargs: Any) -> None:
            del kwargs
            super().__init__(ViewportConfig(width=1280, height=800, device_scale_factor=1))
            captured["browser_config"] = config

    class CapturingClaimVerifier:
        def __init__(
            self,
            *,
            browser_manager: Any,
            artifact_manager: Any,
            n1_client: Any,
            visualize: bool = False,
        ) -> None:
            del browser_manager, artifact_manager, n1_client
            captured["visualize"] = visualize

        async def verify(self, *args: Any, **kwargs: Any) -> ClaimResult:
            del args, kwargs
            raise AssertionError("not expected to be called")

    artifacts = FakeArtifactManager(tmp_path, run_id="run-001")
    monkeypatch.setattr(module, "BrowserManager", CapturingBrowserManager, raising=False)
    monkeypatch.setattr(module, "ClaimVerifier", CapturingClaimVerifier, raising=False)
    monkeypatch.setattr(module, "ArtifactManager", lambda *args, **kwargs: artifacts, raising=False)
    monkeypatch.setattr(module, "N1Client", lambda *args, **kwargs: object(), raising=False)

    browser_config = BrowserConfig(
        mode=BrowserMode.persistent,
        user_data_dir=str(tmp_path / "browser-profile"),
        headless=False,
        visualize=True,
    )
    module.VisualQARunner(browser_config=browser_config)

    assert captured["browser_config"] == browser_config
    assert captured["visualize"] is True


@pytest.mark.asyncio
async def test_runner_preserves_injected_claim_verifier_visualize_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _import_runner_module()
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)
    browser = FakeBrowserManager(viewport)
    verifier = FakeClaimVerifier([_result("Claim one", "passed", viewport)])
    verifier._visualize = True
    artifacts = FakeArtifactManager(tmp_path, run_id="run-001")

    runner = instantiate_with_supported_kwargs(
        module.VisualQARunner,
        browser_manager=browser,
        browser=browser,
        claim_verifier=verifier,
        verifier=verifier,
        artifact_manager=artifacts,
        artifacts=artifacts,
        browser_config=BrowserConfig(visualize=False),
        n1_client=object(),
    )
    runner.browser_manager = browser
    runner.claim_verifier = verifier
    runner.artifact_manager = artifacts

    async def _skip_preflight(url: str) -> None:
        del url
        return None

    runner._preflight_url = _skip_preflight

    result = await runner.run(
        url="http://fixture.local/page",
        claims=["Claim one"],
        viewport=viewport,
        session_key="qa-session",
        reuse_session=True,
        reset_between_claims=True,
    )

    assert [item.status for item in result.results] == ["passed"]
    assert verifier.calls[0]["visualize"] is True


@pytest.mark.asyncio
async def test_per_call_visualize_override_does_not_leak_across_requests(
    tmp_path: Path,
) -> None:
    """Two sequential run_request calls with different visualize values must
    not leak the first call's override into the second call."""
    module = _import_runner_module()
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)
    browser = FakeBrowserManager(viewport)
    verifier = FakeClaimVerifier([
        _result("Claim one", "passed", viewport),
        _result("Claim two", "passed", viewport),
    ])
    verifier._visualize = False
    artifacts = FakeArtifactManager(tmp_path, run_id="run-001")

    runner = instantiate_with_supported_kwargs(
        module.VisualQARunner,
        browser_manager=browser,
        browser=browser,
        claim_verifier=verifier,
        verifier=verifier,
        artifact_manager=artifacts,
        artifacts=artifacts,
        browser_config=BrowserConfig(visualize=False),
        n1_client=object(),
    )
    runner.browser_manager = browser
    runner.claim_verifier = verifier
    runner.artifact_manager = artifacts

    async def _skip_preflight(url: str) -> None:
        del url
        return None

    runner._preflight_url = _skip_preflight

    # First request: visualize=True
    await runner.run(
        url="http://fixture.local/page",
        claims=["Claim one"],
        viewport=viewport,
        visualize=True,
    )
    assert verifier.calls[0]["visualize"] is True

    # Second request: visualize=None (should fall back to config default: False)
    await runner.run(
        url="http://fixture.local/page",
        claims=["Claim two"],
        viewport=viewport,
    )
    assert verifier.calls[1]["visualize"] is False, (
        "Per-call visualize=True from first request must not leak into second request"
    )


class SpyReporter:
    """Test spy that records write() calls."""
    def __init__(self) -> None:
        self.write_calls: list[tuple[Any, Path]] = []

    @property
    def name(self) -> str:
        return "spy"

    def write(self, run_result: Any, output_dir: Path) -> None:
        self.write_calls.append((run_result, output_dir))


@pytest.mark.asyncio
async def test_runner_invokes_reporters_after_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _import_runner_module()
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)
    runner, browser, verifier = _build_runner(
        module,
        tmp_path,
        verifier_results=[_result("Claim one", "passed", viewport)],
        monkeypatch=monkeypatch,
    )
    spy = SpyReporter()
    runner.reporters = [spy]
    result = await _call_run(
        runner,
        url="http://fixture.local/page",
        claims=["Claim one"],
        viewport=viewport,
        session_key="qa-session",
        reuse_session=True,
        reset_between_claims=True,
        max_steps_per_claim=5,
    )
    assert len(spy.write_calls) == 1
    written_result, written_dir = spy.write_calls[0]
    assert written_result.overall_status == "completed"
    assert str(written_dir) == result.artifacts_dir


@pytest.mark.asyncio
async def test_runner_writes_both_native_and_ctrf_reports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _import_runner_module()
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)
    from frontend_visualqa.reporters import get_reporters
    reporters = get_reporters(["native", "ctrf"])
    runner, browser, verifier = _build_runner(
        module,
        tmp_path,
        verifier_results=[_result("Claim one", "passed", viewport), _result("Claim two", "failed", viewport)],
        monkeypatch=monkeypatch,
    )
    runner.reporters = reporters
    result = await _call_run(
        runner,
        url="http://fixture.local/page",
        claims=["Claim one", "Claim two"],
        viewport=viewport,
        session_key="qa-session",
        reuse_session=True,
        reset_between_claims=True,
        max_steps_per_claim=5,
    )
    run_dir = Path(result.artifacts_dir)
    # Native report
    native_path = run_dir / "run_result.json"
    assert native_path.exists()
    native_data = json.loads(native_path.read_text())
    first_result = native_data["results"][0]
    assert set(first_result) == {"claim", "status", "finding", "proof", "page", "trace"}
    assert set(first_result["proof"]) == {"screenshot_path", "step", "after_action", "text", "text_path"}
    assert set(first_result["page"]) == {"url", "viewport"}
    assert set(first_result["trace"]) == {"steps_taken", "wrong_page_recovered", "screenshot_paths", "actions", "trace_path"}
    assert first_result["finding"] == "Claim one: passed"
    assert first_result["trace"]["wrong_page_recovered"] is False
    # CTRF report
    ctrf_path = run_dir / "ctrf-report.json"
    assert ctrf_path.exists()
    ctrf_data = json.loads(ctrf_path.read_text())
    assert ctrf_data["reportFormat"] == "CTRF"
    assert "specVersion" in ctrf_data
    assert ctrf_data["results"]["tool"]["name"] == "frontend-visualqa"
    assert ctrf_data["results"]["summary"]["tests"] == 2
    assert ctrf_data["results"]["summary"]["passed"] == 1
    assert ctrf_data["results"]["summary"]["failed"] == 1
    assert ctrf_data["results"]["tests"][0]["status"] == "passed"
    assert ctrf_data["results"]["tests"][1]["status"] == "failed"


@pytest.mark.asyncio
async def test_runner_ctrf_only_does_not_write_native_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When only ctrf is selected, run_result.json must not be written."""
    module = _import_runner_module()
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)
    from frontend_visualqa.reporters import get_reporters
    reporters = get_reporters(["ctrf"])
    runner, browser, verifier = _build_runner(
        module,
        tmp_path,
        verifier_results=[_result("Claim one", "passed", viewport)],
        monkeypatch=monkeypatch,
    )
    runner.reporters = reporters
    result = await _call_run(
        runner,
        url="http://fixture.local/page",
        claims=["Claim one"],
        viewport=viewport,
        session_key="qa-session",
        reuse_session=True,
        reset_between_claims=True,
        max_steps_per_claim=5,
    )
    run_dir = Path(result.artifacts_dir)
    assert (run_dir / "ctrf-report.json").exists()
    assert not (run_dir / "run_result.json").exists()
