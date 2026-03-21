"""Tests for the headed-mode overlay controller."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_mock_page(
    *,
    persistent_root_exists: bool = True,
    focused_center: dict[str, int] | None = None,
    evaluate_side_effect: Exception | None = None,
) -> MagicMock:
    page = MagicMock()

    async def evaluate(script: str, *args: object) -> object:
        del args
        if evaluate_side_effect is not None:
            raise evaluate_side_effect
        script_text = str(script)
        if "!!document.getElementById('__n1PersistentRoot')" in script_text:
            return persistent_root_exists
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


class TestOverlayShowAction:
    @pytest.mark.asyncio
    async def test_click_effect_uses_coordinates_and_updates_status(self) -> None:
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.claim_started()
        page.evaluate.reset_mock()

        await controller.show_action("double_click", x=100, y=200, num_clicks=2)

        assert controller._current_status == "Clicking"
        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        assert any("__n1TransientRoot" in script and "visibility = 'visible'" in script for script in scripts)
        assert any("const numClicks = 2" in script for script in scripts)
        assert any("left:100px" in script and "top:200px" in script for script in scripts)

    @pytest.mark.asyncio
    async def test_scroll_effect_updates_status(self) -> None:
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.claim_started()
        page.evaluate.reset_mock()

        await controller.show_action("scroll", x=640, y=400, direction="down")

        assert controller._current_status == "Scrolling"
        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        assert any("__n1ScrollStyle" in script for script in scripts)
        assert any("polyline points" in script and "left:640px" in script and "top:400px" in script for script in scripts)

    @pytest.mark.asyncio
    async def test_type_effect_uses_focused_element_center_when_available(self) -> None:
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page(focused_center={"x": 200, "y": 150})
        controller = OverlayController(page)

        await controller.claim_started()
        page.evaluate.reset_mock()

        await controller.show_action("type")

        assert controller._current_status == "Typing"
        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        assert any("document.activeElement" in script for script in scripts)
        assert any("__n1TypeStyle" in script for script in scripts)
        assert any("left:200px" in script and "typing" in script for script in scripts)

    @pytest.mark.asyncio
    async def test_type_effect_skipped_when_no_focused_element(self) -> None:
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page(focused_center=None)
        controller = OverlayController(page)

        await controller.claim_started()
        page.evaluate.reset_mock()

        await controller.show_action("type")

        assert controller._current_status == "Typing"
        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        assert not any("__n1TypeStyle" in script for script in scripts)

    @pytest.mark.asyncio
    async def test_unknown_action_is_noop(self) -> None:
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.claim_started()
        page.evaluate.reset_mock()

        await controller.show_action("hover", x=10, y=20)

        assert controller._current_status == "Analyzing"
        page.evaluate.assert_not_called()


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

    @pytest.mark.asyncio
    async def test_transient_root_restored_when_effect_injected_after_screenshot(self) -> None:
        """After before_screenshot hides both roots and after_screenshot restores
        only persistent, the next show_action must restore transient root visibility."""
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.claim_started()
        await controller.before_screenshot()
        await controller.after_screenshot()
        page.evaluate.reset_mock()

        await controller.show_action("left_click", x=100, y=200)

        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        assert any(
            "__n1TransientRoot" in script and "visibility = 'visible'" in script
            for script in scripts
        ), "Transient root visibility must be restored before injecting new effects"

    @pytest.mark.asyncio
    async def test_after_screenshot_is_noop_when_inactive(self) -> None:
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page()
        controller = OverlayController(page)

        await controller.after_screenshot()

        page.evaluate.assert_not_called()


class TestOverlayNavigationSafety:
    @pytest.mark.asyncio
    async def test_ensure_persistent_ui_reinjects_when_missing(self) -> None:
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page(persistent_root_exists=False)
        controller = OverlayController(page)
        controller._active = True
        controller._current_status = "Navigating"

        await controller.ensure_persistent_ui()

        scripts = [str(call.args[0]) for call in page.evaluate.call_args_list]
        assert scripts[0] == "!!document.getElementById('__n1PersistentRoot')"
        assert any("__n1PersistentRoot" in script for script in scripts[1:])
        assert any("__n1StatusChip" in script and "Navigating" in script for script in scripts[1:])

    @pytest.mark.asyncio
    async def test_ensure_persistent_ui_is_noop_when_present(self) -> None:
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page(persistent_root_exists=True)
        controller = OverlayController(page)
        controller._active = True

        await controller.ensure_persistent_ui()

        page.evaluate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ensure_persistent_ui_is_noop_when_inactive(self) -> None:
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page(persistent_root_exists=False)
        controller = OverlayController(page)

        await controller.ensure_persistent_ui()

        page.evaluate.assert_not_called()


class TestOverlayBestEffort:
    @pytest.mark.asyncio
    async def test_overlay_methods_swallow_evaluate_failures(self) -> None:
        from frontend_visualqa.overlay import OverlayController

        page = _make_mock_page(evaluate_side_effect=RuntimeError("page crashed"))
        controller = OverlayController(page)

        await controller.claim_started()
        await controller.set_status("Analyzing")
        await controller.show_action("left_click", x=100, y=100)
        await controller.show_action("scroll", x=100, y=100, direction="down")
        await controller.show_action("type")
        await controller.before_screenshot()
        await controller.after_screenshot()
        await controller.ensure_persistent_ui()
        await controller.claim_ended()
