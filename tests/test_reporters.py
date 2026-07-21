from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fakes import assert_claim_result_payload_shape, import_or_skip, make_claim_result, simple_proof
from frontend_visualqa.claim_parser import ParsedClaimLine, ParsedClaimsFile, parse_claims_file
from frontend_visualqa.schemas import RunResult, ViewportConfig


def _import_reporters_module():
    return import_or_skip("frontend_visualqa.reporters")


def _write_ctrf_report(reporter: Any, run_result: RunResult, tmp_path: Path) -> dict[str, Any]:
    """Write *run_result* via *reporter* and return the parsed ctrf-report.json.

    Every CTRF reporter test below repeated this identical write-then-reload pair
    (``reporter.write(...)`` followed by ``json.loads((tmp_path / "ctrf-report.json").read_text())``),
    differing only in which run_result was written and which fields were asserted on the
    parsed data. This is the shared helper they delegate to now.
    """
    reporter.write(run_result, tmp_path)
    return json.loads((tmp_path / "ctrf-report.json").read_text())


def _sample_run_result(artifacts_dir: str) -> RunResult:
    viewport = ViewportConfig()
    return RunResult(
        overall_status="completed",
        session_key="default",
        run_name="dashboard-ci",
        results=[
            make_claim_result(
                claim="The heading reads 'Dashboard'",
                status="passed",
                finding="Visible heading matched 'Dashboard'.",
                url="http://localhost:3000/dashboard",
                viewport=viewport,
                proof=simple_proof("artifacts/run-001/claim-01/step-00.webp"),
                trace={
                    "steps_taken": 0,
                    "wrong_page_recovered": False,
                    "screenshot_paths": ["artifacts/run-001/claim-01/step-00.webp"],
                    "actions": [],
                    "trace_path": None,
                },
            ),
            make_claim_result(
                claim="The progress bar shows 100%",
                status="failed",
                finding="Progress bar shows 65%, not 100%.",
                url="http://localhost:3000/dashboard",
                viewport=viewport,
                proof={
                    "screenshot_path": "artifacts/run-001/claim-02/step-01.webp",
                    "step": 1,
                    "after_action": "goto_url(\"http://localhost:3000/dashboard/quota\")",
                    "text": "Visible text included '65%'.",
                    "text_path": "artifacts/run-001/claim-02/step-01.txt",
                },
                trace={
                    "steps_taken": 1,
                    "wrong_page_recovered": False,
                    "screenshot_paths": [
                        "artifacts/run-001/claim-02/step-00.webp",
                        "artifacts/run-001/claim-02/step-01.webp",
                    ],
                    "actions": ["goto_url(\"http://localhost:3000/dashboard/quota\")"],
                    "trace_path": "artifacts/run-001/claim-02/trace.json",
                },
            ),
        ],
        summary="1/2 claims passed. 1 failed.",
        artifacts_dir=artifacts_dir,
    )


def _sample_ctrf_report(tmp_path: Path) -> dict[str, Any]:
    """Write the sample run result via a fresh ``CTRFReporter`` and return the parsed report.

    Five CTRF reporter tests each repeated this identical
    ``module -> reporter -> run_result -> data`` arrange block, differing only in which
    fields they asserted on the returned ``data``. This is the shared helper they
    delegate to now, matching the ``_write_ctrf_report`` write-then-reload convention above.
    """
    module = _import_reporters_module()
    reporter = module.CTRFReporter()
    run_result = _sample_run_result(str(tmp_path))
    return _write_ctrf_report(reporter, run_result, tmp_path)


