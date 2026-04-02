from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from fakes import instantiate_with_supported_kwargs
from frontend_visualqa.schemas import ViewportConfig


def _import_actions_module():
    import importlib

    try:
        return importlib.import_module("frontend_visualqa.actions")
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("frontend_visualqa"):
            pytest.skip("frontend_visualqa.actions is not implemented in this worktree yet")
        raise


async def _call_execute_action(
    executor: Any,
    page: Any,
    action_name: str,
    arguments: dict[str, Any],
    viewport: ViewportConfig,
) -> Any:
    method = getattr(executor, "execute_action", None) or getattr(executor, "execute", None)
    if method is None:
        raise AssertionError("ActionExecutor must expose execute_action(...) or execute(...)")
    signature = inspect.signature(method)
    kwargs: dict[str, Any] = {}
    session = SimpleNamespace(page=page, viewport=viewport)

    if "page" in signature.parameters:
        kwargs["page"] = page
    if "session" in signature.parameters:
        kwargs["session"] = session
    if "action_name" in signature.parameters:
        kwargs["action_name"] = action_name
    if "name" in signature.parameters:
        kwargs["name"] = action_name
    if "arguments" in signature.parameters:
        kwargs["arguments"] = arguments
    if "args" in signature.parameters:
        kwargs["args"] = arguments
    if "action" in signature.parameters:
        kwargs["action"] = {"name": action_name, **arguments}
    if "viewport" in signature.parameters:
        kwargs["viewport"] = viewport
    if "viewport_config" in signature.parameters:
        kwargs["viewport_config"] = viewport

    return await method(**kwargs)


async def _call_execute_tool_call(
    executor: Any,
    page: Any,
    action_name: str,
    arguments: dict[str, Any],
    viewport: ViewportConfig,
) -> Any:
    session = SimpleNamespace(page=page, viewport=viewport)
    tool_call = SimpleNamespace(function=SimpleNamespace(name=action_name, arguments=json.dumps(arguments)))
    return await executor.execute_tool_call(session, tool_call)


class FakeMouse:
    def __init__(self) -> None:
        self.clicks: list[tuple[int, int, str]] = []
        self.click_counts: list[int] = []
        self.double_clicks: list[tuple[int, int]] = []
        self.moves: list[tuple[int, int]] = []
        self.wheels: list[tuple[float, float]] = []
        self.down_count = 0
        self.up_count = 0

    async def click(self, x: int, y: int, *, button: str = "left", click_count: int = 1) -> None:
        self.clicks.append((x, y, button))
        self.click_counts.append(click_count)

    async def dblclick(self, x: int, y: int) -> None:
        self.double_clicks.append((x, y))

    async def move(self, x: int, y: int, *, steps: int | None = None) -> None:
        del steps
        self.moves.append((x, y))

    async def wheel(self, delta_x: float, delta_y: float) -> None:
        self.wheels.append((delta_x, delta_y))

    async def down(self) -> None:
        self.down_count += 1

    async def up(self) -> None:
        self.up_count += 1


class FakeKeyboard:
    def __init__(self) -> None:
        self.typed: list[str] = []
        self.pressed: list[str] = []

    async def type(self, text: str) -> None:
        self.typed.append(text)

    async def press(self, key: str) -> None:
        self.pressed.append(key)


class FakePage:
    def __init__(self) -> None:
        self.mouse = FakeMouse()
        self.keyboard = FakeKeyboard()
        self.url = "http://fixture.local/start"
        self.viewport_size = {"width": 1280, "height": 800}
        self.goto_calls: list[tuple[str, dict[str, Any]]] = []
        self.reload_calls: list[dict[str, Any]] = []
        self.go_back_calls: list[dict[str, Any]] = []
        self.go_forward_calls: list[dict[str, Any]] = []
        self.wait_states: list[tuple[str, dict[str, Any]]] = []
        self.evaluate_results: list[Any] = []
        self.evaluate_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.aria_snapshot_result: str | None = None

    async def goto(self, url: str, **kwargs: Any) -> SimpleNamespace:
        self.url = url
        self.goto_calls.append((url, kwargs))
        return SimpleNamespace(url=url)

    async def reload(self, **kwargs: Any) -> SimpleNamespace:
        self.reload_calls.append(kwargs)
        return SimpleNamespace(url=self.url)

    async def go_back(self, **kwargs: Any) -> None:
        self.go_back_calls.append(kwargs)
        self.url = "http://fixture.local/previous"
        return None

    async def go_forward(self, **kwargs: Any) -> None:
        self.go_forward_calls.append(kwargs)
        self.url = "http://fixture.local/next"
        return None

    async def wait_for_load_state(self, state: str, **kwargs: Any) -> None:
        self.wait_states.append((state, kwargs))

    async def evaluate(self, script: str, *args: Any) -> Any:
        self.evaluate_calls.append((script, args))
        if self.evaluate_results:
            return self.evaluate_results.pop(0)
        raise AssertionError("No evaluate result queued")

    def locator(self, selector: str) -> SimpleNamespace:
        assert selector == "body"
        return SimpleNamespace(aria_snapshot=AsyncMock(return_value=self.aria_snapshot_result))


