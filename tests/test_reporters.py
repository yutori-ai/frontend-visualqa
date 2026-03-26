from __future__ import annotations

import json
from pathlib import Path

import pytest

from frontend_visualqa.schemas import ClaimResult, RunResult, ViewportConfig


def _import_reporters_module():
    import importlib
    try:
        return importlib.import_module("frontend_visualqa.reporters")
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("frontend_visualqa"):
            pytest.skip("frontend_visualqa.reporters is not implemented yet")
        raise


def _sample_run_result(artifacts_dir: str) -> RunResult:
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1.0)
    return RunResult(
        overall_status="completed",
        session_key="default",
        results=[
            ClaimResult(
                claim="The heading reads 'Dashboard'",
                status="passed",
                finding="Visible heading matched 'Dashboard'.",
                proof={
                    "screenshot_path": "artifacts/run-001/claim-01/step-00-initial.webp",
                    "step": 0,
                    "after_action": None,
                    "text": "Visible heading matched 'Dashboard'.",
                    "text_path": "artifacts/run-001/claim-01/step-00-initial.txt",
                },
                page={"url": "http://localhost:3000/dashboard", "viewport": viewport},
                trace={
                    "steps_taken": 0,
                    "wrong_page_recovered": False,
                    "screenshot_paths": ["artifacts/run-001/claim-01/step-00-initial.webp"],
                    "actions": [],
                    "events": [],
                    "trace_path": None,
                },
            ),
            ClaimResult(
                claim="The progress bar shows 100%",
                status="failed",
                finding="Progress bar shows 65%, not 100%.",
                proof={
                    "screenshot_path": "artifacts/run-001/claim-02/step-01.webp",
                    "step": 1,
                    "after_action": "extract_elements()",
                    "text": "Visible text included '65%'.",
                    "text_path": "artifacts/run-001/claim-02/step-01.txt",
                },
                page={"url": "http://localhost:3000/dashboard", "viewport": viewport},
                trace={
                    "steps_taken": 1,
                    "wrong_page_recovered": False,
                    "screenshot_paths": [
                        "artifacts/run-001/claim-02/step-00-initial.webp",
                        "artifacts/run-001/claim-02/step-01.webp",
                    ],
                    "actions": ["extract_elements()"],
                    "events": [],
                    "trace_path": "artifacts/run-001/claim-02/trace.json",
                },
            ),
        ],
        summary="1/2 claims passed. 1 failed.",
        artifacts_dir=artifacts_dir,
    )


def _assert_claim_result_payload_shape(result: dict[str, object]) -> None:
    assert set(result) == {"claim", "status", "finding", "proof", "page", "trace"}

    proof = result["proof"]
    assert proof is not None
    assert set(proof) == {"screenshot_path", "step", "after_action", "text", "text_path"}

    page = result["page"]
    assert isinstance(page, dict)
    assert set(page) == {"url", "viewport"}
    viewport = page["viewport"]
    assert isinstance(viewport, dict)
    assert set(viewport) == {"width", "height", "device_scale_factor"}

    trace = result["trace"]
    assert isinstance(trace, dict)
    assert set(trace) == {"steps_taken", "wrong_page_recovered", "screenshot_paths", "actions", "events", "trace_path"}


def test_native_reporter_writes_run_result_json(tmp_path: Path) -> None:
    module = _import_reporters_module()
    reporter = module.NativeReporter()
    run_result = _sample_run_result(str(tmp_path))
    reporter.write(run_result, tmp_path)
    output_path = tmp_path / "run_result.json"
    assert output_path.exists()
    data = json.loads(output_path.read_text())
    assert data["overall_status"] == "completed"
    first_result = data["results"][0]
    second_result = data["results"][1]
    _assert_claim_result_payload_shape(first_result)
    _assert_claim_result_payload_shape(second_result)
    assert first_result["status"] == "passed"
    assert second_result["status"] == "failed"
    assert first_result["finding"] == "Visible heading matched 'Dashboard'."
    assert second_result["proof"]["text"] == "Visible text included '65%'."
    assert second_result["proof"]["text_path"] == "artifacts/run-001/claim-02/step-01.txt"
    assert first_result["page"]["url"] == "http://localhost:3000/dashboard"
    assert first_result["trace"]["wrong_page_recovered"] is False
    assert second_result["trace"]["actions"] == ["extract_elements()"]


