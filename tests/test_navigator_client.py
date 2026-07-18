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


class _FakeSDKClient:
    """Shared shape for the fake SDK clients below: wrap a completions fake in a `.chat` namespace."""

    def __init__(self, completions: Any) -> None:
        self.completions = completions
        self.chat = SimpleNamespace(completions=completions)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakeClient(_FakeSDKClient):
    def __init__(self, responses: list[Any]) -> None:
        super().__init__(FakeCompletions(responses))


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


class SleepyClient(_FakeSDKClient):
    def __init__(self, behaviors: list[tuple[float, Any]]) -> None:
        super().__init__(SleepyCompletions(behaviors))


def _make_response() -> SimpleNamespace:
    message = SimpleNamespace(
        content="done", tool_calls=None, model_dump=lambda exclude_none=True: {"role": "assistant"}
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


def _check_messages() -> list[dict[str, Any]]:
    """A fresh single-turn "Check" user message list.

    Returns a new list each call (not a shared constant) so tests that mutate
    the payload in place, like the trim_messages fallback test, don't corrupt
    state for other tests.
    """
    return [{"role": "user", "content": [{"type": "text", "text": "Check"}]}]


def test_navigator_client_imports_async_yutori_client_from_sdk() -> None:
    assert AsyncYutoriClient.__name__ == "AsyncYutoriClient"


@pytest.mark.asyncio
async def test_navigator_client_calls_sdk_with_provided_messages() -> None:
    response = _make_response()
    client = FakeClient([response])
    navigator_client = NavigatorClient(client=client)
    messages = _check_messages()

    result = await navigator_client.create(messages=messages)

    assert result is response
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
    response = _make_response()
    client = FakeClient([response])
    navigator_client = NavigatorClient(client=client, timeout_seconds=0.1)

    result = await navigator_client.create(messages=_check_messages())

    assert result is response
    assert len(client.completions.calls) == 1
    assert client.completions.calls[0]["tool_set"] == navigator_client.tool_set


@pytest.mark.asyncio
async def test_navigator_client_wraps_sdk_errors() -> None:
    client = FakeClient([RuntimeError("still failing")])
    navigator_client = NavigatorClient(client=client, timeout_seconds=0.1)

    with pytest.raises(NavigatorClientError):
        await navigator_client.create(messages=_check_messages())


@pytest.mark.asyncio
async def test_navigator_client_retries_transient_errors() -> None:
    response = _make_response()
    client = FakeClient([httpx.ReadTimeout("slow"), response])
    navigator_client = NavigatorClient(
        client=client,
        max_retries=1,
        initial_backoff_seconds=0.0,
        max_backoff_seconds=0.0,
    )

    result = await navigator_client.create(messages=_check_messages())

    assert result is response
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

    messages = _check_messages()
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
    response = _make_response()
    client = FakeClient([response])
    navigator_client = NavigatorClient(client=client, model="n1-latest")

    result = await navigator_client.create(messages=_check_messages())

    assert result is response
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
        await navigator_client.create(messages=_check_messages())

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

    result = await navigator_client.create(messages=_check_messages())

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
        await navigator_client.create(messages=_check_messages())

    # Not retried (it is not transient) and not re-wrapped as NavigatorClientError.
    assert len(client.completions.calls) == 1


# --- HTTP/2 client swap (enable_http2_on_yutori_client) ---


@pytest.mark.asyncio
async def test_build_http2_client_enables_http2_with_shared_limits() -> None:
    import frontend_visualqa.navigator_client as module

    client = module._build_http2_client(7.5)
    try:
        assert client.timeout.connect == 7.5
        assert client._transport._pool._http2 is True
    finally:
        await client.aclose()


def test_enable_http2_on_yutori_client_uses_shared_http2_client_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both swap paths delegate to the same ``_build_http2_client`` helper.

    Regression test for a duplication where ``_swap_chat_openai_to_http2`` and
    ``_swap_yutori_httpx_to_http2`` each independently constructed an identical
    ``httpx.AsyncClient(http2=True, timeout=..., limits=_HTTP2_LIMITS)``.
    """
    import openai

    import frontend_visualqa.navigator_client as module

    build_calls: list[float] = []
    built_clients: list[Any] = []

    def fake_build_http2_client(timeout_seconds: float) -> Any:
        build_calls.append(timeout_seconds)
        client = SimpleNamespace(marker="http2-client")
        built_clients.append(client)
        return client

    monkeypatch.setattr(module, "_build_http2_client", fake_build_http2_client)

    class FakeAsyncOpenAI:
        def __init__(self, *, api_key: str, base_url: str, timeout: float, http_client: Any) -> None:
            self.api_key = api_key
            self.base_url = base_url
            self.timeout = timeout
            self.http_client = http_client

    monkeypatch.setattr(openai, "AsyncOpenAI", FakeAsyncOpenAI)

    old_oai = SimpleNamespace(api_key="key-123", base_url="https://navigator.example/v1")
    chat_ns = SimpleNamespace(_openai_client=old_oai, completions=SimpleNamespace(_client=None))
    yclient = SimpleNamespace(chat=chat_ns, _client=SimpleNamespace())

    module.enable_http2_on_yutori_client(yclient, timeout_seconds=12.5)

    assert build_calls == [12.5, 12.5]
    assert chat_ns._openai_client.http_client is built_clients[0]
    assert chat_ns.completions._client is chat_ns._openai_client
    assert yclient._client is built_clients[1]


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
            await navigator_client.create(messages=_check_messages())

    # Cancelled mid-flight — never retried into a second attempt.
    assert len(client.completions.calls) == 1
