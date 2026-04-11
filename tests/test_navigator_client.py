from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from frontend_visualqa.errors import NavigatorClientError

try:
    from frontend_visualqa.navigator_client import AsyncYutoriClient, NavigatorClient
except ModuleNotFoundError:
    pytestmark = pytest.mark.skip(reason="yutori SDK not installed")


class FakeCompletions:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def create(self, messages: Any, **kwargs: Any) -> Any:
        self.calls.append({"messages": messages, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeClient:
    def __init__(self, responses: list[Any]) -> None:
        self.completions = FakeCompletions(responses)
        self.chat = SimpleNamespace(completions=self.completions)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


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
