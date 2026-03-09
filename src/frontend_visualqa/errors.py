"""Error types for frontend-visualqa."""

from __future__ import annotations


class FrontendVisualQAError(Exception):
    """Base error for the package."""


class ConfigurationError(FrontendVisualQAError):
    """Raised when required local configuration is missing or invalid."""


class BrowserActionError(FrontendVisualQAError):
    """Raised when an n1 tool call cannot be executed against Playwright."""


class N1ClientError(FrontendVisualQAError):
    """Raised when the n1 client cannot complete a request."""
