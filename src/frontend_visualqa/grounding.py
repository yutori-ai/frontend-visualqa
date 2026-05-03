"""DOM-backed claim grounding for deterministic verifier checks."""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Callable
from typing import TypedDict

from frontend_visualqa.browser import BrowserSession
from frontend_visualqa.schemas import ClaimStatus
from frontend_visualqa.text_utils import collapse_whitespace


logger = logging.getLogger(__name__)


class ButtonState(TypedDict):
    text: str
    fullyVisible: bool


class ProgressBarState(TypedDict):
    label: str
    fillRatio: float


class GroundingState(TypedDict, total=False):
    visibleHeadings: list[str]
    visibleButtons: list[str]
    buttonStates: list[ButtonState]
    dialogTitles: list[str]
    progressBars: list[ProgressBarState]


BUTTON_VISIBLE_PATTERN = re.compile(
    r"""^The\s+(?P<label>(?:(?!\b(?:is|are|was|were|has|have|does|do|should|can)\b).)+?)\s+button\s+is\s+visible(?:\s+without\s+scrolling)?\.?$""",
    re.IGNORECASE,
)
BUTTON_FULLY_VISIBLE_PATTERN = re.compile(
    r"""^The\s+(?P<label>.+?)\s+button\s+is\s+fully\s+visible(?:\s+within\s+its\s+container)?\.?$""",
    re.IGNORECASE,
)
HEADING_READS_PATTERN = re.compile(r"""^The\s+heading\s+reads\s+["'](?P<text>.+?)["']\.?$""", re.IGNORECASE)
PAGE_TITLE_READS_PATTERN = re.compile(r"""^The\s+page\s+title\s+reads\s+["'](?P<text>.+?)["']\.?$""", re.IGNORECASE)
MODAL_TITLE_READS_PATTERN = re.compile(r"""^The\s+modal\s+title\s+reads\s+["'](?P<text>.+?)["']\.?$""", re.IGNORECASE)
PROGRESS_BAR_COMPLETELY_FILLED_PATTERN = re.compile(
    r"""^The\s+(?P<label>.+?)\s+progress\s+bar\s+is\s+completely\s+filled\.?$""",
    re.IGNORECASE,
)
GROUNDING_MARKER_SNIPPETS = {" title reads ", " heading reads ", " button is visible", " progress bar "}


