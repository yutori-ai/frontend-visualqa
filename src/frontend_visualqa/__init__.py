"""frontend-visualqa package."""

from __future__ import annotations

from importlib import import_module
from typing import Any


__version__ = "0.3.8"

_LAZY_EXPORTS = {
    "ActionExecutor": ("frontend_visualqa.actions", "ActionExecutor"),
    "ArtifactManager": ("frontend_visualqa.artifacts", "ArtifactManager"),
    "RunArtifacts": ("frontend_visualqa.artifacts", "RunArtifacts"),
    "BrowserManager": ("frontend_visualqa.browser", "BrowserManager"),
    "BrowserSession": ("frontend_visualqa.browser", "BrowserSession"),
    "ClaimVerifier": ("frontend_visualqa.claim_verifier", "ClaimVerifier"),
    "FrontendVisualQAError": ("frontend_visualqa.errors", "FrontendVisualQAError"),
    "ConfigurationError": ("frontend_visualqa.errors", "ConfigurationError"),
    "BrowserActionError": ("frontend_visualqa.errors", "BrowserActionError"),
    "N1ClientError": ("frontend_visualqa.errors", "N1ClientError"),
    "N1Client": ("frontend_visualqa.n1_client", "N1Client"),
    "VisualQARunner": ("frontend_visualqa.runner", "VisualQARunner"),
    "BrowserConfig": ("frontend_visualqa.schemas", "BrowserConfig"),
    "BrowserMode": ("frontend_visualqa.schemas", "BrowserMode"),
    "BrowserSessionStatus": ("frontend_visualqa.schemas", "BrowserSessionStatus"),
    "BrowserStatusResult": ("frontend_visualqa.schemas", "BrowserStatusResult"),
    "ClaimResult": ("frontend_visualqa.schemas", "ClaimResult"),
    "RunResult": ("frontend_visualqa.schemas", "RunResult"),
    "ScreenshotResult": ("frontend_visualqa.schemas", "ScreenshotResult"),
    "VerifyVisualClaimsInput": ("frontend_visualqa.schemas", "VerifyVisualClaimsInput"),
    "ViewportConfig": ("frontend_visualqa.schemas", "ViewportConfig"),
}

__all__ = sorted(_LAZY_EXPORTS)


def __getattr__(name: str) -> Any:
    if name not in _LAZY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = _LAZY_EXPORTS[name]
    module = import_module(module_name)
    value = getattr(module, attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(list(globals()) + __all__)
