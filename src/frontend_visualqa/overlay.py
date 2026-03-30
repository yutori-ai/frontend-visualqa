"""In-browser visual overlay for headed mode action visualization."""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page


logger = logging.getLogger(__name__)

Z_INDEX = 2_147_483_646
YUTORI_GREEN = "#1DCD98"
EFFECT_DURATION_MS = 600
BORDER_CYCLE_MS = 4000

PERSISTENT_ROOT_ID = "__n1PersistentRoot"
TRANSIENT_ROOT_ID = "__n1TransientRoot"
GRADIENT_BORDER_ID = "__n1GradientBorder"
STATUS_CHIP_ID = "__n1StatusChip"
THOUGHT_CARD_ID = "__n1ThoughtCard"
THOUGHT_STYLE_ID = "__n1ThoughtStyle"
CLICK_STYLE_ID = "__n1ClickStyle"
SCROLL_STYLE_ID = "__n1ScrollStyle"
TYPE_STYLE_ID = "__n1TypeStyle"

CURSOR_ID = "__n1Cursor"
DRAG_STYLE_ID = "__n1DragStyle"
CLICK_DURATION_MS = 250
SCROLL_DURATION_MS = 1000
DRAG_DURATION_MS = 200
CURSOR_TRANSITION_MS = 80
THOUGHT_DURATION_MS = 6000