def test_native_reporter_name() -> None:
    module = _import_reporters_module()
    reporter = module.NativeReporter()
    assert reporter.name == "native"


def test_get_reporters_returns_native_by_default() -> None:
    module = _import_reporters_module()
    reporters = module.get_reporters([])
    assert len(reporters) == 1
    assert reporters[0].name == "native"


def test_get_reporters_returns_requested_reporters() -> None:
    module = _import_reporters_module()
    reporters = module.get_reporters(["native"])
    names = [r.name for r in reporters]
    assert "native" in names
    assert len(reporters) == 1


def test_get_reporters_raises_on_unknown_reporter() -> None:
    module = _import_reporters_module()
    with pytest.raises(ValueError, match="unknown_reporter"):
        module.get_reporters(["unknown_reporter"])


def test_get_reporters_returns_native_and_ctrf() -> None:
    module = _import_reporters_module()
    reporters = module.get_reporters(["native", "ctrf"])
    names = [r.name for r in reporters]
    assert "native" in names
    assert "ctrf" in names
    assert len(reporters) == 2


def test_ctrf_reporter_writes_valid_ctrf_json(tmp_path: Path) -> None:
    module = _import_reporters_module()
    reporter = module.CTRFReporter()
    run_result = _sample_run_result(str(tmp_path))
    reporter.write(run_result, tmp_path)
    output_path = tmp_path / "ctrf-report.json"
    assert output_path.exists()
    data = json.loads(output_path.read_text())
    # Required CTRF root fields
    assert data["reportFormat"] == "CTRF"
    assert "specVersion" in data
    # Top-level structure
    results = data["results"]
    assert results["tool"]["name"] == "frontend-visualqa"
    # Summary
    summary = results["summary"]
    assert summary["tests"] == 2
    assert summary["passed"] == 1
    assert summary["failed"] == 1
    assert summary["pending"] == 0
    assert summary["skipped"] == 0
    assert summary["other"] == 0
    assert "start" in summary
    assert "stop" in summary
    # Individual tests
    tests = results["tests"]
    assert len(tests) == 2
    assert tests[0]["name"] == "The heading reads 'Dashboard'"
    assert tests[0]["status"] == "passed"
    assert tests[0]["duration"] >= 0
    assert tests[0]["message"] == "Visible heading matched 'Dashboard'."
    assert tests[1]["name"] == "The progress bar shows 100%"
    assert tests[1]["status"] == "failed"
    assert tests[1]["message"] == "Progress bar shows 65%, not 100%."


def test_ctrf_reporter_uses_real_timing_when_available(tmp_path: Path) -> None:
    module = _import_reporters_module()
    reporter = module.CTRFReporter()
    run_result = _sample_run_result(str(tmp_path))
    run_result = run_result.model_copy(update={"started_at": 1710000000.0, "completed_at": 1710000005.5})
    reporter.write(run_result, tmp_path)
    data = json.loads((tmp_path / "ctrf-report.json").read_text())
    summary = data["results"]["summary"]
    assert summary["start"] == 1710000000000
    assert summary["stop"] == 1710000005500


def test_ctrf_reporter_maps_inconclusive_and_not_testable(tmp_path: Path) -> None:
    module = _import_reporters_module()
    reporter = module.CTRFReporter()
    viewport = ViewportConfig(width=1280, height=800, device_scale_factor=1.0)
    run_result = RunResult(
        overall_status="completed",
        session_key="default",
        results=[
            ClaimResult(
                claim="Inconclusive claim",
                status="inconclusive",
                finding="Could not determine.",
                proof=None,
                page={"url": "http://localhost:3000", "viewport": viewport},
                trace={
                    "steps_taken": 0,
                    "wrong_page_recovered": False,
                    "screenshot_paths": [],
                    "actions": [],
                    "events": [],
                    "trace_path": None,
                },
            ),
            ClaimResult(
                claim="Not testable claim",
                status="not_testable",
                finding="Server was down.",
                proof=None,
                page={"url": "http://localhost:3000", "viewport": viewport},
                trace={
                    "steps_taken": 0,
                    "wrong_page_recovered": False,
                    "screenshot_paths": [],
                    "actions": [],
                    "events": [],
                    "trace_path": None,
                },
            ),
        ],
        summary="0/2 claims passed. 2 inconclusive.",
        artifacts_dir=str(tmp_path),
    )
    reporter.write(run_result, tmp_path)
    data = json.loads((tmp_path / "ctrf-report.json").read_text())
    tests = data["results"]["tests"]
    assert tests[0]["status"] == "other"
    assert tests[0]["rawStatus"] == "inconclusive"
    assert tests[1]["status"] == "skipped"
    assert tests[1]["rawStatus"] == "not_testable"


