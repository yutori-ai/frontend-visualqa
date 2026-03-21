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


def _import_claim_verifier_module():
    import importlib

    try:
        return importlib.import_module("frontend_visualqa.claim_verifier")
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("frontend_visualqa"):
            pytest.skip("frontend_visualqa.claim_verifier is not implemented in this worktree yet")
        raise


def _instantiate_with_supported_kwargs(factory: Any, **candidates: Any) -> Any:
    signature = inspect.signature(factory)
    kwargs = {
        name: value
        for name, value in candidates.items()
        if name in signature.parameters
        and signature.parameters[name].kind in {inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
    }
    return factory(**kwargs)


def _field(result: Any, name: str) -> Any:
    if isinstance(result, dict):
        return result[name]
    return getattr(result, name)


@dataclass
class FakePage:
    url: str


class EvaluatingPage(FakePage):
    def __init__(self, url: str, visual_state: dict[str, list[str]]) -> None:
        super().__init__(url=url)
        self.visual_state = visual_state

    async def evaluate(self, _: str) -> dict[str, list[str]]:
        return self.visual_state


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
        return b"RIFFfakeWEBP"


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
            output_text=(f"Executed {resolved_name}" if resolved_name in {"extract_elements", "extract_content", "find"} else None),
            current_url=session.page.url,
            success=True,
        )


class FakeArtifactManager:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.run = RunArtifacts(run_id="run-test", run_dir=base_dir / "run-test")
        self.run.run_dir.mkdir(parents=True, exist_ok=True)

    def create_run(self, prefix: str = "run", run_id: str | None = None) -> RunArtifacts:
        del prefix, run_id
        return self.run

    def save_screenshot(self, run: RunArtifacts, claim_index: int, label: str, image_bytes: bytes) -> str:
        claim_dir = run.run_dir / f"claim-{claim_index:02d}"
        claim_dir.mkdir(parents=True, exist_ok=True)
        path = claim_dir / f"{label}.webp"
        path.write_bytes(image_bytes)
        return str(path)

    def save_trace(self, run: RunArtifacts, claim_index: int, action_trace: list[str]) -> str:
        claim_dir = run.run_dir / f"claim-{claim_index:02d}"
        claim_dir.mkdir(parents=True, exist_ok=True)
        path = claim_dir / "action_trace.json"
        path.write_text(json.dumps(action_trace))
        return str(path)

    def save_proof_text(self, run: RunArtifacts, claim_index: int, label: str, text: str) -> str:
        claim_dir = run.run_dir / f"claim-{claim_index:02d}"
        claim_dir.mkdir(parents=True, exist_ok=True)
        path = claim_dir / f"{label}-proof.txt"
        path.write_text(text, encoding="utf-8")
        return str(path)


@dataclass
class FakeFunction:
    name: str
    arguments: str


@dataclass
class FakeToolCall:
    id: str
    function: FakeFunction


@dataclass
class FakeMessage:
    role: str = "assistant"
    tool_calls: list[FakeToolCall] | None = None
    content: str | None = None

    def model_dump(self, exclude_none: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "role": self.role,
            "content": self.content,
        }
        if self.tool_calls is not None:
            payload["tool_calls"] = [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                    },
                }
                for tool_call in self.tool_calls
            ]
        if exclude_none:
            return {key: value for key, value in payload.items() if value is not None}
        return payload


