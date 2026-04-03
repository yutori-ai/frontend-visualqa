"""Prompt helpers for the visual QA runner."""

from __future__ import annotations

from typing import Any


EXTRACT_CONTENT_AND_LINKS_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "extract_content_and_links",
        "description": (
            "Read exact visible text, headings, buttons, prices, totals, status strings, and hyperlinks "
            "from the current page. Use this tool to verify copy and arithmetic after you reach the "
            "relevant page state."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


RECORD_CLAIM_RESULT_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "record_claim_result",
        "description": (
            "Report the verification result for the current claim. Call this once you have enough evidence "
            "to decide whether the claim is visually true, false, inconclusive, or not testable."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["passed", "failed", "inconclusive", "not_testable"],
                    "description": (
                        "passed: the claim is visually true. failed: the claim is visually false. "
                        "inconclusive: you tried but still cannot determine. "
                        "not_testable: the environment blocked verification."
                    ),
                },
                "finding": {
                    "type": "string",
                    "description": "Brief evidence-backed finding of what you observed.",
                },
            },
            "required": ["status", "finding"],
        },
    },
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
        "4. Report your final verdict by calling the record_claim_result tool.",
        "5. Treat the claim literally: do not substitute similar controls, nearby text, or adjacent UI for the element named in the claim.",
        "6. A pass requires exact grounding in the current screenshot. If the claim references text, title, heading, tab, or button label, verify that exact wording or a direct prefix match is visible.",
        "7. Do not change browser zoom or device scale. Judge the page at the provided viewport.",
        "8. If reading the page content or visible links would help you orient yourself or verify exact text, you may call the read-only extract_content_and_links tool.",
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
            "Verification checklist:",
            "1. Reach the relevant page state first.",
            "2. If the claim mentions exact text, numbers, totals, prices, status labels, endpoints, or URLs, call extract_content_and_links before deciding.",
            "3. For arithmetic claims, compute the expected value from extracted text step by step, then compare it to the displayed value.",
            "4. Decompose the target element before judging. For the specific element the claim refers to, write down:",
            "   - its color (name it: green, red, yellow, gray, teal, blue, etc.)",
            "   - its position or state (toggle knob left/right, tab highlighted/not, star filled/empty)",
            "   - its size or proportion (bar ~30% filled, ring ~40% of circumference, badge fully visible/clipped)",
            "5. Then make two separate assessments:",
            "   a) Text assessment: based only on extracted text, does the claim hold?",
            "   b) Visual assessment: based only on your decomposed observations from the screenshot, does the claim hold?",
            "6. If the text and visual assessments agree, report that verdict. If they disagree, report failed.",
            "",
            "You are testing for visual bugs. Bugs are common in these pages. Expect contradictions where:",
            "- a status dot is a different color than what the text label says",
            "- a toggle or switch is positioned opposite to what the label claims",
            "- a gauge, bar, or ring shows a different fill level than the numeric label",
            "- fewer or more stars or icons are filled than the rating text indicates",
            "- a button looks disabled or grayed out despite having active text",
            "- content is clipped or truncated by its container",
            "Look carefully at the actual pixels of each element before trusting any text label.",
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
            "Call record_claim_result now with your best verdict and a short evidence-backed finding.",
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
            "Either take exactly one browser action next, or call record_claim_result now if you already have enough evidence.",
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
