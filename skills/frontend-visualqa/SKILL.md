---
name: frontend-visualqa
description: Verify a running local frontend with explicit visual claims, viewports, and screenshot evidence using the frontend-visualqa MCP server or CLI.
argument-hint: "[url] [claims or QA task]"
metadata:
  short-description: Claim-based visual QA for local frontend work
---

# Frontend Visual QA

Use this skill after changing UI code when DOM assertions are not enough and you need screenshot-backed proof of what the page actually rendered.

Use it for:

- confirming the agent is on the right route or UI state
- checking visibility, clipping, overflow, z-index, truncation, and responsive behavior
- verifying interactive states such as modals, menus, toasts, tabs, and disabled buttons

Do not use it for:

- starting the dev server
- vague requests like "make it look better"
- broad visual diffing across a large app surface

## Fast Path

1. Make sure the frontend is already running locally.
2. Prefer the `frontend-visualqa` MCP tools when available. They keep browser state warm across calls.
3. If the route or UI state is uncertain, call `take_screenshot` first.
4. Run `verify_visual_claims` with 1-5 explicit claims and an explicit viewport.
5. Fix the frontend, then rerun the same claims until they pass.

## Claim Discipline

Claims must be observable from pixels. Open `references/claim-writing.md` for examples and anti-patterns.

If a claim requires interaction before it becomes true or false, pass a `navigation_hint`. Keep the claim focused on the final visible state instead of burying a multi-step script inside the claim text.

## Session Strategy

Use ephemeral mode for public pages and quick checks.

Use persistent mode for auth-gated apps, multi-step flows, or any case where cookies and local storage must survive across runs.

Setup and client-specific install commands live in `references/install.md`.
The detailed QA playbook, status meanings, and recovery steps live in `references/protocol.md`.

## Tool Preference

Prefer the tools in this order:

- `take_screenshot` for quick visual grounding
- `verify_visual_claims` for claim-based checks with evidence
- `manage_browser` when the shared browser state is stale, wrong-sized, or needs reset

If the MCP server is not available, use the CLI fallback in `references/install.md`.

$ARGUMENTS
