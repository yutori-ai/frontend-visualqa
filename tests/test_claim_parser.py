from __future__ import annotations

from pathlib import Path

import pytest

from frontend_visualqa.errors import ConfigurationError


def _import_claim_parser_module():
    import importlib

    try:
        return importlib.import_module("frontend_visualqa.claim_parser")
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("frontend_visualqa"):
            pytest.skip("frontend_visualqa.claim_parser is not implemented yet")
        raise


def test_parse_claims_file_extracts_root_level_bullets_and_strips_task_markers(tmp_path: Path) -> None:
    module = _import_claim_parser_module()
    claims_file = tmp_path / "claims.md"
    claims_file.write_text(
        """# Dashboard checks

The parser should ignore prose.

- [ ] The page title reads "Dashboard"
* [x] The status badge is visible

    - Nested bullets are ignored

```md
- This bullet is inside a fenced code block and must be ignored
```
""",
        encoding="utf-8",
    )

    parsed = module.parse_claims_file(claims_file)

    assert parsed.source_path == claims_file
    assert parsed.source_content.startswith("# Dashboard checks")
    assert parsed.claims == ['The page title reads "Dashboard"', "The status badge is visible"]
    assert [line.line_index for line in parsed.lines] == [4, 5]
    assert [line.bullet for line in parsed.lines] == ["-", "*"]
    assert [line.claim for line in parsed.lines] == parsed.claims


def test_parse_claims_file_returns_tuple_of_lines(tmp_path: Path) -> None:
    module = _import_claim_parser_module()
    claims_file = tmp_path / "claims.md"
    claims_file.write_text("- Claim one\n- Claim two\n", encoding="utf-8")

    parsed = module.parse_claims_file(claims_file)

    assert isinstance(parsed.lines, tuple)


def test_parse_claims_file_preserves_duplicate_claim_positions(tmp_path: Path) -> None:
    module = _import_claim_parser_module()
    claims_file = tmp_path / "duplicate-claims.md"
    claims_file.write_text(
        """- The same claim appears twice
- The same claim appears twice
""",
        encoding="utf-8",
    )

    parsed = module.parse_claims_file(claims_file)

    assert parsed.claims == ["The same claim appears twice", "The same claim appears twice"]
    assert [line.line_index for line in parsed.lines] == [0, 1]


def test_parse_claims_file_rejects_missing_file(tmp_path: Path) -> None:
    module = _import_claim_parser_module()
    missing = tmp_path / "missing.md"

    with pytest.raises(ConfigurationError, match="Claims file does not exist"):
        module.parse_claims_file(missing)


def test_parse_claims_file_rejects_files_without_claims(tmp_path: Path) -> None:
    module = _import_claim_parser_module()
    claims_file = tmp_path / "empty.md"
    claims_file.write_text(
        """# Notes

Nothing to see here.

```md
- still ignored
```
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="No claims were found"):
        module.parse_claims_file(claims_file)


def test_parse_claims_file_handles_crlf_line_endings(tmp_path: Path) -> None:
    module = _import_claim_parser_module()
    claims_file = tmp_path / "crlf.md"
    claims_file.write_bytes(b"# Header\r\n\r\n- First claim\r\n- Second claim\r\n")

    parsed = module.parse_claims_file(claims_file)

    assert parsed.claims == ["First claim", "Second claim"]
    assert all("\r" not in c for c in parsed.claims)


def test_parse_claims_file_skips_empty_and_whitespace_only_bullets(tmp_path: Path) -> None:
    module = _import_claim_parser_module()
    claims_file = tmp_path / "empty-bullets.md"
    claims_file.write_text("- \n-    \n- [x] \n- Actual claim\n", encoding="utf-8")

    parsed = module.parse_claims_file(claims_file)

    assert parsed.claims == ["Actual claim"]


def test_parse_claims_file_skips_tilde_fenced_code_blocks(tmp_path: Path) -> None:
    module = _import_claim_parser_module()
    claims_file = tmp_path / "tilde.md"
    claims_file.write_text(
        """- Real claim

~~~
- Inside tilde fence
~~~

- Another real claim
""",
        encoding="utf-8",
    )

    parsed = module.parse_claims_file(claims_file)

    assert parsed.claims == ["Real claim", "Another real claim"]
