from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from frontend_visualqa.artifacts import RunArtifacts
from frontend_visualqa.schemas import ViewportConfig

from fakes import (
    FakeArtifactManager,
    FakeChoice,
    FakeFunction,
    FakeMessage,
    FakeNavigatorClient,
    FakeResponse,
    FakeToolCall,
    RecordingFakeOverlay,
    import_or_skip,
    instantiate_with_aliased_attrs,
    tool_call_message,
)


def _verdict_response(status: str, finding: str) -> FakeResponse:
    """Build a FakeResponse with parsed_json for a structured JSON verdict."""
    return FakeResponse(
        choices=[FakeChoice(message=FakeMessage(content=json.dumps({"status": status, "finding": finding})))],
        parsed_json={"status": status, "finding": finding},
    )


def _import_claim_verifier_module():
    return import_or_skip("frontend_visualqa.claim_verifier")


def _import_recovery_module():
    return import_or_skip("frontend_visualqa.recovery")


def _field(result: Any, name: str) -> Any:
    if isinstance(result, dict):
        return result[name]
    return getattr(result, name)


@dataclass
class FakePage:
    url: str


_EMPTY_VISUAL_STATE: dict[str, Any] = {
    "visibleHeadings": [],
    "visibleButtons": [],
    "buttonStates": [],
    "dialogTitles": [],
}


def _visual_state(**overrides: Any) -> dict[str, Any]:
    """Build a visual_state dict, defaulting unset keys to _EMPTY_VISUAL_STATE."""
    return {**_EMPTY_VISUAL_STATE, **overrides}


class EvaluatingPage(FakePage):
    def __init__(self, url: str, visual_state: dict[str, Any] | None = None) -> None:
        super().__init__(url=url)
        self.visual_state = visual_state if visual_state is not None else dict(_EMPTY_VISUAL_STATE)

    async def evaluate(self, _: str) -> dict[str, Any]:
        return self.visual_state


@dataclass
class PasswordFocusedPage(FakePage):
    """Fake page reporting a focused password input, for redaction tests."""

    async def evaluate(self, script: str) -> Any:
        if "activeElement" in script:
            return True
        return {}


@dataclass
class FakeSession:
    page: FakePage
    viewport: ViewportConfig


class FakeBrowserManager:
    def __init__(self) -> None:
        self.capture_calls = 0

    async def capture_screenshot(self, session: Any) -> bytes:
        del session
        self.capture_calls += 1
        return b"\x89PNGfake"


class FakeActionExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.overlays_seen: list[Any] = []

    async def execute_tool_call(self, session: FakeSession, tool_call: Any) -> Any:
        resolved_name = tool_call.function.name
        resolved_args = json.loads(tool_call.function.arguments or "{}")
        self.calls.append((resolved_name, resolved_args))
        self.overlays_seen.append(getattr(self, "overlay", None))
        if resolved_name == "goto_url":
            session.page.url = resolved_args["url"]
        return SimpleNamespace(
            trace=f"{resolved_name}({resolved_args})",
            output_text=None,
            current_url=session.page.url,
            success=True,
            counts_as_interaction=resolved_name not in {"find", "extract_elements"},
        )


class BlockingNavigatorClient(FakeNavigatorClient):
    async def create(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        json_schema: dict[str, Any] | None = None,
        already_trimmed: bool = False,
    ) -> FakeResponse:
        del already_trimmed
        self.calls.append({"messages": messages, "tools": tools or []})
        await asyncio.sleep(1.0)
        raise AssertionError("BlockingNavigatorClient should be cancelled before returning")


def _build_claim_verifier(
    module: Any,
    tmp_path: Path,
    responses: list[FakeMessage],
    *,
    browser_manager: Any | None = None,
    action_executor: Any | None = None,
    artifact_manager: Any | None = None,
    visualize: bool = False,
) -> tuple[Any, FakeNavigatorClient, FakeActionExecutor]:
    browser = browser_manager or FakeBrowserManager()
    action_executor = action_executor or FakeActionExecutor()
    artifacts = artifact_manager or FakeArtifactManager(tmp_path)
    navigator_client = FakeNavigatorClient(responses)

    verifier = instantiate_with_aliased_attrs(
        module.ClaimVerifier,
        {
            "browser_manager": browser,
            "browser": browser,
            "action_executor": action_executor,
            "artifact_manager": artifacts,
            "artifacts": artifacts,
            "navigator_client": navigator_client,
            "client": navigator_client,
        },
        visualize=visualize,
    )

    return verifier, navigator_client, action_executor


async def _call_verify(
    verifier: Any,
    *,
    page: FakePage,
    viewport: ViewportConfig,
    claim: str,
    url: str,
    navigation_hint: str | None,
    visualize: bool | None = None,
    max_steps: int = 2,
) -> Any:
    signature = inspect.signature(verifier.verify)
    kwargs: dict[str, Any] = {}
    run_dir = Path("/tmp/frontend-visualqa-test")
    run_dir.mkdir(parents=True, exist_ok=True)
    run = RunArtifacts(run_id="run-test", run_dir=run_dir)

    if "page" in signature.parameters:
        kwargs["page"] = page
    if "session" in signature.parameters:
        kwargs["session"] = FakeSession(page=page, viewport=viewport)
    if "claim" in signature.parameters:
        kwargs["claim"] = claim
    if "url" in signature.parameters:
        kwargs["url"] = url
    if "max_steps" in signature.parameters:
        kwargs["max_steps"] = max_steps
    if "navigation_hint" in signature.parameters:
        kwargs["navigation_hint"] = navigation_hint
    if "visualize" in signature.parameters and visualize is not None:
        kwargs["visualize"] = visualize
    if "viewport" in signature.parameters:
        kwargs["viewport"] = viewport
    if "claim_index" in signature.parameters:
        kwargs["claim_index"] = 1
    if "run" in signature.parameters:
        kwargs["run"] = run
    if "run_artifacts" in signature.parameters:
        kwargs["run_artifacts"] = run

    return await verifier.verify(**kwargs)


@pytest.mark.asyncio
async def test_claim_verifier_returns_structured_verdict_from_json_schema(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, navigator_client, action_executor = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            _verdict_response(status="passed", finding="The red button is visible in the hero panel."),
        ],
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/page"),
        viewport=ViewportConfig(),
        claim="The page has a red button",
        url="http://fixture.local/page",
        navigation_hint=None,
    )

    assert _field(result, "claim") == "The page has a red button"
    assert _field(result, "status") == "passed"
    assert "red button" in _field(result, "finding")
    assert _field(result, "page").url == "http://fixture.local/page"
    assert _field(result, "trace").steps_taken == 0
    assert _field(result, "proof").screenshot_path.endswith("step-00.webp")
    assert _field(result, "proof").step == 0
    assert _field(result, "proof").text is None
    assert _field(result, "trace").actions == []
    assert action_executor.calls == []
    assert navigator_client.calls
    assert navigator_client.calls[0]["tools"] == []


