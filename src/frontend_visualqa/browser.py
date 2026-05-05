"""Playwright browser/session management for frontend-visualqa."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image
from playwright.async_api import Browser, BrowserContext, Error as PlaywrightError, Page, Playwright, async_playwright
from yutori.navigator.page_ready import PageReadyChecker

from frontend_visualqa.schemas import BrowserConfig, BrowserMode, BrowserSessionStatus, BrowserStatusResult, ViewportConfig


DEFAULT_NAVIGATION_TIMEOUT_MS = 20_000
DEFAULT_SETTLE_DELAY_SECONDS = 1.0
DEFAULT_PAGE_READY_TIMEOUT_SECONDS = 2
DEFAULT_SCREENSHOT_JPEG_QUALITY = 75
DEFAULT_SCREENSHOT_WEBP_QUALITY = 90
logger = logging.getLogger(__name__)
PERSISTENT_SESSION_KEY_ERROR = (
    "Persistent browser mode supports exactly one named session at a time. "
    "Use the existing session key, close the current persistent session before switching names, "
    "or use ephemeral mode for multiple sessions."
)


@dataclass
class BrowserSession:
    """Mutable session state bound to a Playwright context and page."""

    session_key: str
    context: BrowserContext
    page: Page
    viewport: ViewportConfig


def image_bytes_to_data_url(image_bytes: bytes, mime_type: str = "image/webp") -> str:
    """Encode raw image bytes as a data URL."""

    encoded = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def _viewport_size_dict(viewport: ViewportConfig) -> dict[str, int]:
    """Return the ``{"width", "height"}`` dict Playwright accepts.

    Centralises the four call sites that pass viewport dimensions to
    Playwright (``launch_persistent_context``, ``new_context``,
    ``set_viewport_size``) so the field names cannot drift if Playwright
    ever renames either key.
    """
    return {"width": viewport.width, "height": viewport.height}


class BrowserManager:
    """Own the shared Chromium process and session-scoped browser contexts."""

    def __init__(
        self,
        *,
        config: BrowserConfig | None = None,
        headless: bool = True,
        navigation_timeout_ms: int = DEFAULT_NAVIGATION_TIMEOUT_MS,
        settle_delay_seconds: float = DEFAULT_SETTLE_DELAY_SECONDS,
    ) -> None:
        if config is None:
            config = BrowserConfig(
                headless=headless,
                navigation_timeout_ms=navigation_timeout_ms,
                settle_delay_seconds=settle_delay_seconds,
            )
        self.config = config
        self.headless = self.config.headless
        self.navigation_timeout_ms = self.config.navigation_timeout_ms
        self.settle_delay_seconds = self.config.settle_delay_seconds
        self._page_ready_checker = PageReadyChecker(
            timeout=min(DEFAULT_PAGE_READY_TIMEOUT_SECONDS, max(1, int(self.navigation_timeout_ms / 1000))),
            initial_wait=0.0,
            wait_after_ready=self.settle_delay_seconds,
            replace_native_select_dropdown=True,
            disable_new_tabs=True,
            disable_printing=True,
            poll_interval=0.1,
        )
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._persistent_context: BrowserContext | None = None
        self._sessions: dict[str, BrowserSession] = {}

    @property
    def _persistent_session_key(self) -> str | None:
        """Derive the active persistent session key from ``_sessions``."""
        if self.config.mode != BrowserMode.persistent or not self._sessions:
            return None
        return next(iter(self._sessions))

    async def ensure_browser(self, viewport: ViewportConfig | None = None) -> Browser | BrowserContext:
        """Start Playwright and Chromium if needed."""

        if self.config.mode == BrowserMode.persistent:
            if self._persistent_context is not None:
                return self._persistent_context

            playwright = await self._ensure_playwright()
            persistent_viewport = viewport or ViewportConfig()
            user_data_dir = self.config.resolved_user_data_dir
            assert user_data_dir is not None
            Path(user_data_dir).mkdir(parents=True, exist_ok=True)
            self._persistent_context = await playwright.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=self.headless,
                viewport=_viewport_size_dict(persistent_viewport),
                device_scale_factor=persistent_viewport.device_scale_factor,
            )
            self._configure_context(self._persistent_context)
            self._persistent_context.on("close", lambda *_: self._handle_persistent_context_close())
            return self._persistent_context

        if self._browser is not None:
            return self._browser

        playwright = await self._ensure_playwright()
        self._browser = await playwright.chromium.launch(headless=self.headless)
        return self._browser

    async def get_session(
        self,
        session_key: str = "default",
        *,
        viewport: ViewportConfig | None = None,
        reuse_session: bool = True,
    ) -> BrowserSession:
        """Get or create a session for the provided key."""

        self._validate_session_key(session_key)
        desired_viewport = viewport or ViewportConfig()
        existing = self._sessions.get(session_key)
        if existing and not self._session_is_open(existing):
            self._sessions.pop(session_key, None)
            existing = None
        if existing and reuse_session:
            return await self._ensure_viewport(existing, desired_viewport)

        if existing:
            await self.close_session(session_key)

        await self.ensure_browser(desired_viewport)
        session = await self._create_session(session_key, desired_viewport)
        self._sessions[session_key] = session
        return session

    async def goto(self, session: BrowserSession, url: str) -> str:
        """Navigate the session page to the given URL."""

        response = await session.page.goto(url, wait_until="domcontentloaded", timeout=self.navigation_timeout_ms)
        if response is None:
            await session.page.wait_for_load_state("domcontentloaded", timeout=self.navigation_timeout_ms)
        await self._best_effort_wait_for_page_ready(session.page)
        return session.page.url

    async def reset_to_url(self, session: BrowserSession, url: str) -> str:
        """Reset the session to the provided base URL."""

        return await self.goto(session, url)

    async def capture_screenshot(self, session: BrowserSession) -> bytes:
        """Capture the current page viewport as WebP bytes."""

        image = await self._capture_screenshot_image(session)
        return self._image_to_webp_bytes(image)

    async def _capture_screenshot_image(self, session: BrowserSession) -> Image.Image:
        if not self.headless:
            # In headed Chromium, the default screenshot surface path can
            # flash the live page. Prefer a view-backed CDP capture, then
            # normalize the returned image back to CSS viewport size for
            # Navigator's 1000x1000 coordinate system.
            cdp_image = await self._capture_screenshot_image_via_cdp(session)
            if cdp_image is not None:
                return cdp_image

        # If CDP capture is unavailable, fall back to Playwright screenshots.
        # Keep animations disabled only in headless mode for deterministic
        # evidence. In headed mode, disabling animations is itself visible and
        # can create a separate flash on animated pages.
        screenshot_kwargs: dict[str, Any] = {"type": "png"}
        if self.headless:
            screenshot_kwargs["animations"] = "disabled"
        image = self._image_from_bytes(await session.page.screenshot(**screenshot_kwargs))

        # When device_scale_factor > 1, Playwright returns an image at native
        # pixel resolution (e.g. 2560x1600 for DSF=2 at 1280x800 viewport).
        # Navigator maps its 1000x1000 coordinate grid to the image
        # dimensions, so we must resize back to CSS viewport size to keep
        # coordinates aligned.
        css_size = (session.viewport.width, session.viewport.height)
        if image.size != css_size:
            image = image.resize(css_size, resample=Image.Resampling.LANCZOS)
        return image

    async def _capture_screenshot_image_via_cdp(self, session: BrowserSession) -> Image.Image | None:
        cdp_session = None
        try:
            cdp_session = await session.context.new_cdp_session(session.page)
            layout_metrics = await cdp_session.send("Page.getLayoutMetrics")
            capture_params, target_size = self._build_cdp_capture_request(layout_metrics)
            result = await cdp_session.send(
                "Page.captureScreenshot",
                capture_params,
            )
            data = result.get("data")
            if not data:
                raise ValueError("Chromium did not return screenshot data")
            return self._normalize_cdp_capture_image(self._image_from_bytes(base64.b64decode(data)), target_size)
        except Exception:
            logger.debug("CDP screenshot capture failed; falling back to Playwright screenshot()", exc_info=True)
            return None
        finally:
            if cdp_session is not None:
                try:
                    await cdp_session.detach()
                except PlaywrightError:
                    logger.debug("CDP screenshot session detach failed", exc_info=True)

    @staticmethod
    def _build_cdp_capture_request(layout_metrics: dict[str, Any]) -> tuple[dict[str, Any], tuple[int, int] | None]:
        css_viewport = layout_metrics.get("cssVisualViewport") or {}
        css_width = int(round(float(css_viewport.get("clientWidth") or 0)))
        css_height = int(round(float(css_viewport.get("clientHeight") or 0)))

        if css_width > 0 and css_height > 0:
            return (
                {
                    "format": "png",
                    "captureBeyondViewport": False,
                    "fromSurface": False,
                    "clip": {
                        "x": float(css_viewport.get("pageX") or 0),
                        "y": float(css_viewport.get("pageY") or 0),
                        "width": float(css_width),
                        "height": float(css_height),
                        "scale": 1.0,
                    },
                },
                (css_width, css_height),
            )

        logger.debug("CDP layout metrics missing CSS viewport sizes; using default screenshot params")
        return (
            {
                "format": "png",
                "captureBeyondViewport": False,
                "fromSurface": False,
            },
            None,
        )

    @staticmethod
    def _normalize_cdp_capture_image(image: Image.Image, target_size: tuple[int, int] | None) -> Image.Image:
        if target_size is None:
            return image
        if image.size == target_size:
            return image

        return image.resize(target_size, resample=Image.Resampling.LANCZOS)

    @staticmethod
    def _image_from_bytes(image_bytes: bytes) -> Image.Image:
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
        return image

    @staticmethod
    def _image_to_jpeg_bytes(image: Image.Image) -> bytes:
        buffer = io.BytesIO()
        rgb_image = image.convert("RGB")
        rgb_image.save(buffer, format="JPEG", quality=DEFAULT_SCREENSHOT_JPEG_QUALITY)
        return buffer.getvalue()

    @staticmethod
    def _image_to_webp_bytes(image: Image.Image) -> bytes:
        jpeg_bytes = BrowserManager._image_to_jpeg_bytes(image)
        jpeg_image = BrowserManager._image_from_bytes(jpeg_bytes)
        buffer = io.BytesIO()
        jpeg_image.save(buffer, format="WEBP", quality=DEFAULT_SCREENSHOT_WEBP_QUALITY)
        return buffer.getvalue()

    async def set_viewport(self, session_key: str, viewport: ViewportConfig) -> BrowserSession:
        """Resize or recreate the session to match a new viewport."""

        self._validate_session_key(session_key)
        return await self.get_session(session_key, viewport=viewport, reuse_session=True)

    async def restart_session(
        self,
        session_key: str = "default",
        *,
        viewport: ViewportConfig | None = None,
        preserve_url: bool = True,
    ) -> BrowserSession:
        """Force a fresh context for a session key."""

        self._validate_session_key(session_key)
        previous = self._sessions.get(session_key)
        current_url = previous.page.url if previous and previous.page.url else None
        await self.close_session(session_key)
        session = await self.get_session(session_key, viewport=viewport, reuse_session=False)
        if preserve_url and current_url:
            await self.goto(session, current_url)
        return session

    async def close_session(self, session_key: str) -> None:
        """Close a single session if it exists."""

        self._validate_session_key(session_key)
        if self.config.mode == BrowserMode.persistent:
            self._sessions.pop(session_key, None)
            if self._persistent_context is None:
                return
            await self._persistent_context.close()
            self._persistent_context = None
            await self._stop_playwright_if_idle()
            return

        session = self._sessions.pop(session_key, None)
        if session is None:
            return
        await session.context.close()

    async def close(self) -> None:
        """Close all sessions and browser resources."""

        if self.config.mode == BrowserMode.persistent:
            self._sessions.clear()
            if self._persistent_context is not None:
                await self._persistent_context.close()
                self._persistent_context = None
        else:
            for session_key in list(self._sessions):
                await self.close_session(session_key)

        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    def status(self) -> BrowserStatusResult:
        """Return a serializable view of the current browser state."""

        browser_running = self._browser is not None or self._persistent_context is not None
        sessions = [
            BrowserSessionStatus(
                session_key=session.session_key,
                browser_open=browser_running,
                current_url=self._safe_page_url(session),
                viewport=session.viewport,
            )
            for session in self._sessions.values()
        ]
        return BrowserStatusResult(
            browser_running=browser_running,
            browser_mode=self.config.mode,
            user_data_dir=self.config.resolved_user_data_dir if self.config.mode == BrowserMode.persistent else None,
            sessions=sessions,
        )

    async def _create_session(self, session_key: str, viewport: ViewportConfig) -> BrowserSession:
        if self.config.mode == BrowserMode.persistent:
            context = self._persistent_context or await self.ensure_browser(viewport)
            assert isinstance(context, BrowserContext)
            page = await context.new_page()
            await page.set_viewport_size(_viewport_size_dict(viewport))
            await page.goto("about:blank", wait_until="domcontentloaded", timeout=self.navigation_timeout_ms)
        else:
            browser = await self.ensure_browser(viewport)
            assert isinstance(browser, Browser)
            context = await browser.new_context(
                viewport=_viewport_size_dict(viewport),
                device_scale_factor=viewport.device_scale_factor,
            )
            self._configure_context(context)
            page = await context.new_page()
        return BrowserSession(session_key=session_key, context=context, page=page, viewport=viewport)

    async def _ensure_viewport(self, session: BrowserSession, desired: ViewportConfig) -> BrowserSession:
        if session.viewport == desired:
            return session

        if session.viewport.device_scale_factor != desired.device_scale_factor:
            current_url = session.page.url or None
            session_key = session.session_key
            await self.close_session(session_key)
            refreshed = await self.get_session(session_key, viewport=desired, reuse_session=False)
            if current_url:
                await self.goto(refreshed, current_url)
            return refreshed

        await session.page.set_viewport_size(_viewport_size_dict(desired))
        session.viewport = desired
        return session

    async def _ensure_playwright(self) -> Playwright:
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        return self._playwright

    def _configure_context(self, context: BrowserContext) -> None:
        context.set_default_navigation_timeout(self.navigation_timeout_ms)
        context.set_default_timeout(self.navigation_timeout_ms)

    async def _best_effort_wait_for_page_ready(self, page: Page) -> None:
        try:
            await self._page_ready_checker.wait_until_ready(page, fast_mode=self.settle_delay_seconds == 0)
        except Exception:  # noqa: BLE001 - best-effort readiness check must not fail navigation
            logger.debug("Page ready check failed during navigation", exc_info=True)

    async def _stop_playwright_if_idle(self) -> None:
        if self._browser is not None or self._persistent_context is not None:
            return
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    def _evict_dead_persistent_session(self) -> None:
        """Remove a dead persistent session so it does not hold the name lock."""
        if self.config.mode != BrowserMode.persistent:
            return
        for key in list(self._sessions):
            if not self._session_is_open(self._sessions[key]):
                logger.info("Evicting dead persistent session %r (page was closed or crashed)", key)
                self._sessions.pop(key, None)

    def _validate_session_key(self, session_key: str) -> None:
        if self.config.mode != BrowserMode.persistent:
            return
        self._evict_dead_persistent_session()
        if self._persistent_session_key is None or session_key == self._persistent_session_key:
            return
        raise ValueError(f"{PERSISTENT_SESSION_KEY_ERROR} Active session key: {self._persistent_session_key!r}.")

    def _handle_persistent_context_close(self) -> None:
        self._persistent_context = None
        self._sessions.clear()

    @staticmethod
    def _session_is_open(session: BrowserSession) -> bool:
        try:
            return not session.page.is_closed()
        except PlaywrightError:
            return False

    @staticmethod
    def _safe_page_url(session: BrowserSession) -> str | None:
        try:
            return session.page.url or None
        except PlaywrightError:
            return None

    async def __aenter__(self) -> "BrowserManager":
        if self.config.mode == BrowserMode.ephemeral:
            await self.ensure_browser()
        return self

    async def __aexit__(self, exc_type: type | None, exc: BaseException | None, traceback: Any) -> None:
        await self.close()
