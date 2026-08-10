from __future__ import annotations

from types import SimpleNamespace

import pytest

from frontend_visualqa.hook_adapter import VisualQAHookAdapter


def _response(*, content: str | None, tool_calls: list[object] | None) -> SimpleNamespace:
    """Build a chat-completions-shaped response: ``response.choices[0].message``."""
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _message(*, content: str | None, tool_calls: list[object] | None) -> SimpleNamespace:
    """Build an already-unwrapped message (no ``.choices`` attribute)."""
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def test_current_turn_reasoning_defaults_to_none() -> None:
    adapter = VisualQAHookAdapter(overlay=None)

    assert adapter.current_turn_reasoning is None


@pytest.mark.asyncio
async def test_on_llm_end_sets_reasoning_when_content_and_tool_calls_present() -> None:
    adapter = VisualQAHookAdapter(overlay=None)

    await adapter.on_llm_end(response=_response(content="I should click the button", tool_calls=[object()]))

    assert adapter.current_turn_reasoning == "I should click the button"


@pytest.mark.asyncio
async def test_on_llm_end_clears_reasoning_when_no_tool_calls() -> None:
    adapter = VisualQAHookAdapter(overlay=None)

    await adapter.on_llm_end(response=_response(content="Some reasoning", tool_calls=None))

    assert adapter.current_turn_reasoning is None


@pytest.mark.asyncio
async def test_on_llm_end_clears_reasoning_when_tool_calls_list_is_empty() -> None:
    adapter = VisualQAHookAdapter(overlay=None)

    await adapter.on_llm_end(response=_response(content="Some reasoning", tool_calls=[]))

    assert adapter.current_turn_reasoning is None


@pytest.mark.asyncio
async def test_on_llm_end_clears_reasoning_when_no_content() -> None:
    adapter = VisualQAHookAdapter(overlay=None)

    await adapter.on_llm_end(response=_response(content=None, tool_calls=[object()]))

    assert adapter.current_turn_reasoning is None


@pytest.mark.asyncio
async def test_on_llm_end_accepts_already_unwrapped_message() -> None:
    """claim_verifier passes an already-unwrapped assistant message (no ``.choices``)."""
    adapter = VisualQAHookAdapter(overlay=None)

    await adapter.on_llm_end(response=_message(content="Reasoning text", tool_calls=[object()]))

    assert adapter.current_turn_reasoning == "Reasoning text"


@pytest.mark.asyncio
async def test_on_llm_end_overwrites_previous_reasoning_each_turn() -> None:
    adapter = VisualQAHookAdapter(overlay=None)

    await adapter.on_llm_end(response=_response(content="First turn", tool_calls=[object()]))
    await adapter.on_llm_end(response=_response(content="Second turn", tool_calls=None))

    assert adapter.current_turn_reasoning is None


def test_record_action_event_appends_trace_event_with_current_reasoning() -> None:
    adapter = VisualQAHookAdapter(overlay=None)
    adapter._current_turn_reasoning = "clicking the submit button"

    adapter.record_action_event(
        step=1,
        action="click",
        action_args={"ref": "e5"},
        output_preview="clicked",
        screenshot_path="/tmp/shot.webp",
    )

    assert len(adapter.events) == 1
    event = adapter.events[0]
    assert event.type == "action"
    assert event.step == 1
    assert event.reasoning == "clicking the submit button"
    assert event.action == "click"
    assert event.action_args == {"ref": "e5"}
    assert event.output_preview == "clicked"
    assert event.screenshot_path == "/tmp/shot.webp"


def test_record_action_event_defaults_action_args_to_empty_dict_when_none() -> None:
    adapter = VisualQAHookAdapter(overlay=None)

    adapter.record_action_event(step=2, action="scroll", action_args=None, output_preview=None, screenshot_path=None)

    assert adapter.events[0].action_args == {}


def test_record_verdict_event_appends_trace_event_with_current_reasoning() -> None:
    adapter = VisualQAHookAdapter(overlay=None)
    adapter._current_turn_reasoning = "the button is visibly disabled"

    adapter.record_verdict_event(
        step=3,
        source="json_schema",
        raw_status="failed",
        raw_finding="Button is disabled",
        status="failed",
        finding="Button is disabled",
    )

    assert len(adapter.events) == 1
    event = adapter.events[0]
    assert event.type == "verdict"
    assert event.step == 3
    assert event.reasoning == "the button is visibly disabled"
    assert event.verdict_source == "json_schema"
    assert event.raw_verdict_status == "failed"
    assert event.raw_finding == "Button is disabled"
    assert event.verdict_status == "failed"
    assert event.finding == "Button is disabled"


def test_record_verdict_event_accepts_none_step() -> None:
    adapter = VisualQAHookAdapter(overlay=None)

    adapter.record_verdict_event(
        step=None,
        source="force_stop",
        raw_status="inconclusive",
        raw_finding="stopped early",
        status="inconclusive",
        finding="stopped early",
    )

    assert adapter.events[0].step is None


def test_events_accumulate_in_order_across_multiple_calls() -> None:
    adapter = VisualQAHookAdapter(overlay=None)

    adapter.record_action_event(step=1, action="click", action_args=None, output_preview=None, screenshot_path=None)
    adapter.record_verdict_event(
        step=1,
        source="json_schema",
        raw_status="passed",
        raw_finding="ok",
        status="passed",
        finding="ok",
    )

    assert [event.type for event in adapter.events] == ["action", "verdict"]
