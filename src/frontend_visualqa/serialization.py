"""Shared helpers for native JSON serialization."""

from __future__ import annotations

from typing import Any


def serialize_result(result: Any) -> dict[str, Any]:
    """Convert Pydantic models and plain dicts into the native JSON payload shape."""

    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    if isinstance(result, dict):
        return result
    raise TypeError(f"Unsupported runner result type: {type(result)!r}")
