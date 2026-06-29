"""Error types for frontend-visualqa."""

from __future__ import annotations


class FrontendVisualQAError(Exception):
    """Base error for the package."""


class ConfigurationError(FrontendVisualQAError):
    """Raised when required local configuration is missing or invalid."""


class BrowserActionError(FrontendVisualQAError):
    """Raised when a Navigator tool call cannot be executed against Playwright."""


class NavigatorClientError(FrontendVisualQAError):
    """Raised when the Navigator client cannot complete a request."""


class NavigatorRequestTimeout(NavigatorClientError):
    """Raised when a single Navigator request exceeds its wall-clock deadline.

    httpx only enforces a *per-operation* (connect/read/write) timeout, which a
    long-lived HTTP/2 connection can reset indefinitely via keepalive/PING
    frames — leaving no *total* request bound. ``NavigatorClient`` enforces that
    bound itself with ``asyncio.timeout`` and raises this on expiry. It is
    treated as a transient error so the request is retried before failing.
    """
