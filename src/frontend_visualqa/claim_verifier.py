"""Single-claim verification loop for frontend-visualqa."""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from frontend_visualqa.actions import BROWSER_ACTION_TOOLS, ActionExecutor
from frontend_visualqa.artifacts import ArtifactManager, RunArtifacts
from frontend_visualqa.browser import BrowserManager, BrowserSession, image_bytes_to_data_url
from frontend_visualqa.errors import BrowserActionError, N1ClientError
from frontend_visualqa.hook_adapter import VisualQAHookAdapter
from frontend_visualqa.prompts import (
    RECORD_CLAIM_RESULT_TOOL,
    build_action_or_verdict_prompt,
    build_force_stop_prompt,
    build_verification_task,
)
from frontend_visualqa.schemas import ClaimPage, ClaimProof, ClaimResult, ClaimStatus, ClaimTrace
from frontend_visualqa.tool_arguments import parse_tool_arguments

if TYPE_CHECKING:
    from frontend_visualqa.n1_client import N1Client


FALLBACK_VERDICT_PATTERN = re.compile(
    r"""["']?(?:status|verdict)["']?\s*[:=]\s*["']?(passed|failed|inconclusive|not[_ ]testable)\b""",
    re.IGNORECASE,
)
BUTTON_VISIBLE_PATTERN = re.compile(
    r"""^The\s+(?P<label>(?:(?!\b(?:is|are|was|were|has|have|does|do|should|can)\b).)+?)\s+button\s+is\s+visible(?:\s+without\s+scrolling)?\.?$""",
    re.IGNORECASE,
)
BUTTON_FULLY_VISIBLE_PATTERN = re.compile(
    r"""^The\s+(?P<label>.+?)\s+button\s+is\s+fully\s+visible(?:\s+within\s+its\s+container)?\.?$""",
    re.IGNORECASE,
)
HEADING_READS_PATTERN = re.compile(r"""^The\s+heading\s+reads\s+["'](?P<text>.+?)["']\.?$""", re.IGNORECASE)
PAGE_TITLE_READS_PATTERN = re.compile(r"""^The\s+page\s+title\s+reads\s+["'](?P<text>.+?)["']\.?$""", re.IGNORECASE)
MODAL_TITLE_READS_PATTERN = re.compile(r"""^The\s+modal\s+title\s+reads\s+["'](?P<text>.+?)["']\.?$""", re.IGNORECASE)

logger = logging.getLogger(__name__)

