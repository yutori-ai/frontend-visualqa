"""Playwright browser/session management for frontend-visualqa."""

from __future__ import annotations

import base64
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Self, TypeVar

from PIL import Image
from playwright.async_api import Browser, BrowserContext, Error as PlaywrightError, Page, Playwright, async_playwright
from yutori.navigator.page_ready import PageReadyChecker

from frontend_visualqa import screenshot_capture
from frontend_visualqa.schemas import (
    DEFAULT_NAVIGATION_TIMEOUT_MS,
    DEFAULT_SETTLE_DELAY_SECONDS,
    BrowserConfig,
    BrowserMode,
    BrowserSessionStatus,
    BrowserStatusResult,
    ViewportConfig,
)
from frontend_visualqa.utils import elapsed_ms


# Must exceed the SDK's PageReadyChecker.initial_wait (2.0s, hardcoded) by
# enough margin for at least a few polling cycles, otherwise the outer
# asyncio.wait_for fires before the first is_ready() check ever runs and we
# log a spurious "Page did not become ready" ERROR on every action against
# real-world JS-heavy sites (amazon.com, etc.). 8s gives ~6 polling cycles
# after the initial wait while staying well under DEFAULT_NAVIGATION_TIMEOUT_MS.
DEFAULT_PAGE_READY_TIMEOUT_SECONDS = 8
logger = logging.getLogger(__name__)
_T = TypeVar("_T")
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


def _apply_record_video_kwargs(
    kwargs: dict[str, Any],
    record_video_dir: str | None,
    viewport: ViewportConfig,
) -> None:
    """Add Playwright's ``record_video_*`` keys to *kwargs* in place, if requested.

    Shared by the persistent-mode launch path (``ensure_browser``) and the
    ephemeral-mode context path (``_create_session``), which both need to
    create the recording directory and set the same two keys before handing
    *kwargs* to Playwright.
    """
    if not record_video_dir:
        return
    Path(record_video_dir).mkdir(parents=True, exist_ok=True)
    kwargs["record_video_dir"] = record_video_dir
    kwargs["record_video_size"] = _viewport_size_dict(viewport)


def build_page_ready_checker(navigation_timeout_ms: int, *, wait_after_ready: float = 0.0) -> PageReadyChecker:
    """Construct a ``PageReadyChecker`` with frontend-visualqa's standard tuning.

    Shared by ``BrowserManager`` (post-navigation readiness) and
    ``ActionExecutor`` (post-action readiness) so the two checkers cannot
    drift out of sync. ``wait_after_ready`` is the only field the two
    callers set differently.
    """
    return PageReadyChecker(
        timeout=min(DEFAULT_PAGE_READY_TIMEOUT_SECONDS, max(1, int(navigation_timeout_ms / 1000))),
        initial_wait=0.0,
        wait_after_ready=wait_after_ready,
        replace_native_select_dropdown=True,
        disable_new_tabs=True,
        disable_printing=True,
        poll_interval=0.1,
    )


