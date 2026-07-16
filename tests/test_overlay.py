"""Tests for the headed-mode overlay controller."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from frontend_visualqa.overlay import CURSOR_TRANSITION_MS, OverlayController


def _make_mock_page(
    *,
    focused_center: dict[str, int] | None = None,
    evaluate_side_effect: Exception | None = None,
) -> MagicMock:
    page = MagicMock()
    # Track whether a thought card is currently mounted so the card builder can
    # report hadExisting (the real DOM signal show_thought reads to time its
    # collapse+expand settle) instead of a constant.
    dom = {"thought_card": False}

    async def evaluate(script: str, *args: object) -> object:
        del args
        if evaluate_side_effect is not None:
            raise evaluate_side_effect
        script_text = str(script)
        if "document.activeElement" in script_text:
            return focused_center
        if "n1renderMarkdown" in script_text:  # thought card builder
            had_existing = dom["thought_card"]
            dom["thought_card"] = True
            return had_existing
        if "__n1ThoughtCard" in script_text:  # clear_thought removes the card
            dom["thought_card"] = False
            return None
        return None

    page.evaluate = AsyncMock(side_effect=evaluate)
    return page


def _evaluated_scripts(page: MagicMock) -> list[str]:
    """Return the script text of every page.evaluate() call made so far."""
    return [str(call.args[0]) for call in page.evaluate.call_args_list]


def _last_evaluated_script(page: MagicMock) -> str:
    """Return the script text of the most recent page.evaluate() call."""
    return str(page.evaluate.call_args.args[0])


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


@pytest.fixture
def patched_sleep() -> Iterator[AsyncMock]:
    """Patch out ``overlay.asyncio.sleep`` so preview_action's real-time holds don't slow tests.

    13 test bodies each wrapped their `preview_action`/`_move_cursor` calls in an identical
    ``with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock):`` block,
    one of which also captured the mock to assert a sleep duration. This is the shared fixture
    they request instead. (``test_teleport_when_offscreen_transition_when_onscreen`` keeps its
    own two local patches since it needs two independent mock instances, one per branch.)
    """
    with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        yield mock_sleep


class TestOverlayControllerLifecycle:
    @pytest.mark.asyncio
    async def test_claim_started_injects_persistent_and_transient_roots(self) -> None:
        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.claim_started()

        assert controller._active is True
        assert controller._current_status == "Analyzing"
        scripts = _evaluated_scripts(page)
        assert any("__n1PersistentRoot" in script for script in scripts)
        assert any("__n1TransientRoot" in script for script in scripts)

    @pytest.mark.asyncio
    async def test_claim_ended_removes_all_roots_and_is_idempotent(self) -> None:
        page, controller = await _started_controller()

        await controller.claim_ended()

        assert controller._active is False
        scripts = _evaluated_scripts(page)
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
        script = _last_evaluated_script(page)
        assert "__n1PersistentRoot" in script


class TestOverlayCursor:
    @pytest.mark.asyncio
    async def test_persistent_root_creates_cursor_img(self) -> None:
        # The cursor lives in the persistent root so it survives navigations
        # and screenshot hide/restore cycles. See OverlayController._restore_cursor_position.
        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.claim_started()

        scripts = _evaluated_scripts(page)
        assert any("__n1PersistentRoot" in script and "__n1Cursor" in script and "createElement('img')" in script for script in scripts)

    @pytest.mark.asyncio
    async def test_move_cursor_updates_position(self) -> None:
        page = _make_mock_page()
        controller = OverlayController(page)
        controller._active = True

        await controller._move_cursor(150, 250)

        script = _last_evaluated_script(page)
        assert "__n1Cursor" in script
        assert "150px" in script
        assert "250px" in script

    @pytest.mark.asyncio
    async def test_move_cursor_reflows_thought_pill_for_new_position(self) -> None:
        # The pill's left/right flip is chosen at show_thought time from the
        # previous cursor position; _move_cursor must recompute it for the new
        # position so the capsule can't run off-screen after the cursor moves.
        page = _make_mock_page()
        controller = OverlayController(page)
        controller._active = True

        await controller._move_cursor(1200, 250)

        script = _last_evaluated_script(page)
        assert "__n1ThoughtCard" in script
        assert "goLeft" in script
        assert "innerWidth" in script

    @pytest.mark.asyncio
    async def test_reinject_after_navigation_replays_active_thought(self) -> None:
        # Reasoning is shown before the action; a navigation rebuilds the overlay
        # DOM and must replay the current thought so it doesn't vanish for the
        # rest of the turn. "n1renderMarkdown" is unique to the show_thought card.
        page = _make_mock_page()
        controller = OverlayController(page)
        controller._active = True
        controller._thought_text = "Now let me click the Login button."
        page.evaluate.reset_mock()

        await controller._reinject_after_navigation()

        scripts = _evaluated_scripts(page)
        assert any("n1renderMarkdown" in script for script in scripts)

    @pytest.mark.asyncio
    async def test_reinject_after_navigation_shows_no_thought_when_none(self) -> None:
        page = _make_mock_page()
        controller = OverlayController(page)
        controller._active = True
        controller._thought_text = None
        page.evaluate.reset_mock()

        await controller._reinject_after_navigation()

        scripts = _evaluated_scripts(page)
        assert not any("n1renderMarkdown" in script for script in scripts)


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
        assert "maxHeight" in script
        arg = call.args[1]
        assert isinstance(arg, dict)
        assert len(arg["text"]) <= 520

    @pytest.mark.asyncio
    async def test_show_thought_collapses_then_expands_when_replacing(self) -> None:
        # Replacing a visible thought collapses the pill to the 48px badge (B)
        # then expands to fit, so the container shrinks and re-grows on each text
        # change rather than resizing in place (preview_action no longer clears
        # the thought between actions).
        page, controller = await _started_controller()
        await controller.show_thought("First reasoning.")
        await controller.show_thought("Second, different reasoning.")

        script = _last_evaluated_script(page)
        assert "hadExisting" in script
        assert "badge.style.width = B + 'px'" in script      # collapse to the badge
        assert "badge.style.width = width + 'px'" in script  # then expand to fit

    @pytest.mark.asyncio
    async def test_show_thought_applies_vertical_flip_from_cursor_y(self) -> None:
        # The capsule mirrors _move_cursor's vertical flip: near the bottom edge
        # the badge sits above the pointer, so a thought shown before the action
        # (cursor already low) doesn't hang the pill below the viewport.
        page, controller = await _started_controller()
        controller._cursor_x = 400
        controller._cursor_y = 780

        await controller.show_thought("Reasoning near the bottom edge.")

        call = page.evaluate.call_args_list[-1]
        script = str(call.args[0])
        assert "badge.style.top" in script
        assert "-68.5px" in script
        assert call.args[1]["cy"] == 780

    @pytest.mark.asyncio
    async def test_preview_click_holds_until_thought_settles(self, patched_sleep: AsyncMock) -> None:
        # A click after a *replacing* thought is held until the pill finishes its
        # shrink→expand, so a navigating click's reasoning is fully visible on the
        # pre-nav page instead of expanding on the destination. The hold is a
        # sleep ≈ the settle time (collapse + expand).
        page, controller = await _started_controller()
        await controller.show_thought("First reasoning.")
        await controller.show_thought("Second, replacing reasoning.")
        await controller.preview_action("left_click", x=100, y=200)

        sleeps = [call.args[0] for call in patched_sleep.call_args_list if call.args]
        assert any(s >= 0.5 for s in sleeps), f"expected a settle-hold sleep, got {sleeps}"

    @pytest.mark.asyncio
    async def test_preview_action_preserves_existing_thought_card(self, patched_sleep: AsyncMock) -> None:
        # The reasoning capsule is shown synced with its action (by claim_verifier,
        # before the action runs), so preview_action must NOT clear it — it stays
        # through the cursor move/morph and is hidden only by the evidence
        # screenshot. clear_thought's signature is `vp.remove()`.
        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.claim_started()
        await controller.show_thought("Inspect the form before deciding.")
        page.evaluate.reset_mock()

        await controller.preview_action("left_click", x=100, y=200)

        scripts = _evaluated_scripts(page)
        assert not any("vp.remove()" in script for script in scripts)
        assert any(
            "__n1Cursor" in script and "100px" in script and "200px" in script for script in scripts
        )

    @pytest.mark.asyncio
    async def test_set_status_non_analyzing_preserves_existing_thought_card(self) -> None:
        # A status change no longer clears the thought (see set_status): the synced
        # reasoning must survive the status label changing to "Navigating"/"Running …".
        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.claim_started()
        await controller.show_thought("Inspect the form before deciding.")
        page.evaluate.reset_mock()

        await controller.set_status("Navigating")

        scripts = _evaluated_scripts(page)
        assert not any("vp.remove()" in script for script in scripts)
        assert any("__n1PersistentRoot" in script for script in scripts)

    @pytest.mark.asyncio
    async def test_set_status_analyzing_preserves_existing_thought_card(self) -> None:
        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.claim_started()
        await controller.show_thought("Inspect the form before deciding.")
        page.evaluate.reset_mock()

        await controller.set_status("Analyzing")

        scripts = _evaluated_scripts(page)
        assert not any("__n1ThoughtCard" in script and "current.remove()" in script for script in scripts)

    @pytest.mark.asyncio
    async def test_show_result_injects_fullscreen_verdict_card(self) -> None:
        from frontend_visualqa import overlay as overlay_module
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.claim_started()
        page.evaluate.reset_mock()

        await controller.show_result("failed", "The submit button stayed disabled.", claim="Submit is enabled")

        # Full-screen card injected with the failed accent color.
        card_call = next(call for call in page.evaluate.call_args_list if "__n1ResultCard" in str(call.args[0]))
        card_script = str(card_call.args[0])
        assert "position:fixed;inset:0" in card_script
        # The card must be the topmost layer and the cursor hidden, so the last
        # recorded frame is a clean verdict (the cursor sits at Z_INDEX + 2).
        assert f"z-index:{overlay_module.Z_INDEX + 3}" in card_script
        assert overlay_module.CURSOR_ID in card_script and "display = 'none'" in card_script
        arg = card_call.args[1]
        assert arg["status_label"] == "Failed"
        assert arg["accent"] == "#FF5A5F"
        assert arg["finding"] == "The submit button stayed disabled."
        assert arg["claim"] == "Submit is enabled"

    @pytest.mark.asyncio
    async def test_show_result_noop_when_inactive(self) -> None:
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.show_result("passed", "All good.", claim="It works")

        page.evaluate.assert_not_called()

    @pytest.mark.asyncio
    async def test_preview_action_uses_transient_layer_without_touching_status_chip(
        self, patched_sleep: AsyncMock
    ) -> None:
        del patched_sleep  # patched only so preview_action's internal sleep doesn't run for real
        page, controller = await _started_controller()

        await controller.preview_action("double_click", x=100, y=200, num_clicks=2)

        assert controller._current_status == "Analyzing"
        scripts = _evaluated_scripts(page)
        assert any("__n1TransientRoot" in script and "visibility = 'visible'" in script for script in scripts)
        assert any("__n1Cursor" in script and "100px" in script and "200px" in script for script in scripts)
        assert any("n1click" in script for script in scripts)
        assert not any("__n1StatusChip" in script and "Clicking" in script for script in scripts)


class TestOverlayPreviewAction:
    @pytest.mark.asyncio
    async def test_click_effect_uses_coordinates(self, patched_sleep: AsyncMock) -> None:
        del patched_sleep  # patched only so preview_action's internal sleep doesn't run for real
        page, controller = await _started_controller()

        await controller.preview_action("double_click", x=100, y=200, num_clicks=2)

        # preview_action does not update persistent status
        assert controller._current_status == "Analyzing"
        scripts = _evaluated_scripts(page)
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
    async def test_scroll_effect_morphs_badge_to_chevron(self, patched_sleep: AsyncMock) -> None:
        # Scroll now morphs the cursor badge into a chevron glyph (down = no
        # rotation); the separate transient scroll box was retired in the redesign.
        del patched_sleep  # patched only so preview_action's internal sleep doesn't run for real
        page, controller = await _started_controller()

        await controller.preview_action("scroll", x=640, y=400, direction="down")

        scripts = _evaluated_scripts(page)
        # Cursor leads to the scroll target
        assert any("__n1Cursor" in script and "640px" in script and "400px" in script for script in scripts)
        # Badge morphs to the chevron glyph via the bloom-in keyframes
        assert any("__n1BadgeGlyph" in script and "n1badgeIn" in script for script in scripts)
        assert any("6 10 12 16 18 10" in script for script in scripts)

    @pytest.mark.asyncio
    async def test_scroll_effect_rotates_badge_for_up_direction(self, patched_sleep: AsyncMock) -> None:
        del patched_sleep  # patched only so preview_action's internal sleep doesn't run for real
        page, controller = await _started_controller()

        await controller.preview_action("scroll", x=100, y=200, direction="up")

        scripts = _evaluated_scripts(page)
        # Up rotates the chevron glyph 180deg inside the badge.
        assert any("__n1BadgeGlyph" in script and "rotate(180deg)" in script for script in scripts)

    @pytest.mark.asyncio
    async def test_type_morphs_badge_at_focused_element(self, patched_sleep: AsyncMock) -> None:
        del patched_sleep  # patched only so preview_action's internal sleep doesn't run for real
        page, controller = await _started_controller(focused_center={"x": 200, "y": 150})

        await controller.preview_action("type")

        scripts = _evaluated_scripts(page)
        assert any("document.activeElement" in script for script in scripts)
        # Type morphs the badge to the type glyph (the standalone typing pill was retired)
        assert any("__n1BadgeGlyph" in script and "n1badgeIn" in script for script in scripts)
        # Cursor moved to focused element center
        assert any("__n1Cursor" in script and "200px" in script and "150px" in script for script in scripts)

    @pytest.mark.asyncio
    async def test_type_morphs_badge_even_without_focused_element(self, patched_sleep: AsyncMock) -> None:
        del patched_sleep  # patched only so preview_action's internal sleep doesn't run for real
        page, controller = await _started_controller(focused_center=None)

        await controller.preview_action("type")

        scripts = _evaluated_scripts(page)
        # No focused element to move to, but the badge still morphs to the type glyph.
        assert any("__n1BadgeGlyph" in script and "n1badgeIn" in script for script in scripts)
        # The cursor does not relocate when there is no focused-element centre.
        assert not any("cursor.style.left = '" in script for script in scripts)

    @pytest.mark.asyncio
    async def test_hover_action_moves_cursor(self, patched_sleep: AsyncMock) -> None:
        del patched_sleep  # patched only so preview_action's internal sleep doesn't run for real
        page, controller = await _started_controller()

        await controller.preview_action("hover", x=300, y=400)

        # preview_action does not update persistent status
        assert controller._current_status == "Analyzing"
        scripts = _evaluated_scripts(page)
        # Cursor moved to hover target
        assert any("__n1Cursor" in script and "300px" in script and "400px" in script for script in scripts)
        # No chip update from preview_action
        assert not any("__n1StatusChip" in script and "Hovering" in script for script in scripts)

    @pytest.mark.asyncio
    async def test_drag_action_shows_trail_and_moves_cursor(self, patched_sleep: AsyncMock) -> None:
        del patched_sleep  # patched only so preview_action's internal sleep doesn't run for real
        page, controller = await _started_controller()

        await controller.preview_action("drag", x=500, y=600, start_x=100, start_y=200)

        scripts = _evaluated_scripts(page)
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
    async def test_unknown_action_returns_none(self, patched_sleep: AsyncMock) -> None:
        del patched_sleep  # patched only so preview_action's internal sleep doesn't run for real
        page, controller = await _started_controller()

        await controller.preview_action("unknown_action_xyz", x=10, y=20)

        # Cursor still moves for unknown actions (since x/y are provided)
        scripts = _evaluated_scripts(page)
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

        scripts = _evaluated_scripts(page)
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

        script = _last_evaluated_script(page)
        assert "__n1PersistentRoot" in script
        assert "__n1TransientRoot" in script
        assert "visibility = 'hidden'" in script
        assert "opacity = '0'" in script
        # The hide is preceded by an awaited opacity fade that waits on the real
        # transitionend, with a fallback timer so it can't hang (see
        # _FADE_OUT_AND_HIDE_JS). The hard-hide above stays the capture backstop.
        assert "transition = 'opacity" in script
        assert "transitionend" in script
        assert "setTimeout" in script

    @pytest.mark.asyncio
    async def test_after_screenshot_restores_persistent_root_only(self) -> None:
        page, controller = await _started_controller()

        await controller.after_screenshot()

        script = _last_evaluated_script(page)
        assert "__n1PersistentRoot" in script
        assert "__n1TransientRoot" not in script
        assert "visibility = 'visible'" in script
        assert "opacity = '1'" in script
        # Persistent root fades back in (opacity 0→1) rather than snapping.
        assert "transition = 'opacity" in script

    @pytest.mark.asyncio
    async def test_follow_up_effect_restores_transient_root_visibility_after_screenshot(
        self, patched_sleep: AsyncMock
    ) -> None:
        """Transient root stays hidden after capture and is re-shown when the next preview starts."""
        del patched_sleep  # patched only so preview_action's internal sleep doesn't run for real
        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.claim_started()
        await controller.before_screenshot()
        await controller.after_screenshot()
        page.evaluate.reset_mock()

        await controller.preview_action("left_click", x=100, y=200)

        scripts = _evaluated_scripts(page)
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
    async def test_overlay_methods_swallow_evaluate_failures(self, patched_sleep: AsyncMock) -> None:
        del patched_sleep  # patched only so preview_action's internal sleep doesn't run for real
        page = _make_mock_page(evaluate_side_effect=RuntimeError("page crashed"))
        controller = OverlayController(page)

        await controller.claim_started()
        await controller.set_status("Analyzing")
        await controller.show_thought("test thought")
        await controller.clear_thought()
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
