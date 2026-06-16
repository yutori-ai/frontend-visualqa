# Refactoring Backlog — frontend-visualqa

This file tracks refactoring candidates for automated runs. Checked items have
been completed; open items are sized and scoped for future runs.

## Open Items

_(none — the package is well-factored for its current size)_

## Completed Items

- [x] **Hand-rolled retry → httpx retry** — replaced manual exponential-backoff
  retry loops in the Navigator client with httpx's built-in retry transport.
  No behavioral change; removed ~30 lines of hand-rolled backoff code.

- [x] **HTTP/2 swap guard** — added `http2=True` flag plus `h2` extra install
  guard to the preflight URL check in `runner.py` for transport parity with
  the Navigator client.

## Investigation Notes

- `claim_verifier.py` (~39 KB): well-organized; `FAILED_FINDING_PATTERNS`,
  `INCONCLUSIVE_FINDING_PATTERNS`, `ACTION_NEEDED_FINDING_PATTERNS` are already
  used via the shared `_finding_matches_any` helper — no duplication.
- `actions.py` (~31 KB): long `execute_action` switch is unavoidable given the
  action surface; helper functions are well-extracted.
- `runner.py` (~33 KB): clean orchestration; `_TIMEOUT_FINDING_TEMPLATES` dict
  avoids repetition across claim/run timeout messages.
- `overlay.py` (~49 KB): largest file; not investigated in detail. A candidate
  for a future audit if new bugs surface in overlay rendering.
