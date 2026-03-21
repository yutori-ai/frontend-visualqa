"""Unified CLI entrypoint for frontend-visualqa."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import threading
from typing import Any

from frontend_visualqa import __version__
from frontend_visualqa.browser import BrowserManager
from frontend_visualqa.mcp_server import close_runners_sync, configure_server, get_mcp_server
from frontend_visualqa.schemas import BrowserConfig, BrowserMode, ViewportConfig, validate_url


def build_parser() -> argparse.ArgumentParser:
    """Build the single-entrypoint CLI parser."""

    parser = argparse.ArgumentParser(
        prog="frontend-visualqa",
        description="Gives coding agents eyes for frontend work — visual QA and verification powered by Yutori n1.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Start the FastMCP stdio server.")
    _add_browser_args(serve_parser)
    serve_parser.set_defaults(handler=_handle_serve)

    verify_parser = subparsers.add_parser("verify", help="Verify one or more visual claims against a URL.")
    verify_parser.add_argument("url", help="Target page URL, usually a localhost route.")
    verify_parser.add_argument(
        "--claims",
        nargs="+",
        required=True,
        help="One or more explicit visual claims to verify.",
    )
    _add_viewport_args(verify_parser)
    _add_browser_args(verify_parser)
    verify_parser.add_argument("--session-key", default="default", help="Shared browser session key.")
    verify_parser.add_argument(
        "--reuse-session",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Reuse the named browser session if it already exists.",
    )
    verify_parser.add_argument(
        "--reset-between-claims",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Return to the base URL between claims.",
    )
    verify_parser.add_argument(
        "--max-steps-per-claim",
        type=int,
        default=12,
        help="Maximum browser actions the runner may take for each claim.",
    )
    verify_parser.add_argument(
        "--claim-timeout-seconds",
        type=float,
        default=120.0,
        help="Maximum wall-clock time for an individual claim before it is marked inconclusive.",
    )
    verify_parser.add_argument(
        "--run-timeout-seconds",
        type=float,
        default=300.0,
        help="Maximum wall-clock time for the whole run before remaining claims are marked inconclusive.",
    )
    verify_parser.add_argument(
        "--navigation-hint",
        help="Optional interaction guidance when the page must be manipulated before judging a claim.",
    )
    verify_parser.add_argument(
        "--reporter",
        action="append",
        choices=("native", "ctrf"),
        default=None,
        help="Output reporter. Can be specified multiple times. Defaults to native.",
    )
    verify_parser.set_defaults(handler=_handle_verify)

    screenshot_parser = subparsers.add_parser("screenshot", help="Capture a screenshot for a target URL.")
    screenshot_parser.add_argument("url", help="Target page URL, usually a localhost route.")
    _add_viewport_args(screenshot_parser)
    _add_browser_args(screenshot_parser)
    screenshot_parser.add_argument("--session-key", default="default", help="Shared browser session key.")
    screenshot_parser.add_argument(
        "--reuse-session",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Reuse the named browser session if it already exists.",
    )
    screenshot_parser.set_defaults(handler=_handle_screenshot)

    login_parser = subparsers.add_parser(
        "login",
        help="Open a headed persistent browser profile so you can log in once and reuse the session later.",
    )
    login_parser.add_argument("url", help="Login page URL, usually a localhost route.")
    login_parser.add_argument(
        "--user-data-dir",
        help="Persistent Playwright profile directory. Defaults to the shared frontend-visualqa cache path.",
    )
    login_parser.set_defaults(handler=_handle_login)

    status_parser = subparsers.add_parser("status", help="Show browser status for the current process as JSON.")
    status_parser.set_defaults(handler=_handle_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI."""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except KeyboardInterrupt:
        return 130


def _add_viewport_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--width", type=int, default=1280, help="Viewport width in CSS pixels.")
    parser.add_argument("--height", type=int, default=800, help="Viewport height in CSS pixels.")
    parser.add_argument(
        "--device-scale-factor",
        type=float,
        default=1.0,
        help="Device scale factor. Keep this at 1 unless you explicitly need another DPR.",
    )


def _add_browser_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--browser-mode",
        choices=[mode.value for mode in BrowserMode],
        default=BrowserMode.ephemeral.value,
        help="Browser launch strategy. Use persistent to keep cookies and local storage across runs.",
    )
    parser.add_argument(
        "--user-data-dir",
        help="Persistent Playwright profile directory. Ignored in ephemeral mode.",
    )
    parser.add_argument(
        "--headed",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Run the browser visibly instead of headless.",
    )
    parser.add_argument(
        "--visualize",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Show in-browser action visualization. Defaults to on when --headed is set.",
    )


def _build_viewport(args: argparse.Namespace) -> ViewportConfig:
    return ViewportConfig(
        width=args.width,
        height=args.height,
        device_scale_factor=args.device_scale_factor,
    )


