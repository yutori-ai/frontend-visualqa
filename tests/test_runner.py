from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from frontend_visualqa.claim_parser import parse_claims_file
from fakes import FakeArtifactManager, FakeN1Client, instantiate_with_supported_kwargs, is_bootstrap_step_artifact

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
    def __init__(self, viewport: ViewportConfig, *, config: BrowserConfig | None = None) -> None:
        self.viewport = viewport
        self.config = config or BrowserConfig()
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
            browser_mode=self.config.mode,
            user_data_dir=self.config.resolved_user_data_dir if self.config.mode == BrowserMode.persistent else None,
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
        self.browser_manager: Any | None = None
        self._visualize = False
        self.set_browser_manager_calls: list[dict[str, Any]] = []

    def set_browser_manager(self, browser_manager: Any, *, visualize: bool | None = None) -> None:
        self.browser_manager = browser_manager
        self.set_browser_manager_calls.append(
            {
                "browser_manager": browser_manager,
                "visualize": visualize,
            }
        )
        if visualize is not None:
            self._visualize = visualize

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
    reporters: list[str] | None = None,
) -> tuple[Any, FakeBrowserManager, FakeClaimVerifier]:
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)
    browser = browser_manager or FakeBrowserManager(viewport)
    verifier = claim_verifier or FakeClaimVerifier(verifier_results)
    artifacts = FakeArtifactManager(tmp_path, run_id="run-001")

    monkeypatch.setattr(module, "BrowserManager", lambda *args, **kwargs: browser, raising=False)
    monkeypatch.setattr(module, "ClaimVerifier", lambda *args, **kwargs: verifier, raising=False)
    monkeypatch.setattr(module, "ArtifactManager", lambda *args, **kwargs: artifacts, raising=False)
    monkeypatch.setattr(module, "N1Client", lambda *args, **kwargs: FakeN1Client([]), raising=False)

    runner = instantiate_with_supported_kwargs(
        module.VisualQARunner,
        browser_manager=browser,
        browser=browser,
        claim_verifier=verifier,
        verifier=verifier,
        artifact_manager=artifacts,
        artifacts=artifacts,
        reporters=reporters,
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
    if hasattr(verifier, "browser_manager"):
        verifier.browser_manager = browser

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
    url: str | None = None,
) -> Any:
    signature = inspect.signature(runner.manage_browser)
    filtered: dict[str, Any] = {}

    if "action" in signature.parameters:
        filtered["action"] = action
    if "session_key" in signature.parameters:
        filtered["session_key"] = session_key
    if "viewport" in signature.parameters:
        filtered["viewport"] = viewport
    if "url" in signature.parameters:
        filtered["url"] = url

    input_names = {"request", "input", "payload", "manage_input"}
    target_name = next((name for name in signature.parameters if name in input_names), None)
    if target_name is not None:
        filtered[target_name] = ManageBrowserInput(
            action=action,
            session_key=session_key,
            viewport=viewport,
            url=url,
        )

    return await runner.manage_browser(**filtered)


