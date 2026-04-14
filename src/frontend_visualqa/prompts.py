"""Prompt helpers for the visual QA runner."""

from __future__ import annotations

from typing import Any



VERDICT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["passed", "failed", "inconclusive", "not_testable"],
        },
        "finding": {
            "type": "string",
            "description": "Brief evidence-backed finding of what you observed.",
        },
    },
    "required": ["status", "finding"],
}


def build_verification_task(claim: str, url: str, navigation_hint: str | None = None) -> str:
    """Build the bounded task prompt for a single claim."""

    parts = [
        "You are verifying a visual claim about a web page.",
        "",
        f'Claim to verify: "{claim}"',
        f"Target URL: {url}",
        "",
        "Process:",
        "1. If you are not already on the correct page state, navigate or interact until you reach it.",
        "2. Assess the page visually from the screenshot after each action.",
        "3. Stop as soon as you have enough evidence to make a determination.",
        "4. When you have enough evidence, stop taking actions and output your verdict as JSON.",
        "5. Treat the claim literally: do not substitute similar controls, nearby text, or adjacent UI for the element named in the claim.",
        "6. A pass requires exact grounding in the current screenshot. If the claim references text, title, heading, tab, or button label, verify that exact wording or a direct prefix match is visible.",
        "7. Do not change browser zoom or device scale. Judge the page at the provided viewport.",
        "8. Rely on the screenshot to verify exact text, numbers, and labels. Read carefully from the pixels.",
        "",
        "Use one of these statuses:",
        "- passed: the claim is visually true",
        "- failed: the claim is visually false",
        "- inconclusive: you tried but still cannot determine",
        "- not_testable: the environment blocked verification",
        "",
        "If the page is unreachable, stuck, crashes, or requires credentials you do not have, use not_testable.",
        "",
        "If a button or control is unresponsive, disabled, or you find yourself repeating the same action without progress, stop immediately and report what you found. A disabled button, a broken interaction, or an unresponsive control is itself a meaningful finding — report it as failed with a description of what is blocked and why.",
        "",
        "If the claim involves a calculation or total, compute the expected value step by step, then compare it to the displayed value digit by digit. If the two numbers differ, report failed immediately — do not assume hidden items, rounding, or other explanations.",
        "",
        "Known limitation: native HTML <select> dropdowns render as OS-level widgets outside the browser viewport. You cannot see or interact with their options. If you encounter a native <select> dropdown, report the claim as inconclusive and note that the page uses a native select element that requires a custom in-browser dropdown component for visual testing.",
    ]
    if navigation_hint:
        parts.extend(["", f"Navigation hint: {navigation_hint}"])
    parts.extend(
        [
            "",
            "Additional guidance:",
            "",
            "IMPORTANT — visual-first verification:",
            "You are testing for visual bugs. The most common bug type is a text-visual",
            "contradiction, where a text label says one thing but the pixels show another.",
            "Text labels are frequently WRONG on these pages. You MUST examine the pixels",
            "first and treat what you see as the source of truth.",
            "",
            "Concrete examples of bugs you should expect to find:",
            "- A gauge or circular indicator is filled to ~35% but the label next to it says '72%'",
            "- A toggle switch knob sits on the LEFT (OFF position) but the label says 'Enabled' or 'Locked'",
            "- A status dot is red or gray but the text beside it says 'Healthy' or 'Active'",
            "- A progress bar fills only two-thirds of its track but the label says '100%'",
            "- A star rating shows 4 filled stars but the text says '4.8 out of 5'",
            "- A button is grayed out and unclickable but the text on it says 'Send' as if it were active",
            "- A notification badge shows '2' because the rest of the digits are clipped by the container",
            "",
            "Verification checklist:",
            "1. Reach the relevant page state first.",
            "2. If the claim mentions exact text, numbers, totals, prices, status labels, endpoints, or URLs, read them carefully from the screenshot before deciding.",
            "3. For arithmetic claims, compute the expected value step by step, then compare it to the displayed value digit by digit.",
            "4. BEFORE reading any text label, decompose the target element from its PIXELS ALONE.",
            "   For gauges, bars, and rings:",
            "   - Identify the FULL length/arc of the track (the entire background shape).",
            "   - Identify the FILLED portion (the colored/highlighted section).",
            "   - Compare: does the filled portion reach the halfway point of the track? If not, the fill is BELOW 50%.",
            "   - Estimate the fill as a percentage of the full track. A fill that barely covers a third of the track is ~30-35%, not 70%+.",
            "   For toggles and switches:",
            "   - Identify the KNOB (the circle/handle that moves). Ignore the track color.",
            "   - Is the knob on the LEFT half of the track, or the RIGHT half?",
            "   - LEFT = OFF/disabled. RIGHT = ON/enabled. This is the standard convention.",
            "   For all elements:",
            "   - its color (name it precisely: green, red, yellow, gray, teal, blue, etc.)",
            "   - its physical state as determined above",
            "5. Then make two SEPARATE assessments in this order:",
            "   a) Visual-only assessment: based ONLY on what you see in the pixels (step 4 above), does the claim hold?",
            "   b) Text assessment: what do the nearby text labels say?",
            "6. If the visual and text assessments DISAGREE, always report FAILED.",
            "   The visual state is the ground truth. Do not let a text label override what the pixels show.",
        ]
    )
    return "\n".join(parts)


def build_force_stop_prompt(claim: str) -> str:
    """Prompt appended when the verifier reaches the step limit without a verdict."""

    return "\n".join(
        [
            "You have reached the maximum number of actions for this claim.",
            f'Claim: "{claim}"',
            "Do not take any more browser actions.",
            "Output your verdict as JSON now with your best verdict and a short evidence-backed finding.",
            "Use inconclusive if you truly cannot tell, or not_testable if the environment blocked you.",
        ]
    )


def build_action_or_verdict_prompt(claim: str) -> str:
    """Prompt appended when the model responds with free text instead of a tool call."""

    return "\n".join(
        [
            "You have not finished this claim yet.",
            f'Claim: "{claim}"',
            "Do not narrate your intent in plain text.",
            "Either take exactly one browser action next, or output your verdict as JSON now if you already have enough evidence.",
            "A plain-text response without a tool call will be treated as a failure to follow instructions.",
        ]
    )


def build_follow_navigation_hint_prompt(claim: str, navigation_hint: str) -> str:
    """Prompt appended when the model tries to verdict before following the navigation hint."""

    return "\n".join(
        [
            "You have not followed the navigation hint yet.",
            f'Claim: "{claim}"',
            f"Navigation hint: {navigation_hint}",
            "Do not render a final verdict before taking a browser action that follows the hint.",
            "Take exactly one browser action now, then reassess from the next screenshot.",
        ]
    )


def build_take_action_prompt(claim: str) -> str:
    """Prompt appended when the model says more interaction is needed but does not take it."""

    return "\n".join(
        [
            "Your last finding said more browser interaction is needed before you can decide this claim.",
            f'Claim: "{claim}"',
            "Do not narrate the next step or record another provisional inconclusive verdict.",
            "Take exactly one browser action now, then reassess from the next screenshot.",
        ]
    )
