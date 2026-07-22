from __future__ import annotations

from pathlib import Path

import pytest

from frontend_visualqa.errors import ConfigurationError
from fakes import import_or_skip


def _import_claim_parser_module():
    return import_or_skip("frontend_visualqa.claim_parser")


def _parsed_claims(tmp_path: Path, filename: str, content: str | bytes):
    """Write *content* to ``tmp_path / filename`` and return its ``parse_claims_file`` result.

    12 of the 14 tests in this module each repeated the same arrange block: import the module,
    build the claims-file path, write its content (as text or, for the encoding/CRLF tests, raw
    bytes), then call ``parse_claims_file``. This is the shared helper they delegate to now.
    """
    module = _import_claim_parser_module()
    claims_file = tmp_path / filename
    if isinstance(content, bytes):
        claims_file.write_bytes(content)
    else:
        claims_file.write_text(content, encoding="utf-8")
    return module.parse_claims_file(claims_file)


def test_parse_claims_file_extracts_root_level_bullets_and_strips_task_markers(tmp_path: Path) -> None:
    parsed = _parsed_claims(
        tmp_path,
        "claims.md",
        """# Dashboard checks

The parser should ignore prose.

- [ ] The page title reads "Dashboard"
* [x] The status badge is visible

    - Nested bullets are ignored

```md
- This bullet is inside a fenced code block and must be ignored
```
""",
    )

    assert parsed.source_path == tmp_path / "claims.md"
    assert parsed.source_content.startswith("# Dashboard checks")
    assert parsed.claims == ['The page title reads "Dashboard"', "The status badge is visible"]
    assert [line.line_index for line in parsed.lines] == [4, 5]
    assert [line.bullet for line in parsed.lines] == ["-", "*"]
    assert [line.claim for line in parsed.lines] == parsed.claims
    assert [line.navigation_hint for line in parsed.lines] == [None, None]


def test_parse_claims_file_attaches_nested_navigation_hint_metadata(tmp_path: Path) -> None:
    parsed = _parsed_claims(
        tmp_path,
        "claims.md",
        """# Dashboard checks

- After logging in, the dashboard shows "Welcome back, Developer"
  - navigation_hint: Type "test@yutori.com" in the email field, type "password123" in the password field, then click Continue.

- The API Calls Today stat card shows the value 1,247
""",
    )

    assert parsed.claims == [
        'After logging in, the dashboard shows "Welcome back, Developer"',
        "The API Calls Today stat card shows the value 1,247",
    ]
    assert parsed.lines[0].navigation_hint == (
        'Type "test@yutori.com" in the email field, type "password123" in the password field, then click Continue.'
    )
    assert parsed.lines[1].navigation_hint is None


def test_parse_claims_file_attaches_navigation_hint_to_second_claim(tmp_path: Path) -> None:
    parsed = _parsed_claims(
        tmp_path,
        "claims.md",
        """# Dashboard checks

- The API Calls Today stat card shows the value 1,247
- The Monthly Quota progress bar fill matches the percentage shown in the label
  - navigation_hint: Scroll to the quota card before judging.
""",
    )

    assert parsed.claims == [
        "The API Calls Today stat card shows the value 1,247",
        "The Monthly Quota progress bar fill matches the percentage shown in the label",
    ]
    assert parsed.lines[0].navigation_hint is None
    assert parsed.lines[1].navigation_hint == "Scroll to the quota card before judging."


def test_parse_claims_file_skips_reporter_generated_details_before_navigation_hint(tmp_path: Path) -> None:
    parsed = _parsed_claims(
        tmp_path,
        "report.md",
        """# Dashboard checks

- [ ] After logging in, the dashboard shows "Welcome back, Developer"
<!-- frontend-visualqa:claim-details:start -->
  Status: failed
  Finding: The user is still on the login screen.
<!-- frontend-visualqa:claim-details:end -->
  - navigation_hint: Type "test@yutori.com" in the email field, type "password123" in the password field, then click Continue.

- The API Calls Today stat card shows the value 1,247
""",
    )

    assert parsed.claims == [
        'After logging in, the dashboard shows "Welcome back, Developer"',
        "The API Calls Today stat card shows the value 1,247",
    ]
    assert parsed.lines[0].navigation_hint == (
        'Type "test@yutori.com" in the email field, type "password123" in the password field, then click Continue.'
    )


def test_parse_claims_file_returns_tuple_of_lines(tmp_path: Path) -> None:
    parsed = _parsed_claims(tmp_path, "claims.md", "- Claim one\n- Claim two\n")

    assert isinstance(parsed.lines, tuple)


def test_parse_claims_file_preserves_duplicate_claim_positions(tmp_path: Path) -> None:
    parsed = _parsed_claims(
        tmp_path,
        "duplicate-claims.md",
        """- The same claim appears twice
- The same claim appears twice
""",
    )

    assert parsed.claims == ["The same claim appears twice", "The same claim appears twice"]
    assert [line.line_index for line in parsed.lines] == [0, 1]


def test_parse_claims_file_rejects_missing_file(tmp_path: Path) -> None:
    module = _import_claim_parser_module()
    missing = tmp_path / "missing.md"

    with pytest.raises(ConfigurationError, match="Claims file does not exist"):
        module.parse_claims_file(missing)


def test_parse_claims_file_rejects_files_without_claims(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="No claims were found"):
        _parsed_claims(
            tmp_path,
            "empty.md",
            """# Notes

Nothing to see here.

```md
- still ignored
```
""",
        )


def test_parse_claims_file_rejects_non_utf8_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not valid UTF-8"):
        _parsed_claims(tmp_path, "latin1.md", b"- Caf\xe9 claim\n")


def test_parse_claims_file_handles_crlf_line_endings(tmp_path: Path) -> None:
    parsed = _parsed_claims(tmp_path, "crlf.md", b"# Header\r\n\r\n- First claim\r\n- Second claim\r\n")

    assert parsed.claims == ["First claim", "Second claim"]
    assert all("\r" not in c for c in parsed.claims)


def test_parse_claims_file_skips_empty_and_whitespace_only_bullets(tmp_path: Path) -> None:
    parsed = _parsed_claims(tmp_path, "empty-bullets.md", "- \n-    \n- [x] \n- Actual claim\n")

    assert parsed.claims == ["Actual claim"]


def test_parse_claims_file_skips_tilde_fenced_code_blocks(tmp_path: Path) -> None:
    parsed = _parsed_claims(
        tmp_path,
        "tilde.md",
        """- Real claim

~~~
- Inside tilde fence
~~~

- Another real claim
""",
    )

    assert parsed.claims == ["Real claim", "Another real claim"]
