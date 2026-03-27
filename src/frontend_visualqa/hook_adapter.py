"""App-local hook adapter for headed visual QA runs."""

from __future__ import annotations

from typing import Any

from yutori.n1 import RunHooksBase, extract_text_content

from frontend_visualqa.schemas import ClaimStatus, TraceEvent


class VisualQAHookAdapter(RunHooksBase):
    """Bridge generic SDK lifecycle hooks to frontend-visualqa overlays and trace events."""

    def __init__(self, overlay: Any | None) -> None:
        self._overlay = overlay
        self.events: list[TraceEvent] = []
        self._current_turn_reasoning: str | None = None

    @property
    def current_turn_reasoning(self) -> str | None:
        return self._current_turn_reasoning

    async def on_llm_end(self, *, response: Any) -> None:
        message = response.choices[0].message if hasattr(response, "choices") else response
        tool_calls = list(getattr(message, "tool_calls", []) or [])
        reasoning = extract_text_content(getattr(message, "content", None))
        self._current_turn_reasoning = reasoning if reasoning and tool_calls else None

    def record_action_event(
        self,
        *,
        step: int,
        action: str,
        action_args: dict[str, Any] | None,
        output_preview: str | None,
        screenshot_path: str | None,
    ) -> None:
        self.events.append(
            TraceEvent(
                type="action",
                step=step,
                reasoning=self._current_turn_reasoning,
                action=action,
                action_args=dict(action_args) if action_args else {},
                output_preview=output_preview,
                screenshot_path=screenshot_path,
            )
        )

    def record_verdict_event(
        self,
        *,
        step: int | None,
        source: str,
        status: ClaimStatus,
        finding: str,
    ) -> None:
        self.events.append(
            TraceEvent(
                type="verdict",
                step=step,
                reasoning=self._current_turn_reasoning,
                verdict_source=source,
                verdict_status=status,
                finding=finding,
            )
        )