@pytest.mark.asyncio
async def test_claim_verifier_executes_actions_before_final_verdict(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, _, action_executor = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            tool_call_message(name="goto_url", arguments=json.dumps({"url": "http://fixture.local/modal"})),
            _verdict_response(status="passed", finding="The modal is now visible and titled Edit Task."),
        ],
    )
    page = FakePage(url="http://fixture.local/start")

    result = await _call_verify(
        verifier,
        page=page,
        viewport=ViewportConfig(),
        claim="The modal opens on click",
        url="http://fixture.local/modal",
        navigation_hint="Open the modal before deciding.",
    )

    assert action_executor.calls == [("goto_url", {"url": "http://fixture.local/modal"})]
    assert _field(result, "status") == "passed"
    assert _field(result, "page").url == "http://fixture.local/modal"
    assert _field(result, "trace").steps_taken >= 1
    assert _field(result, "trace").wrong_page_recovered is True
    assert _field(result, "trace").actions == ["goto_url({'url': 'http://fixture.local/modal'})"]
    assert _field(result, "proof").step == _field(result, "trace").steps_taken
    assert _field(result, "proof").after_action == "goto_url({'url': 'http://fixture.local/modal'})"
    assert _field(result, "proof").text is None


@pytest.mark.asyncio
async def test_claim_verifier_requires_an_action_before_accepting_a_verdict_with_navigation_hint(
    tmp_path: Path,
) -> None:
    module = _import_claim_verifier_module()
    verifier, navigator_client, action_executor = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            _verdict_response(status="failed", finding="The cart badge shows 2 items."),
            tool_call_message(
                name="goto_url", arguments=json.dumps({"url": "http://fixture.local/cart"}), call_id="tool-2"
            ),
            _verdict_response(status="passed", finding="The cart badge now shows 3 items."),
        ],
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/products"),
        viewport=ViewportConfig(),
        claim="The cart badge shows 3 items",
        url="http://fixture.local/products",
        navigation_hint="Click Add to Cart before deciding.",
        max_steps=3,
    )

    assert _field(result, "status") == "passed"
    assert action_executor.calls == [("goto_url", {"url": "http://fixture.local/cart"})]
    assert len(navigator_client.calls) == 3
    reminder_message = next(
        message
        for message in reversed(navigator_client.calls[1]["messages"])
        if message.get("role") == "user" and isinstance(message.get("content"), list)
    )
    assert "You have not followed the navigation hint yet." in reminder_message["content"][0]["text"]


@pytest.mark.asyncio
async def test_claim_verifier_does_not_treat_read_only_tools_as_navigation_interaction(
    tmp_path: Path,
) -> None:
    module = _import_claim_verifier_module()
    verifier, navigator_client, action_executor = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            tool_call_message(name="find", arguments=json.dumps({"text": "Cart"})),
            _verdict_response(status="failed", finding="The cart badge shows 2 items."),
            tool_call_message(
                name="goto_url", arguments=json.dumps({"url": "http://fixture.local/cart"}), call_id="tool-2"
            ),
            _verdict_response(status="passed", finding="The cart badge now shows 3 items."),
        ],
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/products"),
        viewport=ViewportConfig(),
        claim="The cart badge shows 3 items",
        url="http://fixture.local/products",
        navigation_hint="Click Add to Cart before deciding.",
        max_steps=3,
    )

    assert _field(result, "status") == "passed"
    assert action_executor.calls == [
        ("find", {"text": "Cart"}),
        ("goto_url", {"url": "http://fixture.local/cart"}),
    ]
    assert len(navigator_client.calls) == 4
    reminder_message = next(
        message
        for message in reversed(navigator_client.calls[2]["messages"])
        if message.get("role") == "user" and isinstance(message.get("content"), list)
    )
    assert "You have not followed the navigation hint yet." in reminder_message["content"][0]["text"]


@pytest.mark.asyncio
async def test_claim_verifier_reprompts_when_model_says_action_is_needed_but_records_inconclusive(
    tmp_path: Path,
) -> None:
    module = _import_claim_verifier_module()
    verifier, navigator_client, action_executor = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            _verdict_response(
                status="inconclusive",
                finding="I need to click on the product to open the detail page before I can verify this.",
            ),
            tool_call_message(
                name="goto_url", arguments=json.dumps({"url": "http://fixture.local/products/1"}), call_id="tool-2"
            ),
            _verdict_response(
                status="passed",
                finding="The product detail page shows Wireless Headphones Pro priced at $149.99.",
            ),
        ],
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/products"),
        viewport=ViewportConfig(),
        claim="The product detail page shows Wireless Headphones Pro priced at $149.99",
        url="http://fixture.local/products",
        navigation_hint=None,
    )

    assert _field(result, "status") == "passed"
    assert action_executor.calls == [("goto_url", {"url": "http://fixture.local/products/1"})]
    assert _field(result, "trace").wrong_page_recovered is True
    reminder_message = next(
        message
        for message in reversed(navigator_client.calls[1]["messages"])
        if message.get("role") == "user" and isinstance(message.get("content"), list)
    )
    assert "more browser interaction is needed" in reminder_message["content"][0]["text"]


def test_claim_verifier_accepts_visualize_flag(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier = module.ClaimVerifier(
        browser_manager=FakeBrowserManager(),
        artifact_manager=FakeArtifactManager(tmp_path),
        navigator_client=FakeNavigatorClient([]),
        visualize=True,
    )

    assert verifier._visualize is True


@pytest.mark.asyncio
async def test_claim_verifier_uses_overlay_lifecycle_when_visualize_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _import_claim_verifier_module()
    events: list[Any] = []
    fake_overlay = RecordingFakeOverlay(events)
    monkeypatch.setattr(module, "_create_overlay_controller", lambda page: fake_overlay)

    verifier, _, action_executor = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            tool_call_message(name="goto_url", arguments=json.dumps({"url": "http://fixture.local/modal"})),
            _verdict_response(status="passed", finding="The modal is now visible and titled Edit Task."),
        ],
        visualize=True,
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/start"),
        viewport=ViewportConfig(),
        claim="The modal opens on click",
        url="http://fixture.local/modal",
        navigation_hint="Open the modal before deciding.",
        visualize=True,
    )

    assert _field(result, "status") == "passed"
    assert events[0] == "claim_started"
    assert events.count("before_screenshot") == 1
    assert events.count("after_screenshot") == 1
    assert events.index("before_screenshot") > events.index("claim_started")
    after_index = events.index("after_screenshot")
    post_capture_status_index = next(
        index for index, event in enumerate(events) if index > after_index and event == ("set_status", "Analyzing")
    )
    assert post_capture_status_index > after_index
    assert events[-1] == "claim_ended"
    assert action_executor.calls == [("goto_url", {"url": "http://fixture.local/modal"})]
    assert action_executor.overlays_seen == [fake_overlay]
    assert action_executor.overlay is None


