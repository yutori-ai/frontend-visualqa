"""Tests for the FVQA adapter to the shared Navigator overlay runtime."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from yutori_navigator_overlay_runtime import PROTOCOL_VERSION, verify_iife

import frontend_visualqa.overlay as overlay_module
from frontend_visualqa.overlay import (
    CURSOR_TRANSITION_MS,
    PERSISTENT_ROOT_ID,
    TRANSIENT_ROOT_ID,
    OverlayController,
)


def _default_snapshot() -> dict[str, object]:
    return {
        "cursor": {"x": 640, "y": 400},
        "thought": None,
        "badge": {"type": "loop"},
        "hidden": False,
    }


def _make_mock_page(
    *,
    focused_center: dict[str, int] | None = None,
    evaluate_side_effect: Exception | None = None,
    emergency_hide_success: bool = True,
    before_screenshot_success: bool = True,
    failed_operations: set[str] | None = None,
) -> MagicMock:
    page = MagicMock()
    page.viewport_size = {"width": 1280, "height": 800}
    page.runtime_installed = False
    page.snapshot = _default_snapshot()
    page.operations = []
    page.iife_evaluations = 0
    page.emergency_hide_present = False

    async def evaluate(script: str, *args: object) -> object:
        if evaluate_side_effect is not None:
            raise evaluate_side_effect
        if script == overlay_module._IIFE_SOURCE:
            page.runtime_installed = True
            page.iife_evaluations += 1
            return None
        if script == overlay_module._FOCUSED_ELEMENT_CENTER_JS:
            return focused_center
        if script == overlay_module._EMERGENCY_HIDE_JS:
            page.emergency_hide_present = True
            if emergency_hide_success:
                page.snapshot["hidden"] = True
            return emergency_hide_success
        if script == overlay_module._EMERGENCY_RESTORE_JS:
            page.emergency_hide_present = False
            return True
        if script != overlay_module._APPLY_OPERATION_JS:
            return None

        operation = deepcopy(args[0])
        if not page.runtime_installed:
            return None
        page.operations.append(operation)
        op = operation["op"]
        if failed_operations and op in failed_operations:
            return None
        if op == "restore":
            page.snapshot = deepcopy(operation["snapshot"])
        elif op == "moveCursor":
            point = operation["point"]
            page.snapshot["cursor"] = {
                "x": max(8, min(point["x"], 1272)),
                "y": max(8, min(point["y"], 792)),
            }
        elif op == "showThought":
            page.snapshot["thought"] = operation["markdown"]
        elif op == "clearThought":
            page.snapshot["thought"] = None
        elif op == "previewAction":
            page.snapshot["badge"] = deepcopy(operation["presentation"]["badge"])
        elif op == "beforeScreenshot":
            if not before_screenshot_success:
                return {
                    "ok": False,
                    "protocolVersion": PROTOCOL_VERSION,
                    "snapshot": deepcopy(page.snapshot),
                    "error": "capture-hide-failed",
                }
            page.snapshot["hidden"] = True
        elif op == "afterScreenshot":
            page.snapshot["hidden"] = False
        elif op == "destroy":
            page.runtime_installed = False
            page.snapshot = None

        return {
            "ok": True,
            "protocolVersion": PROTOCOL_VERSION,
            "snapshot": deepcopy(page.snapshot),
        }

    page.evaluate = AsyncMock(side_effect=evaluate)
    return page


async def _started_controller(**page_options: object) -> tuple[MagicMock, OverlayController]:
    page = _make_mock_page(**page_options)
    controller = OverlayController(page)
    await controller.claim_started()
    page.evaluate.reset_mock()
    return page, controller


def _operation(page: MagicMock, name: str) -> dict[str, object]:
    return next(operation for operation in page.operations if operation["op"] == name)


def test_wheel_artifact_passes_integrity_check_and_owns_both_roots() -> None:
    assert verify_iife() is True
    assert PERSISTENT_ROOT_ID in overlay_module._IIFE_SOURCE
    assert TRANSIENT_ROOT_ID in overlay_module._IIFE_SOURCE


def test_emergency_hide_targets_runtime_owned_roots_including_collision_suffixes() -> None:
    assert "data-yutori-navigator-overlay-root" in overlay_module._EMERGENCY_HIDE_JS
    assert "data-yutori-navigator-overlay-owned" in overlay_module._EMERGENCY_HIDE_JS
    assert "getElementById" not in overlay_module._EMERGENCY_HIDE_JS


def test_emergency_hide_uses_an_owned_stylesheet_with_a_matching_restore() -> None:
    assert overlay_module._EMERGENCY_HIDE_STYLE_ID in overlay_module._EMERGENCY_HIDE_JS
    assert "data-yutori-navigator-overlay-emergency-hide" in overlay_module._EMERGENCY_HIDE_JS
    assert "data-yutori-navigator-overlay-emergency-hide" in overlay_module._EMERGENCY_RESTORE_JS
    assert "style.remove()" in overlay_module._EMERGENCY_RESTORE_JS


@pytest.mark.asyncio
async def test_claim_started_binds_navigation_without_mounting_idle_overlay() -> None:
    page = _make_mock_page()
    controller = OverlayController(page)

    await controller.claim_started()

    assert controller._active is True
    page.on.assert_called_once_with("domcontentloaded", controller._navigation_handler)
    page.evaluate.assert_not_called()


@pytest.mark.asyncio
async def test_claim_ended_destroys_runtime_and_detaches_listener() -> None:
    page, controller = await _started_controller()
    await controller.show_thought("Inspect the form.")
    page.operations.clear()

    await controller.claim_ended()

    assert controller._active is False
    assert page.operations == [{"op": "destroy"}]
    page.remove_listener.assert_called_once_with("domcontentloaded", controller._on_navigation)


@pytest.mark.asyncio
async def test_first_thought_installs_wheel_runtime_and_reuses_it() -> None:
    page, controller = await _started_controller()

    await controller.show_thought("Inspect the form.")
    await controller.show_thought("Then compare the result.")

    assert page.iife_evaluations == 1
    assert [operation["op"] for operation in page.operations] == [
        "restore",
        "showThought",
        "showThought",
    ]
    assert controller._thought_text == "Then compare the result."


@pytest.mark.asyncio
async def test_partial_runtime_install_still_enables_screenshot_hide() -> None:
    page, controller = await _started_controller(failed_operations={"showThought"})

    await controller.show_thought("Inspect the form.")
    await controller.before_screenshot()

    assert controller._activated is True
    assert _operation(page, "beforeScreenshot") == {"op": "beforeScreenshot"}
    assert controller._hidden is True


@pytest.mark.asyncio
async def test_navigation_reinstalls_and_restores_cursor_thought_and_badge() -> None:
    page, controller = await _started_controller()
    await controller.show_thought("Open the detail page.")
    with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock):
        await controller.preview_action("scroll", x=900, y=700, direction="up")
    page.runtime_installed = False
    page.operations.clear()

    await controller._restore_after_navigation()

    assert page.iife_evaluations == 2
    restore = _operation(page, "restore")
    snapshot = restore["snapshot"]
    assert snapshot["cursor"] == {"x": 900, "y": 700}
    assert snapshot["thought"] == "Open the detail page."
    assert snapshot["badge"] == {
        "type": "glyph",
        "glyph": "scroll",
        "rotationDegrees": 180,
    }


@pytest.mark.asyncio
async def test_click_glides_then_presents_shared_click_effect() -> None:
    page, controller = await _started_controller()
    await controller.show_thought("Open the matching row.")
    page.operations.clear()

    with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock) as sleep:
        await controller.preview_action("double_click", x=100, y=200, num_clicks=2)

    assert [operation["op"] for operation in page.operations] == [
        "moveCursor",
        "previewAction",
    ]
    sleep.assert_awaited_once_with(CURSOR_TRANSITION_MS / 1000)
    presentation = _operation(page, "previewAction")["presentation"]
    assert presentation["badge"] == {"type": "loop"}
    assert presentation["transientEffects"][0] == {
        "id": "frontend-visualqa-click-1",
        "type": "click",
        "point": {"x": 100, "y": 200},
        "clicks": 2,
        "startedAtMs": presentation["transientEffects"][0]["startedAtMs"],
        "durationMs": 250,
    }


@pytest.mark.asyncio
async def test_first_action_mounts_at_target_without_artificial_glide_wait() -> None:
    page, controller = await _started_controller()

    with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock) as sleep:
        await controller.preview_action("hover", x=320, y=240)

    assert page.iife_evaluations == 1
    assert page.operations[0] == {
        "op": "restore",
        "snapshot": {
            "cursor": {"x": 320, "y": 240},
            "thought": None,
            "badge": {"type": "loop"},
            "hidden": False,
        },
    }
    sleep.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("direction", "expected_badge"),
    [
        ("down", {"type": "glyph", "glyph": "scroll"}),
        ("up", {"type": "glyph", "glyph": "scroll", "rotationDegrees": 180}),
        ("right", {"type": "glyph", "glyph": "scroll", "rotationDegrees": -90}),
        ("left", {"type": "glyph", "glyph": "scroll", "rotationDegrees": 90}),
    ],
)
async def test_scroll_maps_direction_to_shared_badge(
    direction: str,
    expected_badge: dict[str, object],
) -> None:
    page, controller = await _started_controller()

    with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock):
        await controller.preview_action("scroll", x=640, y=400, direction=direction)

    presentation = _operation(page, "previewAction")["presentation"]
    assert presentation == {"badge": expected_badge, "transientEffects": []}


@pytest.mark.asyncio
async def test_type_moves_to_focused_element_and_uses_shared_type_badge() -> None:
    page, controller = await _started_controller(focused_center={"x": 200, "y": 150})

    with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock):
        await controller.preview_action("type")

    move = _operation(page, "moveCursor")
    assert move["point"] == {"x": 200, "y": 150}
    presentation = _operation(page, "previewAction")["presentation"]
    assert presentation == {
        "badge": {"type": "glyph", "glyph": "type"},
        "transientEffects": [],
    }


@pytest.mark.asyncio
async def test_drag_keeps_cursor_at_start_and_uses_shared_drag_trail() -> None:
    page, controller = await _started_controller()

    with patch("frontend_visualqa.overlay.asyncio.sleep", new_callable=AsyncMock):
        await controller.preview_action("drag", start_x=100, start_y=200, x=500, y=600)

    assert _operation(page, "moveCursor")["point"] == {"x": 100, "y": 200}
    presentation = _operation(page, "previewAction")["presentation"]
    assert presentation["badge"] == {"type": "loop"}
    effect = presentation["transientEffects"][0]
    assert effect["type"] == "drag-trail"
    assert effect["from"] == {"x": 100, "y": 200}
    assert effect["to"] == {"x": 500, "y": 600}
    assert effect["durationMs"] == 200


@pytest.mark.asyncio
async def test_cursor_uses_runtime_clamped_position_for_navigation_restore() -> None:
    _, controller = await _started_controller()

    await controller._move_cursor(5000, -100)

    assert controller._cursor == {"x": 1272, "y": 8}


@pytest.mark.asyncio
async def test_status_is_host_state_only_and_does_not_mutate_overlay() -> None:
    page, controller = await _started_controller()

    await controller.set_status("Navigating")

    assert controller._current_status == "Navigating"
    page.evaluate.assert_not_called()


@pytest.mark.asyncio
async def test_before_screenshot_receives_hidden_acknowledgement() -> None:
    page, controller = await _started_controller()
    await controller.show_thought("Inspect the result.")
    page.operations.clear()

    await controller.before_screenshot()

    assert page.operations == [{"op": "beforeScreenshot"}]
    assert controller._hidden is True


@pytest.mark.asyncio
async def test_navigation_queued_during_screenshot_hide_restores_hidden_snapshot() -> None:
    _, controller = await _started_controller()
    controller._activated = True
    hide_started = asyncio.Event()
    finish_hide = asyncio.Event()
    operations: list[dict[str, object]] = []

    async def apply_now(
        operation: dict[str, object],
        *,
        bootstrap_snapshot: dict[str, object],
    ) -> dict[str, object]:
        del bootstrap_snapshot
        operations.append(deepcopy(operation))
        if operation["op"] == "beforeScreenshot":
            hide_started.set()
            await finish_hide.wait()
            snapshot = controller._snapshot()
            snapshot["hidden"] = True
        else:
            snapshot = deepcopy(operation["snapshot"])
        return {
            "installed": False,
            "result": {
                "ok": True,
                "protocolVersion": PROTOCOL_VERSION,
                "snapshot": snapshot,
            },
        }

    controller._apply_operation_now = AsyncMock(side_effect=apply_now)
    hide_task = asyncio.create_task(controller.before_screenshot())
    await hide_started.wait()
    navigation_task = asyncio.create_task(controller._restore_after_navigation())
    await asyncio.sleep(0)
    finish_hide.set()
    await asyncio.gather(hide_task, navigation_task)

    assert operations[1]["op"] == "restore"
    assert operations[1]["snapshot"]["hidden"] is True


@pytest.mark.asyncio
async def test_before_screenshot_uses_emergency_hide_after_runtime_failure() -> None:
    page, controller = await _started_controller(before_screenshot_success=False)
    await controller.show_thought("Inspect the result.")
    page.evaluate.reset_mock()

    await controller.before_screenshot()

    assert controller._hidden is True
    assert controller._emergency_hidden is True
    assert any(call.args[0] == overlay_module._EMERGENCY_HIDE_JS for call in page.evaluate.call_args_list)


@pytest.mark.asyncio
async def test_after_screenshot_clears_emergency_hide_after_runtime_restore() -> None:
    page, controller = await _started_controller(before_screenshot_success=False)
    await controller.show_thought("Inspect the result.")
    await controller.before_screenshot()
    page.evaluate.reset_mock()

    await controller.after_screenshot()

    evaluated_scripts = [call.args[0] for call in page.evaluate.call_args_list]
    assert evaluated_scripts[-1] == overlay_module._EMERGENCY_RESTORE_JS
    assert controller._emergency_hidden is False


@pytest.mark.asyncio
async def test_claim_ended_clears_emergency_hide_when_controller_is_inactive() -> None:
    page, controller = await _started_controller()
    controller._active = False
    controller._emergency_hidden = True

    await controller.claim_ended()

    page.evaluate.assert_awaited_once_with(overlay_module._EMERGENCY_RESTORE_JS)
    assert controller._emergency_hidden is False


@pytest.mark.asyncio
async def test_new_controller_clears_stale_emergency_hide_before_first_overlay_action() -> None:
    page = _make_mock_page(before_screenshot_success=False, emergency_hide_success=False)
    first_controller = OverlayController(page)
    await first_controller.claim_started()
    await first_controller.show_thought("Inspect the result.")

    with pytest.raises(RuntimeError, match="could not be hidden"):
        await first_controller.before_screenshot()

    assert page.emergency_hide_present is True

    second_controller = OverlayController(page)
    await second_controller.claim_started()
    page.evaluate.reset_mock()

    await second_controller.show_thought("Continue inspecting.")

    assert page.evaluate.call_args_list[0].args[0] == overlay_module._EMERGENCY_RESTORE_JS
    assert page.emergency_hide_present is False
    assert second_controller._emergency_hidden is False


@pytest.mark.asyncio
async def test_navigation_restore_task_failures_are_consumed_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    _, controller = await _started_controller()

    async def fail_restore() -> None:
        raise RuntimeError("page navigated again")

    task = asyncio.create_task(fail_restore())
    await asyncio.wait({task})

    with caplog.at_level("DEBUG", logger=overlay_module.__name__):
        controller._log_navigation_task_result(task)

    assert "Overlay navigation restore failed" in caplog.text


@pytest.mark.asyncio
async def test_before_screenshot_raises_when_runtime_and_backstop_cannot_hide() -> None:
    page, controller = await _started_controller(
        before_screenshot_success=False,
        emergency_hide_success=False,
    )
    await controller.show_thought("Inspect the result.")
    page.evaluate.reset_mock()

    with pytest.raises(RuntimeError, match="could not be hidden"):
        await controller.before_screenshot()

    assert controller._emergency_hidden is True

    await controller.after_screenshot()

    assert any(call.args[0] == overlay_module._EMERGENCY_RESTORE_JS for call in page.evaluate.call_args_list)
    assert controller._emergency_hidden is False


@pytest.mark.asyncio
async def test_after_screenshot_restores_persistent_overlay_state() -> None:
    page, controller = await _started_controller()
    await controller.show_thought("Inspect the result.")
    await controller.before_screenshot()
    page.operations.clear()

    await controller.after_screenshot()

    assert page.operations == [{"op": "afterScreenshot"}]
    assert controller._hidden is False


@pytest.mark.asyncio
async def test_inactive_controller_does_not_install_runtime() -> None:
    page = _make_mock_page()
    controller = OverlayController(page)

    await controller.show_thought("Invisible")
    await controller.preview_action("left_click", x=100, y=100)
    await controller.before_screenshot()
    await controller.after_screenshot()
    await controller.clear_thought()

    page.evaluate.assert_not_called()


@pytest.mark.asyncio
async def test_evaluate_failures_remain_best_effort_for_visualization() -> None:
    page, controller = await _started_controller(evaluate_side_effect=RuntimeError("page crashed"))

    await controller.show_thought("Inspect the result.")
    await controller.preview_action("scroll", x=100, y=200)
    await controller.clear_thought()
    await controller.after_screenshot()
    await controller.claim_ended()

    assert page.evaluate.await_count > 0


@pytest.mark.asyncio
async def test_show_thought_preserves_markdown_line_structure_and_clips_at_wire_boundary() -> None:
    page, controller = await _started_controller()
    text = "# Plan\n- check the bar fill\n- compare against the label"

    await controller.show_thought(text)

    assert _operation(page, "showThought")["markdown"] == text

    await controller.show_thought("x" * 600)
    clipped = page.operations[-1]["markdown"]
    assert len(clipped) == 520
    assert clipped.endswith("…")
