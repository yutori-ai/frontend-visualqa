from __future__ import annotations

import argparse
import asyncio
import copy
import json
import re
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import frontend_visualqa.actions as actions_mod
import frontend_visualqa.claim_verifier as claim_verifier_mod
import frontend_visualqa.prompts as prompts_mod
from frontend_visualqa.claim_parser import parse_claims_file
from frontend_visualqa.runner import VisualQARunner
from frontend_visualqa.schemas import TraceEvent, ViewportConfig


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_PATH = REPO_ROOT / "artifacts" / "readme-variant-benchmark.json"
DEFAULT_VIEWPORT = ViewportConfig(width=1280, height=800, device_scale_factor=1)
DEFAULT_CLAIM_TIMEOUT_SECONDS = 120.0
DEFAULT_RUN_TIMEOUT_SECONDS = 300.0
MAX_TEXT_CHARS = 4_000
MAX_LIST_ITEMS = 20
MAX_LIST_LINE_CHARS = 120

CURRENT_BUILD_VERIFICATION_TASK = prompts_mod.build_verification_task
CURRENT_EXTRACT_TOOL_SCHEMA = copy.deepcopy(prompts_mod.EXTRACT_CONTENT_AND_LINKS_TOOL)
_ARIA_LINK_PATTERN = re.compile(r'- link "([^"]*)"')
_ARIA_URL_PATTERN = re.compile(r"- /url: (.+)")
_LINK_TITLE_CLEANER_PATTERN = re.compile(r"\s+\d+$")


@dataclass(frozen=True)
class PromptVariant:
    name: str
    tool_description: str
    addendum_lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolVariant:
    name: str
    implementation: str
    summary: str


@dataclass(frozen=True)
class BenchmarkCase:
    name: str
    url: str
    claims: tuple[str, ...]
    expected_statuses: tuple[str, ...]
    max_steps_per_claim: int = 12
    claim_timeout_seconds: float = DEFAULT_CLAIM_TIMEOUT_SECONDS
    run_timeout_seconds: float = DEFAULT_RUN_TIMEOUT_SECONDS
    navigation_hint: str | None = None
    claim_navigation_hints: tuple[str | None, ...] | None = None
    reset_between_claims: bool = True
    notes: str | None = None


PROMPT_VARIANTS: tuple[PromptVariant, ...] = (
    PromptVariant(
        name="baseline",
        tool_description=CURRENT_EXTRACT_TOOL_SCHEMA["function"]["description"],
    ),
    PromptVariant(
        name="tool_first_pixels_for_fill",
        tool_description=(
            "Read exact visible text, labels, prices, totals, status strings, and hyperlinks from the current page. "
            "Use this tool before deciding claims about copy, numbers, counts, totals, endpoints, or URLs. "
            "Do not use it to judge visual fill amount, spacing, or layout extent."
        ),
        addendum_lines=(
            "Before deciding any claim about exact text, labels, prices, totals, counts, status strings, endpoints, or URLs, call extract_content_and_links once after you reach the relevant page state.",
            "For progress bars, gauges, fill levels, spacing, clipping, or layout extent, the screenshot pixels are the source of truth. If extracted text says 100% but the bar is not visually full, fail the claim.",
        ),
    ),
    PromptVariant(
        name="structured_checklist",
        tool_description=(
            "Read exact visible text, headings, buttons, prices, totals, status strings, and hyperlinks from the current page. "
            "Use this tool to verify copy and arithmetic after you reach the relevant page state."
        ),
        addendum_lines=(
            "Verification checklist:",
            "1. Reach the relevant page state first.",
            "2. If the claim mentions exact text, numbers, totals, prices, status labels, endpoints, or URLs, call extract_content_and_links before deciding.",
            "3. For arithmetic claims, compute the expected value from extracted text step by step, then compare it to the displayed value.",
            "4. For progress bars or claims that say a visual fill matches a percentage, compare extracted label text against the screenshot's visible fill and judge the fill from pixels when they disagree.",
        ),
    ),
)


TOOL_VARIANTS: tuple[ToolVariant, ...] = (
    ToolVariant(
        name="baseline_accessible_snapshot",
        implementation="baseline_accessible_snapshot",
        summary="Current URL plus aria snapshot and extracted links.",
    ),
    ToolVariant(
        name="visible_text_and_links",
        implementation="visible_text_and_links",
        summary="Current URL plus cleaned visible text and DOM links.",
    ),
    ToolVariant(
        name="structured_dom_digest",
        implementation="structured_dom_digest",
        summary="Current URL plus headings, buttons, key visible text lines, and links.",
    ),
)


