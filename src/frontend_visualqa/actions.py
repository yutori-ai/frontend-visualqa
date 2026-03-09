"""n1 browser action execution against Playwright."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from frontend_visualqa.browser import BrowserSession, DEFAULT_NAVIGATION_TIMEOUT_MS as BROWSER_NAVIGATION_TIMEOUT_MS
from frontend_visualqa.errors import BrowserActionError


MODEL_COORDINATE_SCALE = 1000
DEFAULT_WAIT_SECONDS = 1.0

ACTION_DELAY_SECONDS: dict[str, float] = {
    "left_click": 0.25,
    "double_click": 0.25,
    "triple_click": 0.25,
    "right_click": 0.25,
    "drag": 0.35,
    "type": 0.2,
    "hover": 0.15,
    "scroll": 0.15,
    "key_press": 0.15,
    "goto_url": 0.8,
    "go_back": 0.8,
    "go_forward": 0.8,
    "refresh": 0.8,
    "screenshot": 0.0,
    "wait": 0.0,
}

ACTION_NAME_ALIASES: dict[str, str] = {
    "back": "go_back",
    "goto": "goto_url",
    "key": "key_press",
}

KEY_MAP: dict[str, str] = {
    "alt": "Alt",
    "arrowdown": "ArrowDown",
    "arrowleft": "ArrowLeft",
    "arrowright": "ArrowRight",
    "arrowup": "ArrowUp",
    "backspace": "Backspace",
    "bksp": "Backspace",
    "cmd": "ControlOrMeta",
    "command": "ControlOrMeta",
    "control": "ControlOrMeta",
    "ctrl": "ControlOrMeta",
    "delete": "Delete",
    "del": "Delete",
    "down": "ArrowDown",
    "end": "End",
    "enter": "Enter",
    "esc": "Escape",
    "escape": "Escape",
    "f1": "F1",
    "f2": "F2",
    "f3": "F3",
    "f4": "F4",
    "f5": "F5",
    "f6": "F6",
    "f7": "F7",
    "f8": "F8",
    "f9": "F9",
    "f10": "F10",
    "f11": "F11",
    "f12": "F12",
    "home": "Home",
    "insert": "Insert",
    "kp_enter": "Enter",
    "left": "ArrowLeft",
    "meta": "ControlOrMeta",
    "option": "Alt",
    "page_down": "PageDown",
    "pagedown": "PageDown",
    "page_up": "PageUp",
    "pageup": "PageUp",
    "pgdn": "PageDown",
    "pgup": "PageUp",
    "return": "Enter",
    "right": "ArrowRight",
    "shift": "Shift",
    "space": "Space",
    "spacebar": "Space",
    "super": "ControlOrMeta",
    "tab": "Tab",
    "up": "ArrowUp",
}

KEY_COMBINATION_ACTIONS: dict[str, str] = {
    "Alt+ArrowLeft": "go_back",
    "Alt+ArrowRight": "go_forward",
    "F5": "refresh",
}


def map_key_to_playwright(key: str) -> str:
    """Map a single key token to Playwright's naming."""

    stripped = key.strip()
    mapped = KEY_MAP.get(stripped.lower())
    if mapped is not None:
        return mapped
    if len(stripped) == 1:
        return stripped.lower()
    return stripped


def map_key_combination_to_playwright(key_text: str) -> str:
    """Map a keyboard combination string to a Playwright-compatible chord."""

    parts = [map_key_to_playwright(part) for part in key_text.split("+") if part.strip()]
    return "+".join(parts)


def scale_coordinates(coordinates: list[int] | tuple[int, int], width: int, height: int) -> tuple[int, int]:
    """Convert normalized n1 coordinates into viewport pixels."""

    if len(coordinates) != 2:
        raise BrowserActionError(f"coordinates must have exactly 2 items: {coordinates}")

    raw_x = int(float(coordinates[0]) / MODEL_COORDINATE_SCALE * width)
    raw_y = int(float(coordinates[1]) / MODEL_COORDINATE_SCALE * height)
    clamped_x = max(0, min(max(width - 1, 0), raw_x))
    clamped_y = max(0, min(max(height - 1, 0), raw_y))
    return clamped_x, clamped_y


