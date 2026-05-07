"""Thin wrapper around the Yutori SDK for Navigator model calls."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Protocol

import httpx
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential
from yutori import AsyncYutoriClient
from yutori.navigator import N1_5_MODEL, TOOL_SET_CORE, estimate_messages_size_bytes, trim_images_to_fit

from frontend_visualqa.errors import NavigatorClientError


logger = logging.getLogger(__name__)

DEFAULT_MAX_REQUEST_BYTES = 9_500_000
DEFAULT_KEEP_RECENT_SCREENSHOTS = 6


def _schedule_close(client: Any, *, attr: str = "close") -> None:
    """Best-effort async close of a swapped-out HTTP client.

    Schedules the close as a background task on the running loop; called
    from sync code that can't ``await``. If no loop is running, the leak
    is bounded — the original client never sent a request and gets
    cleaned up by GC.
    """
    coro_factory = getattr(client, attr, None)
    if coro_factory is None:
        return
    try:
        asyncio.get_running_loop().create_task(coro_factory())
    except RuntimeError:
        logger.debug("No running loop available to close swapped-out client")


def enable_http2_on_yutori_client(yclient: Any, *, timeout_seconds: float) -> None:
    """Swap both httpx clients inside an ``AsyncYutoriClient`` for HTTP/2.

    Two distinct httpx clients live inside ``AsyncYutoriClient``:

    1. ``yclient.chat._openai_client`` — the OpenAI SDK's internal httpx
       client (used by the chat completions hot loop).
    2. ``yclient._client`` — yutori's own httpx client (used by
       ``get_usage()`` and the scouts/browsing/research namespaces).

    Neither is constructable with ``http2=True`` through public yutori SDK
    API, so we patch private attributes after construction. Each swap is
    fenced — if a yutori SDK rename breaks one, the other still upgrades,
    and we log a warning rather than crashing.

    Used by both ``NavigatorClient`` (for the verifier hot loop) and
    ``cli._preflight_verify_auth`` (for the standalone auth-check client).
    """
    _swap_chat_openai_to_http2(yclient, timeout_seconds=timeout_seconds)
    _swap_yutori_httpx_to_http2(yclient, timeout_seconds=timeout_seconds)


def _swap_chat_openai_to_http2(yclient: Any, *, timeout_seconds: float) -> None:
    try:
        from openai import AsyncOpenAI

        chat_ns = yclient.chat
        old_oai = chat_ns._openai_client  # type: ignore[attr-defined]
        api_key = old_oai.api_key
        base_url = str(old_oai.base_url)
        new_http2_client = httpx.AsyncClient(
            http2=True,
            timeout=timeout_seconds,
        )
        new_oai = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            http_client=new_http2_client,
        )
        chat_ns._openai_client = new_oai  # type: ignore[attr-defined]
        chat_ns.completions._client = new_oai  # type: ignore[attr-defined]
        _schedule_close(old_oai)
        logger.info("Navigator HTTP/2 transport enabled (chat completions)")
    except Exception:
        logger.warning(
            "Could not enable HTTP/2 on chat namespace; "
            "chat completions will use HTTP/1.1",
            exc_info=True,
        )


def _swap_yutori_httpx_to_http2(yclient: Any, *, timeout_seconds: float) -> None:
    try:
        old_httpx = yclient._client  # type: ignore[attr-defined]
        new_httpx = httpx.AsyncClient(
            http2=True,
            timeout=timeout_seconds,
        )
        yclient._client = new_httpx  # type: ignore[attr-defined]
        # Each namespace stores its own ref to the original client; update
        # them too so usage/scouts/browsing/research calls also use h2.
        for ns_name in ("scouts", "browsing", "research"):
            ns = getattr(yclient, ns_name, None)
            if ns is not None and hasattr(ns, "_client"):
                ns._client = new_httpx  # type: ignore[attr-defined]
        _schedule_close(old_httpx, attr="aclose")
        logger.info("Navigator HTTP/2 transport enabled (yutori SDK client)")
    except Exception:
        logger.warning(
            "Could not enable HTTP/2 on yutori SDK client; "
            "usage/auth preflight will use HTTP/1.1",
            exc_info=True,
        )


class SupportsChatCompletionCreate(Protocol):
    """Protocol for the small surface claim_verifier needs from the SDK client."""

    chat: Any

    async def close(self) -> None:
        """Close any underlying network resources."""


class NavigatorClient:
    """Own the Yutori SDK client lifecycle and request dispatch."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = "https://api.yutori.com/v1",
        model: str = N1_5_MODEL,
        tool_set: str | None = TOOL_SET_CORE,
        disable_tools: list[str] | None = None,
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
        self.tool_set = tool_set
        self.disable_tools = list(disable_tools) if disable_tools else None
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
        json_schema: dict[str, Any] | None = None,
        already_trimmed: bool = False,
    ) -> Any:
        """Call the Navigator model and return the full response.

        When *json_schema* is provided and the model emits structured JSON
        (instead of tool calls), the parsed result is available on
        ``response.parsed_json``.

        ``already_trimmed`` skips the size-estimation+trim pass when the
        caller has already trimmed (e.g. ``ClaimVerifier`` does this so the
        ``on_llm_start`` hook sees the post-trim payload). Each
        ``trim_messages`` call serializes the entire message list to JSON to
        estimate size — for screenshot-heavy traces that's a multi-MB dump
        per turn, so dedupe matters. External callers should keep the
        default ``False`` to stay protected.
        """

        client = await self._ensure_client()
        prepared_messages = messages if already_trimmed else self.trim_messages(messages)
        kwargs: dict[str, Any] = {}
        if tools:
            kwargs["tools"] = tools
        if json_schema is not None:
            kwargs["json_schema"] = json_schema
        if self._supports_tool_set():
            if self.tool_set is not None:
                kwargs["tool_set"] = self.tool_set
            if self.disable_tools:
                kwargs["disable_tools"] = self.disable_tools
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature

        request_started = time.perf_counter()
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self.max_retries + 1),
                # wait_exponential uses attempt_number - 1 internally, so this
                # preserves the original delay sequence: 0.5, 1.0, 2.0, ...
                wait=wait_exponential(
                    multiplier=self.initial_backoff_seconds,
                    max=self.max_backoff_seconds,
                ),
                retry=retry_if_exception(self._is_transient_error),
                before_sleep=self._log_retry,
                reraise=True,
            ):
                with attempt:
                    response = await client.chat.completions.create(
                        model=self.model,
                        messages=prepared_messages,
                        **kwargs,
                    )
        except Exception as exc:
            raise NavigatorClientError(f"Navigator request failed: {exc}") from exc

        # Wall-clock per Navigator call. Surfaced by `frontend-visualqa verify
        # -v` (INFO). Includes any retry+backoff time, so a single line can
        # account for both raw latency and transient-failure recovery cost.
        elapsed_ms = (time.perf_counter() - request_started) * 1000
        usage = getattr(response, "usage", None)
        if usage is not None:
            logger.info(
                "Navigator call %.0f ms — usage prompt=%s completion=%s total=%s",
                elapsed_ms,
                getattr(usage, "prompt_tokens", "?"),
                getattr(usage, "completion_tokens", "?"),
                getattr(usage, "total_tokens", "?"),
            )
        else:
            logger.info("Navigator call %.0f ms (no usage info)", elapsed_ms)
        return response

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
                "Trimmed %s screenshot(s) from the claim trace; request size is now %.2f MB",
                removed,
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

        self._client = AsyncYutoriClient(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout_seconds,
        )
        self._enable_http2(self._client)
        return self._client

    def _enable_http2(self, yclient: Any) -> None:
        """Swap both httpx clients inside this client's ``AsyncYutoriClient``
        for HTTP/2 equivalents. See ``enable_http2_on_yutori_client`` for
        details — kept as an instance method so subclasses can override.
        """
        enable_http2_on_yutori_client(yclient, timeout_seconds=self.timeout_seconds)

    def _log_retry(self, retry_state: Any) -> None:
        """Log retry timing before the next transient-error retry attempt."""

        logger.warning(
            "Transient Navigator failure on attempt %s/%s; retrying in %.2fs",
            retry_state.attempt_number,
            self.max_retries + 1,
            retry_state.next_action.sleep,
        )

    @staticmethod
    def _is_transient_error(exc: Exception) -> bool:
        if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
            return True

        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None) or getattr(exc, "status_code", None)
        return status_code in {408, 409, 425, 429, 500, 502, 503, 504}

    def _supports_tool_set(self) -> bool:
        """Return whether the configured model supports Navigator's tool_set option."""

        # tool_set is supported by n1.5+ models. Legacy n1 (and n1-experimental)
        # models do not accept it. Rather than maintaining a prefix list, reject
        # only the known-legacy patterns.
        m = str(self.model).strip().lower()
        return not (m.startswith("n1-") or m == "n1")
