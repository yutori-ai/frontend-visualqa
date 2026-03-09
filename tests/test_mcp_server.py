from __future__ import annotations

import inspect
import json
from typing import Any

import pytest

from frontend_visualqa.schemas import BrowserStatusResult, ScreenshotResult, ViewportConfig


def _import_mcp_server_module():
    import importlib

    try:
        return importlib.import_module("frontend_visualqa.mcp_server")
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("frontend_visualqa"):
            pytest.skip("frontend_visualqa.mcp_server is not implemented in this worktree yet")
        raise


class FakeRunner:
    def __init__(self) -> None:
        self.run_calls: list[dict[str, Any]] = []
        self.run_request_calls: list[Any] = []
        self.screenshot_calls: list[dict[str, Any]] = []
        self.browser_calls: list[dict[str, Any]] = []
        self.browser_request_calls: list[Any] = []
        self.close_calls = 0

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        self.run_calls.append(kwargs)
        return {
            "overall_status": "completed",
            "session_key": kwargs.get("session_key", "default"),
            "results": [],
            "summary": "0 claims executed in fake runner",
            "artifacts_dir": "artifacts/run-fake",
        }

    async def run_request(self, request: Any) -> dict[str, Any]:
        self.run_request_calls.append(request)
        return {
            "overall_status": "completed",
            "session_key": request.session_key,
            "results": [],
            "summary": "0 claims executed in fake runner",
            "artifacts_dir": "artifacts/run-fake",
        }

    async def take_screenshot(self, **kwargs: Any) -> ScreenshotResult:
        self.screenshot_calls.append(kwargs)
        return ScreenshotResult(
            session_key=kwargs.get("session_key", "default"),
            final_url=kwargs["url"],
            viewport=kwargs.get("viewport", ViewportConfig()),
            screenshot_path="artifacts/run-fake/screenshot.webp",
        )

    async def manage_browser(self, **kwargs: Any) -> BrowserStatusResult:
        self.browser_calls.append(kwargs)
        return BrowserStatusResult(browser_running=True, sessions=[])

    async def manage_browser_request(self, request: Any) -> BrowserStatusResult:
        self.browser_request_calls.append(request)
        return BrowserStatusResult(browser_running=True, sessions=[])

    async def close(self) -> None:
        self.close_calls += 1


async def _call_tool(module: Any, tool_name: str, arguments: dict[str, Any]) -> Any:
    if hasattr(module, tool_name):
        target = getattr(module, tool_name)
        if inspect.iscoroutinefunction(target):
            return await target(**arguments)
        return target(**arguments)

    if hasattr(module, "mcp"):
        response = await module.mcp.call_tool(tool_name, arguments)
        if isinstance(response, list):
            assert response, "tool returned no content"
            return json.loads(response[0].text)
        return response

    raise AssertionError(f"Unable to call {tool_name}: module does not expose a direct handler or FastMCP instance")


def _install_fake_runner(module: Any, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "VisualQARunner", lambda *args, **kwargs: fake_runner, raising=False)
    monkeypatch.setattr(module, "runner", fake_runner, raising=False)
    monkeypatch.setattr(module, "_runner", fake_runner, raising=False)
    monkeypatch.setattr(module, "RUNNER", fake_runner, raising=False)
    if hasattr(module, "_runners_by_loop"):
        monkeypatch.setitem(module._runners_by_loop, module._loop_key(), fake_runner)
    if hasattr(module, "get_runner"):
        monkeypatch.setattr(module, "get_runner", lambda: fake_runner, raising=False)
    if hasattr(module, "_get_runner"):

        async def _return_runner() -> FakeRunner:
            return fake_runner

        monkeypatch.setattr(module, "_get_runner", _return_runner, raising=False)


@pytest.mark.asyncio
async def test_mcp_server_registers_expected_tools() -> None:
    module = _import_mcp_server_module()

    if hasattr(module, "mcp"):
        tools = await module.mcp.list_tools()
        tool_names = {tool.name for tool in tools}
    else:
        tool_names = {
            name for name in ("verify_visual_claims", "take_screenshot", "manage_browser") if hasattr(module, name)
        }

    assert {"verify_visual_claims", "take_screenshot", "manage_browser"} <= tool_names


@pytest.mark.asyncio
async def test_mcp_server_verify_visual_claims_delegates_to_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _import_mcp_server_module()
    fake_runner = FakeRunner()
    _install_fake_runner(module, fake_runner, monkeypatch)

    payload = {
        "url": "http://localhost:3000/tasks/123",
        "claims": ["The edit modal opens when clicking the task row"],
        "viewport": {"width": 1280, "height": 800, "device_scale_factor": 1},
        "session_key": "frontend-visualqa",
        "reuse_session": True,
        "reset_between_claims": True,
        "max_steps_per_claim": 4,
        "navigation_hint": "Open the first task row.",
    }
    result = await _call_tool(module, "verify_visual_claims", payload)

    assert fake_runner.run_request_calls
    forwarded = fake_runner.run_request_calls[0]
    assert forwarded.url == payload["url"]
    assert forwarded.claims == payload["claims"]
    assert result["overall_status"] == "completed"


@pytest.mark.asyncio
async def test_mcp_server_helpers_delegate_to_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _import_mcp_server_module()
    fake_runner = FakeRunner()
    _install_fake_runner(module, fake_runner, monkeypatch)

    screenshot_result = await _call_tool(
        module,
        "take_screenshot",
        {
            "url": "http://localhost:3000/tasks/123",
            "viewport": {"width": 390, "height": 844, "device_scale_factor": 1},
            "session_key": "mobile",
        },
    )
    browser_result = await _call_tool(
        module,
        "manage_browser",
        {
            "action": "status",
            "session_key": "mobile",
        },
    )

    assert fake_runner.screenshot_calls
    assert fake_runner.browser_request_calls
    assert screenshot_result["final_url"] == "http://localhost:3000/tasks/123"
    assert browser_result["browser_running"] is True


def test_close_runners_sync_closes_cached_runners(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _import_mcp_server_module()
    fake_runner = FakeRunner()
    module._runners_by_loop.clear()
    module._runner_locks_by_loop.clear()
    monkeypatch.setitem(module._runners_by_loop, 123, fake_runner)

    module.close_runners_sync()

    assert fake_runner.close_calls == 1
    assert module._runners_by_loop == {}
    assert module._runner_locks_by_loop == {}