def _duplicate_claim_run_result(artifacts_dir: str) -> RunResult:
    viewport = ViewportConfig()
    return RunResult(
        overall_status="completed",
        session_key="default",
        run_name="duplicate-claims",
        results=[
            make_claim_result(
                claim="The heading reads 'Dashboard'",
                status="passed",
                finding="Visible heading matched 'Dashboard'.",
                url="http://localhost:3000/dashboard",
                viewport=viewport,
                proof=simple_proof("artifacts/run-002/claim-01/step-00.webp"),
                trace={
                    "steps_taken": 0,
                    "wrong_page_recovered": False,
                    "screenshot_paths": ["artifacts/run-002/claim-01/step-00.webp"],
                    "actions": [],
                    "trace_path": None,
                },
            ),
            make_claim_result(
                claim="The heading reads 'Dashboard'",
                status="failed",
                finding="The second heading is missing.",
                url="http://localhost:3000/dashboard",
                viewport=viewport,
                proof=simple_proof("artifacts/run-002/claim-02/step-01.webp", step=1),
                trace={
                    "steps_taken": 1,
                    "wrong_page_recovered": False,
                    "screenshot_paths": ["artifacts/run-002/claim-02/step-01.webp"],
                    "actions": [],
                    "trace_path": None,
                },
            ),
        ],
        summary="1/2 claims passed. 1 failed.",
        artifacts_dir=artifacts_dir,
    )


def test_native_reporter_writes_run_result_json(tmp_path: Path) -> None:
    module = _import_reporters_module()
    reporter = module.NativeReporter()
    run_result = _sample_run_result(str(tmp_path))
    reporter.write(run_result, tmp_path)
    output_path = tmp_path / "run_result.json"
    assert output_path.exists()
    data = json.loads(output_path.read_text())
    assert data["overall_status"] == "completed"
    assert data["run_name"] == "dashboard-ci"
    first_result = data["results"][0]
    second_result = data["results"][1]
    assert_claim_result_payload_shape(first_result)
    assert_claim_result_payload_shape(second_result)
    assert first_result["status"] == "passed"
    assert second_result["status"] == "failed"
    assert first_result["finding"] == "Visible heading matched 'Dashboard'."
    assert second_result["proof"]["text"] == "Visible text included '65%'."
    assert second_result["proof"]["text_path"] == "artifacts/run-001/claim-02/step-01.txt"
    assert first_result["page"]["url"] == "http://localhost:3000/dashboard"
    assert first_result["trace"]["wrong_page_recovered"] is False
    assert second_result["trace"]["actions"] == ["goto_url(\"http://localhost:3000/dashboard/quota\")"]


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


def test_get_reporters_returns_markdown_reporter() -> None:
    module = _import_reporters_module()
    reporters = module.get_reporters(["markdown"])
    assert len(reporters) == 1
    assert reporters[0].name == "markdown"


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
    data = _sample_ctrf_report(tmp_path)
    assert (tmp_path / "ctrf-report.json").exists()
    # Required CTRF root fields
    assert data["reportFormat"] == "CTRF"
    assert "specVersion" in data
    # Top-level structure
    results = data["results"]
    assert results["tool"]["name"] == "frontend-visualqa"
    assert results["extra"]["runName"] == "dashboard-ci"
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
    data = _write_ctrf_report(reporter, run_result, tmp_path)
    summary = data["results"]["summary"]
    assert summary["start"] == 1710000000000
    assert summary["stop"] == 1710000005500


def test_ctrf_reporter_maps_inconclusive_and_not_testable(tmp_path: Path) -> None:
    module = _import_reporters_module()
    reporter = module.CTRFReporter()
    viewport = ViewportConfig()
    run_result = RunResult(
        overall_status="completed",
        session_key="default",
        run_name=None,
        results=[
            make_claim_result(
                claim="Inconclusive claim",
                status="inconclusive",
                finding="Could not determine.",
                url="http://localhost:3000",
                viewport=viewport,
            ),
            make_claim_result(
                claim="Not testable claim",
                status="not_testable",
                finding="Server was down.",
                url="http://localhost:3000",
                viewport=viewport,
            ),
        ],
        summary="0/2 claims passed. 2 inconclusive.",
        artifacts_dir=str(tmp_path),
    )
    data = _write_ctrf_report(reporter, run_result, tmp_path)
    tests = data["results"]["tests"]
    assert "extra" not in data["results"]
    assert tests[0]["status"] == "other"
    assert tests[0]["rawStatus"] == "inconclusive"
    assert tests[1]["status"] == "skipped"
    assert tests[1]["rawStatus"] == "not_testable"


