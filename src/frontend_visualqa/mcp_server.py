"""FastMCP adapter for frontend-visualqa."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from frontend_visualqa.serialization import serialize_result
from frontend_visualqa.schemas import (
    BrowserConfig,
    ManageBrowserInput,
    VerifyVisualClaimsInput,
    ViewportConfig,
    validate_url,
)


logger = logging.getLogger(__name__)

SERVER_INSTRUCTIONS = (
    "Use verify_visual_claims for explicit, observable frontend claims. "
    "Use take_screenshot when you need a quick visual baseline before writing claims. "
    "Use manage_browser to inspect browser state, reset the shared session, or open a persistent headed browser "
    "for human login on auth-gated apps. "
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
    run_name: str | None = None,
    reuse_session: bool = True,
    reset_between_claims: bool = True,
    visualize: bool | None = None,
    max_steps_per_claim: int = 12,
    claim_timeout_seconds: float | None = 120.0,
    run_timeout_seconds: float | None = 300.0,
    navigation_hint: str | None = None,
) -> dict[str, Any]:
    """Run the shared visual QA runner over one or more claims."""

    runner = await _get_runner()
    request = VerifyVisualClaimsInput(
        url=validate_url(url),
        claims=claims,
        viewport=_coerce_viewport(viewport),
        session_key=session_key,
        run_name=run_name,
        reuse_session=reuse_session,
        reset_between_claims=reset_between_claims,
        visualize=visualize,
        max_steps_per_claim=max_steps_per_claim,
        claim_timeout_seconds=claim_timeout_seconds,
        run_timeout_seconds=run_timeout_seconds,
        navigation_hint=navigation_hint,
    )
    return serialize_result(await runner.run_request(request))


@mcp.tool(
    name="take_screenshot",
    description="Navigate to a local frontend URL and save a screenshot for visual inspection.",
)
async def take_screenshot(
    url: str,
    viewport: ViewportConfig | None = None,
    session_key: str = "default",
    run_name: str | None = None,
    reuse_session: bool = True,
) -> dict[str, Any]:
    """Capture a screenshot through the shared runner without running full claim verification."""

    runner = await _get_runner()
    result = await runner.take_screenshot(
        url=validate_url(url),
        viewport=_coerce_viewport(viewport),
        session_key=session_key,
        run_name=run_name,
        reuse_session=reuse_session,
    )
    return serialize_result(result)


@mcp.tool(
    name="manage_browser",
    description=(
        "Manage the shared Playwright browser session. "
        "Valid actions: status, restart, close, set_viewport, login. "
        "Use action='login' with a url to open a persistent headed browser for human authentication on "
        "auth-gated apps."
    ),
)
async def manage_browser(
    action: str,
    session_key: str = "default",
    viewport: ViewportConfig | None = None,
    url: str | None = None,
) -> dict[str, Any]:
    """Manage shared browser lifecycle.

    Args:
        action: One of status, restart, close, set_viewport, login.
        session_key: Named browser session to operate on.
        viewport: Viewport dimensions (used by set_viewport, restart, login).
        url: Required when action is 'login'. The URL where the human should complete authentication.
    """

    runner = await _get_runner()
    request = ManageBrowserInput(
        action=action,
        session_key=session_key,
        viewport=_coerce_viewport(viewport) if viewport is not None else None,
        url=url,
    )
    return serialize_result(await runner.manage_browser_request(request))


def main() -> None:
    """Run the MCP server over stdio."""

    try:
        get_mcp_server().run(transport="stdio")
    finally:
        close_runners_sync()


if __name__ == "__main__":
    main()
