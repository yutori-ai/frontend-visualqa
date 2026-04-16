"""Shared async helper utilities for frontend-visualqa."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


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
    if target is None:
        return
    method = getattr(target, method_name, None)
    if not callable(method):
        return
    try:
        await method(*args, **kwargs)
    except Exception:
        logger.debug("%s %s failed", log_label or type(target).__name__, method_name, exc_info=True)
