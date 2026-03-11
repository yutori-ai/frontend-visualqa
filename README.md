# frontend-visualqa

`frontend-visualqa` gives coding agents a local visual QA loop for frontend work. The package exposes a single CLI entrypoint plus a FastMCP server, both backed by the same `VisualQARunner`.

## What it does

- Verifies explicit visual claims against a locally running frontend
- Captures screenshots for quick visual inspection
- Reuses browser sessions inside the long-running MCP server when you need multi-step debugging
- Exposes the same runtime through `frontend-visualqa serve`, `verify`, `screenshot`, and `status`

The package does not start your dev server for you. If the target URL is unreachable, expect `not_testable`. For auth-gated pages, use a persistent browser profile so you only log in once.

## Why n1

Playwright MCP provides hands — it can click, type, and assert against the DOM. But it cannot *see* the page. A Playwright script can run cleanly on the wrong page, assert `modal.isVisible()` on a modal that rendered off-screen, or miss a layout that broke on mobile. It has no visual ground truth.

n1 is a pixels-to-actions model trained with RL on live websites. The two capabilities that matter here:

**Self-correcting navigation.** When a coding agent sends the tool to `localhost:3000/tasks` instead of `localhost:3000/tasks/123`, n1 sees a list view, recognizes it is not the task detail page, and navigates there autonomously. In testing, n1 started on the home page, clicked "Tasks" in the sidebar, then clicked a task row — arriving at the correct page in 2 steps with no human guidance. The result includes `wrong_page_recovered: true` so the coding agent knows what happened. A DOM-based tool would have run its assertions on the wrong page and reported success.

**Rich visual state evaluation.** After clicking a single "Mark Complete" button, n1 reported three visual changes in its summary: the status badge changed from blue "In Progress" to green "Done" with a checkmark, the button label changed to "Completed", and a toast notification appeared confirming the action. Playwright MCP would need three separate hand-written assertions. n1 just saw it.

## Install

```bash
uv tool install /path/to/frontend-visualqa
frontend-visualqa --help
```

This installs `frontend-visualqa` as a global CLI command. You also need Playwright's Chromium browser:

```bash
playwright install chromium
```

## CLI usage

Quick screenshot:

```bash
frontend-visualqa screenshot http://localhost:3000
```

Verify a few claims:

```bash
frontend-visualqa verify http://localhost:3000/tasks/123 \
  --claims \
  "The page title reads 'Task Details'" \
  "The Save button is visible without scrolling" \
  "The activity sidebar is open on the right"
```

Include navigation context when the page needs interaction first:

```bash
frontend-visualqa verify http://localhost:3000/tasks \
  --claims "The edit modal title reads 'Edit Task'" \
  --navigation-hint "Click the first task row to open the edit modal before judging the claim."
```

Switch viewport:

```bash
frontend-visualqa verify http://localhost:3000 \
  --claims "The mobile menu button is visible in the header" \
  --width 375 \
  --height 812
```

Watch n1 work in real time:

```bash
frontend-visualqa verify http://localhost:3000 \
  --headed \
  --claims "The sidebar shows 3 links"
```

Smoke-check the current process state:

```bash
frontend-visualqa status
```

One-shot CLI commands do not share browser state with each other. Session reuse and browser status are meaningful in the long-running `frontend-visualqa serve` process or when you embed `VisualQARunner` programmatically in a single Python process.

## Browser modes

By default the runner launches a fresh Chromium instance with no saved state. For auth-gated pages, switch to a persistent browser profile so cookies and localStorage survive across runs.

### Persistent profile

Log in once, reuse the session for all future runs:

```bash
# 1. One-time login: opens a headed browser, you log in, press Enter to save
frontend-visualqa login http://localhost:3000/login

# 2. All subsequent runs reuse the saved session
frontend-visualqa verify http://localhost:3000/dashboard \
  --browser-mode persistent \
  --claims "The user avatar is visible in the header"
```

The profile is stored at `~/.cache/frontend-visualqa/browser-profile/` by default. Override with `--user-data-dir`:

```bash
frontend-visualqa login http://localhost:3000/login \
  --user-data-dir /tmp/my-project-profile

frontend-visualqa verify http://localhost:3000/dashboard \
  --browser-mode persistent \
  --user-data-dir /tmp/my-project-profile \
  --claims "The dashboard loads without a login redirect"
```

### Mode summary

| Mode | Flag | Cookies persist? | Extensions? | Use case |
|------|------|-----------------|-------------|----------|
| Ephemeral (default) | *(none)* | No | No | Public pages, CI |
| Persistent | `--browser-mode persistent` | Yes | No | Auth-gated local dev |

## Development

To run from source without installing:

```bash
uv sync
uv run playwright install chromium
uv run frontend-visualqa --help
```

For an editable install into your current environment:

```bash
uv pip install -e .
```

## MCP setup

Claude Code:

```bash
claude mcp add --scope user frontend-visualqa -- \
  uvx --from /absolute/path/to/frontend-visualqa frontend-visualqa serve
```

With persistent sessions (auth-gated pages):

```bash
claude mcp add --scope user frontend-visualqa -- \
  uvx --from /absolute/path/to/frontend-visualqa frontend-visualqa serve \
  --browser-mode persistent
```

Codex:

```bash
codex mcp add frontend-visualqa -- \
  uvx --from /absolute/path/to/frontend-visualqa frontend-visualqa serve
```

## Tool surface

- `verify_visual_claims`: main tool for structured pass/fail visual checks
- `take_screenshot`: capture the current page state and save evidence
- `manage_browser`: inspect, reset, close, or resize the shared browser session

`manage_browser` is mainly useful through the MCP server, where the runner stays alive across multiple tool calls.

## Writing good claims

Good claims are observable, scoped, and testable from pixels alone.

Good:

- `The modal title reads "Edit Task"`
- `The Save button is visible without scrolling`
- `The sidebar contains links labeled "Scouts", "Findings", and "Settings"`
- `At 375px width, the primary navigation collapses behind a menu button`

Weak:

- `The page looks polished`
- `The spacing feels better`
- `The modal works correctly`
- `The UI is intuitive`

Write claims so the runner can prove them with a screenshot. If a state requires interaction, add a `navigation_hint`.

## Result statuses

- `pass`: the claim matched the final visual evidence
- `fail`: the claim was visually false
- `inconclusive`: the runner explored but could not determine the answer confidently
- `not_testable`: the environment blocked verification, such as an unreachable dev server or auth wall

For a small set of simple textual claims, the runner may use DOM-derived text visibility as a defensive downgrade guard. That guard only exists to prevent obvious false-positive `pass` verdicts; n1 remains the primary evaluator.

## Recommended workflow for agents

1. Ensure the local frontend is already running.
2. Start with `take_screenshot` if you do not yet trust the page state.
3. Write 1-5 concrete visual claims.
4. Run `verify_visual_claims`.
5. Fix the frontend and rerun the same claims until they settle.