def test_ctrf_reporter_includes_extra_fields(tmp_path: Path) -> None:
    module = _import_reporters_module()
    reporter = module.CTRFReporter()
    run_result = _sample_run_result(str(tmp_path))
    reporter.write(run_result, tmp_path)
    data = json.loads((tmp_path / "ctrf-report.json").read_text())
    t1 = data["results"]["tests"][1]
    extra = t1["extra"]
    assert extra["claimResult"]["page"]["url"] == "http://localhost:3000/dashboard"
    assert extra["claimResult"]["trace"]["wrong_page_recovered"] is False
    assert extra["claimResult"]["trace"]["steps_taken"] == 1
    assert extra["claimResult"]["page"]["viewport"] == {"width": 1280, "height": 800, "device_scale_factor": 1.0}
    assert extra["claimResult"]["trace"]["actions"] == ["extract_elements()"]


def test_ctrf_reporter_includes_screenshots_as_attachments(tmp_path: Path) -> None:
    module = _import_reporters_module()
    reporter = module.CTRFReporter()
    run_result = _sample_run_result(str(tmp_path))
    reporter.write(run_result, tmp_path)
    data = json.loads((tmp_path / "ctrf-report.json").read_text())
    t0 = data["results"]["tests"][0]
    assert "attachments" in t0
    assert len(t0["attachments"]) == 2
    assert t0["attachments"][0]["name"] == "step-00-initial.webp"
    assert t0["attachments"][0]["contentType"] == "image/webp"
    assert t0["attachments"][1]["name"] == "step-00-initial.txt"
    assert t0["attachments"][1]["contentType"] == "text/plain"


def test_ctrf_reporter_includes_trace_path_as_attachment(tmp_path: Path) -> None:
    module = _import_reporters_module()
    reporter = module.CTRFReporter()
    run_result = _sample_run_result(str(tmp_path))
    reporter.write(run_result, tmp_path)
    data = json.loads((tmp_path / "ctrf-report.json").read_text())
    t1 = data["results"]["tests"][1]
    assert len(t1["attachments"]) == 4  # 2 screenshots + 1 proof text + 1 trace
    proof_text_attachment = t1["attachments"][-2]
    assert proof_text_attachment["name"] == "step-01.txt"
    assert proof_text_attachment["contentType"] == "text/plain"
    trace_attachment = t1["attachments"][-1]
    assert trace_attachment["name"] == "trace.json"
    assert trace_attachment["contentType"] == "application/json"


def test_ctrf_reporter_name() -> None:
    module = _import_reporters_module()
    reporter = module.CTRFReporter()
    assert reporter.name == "ctrf"


def test_ctrf_output_validates_against_official_schema(tmp_path: Path) -> None:
    """Validate CTRF output against the vendored official JSON schema."""
    try:
        import jsonschema
    except ImportError:
        pytest.skip("jsonschema not installed")
    schema_path = Path(__file__).parent / "fixtures" / "ctrf.schema.json"
    assert schema_path.exists(), "Vendored CTRF schema missing -- expected at tests/fixtures/ctrf.schema.json"
    schema = json.loads(schema_path.read_text())
    module = _import_reporters_module()
    reporter = module.CTRFReporter()
    run_result = _sample_run_result(str(tmp_path))
    reporter.write(run_result, tmp_path)
    data = json.loads((tmp_path / "ctrf-report.json").read_text())
    jsonschema.validate(instance=data, schema=schema)