def test_ctrf_reporter_includes_extra_fields(tmp_path: Path) -> None:
    data = _sample_ctrf_report(tmp_path)
    t1 = data["results"]["tests"][1]
    extra = t1["extra"]
    assert extra["claimResult"]["page"]["url"] == "http://localhost:3000/dashboard"
    assert extra["claimResult"]["trace"]["wrong_page_recovered"] is False
    assert extra["claimResult"]["trace"]["steps_taken"] == 1
    assert extra["claimResult"]["page"]["viewport"] == {"width": 1280, "height": 800, "device_scale_factor": 1.0}
    assert extra["claimResult"]["trace"]["actions"] == ["goto_url(\"http://localhost:3000/dashboard/quota\")"]


def test_ctrf_reporter_includes_screenshots_as_attachments(tmp_path: Path) -> None:
    data = _sample_ctrf_report(tmp_path)
    t0 = data["results"]["tests"][0]
    assert "attachments" in t0
    assert len(t0["attachments"]) == 1
    assert t0["attachments"][0]["name"] == "step-00.webp"
    assert t0["attachments"][0]["contentType"] == "image/webp"


def test_ctrf_reporter_includes_trace_path_as_attachment(tmp_path: Path) -> None:
    data = _sample_ctrf_report(tmp_path)
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


def test_markdown_reporter_name() -> None:
    module = _import_reporters_module()
    reporter = module.MarkdownReporter()
    assert reporter.name == "markdown"


def test_markdown_reporter_annotates_source_markdown_and_preserves_non_claim_lines(tmp_path: Path) -> None:
    module = _import_reporters_module()
    reporter = module.MarkdownReporter()
    viewport = ViewportConfig()
    run_result = _duplicate_claim_run_result(str(tmp_path))
    source = ParsedClaimsFile(
        source_path=tmp_path / "claims.md",
        source_content=(
            "# Dashboard checks\n"
            "\n"
            "A note above the claims.\n"
            "\n"
            "- The heading reads 'Dashboard'\n"
            "  - Nested note that should stay untouched\n"
            "- The heading reads 'Dashboard'\n"
            "\n"
            "Trailing note.\n"
        ),
        lines=(
            ParsedClaimLine(line_index=4, bullet="-", claim="The heading reads 'Dashboard'"),
            ParsedClaimLine(line_index=6, bullet="-", claim="The heading reads 'Dashboard'"),
        ),
    )

    reporter.write(run_result, tmp_path, claims_file=source)

    output_path = tmp_path / "report.md"
    assert output_path.exists()
    rendered = output_path.read_text()
    assert "# Dashboard checks" in rendered
    assert "A note above the claims." in rendered
    assert "Trailing note." in rendered
    assert "  - Nested note that should stay untouched" in rendered
    assert rendered.count("- [x] The heading reads 'Dashboard'") == 1
    assert rendered.count("- [ ] The heading reads 'Dashboard'") == 1
    assert "  Status: failed" in rendered
    assert "  Finding: The second heading is missing." in rendered
    assert "## Summary" in rendered
    assert "Run summary: 1/2 claims passed. 1 failed." in rendered
    assert run_result.results[0].page.viewport == viewport


def test_markdown_reporter_preserves_navigation_hint_metadata_when_reannotated(tmp_path: Path) -> None:
    module = _import_reporters_module()
    reporter = module.MarkdownReporter()
    viewport = ViewportConfig()
    run_result = RunResult(
        overall_status="completed",
        session_key="default",
        run_name="login-flow",
        results=[
            make_claim_result(
                claim='After logging in, the dashboard shows "Welcome back, Developer"',
                status="failed",
                finding="The page is still on the login screen.",
                url="http://localhost:3000/login",
                viewport=viewport,
            ),
            make_claim_result(
                claim="The API Calls Today stat card shows the value 1,247",
                status="passed",
                finding="The stat card shows 1,247.",
                url="http://localhost:3000/dashboard",
                viewport=viewport,
            ),
        ],
        summary="1/2 claims passed. 1 failed.",
        artifacts_dir=str(tmp_path),
    )
    source = ParsedClaimsFile(
        source_path=tmp_path / "claims.md",
        source_content=(
            "# Dashboard checks\n"
            "\n"
            '- After logging in, the dashboard shows "Welcome back, Developer"\n'
            '  - navigation_hint: Type "test@yutori.com" in the email field, type "password123" in the password field, then click Continue.\n'
            "\n"
            "- The API Calls Today stat card shows the value 1,247\n"
        ),
        lines=(
            ParsedClaimLine(
                line_index=2,
                bullet="-",
                claim='After logging in, the dashboard shows "Welcome back, Developer"',
                navigation_hint='Type "test@yutori.com" in the email field, type "password123" in the password field, then click Continue.',
            ),
            ParsedClaimLine(
                line_index=5,
                bullet="-",
                claim="The API Calls Today stat card shows the value 1,247",
            ),
        ),
    )

    reporter.write(run_result, tmp_path, claims_file=source)

    rendered = (tmp_path / "report.md").read_text()
    assert "  - navigation_hint: Type \"test@yutori.com\" in the email field" in rendered
    assert "Status: failed" in rendered
    reparsed = parse_claims_file(tmp_path / "report.md")
    assert reparsed.claims == source.claims
    assert reparsed.lines[0].navigation_hint == source.lines[0].navigation_hint
    assert reparsed.lines[1].navigation_hint is None


