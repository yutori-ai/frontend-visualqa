# Changelog

All notable changes to this project will be documented in this file.

## [0.8.0] - 2026-04-14

### Breaking
- Renamed `N1Client` → `NavigatorClient`; `N1ClientError` → `NavigatorClientError` (backward-compat aliases kept)
- Default model changed from `n1-latest` to `n1.5-latest`
- Imports changed from `yutori.n1` to `yutori.navigator`
- Replaced `record_claim_result` tool with `json_schema` structured output; `verdict_source` values are now `json_schema` | `force_stop`
- Removed `extract_content_and_links` tool — n1.5 handles visual QA from screenshots alone

### Added
- n1.5 action handlers: `mouse_move`, `middle_click`, `mouse_down`, `mouse_up`, `hold_key`, click modifiers
- Ref-based coordinate resolution for n1.5 element references
- SDK `PageReadyChecker` integration for page-settle waits
- Expanded tool handlers (`extract_elements`, `find`, `set_element_value`, `execute_js`) — available but not default
- `tool_set` and `disable_tools` parameters on `NavigatorClient.__init__` (applied during `create()`)
- `json_schema` parameter on `NavigatorClient.create()` for structured output
- `safe_async_method_call()` utility consolidating four near-identical helpers from `claim_verifier.py` and `actions.py`

### Changed
- `NavigatorClient.create()` retry mechanism replaced with `tenacity` (same backoff behavior, battle-tested library)
- Prompt tuned for visual-first reasoning with calibrated gauge and toggle examples
- `DEFAULT_PAGE_READY_TIMEOUT_SECONDS` lowered from 3 to 2
- `yutori` SDK dependency bumped from `>=0.4.10` to `>=0.6.0`
- New dependency: `tenacity>=8.2.0`

## [0.7.0] - 2026-04-07

### Added
- `manage_browser(action="login")` for MCP-driven interactive authentication on auth-gated apps
- `url` field and validators on `ManageBrowserInput` schema
- `summary` field on `BrowserStatusResult`
- `set_browser_manager()` method on `ClaimVerifier` for safe rebinding after browser reconfiguration

### Changed
- Updated MCP server tool descriptions to enumerate valid `manage_browser` actions
- Enhanced runner with robust browser reconfiguration and rollback on failure

## [0.6.0] - 2026-04-01

### Added
- Skill-in-Claude-Code demo gif in README
- Restore `extract_content_and_links` for n1 verification
- Visual contradiction example pages and `CLAIMS.md`

### Changed
- Simplified tool execution and cleaned up schemas
- Adopted skeptic decomposition prompt for claim verification

### Fixed
- Fix duplicate tool results when extract and verdict share a turn

## [0.5.1] - 2026-03-22

### Fixed
- Preserve raw verdicts in trace events

## [0.5.0] - 2026-03-20

### Added
- Per-claim navigation hints via claims file metadata bullets
- CLI demo GIF in README

### Changed
- Prioritize `verify_visual_claims` over `take_screenshot` in skill
- Document proof artifacts in skill so agents surface screenshots
- Removed dead files and fixed license to Apache 2.0

## [0.4.0] - 2026-03-16

### Added
- `--claims-file` flag to read claims from Markdown files
- Per-claim progress output to stderr
- Markdown reporter (`--reporter markdown`)
- Annotated Markdown reports that can be fed back as input

## [0.3.23] - 2026-03-14

### Changed
- Split login and dashboard into separate persistent-session steps

## [0.3.22] - 2026-03-13

### Added
- CI workflow example (`.github/workflows/visualqa.yml`)
- Mock Yutori login page for CI testing

### Fixed
- Skip visual QA job on fork PRs

## [0.3.21] - 2026-03-12

### Fixed
- Thought card lingering too long in headed mode

## [0.3.20] - 2026-03-11

### Fixed
- Cursor glide invisible in headed mode

## [0.3.19] - 2026-03-10

### Added
- Action preview before browser action in headed mode
- Directional chevron for scroll preview effect

## [0.3.18] - 2026-03-09

### Fixed
- Overlay flash before evidence screenshots in headed mode

## [0.3.17] - 2026-03-08

### Fixed
- Verdict reconciliation regressions from PR #12
- Nav hint enforcement skipped for `not_testable` verdicts

## [0.3.14] - 2026-03-06

### Added
- Bootstrap first model turn with `extract_elements`

### Fixed
- Overlay flash on first screenshot

## [0.3.12] - 2026-03-04

### Added
- Named session keys in persistent mode (`--session-key`)
- `--run-name` flag for tagging CI output

### Fixed
- Scan bar hidden after screenshots; overlays moved to post-capture

## [0.3.11] - 2026-03-03

### Added
- Thought card with read-effect scan animation
- Rich trace events with reasoning metadata

## [0.3.10] - 2026-03-02

### Added
- Yutori replay cursor and action visualization overlay in headed mode

## [0.3.9] - 2026-03-01

### Fixed
- Screenshot quality degradation from unnecessary JPEG round-trip

### Changed
- Switch screenshot output from PNG to WebP

## [0.3.8] - 2026-02-28

### Added
- Booking form example with date picker and off-by-one bug
- `--navigation-hint` documentation and examples
- Native `<select>` known limitation documented

## [0.3.7] - 2026-02-27

### Added
- Realistic demo pages: discount bug, visual-only quota bug

### Changed
- Replaced README hero examples with realistic QA scenarios

## [0.3.0] - 2026-02-20

### Changed
- **Breaking:** Restructured `ClaimResult` into `proof`/`page`/`trace` fields
- Renamed `pass`/`fail` to `passed`/`failed`

### Added
- Run-level timing

## [0.2.0] - 2026-02-14

### Added
- Headed-mode overlay visualization for n1 actions
- `--headed` and `--visualize` flags

## [0.1.0] - 2026-02-01

### Added
- Initial release
- CLI with `verify`, `screenshot`, `login`, `status`, `serve` subcommands
- MCP server with `verify_visual_claims`, `take_screenshot`, `manage_browser` tools
- Browser mode abstraction: ephemeral and persistent
- Reporter protocol with Native and CTRF reporters
