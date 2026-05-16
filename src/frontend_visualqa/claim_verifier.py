"""Single-claim verification loop for frontend-visualqa."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from frontend_visualqa.actions import ActionExecutor
from frontend_visualqa.artifacts import ArtifactManager, RunArtifacts
from frontend_visualqa.browser import BrowserManager, BrowserSession, image_bytes_to_data_url
from frontend_visualqa.errors import BrowserActionError, NavigatorClientError
from frontend_visualqa.grounding import capture_grounding_state, ground_claim_verdict
from frontend_visualqa.hook_adapter import VisualQAHookAdapter
from frontend_visualqa.recovery import wrong_page_recovered
from frontend_visualqa.prompts import (
    VERDICT_JSON_SCHEMA,
    build_action_or_verdict_prompt,
    build_follow_navigation_hint_prompt,
    build_force_stop_prompt,
    build_take_action_prompt,
    build_verification_task,
)
from frontend_visualqa.schemas import ClaimPage, ClaimProof, ClaimResult, ClaimStatus, ClaimTrace
from frontend_visualqa.text_utils import clip_text
from frontend_visualqa.tool_arguments import parse_tool_arguments, tool_call_name
from frontend_visualqa.utils import safe_async_method_call, safe_method_call

if TYPE_CHECKING:
    from frontend_visualqa.navigator_client import NavigatorClient


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
        r"""\b(?:i\s+)?need\s+to\s+(?:click|tap|open|navigate|go|scroll|expand|select|hover|move|drag|press|hold)\b""",
        re.IGNORECASE,
    ),
    re.compile(
        r"""\b(?:i\s+)?should\s+(?:click|tap|open|navigate|go|scroll|expand|select|hover|move|drag|press|hold)\b""",
        re.IGNORECASE,
    ),
    re.compile(r"""\bbefore\s+i\s+can\s+(?:verify|determine|confirm|decide)\b""", re.IGNORECASE),
)

logger = logging.getLogger(__name__)

MAX_NON_ACTION_REPROMPTS = 2
MAX_INLINE_PROOF_TEXT_CHARS = 280
MAX_INLINE_PROOF_TEXT_LINES = 6

VERDICT_SOURCE_JSON = "json_schema"
VERDICT_SOURCE_FORCE_STOP = "force_stop"


def _user_text_message(text: str) -> dict[str, Any]:
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def _finding_matches_any(finding: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(finding) for pattern in patterns)


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
    except ImportError:
        return None
    try:
        return OverlayController(page)
    except Exception:
        logger.debug("Failed to construct overlay controller", exc_info=True)
        return None


class ClaimVerifier:
    """Run the Navigator observe-think-act loop for a single claim."""

    def __init__(
        self,
        *,
        browser_manager: BrowserManager,
        artifact_manager: ArtifactManager,
        navigator_client: NavigatorClient,
        action_executor: ActionExecutor | None = None,
        visualize: bool = False,
    ) -> None:
        self.browser_manager = browser_manager
        self.artifact_manager = artifact_manager
        self.navigator_client = navigator_client
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
                response, messages = await self._invoke_navigator_turn(messages)
                assistant_message = response.choices[0].message

                # --- Check for structured JSON verdict (parsed_json) ---
                json_verdict = self._extract_json_verdict(response)
                if json_verdict is not None:
                    reprompt_text: str | None = None
                    force_stop_finding: str | None = None
                    if (
                        not progress.has_interacted
                        and json_verdict[0] == "inconclusive"
                        and self._finding_says_action_is_needed(json_verdict[1])
                    ):
                        reprompt_text = build_take_action_prompt(claim)
                        force_stop_finding = "The model kept saying more interaction was needed without taking the next browser action."
                    elif navigation_hint and not progress.has_interacted and json_verdict[0] != "not_testable":
                        reprompt_text = build_follow_navigation_hint_prompt(claim, navigation_hint)
                        force_stop_finding = (
                            "The model tried to render a verdict before following the navigation hint."
                        )
                    if reprompt_text is not None:
                        if non_action_reprompts < MAX_NON_ACTION_REPROMPTS:
                            non_action_reprompts += 1
                            messages.append(_user_text_message(reprompt_text))
                            continue
                        result = await self._finalize_result(
                            progress=progress,
                            verdict=("inconclusive", force_stop_finding or ""),
                            verdict_source=VERDICT_SOURCE_FORCE_STOP,
                        )
                        return await self._complete_result(result)
                    result = await self._finalize_result(
                        progress=progress,
                        verdict=json_verdict,
                        verdict_source=VERDICT_SOURCE_JSON,
                    )
                    return await self._complete_result(result)

                # --- Check for tool calls (browser actions) ---
                tool_calls = list(getattr(assistant_message, "tool_calls", []) or [])
                if not tool_calls:
                    # No JSON verdict and no tool calls — reprompt
                    if non_action_reprompts < MAX_NON_ACTION_REPROMPTS:
                        non_action_reprompts += 1
                        messages.append(_user_text_message(build_action_or_verdict_prompt(claim)))
                        continue
                    break

                had_action_in_turn = False
                responded_tool_ids: set[str] = set()
                for tool_call in tool_calls:
                    tool_name = tool_call_name(tool_call)
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
                    counts_as_interaction = bool(execution.get("counts_as_interaction", True))
                    progress.url_history.append(current_url)
                    progress.step_count += 1
                    if counts_as_interaction:
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
        except (BrowserActionError, NavigatorClientError) as exc:
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

    async def _invoke_navigator_turn(
        self, messages: list[dict[str, Any]]
    ) -> tuple[Any, list[dict[str, Any]]]:
        """Trim messages, dispatch a navigator request, and append the assistant reply.

        Returns ``(response, messages)`` where ``messages`` is the trimmed list with
        the assistant reply appended.
        """
        messages = self._prepare_messages_for_request(messages)
        model_tools = self._model_tools()
        await self._safe_hook_call("on_llm_start", messages=messages, tools=model_tools)
        await self._best_effort_overlay_call("set_status", "Analyzing")
        # already_trimmed=True: _prepare_messages_for_request just trimmed.
        # Without this flag NavigatorClient.create would re-run the trim
        # (which JSON-serializes the entire message list to estimate size —
        # multi-MB on screenshot-heavy traces) on every turn.
        response = await self.navigator_client.create(
            messages,
            tools=model_tools,
            json_schema=VERDICT_JSON_SCHEMA,
            already_trimmed=True,
        )
        assistant_message = response.choices[0].message
        await self._safe_hook_call("on_llm_end", response=assistant_message)
        messages.append(self._message_to_dict(assistant_message))
        return response, messages

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
        response, _ = await self._invoke_navigator_turn(messages)

        verdict_source = VERDICT_SOURCE_JSON
        verdict = self._extract_json_verdict(response)
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
        """Invoke an optional overlay hook without interrupting verification."""

        await safe_async_method_call(self._overlay, method_name, *args, log_label="Overlay", **kwargs)

    async def _safe_hook_call(self, method_name: str, **kwargs: Any) -> None:
        """Invoke an optional lifecycle hook without interrupting verification."""

        await safe_async_method_call(self._hook, method_name, log_label="Hook", **kwargs)

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
        return self.navigator_client.trim_messages(messages)

    @staticmethod
    def _model_tools() -> list[dict[str, Any]]:
        # Navigator's built-in browser actions are injected server-side by the
        # Yutori chat completions endpoint. No custom tools needed — verdicts
        # are delivered via json_schema structured output.
        return []

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
        safe_method_call(
            self._hook,
            "record_action_event",
            log_label="Hook",
            step=step,
            action=action,
            action_args=action_args,
            output_preview=self._clip_trace_output_preview(output_text),
            screenshot_path=screenshot_path,
        )

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
    def _extract_json_verdict(response: Any) -> tuple[str, str] | None:
        """Extract a verdict from the response's ``parsed_json`` attribute.

        Returns ``(status, finding)`` when a valid structured verdict is
        present, or ``None`` when the model emitted tool calls / free text
        instead.
        """
        parsed = getattr(response, "parsed_json", None)
        if not isinstance(parsed, dict):
            return None
        status = str(parsed.get("status", "")).strip()
        finding = str(parsed.get("finding", "")).strip() or "No finding provided."
        if status in {"passed", "failed", "inconclusive", "not_testable"}:
            return status, finding
        return None

    async def _execute_tool_call(self, session: BrowserSession, tool_call: Any) -> dict[str, Any]:
        from frontend_visualqa.actions import tool_counts_as_interaction

        result = await self.action_executor.execute_tool_call(session, tool_call)
        if isinstance(result, str):
            tool_name = tool_call_name(tool_call)
            return {
                "trace": result,
                "output_text": None,
                "current_url": session.page.url,
                "counts_as_interaction": tool_counts_as_interaction(tool_name),
            }
        return {
            "trace": getattr(result, "trace", str(result)),
            "output_text": getattr(result, "output_text", None),
            "current_url": getattr(result, "current_url", None) or session.page.url,
            "counts_as_interaction": getattr(result, "counts_as_interaction", True),
        }

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
        if verdict_source is not None:
            safe_method_call(
                self._hook,
                "record_verdict_event",
                log_label="Hook",
                step=progress.step_count,
                source=verdict_source,
                raw_status=status,
                raw_finding=finding,
                status=grounded_status,
                finding=grounded_finding,
            )
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
        return _finding_matches_any(finding, FAILED_FINDING_PATTERNS)

    @staticmethod
    def _finding_has_inconclusive_cue(finding: str) -> bool:
        return _finding_matches_any(finding, INCONCLUSIVE_FINDING_PATTERNS)

    @staticmethod
    def _finding_says_action_is_needed(finding: str) -> bool:
        return _finding_matches_any(finding, ACTION_NEEDED_FINDING_PATTERNS)
