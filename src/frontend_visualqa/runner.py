"""Multi-claim orchestration for frontend-visualqa."""

from __future__ import annotations

import inspect
import asyncio
import logging
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

import httpx

from frontend_visualqa.artifacts import ArtifactManager
from frontend_visualqa.browser import BrowserManager
from frontend_visualqa.reporters import get_reporters
from frontend_visualqa.schemas import (
    BrowserConfig,
    BrowserStatusResult,
    ClaimHistory,
    ClaimPage,
    ClaimResult,
    ClaimStatus,
    ManageBrowserInput,
    RunResult,
    ScreenshotResult,
    VerifyVisualClaimsInput,
    ViewportConfig,
)

logger = logging.getLogger(__name__)

if not TYPE_CHECKING:
    ClaimVerifier = None  # type: ignore[assignment]
    N1Client = None  # type: ignore[assignment]


def _callable_accepts_visualize(target: Any) -> bool:
    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError):
        return False
    return "visualize" in signature.parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()
    )


def _load_claim_verifier_class() -> Any:
    global ClaimVerifier
    if ClaimVerifier is None:
        from frontend_visualqa.claim_verifier import ClaimVerifier as loaded_claim_verifier

        ClaimVerifier = loaded_claim_verifier  # type: ignore[assignment]
    return ClaimVerifier


def _load_n1_client_class() -> Any:
    global N1Client
    if N1Client is None:
        from frontend_visualqa.n1_client import N1Client as loaded_n1_client

        N1Client = loaded_n1_client  # type: ignore[assignment]
    return N1Client


