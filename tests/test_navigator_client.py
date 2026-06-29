from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from frontend_visualqa.errors import NavigatorClientError, NavigatorRequestTimeout

try:
    from frontend_visualqa.navigator_client import AsyncYutoriClient, NavigatorClient, wait_exponential
except ModuleNotFoundError:
    pytestmark = pytest.mark.skip(reason="yutori SDK not installed")


class FakeCompletions:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def create(self, messages: Any, **kwargs: Any) -> Any:
        self.calls.append({"messages": messages, **kwargs})
        response = self.responses.pop(0)
        # BaseException (not just Exception) so the fake can raise control-flow
        # exceptions like asyncio.CancelledError, which are BaseException.
        if isinstance(response, BaseException):
            raise response
        return response


class FakeClient:
    def __init__(self, responses: list[Any]) -> None:
        self.completions = FakeCompletions(responses)
        self.chat = SimpleNamespace(completions=self.completions)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class SleepyCompletions:
    """Fake completions whose calls sleep before resolving.

    Each behavior is ``(sleep_seconds, outcome)``: the call awaits
    ``asyncio.sleep(sleep_seconds)`` (which an enclosing ``asyncio.timeout`` can
    cancel) and then returns ``outcome`` or raises it if it is an exception.
    """

    def __init__(self, behaviors: list[tuple[float, Any]]) -> None:
        self.behaviors = list(behaviors)
        self.calls: list[dict[str, Any]] = []

    async def create(self, messages: Any, **kwargs: Any) -> Any:
        self.calls.append({"messages": messages, **kwargs})
        sleep_seconds, outcome = self.behaviors.pop(0)
        if sleep_seconds:
            await asyncio.sleep(sleep_seconds)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class SleepyClient:
    def __init__(self, behaviors: list[tuple[float, Any]]) -> None:
        self.completions = SleepyCompletions(behaviors)
        self.chat = SimpleNamespace(completions=self.completions)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _make_response() -> SimpleNamespace:
    message = SimpleNamespace(
        content="done", tool_calls=None, model_dump=lambda exclude_none=True: {"role": "assistant"}
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


def test_navigator_client_imports_async_yutori_client_from_sdk() -> None:
    assert AsyncYutoriClient.__name__ == "AsyncYutoriClient"


@pytest.mark.asyncio
async def test_navigator_client_calls_sdk_with_provided_messages() -> None:
    message = SimpleNamespace(
        content="done", tool_calls=None, model_dump=lambda exclude_none=True: {"role": "assistant"}
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )
    client = FakeClient([response])
    navigator_client = NavigatorClient(client=client)
    messages = [{"role": "user", "content": [{"type": "text", "text": "Check"}]}]

    result = await navigator_client.create(messages=messages)

    assert result is response
    assert result.choices[0].message is message
    assert client.completions.calls == [
        {
            "messages": messages,
            "model": navigator_client.model,
            "tool_set": navigator_client.tool_set,
            "temperature": navigator_client.temperature,
        }
    ]


@pytest.mark.asyncio
async def test_navigator_client_calls_sdk_once_and_returns_message() -> None:
    message = SimpleNamespace(
        content="done", tool_calls=None, model_dump=lambda exclude_none=True: {"role": "assistant"}
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )
    client = FakeClient([response])
    navigator_client = NavigatorClient(client=client, timeout_seconds=0.1)

    result = await navigator_client.create(messages=[{"role": "user", "content": [{"type": "text", "text": "Check"}]}])

    assert result is response
    assert result.choices[0].message is message
    assert len(client.completions.calls) == 1
    assert client.completions.calls[0]["tool_set"] == navigator_client.tool_set


@pytest.mark.asyncio
async def test_navigator_client_wraps_sdk_errors() -> None:
    client = FakeClient([RuntimeError("still failing")])
    navigator_client = NavigatorClient(client=client, timeout_seconds=0.1)

    with pytest.raises(NavigatorClientError):
        await navigator_client.create(messages=[{"role": "user", "content": [{"type": "text", "text": "Check"}]}])


@pytest.mark.asyncio
async def test_navigator_client_retries_transient_errors() -> None:
    message = SimpleNamespace(
        content="done", tool_calls=None, model_dump=lambda exclude_none=True: {"role": "assistant"}
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )
    client = FakeClient([httpx.ReadTimeout("slow"), response])
    navigator_client = NavigatorClient(
        client=client,
        max_retries=1,
        initial_backoff_seconds=0.0,
        max_backoff_seconds=0.0,
    )

    result = await navigator_client.create(messages=[{"role": "user", "content": [{"type": "text", "text": "Check"}]}])

    assert result is response
    assert result.choices[0].message is message
    assert len(client.completions.calls) == 2


def test_wait_exponential_matches_original_retry_delays() -> None:
    """Tenacity's wait strategy should preserve the original backoff sequence."""

    wait_strategy = wait_exponential(multiplier=0.5, max=4.0)

    class RetryState:
        def __init__(self, attempt_number: int) -> None:
            self.attempt_number = attempt_number

    waits = [wait_strategy(RetryState(attempt_number)) for attempt_number in range(1, 6)]

    assert waits == [0.5, 1.0, 2.0, 4.0, 4.0]


def test_navigator_client_trim_messages_uses_sdk_compatibility_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import frontend_visualqa.navigator_client as module

    messages = [{"role": "user", "content": [{"type": "text", "text": "Check"}]}]
    navigator_client = NavigatorClient(client=FakeClient([]), max_request_bytes=10)
    trim_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(module, "estimate_messages_size_bytes", lambda _: 1_000_000)

    def fake_trim_images_to_fit(payload: list[dict[str, Any]], **kwargs: Any) -> tuple[int, int]:
        trim_calls.append({"messages": payload, **kwargs})
        payload[:] = [{"role": "user", "content": [{"type": "text", "text": "trimmed"}]}]
        return 128, 1

    monkeypatch.setattr(module, "trim_images_to_fit", fake_trim_images_to_fit)

    trimmed_messages = navigator_client.trim_messages(messages)

    assert trim_calls
    assert trimmed_messages[0]["content"][0]["text"] == "trimmed"
    assert messages[0]["content"][0]["text"] == "trimmed"


@pytest.mark.asyncio
async def test_navigator_client_omits_tool_set_for_legacy_n1_models() -> None:
    message = SimpleNamespace(
        content="done", tool_calls=None, model_dump=lambda exclude_none=True: {"role": "assistant"}
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )
    client = FakeClient([response])
    navigator_client = NavigatorClient(client=client, model="n1-latest")

    result = await navigator_client.create(messages=[{"role": "user", "content": [{"type": "text", "text": "Check"}]}])

    assert result is response
    assert result.choices[0].message is message
    assert "tool_set" not in client.completions.calls[0]


# --- ENG-5206: per-request wall-clock timeout + cancellation propagation ---


@pytest.mark.asyncio
async def test_navigator_client_bounds_stalled_request_with_per_request_timeout() -> None:
    """A request that never returns within timeout_seconds fails instead of hanging.

    Reproduces the ENG-5206 hang: httpx's per-read timeout can't bound a stalled
    HTTP/2 stream, so create() must enforce a total wall-clock deadline itself.
    """
    client = SleepyClient([(5.0, _make_response())])
    navigator_client = NavigatorClient(client=client, timeout_seconds=0.05, max_retries=0)

    with pytest.raises(NavigatorRequestTimeout):
        await navigator_client.create(messages=[{"role": "user", "content": [{"type": "text", "text": "Check"}]}])

    # The hung request was cancelled by the per-request timeout, not awaited to completion.
    assert len(client.completions.calls) == 1


@pytest.mark.asyncio
async def test_navigator_client_retries_after_per_request_timeout() -> None:
    """A per-request timeout is transient: retry, and a recovered request succeeds."""
    response = _make_response()
    client = SleepyClient([(5.0, None), (0.0, response)])
    navigator_client = NavigatorClient(
        client=client,
        timeout_seconds=0.05,
        max_retries=1,
        initial_backoff_seconds=0.0,
        max_backoff_seconds=0.0,
    )

    result = await navigator_client.create(messages=[{"role": "user", "content": [{"type": "text", "text": "Check"}]}])

    assert result is response
    assert len(client.completions.calls) == 2


@pytest.mark.asyncio
async def test_navigator_client_does_not_retry_or_wrap_cancelled_error() -> None:
    """CancelledError must propagate untouched so asyncio.timeout deadlines fire (ENG-5206)."""
    client = FakeClient([asyncio.CancelledError()])
    navigator_client = NavigatorClient(
        client=client,
        max_retries=2,
        initial_backoff_seconds=0.0,
        max_backoff_seconds=0.0,
    )

    with pytest.raises(asyncio.CancelledError):
        await navigator_client.create(messages=[{"role": "user", "content": [{"type": "text", "text": "Check"}]}])

    # Not retried (it is not transient) and not re-wrapped as NavigatorClientError.
    assert len(client.completions.calls) == 1


@pytest.mark.asyncio
async def test_outer_asyncio_timeout_cancels_hung_request() -> None:
    """A claim/run-level asyncio.timeout tears down a hung create() through the retry loop.

    The per-request timeout is left long (60s) so it does NOT fire; the outer
    deadline must cancel the in-flight request and surface as TimeoutError rather
    than being swallowed/retried by tenacity. This is the backstop that failed to
    engage in ENG-5206.
    """
    client = SleepyClient([(5.0, _make_response())])
    navigator_client = NavigatorClient(
        client=client,
        timeout_seconds=60.0,
        max_retries=2,
        initial_backoff_seconds=0.0,
        max_backoff_seconds=0.0,
    )

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await navigator_client.create(messages=[{"role": "user", "content": [{"type": "text", "text": "Check"}]}])

    # Cancelled mid-flight — never retried into a second attempt.
    assert len(client.completions.calls) == 1
