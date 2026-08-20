"""Headed-mode adapter for the shared Navigator overlay page runtime."""

from __future__ import annotations

import asyncio
import logging
import math
from typing import TYPE_CHECKING, Any

from yutori_navigator_overlay_runtime import PROTOCOL_VERSION, get_iife, verify_iife

from frontend_visualqa.actions import CLICK_ACTIONS
from frontend_visualqa.schemas import ViewportConfig, _pydantic_field_default
from frontend_visualqa.text_utils import clip_text_preserving_lines
from frontend_visualqa.utils import now_ms, safe_page_evaluate

if TYPE_CHECKING:
    from playwright.async_api import Page


logger = logging.getLogger(__name__)

PERSISTENT_ROOT_ID = "__yutoriNavigatorOverlayPersistent"
TRANSIENT_ROOT_ID = "__yutoriNavigatorOverlayTransient"

CURSOR_TRANSITION_MS = 350
CLICK_DURATION_MS = 250
DRAG_DURATION_MS = 200
MIN_GLIDE_DISTANCE_PX = 6
THOUGHT_MAX_CHARACTERS = 520

# Sourced from ViewportConfig's own field defaults so this fallback (used only
# when Playwright's page.viewport_size is unavailable) can't drift from the
# canonical default viewport if ViewportConfig's ever changes.
_DEFAULT_VIEWPORT_WIDTH: int = _pydantic_field_default(ViewportConfig, "width")
_DEFAULT_VIEWPORT_HEIGHT: int = _pydantic_field_default(ViewportConfig, "height")
_RUNTIME_GLOBAL = "__yutoriNavigatorOverlay"
_RUNTIME_REGISTRY = "yutori.navigator-overlay.runtime.registry"
_EMERGENCY_HIDE_STYLE_ID = "__yutoriNavigatorOverlayEmergencyHide"

if not verify_iife():
    raise RuntimeError("The installed Navigator overlay runtime failed its integrity check")
_IIFE_SOURCE = get_iife()

_APPLY_OPERATION_JS = f"""async (operation) => {{
    const isRuntime = (candidate) =>
        candidate &&
        typeof candidate === 'object' &&
        candidate.protocolVersion === {PROTOCOL_VERSION} &&
        typeof candidate.apply === 'function' &&
        typeof candidate.inspect === 'function';
    const registered = Reflect.get(
        window,
        Symbol.for('{_RUNTIME_REGISTRY}'),
    );
    const publicRuntime = window.{_RUNTIME_GLOBAL};
    const runtime = isRuntime(registered)
        ? registered
        : (isRuntime(publicRuntime) ? publicRuntime : null);
    return runtime ? runtime.apply(operation) : null;
}}"""

_FOCUSED_ELEMENT_CENTER_JS = """() => {
    const element = document.activeElement;
    if (!element || element === document.body || element === document.documentElement) return null;
    const rect = element.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;
    return {
        x: Math.round(rect.left + rect.width / 2),
        y: Math.round(rect.top + rect.height / 2),
    };
}"""

_EMERGENCY_HIDE_JS = f"""() => {{
    let emergencyStyle = document.querySelector(
        'style[data-yutori-navigator-overlay-emergency-hide]'
    );
    if (!emergencyStyle) {{
        emergencyStyle = document.createElement('style');
        emergencyStyle.id = '{_EMERGENCY_HIDE_STYLE_ID}';
        emergencyStyle.setAttribute('data-yutori-navigator-overlay-emergency-hide', '');
        emergencyStyle.textContent = `
            [data-yutori-navigator-overlay-root][data-yutori-navigator-overlay-owned] {{
                transition: none !important;
                visibility: hidden !important;
                opacity: 0 !important;
            }}
        `;
        (document.head || document.documentElement).appendChild(emergencyStyle);
    }}
    const roots = Array.from(document.querySelectorAll(
        '[data-yutori-navigator-overlay-root][data-yutori-navigator-overlay-owned]'
    ));
    return roots.every((root) => {{
        const style = window.getComputedStyle(root);
        return style.visibility === 'hidden' && style.opacity === '0';
    }});
}}"""

_EMERGENCY_RESTORE_JS = """() => {
    const styles = Array.from(document.querySelectorAll(
        'style[data-yutori-navigator-overlay-emergency-hide]'
    ));
    for (const style of styles) style.remove();
    return document.querySelector(
        'style[data-yutori-navigator-overlay-emergency-hide]'
    ) === null;
}"""


