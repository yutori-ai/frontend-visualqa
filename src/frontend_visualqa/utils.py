"""Shared helper utilities for frontend-visualqa."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)


def retain_background_task(pending: set[asyncio.Task[Any]], task: asyncio.Task[Any]) -> asyncio.Task[Any]:
    """Hold a strong reference to a fire-and-forget background *task* until it completes.

    asyncio only holds a *weak* reference to a scheduled ``Task`` once it starts
    awaiting; with no other strong reference the task can be garbage-collected
    mid-execution, silently dropping whatever cleanup or work it was scheduled
    to do. Adds *task* to *pending* and removes it via a completion callback,
    so *pending* must be a set that outlives this call (module- or
    instance-level). Returns *task* so callers can chain further callbacks
    onto it. Centralizes the identical add-then-discard-on-completion pattern
    independently duplicated in ``navigator_client.py``'s ``_schedule_close``
    and ``mcp_server.py``'s ``close_runners_sync`` (both guarding the same
    fire-and-forget-task GC hazard), and also used by ``overlay.py``'s
    ``_on_navigation``.
    """
    pending.add(task)
    task.add_done_callback(pending.discard)
    return task


def elapsed_ms(start: float) -> float:
    """Milliseconds elapsed since *start* (a ``time.perf_counter()`` reading).

    Centralizes the ``(time.perf_counter() - start) * 1000`` idiom repeated at
    every latency-logging call site (screenshot capture/encode timing in
    ``browser.py``, Navigator request timing in ``navigator_client.py``).
    """
    return (time.perf_counter() - start) * 1000


def now_ms() -> int:
    """Current wall-clock time in whole milliseconds since the Unix epoch.

    Centralizes the epoch-milliseconds idiom repeated at trace-event timestamp
    (``schemas.py``), CTRF report timestamp (``reporters.py``), and overlay
    transient-effect timing (``overlay.py``) call sites. Uses ``time.time_ns()``
    rather than ``int(time.time() * 1000)`` to avoid float-rounding drift.
    """
    return time.time_ns() // 1_000_000


def resolve_optional_method(target: Any | None, method_name: str) -> Any | None:
    """Return the bound *method_name* on *target* if it exists and is callable, else ``None``.

    No-op-safe when *target* is ``None``. Public so callers that need to invoke an
    optional method directly (rather than through ``safe_method_call``/``safe_async_method_call``'s
    swallow-and-log semantics) can still share the same lookup logic.
    """
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
    method = resolve_optional_method(target, method_name)
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
    method = resolve_optional_method(target, method_name)
    if method is None:
        return
    try:
        await method(*args, **kwargs)
    except Exception:
        logger.debug("%s %s failed", log_label or type(target).__name__, method_name, exc_info=True)


async def safe_page_evaluate(
    page: Page,
    script: str,
    arg: object | None = None,
    *,
    default: Any = None,
    log_label: str = "Page",
) -> Any:
    """Best-effort ``page.evaluate``; return ``default`` on failure (logged at DEBUG)."""
    try:
        if arg is None:
            return await page.evaluate(script)
        return await page.evaluate(script, arg)
    except Exception:
        logger.debug("%s evaluate failed (best-effort)", log_label, exc_info=True)
        return default


def safe_callback_call(
    callback: Callable[..., Any] | None,
    *args: Any,
    log_label: str = "Callback",
    log: logging.Logger | None = None,
    **kwargs: Any,
) -> None:
    """Best-effort call to an optional user-provided callback.

    Mirrors :func:`safe_method_call` but operates on a direct callable rather
    than a ``(target, method_name)`` pair. No-op when *callback* is ``None``.
    Any exception raised by the callback is caught and logged at WARNING so
    that callback failures never break the main control flow.

    Pass ``log=...`` to emit the warning under the caller's logger name (e.g.
    so existing log-aggregation rules keyed on ``frontend_visualqa.runner``
    keep capturing these records); defaults to this module's logger.
    """
    if callback is None:
        return
    try:
        callback(*args, **kwargs)
    except Exception:
        (log or logger).warning("%s failed", log_label, exc_info=True)
