"""Reporter abstraction for writing run results in different formats."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Protocol

from frontend_visualqa.schemas import RunResult


class Reporter(Protocol):
    """Interface for result reporters."""

    @property
    def name(self) -> str: ...

    def write(self, run_result: RunResult, output_dir: Path) -> None: ...


class NativeReporter:
    """Writes the full RunResult as run_result.json (the domain-specific schema)."""

    name: str = "native"

    def write(self, run_result: RunResult, output_dir: Path) -> None:
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

    def write(self, run_result: RunResult, output_dir: Path) -> None:
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

        path = output_dir / "ctrf-report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ctrf_report, indent=2))


_REPORTERS: dict[str, type[Reporter]] = {
    "native": NativeReporter,
    "ctrf": CTRFReporter,
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