@pytest.mark.asyncio
async def test_claim_verifier_reprompts_after_plain_text_thought_and_continues(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, navigator_client, action_executor = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            FakeMessage(content="I should open the task detail page before deciding."),
            tool_call_message(name="goto_url", arguments=json.dumps({"url": "http://fixture.local/tasks/123"})),
            _verdict_response(status="passed", finding="The Task Details heading is visible."),
        ],
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/tasks"),
        viewport=ViewportConfig(),
        claim="The page title reads 'Task Details'",
        url="http://fixture.local/tasks",
        navigation_hint="Open the task detail page before deciding.",
    )

    assert _field(result, "status") == "passed"
    assert action_executor.calls == [("goto_url", {"url": "http://fixture.local/tasks/123"})]
    assert len(navigator_client.calls) == 3
    reminder_message = next(
        message
        for message in reversed(navigator_client.calls[1]["messages"])
        if message.get("role") == "user" and isinstance(message.get("content"), list)
    )
    reminder_text = reminder_message["content"][0]["text"]
    assert "Do not narrate your intent in plain text." in reminder_text


@pytest.mark.asyncio
async def test_claim_verifier_records_reasoning_events_and_shows_thought_for_tool_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _import_claim_verifier_module()
    overlay_events: list[Any] = []
    monkeypatch.setattr(module, "_create_overlay_controller", lambda page: RecordingFakeOverlay(overlay_events))
    reasoning = "Inspect the Save button before deciding."
    verifier, _, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            FakeMessage(
                content=reasoning,
                tool_calls=[
                    FakeToolCall(
                        id="tool-1",
                        function=FakeFunction(
                            name="goto_url", arguments=json.dumps({"url": "http://fixture.local/save"})
                        ),
                    )
                ],
            ),
            _verdict_response(status="passed", finding="The Save button is visible."),
        ],
        visualize=True,
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/page"),
        viewport=ViewportConfig(),
        claim="The Save button is visible",
        url="http://fixture.local/page",
        navigation_hint=None,
        visualize=True,
    )

    assert ("show_thought", reasoning) in overlay_events
    # The thought card must appear synced with its action: BEFORE the evidence
    # screenshot (which hides the whole overlay), so it lands on the action's own
    # page and the model never reads its own reasoning off the capture.
    first_thought = overlay_events.index(("show_thought", reasoning))
    first_before = overlay_events.index("before_screenshot")
    assert first_thought < first_before, (
        f"show_thought at index {first_thought} should come before before_screenshot at index {first_before}"
    )
    action_event, verdict_event = _field(result, "trace").events
    assert action_event.type == "action"
    assert action_event.reasoning == reasoning
    assert action_event.action == "goto_url"
    assert action_event.action_args == {"url": "http://fixture.local/save"}
    assert action_event.output_preview is None
    assert action_event.screenshot_path.endswith("step-01.webp")
    assert verdict_event.type == "verdict"
    assert verdict_event.reasoning is None
    assert verdict_event.verdict_source == "json_schema"
    assert verdict_event.raw_verdict_status == "passed"
    assert "Save button" in verdict_event.raw_finding
    assert verdict_event.verdict_status == "passed"
    assert "Save button" in verdict_event.finding
    trace_payload = json.loads(Path(_field(result, "trace").trace_path).read_text(encoding="utf-8"))
    assert [item["type"] for item in trace_payload] == ["action", "verdict"]
    assert trace_payload[0]["reasoning"] == reasoning
    assert trace_payload[1]["raw_verdict_status"] == "passed"
    assert "Save button" in trace_payload[1]["raw_finding"]
    assert trace_payload[1]["finding"] == verdict_event.finding


@pytest.mark.asyncio
async def test_claim_verifier_shows_post_capture_analysis_ui_after_action_screenshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _import_claim_verifier_module()
    overlay_events: list[Any] = []
    monkeypatch.setattr(module, "_create_overlay_controller", lambda page: RecordingFakeOverlay(overlay_events))
    reasoning = "Click into the form before deciding."
    verifier, _, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            FakeMessage(
                content=reasoning,
                tool_calls=[
                    FakeToolCall(
                        id="tool-1",
                        function=FakeFunction(
                            name="left_click",
                            arguments=json.dumps({"coordinates": [500, 250]}),
                        ),
                    )
                ],
            ),
            _verdict_response(status="passed", finding="The field is focused."),
        ],
        visualize=True,
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/form"),
        viewport=ViewportConfig(),
        claim="The first field is focused",
        url="http://fixture.local/form",
        navigation_hint=None,
        visualize=True,
    )

    assert _field(result, "status") == "passed"
    thought_index = overlay_events.index(("show_thought", reasoning))
    before_index = overlay_events.index("before_screenshot")
    assert thought_index < before_index


@pytest.mark.asyncio
async def test_claim_verifier_shows_thought_before_a_passive_first_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _import_claim_verifier_module()
    overlay_events: list[Any] = []
    monkeypatch.setattr(module, "_create_overlay_controller", lambda page: RecordingFakeOverlay(overlay_events))
    reasoning = "I need the form fields — let me extract the interactive elements."
    verifier, _, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            FakeMessage(
                content=reasoning,
                tool_calls=[
                    FakeToolCall(
                        id="tool-1",
                        function=FakeFunction(
                            name="extract_elements", arguments=json.dumps({"filter": "interactive"})
                        ),
                    )
                ],
            ),
            _verdict_response(status="passed", finding="The form is present."),
        ],
        visualize=True,
    )

    await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/page"),
        viewport=ViewportConfig(),
        claim="The form is present",
        url="http://fixture.local/page",
        navigation_hint=None,
        visualize=True,
    )

    # The turn's only tool is passive (extract_elements): the reasoning must still
    # be shown (before the evidence screenshot), not left as the prior turn's
    # stale capsule.
    assert ("show_thought", reasoning) in overlay_events
    thought_index = overlay_events.index(("show_thought", reasoning))
    before_index = overlay_events.index("before_screenshot")
    assert thought_index < before_index


@pytest.mark.asyncio
async def test_claim_verifier_does_not_show_thought_for_plain_text_turn_without_tool_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _import_claim_verifier_module()
    overlay_events: list[Any] = []
    monkeypatch.setattr(module, "_create_overlay_controller", lambda page: RecordingFakeOverlay(overlay_events))
    original_capture = module.ClaimVerifier._capture_evidence_screenshot

    async def instrumented_capture(self: Any, *args: Any, **kwargs: Any) -> Any:
        overlay_events.append("initial_screenshot")
        return await original_capture(self, *args, **kwargs)

    monkeypatch.setattr(module.ClaimVerifier, "_capture_evidence_screenshot", instrumented_capture)
    verifier, _, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            FakeMessage(content="I should inspect the page title before deciding."),
            _verdict_response(status="passed", finding="The page title reads Dashboard."),
        ],
        visualize=True,
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/page"),
        viewport=ViewportConfig(),
        claim="The page title reads 'Dashboard'",
        url="http://fixture.local/page",
        navigation_hint=None,
        visualize=True,
    )

    assert _field(result, "status") == "passed"
    assert overlay_events[0] == "initial_screenshot"
    assert overlay_events.index("claim_started") > overlay_events.index("initial_screenshot")
    assert "before_screenshot" not in overlay_events
    assert "after_screenshot" not in overlay_events
    assert not any(event[0] == "show_thought" for event in overlay_events if isinstance(event, tuple))
    assert len(_field(result, "trace").events) == 1
    verdict_event = _field(result, "trace").events[0]
    assert verdict_event.type == "verdict"
    assert verdict_event.reasoning is None
    assert verdict_event.verdict_source == "json_schema"


