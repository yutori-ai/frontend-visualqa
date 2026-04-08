"""Single-claim verification loop for frontend-visualqa."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from frontend_visualqa.actions import ActionExecutor, EXTRACT_CONTENT_AND_LINKS_TOOL_NAME
from frontend_visualqa.artifacts import ArtifactManager, RunArtifacts
from frontend_visualqa.browser import BrowserManager, BrowserSession, image_bytes_to_data_url
from frontend_visualqa.errors import BrowserActionError, N1ClientError
from frontend_visualqa.grounding import capture_grounding_state, ground_claim_verdict
from frontend_visualqa.hook_adapter import VisualQAHookAdapter
from frontend_visualqa.recovery import wrong_page_recovered
from frontend_visualqa.prompts import (
    EXTRACT_CONTENT_AND_LINKS_TOOL,
    RECORD_CLAIM_RESULT_TOOL,
    build_action_or_verdict_prompt,
    build_follow_navigation_hint_prompt,
    build_force_stop_prompt,
    build_take_action_prompt,
    build_verification_task,
)
from frontend_visualqa.schemas import ClaimPage, ClaimProof, ClaimResult, ClaimStatus, ClaimTrace
from frontend_visualqa.text_utils import clip_text
from frontend_visualqa.tool_arguments import parse_tool_arguments

if TYPE_CHECKING:
    from frontend_visualqa.n1_client import N1Client


FALLBACK_VERDICT_PATTERN = re.compile(
    r"""["']?(?:status|verdict)["']?\s*[:=]\s*["']?(passed|failed|inconclusive|not[_ ]testable)\b""",
    re.IGNORECASE,
)
NEGATIVE_CLAIM_PATTERN = re.compile(
    r"""\bnot\b|\bno\s+\w+|\bhidden\b|\bdisabled\b|\bmissing\b|\babsent\b|\bincorrect\b|\bwrong\b""",
    re.IGNORECASE,
)
FAILED_FINDING_PATTERNS = (
    re.compile(r"""\b(?:does not|do not|did not|doesn't|don't)\s+match\b""", re.IGNORECASE),
    re.compile(r"""\b(?:does not|do not|did not|doesn't|don't)\s+equal\b""", re.IGNORECASE),
    re.compile(r"""\bnot\s+equal\b""", re.IGNORECASE),
    re.compile(r"""\b(?:is|are)\s+not\s+visible\b""", re.IGNORECASE),
    re.compile(r"""\bnot\s+fully\s+visible\b""", re.IGNORECASE),
    re.compile(r"""\bnot\s+correct\b""", re.IGNORECASE),
    re.compile(r"""\bincorrect\b""", re.IGNORECASE),
    re.compile(r"""\bclaim\s+is\s+false\b""", re.IGNORECASE),
)
INCONCLUSIVE_FINDING_PATTERNS = (
    re.compile(r"""\bcannot\s+(?:be\s+)?(?:definitively\s+)?(?:verify|verified|determine|tell)\b""", re.IGNORECASE),
    re.compile(r"""\bcan['’]?t\s+(?:determine|tell|verify)\b""", re.IGNORECASE),
    re.compile(r"""\bnot\s+enough\s+evidence\b""", re.IGNORECASE),
    re.compile(r"""\binconclusive\b""", re.IGNORECASE),
)
ACTION_NEEDED_FINDING_PATTERNS = (
    re.compile(
        r"""\b(?:i\s+)?need\s+to\s+(?:click|tap|open|navigate|go|scroll|expand|select|hover)\b""", re.IGNORECASE
    ),
    re.compile(r"""\b(?:i\s+)?should\s+(?:click|tap|open|navigate|go|scroll|expand|select|hover)\b""", re.IGNORECASE),
    re.compile(r"""\bbefore\s+i\s+can\s+(?:verify|determine|confirm|decide)\b""", re.IGNORECASE),
)

logger = logging.getLogger(__name__)

MAX_NON_ACTION_REPROMPTS = 2
MAX_INLINE_PROOF_TEXT_CHARS = 280
MAX_INLINE_PROOF_TEXT_LINES = 6