def _result(name: str, status: str, viewport: ViewportConfig) -> ClaimResult:
    return ClaimResult(
        claim=name,
        status=status,
        finding=f"{name}: {status}",
        proof={
            "screenshot_path": "artifacts/run-001/claim-01/step-01.webp",
            "step": 1,
            "after_action": 'goto_url("http://fixture.local/claim-status")',
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
            "actions": ['goto_url("http://fixture.local/claim-status")'],
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
async def test_runner_run_uses_per_claim_navigation_hints_and_falls_back_to_global_hint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _import_runner_module()
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)
    runner, _, verifier = _build_runner(
        module,
        tmp_path,
        verifier_results=[_result("Claim one", "passed", viewport), _result("Claim two", "passed", viewport)],
        monkeypatch=monkeypatch,
    )

    result = await _call_run(
        runner,
        url="http://fixture.local/page",
        claims=["Claim one", "Claim two"],
        claim_navigation_hints=["Open the modal first.", None],
        viewport=viewport,
        session_key="qa-session",
        reuse_session=True,
        reset_between_claims=True,
        max_steps_per_claim=5,
        navigation_hint="Open the login page if needed.",
    )

    assert [item.status for item in result.results] == ["passed", "passed"]
    assert verifier.calls[0]["navigation_hint"] == "Open the modal first."
    assert verifier.calls[1]["navigation_hint"] == "Open the login page if needed."


@pytest.mark.asyncio
async def test_runner_run_uses_second_claim_navigation_hint_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _import_runner_module()
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)
    runner, _, verifier = _build_runner(
        module,
        tmp_path,
        verifier_results=[_result("Claim one", "passed", viewport), _result("Claim two", "passed", viewport)],
        monkeypatch=monkeypatch,
    )

    result = await _call_run(
        runner,
        url="http://fixture.local/page",
        claims=["Claim one", "Claim two"],
        claim_navigation_hints=[None, "Scroll to the quota card before judging."],
        viewport=viewport,
        session_key="qa-session",
        reuse_session=True,
        reset_between_claims=True,
        max_steps_per_claim=5,
        navigation_hint="Open the login page if needed.",
    )

    assert [item.status for item in result.results] == ["passed", "passed"]
    assert verifier.calls[0]["navigation_hint"] == "Open the login page if needed."
    assert verifier.calls[1]["navigation_hint"] == "Scroll to the quota card before judging."


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
        claim_navigation_hints=["Open the modal if needed."],
        navigation_hint="Open the modal if needed.",
    )

    result = await runner.run_request(request)

    assert result.overall_status == "completed"
    assert result.run_name == "modal-check"
    assert [item.status for item in result.results] == ["passed"]
    assert browser.goto_calls[0] == ("qa-session", "http://fixture.local/page")
    assert verifier.calls[0]["navigation_hint"] == "Open the modal if needed."
    assert verifier.calls[0]["visualize"] is True


def test_verify_visual_claims_input_requires_navigation_hint_alignment() -> None:
    with pytest.raises(ValueError, match="claim_navigation_hints must match claims length"):
        VerifyVisualClaimsInput(
            url="http://fixture.local/page",
            claims=["Claim one", "Claim two"],
            claim_navigation_hints=["Only one hint"],
        )


@pytest.mark.asyncio
async def test_runner_ignores_callback_exceptions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _import_runner_module()
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)
    runner, _, _ = _build_runner(
        module,
        tmp_path,
        verifier_results=[_result("Claim one", "passed", viewport)],
        monkeypatch=monkeypatch,
    )

    def _boom(*_: Any) -> None:
        raise RuntimeError("progress renderer failed")

    result = await _call_run(
        runner,
        url="http://fixture.local/page",
        claims=["Claim one"],
        viewport=viewport,
        session_key="qa-session",
        reuse_session=True,
        reset_between_claims=True,
        max_steps_per_claim=5,
        on_claim_start=_boom,
        on_claim_complete=_boom,
    )

    assert [item.status for item in result.results] == ["passed"]


def test_runner_parses_claims_file_and_ignores_nested_or_fenced_content(tmp_path: Path) -> None:
    claims_file = tmp_path / "claims.md"
    claims_file.write_text(
        """# Dashboard checks

- The heading reads 'Dashboard'
  - nested note should be ignored
* [x] The progress bar shows 100%

```markdown
- This line lives in code and should be ignored
```

Some prose that should be ignored too.
""",
        encoding="utf-8",
    )

    parsed = parse_claims_file(claims_file)

    assert parsed.claims == ["The heading reads 'Dashboard'", "The progress bar shows 100%"]
    assert [line.line_index for line in parsed.lines] == [2, 4]
    assert parsed.lines[1].bullet == "*"
    assert parsed.lines[1].claim == "The progress bar shows 100%"