def _load_readme_cases() -> tuple[BenchmarkCase, ...]:
    login_claims = parse_claims_file(REPO_ROOT / "examples" / "login_flow_claims.md")
    return (
        BenchmarkCase(
            name="readme_product_navigation",
            url="http://localhost:8000/ecommerce_store.html",
            claims=("The product detail page shows Wireless Headphones Pro priced at $149.99",),
            expected_statuses=("passed",),
            notes="README self-correcting navigation example.",
        ),
        BenchmarkCase(
            name="readme_dashboard_regressions",
            url="http://localhost:8000/analytics_dashboard.html",
            claims=(
                "The API status indicator shows Active",
                "The monthly quota progress bar is completely filled",
            ),
            expected_statuses=("passed", "failed"),
            notes="README catching regressions example.",
        ),
        BenchmarkCase(
            name="readme_cart_subtotal",
            url="http://localhost:8000/ecommerce_store.html#/cart",
            claims=("The displayed cart subtotal equals the sum of the visible sale prices",),
            expected_statuses=("failed",),
            notes="README pricing bug example.",
        ),
        BenchmarkCase(
            name="readme_booking_form_timezone",
            url="http://localhost:8000/booking_form.html",
            claims=("The date on the confirmation page matches the date selected on the calendar",),
            expected_statuses=("failed",),
            max_steps_per_claim=25,
            navigation_hint="Fill out the form with example data (grayed text is showing example format, not filled out values)",
            notes="README booking form example.",
        ),
        BenchmarkCase(
            name="readme_login_flow_claims_file",
            url="http://localhost:8000/yutori_login.html",
            claims=tuple(login_claims.claims),
            expected_statuses=("passed", "passed", "failed"),
            max_steps_per_claim=20,
            claim_navigation_hints=tuple(line.navigation_hint for line in login_claims.lines),
            reset_between_claims=False,
            notes="README claims-file login flow example.",
        ),
        BenchmarkCase(
            name="readme_cart_badge_with_hint",
            url="http://localhost:8000/ecommerce_store.html",
            claims=("The cart badge shows 3 items",),
            expected_statuses=("passed",),
            navigation_hint="Click 'Add to Cart' on the Mechanical Keyboard K7 product card.",
            notes="README navigation hint example.",
        ),
        BenchmarkCase(
            name="readme_webhooks_table_status",
            url="http://localhost:8000/analytics_dashboard.html",
            claims=("The /api/v1/webhooks endpoint returned a 200 OK status",),
            expected_statuses=("failed",),
            notes="README off-screen request table example.",
        ),
        BenchmarkCase(
            name="readme_login_validation",
            url="http://localhost:8000/yutori_login.html",
            claims=('The email field shows "Please enter a valid email address" after submitting the empty form',),
            expected_statuses=("passed",),
            navigation_hint="The grayed text in the fields is placeholder, not real input. Click the Continue button immediately without typing anything.",
            notes="README form validation example.",
        ),
    )


def _make_tool_schema(description: str) -> dict[str, Any]:
    schema = copy.deepcopy(CURRENT_EXTRACT_TOOL_SCHEMA)
    schema["function"]["description"] = description
    return schema


def _build_prompt_variant(prompt_variant: PromptVariant, claim: str, url: str, navigation_hint: str | None = None) -> str:
    base = CURRENT_BUILD_VERIFICATION_TASK(claim, url, navigation_hint)
    if not prompt_variant.addendum_lines:
        return base
    return "\n".join([base, "", "Additional guidance:", *prompt_variant.addendum_lines])


async def _extract_visible_text(page: Any) -> str | None:
    text = await page.evaluate(
        """() => {
            const text = (document.body?.innerText || "").replace(/\\n{3,}/g, "\\n\\n").trim();
            return text || null;
        }"""
    )
    if not text:
        return None
    return str(text).strip() or None


