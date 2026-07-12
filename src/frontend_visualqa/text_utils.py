"""Shared text helpers used across modules."""

from __future__ import annotations


def collapse_whitespace(text: str) -> str:
    """Normalize runs of whitespace into single spaces."""
    return " ".join(text.split())


def _truncate_with_ellipsis(normalized: str, limit: int, ellipsis: str) -> str:
    """Truncate an already-normalized string to *limit* characters, appending *ellipsis* if clipped."""
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(limit - len(ellipsis), 0)].rstrip() + ellipsis


def clip_text(text: str, limit: int, *, ellipsis: str = "...") -> str:
    """Collapse whitespace then truncate to *limit* characters with an ellipsis suffix."""
    return _truncate_with_ellipsis(collapse_whitespace(text), limit, ellipsis)


def clip_text_preserving_lines(text: str, limit: int, *, ellipsis: str = "...") -> str:
    """Truncate to *limit* characters with an ellipsis, keeping line structure intact.

    Unlike :func:`clip_text`, newlines and indentation survive — needed when the
    clipped text is rendered as markdown, where headers, lists, and fenced code
    blocks are all anchored on line starts.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return _truncate_with_ellipsis(normalized, limit, ellipsis)