class VisualQARunner:
    """High-level orchestration for verify/screenshot/manage-browser flows."""

    def __init__(
        self,
        *,
        browser_manager: BrowserManager | None = None,
        browser_config: BrowserConfig | None = None,
        artifact_manager: ArtifactManager | None = None,
        n1_client: N1Client | None = None,
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
        configured_visualize = bool(getattr(resolved_browser_config or BrowserConfig(), "visualize", False))
        self.browser_manager = browser_manager or BrowserManager(config=resolved_browser_config)
        self.artifact_manager = artifact_manager or ArtifactManager(artifacts_dir)
        if n1_client is None:
            n1_client_class = _load_n1_client_class()
            self.n1_client = n1_client_class()
        else:
            self.n1_client = n1_client
        if claim_verifier is None:
            claim_verifier_class = _load_claim_verifier_class()
            claim_verifier_kwargs: dict[str, Any] = {
                "browser_manager": self.browser_manager,
                "artifact_manager": self.artifact_manager,
                "n1_client": self.n1_client,
            }
            if _callable_accepts_visualize(claim_verifier_class):
                claim_verifier_kwargs["visualize"] = configured_visualize
            try:
                self.claim_verifier = claim_verifier_class(**claim_verifier_kwargs)
            except TypeError:
                claim_verifier_kwargs.pop("visualize", None)
                self.claim_verifier = claim_verifier_class(**claim_verifier_kwargs)
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
        viewport: ViewportConfig | dict[str, Any] | None = None,
        session_key: str = "default",
        reuse_session: bool = True,
        reset_between_claims: bool = True,
        visualize: bool | None = None,
        max_steps_per_claim: int = 12,
        claim_timeout_seconds: float | None = 120.0,
        run_timeout_seconds: float | None = 300.0,
        navigation_hint: str | None = None,
    ) -> RunResult:
        """Verify a set of claims against a URL."""

        request = VerifyVisualClaimsInput(
            url=url,
            claims=claims,
            viewport=self._coerce_viewport(viewport),
            session_key=session_key,
            reuse_session=reuse_session,
            reset_between_claims=reset_between_claims,
            visualize=visualize,
            max_steps_per_claim=max_steps_per_claim,
            claim_timeout_seconds=claim_timeout_seconds,
            run_timeout_seconds=run_timeout_seconds,
            navigation_hint=navigation_hint,
        )
        return await self.run_request(request)

    async def run_request(self, request: VerifyVisualClaimsInput) -> RunResult:
        """Verify a set of claims from a prevalidated request."""

        async with self._operation_lock:
            run_started_at = time.time()
            run_artifacts = self.artifact_manager.create_run(prefix="run")

            preflight_error = await self._preflight_url(request.url)
            if preflight_error is not None:
                result = self._build_not_testable_run(
                    request=request,
                    run_dir=str(run_artifacts.run_dir),
                    finding=preflight_error,
                    started_at=run_started_at,
                    completed_at=time.time(),
                )
                self._write_reports(result, str(run_artifacts.run_dir))
                return result

            try:
                session = await self.browser_manager.get_session(
                    request.session_key,
                    viewport=request.viewport,
                    reuse_session=request.reuse_session,
                )
            except Exception as exc:
                result = self._build_not_testable_run(
                    request=request,
                    run_dir=str(run_artifacts.run_dir),
                    finding=f"Could not start a browser session for {request.url}: {exc}",
                    started_at=run_started_at,
                    completed_at=time.time(),
                )
                self._write_reports(result, str(run_artifacts.run_dir))
                return result

            try:
                await self.browser_manager.goto(session, request.url)
            except Exception as exc:
                result = self._build_not_testable_run(
                    request=request,
                    run_dir=str(run_artifacts.run_dir),
                    finding=f"Could not navigate to {request.url}: {exc}",
                    started_at=run_started_at,
                    completed_at=time.time(),
                )
                self._write_reports(result, str(run_artifacts.run_dir))
                return result

            claim_results: list[ClaimResult] = []
            next_claim_index = 1

            try:
                timeout_cm = asyncio.timeout(request.run_timeout_seconds) if request.run_timeout_seconds else _null_async_context()
                async with timeout_cm:
                    for index, claim in enumerate(request.claims, start=1):
                        next_claim_index = index
                        try:
                            session = await self._prepare_session_for_claim(
                                session=session,
                                request=request,
                                claim_index=index,
                            )
                        except Exception as exc:
                            claim_results.append(
                                self._build_claim(
                                    claim=claim,
                                    status="not_testable",
                                    finding=f"Could not prepare browser state for this claim: {exc}",
                                    final_url=getattr(session.page, "url", request.url) or request.url,
                                    viewport=getattr(session, "viewport", request.viewport),
                                )
                            )
                            continue

                        try:
                            result = await self._verify_claim(
                                session=session,
                                claim=claim,
                                request=request,
                                run_artifacts=run_artifacts,
                                claim_index=index,
                            )
                        except TimeoutError:
                            finding = self._format_claim_timeout_finding(request.claim_timeout_seconds)
                            result = self._consume_partial_claim_result(
                                status="inconclusive",
                                finding=finding,
                            ) or self._build_claim(
                                claim=claim,
                                status="inconclusive",
                                finding=finding,
                                final_url=getattr(session.page, "url", request.url) or request.url,
                                viewport=getattr(session, "viewport", request.viewport),
                            )
                        except Exception as exc:
                            finding = f"Verification crashed unexpectedly before returning a verdict: {exc}"
                            result = self._consume_partial_claim_result(
                                status="inconclusive",
                                finding=finding,
                            ) or self._build_claim(
                                claim=claim,
                                status="inconclusive",
                                finding=finding,
                                final_url=getattr(session.page, "url", request.url) or request.url,
                                viewport=getattr(session, "viewport", request.viewport),
                            )
                        claim_results.append(result)
                        next_claim_index = index + 1
            except TimeoutError:
                timed_out_claims = request.claims[next_claim_index - 1 :]
                remaining_claims = list(timed_out_claims)
                timeout_finding = self._format_run_timeout_finding(request.run_timeout_seconds)
                if remaining_claims:
                    interrupted_result = self._consume_partial_claim_result(
                        status="inconclusive",
                        finding=timeout_finding,
                    )
                    if interrupted_result is not None:
                        claim_results.append(interrupted_result)
                    else:
                        claim_results.append(
                            self._build_claim(
                                claim=remaining_claims[0],
                                status="inconclusive",
                                finding=timeout_finding,
                                final_url=getattr(session.page, "url", request.url) or request.url,
                                viewport=getattr(session, "viewport", request.viewport),
                            )
                        )
                    remaining_claims = remaining_claims[1:]

                claim_results.extend(
                    [
                        self._build_claim(
                            claim=claim,
                            status="inconclusive",
                            finding=timeout_finding,
                            final_url=getattr(session.page, "url", request.url) or request.url,
                            viewport=getattr(session, "viewport", request.viewport),
                        )
                        for claim in remaining_claims
                    ]
                )

            summary = self._summarize_results(claim_results)
            overall_status = (
                "not_testable" if claim_results and all(result.status == "not_testable" for result in claim_results) else "completed"
            )
            run_result = RunResult(
                overall_status=overall_status,
                started_at=run_started_at,
                completed_at=time.time(),
                session_key=request.session_key,
                results=claim_results,
                summary=summary,
                artifacts_dir=str(run_artifacts.run_dir),
            )
            self._write_reports(run_result, str(run_artifacts.run_dir))
            return run_result

    async def _prepare_session_for_claim(
        self,
        *,
        session: Any,
        request: VerifyVisualClaimsInput,
        claim_index: int,
    ) -> Any:
        if claim_index <= 1:
            return session

        if request.reuse_session:
            if request.reset_between_claims:
                await self.browser_manager.reset_to_url(session, request.url)
            return session

        restarted = await self.browser_manager.get_session(
            request.session_key,
            viewport=request.viewport,
            reuse_session=False,
        )
        await self.browser_manager.goto(restarted, request.url)
        return restarted

    async def take_screenshot(
        self,
        *,
        url: str,
        viewport: ViewportConfig | dict[str, Any] | None = None,
        session_key: str = "default",
        reuse_session: bool = True,
    ) -> ScreenshotResult:
        """Navigate to a page and persist a screenshot."""

        async with self._operation_lock:
            viewport_config = self._coerce_viewport(viewport)
            run_artifacts = self.artifact_manager.create_run(prefix="screenshot")

            preflight_error = await self._preflight_url(url)
            if preflight_error is not None:
                result = ScreenshotResult(
                    status="not_testable",
                    session_key=session_key,
                    final_url=url,
                    viewport=viewport_config,
                    screenshot_path=None,
                    summary=preflight_error,
                )
                self._save_json(run_artifacts, "screenshot_result.json", result.model_dump())
                return result

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
                    final_url=session.page.url,
                    viewport=session.viewport,
                    screenshot_path=screenshot_path,
                    summary="Captured the current page state successfully.",
                )
            except Exception as exc:
                result = ScreenshotResult(
                    status="not_testable",
                    session_key=session_key,
                    final_url=url,
                    viewport=viewport_config,
                    screenshot_path=None,
                    summary=f"Could not capture a screenshot for {url}: {exc}",
                )
            self._save_json(run_artifacts, "screenshot_result.json", result.model_dump())
            return result

    async def manage_browser(
        self,
        *,
        action: str,
        session_key: str = "default",
        viewport: ViewportConfig | dict[str, Any] | None = None,
    ) -> BrowserStatusResult:
        """Inspect or mutate browser session state."""

        request = ManageBrowserInput(
            action=action, session_key=session_key, viewport=self._coerce_optional_viewport(viewport)
        )
        return await self.manage_browser_request(request)

    async def manage_browser_request(self, request: ManageBrowserInput) -> BrowserStatusResult:
        """Inspect or mutate browser session state from a prevalidated request."""

        async with self._operation_lock:
            if request.action == "status":
                return self.browser_manager.status()
            if request.action == "close":
                await self.browser_manager.close_session(request.session_key)
                return self.browser_manager.status()
            if request.action == "restart":
                try:
                    await self.browser_manager.restart_session(
                        request.session_key,
                        viewport=request.viewport,
                        preserve_url=True,
                    )
                except TypeError:
                    await self.browser_manager.restart_session(
                        request.session_key,
                        viewport=request.viewport,
                    )
                return self.browser_manager.status()
            if request.action == "set_viewport":
                await self.browser_manager.set_viewport(request.session_key, request.viewport or ViewportConfig())
                return self.browser_manager.status()
            raise ValueError(f"Unsupported browser action: {request.action}")

    async def close(self) -> None:
        """Close all long-lived resources."""

        await self.browser_manager.close()
        close = getattr(self.n1_client, "close", None)
        if callable(close):
            await close()

    @staticmethod
    def _summarize_results(results: list[ClaimResult]) -> str:
        counts = {
            "passed": sum(result.status == "passed" for result in results),
            "failed": sum(result.status == "failed" for result in results),
            "inconclusive": sum(result.status == "inconclusive" for result in results),
            "not_testable": sum(result.status == "not_testable" for result in results),
        }
        parts = [f"{counts['passed']}/{len(results)} claims passed."]
        if counts["failed"]:
            parts.append(f"{counts['failed']} failed.")
        if counts["inconclusive"]:
            parts.append(f"{counts['inconclusive']} inconclusive.")
        if counts["not_testable"]:
            parts.append(f"{counts['not_testable']} not testable.")
        return " ".join(parts)

    @staticmethod
    def _coerce_viewport(value: ViewportConfig | dict[str, Any] | None) -> ViewportConfig:
        if isinstance(value, ViewportConfig):
            return value
        if value is None:
            return ViewportConfig()
        return ViewportConfig.model_validate(value)

    @staticmethod
    def _coerce_optional_viewport(value: ViewportConfig | dict[str, Any] | None) -> ViewportConfig | None:
        if value is None:
            return None
        return VisualQARunner._coerce_viewport(value)

    @staticmethod
    def _build_not_testable_run(
        *,
        request: VerifyVisualClaimsInput,
        run_dir: str,
        finding: str,
        started_at: float | None = None,
        completed_at: float | None = None,
    ) -> RunResult:
        results = [
            ClaimResult(
                claim=claim,
                status="not_testable",
                finding=finding,
                proof=None,
                page=ClaimPage(url=request.url, viewport=request.viewport),
                history=ClaimHistory(),
            )
            for claim in request.claims
        ]
        return RunResult(
            overall_status="not_testable",
            started_at=started_at,
            completed_at=completed_at,
            session_key=request.session_key,
            results=results,
            summary=VisualQARunner._summarize_results(results),
            artifacts_dir=run_dir,
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
            history=ClaimHistory(),
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

    @staticmethod
    def _format_claim_timeout_finding(timeout_seconds: float | None) -> str:
        if timeout_seconds is None:
            return "Claim verification timed out before a verdict was recorded."
        return (
            f"Claim verification timed out after {VisualQARunner._format_timeout_seconds(timeout_seconds)} "
            "before a verdict was recorded."
        )

    @staticmethod
    def _format_run_timeout_finding(timeout_seconds: float | None) -> str:
        if timeout_seconds is None:
            return "Run timed out before this claim could finish."
        return f"Run timed out after {VisualQARunner._format_timeout_seconds(timeout_seconds)} before this claim could finish."

    @staticmethod
    def _format_timeout_seconds(timeout_seconds: float) -> str:
        if timeout_seconds >= 1 and float(timeout_seconds).is_integer():
            return f"{int(timeout_seconds)}s"
        if timeout_seconds >= 1:
            return f"{timeout_seconds:.1f}s"
        return f"{timeout_seconds:.2f}s".rstrip("0").rstrip(".") + "s"

    def _write_reports(self, run_result: RunResult, run_dir: str) -> None:
        output_dir = Path(run_dir)
        for reporter in self.reporters:
            try:
                reporter.write(run_result, output_dir)
            except Exception:
                logger.warning("Reporter %s failed to write", reporter.name, exc_info=True)

    def _save_json(self, run_artifacts: Any, relative_path: str, payload: dict[str, Any]) -> None:
        save_json = getattr(self.artifact_manager, "save_json", None)
        if callable(save_json):
            try:
                save_json(run_artifacts, relative_path, payload)
            except Exception:
                logger.warning("Failed to save JSON artifact %s", relative_path, exc_info=True)

    async def _preflight_url(self, url: str) -> str | None:
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=5.0) as client:
                try:
                    response = await client.head(url)
                except httpx.RequestError as exc:
                    return f"Could not reach {url} before opening the browser: {exc}"

                if response is not None and response.status_code not in {405, 501}:
                    return None

                try:
                    await client.get(url)
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
    ) -> ClaimResult:
        visualize = request.visualize if request.visualize is not None else self._default_visualize

        async def _call_verifier() -> ClaimResult:
            verify_kwargs: dict[str, Any] = {
                "session": session,
                "claim": claim,
                "url": request.url,
                "claim_index": claim_index,
                "run_artifacts": run_artifacts,
                "max_steps": request.max_steps_per_claim,
                "navigation_hint": request.navigation_hint,
            }
            if _callable_accepts_visualize(self.claim_verifier.verify):
                verify_kwargs["visualize"] = visualize
            return await self.claim_verifier.verify(**verify_kwargs)

        if request.claim_timeout_seconds:
            async with asyncio.timeout(request.claim_timeout_seconds):
                return await _call_verifier()
        return await _call_verifier()


class _null_async_context:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: type | None, exc: BaseException | None, traceback: Any) -> None:
        return None