async def _extract_visible_links(page: Any) -> list[tuple[str, str]]:
    links = await page.evaluate(
        """() => {
            const normalize = (value) => (value || "").replace(/\\s+/g, " ").trim();
            const isVisible = (element) => {
                if (!element) return false;
                const style = window.getComputedStyle(element);
                if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0) {
                    return false;
                }
                const rect = element.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };
            const items = [];
            for (const anchor of Array.from(document.querySelectorAll("a[href]"))) {
                if (!isVisible(anchor)) continue;
                const title = normalize(anchor.innerText || anchor.textContent || anchor.getAttribute("aria-label") || "");
                const href = normalize(anchor.href || "");
                if (!title || !href) continue;
                items.push([title, href]);
            }
            return items;
        }"""
    )
    if not isinstance(links, list):
        return []
    cleaned: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in links:
        if not isinstance(item, list) or len(item) != 2:
            continue
        pair = (str(item[0]).strip(), str(item[1]).strip())
        if not pair[0] or not pair[1] or pair in seen:
            continue
        seen.add(pair)
        cleaned.append(pair)
    return cleaned[:MAX_LIST_ITEMS]


async def _extract_structured_digest(page: Any) -> dict[str, Any]:
    return await page.evaluate(
        """() => {
            const normalize = (value) => (value || "").replace(/\\s+/g, " ").trim();
            const isVisible = (element) => {
                if (!element) return false;
                const style = window.getComputedStyle(element);
                if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0) {
                    return false;
                }
                const rect = element.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };
            const visibleText = normalize(document.body?.innerText || "");
            const textLines = visibleText.split(/\\n+/).map(normalize).filter(Boolean);
            const headings = Array.from(document.querySelectorAll("h1, h2, h3, h4, [role='heading']"))
                .filter(isVisible)
                .map((node) => normalize(node.innerText || node.textContent || ""))
                .filter(Boolean);
            const buttons = Array.from(document.querySelectorAll("button, [role='button'], input[type='button'], input[type='submit']"))
                .filter(isVisible)
                .map((node) => normalize(node.innerText || node.textContent || node.getAttribute("value") || node.getAttribute("aria-label") || ""))
                .filter(Boolean);
            const links = Array.from(document.querySelectorAll("a[href]"))
                .filter(isVisible)
                .map((node) => [normalize(node.innerText || node.textContent || node.getAttribute("aria-label") || ""), normalize(node.href || "")])
                .filter((item) => item[0] && item[1]);
            return { headings, buttons, textLines, links };
        }"""
    )


def _clip(text: str | None, limit: int = MAX_TEXT_CHARS) -> str | None:
    if text is None:
        return None
    normalized = text.strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(limit - 3, 0)].rstrip() + "..."


def _render_list(title: str, values: Sequence[str]) -> list[str]:
    if not values:
        return []
    rendered = [title]
    for value in values[:MAX_LIST_ITEMS]:
        clipped = value[:MAX_LIST_LINE_CHARS].rstrip()
        rendered.append(f"- {clipped}")
    return rendered


async def _tool_output_baseline(self: Any, page: Any) -> str:
    snapshot = await self._accessible_page_snapshot(page)
    links = _extract_links_from_snapshot(snapshot) if snapshot else []
    sections = [f"Current URL: {page.url}"]
    if snapshot:
        sections.extend(
            [
                "Accessible page snapshot:",
                self._clip_multiline_text(snapshot, MAX_TEXT_CHARS),
            ]
        )
    if links:
        sections.extend(
            [
                "Links on the page:",
                "\n".join(f"- [{title}]({url})" for title, url in links),
            ]
        )
    return "\n\n".join(sections)


def _extract_links_from_snapshot(snapshot: str) -> list[tuple[str, str]]:
    url_to_title: dict[str, str] = {}
    lines = snapshot.splitlines()
    for index, line in enumerate(lines):
        link_match = _ARIA_LINK_PATTERN.search(line)
        if link_match is None:
            continue
        title = _LINK_TITLE_CLEANER_PATTERN.sub("", link_match.group(1)).strip()
        if not title:
            continue

        url: str | None = None
        child_indent = len(line) - len(line.lstrip()) + 2
        for next_line in lines[index + 1 :]:
            if next_line.strip() and not next_line.startswith(" " * child_indent):
                break
            url_match = _ARIA_URL_PATTERN.search(next_line)
            if url_match is not None:
                url = url_match.group(1).strip()
                break

        if not url:
            continue

        existing_title = url_to_title.get(url)
        if existing_title is None or len(title) > len(existing_title):
            url_to_title[url] = title

    return [(title, url) for url, title in url_to_title.items()]