VERDICT_SOURCE_RECORD = "record_claim_result"
VERDICT_SOURCE_FALLBACK = "fallback_content"
VERDICT_SOURCE_LEGACY_STOP = "legacy_stop"
VERDICT_SOURCE_FORCE_STOP = "force_stop"


def _user_text_message(text: str) -> dict[str, Any]:
    return {"role": "user", "content": [{"type": "text", "text": text}]}


@dataclass
class _VerificationProgress:
    claim: str
    session: BrowserSession
    url: str
    run_artifacts: RunArtifacts
    claim_index: int
    step_count: int
    screenshot_paths: list[str]
    action_trace: list[str]
    url_history: list[str]
    has_interacted: bool = False
    proof_text: str | None = None
    proof_text_path: str | None = None


def _create_overlay_controller(page: Any) -> Any | None:
    try:
        from frontend_visualqa.overlay import OverlayController
    except Exception:
        return None
    try:
        return OverlayController(page)
    except Exception:
        logger.debug("Failed to construct overlay controller", exc_info=True)
        return None


class ClaimVerifier:
    """Run the n1 observe-think-act loop for a single claim."""

    def __init__(
        self,
        *,
        browser_manager: BrowserManager,
        artifact_manager: ArtifactManager,
        n1_client: N1Client,
        action_executor: ActionExecutor | None = None,
        visualize: bool = False,
    ) -> None:
        self.browser_manager = browser_manager
        self.artifact_manager = artifact_manager
        self.n1_client = n1_client
        self.action_executor = action_executor or ActionExecutor(
            navigation_timeout_ms=getattr(browser_manager, "navigation_timeout_ms", 20_000)
        )
        self._visualize = visualize
        self._overlay: Any | None = None
        self._hook: VisualQAHookAdapter | None = None
        self._partial_progress: _VerificationProgress | None = None

    def set_browser_manager(self, browser_manager: BrowserManager, *, visualize: bool | None = None) -> None:
        """Rebind long-lived browser dependencies after the runner reconfigures the browser.

        Clears overlay, hook, and partial progress state tied to the previous browser instance.
        """

        self.browser_manager = browser_manager
        self.action_executor.navigation_timeout_ms = getattr(browser_manager, "navigation_timeout_ms", 20_000)
        self.action_executor.overlay = None
        self._overlay = None
        self._hook = None
        self._partial_progress = None
        if visualize is not None:
            self._visualize = visualize

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
        visualize: bool | None = None,
    ) -> ClaimResult:
        """Verify a single claim within an existing browser session."""

        messages: list[dict[str, Any]] = []
        non_action_reprompts = 0
        should_visualize = self._visualize if visualize is None else visualize
        progress = _VerificationProgress(
            claim=claim,
            session=session,
            url=url,
            run_artifacts=run_artifacts,
            claim_index=claim_index,
            step_count=0,
            screenshot_paths=[],
            action_trace=[],
            url_history=[session.page.url or url],
        )
        self._partial_progress = progress
        preserve_partial_progress = False

        try:
            self.action_executor.overlay = None
            self._overlay = None
            self._hook = None

            initial_bytes, initial_path = await self._capture_evidence_screenshot(
                session=session,
                run_artifacts=run_artifacts,
                claim_index=claim_index,
                label="step-00",
            )
            progress.screenshot_paths.append(initial_path)

            if should_visualize:
                self._overlay = _create_overlay_controller(session.page)
                if self._overlay is not None:
                    self.action_executor.overlay = self._overlay
                    await self._best_effort_overlay_call("claim_started")
            self._hook = VisualQAHookAdapter(self._overlay)

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

            await self._safe_hook_call("on_agent_start", messages=messages)

            while progress.step_count < max_steps:
                messages = self._prepare_messages_for_request(messages)
                model_tools = self._model_tools()
                await self._safe_hook_call("on_llm_start", messages=messages, tools=model_tools)
                await self._best_effort_overlay_call("set_status", "Analyzing")
                response = await self.n1_client.create(messages, tools=model_tools)
                assistant_message = self._coerce_assistant_message(response)
                await self._safe_hook_call("on_llm_end", response=assistant_message)
                messages.append(self._message_to_dict(assistant_message))

                tool_calls = list(getattr(assistant_message, "tool_calls", []) or [])
                if not tool_calls:
                    fallback = self._parse_fallback_verdict(getattr(assistant_message, "content", None))
                    if fallback is not None:
                        result = await self._finalize_result(
                            progress=progress,
                            verdict=fallback,
                            verdict_source=VERDICT_SOURCE_FALLBACK,
                        )
                        return await self._complete_result(result)
                    if non_action_reprompts < MAX_NON_ACTION_REPROMPTS:
                        non_action_reprompts += 1
                        messages.append(_user_text_message(build_action_or_verdict_prompt(claim)))
                        continue
                    break

                had_action_in_turn = False
                responded_tool_ids: set[str] = set()
                for tool_call in tool_calls:
                    tool_name = getattr(tool_call.function, "name", "")
                    if tool_name == "record_claim_result":
                        verdict = self._extract_structured_verdict([tool_call])
                        if verdict is not None:
                            reprompt_text: str | None = None
                            force_stop_finding: str | None = None
                            if (
                                not progress.has_interacted
                                and verdict[0] == "inconclusive"
                                and self._finding_says_action_is_needed(verdict[1])
                            ):
                                reprompt_text = build_take_action_prompt(claim)
                                force_stop_finding = "The model kept saying more interaction was needed without taking the next browser action."
                            elif navigation_hint and not progress.has_interacted and verdict[0] != "not_testable":
                                reprompt_text = build_follow_navigation_hint_prompt(claim, navigation_hint)
                                force_stop_finding = (
                                    "The model tried to render a verdict before following the navigation hint."
                                )
                            if reprompt_text is not None:
                                if non_action_reprompts < MAX_NON_ACTION_REPROMPTS:
                                    non_action_reprompts += 1
                                    for tc in tool_calls:
                                        if tc.id not in responded_tool_ids:
                                            messages.append(
                                                {
                                                    "role": "tool",
                                                    "tool_call_id": tc.id,
                                                    "content": [
                                                        {
                                                            "type": "text",
                                                            "text": "Verdict not accepted; see follow-up instructions.",
                                                        }
                                                    ],
                                                }
                                            )
                                    messages.append(_user_text_message(reprompt_text))
                                    break
                                result = await self._finalize_result(
                                    progress=progress,
                                    verdict=("inconclusive", force_stop_finding or ""),
                                    verdict_source=VERDICT_SOURCE_FORCE_STOP,
                                )
                                await self._show_post_capture_analysis(had_actions=had_action_in_turn)
                                return await self._complete_result(result)
                            result = await self._finalize_result(
                                progress=progress,
                                verdict=verdict,
                                verdict_source=VERDICT_SOURCE_RECORD,
                            )
                            await self._show_post_capture_analysis(had_actions=had_action_in_turn)
                            return await self._complete_result(result)
                        continue
                    if tool_name == "stop":
                        stop_verdict = self._extract_stop_verdict([tool_call])
                        if stop_verdict is not None:
                            result = await self._finalize_result(
                                progress=progress,
                                verdict=stop_verdict,
                                verdict_source=VERDICT_SOURCE_LEGACY_STOP,
                            )
                            await self._show_post_capture_analysis(had_actions=had_action_in_turn)
                            return await self._complete_result(result)
                        continue
                    if progress.step_count >= max_steps:
                        break
                    tool_arguments = parse_tool_arguments(tool_call)
                    await self._safe_hook_call("on_tool_start", name=tool_name, arguments=tool_arguments)
                    execution = await self._execute_tool_call(session, tool_call)
                    trace = execution["trace"]
                    progress.action_trace.append(trace)
                    output_text = execution.get("output_text")
                    await self._safe_hook_call(
                        "on_tool_end",
                        name=tool_name,
                        arguments=tool_arguments,
                        output=output_text,
                        trace=trace,
                    )
                    current_url = execution.get("current_url", session.page.url) or url
                    progress.url_history.append(current_url)
                    is_read_only = tool_name == EXTRACT_CONTENT_AND_LINKS_TOOL_NAME
                    progress.step_count += 1
                    if not is_read_only:
                        non_action_reprompts = 0
                        had_action_in_turn = True
                        progress.has_interacted = True
                    screenshot_bytes, screenshot_path = await self._capture_evidence_screenshot(
                        session=session,
                        run_artifacts=run_artifacts,
                        claim_index=claim_index,
                        label=f"step-{progress.step_count:02d}",
                    )
                    progress.screenshot_paths.append(screenshot_path)
                    self._record_action_event(
                        step=progress.step_count,
                        action=tool_name,
                        action_args=tool_arguments,
                        output_text=output_text,
                        screenshot_path=screenshot_path,
                    )
                    progress.proof_text = str(output_text) if output_text else None
                    progress.proof_text_path = self._save_proof_text(
                        run_artifacts=run_artifacts,
                        claim_index=claim_index,
                        label=f"step-{progress.step_count:02d}",
                        proof_text=progress.proof_text,
                    )
                    responded_tool_ids.add(tool_call.id)
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
                                        current_url=current_url,
                                    ),
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {"url": image_bytes_to_data_url(screenshot_bytes), "detail": "high"},
                                },
                            ],
                        }
                    )

                await self._show_post_capture_analysis(had_actions=had_action_in_turn)
            result = await self._force_stop(progress=progress, messages=messages)
            return await self._complete_result(result)
        except asyncio.CancelledError:
            # on_agent_end is intentionally skipped here: the task is being
            # torn down externally, and the async hook cannot be awaited
            # reliably during cancellation.  Events are still preserved via
            # _partial_progress and collected by consume_partial_result.
            preserve_partial_progress = True
            raise
        except (BrowserActionError, N1ClientError) as exc:
            result = self._build_result(progress=progress, status="not_testable", finding=str(exc))
            return await self._complete_result(result)
        except Exception as exc:
            logger.warning("Unexpected verifier failure for claim %r", claim, exc_info=True)
            result = self._build_result(
                progress=progress,
                status="inconclusive",
                finding=f"Verification failed unexpectedly before a verdict was recorded: {exc}",
            )
            return await self._complete_result(result)
        finally:
            try:
                if not preserve_partial_progress:
                    self._partial_progress = None
                    self._hook = None
                await self._best_effort_overlay_call("claim_ended")
            finally:
                self.action_executor.overlay = None
                self._overlay = None

    async def _capture_evidence_screenshot(
        self,
        *,
        session: BrowserSession,
        run_artifacts: RunArtifacts,
        claim_index: int,
        label: str,
    ) -> tuple[bytes, str]:
        try:
            await self._best_effort_overlay_call("before_screenshot")
            screenshot_bytes = await self.browser_manager.capture_screenshot(session)
        except Exception as exc:
            raise BrowserActionError(f"Failed to capture screenshot for {label}: {exc}") from exc
        finally:
            await self._best_effort_overlay_call("after_screenshot")

        try:
            screenshot_path = self.artifact_manager.save_screenshot(run_artifacts, claim_index, label, screenshot_bytes)
        except Exception as exc:
            raise BrowserActionError(f"Failed to save screenshot for {label}: {exc}") from exc

        return screenshot_bytes, screenshot_path

    async def _force_stop(
        self,
        *,
        progress: _VerificationProgress,
        messages: list[dict[str, Any]],
    ) -> ClaimResult:
        messages.append(_user_text_message(build_force_stop_prompt(progress.claim)))
        messages = self._prepare_messages_for_request(messages)
        model_tools = self._model_tools()
        await self._safe_hook_call("on_llm_start", messages=messages, tools=model_tools)
        await self._best_effort_overlay_call("set_status", "Analyzing")
        response = await self.n1_client.create(messages, tools=model_tools)
        assistant_message = self._coerce_assistant_message(response)
        await self._safe_hook_call("on_llm_end", response=assistant_message)
        messages.append(self._message_to_dict(assistant_message))

        verdict_source = VERDICT_SOURCE_RECORD
        verdict = self._extract_structured_verdict(getattr(assistant_message, "tool_calls", []) or [])
        if verdict is None:
            verdict_source = VERDICT_SOURCE_LEGACY_STOP
            verdict = self._extract_stop_verdict(getattr(assistant_message, "tool_calls", []) or [])
        if verdict is None:
            verdict_source = VERDICT_SOURCE_FORCE_STOP
            verdict = self._parse_fallback_verdict(getattr(assistant_message, "content", None))
        if verdict is None:
            verdict_source = VERDICT_SOURCE_FORCE_STOP
            verdict = ("inconclusive", "The model did not provide a structured verdict before the step limit.")

        return await self._finalize_result(progress=progress, verdict=verdict, verdict_source=verdict_source)

    async def _show_post_capture_analysis(self, *, had_actions: bool) -> None:
        if not had_actions:
            return

        # The screenshot is already clean at this point. Restore only the
        # persistent analysis affordances that should cover the next turn.
        await self._best_effort_overlay_call("set_status", "Analyzing")
        reasoning = self._hook.current_turn_reasoning if self._hook else None
        if reasoning and self._overlay:
            await self._best_effort_overlay_call("show_thought", reasoning)

    async def _best_effort_overlay_call(self, method_name: str, *args: Any, **kwargs: Any) -> None:
        overlay = self._overlay
        if overlay is None:
            return
        method = getattr(overlay, method_name, None)
        if not callable(method):
            return
        try:
            await method(*args, **kwargs)
        except Exception:
            logger.debug("Overlay method %s failed", method_name, exc_info=True)

    async def _safe_hook_call(self, method_name: str, **kwargs: Any) -> None:
        hook = self._hook
        if hook is None:
            return
        method = getattr(hook, method_name, None)
        if not callable(method):
            return
        try:
            await method(**kwargs)
        except Exception:
            logger.debug("Hook method %s failed", method_name, exc_info=True)

    async def _complete_result(self, result: ClaimResult) -> ClaimResult:
        await self._safe_hook_call("on_agent_end", output=result)
        return result

    def _build_result(
        self,
        *,
        progress: _VerificationProgress,
        status: ClaimStatus,
        finding: str,
    ) -> ClaimResult:
        events = list(self._hook.events) if self._hook is not None else []
        try:
            trace_path = self.artifact_manager.save_rich_trace(
                progress.run_artifacts,
                progress.claim_index,
                [event.model_dump(mode="json") for event in events],
            )
        except Exception:
            trace_path = None
        proof = None
        if progress.screenshot_paths:
            proof_step = max(len(progress.screenshot_paths) - 1, 0)
            proof = ClaimProof(
                screenshot_path=progress.screenshot_paths[-1],
                step=proof_step,
                after_action=progress.action_trace[proof_step - 1]
                if proof_step > 0 and len(progress.action_trace) >= proof_step
                else None,
                text=self._build_inline_proof_text(progress.proof_text),
                text_path=progress.proof_text_path,
            )
        return ClaimResult(
            claim=progress.claim,
            status=status,
            finding=finding,
            proof=proof,
            page=ClaimPage(url=progress.session.page.url or progress.url, viewport=progress.session.viewport),
            trace=ClaimTrace(
                steps_taken=progress.step_count,
                wrong_page_recovered=wrong_page_recovered(progress.url_history, progress.url),
                screenshot_paths=progress.screenshot_paths,
                actions=progress.action_trace,
                events=events,
                trace_path=trace_path,
            ),
        )

    def consume_partial_result(self, *, status: ClaimStatus, finding: str) -> ClaimResult | None:
        progress = self._partial_progress
        self._partial_progress = None
        if progress is None:
            return None
        result = self._build_result(progress=progress, status=status, finding=finding)
        self._hook = None
        return result

    def _prepare_messages_for_request(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self.n1_client.trim_messages(messages)

    @staticmethod
    def _model_tools() -> list[dict[str, Any]]:
        # n1's built-in browser actions are injected server-side by the Yutori
        # chat completions endpoint. We only need to send truly custom tools.
        return [EXTRACT_CONTENT_AND_LINKS_TOOL, RECORD_CLAIM_RESULT_TOOL]

    @staticmethod
    def _build_tool_result_text(trace: str, current_url: str, output_text: str | None = None) -> str:
        if output_text:
            return f"{output_text}\nCurrent URL: {current_url}"
        return f"Executed {trace}.\nCurrent URL: {current_url}"

    def _save_proof_text(
        self,
        *,
        run_artifacts: RunArtifacts,
        claim_index: int,
        label: str,
        proof_text: str | None,
    ) -> str | None:
        if not proof_text:
            return None
        try:
            return self.artifact_manager.save_proof_text(run_artifacts, claim_index, label, proof_text)
        except Exception:
            logger.warning("Failed to save proof text for %s", label, exc_info=True)
            return None

    @staticmethod
    def _build_inline_proof_text(proof_text: str | None) -> str | None:
        if proof_text is None:
            return None
        normalized = proof_text.strip()
        if not normalized:
            return None

        lines = normalized.splitlines()
        preview_lines: list[str] = []
        used_chars = 0
        truncated = False
        for line in lines:
            cleaned_line = line.rstrip()
            if not preview_lines and not cleaned_line:
                continue
            additional_chars = len(cleaned_line) + (1 if preview_lines else 0)
            if (
                len(preview_lines) >= MAX_INLINE_PROOF_TEXT_LINES
                or used_chars + additional_chars > MAX_INLINE_PROOF_TEXT_CHARS
            ):
                truncated = True
                break
            preview_lines.append(cleaned_line)
            used_chars += additional_chars

        if preview_lines:
            preview = "\n".join(preview_lines).rstrip()
        else:
            preview = normalized[:MAX_INLINE_PROOF_TEXT_CHARS].rstrip()
            truncated = len(preview) < len(normalized)

        if not truncated and len(preview) < len(normalized):
            truncated = True

        if truncated:
            suffix = "\n..." if "\n" in preview else "..."
            if len(preview) + len(suffix) > MAX_INLINE_PROOF_TEXT_CHARS:
                preview = preview[: MAX_INLINE_PROOF_TEXT_CHARS - len(suffix)].rstrip()
            preview = f"{preview}{suffix}"
        return preview

    def _record_action_event(
        self,
        *,
        step: int,
        action: str,
        action_args: dict[str, Any],
        output_text: str | None,
        screenshot_path: str | None,
    ) -> None:
        hook = self._hook
        if hook is None:
            return
        try:
            hook.record_action_event(
                step=step,
                action=action,
                action_args=action_args,
                output_preview=self._clip_trace_output_preview(output_text),
                screenshot_path=screenshot_path,
            )
        except Exception:
            logger.debug("record_action_event failed", exc_info=True)

    @staticmethod
    def _clip_trace_output_preview(output_text: str | None) -> str | None:
        if output_text is None:
            return None
        clipped = clip_text(output_text, 280, ellipsis="…")
        return clipped or None

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
        result = await self.action_executor.execute_tool_call(session, tool_call)
        if isinstance(result, str):
            return {"trace": result, "output_text": None, "current_url": session.page.url}
        return {
            "trace": getattr(result, "trace", str(result)),
            "output_text": getattr(result, "output_text", None),
            "current_url": getattr(result, "current_url", None) or session.page.url,
        }

    @staticmethod
    def _extract_structured_verdict(tool_calls: list[Any]) -> tuple[ClaimStatus, str] | None:
        for tool_call in tool_calls:
            if getattr(tool_call.function, "name", "") != "record_claim_result":
                continue
            try:
                arguments = parse_tool_arguments(tool_call)
            except BrowserActionError as exc:
                return "inconclusive", str(exc)
            status = str(arguments.get("status", "")).strip()
            finding = str(arguments.get("finding") or arguments.get("summary") or "").strip() or "No finding provided."
            if status in {"passed", "failed", "inconclusive", "not_testable"}:
                return status, finding
        return None

    @staticmethod
    def _extract_stop_verdict(tool_calls: list[Any]) -> tuple[ClaimStatus, str] | None:
        for tool_call in tool_calls:
            if getattr(tool_call.function, "name", "") != "stop":
                continue

            status: ClaimStatus = "inconclusive"
            finding = "The model stopped before recording a structured verdict."
            try:
                arguments = parse_tool_arguments(tool_call)
            except BrowserActionError:
                return status, finding

            explicit_status = str(arguments.get("status", "")).strip().lower().replace(" ", "_")
            if explicit_status in {"passed", "failed", "inconclusive", "not_testable"}:
                status = explicit_status

            explicit_finding = str(
                arguments.get("finding")
                or arguments.get("summary")
                or arguments.get("reason")
                or arguments.get("message")
                or ""
            ).strip()
            if explicit_finding:
                finding = explicit_finding

            return status, finding
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

    async def _finalize_result(
        self,
        *,
        progress: _VerificationProgress,
        verdict: tuple[ClaimStatus, str],
        verdict_source: str | None = None,
    ) -> ClaimResult:
        status, finding = verdict
        grounded_status, grounded_finding = await self._ground_verdict(
            session=progress.session,
            claim=progress.claim,
            status=status,
            finding=finding,
        )
        hook = self._hook
        if verdict_source is not None and hook is not None:
            try:
                hook.record_verdict_event(
                    step=progress.step_count,
                    source=verdict_source,
                    raw_status=status,
                    raw_finding=finding,
                    status=grounded_status,
                    finding=grounded_finding,
                )
            except Exception:
                logger.debug("record_verdict_event failed", exc_info=True)
        return self._build_result(progress=progress, status=grounded_status, finding=grounded_finding)

    async def _ground_verdict(
        self,
        *,
        session: BrowserSession,
        claim: str,
        status: ClaimStatus,
        finding: str,
    ) -> tuple[ClaimStatus, str]:
        if status == "not_testable":
            return status, finding

        try:
            grounding_state = await capture_grounding_state(session)
        except Exception:
            logger.warning("Failed to gather grounding state for claim %r", claim, exc_info=True)
            return self._reconcile_verdict_and_finding(claim=claim, status=status, finding=finding)

        grounded_status, grounded_finding = ground_claim_verdict(
            claim=claim,
            status=status,
            finding=finding,
            grounding_state=grounding_state,
        )
        return self._reconcile_verdict_and_finding(
            claim=claim,
            status=grounded_status,
            finding=grounded_finding,
        )

    @classmethod
    def _reconcile_verdict_and_finding(
        cls,
        *,
        claim: str,
        status: ClaimStatus,
        finding: str,
    ) -> tuple[ClaimStatus, str]:
        if status != "passed":
            return status, finding

        if cls._finding_has_failure_cue(finding) and not cls._claim_is_negative(claim):
            logger.info(
                "Downgrading pass verdict for claim %r because the finding described contradictory evidence", claim
            )
            return "failed", f"Model reported passed, but its own finding described contradictory evidence. {finding}"

        if cls._finding_has_inconclusive_cue(finding):
            logger.info("Downgrading pass verdict for claim %r because the finding described uncertainty", claim)
            return (
                "inconclusive",
                f"Model reported passed, but its own finding said the evidence was inconclusive. {finding}",
            )

        return status, finding

    @staticmethod
    def _claim_is_negative(claim: str) -> bool:
        return NEGATIVE_CLAIM_PATTERN.search(claim) is not None

    @staticmethod
    def _finding_has_failure_cue(finding: str) -> bool:
        return any(pattern.search(finding) for pattern in FAILED_FINDING_PATTERNS)

    @staticmethod
    def _finding_has_inconclusive_cue(finding: str) -> bool:
        return any(pattern.search(finding) for pattern in INCONCLUSIVE_FINDING_PATTERNS)

    @staticmethod
    def _finding_says_action_is_needed(finding: str) -> bool:
        return any(pattern.search(finding) for pattern in ACTION_NEEDED_FINDING_PATTERNS)