@pytest.mark.asyncio
async def test_runner_writes_rerunnable_markdown_report_from_claims_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _import_runner_module()
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)
    claims_file = tmp_path / "claims.md"
    claims_file.write_text(
        """# Dashboard checks

- The heading reads 'Dashboard'
- The progress bar shows 100%
""",
        encoding="utf-8",
    )
    parsed = parse_claims_file(claims_file)
    runner, _, _ = _build_runner(
        module,
        tmp_path,
        verifier_results=[
            _result("The heading reads 'Dashboard'", "passed", viewport),
            _result("The progress bar shows 100%", "failed", viewport),
        ],
        monkeypatch=monkeypatch,
        reporters=["markdown"],
    )

    result = await _call_run(
        runner,
        url="http://fixture.local/page",
        claims=parsed.claims,
        claims_file=parsed,
        viewport=viewport,
        session_key="qa-session",
        reuse_session=True,
        reset_between_claims=True,
        max_steps_per_claim=5,
    )

    report_path = Path(result.artifacts_dir) / "report.md"
    report_text = report_path.read_text(encoding="utf-8")

    assert report_path.exists()
    assert "- [x] The heading reads 'Dashboard'" in report_text
    assert "- [ ] The progress bar shows 100%" in report_text
    assert "Status: failed" in report_text
    assert "Finding: The progress bar shows 100%: failed" in report_text
    assert parse_claims_file(report_path).claims == parsed.claims


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
    assert status.summary == "Reported shared browser status."

    resized_status = await _call_manage_browser(runner, action="set_viewport", session_key="managed", viewport=viewport)
    assert resized_status.sessions[0].viewport == viewport
    assert resized_status.summary == "Updated the viewport for shared browser session 'managed'."

    restarted_status = await _call_manage_browser(runner, action="restart", session_key="managed", viewport=viewport)
    assert "managed" in browser.restart_calls
    assert restarted_status.sessions[0].session_key == "managed"
    assert restarted_status.summary == "Restarted shared browser session 'managed'."

    closed_status = await _call_manage_browser(runner, action="close", session_key="managed")
    assert "managed" in browser.closed_sessions or browser.closed is True
    assert closed_status.browser_running in {True, False}
    assert closed_status.summary == "Closed shared browser session 'managed'."


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


@pytest.mark.asyncio
async def test_runner_manage_browser_login_reconfigures_to_persistent_headed_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _import_runner_module()
    initial_browser = FakeBrowserManager(ViewportConfig(), config=BrowserConfig())
    replacement_browsers: list[FakeBrowserManager] = []

    def _browser_factory(*args: Any, **kwargs: Any) -> FakeBrowserManager:
        del args
        config = kwargs["config"]
        browser = FakeBrowserManager(ViewportConfig(), config=config)
        replacement_browsers.append(browser)
        return browser

    runner, browser, verifier = _build_runner(
        module,
        tmp_path,
        verifier_results=[],
        monkeypatch=monkeypatch,
        browser_manager=initial_browser,
    )
    monkeypatch.setattr(module, "BrowserManager", _browser_factory)

    status = await _call_manage_browser(
        runner,
        action="login",
        session_key="auth",
        url="http://localhost:3000/sign-in",
    )

    assert browser.closed is True
    assert len(replacement_browsers) == 1
    login_browser = replacement_browsers[0]
    assert login_browser.config.mode == BrowserMode.persistent
    assert login_browser.config.headless is False
    assert login_browser.goto_calls == [("auth", "http://localhost:3000/sign-in")]
    assert status.browser_mode == BrowserMode.persistent
    assert status.sessions[0].current_url == "http://localhost:3000/sign-in"
    assert "interactive login" in (status.summary or "")
    assert runner.browser_manager is login_browser
    assert verifier.browser_manager is login_browser
    assert verifier.set_browser_manager_calls == [
        {
            "browser_manager": login_browser,
            "visualize": False,
        }
    ]


