"""Multi-claim orchestration for frontend-visualqa."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from frontend_visualqa.artifacts import ArtifactManager
from frontend_visualqa.browser import BrowserManager
from frontend_visualqa.claim_verifier import ClaimVerifier
from frontend_visualqa.n1_client import N1Client
from frontend_visualqa.schemas import (
    BrowserConfig,
    BrowserStatusResult,
    ClaimResult,
    ClaimStatus,
    ManageBrowserInput,
    RunResult,
    ScreenshotResult,
    VerifyVisualClaimsInput,
    ViewportConfig,
)

logger = logging.getLogger(__name__)


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
    ) -> None:
        resolved_browser_config = browser_config
        if resolved_browser_config is None:
            if headless is not None:
                resolved_browser_config = BrowserConfig(headless=headless)
        elif headless is not None:
            resolved_browser_config = resolved_browser_config.model_copy(update={"headless": headless})
        self.browser_manager = browser_manager or BrowserManager(config=resolved_browser_config)
        self.artifact_manager = artifact_manager or ArtifactManager(artifacts_dir)
        self.n1_client = n1_client or N1Client()
        self.claim_verifier = claim_verifier or ClaimVerifier(
            browser_manager=self.browser_manager,
            artifact_manager=self.artifact_manager,
            n1_client=self.n1_client,
        )
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
            max_steps_per_claim=max_steps_per_claim,
            claim_timeout_seconds=claim_timeout_seconds,
            run_timeout_seconds=run_timeout_seconds,
            navigation_hint=navigation_hint,
        )
        return await self.run_request(request)

    async def run_request(self, request: VerifyVisualClaimsInput) -> RunResult:
        """Verify a set of claims from a prevalidated request."""

        async with self._operation_lock:
            run_artifacts = self.artifact_manager.create_run(prefix="run")

            preflight_error = await self._preflight_url(request.url)
            if preflight_error is not None:
                result = self._build_not_testable_run(
                    request=request,
                    run_dir=str(run_artifacts.run_dir),
                    summary=preflight_error,
                )
                self._save_json(run_artifacts, "run_result.json", result.model_dump())
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
                    summary=f"Could not start a browser session for {request.url}: {exc}",
                )
                self._save_json(run_artifacts, "run_result.json", result.model_dump())
                return result

            try:
                await self.browser_manager.goto(session, request.url)
            except Exception as exc:
                result = self._build_not_testable_run(
                    request=request,
                    run_dir=str(run_artifacts.run_dir),
                    summary=f"Could not navigate to {request.url}: {exc}",
                )
                self._save_json(run_artifacts, "run_result.json", result.model_dump())
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
                                    summary=f"Could not prepare browser state for this claim: {exc}",
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
                            result = self._build_claim(
                                claim=claim,
                                status="inconclusive",
                                summary=(
                                    f"Claim verification timed out after {request.claim_timeout_seconds:.0f}s "
                                    "before a verdict was recorded."
                                ),
                                final_url=getattr(session.page, "url", request.url) or request.url,
                                viewport=getattr(session, "viewport", request.viewport),
                            )
                        except Exception as exc:
                            result = self._build_claim(
                                claim=claim,
                                status="inconclusive",
                                summary=f"Verification crashed unexpectedly before returning a verdict: {exc}",
                                final_url=getattr(session.page, "url", request.url) or request.url,
                                viewport=getattr(session, "viewport", request.viewport),
                            )
                        claim_results.append(result)
                        next_claim_index = index + 1
            except TimeoutError:
                timed_out_claims = request.claims[next_claim_index - 1 :]
                claim_results.extend(
                    [
                        self._build_claim(
                            claim=claim,
                            status="inconclusive",
                            summary=(
                                f"Run timed out after {request.run_timeout_seconds:.0f}s before this claim could finish."
                            ),
                            final_url=getattr(session.page, "url", request.url) or request.url,
                            viewport=getattr(session, "viewport", request.viewport),
                        )
                        for claim in timed_out_claims
                    ]
                )

            summary = self._summarize_results(claim_results)
            overall_status = (
                "not_testable" if claim_results and all(result.status == "not_testable" for result in claim_results) else "completed"
            )
            run_result = RunResult(
                overall_status=overall_status,
                session_key=request.session_key,
                results=claim_results,
                summary=summary,
                artifacts_dir=str(run_artifacts.run_dir),
            )
            self._save_json(run_artifacts, "run_result.json", run_result.model_dump())
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
        await self.n1_client.close()

    @staticmethod
    def _summarize_results(results: list[ClaimResult]) -> str:
        counts = {
            "pass": sum(result.status == "pass" for result in results),
            "fail": sum(result.status == "fail" for result in results),
            "inconclusive": sum(result.status == "inconclusive" for result in results),
            "not_testable": sum(result.status == "not_testable" for result in results),
        }
        parts = [f"{counts['pass']}/{len(results)} claims passed."]
        if counts["fail"]:
            parts.append(f"{counts['fail']} failed.")
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
        summary: str,
    ) -> RunResult:
        results = [
            ClaimResult(
                claim=claim,
                status="not_testable",
                summary=summary,
                final_url=request.url,
                wrong_page_recovered=False,
                steps_taken=0,
                viewport=request.viewport,
                screenshots=[],
                action_trace=[],
            )
            for claim in request.claims
        ]
        return RunResult(
            overall_status="not_testable",
            session_key=request.session_key,
            results=results,
            summary=summary,
            artifacts_dir=run_dir,
        )

    @staticmethod
    def _build_claim(
        *,
        claim: str,
        status: ClaimStatus,
        summary: str,
        final_url: str,
        viewport: ViewportConfig,
    ) -> ClaimResult:
        return ClaimResult(
            claim=claim,
            status=status,
            summary=summary,
            final_url=final_url,
            wrong_page_recovered=False,
            steps_taken=0,
            viewport=viewport,
            screenshots=[],
            action_trace=[],
        )

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
        async def _call_verifier() -> ClaimResult:
            return await self.claim_verifier.verify(
                session=session,
                claim=claim,
                url=request.url,
                claim_index=claim_index,
                run_artifacts=run_artifacts,
                max_steps=request.max_steps_per_claim,
                navigation_hint=request.navigation_hint,
            )

        if request.claim_timeout_seconds:
            async with asyncio.timeout(request.claim_timeout_seconds):
                return await _call_verifier()
        return await _call_verifier()


class _null_async_context:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: type | None, exc: BaseException | None, traceback: Any) -> None:
        return None