_CURSOR_SVG = (
    '<svg width="134" height="181" viewBox="0 0 134 181" fill="none" xmlns="http://www.w3.org/2000/svg">'
    '<g id="Yutori Cursor"><g id="Yutori Cursor_2" filter="url(#filter0_ddddii_2284_14081)">'
    '<path d="M31.1586 11.5639C29.9123 8.57945 32.7297 5.50285 35.812 6.48228L99.1562 26.6099C104.603 28.3406 104.391 36.1195 98.8584 37.5515L92.5099 39.1945C80.7217 42.2453 71.8947 52.0409 70.0833 64.0819L68.6149 73.8422C67.7576 79.541 59.9482 80.5074 57.7275 75.1895L31.1586 11.5639Z" fill="url(#paint0_linear_2284_14081)"/>'
    '<path d="M31.1586 11.5639C29.9123 8.57945 32.7297 5.50285 35.812 6.48228L99.1562 26.6099C104.603 28.3406 104.391 36.1195 98.8584 37.5515L92.5099 39.1945C80.7217 42.2453 71.8947 52.0409 70.0833 64.0819L68.6149 73.8422C67.7576 79.541 59.9482 80.5074 57.7275 75.1895L31.1586 11.5639Z" stroke="url(#paint1_linear_2284_14081)" stroke-width="1.89844"/>'
    '</g></g><defs>'
    '<filter id="filter0_ddddii_2284_14081" x="0.655027" y="0.845703" width="132.671" height="180.046" filterUnits="userSpaceOnUse" color-interpolation-filters="sRGB">'
    '<feFlood flood-opacity="0" result="BackgroundImageFix"/>'
    '<feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0" result="hardAlpha"/>'
    '<feOffset dy="4.5"/><feGaussianBlur stdDeviation="4.5"/>'
    '<feColorMatrix type="matrix" values="0 0 0 0 0.0627451 0 0 0 0 0.403922 0 0 0 0 0.435294 0 0 0 0.07 0"/>'
    '<feBlend mode="normal" in2="BackgroundImageFix" result="effect1_dropShadow_2284_14081"/>'
    '<feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0" result="hardAlpha"/>'
    '<feOffset dy="18"/><feGaussianBlur stdDeviation="9"/>'
    '<feColorMatrix type="matrix" values="0 0 0 0 0.0627451 0 0 0 0 0.403922 0 0 0 0 0.435294 0 0 0 0.06 0"/>'
    '<feBlend mode="normal" in2="effect1_dropShadow_2284_14081" result="effect2_dropShadow_2284_14081"/>'
    '<feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0" result="hardAlpha"/>'
    '<feOffset dy="40.5"/><feGaussianBlur stdDeviation="12.375"/>'
    '<feColorMatrix type="matrix" values="0 0 0 0 0.0627451 0 0 0 0 0.403922 0 0 0 0 0.435294 0 0 0 0.04 0"/>'
    '<feBlend mode="normal" in2="effect2_dropShadow_2284_14081" result="effect3_dropShadow_2284_14081"/>'
    '<feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0" result="hardAlpha"/>'
    '<feOffset dy="72"/><feGaussianBlur stdDeviation="14.625"/>'
    '<feColorMatrix type="matrix" values="0 0 0 0 0.0627451 0 0 0 0 0.403922 0 0 0 0 0.435294 0 0 0 0.01 0"/>'
    '<feBlend mode="normal" in2="effect3_dropShadow_2284_14081" result="effect4_dropShadow_2284_14081"/>'
    '<feBlend mode="normal" in="SourceGraphic" in2="effect4_dropShadow_2284_14081" result="shape"/>'
    '<feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0" result="hardAlpha"/>'
    '<feOffset dx="2.25" dy="-4.5"/><feGaussianBlur stdDeviation="4.5"/>'
    '<feComposite in2="hardAlpha" operator="arithmetic" k2="-1" k3="1"/>'
    '<feColorMatrix type="matrix" values="0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 0.15 0"/>'
    '<feBlend mode="normal" in2="shape" result="effect5_innerShadow_2284_14081"/>'
    '<feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0" result="hardAlpha"/>'
    '<feOffset dx="-2.25" dy="6.75"/><feGaussianBlur stdDeviation="4.5"/>'
    '<feComposite in2="hardAlpha" operator="arithmetic" k2="-1" k3="1"/>'
    '<feColorMatrix type="matrix" values="0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 0.4 0"/>'
    '<feBlend mode="normal" in2="effect5_innerShadow_2284_14081" result="effect6_innerShadow_2284_14081"/>'
    '</filter>'
    '<linearGradient id="paint0_linear_2284_14081" x1="79.2133" y1="2.92857" x2="31.285" y2="73.7171" gradientUnits="userSpaceOnUse">'
    '<stop stop-color="#18AA7E"/><stop offset="0.45" stop-color="#148F6A"/>'
    '<stop offset="0.75" stop-color="#148F6A"/><stop offset="1" stop-color="#159871"/>'
    '</linearGradient>'
    '<linearGradient id="paint1_linear_2284_14081" x1="78.201" y1="2.92858" x2="32.6124" y2="76.674" gradientUnits="userSpaceOnUse">'
    '<stop stop-color="#5AE8BD"/><stop offset="0.5" stop-color="#127D5D"/>'
    '<stop offset="0.9" stop-color="#148F6A"/><stop offset="1" stop-color="#19B385"/>'
    '</linearGradient>'
    '</defs></svg>'
)

_CURSOR_DATA_URI = "data:image/svg+xml;base64," + base64.b64encode(_CURSOR_SVG.encode()).decode()

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
    border.style.cssText = 'position:absolute;inset:0;pointer-events:none;filter:blur(4px);';
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
        const spread = lerp(3, 6, eased);
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