async def _tool_output_visible_text(self: Any, page: Any) -> str:
    text = _clip(await _extract_visible_text(page))
    links = await _extract_visible_links(page)
    sections = [f"Current URL: {page.url}"]
    if text:
        sections.extend(["Visible text on the page:", text])
    if links:
        sections.extend(["Visible links on the page:", "\n".join(f"- [{title}]({url})" for title, url in links)])
    return "\n\n".join(sections)


async def _tool_output_structured_dom(self: Any, page: Any) -> str:
    digest = await _extract_structured_digest(page)
    headings = [str(item).strip() for item in digest.get("headings", []) if str(item).strip()]
    buttons = [str(item).strip() for item in digest.get("buttons", []) if str(item).strip()]
    text_lines = [str(item).strip() for item in digest.get("textLines", []) if str(item).strip()]
    links = []
    for item in digest.get("links", []):
        if not isinstance(item, list) or len(item) != 2:
            continue
        title = str(item[0]).strip()
        url = str(item[1]).strip()
        if title and url:
            links.append((title, url))

    sections = [f"Current URL: {page.url}"]
    sections.extend(_render_list("Visible headings:", headings))
    sections.extend(_render_list("Visible buttons and controls:", buttons))
    if text_lines:
        sections.extend(
            [
                "Key visible text lines:",
                "\n".join(f"- {line[:MAX_LIST_LINE_CHARS].rstrip()}" for line in text_lines[:MAX_LIST_ITEMS]),
            ]
        )
    if links:
        sections.extend(["Visible links on the page:", "\n".join(f"- [{title}]({url})" for title, url in links[:MAX_LIST_ITEMS])])
    return "\n\n".join(sections)


TOOL_IMPLEMENTATIONS: dict[str, Callable[[Any, Any], Any]] = {
    "baseline_accessible_snapshot": _tool_output_baseline,
    "visible_text_and_links": _tool_output_visible_text,
    "structured_dom_digest": _tool_output_structured_dom,
}


@contextmanager
def _apply_variants(prompt_variant: PromptVariant, tool_variant: ToolVariant) -> Iterable[None]:
    original_prompt_builder_prompts = prompts_mod.build_verification_task
    original_prompt_builder_claim = claim_verifier_mod.build_verification_task
    original_tool_schema_prompts = prompts_mod.EXTRACT_CONTENT_AND_LINKS_TOOL
    original_tool_schema_claim = claim_verifier_mod.EXTRACT_CONTENT_AND_LINKS_TOOL
    original_extract_method = actions_mod.ActionExecutor._extract_content_and_links

    tool_schema = _make_tool_schema(prompt_variant.tool_description)

    def build_variant_prompt(claim: str, url: str, navigation_hint: str | None = None) -> str:
        return _build_prompt_variant(prompt_variant, claim, url, navigation_hint)

    prompts_mod.build_verification_task = build_variant_prompt
    claim_verifier_mod.build_verification_task = build_variant_prompt
    prompts_mod.EXTRACT_CONTENT_AND_LINKS_TOOL = tool_schema
    claim_verifier_mod.EXTRACT_CONTENT_AND_LINKS_TOOL = tool_schema
    actions_mod.ActionExecutor._extract_content_and_links = TOOL_IMPLEMENTATIONS[tool_variant.implementation]
    try:
        yield
    finally:
        prompts_mod.build_verification_task = original_prompt_builder_prompts
        claim_verifier_mod.build_verification_task = original_prompt_builder_claim
        prompts_mod.EXTRACT_CONTENT_AND_LINKS_TOOL = original_tool_schema_prompts
        claim_verifier_mod.EXTRACT_CONTENT_AND_LINKS_TOOL = original_tool_schema_claim
        actions_mod.ActionExecutor._extract_content_and_links = original_extract_method


def _find_verdict_event(events: Sequence[TraceEvent]) -> TraceEvent | None:
    for event in reversed(events):
        if event.type == "verdict":
            return event
    return None