def render_action_trace(
    action_name: str,
    arguments: dict[str, Any],
    *,
    width: int | None = None,
    height: int | None = None,
) -> str:
    """Render a compact trace line for a tool call."""

    canonical_name = ACTION_NAME_ALIASES.get(action_name, action_name)

    if canonical_name in {"left_click", "double_click", "triple_click", "right_click", "hover"} and width and height:
        coordinates = arguments.get("coordinates")
        if coordinates is not None:
            x, y = scale_coordinates(coordinates, width, height)
            return f"{canonical_name}([{x}, {y}])"

    if canonical_name == "drag" and width and height:
        start = arguments.get("start_coordinates")
        end = arguments.get("coordinates")
        if start is not None and end is not None:
            start_x, start_y = scale_coordinates(start, width, height)
            end_x, end_y = scale_coordinates(end, width, height)
            return f"drag([{start_x}, {start_y}], [{end_x}, {end_y}])"

    if canonical_name == "scroll" and width and height:
        coordinates = arguments.get("coordinates", [500, 500])
        x, y = scale_coordinates(coordinates, width, height)
        direction = str(arguments.get("direction", "down")).lower()
        amount = arguments.get("amount", 1)
        return f"scroll([{x}, {y}], direction={direction}, amount={amount})"

    if canonical_name == "type":
        text = json.dumps(str(arguments.get("text", "")))
        press_enter = bool(arguments.get("press_enter_after"))
        clear_before = bool(
            arguments.get("clear_before_typing") or arguments.get("clear_before") or arguments.get("clear_before_type")
        )
        return f"type({text}, press_enter_after={press_enter}, clear_before={clear_before})"

    if canonical_name == "key_press":
        key_comb = str(arguments.get("key_comb") or arguments.get("key") or "")
        return f"key_press({map_key_combination_to_playwright(key_comb)})"

    if canonical_name == "goto_url":
        return f"goto_url({json.dumps(str(arguments.get('url') or arguments.get('href') or ''))})"

    if not arguments:
        return f"{canonical_name}()"

    ordered_parts = ", ".join(f"{key}={arguments[key]!r}" for key in sorted(arguments))
    return f"{canonical_name}({ordered_parts})"