@pytest.mark.asyncio
async def test_claim_verifier_seeds_first_model_turn_with_current_url_and_screenshot(
    tmp_path: Path,
) -> None:
    module = _import_claim_verifier_module()
    verifier, navigator_client, action_executor = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            _verdict_response(status="passed", finding="Seeded."),
        ],
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/page"),
        viewport=ViewportConfig(),
        claim="The page title reads 'Dashboard'",
        url="http://fixture.local/page",
        navigation_hint=None,
    )

    assert _field(result, "status") == "passed"
    assert action_executor.calls == []
    first_call_messages = navigator_client.calls[0]["messages"]
    assert first_call_messages[0]["role"] == "user"
    first_content = first_call_messages[0]["content"]
    assert isinstance(first_content, list)
    text_parts = [
        part.get("text", "") for part in first_content if isinstance(part, dict) and part.get("type") == "text"
    ]
    assert any("Current URL:" in text for text in text_parts)
    assert not any("Verifier-owned bootstrap observation." in text for text in text_parts)
    assert any(isinstance(part, dict) and part.get("type") == "image_url" for part in first_content)
    assert _field(result, "proof").screenshot_path.endswith("step-00.webp")
    assert _field(result, "proof").step == 0
    assert _field(result, "proof").after_action is None
    assert _field(result, "proof").text is None
    assert _field(result, "trace").actions == []


@pytest.mark.asyncio
async def test_claim_verifier_records_json_schema_verdict_source(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, _, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            _verdict_response(status="passed", finding="The page title reads Dashboard."),
        ],
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/page"),
        viewport=ViewportConfig(),
        claim="The page title reads 'Dashboard'",
        url="http://fixture.local/page",
        navigation_hint=None,
    )

    verdict_event = _field(result, "trace").events[0]
    assert verdict_event.type == "verdict"
    assert verdict_event.verdict_source == "json_schema"


@pytest.mark.asyncio
async def test_claim_verifier_recovers_from_plain_text_with_json_verdict(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, _, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            FakeMessage(content="I should inspect the page."),
            FakeMessage(content="I still need more time."),
            FakeMessage(content="I cannot decide yet."),
            # force_stop path: model finally returns JSON verdict
            _verdict_response(status="inconclusive", finding="The model hit the step limit."),
        ],
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/page"),
        viewport=ViewportConfig(),
        claim="The page title reads 'Dashboard'",
        url="http://fixture.local/page",
        navigation_hint=None,
    )

    verdict_event = _field(result, "trace").events[0]
    assert verdict_event.type == "verdict"
    assert verdict_event.verdict_source == "json_schema"


@pytest.mark.asyncio
async def test_claim_verifier_preserves_tool_call_order_when_action_and_verdict_share_a_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _import_claim_verifier_module()
    overlay_events: list[Any] = []
    monkeypatch.setattr(module, "_create_overlay_controller", lambda page: RecordingFakeOverlay(overlay_events))
    reasoning = "Click the modal and then decide."
    verifier, _, action_executor = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            FakeMessage(
                content=reasoning,
                tool_calls=[
                    FakeToolCall(
                        id="tool-1",
                        function=FakeFunction(
                            name="goto_url",
                            arguments=json.dumps({"url": "http://fixture.local/modal"}),
                        ),
                    ),
                ],
            ),
            _verdict_response(status="passed", finding="The modal is visible."),
        ],
        visualize=True,
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/start"),
        viewport=ViewportConfig(),
        claim="The modal opens on click",
        url="http://fixture.local/modal",
        navigation_hint=None,
        visualize=True,
    )

    assert _field(result, "status") == "passed"
    assert action_executor.calls == [("goto_url", {"url": "http://fixture.local/modal"})]
    assert _field(result, "page").url == "http://fixture.local/modal"
    thought_index = overlay_events.index(("show_thought", reasoning))
    before_index = overlay_events.index("before_screenshot")
    assert thought_index < before_index
    assert overlay_events[-1] == "claim_ended"


@pytest.mark.asyncio
async def test_claim_verifier_downgrades_pass_when_button_grounding_disagrees(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, _, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            _verdict_response(
                status="passed",
                finding="The Show Save Confirmation button is visible in the header.",
            ),
        ],
    )

    result = await _call_verify(
        verifier,
        page=EvaluatingPage(
            url="http://fixture.local/page",
            visual_state=_visual_state(
                visibleHeadings=["Frontend Visual QA Playground"],
                visibleButtons=["Open Edit Task Modal", "Show Save Confirmation"],
                buttonStates=[
                    {"text": "Open Edit Task Modal", "fullyVisible": True},
                    {"text": "Show Save Confirmation", "fullyVisible": True},
                ],
            ),
        ),
        viewport=ViewportConfig(),
        claim="The Save button is visible without scrolling",
        url="http://fixture.local/page",
        navigation_hint=None,
    )

    assert _field(result, "status") == "failed"
    assert "No visible button label matched" in _field(result, "finding")


@pytest.mark.asyncio
async def test_claim_verifier_downgrades_pass_when_finding_contradicts_verdict(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, _, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            _verdict_response(
                status="passed",
                finding="The displayed subtotal does not equal the visible sale prices.",
            ),
        ],
    )

    result = await _call_verify(
        verifier,
        page=EvaluatingPage(
            url="http://fixture.local/cart",
        ),
        viewport=ViewportConfig(),
        claim="The cart subtotal is correct",
        url="http://fixture.local/cart",
        navigation_hint=None,
    )

    assert _field(result, "status") == "failed"
    assert "contradictory evidence" in _field(result, "finding")


@pytest.mark.asyncio
async def test_claim_verifier_downgrades_pass_to_inconclusive_when_finding_is_uncertain(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, _, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            _verdict_response(
                status="passed",
                finding="The screenshot cannot be definitively verified from the current evidence.",
            ),
        ],
    )

    result = await _call_verify(
        verifier,
        page=EvaluatingPage(
            url="http://fixture.local/page",
        ),
        viewport=ViewportConfig(),
        claim="The chart is visible",
        url="http://fixture.local/page",
        navigation_hint=None,
    )

    assert _field(result, "status") == "inconclusive"
    assert "evidence was inconclusive" in _field(result, "finding")


@pytest.mark.asyncio
async def test_claim_verifier_preserves_pass_for_negative_claims_with_negative_findings(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, _, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            _verdict_response(status="passed", finding="The Save button is not visible."),
        ],
    )

    result = await _call_verify(
        verifier,
        page=EvaluatingPage(
            url="http://fixture.local/page",
        ),
        viewport=ViewportConfig(),
        claim="The Save button is not visible",
        url="http://fixture.local/page",
        navigation_hint=None,
    )

    assert _field(result, "status") == "passed"
    assert _field(result, "finding") == "The Save button is not visible."


