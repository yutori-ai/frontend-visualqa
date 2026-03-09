from __future__ import annotations

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from frontend_visualqa.browser import BrowserManager, image_bytes_to_data_url
from frontend_visualqa.schemas import ViewportConfig

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


class _SilentStaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return


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


def test_image_bytes_to_data_url_prefixes_payload() -> None:
    data_url = image_bytes_to_data_url(b"visual-qa", mime_type="image/webp")

    assert data_url.startswith("data:image/webp;base64,")


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