_THOUGHT_CARD_JS = f"""(text) => {{
    const root = document.getElementById('{PERSISTENT_ROOT_ID}');
    if (!root) return;
    const existing = document.getElementById('{THOUGHT_CARD_ID}');
    if (existing) existing.remove();

    const style = document.getElementById('{THOUGHT_STYLE_ID}') || document.createElement('style');
    style.id = '{THOUGHT_STYLE_ID}';
    style.textContent = '@keyframes n1thoughtShimmer{{0%{{background-position:0% 50%}}100%{{background-position:200% 50%}}}}';
    if (!style.isConnected) root.appendChild(style);

    const card = document.createElement('div');
    card.id = '{THOUGHT_CARD_ID}';
    card.style.cssText = 'position:fixed;top:76px;left:50%;transform:translateX(-50%);width:min(720px,calc(100vw - 48px));pointer-events:none;z-index:{Z_INDEX + 1};padding:22px 24px;border-radius:22px;background:linear-gradient(180deg,rgba(8,16,20,0.96),rgba(6,12,16,0.92));border:1px solid rgba(29,205,152,0.28);box-shadow:0 18px 50px rgba(0,0,0,0.28),0 0 0 1px rgba(29,205,152,0.08) inset;backdrop-filter:blur(12px);color:#eef6f3;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;';
    const header = document.createElement('div');
    header.style.cssText = 'display:flex;align-items:center;gap:10px;margin-bottom:12px;';
    const badge = document.createElement('span');
    badge.style.cssText = 'display:inline-block;padding:5px 10px;border-radius:999px;background:linear-gradient(90deg,rgba(29,205,152,0.22),rgba(90,232,189,0.45),rgba(29,205,152,0.22));background-size:200% 200%;animation:n1thoughtShimmer 1.6s linear infinite;color:#d9fff1;font-size:11px;font-weight:800;letter-spacing:0.9px;text-transform:uppercase;';
    badge.textContent = 'Thinking';
    header.appendChild(badge);

    const body = document.createElement('div');
    body.style.cssText = 'font-size:17px;line-height:1.55;color:rgba(238,246,243,0.95);display:-webkit-box;-webkit-line-clamp:6;-webkit-box-orient:vertical;overflow:hidden;word-break:break-word;';
    body.textContent = text;

    card.appendChild(header);
    card.appendChild(body);
    root.appendChild(card);

    const previousTimer = root.__n1ThoughtTimer;
    if (previousTimer) clearTimeout(previousTimer);
    root.__n1ThoughtTimer = setTimeout(() => {{
        const current = document.getElementById('{THOUGHT_CARD_ID}');
        if (current) current.remove();
    }}, {THOUGHT_DURATION_MS});
}}"""

_TRANSIENT_ROOT_JS = f"""() => {{
    let root = document.getElementById('{TRANSIENT_ROOT_ID}');
    if (!root) {{
        root = document.createElement('div');
        root.id = '{TRANSIENT_ROOT_ID}';
        root.style.cssText = '{_ROOT_STYLE}';
        document.documentElement.appendChild(root);
    }}
    {_set_visibility_opacity_js("root", visibility="visible", opacity="1")}

    if (!document.getElementById('{CURSOR_ID}')) {{
        const cursor = document.createElement('img');
        cursor.id = '{CURSOR_ID}';
        cursor.src = '{_CURSOR_DATA_URI}';
        cursor.style.cssText = 'position:fixed;left:-200px;top:-200px;width:75px;height:101px;pointer-events:none;z-index:{Z_INDEX + 2};transition:left {CURSOR_TRANSITION_MS}ms ease-in-out,top {CURSOR_TRANSITION_MS}ms ease-in-out;transform:translate(-17px,-4px);filter:drop-shadow(0 2px 5px rgba(0,0,0,0.18));';
        root.appendChild(cursor);
    }}
}}"""

_REMOVE_ALL_JS = f"""() => {{
    const persistent = document.getElementById('{PERSISTENT_ROOT_ID}');
    if (persistent) {{
        if (persistent.__n1AnimationFrame) cancelAnimationFrame(persistent.__n1AnimationFrame);
        if (persistent.__n1ThoughtTimer) clearTimeout(persistent.__n1ThoughtTimer);
        persistent.remove();
    }}
    const transient = document.getElementById('{TRANSIENT_ROOT_ID}');
    if (transient) transient.remove();
    for (const styleId of ['{CLICK_STYLE_ID}', '{SCROLL_STYLE_ID}', '{TYPE_STYLE_ID}', '{DRAG_STYLE_ID}', '{THOUGHT_STYLE_ID}']) {{
        const style = document.getElementById(styleId);
        if (style) style.remove();
    }}
}}"""