class FakeN1Client:
    def __init__(self, responses: list[FakeMessage]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def create(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> FakeMessage:
        self.calls.append({"messages": messages, "tools": tools})
        return self.responses.pop(0)


class BlockingN1Client(FakeN1Client):
    async def create(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> FakeMessage:
        self.calls.append({"messages": messages, "tools": tools})
        await asyncio.sleep(1.0)
        raise AssertionError("BlockingN1Client should be cancelled before returning")


def _build_claim_verifier(
    module: Any,
    tmp_path: Path,
    responses: list[FakeMessage],
    *,
    browser_manager: Any | None = None,
    action_executor: Any | None = None,
    artifact_manager: Any | None = None,
    visualize: bool = False,
) -> tuple[Any, FakeN1Client, FakeActionExecutor]:
    browser = browser_manager or FakeBrowserManager()
    action_executor = action_executor or FakeActionExecutor()
    artifacts = artifact_manager or FakeArtifactManager(tmp_path)
    n1_client = FakeN1Client(responses)

    verifier = _instantiate_with_supported_kwargs(
        module.ClaimVerifier,
        browser_manager=browser,
        browser=browser,
        action_executor=action_executor,
        artifact_manager=artifacts,
        artifacts=artifacts,
        n1_client=n1_client,
        client=n1_client,
        visualize=visualize,
    )

    for attribute_name, value in {
        "browser_manager": browser,
        "browser": browser,
        "action_executor": action_executor,
        "artifact_manager": artifacts,
        "artifacts": artifacts,
        "n1_client": n1_client,
        "client": n1_client,
    }.items():
        setattr(verifier, attribute_name, value)

    return verifier, n1_client, action_executor


async def _call_verify(
    verifier: Any,
    *,
    page: FakePage,
    viewport: ViewportConfig,
    claim: str,
    url: str,
    navigation_hint: str | None,
    visualize: bool | None = None,
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
        kwargs["max_steps"] = 2
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
async def test_claim_verifier_returns_structured_verdict_from_record_claim_result(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, n1_client, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        id="tool-1",
                        function=FakeFunction(
                            name="record_claim_result",
                            arguments=json.dumps(
                                {
                                    "status": "passed",
                                    "finding": "The red button is visible in the hero panel.",
                                }
                            ),
                        ),
                    )
                ]
            )
        ],
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/page"),
        viewport=ViewportConfig(width=1280, height=800, device_scale_factor=1),
        claim="The page has a red button",
        url="http://fixture.local/page",
        navigation_hint=None,
    )

    assert _field(result, "claim") == "The page has a red button"
    assert _field(result, "status") == "passed"
    assert "red button" in _field(result, "finding")
    assert _field(result, "page").url == "http://fixture.local/page"
    assert _field(result, "trace").steps_taken == 0
    assert _field(result, "proof").screenshot_path.endswith("step-00-initial.webp")
    assert _field(result, "proof").step == 0
    assert _field(result, "proof").text is None
    assert n1_client.calls
    assert any(tool["function"]["name"] == "goto_url" for tool in n1_client.calls[0]["tools"])
    assert any(tool["function"]["name"] == "record_claim_result" for tool in n1_client.calls[0]["tools"])


@pytest.mark.asyncio
async def test_claim_verifier_executes_actions_before_final_verdict(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, _, action_executor = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        id="tool-1",
                        function=FakeFunction(
                            name="goto_url",
                            arguments=json.dumps({"url": "http://fixture.local/modal"}),
                        ),
                    )
                ]
            ),
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        id="tool-2",
                        function=FakeFunction(
                            name="record_claim_result",
                            arguments=json.dumps(
                                {
                                    "status": "passed",
                                    "finding": "The modal is now visible and titled Edit Task.",
                                }
                            ),
                        ),
                    )
                ]
            ),
        ],
    )
    page = FakePage(url="http://fixture.local/start")

    result = await _call_verify(
        verifier,
        page=page,
        viewport=ViewportConfig(width=1280, height=800, device_scale_factor=1),
        claim="The modal opens on click",
        url="http://fixture.local/modal",
        navigation_hint="Open the modal before deciding.",
    )

    assert action_executor.calls == [("goto_url", {"url": "http://fixture.local/modal"})]
    assert _field(result, "status") == "passed"
    assert _field(result, "page").url == "http://fixture.local/modal"
    assert _field(result, "trace").steps_taken >= 1
    assert _field(result, "trace").wrong_page_recovered is True
    assert _field(result, "proof").step == _field(result, "trace").steps_taken
    assert _field(result, "proof").after_action == "goto_url({'url': 'http://fixture.local/modal'})"
    assert _field(result, "proof").text is None