def test_markdown_reporter_output_is_rerunnable_as_claim_input(tmp_path: Path) -> None:
    module = _import_reporters_module()
    reporter = module.MarkdownReporter()
    run_result = _duplicate_claim_run_result(str(tmp_path))
    source_path = tmp_path / "claims.md"
    source_path.write_text(
        "# Dashboard checks\n\n- The heading reads 'Dashboard'\n- The heading reads 'Dashboard'\n",
        encoding="utf-8",
    )
    claims_file = parse_claims_file(source_path)

    reporter.write(run_result, tmp_path, claims_file=claims_file)

    reparsed = parse_claims_file(tmp_path / "report.md")
    assert reparsed.claims == claims_file.claims


def test_markdown_reporter_re_annotation_strips_stale_details_and_summary(tmp_path: Path) -> None:
    """Re-annotating an already-annotated file should not accumulate stale detail lines or duplicate summaries."""
    module = _import_reporters_module()
    reporter = module.MarkdownReporter()
    viewport = ViewportConfig()

    # First run: one claim fails
    run1_result = RunResult(
        overall_status="completed",
        session_key="default",
        run_name=None,
        results=[
            make_claim_result(
                claim="The heading reads 'Dashboard'",
                status="failed",
                finding="Heading says 'Home' instead.",
                url="http://localhost:3000",
                viewport=viewport,
            ),
        ],
        summary="0/1 claims passed. 1 failed.",
        artifacts_dir=str(tmp_path),
    )

    source_path = tmp_path / "claims.md"
    source_path.write_text("# Checks\n\n- The heading reads 'Dashboard'\n", encoding="utf-8")
    claims_file1 = parse_claims_file(source_path)
    out1 = tmp_path / "round1"
    out1.mkdir()
    reporter.write(run1_result, out1, claims_file=claims_file1)

    round1_report = out1 / "report.md"
    round1_text = round1_report.read_text()
    assert round1_text.count("## Summary") == 1
    assert "  Status: failed" in round1_text

    # Second run: same claim now passes — reparse the annotated output
    claims_file2 = parse_claims_file(round1_report)
    assert claims_file2.claims == ["The heading reads 'Dashboard'"]

    run2_result = RunResult(
        overall_status="completed",
        session_key="default",
        run_name=None,
        results=[
            make_claim_result(
                claim="The heading reads 'Dashboard'",
                status="passed",
                finding="Heading matches.",
                url="http://localhost:3000",
                viewport=viewport,
            ),
        ],
        summary="1/1 claims passed.",
        artifacts_dir=str(tmp_path),
    )

    out2 = tmp_path / "round2"
    out2.mkdir()
    reporter.write(run2_result, out2, claims_file=claims_file2)

    round2_text = (out2 / "report.md").read_text()

    # No stale detail lines from the prior failed run
    assert "Status: failed" not in round2_text
    assert "Heading says" not in round2_text
    # Exactly one summary section
    assert round2_text.count("## Summary") == 1
    assert "1/1 claims passed." in round2_text
    # The claim is now passing
    assert "- [x] The heading reads 'Dashboard'" in round2_text


