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
