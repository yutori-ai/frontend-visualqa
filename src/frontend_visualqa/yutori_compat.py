"""Compatibility helpers for SDK symbols not yet present in released yutori builds."""

from __future__ import annotations

from typing import Any

try:
    from yutori.n1 import RunHooksBase, extract_text_content
except (ImportError, AttributeError):
    def extract_text_content(content: Any) -> str | None:
        if content is None:
            return None
        if isinstance(content, str):
            return content.strip() or None
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text = block.get("text", "")
                        if isinstance(text, str):
                            parts.append(text)
                elif getattr(block, "type", None) == "text":
                    text = getattr(block, "text", "")
                    parts.append(text if isinstance(text, str) else str(text))
            normalized = "\n".join(part for part in parts if part).strip()
            return normalized or None

        text_attr = getattr(content, "text", None)
        if isinstance(text_attr, str):
            return text_attr.strip() or None
        return str(content).strip() or None


    class RunHooksBase:
        """Agents-inspired lifecycle hooks for chat-completions-based n1 loops.

        This is intentionally not a drop-in replacement for the OpenAI Agents SDK
        RunHooksBase. It mirrors the lifecycle phases, not the exact signatures.
        """

        async def on_agent_start(self, *, messages: list[dict[str, Any]]) -> None:
            pass

        async def on_llm_start(
            self,
            *,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]] | None = None,
        ) -> None:
            pass

        async def on_llm_end(self, *, response: Any) -> None:
            pass

        async def on_tool_start(self, *, name: str, arguments: dict[str, Any]) -> None:
            pass

        async def on_tool_end(
            self,
            *,
            name: str,
            arguments: dict[str, Any],
            output: str | None,
            trace: str,
        ) -> None:
            pass

        async def on_agent_end(self, *, output: Any | None = None) -> None:
            pass