def test_claim_verifier_accepts_visualize_flag(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier = module.ClaimVerifier(
        browser_manager=FakeBrowserManager(),
        artifact_manager=FakeArtifactManager(tmp_path),
        n1_client=FakeN1Client([]),
        visualize=True,
    )

    assert verifier._visualize is True


@pytest.mark.asyncio
async def test_claim_verifier_uses_overlay_lifecycle_when_visualize_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _import_claim_verifier_module()
    events: list[Any] = []

    class FakeOverlay:
        async def claim_started(self) -> None:
            events.append("claim_started")

        async def set_status(self, label: str) -> None:
            events.append(("set_status", label))

        async def before_screenshot(self) -> None:
            events.append("before_screenshot")

        async def after_screenshot(self) -> None:
            events.append("after_screenshot")

        async def claim_ended(self) -> None:
            events.append("claim_ended")

    fake_overlay = FakeOverlay()
    monkeypatch.setattr(module, "_create_overlay_controller", lambda page: fake_overlay)

    verifier, _, action_executor = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        id="tool-1",
                        function=FakeFunction(
                            name="goto_url",
                            arguments=json.dumps({"url": "http://fixture.local/modal"}),
                        ),
                    )
                ]
            ),
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        id="tool-2",
                        function=FakeFunction(
                            name="record_claim_result",
                            arguments=json.dumps(
                                {
                                    "status": "passed",
                                    "finding": "The modal is now visible and titled Edit Task.",
                                }
                            ),
                        ),
                    )
                ]
            ),
        ],
        visualize=True,
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/start"),
        viewport=ViewportConfig(width=1280, height=800, device_scale_factor=1),
        claim="The modal opens on click",
        url="http://fixture.local/modal",
        navigation_hint="Open the modal before deciding.",
        visualize=True,
    )

    assert _field(result, "status") == "passed"
    assert events[0] == "claim_started"
    assert events.count("before_screenshot") == 2
    assert events.count("after_screenshot") == 2
    assert ("set_status", "Analyzing") in events
    assert events[-1] == "claim_ended"
    assert action_executor.overlays_seen[0] is fake_overlay
    assert action_executor.overlay is None


@pytest.mark.asyncio
async def test_claim_verifier_reprompts_after_plain_text_thought_and_continues(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, n1_client, action_executor = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            FakeMessage(content="I should open the task detail page before deciding."),
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        id="tool-1",
                        function=FakeFunction(
                            name="goto_url",
                            arguments=json.dumps({"url": "http://fixture.local/tasks/123"}),
                        ),
                    )
                ]
            ),
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        id="tool-2",
                        function=FakeFunction(
                            name="record_claim_result",
                            arguments=json.dumps({"status": "passed", "finding": "The Task Details heading is visible."}),
                        ),
                    )
                ]
            ),
        ],
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/tasks"),
        viewport=ViewportConfig(width=1280, height=800, device_scale_factor=1),
        claim="The page title reads 'Task Details'",
        url="http://fixture.local/tasks",
        navigation_hint="Open the task detail page before deciding.",
    )

    assert _field(result, "status") == "passed"
    assert action_executor.calls == [("goto_url", {"url": "http://fixture.local/tasks/123"})]
    assert len(n1_client.calls) == 3
    reminder_message = next(
        message
        for message in reversed(n1_client.calls[1]["messages"])
        if message.get("role") == "user" and isinstance(message.get("content"), list)
    )
    reminder_text = reminder_message["content"][0]["text"]
    assert "Do not narrate your intent in plain text." in reminder_text


@pytest.mark.asyncio
async def test_claim_verifier_preserves_tool_call_order_when_action_and_verdict_share_a_turn(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, _, action_executor = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        id="tool-1",
                        function=FakeFunction(
                            name="goto_url",
                            arguments=json.dumps({"url": "http://fixture.local/modal"}),
                        ),
                    ),
                    FakeToolCall(
                        id="tool-2",
                        function=FakeFunction(
                            name="record_claim_result",
                            arguments=json.dumps({"status": "passed", "finding": "The modal is visible."}),
                        ),
                    ),
                ]
            )
        ],
    )
    page = FakePage(url="http://fixture.local/start")

    result = await _call_verify(
        verifier,
        page=page,
        viewport=ViewportConfig(width=1280, height=800, device_scale_factor=1),
        claim="The modal opens on click",
        url="http://fixture.local/modal",
        navigation_hint="Open the modal before deciding.",
    )

    assert action_executor.calls == [("goto_url", {"url": "http://fixture.local/modal"})]
    assert _field(result, "status") == "passed"
    assert _field(result, "page").url == "http://fixture.local/modal"