async def best_effort_wait_until_ready(
    checker: PageReadyChecker,
    page: Page,
    *,
    settle_delay_seconds: float | None,
    log_label: str,
) -> None:
    """Await ``checker.wait_until_ready``, swallowing any failure.

    Shared by ``BrowserManager`` (post-navigation) and ``ActionExecutor``
    (post-action), which both need the identical readiness check to be
    best-effort: a failure here must never break navigation or an action.
    ``log_label`` distinguishes the two call sites in the log line.
    """
    try:
        await checker.wait_until_ready(page, fast_mode=settle_delay_seconds == 0)
    except Exception:  # noqa: BLE001 - best-effort readiness check must not fail navigation/actions
        logger.debug("Page ready check failed %s", log_label, exc_info=True)


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
        self._page_ready_checker = build_page_ready_checker(
            self.navigation_timeout_ms, wait_after_ready=self.settle_delay_seconds
        )
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._persistent_context: BrowserContext | None = None
        self._sessions: dict[str, BrowserSession] = {}
        # Pre-warm Pillow's WebP encoder. The first WEBP save in a process
        # lazy-loads libwebp via Pillow's plugin system — which costs ~1s on
        # macOS — and that latency would otherwise land on the first real
        # screenshot of the first claim. Encoding a 1x1 image here pays
        # the cost during BrowserManager construction (a "warm" path the
        # caller already considers part of startup) so claim 1 reaches
        # encoder steady-state immediately.
        self._warm_webp_encoder()

    @staticmethod
    def _warm_webp_encoder() -> None:
        """Trigger libwebp lazy-load with a throwaway 1x1 encode."""
        try:
            screenshot_capture.image_to_webp_bytes(Image.new("RGB", (1, 1), (0, 0, 0)))
        except Exception:  # pragma: no cover - warmup is best-effort
            logger.debug("WebP encoder warmup failed (non-fatal)", exc_info=True)

    @property
    def _persistent_session_key(self) -> str | None:
        """Derive the active persistent session key from ``_sessions``."""
        if not self.config.is_persistent or not self._sessions:
            return None
        return next(iter(self._sessions))

    async def ensure_browser(
        self,
        viewport: ViewportConfig | None = None,
        *,
        record_video_dir: str | None = None,
    ) -> Browser | BrowserContext:
        """Start Playwright and Chromium if needed.

        ``record_video_dir`` is consulted only on persistent-mode first launch
        (the context lives across runs, so video config is fixed at launch).
        Ephemeral mode honors it at ``_create_session`` time instead.
        """

        if self.config.is_persistent:
            if self._persistent_context is not None:
                return self._persistent_context

            playwright = await self._ensure_playwright()
            persistent_viewport = viewport or ViewportConfig()
            user_data_dir = self.config.resolved_user_data_dir
            assert user_data_dir is not None
            Path(user_data_dir).mkdir(parents=True, exist_ok=True)
            launch_kwargs: dict[str, Any] = {
                "user_data_dir": user_data_dir,
                "headless": self.headless,
                "viewport": _viewport_size_dict(persistent_viewport),
                "device_scale_factor": persistent_viewport.device_scale_factor,
            }
            _apply_record_video_kwargs(launch_kwargs, record_video_dir, persistent_viewport)
            self._persistent_context = await playwright.chromium.launch_persistent_context(**launch_kwargs)
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
        record_video_dir: str | None = None,
    ) -> BrowserSession:
        """Get or create a session for the provided key.

        When ``record_video_dir`` is provided, the underlying Playwright
        context records video for every page. One ``.webm`` per page; videos
        finalize when the context closes (or when the page closes for
        ephemeral contexts created here). The directory is created if it
        doesn't exist.
        """

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

        await self.ensure_browser(desired_viewport, record_video_dir=record_video_dir)
        session = await self._create_session(session_key, desired_viewport, record_video_dir=record_video_dir)
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

        # Split timing so the verbose log can attribute slowness to the
        # Chromium-side capture (CDP/Playwright protocol roundtrip + render)
        # vs the Pillow encode (CPU-bound). With Navigator latency already
        # logged in navigator_client, this gives a per-turn "LLM ms / capture
        # ms / encode ms" breakdown when running with `verify -v`.
        capture_started = time.perf_counter()
        image = await screenshot_capture.capture_screenshot_image(session, headless=self.headless)
        capture_ms = elapsed_ms(capture_started)

        encode_started = time.perf_counter()
        webp_bytes = screenshot_capture.image_to_webp_bytes(image)
        encode_ms = elapsed_ms(encode_started)

        logger.info(
            "Screenshot capture %.0f ms / encode %.0f ms / size %d KB",
            capture_ms,
            encode_ms,
            len(webp_bytes) // 1024,
        )
        return webp_bytes

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
        return await self._recreate_session(session_key, viewport, current_url=current_url if preserve_url else None)

    async def close_session(self, session_key: str) -> None:
        """Close a single session if it exists."""

        self._validate_session_key(session_key)
        if self.config.is_persistent:
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

        if self.config.is_persistent:
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
        await self._stop_playwright()

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
            user_data_dir=self.config.resolved_user_data_dir if self.config.is_persistent else None,
            sessions=sessions,
        )

    async def _create_session(
        self,
        session_key: str,
        viewport: ViewportConfig,
        *,
        record_video_dir: str | None = None,
    ) -> BrowserSession:
        if self.config.is_persistent:
            # Persistent mode receives video config at launch time via
            # ensure_browser; nothing to do per-session. The fallback forwards
            # record_video_dir so a context launched here still records
            # (normally _persistent_context is already set by the caller).
            context = self._persistent_context or await self.ensure_browser(viewport, record_video_dir=record_video_dir)
            assert isinstance(context, BrowserContext)
            page = await context.new_page()
            await page.set_viewport_size(_viewport_size_dict(viewport))
            await page.goto("about:blank", wait_until="domcontentloaded", timeout=self.navigation_timeout_ms)
        else:
            browser = await self.ensure_browser(viewport)
            assert isinstance(browser, Browser)
            context_kwargs: dict[str, Any] = {
                "viewport": _viewport_size_dict(viewport),
                "device_scale_factor": viewport.device_scale_factor,
            }
            _apply_record_video_kwargs(context_kwargs, record_video_dir, viewport)
            context = await browser.new_context(**context_kwargs)
            self._configure_context(context)
            page = await context.new_page()
        return BrowserSession(session_key=session_key, context=context, page=page, viewport=viewport)

    async def _ensure_viewport(self, session: BrowserSession, desired: ViewportConfig) -> BrowserSession:
        if session.viewport == desired:
            return session

        if session.viewport.device_scale_factor != desired.device_scale_factor:
            return await self._recreate_session(session.session_key, desired, current_url=session.page.url or None)

        await session.page.set_viewport_size(_viewport_size_dict(desired))
        session.viewport = desired
        return session

    async def _recreate_session(
        self,
        session_key: str,
        viewport: ViewportConfig | None,
        *,
        current_url: str | None,
    ) -> BrowserSession:
        """Close and reopen ``session_key`` with ``viewport``, restoring ``current_url`` if given.

        Shared by ``restart_session`` and ``_ensure_viewport``'s device-scale-factor
        change path, both of which must tear down and recreate the underlying
        Playwright context (a DPR change can't be applied to a live context).
        """
        await self.close_session(session_key)
        session = await self.get_session(session_key, viewport=viewport, reuse_session=False)
        if current_url:
            await self.goto(session, current_url)
        return session

    async def _ensure_playwright(self) -> Playwright:
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        return self._playwright

    def _configure_context(self, context: BrowserContext) -> None:
        context.set_default_navigation_timeout(self.navigation_timeout_ms)
        context.set_default_timeout(self.navigation_timeout_ms)

    async def _best_effort_wait_for_page_ready(self, page: Page) -> None:
        await best_effort_wait_until_ready(
            self._page_ready_checker,
            page,
            settle_delay_seconds=self.settle_delay_seconds,
            log_label="during navigation",
        )

    async def _stop_playwright_if_idle(self) -> None:
        if self._browser is not None or self._persistent_context is not None:
            return
        await self._stop_playwright()

    async def _stop_playwright(self) -> None:
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    def _evict_dead_persistent_session(self) -> None:
        """Remove a dead persistent session so it does not hold the name lock."""
        if not self.config.is_persistent:
            return
        for key in list(self._sessions):
            if not self._session_is_open(self._sessions[key]):
                logger.info("Evicting dead persistent session %r (page was closed or crashed)", key)
                self._sessions.pop(key, None)

    def _validate_session_key(self, session_key: str) -> None:
        if not self.config.is_persistent:
            return
        self._evict_dead_persistent_session()
        if self._persistent_session_key is None or session_key == self._persistent_session_key:
            return
        raise ValueError(f"{PERSISTENT_SESSION_KEY_ERROR} Active session key: {self._persistent_session_key!r}.")

    def _handle_persistent_context_close(self) -> None:
        self._persistent_context = None
        self._sessions.clear()

    @staticmethod
    def _safe_page_read(session: BrowserSession, read: Callable[[Page], _T], default: _T) -> _T:
        """Best-effort read of one `session.page` attribute; `default` if the page/context is gone."""
        try:
            return read(session.page)
        except PlaywrightError:
            return default

    @classmethod
    def _session_is_open(cls, session: BrowserSession) -> bool:
        return cls._safe_page_read(session, lambda page: not page.is_closed(), False)

    @classmethod
    def _safe_page_url(cls, session: BrowserSession) -> str | None:
        return cls._safe_page_read(session, lambda page: page.url or None, None)

    async def __aenter__(self) -> Self:
        if self.config.mode == BrowserMode.ephemeral:
            await self.ensure_browser()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()
