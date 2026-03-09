"""Shared models for frontend-visualqa."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ClaimStatus = Literal["pass", "fail", "inconclusive", "not_testable"]
OverallStatus = Literal["completed", "not_testable"]
ScreenshotStatus = Literal["completed", "not_testable"]
BrowserAction = Literal["status", "restart", "close", "set_viewport"]


class FrontendVisualQABaseModel(BaseModel):
    """Base model with consistent validation settings."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ViewportConfig(FrontendVisualQABaseModel):
    """Viewport and DPR for a browser session."""

    width: int = Field(default=1280, ge=320, le=4096)
    height: int = Field(default=800, ge=200, le=4096)
    device_scale_factor: float = Field(default=1.0, gt=0, le=4)


class VerifyVisualClaimsInput(FrontendVisualQABaseModel):
    """Top-level input for a verification run."""

    url: str
    claims: list[str] = Field(min_length=1)
    viewport: ViewportConfig = Field(default_factory=ViewportConfig)
    session_key: str = "default"
    reuse_session: bool = True
    reset_between_claims: bool = True
    max_steps_per_claim: int = Field(default=12, ge=1, le=50)
    claim_timeout_seconds: float | None = Field(default=120.0, gt=0, le=900)
    run_timeout_seconds: float | None = Field(default=300.0, gt=0, le=3_600)
    navigation_hint: str | None = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        return value

    @field_validator("claims")
    @classmethod
    def validate_claims(cls, value: list[str]) -> list[str]:
        normalized = [claim.strip() for claim in value if claim.strip()]
        if not normalized:
            raise ValueError("claims must contain at least one non-empty claim")
        return normalized


class ClaimResult(FrontendVisualQABaseModel):
    """Structured output for a single visual claim."""

    claim: str
    status: ClaimStatus
    summary: str
    final_url: str
    wrong_page_recovered: bool = False
    steps_taken: int = Field(default=0, ge=0)
    viewport: ViewportConfig
    screenshots: list[str] = Field(default_factory=list)
    action_trace: list[str] = Field(default_factory=list)
    trace_path: str | None = None


class RunResult(FrontendVisualQABaseModel):
    """Structured output for a verification run."""

    overall_status: OverallStatus
    runner_version: str = "0.1.0"
    session_key: str
    results: list[ClaimResult] = Field(default_factory=list)
    summary: str
    artifacts_dir: str


class ScreenshotResult(FrontendVisualQABaseModel):
    """Structured output for the take_screenshot helper."""

    status: ScreenshotStatus = "completed"
    session_key: str
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
