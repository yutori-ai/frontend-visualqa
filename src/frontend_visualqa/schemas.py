"""Shared models for frontend-visualqa."""

from __future__ import annotations

import time
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from frontend_visualqa import __version__


ClaimStatus = Literal["passed", "failed", "inconclusive", "not_testable"]
OverallStatus = Literal["completed", "not_testable"]
ScreenshotStatus = Literal["completed", "not_testable"]
BrowserAction = Literal["status", "restart", "close", "set_viewport"]
DEFAULT_PERSISTENT_USER_DATA_DIR = Path("~/.cache/frontend-visualqa/browser-profile").expanduser()


def validate_url(url: str) -> str:
    """Raise ValueError if *url* does not start with http:// or https://."""
    if not url.startswith(("http://", "https://")):
        raise ValueError("url must start with http:// or https://")
    return url


class BrowserMode(str, Enum):
    """Supported Playwright session ownership strategies."""

    ephemeral = "ephemeral"
    persistent = "persistent"


class FrontendVisualQABaseModel(BaseModel):
    """Base model with consistent validation settings."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ViewportConfig(FrontendVisualQABaseModel):
    """Viewport and DPR for a browser session."""

    width: int = Field(default=1280, ge=320, le=4096)
    height: int = Field(default=800, ge=200, le=4096)
    device_scale_factor: float = Field(default=1.0, gt=0, le=4)


class ClaimProof(FrontendVisualQABaseModel):
    """Primary evidence for a claim verdict."""

    screenshot_path: str
    step: int = Field(ge=0)
    after_action: str | None = None
    text: str | None = None
    text_path: str | None = None


class ClaimPage(FrontendVisualQABaseModel):
    """Page context for a claim verdict."""

    url: str
    viewport: ViewportConfig


class TraceEvent(FrontendVisualQABaseModel):
    """Canonical machine-readable event emitted during claim verification."""

    type: Literal["action", "verdict"]
    step: int | None = None
    reasoning: str | None = None
    action: str | None = None
    action_args: dict[str, Any] | None = None
    output_preview: str | None = None
    screenshot_path: str | None = None
    verdict_status: ClaimStatus | None = None
    verdict_source: Literal["record_claim_result", "fallback_content", "force_stop", "legacy_stop"] | None = None
    finding: str | None = None
    timestamp_ms: int = Field(default_factory=lambda: int(time.time() * 1000))


class ClaimTrace(FrontendVisualQABaseModel):
    """Execution trace: actions taken and screenshots captured while verifying a claim."""

    steps_taken: int = Field(default=0, ge=0)
    wrong_page_recovered: bool = False
    screenshot_paths: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    events: list[TraceEvent] = Field(default_factory=list, exclude=True)
    trace_path: str | None = None


class BrowserConfig(FrontendVisualQABaseModel):
    """Browser lifecycle configuration shared by CLI and MCP flows."""

    mode: BrowserMode = BrowserMode.ephemeral
    user_data_dir: str | None = None
    headless: bool = True
    visualize: bool = False
    navigation_timeout_ms: int = Field(default=20_000, ge=1)
    settle_delay_seconds: float = Field(default=1.0, ge=0, le=60)

    @field_validator("user_data_dir")
    @classmethod
    def normalize_user_data_dir(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return str(Path(value).expanduser())

    @model_validator(mode="after")
    def apply_persistent_defaults(self) -> "BrowserConfig":
        if self.mode == BrowserMode.persistent and self.user_data_dir is None:
            self.user_data_dir = str(DEFAULT_PERSISTENT_USER_DATA_DIR)
        return self

    @property
    def resolved_user_data_dir(self) -> str | None:
        if self.mode != BrowserMode.persistent:
            return self.user_data_dir
        return self.user_data_dir or str(DEFAULT_PERSISTENT_USER_DATA_DIR)


class VerifyVisualClaimsInput(FrontendVisualQABaseModel):
    """Top-level input for a verification run."""

    url: str
    claims: list[str] = Field(min_length=1)
    claim_navigation_hints: list[str | None] | None = None
    viewport: ViewportConfig = Field(default_factory=ViewportConfig)
    session_key: str = "default"
    run_name: str | None = None
    reuse_session: bool = True
    reset_between_claims: bool = True
    visualize: bool | None = None
    max_steps_per_claim: int = Field(default=12, ge=1, le=50)
    claim_timeout_seconds: float | None = Field(default=120.0, gt=0, le=900)
    run_timeout_seconds: float | None = Field(default=300.0, gt=0, le=3_600)
    navigation_hint: str | None = None

    @field_validator("url")
    @classmethod
    def validate_url_field(cls, value: str) -> str:
        return validate_url(value)

    @field_validator("claims")
    @classmethod
    def validate_claims(cls, value: list[str]) -> list[str]:
        normalized = [claim.strip() for claim in value]
        if any(not claim for claim in normalized):
            raise ValueError("claims must not contain empty strings")
        if not normalized:
            raise ValueError("claims must contain at least one non-empty claim")
        return normalized

    @field_validator("claim_navigation_hints")
    @classmethod
    def normalize_claim_navigation_hints(cls, value: list[str | None] | None) -> list[str | None] | None:
        if value is None:
            return None
        normalized: list[str | None] = []
        for hint in value:
            if hint is None:
                normalized.append(None)
                continue
            stripped = hint.strip()
            normalized.append(stripped or None)
        return normalized

    @model_validator(mode="after")
    def validate_claim_navigation_hint_alignment(self) -> "VerifyVisualClaimsInput":
        if self.claim_navigation_hints is not None and len(self.claim_navigation_hints) != len(self.claims):
            raise ValueError("claim_navigation_hints must match claims length")
        return self


class ClaimResult(FrontendVisualQABaseModel):
    """Structured output for a single visual claim."""

    claim: str
    status: ClaimStatus
    finding: str
    proof: ClaimProof | None = None
    page: ClaimPage
    trace: ClaimTrace = Field(default_factory=ClaimTrace)


class RunResult(FrontendVisualQABaseModel):
    """Structured output for a verification run."""

    overall_status: OverallStatus
    runner_version: str = __version__
    started_at: float | None = None
    completed_at: float | None = None
    session_key: str
    run_name: str | None = None
    results: list[ClaimResult] = Field(default_factory=list)
    summary: str
    artifacts_dir: str


class ScreenshotResult(FrontendVisualQABaseModel):
    """Structured output for the take_screenshot helper."""

    status: ScreenshotStatus = "completed"
    session_key: str
    run_name: str | None = None
    final_url: str
    viewport: ViewportConfig
    screenshot_path: str | None = None
    summary: str | None = None


class BrowserSessionStatus(FrontendVisualQABaseModel):
    """Inspectable state for a single browser session."""

    session_key: str
    browser_open: bool
    current_url: str | None = None
    viewport: ViewportConfig


class BrowserStatusResult(FrontendVisualQABaseModel):
    """Aggregated browser status across all sessions."""

    browser_running: bool
    browser_mode: BrowserMode = BrowserMode.ephemeral
    user_data_dir: str | None = None
    sessions: list[BrowserSessionStatus] = Field(default_factory=list)


class ManageBrowserInput(FrontendVisualQABaseModel):
    """Input contract for browser management helpers."""

    action: BrowserAction
    session_key: str = "default"
    viewport: ViewportConfig | None = None


class RunArtifactsSummary(FrontendVisualQABaseModel):
    """Serializable description of the files generated for a run."""

    run_id: str
    run_dir: str

    @classmethod
    def from_path(cls, run_id: str, path: Path) -> "RunArtifactsSummary":
        return cls(run_id=run_id, run_dir=str(path))