class ActionExecutor:
    """Execute n1 browser action tool calls against a Playwright page."""

    def __init__(
        self,
        *,
        navigation_timeout_ms: int = BROWSER_NAVIGATION_TIMEOUT_MS,
        settle_delay_seconds: float | None = None,
    ) -> None:
        self.navigation_timeout_ms = navigation_timeout_ms
        self.settle_delay_seconds = settle_delay_seconds

    async def execute_tool_call(self, session: BrowserSession, tool_call: Any) -> str:
        """Execute a tool call object with ``function.name`` and JSON arguments."""

        arguments = self._parse_tool_arguments(tool_call)
        action_name = getattr(getattr(tool_call, "function", tool_call), "name", "")
        return await self.execute_action(session=session, action_name=action_name, arguments=arguments)

    async def execute_action(
        self,
        session: BrowserSession,
        action_name: str,
        arguments: dict[str, Any] | None,
    ) -> str:
        raw_arguments = arguments or {}
        canonical_name = ACTION_NAME_ALIASES.get(action_name, action_name)
        trace = render_action_trace(
            canonical_name,
            raw_arguments,
            width=session.viewport.width,
            height=session.viewport.height,
        )
        page = session.page
        width = session.viewport.width
        height = session.viewport.height

        try:
            if canonical_name in {"left_click", "double_click", "triple_click", "right_click", "hover"}:
                coords = raw_arguments.get("coordinates")
                if coords is None:
                    raise BrowserActionError(f"{canonical_name} requires coordinates")
                x, y = scale_coordinates(coords, width, height)
                if canonical_name == "hover":
                    await page.mouse.move(x, y)
                elif canonical_name == "double_click":
                    await page.mouse.dblclick(x, y)
                else:
                    click_count = 3 if canonical_name == "triple_click" else 1
                    button = "right" if canonical_name == "right_click" else "left"
                    await page.mouse.click(x, y, button=button, click_count=click_count)

            elif canonical_name == "drag":
                start = raw_arguments.get("start_coordinates")
                end = raw_arguments.get("coordinates")
                if start is None or end is None:
                    raise BrowserActionError("drag requires start_coordinates and coordinates")
                start_x, start_y = scale_coordinates(start, width, height)
                end_x, end_y = scale_coordinates(end, width, height)
                await page.mouse.move(start_x, start_y)
                await page.mouse.down()
                await page.mouse.move(end_x, end_y, steps=10)
                await page.mouse.up()

            elif canonical_name == "scroll":
                coords = raw_arguments.get("coordinates", [500, 500])
                direction = str(raw_arguments.get("direction", "down")).lower()
                amount = float(raw_arguments.get("amount", 1))
                if direction not in {"up", "down", "left", "right"}:
                    raise BrowserActionError(f"unsupported scroll direction: {direction}")
                x, y = scale_coordinates(coords, width, height)
                await page.mouse.move(x, y)
                delta_x = (
                    width * 0.1 * amount
                    if direction == "right"
                    else -width * 0.1 * amount
                    if direction == "left"
                    else 0.0
                )
                delta_y = (
                    height * 0.1 * amount
                    if direction == "down"
                    else -height * 0.1 * amount
                    if direction == "up"
                    else 0.0
                )
                await page.mouse.wheel(delta_x, delta_y)

            elif canonical_name == "type":
                text = str(raw_arguments.get("text", ""))
                clear_before = bool(
                    raw_arguments.get("clear_before_typing")
                    or raw_arguments.get("clear_before")
                    or raw_arguments.get("clear_before_type")
                )
                press_enter = bool(raw_arguments.get("press_enter_after"))
                if clear_before:
                    await page.keyboard.press("ControlOrMeta+A")
                    await page.keyboard.press("Backspace")
                if text:
                    await page.keyboard.type(text)
                if press_enter:
                    await page.keyboard.press("Enter")

            elif canonical_name == "key_press":
                key_comb = str(raw_arguments.get("key_comb") or raw_arguments.get("key") or "")
                if not key_comb:
                    raise BrowserActionError("key_press requires key_comb")
                normalized = map_key_combination_to_playwright(key_comb)
                semantic_action = KEY_COMBINATION_ACTIONS.get(normalized)
                if semantic_action is not None:
                    await self.execute_action(session, semantic_action, {})
                    return trace
                await page.keyboard.press(normalized)

            elif canonical_name == "goto_url":
                url = raw_arguments.get("url") or raw_arguments.get("href")
                if not url:
                    raise BrowserActionError("goto_url requires url")
                await page.goto(url, wait_until="domcontentloaded")

            elif canonical_name == "go_back":
                await page.go_back(wait_until="domcontentloaded")

            elif canonical_name == "go_forward":
                await page.go_forward(wait_until="domcontentloaded")

            elif canonical_name == "refresh":
                await page.reload(wait_until="domcontentloaded")

            elif canonical_name == "screenshot":
                return trace

            elif canonical_name == "wait":
                await asyncio.sleep(float(raw_arguments.get("seconds", DEFAULT_WAIT_SECONDS)))
                return trace

            else:
                raise BrowserActionError(f"unsupported action: {canonical_name}")

        except BrowserActionError:
            raise
        except Exception as exc:  # pragma: no cover - exercised through integration tests
            raise BrowserActionError(f"failed to execute {trace}: {exc}") from exc

        await self._best_effort_wait_for_domcontentloaded(page)
        await asyncio.sleep(self._post_action_delay(canonical_name))
        return trace

    @staticmethod
    def _parse_tool_arguments(tool_call: Any) -> dict[str, Any]:
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

    async def _best_effort_wait_for_domcontentloaded(self, page: Any) -> None:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=self.navigation_timeout_ms)
        except Exception:
            return

    def _post_action_delay(self, action_name: str) -> float:
        if self.settle_delay_seconds is not None:
            return self.settle_delay_seconds
        return ACTION_DELAY_SECONDS.get(action_name, 0.3)
