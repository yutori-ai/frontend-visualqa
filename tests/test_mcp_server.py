from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any

import pytest

from frontend_visualqa import __version__
from frontend_visualqa.schemas import (
    BrowserConfig,
    BrowserMode,
    BrowserStatusResult,
    ClaimResult,
    RunResult,
    ScreenshotResult,
    ViewportConfig,
)


def _sample_claim_result(*, url: str, viewport: ViewportConfig) -> ClaimResult:
    return ClaimResult(
        claim="The edit modal opens when clicking the task row",
        status="passed",
        finding="The modal is visible.",
        proof={
            "screenshot_path": "artifacts/run-fake/claim-01/step-01.webp",
            "step": 1,
            "after_action": "left_click([419, 348])",
            "text": None,
            "text_path": None,
        },
        page={"url": url, "viewport": viewport},
        trace={
            "steps_taken": 1,
            "wrong_page_recovered": False,
            "screenshot_paths": [
                "artifacts/run-fake/claim-01/step-00-initial.webp",
                "artifacts/run-fake/claim-01/step-01.webp",
            ],
            "actions": ["left_click([419, 348])"],
            "events": [],
            "trace_path": "artifacts/run-fake/claim-01/trace.json",
        },
    )


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

    async def run(self, **kwargs: Any) -> RunResult:
        self.run_calls.append(kwargs)
        viewport = kwargs.get("viewport", ViewportConfig())
        return RunResult(
            overall_status="completed",
            session_key=kwargs.get("session_key", "default"),
            results=[_sample_claim_result(url=kwargs["url"], viewport=viewport)],
            summary="1/1 claims passed.",
            artifacts_dir="artifacts/run-fake",
        )

    async def run_request(self, request: Any) -> RunResult:
        self.run_request_calls.append(request)
        return RunResult(
            overall_status="completed",
            session_key=request.session_key,
            results=[_sample_claim_result(url=request.url, viewport=request.viewport)],
            summary="1/1 claims passed.",
            artifacts_dir="artifacts/run-fake",
        )

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
    if hasattr(module, "mcp"):
        response = await module.mcp.call_tool(tool_name, arguments)
        if isinstance(response, tuple):
            assert response, "tool returned an empty tuple"
            response = response[0]
        if isinstance(response, list):
            assert response, "tool returned no content"
            return json.loads(response[0].text)
        return response

    if hasattr(module, tool_name):
        target = getattr(module, tool_name)
        if inspect.iscoroutinefunction(target):
            return await target(**arguments)
        return target(**arguments)

    raise AssertionError(f"Unable to call {tool_name}: module does not expose a direct handler or FastMCP instance")


def _assert_claim_result_payload_shape(result: dict[str, Any]) -> None:
    assert set(result) == {"claim", "status", "finding", "proof", "page", "trace"}

    proof = result["proof"]
    assert proof is not None
    assert set(proof) == {"screenshot_path", "step", "after_action", "text", "text_path"}

    page = result["page"]
    assert set(page) == {"url", "viewport"}
    assert set(page["viewport"]) == {"width", "height", "device_scale_factor"}

    trace = result["trace"]
    assert set(trace) == {"steps_taken", "wrong_page_recovered", "screenshot_paths", "actions", "events", "trace_path"}


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


def _reset_server_module_state(module: Any) -> None:
    module._runners_by_loop.clear()
    module._runner_locks_by_loop.clear()
    if hasattr(module, "_server_browser_config"):
        module._server_browser_config = None
    if hasattr(module, "_config_frozen"):
        module._config_frozen = False


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
        "visualize": True,
        "max_steps_per_claim": 4,
        "navigation_hint": "Open the first task row.",
    }
    result = await _call_tool(module, "verify_visual_claims", payload)

    assert fake_runner.run_request_calls
    forwarded = fake_runner.run_request_calls[0]
    assert forwarded.url == payload["url"]
    assert forwarded.claims == payload["claims"]
    assert forwarded.visualize is True
    assert result["overall_status"] == "completed"
    assert result["runner_version"] == __version__
    claim_result = result["results"][0]
    _assert_claim_result_payload_shape(claim_result)
    assert claim_result["finding"] == "The modal is visible."
    assert claim_result["proof"]["after_action"] == "left_click([419, 348])"
    assert claim_result["page"]["url"] == payload["url"]
    assert claim_result["trace"]["steps_taken"] == 1


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
    _reset_server_module_state(module)
    monkeypatch.setitem(module._runners_by_loop, 123, fake_runner)
    module._server_browser_config = BrowserConfig(mode=BrowserMode.persistent, user_data_dir="/tmp/profile")
    module._config_frozen = True

    module.close_runners_sync()

    assert fake_runner.close_calls == 1
    assert module._runners_by_loop == {}
    assert module._runner_locks_by_loop == {}
    assert module._server_browser_config is None
    assert module._config_frozen is False


@pytest.mark.asyncio
async def test_configure_server_passes_browser_config_to_new_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _import_mcp_server_module()
    _reset_server_module_state(module)
    fake_runner = FakeRunner()
    captured: dict[str, Any] = {}

    import frontend_visualqa.runner as runner_module

    def fake_visual_qa_runner(*, browser_config: BrowserConfig | None = None, **kwargs: Any) -> FakeRunner:
        del kwargs
        captured["browser_config"] = browser_config
        return fake_runner

    monkeypatch.setattr(runner_module, "VisualQARunner", fake_visual_qa_runner)

    browser_config = BrowserConfig(mode=BrowserMode.persistent, user_data_dir="/tmp/profile", headless=False)
    module.configure_server(browser_config)
    runner = await module._get_runner()

    assert runner is fake_runner
    assert captured["browser_config"] == browser_config
    assert module._config_frozen is True


@pytest.mark.asyncio
async def test_configure_server_rejects_changes_after_runner_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _import_mcp_server_module()
    _reset_server_module_state(module)
    fake_runner = FakeRunner()

    import frontend_visualqa.runner as runner_module

    monkeypatch.setattr(runner_module, "VisualQARunner", lambda **kwargs: fake_runner)

    await module._get_runner()

    with pytest.raises(RuntimeError, match="Cannot change browser config after runner has been created"):
        module.configure_server(BrowserConfig(mode=BrowserMode.persistent, user_data_dir="/tmp/profile"))


def test_close_runners_sync_resets_config_without_cached_runners() -> None:
    module = _import_mcp_server_module()
    _reset_server_module_state(module)
    module._server_browser_config = BrowserConfig(mode=BrowserMode.persistent, user_data_dir="/tmp/profile")

    module.close_runners_sync()

    assert module._server_browser_config is None
    assert module._config_frozen is False


@pytest.mark.asyncio
async def test_close_runners_sync_resets_state_immediately_inside_running_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _import_mcp_server_module()
    fake_runner = FakeRunner()
    _reset_server_module_state(module)
    monkeypatch.setitem(module._runners_by_loop, module._loop_key(), fake_runner)
    module._server_browser_config = BrowserConfig(mode=BrowserMode.persistent, user_data_dir="/tmp/profile")
    module._config_frozen = True

    module.close_runners_sync()

    assert module._runners_by_loop == {}
    assert module._runner_locks_by_loop == {}
    assert module._server_browser_config is None
    assert module._config_frozen is False

    await asyncio.sleep(0)

    assert fake_runner.close_calls == 1
