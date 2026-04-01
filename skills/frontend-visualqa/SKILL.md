---
name: frontend-visualqa
description: Verify a running frontend with explicit visual claims, viewports, and screenshot evidence using the frontend-visualqa MCP server or CLI.
argument-hint: "[url] [claims or QA task]"
metadata:
  short-description: Gives coding agents eyes for frontend work — visual QA and verification powered by Yutori n1
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
3. If the user provides a URL and explicit claims, go straight to `verify_visual_claims` — it captures screenshots internally, so a separate `take_screenshot` is redundant.
4. Only call `take_screenshot` first when you have no claims yet and need to see the page before writing them.
5. Fix the frontend, then rerun the same claims until they pass.

## Claim Discipline

Claims must be observable from pixels. Open `references/claim-writing.md` for examples and anti-patterns.

If a claim requires interaction before it becomes true or false, pass a `navigation_hint`. Keep the claim focused on the final visible state instead of burying a multi-step script inside the claim text.

For multi-claim flows where only some claims need setup, prefer a claims file and put per-claim metadata under the specific bullet, for example:

```md
- After logging in, the dashboard shows "Welcome back, Developer"
  - navigation_hint: Type "test@yutori.com" in the email field, type "password123" in the password field, then click Continue.
- The API Calls Today stat card shows the value 1,247
```

When using MCP, split warm-session verification into successive `verify_visual_claims` calls if the setup differs between claims. Do not expect one global navigation hint to apply selectively within a single batch.

## Session Strategy

Use ephemeral mode for public pages and quick checks.

Use persistent mode for auth-gated apps, multi-step flows, or any case where cookies and local storage must survive across runs.

Setup and client-specific install commands live in `references/install.md`.
The detailed QA playbook, status meanings, and recovery steps live in `references/protocol.md`.

## Presenting Proof to the User

Every tool response includes file paths to screenshot evidence. Always surface this proof — do not just summarize the text result.

`verify_visual_claims` returns an `artifacts_dir` and, for each claim, `results[].proof.screenshot_path` (the decisive screenshot) and `results[].proof.text` (a textual summary). Additional screenshots from intermediate steps live in `results[].trace.screenshot_paths`.

`take_screenshot` returns a `screenshot_path`.

After a verification run:

1. Read the proof screenshot file for any failed or inconclusive claim so you can describe what is visually wrong.
2. Show the user the screenshot path and, if the client supports it, open or display the image inline.
3. When summarizing results, reference the specific visual evidence — do not just repeat the text finding.

If your client cannot display images inline, print the absolute path so the user can open it manually.

## Tool Preference

Prefer the tools in this order:

- `verify_visual_claims` when the user provides claims — this is the primary tool and captures its own screenshots
- `take_screenshot` only when you need to see the page before you can write claims
- `manage_browser` when the shared browser state is stale, wrong-sized, or needs reset

If the MCP server is not available, use the CLI fallback in `references/install.md`.

$ARGUMENTS