@pytest.mark.asyncio
async def test_claim_verifier_preserves_pass_for_incorrect_claims_with_confirming_findings(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, _, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            _verdict_response(
                status="passed",
                finding="The price is incorrect — it shows $279.98 instead of $229.98.",
            ),
        ],
    )

    result = await _call_verify(
        verifier,
        page=EvaluatingPage(
            url="http://fixture.local/cart",
        ),
        viewport=ViewportConfig(),
        claim="The price is incorrect",
        url="http://fixture.local/cart",
        navigation_hint=None,
    )

    assert _field(result, "status") == "passed"
    assert "incorrect" in _field(result, "finding")


@pytest.mark.asyncio
async def test_claim_verifier_still_downgrades_positive_error_state_claims_when_finding_contradicts_verdict(
    tmp_path: Path,
) -> None:
    module = _import_claim_verifier_module()
    verifier, _, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            _verdict_response(status="passed", finding="The error message is not visible."),
        ],
    )

    result = await _call_verify(
        verifier,
        page=EvaluatingPage(
            url="http://fixture.local/login",
        ),
        viewport=ViewportConfig(),
        claim="The error message is visible",
        url="http://fixture.local/login",
        navigation_hint=None,
    )

    assert _field(result, "status") == "failed"
    assert "contradictory evidence" in _field(result, "finding")


@pytest.mark.asyncio
async def test_claim_verifier_does_not_treat_ambiguous_ui_copy_as_inconclusive_evidence(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, _, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            _verdict_response(
                status="passed",
                finding='The label is ambiguous but reads "Total".',
            ),
        ],
    )

    result = await _call_verify(
        verifier,
        page=EvaluatingPage(
            url="http://fixture.local/checkout",
        ),
        viewport=ViewportConfig(),
        claim='The label reads "Total"',
        url="http://fixture.local/checkout",
        navigation_hint=None,
    )

    assert _field(result, "status") == "passed"
    assert _field(result, "finding") == 'The label is ambiguous but reads "Total".'


@pytest.mark.asyncio
async def test_claim_verifier_downgrades_partially_filled_progress_bar_claim(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, _, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            _verdict_response(
                status="passed",
                finding="The Monthly Quota progress bar appears completely filled.",
            ),
        ],
    )

    result = await _call_verify(
        verifier,
        page=EvaluatingPage(
            url="http://fixture.local/dashboard",
            visual_state=_visual_state(
                progressBars=[{"label": "Monthly Quota", "fillRatio": 0.65}],
            ),
        ),
        viewport=ViewportConfig(),
        claim="The Monthly Quota progress bar is completely filled",
        url="http://fixture.local/dashboard",
        navigation_hint=None,
    )

    assert _field(result, "status") == "failed"
    assert "65% filled" in _field(result, "finding")
    verdict_event = _field(result, "trace").events[0]
    assert verdict_event.type == "verdict"
    assert verdict_event.raw_verdict_status == "passed"
    assert verdict_event.raw_finding == "The Monthly Quota progress bar appears completely filled."
    assert verdict_event.verdict_status == "failed"
    assert "65% filled" in verdict_event.finding


@pytest.mark.asyncio
async def test_claim_verifier_converts_inconclusive_full_visibility_button_claim_to_fail(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, _, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            _verdict_response(
                status="inconclusive",
                finding="I could not tell whether the button was fully visible.",
            ),
        ],
    )

    result = await _call_verify(
        verifier,
        page=EvaluatingPage(
            url="http://fixture.local/settings",
            visual_state=_visual_state(
                visibleHeadings=["Workspace Settings"],
                visibleButtons=["Save"],
                buttonStates=[{"text": "Save", "fullyVisible": False}],
            ),
        ),
        viewport=ViewportConfig(),
        claim="The Save button is fully visible within its container",
        url="http://fixture.local/settings",
        navigation_hint=None,
    )

    assert _field(result, "status") == "failed"
    assert "not fully visible" in _field(result, "finding")


@pytest.mark.asyncio
async def test_claim_verifier_fuzzy_matches_button_with_decorative_chars_and_quotes(tmp_path: Path) -> None:
    """Fuzzy matching passes when claim label has quotes/descriptors and button text has decorative chars."""
    module = _import_claim_verifier_module()
    verifier, _, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            _verdict_response(status="passed", finding="Button visible."),
        ],
    )

    result = await _call_verify(
        verifier,
        page=EvaluatingPage(
            url="http://fixture.local/page",
            visual_state=_visual_state(
                visibleHeadings=["Page Title"],
                visibleButtons=["Select Priority \u25bc"],
                buttonStates=[{"text": "Select Priority \u25bc", "fullyVisible": True}],
            ),
        ),
        viewport=ViewportConfig(),
        claim="The 'Select Priority' dropdown button is visible",
        url="http://fixture.local/page",
        navigation_hint=None,
    )

    assert _field(result, "status") == "passed"
    assert "Select Priority" in _field(result, "finding")


@pytest.mark.asyncio
async def test_claim_verifier_skips_grounding_for_compound_claims(tmp_path: Path) -> None:
    """Compound claims with verbs in the label should not be caught by button grounding."""
    module = _import_claim_verifier_module()
    verifier, _, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            _verdict_response(status="passed", finding="Both conditions met."),
        ],
    )

    result = await _call_verify(
        verifier,
        page=EvaluatingPage(
            url="http://fixture.local/page",
        ),
        viewport=ViewportConfig(width=375, height=812, device_scale_factor=1),
        claim="The sidebar is hidden and a hamburger menu button is visible",
        url="http://fixture.local/page",
        navigation_hint=None,
    )

    # n1 said pass; grounding should NOT override because claim is compound
    assert _field(result, "status") == "passed"


@pytest.mark.asyncio
async def test_claim_verifier_reuses_trimmed_history_across_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _import_claim_verifier_module()
    verifier, navigator_client, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            tool_call_message(name="goto_url", arguments=json.dumps({"url": "http://fixture.local/modal"})),
            _verdict_response(
                status="passed",
                finding="The modal is now visible and titled Edit Task.",
            ),
        ],
    )
    trim_calls: list[dict[str, Any]] = []
    trimmed_payload = [{"role": "user", "content": [{"type": "text", "text": "trimmed"}]}]

    def fake_trim_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        trim_calls.append({"messages": messages})
        return trimmed_payload

    navigator_client.trim_messages = fake_trim_messages  # type: ignore[attr-defined]

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/start"),
        viewport=ViewportConfig(),
        claim="The modal opens on click",
        url="http://fixture.local/modal",
        navigation_hint="Open the modal before deciding.",
    )

    assert _field(result, "status") == "passed"
    assert len(trim_calls) >= 2
    assert trim_calls[0]["messages"][0]["content"][0]["text"] != "trimmed"
    assert trim_calls[1]["messages"][0]["content"][0]["text"] == "trimmed"
    assert navigator_client.calls[0]["messages"][0]["content"][0]["text"] == "trimmed"
    assert navigator_client.calls[1]["messages"][0]["content"][0]["text"] == "trimmed"


