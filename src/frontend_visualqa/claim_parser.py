"""Lightweight Markdown claims parser for frontend-visualqa."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from frontend_visualqa.errors import ConfigurationError


_TASK_MARKER_RE = re.compile(r"^\[(?: |x|X)\]\s*")
_FENCE_RE = re.compile(r"^(?P<fence>`{3,}|~{3,})")


@dataclass(frozen=True)
class ParsedClaimLine:
    """Metadata for a claim line in a Markdown source file."""

    claim: str
    line_index: int
    bullet: Literal["-", "*"]


@dataclass(frozen=True)
class ParsedClaimsFile:
    """Parsed claims plus the original source content."""

    lines: tuple[ParsedClaimLine, ...]
    source_path: Path
    source_content: str

    @property
    def claims(self) -> list[str]:
        return [line.claim for line in self.lines]


def parse_claims_file(path: Path) -> ParsedClaimsFile:
    """Parse root-level Markdown bullet claims from *path*."""

    try:
        source_content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigurationError(f"Claims file does not exist: {path}")
    except OSError as exc:
        raise ConfigurationError(f"Could not read claims file {path}: {exc}")

    lines: list[ParsedClaimLine] = []

    in_fence = False
    fence_marker: str | None = None

    for line_index, raw_line in enumerate(source_content.splitlines()):
        stripped = raw_line.lstrip()

        fence_match = _FENCE_RE.match(stripped)
        if fence_match:
            marker = fence_match.group("fence")
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif fence_marker is not None and marker[0] == fence_marker[0] and len(marker) >= len(fence_marker):
                in_fence = False
                fence_marker = None
            continue

        if in_fence or not raw_line:
            continue

        if raw_line[0] not in "-*":
            continue

        bullet = raw_line[0]
        if len(raw_line) == 1 or not raw_line[1].isspace():
            continue

        claim = _strip_task_marker(raw_line[1:].lstrip())
        if not claim:
            continue

        lines.append(
            ParsedClaimLine(
                claim=claim,
                line_index=line_index,
                bullet=bullet,  # type: ignore[arg-type]  # validated by raw_line[0] check above
            )
        )

    if not lines:
        raise ConfigurationError(f"No claims were found in Markdown file: {path}")

    return ParsedClaimsFile(
        lines=tuple(lines),
        source_path=path,
        source_content=source_content,
    )


def _strip_task_marker(text: str) -> str:
    stripped = text.strip()
    match = _TASK_MARKER_RE.match(stripped)
    if match is None:
        return stripped
    return stripped[match.end() :].strip()
