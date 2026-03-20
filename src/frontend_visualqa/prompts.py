"""Prompt helpers for the visual QA runner."""

from __future__ import annotations

from typing import Any


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
                "summary": {
                    "type": "string",
                    "description": "Brief evidence-backed explanation of what you observed.",
                },
            },
            "required": ["status", "summary"],
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
        "",
        "Use one of these statuses:",
        "- passed: the claim is visually true",
        "- failed: the claim is visually false",
        "- inconclusive: you tried but still cannot determine",
        "- not_testable: the environment blocked verification",
        "",
        "Available tools include goto_url, left_click, double_click, triple_click, right_click, hover, drag, scroll, type, key_press, wait, refresh, go_back, go_forward, extract_elements, extract_content, and find.",
        "If the page is unreachable, stuck, crashes, or requires credentials you do not have, use not_testable.",
    ]
    if navigation_hint:
        parts.extend(["", f"Navigation hint: {navigation_hint}"])
    return "\n".join(parts)


def build_force_stop_prompt(claim: str) -> str:
    """Prompt appended when the verifier reaches the step limit without a verdict."""

    return "\n".join(
        [
            "You have reached the maximum number of actions for this claim.",
            f'Claim: "{claim}"',
            "Do not take any more browser actions.",
            "Call record_claim_result now with your best verdict and a short evidence summary.",
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