@pytest.mark.asyncio
async def test_claim_verifier_downgrades_pass_when_button_grounding_disagrees(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, _, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        id="tool-1",
                        function=FakeFunction(
                            name="record_claim_result",
                            arguments=json.dumps(
                                {
                                    "status": "passed",
                                    "finding": "The Show Save Confirmation button is visible in the header.",
                                }
                            ),
                        ),
                    )
                ]
            )
        ],
    )

    result = await _call_verify(
        verifier,
        page=EvaluatingPage(
            url="http://fixture.local/page",
            visual_state={
                "visibleHeadings": ["Frontend Visual QA Playground"],
                "visibleButtons": ["Open Edit Task Modal", "Show Save Confirmation"],
                "buttonStates": [
                    {"text": "Open Edit Task Modal", "fullyVisible": True},
                    {"text": "Show Save Confirmation", "fullyVisible": True},
                ],
                "dialogTitles": [],
            },
        ),
        viewport=ViewportConfig(width=1280, height=800, device_scale_factor=1),
        claim="The Save button is visible without scrolling",
        url="http://fixture.local/page",
        navigation_hint=None,
    )

    assert _field(result, "status") == "failed"
    assert "No visible button label matched" in _field(result, "finding")


@pytest.mark.asyncio
async def test_claim_verifier_converts_inconclusive_full_visibility_button_claim_to_fail(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, _, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        id="tool-1",
                        function=FakeFunction(
                            name="record_claim_result",
                            arguments=json.dumps(
                                {
                                    "status": "inconclusive",
                                    "finding": "I could not tell whether the button was fully visible.",
                                }
                            ),
                        ),
                    )
                ]
            )
        ],
    )

    result = await _call_verify(
        verifier,
        page=EvaluatingPage(
            url="http://fixture.local/settings",
            visual_state={
                "visibleHeadings": ["Workspace Settings"],
                "visibleButtons": ["Save"],
                "buttonStates": [{"text": "Save", "fullyVisible": False}],
                "dialogTitles": [],
            },
        ),
        viewport=ViewportConfig(width=1280, height=800, device_scale_factor=1),
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
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        id="tool-1",
                        function=FakeFunction(
                            name="record_claim_result",
                            arguments=json.dumps({"status": "passed", "finding": "Button visible."}),
                        ),
                    )
                ]
            )
        ],
    )

    result = await _call_verify(
        verifier,
        page=EvaluatingPage(
            url="http://fixture.local/page",
            visual_state={
                "visibleHeadings": ["Page Title"],
                "visibleButtons": ["Select Priority \u25bc"],
                "buttonStates": [{"text": "Select Priority \u25bc", "fullyVisible": True}],
                "dialogTitles": [],
            },
        ),
        viewport=ViewportConfig(width=1280, height=800, device_scale_factor=1),
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
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        id="tool-1",
                        function=FakeFunction(
                            name="record_claim_result",
                            arguments=json.dumps({"status": "passed", "finding": "Both conditions met."}),
                        ),
                    )
                ]
            )
        ],
    )

    result = await _call_verify(
        verifier,
        page=EvaluatingPage(
            url="http://fixture.local/page",
            visual_state={
                "visibleHeadings": [],
                "visibleButtons": [],
                "buttonStates": [],
                "dialogTitles": [],
            },
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
    verifier, n1_client, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        id="tool-1",
                        function=FakeFunction(
                            name="goto_url",
                            arguments=json.dumps({"url": "http://fixture.local/modal"}),
                        ),
                    )
                ]
            ),
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        id="tool-2",
                        function=FakeFunction(
                            name="record_claim_result",
                            arguments=json.dumps(
                                {
                                    "status": "passed",
                                    "finding": "The modal is now visible and titled Edit Task.",
                                }
                            ),
                        ),
                    )
                ]
            ),
        ],
    )
    trim_calls: list[dict[str, Any]] = []
    trimmed_payload = [{"role": "user", "content": [{"type": "text", "text": "trimmed"}]}]

    def fake_trim_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        trim_calls.append({"messages": messages})
        return trimmed_payload

    n1_client.trim_messages = fake_trim_messages  # type: ignore[attr-defined]

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/start"),
        viewport=ViewportConfig(width=1280, height=800, device_scale_factor=1),
        claim="The modal opens on click",
        url="http://fixture.local/modal",
        navigation_hint="Open the modal before deciding.",
    )

    assert _field(result, "status") == "passed"
    assert len(trim_calls) >= 2
    assert trim_calls[0]["messages"][0]["content"][0]["text"] != "trimmed"
    assert trim_calls[1]["messages"][0]["content"][0]["text"] == "trimmed"
    assert n1_client.calls[0]["messages"][0]["content"][0]["text"] == "trimmed"
    assert n1_client.calls[1]["messages"][0]["content"][0]["text"] == "trimmed"


