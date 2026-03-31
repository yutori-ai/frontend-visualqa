"""Lightweight Markdown claims parser for frontend-visualqa."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from frontend_visualqa.errors import ConfigurationError


_TASK_MARKER_RE = re.compile(r"^\[(?: |x|X)\]\s*")
_FENCE_RE = re.compile(r"^(?P<fence>`{3,}|~{3,})")
_NAVIGATION_HINT_BULLET_RE = re.compile(r"^\s*[*-]\s+(.*\S.*)$")


ClaimBullet = Literal["-", "*"]


@dataclass(frozen=True)
class ParsedClaimLine:
    """Metadata for a claim line in a Markdown source file."""

    claim: str
    line_index: int
    bullet: ClaimBullet
    navigation_hint: str | None = None


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
    except UnicodeDecodeError:
        raise ConfigurationError(
            f"Claims file is not valid UTF-8: {path}. Save the file as UTF-8 and retry."
        )
    except OSError as exc:
        raise ConfigurationError(f"Could not read claims file {path}: {exc}")

    source_lines = source_content.splitlines()
    lines: list[ParsedClaimLine] = []

    index = 0
    in_fence = False
    fence_marker: str | None = None

    while index < len(source_lines):
        raw_line = source_lines[index]
        in_fence, fence_marker, consumed = _update_fence_state(raw_line.lstrip(), in_fence, fence_marker)
        if consumed:
            index += 1
            continue

        if in_fence or not raw_line:
            index += 1
            continue

        if not _is_root_bullet(raw_line):
            index += 1
            continue

        bullet = cast(ClaimBullet, raw_line[0])
        claim = _strip_task_marker(raw_line[1:].lstrip())
        if not claim:
            index += 1
            continue

        claim_line_index = index
        navigation_hint: str | None = None
        index += 1
        while index < len(source_lines):
            next_line = source_lines[index]
            next_stripped = next_line.lstrip()

            in_fence, fence_marker, consumed = _update_fence_state(next_stripped, in_fence, fence_marker)
            if consumed:
                index += 1
                continue

            if in_fence or not next_line.strip():
                index += 1
                continue

            if _is_root_bullet(next_line):
                break

            if next_stripped.startswith("<!--"):
                index += 1
                continue

            # Unindented non-bullet line (heading, prose) ends the child region.
            if next_line == next_stripped:
                break

            metadata_hint = _parse_navigation_hint(next_stripped)
            if metadata_hint is not None:
                navigation_hint = metadata_hint

            index += 1

        lines.append(
            ParsedClaimLine(
                claim=claim,
                line_index=claim_line_index,
                bullet=bullet,
                navigation_hint=navigation_hint,
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


def _is_root_bullet(line: str) -> bool:
    return bool(line) and line[0] in "-*" and (len(line) == 1 or line[1].isspace())


def _update_fence_state(
    stripped: str,
    in_fence: bool,
    fence_marker: str | None,
) -> tuple[bool, str | None, bool]:
    """Toggle fenced-code-block state. Returns (in_fence, fence_marker, consumed)."""
    fence_match = _FENCE_RE.match(stripped)
    if fence_match is None:
        return in_fence, fence_marker, False
    marker = fence_match.group("fence")
    if not in_fence:
        return True, marker, True
    if fence_marker is not None and marker[0] == fence_marker[0] and len(marker) >= len(fence_marker):
        return False, None, True
    return in_fence, fence_marker, True


def _parse_navigation_hint(stripped: str) -> str | None:
    """Extract a navigation hint from an already-lstripped child bullet line."""
    match = _NAVIGATION_HINT_BULLET_RE.match(stripped)
    if match is None:
        return None
    content = match.group(1).strip()
    if not content.startswith("navigation_hint:"):
        return None
    value = content[len("navigation_hint:") :].strip()
    return value or None
