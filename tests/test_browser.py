from __future__ import annotations

import base64
import io
from functools import partial
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from PIL import Image

from frontend_visualqa.browser import BrowserManager, BrowserSession, PERSISTENT_SESSION_KEY_ERROR, image_bytes_to_data_url
from frontend_visualqa.schemas import BrowserConfig, BrowserMode, DEFAULT_PERSISTENT_USER_DATA_DIR, ViewportConfig

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


class _SilentStaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return


class _CookieHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/set-cookie":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Set-Cookie", "qa_cookie=present; Max-Age=3600; Path=/")
            self.end_headers()
            self.wfile.write(b"<html><body>cookie set</body></html>")
            return

        if self.path == "/echo-cookie":
            cookie_header = self.headers.get("Cookie", "")
            body = f"<html><body><div id='cookie'>{cookie_header}</div></body></html>".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return


def _png_bytes(*, size: tuple[int, int] = (8, 6), color: tuple[int, int, int] = (29, 205, 152)) -> bytes:
    image = Image.new("RGB", size, color=color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture()
def example_url() -> str:
    handler = partial(_SilentStaticHandler, directory=str(PACKAGE_ROOT / "examples"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/test_page.html"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.fixture()
def cookie_server() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CookieHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_image_bytes_to_data_url_prefixes_payload() -> None:
    data_url = image_bytes_to_data_url(b"visual-qa", mime_type="image/webp")

    assert data_url.startswith("data:image/webp;base64,")


def test_browser_config_defaults_persistent_user_data_dir() -> None:
    config = BrowserConfig(mode=BrowserMode.persistent)

    assert config.user_data_dir == str(DEFAULT_PERSISTENT_USER_DATA_DIR)
    assert config.resolved_user_data_dir == str(DEFAULT_PERSISTENT_USER_DATA_DIR)


@pytest.mark.asyncio
async def test_browser_manager_capture_screenshot_prefers_cdp_in_headed_mode() -> None:
    class FakeCDPSession:
        def __init__(self, payload: bytes) -> None:
            self.payload = payload
            self.send_calls: list[tuple[str, dict[str, object]]] = []
            self.detach_calls = 0

        async def send(self, method: str, params: dict[str, object] | None = None) -> dict[str, str]:
            resolved_params = params or {}
            self.send_calls.append((method, resolved_params))
            if method == "Page.getLayoutMetrics":
                return {
                    "visualViewport": {"clientWidth": 2560, "clientHeight": 1600},
                    "cssVisualViewport": {"pageX": 0, "pageY": 0, "clientWidth": 1280, "clientHeight": 800},
                }
            return {"data": base64.b64encode(self.payload).decode("ascii")}

        async def detach(self) -> None:
            self.detach_calls += 1

    class FakeContext:
        def __init__(self, cdp_session: FakeCDPSession) -> None:
            self.cdp_session = cdp_session
            self.new_cdp_session_calls = 0

        async def new_cdp_session(self, page: object) -> FakeCDPSession:
            self.new_cdp_session_calls += 1
            return self.cdp_session

    class FakePage:
        def __init__(self) -> None:
            self.screenshot_calls: list[dict[str, object]] = []

        async def screenshot(self, **kwargs: object) -> bytes:
            self.screenshot_calls.append(kwargs)
            return _png_bytes(color=(255, 0, 0))

    png_payload = _png_bytes()
    cdp_session = FakeCDPSession(png_payload)
    context = FakeContext(cdp_session)
    page = FakePage()
    session = BrowserSession(
        session_key="default",
        context=context,  # type: ignore[arg-type]
        page=page,  # type: ignore[arg-type]
        viewport=ViewportConfig(width=1280, height=800, device_scale_factor=1),
    )
    manager = BrowserManager(config=BrowserConfig(headless=False))

    screenshot = await manager.capture_screenshot(session)

    assert screenshot[:4] == b"RIFF"
    assert b"WEBP" in screenshot[:24]
    assert context.new_cdp_session_calls == 1
    assert cdp_session.send_calls == [
        ("Page.getLayoutMetrics", {}),
        (
            "Page.captureScreenshot",
            {
                "format": "png",
                "captureBeyondViewport": False,
                "clip": {
                    "x": 0.0,
                    "y": 0.0,
                    "width": 1280.0,
                    "height": 800.0,
                    "scale": 0.5,
                },
            },
        ),
    ]
    assert cdp_session.detach_calls == 1
    assert page.screenshot_calls == []


@pytest.mark.asyncio
async def test_browser_manager_capture_screenshot_falls_back_to_playwright_in_headed_mode() -> None:
    class FakeCDPSession:
        def __init__(self) -> None:
            self.detach_calls = 0

        async def send(self, method: str, params: dict[str, object] | None = None) -> dict[str, str]:
            if method == "Page.getLayoutMetrics":
                return {
                    "visualViewport": {"clientWidth": 2560, "clientHeight": 1600},
                    "cssVisualViewport": {"pageX": 0, "pageY": 0, "clientWidth": 1280, "clientHeight": 800},
                }
            raise RuntimeError("capture failed")

        async def detach(self) -> None:
            self.detach_calls += 1

    class FakeContext:
        def __init__(self, cdp_session: FakeCDPSession) -> None:
            self.cdp_session = cdp_session

        async def new_cdp_session(self, page: object) -> FakeCDPSession:
            return self.cdp_session

    class FakePage:
        def __init__(self) -> None:
            self.screenshot_calls: list[dict[str, object]] = []

        async def screenshot(self, **kwargs: object) -> bytes:
            self.screenshot_calls.append(kwargs)
            return _png_bytes(color=(0, 0, 255))

    cdp_session = FakeCDPSession()
    context = FakeContext(cdp_session)
    page = FakePage()
    session = BrowserSession(
        session_key="default",
        context=context,  # type: ignore[arg-type]
        page=page,  # type: ignore[arg-type]
        viewport=ViewportConfig(width=1280, height=800, device_scale_factor=1),
    )
    manager = BrowserManager(config=BrowserConfig(headless=False))

    screenshot = await manager.capture_screenshot(session)

    assert screenshot[:4] == b"RIFF"
    assert b"WEBP" in screenshot[:24]
    assert cdp_session.detach_calls == 1
    assert page.screenshot_calls == [{"type": "png"}]


def test_browser_manager_build_cdp_capture_params_defaults_when_metrics_are_missing() -> None:
    params = BrowserManager._build_cdp_capture_params({})

    assert params == {
        "format": "png",
        "captureBeyondViewport": False,
    }


@pytest.mark.parametrize(
    ("surface_width", "surface_height", "expected_scale"),
    [
        (1280, 800, 1.0),
        (1920, 1200, 2 / 3),
        (2560, 1600, 0.5),
    ],
)
def test_browser_manager_build_cdp_capture_params_derives_scale_from_runtime_metrics(
    surface_width: int,
    surface_height: int,
    expected_scale: float,
) -> None:
    params = BrowserManager._build_cdp_capture_params(
        {
            "visualViewport": {"clientWidth": surface_width, "clientHeight": surface_height},
            "cssVisualViewport": {"pageX": 0, "pageY": 0, "clientWidth": 1280, "clientHeight": 800},
        }
    )

    assert params == {
        "format": "png",
        "captureBeyondViewport": False,
        "clip": {
            "x": 0.0,
            "y": 0.0,
            "width": 1280.0,
            "height": 800.0,
            "scale": expected_scale,
        },
    }


@pytest.mark.asyncio
async def test_browser_manager_navigates_reuses_session_and_captures_webp(example_url: str) -> None:
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)

    async with BrowserManager(headless=True, settle_delay_seconds=0) as manager:
        session = await manager.get_session("qa-fixture", viewport=viewport)
        final_url = await manager.goto(session, example_url)

        assert final_url == example_url
        assert await session.page.locator("h1").text_content() == "Frontend Visual QA Playground"
        assert await session.page.locator("[data-testid='primary-red-button']").is_visible()

        screenshot = await manager.capture_screenshot(session)
        assert screenshot[:4] == b"RIFF"
        assert b"WEBP" in screenshot[:24]

        same_session = await manager.get_session("qa-fixture", viewport=viewport, reuse_session=True)
        assert same_session.page is session.page

        resized = await manager.get_session(
            "qa-fixture",
            viewport=ViewportConfig(width=390, height=844, device_scale_factor=1),
            reuse_session=True,
        )
        assert resized.page is session.page
        assert resized.viewport.width == 390

        measured = await resized.page.evaluate(
            """() => ({
                width: window.innerWidth,
                height: window.innerHeight,
                badge: document.querySelector('[data-testid="status-badge"]').textContent.trim(),
            })"""
        )
        assert measured["width"] == 390
        assert measured["height"] == 844
        assert measured["badge"] == "QA Ready"

        status = manager.status()
        assert status.browser_running is True
        assert status.browser_mode == BrowserMode.ephemeral
        assert status.user_data_dir is None
        assert [item.session_key for item in status.sessions] == ["qa-fixture"]
        assert status.sessions[0].current_url == example_url


@pytest.mark.asyncio
async def test_browser_manager_restart_session_preserves_last_url(example_url: str) -> None:
    viewport = ViewportConfig(width=1024, height=768, device_scale_factor=1)

    async with BrowserManager(headless=True, settle_delay_seconds=0) as manager:
        session = await manager.get_session("restartable", viewport=viewport)
        await manager.goto(session, example_url)
        original_page = session.page

        restarted = await manager.restart_session("restartable", viewport=viewport)

        assert restarted.page is not original_page
        assert restarted.page.url == example_url
        assert await restarted.page.locator("[data-testid='task-row']").is_visible()


@pytest.mark.asyncio
async def test_example_fixture_supports_modal_tabs_search_and_toast(example_url: str) -> None:
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)

    async with BrowserManager(headless=True, settle_delay_seconds=0) as manager:
        session = await manager.get_session("interactive-fixture", viewport=viewport)
        await manager.goto(session, example_url)

        await session.page.locator("[data-testid='task-row']").click()
        assert await session.page.locator("[data-testid='task-modal']").is_visible()
        assert await session.page.locator("#modal-title").text_content() == "Edit Task"
        assert await session.page.locator("[data-testid='save-task']").is_visible()
        await session.page.locator("#close-modal").click(force=True)
        assert await session.page.locator("[data-testid='task-modal']").is_hidden()

        await session.page.locator("#tab-details").click(force=True)
        assert await session.page.locator("#panel-details").is_visible()
        assert await session.page.locator("#tab-details").get_attribute("aria-selected") == "true"

        await session.page.locator("[data-testid='search-input']").fill("toast preview")
        chip_text = await session.page.locator("[data-testid='query-chip']").text_content()
        assert chip_text is not None
        assert "toast preview" in chip_text

        await session.page.locator("#show-toast").click(force=True)
        assert await session.page.locator("#toast").get_attribute("data-visible") == "true"


@pytest.mark.asyncio
async def test_browser_manager_persistent_mode_preserves_cookies_across_relaunch(
    cookie_server: str,
    tmp_path: Path,
) -> None:
    profile_dir = tmp_path / "browser-profile"
    config = BrowserConfig(
        mode=BrowserMode.persistent,
        user_data_dir=str(profile_dir),
        headless=True,
        settle_delay_seconds=0,
    )
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)

    async with BrowserManager(config=config) as manager:
        session = await manager.get_session(viewport=viewport)
        await manager.goto(session, f"{cookie_server}/set-cookie")

    assert profile_dir.exists()

    async with BrowserManager(config=config) as manager:
        session = await manager.get_session(viewport=viewport)
        await manager.goto(session, f"{cookie_server}/echo-cookie")
        cookie_text = await session.page.locator("#cookie").text_content()
        status = manager.status()

    assert cookie_text is not None
    assert "qa_cookie=present" in cookie_text
    assert status.browser_mode == BrowserMode.persistent
    assert status.user_data_dir == str(profile_dir)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["get_session", "close_session", "restart_session", "set_viewport"])