@pytest.mark.asyncio
async def test_claim_verifier_treats_stop_tool_call_as_a_final_inconclusive_verdict(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, _, action_executor = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        id="tool-1",
                        function=FakeFunction(
                            name="stop",
                            arguments=json.dumps({"reason": "Need a human to decide from this screenshot."}),
                        ),
                    )
                ]
            )
        ],
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/page"),
        viewport=ViewportConfig(width=1280, height=800, device_scale_factor=1),
        claim="The promotion banner feels too crowded",
        url="http://fixture.local/page",
        navigation_hint=None,
    )

    assert _field(result, "status") == "inconclusive"
    assert "Need a human" in _field(result, "finding")
    assert action_executor.calls == []


@pytest.mark.asyncio
async def test_claim_verifier_records_proof_text_for_read_only_final_action(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()

    class ReadOnlyActionExecutor(FakeActionExecutor):
        async def execute_tool_call(self, session: FakeSession, tool_call: Any) -> Any:
            result = await super().execute_tool_call(session, tool_call)
            if tool_call.function.name == "extract_elements":
                result.output_text = "Visible buttons:\n- Save"
            return result

    verifier, _, action_executor = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        id="tool-1",
                        function=FakeFunction(name="extract_elements", arguments=json.dumps({"filter": "Save"})),
                    )
                ]
            ),
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        id="tool-2",
                        function=FakeFunction(
                            name="record_claim_result",
                            arguments=json.dumps({"status": "passed", "finding": "The Save button is visible."}),
                        ),
                    )
                ]
            ),
        ],
        action_executor=ReadOnlyActionExecutor(),
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/page"),
        viewport=ViewportConfig(width=1280, height=800, device_scale_factor=1),
        claim="The Save button is visible",
        url="http://fixture.local/page",
        navigation_hint=None,
    )

    assert action_executor.calls == [("extract_elements", {"filter": "Save"})]
    assert _field(result, "proof").step == 1
    assert _field(result, "proof").after_action == "extract_elements({'filter': 'Save'})"
    assert _field(result, "proof").text == "Visible buttons:\n- Save"
    assert _field(result, "proof").text_path.endswith("step-01-proof.txt")
    assert Path(_field(result, "proof").text_path).read_text(encoding="utf-8") == "Visible buttons:\n- Save"


@pytest.mark.asyncio
async def test_claim_verifier_truncates_inline_proof_text_but_saves_full_artifact(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    full_proof_text = "Page text:\n" + " ".join(f"token-{index:03d}" for index in range(80))

    class ReadOnlyActionExecutor(FakeActionExecutor):
        async def execute_tool_call(self, session: FakeSession, tool_call: Any) -> Any:
            result = await super().execute_tool_call(session, tool_call)
            if tool_call.function.name == "extract_content":
                result.output_text = full_proof_text
            return result

    verifier, _, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        id="tool-1",
                        function=FakeFunction(name="extract_content", arguments=json.dumps({})),
                    )
                ]
            ),
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        id="tool-2",
                        function=FakeFunction(
                            name="record_claim_result",
                            arguments=json.dumps({"status": "failed", "finding": "The page text does not match."}),
                        ),
                    )
                ]
            ),
        ],
        action_executor=ReadOnlyActionExecutor(),
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/page"),
        viewport=ViewportConfig(width=1280, height=800, device_scale_factor=1),
        claim="The page text matches exactly",
        url="http://fixture.local/page",
        navigation_hint=None,
    )

    assert _field(result, "proof").text is not None
    assert _field(result, "proof").text != full_proof_text
    assert _field(result, "proof").text.endswith("...")
    assert _field(result, "proof").text_path.endswith("step-01-proof.txt")
    assert Path(_field(result, "proof").text_path).read_text(encoding="utf-8") == full_proof_text


