from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from frontend_visualqa import __version__
from frontend_visualqa.schemas import BrowserConfig, BrowserMode, BrowserStatusResult, ClaimResult, RunResult, ScreenshotResult, ViewportConfig


def _sample_claim_result(*, url: str, viewport: ViewportConfig) -> ClaimResult:
    return ClaimResult(
        claim="The modal title reads Edit Task",
        status="passed",
        finding="The modal title reads Edit Task.",
        proof={
            "screenshot_path": "artifacts/run-fake/claim-01/step-00-initial.webp",
            "step": 0,
            "after_action": None,
            "text": None,
            "text_path": None,
        },
        page={"url": url, "viewport": viewport},
        trace={
            "steps_taken": 0,
            "wrong_page_recovered": False,
            "screenshot_paths": ["artifacts/run-fake/claim-01/step-00-initial.webp"],
            "actions": [],
            "trace_path": None,
        },
    )


def _assert_claim_result_payload_shape(result: dict[str, Any]) -> None:
    assert set(result) == {"claim", "status", "finding", "proof", "page", "trace"}

    proof = result["proof"]
    assert proof is not None
    assert set(proof) == {"screenshot_path", "step", "after_action", "text", "text_path"}

    page = result["page"]
    assert set(page) == {"url", "viewport"}
    assert set(page["viewport"]) == {"width", "height", "device_scale_factor"}

    trace = result["trace"]
    assert set(trace) == {"steps_taken", "wrong_page_recovered", "screenshot_paths", "actions", "trace_path"}


class FakeRunner:
    def __init__(self) -> None:
        self.run_calls: list[dict[str, Any]] = []
        self.screenshot_calls: list[dict[str, Any]] = []
        self.browser_calls: list[dict[str, Any]] = []
        self.close_calls = 0

    async def run(self, **kwargs: Any) -> RunResult:
        self.run_calls.append(kwargs)
        viewport = kwargs.get("viewport", ViewportConfig())
        return RunResult(
            overall_status="completed",
            session_key=kwargs["session_key"],
            results=[_sample_claim_result(url=kwargs["url"], viewport=viewport)],
            summary="1/1 claims passed.",
            artifacts_dir="artifacts/run-fake",
        )

    async def take_screenshot(self, **kwargs: Any) -> ScreenshotResult:
        self.screenshot_calls.append(kwargs)
        return ScreenshotResult(
            session_key=kwargs["session_key"],
            final_url=kwargs["url"],
            viewport=kwargs["viewport"],
            screenshot_path="artifacts/run-fake/screenshot.webp",
        )

    async def manage_browser(self, **kwargs: Any) -> BrowserStatusResult:
        self.browser_calls.append(kwargs)
        return BrowserStatusResult(browser_running=False, sessions=[])

    async def close(self) -> None:
        self.close_calls += 1


class FakeServer:
    def __init__(self) -> None:
        self.run_calls: list[str] = []

    def run(self, *, transport: str) -> None:
        self.run_calls.append(transport)


class FakeContext:
    def __init__(self) -> None:
        self.events: dict[str, Any] = {}

    def on(self, event: str, handler: Any) -> None:
        self.events[event] = handler


class FakeBrowserSession:
    def __init__(self) -> None:
        self.context = FakeContext()


class FakeBrowserManager:
    def __init__(self, *, config: BrowserConfig) -> None:
        self.config = config
        self.get_session_calls: list[dict[str, Any]] = []
        self.goto_calls: list[tuple[Any, str]] = []
        self.close_calls = 0
        self.session = FakeBrowserSession()

    async def get_session(self, session_key: str = "default", *, reuse_session: bool = True, **_: Any) -> FakeBrowserSession:
        self.get_session_calls.append({"session_key": session_key, "reuse_session": reuse_session})
        return self.session

    async def goto(self, session: FakeBrowserSession, url: str) -> str:
        self.goto_calls.append((session, url))
        return url

    async def close(self) -> None:
        self.close_calls += 1


