"""Shared helpers for native JSON serialization."""

from __future__ import annotations

from typing import Any


def dump_or_pass_through(value: Any, *, model_dump_kwargs: dict[str, Any], type_label: str) -> dict[str, Any]:
    """Convert a Pydantic model or plain dict into a JSON-compatible dict, else raise."""

    if hasattr(value, "model_dump"):
        return value.model_dump(**model_dump_kwargs)
    if isinstance(value, dict):
        return value
    raise TypeError(f"Unsupported {type_label} type: {type(value)!r}")


def serialize_result(result: Any) -> dict[str, Any]:
    """Convert Pydantic models and plain dicts into the native JSON payload shape."""

    return dump_or_pass_through(result, model_dump_kwargs={"mode": "json"}, type_label="runner result")
