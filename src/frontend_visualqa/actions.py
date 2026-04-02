"""n1 browser action execution against Playwright."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from frontend_visualqa.browser import BrowserSession, DEFAULT_NAVIGATION_TIMEOUT_MS as BROWSER_NAVIGATION_TIMEOUT_MS
from frontend_visualqa.errors import BrowserActionError
from frontend_visualqa.tool_arguments import parse_tool_arguments

if TYPE_CHECKING:
    from frontend_visualqa.overlay import OverlayController

logger = logging.getLogger(__name__)

MODEL_COORDINATE_SCALE = 1000
DEFAULT_WAIT_SECONDS = 1.0
EXTRACT_CONTENT_AND_LINKS_TOOL_NAME = "extract_content_and_links"
MAX_ACCESSIBLE_SNAPSHOT_CHARS = 4_000
_ARIA_LINK_PATTERN = re.compile(r'- link "([^"]*)"')
_ARIA_URL_PATTERN = re.compile(r"- /url: (.+)")
_LINK_TITLE_CLEANER_PATTERN = re.compile(r"\s+\d+$")

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

DISALLOWED_ZOOM_KEYS = {"-", "=", "0", "Minus", "Plus"}


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


def expand_key_sequence(key_text: str) -> list[str]:
    """Expand a keyboard input into one or more Playwright key presses."""

    stripped = key_text.strip()
    if not stripped:
        return []

    if "+" in stripped:
        return [map_key_combination_to_playwright(stripped)]

    token_candidates = [token for token in re.split(r"[\s,]+", stripped) if token]
    if len(token_candidates) > 1 and all(token == token_candidates[0] for token in token_candidates):
        return [map_key_to_playwright(token) for token in token_candidates]

    return [map_key_combination_to_playwright(stripped)]


def is_disallowed_zoom_shortcut(key_text: str) -> bool:
    """Return true when the key chord would change the browser zoom level."""

    parts = [part for part in key_text.split("+") if part]
    return "ControlOrMeta" in parts and any(part in DISALLOWED_ZOOM_KEYS for part in parts[1:])


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

    if canonical_name in {
        "left_click",
        "double_click",
        "triple_click",
        "right_click",
        "hover",
    } and width and height:
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
        key_sequence = expand_key_sequence(key_comb)
        if not key_sequence:
            return "key_press()"
        if len(key_sequence) == 1:
            return f"key_press({key_sequence[0]})"
        return f"key_press_sequence({', '.join(key_sequence)})"

    if canonical_name == "goto_url":
        return f"goto_url({json.dumps(str(arguments.get('url') or arguments.get('href') or ''))})"

    if not arguments:
        return f"{canonical_name}()"

    ordered_parts = ", ".join(f"{key}={arguments[key]!r}" for key in sorted(arguments))
    return f"{canonical_name}({ordered_parts})"


@dataclass
class ToolExecutionResult:
    trace: str
    output_text: str | None = None
    current_url: str | None = None


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
        self._overlay: OverlayController | None = None

    @property
    def overlay(self) -> OverlayController | None:
        return self._overlay

    @overlay.setter
    def overlay(self, value: OverlayController | None) -> None:
        self._overlay = value

    async def execute_tool_call(self, session: BrowserSession, tool_call: Any) -> ToolExecutionResult | str:
        """Execute a tool call object with ``function.name`` and JSON arguments."""

        arguments = parse_tool_arguments(tool_call)
        action_name = getattr(getattr(tool_call, "function", tool_call), "name", "")
        canonical_name = ACTION_NAME_ALIASES.get(action_name, action_name)
        if canonical_name == EXTRACT_CONTENT_AND_LINKS_TOOL_NAME:
            return await self._execute_extract_content_and_links(session)
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
        needs_domcontentloaded_wait = canonical_name not in {"screenshot", "wait"}

        try:
            if canonical_name == "hover":
                coords = raw_arguments.get("coordinates")
                if coords is None:
                    raise BrowserActionError("hover requires coordinates")
                x, y = scale_coordinates(coords, width, height)
                # Lead the real page hover with the branded cursor. The cursor
                # transition delay now sits on the critical path before the DOM
                # mutation so the headed sequence reads naturally.
                await self._best_effort_overlay_preview_action(action_type="hover", x=x, y=y)
                await page.mouse.move(x, y)

            elif canonical_name in {
                "left_click",
                "double_click",
                "triple_click",
                "right_click",
            }:
                coords = raw_arguments.get("coordinates")
                if coords is None:
                    raise BrowserActionError(f"{canonical_name} requires coordinates")
                x, y = scale_coordinates(coords, width, height)
                await self._best_effort_overlay_preview_action(
                    action_type=canonical_name,
                    x=x,
                    y=y,
                    num_clicks=3 if canonical_name == "triple_click" else 2 if canonical_name == "double_click" else 1,
                )
                if canonical_name == "double_click":
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
                await self._best_effort_overlay_preview_action(
                    action_type="drag",
                    x=end_x,
                    y=end_y,
                    start_x=start_x,
                    start_y=start_y,
                )
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
                await self._best_effort_overlay_preview_action(action_type="scroll", x=x, y=y, direction=direction)
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
                await self._best_effort_overlay_preview_action(action_type="type")
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
                key_sequence = expand_key_sequence(key_comb)
                if not key_sequence:
                    raise BrowserActionError("key_press requires key_comb")
                if len(key_sequence) == 1:
                    semantic_action = KEY_COMBINATION_ACTIONS.get(key_sequence[0])
                    if semantic_action is not None:
                        await self.execute_action(session, semantic_action, {})
                        return trace
                await self._best_effort_overlay_set_status("Pressing keys")
                for key_name in key_sequence:
                    if is_disallowed_zoom_shortcut(key_name):
                        continue
                    await page.keyboard.press(key_name)

            elif canonical_name == "goto_url":
                url = raw_arguments.get("url") or raw_arguments.get("href")
                if not url:
                    raise BrowserActionError("goto_url requires url")
                await self._best_effort_overlay_set_status("Navigating")
                await page.goto(url, wait_until="domcontentloaded")

            elif canonical_name == "go_back":
                await self._best_effort_overlay_set_status("Navigating")
                await page.go_back(wait_until="domcontentloaded")

            elif canonical_name == "go_forward":
                await self._best_effort_overlay_set_status("Navigating")
                await page.go_forward(wait_until="domcontentloaded")

            elif canonical_name == "refresh":
                await self._best_effort_overlay_set_status("Refreshing")
                await page.reload(wait_until="domcontentloaded")

            elif canonical_name == "screenshot":
                pass

            elif canonical_name == "wait":
                await self._best_effort_overlay_set_status("Waiting")
                await asyncio.sleep(float(raw_arguments.get("seconds", DEFAULT_WAIT_SECONDS)))

            else:
                raise BrowserActionError(f"unsupported action: {canonical_name}")

        except BrowserActionError:
            raise
        except Exception as exc:  # pragma: no cover - exercised through integration tests
            raise BrowserActionError(f"failed to execute {trace}: {exc}") from exc

        if needs_domcontentloaded_wait:
            await self._best_effort_wait_for_domcontentloaded(page)
            await asyncio.sleep(self._post_action_delay(canonical_name))
        return trace

    async def _execute_extract_content_and_links(self, session: BrowserSession) -> ToolExecutionResult:
        await self._best_effort_overlay_set_status("Reading page")
        output_text = await self._extract_content_and_links(session.page)
        return ToolExecutionResult(
            trace=f"{EXTRACT_CONTENT_AND_LINKS_TOOL_NAME}()",
            output_text=output_text,
            current_url=session.page.url,
        )

    async def _extract_content_and_links(self, page: Any) -> str:
        snapshot = await self._accessible_page_snapshot(page)
        links = self._extract_links_from_snapshot(snapshot) if snapshot else []

        sections = [f"Current URL: {page.url}"]
        if snapshot:
            sections.extend(
                [
                    "Accessible page snapshot:",
                    self._clip_multiline_text(snapshot, MAX_ACCESSIBLE_SNAPSHOT_CHARS),
                ]
            )
        if links:
            sections.extend(
                [
                    "Links on the page:",
                    "\n".join(f"- [{title}]({url})" for title, url in links),
                ]
            )
        return "\n\n".join(sections)

    async def _accessible_page_snapshot(self, page: Any) -> str | None:
        locator = getattr(page, "locator", None)
        if callable(locator):
            try:
                body = locator("body")
                aria_snapshot = getattr(body, "aria_snapshot", None)
                if callable(aria_snapshot):
                    snapshot = await aria_snapshot()
                    if snapshot:
                        return str(snapshot).strip() or None
            except Exception:
                logger.debug("body aria_snapshot failed; falling back to innerText", exc_info=True)

        try:
            snapshot = await page.evaluate(
                """() => {
                    const text = (document.body?.innerText || "").replace(/\\n{3,}/g, "\\n\\n").trim();
                    return text || null;
                }"""
            )
        except Exception:
            logger.debug("innerText fallback failed for extract_content_and_links", exc_info=True)
            return None
        if not snapshot:
            return None
        return str(snapshot).strip() or None

    @staticmethod
    def _extract_links_from_snapshot(snapshot: str) -> list[tuple[str, str]]:
        url_to_title: dict[str, str] = {}
        lines = snapshot.splitlines()
        for index, line in enumerate(lines):
            link_match = _ARIA_LINK_PATTERN.search(line)
            if link_match is None:
                continue
            title = _LINK_TITLE_CLEANER_PATTERN.sub("", link_match.group(1)).strip()
            if not title:
                continue

            url: str | None = None
            child_indent = len(line) - len(line.lstrip()) + 2
            for next_line in lines[index + 1 :]:
                if next_line.strip() and not next_line.startswith(" " * child_indent):
                    break
                url_match = _ARIA_URL_PATTERN.search(next_line)
                if url_match is not None:
                    url = url_match.group(1).strip()
                    break

            if not url:
                continue

            existing_title = url_to_title.get(url)
            if existing_title is None or len(title) > len(existing_title):
                url_to_title[url] = title

        return [(title, url) for url, title in url_to_title.items()]

    @staticmethod
    def _clip_multiline_text(text: str, limit: int) -> str:
        normalized = text.strip()
        if len(normalized) <= limit:
            return normalized
        return normalized[: max(limit - 3, 0)].rstrip() + "..."

    async def _best_effort_wait_for_domcontentloaded(self, page: Any) -> None:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=self.navigation_timeout_ms)
        except Exception:
            return

    async def _best_effort_overlay_preview_action(self, **kwargs: Any) -> None:
        overlay = self._overlay
        if overlay is None:
            return
        preview_action = getattr(overlay, "preview_action", None)
        if not callable(preview_action):
            return
        try:
            await preview_action(**kwargs)
        except Exception:
            logger.debug("Overlay preview_action failed", exc_info=True)

    async def _best_effort_overlay_set_status(self, label: str) -> None:
        overlay = self._overlay
        if overlay is None:
            return
        set_status = getattr(overlay, "set_status", None)
        if not callable(set_status):
            return
        try:
            await set_status(label)
        except Exception:
            logger.debug("Overlay set_status failed", exc_info=True)

    def _post_action_delay(self, action_name: str) -> float:
        if self.settle_delay_seconds is not None:
            return self.settle_delay_seconds
        return ACTION_DELAY_SECONDS.get(action_name, 0.3)