@pytest.mark.asyncio
async def test_claim_verifier_returns_inconclusive_json_verdict(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, _, action_executor = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            _verdict_response(
                status="inconclusive",
                finding="Need a human to decide from this screenshot.",
            ),
        ],
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/page"),
        viewport=ViewportConfig(),
        claim="The promotion banner feels too crowded",
        url="http://fixture.local/page",
        navigation_hint=None,
    )

    assert _field(result, "status") == "inconclusive"
    assert "Need a human" in _field(result, "finding")
    assert action_executor.calls == []
    verdict_event = _field(result, "trace").events[0]
    assert verdict_event.type == "verdict"
    assert verdict_event.verdict_source == "json_schema"


@pytest.mark.asyncio
async def test_claim_verifier_writes_trace_json_with_action_and_verdict_events(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()

    verifier, _, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            FakeMessage(
                content="Open the save page before deciding.",
                tool_calls=[
                    FakeToolCall(
                        id="tool-1",
                        function=FakeFunction(
                            name="goto_url", arguments=json.dumps({"url": "http://fixture.local/save"})
                        ),
                    )
                ],
            ),
            _verdict_response(status="passed", finding="The Save button is visible."),
        ],
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/page"),
        viewport=ViewportConfig(),
        claim="The Save button is visible",
        url="http://fixture.local/page",
        navigation_hint=None,
    )

    trace_path = _field(_field(result, "trace"), "trace_path")
    assert trace_path is not None
    trace_payload = json.loads(Path(trace_path).read_text())
    assert [event["type"] for event in trace_payload] == ["action", "verdict"]
    assert trace_payload[0]["action"] == "goto_url"
    assert trace_payload[1]["verdict_source"] == "json_schema"


@pytest.mark.asyncio
async def test_claim_verifier_accepts_json_inconclusive_with_finding(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, _, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            _verdict_response(
                status="inconclusive",
                finding="The screenshot needs a human judgment.",
            ),
        ],
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/page"),
        viewport=ViewportConfig(),
        claim="The promotion banner feels too crowded",
        url="http://fixture.local/page",
        navigation_hint=None,
    )

    assert _field(result, "status") == "inconclusive"
    assert "human judgment" in _field(result, "finding")


def test_extract_json_verdict_requires_valid_parsed_json() -> None:
    module = _import_claim_verifier_module()

    # No parsed_json attribute → None
    assert module.ClaimVerifier._extract_json_verdict(SimpleNamespace()) is None
    # parsed_json is not a dict → None
    assert module.ClaimVerifier._extract_json_verdict(SimpleNamespace(parsed_json="text")) is None
    # Valid parsed_json → (status, finding)
    assert module.ClaimVerifier._extract_json_verdict(
        SimpleNamespace(parsed_json={"status": "passed", "finding": "The header matches."})
    ) == ("passed", "The header matches.")
    # Invalid status → None
    assert (
        module.ClaimVerifier._extract_json_verdict(
            SimpleNamespace(parsed_json={"status": "unknown", "finding": "Something."})
        )
        is None
    )
    # Missing finding → default message
    assert module.ClaimVerifier._extract_json_verdict(
        SimpleNamespace(parsed_json={"status": "not_testable", "finding": ""})
    ) == ("not_testable", "No finding provided.")


def test_wrong_page_recovered_distinguishes_recovery_from_unrelated_navigation() -> None:
    module = _import_recovery_module()

    assert module.wrong_page_recovered(["http://fixture.local/page"], "http://fixture.local/page") is False
    assert (
        module.wrong_page_recovered(
            ["http://fixture.local/start", "http://fixture.local/page"],
            "http://fixture.local/page",
        )
        is True
    )
    assert (
        module.wrong_page_recovered(
            ["http://fixture.local/tasks", "http://fixture.local/tasks/123"],
            "http://fixture.local/tasks",
        )
        is True
    )
    assert (
        module.wrong_page_recovered(
            ["http://fixture.local/store#/products", "http://fixture.local/store#/products/1"],
            "http://fixture.local/store",
        )
        is True
    )
    assert (
        module.wrong_page_recovered(
            ["http://fixture.local/store#/products", "http://fixture.local/store#/cart"],
            "http://fixture.local/store",
        )
        is False
    )
    assert (
        module.wrong_page_recovered(
            ["http://fixture.local/dashboard", "http://fixture.local/dashboard#"],
            "http://fixture.local/dashboard",
        )
        is False
    )
    assert (
        module.wrong_page_recovered(
            ["http://fixture.local/page"],
            "http://fixture.local/page",
        )
        is False
    )


class FailingBrowserManager(FakeBrowserManager):
    def __init__(self, *, fail_on_capture_call: int) -> None:
        super().__init__()
        self.fail_on_capture_call = fail_on_capture_call

    async def capture_screenshot(self, session: Any) -> bytes:
        del session
        self.capture_calls += 1
        if self.capture_calls == self.fail_on_capture_call:
            raise RuntimeError("page crashed while capturing screenshot")
        return b"\x89PNGfake"