async def capture_grounding_state(session: BrowserSession) -> GroundingState:
    return await session.page.evaluate(
        """() => {
            const normalize = (value) => (value || "").replace(/\\s+/g, " ").trim();
            const isVisible = (element) => {
                if (!element) return false;
                const style = window.getComputedStyle(element);
                if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0) {
                    return false;
                }
                const rect = element.getBoundingClientRect();
                if (rect.width <= 0 || rect.height <= 0) {
                    return false;
                }
                if (rect.bottom <= 0 || rect.right <= 0) {
                    return false;
                }
                if (rect.top >= window.innerHeight || rect.left >= window.innerWidth) {
                    return false;
                }
                return true;
            };
            const elementText = (element) => {
                if (!element) return "";
                const explicitText = normalize(element.innerText || element.textContent || "");
                if (explicitText) return explicitText;
                const ariaLabel = normalize(element.getAttribute("aria-label"));
                if (ariaLabel) return ariaLabel;
                const value = normalize(element.value);
                return value;
            };
            const visibleProgressBars = Array.from(
                document.querySelectorAll(
                    "[role='progressbar'], progress, meter, [aria-valuenow], .progress-track, .progress-bar, [class*='progress-track'], [class*='progress-bar']"
                )
            )
                .filter(isVisible)
                .map((element) => {
                    const rect = element.getBoundingClientRect();
                    if (rect.width < 24 || rect.height < 4) {
                        return null;
                    }

                    let fillRatio = null;
                    const ariaNow = element.getAttribute("aria-valuenow");
                    const ariaMin = element.getAttribute("aria-valuemin");
                    const ariaMax = element.getAttribute("aria-valuemax");
                    const min = ariaMin === null ? 0 : Number(ariaMin);
                    const max = ariaMax === null ? 100 : Number(ariaMax);
                    const now = ariaNow === null ? null : Number(ariaNow);
                    if (now !== null && Number.isFinite(now) && Number.isFinite(min) && Number.isFinite(max) && max > min) {
                        fillRatio = Math.max(0, Math.min(1, (now - min) / (max - min)));
                    } else if (element instanceof HTMLProgressElement && Number.isFinite(element.max) && element.max > 0) {
                        fillRatio = Math.max(0, Math.min(1, element.value / element.max));
                    } else if (element instanceof HTMLMeterElement && Number.isFinite(element.max) && element.max > element.min) {
                        fillRatio = Math.max(0, Math.min(1, (element.value - element.min) / (element.max - element.min)));
                    } else {
                        let maxChildWidth = 0;
                        for (const child of Array.from(element.children)) {
                            if (!isVisible(child)) continue;
                            const childRect = child.getBoundingClientRect();
                            if (childRect.height < rect.height * 0.5) continue;
                            maxChildWidth = Math.max(maxChildWidth, Math.min(childRect.width, rect.width));
                        }
                        if (maxChildWidth > 0) {
                            fillRatio = Math.max(0, Math.min(1, maxChildWidth / rect.width));
                        }
                    }

                    if (fillRatio === null) {
                        return null;
                    }

                    const labels = [];
                    const region = element.closest(
                        "section, article, aside, form, [role='region'], [role='group'], .glass-card, .card, .panel"
                    );
                    const labelSelectors = "h1, h2, h3, h4, legend, label, .card-title, .panel-title, .section-title, .title";
                    if (region) {
                        for (const candidate of Array.from(region.querySelectorAll(labelSelectors))) {
                            if (!isVisible(candidate)) continue;
                            const text = elementText(candidate);
                            if (text) labels.push(text);
                        }
                    }
                    for (let sibling = element.previousElementSibling; sibling; sibling = sibling.previousElementSibling) {
                        if (!isVisible(sibling)) continue;
                        const text = elementText(sibling);
                        if (text) labels.push(text);
                    }

                    const label = labels.find(Boolean) || "";
                    return { label, fillRatio };
                })
                .filter(Boolean);
            const visibleHeadings = Array.from(document.querySelectorAll("h1, h2, h3, h4, [role='heading']"))
                .filter(isVisible)
                .map(elementText)
                .filter(Boolean);
            const visibleButtons = Array.from(
                document.querySelectorAll("button, [role='button'], input[type='button'], input[type='submit']")
            )
                .filter(isVisible)
                .map(elementText)
                .filter(Boolean);
            const buttonStates = Array.from(
                document.querySelectorAll("button, [role='button'], input[type='button'], input[type='submit']")
            )
                .filter(isVisible)
                .map((element) => {
                    const text = elementText(element);
                    if (!text) return null;
                    const rect = element.getBoundingClientRect();
                    let fullyVisible =
                        rect.top >= 0 &&
                        rect.left >= 0 &&
                        rect.bottom <= window.innerHeight &&
                        rect.right <= window.innerWidth;
                    for (let ancestor = element.parentElement; ancestor && fullyVisible; ancestor = ancestor.parentElement) {
                        const style = window.getComputedStyle(ancestor);
                        const clips =
                            ["hidden", "clip", "scroll", "auto"].includes(style.overflow) ||
                            ["hidden", "clip", "scroll", "auto"].includes(style.overflowX) ||
                            ["hidden", "clip", "scroll", "auto"].includes(style.overflowY);
                        if (!clips) continue;
                        const ancestorRect = ancestor.getBoundingClientRect();
                        if (
                            rect.top < ancestorRect.top ||
                            rect.left < ancestorRect.left ||
                            rect.bottom > ancestorRect.bottom ||
                            rect.right > ancestorRect.right
                        ) {
                            fullyVisible = false;
                        }
                    }
                    return { text, fullyVisible };
                })
                .filter(Boolean);
            const dialogTitles = Array.from(document.querySelectorAll("[role='dialog'], dialog, [aria-modal='true']"))
                .filter(isVisible)
                .flatMap((dialog) => {
                    const titles = [];
                    const labelledBy = dialog.getAttribute("aria-labelledby");
                    if (labelledBy) {
                        const labelElement = document.getElementById(labelledBy);
                        if (isVisible(labelElement)) {
                            const text = elementText(labelElement);
                            if (text) titles.push(text);
                        }
                    }
                    for (const heading of dialog.querySelectorAll("h1, h2, h3, h4, [role='heading']")) {
                        if (!isVisible(heading)) continue;
                        const text = elementText(heading);
                        if (text) titles.push(text);
                    }
                    return titles;
                })
                .filter(Boolean);
            return { visibleHeadings, visibleButtons, buttonStates, dialogTitles, progressBars: visibleProgressBars };
        }"""
    )


