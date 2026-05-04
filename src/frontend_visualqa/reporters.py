"""Reporter abstraction for writing run results in different formats."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Protocol

from frontend_visualqa.artifacts import write_json_file, write_text_file
from frontend_visualqa.claim_parser import ParsedClaimLine, ParsedClaimsFile
from frontend_visualqa.schemas import ClaimProof, ClaimResult, ClaimTrace, RunResult
from frontend_visualqa.serialization import serialize_result
from frontend_visualqa.text_utils import collapse_whitespace as _collapse_whitespace


class Reporter(Protocol):
    """Interface for result reporters."""

    @property
    def name(self) -> str: ...

    def write(self, run_result: RunResult, output_dir: Path, *, claims_file: ParsedClaimsFile | None = None) -> None: ...


class NativeReporter:
    """Writes the full RunResult as run_result.json (the domain-specific schema)."""

    name: str = "native"

    def write(self, run_result: RunResult, output_dir: Path, *, claims_file: ParsedClaimsFile | None = None) -> None:
        del claims_file
        write_json_file(output_dir / "run_result.json", serialize_result(run_result))


_CTRF_STATUS_MAP: dict[str, str] = {
    "passed": "passed",
    "failed": "failed",
    "inconclusive": "other",
    "not_testable": "skipped",
}


def _ctrf_attachment(path: str, content_type: str) -> dict[str, str]:
    """Build a CTRF attachment dict (name derived from path basename)."""
    return {
        "name": Path(path).name,
        "contentType": content_type,
        "path": path,
    }


def _gather_ctrf_attachments(proof: ClaimProof | None, trace: ClaimTrace) -> list[dict[str, str]]:
    """Collect every artifact attachment (screenshots, proof text, trace JSON) for a claim."""
    attachments: list[dict[str, str]] = [
        _ctrf_attachment(screenshot_path, "image/webp")
        for screenshot_path in trace.screenshot_paths
    ]
    proof_text_path = proof.text_path if proof is not None else None
    if proof_text_path:
        attachments.append(_ctrf_attachment(proof_text_path, "text/plain"))
    if trace.trace_path:
        attachments.append(_ctrf_attachment(trace.trace_path, "application/json"))
    return attachments


_CLAIM_DETAILS_START_MARKER = "<!-- frontend-visualqa:claim-details:start -->"
_CLAIM_DETAILS_END_MARKER = "<!-- frontend-visualqa:claim-details:end -->"
_APPENDIX_START_MARKER = "<!-- frontend-visualqa:appendix:start -->"
_APPENDIX_END_MARKER = "<!-- frontend-visualqa:appendix:end -->"


class CTRFReporter:
    """Writes a CTRF-compliant ctrf-report.json."""

    name: str = "ctrf"

    def write(self, run_result: RunResult, output_dir: Path, *, claims_file: ParsedClaimsFile | None = None) -> None:
        del claims_file
        now_ms = int(time.time() * 1000)
        summary_counts: dict[str, int] = {
            "passed": 0, "failed": 0, "pending": 0, "skipped": 0, "other": 0,
        }
        ctrf_tests: list[dict[str, Any]] = []

        for claim_result in run_result.results:
            ctrf_status = _CTRF_STATUS_MAP.get(claim_result.status, "other")
            summary_counts[ctrf_status] += 1

            extra: dict[str, Any] = {"claimResult": serialize_result(claim_result)}
            attachments = _gather_ctrf_attachments(claim_result.proof, claim_result.trace)

            ctrf_test: dict[str, Any] = {
                "name": claim_result.claim,
                "status": ctrf_status,
                "duration": 0,
                "message": claim_result.finding,
                "extra": extra,
            }
            if claim_result.status not in ("passed", "failed"):
                ctrf_test["rawStatus"] = claim_result.status
            if attachments:
                ctrf_test["attachments"] = attachments
            ctrf_tests.append(ctrf_test)

        start_ms = int(run_result.started_at * 1000) if run_result.started_at is not None else now_ms
        stop_ms = int(run_result.completed_at * 1000) if run_result.completed_at is not None else now_ms

        ctrf_report = {
            "reportFormat": "CTRF",
            "specVersion": "0.0.0",
            "results": {
                "tool": {
                    "name": "frontend-visualqa",
                    "version": run_result.runner_version,
                },
                "summary": {
                    "tests": len(ctrf_tests),
                    **summary_counts,
                    "start": start_ms,
                    "stop": stop_ms,
                },
                "tests": ctrf_tests,
            },
        }
        if run_result.run_name is not None:
            ctrf_report["results"]["extra"] = {"runName": run_result.run_name}

        write_json_file(output_dir / "ctrf-report.json", ctrf_report)


# Single-pass per-character translation, used by _escape_markdown_inline.
# Built once at import time. The chained .replace() form this replaces had a
# load-bearing ordering requirement (escape "\" first, before subsequent steps
# introduced new "\" characters); str.translate cannot collide with itself, so
# the hazard is gone by construction.
_MARKDOWN_INLINE_ESCAPES = str.maketrans(
    {
        "\\": r"\\",
        "*": r"\*",
        "_": r"\_",
        "[": r"\[",
        "]": r"\]",
        "`": r"\`",
        "<": r"\<",
        ">": r"\>",
        "|": r"\|",
    }
)


def _escape_markdown_inline(text: str) -> str:
    return text.translate(_MARKDOWN_INLINE_ESCAPES)


def _line_ending(text: str) -> str:
    if text.endswith("\r\n"):
        return "\r\n"
    if text.endswith("\n"):
        return "\n"
    if text.endswith("\r"):
        return "\r"
    return "\n"


def _render_detail_line(prefix: str, label: str, value: str) -> str:
    return f"{prefix}{label}: {_escape_markdown_inline(value)}"


def _render_claim_lines(*, bullet: str, claim: str, claim_result: ClaimResult) -> list[str]:
    marker = "x" if claim_result.status == "passed" else " "
    lines = [f"{bullet} [{marker}] {claim}"]
    if claim_result.status != "passed":
        lines.append(_CLAIM_DETAILS_START_MARKER)
        lines.append(_render_detail_line("  ", "Status", claim_result.status))
        lines.append(_render_detail_line("  ", "Finding", _collapse_whitespace(claim_result.finding)))
        lines.append(_CLAIM_DETAILS_END_MARKER)
    return lines


def _render_claim_block(*, source_line: ParsedClaimLine, claim_result: ClaimResult, line_ending: str) -> str:
    lines = _render_claim_lines(
        bullet=source_line.bullet,
        claim=source_line.claim,
        claim_result=claim_result,
    )
    return line_ending.join(lines) + line_ending


def _render_appendix(run_result: RunResult, *, additional_results: list[ClaimResult]) -> str:
    lines = [_APPENDIX_START_MARKER, ""]
    if additional_results:
        lines.extend(["## Additional Results", ""])
        for claim_result in additional_results:
            lines.extend(_render_claim_lines(bullet="-", claim=claim_result.claim, claim_result=claim_result))
        lines.append("")
    lines.extend(["## Summary", "", f"Run summary: {run_result.summary}", _APPENDIX_END_MARKER])
    return "\n".join(lines) + "\n"


def _render_synthesized_markdown(run_result: RunResult) -> str:
    lines = ["# frontend-visualqa report", ""]
    if run_result.run_name is not None:
        lines.append(f"Run: {run_result.run_name}")
    lines.append(f"Artifacts: {run_result.artifacts_dir}")
    lines.append("")
    lines.append("## Claims")
    lines.append("")
    for claim_result in run_result.results:
        lines.extend(_render_claim_lines(bullet="-", claim=claim_result.claim, claim_result=claim_result))
    lines.append("")
    return "\n".join(lines) + _render_appendix(run_result, additional_results=[])


def _collect_generated_skip_indices(source_lines: list[str], claim_line_indices: set[int]) -> set[int]:
    skip_indices: set[int] = set()
    saw_markers = False
    in_generated_block = False

    for index, line in enumerate(source_lines):
        stripped = line.strip()
        if stripped in {_CLAIM_DETAILS_START_MARKER, _APPENDIX_START_MARKER}:
            saw_markers = True
            in_generated_block = True
            skip_indices.add(index)
            continue
        if stripped in {_CLAIM_DETAILS_END_MARKER, _APPENDIX_END_MARKER}:
            saw_markers = True
            skip_indices.add(index)
            in_generated_block = False
            continue
        if in_generated_block:
            skip_indices.add(index)

    if saw_markers:
        return skip_indices

    for claim_idx in sorted(claim_line_indices):
        index = claim_idx + 1
        while index < len(source_lines):
            stripped = source_lines[index].lstrip()
            if stripped.startswith("Status: ") or stripped.startswith("Finding: "):
                skip_indices.add(index)
                index += 1
                continue
            break

    for index, line in enumerate(source_lines):
        heading = line.strip()
        if heading in ("## Summary", "## Additional Results"):
            skip_indices.update(range(index, len(source_lines)))
            break

    return skip_indices


def _render_annotated_source_markdown(run_result: RunResult, claims_file: ParsedClaimsFile) -> str:
    source_lines = claims_file.source_content.splitlines(keepends=True)
    rendered_by_index: dict[int, str] = {}

    for source_line, claim_result in zip(claims_file.lines, run_result.results):
        original_text = source_lines[source_line.line_index] if 0 <= source_line.line_index < len(source_lines) else ""
        rendered_by_index[source_line.line_index] = _render_claim_block(
            source_line=source_line,
            claim_result=claim_result,
            line_ending=_line_ending(original_text),
        )

    skip_indices = _collect_generated_skip_indices(source_lines, set(rendered_by_index))

    rendered_lines: list[str] = []
    for index, line in enumerate(source_lines):
        if index in skip_indices:
            continue
        replacement = rendered_by_index.get(index)
        if replacement is None:
            rendered_lines.append(line)
        else:
            rendered_lines.append(replacement)

    if rendered_lines and not rendered_lines[-1].endswith(("\n", "\r")):
        rendered_lines.append("\n")

    rendered_lines.append(
        _render_appendix(
            run_result,
            additional_results=run_result.results[len(claims_file.lines) :],
        )
    )
    return "".join(rendered_lines)


class MarkdownReporter:
    """Writes a Markdown report that can annotate the original claims file."""

    name: str = "markdown"

    def write(self, run_result: RunResult, output_dir: Path, *, claims_file: ParsedClaimsFile | None = None) -> None:
        if claims_file is None:
            content = _render_synthesized_markdown(run_result)
        else:
            content = _render_annotated_source_markdown(run_result, claims_file)

        write_text_file(output_dir / "report.md", content)


_REPORTERS: dict[str, type[Reporter]] = {
    "native": NativeReporter,
    "ctrf": CTRFReporter,
    "markdown": MarkdownReporter,
}


def get_reporters(names: list[str]) -> list[Reporter]:
    """Instantiate reporters by name. Defaults to ['native'] if empty."""
    if not names:
        names = ["native"]
    reporters: list[Reporter] = []
    for name in names:
        cls = _REPORTERS.get(name)
        if cls is None:
            raise ValueError(
                f"Unknown reporter: {name!r}. "
                f"Available reporters: {sorted(_REPORTERS.keys())}"
            )
        reporters.append(cls())
    return reporters