@pytest.mark.asyncio
async def test_claim_verifier_accepts_stop_finding_field(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, _, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        id="tool-1",
                        function=FakeFunction(
                            name="stop",
                            arguments=json.dumps({"finding": "The screenshot needs a human judgment."}),
                        ),
                    )
                ]
            )
        ],
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/page"),
        viewport=ViewportConfig(width=1280, height=800, device_scale_factor=1),
        claim="The promotion banner feels too crowded",
        url="http://fixture.local/page",
        navigation_hint=None,
    )

    assert _field(result, "status") == "inconclusive"
    assert "human judgment" in _field(result, "finding")


@pytest.mark.asyncio
async def test_claim_verifier_clears_stale_read_only_proof_text_after_mutating_action(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()

    class MixedActionExecutor(FakeActionExecutor):
        async def execute_tool_call(self, session: FakeSession, tool_call: Any) -> Any:
            result = await super().execute_tool_call(session, tool_call)
            if tool_call.function.name == "extract_elements":
                result.output_text = "Visible buttons:\n- Save"
            return result

    verifier, _, action_executor = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        id="tool-1",
                        function=FakeFunction(name="extract_elements", arguments=json.dumps({"filter": "Save"})),
                    )
                ]
            ),
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        id="tool-2",
                        function=FakeFunction(name="goto_url", arguments=json.dumps({"url": "http://fixture.local/modal"})),
                    )
                ]
            ),
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        id="tool-3",
                        function=FakeFunction(
                            name="record_claim_result",
                            arguments=json.dumps({"status": "passed", "finding": "The modal is visible."}),
                        ),
                    )
                ]
            ),
        ],
        action_executor=MixedActionExecutor(),
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/start"),
        viewport=ViewportConfig(width=1280, height=800, device_scale_factor=1),
        claim="The modal opens on click",
        url="http://fixture.local/modal",
        navigation_hint=None,
    )

    assert action_executor.calls == [
        ("extract_elements", {"filter": "Save"}),
        ("goto_url", {"url": "http://fixture.local/modal"}),
    ]
    assert _field(result, "proof").after_action == "goto_url({'url': 'http://fixture.local/modal'})"
    assert _field(result, "proof").text is None


def test_parse_fallback_verdict_requires_explicit_status_markers() -> None:
    module = _import_claim_verifier_module()

    assert module.ClaimVerifier._parse_fallback_verdict("The screenshot seems verified.") is None
    assert module.ClaimVerifier._parse_fallback_verdict("Status: passed\nThe header matches.") == (
        "passed",
        "Status: passed\nThe header matches.",
    )
    assert module.ClaimVerifier._parse_fallback_verdict('{"verdict":"not_testable","summary":"Auth wall"}') == (
        "not_testable",
        '{"verdict":"not_testable","summary":"Auth wall"}',
    )


def test_wrong_page_recovered_handles_reaching_or_leaving_the_starting_url() -> None:
    module = _import_claim_verifier_module()

    assert module.ClaimVerifier._wrong_page_recovered(["http://fixture.local/page"], "http://fixture.local/page") is False
    assert (
        module.ClaimVerifier._wrong_page_recovered(
            ["http://fixture.local/start", "http://fixture.local/page"],
            "http://fixture.local/page",
        )
        is True
    )
    assert (
        module.ClaimVerifier._wrong_page_recovered(
            ["http://fixture.local/tasks", "http://fixture.local/tasks/123"],
            "http://fixture.local/tasks",
        )
        is True
    )
    assert (
        module.ClaimVerifier._wrong_page_recovered(
            ["http://fixture.local/page"],
            "http://fixture.local/page",
            ["refresh()"],
        )
        is False
    )


def test_extract_structured_verdict_returns_none_for_empty_tool_calls() -> None:
    module = _import_claim_verifier_module()

    assert module.ClaimVerifier._extract_structured_verdict([]) is None