def _make_overlay_enabled_page(call_order: list[tuple[Any, ...]]) -> FakePage:
    page = FakePage()

    async def _click(x: int, y: int, *, button: str = "left", click_count: int = 1) -> None:
        call_order.append(("click", x, y, button, click_count))

    async def _goto(url: str, **kwargs: Any) -> SimpleNamespace:
        call_order.append(("goto", url, kwargs))
        page.url = url
        return SimpleNamespace(url=url)

    async def _wait_for_load_state(state: str, **kwargs: Any) -> None:
        call_order.append(("wait_for_load_state", state, kwargs))

    page.mouse.click = AsyncMock(side_effect=_click)
    page.goto = AsyncMock(side_effect=_goto)
    page.wait_for_load_state = AsyncMock(side_effect=_wait_for_load_state)
    return page


def test_scale_coordinates_maps_n1_grid_to_viewport_pixels() -> None:
    module = _import_actions_module()
    executor = instantiate_with_supported_kwargs(
        module.ActionExecutor,
        navigation_timeout_ms=1_000,
        settle_delay_seconds=0,
    )
    scale_coordinates = getattr(executor, "scale_coordinates", None) or getattr(module, "scale_coordinates", None)
    assert scale_coordinates is not None, "actions module must expose scale_coordinates(...)"

    signature = inspect.signature(scale_coordinates)
    if "viewport" in signature.parameters:
        result = scale_coordinates([500, 250], ViewportConfig(width=1280, height=800, device_scale_factor=1))
    else:
        result = scale_coordinates([500, 250], 1280, 800)

    assert result == (640, 200)


@pytest.mark.asyncio
async def test_execute_action_left_click_scales_coordinates_before_dispatch() -> None:
    module = _import_actions_module()
    executor = instantiate_with_supported_kwargs(
        module.ActionExecutor,
        navigation_timeout_ms=1_000,
        settle_delay_seconds=0,
    )
    page = FakePage()
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)

    await _call_execute_action(executor, page, "left_click", {"coordinates": [500, 250]}, viewport)

    assert page.mouse.clicks == [(640, 200, "left")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action_name", "arguments", "expected_url_attr"),
    [
        ("goto_url", {"url": "http://fixture.local/modal"}, "http://fixture.local/modal"),
        ("go_back", {}, "http://fixture.local/previous"),
        ("refresh", {}, "http://fixture.local/start"),
    ],
)
async def test_navigation_actions_wait_for_domcontentloaded(
    action_name: str,
    arguments: dict[str, Any],
    expected_url_attr: str,
) -> None:
    module = _import_actions_module()
    executor = instantiate_with_supported_kwargs(
        module.ActionExecutor,
        navigation_timeout_ms=1_000,
        settle_delay_seconds=0,
    )
    page = FakePage()
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)

    await _call_execute_action(executor, page, action_name, arguments, viewport)

    assert page.url == expected_url_attr
    assert any(state == "domcontentloaded" for state, _ in page.wait_states)
    assert not any(state == "networkidle" for state, _ in page.wait_states)


