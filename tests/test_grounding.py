"""Direct unit tests for frontend_visualqa.grounding.

These exercise ground_claim_verdict()/GroundingState handling without going
through a live browser or the full ClaimVerifier pipeline, and pin the
observable pass/fail behavior of the DOM-backed grounding checks.
"""

from __future__ import annotations

from frontend_visualqa.grounding import ground_claim_verdict

from fakes import default_grounding_state as _state


def test_button_visible_claim_passes_when_button_state_matches() -> None:
    state = _state(
        visibleButtons=["Save"],
        buttonStates=[{"text": "Save", "fullyVisible": False}],
    )
    status, finding = ground_claim_verdict(
        claim="The Save button is visible.",
        status="passed",
        finding="model said so",
        grounding_state=state,
    )
    assert status == "passed"
    assert "Save" in finding


def test_button_visible_claim_fails_when_no_button_matches() -> None:
    status, finding = ground_claim_verdict(
        claim="The Save button is visible.",
        status="passed",
        finding="model said so",
        grounding_state=_state(),
    )
    assert status == "failed"
    assert "No visible button label matched" in finding


def test_button_fully_visible_claim_fails_when_clipped() -> None:
    state = _state(
        visibleButtons=["Save"],
        buttonStates=[{"text": "Save", "fullyVisible": False}],
    )
    status, finding = ground_claim_verdict(
        claim="The Save button is fully visible.",
        status="passed",
        finding="model said so",
        grounding_state=state,
    )
    assert status == "failed"
    assert "clipped" in finding


def test_heading_reads_claim_passes_on_exact_match() -> None:
    state = _state(visibleHeadings=["Analytics Dashboard"])
    status, finding = ground_claim_verdict(
        claim='The heading reads "Analytics Dashboard".',
        status="passed",
        finding="model said so",
        grounding_state=state,
    )
    assert status == "passed"
    assert "Analytics Dashboard" in finding


def test_progress_bar_completely_filled_fails_when_partial() -> None:
    state = _state(progressBars=[{"label": "Upload", "fillRatio": 0.5}])
    status, finding = ground_claim_verdict(
        claim="The Upload progress bar is completely filled.",
        status="passed",
        finding="model said so",
        grounding_state=state,
    )
    assert status == "failed"
    assert "50%" in finding


def test_not_testable_status_bypasses_grounding() -> None:
    status, finding = ground_claim_verdict(
        claim="The Save button is visible.",
        status="not_testable",
        finding="unrelated to page",
        grounding_state=_state(),
    )
    assert status == "not_testable"
    assert finding == "unrelated to page"


def test_grounding_never_upgrades_a_failed_verdict() -> None:
    state = _state(
        visibleButtons=["Save"],
        buttonStates=[{"text": "Save", "fullyVisible": True}],
    )
    status, finding = ground_claim_verdict(
        claim="The Save button is fully visible.",
        status="failed",
        finding="model said button is clipped",
        grounding_state=state,
    )
    assert status == "failed"
    assert finding == "model said button is clipped"
