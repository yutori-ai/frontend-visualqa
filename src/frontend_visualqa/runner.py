"""Multi-claim orchestration for frontend-visualqa."""

from __future__ import annotations

import asyncio
import importlib
import logging
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, TYPE_CHECKING

import httpx

from frontend_visualqa.artifacts import ArtifactManager, RunArtifacts
from frontend_visualqa.browser import BrowserManager
from frontend_visualqa.claim_parser import ParsedClaimsFile
from frontend_visualqa.reporters import get_reporters
from frontend_visualqa.utils import safe_callback_call
from frontend_visualqa.schemas import (
    BrowserConfig,
    BrowserMode,
    BrowserStatusResult,
    ClaimPage,
    ClaimResult,
    ClaimStatus,
    ClaimTrace,
    ManageBrowserInput,
    RunResult,
    ScreenshotResult,
    VerifyVisualClaimsInput,
    ViewportConfig,
    _pydantic_field_default,
    coerce_optional_viewport,
    coerce_viewport,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from frontend_visualqa.claim_verifier import ClaimVerifier
    from frontend_visualqa.navigator_client import NavigatorClient
else:
    ClaimVerifier = None  # type: ignore[assignment]
    NavigatorClient = None  # type: ignore[assignment]


_TimeoutScope = Literal["claim", "run"]

# (no-timeout message, with-timeout template using {seconds})
_TIMEOUT_FINDING_TEMPLATES: dict[_TimeoutScope, tuple[str, str]] = {
    "claim": (
        "Claim verification timed out before a verdict was recorded.",
        "Claim verification timed out after {seconds} before a verdict was recorded.",
    ),
    "run": (
        "Run timed out before this claim could finish.",
        "Run timed out after {seconds} before this claim could finish.",
    ),
}


# Map deferred class name -> source module. The imports are deferred to
# runtime (rather than top-of-file) to break circular-import paths between
# runner.py and the claim_verifier / navigator_client modules.
_DEFERRED_IMPORTS: dict[str, str] = {
    "ClaimVerifier": "frontend_visualqa.claim_verifier",
    "NavigatorClient": "frontend_visualqa.navigator_client",
}


def _load_class(name: str) -> Any:
    """Return ``frontend_visualqa.<module>.<name>``, importing on first use.

    Caches the resolved class as a module-level attribute so that
    ``monkeypatch.setattr(runner, name, ...)``-based test substitutions
    remain effective: any value already bound at module scope (placeholder
    ``None``, real class, or test fake) takes precedence over re-import.
    """
    cached = globals().get(name)
    if cached is not None:
        return cached
    loaded = getattr(importlib.import_module(_DEFERRED_IMPORTS[name]), name)
    globals()[name] = loaded
    return loaded


class VisualQARunner:
    """High-level orchestration for verify/screenshot/manage-browser flows."""

    def __init__(
        self,
        *,
        browser_manager: BrowserManager | None = None,
        browser_config: BrowserConfig | None = None,
        artifact_manager: ArtifactManager | None = None,
        navigator_client: NavigatorClient | None = None,
        claim_verifier: ClaimVerifier | None = None,
        artifacts_dir: str = "artifacts",
        headless: bool | None = None,
        reporters: list[str] | None = None,
    ) -> None:
        resolved_browser_config = browser_config
        if resolved_browser_config is None:
            if headless is not None:
                resolved_browser_config = BrowserConfig(headless=headless)
        elif headless is not None:
            resolved_browser_config = resolved_browser_config.model_copy(update={"headless": headless})
        configured_visualize = (resolved_browser_config or BrowserConfig()).visualize
        self.browser_manager = browser_manager or BrowserManager(config=resolved_browser_config)
        self._base_browser_config = self.browser_manager.config.model_copy()
        self._login_override_active = False
        self.artifact_manager = artifact_manager or ArtifactManager(artifacts_dir)
        if navigator_client is None:
            navigator_client_class = _load_class("NavigatorClient")
            self.navigator_client = navigator_client_class()
        else:
            self.navigator_client = navigator_client
        if claim_verifier is None:
            claim_verifier_class = _load_class("ClaimVerifier")
            self.claim_verifier = claim_verifier_class(
                browser_manager=self.browser_manager,
                artifact_manager=self.artifact_manager,
                navigator_client=self.navigator_client,
                visualize=configured_visualize,
            )
        else:
            self.claim_verifier = claim_verifier
        self._default_visualize = bool(getattr(self.claim_verifier, "_visualize", configured_visualize))
        self.reporters = get_reporters(reporters or [])
        self._operation_lock = asyncio.Lock()

    async def run(
        self,
        *,
        url: str,
        claims: list[str],
        claim_navigation_hints: list[str | None] | None = None,
        claims_file: ParsedClaimsFile | None = None,
        viewport: ViewportConfig | dict[str, Any] | None = None,
        session_key: str = "default",
        run_name: str | None = None,
        reuse_session: bool = True,
        reset_between_claims: bool = True,
        visualize: bool | None = None,
        max_steps_per_claim: int = _pydantic_field_default(VerifyVisualClaimsInput, "max_steps_per_claim"),
        claim_timeout_seconds: float | None = _pydantic_field_default(VerifyVisualClaimsInput, "claim_timeout_seconds"),
        run_timeout_seconds: float | None = _pydantic_field_default(VerifyVisualClaimsInput, "run_timeout_seconds"),
        navigation_hint: str | None = None,
        on_claim_start: Callable[[int, str], None] | None = None,
        on_claim_complete: Callable[[int, str, ClaimResult], None] | None = None,
    ) -> RunResult:
        """Verify a set of claims against a URL."""

        request = VerifyVisualClaimsInput(
            url=url,
            claims=claims,
            claim_navigation_hints=claim_navigation_hints,
            viewport=coerce_viewport(viewport),
            session_key=session_key,
            run_name=run_name,
            reuse_session=reuse_session,
            reset_between_claims=reset_between_claims,
            visualize=visualize,
            max_steps_per_claim=max_steps_per_claim,
            claim_timeout_seconds=claim_timeout_seconds,
            run_timeout_seconds=run_timeout_seconds,
            navigation_hint=navigation_hint,
        )
        return await self.run_request(
            request,
            claims_file=claims_file,
            on_claim_start=on_claim_start,
            on_claim_complete=on_claim_complete,
        )

    async def run_request(
        self,
        request: VerifyVisualClaimsInput,
        *,
        claims_file: ParsedClaimsFile | None = None,
        on_claim_start: Callable[[int, str], None] | None = None,
        on_claim_complete: Callable[[int, str, ClaimResult], None] | None = None,
    ) -> RunResult:
        """Verify a set of claims from a prevalidated request."""

        async with self._operation_lock:
            run_started_at = time.time()
            run_artifacts = self.artifact_manager.create_run(prefix="run")

            session: Any = None
            finding = await self._preflight_url(request.url)
            # Plumb video recording into the Playwright context if the browser
            # config opts in. Videos go under <run_dir>/videos/ so recordings
            # are co-located with screenshots, traces, and reports for the same
            # run. .webm files are finalized when the context closes.
            record_video_dir = self._record_video_dir_for(run_artifacts)
            if finding is None:
                session, finding = await self._open_session_for_request(request, record_video_dir=record_video_dir)
            if finding is not None:
                # If a session started recording before the run became
                # not-testable (e.g. navigation failed), save its video so
                # --video still yields an artifact and the context is closed.
                early_video_paths: list[str] = []
                if session is not None:
                    await self._save_and_track_video(
                        session,
                        early_video_paths,
                        target=self._video_target_for(
                            run_artifacts,
                            reuse_session=request.reuse_session,
                            claim_index=1,
                            total_claims=len(request.claims),
                        ),
                    )
                return self._finalize_not_testable_run(
                    request=request,
                    run_artifacts=run_artifacts,
                    finding=finding,
                    started_at=run_started_at,
                    claims_file=claims_file,
                    video_paths=early_video_paths,
                )

            claim_results: list[ClaimResult] = []
            next_claim_index = 1
            # Accumulates the saved .webm for each session as it is retired.
            # With reuse_session there is a single session saved at end-of-run;
            # without it, each claim runs in its own session and its video is
            # saved by _prepare_session_for_claim just before the session is
            # replaced for the next claim.
            video_paths: list[str] = []

            def _safe_on_claim_start(index: int, claim: str) -> None:
                safe_callback_call(
                    on_claim_start,
                    index,
                    claim,
                    log_label=f"Claim start callback for claim {index}",
                    log=logger,
                )

            def _safe_on_claim_complete(index: int, claim: str, result: ClaimResult) -> None:
                safe_callback_call(
                    on_claim_complete,
                    index,
                    claim,
                    result,
                    log_label=f"Claim completion callback for claim {index}",
                    log=logger,
                )

            def _append_result(index: int, claim: str, result: ClaimResult) -> None:
                claim_results.append(result)
                _safe_on_claim_complete(index, claim, result)

            try:
                async with asyncio.timeout(request.run_timeout_seconds or None):
                    for index, claim in enumerate(request.claims, start=1):
                        next_claim_index = index
                        _safe_on_claim_start(index, claim)
                        navigation_hint_for_claim = self._navigation_hint_for_claim(request, index)
                        try:
                            session = await self._prepare_session_for_claim(
                                session=session,
                                request=request,
                                claim_index=index,
                                run_artifacts=run_artifacts,
                                video_paths=video_paths,
                            )
                        except Exception as exc:
                            result = self._build_claim(
                                claim=claim,
                                status="not_testable",
                                finding=f"Could not prepare browser state for this claim: {exc}",
                                final_url=session.page.url or request.url,
                                viewport=session.viewport,
                            )
                            _append_result(index, claim, result)
                            continue

                        try:
                            result = await self._verify_claim(
                                session=session,
                                claim=claim,
                                request=request,
                                run_artifacts=run_artifacts,
                                claim_index=index,
                                navigation_hint=navigation_hint_for_claim,
                            )
                        except TimeoutError:
                            finding = self._format_timeout_finding("claim", request.claim_timeout_seconds)
                            result = self._inconclusive_claim_result(
                                claim=claim, finding=finding, session=session, request=request
                            )
                        except Exception as exc:
                            finding = f"Verification crashed unexpectedly before returning a verdict: {exc}"
                            result = self._inconclusive_claim_result(
                                claim=claim, finding=finding, session=session, request=request
                            )
                        _append_result(index, claim, result)
                        next_claim_index = index + 1
            except TimeoutError:
                timed_out_claims = request.claims[next_claim_index - 1 :]
                timeout_finding = self._format_timeout_finding("run", request.run_timeout_seconds)
                if timed_out_claims:
                    interrupted_index = next_claim_index
                    interrupted_claim = timed_out_claims[0]
                    interrupted_result = self._inconclusive_claim_result(
                        claim=interrupted_claim,
                        finding=timeout_finding,
                        session=session,
                        request=request,
                    )
                    _append_result(interrupted_index, interrupted_claim, interrupted_result)

                    for claim_index, claim in enumerate(timed_out_claims[1:], start=next_claim_index + 1):
                        _safe_on_claim_start(claim_index, claim)
                        fallback_result = self._build_claim(
                            claim=claim,
                            status="inconclusive",
                            finding=timeout_finding,
                            final_url=session.page.url or request.url,
                            viewport=session.viewport,
                        )
                        _append_result(claim_index, claim, fallback_result)

            summary = self._summarize_results(claim_results)
            overall_status = (
                "not_testable"
                if claim_results and all(result.status == "not_testable" for result in claim_results)
                else "completed"
            )
            # Save the final (or only) live session. Earlier per-claim
            # sessions, if any, were already saved as they were retired.
            video_target = self._video_target_for(
                run_artifacts,
                reuse_session=request.reuse_session,
                claim_index=min(next_claim_index, len(request.claims)),
                total_claims=len(request.claims),
            )
            await self._save_and_track_video(session, video_paths, target=video_target)
            run_result = RunResult(
                overall_status=overall_status,
                started_at=run_started_at,
                completed_at=time.time(),
                session_key=request.session_key,
                run_name=request.run_name,
                results=claim_results,
                summary=summary,
                artifacts_dir=str(run_artifacts.run_dir),
                video_paths=video_paths,
            )
            self._write_reports(run_result, str(run_artifacts.run_dir), claims_file=claims_file)
            return run_result

    async def _prepare_session_for_claim(
        self,
        *,
        session: Any,
        request: VerifyVisualClaimsInput,
        claim_index: int,
        run_artifacts: RunArtifacts,
        video_paths: list[str],
    ) -> Any:
        """Return the session to use for ``claim_index``.

        For the first claim, or when ``reuse_session`` is set, the existing
        session is reused (optionally reset to the request URL). When sessions
        aren't reused, each claim runs in its own context: the previous claim's
        session is saved (appending its ``.webm`` to ``video_paths``) and
        retired, and a fresh recording-enabled session is opened and navigated.
        """
        if claim_index <= 1:
            return session

        if request.reuse_session:
            if request.reset_between_claims:
                await self.browser_manager.reset_to_url(session, request.url)
            return session

        # Without session reuse, each claim runs in its own browser context.
        # The current session has just finished serving the previous claim and
        # is about to be discarded, so save its video (which closes the context
        # and finalizes the .webm) before spinning up a fresh one. The saved
        # path is keyed to the claim this session covered (claim_index - 1).
        await self._save_and_track_video(
            session,
            video_paths,
            target=self._video_target_for(
                run_artifacts,
                reuse_session=False,
                claim_index=claim_index - 1,
                total_claims=len(request.claims),
            ),
        )

        record_video_dir = self._record_video_dir_for(run_artifacts)
        restarted = await self.browser_manager.get_session(
            request.session_key,
            viewport=request.viewport,
            reuse_session=False,
            record_video_dir=record_video_dir,
        )
        await self.browser_manager.goto(restarted, request.url)
        return restarted

    async def take_screenshot(
        self,
        *,
        url: str,
        viewport: ViewportConfig | dict[str, Any] | None = None,
        session_key: str = "default",
        run_name: str | None = None,
        reuse_session: bool = True,
    ) -> ScreenshotResult:
        """Navigate to a page and persist a screenshot."""

        async with self._operation_lock:
            viewport_config = coerce_viewport(viewport)
            run_artifacts = self.artifact_manager.create_run(prefix="screenshot")

            def _not_testable(summary: str) -> ScreenshotResult:
                return ScreenshotResult(
                    status="not_testable",
                    session_key=session_key,
                    run_name=run_name,
                    final_url=url,
                    viewport=viewport_config,
                    screenshot_path=None,
                    summary=summary,
                )

            preflight_error = await self._preflight_url(url)
            if preflight_error is not None:
                result: ScreenshotResult = _not_testable(preflight_error)
            else:
                try:
                    session = await self.browser_manager.get_session(
                        session_key, viewport=viewport_config, reuse_session=reuse_session
                    )
                    await self.browser_manager.goto(session, url)
                    image_bytes = await self.browser_manager.capture_screenshot(session)
                    screenshot_path = self.artifact_manager.save_screenshot(run_artifacts, 1, "screenshot", image_bytes)
                    result = ScreenshotResult(
                        status="completed",
                        session_key=session_key,
                        run_name=run_name,
                        final_url=session.page.url,
                        viewport=session.viewport,
                        screenshot_path=screenshot_path,
                        summary="Captured the current page state successfully.",
                    )
                except Exception as exc:
                    result = _not_testable(f"Could not capture a screenshot for {url}: {exc}")
            self.artifact_manager.save_json(run_artifacts, "screenshot_result.json", result.model_dump())
            return result

    async def manage_browser(
        self,
        *,
        action: str,
        session_key: str = "default",
        viewport: ViewportConfig | dict[str, Any] | None = None,
        url: str | None = None,
    ) -> BrowserStatusResult:
        """Inspect or mutate browser session state."""

        request = ManageBrowserInput(
            action=action,
            session_key=session_key,
            viewport=coerce_optional_viewport(viewport),
            url=url,
        )
        return await self.manage_browser_request(request)

    async def manage_browser_request(self, request: ManageBrowserInput) -> BrowserStatusResult:
        """Inspect or mutate browser session state from a prevalidated request."""

        async with self._operation_lock:
            if request.action == "status":
                return self._status_with_summary("Reported shared browser status.")
            if request.action == "login":
                if request.url is None:
                    raise ValueError("url is required when action is 'login'")
                await self._ensure_login_browser()
                try:
                    session = await self.browser_manager.get_session(
                        request.session_key,
                        viewport=request.viewport or ViewportConfig(),
                        reuse_session=True,
                    )
                    await self.browser_manager.goto(session, request.url)
                except Exception as exc:
                    logger.error("Login browser launched but navigation failed: %s", exc, exc_info=True)
                    return self._status_with_summary(
                        f"Opened a persistent headed browser but failed to navigate to {request.url}: {exc}. "
                        "The browser is in persistent headed mode. "
                        "Try navigating manually or close the session to restore the original configuration."
                    )
                return self._status_with_summary(
                    "Opened a persistent headed browser for interactive login. "
                    f"Ask the user to finish authentication at {request.url}, then reuse "
                    f"session_key '{request.session_key}' with take_screenshot or "
                    "verify_visual_claims."
                )
            if request.action == "close":
                await self.browser_manager.close_session(request.session_key)
                summary = f"Closed shared browser session '{request.session_key}'."
                try:
                    restored, restore_note = await self._restore_base_browser_config_after_login_close()
                except Exception:
                    logger.error("Failed to restore base browser config after login close", exc_info=True)
                    summary += (
                        " Warning: failed to restore the browser to its original configuration."
                        " The browser is still in login mode."
                        " Use manage_browser(action='restart') to reset."
                    )
                    return self._status_with_summary(summary)
                if restored:
                    summary += " Restored the shared browser to its original configuration."
                elif restore_note:
                    summary += f" {restore_note}"
                return self._status_with_summary(summary)
            if request.action == "restart":
                await self.browser_manager.restart_session(
                    request.session_key,
                    viewport=request.viewport,
                    preserve_url=True,
                )
                return self._status_with_summary(f"Restarted shared browser session '{request.session_key}'.")
            if request.action == "set_viewport":
                await self.browser_manager.set_viewport(request.session_key, request.viewport or ViewportConfig())
                return self._status_with_summary(
                    f"Updated the viewport for shared browser session '{request.session_key}'."
                )
            raise ValueError(f"Unsupported browser action: {request.action}")

    async def _ensure_login_browser(self) -> None:
        current_config = self.browser_manager.config
        desired_config = BrowserConfig.model_validate(
            {**current_config.model_dump(), "mode": BrowserMode.persistent, "headless": False}
        )

        if current_config == desired_config:
            self._login_override_active = desired_config != self._base_browser_config
            return

        await self._reconfigure_browser_manager(
            desired_config,
            login_override_active=desired_config != self._base_browser_config,
        )

    async def _restore_base_browser_config_after_login_close(self) -> tuple[bool, str | None]:
        """Attempt to restore the base browser config after a login override.

        Returns (restored, note) where note explains why restoration was skipped.
        """
        if not self._login_override_active:
            return False, None
        if self.browser_manager.config == self._base_browser_config:
            self._login_override_active = False
            return False, None
        # In persistent mode, close_session tears down the entire persistent context
        # and clears all sessions, so this guard is currently unreachable in production.
        # Kept as a safety net in case BrowserManager changes to per-session close.
        active_sessions = self.browser_manager.status().sessions
        if active_sessions:
            logger.info(
                "Skipping browser config restoration: %d other session(s) still active",
                len(active_sessions),
            )
            return False, (
                f"The browser remains in login mode because {len(active_sessions)} other session(s) "
                "are still open. Close them to restore the original configuration."
            )
        await self._reconfigure_browser_manager(self._base_browser_config, login_override_active=False)
        return True, None

    async def _reconfigure_browser_manager(
        self,
        browser_config: BrowserConfig,
        *,
        login_override_active: bool,
    ) -> None:
        old_config = self.browser_manager.config
        try:
            await self.browser_manager.close()
        except Exception:
            logger.error("Failed to close existing browser during reconfiguration; proceeding", exc_info=True)

        try:
            new_browser = BrowserManager(config=browser_config)
        except Exception:
            logger.error("Failed to create browser with new config; restoring previous config", exc_info=True)
            self.browser_manager = BrowserManager(config=old_config)
            self._rebind_claim_verifier(old_config)
            raise

        self.browser_manager = new_browser
        self._rebind_claim_verifier(browser_config)
        self._login_override_active = login_override_active

    def _rebind_claim_verifier(self, browser_config: BrowserConfig) -> None:
        """Rebind the claim verifier to the current browser manager."""
        rebind_browser_manager = getattr(self.claim_verifier, "set_browser_manager", None)
        if callable(rebind_browser_manager):
            rebind_browser_manager(self.browser_manager, visualize=browser_config.visualize)
        else:
            logger.info("claim_verifier lacks set_browser_manager; falling back to manual attribute patching")
            self.claim_verifier.browser_manager = self.browser_manager
            action_executor = getattr(self.claim_verifier, "action_executor", None)
            if action_executor is not None:
                action_executor.navigation_timeout_ms = self.browser_manager.navigation_timeout_ms
            if hasattr(self.claim_verifier, "_visualize"):
                self.claim_verifier._visualize = browser_config.visualize

        self._default_visualize = bool(getattr(self.claim_verifier, "_visualize", browser_config.visualize))

    def _status_with_summary(self, summary: str) -> BrowserStatusResult:
        return self.browser_manager.status().model_copy(update={"summary": summary})

    async def close(self) -> None:
        """Close all long-lived resources."""

        await self.browser_manager.close()
        await self.navigator_client.close()

    async def _save_session_video(self, session: Any, *, target: Path) -> str | None:
        """Close the session's context and move its recording to ``target``.

        Playwright finalizes a video to disk only when its BrowserContext
        closes, writing it under an auto-generated ``<hash>.webm`` name in the
        context's ``record_video_dir``. We close the session first (which
        finalizes the file) and then move that file to the predictable
        ``target``.

        The move is a plain filesystem rename rather than ``page.video.save_as``
        on purpose: in persistent mode ``close_session`` also stops the
        Playwright driver (the context is the only thing keeping it alive), so a
        subsequent ``save_as`` channel call would fail. A filesystem move works
        regardless of whether the driver is still running and leaves no leftover
        copy to clean up. ``save_as`` is kept only as a fallback for the rare
        case where the finalized file can't be isolated on disk (and that
        fallback only works while the driver is still up, i.e. ephemeral mode).

        Returns the saved path, or ``None`` if recording is disabled, the
        session has no video, or saving failed. Best-effort — never raises; on
        failure we log at DEBUG and leave any finalized recording in place under
        its original name for manual recovery.
        """
        if not self.browser_manager.config.record_video:
            return None
        page = getattr(session, "page", None)
        video = getattr(page, "video", None) if page is not None else None
        if video is None:
            return None

        video_dir = target.parent
        try:
            video_dir.mkdir(parents=True, exist_ok=True)
            # Snapshot existing recordings so we can identify the one this
            # context produces when it closes. Files already saved to a
            # predictable target (from earlier per-claim sessions) and any
            # un-recovered orphans are excluded by this diff.
            existing = {p.resolve() for p in video_dir.glob("*.webm")}
            await self.browser_manager.close_session(session.session_key)
            new_files = [p for p in video_dir.glob("*.webm") if p.resolve() not in existing]
            if len(new_files) == 1:
                new_files[0].replace(target)
                return str(target)
            # 0 or >1 newly-finalized files: can't isolate this session's
            # recording by name. Fall back to the Playwright channel (only
            # viable while the driver is still up); leave files in place.
            await video.save_as(str(target))
            return str(target)
        except Exception:  # noqa: BLE001 - best-effort video save must not propagate
            logger.debug("Failed to save session video to %s", target, exc_info=True)
            return None

    async def _save_and_track_video(self, session: Any, video_paths: list[str], *, target: Path) -> None:
        """Save ``session``'s video to ``target`` and append it to ``video_paths`` if produced."""
        saved = await self._save_session_video(session, target=target)
        if saved is not None:
            video_paths.append(saved)

    @staticmethod
    def _videos_dir_for(run_artifacts: RunArtifacts) -> Path:
        """Directory videos for this run are written to: ``<run_dir>/videos``."""
        return Path(run_artifacts.run_dir) / "videos"

    def _record_video_dir_for(self, run_artifacts: RunArtifacts) -> str | None:
        """Playwright ``record_video_dir`` for this run, or ``None`` if recording is off.

        Videos go under ``<run_dir>/videos/`` so recordings are co-located with
        screenshots, traces, and reports for the same run.
        """
        if not self.browser_manager.config.record_video:
            return None
        return str(self._videos_dir_for(run_artifacts))

    @staticmethod
    def _video_target_for(
        run_artifacts: RunArtifacts,
        *,
        reuse_session: bool,
        claim_index: int,
        total_claims: int,
    ) -> Path:
        """Pick a predictable filename for a saved session video.

        - One session for the whole run (``reuse_session=True``, or a single-
          claim run): ``<run_id>.webm``.
        - One session per claim (``reuse_session=False`` with multiple
          claims): ``<run_id>-claim-<N>.webm``, where ``N`` is the claim
          covered by that session.
        """
        videos_dir = VisualQARunner._videos_dir_for(run_artifacts)
        if reuse_session or total_claims <= 1:
            return videos_dir / f"{run_artifacts.run_id}.webm"
        return videos_dir / f"{run_artifacts.run_id}-claim-{claim_index}.webm"

    @staticmethod
    def _summarize_results(results: list[ClaimResult]) -> str:
        counts: Counter[ClaimStatus] = Counter(result.status for result in results)
        parts = [f"{counts['passed']}/{len(results)} claims passed."]
        if counts["failed"]:
            parts.append(f"{counts['failed']} failed.")
        if counts["inconclusive"]:
            parts.append(f"{counts['inconclusive']} inconclusive.")
        if counts["not_testable"]:
            parts.append(f"{counts['not_testable']} not testable.")
        return " ".join(parts)

    def _finalize_not_testable_run(
        self,
        *,
        request: VerifyVisualClaimsInput,
        run_artifacts: RunArtifacts,
        finding: str,
        started_at: float,
        claims_file: ParsedClaimsFile | None,
        video_paths: list[str] | None = None,
    ) -> RunResult:
        """Build a not-testable run result, persist reports, and return it."""
        result = self._build_not_testable_run(
            request=request,
            run_dir=str(run_artifacts.run_dir),
            finding=finding,
            started_at=started_at,
            completed_at=time.time(),
            video_paths=video_paths,
        )
        self._write_reports(result, str(run_artifacts.run_dir), claims_file=claims_file)
        return result

    @staticmethod
    def _build_not_testable_run(
        *,
        request: VerifyVisualClaimsInput,
        run_dir: str,
        finding: str,
        started_at: float | None = None,
        completed_at: float | None = None,
        video_paths: list[str] | None = None,
    ) -> RunResult:
        results = [
            VisualQARunner._build_claim(
                claim=claim,
                status="not_testable",
                finding=finding,
                final_url=request.url,
                viewport=request.viewport,
            )
            for claim in request.claims
        ]
        return RunResult(
            overall_status="not_testable",
            started_at=started_at,
            completed_at=completed_at,
            session_key=request.session_key,
            run_name=request.run_name,
            results=results,
            summary=VisualQARunner._summarize_results(results),
            artifacts_dir=run_dir,
            video_paths=video_paths or [],
        )

    @staticmethod
    def _build_claim(
        *,
        claim: str,
        status: ClaimStatus,
        finding: str,
        final_url: str,
        viewport: ViewportConfig,
    ) -> ClaimResult:
        return ClaimResult(
            claim=claim,
            status=status,
            finding=finding,
            proof=None,
            page=ClaimPage(url=final_url, viewport=viewport),
            trace=ClaimTrace(),
        )

    def _consume_partial_claim_result(self, *, status: ClaimStatus, finding: str) -> ClaimResult | None:
        consume_partial_result = getattr(self.claim_verifier, "consume_partial_result", None)
        if not callable(consume_partial_result):
            return None
        try:
            return consume_partial_result(status=status, finding=finding)
        except Exception:
            logger.warning("Failed to recover partial claim result after verifier interruption", exc_info=True)
            return None

    def _inconclusive_claim_result(
        self,
        *,
        claim: str,
        finding: str,
        session: Any,
        request: VerifyVisualClaimsInput,
    ) -> ClaimResult:
        """Return an inconclusive ClaimResult, preferring a partial result from the verifier."""
        return self._consume_partial_claim_result(
            status="inconclusive",
            finding=finding,
        ) or self._build_claim(
            claim=claim,
            status="inconclusive",
            finding=finding,
            final_url=session.page.url or request.url,
            viewport=session.viewport,
        )

    @staticmethod
    def _format_timeout_finding(scope: _TimeoutScope, timeout_seconds: float | None) -> str:
        no_timeout_msg, with_timeout_template = _TIMEOUT_FINDING_TEMPLATES[scope]
        if timeout_seconds is None:
            return no_timeout_msg
        return with_timeout_template.format(seconds=VisualQARunner._format_timeout_seconds(timeout_seconds))

    @staticmethod
    def _format_timeout_seconds(timeout_seconds: float) -> str:
        if timeout_seconds >= 1 and float(timeout_seconds).is_integer():
            return f"{int(timeout_seconds)}s"
        if timeout_seconds >= 1:
            return f"{timeout_seconds:.1f}s"
        return f"{timeout_seconds:.2f}".rstrip("0").rstrip(".") + "s"

    def _write_reports(
        self,
        run_result: RunResult,
        run_dir: str,
        *,
        claims_file: ParsedClaimsFile | None = None,
    ) -> None:
        output_dir = Path(run_dir)
        for reporter in self.reporters:
            try:
                reporter.write(run_result, output_dir, claims_file=claims_file)
            except Exception:
                logger.warning("Reporter %s failed to write", reporter.name, exc_info=True)

    async def _open_session_for_request(
        self,
        request: VerifyVisualClaimsInput,
        *,
        record_video_dir: str | None = None,
    ) -> tuple[Any, str | None]:
        """Open a browser session for ``request`` and navigate to its URL.

        Returns ``(session, None)`` on success. On failure it returns
        ``(None, finding)`` if the session never started, or
        ``(session, finding)`` if the session started (and may have begun
        recording) but navigation failed — so the caller can still finalize
        and save that session's video. ``record_video_dir``, when set, enables
        Playwright video recording on the created context.
        """
        try:
            session = await self.browser_manager.get_session(
                request.session_key,
                viewport=request.viewport,
                reuse_session=request.reuse_session,
                record_video_dir=record_video_dir,
            )
        except Exception as exc:
            return None, f"Could not start a browser session for {request.url}: {exc}"

        try:
            await self.browser_manager.goto(session, request.url)
        except Exception as exc:
            return session, f"Could not navigate to {request.url}: {exc}"

        return session, None

    async def _preflight_url(self, url: str) -> str | None:
        try:
            # http2=True opts into ALPN h2 negotiation for parity with the
            # Navigator client. A single HEAD doesn't benefit perf-wise;
            # this is purely consistency-of-transport across all outbound
            # HTTP. Requires the httpx[http2] extra (pulled by pyproject).
            async with httpx.AsyncClient(http2=True, follow_redirects=True, timeout=5.0) as client:
                try:
                    response = await client.head(url)
                    if response.status_code in {405, 501}:
                        await client.get(url)
                except httpx.TimeoutException:
                    # A slow first response (e.g. a dev server cold-compiling
                    # the route) is not a hard failure. The browser navigation
                    # path has a longer timeout; let it make the call.
                    logger.info("Preflight for %s timed out after 5s; deferring to browser navigation", url)
                    return None
                except httpx.RequestError as exc:
                    return f"Could not reach {url} before opening the browser: {exc}"
        except Exception as exc:
            logger.warning("Preflight check failed unexpectedly for %s", url, exc_info=True)
            return f"Could not preflight {url} before opening the browser: {exc}"
        return None

    async def _verify_claim(
        self,
        *,
        session: Any,
        claim: str,
        request: VerifyVisualClaimsInput,
        run_artifacts: Any,
        claim_index: int,
        navigation_hint: str | None,
    ) -> ClaimResult:
        visualize = request.visualize if request.visualize is not None else self._default_visualize

        async def _call_verifier() -> ClaimResult:
            return await self.claim_verifier.verify(
                session=session,
                claim=claim,
                url=request.url,
                claim_index=claim_index,
                run_artifacts=run_artifacts,
                max_steps=request.max_steps_per_claim,
                navigation_hint=navigation_hint,
                visualize=visualize,
            )

        async with asyncio.timeout(request.claim_timeout_seconds or None):
            return await _call_verifier()

    @staticmethod
    def _navigation_hint_for_claim(request: VerifyVisualClaimsInput, claim_index: int) -> str | None:
        if request.claim_navigation_hints is not None:
            claim_hint = request.claim_navigation_hints[claim_index - 1]
            if claim_hint is not None:
                return claim_hint
        return request.navigation_hint
