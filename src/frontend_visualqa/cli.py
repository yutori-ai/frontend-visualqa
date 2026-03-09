"""Unified CLI entrypoint for frontend-visualqa."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Any

from frontend_visualqa.mcp_server import close_runners_sync, get_mcp_server
from frontend_visualqa.schemas import ViewportConfig


def build_parser() -> argparse.ArgumentParser:
    """Build the single-entrypoint CLI parser."""

    parser = argparse.ArgumentParser(
        prog="frontend-visualqa",
        description="Visual QA for local frontends using the shared frontend-visualqa runner.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Start the FastMCP stdio server.")
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
    verify_parser.set_defaults(handler=_handle_verify)

    screenshot_parser = subparsers.add_parser("screenshot", help="Capture a screenshot for a target URL.")
    screenshot_parser.add_argument("url", help="Target page URL, usually a localhost route.")
    _add_viewport_args(screenshot_parser)
    screenshot_parser.add_argument("--session-key", default="default", help="Shared browser session key.")
    screenshot_parser.add_argument(
        "--reuse-session",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Reuse the named browser session if it already exists.",
    )
    screenshot_parser.set_defaults(handler=_handle_screenshot)

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


def _build_viewport(args: argparse.Namespace) -> ViewportConfig:
    return ViewportConfig(
        width=args.width,
        height=args.height,
        device_scale_factor=args.device_scale_factor,
    )


def _configure_serve_logging() -> None:
    # Stdio transport depends on a clean stdout channel for MCP messages.
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, force=True)
    logging.getLogger("frontend_visualqa").setLevel(logging.WARNING)
    logging.getLogger("mcp").setLevel(logging.ERROR)


def _emit_json(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")


def _handle_serve(_: argparse.Namespace) -> int:
    _configure_serve_logging()
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


def _handle_status(_: argparse.Namespace) -> int:
    result = asyncio.run(_run_status())
    _emit_json(result)
    return 0


async def _run_verify(args: argparse.Namespace) -> dict[str, Any]:
    runner = _new_runner()
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
    runner = _new_runner()
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


def _new_runner() -> Any:
    from frontend_visualqa.runner import VisualQARunner

    return VisualQARunner()


if __name__ == "__main__":
    raise SystemExit(main())
