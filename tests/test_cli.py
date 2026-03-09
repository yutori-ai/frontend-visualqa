from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from frontend_visualqa.schemas import ViewportConfig


class FakeRunner:
    def __init__(self) -> None:
        self.run_calls: list[dict[str, Any]] = []
        self.screenshot_calls: list[dict[str, Any]] = []
        self.browser_calls: list[dict[str, Any]] = []
        self.close_calls = 0

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        self.run_calls.append(kwargs)
        return {
            "overall_status": "completed",
            "session_key": kwargs["session_key"],
            "results": [],
            "summary": "ok",
            "artifacts_dir": "artifacts/run-fake",
        }

    async def take_screenshot(self, **kwargs: Any) -> dict[str, Any]:
        self.screenshot_calls.append(kwargs)
        return {
            "session_key": kwargs["session_key"],
            "final_url": kwargs["url"],
            "viewport": kwargs["viewport"].model_dump(mode="json"),
            "screenshot_path": "artifacts/run-fake/screenshot.webp",
        }

    async def manage_browser(self, **kwargs: Any) -> dict[str, Any]:
        self.browser_calls.append(kwargs)
        return {"browser_running": False, "sessions": []}

    async def close(self) -> None:
        self.close_calls += 1


class FakeServer:
    def __init__(self) -> None:
        self.run_calls: list[str] = []

    def run(self, *, transport: str) -> None:
        self.run_calls.append(transport)


def test_handle_verify_closes_runner(monkeypatch: Any) -> None:
    import frontend_visualqa.cli as cli

    fake_runner = FakeRunner()
    emitted: list[dict[str, Any]] = []
    monkeypatch.setattr(cli, "_new_runner", lambda: fake_runner)
    monkeypatch.setattr(cli, "_emit_json", emitted.append)

    exit_code = cli._handle_verify(
        SimpleNamespace(
            url="http://localhost:3000/tasks/123",
            claims=["The modal title reads Edit Task"],
            width=1280,
            height=800,
            device_scale_factor=1.0,
            session_key="verify-session",
            reuse_session=True,
            reset_between_claims=True,
            max_steps_per_claim=4,
            claim_timeout_seconds=120.0,
            run_timeout_seconds=300.0,
            navigation_hint="Open the modal first.",
        )
    )

    assert exit_code == 0
    assert fake_runner.close_calls == 1
    assert fake_runner.run_calls[0]["viewport"] == ViewportConfig()
    assert emitted[0]["overall_status"] == "completed"


def test_handle_screenshot_closes_runner(monkeypatch: Any) -> None:
    import frontend_visualqa.cli as cli

    fake_runner = FakeRunner()
    emitted: list[dict[str, Any]] = []
    monkeypatch.setattr(cli, "_new_runner", lambda: fake_runner)
    monkeypatch.setattr(cli, "_emit_json", emitted.append)

    exit_code = cli._handle_screenshot(
        SimpleNamespace(
            url="http://localhost:3000/tasks/123",
            width=390,
            height=844,
            device_scale_factor=1.0,
            session_key="shot-session",
            reuse_session=False,
        )
    )

    assert exit_code == 0
    assert fake_runner.close_calls == 1
    assert fake_runner.screenshot_calls[0]["viewport"] == ViewportConfig(width=390, height=844, device_scale_factor=1.0)
    assert emitted[0]["final_url"] == "http://localhost:3000/tasks/123"


def test_handle_status_closes_runner(monkeypatch: Any) -> None:
    import frontend_visualqa.cli as cli

    fake_runner = FakeRunner()
    emitted: list[dict[str, Any]] = []
    monkeypatch.setattr(cli, "_new_runner", lambda: fake_runner)
    monkeypatch.setattr(cli, "_emit_json", emitted.append)

    exit_code = cli._handle_status(SimpleNamespace())

    assert exit_code == 0
    assert fake_runner.close_calls == 1
    assert fake_runner.browser_calls == [{"action": "status"}]
    assert emitted[0]["browser_running"] is False


def test_handle_serve_closes_cached_mcp_runners(monkeypatch: Any) -> None:
    import frontend_visualqa.cli as cli

    fake_server = FakeServer()
    closed: list[str] = []
    monkeypatch.setattr(cli, "get_mcp_server", lambda: fake_server)
    monkeypatch.setattr(cli, "close_runners_sync", lambda: closed.append("closed"))

    exit_code = cli._handle_serve(SimpleNamespace())

    assert exit_code == 0
    assert fake_server.run_calls == ["stdio"]
    assert closed == ["closed"]