def ground_claim_verdict(
    *,
    claim: str,
    status: ClaimStatus,
    finding: str,
    grounding_state: GroundingState,
) -> tuple[ClaimStatus, str]:
    if status == "not_testable":
        return status, finding

    normalized_claim = _normalize_text(claim)
    for pattern, checker in (
        (PROGRESS_BAR_COMPLETELY_FILLED_PATTERN, _check_progress_bar_completely_filled),
        (BUTTON_FULLY_VISIBLE_PATTERN, _check_button_fully_visible),
        (MODAL_TITLE_READS_PATTERN, _check_dialog_title_match),
        (HEADING_READS_PATTERN, _check_heading_match),
        (PAGE_TITLE_READS_PATTERN, _check_heading_match),
        (BUTTON_VISIBLE_PATTERN, _check_button_match),
    ):
        match = pattern.match(claim.strip())
        if match is None:
            continue
        grounded = checker(grounding_state, match.groupdict())
        if grounded is None:
            return status, finding
        grounded_status, grounded_finding = grounded
        if grounded_status != "passed":
            logger.info("Downgrading pass verdict for claim %r after grounding check", claim)
        return grounded_status, grounded_finding

    if any(marker in normalized_claim for marker in GROUNDING_MARKER_SNIPPETS):
        logger.info("No grounding rule matched pass verdict for claim %r", claim)
    return status, finding


def _normalize_text(value: str) -> str:
    return collapse_whitespace(value).casefold()


def _normalize_label_for_match(value: str) -> str:
    text = collapse_whitespace(value).casefold()
    for quote in ("'", '"', "‘", "’", "“", "”"):
        text = text.replace(quote, "")
    for suffix in (" dropdown", " menu", " icon", " button"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] not in ("S",) and ch not in "▼▶▾▸◀◂✕×›‹«»")
    return collapse_whitespace(text)


def _label_matches(
    *,
    candidate: str,
    normalized_label: str,
    fuzzy_label: str,
    allow_substring: bool = False,
) -> bool:
    """Return True if ``candidate`` matches the pre-normalized label values.

    ``normalized_label`` and ``fuzzy_label`` should be produced via
    :func:`_normalize_text` and :func:`_normalize_label_for_match` respectively.
    ``allow_substring`` enables the more permissive containment check used for
    progress-bar labels (which are often surrounded by extra context text).
    """
    normalized_candidate = _normalize_text(candidate)
    fuzzy_candidate = _normalize_label_for_match(candidate)
    if (
        normalized_candidate == normalized_label
        or normalized_candidate.startswith(f"{normalized_label} ")
        or (fuzzy_label and fuzzy_candidate == fuzzy_label)
        or (fuzzy_label and fuzzy_candidate.startswith(f"{fuzzy_label} "))
    ):
        return True
    if allow_substring and (
        normalized_label in normalized_candidate
        or (fuzzy_label and fuzzy_label in fuzzy_candidate)
    ):
        return True
    return False


def _make_label_matcher(label: str, *, allow_substring: bool = False) -> Callable[[str], bool]:
    """Return a predicate that tests candidate strings against ``label``.

    Pre-normalizes ``label`` once so the predicate can be applied to many
    candidates without redoing the (collapse-whitespace, casefold, fuzzy-strip)
    work each call.
    """
    normalized_label = _normalize_text(label)
    fuzzy_label = _normalize_label_for_match(label)

    def matches(candidate: str) -> bool:
        return _label_matches(
            candidate=candidate,
            normalized_label=normalized_label,
            fuzzy_label=fuzzy_label,
            allow_substring=allow_substring,
        )

    return matches


def _check_exact_text_match(
    grounding_state: GroundingState,
    groups: dict[str, str],
    *,
    state_key: str,
    entity_label: str,
) -> tuple[ClaimStatus, str] | None:
    expected = _normalize_text(groups["text"])
    candidates = grounding_state.get(state_key, [])
    if any(_normalize_text(text) == expected for text in candidates):
        return "passed", f"Visible {entity_label} matched {groups['text']!r}."
    return (
        "failed",
        f"No visible {entity_label} matched {groups['text']!r}. Visible {entity_label}s: {candidates or ['<none>']}.",
    )


