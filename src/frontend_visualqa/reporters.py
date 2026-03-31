"""Reporter abstraction for writing run results in different formats."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Protocol

from frontend_visualqa.claim_parser import ParsedClaimLine, ParsedClaimsFile
from frontend_visualqa.schemas import RunResult
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
        path = output_dir / "run_result.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(run_result.model_dump(mode="json"), indent=2))


_CTRF_STATUS_MAP: dict[str, str] = {
    "passed": "passed",
    "failed": "failed",
    "inconclusive": "other",
    "not_testable": "skipped",
}


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

            extra: dict[str, Any] = {"claimResult": claim_result.model_dump(mode="json")}
            trace = claim_result.trace
            screenshots = trace.screenshot_paths
            attachments = [
                {
                    "name": Path(screenshot_path).name,
                    "contentType": "image/webp",
                    "path": screenshot_path,
                }
                for screenshot_path in screenshots
            ]
            proof = claim_result.proof
            proof_text_path = proof.text_path if proof is not None else None
            if proof_text_path:
                attachments.append({
                    "name": Path(proof_text_path).name,
                    "contentType": "text/plain",
                    "path": proof_text_path,
                })
            trace_path = trace.trace_path
            if trace_path:
                attachments.append({
                    "name": Path(trace_path).name,
                    "contentType": "application/json",
                    "path": trace_path,
                })

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

        path = output_dir / "ctrf-report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ctrf_report, indent=2))


def _escape_markdown_inline(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("*", r"\*")
        .replace("_", r"\_")
        .replace("[", r"\[")
        .replace("]", r"\]")
        .replace("`", r"\`")
        .replace("<", r"\<")
        .replace(">", r"\>")
        .replace("|", r"\|")
    )


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


def _render_claim_block(*, source_line: ParsedClaimLine, claim_result: Any, line_ending: str) -> str:
    marker = "x" if claim_result.status == "passed" else " "
    lines = [f"{source_line.bullet} [{marker}] {source_line.claim}"]
    if claim_result.status != "passed":
        lines.append(_render_detail_line("  ", "Status", claim_result.status))
        lines.append(_render_detail_line("  ", "Finding", _collapse_whitespace(claim_result.finding)))
    return line_ending.join(lines) + line_ending


def _render_summary_section(run_result: RunResult) -> str:
    lines = ["## Summary", "", f"Run summary: {run_result.summary}"]
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
        marker = "x" if claim_result.status == "passed" else " "
        lines.append(f"- [{marker}] {claim_result.claim}")
        if claim_result.status != "passed":
            lines.append(f"  Status: {claim_result.status}")
            lines.append(f"  Finding: {_escape_markdown_inline(_collapse_whitespace(claim_result.finding))}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"Run summary: {run_result.summary}")
    return "\n".join(lines) + "\n"


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

    rendered_lines: list[str] = []
    for index, line in enumerate(source_lines):
        replacement = rendered_by_index.get(index)
        if replacement is None:
            rendered_lines.append(line)
            continue
        rendered_lines.append(replacement)

    if rendered_lines and not rendered_lines[-1].endswith(("\n", "\r")):
        rendered_lines.append("\n")

    if len(run_result.results) > len(claims_file.lines):
        rendered_lines.append("\n## Additional Results\n\n")
        for claim_result in run_result.results[len(claims_file.lines) :]:
            marker = "x" if claim_result.status == "passed" else " "
            rendered_lines.append(f"- [{marker}] {claim_result.claim}\n")
            if claim_result.status != "passed":
                rendered_lines.append(f"  - Status: {claim_result.status}\n")
                rendered_lines.append(
                    f"  - Finding: {_escape_markdown_inline(_collapse_whitespace(claim_result.finding))}\n"
                )

    rendered_lines.append(_render_summary_section(run_result))
    return "".join(rendered_lines)


class MarkdownReporter:
    """Writes a Markdown report that can annotate the original claims file."""

    name: str = "markdown"

    def write(self, run_result: RunResult, output_dir: Path, *, claims_file: ParsedClaimsFile | None = None) -> None:
        if claims_file is None:
            content = _render_synthesized_markdown(run_result)
        else:
            content = _render_annotated_source_markdown(run_result, claims_file)

        path = output_dir / "report.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


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
