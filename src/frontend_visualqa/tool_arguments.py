"""Shared parsing helpers for tool-call arguments."""

from __future__ import annotations

import json
from typing import Any

from frontend_visualqa.errors import BrowserActionError


def tool_call_name(tool_call: Any) -> str:
    """Return ``tool_call.function.name`` (or ``tool_call.name``), defaulting to ``""``.

    Mirrors the unwrap-or-fallback pattern in :func:`parse_tool_arguments`:
    chat-completions tool calls expose the action name on a nested
    ``function`` attribute, while flatter test stubs / shorthand objects
    sometimes attach ``name`` directly to the tool-call itself.
    """
    return getattr(getattr(tool_call, "function", tool_call), "name", "")


def tool_call_arguments_as_text(tool_call: Any) -> str:
    """Return ``tool_call``'s raw arguments as text, best-effort.

    Unlike :func:`parse_tool_arguments`, this never raises: dict arguments are
    JSON-encoded, string arguments are passed through, and anything else falls
    back to ``str()``. Useful for callers that need a text representation even
    when the arguments are malformed (e.g. redacting an unparseable payload).
    """
    arguments = getattr(getattr(tool_call, "function", tool_call), "arguments", "")
    if isinstance(arguments, str):
        return arguments
    if isinstance(arguments, dict):
        try:
            return json.dumps(arguments)
        except TypeError:
            pass
    return str(arguments)


def parse_tool_arguments(tool_call: Any) -> dict[str, Any]:
    """Parse chat-completions tool arguments into a JSON object."""

    arguments = getattr(getattr(tool_call, "function", tool_call), "arguments", "{}") or "{}"
    if isinstance(arguments, dict):
        return arguments
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError as exc:
        raise BrowserActionError(f"tool arguments were not valid JSON: {arguments}") from exc
    if not isinstance(parsed, dict):
        raise BrowserActionError(f"tool arguments must decode to an object: {arguments}")
    return parsed