class ClosingFakeBrowserManager(FakeBrowserManager):
    async def goto(self, session: FakeBrowserSession, url: str) -> str:
        final_url = await super().goto(session, url)
        session.context.events["close"]()
        return final_url


def test_build_parser_supports_version_flag_without_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    import frontend_visualqa.cli as cli

    with pytest.raises(SystemExit) as exc_info:
        cli.build_parser().parse_args(["--version"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"frontend-visualqa {__version__}"


def test_handle_verify_closes_runner_and_forwards_browser_config(monkeypatch: Any) -> None:
    import frontend_visualqa.cli as cli

    fake_runner = FakeRunner()
    emitted: list[dict[str, Any]] = []
    browser_configs: list[BrowserConfig | None] = []

    def _fake_new_runner(*, browser_config=None, reporters=None):
        browser_configs.append(browser_config)
        return fake_runner

    monkeypatch.setattr(cli, "_new_runner", _fake_new_runner)
    monkeypatch.setattr(cli, "_emit_json", emitted.append)

    exit_code = cli._handle_verify(
        SimpleNamespace(
            url="http://localhost:3000/tasks/123",
            claims=["The modal title reads Edit Task"],
            width=1280,
            height=800,
            device_scale_factor=1.0,
            browser_mode="persistent",
            user_data_dir="/tmp/frontend-visualqa-profile",
            headed=True,
            session_key="verify-session",
            reuse_session=True,
            reset_between_claims=True,
            max_steps_per_claim=4,
            claim_timeout_seconds=120.0,
            run_timeout_seconds=300.0,
            navigation_hint="Open the modal first.",
            reporter=None,
        )
    )

    assert exit_code == 0
    assert fake_runner.close_calls == 1
    assert fake_runner.run_calls[0]["viewport"] == ViewportConfig()
    assert browser_configs == [
        BrowserConfig(
            mode=BrowserMode.persistent,
            user_data_dir="/tmp/frontend-visualqa-profile",
            headless=False,
            visualize=True,
        )
    ]
    assert emitted[0]["overall_status"] == "completed"
    assert emitted[0]["runner_version"] == "0.3.0"
    claim_result = emitted[0]["results"][0]
    _assert_claim_result_payload_shape(claim_result)
    assert claim_result["finding"] == "The modal title reads Edit Task."
    assert claim_result["proof"]["step"] == 0
    assert claim_result["page"]["url"] == "http://localhost:3000/tasks/123"
    assert claim_result["trace"]["screenshot_paths"] == ["artifacts/run-fake/claim-01/step-00-initial.webp"]


def test_handle_screenshot_closes_runner_and_forwards_browser_config(monkeypatch: Any) -> None:
    import frontend_visualqa.cli as cli

    fake_runner = FakeRunner()
    emitted: list[dict[str, Any]] = []
    browser_configs: list[BrowserConfig | None] = []

    def _fake_new_runner(*, browser_config: BrowserConfig | None = None) -> FakeRunner:
        browser_configs.append(browser_config)
        return fake_runner

    monkeypatch.setattr(cli, "_new_runner", _fake_new_runner)
    monkeypatch.setattr(cli, "_emit_json", emitted.append)

    exit_code = cli._handle_screenshot(
        SimpleNamespace(
            url="http://localhost:3000/tasks/123",
            width=390,
            height=844,
            device_scale_factor=1.0,
            browser_mode="ephemeral",
            user_data_dir=None,
            headed=False,
            session_key="shot-session",
            reuse_session=False,
        )
    )

    assert exit_code == 0
    assert fake_runner.close_calls == 1
    assert fake_runner.screenshot_calls[0]["viewport"] == ViewportConfig(width=390, height=844, device_scale_factor=1.0)
    assert browser_configs == [BrowserConfig(mode=BrowserMode.ephemeral, user_data_dir=None, headless=True)]
    assert emitted[0]["final_url"] == "http://localhost:3000/tasks/123"


def test_handle_status_closes_runner(monkeypatch: Any) -> None:
    import frontend_visualqa.cli as cli

    fake_runner = FakeRunner()
    emitted: list[dict[str, Any]] = []
    monkeypatch.setattr(cli, "_new_runner", lambda **_: fake_runner)
    monkeypatch.setattr(cli, "_emit_json", emitted.append)

    exit_code = cli._handle_status(SimpleNamespace())

    assert exit_code == 0
    assert fake_runner.close_calls == 1
    assert fake_runner.browser_calls == [{"action": "status"}]
    assert emitted[0]["browser_running"] is False


def test_handle_serve_configures_server_and_closes_cached_mcp_runners(monkeypatch: Any) -> None:
    import frontend_visualqa.cli as cli

    fake_server = FakeServer()
    configured: list[BrowserConfig] = []
    closed: list[str] = []
    monkeypatch.setattr(cli, "get_mcp_server", lambda: fake_server)
    monkeypatch.setattr(cli, "configure_server", configured.append)
    monkeypatch.setattr(cli, "close_runners_sync", lambda: closed.append("closed"))

    exit_code = cli._handle_serve(
        SimpleNamespace(
            browser_mode="persistent",
            user_data_dir="/tmp/frontend-visualqa-profile",
            headed=True,
        )
    )

    assert exit_code == 0
    assert configured == [
        BrowserConfig(
            mode=BrowserMode.persistent,
            user_data_dir="/tmp/frontend-visualqa-profile",
            headless=False,
            visualize=True,
        )
    ]
    assert fake_server.run_calls == ["stdio"]
    assert closed == ["closed"]


def test_handle_verify_passes_reporters_to_runner(monkeypatch: Any) -> None:
    import frontend_visualqa.cli as cli

    fake_runner = FakeRunner()
    emitted: list[dict[str, Any]] = []
    captured_reporters: list[list[str] | None] = []

    def _fake_new_runner(*, browser_config=None, reporters=None):
        captured_reporters.append(reporters)
        return fake_runner

    monkeypatch.setattr(cli, "_new_runner", _fake_new_runner)
    monkeypatch.setattr(cli, "_emit_json", emitted.append)

    exit_code = cli._handle_verify(
        SimpleNamespace(
            url="http://localhost:3000/tasks/123",
            claims=["The modal title reads Edit Task"],
            width=1280,
            height=800,
            device_scale_factor=1.0,
            browser_mode="ephemeral",
            user_data_dir=None,
            headed=False,
            session_key="default",
            reuse_session=True,
            reset_between_claims=True,
            max_steps_per_claim=12,
            claim_timeout_seconds=120.0,
            run_timeout_seconds=300.0,
            navigation_hint=None,
            reporter=["native", "ctrf"],
        )
    )

    assert exit_code == 0
    assert captured_reporters == [["native", "ctrf"]]


def test_verify_parser_supports_visualize_flags_and_headed_default() -> None:
    from frontend_visualqa.cli import _build_browser_config, build_parser

    parser = build_parser()

    headed_args = parser.parse_args(["verify", "http://localhost:3000", "--claims", "test", "--headed"])
    assert headed_args.visualize is None
    assert _build_browser_config(headed_args).visualize is True

    explicit_true_args = parser.parse_args([
        "verify",
        "http://localhost:3000",
        "--claims",
        "test",
        "--visualize",
    ])
    assert explicit_true_args.visualize is True
    assert _build_browser_config(explicit_true_args).visualize is True

    explicit_false_args = parser.parse_args([
        "verify",
        "http://localhost:3000",
        "--claims",
        "test",
        "--headed",
        "--no-visualize",
    ])
    assert explicit_false_args.visualize is False
    assert _build_browser_config(explicit_false_args).visualize is False


def test_handle_login_requires_tty(monkeypatch: Any, capsys: pytest.CaptureFixture[str]) -> None:
    import frontend_visualqa.cli as cli

    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: False))

    exit_code = cli._handle_login(SimpleNamespace(url="http://localhost:3000/login", user_data_dir=None))

    assert exit_code == 1
    assert "login requires an interactive terminal" in capsys.readouterr().err


