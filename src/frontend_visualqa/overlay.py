"""In-browser visual overlay for headed mode action visualization."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page


logger = logging.getLogger(__name__)

Z_INDEX = 2_147_483_646
YUTORI_GREEN = "#1DCD98"
LEAD_TIME_MS = 450
EFFECT_DURATION_MS = 600
BORDER_CYCLE_MS = 4000

PERSISTENT_ROOT_ID = "__n1PersistentRoot"
TRANSIENT_ROOT_ID = "__n1TransientRoot"
GRADIENT_BORDER_ID = "__n1GradientBorder"
STATUS_CHIP_ID = "__n1StatusChip"
CLICK_STYLE_ID = "__n1ClickStyle"
SCROLL_STYLE_ID = "__n1ScrollStyle"
TYPE_STYLE_ID = "__n1TypeStyle"

_ROOT_STYLE = (
    f"position:fixed;top:0;left:0;right:0;bottom:0;"
    f"pointer-events:none;z-index:{Z_INDEX};visibility:visible;opacity:1;"
)


def _set_visibility_opacity_js(element_ref: str, *, visibility: str, opacity: str) -> str:
    return (
        f"{element_ref}.style.visibility = '{visibility}';"
        f"{element_ref}.style.opacity = '{opacity}';"
    )

_PERSISTENT_ROOT_JS = f"""() => {{
    if (document.getElementById('{PERSISTENT_ROOT_ID}')) return;
    const root = document.createElement('div');
    root.id = '{PERSISTENT_ROOT_ID}';
    root.style.cssText = '{_ROOT_STYLE}';

    const border = document.createElement('div');
    border.id = '{GRADIENT_BORDER_ID}';
    border.style.cssText = 'position:absolute;inset:0;pointer-events:none;filter:blur(8px);';
    root.appendChild(border);

    const chip = document.createElement('div');
    chip.id = '{STATUS_CHIP_ID}';
    chip.style.cssText = 'position:fixed;top:12px;right:12px;background:{YUTORI_GREEN};color:#000;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;font-size:10px;font-weight:700;letter-spacing:0.6px;text-transform:uppercase;padding:6px 12px;border-radius:999px;box-shadow:0 2px 10px rgba(29,205,152,0.45);z-index:{Z_INDEX + 1};';
    chip.textContent = 'Analyzing';
    root.appendChild(chip);

    document.documentElement.appendChild(root);

    const duration = {BORDER_CYCLE_MS};
    const easeInOut = (value) => value < 0.5 ? 2 * value * value : 1 - Math.pow(-2 * value + 2, 2) / 2;
    const lerp = (start, end, value) => start + (end - start) * value;
    let startTime = null;

    const animate = (timestamp) => {{
        if (!startTime) startTime = timestamp;
        const cycle = ((timestamp - startTime) % duration) / duration;
        const pingPong = cycle < 0.5 ? cycle * 2 : 2 - cycle * 2;
        const eased = easeInOut(pingPong);
        const opacity = lerp(0.25, 0.4, eased);
        const spread = lerp(8, 15, eased);
        const borderElement = document.getElementById('{GRADIENT_BORDER_ID}');
        if (!borderElement) return;
        borderElement.style.background =
            'linear-gradient(to right, rgba(29,205,152,' + opacity + ') 0%, transparent ' + spread + '%),' +
            'linear-gradient(to left, rgba(29,205,152,' + opacity + ') 0%, transparent ' + spread + '%),' +
            'linear-gradient(to bottom, rgba(29,205,152,' + opacity + ') 0%, transparent ' + spread + '%),' +
            'linear-gradient(to top, rgba(29,205,152,' + opacity + ') 0%, transparent ' + spread + '%)';
        root.__n1AnimationFrame = requestAnimationFrame(animate);
    }};

    root.__n1AnimationFrame = requestAnimationFrame(animate);
}}"""

_TRANSIENT_ROOT_JS = f"""() => {{
    let root = document.getElementById('{TRANSIENT_ROOT_ID}');
    if (root) {{
        {_set_visibility_opacity_js("root", visibility="visible", opacity="1")}
        return;
    }}

    root = document.createElement('div');
    root.id = '{TRANSIENT_ROOT_ID}';
    root.style.cssText = '{_ROOT_STYLE}';
    document.documentElement.appendChild(root);
}}"""

_REMOVE_ALL_JS = f"""() => {{
    const persistent = document.getElementById('{PERSISTENT_ROOT_ID}');
    if (persistent) {{
        if (persistent.__n1AnimationFrame) cancelAnimationFrame(persistent.__n1AnimationFrame);
        persistent.remove();
    }}
    const transient = document.getElementById('{TRANSIENT_ROOT_ID}');
    if (transient) transient.remove();
    for (const styleId of ['{CLICK_STYLE_ID}', '{SCROLL_STYLE_ID}', '{TYPE_STYLE_ID}']) {{
        const style = document.getElementById(styleId);
        if (style) style.remove();
    }}
}}"""

_HIDE_BOTH_JS = f"""() => {{
    // Keep the full-viewport overlay layers mounted during capture.
    // In headed Chromium, toggling display on these layers can trigger
    // a visible compositor snap even though the page viewport is unchanged.
    const persistent = document.getElementById('{PERSISTENT_ROOT_ID}');
    if (persistent) {{
        {_set_visibility_opacity_js("persistent", visibility="hidden", opacity="0")}
    }}
    const transient = document.getElementById('{TRANSIENT_ROOT_ID}');
    if (transient) {{
        {_set_visibility_opacity_js("transient", visibility="hidden", opacity="0")}
    }}
}}"""

_RESTORE_PERSISTENT_JS = f"""() => {{
    const persistent = document.getElementById('{PERSISTENT_ROOT_ID}');
    if (persistent) {{
        {_set_visibility_opacity_js("persistent", visibility="visible", opacity="1")}
    }}
}}"""

_CHECK_PERSISTENT_JS = f"!!document.getElementById('{PERSISTENT_ROOT_ID}')"


class OverlayController:
    """Manage in-browser visual effects for a single claim lifecycle."""

    def __init__(self, page: Page) -> None:
        self._page = page
        self._active = False
        self._current_status = "Analyzing"

    async def claim_started(self) -> None:
        self._active = True
        self._current_status = "Analyzing"
        await self._inject_persistent_root()
        await self._ensure_transient_root()

    async def claim_ended(self) -> None:
        if not self._active:
            return
        self._active = False
        await self._eval(_REMOVE_ALL_JS)

    async def show_action(
        self,
        action_type: str,
        *,
        x: int = 0,
        y: int = 0,
        num_clicks: int = 1,
        direction: str = "down",
    ) -> None:
        if not self._active:
            return

        if action_type in {"left_click", "double_click", "triple_click", "right_click"}:
            await self._show_click_effect(x, y, num_clicks)
            await self.set_status("Clicking")
            return
        if action_type == "scroll":
            await self._show_scroll_effect(x, y, direction)
            await self.set_status("Scrolling")
            return
        if action_type == "type":
            await self._show_type_effect()
            await self.set_status("Typing")

    async def set_status(self, label: str) -> None:
        self._current_status = label
        if not self._active:
            return
        await self._eval(
            f"""() => {{
                const chip = document.getElementById('{STATUS_CHIP_ID}');
                if (chip) chip.textContent = {label!r};
            }}"""
        )

    async def before_screenshot(self) -> None:
        if not self._active:
            return
        await self._eval(_HIDE_BOTH_JS)

    async def after_screenshot(self) -> None:
        if not self._active:
            return
        await self._eval(_RESTORE_PERSISTENT_JS)

    async def ensure_persistent_ui(self) -> None:
        if not self._active:
            return
        try:
            exists = await self._page.evaluate(_CHECK_PERSISTENT_JS)
        except Exception:
            exists = False
        if not exists:
            await self._inject_persistent_root()

    async def _inject_persistent_root(self) -> None:
        await self._eval(_PERSISTENT_ROOT_JS)
        if self._current_status != "Analyzing":
            await self.set_status(self._current_status)

    async def _show_click_effect(self, x: int, y: int, num_clicks: int) -> None:
        await self._ensure_transient_root()
        pulse_gap = int(EFFECT_DURATION_MS * 0.5)
        await self._eval(
            f"""() => {{
                const root = document.getElementById('{TRANSIENT_ROOT_ID}');
                if (!root) return;
                if (!document.getElementById('{CLICK_STYLE_ID}')) {{
                    const style = document.createElement('style');
                    style.id = '{CLICK_STYLE_ID}';
                    style.textContent = '@keyframes n1click{{0%{{transform:translate(-50%,-50%) scale(0.4);opacity:1}}50%{{opacity:0.9}}100%{{transform:translate(-50%,-50%) scale(2.5);opacity:0}}}}@keyframes n1dot{{0%{{transform:translate(-50%,-50%) scale(1);opacity:1}}50%{{opacity:1}}100%{{transform:translate(-50%,-50%) scale(0.4);opacity:0}}}}';
                    document.head.appendChild(style);
                }}

                const numClicks = {num_clicks};
                const duration = {EFFECT_DURATION_MS};
                const gap = {pulse_gap};

                for (let index = 0; index < numClicks; index += 1) {{
                    const delay = index * gap;
                    const container = document.createElement('div');
                    container.style.cssText = 'position:fixed;left:{x}px;top:{y}px;pointer-events:none;z-index:{Z_INDEX};';

                    const dot = document.createElement('div');
                    dot.style.cssText = 'position:absolute;left:0;top:0;width:14px;height:14px;background:{YUTORI_GREEN};border-radius:50%;transform:translate(-50%,-50%);animation:n1dot ' + (duration / 1000) + 's ease-out forwards;animation-delay:' + delay + 'ms;opacity:0;box-shadow:0 0 16px {YUTORI_GREEN},0 0 32px rgba(29,205,152,0.5);';

                    const ring = document.createElement('div');
                    ring.style.cssText = 'position:absolute;left:0;top:0;width:50px;height:50px;border:3px solid {YUTORI_GREEN};border-radius:50%;transform:translate(-50%,-50%);animation:n1click ' + (duration / 1000) + 's ease-out forwards;animation-delay:' + delay + 'ms;opacity:0;box-shadow:0 0 20px rgba(29,205,152,0.6),inset 0 0 12px rgba(29,205,152,0.2);';

                    container.appendChild(ring);
                    container.appendChild(dot);
                    root.appendChild(container);
                    setTimeout(() => container.remove(), delay + duration + 100);
                }}
            }}"""
        )

    async def _show_scroll_effect(self, x: int, y: int, direction: str) -> None:
        await self._ensure_transient_root()
        rotation = {"up": -90, "down": 90, "left": 180, "right": 0}.get(direction, 90)
        move_x = -10 if direction == "left" else 10 if direction == "right" else 0
        move_y = -10 if direction == "up" else 10 if direction == "down" else 0
        await self._eval(
            f"""() => {{
                const root = document.getElementById('{TRANSIENT_ROOT_ID}');
                if (!root) return;
                const existing = document.getElementById('{SCROLL_STYLE_ID}');
                if (existing) existing.remove();

                const duration = {EFFECT_DURATION_MS};
                const style = document.createElement('style');
                style.id = '{SCROLL_STYLE_ID}';
                style.textContent = `@keyframes n1schev{{0%,100%{{transform:translate(0,0);opacity:0.6}}50%{{transform:translate({move_x}px,{move_y}px);opacity:1}}}}@keyframes n1sfade{{0%{{opacity:0;transform:translate(-50%,-50%) scale(0.9)}}15%{{opacity:1;transform:translate(-50%,-50%) scale(1)}}85%{{opacity:1}}100%{{opacity:0;transform:translate(-50%,-50%) scale(0.95)}}}}`;
                document.head.appendChild(style);

                const container = document.createElement('div');
                container.style.cssText = 'position:fixed;left:{x}px;top:{y}px;pointer-events:none;z-index:{Z_INDEX};transform:translate(-50%,-50%);animation:n1sfade ' + (duration / 1000) + 's ease-out forwards;';

                const box = document.createElement('div');
                box.style.cssText = 'width:56px;height:56px;border:2.5px solid {YUTORI_GREEN};border-radius:12px;display:flex;align-items:center;justify-content:center;box-shadow:0 0 20px rgba(29,205,152,0.4);';
                box.innerHTML = '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="{YUTORI_GREEN}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="transform:rotate({rotation}deg);animation:n1schev 0.7s ease-in-out infinite"><polyline points="9 18 15 12 9 6"></polyline></svg>';

                container.appendChild(box);
                root.appendChild(container);
                setTimeout(() => {{
                    container.remove();
                    style.remove();
                }}, duration);
            }}"""
        )

    async def _show_type_effect(self) -> None:
        center = await self._get_focused_element_center()
        if center is None:
            return

        x = center["x"]
        y_raw = center["y"]
        show_below = y_raw < 50
        y = y_raw + 30 if show_below else y_raw - 36
        bob_direction = "6px" if show_below else "-6px"

        await self._ensure_transient_root()
        await self._eval(
            f"""() => {{
                const root = document.getElementById('{TRANSIENT_ROOT_ID}');
                if (!root) return;
                const existing = document.getElementById('{TYPE_STYLE_ID}');
                if (existing) existing.remove();

                const duration = {EFFECT_DURATION_MS};
                const style = document.createElement('style');
                style.id = '{TYPE_STYLE_ID}';
                style.textContent = `@keyframes n1tbob{{0%,100%{{transform:translateX(-50%) translateY(0)}}50%{{transform:translateX(-50%) translateY({bob_direction})}}}}@keyframes n1tglow{{0%,100%{{box-shadow:0 4px 16px rgba(29,205,152,0.5)}}50%{{box-shadow:0 4px 28px rgba(29,205,152,0.9),0 0 12px rgba(29,205,152,0.5)}}}}@keyframes n1tfade{{0%{{opacity:0}}15%{{opacity:1}}85%{{opacity:1}}100%{{opacity:0}}}}@keyframes n1tcur{{0%,100%{{opacity:1}}50%{{opacity:0}}}}`;
                document.head.appendChild(style);

                const indicator = document.createElement('div');
                indicator.style.cssText = 'position:fixed;left:{x}px;top:{y}px;background:{YUTORI_GREEN};color:#000;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;font-size:11px;font-weight:700;letter-spacing:0.5px;text-transform:uppercase;padding:6px 12px;border-radius:999px;pointer-events:none;z-index:{Z_INDEX};transform:translateX(-50%);animation:n1tfade ' + (duration / 1000) + 's ease-out forwards,n1tbob 0.6s ease-in-out infinite,n1tglow 0.8s ease-in-out infinite;display:flex;align-items:center;gap:3px;';

                const text = document.createElement('span');
                text.textContent = 'typing';
                indicator.appendChild(text);

                for (let index = 0; index < 3; index += 1) {{
                    const dot = document.createElement('span');
                    dot.textContent = '\\u00B7';
                    dot.style.cssText = 'display:inline-block;font-size:14px;font-weight:700;line-height:1;animation:n1tbob 0.5s ease-in-out infinite;animation-delay:' + (index * 0.1) + 's;';
                    indicator.appendChild(dot);
                }}

                const cursor = document.createElement('span');
                cursor.style.cssText = 'width:2px;height:12px;background:#000;margin-left:4px;border-radius:1px;animation:n1tcur 0.53s step-end infinite;';
                indicator.appendChild(cursor);

                root.appendChild(indicator);
                setTimeout(() => {{
                    indicator.remove();
                    style.remove();
                }}, duration);
            }}"""
        )

    async def _get_focused_element_center(self) -> dict[str, int] | None:
        try:
            return await self._page.evaluate(
                """() => {
                    const element = document.activeElement;
                    if (!element || element === document.body || element === document.documentElement) return null;
                    const rect = element.getBoundingClientRect();
                    if (rect.width <= 0 || rect.height <= 0) return null;
                    return {
                        x: Math.round(rect.left + rect.width / 2),
                        y: Math.round(rect.top + rect.height / 2),
                    };
                }"""
            )
        except Exception:
            return None

    async def _ensure_transient_root(self) -> None:
        await self._eval(_TRANSIENT_ROOT_JS)

    async def _eval(self, script: str) -> None:
        try:
            await self._page.evaluate(script)
        except Exception:
            logger.debug("Overlay evaluate failed (best-effort)", exc_info=True)
