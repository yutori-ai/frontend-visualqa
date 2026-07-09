"""Tests for the headed-mode overlay controller."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from frontend_visualqa.overlay import CURSOR_TRANSITION_MS, OverlayController


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


async def _started_controller(
    *, focused_center: dict[str, int] | None = None
) -> tuple[MagicMock, OverlayController]:
    """Build a page + OverlayController, run claim_started, and clear the call log.

    Most preview/status tests only care about evaluate() calls made by the action
    under test, not the roots/styles claim_started() injects on the way in.
    """
    page = _make_mock_page(focused_center=focused_center)
    controller = OverlayController(page)
    await controller.claim_started()
    page.evaluate.reset_mock()
    return page, controller


class TestOverlayControllerLifecycle:
    @pytest.mark.asyncio
    async def test_claim_started_injects_persistent_and_transient_roots(self) -> None:
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
        page, controller = await _started_controller()

        await controller.claim_ended()

        assert controller._active is False
        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        assert any("__n1PersistentRoot" in script for script in scripts)
        assert any("__n1TransientRoot" in script for script in scripts)
        assert any("__n1ClickStyle" in script for script in scripts)
        assert any("__n1DragStyle" in script for script in scripts)
        assert any("__n1BadgeKf" in script for script in scripts)
        assert any("__n1ThoughtTimer" in script for script in scripts)

        page.evaluate.reset_mock()
        await controller.claim_ended()
        page.evaluate.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_status_updates_label_when_active(self) -> None:
        page, controller = await _started_controller()

        await controller.set_status("Navigating")

        assert controller._current_status == "Navigating"
        script = str(page.evaluate.call_args.args[0])
        assert "__n1StatusChip" in script
        assert "Navigating" in script


class TestOverlayCursor:
    @pytest.mark.asyncio
    async def test_persistent_root_creates_cursor_img(self) -> None:
        # The cursor lives in the persistent root so it survives navigations
        # and screenshot hide/restore cycles. See OverlayController._restore_cursor_position.
        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.claim_started()

        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        assert any("__n1PersistentRoot" in script and "__n1Cursor" in script and "createElement('img')" in script for script in scripts)

    @pytest.mark.asyncio
    async def test_move_cursor_updates_position(self) -> None:
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
        page, controller = await _started_controller()

        text = "Inspect the quota widget before deciding because the bar and label may disagree on completion."
        await controller.show_thought(text)

        call = page.evaluate.call_args_list[-1]
        script = str(call.args[0])
        assert "__n1ThoughtCard" in script
        # The thought is a capsule that stretches the badge and can wrap to two lines.
        assert "badge.style.width" in script
        assert "webkitLineClamp" in script
        arg = call.args[1]
        assert isinstance(arg, dict)
        assert len(arg["text"]) <= 520

    @pytest.mark.asyncio
    async def test_preview_action_clears_existing_thought_card_before_animating(self) -> None:
        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.claim_started()
        await controller.show_thought("Inspect the form before deciding.")
        page.evaluate.reset_mock()

        with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock):
            await controller.preview_action("left_click", x=100, y=200)

        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        clear_index = next(
            index for index, script in enumerate(scripts) if "__n1ThoughtCard" in script and "vp.remove()" in script
        )
        cursor_index = next(
            index for index, script in enumerate(scripts) if "__n1Cursor" in script and "100px" in script and "200px" in script
        )
        assert clear_index < cursor_index

    @pytest.mark.asyncio
    async def test_set_status_non_analyzing_clears_existing_thought_card(self) -> None:
        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.claim_started()
        await controller.show_thought("Inspect the form before deciding.")
        page.evaluate.reset_mock()

        await controller.set_status("Navigating")

        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        assert any("__n1ThoughtCard" in script and "vp.remove()" in script for script in scripts)
        assert any("__n1StatusChip" in script and "Navigating" in script for script in scripts)

    @pytest.mark.asyncio
    async def test_set_status_analyzing_preserves_existing_thought_card(self) -> None:
        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.claim_started()
        await controller.show_thought("Inspect the form before deciding.")
        page.evaluate.reset_mock()

        await controller.set_status("Analyzing")

        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        assert not any("__n1ThoughtCard" in script and "current.remove()" in script for script in scripts)

    @pytest.mark.asyncio
    async def test_preview_action_uses_transient_layer_without_touching_status_chip(self) -> None:
        page, controller = await _started_controller()

        with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock):
            await controller.preview_action("double_click", x=100, y=200, num_clicks=2)

        assert controller._current_status == "Analyzing"
        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        assert any("__n1TransientRoot" in script and "visibility = 'visible'" in script for script in scripts)
        assert any("__n1Cursor" in script and "100px" in script and "200px" in script for script in scripts)
        assert any("n1click" in script for script in scripts)
        assert not any("__n1StatusChip" in script and "Clicking" in script for script in scripts)


class TestOverlayPreviewAction:
    @pytest.mark.asyncio
    async def test_click_effect_uses_coordinates(self) -> None:
        page, controller = await _started_controller()

        with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock):
            await controller.preview_action("double_click", x=100, y=200, num_clicks=2)

        # preview_action does not update persistent status
        assert controller._current_status == "Analyzing"
        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        # Cursor moved first
        assert any("__n1Cursor" in script and "100px" in script and "200px" in script for script in scripts)
        # Transient root made visible
        assert any("__n1TransientRoot" in script and "visibility = 'visible'" in script for script in scripts)
        # Ripple loop runs num_clicks times
        assert any("i < 2" in script for script in scripts)
        # Refreshed click ripple: an expanding ring plus a shrinking centre dot
        assert any("__n1ClickStyle" in script for script in scripts)
        assert any("n1clickring" in script and "n1clickdot" in script for script in scripts)
        # Coordinates used in effect
        assert any("left:100px" in script and "top:200px" in script for script in scripts)

    @pytest.mark.asyncio
    async def test_scroll_effect_morphs_badge_to_chevron(self) -> None:
        # Scroll now morphs the cursor badge into a chevron glyph (down = no
        # rotation); the separate transient scroll box was retired in the redesign.
        page, controller = await _started_controller()

        with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock):
            await controller.preview_action("scroll", x=640, y=400, direction="down")

        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        # Cursor leads to the scroll target
        assert any("__n1Cursor" in script and "640px" in script and "400px" in script for script in scripts)
        # Badge morphs to the chevron glyph via the bloom-in keyframes
        assert any("__n1BadgeGlyph" in script and "n1badgeIn" in script for script in scripts)
        assert any("6 10 12 16 18 10" in script for script in scripts)

    @pytest.mark.asyncio
    async def test_scroll_effect_rotates_badge_for_up_direction(self) -> None:
        page, controller = await _started_controller()

        with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock):
            await controller.preview_action("scroll", x=100, y=200, direction="up")

        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        # Up rotates the chevron glyph 180deg inside the badge.
        assert any("__n1BadgeGlyph" in script and "rotate(180deg)" in script for script in scripts)

    @pytest.mark.asyncio
    async def test_type_morphs_badge_at_focused_element(self) -> None:
        page, controller = await _started_controller(focused_center={"x": 200, "y": 150})

        with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock):
            await controller.preview_action("type")

        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        assert any("document.activeElement" in script for script in scripts)
        # Type morphs the badge to the type glyph (the standalone typing pill was retired)
        assert any("__n1BadgeGlyph" in script and "n1badgeIn" in script for script in scripts)
        # Cursor moved to focused element center
        assert any("__n1Cursor" in script and "200px" in script and "150px" in script for script in scripts)

    @pytest.mark.asyncio
    async def test_type_morphs_badge_even_without_focused_element(self) -> None:
        page, controller = await _started_controller(focused_center=None)

        with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock):
            await controller.preview_action("type")

        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        # No focused element to move to, but the badge still morphs to the type glyph.
        assert any("__n1BadgeGlyph" in script and "n1badgeIn" in script for script in scripts)
        # The cursor does not relocate when there is no focused-element centre.
        assert not any("cursor.style.left = '" in script for script in scripts)

    @pytest.mark.asyncio
    async def test_hover_action_moves_cursor(self) -> None:
        page, controller = await _started_controller()

        with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock):
            await controller.preview_action("hover", x=300, y=400)

        # preview_action does not update persistent status
        assert controller._current_status == "Analyzing"
        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        # Cursor moved to hover target
        assert any("__n1Cursor" in script and "300px" in script and "400px" in script for script in scripts)
        # No chip update from preview_action
        assert not any("__n1StatusChip" in script and "Hovering" in script for script in scripts)

    @pytest.mark.asyncio
    async def test_drag_action_shows_trail_and_moves_cursor(self) -> None:
        page, controller = await _started_controller()

        with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock):
            await controller.preview_action("drag", x=500, y=600, start_x=100, start_y=200)

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
        page, controller = await _started_controller()

        with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock):
            await controller.preview_action("unknown_action_xyz", x=10, y=20)

        # Cursor still moves for unknown actions (since x/y are provided)
        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        cursor_moves = [s for s in scripts if "__n1Cursor" in s]
        assert len(cursor_moves) >= 1

    @pytest.mark.asyncio
    async def test_teleport_when_offscreen_transition_when_onscreen(self) -> None:
        """Teleport (short sleep) when cursor is off-screen, full transition otherwise."""
        page = _make_mock_page()
        controller = OverlayController(page)
        await controller.claim_started()

        # _move_cursor returns True (off-screen → teleported) → short sleep
        with patch.object(controller, "_move_cursor", new_callable=AsyncMock, return_value=True):
            with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                await controller.preview_action("left_click", x=50, y=60)
        mock_sleep.assert_awaited_once_with(0.05)

        # _move_cursor returns False (on-screen → transitioned) → full sleep
        with patch.object(controller, "_move_cursor", new_callable=AsyncMock, return_value=False):
            with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                await controller.preview_action("left_click", x=100, y=120)
        mock_sleep.assert_awaited_once_with(CURSOR_TRANSITION_MS / 1000)

    @pytest.mark.asyncio
    async def test_move_cursor_js_contains_teleport_logic(self) -> None:
        """_move_cursor JS detects off-screen via left==='-200px' and branches."""
        page, controller = await _started_controller()

        await controller._move_cursor(50, 60)

        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        cursor_scripts = [s for s in scripts if "offScreen" in s]
        assert len(cursor_scripts) == 1
        js = cursor_scripts[0]
        # Off-screen detection: checks the initial -200px position
        assert "cursor.style.left === '-200px'" in js
        # Teleport path: disables transition, sets position, forces reflow, re-enables
        assert "cursor.style.transition = 'none'" in js
        assert "cursor.offsetHeight" in js
        assert f"left {CURSOR_TRANSITION_MS}ms ease-in-out" in js
        # Both code paths return a boolean
        assert "return true" in js
        assert "return false" in js


class TestOverlayScreenshotBoundary:
    @pytest.mark.asyncio
    async def test_before_screenshot_hides_both_roots(self) -> None:
        page, controller = await _started_controller()

        await controller.before_screenshot()

        script = str(page.evaluate.call_args.args[0])
        assert "__n1PersistentRoot" in script
        assert "__n1TransientRoot" in script
        assert "visibility = 'hidden'" in script
        assert "opacity = '0'" in script

    @pytest.mark.asyncio
    async def test_after_screenshot_restores_persistent_root_only(self) -> None:
        page, controller = await _started_controller()

        await controller.after_screenshot()

        script = str(page.evaluate.call_args.args[0])
        assert "__n1PersistentRoot" in script
        assert "__n1TransientRoot" not in script
        assert "visibility = 'visible'" in script
        assert "opacity = '1'" in script

    @pytest.mark.asyncio
    async def test_follow_up_effect_restores_transient_root_visibility_after_screenshot(self) -> None:
        """Transient root stays hidden after capture and is re-shown when the next preview starts."""
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
    async def test_clear_thought_is_noop_when_inactive(self) -> None:
        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.clear_thought()

        page.evaluate.assert_not_called()

    @pytest.mark.asyncio
    async def test_after_screenshot_is_noop_when_inactive(self) -> None:
        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.after_screenshot()

        page.evaluate.assert_not_called()


class TestOverlayBestEffort:
    @pytest.mark.asyncio
    async def test_overlay_methods_swallow_evaluate_failures(self) -> None:
        page = _make_mock_page(evaluate_side_effect=RuntimeError("page crashed"))
        controller = OverlayController(page)

        await controller.claim_started()
        await controller.set_status("Analyzing")
        await controller.show_thought("test thought")
        await controller.clear_thought()
        with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock):
            await controller.preview_action("left_click", x=100, y=100)
            await controller.preview_action("scroll", x=100, y=100, direction="down")
            await controller.preview_action("type")
            await controller.preview_action("hover", x=10, y=20)
            await controller.preview_action("drag", x=200, y=200, start_x=100, start_y=100)
        await controller.before_screenshot()
        await controller.after_screenshot()
        await controller.claim_ended()


@pytest.mark.asyncio
async def test_show_thought_preserves_markdown_line_structure() -> None:
    page, controller = await _started_controller()

    text = "# Plan\n- check the bar fill\n- compare against the label"
    await controller.show_thought(text)

    call = page.evaluate.call_args_list[-1]
    arg = call.args[1]
    # Newlines must survive clipping: n1renderMarkdown's headers and lists are
    # line-anchored and would never render after a whitespace-collapsing clip.
    assert arg["text"] == text