def _check_heading_match(
    grounding_state: GroundingState,
    groups: dict[str, str],
) -> tuple[ClaimStatus, str] | None:
    return _check_exact_text_match(
        grounding_state,
        groups,
        state_key="visibleHeadings",
        entity_label="heading",
    )


def _check_dialog_title_match(
    grounding_state: GroundingState,
    groups: dict[str, str],
) -> tuple[ClaimStatus, str] | None:
    return _check_exact_text_match(
        grounding_state,
        groups,
        state_key="dialogTitles",
        entity_label="dialog title",
    )


def _check_button_match(
    grounding_state: GroundingState,
    groups: dict[str, str],
) -> tuple[ClaimStatus, str] | None:
    matched_states = _matching_button_states(grounding_state, groups["label"])
    if matched_states:
        if any(state.get("fullyVisible", False) for state in matched_states):
            candidate = matched_states[0].get("text", groups["label"])
            return "passed", f"Visible button label matched {groups['label']!r}: {candidate!r}."
        candidate = matched_states[0].get("text", groups["label"])
        return (
            "failed",
            f"Visible button label matched {groups['label']!r}, but {candidate!r} is clipped or only partially visible.",
        )

    matches = _make_label_matcher(groups["label"])
    visible_buttons = grounding_state.get("visibleButtons", [])
    for candidate in visible_buttons:
        if matches(candidate):
            return "passed", f"Visible button label matched {groups['label']!r}: {candidate!r}."
    return (
        "failed",
        f"No visible button label matched {groups['label']!r}. Visible buttons: {visible_buttons or ['<none>']}.",
    )


def _check_button_fully_visible(
    grounding_state: GroundingState,
    groups: dict[str, str],
) -> tuple[ClaimStatus, str] | None:
    matched_states = _matching_button_states(grounding_state, groups["label"])
    if not matched_states:
        visible_buttons = grounding_state.get("visibleButtons", [])
        return (
            "failed",
            f"No visible button label matched {groups['label']!r}. Visible buttons: {visible_buttons or ['<none>']}.",
        )

    if any(state.get("fullyVisible", False) for state in matched_states):
        candidate = next(state.get("text", groups["label"]) for state in matched_states if state.get("fullyVisible", False))
        return "passed", f"Visible button label matched {groups['label']!r} and is fully visible: {candidate!r}."

    candidate = matched_states[0].get("text", groups["label"])
    return (
        "failed",
        f"Visible button label matched {groups['label']!r}, but {candidate!r} is clipped or not fully visible.",
    )


def _check_progress_bar_completely_filled(
    grounding_state: GroundingState,
    groups: dict[str, str],
) -> tuple[ClaimStatus, str] | None:
    matched_bars = _matching_progress_bars(grounding_state, groups["label"])
    if not matched_bars:
        visible_labels = [bar.get("label", "") for bar in grounding_state.get("progressBars", []) if bar.get("label")]
        return (
            "failed",
            f"No visible progress bar label matched {groups['label']!r}. Visible progress labels: {visible_labels or ['<none>']}.",
        )

    fullest_bar = max(matched_bars, key=lambda bar: float(bar.get("fillRatio", 0.0)))
    fill_ratio = float(fullest_bar.get("fillRatio", 0.0))
    label = str(fullest_bar.get("label", groups["label"]))
    if fill_ratio >= 0.99:
        return "passed", f"Visible progress bar label matched {groups['label']!r} and is fully filled."
    return (
        "failed",
        f"Visible progress bar label matched {groups['label']!r}, but {label!r} is only {fill_ratio:.0%} filled.",
    )


def _matching_button_states(grounding_state: GroundingState, label: str) -> list[ButtonState]:
    matches = _make_label_matcher(label)
    return [
        state
        for state in grounding_state.get("buttonStates", [])
        if matches(str(state.get("text", "")))
    ]


def _matching_progress_bars(grounding_state: GroundingState, label: str) -> list[ProgressBarState]:
    matches = _make_label_matcher(label, allow_substring=True)
    return [
        bar
        for bar in grounding_state.get("progressBars", [])
        if matches(str(bar.get("label", "")))
    ]