class FailingBrowserManager(FakeBrowserManager):
    def __init__(self, *, fail_on_capture_call: int) -> None:
        super().__init__()
        self.fail_on_capture_call = fail_on_capture_call

    async def capture_screenshot(self, session: Any) -> bytes:
        del session
        self.capture_calls += 1
        if self.capture_calls == self.fail_on_capture_call:
            raise RuntimeError("page crashed while capturing screenshot")
        return b"RIFFfakeWEBP"


@pytest.mark.asyncio
async def test_claim_verifier_normalizes_initial_screenshot_failures_to_not_testable(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, _, _ = _build_claim_verifier(
        module,
        tmp_path,
        responses=[],
        browser_manager=FailingBrowserManager(fail_on_capture_call=1),
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/page"),
        viewport=ViewportConfig(width=1280, height=800, device_scale_factor=1),
        claim="The page has a red button",
        url="http://fixture.local/page",
        navigation_hint=None,
    )

    assert _field(result, "status") == "not_testable"
    assert "Failed to capture screenshot for step-00-initial" in _field(result, "finding")


@pytest.mark.asyncio
async def test_claim_verifier_normalizes_post_action_screenshot_failures_to_not_testable(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, _, action_executor = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        id="tool-1",
                        function=FakeFunction(
                            name="goto_url",
                            arguments=json.dumps({"url": "http://fixture.local/modal"}),
                        ),
                    )
                ]
            )
        ],
        browser_manager=FailingBrowserManager(fail_on_capture_call=2),
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/start"),
        viewport=ViewportConfig(width=1280, height=800, device_scale_factor=1),
        claim="The modal opens on click",
        url="http://fixture.local/modal",
        navigation_hint=None,
    )

    assert action_executor.calls == [("goto_url", {"url": "http://fixture.local/modal"})]
    assert _field(result, "status") == "not_testable"
    assert "Failed to capture screenshot for step-01" in _field(result, "finding")
    assert _field(_field(result, "proof"), "screenshot_path").endswith("step-00-initial.webp")
    assert _field(_field(result, "proof"), "step") == 0
    assert _field(_field(result, "proof"), "after_action") is None
    assert _field(_field(result, "trace"), "steps_taken") == 1
    assert _field(_field(result, "trace"), "wrong_page_recovered") is True
    assert _field(_field(result, "trace"), "actions") == ["goto_url({'url': 'http://fixture.local/modal'})"]


@pytest.mark.asyncio
async def test_claim_verifier_preserves_partial_result_on_cancellation(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, _, _ = _build_claim_verifier(module, tmp_path, responses=[])
    verifier.n1_client = BlockingN1Client([])

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.01):
            await _call_verify(
                verifier,
                page=FakePage(url="http://fixture.local/page"),
                viewport=ViewportConfig(width=1280, height=800, device_scale_factor=1),
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
    assert _field(_field(partial, "proof"), "screenshot_path").endswith("step-00-initial.webp")
    assert _field(_field(partial, "proof"), "step") == 0
    assert _field(_field(partial, "trace"), "steps_taken") == 0
    assert _field(_field(partial, "trace"), "screenshot_paths")[0].endswith("step-00-initial.webp")


@pytest.mark.asyncio
async def test_claim_verifier_uses_stop_reason_in_force_stop_path(tmp_path: Path) -> None:
    module = _import_claim_verifier_module()
    verifier, _, action_executor = _build_claim_verifier(
        module,
        tmp_path,
        responses=[
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        id="tool-1",
                        function=FakeFunction(
                            name="goto_url",
                            arguments=json.dumps({"url": "http://fixture.local/modal"}),
                        ),
                    )
                ]
            ),
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        id="tool-2",
                        function=FakeFunction(
                            name="stop",
                            arguments=json.dumps({"reason": "Reached the step limit without enough evidence."}),
                        ),
                    )
                ]
            ),
        ],
    )

    result = await _call_verify(
        verifier,
        page=FakePage(url="http://fixture.local/start"),
        viewport=ViewportConfig(width=1280, height=800, device_scale_factor=1),
        claim="The modal opens on click",
        url="http://fixture.local/modal",
        navigation_hint=None,
    )

    assert action_executor.calls == [("goto_url", {"url": "http://fixture.local/modal"})]
    assert _field(result, "status") == "inconclusive"
    assert "Reached the step limit" in _field(result, "finding")