def test_markdown_reporter_strips_legacy_unmarked_annotations_on_rerun(tmp_path: Path) -> None:
    module = _import_reporters_module()
    reporter = module.MarkdownReporter()
    viewport = ViewportConfig()
    legacy_report = tmp_path / "legacy-report.md"
    legacy_report.write_text(
        (
            "# Checks\n\n"
            "- [ ] The heading reads 'Dashboard'\n"
            "  Status: failed\n"
            "  Finding: Heading says 'Home' instead.\n"
            "\n"
            "## Summary\n\n"
            "Run summary: 0/1 claims passed. 1 failed.\n"
        ),
        encoding="utf-8",
    )
    claims_file = parse_claims_file(legacy_report)

    run_result = RunResult(
        overall_status="completed",
        session_key="default",
        run_name=None,
        results=[
            make_claim_result(
                claim="The heading reads 'Dashboard'",
                status="passed",
                finding="Heading matches.",
                url="http://localhost:3000",
                viewport=viewport,
            ),
        ],
        summary="1/1 claims passed.",
        artifacts_dir=str(tmp_path),
    )

    reporter.write(run_result, tmp_path, claims_file=claims_file)

    rendered = (tmp_path / "report.md").read_text()
    assert "Status: failed" not in rendered
    assert "Heading says 'Home' instead." not in rendered
    assert rendered.count("## Summary") == 1
    assert "- [x] The heading reads 'Dashboard'" in rendered


def test_markdown_reporter_formats_additional_results_like_normal_claim_blocks(tmp_path: Path) -> None:
    module = _import_reporters_module()
    reporter = module.MarkdownReporter()
    run_result = _sample_run_result(str(tmp_path))
    source = ParsedClaimsFile(
        source_path=tmp_path / "claims.md",
        source_content="- The heading reads 'Dashboard'\n",
        lines=(ParsedClaimLine(line_index=0, bullet="-", claim="The heading reads 'Dashboard'"),),
    )

    reporter.write(run_result, tmp_path, claims_file=source)

    rendered = (tmp_path / "report.md").read_text()
    assert "## Additional Results" in rendered
    assert "- [ ] The progress bar shows 100%" in rendered
    assert "  Status: failed" in rendered
    assert "  Finding: Progress bar shows 65%, not 100%." in rendered
    assert "  - Status:" not in rendered
    assert "  - Finding:" not in rendered


def test_markdown_reporter_synthesizes_markdown_without_source(tmp_path: Path) -> None:
    module = _import_reporters_module()
    reporter = module.MarkdownReporter()
    run_result = _sample_run_result(str(tmp_path))

    reporter.write(run_result, tmp_path)

    rendered = (tmp_path / "report.md").read_text()
    assert "# frontend-visualqa report" in rendered
    assert "Run: dashboard-ci" in rendered
    assert "Artifacts: " in rendered
    assert "## Claims" in rendered
    assert "- [x] The heading reads 'Dashboard'" in rendered
    assert "- [ ] The progress bar shows 100%" in rendered
    assert "  Status: failed" in rendered
    assert "  Finding: Progress bar shows 65%, not 100%." in rendered
    assert "## Summary" in rendered
    assert "Run summary: 1/2 claims passed. 1 failed." in rendered


def test_markdown_reporter_synthesized_output_is_rerunnable(tmp_path: Path) -> None:
    module = _import_reporters_module()
    reporter = module.MarkdownReporter()
    run_result = _sample_run_result(str(tmp_path))

    reporter.write(run_result, tmp_path)

    reparsed = parse_claims_file(tmp_path / "report.md")
    assert reparsed.claims == [r.claim for r in run_result.results]


def test_ctrf_output_validates_against_official_schema(tmp_path: Path) -> None:
    """Validate CTRF output against the vendored official JSON schema."""
    try:
        import jsonschema
    except ImportError:
        pytest.skip("jsonschema not installed")
    schema_path = Path(__file__).parent / "fixtures" / "ctrf.schema.json"
    assert schema_path.exists(), "Vendored CTRF schema missing -- expected at tests/fixtures/ctrf.schema.json"
    schema = json.loads(schema_path.read_text())
    data = _sample_ctrf_report(tmp_path)
    jsonschema.validate(instance=data, schema=schema)