@pytest.mark.asyncio
async def test_claim_verifier_normalizes_initial_screenshot_failures_to_not_testable(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, _, action_executor = _build_claim_verifier(
        module,
        tmp_path,
        responses=[],
        browser_manager=FailingBrowserManager(fail_on_capture_call=1),
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/page"),
        viewport=ViewportConfig(),
        claim="The page has a red button",
        url="http://fixture.local/page",
        navigation_hint=None,
    )

    assert _field(result, "status") == "not_testable"
    assert "Failed to capture screenshot for step-00" in _field(result, "finding")
    assert action_executor.calls == []


@pytest.mark.asyncio
async def test_claim_verifier_normalizes_post_action_screenshot_failures_to_not_testable(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, _, action_executor = _build_claim_verifier(
        module,
        tmp_path,
        responses=[tool_call_message(name="goto_url", arguments=json.dumps({"url": "http://fixture.local/modal"}))],
        browser_manager=FailingBrowserManager(fail_on_capture_call=2),
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/start"),
        viewport=ViewportConfig(),
        claim="The modal opens on click",
        url="http://fixture.local/modal",
        navigation_hint=None,
    )

    assert action_executor.calls == [("goto_url", {"url": "http://fixture.local/modal"})]
    assert _field(result, "status") == "not_testable"
    assert "Failed to capture screenshot for step-01" in _field(result, "finding")
    assert _field(_field(result, "proof"), "screenshot_path").endswith("step-00.webp")
    assert _field(_field(result, "proof"), "step") == 0
    assert _field(_field(result, "proof"), "after_action") is None
    assert _field(_field(result, "trace"), "steps_taken") == 1
    assert _field(_field(result, "trace"), "wrong_page_recovered") is True
    assert _field(_field(result, "trace"), "actions") == ["goto_url({'url': 'http://fixture.local/modal'})"]


@pytest.mark.asyncio
async def test_claim_verifier_preserves_partial_result_on_cancellation(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, _, _ = _build_claim_verifier(module, tmp_path, responses=[])
    verifier.navigator_client = BlockingNavigatorClient([])

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.01):
            await _call_verify(
                verifier,
                page=FakePage(url="http://fixture.local/page"),
                viewport=ViewportConfig(),
                claim="The page has a red button",
                url="http://fixture.local/page",
                navigation_hint=None,
            )

    partial = verifier.consume_partial_result(
        status="inconclusive",
        finding="Claim verification timed out before a verdict was recorded.",
    )

    assert partial is not None
    assert _field(partial, "status") == "inconclusive"
    assert _field(_field(partial, "proof"), "screenshot_path").endswith("step-00.webp")
    assert _field(_field(partial, "proof"), "step") == 0
    assert _field(_field(partial, "trace"), "steps_taken") == 0
    assert _field(_field(partial, "trace"), "screenshot_paths")[0].endswith("step-00.webp")


@pytest.mark.asyncio
async def test_claim_verifier_uses_json_verdict_in_force_stop_path(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, _, action_executor = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            tool_call_message(name="goto_url", arguments=json.dumps({"url": "http://fixture.local/modal"})),
            # After exhausting steps, the force-stop path asks for a final verdict.
            _verdict_response(
                status="inconclusive",
                finding="Reached the step limit without enough evidence.",
            ),
        ],
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/start"),
        viewport=ViewportConfig(),
        claim="The modal opens on click",
        url="http://fixture.local/modal",
        navigation_hint=None,
    )

    assert action_executor.calls == [("goto_url", {"url": "http://fixture.local/modal"})]
    assert _field(result, "status") == "inconclusive"
    assert "Reached the step limit" in _field(result, "finding")


@pytest.mark.asyncio
async def test_claim_verifier_answers_every_tool_call_when_step_limit_hits_mid_turn(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, navigator_client, action_executor = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        id="tool-1",
                        function=FakeFunction(name="goto_url", arguments=json.dumps({"url": "http://fixture.local/a"})),
                    ),
                    FakeToolCall(
                        id="tool-2",
                        function=FakeFunction(name="goto_url", arguments=json.dumps({"url": "http://fixture.local/b"})),
                    ),
                ]
            ),
            _verdict_response(status="inconclusive", finding="Step limit reached."),
        ],
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/start"),
        viewport=ViewportConfig(),
        claim="The cart badge shows 3 items",
        url="http://fixture.local/start",
        navigation_hint=None,
        max_steps=1,
    )

    # Both tool calls executed even though the step budget ran out after the first.
    assert [name for name, _ in action_executor.calls] == ["goto_url", "goto_url"]
    # Every tool_call id received a role="tool" reply before the force-stop user message,
    # keeping the chat-completions transcript valid.
    force_stop_messages = navigator_client.calls[-1]["messages"]
    tool_reply_ids = [message.get("tool_call_id") for message in force_stop_messages if message.get("role") == "tool"]
    assert tool_reply_ids == ["tool-1", "tool-2"]
    assert _field(result, "status") == "inconclusive"


@pytest.mark.asyncio
async def test_claim_verifier_feeds_action_error_back_to_model_and_recovers(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()

    class FlakyActionExecutor(FakeActionExecutor):
        def __init__(self) -> None:
            super().__init__()
            self.failures_remaining = 1

        async def execute_tool_call(self, session: FakeSession, tool_call: Any) -> Any:
            if self.failures_remaining > 0:
                self.failures_remaining -= 1
                raise module.BrowserActionError("left_click ref resolution failed for e12: not found")
            return await super().execute_tool_call(session, tool_call)

    verifier, navigator_client, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            tool_call_message(name="left_click", arguments=json.dumps({"ref": "e12"})),
            tool_call_message(
                name="goto_url", arguments=json.dumps({"url": "http://fixture.local/cart"}), call_id="tool-2"
            ),
            _verdict_response(status="passed", finding="The cart badge shows 3 items."),
        ],
        action_executor=FlakyActionExecutor(),
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/start"),
        viewport=ViewportConfig(),
        claim="The cart badge shows 3 items",
        url="http://fixture.local/start",
        navigation_hint=None,
        max_steps=5,
    )

    # The failed action becomes an [ERROR] tool result the model can react to,
    # not a not_testable verdict for the whole claim.
    second_turn_messages = navigator_client.calls[1]["messages"]
    error_tool_messages = [
        message
        for message in second_turn_messages
        if message.get("role") == "tool" and "[ERROR]" in message["content"][0]["text"]
    ]
    assert len(error_tool_messages) == 1
    assert "ref resolution failed" in error_tool_messages[0]["content"][0]["text"]
    assert _field(result, "status") == "passed"


@pytest.mark.asyncio
async def test_claim_verifier_gives_up_inconclusive_after_repeated_action_failures(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()

    class AlwaysFailingActionExecutor(FakeActionExecutor):
        async def execute_tool_call(self, session: FakeSession, tool_call: Any) -> Any:
            del session
            raise module.BrowserActionError(f"{tool_call.function.name} ref resolution failed for e12: not found")

    failing_tool_calls = [
        FakeToolCall(
            id=f"tool-{index}",
            function=FakeFunction(name="left_click", arguments=json.dumps({"ref": "e12"})),
        )
        for index in range(1, 4)
    ]
    verifier, navigator_client, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[FakeMessage(tool_calls=failing_tool_calls)],
        action_executor=AlwaysFailingActionExecutor(),
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/start"),
        viewport=ViewportConfig(),
        claim="The cart badge shows 3 items",
        url="http://fixture.local/start",
        navigation_hint=None,
        max_steps=5,
    )

    assert _field(result, "status") == "inconclusive"
    assert "failed 3 times in a row" in _field(result, "finding")
    # The claim ends locally; no extra navigator turn is spent after the third failure.
    assert len(navigator_client.calls) == 1


@pytest.mark.asyncio
async def test_claim_verifier_redacts_password_typing_in_trace_events(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()

    verifier, navigator_client, action_executor = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            tool_call_message(name="type", arguments=json.dumps({"text": "hunter2"})),
            _verdict_response(status="passed", finding="The password field accepts input."),
        ],
    )

    result = await _call_verify(
        verifier,
        page=PasswordFocusedPage(url="http://fixture.local/login"),
        viewport=ViewportConfig(),
        claim="The password field accepts input",
        url="http://fixture.local/login",
        navigation_hint=None,
        max_steps=3,
    )

    # The executor still types the real text...
    assert action_executor.calls == [("type", {"text": "hunter2"})]
    # ...but the recorded trace event is masked.
    action_events = [event for event in _field(result, "trace").events if event.type == "action"]
    assert action_events[0].action_args["text"] == "[redacted]"
    transcript = json.dumps(navigator_client.calls[1]["messages"])
    assert "hunter2" not in transcript
    assert "[redacted]" in transcript
    assert all("hunter2" not in action for action in _field(result, "trace").actions)
    assert _field(result, "status") == "passed"


@pytest.mark.asyncio
async def test_claim_verifier_redacts_malformed_password_type_arguments(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()

    verifier, navigator_client, action_executor = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            tool_call_message(name="type", arguments='{"text": "hunter2"'),
            _verdict_response(status="inconclusive", finding="The type action arguments were malformed."),
        ],
        action_executor=module.ActionExecutor(navigation_timeout_ms=1_000, settle_delay_seconds=0),
    )

    result = await _call_verify(
        verifier,
        page=PasswordFocusedPage(url="http://fixture.local/login"),
        viewport=ViewportConfig(),
        claim="The password field accepts input",
        url="http://fixture.local/login",
        navigation_hint=None,
        max_steps=3,
    )

    assert not hasattr(action_executor, "calls")
    transcript = json.dumps(navigator_client.calls[1]["messages"])
    assert "hunter2" not in transcript
    assert "[redacted]" in transcript
    assert all("hunter2" not in action for action in _field(result, "trace").actions)
    assert _field(result, "status") == "inconclusive"