def test_verify_rejects_invalid_reporter_at_parse_time(capsys: pytest.CaptureFixture[str]) -> None:
    from frontend_visualqa.cli import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit, match="2"):
        parser.parse_args(["verify", "http://localhost:3000", "--claims", "x", "--reporter", "bogus"])
    assert "invalid choice" in capsys.readouterr().err


def test_handle_login_rejects_invalid_url(monkeypatch: Any, capsys: pytest.CaptureFixture[str]) -> None:
    import frontend_visualqa.cli as cli

    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: True))

    exit_code = cli._handle_login(SimpleNamespace(url="localhost:3000/login", user_data_dir=None))

    assert exit_code == 1
    assert "url must start with http:// or https://" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_run_login_opens_headed_persistent_browser_and_saves_profile(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import frontend_visualqa.cli as cli

    created_managers: list[FakeBrowserManager] = []

    def _fake_browser_manager(*, config: BrowserConfig) -> FakeBrowserManager:
        manager = FakeBrowserManager(config=config)
        created_managers.append(manager)
        return manager

    async def _noop_sleep(_: float) -> None:
        return None

    fake_stdin = SimpleNamespace(readline=lambda: "\n", isatty=lambda: True)
    monkeypatch.setattr(cli, "BrowserManager", _fake_browser_manager)
    monkeypatch.setattr(cli.sys, "stdin", fake_stdin)
    monkeypatch.setattr(cli.asyncio, "sleep", _noop_sleep)

    exit_code = await cli._run_login(SimpleNamespace(url="http://localhost:3000/login", user_data_dir="/tmp/profile"))

    assert exit_code == 0
    assert len(created_managers) == 1
    manager = created_managers[0]
    assert manager.config == BrowserConfig(
        mode=BrowserMode.persistent,
        user_data_dir="/tmp/profile",
        headless=False,
        visualize=True,
    )
    assert manager.get_session_calls == [{"session_key": "default", "reuse_session": False}]
    assert manager.goto_calls[0][1] == "http://localhost:3000/login"
    assert manager.close_calls == 1
    assert "Browser is open. Log in, then press Enter here to close and save the session." in capsys.readouterr().err


@pytest.mark.asyncio
async def test_run_login_exits_cleanly_when_browser_window_closes_first(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import frontend_visualqa.cli as cli

    created_managers: list[ClosingFakeBrowserManager] = []

    def _fake_browser_manager(*, config: BrowserConfig) -> ClosingFakeBrowserManager:
        manager = ClosingFakeBrowserManager(config=config)
        created_managers.append(manager)
        return manager

    async def _noop_sleep(_: float) -> None:
        return None

    fake_stdin = SimpleNamespace(readline=lambda: "", isatty=lambda: True)
    monkeypatch.setattr(cli, "BrowserManager", _fake_browser_manager)
    monkeypatch.setattr(cli.sys, "stdin", fake_stdin)
    monkeypatch.setattr(cli.asyncio, "sleep", _noop_sleep)

    exit_code = await cli._run_login(SimpleNamespace(url="http://localhost:3000/login", user_data_dir="/tmp/profile"))

    assert exit_code == 0
    assert len(created_managers) == 1
    manager = created_managers[0]
    assert manager.close_calls == 1  # manager.close() called to stop Playwright subprocess
    stderr = capsys.readouterr().err
    assert "Browser is open. Log in, then press Enter here to close and save the session." in stderr
    assert "Browser closed." in stderr
