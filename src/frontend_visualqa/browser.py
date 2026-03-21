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
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from frontend_visualqa.schemas import BrowserConfig, BrowserMode, BrowserSessionStatus, BrowserStatusResult, ViewportConfig


DEFAULT_NAVIGATION_TIMEOUT_MS = 20_000
DEFAULT_SETTLE_DELAY_SECONDS = 1.0
logger = logging.getLogger(__name__)
PERSISTENT_SESSION_KEY_ERROR = (
    "Persistent browser mode supports only the 'default' session. "
    "Use ephemeral mode for multiple sessions, or omit session_key."
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
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._persistent_context: BrowserContext | None = None
        self._sessions: dict[str, BrowserSession] = {}

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
                viewport={"width": persistent_viewport.width, "height": persistent_viewport.height},
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
        await asyncio.sleep(self.settle_delay_seconds)
        return session.page.url

    async def reset_to_url(self, session: BrowserSession, url: str) -> str:
        """Reset the session to the provided base URL."""

        return await self.goto(session, url)

    async def capture_screenshot(self, session: BrowserSession) -> bytes:
        """Capture the current page viewport as WebP bytes."""

        png_bytes = await self._capture_png_bytes(session)
        image = Image.open(io.BytesIO(png_bytes))
        image.load()
        buffer = io.BytesIO()
        image.save(buffer, format="WEBP", quality=90)
        return buffer.getvalue()

    async def _capture_png_bytes(self, session: BrowserSession) -> bytes:
        if not self.headless:
            # In headed Chromium, Playwright's page.screenshot() can visibly
            # shrink the live page during capture. Use the lower-level CDP
            # capture path first so the visible browser stays stable.
            cdp_png = await self._capture_png_bytes_via_cdp(session)
            if cdp_png is not None:
                return cdp_png

        # If CDP capture is unavailable, fall back to Playwright screenshots.
        # Keep animations disabled only in headless mode for deterministic
        # evidence. In headed mode, disabling animations is itself visible and
        # can create a separate flash on animated pages.
        screenshot_kwargs: dict[str, Any] = {"type": "png"}
        if self.headless:
            screenshot_kwargs["animations"] = "disabled"
        return await session.page.screenshot(**screenshot_kwargs)

    async def _capture_png_bytes_via_cdp(self, session: BrowserSession) -> bytes | None:
        cdp_session = None
        try:
            cdp_session = await session.context.new_cdp_session(session.page)
            layout_metrics = await cdp_session.send("Page.getLayoutMetrics")
            capture_params = self._build_cdp_capture_params(layout_metrics)
            result = await cdp_session.send(
                "Page.captureScreenshot",
                capture_params,
            )
            data = result.get("data")
            if not data:
                raise ValueError("Chromium did not return screenshot data")
            return base64.b64decode(data)
        except Exception:
            logger.debug("CDP screenshot capture failed; falling back to Playwright screenshot()", exc_info=True)
            return None
        finally:
            if cdp_session is not None:
                try:
                    await cdp_session.detach()
                except Exception:
                    logger.debug("CDP screenshot session detach failed", exc_info=True)

    @staticmethod
    def _build_cdp_capture_params(layout_metrics: dict[str, Any]) -> dict[str, Any]:
        css_viewport = layout_metrics.get("cssVisualViewport") or {}
        surface_viewport = layout_metrics.get("visualViewport") or {}

        css_width = float(css_viewport.get("clientWidth") or 0)
        css_height = float(css_viewport.get("clientHeight") or 0)
        surface_width = float(surface_viewport.get("clientWidth") or 0)
        surface_height = float(surface_viewport.get("clientHeight") or 0)

        if css_width > 0 and css_height > 0 and surface_width > 0 and surface_height > 0:
            surface_scale_x = surface_width / css_width
            surface_scale_y = surface_height / css_height
            surface_scale = max(surface_scale_x, surface_scale_y, 1.0)
            return {
                "format": "png",
                "captureBeyondViewport": False,
                "clip": {
                    "x": float(css_viewport.get("pageX") or 0),
                    "y": float(css_viewport.get("pageY") or 0),
                    "width": css_width,
                    "height": css_height,
                    "scale": 1.0 / surface_scale,
                },
            }

        logger.debug("CDP layout metrics missing CSS/surface viewport sizes; using default screenshot params")
        return {
            "format": "png",
            "captureBeyondViewport": False,
        }

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
            await page.set_viewport_size({"width": viewport.width, "height": viewport.height})
            await page.goto("about:blank", wait_until="domcontentloaded", timeout=self.navigation_timeout_ms)
        else:
            browser = await self.ensure_browser(viewport)
            assert isinstance(browser, Browser)
            context = await browser.new_context(
                viewport={"width": viewport.width, "height": viewport.height},
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

        await session.page.set_viewport_size({"width": desired.width, "height": desired.height})
        session.viewport = desired
        return session

    async def _ensure_playwright(self) -> Playwright:
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        return self._playwright

    def _configure_context(self, context: BrowserContext) -> None:
        context.set_default_navigation_timeout(self.navigation_timeout_ms)
        context.set_default_timeout(self.navigation_timeout_ms)

    async def _stop_playwright_if_idle(self) -> None:
        if self._browser is not None or self._persistent_context is not None:
            return
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    def _validate_session_key(self, session_key: str) -> None:
        if self.config.mode != BrowserMode.persistent:
            return
        if session_key == "default":
            return
        raise ValueError(PERSISTENT_SESSION_KEY_ERROR)

    def _handle_persistent_context_close(self) -> None:
        self._persistent_context = None
        self._sessions.clear()

    @staticmethod
    def _session_is_open(session: BrowserSession) -> bool:
        try:
            return not session.page.is_closed()
        except Exception:
            return False

    @staticmethod
    def _safe_page_url(session: BrowserSession) -> str | None:
        try:
            return session.page.url or None
        except Exception:
            return None

    async def __aenter__(self) -> "BrowserManager":
        if self.config.mode == BrowserMode.ephemeral:
            await self.ensure_browser()
        return self

    async def __aexit__(self, exc_type: type | None, exc: BaseException | None, traceback: Any) -> None:
        await self.close()
