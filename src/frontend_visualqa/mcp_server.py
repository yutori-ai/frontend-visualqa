"""FastMCP adapter for frontend-visualqa."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from frontend_visualqa.schemas import BrowserConfig, ManageBrowserInput, VerifyVisualClaimsInput, ViewportConfig


logger = logging.getLogger(__name__)

SERVER_INSTRUCTIONS = (
    "Use verify_visual_claims for explicit, observable frontend claims. "
    "Use take_screenshot when you need a quick visual baseline before writing claims. "
    "Use manage_browser to inspect or reset browser state. "
    "Do not expect this server to start the local frontend for you."
)

mcp = FastMCP("frontend-visualqa", instructions=SERVER_INSTRUCTIONS, log_level="ERROR")

_runners_by_loop: dict[int, Any] = {}
_runner_locks_by_loop: dict[int, asyncio.Lock] = {}
_server_browser_config: BrowserConfig | None = None
_config_frozen = False


def get_mcp_server() -> FastMCP:
    """Return the configured FastMCP server instance."""

    return mcp


def configure_server(browser_config: BrowserConfig) -> None:
    """Set the browser config for future MCP runner construction."""

    global _server_browser_config, _config_frozen
    if _config_frozen:
        raise RuntimeError(
            "Cannot change browser config after runner has been created. "
            "Call configure_server() before the first tool invocation."
        )
    _server_browser_config = browser_config


def _coerce_viewport(viewport: ViewportConfig | dict[str, Any] | None) -> ViewportConfig:
    if viewport is None:
        return ViewportConfig()
    if isinstance(viewport, ViewportConfig):
        return viewport
    return ViewportConfig.model_validate(viewport)


def _loop_key() -> int:
    return id(asyncio.get_running_loop())


def _ensure_lock() -> asyncio.Lock:
    loop_key = _loop_key()
    lock = _runner_locks_by_loop.get(loop_key)
    if lock is None:
        lock = asyncio.Lock()
        _runner_locks_by_loop[loop_key] = lock
    return lock


async def _get_runner() -> Any:
    global _config_frozen

    loop_key = _loop_key()
    runner = _runners_by_loop.get(loop_key)
    if runner is not None:
        return runner

    async with _ensure_lock():
        runner = _runners_by_loop.get(loop_key)
        if runner is not None:
            return runner
        try:
            from frontend_visualqa.runner import VisualQARunner
        except ImportError as exc:
            raise RuntimeError(
                "frontend_visualqa.runner.VisualQARunner is unavailable. "
                "Make sure the shared runtime files are present before invoking the CLI or MCP server."
            ) from exc
        runner = VisualQARunner(browser_config=_server_browser_config or BrowserConfig())
        _config_frozen = True
        _runners_by_loop[loop_key] = runner
        return runner


def _serialize_result(result: Any) -> dict[str, Any]:
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    if isinstance(result, dict):
        return result
    raise TypeError(f"Unsupported runner result type: {type(result)!r}")


def _reset_server_state() -> None:
    global _server_browser_config, _config_frozen
    _runner_locks_by_loop.clear()
    _server_browser_config = None
    _config_frozen = False


def _detach_runners_for_close() -> list[Any]:
    runners = list(_runners_by_loop.values())
    _runners_by_loop.clear()
    _reset_server_state()
    return runners


async def _close_detached_runners(runners: list[Any]) -> None:
    """Close runners after server state has already been reset."""

    for runner in runners:
        close = getattr(runner, "close", None)
        if close is None:
            continue
        try:
            await close()
        except Exception:
            logger.warning("Failed to close frontend-visualqa runner during shutdown", exc_info=True)


async def _close_all_runners() -> None:
    await _close_detached_runners(_detach_runners_for_close())


def close_runners_sync() -> None:
    """Close any cached runners after the MCP server exits."""

    if not _runners_by_loop and _server_browser_config is None and not _config_frozen:
        return

    runners = _detach_runners_for_close()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        if runners:
            asyncio.run(_close_detached_runners(runners))
        return

    if runners:
        loop.create_task(_close_detached_runners(runners))


def _validate_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        raise ValueError("url must start with http:// or https://")
    return url


@mcp.tool(
    name="verify_visual_claims",
    description=(
        "Verify one or more explicit visual claims against a locally running frontend. "
        "Return structured pass/fail results with screenshot evidence."
    ),
)
async def verify_visual_claims(
    url: str,
    claims: list[str],
    viewport: ViewportConfig | None = None,
    session_key: str = "default",
    reuse_session: bool = True,
    reset_between_claims: bool = True,
    max_steps_per_claim: int = 12,
    claim_timeout_seconds: float | None = 120.0,
    run_timeout_seconds: float | None = 300.0,
    navigation_hint: str | None = None,
) -> dict[str, Any]:
    """Run the shared visual QA runner over one or more claims."""

    runner = await _get_runner()
    request = VerifyVisualClaimsInput(
        url=_validate_url(url),
        claims=claims,
        viewport=_coerce_viewport(viewport),
        session_key=session_key,
        reuse_session=reuse_session,
        reset_between_claims=reset_between_claims,
        max_steps_per_claim=max_steps_per_claim,
        claim_timeout_seconds=claim_timeout_seconds,
        run_timeout_seconds=run_timeout_seconds,
        navigation_hint=navigation_hint,
    )
    return _serialize_result(await runner.run_request(request))


@mcp.tool(
    name="take_screenshot",
    description="Navigate to a local frontend URL and save a screenshot for visual inspection.",
)
async def take_screenshot(
    url: str,
    viewport: ViewportConfig | None = None,
    session_key: str = "default",
    reuse_session: bool = True,
) -> dict[str, Any]:
    """Capture a screenshot through the shared runner without running full claim verification."""

    runner = await _get_runner()
    result = await runner.take_screenshot(
        url=_validate_url(url),
        viewport=_coerce_viewport(viewport),
        session_key=session_key,
        reuse_session=reuse_session,
    )
    return _serialize_result(result)


@mcp.tool(
    name="manage_browser",
    description="Inspect, resize, restart, or close the shared Playwright browser session.",
)
async def manage_browser(
    action: str,
    session_key: str = "default",
    viewport: ViewportConfig | None = None,
) -> dict[str, Any]:
    """Manage shared browser lifecycle without duplicating runner logic in the adapter."""

    runner = await _get_runner()
    request = ManageBrowserInput(
        action=action,
        session_key=session_key,
        viewport=_coerce_viewport(viewport) if viewport is not None else None,
    )
    return _serialize_result(await runner.manage_browser_request(request))


def main() -> None:
    """Run the MCP server over stdio."""

    try:
        get_mcp_server().run(transport="stdio")
    finally:
        close_runners_sync()


if __name__ == "__main__":
    main()
