from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from frontend_visualqa.artifacts import RunArtifacts
from frontend_visualqa.schemas import (
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


def _instantiate_with_supported_kwargs(factory: Any, **candidates: Any) -> Any:
    signature = inspect.signature(factory)
    kwargs = {
        name: value
        for name, value in candidates.items()
        if name in signature.parameters
        and signature.parameters[name].kind in {inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
    }
    return factory(**kwargs)


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
        return b"RIFFfakeWEBP"

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


class FakeArtifactManager:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.run = RunArtifacts(run_id="run-001", run_dir=base_dir / "run-001")
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

    def save_trace(self, run: RunArtifacts, claim_index: int, action_trace: list[str]) -> str:
        claim_dir = run.run_dir / f"claim-{claim_index:02d}"
        claim_dir.mkdir(parents=True, exist_ok=True)
        path = claim_dir / "action_trace.json"
        path.write_text(json.dumps(action_trace))
        return str(path)

    def save_json(self, run: RunArtifacts, relative_path: str, payload: dict[str, Any]) -> str:
        path = run.run_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))
        return str(path)


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
    artifacts = FakeArtifactManager(tmp_path)

    monkeypatch.setattr(module, "BrowserManager", lambda *args, **kwargs: browser, raising=False)
    monkeypatch.setattr(module, "ClaimVerifier", lambda *args, **kwargs: verifier, raising=False)
    monkeypatch.setattr(module, "ArtifactManager", lambda *args, **kwargs: artifacts, raising=False)

    runner = _instantiate_with_supported_kwargs(
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
        summary=f"{name}: {status}",
        final_url="http://fixture.local/page",
        steps_taken=1,
        viewport=viewport,
        screenshots=[],
        action_trace=[],
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
        verifier_results=[_result("Claim one", "pass", viewport), _result("Claim two", "fail", viewport)],
        monkeypatch=monkeypatch,
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
        navigation_hint="Open the modal if needed.",
    )

    assert result.overall_status == "completed"
    assert result.session_key == "qa-session"
    assert [item.status for item in result.results] == ["pass", "fail"]
    assert result.artifacts_dir
    assert result.summary
    assert len([call for call in browser.goto_calls if call == ("qa-session", "http://fixture.local/page")]) >= 2
    assert verifier.calls
    assert verifier.calls[0]["claim"] == "Claim one"
    assert verifier.calls[0]["url"] == "http://fixture.local/page"
    assert verifier.calls[0]["navigation_hint"] == "Open the modal if needed."


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
        verifier_results=[_result("Claim one", "pass", viewport)],
        monkeypatch=monkeypatch,
    )
    request = VerifyVisualClaimsInput(
        url="http://fixture.local/page",
        claims=["Claim one"],
        viewport=viewport,
        session_key="qa-session",
        reuse_session=True,
        reset_between_claims=True,
        max_steps_per_claim=5,
        navigation_hint="Open the modal if needed.",
    )

    result = await runner.run_request(request)

    assert result.overall_status == "completed"
    assert [item.status for item in result.results] == ["pass"]
    assert browser.goto_calls[0] == ("qa-session", "http://fixture.local/page")
    assert verifier.calls[0]["navigation_hint"] == "Open the modal if needed."


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
    )

    assert result.final_url == "http://fixture.local/page"
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
        verifier_results=[_result("Claim one", "pass", viewport)],
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

    assert [item.status for item in result.results] == ["pass", "not_testable"]
    assert "Could not prepare browser state" in result.results[1].summary
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
    assert "Verification crashed unexpectedly before returning a verdict" in result.results[0].summary
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
    slow_verifier = SlowClaimVerifier(delay_seconds=0.05, result=_result("Claim one", "pass", viewport))
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
    assert "Claim verification timed out" in result.results[0].summary


@pytest.mark.asyncio
async def test_runner_marks_remaining_claims_inconclusive_when_run_timeout_expires(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _import_runner_module()
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)
    slow_verifier = SlowClaimVerifier(delay_seconds=0.05, result=_result("Claim one", "pass", viewport))
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
    assert all("Run timed out" in item.summary for item in result.results)