@pytest.mark.asyncio
async def test_execute_action_type_and_scroll_use_keyboard_and_mouse_inputs() -> None:
    module = _import_actions_module()
    executor = instantiate_with_supported_kwargs(
        module.ActionExecutor,
        navigation_timeout_ms=1_000,
        settle_delay_seconds=0,
    )
    page = FakePage()
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)

    await _call_execute_action(
        executor,
        page,
        "type",
        {"text": "release notes", "press_enter_after": True, "clear_before": True},
        viewport,
    )
    await _call_execute_action(
        executor,
        page,
        "scroll",
        {"coordinates": [500, 500], "direction": "down", "amount": 2},
        viewport,
    )

    assert page.keyboard.typed == ["release notes"]
    assert "Enter" in page.keyboard.pressed
    assert page.mouse.wheels
    assert page.mouse.wheels[-1][1] == pytest.approx(160.0, abs=1e-6)


@pytest.mark.asyncio
async def test_execute_action_supports_hover_drag_and_multi_click_variants() -> None:
    module = _import_actions_module()
    executor = instantiate_with_supported_kwargs(
        module.ActionExecutor,
        navigation_timeout_ms=1_000,
        settle_delay_seconds=0,
    )
    page = FakePage()
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)

    await _call_execute_action(executor, page, "hover", {"coordinates": [250, 500]}, viewport)
    await _call_execute_action(
        executor,
        page,
        "drag",
        {"start_coordinates": [100, 100], "coordinates": [700, 600]},
        viewport,
    )
    await _call_execute_action(executor, page, "triple_click", {"coordinates": [500, 250]}, viewport)
    await _call_execute_action(executor, page, "right_click", {"coordinates": [500, 250]}, viewport)

    assert page.mouse.moves[0] == (320, 400)
    assert page.mouse.down_count == 1
    assert page.mouse.up_count == 1
    assert page.mouse.click_counts[-2:] == [3, 1]
    assert page.mouse.clicks[-1] == (640, 200, "right")


@pytest.mark.asyncio
async def test_execute_action_left_click_previews_before_dispatch_and_waits_for_cursor_transition() -> None:
    module = _import_actions_module()
    call_order: list[tuple[Any, ...]] = []
    page = _make_overlay_enabled_page(call_order)
    overlay = MagicMock()

    preview_started = asyncio.Event()
    release_preview = asyncio.Event()

    async def _preview_action(action_type: str, **kwargs: Any) -> None:
        call_order.append(("preview_action", action_type, kwargs, "start"))
        preview_started.set()
        await release_preview.wait()
        call_order.append(("preview_action", action_type, kwargs, "end"))

    overlay.preview_action = AsyncMock(side_effect=_preview_action)

    executor = instantiate_with_supported_kwargs(
        module.ActionExecutor,
        navigation_timeout_ms=1_000,
        settle_delay_seconds=0,
    )
    executor.overlay = overlay

    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)
    task = asyncio.create_task(_call_execute_action(executor, page, "left_click", {"coordinates": [500, 250]}, viewport))

    await asyncio.wait_for(preview_started.wait(), timeout=1)
    assert call_order == [
        (
            "preview_action",
            "left_click",
            {"x": 640, "y": 200, "num_clicks": 1},
            "start",
        )
    ]
    assert not page.mouse.clicks

    release_preview.set()
    trace = await task

    assert trace == "left_click([640, 200])"
    assert call_order[1] == ("preview_action", "left_click", {"x": 640, "y": 200, "num_clicks": 1}, "end")
    assert call_order[2] == ("click", 640, 200, "left", 1)
    assert any(entry[0] == "wait_for_load_state" for entry in call_order)
    overlay.preview_action.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_action_navigation_shows_status_before_dispatch() -> None:
    module = _import_actions_module()
    call_order: list[tuple[Any, ...]] = []
    page = _make_overlay_enabled_page(call_order)
    overlay = MagicMock()

    async def _set_status(label: str) -> None:
        call_order.append(("set_status", label))

    overlay.set_status = AsyncMock(side_effect=_set_status)

    executor = instantiate_with_supported_kwargs(
        module.ActionExecutor,
        navigation_timeout_ms=1_000,
        settle_delay_seconds=0,
    )
    executor.overlay = overlay

    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)
    trace = await _call_execute_action(
        executor,
        page,
        "goto_url",
        {"url": "http://fixture.local/modal"},
        viewport,
    )

    assert trace == "goto_url(\"http://fixture.local/modal\")"
    assert call_order[0] == ("set_status", "Navigating")
    assert call_order[1][0] == "goto"
    assert overlay.set_status.await_count == 1


