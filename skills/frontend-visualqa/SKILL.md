---
name: frontend-visualqa
description: Verify local frontend changes with visual claims using the shared frontend-visualqa CLI or MCP server.
metadata:
  short-description: Visual QA loop for local frontend work
---

# Frontend Visual QA

Use `frontend-visualqa` when code changes need pixel-level verification rather than DOM-only assertions.

## Setup

The frontend must already be running locally. This tool checks the page, but it does not boot the dev server.

On a fresh clone, install the Playwright browser binary once before using the CLI or MCP server:

```bash
uv sync
uv run playwright install chromium
```

Claude Code MCP registration:

```bash
claude mcp add --scope user frontend-visualqa -- \
  uvx --from /absolute/path/to/frontend-visualqa frontend-visualqa serve
```

Codex MCP registration:

```bash
codex mcp add frontend-visualqa -- \
  uvx --from /absolute/path/to/frontend-visualqa frontend-visualqa serve
```

Direct local CLI usage:

```bash
uv run frontend-visualqa screenshot http://localhost:3000
uv run frontend-visualqa verify http://localhost:3000 --claims "The header shows the signed-in user's email"
uv run frontend-visualqa status
```

Separate CLI invocations do not share browser state. Session reuse and `manage_browser` are most useful through the long-running `frontend-visualqa serve` MCP process, where one tool call can reuse the same browser session as the next.

## Claim writing rules

Use claims that can be proven from the final screenshot.

Good claims:

- `The modal title reads "Invite teammate"`
- `The Publish button is disabled`
- `The selected tab has a blue underline`
- `At 375px width, the sidebar is replaced by a menu button`

Bad claims:

- `The page looks good`
- `The design feels modern`
- `The layout is cleaner now`
- `The modal works`

If the page needs interaction before a claim becomes true or false, include a `navigation_hint`.

Example:

- Claim: `The delete confirmation dialog is visible`
- Navigation hint: `Open the kebab menu in the first row, click Delete, then judge the claim.`

## Recommended process

1. Confirm the local URL and viewport you need.
2. If page state is unclear, call `take_screenshot` first.
3. Run `verify_visual_claims` with a small batch of explicit claims.
4. Read each result status and summary, then inspect the saved screenshots.
5. Fix the frontend and rerun the same claims until they pass.

## Viewports

Use explicit viewports when responsive behavior matters.

- Desktop: `1280x800`
- Tablet: `768x1024`
- Mobile: `375x812`

## Status meanings

- `pass`: visual evidence matched the claim
- `fail`: visual evidence contradicted the claim
- `inconclusive`: the runner tried but could not make a reliable determination
- `not_testable`: the environment blocked verification, usually because the page was unreachable, crashed, or required auth

## Grounding note

The primary evaluator is still the vision loop. The runner may use lightweight DOM-derived text visibility checks only as a defensive guardrail to downgrade suspicious `pass` verdicts for simple textual claims like headings, modal titles, and button labels. It is not intended to replace the visual evaluation or turn the tool into a DOM-first assertion system.

## When to prefer screenshot first

Start with `take_screenshot` when:

- you are not sure the app is on the expected route
- the page may still be broken enough that writing claims would be premature
- you need to confirm whether a prior interaction already changed the browser session

$ARGUMENTS