def _point(x: int | float, y: int | float) -> dict[str, int | float]:
    return {"x": x, "y": y}


def _loop_badge() -> dict[str, str]:
    return {"type": "loop"}


class OverlayController:
    """Translate FVQA lifecycle events into shared overlay wire operations."""

    def __init__(self, page: Page) -> None:
        self._page = page
        self._active = False
        self._reset_state()
        self._emergency_hidden = False
        self._effect_sequence = 0
        self._operation_lock = asyncio.Lock()
        self._navigation_handler: Any | None = None
        self._navigation_tasks: set[asyncio.Task[None]] = set()

    def _reset_state(self) -> None:
        """Reset the per-claim overlay state shared by ``__init__`` and ``claim_started``."""

        self._activated = False
        self._disposed = False
        self._current_status = "Analyzing"
        self._cursor = self._initial_cursor()
        self._thought_text: str | None = None
        self._badge: dict[str, Any] = _loop_badge()
        self._hidden = False

    async def claim_started(self) -> None:
        await self._clear_emergency_hide()
        self._active = True
        self._reset_state()
        self._detach_navigation_listener()
        self._navigation_handler = self._on_navigation
        try:
            self._page.on("domcontentloaded", self._navigation_handler)
        except Exception:
            logger.debug("Failed to attach overlay navigation listener", exc_info=True)
            self._navigation_handler = None

    async def claim_ended(self) -> None:
        try:
            if not self._active:
                return
            self._active = False
            self._disposed = True
            self._detach_navigation_listener()
            await self._apply_operation({"op": "destroy"})
            self._activated = False
        finally:
            await self._clear_emergency_hide()

    async def preview_action(
        self,
        action_type: str,
        *,
        x: int = 0,
        y: int = 0,
        start_x: int = 0,
        start_y: int = 0,
        num_clicks: int = 1,
        direction: str = "down",
        amount: int = 1,
    ) -> None:
        del amount
        if not self._active:
            return

        if action_type in {"type", "copy", "paste"}:
            center = await self._get_focused_element_center()
            if center is not None:
                await self._move_cursor(center["x"], center["y"])
        elif action_type == "drag":
            await self._move_cursor(start_x, start_y)
        else:
            await self._move_cursor(x, y)

        if action_type in CLICK_ACTIONS:
            await self._present_action(
                badge=_loop_badge(),
                transient_effects=[
                    {
                        "id": self._next_effect_id("click"),
                        "type": "click",
                        "point": _point(x, y),
                        "clicks": max(1, num_clicks),
                        "startedAtMs": now_ms(),
                        "durationMs": CLICK_DURATION_MS,
                    }
                ],
            )
        elif action_type == "scroll":
            rotation = {"down": 0, "up": 180, "right": -90, "left": 90}.get(direction, 0)
            await self._present_glyph("scroll", rotation_degrees=rotation)
        elif action_type == "type":
            await self._present_glyph("type")
        elif action_type == "copy":
            await self._present_glyph("copy")
        elif action_type in {"paste", "set_element_value"}:
            await self._present_glyph("paste")
        elif action_type == "drag":
            await self._present_action(
                badge=_loop_badge(),
                transient_effects=[
                    {
                        "id": self._next_effect_id("drag"),
                        "type": "drag-trail",
                        "from": _point(start_x, start_y),
                        "to": _point(x, y),
                        "startedAtMs": now_ms(),
                        "durationMs": DRAG_DURATION_MS,
                    }
                ],
            )

    async def set_status(self, label: str) -> None:
        self._current_status = label

    async def show_thought(self, text: str) -> None:
        if not self._active:
            return
        clipped = self._clip_text(text, THOUGHT_MAX_CHARACTERS)
        bootstrap = self._snapshot()
        bootstrap["thought"] = None
        self._thought_text = clipped
        await self._apply_operation_and_sync(
            {"op": "showThought", "markdown": clipped},
            bootstrap_snapshot=bootstrap,
        )

    async def clear_thought(self) -> None:
        self._thought_text = None
        if not self._active:
            return
        await self._apply_operation_and_sync({"op": "clearThought"})

    @property
    def _ready(self) -> bool:
        """True once a claim is active and the runtime has been activated for it.

        Shared by the screenshot-hide/restore and navigation-restore paths, which
        must all no-op until the overlay has actually been activated on the page.
        """
        return self._active and self._activated

    async def before_screenshot(self) -> None:
        if not self._ready:
            return
        self._hidden = True
        evaluation = await self._apply_operation({"op": "beforeScreenshot"})
        result = evaluation["result"]
        snapshot = result.get("snapshot") if self._result_ok(result) else None
        if isinstance(snapshot, dict) and snapshot.get("hidden") is True:
            return

        self._emergency_hidden = True
        hidden = await self._safe_evaluate(_EMERGENCY_HIDE_JS, default=False)
        if hidden is not True:
            raise RuntimeError("Navigator overlay could not be hidden before evidence capture")

    async def after_screenshot(self) -> None:
        try:
            if not self._ready:
                return
            self._hidden = False
            await self._apply_operation_and_sync({"op": "afterScreenshot"})
        finally:
            await self._clear_emergency_hide()

    async def _move_cursor(self, x: int, y: int) -> None:
        previous = dict(self._cursor)
        self._cursor = _point(x, y)
        evaluation = await self._apply_operation(
            {"op": "moveCursor", "point": _point(x, y)},
        )
        result = evaluation["result"]
        self._sync_snapshot(result)
        if (
            self._result_ok(result)
            and not evaluation["installed"]
            and math.hypot(float(x) - float(previous["x"]), float(y) - float(previous["y"])) >= MIN_GLIDE_DISTANCE_PX
        ):
            await asyncio.sleep(CURSOR_TRANSITION_MS / 1000)

    async def _present_glyph(self, glyph: str, *, rotation_degrees: int = 0) -> None:
        badge: dict[str, Any] = {"type": "glyph", "glyph": glyph}
        if rotation_degrees:
            badge["rotationDegrees"] = rotation_degrees
        await self._present_action(badge=badge, transient_effects=[])

    async def _present_action(
        self,
        *,
        badge: dict[str, Any],
        transient_effects: list[dict[str, Any]],
    ) -> None:
        bootstrap = self._snapshot()
        self._badge = dict(badge)
        await self._apply_operation_and_sync(
            {
                "op": "previewAction",
                "presentation": {
                    "badge": badge,
                    "transientEffects": transient_effects,
                },
            },
            bootstrap_snapshot=bootstrap,
        )

    async def _restore_after_navigation(self) -> None:
        async with self._operation_lock:
            if not self._ready:
                return
            snapshot = self._snapshot()
            evaluation = await self._apply_operation_now(
                {"op": "restore", "snapshot": snapshot},
                bootstrap_snapshot=snapshot,
            )
        self._sync_snapshot(evaluation["result"])

    def _on_navigation(self, _frame: Any = None) -> None:
        task = asyncio.create_task(self._restore_after_navigation())
        self._navigation_tasks.add(task)
        task.add_done_callback(self._navigation_tasks.discard)
        task.add_done_callback(self._log_navigation_task_result)

    @staticmethod
    def _log_navigation_task_result(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.debug(
                "Overlay navigation restore failed",
                exc_info=(type(error), error, error.__traceback__),
            )

    async def _clear_emergency_hide(self, *, force: bool = False) -> None:
        if not force and not self._emergency_hidden:
            return
        restored = await self._safe_evaluate(_EMERGENCY_RESTORE_JS, default=False)
        self._emergency_hidden = restored is not True
        if self._emergency_hidden:
            logger.debug("Failed to clear emergency overlay hide")

    def _detach_navigation_listener(self) -> None:
        if self._navigation_handler is None:
            return
        try:
            self._page.remove_listener("domcontentloaded", self._navigation_handler)
        except Exception:
            logger.debug("Failed to detach overlay navigation listener", exc_info=True)
        self._navigation_handler = None

    async def _apply_operation(
        self,
        operation: dict[str, Any],
        *,
        bootstrap_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        activates_overlay = self._operation_activates_overlay(operation)
        async with self._operation_lock:
            if activates_overlay and not self._activated and not self._hidden:
                await self._clear_emergency_hide(force=True)
            if activates_overlay:
                self._activated = True
            return await self._apply_operation_now(
                operation,
                bootstrap_snapshot=bootstrap_snapshot or self._snapshot(),
            )

    async def _apply_operation_and_sync(
        self,
        operation: dict[str, Any],
        *,
        bootstrap_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        evaluation = await self._apply_operation(operation, bootstrap_snapshot=bootstrap_snapshot)
        self._sync_snapshot(evaluation["result"])
        return evaluation

    async def _apply_operation_now(
        self,
        operation: dict[str, Any],
        *,
        bootstrap_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        if self._disposed and operation["op"] != "destroy":
            return {"installed": False, "result": None}

        result = await self._safe_evaluate(_APPLY_OPERATION_JS, operation)
        if isinstance(result, dict) and result.get("error") != "runtime-destroyed":
            if self._result_ok(result) and self._operation_activates_overlay(operation):
                self._activated = True
            return {"installed": False, "result": result}
        if self._operation_can_remain_unmounted(operation):
            return {"installed": False, "result": result}

        await self._safe_evaluate(_IIFE_SOURCE)
        if self._disposed and operation["op"] != "destroy":
            await self._safe_evaluate(_APPLY_OPERATION_JS, {"op": "destroy"})
            return {"installed": False, "result": None}

        restore_result = await self._safe_evaluate(
            _APPLY_OPERATION_JS,
            {"op": "restore", "snapshot": bootstrap_snapshot},
        )
        if self._result_ok(restore_result):
            self._activated = True
        if operation["op"] == "restore":
            result = restore_result
        else:
            result = await self._safe_evaluate(_APPLY_OPERATION_JS, operation)
        if self._result_ok(result):
            self._activated = True
        return {"installed": True, "result": result}

    def _snapshot(self) -> dict[str, Any]:
        return {
            "cursor": dict(self._cursor),
            "thought": self._thought_text,
            "badge": dict(self._badge),
            "hidden": self._hidden,
        }

    def _sync_snapshot(self, result: Any) -> None:
        if not self._result_ok(result):
            return
        snapshot = result.get("snapshot")
        if not isinstance(snapshot, dict):
            return
        cursor = snapshot.get("cursor")
        if (
            isinstance(cursor, dict)
            and isinstance(cursor.get("x"), (int, float))
            and isinstance(cursor.get("y"), (int, float))
        ):
            self._cursor = _point(cursor["x"], cursor["y"])
        thought = snapshot.get("thought")
        if thought is None or isinstance(thought, str):
            self._thought_text = thought
        badge = snapshot.get("badge")
        if isinstance(badge, dict):
            self._badge = dict(badge)
        hidden = snapshot.get("hidden")
        if isinstance(hidden, bool):
            self._hidden = hidden

    async def _get_focused_element_center(self) -> dict[str, int] | None:
        center = await self._safe_evaluate(_FOCUSED_ELEMENT_CENTER_JS)
        if not isinstance(center, dict):
            return None
        x = center.get("x")
        y = center.get("y")
        if not isinstance(x, int) or not isinstance(y, int):
            return None
        return {"x": x, "y": y}

    async def _safe_evaluate(
        self,
        script: str,
        arg: object | None = None,
        *,
        default: Any = None,
    ) -> Any:
        return await safe_page_evaluate(self._page, script, arg, default=default, log_label="Overlay")

    def _initial_cursor(self) -> dict[str, int | float]:
        viewport = getattr(self._page, "viewport_size", None)
        if isinstance(viewport, dict):
            width = viewport.get("width", _DEFAULT_VIEWPORT_WIDTH)
            height = viewport.get("height", _DEFAULT_VIEWPORT_HEIGHT)
        else:
            width = _DEFAULT_VIEWPORT_WIDTH
            height = _DEFAULT_VIEWPORT_HEIGHT
        if not isinstance(width, (int, float)) or not isinstance(height, (int, float)):
            width = _DEFAULT_VIEWPORT_WIDTH
            height = _DEFAULT_VIEWPORT_HEIGHT
        return _point(width / 2, height / 2)

    def _next_effect_id(self, effect: str) -> str:
        self._effect_sequence += 1
        return f"frontend-visualqa-{effect}-{self._effect_sequence}"

    @staticmethod
    def _result_ok(result: Any) -> bool:
        return (
            isinstance(result, dict) and result.get("ok") is True and result.get("protocolVersion") == PROTOCOL_VERSION
        )

    @staticmethod
    def _operation_can_remain_unmounted(operation: dict[str, Any]) -> bool:
        return operation["op"] in {"afterScreenshot", "clearThought", "destroy"}

    @staticmethod
    def _operation_activates_overlay(operation: dict[str, Any]) -> bool:
        return operation["op"] in {
            "mount",
            "moveCursor",
            "showThought",
            "previewAction",
            "restore",
        }

    @staticmethod
    def _clip_text(text: str, limit: int) -> str:
        return clip_text_preserving_lines(str(text), limit, ellipsis="…")