async def _run_case(case: BenchmarkCase, prompt_variant: PromptVariant, tool_variant: ToolVariant, rep: int) -> list[dict[str, Any]]:
    with _apply_variants(prompt_variant, tool_variant):
        runner = VisualQARunner(headless=True)
        started = time.time()
        try:
            result = await runner.run(
                url=case.url,
                claims=list(case.claims),
                claim_navigation_hints=list(case.claim_navigation_hints) if case.claim_navigation_hints is not None else None,
                viewport=DEFAULT_VIEWPORT,
                reset_between_claims=case.reset_between_claims,
                max_steps_per_claim=case.max_steps_per_claim,
                claim_timeout_seconds=case.claim_timeout_seconds,
                run_timeout_seconds=case.run_timeout_seconds,
                navigation_hint=case.navigation_hint,
                run_name=f"{prompt_variant.name}__{tool_variant.name}__{case.name}__rep{rep}",
            )
        finally:
            await runner.close()
        duration_seconds = round(time.time() - started, 2)

    results: list[dict[str, Any]] = []
    for index, claim_result in enumerate(result.results):
        verdict_event = _find_verdict_event(claim_result.trace.events)
        expected_status = case.expected_statuses[index]
        actions = list(claim_result.trace.actions)
        results.append(
            {
                "prompt_variant": prompt_variant.name,
                "tool_variant": tool_variant.name,
                "case": case.name,
                "rep": rep,
                "claim_index": index + 1,
                "claim": claim_result.claim,
                "expected_status": expected_status,
                "final_status": claim_result.status,
                "final_correct": claim_result.status == expected_status,
                "finding": claim_result.finding,
                "raw_status": verdict_event.raw_verdict_status if verdict_event else None,
                "raw_correct": (verdict_event.raw_verdict_status == expected_status) if verdict_event else None,
                "raw_finding": verdict_event.raw_finding if verdict_event else None,
                "verdict_status": verdict_event.verdict_status if verdict_event else None,
                "verdict_source": verdict_event.verdict_source if verdict_event else None,
                "extract_tool_used": any(action.startswith("extract_content_and_links()") for action in actions),
                "actions": actions,
                "steps_taken": claim_result.trace.steps_taken,
                "run_duration_seconds": duration_seconds,
                "artifacts_dir": result.artifacts_dir,
                "proof_screenshot_path": claim_result.proof.screenshot_path if claim_result.proof else None,
            }
        )
    return results


def _record_case_error(
    *,
    case: BenchmarkCase,
    prompt_variant: PromptVariant,
    tool_variant: ToolVariant,
    rep: int,
    error: BaseException,
) -> dict[str, Any]:
    return {
        "prompt_variant": prompt_variant.name,
        "tool_variant": tool_variant.name,
        "case": case.name,
        "rep": rep,
        "error_type": type(error).__name__,
        "error_message": str(error),
    }