def _build_browser_config(
    args: argparse.Namespace,
    *,
    force_mode: BrowserMode | None = None,
    force_headed: bool | None = None,
) -> BrowserConfig:
    mode = force_mode or BrowserMode(getattr(args, "browser_mode", BrowserMode.ephemeral.value))
    headed = force_headed if force_headed is not None else getattr(args, "headed", False)
    explicit_visualize = getattr(args, "visualize", None)
    visualize = explicit_visualize if explicit_visualize is not None else headed
    return BrowserConfig(
        mode=mode,
        user_data_dir=getattr(args, "user_data_dir", None),
        headless=not headed,
        visualize=visualize,
    )


def _configure_serve_logging() -> None:
    # Stdio transport depends on a clean stdout channel for MCP messages.
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, force=True)
    logging.getLogger("frontend_visualqa").setLevel(logging.WARNING)
    logging.getLogger("mcp").setLevel(logging.ERROR)


def _emit_json(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")


def _handle_serve(args: argparse.Namespace) -> int:
    _configure_serve_logging()
    configure_server(_build_browser_config(args))
    try:
        get_mcp_server().run(transport="stdio")
    finally:
        close_runners_sync()
    return 0


def _handle_verify(args: argparse.Namespace) -> int:
    result = asyncio.run(_run_verify(args))
    _emit_json(result)
    return 0


def _handle_screenshot(args: argparse.Namespace) -> int:
    result = asyncio.run(_run_screenshot(args))
    _emit_json(result)
    return 0


def _handle_login(args: argparse.Namespace) -> int:
    if not sys.stdin.isatty():
        print("login requires an interactive terminal (stdin must be a TTY).", file=sys.stderr)
        return 1
    try:
        validate_url(args.url)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return asyncio.run(_run_login(args))


def _handle_status(_: argparse.Namespace) -> int:
    result = asyncio.run(_run_status())
    _emit_json(result)
    return 0


async def _run_verify(args: argparse.Namespace) -> dict[str, Any]:
    runner = _new_runner(
        browser_config=_build_browser_config(args),
        reporters=getattr(args, "reporter", None),
    )
    try:
        result = await runner.run(
            url=args.url,
            claims=args.claims,
            viewport=_build_viewport(args),
            session_key=args.session_key,
            reuse_session=args.reuse_session,
            reset_between_claims=args.reset_between_claims,
            max_steps_per_claim=args.max_steps_per_claim,
            claim_timeout_seconds=args.claim_timeout_seconds,
            run_timeout_seconds=args.run_timeout_seconds,
            navigation_hint=args.navigation_hint,
        )
        return _serialize_result(result)
    finally:
        await runner.close()


async def _run_screenshot(args: argparse.Namespace) -> dict[str, Any]:
    runner = _new_runner(browser_config=_build_browser_config(args))
    try:
        result = await runner.take_screenshot(
            url=args.url,
            viewport=_build_viewport(args),
            session_key=args.session_key,
            reuse_session=args.reuse_session,
        )
        return _serialize_result(result)
    finally:
        await runner.close()


async def _run_login(args: argparse.Namespace) -> int:
    manager = BrowserManager(config=_build_browser_config(args, force_mode=BrowserMode.persistent, force_headed=True))
    browser_closed = False
    manager_closed = False
    done = threading.Event()

    def _mark_browser_closed(*_: object) -> None:
        nonlocal browser_closed
        browser_closed = True
        done.set()

    def _read_stdin() -> None:
        try:
            sys.stdin.readline()
        finally:
            done.set()

    session = None
    try:
        session = await manager.get_session("default", reuse_session=False)
        session.context.on("close", _mark_browser_closed)
        await manager.goto(session, args.url)
        print("Browser is open. Log in, then press Enter here to close and save the session.", file=sys.stderr)
        reader = threading.Thread(target=_read_stdin, daemon=True)
        reader.start()
        while not done.is_set():
            await asyncio.sleep(0.2)

        if browser_closed:
            print("Browser closed.", file=sys.stderr)
            await manager.close()  # stops Playwright subprocess
            manager_closed = True
        else:
            await manager.close()
            manager_closed = True
            print("Saved session.", file=sys.stderr)
        return 0
    finally:
        if not manager_closed:
            try:
                await manager.close()
            except Exception:
                pass


async def _run_status() -> dict[str, Any]:
    runner = _new_runner()
    try:
        result = await runner.manage_browser(action="status")
        return _serialize_result(result)
    finally:
        await runner.close()


def _serialize_result(result: Any) -> dict[str, Any]:
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    if isinstance(result, dict):
        return result
    raise TypeError(f"CLI command returned unsupported type: {type(result)!r}")


def _new_runner(*, browser_config: BrowserConfig | None = None, reporters: list[str] | None = None) -> Any:
    from frontend_visualqa.runner import VisualQARunner

    return VisualQARunner(browser_config=browser_config, reporters=reporters)


if __name__ == "__main__":
    raise SystemExit(main())
