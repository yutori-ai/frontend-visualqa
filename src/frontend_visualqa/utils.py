"""Shared helper utilities for frontend-visualqa."""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


def _resolve_optional_method(target: Any | None, method_name: str) -> Any | None:
    if target is None:
        return None
    method = getattr(target, method_name, None)
    return method if callable(method) else None


def safe_method_call(
    target: Any | None,
    method_name: str,
    *args: Any,
    log_label: str = "",
    **kwargs: Any,
) -> None:
    """Best-effort call to an optional sync method on *target*.

    No-op when *target* is ``None`` or does not expose *method_name*.
    Any exception raised by the method is caught and logged at DEBUG level
    so that hook / overlay failures never break the main control flow.
    """
    method = _resolve_optional_method(target, method_name)
    if method is None:
        return
    try:
        method(*args, **kwargs)
    except Exception:
        logger.debug("%s %s failed", log_label or type(target).__name__, method_name, exc_info=True)


async def safe_async_method_call(
    target: Any | None,
    method_name: str,
    *args: Any,
    log_label: str = "",
    **kwargs: Any,
) -> None:
    """Best-effort call to an optional async method on *target*.

    No-op when *target* is ``None`` or does not expose *method_name*.
    Any exception raised by the method is caught and logged at DEBUG level
    so that overlay / hook failures never break the main control flow.
    """
    method = _resolve_optional_method(target, method_name)
    if method is None:
        return
    try:
        await method(*args, **kwargs)
    except Exception:
        logger.debug("%s %s failed", log_label or type(target).__name__, method_name, exc_info=True)


def safe_callback_call(
    callback: Callable[..., Any] | None,
    *args: Any,
    log_label: str = "Callback",
    **kwargs: Any,
) -> None:
    """Best-effort call to an optional user-provided callback.

    Mirrors :func:`safe_method_call` but operates on a direct callable rather
    than a ``(target, method_name)`` pair. No-op when *callback* is ``None``.
    Any exception raised by the callback is caught and logged at WARNING so
    that callback failures never break the main control flow.
    """
    if callback is None:
        return
    try:
        callback(*args, **kwargs)
    except Exception:
        logger.warning("%s failed", log_label, exc_info=True)
