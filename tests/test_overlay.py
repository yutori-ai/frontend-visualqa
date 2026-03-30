"""Tests for the headed-mode overlay controller."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_mock_page(
    *,
    focused_center: dict[str, int] | None = None,
    evaluate_side_effect: Exception | None = None,
) -> MagicMock:
    page = MagicMock()

    async def evaluate(script: str, *args: object) -> object:
        del args
        if evaluate_side_effect is not None:
            raise evaluate_side_effect
        script_text = str(script)
        if "document.activeElement" in script_text:
            return focused_center
        return None

    page.evaluate = AsyncMock(side_effect=evaluate)
    return page


class TestOverlayControllerLifecycle:
    @pytest.mark.asyncio
    async def test_claim_started_injects_persistent_and_transient_roots(self) -> None:
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.claim_started()

        assert controller._active is True
        assert controller._current_status == "Analyzing"
        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        assert any("__n1PersistentRoot" in script for script in scripts)
        assert any("__n1TransientRoot" in script for script in scripts)

    @pytest.mark.asyncio
    async def test_claim_ended_removes_all_roots_and_is_idempotent(self) -> None:
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.claim_started()
        page.evaluate.reset_mock()

        await controller.claim_ended()

        assert controller._active is False
        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        assert any("__n1PersistentRoot" in script for script in scripts)
        assert any("__n1TransientRoot" in script for script in scripts)
        assert any("__n1ClickStyle" in script for script in scripts)
        assert any("__n1ScrollStyle" in script for script in scripts)
        assert any("__n1TypeStyle" in script for script in scripts)
        assert any("__n1DragStyle" in script for script in scripts)
        assert any("__n1ThoughtTimer" in script for script in scripts)

        page.evaluate.reset_mock()
        await controller.claim_ended()
        page.evaluate.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_status_updates_label_when_active(self) -> None:
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.claim_started()
        page.evaluate.reset_mock()

        await controller.set_status("Navigating")

        assert controller._current_status == "Navigating"
        script = str(page.evaluate.call_args.args[0])
        assert "__n1StatusChip" in script
        assert "Navigating" in script


class TestOverlayCursor:
    @pytest.mark.asyncio
    async def test_transient_root_creates_cursor_img(self) -> None:
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.claim_started()

        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        assert any("__n1TransientRoot" in script and "__n1Cursor" in script and "createElement('img')" in script for script in scripts)

    @pytest.mark.asyncio
    async def test_move_cursor_updates_position(self) -> None:
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page()
        controller = OverlayController(page)
        controller._active = True

        await controller._move_cursor(150, 250)

        script = str(page.evaluate.call_args.args[0])
        assert "__n1Cursor" in script
        assert "150px" in script
        assert "250px" in script


class TestOverlayInformationalCards:
    @pytest.mark.asyncio
    async def test_show_thought_injects_thought_card_with_clipped_text(self) -> None:
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.claim_started()
        page.evaluate.reset_mock()

        text = "Inspect the quota widget before deciding because the bar and label may disagree on completion."
        await controller.show_thought(text)

        call = page.evaluate.call_args_list[-1]
        script = str(call.args[0])
        assert "__n1ThoughtCard" in script
        assert "left:50%" in script
        assert "width:min(720px,calc(100vw - 48px))" in script
        assert "backdrop-filter:blur(12px)" in script
        assert "font-size:17px" in script
        assert len(call.args[1]) <= 150

    @pytest.mark.asyncio
    async def test_preview_action_uses_transient_layer_without_touching_status_chip(self) -> None:
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.claim_started()
        page.evaluate.reset_mock()

        with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock):
            label = await controller.preview_action("double_click", x=100, y=200, num_clicks=2)

        assert label == "Clicking"
        assert controller._current_status == "Analyzing"
        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        assert any("__n1TransientRoot" in script and "visibility = 'visible'" in script for script in scripts)
        assert any("__n1Cursor" in script and "100px" in script and "200px" in script for script in scripts)
        assert any("n1click" in script for script in scripts)
        assert not any("__n1StatusChip" in script and "Clicking" in script for script in scripts)


class TestOverlayPreviewAction:
    @pytest.mark.asyncio
    async def test_click_effect_uses_coordinates(self) -> None:
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.claim_started()
        page.evaluate.reset_mock()

        with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock):
            label = await controller.preview_action("double_click", x=100, y=200, num_clicks=2)

        assert label == "Clicking"
        # preview_action does not update persistent status
        assert controller._current_status == "Analyzing"
        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        # Cursor moved first
        assert any("__n1Cursor" in script and "100px" in script and "200px" in script for script in scripts)
        # Transient root made visible
        assert any("__n1TransientRoot" in script and "visibility = 'visible'" in script for script in scripts)
        # Click effect with num_clicks
        assert any("const numClicks = 2" in script or "< 2" in script for script in scripts)
        # Click keyframe: simple expansion
        assert any(
            "n1click" in script
            and "width:5px;height:5px;opacity:0.6" in script
            and "width:30px;height:30px;opacity:0" in script
            for script in scripts
        )
        # Coordinates used in effect
        assert any("left:100px" in script and "top:200px" in script for script in scripts)

    @pytest.mark.asyncio
    async def test_scroll_effect_uses_spinning_dots(self) -> None:
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.claim_started()
        page.evaluate.reset_mock()

        with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock):
            label = await controller.preview_action("scroll", x=640, y=400, direction="down")

        assert label == "Scrolling"
        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        assert any("__n1ScrollStyle" in script for script in scripts)
        # Scroll uses n1scroll keyframe with spinning dots
        assert any(
            "n1scroll" in script
            and "rotate(0deg)" in script
            and "rotate(360deg)" in script
            for script in scripts
        )
        # Coordinates used
        assert any("left:640px" in script and "top:400px" in script for script in scripts)

    @pytest.mark.asyncio
    async def test_type_effect_uses_caret_and_dots(self) -> None:
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page(focused_center={"x": 200, "y": 150})
        controller = OverlayController(page)

        await controller.claim_started()
        page.evaluate.reset_mock()

        with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock):
            label = await controller.preview_action("type")

        assert label == "Typing"
        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        assert any("document.activeElement" in script for script in scripts)
        assert any("__n1TypeStyle" in script for script in scripts)
        # Type effect uses caret + dots keyframes
        assert any("n1tcaret" in script and "n1tdot" in script and "n1tfade" in script for script in scripts)
        # Cursor moved to focused element center
        assert any("__n1Cursor" in script and "200px" in script and "150px" in script for script in scripts)

    @pytest.mark.asyncio
    async def test_type_effect_skipped_when_no_focused_element(self) -> None:
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page(focused_center=None)
        controller = OverlayController(page)

        await controller.claim_started()
        page.evaluate.reset_mock()

        with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock):
            label = await controller.preview_action("type")

        assert label == "Typing"
        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        assert not any("__n1TypeStyle" in script for script in scripts)

    @pytest.mark.asyncio
    async def test_hover_action_moves_cursor(self) -> None:
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.claim_started()
        page.evaluate.reset_mock()

        with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock):
            label = await controller.preview_action("hover", x=300, y=400)

        assert label == "Hovering"
        # preview_action does not update persistent status
        assert controller._current_status == "Analyzing"
        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        # Cursor moved to hover target
        assert any("__n1Cursor" in script and "300px" in script and "400px" in script for script in scripts)
        # No chip update from preview_action
        assert not any("__n1StatusChip" in script and "Hovering" in script for script in scripts)

    @pytest.mark.asyncio
    async def test_drag_action_shows_trail_and_moves_cursor(self) -> None:
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.claim_started()
        page.evaluate.reset_mock()

        with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock):
            label = await controller.preview_action("drag", x=500, y=600, start_x=100, start_y=200)

        assert label == "Dragging"
        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        # Cursor moved to start point first
        assert any("__n1Cursor" in script and "100px" in script and "200px" in script for script in scripts)
        # Drag style injected with trail keyframes
        assert any("__n1DragStyle" in script for script in scripts)
        assert any("n1dfade" in script and "n1dtrail" in script for script in scripts)
        # Start coordinates used for pressed indicator
        assert any("left:100px" in script and "top:200px" in script for script in scripts)
        # Cursor stays at start point — the real drag moves the page element
        cursor_scripts = [s for s in scripts if "__n1Cursor" in s]
        assert not any("500px" in s and "600px" in s for s in cursor_scripts)

    @pytest.mark.asyncio
    async def test_unknown_action_returns_none(self) -> None:
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.claim_started()
        page.evaluate.reset_mock()

        with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock):
            label = await controller.preview_action("unknown_action_xyz", x=10, y=20)

        assert label is None
        # Cursor still moves for unknown actions (since x/y are provided)
        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        cursor_moves = [s for s in scripts if "__n1Cursor" in s]
        assert len(cursor_moves) >= 1

    @pytest.mark.asyncio
    async def test_preview_action_sleeps_for_cursor_transition(self) -> None:
        from frontend_visualqa.overlay import CURSOR_TRANSITION_MS, OverlayController

        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.claim_started()
        page.evaluate.reset_mock()

        with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await controller.preview_action("left_click", x=50, y=60)

        mock_sleep.assert_awaited_once_with(CURSOR_TRANSITION_MS / 1000)


class TestOverlayScreenshotBoundary:
    @pytest.mark.asyncio
    async def test_before_screenshot_hides_both_roots(self) -> None:
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.claim_started()
        page.evaluate.reset_mock()

        await controller.before_screenshot()

        script = str(page.evaluate.call_args.args[0])
        assert "__n1PersistentRoot" in script
        assert "__n1TransientRoot" in script
        assert "visibility = 'hidden'" in script
        assert "opacity = '0'" in script

    @pytest.mark.asyncio
    async def test_after_screenshot_restores_persistent_root_only(self) -> None:
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.claim_started()
        page.evaluate.reset_mock()

        await controller.after_screenshot()

        script = str(page.evaluate.call_args.args[0])
        assert "__n1PersistentRoot" in script
        assert "__n1TransientRoot" not in script
        assert "visibility = 'visible'" in script
        assert "opacity = '1'" in script

    @pytest.mark.asyncio
    async def test_follow_up_effect_restores_transient_root_visibility_after_screenshot(self) -> None:
        """Transient root stays hidden after capture and is re-shown when the next preview starts."""
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.claim_started()
        await controller.before_screenshot()
        await controller.after_screenshot()
        page.evaluate.reset_mock()

        with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock):
            await controller.preview_action("left_click", x=100, y=200)

        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        assert any(
            "__n1TransientRoot" in script and "visibility = 'visible'" in script
            for script in scripts
        ), "Transient root visibility must be restored when starting the next preview"

    @pytest.mark.asyncio
    async def test_after_screenshot_is_noop_when_inactive(self) -> None:
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.after_screenshot()

        page.evaluate.assert_not_called()


class TestOverlayBestEffort:
    @pytest.mark.asyncio
    async def test_overlay_methods_swallow_evaluate_failures(self) -> None:
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page(evaluate_side_effect=RuntimeError("page crashed"))
        controller = OverlayController(page)

        await controller.claim_started()
        await controller.set_status("Analyzing")
        with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock):
            await controller.preview_action("left_click", x=100, y=100)
            await controller.preview_action("scroll", x=100, y=100, direction="down")
            await controller.preview_action("type")
            await controller.preview_action("hover", x=10, y=20)
            await controller.preview_action("drag", x=200, y=200, start_x=100, start_y=100)
        await controller.before_screenshot()
        await controller.after_screenshot()
        await controller.claim_ended()
