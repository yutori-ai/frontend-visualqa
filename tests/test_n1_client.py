from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from frontend_visualqa.errors import N1ClientError
from frontend_visualqa.n1_client import AsyncYutoriClient, N1Client


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


def test_n1_client_imports_async_yutori_client_from_sdk() -> None:
    assert AsyncYutoriClient.__name__ == "AsyncYutoriClient"


@pytest.mark.asyncio
async def test_n1_client_calls_sdk_with_provided_messages() -> None:
    message = SimpleNamespace(
        content="done", tool_calls=None, model_dump=lambda exclude_none=True: {"role": "assistant"}
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )
    client = FakeClient([response])
    n1_client = N1Client(client=client)
    messages = [{"role": "user", "content": [{"type": "text", "text": "Check"}]}]

    result = await n1_client.create(messages=messages)

    assert result is message
    assert client.completions.calls == [
        {
            "messages": messages,
            "model": n1_client.model,
            "temperature": n1_client.temperature,
        }
    ]


@pytest.mark.asyncio
async def test_n1_client_calls_sdk_once_and_returns_message() -> None:
    message = SimpleNamespace(
        content="done", tool_calls=None, model_dump=lambda exclude_none=True: {"role": "assistant"}
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )
    client = FakeClient([response])
    n1_client = N1Client(client=client, timeout_seconds=0.1)

    result = await n1_client.create(messages=[{"role": "user", "content": [{"type": "text", "text": "Check"}]}])

    assert result is message
    assert len(client.completions.calls) == 1


@pytest.mark.asyncio
async def test_n1_client_wraps_sdk_errors() -> None:
    client = FakeClient([RuntimeError("still failing")])
    n1_client = N1Client(client=client, timeout_seconds=0.1)

    with pytest.raises(N1ClientError):
        await n1_client.create(messages=[{"role": "user", "content": [{"type": "text", "text": "Check"}]}])


@pytest.mark.asyncio
async def test_n1_client_retries_transient_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    import frontend_visualqa.n1_client as module

    message = SimpleNamespace(
        content="done", tool_calls=None, model_dump=lambda exclude_none=True: {"role": "assistant"}
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )
    client = FakeClient([httpx.ReadTimeout("slow"), response])
    n1_client = N1Client(
        client=client,
        max_retries=1,
        initial_backoff_seconds=0.0,
        max_backoff_seconds=0.0,
    )

    async def _noop_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(module.asyncio, "sleep", _noop_sleep)

    result = await n1_client.create(messages=[{"role": "user", "content": [{"type": "text", "text": "Check"}]}])

    assert result is message
    assert len(client.completions.calls) == 2


def test_n1_client_trim_messages_uses_sdk_compatibility_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import frontend_visualqa.n1_client as module

    messages = [{"role": "user", "content": [{"type": "text", "text": "Check"}]}]
    n1_client = N1Client(client=FakeClient([]), max_request_bytes=10)
    trim_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(module, "estimate_messages_size_bytes", lambda _: 1_000_000)

    def fake_trim_images_to_fit(payload: list[dict[str, Any]], **kwargs: Any) -> tuple[int, int]:
        trim_calls.append({"messages": payload, **kwargs})
        payload[:] = [{"role": "user", "content": [{"type": "text", "text": "trimmed"}]}]
        return 128, 1

    monkeypatch.setattr(module, "trim_images_to_fit", fake_trim_images_to_fit)

    trimmed_messages = n1_client.trim_messages(messages)

    assert trim_calls
    assert trimmed_messages[0]["content"][0]["text"] == "trimmed"
    assert messages[0]["content"][0]["text"] == "trimmed"


def test_n1_client_fallback_trim_removes_oldest_images_when_sdk_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    import frontend_visualqa.n1_client as module

    messages = [
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + "a" * 500}}]},
        {"role": "tool", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + "b" * 500}}]},
        {"role": "user", "content": [{"type": "text", "text": "keep"}]},
    ]
    n1_client = N1Client(client=FakeClient([]), max_request_bytes=400, keep_recent_screenshots=1)

    monkeypatch.setattr(module, "estimate_messages_size_bytes", lambda payload: len(json.dumps(payload).encode("utf-8")))

    trimmed_messages = n1_client.trim_messages(messages)

    image_items = [
        item
        for message in trimmed_messages
        for item in message.get("content", [])
        if isinstance(item, dict) and item.get("type") == "image_url"
    ]
    assert len(image_items) == 1
    assert "b" * 100 in image_items[0]["image_url"]["url"]