@pytest.mark.asyncio
async def test_runner_manage_browser_close_restores_base_config_after_login_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _import_runner_module()
    initial_browser = FakeBrowserManager(ViewportConfig(), config=BrowserConfig())
    replacement_browsers: list[FakeBrowserManager] = []

    def _browser_factory(*args: Any, **kwargs: Any) -> FakeBrowserManager:
        del args
        browser = FakeBrowserManager(ViewportConfig(), config=kwargs["config"])
        replacement_browsers.append(browser)
        return browser

    runner, _, verifier = _build_runner(
        module,
        tmp_path,
        verifier_results=[],
        monkeypatch=monkeypatch,
        browser_manager=initial_browser,
    )
    monkeypatch.setattr(module, "BrowserManager", _browser_factory)

    await _call_manage_browser(
        runner,
        action="login",
        session_key="auth",
        url="http://localhost:3000/sign-in",
    )
    closed_status = await _call_manage_browser(runner, action="close", session_key="auth")

    assert len(replacement_browsers) == 2
    restored_browser = replacement_browsers[1]
    assert runner.browser_manager is restored_browser
    assert restored_browser.config == BrowserConfig()
    assert closed_status.browser_mode == BrowserMode.ephemeral
    assert closed_status.summary == (
        "Closed shared browser session 'auth'. Restored the shared browser to its original configuration."
    )
    assert verifier.browser_manager is restored_browser