def _toggle_both_roots_js(*, visibility: str, opacity: str) -> str:
    """Generate JS to set visibility/opacity on both overlay roots."""
    return f"""() => {{
    const persistent = document.getElementById('{PERSISTENT_ROOT_ID}');
    if (persistent) {{
        {_set_visibility_opacity_js("persistent", visibility=visibility, opacity=opacity)}
    }}
    const transient = document.getElementById('{TRANSIENT_ROOT_ID}');
    if (transient) {{
        {_set_visibility_opacity_js("transient", visibility=visibility, opacity=opacity)}
    }}
}}"""


# Keep the full-viewport overlay layers mounted during capture.
# In headed Chromium, toggling display on these layers can trigger
# a visible compositor snap even though the page viewport is unchanged.
_HIDE_BOTH_JS = _toggle_both_roots_js(visibility="hidden", opacity="0")
_RESTORE_PERSISTENT_JS = f"""() => {{
    const persistent = document.getElementById('{PERSISTENT_ROOT_ID}');
    if (persistent) {{
        {_set_visibility_opacity_js("persistent", visibility="visible", opacity="1")}
    }}
}}"""


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
    ) -> None:
        if not self._active:
            return

        await self._ensure_transient_root()

        # Cursor-first choreography: move cursor to target, then trigger effect.
        center: dict[str, int] | None = None
        if action_type == "type":
            center = await self._get_focused_element_center()
            if center:
                await self._move_cursor(center["x"], center["y"])
        elif action_type == "drag":
            await self._move_cursor(start_x, start_y)
        else:
            await self._move_cursor(x, y)

        await asyncio.sleep(CURSOR_TRANSITION_MS / 1000)

        if action_type in {"left_click", "double_click", "triple_click", "right_click"}:
            await self._show_click_effect(x, y, num_clicks)
        elif action_type == "scroll":
            await self._show_scroll_effect(x, y, direction)
        elif action_type == "type":
            await self._show_type_effect(center)
        elif action_type == "drag":
            await self._show_drag_effect(start_x, start_y, x, y)

    async def set_status(self, label: str) -> None:
        self._current_status = label
        if not self._active:
            return
        await self._inject_persistent_root()

    async def show_thought(self, text: str) -> None:
        if not self._active:
            return
        await self._inject_persistent_root()
        clipped = self._clip_text(text, 520)
        await self._eval(_THOUGHT_CARD_JS, clipped)

    async def before_screenshot(self) -> None:
        if not self._active:
            return
        await self._eval(_HIDE_BOTH_JS)

    async def after_screenshot(self) -> None:
        if not self._active:
            return
        await self._eval(_RESTORE_PERSISTENT_JS)
        # Leave the transient root hidden; the next preview_action call re-shows it.


    async def _inject_persistent_root(self) -> None:
        await self._eval(_PERSISTENT_ROOT_JS)
        await self._set_chip_text(self._current_status)

    async def _set_chip_text(self, label: str) -> None:
        await self._eval(
            f"""() => {{
                const chip = document.getElementById('{STATUS_CHIP_ID}');
                if (chip) chip.textContent = {label!r};
            }}"""
        )

    async def _move_cursor(self, x: int, y: int) -> None:
        """Move the branded cursor to the given viewport coordinates."""
        await self._eval(
            f"""() => {{
                const cursor = document.getElementById('{CURSOR_ID}');
                if (cursor) {{ cursor.style.left = '{x}px'; cursor.style.top = '{y}px'; }}
            }}"""
        )

    async def _show_click_effect(self, x: int, y: int, num_clicks: int) -> None:
        gap = int(CLICK_DURATION_MS * 0.5)
        await self._eval(
            f"""() => {{
                const root = document.getElementById('{TRANSIENT_ROOT_ID}');
                if (!root) return;
                if (!document.getElementById('{CLICK_STYLE_ID}')) {{
                    const style = document.createElement('style');
                    style.id = '{CLICK_STYLE_ID}';
                    style.textContent = '@keyframes n1click{{0%{{width:5px;height:5px;opacity:0.6}}100%{{width:30px;height:30px;opacity:0}}}}';
                    document.head.appendChild(style);
                }}
                for (let i = 0; i < {num_clicks}; i++) {{
                    const delay = i * {gap};
                    const el = document.createElement('div');
                    el.style.cssText = 'position:fixed;left:{x}px;top:{y}px;width:5px;height:5px;background:{YUTORI_GREEN};border-radius:50%;pointer-events:none;z-index:{Z_INDEX};transform:translate(-50%,-50%);animation:n1click {CLICK_DURATION_MS}ms ease-out forwards;animation-delay:' + delay + 'ms;opacity:0;';
                    root.appendChild(el);
                    setTimeout(() => el.remove(), delay + {CLICK_DURATION_MS} + 100);
                }}
            }}"""
        )

    async def _show_scroll_effect(self, x: int, y: int, direction: str = "down") -> None:
        rotation = {"down": 0, "up": 180, "right": 270, "left": 90}.get(direction, 0)
        # Translate in the scroll direction as the chevron fades out.
        tx = {"right": 18, "left": -18}.get(direction, 0)
        ty = {"down": 18, "up": -18}.get(direction, 0)
        await self._eval(
            f"""() => {{
                const root = document.getElementById('{TRANSIENT_ROOT_ID}');
                if (!root) return;
                const existing = document.getElementById('{SCROLL_STYLE_ID}');
                if (existing) existing.remove();

                const style = document.createElement('style');
                style.id = '{SCROLL_STYLE_ID}';
                style.textContent = '@keyframes n1scroll{{0%{{opacity:0.7;transform:translate(-50%,-50%) rotate({rotation}deg)}}100%{{opacity:0;transform:translate(calc(-50% + {tx}px),calc(-50% + {ty}px)) rotate({rotation}deg)}}}}';
                document.head.appendChild(style);

                const container = document.createElement('div');
                container.style.cssText = 'position:fixed;left:{x}px;top:{y}px;width:20px;height:20px;pointer-events:none;z-index:{Z_INDEX};animation:n1scroll {SCROLL_DURATION_MS}ms ease-out forwards;';
                const chevron = document.createElement('div');
                chevron.style.cssText = 'position:absolute;left:50%;top:50%;width:10px;height:10px;border-right:2px solid {YUTORI_GREEN};border-bottom:2px solid {YUTORI_GREEN};transform:translate(-50%,-70%) rotate(45deg);';
                container.appendChild(chevron);
                root.appendChild(container);
                setTimeout(() => {{ container.remove(); style.remove(); }}, {SCROLL_DURATION_MS} + 100);
            }}"""
        )

    async def _show_type_effect(self, center: dict[str, int] | None) -> None:
        if center is None:
            return

        cx = center["x"]
        cy_raw = center["y"]
        show_below = cy_raw < 50
        cy = cy_raw + 30 if show_below else cy_raw - 7

        await self._eval(
            f"""() => {{
                const root = document.getElementById('{TRANSIENT_ROOT_ID}');
                if (!root) return;
                const existing = document.getElementById('{TYPE_STYLE_ID}');
                if (existing) existing.remove();

                const style = document.createElement('style');
                style.id = '{TYPE_STYLE_ID}';
                style.textContent = '@keyframes n1tcaret{{0%,100%{{opacity:1}}50%{{opacity:0}}}}@keyframes n1tdot{{0%,100%{{transform:scale(1);opacity:0.5}}50%{{transform:scale(1.4);opacity:1}}}}@keyframes n1tfade{{0%{{opacity:0}}15%{{opacity:1}}85%{{opacity:1}}100%{{opacity:0}}}}';
                document.head.appendChild(style);

                const container = document.createElement('div');
                container.style.cssText = 'position:fixed;left:{cx + 14}px;top:{cy}px;pointer-events:none;z-index:{Z_INDEX};animation:n1tfade {EFFECT_DURATION_MS}ms ease-out forwards;display:flex;align-items:flex-end;gap:3px;';

                const caret = document.createElement('div');
                caret.style.cssText = 'width:2px;height:14px;background:{YUTORI_GREEN};border-radius:1px;animation:n1tcaret 0.53s step-end infinite;';
                container.appendChild(caret);

                const dots = document.createElement('div');
                dots.style.cssText = 'display:flex;gap:2px;margin-left:2px;margin-bottom:1px;';
                for (let i = 0; i < 3; i++) {{
                    const dot = document.createElement('div');
                    dot.style.cssText = 'width:3px;height:3px;background:{YUTORI_GREEN};border-radius:50%;animation:n1tdot 0.6s ease-in-out infinite;animation-delay:' + (i * 0.12) + 's;';
                    dots.appendChild(dot);
                }}
                container.appendChild(dots);

                root.appendChild(container);
                setTimeout(() => {{ container.remove(); style.remove(); }}, {EFFECT_DURATION_MS} + 100);
            }}"""
        )

    async def _show_drag_effect(self, start_x: int, start_y: int, end_x: int, end_y: int) -> None:
        await self._eval(
            f"""() => {{
                const root = document.getElementById('{TRANSIENT_ROOT_ID}');
                if (!root) return;
                const existing = document.getElementById('{DRAG_STYLE_ID}');
                if (existing) existing.remove();

                const style = document.createElement('style');
                style.id = '{DRAG_STYLE_ID}';
                style.textContent = '@keyframes n1dfade{{0%{{opacity:0.5}}100%{{opacity:0}}}}@keyframes n1dtrail{{0%{{opacity:0.6}}100%{{opacity:0}}}}';
                document.head.appendChild(style);

                const pressed = document.createElement('div');
                pressed.style.cssText = 'position:fixed;left:{start_x}px;top:{start_y}px;width:8px;height:8px;background:rgba(29,205,152,0.5);border-radius:50%;transform:translate(-50%,-50%);pointer-events:none;z-index:{Z_INDEX};animation:n1dfade {DRAG_DURATION_MS + 100}ms ease-out forwards;';
                root.appendChild(pressed);

                const dx = {end_x} - {start_x};
                const dy = {end_y} - {start_y};
                const length = Math.sqrt(dx * dx + dy * dy);
                const angle = Math.atan2(dy, dx) * 180 / Math.PI;
                const trail = document.createElement('div');
                trail.style.cssText = 'position:fixed;left:{start_x}px;top:{start_y}px;width:' + length + 'px;height:2px;background:linear-gradient(to right,{YUTORI_GREEN},transparent);transform-origin:0 50%;transform:translateY(-50%) rotate(' + angle + 'deg);pointer-events:none;z-index:{Z_INDEX};animation:n1dtrail {DRAG_DURATION_MS + 200}ms ease-out forwards;';
                root.appendChild(trail);

                setTimeout(() => {{ pressed.remove(); trail.remove(); style.remove(); }}, {DRAG_DURATION_MS + 300});
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

    async def _eval(self, script: str, arg: object | None = None) -> None:
        try:
            if arg is None:
                await self._page.evaluate(script)
            else:
                await self._page.evaluate(script, arg)
        except Exception:
            logger.debug("Overlay evaluate failed (best-effort)", exc_info=True)

    @staticmethod
    def _clip_text(text: str, limit: int) -> str:
        normalized = " ".join(str(text).split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: max(limit - 1, 0)].rstrip() + "…"