@pytest.mark.asyncio
async def test_execute_action_semantic_key_shortcut_uses_single_overlay_footer() -> None:
    module = _import_actions_module()
    call_order: list[tuple[Any, ...]] = []
    page = _make_overlay_enabled_page(call_order)
    overlay = MagicMock()

    status_calls: list[str] = []

    async def _set_status(label: str) -> None:
        status_calls.append(label)
        call_order.append(("set_status", label))

    overlay.set_status = AsyncMock(side_effect=_set_status)

    executor = instantiate_with_supported_kwargs(
        module.ActionExecutor,
        navigation_timeout_ms=1_000,
        settle_delay_seconds=0,
    )
    executor.overlay = overlay

    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)
    trace = await _call_execute_action(executor, page, "key_press", {"key_comb": "F5"}, viewport)

    assert trace == "key_press(F5)"
    assert status_calls == ["Refreshing"]
    assert call_order[0] == ("set_status", "Refreshing")
    assert page.reload_calls


@pytest.mark.asyncio
async def test_execute_action_hover_previews_before_mouse_move() -> None:
    module = _import_actions_module()
    call_order: list[tuple[Any, ...]] = []
    page = _make_overlay_enabled_page(call_order)

    async def _move(x: int, y: int, *, steps: int | None = None) -> None:
        call_order.append(("move", x, y))

    page.mouse.move = AsyncMock(side_effect=_move)

    overlay = MagicMock()

    async def _preview_action(action_type: str, **kwargs: Any) -> None:
        call_order.append(("preview_action", action_type, kwargs))

    overlay.preview_action = AsyncMock(side_effect=_preview_action)

    executor = instantiate_with_supported_kwargs(
        module.ActionExecutor,
        navigation_timeout_ms=1_000,
        settle_delay_seconds=0,
    )
    executor.overlay = overlay

    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)
    trace = await _call_execute_action(executor, page, "hover", {"coordinates": [250, 500]}, viewport)

    assert trace == "hover([320, 400])"
    assert call_order[0] == ("preview_action", "hover", {"x": 320, "y": 400})
    assert call_order[1] == ("move", 320, 400)
    overlay.preview_action.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_action_drag_previews_before_drag_motion() -> None:
    module = _import_actions_module()
    call_order: list[tuple[Any, ...]] = []
    page = _make_overlay_enabled_page(call_order)

    async def _move(x: int, y: int, *, steps: int | None = None) -> None:
        call_order.append(("move", x, y))

    async def _down() -> None:
        call_order.append(("down",))

    async def _up() -> None:
        call_order.append(("up",))

    page.mouse.move = AsyncMock(side_effect=_move)
    page.mouse.down = AsyncMock(side_effect=_down)
    page.mouse.up = AsyncMock(side_effect=_up)

    overlay = MagicMock()

    async def _preview_action(action_type: str, **kwargs: Any) -> None:
        call_order.append(("preview_action", action_type, kwargs))

    overlay.preview_action = AsyncMock(side_effect=_preview_action)

    executor = instantiate_with_supported_kwargs(
        module.ActionExecutor,
        navigation_timeout_ms=1_000,
        settle_delay_seconds=0,
    )
    executor.overlay = overlay

    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)
    trace = await _call_execute_action(
        executor,
        page,
        "drag",
        {"start_coordinates": [100, 100], "coordinates": [700, 600]},
        viewport,
    )

    assert trace == "drag([128, 80], [896, 480])"
    assert call_order[0] == ("preview_action", "drag", {"x": 896, "y": 480, "start_x": 128, "start_y": 80})
    assert call_order[1][0] == "move"
    assert call_order[2][0] == "down"
    assert call_order[3][0] == "move"
    assert call_order[4][0] == "up"
    overlay.preview_action.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_action_key_press_supports_shortcuts_and_semantic_navigation() -> None:
    module = _import_actions_module()
    executor = instantiate_with_supported_kwargs(
        module.ActionExecutor,
        navigation_timeout_ms=1_000,
        settle_delay_seconds=0,
    )
    page = FakePage()
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)

    await _call_execute_action(executor, page, "key_press", {"key_comb": "ctrl+a"}, viewport)
    await _call_execute_action(executor, page, "key_press", {"key_comb": "F5"}, viewport)
    await _call_execute_action(executor, page, "key_press", {"key_comb": "Alt+ArrowRight"}, viewport)
    await _call_execute_action(executor, page, "wait", {"seconds": 0}, viewport)

    assert "ControlOrMeta+a" in page.keyboard.pressed
    assert page.reload_calls
    assert page.go_forward_calls