async def test_browser_manager_persistent_mode_rejects_non_default_session_keys(
    operation: str,
    tmp_path: Path,
) -> None:
    manager = BrowserManager(
        config=BrowserConfig(
            mode=BrowserMode.persistent,
            user_data_dir=str(tmp_path / "browser-profile"),
            headless=True,
            settle_delay_seconds=0,
        )
    )

    try:
        with pytest.raises(ValueError, match=PERSISTENT_SESSION_KEY_ERROR):
            if operation == "get_session":
                await manager.get_session("secondary", viewport=ViewportConfig())
            elif operation == "close_session":
                await manager.close_session("secondary")
            elif operation == "restart_session":
                await manager.restart_session("secondary", viewport=ViewportConfig())
            else:
                await manager.set_viewport("secondary", ViewportConfig())
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_browser_manager_persistent_mode_uses_dedicated_automation_page(
    example_url: str,
    tmp_path: Path,
) -> None:
    manager = BrowserManager(
        config=BrowserConfig(
            mode=BrowserMode.persistent,
            user_data_dir=str(tmp_path / "browser-profile"),
            headless=True,
            settle_delay_seconds=0,
        )
    )
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)

    try:
        await manager.ensure_browser(viewport)
        assert manager._persistent_context is not None
        restored_page = await manager._persistent_context.new_page()
        await restored_page.goto(example_url, wait_until="domcontentloaded")

        session = await manager.get_session("default", viewport=viewport)

        assert session.page is not restored_page
        assert session.page.url == "about:blank"
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_browser_manager_persistent_mode_relaunches_for_dpr_change(
    example_url: str,
    tmp_path: Path,
) -> None:
    manager = BrowserManager(
        config=BrowserConfig(
            mode=BrowserMode.persistent,
            user_data_dir=str(tmp_path / "browser-profile"),
            headless=True,
            settle_delay_seconds=0,
        )
    )
    initial_viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)
    refreshed_viewport = ViewportConfig(width=1280, height=800, device_scale_factor=2)

    try:
        session = await manager.get_session("default", viewport=initial_viewport)
        await manager.goto(session, example_url)
        original_context = session.context

        refreshed = await manager.get_session("default", viewport=refreshed_viewport, reuse_session=True)

        assert refreshed.context is not original_context
        assert refreshed.viewport == refreshed_viewport
        assert refreshed.page.url == example_url
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_browser_manager_persistent_mode_recovers_after_external_context_close(
    example_url: str,
    tmp_path: Path,
) -> None:
    manager = BrowserManager(
        config=BrowserConfig(
            mode=BrowserMode.persistent,
            user_data_dir=str(tmp_path / "browser-profile"),
            headless=True,
            settle_delay_seconds=0,
        )
    )
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)

    try:
        session = await manager.get_session("default", viewport=viewport)
        await manager.goto(session, example_url)
        await session.context.close()

        assert manager.status().browser_running is False
        assert manager.status().sessions == []

        recovered = await manager.get_session("default", viewport=viewport, reuse_session=True)

        assert recovered is not session
        assert recovered.context is not session.context
        # After recovery, the dedicated automation page starts at about:blank.
        # Navigate to example_url and verify we have a working page.
        await manager.goto(recovered, example_url)
        assert await recovered.page.locator("h1").text_content() == "Frontend Visual QA Playground"
    finally:
        await manager.close()
