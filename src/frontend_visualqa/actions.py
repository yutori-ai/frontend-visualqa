"""Navigator browser action execution against Playwright."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from frontend_visualqa.browser import (
    BrowserSession,
    DEFAULT_NAVIGATION_TIMEOUT_MS as BROWSER_NAVIGATION_TIMEOUT_MS,
    DEFAULT_PAGE_READY_TIMEOUT_SECONDS,
)
from frontend_visualqa.errors import BrowserActionError
from frontend_visualqa.tool_arguments import parse_tool_arguments
from yutori.navigator import (
    denormalize_coordinates,
    map_key_to_playwright,
    map_keys_individual,
)
from yutori.navigator.page_ready import PageReadyChecker
from yutori.navigator.tools import GET_ELEMENT_BY_REF_SCRIPT, evaluate_tool_script

if TYPE_CHECKING:
    from frontend_visualqa.overlay import OverlayController

logger = logging.getLogger(__name__)

DEFAULT_WAIT_SECONDS = 1.0
EXTRACT_CONTENT_AND_LINKS_TOOL_NAME = "extract_content_and_links"
MAX_ACCESSIBLE_SNAPSHOT_CHARS = 4_000
_ARIA_LINK_PATTERN = re.compile(r'- link "([^"]*)"')
_ARIA_URL_PATTERN = re.compile(r"- /url: (.+)")

CLICK_ACTIONS = {
    "left_click",
    "double_click",
    "triple_click",
    "middle_click",
    "right_click",
}
MOVE_ACTIONS = {"hover", "mouse_move"}

ACTION_DELAY_SECONDS: dict[str, float] = {
    "left_click": 0.25,
    "double_click": 0.25,
    "triple_click": 0.25,
    "middle_click": 0.25,
    "right_click": 0.25,
    "drag": 0.35,
    "type": 0.2,
    "hover": 0.15,
    "mouse_move": 0.15,
    "mouse_down": 0.15,
    "mouse_up": 0.15,
    "scroll": 0.15,
    "key_press": 0.15,
    "hold_key": 0.15,
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

KEY_COMBINATION_ACTIONS: dict[str, str] = {
    "Alt+ArrowLeft": "go_back",
    "Alt+ArrowRight": "go_forward",
    "F5": "refresh",
}

DISALLOWED_ZOOM_KEYS = {"-", "+", "=", "0"}


def _mapped_key_presses(key_text: str) -> list[str]:
    stripped = key_text.strip()
    if not stripped:
        return []
    return map_key_to_playwright(stripped)


def _map_modifier_keys(modifier: Any) -> list[str]:
    if modifier is None:
        return []
    if isinstance(modifier, str):
        return map_keys_individual(modifier)
    if isinstance(modifier, (list, tuple)):
        keys: list[str] = []
        for item in modifier:
            if isinstance(item, str):
                keys.extend(map_keys_individual(item))
        return keys
    return []


def _format_modifier_trace_suffix(modifier: Any) -> str:
    modifier_keys = _map_modifier_keys(modifier)
    if not modifier_keys:
        return ""
    return f", modifier={'+'.join(modifier_keys)}"


def is_disallowed_zoom_shortcut(key_text: str) -> bool:
    """Return true when the key chord would change the browser zoom level."""

    parts = [part for part in key_text.split("+") if part]
    return any(part in {"Control", "Meta", "ControlOrMeta"} for part in parts) and any(
        part in DISALLOWED_ZOOM_KEYS for part in parts[1:]
    )

def render_action_trace(
    action_name: str,
    arguments: dict[str, Any],
    *,
    width: int | None = None,
    height: int | None = None,
) -> str:
    """Render a compact trace line for a tool call."""

    canonical_name = ACTION_NAME_ALIASES.get(action_name, action_name)

    if canonical_name in CLICK_ACTIONS | MOVE_ACTIONS and width and height:
        coordinates = arguments.get("coordinates")
        if coordinates is not None:
            x, y = denormalize_coordinates(coordinates, width=width, height=height)
            modifier_suffix = _format_modifier_trace_suffix(arguments.get("modifier")) if canonical_name in CLICK_ACTIONS else ""
            return f"{canonical_name}([{x}, {y}]{modifier_suffix})"

    if canonical_name == "drag" and width and height:
        start = arguments.get("start_coordinates")
        end = arguments.get("coordinates")
        if start is not None and end is not None:
            start_x, start_y = denormalize_coordinates(start, width=width, height=height)
            end_x, end_y = denormalize_coordinates(end, width=width, height=height)
            return f"drag([{start_x}, {start_y}], [{end_x}, {end_y}])"

    if canonical_name == "scroll" and width and height:
        coordinates = arguments.get("coordinates", [500, 500])
        x, y = denormalize_coordinates(coordinates, width=width, height=height)
        direction = str(arguments.get("direction", "down")).lower()
        amount = arguments.get("amount", 1)
        modifier_suffix = _format_modifier_trace_suffix(arguments.get("modifier"))
        return f"scroll([{x}, {y}], direction={direction}, amount={amount}{modifier_suffix})"

    if canonical_name == "type":
        text = json.dumps(str(arguments.get("text", "")))
        press_enter = bool(arguments.get("press_enter_after"))
        clear_before = bool(
            arguments.get("clear_before_typing") or arguments.get("clear_before") or arguments.get("clear_before_type")
        )
        return f"type({text}, press_enter_after={press_enter}, clear_before={clear_before})"

    if canonical_name == "key_press":
        key_comb = str(arguments.get("key") or arguments.get("key_comb") or "")
        key_sequence = _mapped_key_presses(key_comb)
        if not key_sequence:
            return "key_press()"
        if len(key_sequence) == 1:
            return f"key_press({key_sequence[0]})"
        return f"key_press_sequence({', '.join(key_sequence)})"

    if canonical_name == "hold_key":
        key_text = str(arguments.get("key") or arguments.get("key_comb") or "")
        key_sequence = map_keys_individual(key_text)
        rendered = "+".join(key_sequence) if key_sequence else ""
        duration = arguments.get("duration")
        if rendered and duration is not None:
            return f"hold_key({rendered}, duration={duration})"
        if rendered:
            return f"hold_key({rendered})"
        return "hold_key()"

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
    """Execute Navigator browser action tool calls against a Playwright page."""

    def __init__(
        self,
        *,
        navigation_timeout_ms: int = BROWSER_NAVIGATION_TIMEOUT_MS,
        settle_delay_seconds: float | None = None,
        page_ready_checker: PageReadyChecker | None = None,
    ) -> None:
        self.navigation_timeout_ms = navigation_timeout_ms
        self.settle_delay_seconds = settle_delay_seconds
        self.page_ready_checker = page_ready_checker or PageReadyChecker(
            timeout=min(DEFAULT_PAGE_READY_TIMEOUT_SECONDS, max(1, int(navigation_timeout_ms / 1000))),
            initial_wait=0.0,
            wait_after_ready=0.0,
            replace_native_select_dropdown=True,
            disable_new_tabs=True,
            disable_printing=True,
            poll_interval=0.1,
        )
        self._overlay: OverlayController | None = None

    @property
    def overlay(self) -> OverlayController | None:
        return self._overlay

    @overlay.setter
    def overlay(self, value: OverlayController | None) -> None:
        self._overlay = value

    async def execute_tool_call(self, session: BrowserSession, tool_call: Any) -> ToolExecutionResult | str:
        """Execute a tool call object with ``function.name`` and JSON arguments."""

        action_name = getattr(getattr(tool_call, "function", tool_call), "name", "")
        canonical_name = ACTION_NAME_ALIASES.get(action_name, action_name)
        if canonical_name == EXTRACT_CONTENT_AND_LINKS_TOOL_NAME:
            return await self._execute_extract_content_and_links(session)
        arguments = parse_tool_arguments(tool_call)
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
            action_name,
            raw_arguments,
            width=session.viewport.width,
            height=session.viewport.height,
        )
        page = session.page
        width = session.viewport.width
        height = session.viewport.height
        needs_domcontentloaded_wait = canonical_name not in {"screenshot", "wait"}

        try:
            if canonical_name in MOVE_ACTIONS:
                x, y = await self._resolve_coordinates(
                    page,
                    raw_arguments,
                    width=width,
                    height=height,
                    action_name=canonical_name,
                )
                # Lead the real page hover with the branded cursor. The cursor
                # transition delay now sits on the critical path before the DOM
                # mutation so the headed sequence reads naturally.
                await self._best_effort_overlay_preview_action(action_type=canonical_name, x=x, y=y)
                await page.mouse.move(x, y)

            elif canonical_name in CLICK_ACTIONS:
                x, y = await self._resolve_coordinates(
                    page,
                    raw_arguments,
                    width=width,
                    height=height,
                    action_name=canonical_name,
                )
                await self._best_effort_overlay_preview_action(
                    action_type=canonical_name,
                    x=x,
                    y=y,
                    num_clicks=3 if canonical_name == "triple_click" else 2 if canonical_name == "double_click" else 1,
                )
                modifier_keys = await self._press_modifier_keys(page, raw_arguments.get("modifier"))
                try:
                    if canonical_name == "double_click":
                        await page.mouse.dblclick(x, y)
                    else:
                        click_count = 3 if canonical_name == "triple_click" else 1
                        button = {"middle_click": "middle", "right_click": "right"}.get(canonical_name, "left")
                        await page.mouse.click(x, y, button=button, click_count=click_count)
                finally:
                    await self._release_modifier_keys(page, modifier_keys)

            elif canonical_name == "drag":
                start = raw_arguments.get("start_coordinates")
                end = raw_arguments.get("coordinates")
                if start is None or end is None:
                    raise BrowserActionError("drag requires start_coordinates and coordinates")
                start_x, start_y = denormalize_coordinates(start, width=width, height=height)
                end_x, end_y = denormalize_coordinates(end, width=width, height=height)
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

            elif canonical_name == "mouse_down":
                x, y = await self._resolve_coordinates(
                    page,
                    raw_arguments,
                    width=width,
                    height=height,
                    action_name=canonical_name,
                )
                await self._best_effort_overlay_preview_action(action_type=canonical_name, x=x, y=y)
                await page.mouse.move(x, y)
                await page.mouse.down()

            elif canonical_name == "mouse_up":
                x, y = await self._resolve_coordinates(
                    page,
                    raw_arguments,
                    width=width,
                    height=height,
                    action_name=canonical_name,
                )
                await self._best_effort_overlay_preview_action(action_type=canonical_name, x=x, y=y)
                await page.mouse.move(x, y)
                await page.mouse.up()

            elif canonical_name == "scroll":
                direction = str(raw_arguments.get("direction", "down")).lower()
                amount = float(raw_arguments.get("amount", 1))
                if direction not in {"up", "down", "left", "right"}:
                    raise BrowserActionError(f"unsupported scroll direction: {direction}")
                # Resolve coordinates from ref or raw coordinates, then share
                # the overlay/modifier/wheel logic for both paths.
                if raw_arguments.get("ref") and raw_arguments.get("coordinates") is None:
                    x, y = await self._resolve_coordinates(
                        page, raw_arguments, width=width, height=height, action_name=canonical_name,
                    )
                else:
                    coords = raw_arguments.get("coordinates", [500, 500])
                    x, y = denormalize_coordinates(coords, width=width, height=height)
                await self._best_effort_overlay_preview_action(action_type="scroll", x=x, y=y, direction=direction)
                modifier_keys = await self._press_modifier_keys(page, raw_arguments.get("modifier"))
                try:
                    await page.mouse.move(x, y)
                    scroll_deltas = {
                        "right": (width * 0.1, 0.0),
                        "left": (-width * 0.1, 0.0),
                        "down": (0.0, height * 0.1),
                        "up": (0.0, -height * 0.1),
                    }
                    dx, dy = scroll_deltas[direction]
                    await page.mouse.wheel(dx * amount, dy * amount)
                finally:
                    await self._release_modifier_keys(page, modifier_keys)

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
                key_comb = str(raw_arguments.get("key") or raw_arguments.get("key_comb") or "")
                if not key_comb:
                    raise BrowserActionError("key_press requires key")
                key_sequence = _mapped_key_presses(key_comb)
                if not key_sequence:
                    raise BrowserActionError("key_press requires key")
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

            elif canonical_name == "hold_key":
                key_text = str(raw_arguments.get("key") or raw_arguments.get("key_comb") or "")
                if not key_text:
                    raise BrowserActionError("hold_key requires key")
                hold_duration = raw_arguments.get("duration")
                if hold_duration is not None and float(hold_duration) > 0:
                    modifier_keys = map_keys_individual(key_text)
                    await self._best_effort_overlay_set_status("Holding key")
                    for key_name in modifier_keys:
                        await page.keyboard.down(key_name)
                    try:
                        await asyncio.sleep(min(float(hold_duration), 100.0))
                    finally:
                        for key_name in reversed(modifier_keys):
                            await page.keyboard.up(key_name)
                else:
                    for key_name in _mapped_key_presses(key_text):
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
                await asyncio.sleep(float(raw_arguments.get("duration") or raw_arguments.get("seconds", DEFAULT_WAIT_SECONDS)))

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
        if not links:
            links = await self._extract_links_from_dom(page)

        sections = []
        if snapshot:
            sections.extend([
                "Accessible page snapshot:",
                self._clip_multiline_text(snapshot, MAX_ACCESSIBLE_SNAPSHOT_CHARS),
            ])
        if links:
            links_text = "\n".join(f"- [{title}]({url})" for title, url in links)
            sections.extend([
                "Links on the page:",
                self._clip_multiline_text(links_text, MAX_ACCESSIBLE_SNAPSHOT_CHARS),
            ])
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
            title = link_match.group(1).strip()
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
    async def _extract_links_from_dom(page: Any) -> list[tuple[str, str]]:
        try:
            raw = await page.evaluate(
                """() => {
                    const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                    const isVisible = (element) => {
                        if (!element) return false;
                        const style = window.getComputedStyle(element);
                        if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) {
                            return false;
                        }
                        const rect = element.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0;
                    };
                    const links = [];
                    for (const a of document.querySelectorAll('a[href]')) {
                        if (!isVisible(a)) continue;
                        const title = normalize(a.innerText || a.textContent || a.getAttribute('aria-label') || a.getAttribute('title') || '');
                        const url = a.href;
                        if (title && url) links.push([title, url]);
                    }
                    return links;
                }"""
            )
        except Exception:
            return []
        if not raw or not isinstance(raw, list):
            return []
        url_to_title: dict[str, str] = {}
        for pair in raw:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                title, url = str(pair[0]).strip(), str(pair[1]).strip()
                if title and url:
                    existing = url_to_title.get(url)
                    if existing is None or len(title) > len(existing):
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
        try:
            await self.page_ready_checker.wait_until_ready(page, fast_mode=self.settle_delay_seconds == 0)
        except Exception:
            logger.warning("Page ready check failed; continuing with action", exc_info=True)

    async def _resolve_coordinates(
        self,
        page: Any,
        arguments: dict[str, Any],
        *,
        width: int,
        height: int,
        action_name: str,
    ) -> tuple[int, int]:
        ref = arguments.get("ref")
        coordinates = arguments.get("coordinates")

        if ref:
            try:
                result = await evaluate_tool_script(page, GET_ELEMENT_BY_REF_SCRIPT, ref)
            except Exception as exc:  # pragma: no cover - defensive around browser evaluate failures
                result = {"success": False, "message": str(exc)}
            if result.get("success"):
                resolved_coordinates = result.get("coordinates")
                if isinstance(resolved_coordinates, list | tuple) and len(resolved_coordinates) == 2:
                    return int(round(float(resolved_coordinates[0]))), int(round(float(resolved_coordinates[1])))
            if coordinates is None:
                message = result.get("message", "Unknown error")
                raise BrowserActionError(f"{action_name} ref resolution failed for {ref}: {message}")
            logger.warning(
                "Ref %s failed for %s (%s); falling back to coordinates %s",
                ref,
                action_name,
                result.get("message", "Unknown error"),
                coordinates,
            )

        if coordinates is None:
            raise BrowserActionError(f"{action_name} requires coordinates")
        return denormalize_coordinates(coordinates, width=width, height=height)

    @staticmethod
    async def _press_modifier_keys(page: Any, modifier: Any) -> list[str]:
        modifier_keys = _map_modifier_keys(modifier)
        for key_name in modifier_keys:
            await page.keyboard.down(key_name)
        return modifier_keys

    @staticmethod
    async def _release_modifier_keys(page: Any, modifier_keys: list[str]) -> None:
        for key_name in reversed(modifier_keys):
            await page.keyboard.up(key_name)

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