@pytest.mark.asyncio
async def test_execute_action_key_press_supports_repeated_key_sequences() -> None:
    module = _import_actions_module()
    executor = instantiate_with_supported_kwargs(
        module.ActionExecutor,
        navigation_timeout_ms=1_000,
        settle_delay_seconds=0,
    )
    page = FakePage()
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)

    trace = await _call_execute_action(executor, page, "key_press", {"key_comb": "Tab Tab Tab"}, viewport)

    assert trace == "key_press_sequence(Tab, Tab, Tab)"
    assert page.keyboard.pressed == ["Tab", "Tab", "Tab"]


@pytest.mark.asyncio
async def test_execute_action_key_press_ignores_zoom_shortcuts() -> None:
    module = _import_actions_module()
    executor = instantiate_with_supported_kwargs(
        module.ActionExecutor,
        navigation_timeout_ms=1_000,
        settle_delay_seconds=0,
    )
    page = FakePage()
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)

    trace = await _call_execute_action(executor, page, "key_press", {"key_comb": "ControlOrMeta+Minus"}, viewport)

    assert trace == "key_press(ControlOrMeta+Minus)"
    assert page.keyboard.pressed == []


@pytest.mark.asyncio
async def test_execute_action_screenshot_is_a_no_op_for_n1_default_tool_calls() -> None:
    module = _import_actions_module()
    executor = instantiate_with_supported_kwargs(
        module.ActionExecutor,
        navigation_timeout_ms=1_000,
        settle_delay_seconds=0,
    )
    page = FakePage()
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)

    trace = await _call_execute_action(executor, page, "screenshot", {}, viewport)

    assert trace == "screenshot()"
    assert not page.mouse.clicks
    assert not page.keyboard.pressed


@pytest.mark.asyncio
async def test_execute_tool_call_extract_content_and_links_returns_snapshot_and_links() -> None:
    module = _import_actions_module()
    executor = instantiate_with_supported_kwargs(
        module.ActionExecutor,
        navigation_timeout_ms=1_000,
        settle_delay_seconds=0,
    )
    page = FakePage()
    page.url = "http://fixture.local/cart"
    page.aria_snapshot_result = "\n".join(
        [
            '- heading "Shopping Cart"',
            '- link "Wireless Headphones Pro"',
            "  - /url: http://fixture.local/products/1",
            '- link "USB-C Hub Pro 2"',
            "  - /url: http://fixture.local/products/2",
        ]
    )
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)

    result = await _call_execute_tool_call(executor, page, "extract_content_and_links", {}, viewport)

    assert result.trace == "extract_content_and_links()"
    assert result.current_url == "http://fixture.local/cart"
    assert "Accessible page snapshot:" in result.output_text
    assert '- heading "Shopping Cart"' in result.output_text
    assert '- [Wireless Headphones Pro](http://fixture.local/products/1)' in result.output_text
    assert '- [USB-C Hub Pro 2](http://fixture.local/products/2)' in result.output_text


@pytest.mark.asyncio
async def test_execute_action_rejects_invalid_scroll_direction() -> None:
    module = _import_actions_module()
    executor = instantiate_with_supported_kwargs(
        module.ActionExecutor,
        navigation_timeout_ms=1_000,
        settle_delay_seconds=0,
    )
    page = FakePage()
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)

    with pytest.raises(Exception):
        await _call_execute_action(
            executor,
            page,
            "scroll",
            {"coordinates": [500, 500], "direction": "diagonal", "amount": 1},
            viewport,
        )


@pytest.mark.asyncio
async def test_execute_tool_call_rejects_removed_find_tool() -> None:
    module = _import_actions_module()
    executor = instantiate_with_supported_kwargs(
        module.ActionExecutor,
        navigation_timeout_ms=1_000,
        settle_delay_seconds=0,
    )
    page = FakePage()
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1)

    with pytest.raises(Exception):
        await _call_execute_tool_call(executor, page, "find", {"text": "ATMOS"}, viewport)
