"""Playwright browser/session management for frontend-visualqa."""

from __future__ import annotations

import asyncio
import base64
import io
from dataclasses import dataclass
from typing import Any

from PIL import Image
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from frontend_visualqa.schemas import BrowserSessionStatus, BrowserStatusResult, ViewportConfig


DEFAULT_NAVIGATION_TIMEOUT_MS = 20_000
DEFAULT_SETTLE_DELAY_SECONDS = 1.0


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
        headless: bool = True,
        navigation_timeout_ms: int = DEFAULT_NAVIGATION_TIMEOUT_MS,
        settle_delay_seconds: float = DEFAULT_SETTLE_DELAY_SECONDS,
    ) -> None:
        self.headless = headless
        self.navigation_timeout_ms = navigation_timeout_ms
        self.settle_delay_seconds = settle_delay_seconds
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._sessions: dict[str, BrowserSession] = {}

    async def ensure_browser(self) -> Browser:
        """Start Playwright and Chromium if needed."""

        if self._browser is not None:
            return self._browser

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)
        return self._browser

    async def get_session(
        self,
        session_key: str = "default",
        *,
        viewport: ViewportConfig | None = None,
        reuse_session: bool = True,
    ) -> BrowserSession:
        """Get or create a session for the provided key."""

        desired_viewport = viewport or ViewportConfig()
        existing = self._sessions.get(session_key)
        if existing and reuse_session:
            return await self._ensure_viewport(existing, desired_viewport)

        if existing:
            await self.close_session(session_key)

        await self.ensure_browser()
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

        png_bytes = await session.page.screenshot(type="png", animations="disabled")
        image = Image.open(io.BytesIO(png_bytes))
        image.load()
        buffer = io.BytesIO()
        image.save(buffer, format="WEBP", quality=90)
        return buffer.getvalue()

    async def set_viewport(self, session_key: str, viewport: ViewportConfig) -> BrowserSession:
        """Resize or recreate the session to match a new viewport."""

        return await self.get_session(session_key, viewport=viewport, reuse_session=True)

    async def restart_session(
        self,
        session_key: str = "default",
        *,
        viewport: ViewportConfig | None = None,
        preserve_url: bool = True,
    ) -> BrowserSession:
        """Force a fresh context for a session key."""

        previous = self._sessions.get(session_key)
        current_url = previous.page.url if previous and previous.page.url else None
        await self.close_session(session_key)
        session = await self.get_session(session_key, viewport=viewport, reuse_session=False)
        if preserve_url and current_url:
            await self.goto(session, current_url)
        return session

    async def close_session(self, session_key: str) -> None:
        """Close a single session if it exists."""

        session = self._sessions.pop(session_key, None)
        if session is None:
            return
        await session.context.close()

    async def close(self) -> None:
        """Close all sessions and browser resources."""

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

        sessions = [
            BrowserSessionStatus(
                session_key=session.session_key,
                browser_open=self._browser is not None,
                current_url=session.page.url or None,
                viewport=session.viewport,
            )
            for session in self._sessions.values()
        ]
        return BrowserStatusResult(browser_running=self._browser is not None, sessions=sessions)

    async def _create_session(self, session_key: str, viewport: ViewportConfig) -> BrowserSession:
        browser = await self.ensure_browser()
        context = await browser.new_context(
            viewport={"width": viewport.width, "height": viewport.height},
            device_scale_factor=viewport.device_scale_factor,
        )
        context.set_default_navigation_timeout(self.navigation_timeout_ms)
        context.set_default_timeout(self.navigation_timeout_ms)
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

    async def __aenter__(self) -> "BrowserManager":
        await self.ensure_browser()
        return self

    async def __aexit__(self, exc_type: type | None, exc: BaseException | None, traceback: Any) -> None:
        await self.close()
