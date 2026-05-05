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