MAX_NON_ACTION_REPROMPTS = 2
MAX_INLINE_PROOF_TEXT_CHARS = 280
MAX_INLINE_PROOF_TEXT_LINES = 6


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

        action_trace: list[str] = []
        url_history = [session.page.url or url]
        screenshot_paths: list[str] = []
        messages: list[dict[str, Any]] = []
        step_count = 0
        non_action_reprompts = 0
        last_proof_text: str | None = None
        last_proof_text_path: str | None = None
        should_visualize = self._visualize if visualize is None else visualize
        progress = _VerificationProgress(
            claim=claim,
            session=session,
            url=url,
            run_artifacts=run_artifacts,
            claim_index=claim_index,
            step_count=step_count,
            screenshot_paths=screenshot_paths,
            action_trace=action_trace,
            url_history=url_history,
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
            screenshot_paths.append(initial_path)

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

            while step_count < max_steps:
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
                            claim=claim,
                            session=session,
                            verdict=fallback,
                            verdict_source="fallback_content",
                            step_count=step_count,
                            screenshot_paths=screenshot_paths,
                            action_trace=action_trace,
                            proof_text=last_proof_text,
                            proof_text_path=last_proof_text_path,
                            url_history=url_history,
                            url=url,
                            run_artifacts=run_artifacts,
                            claim_index=claim_index,
                        )
                        return await self._complete_result(result)
                    if non_action_reprompts < MAX_NON_ACTION_REPROMPTS:
                        non_action_reprompts += 1
                        messages.append(
                            {
                                "role": "user",
                                "content": [{"type": "text", "text": build_action_or_verdict_prompt(claim)}],
                            }
                        )
                        continue
                    break

                for tool_call in tool_calls:
                    tool_name = getattr(tool_call.function, "name", "")
                    if tool_name == "record_claim_result":
                        verdict = self._extract_structured_verdict([tool_call])
                        if verdict is not None:
                            result = await self._finalize_result(
                                claim=claim,
                                session=session,
                                verdict=verdict,
                                verdict_source="record_claim_result",
                                step_count=step_count,
                                screenshot_paths=screenshot_paths,
                                action_trace=action_trace,
                                proof_text=last_proof_text,
                                proof_text_path=last_proof_text_path,
                                url_history=url_history,
                                url=url,
                                run_artifacts=run_artifacts,
                                claim_index=claim_index,
                            )
                            return await self._complete_result(result)
                        continue
                    if tool_name == "stop":
                        stop_verdict = self._extract_stop_verdict([tool_call])
                        if stop_verdict is not None:
                            result = await self._finalize_result(
                                claim=claim,
                                session=session,
                                verdict=stop_verdict,
                                verdict_source="legacy_stop",
                                step_count=step_count,
                                screenshot_paths=screenshot_paths,
                                action_trace=action_trace,
                                proof_text=last_proof_text,
                                proof_text_path=last_proof_text_path,
                                url_history=url_history,
                                url=url,
                                run_artifacts=run_artifacts,
                                claim_index=claim_index,
                            )
                            return await self._complete_result(result)
                        continue
                    if step_count >= max_steps:
                        break
                    tool_arguments = parse_tool_arguments(tool_call)
                    await self._safe_hook_call("on_tool_start", name=tool_name, arguments=tool_arguments)
                    execution = await self._execute_tool_call(session, tool_call)
                    trace = execution["trace"]
                    action_trace.append(trace)
                    output_text = execution.get("output_text")
                    await self._safe_hook_call(
                        "on_tool_end",
                        name=tool_name,
                        arguments=tool_arguments,
                        output=output_text,
                        trace=trace,
                    )
                    pending_proof_text = str(output_text) if output_text else None
                    current_url = execution.get("current_url", session.page.url) or url
                    url_history.append(current_url)
                    step_count += 1
                    progress.step_count = step_count
                    non_action_reprompts = 0
                    screenshot_bytes, screenshot_path = await self._capture_evidence_screenshot(
                        session=session,
                        run_artifacts=run_artifacts,
                        claim_index=claim_index,
                        label=f"step-{step_count:02d}",
                    )
                    screenshot_paths.append(screenshot_path)
                    self._record_action_event(
                        step=step_count,
                        action=tool_name,
                        action_args=tool_arguments,
                        output_text=output_text,
                        screenshot_path=screenshot_path,
                    )
                    last_proof_text = pending_proof_text
                    last_proof_text_path = self._save_proof_text(
                        run_artifacts=run_artifacts,
                        claim_index=claim_index,
                        label=f"step-{step_count:02d}",
                        proof_text=pending_proof_text,
                    )
                    progress.proof_text = last_proof_text
                    progress.proof_text_path = last_proof_text_path
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

                # Show thought card and scan bar once per turn, after all
                # evidence is captured — they play while the next LLM call
                # is in flight.  Placed outside the tool loop so multi-tool
                # turns show the overlays exactly once.
                reasoning = self._hook.current_turn_reasoning if self._hook else None
                if reasoning and self._overlay:
                    await self._best_effort_overlay_call("show_thought", reasoning)
            result = await self._force_stop(
                claim=claim,
                session=session,
                messages=messages,
                step_count=step_count,
                screenshot_paths=screenshot_paths,
                action_trace=action_trace,
                proof_text=last_proof_text,
                proof_text_path=last_proof_text_path,
                url_history=url_history,
                url=url,
                run_artifacts=run_artifacts,
                claim_index=claim_index,
            )
            return await self._complete_result(result)
        except asyncio.CancelledError:
            # on_agent_end is intentionally skipped here: the task is being
            # torn down externally, and the async hook cannot be awaited
            # reliably during cancellation.  Events are still preserved via
            # _partial_progress and collected by consume_partial_result.
            preserve_partial_progress = True
            raise
        except (BrowserActionError, N1ClientError) as exc:
            result = self._build_result(
                claim=claim,
                session=session,
                status="not_testable",
                finding=str(exc),
                step_count=step_count,
                screenshot_paths=screenshot_paths,
                action_trace=action_trace,
                proof_text=last_proof_text,
                proof_text_path=last_proof_text_path,
                url_history=url_history,
                url=url,
                run_artifacts=run_artifacts,
                claim_index=claim_index,
            )
            return await self._complete_result(result)
        except Exception as exc:
            logger.warning("Unexpected verifier failure for claim %r", claim, exc_info=True)
            result = self._build_result(
                claim=claim,
                session=session,
                status="inconclusive",
                finding=f"Verification failed unexpectedly before a verdict was recorded: {exc}",
                step_count=step_count,
                screenshot_paths=screenshot_paths,
                action_trace=action_trace,
                proof_text=last_proof_text,
                proof_text_path=last_proof_text_path,
                url_history=url_history,
                url=url,
                run_artifacts=run_artifacts,
                claim_index=claim_index,
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
        claim: str,
        session: BrowserSession,
        messages: list[dict[str, Any]],
        step_count: int,
        screenshot_paths: list[str],
        action_trace: list[str],
        proof_text: str | None,
        proof_text_path: str | None,
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
        model_tools = self._model_tools()
        await self._safe_hook_call("on_llm_start", messages=messages, tools=model_tools)
        await self._best_effort_overlay_call("set_status", "Analyzing")
        response = await self.n1_client.create(messages, tools=model_tools)
        assistant_message = self._coerce_assistant_message(response)
        await self._safe_hook_call("on_llm_end", response=assistant_message)
        messages.append(self._message_to_dict(assistant_message))

        verdict_source = "record_claim_result"
        verdict = self._extract_structured_verdict(getattr(assistant_message, "tool_calls", []) or [])
        if verdict is None:
            verdict_source = "legacy_stop"
            verdict = self._extract_stop_verdict(getattr(assistant_message, "tool_calls", []) or [])
        if verdict is None:
            verdict_source = "force_stop"
            verdict = self._parse_fallback_verdict(getattr(assistant_message, "content", None))
        if verdict is None:
            verdict_source = "force_stop"
            verdict = ("inconclusive", "The model did not provide a structured verdict before the step limit.")

        return await self._finalize_result(
            claim=claim,
            session=session,
            verdict=verdict,
            verdict_source=verdict_source,
            step_count=step_count,
            screenshot_paths=screenshot_paths,
            action_trace=action_trace,
            proof_text=proof_text,
            proof_text_path=proof_text_path,
            url_history=url_history,
            url=url,
            run_artifacts=run_artifacts,
            claim_index=claim_index,
        )

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
        claim: str,
        session: BrowserSession,
        status: ClaimStatus,
        finding: str,
        step_count: int,
        screenshot_paths: list[str],
        action_trace: list[str],
        proof_text: str | None,
        proof_text_path: str | None,
        url_history: list[str],
        url: str,
        run_artifacts: RunArtifacts,
        claim_index: int,
    ) -> ClaimResult:
        events = list(self._hook.events) if self._hook is not None else []
        try:
            trace_path = self.artifact_manager.save_rich_trace(
                run_artifacts,
                claim_index,
                [event.model_dump(mode="json") for event in events],
            )
        except Exception:
            trace_path = None
        proof = None
        if screenshot_paths:
            proof_step = max(len(screenshot_paths) - 1, 0)
            proof = ClaimProof(
                screenshot_path=screenshot_paths[-1],
                step=proof_step,
                after_action=action_trace[proof_step - 1] if proof_step > 0 and len(action_trace) >= proof_step else None,
                text=self._build_inline_proof_text(proof_text),
                text_path=proof_text_path,
            )
        return ClaimResult(
            claim=claim,
            status=status,
            finding=finding,
            proof=proof,
            page=ClaimPage(url=session.page.url or url, viewport=session.viewport),
            trace=ClaimTrace(
                steps_taken=step_count,
                wrong_page_recovered=self._wrong_page_recovered(url_history, url, action_trace),
                screenshot_paths=screenshot_paths,
                actions=action_trace,
                events=events,
                trace_path=trace_path,
            ),
        )

    def consume_partial_result(self, *, status: ClaimStatus, finding: str) -> ClaimResult | None:
        progress = self._partial_progress
        self._partial_progress = None
        if progress is None:
            return None
        result = self._build_result(
            claim=progress.claim,
            session=progress.session,
            status=status,
            finding=finding,
            step_count=progress.step_count,
            screenshot_paths=progress.screenshot_paths,
            action_trace=progress.action_trace,
            proof_text=progress.proof_text,
            proof_text_path=progress.proof_text_path,
            url_history=progress.url_history,
            url=progress.url,
            run_artifacts=progress.run_artifacts,
            claim_index=progress.claim_index,
        )
        self._hook = None
        return result

    def _prepare_messages_for_request(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        trim_messages = getattr(self.n1_client, "trim_messages", None)
        if callable(trim_messages):
            return trim_messages(messages)
        return messages

    @staticmethod
    def _model_tools() -> list[dict[str, Any]]:
        return [*BROWSER_ACTION_TOOLS, RECORD_CLAIM_RESULT_TOOL]

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
        normalized = " ".join(output_text.split())
        if not normalized:
            return None
        if len(normalized) <= 280:
            return normalized
        return normalized[:279].rstrip() + "…"

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

        arguments = parse_tool_arguments(tool_call)
        trace = await self.action_executor.execute_action(session, tool_call.function.name, arguments)
        return {"trace": trace, "output_text": None, "current_url": session.page.url}

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

    @staticmethod
    def _wrong_page_recovered(url_history: list[str], target_url: str, action_trace: list[str] | None = None) -> bool:
        del action_trace
        if not url_history:
            return False

        starting_url = url_history[0]
        if starting_url != target_url:
            return any(current_url == target_url for current_url in url_history[1:])

        return any(current_url != target_url for current_url in url_history[1:])

    async def _finalize_result(
        self,
        *,
        claim: str,
        session: BrowserSession,
        verdict: tuple[ClaimStatus, str],
        verdict_source: str | None = None,
        step_count: int,
        screenshot_paths: list[str],
        action_trace: list[str],
        proof_text: str | None,
        proof_text_path: str | None,
        url_history: list[str],
        url: str,
        run_artifacts: RunArtifacts,
        claim_index: int,
    ) -> ClaimResult:
        status, finding = verdict
        grounded_status, grounded_finding = await self._ground_verdict(
            session=session,
            claim=claim,
            status=status,
            finding=finding,
        )
        hook = self._hook
        if verdict_source is not None and hook is not None:
            try:
                hook.record_verdict_event(
                    step=step_count,
                    source=verdict_source,
                    status=grounded_status,
                    finding=grounded_finding,
                )
            except Exception:
                logger.debug("record_verdict_event failed", exc_info=True)
        return self._build_result(
            claim=claim,
            session=session,
            status=grounded_status,
            finding=grounded_finding,
            step_count=step_count,
            screenshot_paths=screenshot_paths,
            action_trace=action_trace,
            proof_text=proof_text,
            proof_text_path=proof_text_path,
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
        finding: str,
    ) -> tuple[ClaimStatus, str]:
        if status == "not_testable":
            return status, finding

        try:
            visual_state = await self._capture_visible_text_state(session)
        except Exception:
            logger.warning("Failed to gather DOM grounding hints for pass verdict", exc_info=True)
            return status, finding

        normalized_claim = self._normalize_text(claim)
        for pattern, checker in (
            (BUTTON_FULLY_VISIBLE_PATTERN, self._check_button_fully_visible),
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
                return status, finding
            grounded_status, grounded_finding = grounded
            if grounded_status != "passed":
                logger.info("Downgrading pass verdict for claim %r after grounding check", claim)
            return grounded_status, grounded_finding

        if any(marker in normalized_claim for marker in {" title reads ", " heading reads ", " button is visible"}):
            logger.info("No grounding rule matched pass verdict for claim %r", claim)
        return status, finding

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
                const buttonStates = Array.from(
                    document.querySelectorAll("button, [role='button'], input[type='button'], input[type='submit']")
                )
                    .filter(isVisible)
                    .map((element) => {
                        const text = elementText(element);
                        if (!text) return null;
                        const rect = element.getBoundingClientRect();
                        let fullyVisible =
                            rect.top >= 0 &&
                            rect.left >= 0 &&
                            rect.bottom <= window.innerHeight &&
                            rect.right <= window.innerWidth;
                        for (let ancestor = element.parentElement; ancestor && fullyVisible; ancestor = ancestor.parentElement) {
                            const style = window.getComputedStyle(ancestor);
                            const clips =
                                ["hidden", "clip", "scroll", "auto"].includes(style.overflow) ||
                                ["hidden", "clip", "scroll", "auto"].includes(style.overflowX) ||
                                ["hidden", "clip", "scroll", "auto"].includes(style.overflowY);
                            if (!clips) continue;
                            const ancestorRect = ancestor.getBoundingClientRect();
                            if (
                                rect.top < ancestorRect.top ||
                                rect.left < ancestorRect.left ||
                                rect.bottom > ancestorRect.bottom ||
                                rect.right > ancestorRect.right
                            ) {
                                fullyVisible = false;
                            }
                        }
                        return { text, fullyVisible };
                    })
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
                return { visibleHeadings, visibleButtons, buttonStates, dialogTitles };
            }"""
        )

    @staticmethod
    def _normalize_text(value: str) -> str:
        return " ".join(value.split()).strip().casefold()

    @staticmethod
    def _normalize_label_for_match(value: str) -> str:
        """Normalize a label for fuzzy button matching.

        Strips surrounding quotes, common decorative characters (▼▶✕×…),
        and trailing descriptor words like 'dropdown', 'menu', 'icon'.
        """
        text = " ".join(value.split()).strip().casefold()
        # Remove all quote characters (surrounding and embedded)
        for q in ("'", '"', "\u2018", "\u2019", "\u201c", "\u201d"):
            text = text.replace(q, "")
        # Remove common trailing descriptors that don't appear in button text
        for suffix in (" dropdown", " menu", " icon", " button"):
            if text.endswith(suffix):
                text = text[: -len(suffix)]
        # Category "S" catches Symbol chars (▼▶▾▸◀◂✕×); the explicit set also
        # includes punctuation quote-marks (›‹«») that fall under Pi/Pf.
        text = "".join(
            ch for ch in text if unicodedata.category(ch)[0] not in ("S",) and ch not in "▼▶▾▸◀◂✕×›‹«»"
        )
        return " ".join(text.split()).strip()

    def _check_heading_match(
        self,
        visual_state: dict[str, list[str]],
        groups: dict[str, str],
    ) -> tuple[ClaimStatus, str] | None:
        expected = self._normalize_text(groups["text"])
        visible_headings = visual_state.get("visibleHeadings", [])
        if any(self._normalize_text(text) == expected for text in visible_headings):
            return "passed", f"Visible heading matched {groups['text']!r}."
        return (
            "failed",
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
            return "passed", f"Visible dialog title matched {groups['text']!r}."
        return (
            "failed",
            f"No visible dialog title matched {groups['text']!r}. Visible dialog titles: {dialog_titles or ['<none>']}.",
        )

    def _check_button_match(
        self,
        visual_state: dict[str, list[str]],
        groups: dict[str, str],
    ) -> tuple[ClaimStatus, str] | None:
        matched_states = self._matching_button_states(visual_state, groups["label"])
        if matched_states:
            if any(state.get("fullyVisible", False) for state in matched_states):
                candidate = matched_states[0].get("text", groups["label"])
                return "passed", f"Visible button label matched {groups['label']!r}: {candidate!r}."
            candidate = matched_states[0].get("text", groups["label"])
            return (
                "failed",
                f"Visible button label matched {groups['label']!r}, but {candidate!r} is clipped or only partially visible.",
            )

        label = self._normalize_text(groups["label"])
        fuzzy_label = self._normalize_label_for_match(groups["label"])
        visible_buttons = visual_state.get("visibleButtons", [])
        for candidate in visible_buttons:
            normalized_candidate = self._normalize_text(candidate)
            fuzzy_candidate = self._normalize_label_for_match(candidate)
            if (
                normalized_candidate == label
                or normalized_candidate.startswith(f"{label} ")
                or (fuzzy_label and fuzzy_candidate == fuzzy_label)
                or (fuzzy_label and fuzzy_candidate.startswith(f"{fuzzy_label} "))
            ):
                return "passed", f"Visible button label matched {groups['label']!r}: {candidate!r}."
        return (
            "failed",
            f"No visible button label matched {groups['label']!r}. Visible buttons: {visible_buttons or ['<none>']}.",
        )

    def _check_button_fully_visible(
        self,
        visual_state: dict[str, list[str]],
        groups: dict[str, str],
    ) -> tuple[ClaimStatus, str] | None:
        matched_states = self._matching_button_states(visual_state, groups["label"])
        if not matched_states:
            visible_buttons = visual_state.get("visibleButtons", [])
            return (
                "failed",
                f"No visible button label matched {groups['label']!r}. Visible buttons: {visible_buttons or ['<none>']}.",
            )

        if any(state.get("fullyVisible", False) for state in matched_states):
            candidate = next(
                state.get("text", groups["label"]) for state in matched_states if state.get("fullyVisible", False)
            )
            return "passed", f"Visible button label matched {groups['label']!r} and is fully visible: {candidate!r}."

        candidate = matched_states[0].get("text", groups["label"])
        return (
            "failed",
            f"Visible button label matched {groups['label']!r}, but {candidate!r} is clipped or not fully visible.",
        )

    def _matching_button_states(self, visual_state: dict[str, list[str]], label: str) -> list[dict[str, Any]]:
        normalized_label = self._normalize_text(label)
        fuzzy_label = self._normalize_label_for_match(label)
        matched_states: list[dict[str, Any]] = []
        for state in visual_state.get("buttonStates", []):
            text = str(state.get("text", ""))
            normalized_candidate = self._normalize_text(text)
            fuzzy_candidate = self._normalize_label_for_match(text)
            if (
                normalized_candidate == normalized_label
                or normalized_candidate.startswith(f"{normalized_label} ")
                or (fuzzy_label and fuzzy_candidate == fuzzy_label)
                or (fuzzy_label and fuzzy_candidate.startswith(f"{fuzzy_label} "))
            ):
                matched_states.append(state)
        return matched_states