@pytest.mark.asyncio
async def test_claim_verifier_redacts_password_set_element_value_transcript(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _import_claim_verifier_module()

    class SetPasswordActionExecutor(FakeActionExecutor):
        async def execute_tool_call(self, session: FakeSession, tool_call: Any) -> Any:
            resolved_args = json.loads(tool_call.function.arguments or "{}")
            self.calls.append((tool_call.function.name, resolved_args))
            return SimpleNamespace(
                trace=f"set_element_value(ref='password-input', value='{resolved_args['value']}')",
                output_text=f'Set password value to "{resolved_args["value"]}"',
                current_url=session.page.url,
                counts_as_interaction=True,
            )

    async def _password_ref(_page: Any, ref: str) -> bool:
        return ref == "password-input"

    monkeypatch.setattr(module, "referenced_element_is_password", _password_ref)

    verifier, navigator_client, action_executor = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            tool_call_message(
                name="set_element_value", arguments=json.dumps({"ref": "password-input", "value": "hunter2"})
            ),
            _verdict_response(status="passed", finding="The password field accepts input."),
        ],
        action_executor=SetPasswordActionExecutor(),
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/login"),
        viewport=ViewportConfig(),
        claim="The password field accepts input",
        url="http://fixture.local/login",
        navigation_hint=None,
        max_steps=3,
    )

    assert action_executor.calls == [("set_element_value", {"ref": "password-input", "value": "hunter2"})]
    transcript = json.dumps(navigator_client.calls[1]["messages"])
    assert "hunter2" not in transcript
    assert "[redacted]" in transcript
    action_events = [event for event in _field(result, "trace").events if event.type == "action"]
    assert action_events[0].action_args["value"] == "[redacted]"
    assert all("hunter2" not in action for action in _field(result, "trace").actions)


@pytest.mark.asyncio
async def test_claim_verifier_grounding_never_upgrades_failed_verdict_to_passed(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, _, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            _verdict_response(
                status="failed",
                finding="The Save button is hidden behind the cookie-consent overlay.",
            ),
        ],
    )

    result = await _call_verify(
        verifier,
        page=EvaluatingPage(
            url="http://fixture.local/page",
            visual_state=_visual_state(
                # DOM-visible, but the model judged the pixels and failed the
                # claim (e.g. the button is covered by an overlay).
                visibleButtons=["Save"],
                buttonStates=[{"text": "Save", "fullyVisible": True}],
            ),
        ),
        viewport=ViewportConfig(),
        claim="The Save button is visible",
        url="http://fixture.local/page",
        navigation_hint=None,
    )

    assert _field(result, "status") == "failed"
    assert _field(result, "finding") == "The Save button is hidden behind the cookie-consent overlay."


@pytest.mark.asyncio
async def test_claim_verifier_visible_claim_passes_for_partially_clipped_button(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, _, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            _verdict_response(status="passed", finding="The Save button is visible."),
        ],
    )

    result = await _call_verify(
        verifier,
        page=EvaluatingPage(
            url="http://fixture.local/page",
            visual_state=_visual_state(
                visibleButtons=["Save"],
                # Partially clipped is still visible; only the separate
                # "fully visible" pattern demands fullyVisible=True.
                buttonStates=[{"text": "Save", "fullyVisible": False}],
            ),
        ),
        viewport=ViewportConfig(),
        claim="The Save button is visible",
        url="http://fixture.local/page",
        navigation_hint=None,
    )

    assert _field(result, "status") == "passed"
    assert "Visible button label matched" in _field(result, "finding")


@pytest.mark.asyncio
async def test_claim_verifier_redacts_every_password_tool_call_in_multi_tool_turn(tmp_path: Path) -> None:
    """Regression: messages[-1] is a tool reply (not the assistant message) for
    the second tool call of a turn; the transcript rewrite must still find the
    stored assistant tool_calls for it."""
    module = _import_claim_verifier_module()

    verifier, navigator_client, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        id="tool-1",
                        function=FakeFunction(name="type", arguments=json.dumps({"text": "first-secret"})),
                    ),
                    FakeToolCall(
                        id="tool-2",
                        function=FakeFunction(name="type", arguments=json.dumps({"text": "second-secret"})),
                    ),
                ]
            ),
            _verdict_response(status="passed", finding="The password fields accept input."),
        ],
    )

    await _call_verify(
        verifier,
        page=PasswordFocusedPage(url="http://fixture.local/login"),
        viewport=ViewportConfig(),
        claim="The password fields accept input",
        url="http://fixture.local/login",
        navigation_hint=None,
        max_steps=4,
    )

    transcript = json.dumps(navigator_client.calls[1]["messages"])
    assert "first-secret" not in transcript
    assert "second-secret" not in transcript
    assert transcript.count("[redacted]") >= 2


@pytest.mark.asyncio
async def test_claim_verifier_redacts_malformed_password_set_element_value_arguments(tmp_path: Path) -> None:
    """Regression: malformed set_element_value arguments can embed a secret in
    the executor's [ERROR] output; with the ref unparseable the call must be
    treated as sensitive (fail closed)."""
    module = _import_claim_verifier_module()

    verifier, navigator_client, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            tool_call_message(name="set_element_value", arguments='{"ref": "password-input", "value": "hunter2"'),
            _verdict_response(status="inconclusive", finding="The arguments were malformed."),
        ],
        action_executor=module.ActionExecutor(navigation_timeout_ms=1_000, settle_delay_seconds=0),
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/login"),
        viewport=ViewportConfig(),
        claim="The password field accepts input",
        url="http://fixture.local/login",
        navigation_hint=None,
        max_steps=3,
    )

    transcript = json.dumps(navigator_client.calls[1]["messages"])
    assert "hunter2" not in transcript
    assert all("hunter2" not in action for action in _field(result, "trace").actions)


@pytest.mark.asyncio
async def test_claim_verifier_redacts_type_when_password_detection_fails(tmp_path: Path) -> None:
    """Detection failures must fail closed: FakePage has no evaluate, so the
    password check errors out — the typed text still gets masked."""
    module = _import_claim_verifier_module()

    verifier, navigator_client, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            tool_call_message(name="type", arguments=json.dumps({"text": "maybe-secret"})),
            _verdict_response(status="passed", finding="The field accepts input."),
        ],
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/form"),
        viewport=ViewportConfig(),
        claim="The field accepts input",
        url="http://fixture.local/form",
        navigation_hint=None,
        max_steps=3,
    )

    transcript = json.dumps(navigator_client.calls[1]["messages"])
    assert "maybe-secret" not in transcript
    action_events = [event for event in _field(result, "trace").events if event.type == "action"]
    assert action_events[0].action_args["text"] == "[redacted]"