def _summarize_claim_results(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    final_correct = sum(1 for item in results if item["final_correct"])
    raw_known = [item for item in results if item["raw_correct"] is not None]
    raw_correct = sum(1 for item in raw_known if item["raw_correct"])
    extract_used = sum(1 for item in results if item["extract_tool_used"])
    avg_steps = round(sum(item["steps_taken"] for item in results) / len(results), 2)
    avg_duration_seconds = round(sum(item["run_duration_seconds"] for item in results) / len(results), 2)
    return {
        "final_correct": f"{final_correct}/{len(results)}",
        "raw_correct": f"{raw_correct}/{len(raw_known)}" if raw_known else None,
        "extract_tool_used": f"{extract_used}/{len(results)}",
        "avg_steps": avg_steps,
        "avg_run_duration_seconds": avg_duration_seconds,
    }


def _aggregate_results(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    by_combo: dict[str, list[dict[str, Any]]] = {}
    by_combo_claim: dict[str, dict[str, list[dict[str, Any]]]] = {}

    for item in results:
        combo = f"{item['prompt_variant']}__{item['tool_variant']}"
        by_combo.setdefault(combo, []).append(item)
        claim_key = f"{item['case']}::claim{item['claim_index']}::{item['claim']}"
        by_combo_claim.setdefault(combo, {}).setdefault(claim_key, []).append(item)

    summary: dict[str, Any] = {"overall": {}, "per_claim": {}}
    for combo, combo_results in by_combo.items():
        summary["overall"][combo] = _summarize_claim_results(combo_results)
        summary["per_claim"][combo] = {
            claim_key: _summarize_claim_results(claim_results)
            for claim_key, claim_results in sorted(by_combo_claim[combo].items())
        }
    return summary


def _build_payload(
    *,
    args: argparse.Namespace,
    selected_prompts: Sequence[PromptVariant],
    selected_tools: Sequence[ToolVariant],
    cases: Sequence[BenchmarkCase],
    results: Sequence[dict[str, Any]],
    errors: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "generated_at_epoch": time.time(),
        "repeats": args.repeats,
        "selected_prompt_variants": [asdict(variant) for variant in selected_prompts],
        "selected_tool_variants": [asdict(variant) for variant in selected_tools],
        "selected_cases": [asdict(case) for case in cases],
        "summary": _aggregate_results(results),
        "results": list(results),
        "errors": list(errors),
    }


def _write_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _select_variants(
    prompt_names: set[str] | None,
    tool_names: set[str] | None,
) -> tuple[tuple[PromptVariant, ...], tuple[ToolVariant, ...]]:
    prompts = tuple(
        variant for variant in PROMPT_VARIANTS if prompt_names is None or variant.name in prompt_names
    )
    tools = tuple(variant for variant in TOOL_VARIANTS if tool_names is None or variant.name in tool_names)
    if not prompts:
        raise SystemExit("No prompt variants selected.")
    if not tools:
        raise SystemExit("No tool variants selected.")
    return prompts, tools


def _parse_name_filter(raw_values: Sequence[str] | None) -> set[str] | None:
    if not raw_values:
        return None
    names = {value.strip() for value in raw_values if value.strip()}
    return names or None


async def _main(args: argparse.Namespace) -> None:
    prompt_names = _parse_name_filter(args.prompt_variant)
    tool_names = _parse_name_filter(args.tool_variant)
    selected_prompts, selected_tools = _select_variants(prompt_names, tool_names)

    all_cases = _load_readme_cases()
    selected_case_names = _parse_name_filter(args.case)
    cases = tuple(case for case in all_cases if selected_case_names is None or case.name in selected_case_names)
    if not cases:
        raise SystemExit("No benchmark cases selected.")

    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    total_runs = len(selected_prompts) * len(selected_tools) * len(cases) * args.repeats
    current_run = 0

    for prompt_variant in selected_prompts:
        for tool_variant in selected_tools:
            for case in cases:
                for rep in range(1, args.repeats + 1):
                    current_run += 1
                    print(
                        f"[{current_run}/{total_runs}] prompt={prompt_variant.name} tool={tool_variant.name} case={case.name} rep={rep}",
                        file=sys.stderr,
                        flush=True,
                    )
                    try:
                        case_results = await _run_case(case, prompt_variant, tool_variant, rep)
                    except Exception as error:  # pragma: no cover - benchmark fault tolerance
                        error_record = _record_case_error(
                            case=case,
                            prompt_variant=prompt_variant,
                            tool_variant=tool_variant,
                            rep=rep,
                            error=error,
                        )
                        errors.append(error_record)
                        print(
                            f"Case failed: prompt={prompt_variant.name} tool={tool_variant.name} case={case.name} rep={rep} error={type(error).__name__}: {error}",
                            file=sys.stderr,
                            flush=True,
                        )
                    else:
                        results.extend(case_results)
                    finally:
                        payload = _build_payload(
                            args=args,
                            selected_prompts=selected_prompts,
                            selected_tools=selected_tools,
                            cases=cases,
                            results=results,
                            errors=errors,
                        )
                        _write_payload(args.output, payload)

    payload = _build_payload(
        args=args,
        selected_prompts=selected_prompts,
        selected_tools=selected_tools,
        cases=cases,
        results=results,
        errors=errors,
    )
    _write_payload(args.output, payload)
    print(f"Wrote benchmark report to {args.output}", file=sys.stderr)
    print(json.dumps(payload["summary"]["overall"], indent=2))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark prompt/tool variants against runnable README examples.")
    parser.add_argument("--repeats", type=int, default=1, help="Number of repeats per prompt/tool/case combination.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Path to write the JSON benchmark report.",
    )
    parser.add_argument(
        "--prompt-variant",
        action="append",
        help=f"Restrict to one or more prompt variant names. Available: {', '.join(v.name for v in PROMPT_VARIANTS)}",
    )
    parser.add_argument(
        "--tool-variant",
        action="append",
        help=f"Restrict to one or more tool variant names. Available: {', '.join(v.name for v in TOOL_VARIANTS)}",
    )
    parser.add_argument(
        "--case",
        action="append",
        help="Restrict to one or more benchmark case names.",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.repeats < 1:
        raise SystemExit("--repeats must be at least 1")
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