@pytest.mark.asyncio
async def test_runner_manage_browser_login_rejects_missing_url_even_if_validation_was_bypassed(
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

    with pytest.raises(ValueError, match="url is required when action is 'login'"):
        await runner.manage_browser_request(
            ManageBrowserInput.model_construct(action="login", session_key="auth", viewport=None, url=None)
        )


@pytest.mark.asyncio
async def test_runner_manage_browser_login_skips_reconfiguration_when_already_persistent_headed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling login when the browser is already persistent+headed should skip reconfiguration."""
    module = _import_runner_module()
    persistent_config = BrowserConfig(mode=BrowserMode.persistent, headless=False)
    initial_browser = FakeBrowserManager(ViewportConfig(), config=persistent_config)

    runner, browser, _ = _build_runner(
        module,
        tmp_path,
        verifier_results=[],
        monkeypatch=monkeypatch,
        browser_manager=initial_browser,
    )

    status = await _call_manage_browser(
        runner,
        action="login",
        session_key="auth",
        url="http://localhost:3000/sign-in",
    )

    assert browser.closed is False
    assert runner.browser_manager is browser
    assert browser.goto_calls == [("auth", "http://localhost:3000/sign-in")]
    assert "interactive login" in (status.summary or "")


@pytest.mark.asyncio
async def test_runner_manage_browser_close_skips_restore_when_other_sessions_exist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Close should not restore browser config when other sessions are still open."""
    module = _import_runner_module()
    initial_browser = FakeBrowserManager(ViewportConfig(), config=BrowserConfig())
    replacement_browsers: list[FakeBrowserManager] = []

    def _browser_factory(*args: Any, **kwargs: Any) -> FakeBrowserManager:
        del args
        browser = FakeBrowserManager(ViewportConfig(), config=kwargs["config"])
        replacement_browsers.append(browser)
        return browser

    runner, _, _ = _build_runner(
        module,
        tmp_path,
        verifier_results=[],
        monkeypatch=monkeypatch,
        browser_manager=initial_browser,
    )
    monkeypatch.setattr(module, "BrowserManager", _browser_factory)

    await _call_manage_browser(
        runner,
        action="login",
        session_key="auth",
        url="http://localhost:3000/sign-in",
    )

    login_browser = replacement_browsers[0]
    await login_browser.get_session("other", viewport=ViewportConfig(), reuse_session=True)

    closed_status = await _call_manage_browser(runner, action="close", session_key="auth")

    assert len(replacement_browsers) == 1
    assert runner.browser_manager is login_browser
    assert login_browser.config.mode == BrowserMode.persistent
    assert "other session(s) are still open" in (closed_status.summary or "")


def test_manage_browser_input_schema_rejects_login_without_url() -> None:
    """Pydantic validation should reject action='login' with no url."""
    with pytest.raises(Exception, match="url is required when action is 'login'"):
        ManageBrowserInput(action="login", session_key="auth")


def test_manage_browser_input_schema_rejects_login_with_invalid_url() -> None:
    """Pydantic validation should reject action='login' with a non-http url."""
    with pytest.raises(Exception, match="url must start with http"):
        ManageBrowserInput(action="login", session_key="auth", url="ftp://example.com")


class GotoFailingBrowserManager(FakeBrowserManager):
    """Browser manager whose goto() always raises after the session is created."""

    async def goto(self, session: FakeSession, url: str) -> str:
        self.goto_calls.append((session.session_key, url))
        raise RuntimeError("Chromium cannot connect on headless host")


@pytest.mark.asyncio
async def test_runner_manage_browser_login_returns_structured_error_when_navigation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If get_session/goto fails after reconfiguration, return a summary instead of crashing."""
    module = _import_runner_module()
    initial_browser = FakeBrowserManager(ViewportConfig(), config=BrowserConfig())

    def _browser_factory(*args: Any, **kwargs: Any) -> GotoFailingBrowserManager:
        del args
        return GotoFailingBrowserManager(ViewportConfig(), config=kwargs["config"])

    runner, _, _ = _build_runner(
        module,
        tmp_path,
        verifier_results=[],
        monkeypatch=monkeypatch,
        browser_manager=initial_browser,
    )
    monkeypatch.setattr(module, "BrowserManager", _browser_factory)

    status = await _call_manage_browser(
        runner,
        action="login",
        session_key="auth",
        url="http://localhost:3000/sign-in",
    )

    assert "failed to navigate" in (status.summary or "").lower()
    assert "persistent headed mode" in (status.summary or "").lower()


@pytest.mark.asyncio
async def test_runner_manage_browser_login_rolls_back_when_browser_constructor_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If BrowserManager() constructor fails, the runner should roll back to the old config."""
    module = _import_runner_module()
    initial_browser = FakeBrowserManager(ViewportConfig(), config=BrowserConfig())
    construction_attempts = []

    def _failing_browser_factory(*args: Any, **kwargs: Any) -> FakeBrowserManager:
        del args
        construction_attempts.append(kwargs.get("config"))
        if len(construction_attempts) == 1:
            raise RuntimeError("Chromium binary not found")
        return FakeBrowserManager(ViewportConfig(), config=kwargs["config"])

    runner, browser, verifier = _build_runner(
        module,
        tmp_path,
        verifier_results=[],
        monkeypatch=monkeypatch,
        browser_manager=initial_browser,
    )
    monkeypatch.setattr(module, "BrowserManager", _failing_browser_factory)

    with pytest.raises(RuntimeError, match="Chromium binary not found"):
        await _call_manage_browser(
            runner,
            action="login",
            session_key="auth",
            url="http://localhost:3000/sign-in",
        )

    assert len(construction_attempts) == 2
    assert construction_attempts[0].mode == BrowserMode.persistent
    assert construction_attempts[1] == BrowserConfig()
    assert runner.browser_manager is not browser
    assert runner.browser_manager.config == BrowserConfig()


@pytest.mark.asyncio
async def test_runner_login_then_take_screenshot_reuses_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After login, take_screenshot on the same session_key should reuse the login browser."""
    module = _import_runner_module()
    initial_browser = FakeBrowserManager(ViewportConfig(), config=BrowserConfig())
    replacement_browsers: list[FakeBrowserManager] = []

    def _browser_factory(*args: Any, **kwargs: Any) -> FakeBrowserManager:
        del args
        browser = FakeBrowserManager(ViewportConfig(), config=kwargs["config"])
        replacement_browsers.append(browser)
        return browser

    runner, _, _ = _build_runner(
        module,
        tmp_path,
        verifier_results=[],
        monkeypatch=monkeypatch,
        browser_manager=initial_browser,
    )
    monkeypatch.setattr(module, "BrowserManager", _browser_factory)

    await _call_manage_browser(
        runner,
        action="login",
        session_key="dev",
        url="http://localhost:8000/yutori_login.html",
    )

    login_browser = replacement_browsers[0]
    screenshot_result = await runner.take_screenshot(
        url="http://localhost:8000/yutori_login.html",
        session_key="dev",
        reuse_session=True,
    )

    assert screenshot_result.status == "completed"
    assert screenshot_result.session_key == "dev"
    assert runner.browser_manager is login_browser
    assert any("dev" in str(call) for call in login_browser.goto_calls)


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
    events: list[tuple[str, int, str, str | None]] = []

    result = await _call_run(
        runner,
        url="http://fixture.local/page",
        claims=["Claim one", "Claim two"],
        viewport=viewport,
        session_key="qa-session",
        reuse_session=True,
        reset_between_claims=True,
        max_steps_per_claim=5,
        on_claim_start=lambda index, claim: events.append(("start", index, claim, None)),
        on_claim_complete=lambda index, claim, claim_result: events.append(
            ("complete", index, claim, claim_result.status)
        ),
    )

    assert [item.status for item in result.results] == ["passed", "not_testable"]
    assert "Could not prepare browser state" in result.results[1].finding
    assert len(verifier.calls) == 1
    assert events == [
        ("start", 1, "Claim one", None),
        ("complete", 1, "Claim one", "passed"),
        ("start", 2, "Claim two", None),
        ("complete", 2, "Claim two", "not_testable"),
    ]


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
    events: list[tuple[str, int, str, str | None]] = []

    result = await _call_run(
        runner,
        url="http://fixture.local/page",
        claims=["Claim one"],
        viewport=viewport,
        session_key="qa-session",
        reuse_session=True,
        reset_between_claims=True,
        max_steps_per_claim=5,
        on_claim_start=lambda index, claim: events.append(("start", index, claim, None)),
        on_claim_complete=lambda index, claim, claim_result: events.append(
            ("complete", index, claim, claim_result.status)
        ),
    )

    assert [item.status for item in result.results] == ["inconclusive"]
    assert "Verification crashed unexpectedly before returning a verdict" in result.results[0].finding
    assert exploding_verifier.calls
    assert events == [
        ("start", 1, "Claim one", None),
        ("complete", 1, "Claim one", "inconclusive"),
    ]


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
    events: list[tuple[str, int, str, str | None]] = []

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
        on_claim_start=lambda index, claim: events.append(("start", index, claim, None)),
        on_claim_complete=lambda index, claim, claim_result: events.append(
            ("complete", index, claim, claim_result.status)
        ),
    )

    assert [item.status for item in result.results] == ["inconclusive"]
    assert "Claim verification timed out" in result.results[0].finding
    assert events == [
        ("start", 1, "Claim one", None),
        ("complete", 1, "Claim one", "inconclusive"),
    ]


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
    events: list[tuple[str, int, str, str | None]] = []

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
        on_claim_start=lambda index, claim: events.append(("start", index, claim, None)),
        on_claim_complete=lambda index, claim, claim_result: events.append(
            ("complete", index, claim, claim_result.status)
        ),
    )

    assert [item.status for item in result.results] == ["inconclusive"]
    assert result.results[0].finding == "Claim verification timed out before a verdict was recorded."
    assert events == [
        ("start", 1, "Claim one", None),
        ("complete", 1, "Claim one", "inconclusive"),
    ]


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
    events: list[tuple[str, int, str, str | None]] = []

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
        on_claim_start=lambda index, claim: events.append(("start", index, claim, None)),
        on_claim_complete=lambda index, claim, claim_result: events.append(
            ("complete", index, claim, claim_result.status)
        ),
    )

    assert [item.status for item in result.results] == ["inconclusive"]
    assert result.results[0].proof is not None
    assert is_bootstrap_step_artifact(result.results[0].proof.screenshot_path)
    assert result.results[0].trace.steps_taken == 1
    assert result.results[0].trace.actions == ["scroll(direction='down', amount=300)"]
    assert events == [
        ("start", 1, "Claim one", None),
        ("complete", 1, "Claim one", "inconclusive"),
    ]


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
            "after_action": 'goto_url("http://fixture.local/dashboard")',
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
            "actions": ['goto_url("http://fixture.local/dashboard")'],
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
    events: list[tuple[str, int, str, str | None]] = []

    result = await _call_run(
        runner,
        url="http://fixture.local/page",
        claims=["Claim one"],
        viewport=viewport,
        session_key="qa-session",
        reuse_session=True,
        reset_between_claims=True,
        max_steps_per_claim=5,
        on_claim_start=lambda index, claim: events.append(("start", index, claim, None)),
        on_claim_complete=lambda index, claim, claim_result: events.append(
            ("complete", index, claim, claim_result.status)
        ),
    )

    assert [item.status for item in result.results] == ["inconclusive"]
    assert result.results[0].proof is not None
    assert result.results[0].proof.after_action == 'goto_url("http://fixture.local/dashboard")'
    assert result.results[0].trace.trace_path == "artifacts/run-001/claim-01/trace.json"
    assert events == [
        ("start", 1, "Claim one", None),
        ("complete", 1, "Claim one", "inconclusive"),
    ]


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
    events: list[tuple[str, int, str, str | None]] = []

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
        on_claim_start=lambda index, claim: events.append(("start", index, claim, None)),
        on_claim_complete=lambda index, claim, claim_result: events.append(
            ("complete", index, claim, claim_result.status)
        ),
    )

    assert [item.status for item in result.results] == ["inconclusive", "inconclusive"]
    assert all("Run timed out" in item.finding for item in result.results)
    assert events == [
        ("start", 1, "Claim one", None),
        ("complete", 1, "Claim one", "inconclusive"),
        ("start", 2, "Claim two", None),
        ("complete", 2, "Claim two", "inconclusive"),
    ]


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
            "after_action": 'goto_url("http://fixture.local/dashboard")',
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
            "actions": ['goto_url("http://fixture.local/dashboard")'],
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
    events: list[tuple[str, int, str, str | None]] = []

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
        on_claim_start=lambda index, claim: events.append(("start", index, claim, None)),
        on_claim_complete=lambda index, claim, claim_result: events.append(
            ("complete", index, claim, claim_result.status)
        ),
    )

    assert [item.status for item in result.results] == ["inconclusive", "inconclusive"]
    assert result.results[0].proof is not None
    assert result.results[0].proof.after_action == 'goto_url("http://fixture.local/dashboard")'
    assert result.results[0].trace.steps_taken == 1
    assert result.results[1].proof is None
    assert result.results[1].trace.steps_taken == 0
    assert events == [
        ("start", 1, "Claim one", None),
        ("complete", 1, "Claim one", "inconclusive"),
        ("start", 2, "Claim two", None),
        ("complete", 2, "Claim two", "inconclusive"),
    ]


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
    monkeypatch.setattr(module, "N1Client", lambda *args, **kwargs: FakeN1Client([]), raising=False)

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
        n1_client=FakeN1Client([]),
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
    verifier = FakeClaimVerifier(
        [
            _result("Claim one", "passed", viewport),
            _result("Claim two", "passed", viewport),
        ]
    )
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
        n1_client=FakeN1Client([]),
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
        self.write_calls: list[tuple[Any, Path, Any | None]] = []

    @property
    def name(self) -> str:
        return "spy"

    def write(self, run_result: Any, output_dir: Path, *, claims_file: Any | None = None) -> None:
        self.write_calls.append((run_result, output_dir, claims_file))


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
    written_result, written_dir, written_claims_file = spy.write_calls[0]
    assert written_result.overall_status == "completed"
    assert str(written_dir) == result.artifacts_dir
    assert written_claims_file is None


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
    assert set(first_result["trace"]) == {
        "steps_taken",
        "wrong_page_recovered",
        "screenshot_paths",
        "actions",
        "trace_path",
    }
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
