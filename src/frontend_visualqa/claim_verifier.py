"""Single-claim verification loop for frontend-visualqa."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from frontend_visualqa.actions import ActionExecutor
from frontend_visualqa.artifacts import ArtifactManager, RunArtifacts
from frontend_visualqa.browser import BrowserManager, BrowserSession, image_bytes_to_data_url
from frontend_visualqa.errors import BrowserActionError, N1ClientError
from frontend_visualqa.n1_client import N1Client
from frontend_visualqa.prompts import RECORD_CLAIM_RESULT_TOOL, build_force_stop_prompt, build_verification_task
from frontend_visualqa.schemas import ClaimResult, ClaimStatus


FALLBACK_VERDICT_PATTERN = re.compile(
    r"""["']?(?:status|verdict)["']?\s*[:=]\s*["']?(pass|fail|inconclusive|not[_ ]testable)\b""",
    re.IGNORECASE,
)
BUTTON_VISIBLE_PATTERN = re.compile(
    r"""^The\s+(?P<label>.+?)\s+button\s+is\s+visible(?:\s+without\s+scrolling)?\.?$""",
    re.IGNORECASE,
)
HEADING_READS_PATTERN = re.compile(r"""^The\s+heading\s+reads\s+["'](?P<text>.+?)["']\.?$""", re.IGNORECASE)
PAGE_TITLE_READS_PATTERN = re.compile(r"""^The\s+page\s+title\s+reads\s+["'](?P<text>.+?)["']\.?$""", re.IGNORECASE)
MODAL_TITLE_READS_PATTERN = re.compile(r"""^The\s+modal\s+title\s+reads\s+["'](?P<text>.+?)["']\.?$""", re.IGNORECASE)

logger = logging.getLogger(__name__)


class ClaimVerifier:
    """Run the n1 observe-think-act loop for a single claim."""

    def __init__(
        self,
        *,
        browser_manager: BrowserManager,
        artifact_manager: ArtifactManager,
        n1_client: N1Client,
        action_executor: ActionExecutor | None = None,
    ) -> None:
        self.browser_manager = browser_manager
        self.artifact_manager = artifact_manager
        self.n1_client = n1_client
        self.action_executor = action_executor or ActionExecutor(
            navigation_timeout_ms=getattr(browser_manager, "navigation_timeout_ms", 20_000)
        )

    async def verify(
        self,
        *,
        session: BrowserSession,
        claim: str,
        url: str,
        claim_index: int,
        run_artifacts: RunArtifacts,
        max_steps: int,
        navigation_hint: str | None = None,
    ) -> ClaimResult:
        """Verify a single claim within an existing browser session."""

        action_trace: list[str] = []
        url_history = [session.page.url or url]
        screenshot_paths: list[str] = []
        messages: list[dict[str, Any]] = []
        step_count = 0

        try:
            initial_bytes, initial_path = await self._capture_evidence_screenshot(
                session=session,
                run_artifacts=run_artifacts,
                claim_index=claim_index,
                label="step-00-initial",
            )
            screenshot_paths.append(initial_path)

            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": build_verification_task(claim, url, navigation_hint)},
                        {"type": "text", "text": f"Current URL: {session.page.url or url}"},
                        {
                            "type": "image_url",
                            "image_url": {"url": image_bytes_to_data_url(initial_bytes), "detail": "high"},
                        },
                    ],
                }
            ]

            while step_count < max_steps:
                messages = self._prepare_messages_for_request(messages)
                response = await self.n1_client.create(messages, tools=[RECORD_CLAIM_RESULT_TOOL])
                assistant_message = self._coerce_assistant_message(response)
                messages.append(self._message_to_dict(assistant_message))

                tool_calls = list(getattr(assistant_message, "tool_calls", []) or [])
                if not tool_calls:
                    fallback = self._parse_fallback_verdict(getattr(assistant_message, "content", None))
                    if fallback is not None:
                        return await self._finalize_result(
                            claim=claim,
                            session=session,
                            verdict=fallback,
                            step_count=step_count,
                            screenshot_paths=screenshot_paths,
                            action_trace=action_trace,
                            url_history=url_history,
                            url=url,
                            run_artifacts=run_artifacts,
                            claim_index=claim_index,
                        )
                    break

                for tool_call in tool_calls:
                    tool_name = getattr(tool_call.function, "name", "")
                    if tool_name == "record_claim_result":
                        verdict = self._extract_structured_verdict([tool_call])
                        if verdict is not None:
                            return await self._finalize_result(
                                claim=claim,
                                session=session,
                                verdict=verdict,
                                step_count=step_count,
                                screenshot_paths=screenshot_paths,
                                action_trace=action_trace,
                                url_history=url_history,
                                url=url,
                                run_artifacts=run_artifacts,
                                claim_index=claim_index,
                            )
                        continue
                    if tool_name == "stop":
                        stop_verdict = self._extract_stop_verdict([tool_call])
                        if stop_verdict is not None:
                            return await self._finalize_result(
                                claim=claim,
                                session=session,
                                verdict=stop_verdict,
                                step_count=step_count,
                                screenshot_paths=screenshot_paths,
                                action_trace=action_trace,
                                url_history=url_history,
                                url=url,
                                run_artifacts=run_artifacts,
                                claim_index=claim_index,
                            )
                        continue
                    if step_count >= max_steps:
                        break
                    execution = await self._execute_tool_call(session, tool_call)
                    trace = execution["trace"]
                    action_trace.append(trace)
                    step_count += 1
                    screenshot_bytes, screenshot_path = await self._capture_evidence_screenshot(
                        session=session,
                        run_artifacts=run_artifacts,
                        claim_index=claim_index,
                        label=f"step-{step_count:02d}",
                    )
                    screenshot_paths.append(screenshot_path)
                    url_history.append(session.page.url or url)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": [
                                {
                                    "type": "text",
                                    "text": self._build_tool_result_text(
                                        trace=trace,
                                        output_text=execution.get("output_text"),
                                        current_url=execution.get("current_url", session.page.url),
                                    ),
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {"url": image_bytes_to_data_url(screenshot_bytes), "detail": "high"},
                                },
                            ],
                        }
                    )

            return await self._force_stop(
                claim=claim,
                session=session,
                messages=messages,
                step_count=step_count,
                screenshot_paths=screenshot_paths,
                action_trace=action_trace,
                url_history=url_history,
                url=url,
                run_artifacts=run_artifacts,
                claim_index=claim_index,
            )

        except (BrowserActionError, N1ClientError) as exc:
            return self._build_result(
                claim=claim,
                session=session,
                status="not_testable",
                summary=str(exc),
                step_count=step_count,
                screenshot_paths=screenshot_paths,
                action_trace=action_trace,
                url_history=url_history,
                url=url,
                run_artifacts=run_artifacts,
                claim_index=claim_index,
            )
        except Exception as exc:
            logger.warning("Unexpected verifier failure for claim %r", claim, exc_info=True)
            return self._build_result(
                claim=claim,
                session=session,
                status="inconclusive",
                summary=f"Verification failed unexpectedly before a verdict was recorded: {exc}",
                step_count=step_count,
                screenshot_paths=screenshot_paths,
                action_trace=action_trace,
                url_history=url_history,
                url=url,
                run_artifacts=run_artifacts,
                claim_index=claim_index,
            )

    async def _capture_evidence_screenshot(
        self,
        *,
        session: BrowserSession,
        run_artifacts: RunArtifacts,
        claim_index: int,
        label: str,
    ) -> tuple[bytes, str]:
        try:
            screenshot_bytes = await self.browser_manager.capture_screenshot(session)
        except Exception as exc:
            raise BrowserActionError(f"Failed to capture screenshot for {label}: {exc}") from exc

        try:
            screenshot_path = self.artifact_manager.save_screenshot(run_artifacts, claim_index, label, screenshot_bytes)
        except Exception as exc:
            raise BrowserActionError(f"Failed to save screenshot for {label}: {exc}") from exc

        return screenshot_bytes, screenshot_path

    async def _force_stop(
        self,
        *,
        claim: str,
        session: BrowserSession,
        messages: list[dict[str, Any]],
        step_count: int,
        screenshot_paths: list[str],
        action_trace: list[str],
        url_history: list[str],
        url: str,
        run_artifacts: RunArtifacts,
        claim_index: int,
    ) -> ClaimResult:
        messages.append(
            {
                "role": "user",
                "content": [{"type": "text", "text": build_force_stop_prompt(claim)}],
            }
        )
        messages = self._prepare_messages_for_request(messages)
        assistant_message = await self.n1_client.create(messages, tools=[RECORD_CLAIM_RESULT_TOOL])
        messages.append(self._message_to_dict(assistant_message))

        verdict = self._extract_structured_verdict(getattr(assistant_message, "tool_calls", []) or [])
        if verdict is None:
            verdict = self._extract_stop_verdict(getattr(assistant_message, "tool_calls", []) or [])
        if verdict is None:
            verdict = self._parse_fallback_verdict(getattr(assistant_message, "content", None))
        if verdict is None:
            verdict = ("inconclusive", "The model did not provide a structured verdict before the step limit.")

        return await self._finalize_result(
            claim=claim,
            session=session,
            verdict=verdict,
            step_count=step_count,
            screenshot_paths=screenshot_paths,
            action_trace=action_trace,
            url_history=url_history,
            url=url,
            run_artifacts=run_artifacts,
            claim_index=claim_index,
        )

    def _build_result(
        self,
        *,
        claim: str,
        session: BrowserSession,
        status: ClaimStatus,
        summary: str,
        step_count: int,
        screenshot_paths: list[str],
        action_trace: list[str],
        url_history: list[str],
        url: str,
        run_artifacts: RunArtifacts,
        claim_index: int,
    ) -> ClaimResult:
        try:
            trace_path = self.artifact_manager.save_trace(run_artifacts, claim_index, action_trace)
        except Exception:
            trace_path = None
        return ClaimResult(
            claim=claim,
            status=status,
            summary=summary,
            final_url=session.page.url or url,
            wrong_page_recovered=self._wrong_page_recovered(url_history, url, action_trace),
            steps_taken=step_count,
            viewport=session.viewport,
            screenshots=screenshot_paths,
            action_trace=action_trace,
            trace_path=trace_path,
        )

    def _prepare_messages_for_request(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        trim_messages = getattr(self.n1_client, "trim_messages", None)
        if callable(trim_messages):
            return trim_messages(messages)
        return messages

    @staticmethod
    def _build_tool_result_text(trace: str, current_url: str, output_text: str | None = None) -> str:
        if output_text:
            return f"{output_text}\nCurrent URL: {current_url}"
        return f"Executed {trace}.\nCurrent URL: {current_url}"

    @staticmethod
    def _message_to_dict(message: Any) -> dict[str, Any]:
        if hasattr(message, "model_dump"):
            return message.model_dump(exclude_none=True)
        if isinstance(message, dict):
            return message
        raise TypeError(f"Unsupported assistant message type: {type(message)!r}")

    @staticmethod
    def _coerce_assistant_message(response_or_message: Any) -> Any:
        if hasattr(response_or_message, "choices"):
            return response_or_message.choices[0].message
        return response_or_message

    async def _execute_tool_call(self, session: BrowserSession, tool_call: Any) -> dict[str, Any]:
        if hasattr(self.action_executor, "execute_tool_call"):
            result = await self.action_executor.execute_tool_call(session, tool_call)
            if isinstance(result, str):
                return {"trace": result, "output_text": None, "current_url": session.page.url}
            return {
                "trace": getattr(result, "trace", str(result)),
                "output_text": getattr(result, "output_text", None),
                "current_url": getattr(result, "current_url", session.page.url),
            }

        arguments = self._parse_tool_arguments(tool_call)
        trace = await self.action_executor.execute_action(session, tool_call.function.name, arguments)
        return {"trace": trace, "output_text": None, "current_url": session.page.url}

    @staticmethod
    def _parse_tool_arguments(tool_call: Any) -> dict[str, Any]:
        arguments = getattr(tool_call.function, "arguments", "{}") or "{}"
        if isinstance(arguments, dict):
            return arguments
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise BrowserActionError(f"tool arguments were not valid JSON: {arguments}") from exc
        if not isinstance(parsed, dict):
            raise BrowserActionError(f"tool arguments must decode to an object: {arguments}")
        return parsed

    @staticmethod
    def _extract_structured_verdict(tool_calls: list[Any]) -> tuple[ClaimStatus, str] | None:
        for tool_call in tool_calls:
            if getattr(tool_call.function, "name", "") != "record_claim_result":
                continue
            try:
                arguments = ClaimVerifier._parse_tool_arguments(tool_call)
            except BrowserActionError as exc:
                return "inconclusive", str(exc)
            status = str(arguments.get("status", "")).strip()
            summary = str(arguments.get("summary", "")).strip() or "No summary provided."
            if status in {"pass", "fail", "inconclusive", "not_testable"}:
                return status, summary
        return None

    @staticmethod
    def _extract_stop_verdict(tool_calls: list[Any]) -> tuple[ClaimStatus, str] | None:
        for tool_call in tool_calls:
            if getattr(tool_call.function, "name", "") != "stop":
                continue

            status: ClaimStatus = "inconclusive"
            summary = "The model stopped before recording a structured verdict."
            try:
                arguments = ClaimVerifier._parse_tool_arguments(tool_call)
            except BrowserActionError:
                return status, summary

            explicit_status = str(arguments.get("status", "")).strip().lower().replace(" ", "_")
            if explicit_status in {"pass", "fail", "inconclusive", "not_testable"}:
                status = explicit_status

            explicit_summary = str(
                arguments.get("summary") or arguments.get("reason") or arguments.get("message") or ""
            ).strip()
            if explicit_summary:
                summary = explicit_summary

            return status, summary
        return None

    @staticmethod
    def _parse_fallback_verdict(content: Any) -> tuple[ClaimStatus, str] | None:
        if content is None:
            return None
        text = content if isinstance(content, str) else str(content)
        match = FALLBACK_VERDICT_PATTERN.search(text)
        if match is None:
            return None

        status = match.group(1).lower().replace(" ", "_")
        return status, text

    @staticmethod
    def _wrong_page_recovered(url_history: list[str], target_url: str, action_trace: list[str] | None = None) -> bool:
        del action_trace
        seen_other = False
        for current_url in url_history:
            if current_url != target_url:
                seen_other = True
            if seen_other and current_url == target_url:
                return True
        return False

    async def _finalize_result(
        self,
        *,
        claim: str,
        session: BrowserSession,
        verdict: tuple[ClaimStatus, str],
        step_count: int,
        screenshot_paths: list[str],
        action_trace: list[str],
        url_history: list[str],
        url: str,
        run_artifacts: RunArtifacts,
        claim_index: int,
    ) -> ClaimResult:
        status, summary = verdict
        grounded_status, grounded_summary = await self._ground_verdict(
            session=session,
            claim=claim,
            status=status,
            summary=summary,
        )
        return self._build_result(
            claim=claim,
            session=session,
            status=grounded_status,
            summary=grounded_summary,
            step_count=step_count,
            screenshot_paths=screenshot_paths,
            action_trace=action_trace,
            url_history=url_history,
            url=url,
            run_artifacts=run_artifacts,
            claim_index=claim_index,
        )

    async def _ground_verdict(
        self,
        *,
        session: BrowserSession,
        claim: str,
        status: ClaimStatus,
        summary: str,
    ) -> tuple[ClaimStatus, str]:
        if status != "pass":
            return status, summary

        try:
            visual_state = await self._capture_visible_text_state(session)
        except Exception:
            logger.warning("Failed to gather DOM grounding hints for pass verdict", exc_info=True)
            return status, summary

        normalized_claim = self._normalize_text(claim)
        for pattern, checker in (
            (MODAL_TITLE_READS_PATTERN, self._check_dialog_title_match),
            (HEADING_READS_PATTERN, self._check_heading_match),
            (PAGE_TITLE_READS_PATTERN, self._check_heading_match),
            (BUTTON_VISIBLE_PATTERN, self._check_button_match),
        ):
            match = pattern.match(claim.strip())
            if match is None:
                continue
            grounded = checker(visual_state, match.groupdict())
            if grounded is None:
                return status, summary
            grounded_status, grounded_summary = grounded
            if grounded_status != "pass":
                logger.info("Downgrading pass verdict for claim %r after grounding check", claim)
            return grounded_status, grounded_summary

        if any(marker in normalized_claim for marker in {" title reads ", " heading reads ", " button is visible"}):
            logger.info("No grounding rule matched pass verdict for claim %r", claim)
        return status, summary

    async def _capture_visible_text_state(self, session: BrowserSession) -> dict[str, list[str]]:
        return await session.page.evaluate(
            """() => {
                const normalize = (value) => (value || "").replace(/\\s+/g, " ").trim();
                const isVisible = (element) => {
                    if (!element) return false;
                    const style = window.getComputedStyle(element);
                    if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0) {
                        return false;
                    }
                    const rect = element.getBoundingClientRect();
                    if (rect.width <= 0 || rect.height <= 0) {
                        return false;
                    }
                    if (rect.bottom <= 0 || rect.right <= 0) {
                        return false;
                    }
                    if (rect.top >= window.innerHeight || rect.left >= window.innerWidth) {
                        return false;
                    }
                    return true;
                };
                const elementText = (element) => {
                    if (!element) return "";
                    const explicitText = normalize(element.innerText || element.textContent || "");
                    if (explicitText) return explicitText;
                    const ariaLabel = normalize(element.getAttribute("aria-label"));
                    if (ariaLabel) return ariaLabel;
                    const value = normalize(element.value);
                    return value;
                };
                const visibleHeadings = Array.from(document.querySelectorAll("h1, h2, h3, h4, [role='heading']"))
                    .filter(isVisible)
                    .map(elementText)
                    .filter(Boolean);
                const visibleButtons = Array.from(
                    document.querySelectorAll("button, [role='button'], input[type='button'], input[type='submit']")
                )
                    .filter(isVisible)
                    .map(elementText)
                    .filter(Boolean);
                const dialogTitles = Array.from(document.querySelectorAll("[role='dialog'], dialog, [aria-modal='true']"))
                    .filter(isVisible)
                    .flatMap((dialog) => {
                        const titles = [];
                        const labelledBy = dialog.getAttribute("aria-labelledby");
                        if (labelledBy) {
                            const labelElement = document.getElementById(labelledBy);
                            if (isVisible(labelElement)) {
                                const text = elementText(labelElement);
                                if (text) titles.push(text);
                            }
                        }
                        for (const heading of dialog.querySelectorAll("h1, h2, h3, h4, [role='heading']")) {
                            if (!isVisible(heading)) continue;
                            const text = elementText(heading);
                            if (text) titles.push(text);
                        }
                        return titles;
                    })
                    .filter(Boolean);
                return { visibleHeadings, visibleButtons, dialogTitles };
            }"""
        )

    @staticmethod
    def _normalize_text(value: str) -> str:
        return " ".join(value.split()).strip().casefold()

    def _check_heading_match(
        self,
        visual_state: dict[str, list[str]],
        groups: dict[str, str],
    ) -> tuple[ClaimStatus, str] | None:
        expected = self._normalize_text(groups["text"])
        visible_headings = visual_state.get("visibleHeadings", [])
        if any(self._normalize_text(text) == expected for text in visible_headings):
            return "pass", f"Visible heading matched {groups['text']!r}."
        return (
            "fail",
            f"No visible heading matched {groups['text']!r}. Visible headings: {visible_headings or ['<none>']}.",
        )

    def _check_dialog_title_match(
        self,
        visual_state: dict[str, list[str]],
        groups: dict[str, str],
    ) -> tuple[ClaimStatus, str] | None:
        expected = self._normalize_text(groups["text"])
        dialog_titles = visual_state.get("dialogTitles", [])
        if any(self._normalize_text(text) == expected for text in dialog_titles):
            return "pass", f"Visible dialog title matched {groups['text']!r}."
        return (
            "fail",
            f"No visible dialog title matched {groups['text']!r}. Visible dialog titles: {dialog_titles or ['<none>']}.",
        )

    def _check_button_match(
        self,
        visual_state: dict[str, list[str]],
        groups: dict[str, str],
    ) -> tuple[ClaimStatus, str] | None:
        label = self._normalize_text(groups["label"])
        visible_buttons = visual_state.get("visibleButtons", [])
        for candidate in visible_buttons:
            normalized_candidate = self._normalize_text(candidate)
            if normalized_candidate == label or normalized_candidate.startswith(f"{label} "):
                return "pass", f"Visible button label matched {groups['label']!r}: {candidate!r}."
        return (
            "fail",
            f"No visible button label matched {groups['label']!r}. Visible buttons: {visible_buttons or ['<none>']}.",
        )
