"""Thin wrapper around the Yutori SDK for n1 calls."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Protocol

import httpx

try:
    from yutori import AsyncYutoriClient
    from yutori.n1 import estimate_messages_size_bytes, trim_images_to_fit
except ModuleNotFoundError:
    class AsyncYutoriClient:  # type: ignore[no-redef]
        """Fallback stub when the Yutori SDK is unavailable."""

        def __init__(self, *_: Any, **__: Any) -> None:
            raise ModuleNotFoundError("No module named 'yutori'")

    def estimate_messages_size_bytes(messages: list[dict[str, Any]]) -> int:
        """Fallback byte estimation when the SDK helper is unavailable."""

        return len(json.dumps(messages).encode("utf-8"))

    def _fallback_trim_images_to_fit(
        messages: list[dict[str, Any]],
        *,
        max_bytes: int,
        keep_recent: int,
    ) -> tuple[int, int]:
        """Trim oldest image payloads when the SDK helper is unavailable.

        ``keep_recent`` protects the last N *messages* that contain images,
        matching the SDK's semantics (one screenshot per message in typical
        verification flows).
        """

        def collect_removable_slots() -> list[tuple[list[dict[str, Any]], int]]:
            """Return image slots eligible for removal (oldest first).

            Groups by message so ``keep_recent`` protects whole messages,
            not individual image items within a message.
            """
            per_message: list[list[tuple[list[dict[str, Any]], int]]] = []
            for message in messages:
                content = message.get("content")
                if not isinstance(content, list):
                    continue
                msg_slots: list[tuple[list[dict[str, Any]], int]] = []
                for index, item in enumerate(content):
                    if isinstance(item, dict) and item.get("type") in {"image_url", "input_image"}:
                        msg_slots.append((content, index))
                if msg_slots:
                    per_message.append(msg_slots)
            # Protect the last keep_recent *messages*
            removable_messages = per_message[:-keep_recent] if keep_recent > 0 else per_message
            # Flatten: return individual slots from removable messages, oldest first
            return [slot for group in removable_messages for slot in group]

        removed = 0
        size_bytes = estimate_messages_size_bytes(messages)
        while size_bytes > max_bytes:
            removable = collect_removable_slots()
            if not removable:
                break
            content, index = removable[0]
            content.pop(index)
            removed += 1
            size_bytes = estimate_messages_size_bytes(messages)
        return size_bytes, removed

    def trim_images_to_fit(
        messages: list[dict[str, Any]],
        *,
        max_bytes: int,
        keep_recent: int,
    ) -> tuple[int, int]:
        """Fallback image trimming when the SDK helper is unavailable."""

        return _fallback_trim_images_to_fit(
            messages,
            max_bytes=max_bytes,
            keep_recent=keep_recent,
        )

from frontend_visualqa.errors import N1ClientError


logger = logging.getLogger(__name__)

DEFAULT_MAX_REQUEST_BYTES = 9_500_000
DEFAULT_KEEP_RECENT_SCREENSHOTS = 6


class SupportsChatCompletionCreate(Protocol):
    """Protocol for the small surface claim_verifier needs from the SDK client."""

    chat: Any

    async def close(self) -> None:
        """Close any underlying network resources."""


class N1Client:
    """Own the n1 SDK client lifecycle and request dispatch."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = "https://api.yutori.com/v1",
        model: str = "n1-latest",
        temperature: float = 0.3,
        timeout_seconds: float = 60.0,
        max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
        keep_recent_screenshots: int = DEFAULT_KEEP_RECENT_SCREENSHOTS,
        max_retries: int = 2,
        initial_backoff_seconds: float = 0.5,
        max_backoff_seconds: float = 4.0,
        client: SupportsChatCompletionCreate | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self.max_request_bytes = max_request_bytes
        self.keep_recent_screenshots = keep_recent_screenshots
        self.max_retries = max_retries
        self.initial_backoff_seconds = initial_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self._client = client
        self._owns_client = client is None

    async def create(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> Any:
        """Call n1 and return the assistant message for the next step."""

        client = await self._ensure_client()
        prepared_messages = self.trim_messages(messages)
        kwargs: dict[str, Any] = {}
        if tools:
            kwargs["tools"] = tools
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await client.chat.completions.create(
                    model=self.model,
                    messages=prepared_messages,
                    **kwargs,
                )
                break
            except Exception as exc:  # noqa: PERF203 - retry loop is intentional
                last_error = exc
                if attempt >= self.max_retries or not self._is_transient_error(exc):
                    raise N1ClientError(f"n1 request failed: {exc}") from exc
                delay = min(self.initial_backoff_seconds * (2**attempt), self.max_backoff_seconds)
                logger.warning(
                    "Transient n1 failure on attempt %s/%s; retrying in %.2fs",
                    attempt + 1,
                    self.max_retries + 1,
                    delay,
                )
                await asyncio.sleep(delay)
        else:  # pragma: no cover - defensive, loop always breaks or raises
            raise N1ClientError(f"n1 request failed: {last_error}")

        usage = getattr(response, "usage", None)
        if usage is not None:
            logger.info(
                "n1 usage prompt=%s completion=%s total=%s",
                getattr(usage, "prompt_tokens", "?"),
                getattr(usage, "completion_tokens", "?"),
                getattr(usage, "total_tokens", "?"),
            )
        try:
            return response.choices[0].message
        except Exception as exc:
            raise N1ClientError(f"n1 response did not contain a message choice: {exc}") from exc

    def trim_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Trim oversized image payloads while preserving recent screenshots."""

        size_bytes = estimate_messages_size_bytes(messages)
        if size_bytes <= self.max_request_bytes:
            return messages

        trimmed_messages = messages
        trimmed_size, removed = trim_images_to_fit(
            trimmed_messages,
            max_bytes=self.max_request_bytes,
            keep_recent=self.keep_recent_screenshots,
        )

        if removed:
            logger.info(
                "Trimmed %s screenshot(s) from the claim history; request size is now %.2f MB",
                removed,
                trimmed_size / (1024 * 1024),
            )
        elif trimmed_size > self.max_request_bytes:
            logger.warning(
                "Request still exceeds the %.2f MB limit after compatibility trimming (%.2f MB).",
                self.max_request_bytes / (1024 * 1024),
                trimmed_size / (1024 * 1024),
            )
        return trimmed_messages

    async def close(self) -> None:
        """Close the SDK client if this instance created it."""

        if self._client is not None and self._owns_client:
            await self._client.close()
            self._client = None

    async def _ensure_client(self) -> SupportsChatCompletionCreate:
        if self._client is not None:
            return self._client

        try:
            self._client = AsyncYutoriClient(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout_seconds,
            )
        except ModuleNotFoundError as exc:
            raise N1ClientError(
                "The Yutori SDK is not installed. Install the optional 'yutori' dependency to use the live n1 client."
            ) from exc
        return self._client

    @staticmethod
    def _is_transient_error(exc: Exception) -> bool:
        if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
            return True

        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None) or getattr(exc, "status_code", None)
        return status_code in {408, 409, 425, 429, 500, 502, 503, 504}
