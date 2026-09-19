"""Screenshot capture and WebP-encoding pipeline.

Split out of ``BrowserManager`` because this pipeline — CDP protocol params,
PIL image objects, WebP encoding — has essentially no dependency on
browser/session lifecycle state. The only piece of ``BrowserManager`` state it
needs (``headless``) is passed in explicitly by the caller.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from typing import TYPE_CHECKING, Any

from PIL import Image
from playwright.async_api import Error as PlaywrightError

if TYPE_CHECKING:
    from playwright.async_api import CDPSession

    from frontend_visualqa.browser import BrowserSession

logger = logging.getLogger(__name__)

DEFAULT_SCREENSHOT_WEBP_QUALITY = 75
DEFAULT_CDP_SCREENSHOT_TIMEOUT_SECONDS = 5.0


async def capture_screenshot_image(session: "BrowserSession", *, headless: bool) -> Image.Image:
    # CDP Page.captureScreenshot avoids re-rendering through Playwright's
    # protocol bridge and reuses the existing compositor frame — typically
    # 30–60% faster than page.screenshot() on a 1280×800 viewport. We use
    # it in both headless and headed modes:
    #   - In headed mode, it also avoids the visible flash that the
    #     Playwright surface path can cause on the live page.
    #   - In headless mode, the only motivation was the latency win.
    # Either way, normalize the returned image back to CSS viewport size
    # for Navigator's 1000x1000 coordinate system.
    cdp_image = await capture_screenshot_image_via_cdp(session)
    if cdp_image is not None:
        return cdp_image

    # CDP unavailable (older Chromium, detached context, etc.) — fall back
    # to Playwright screenshots. Keep animations disabled only in headless
    # mode for deterministic evidence. In headed mode, disabling
    # animations is itself visible and can create a separate flash on
    # animated pages.
    screenshot_kwargs: dict[str, Any] = {"type": "png"}
    if headless:
        screenshot_kwargs["animations"] = "disabled"
    image = image_from_bytes(await session.page.screenshot(**screenshot_kwargs))

    # When device_scale_factor > 1, Playwright returns an image at native
    # pixel resolution (e.g. 2560x1600 for DSF=2 at 1280x800 viewport).
    # Navigator maps its 1000x1000 coordinate grid to the image
    # dimensions, so we must resize back to CSS viewport size to keep
    # coordinates aligned.
    css_size = (session.viewport.width, session.viewport.height)
    return resize_to(image, css_size)


async def _cdp_send_with_timeout(cdp_session: CDPSession, method: str, params: dict[str, Any] | None = None) -> Any:
    """Send a CDP command bounded by ``DEFAULT_CDP_SCREENSHOT_TIMEOUT_SECONDS``.

    Both CDP sends in :func:`capture_screenshot_image_via_cdp` share the
    concurrent-hang risk that motivated the captureScreenshot timeout (#93);
    centralizing the identical ``asyncio.wait_for(...)`` wrapping here keeps
    the two call sites' timeouts from drifting apart.
    """
    return await asyncio.wait_for(cdp_session.send(method, params), timeout=DEFAULT_CDP_SCREENSHOT_TIMEOUT_SECONDS)


async def capture_screenshot_image_via_cdp(session: "BrowserSession") -> Image.Image | None:
    cdp_session: CDPSession | None = None
    try:
        cdp_session = await session.context.new_cdp_session(session.page)
        layout_metrics = await _cdp_send_with_timeout(cdp_session, "Page.getLayoutMetrics")
        capture_params, target_size = build_cdp_capture_request(layout_metrics)
        result = await _cdp_send_with_timeout(cdp_session, "Page.captureScreenshot", capture_params)
        data = result.get("data")
        if not data:
            raise ValueError("Chromium did not return screenshot data")
        return normalize_cdp_capture_image(image_from_bytes(base64.b64decode(data)), target_size)
    except Exception:
        logger.debug("CDP screenshot capture failed; falling back to Playwright screenshot()", exc_info=True)
        return None
    finally:
        if cdp_session is not None:
            try:
                await cdp_session.detach()
            except PlaywrightError:
                logger.debug("CDP screenshot session detach failed", exc_info=True)


def build_cdp_capture_request(layout_metrics: dict[str, Any]) -> tuple[dict[str, Any], tuple[int, int] | None]:
    css_viewport = layout_metrics.get("cssVisualViewport") or {}
    css_width = int(round(float(css_viewport.get("clientWidth") or 0)))
    css_height = int(round(float(css_viewport.get("clientHeight") or 0)))
    params: dict[str, Any] = {"format": "png", "captureBeyondViewport": False, "fromSurface": True}

    if css_width > 0 and css_height > 0:
        params["clip"] = {
            "x": float(css_viewport.get("pageX") or 0),
            "y": float(css_viewport.get("pageY") or 0),
            "width": float(css_width),
            "height": float(css_height),
            "scale": 1.0,
        }
        return params, (css_width, css_height)

    logger.debug("CDP layout metrics missing CSS viewport sizes; using default screenshot params")
    return params, None


def resize_to(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Resize *image* to *size* with LANCZOS, unless it is already that size."""
    return image if image.size == size else image.resize(size, resample=Image.Resampling.LANCZOS)


def normalize_cdp_capture_image(image: Image.Image, target_size: tuple[int, int] | None) -> Image.Image:
    if target_size is None:
        return image
    return resize_to(image, target_size)


def image_from_bytes(image_bytes: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(image_bytes))
    image.load()
    return image


def image_to_webp_bytes(image: Image.Image) -> bytes:
    # Encode WebP directly from the PIL.Image. The previous implementation
    # round-tripped through JPEG (encode → decode → re-encode as WebP),
    # which paid for two extra codec passes per screenshot and degraded
    # the WebP input with JPEG quantization artifacts before the real
    # encode. Chromium-sourced PNGs are RGB/RGBA, both of which WebP
    # supports natively — no convert("RGB") needed; we keep alpha if it
    # was present. The defensive convert below only trips for exotic
    # modes (P, CMYK, etc.) that the screenshot path can't produce in
    # practice but cost nothing to guard against.
    if image.mode not in {"RGB", "RGBA"}:
        image = image.convert("RGBA")
    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", quality=DEFAULT_SCREENSHOT_WEBP_QUALITY)
    return buffer.getvalue()
