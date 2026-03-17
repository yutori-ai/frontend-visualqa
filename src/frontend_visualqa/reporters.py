"""Reporter abstraction for writing run results in different formats."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from frontend_visualqa.schemas import RunResult


@runtime_checkable
class Reporter(Protocol):
    """Interface for result reporters."""

    @property
    def name(self) -> str: ...

    def write(self, run_result: RunResult, output_dir: Path) -> None: ...


class NativeReporter:
    """Writes the full RunResult as run_result.json (the domain-specific schema)."""

    @property
    def name(self) -> str:
        return "native"

    def write(self, run_result: RunResult, output_dir: Path) -> None:
        path = Path(output_dir) / "run_result.json"
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

    @property
    def name(self) -> str:
        return "ctrf"

    def write(self, run_result: RunResult, output_dir: Path) -> None:
        now_ms = int(time.time() * 1000)
        ctrf_tests = []
        for claim_result in run_result.results:
            ctrf_status = _CTRF_STATUS_MAP.get(claim_result.status, "other")
            extra: dict[str, Any] = {
                "finalUrl": claim_result.final_url,
                "wrongPageRecovered": claim_result.wrong_page_recovered,
                "stepsTaken": claim_result.steps_taken,
                "viewport": claim_result.viewport.model_dump(mode="json"),
                "actionTrace": claim_result.action_trace,
            }
            if claim_result.status not in ("passed", "failed"):
                extra["nativeStatus"] = claim_result.status

            attachments = [
                {
                    "name": Path(screenshot_path).name,
                    "contentType": "image/webp",
                    "path": screenshot_path,
                }
                for screenshot_path in claim_result.screenshots
            ]
            if claim_result.trace_path:
                attachments.append({
                    "name": Path(claim_result.trace_path).name,
                    "contentType": "application/json",
                    "path": claim_result.trace_path,
                })

            ctrf_test: dict[str, Any] = {
                "name": claim_result.claim,
                "status": ctrf_status,
                "duration": 0,
                "message": claim_result.summary,
                "extra": extra,
            }
            if attachments:
                ctrf_test["attachments"] = attachments
            ctrf_tests.append(ctrf_test)

        summary_counts = {"passed": 0, "failed": 0, "pending": 0, "skipped": 0, "other": 0}
        for test in ctrf_tests:
            status = test["status"]
            if status in summary_counts:
                summary_counts[status] += 1

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

        path = Path(output_dir) / "ctrf-report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ctrf_report, indent=2))


_REPORTER_REGISTRY: dict[str, type[Reporter]] = {
    "native": NativeReporter,
    "ctrf": CTRFReporter,
}


def register_reporter(name: str, cls: type[Reporter]) -> None:
    """Register a reporter class by name."""
    _REPORTER_REGISTRY[name] = cls


def get_reporters(names: list[str]) -> list[Reporter]:
    """Instantiate reporters by name. Defaults to ['native'] if empty."""
    if not names:
        names = ["native"]
    reporters: list[Reporter] = []
    for reporter_name in names:
        cls = _REPORTER_REGISTRY.get(reporter_name)
        if cls is None:
            raise ValueError(
                f"Unknown reporter: {reporter_name!r}. "
                f"Available reporters: {sorted(_REPORTER_REGISTRY.keys())}"
            )
        reporters.append(cls())
    return reporters
