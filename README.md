# frontend-visualqa

Visual QA for local frontends — gives coding agents a pixel-level verification loop powered by [Yutori n1](https://yutori.com/api).

## What it does

- Verifies explicit visual claims against a running localhost frontend
- Captures screenshots for quick visual inspection
- Reuses browser sessions across MCP tool calls for multi-step debugging
- Works as a CLI (`frontend-visualqa verify`) or MCP server (`frontend-visualqa serve`)

Does not start your dev server. If the URL is unreachable, claims return `not_testable`.

## Why n1

Playwright MCP can click, type, and assert against the DOM — but it cannot *see* the page. It can run cleanly on the wrong page, assert `modal.isVisible()` on a modal rendered off-screen, or miss a layout that broke on mobile.

n1 is a pixels-to-actions model trained with RL on live websites. Two capabilities matter here:

- **Self-correcting navigation** — Send the tool to `/tasks` instead of `/tasks/123` and n1 recognizes the wrong page, clicks through to the right one, and reports `wrong_page_recovered: true`. A DOM-based tool would assert on the wrong page and report success.
- **Rich visual evaluation** — After clicking "Mark Complete", n1 reported three changes: status badge blue→green, button label→"Completed", toast notification appeared. Playwright MCP would need three hand-written assertions.

## Install

```bash
uv tool install /path/to/frontend-visualqa
playwright install chromium
```

## Quick start

The repo includes a test page you can use immediately — no dev server required:

```bash
# Serve the included test page
python3 -m http.server 8000 -d examples &

# Verify some claims against it
frontend-visualqa verify http://localhost:8000/comprehensive_test.html \
  --claims \
  "The page title reads 'Comprehensive QA Test Suite'" \
  "The sidebar contains links labeled Dashboard, Tasks, and Settings" \
  "The notification badge shows the number 3"
```

Watch n1 work in a visible browser:

```bash
frontend-visualqa verify http://localhost:8000/comprehensive_test.html \
  --headed \
  --claims \
  "The counter shows 1" \
  --navigation-hint "Click the + button once before judging the claim."
```

Use against your own frontend the same way — just swap the URL:

```bash
frontend-visualqa screenshot http://localhost:3000
frontend-visualqa verify http://localhost:3000/tasks/123 \
  --claims "The Save button is visible without scrolling"
```

## MCP setup

<details>
<summary><strong>Claude Code</strong></summary>

```bash
claude mcp add --scope user frontend-visualqa -- \
  uvx --from /absolute/path/to/frontend-visualqa frontend-visualqa serve
```

With persistent sessions for auth-gated pages:

```bash
claude mcp add --scope user frontend-visualqa -- \
  uvx --from /absolute/path/to/frontend-visualqa frontend-visualqa serve \
  --browser-mode persistent
```

</details>

<details>
<summary><strong>Codex</strong></summary>

```bash
codex mcp add frontend-visualqa -- \
  uvx --from /absolute/path/to/frontend-visualqa frontend-visualqa serve
```

</details>

### MCP tools

| Tool | Description |
|------|-------------|
| `verify_visual_claims` | Structured pass/fail visual checks with screenshot evidence |
| `take_screenshot` | Capture current page state |
| `manage_browser` | Inspect, reset, close, or resize the shared browser session |

### Recommended agent workflow

1. Ensure the local frontend is running
2. `take_screenshot` to confirm page state
3. Write 1–5 concrete visual claims
4. `verify_visual_claims`
5. Fix code, rerun claims until they pass

## CLI reference

```
frontend-visualqa <command> [options]
```

| Command | Description |
|---------|-------------|
| `verify` | Verify visual claims against a URL |
| `screenshot` | Capture a screenshot |
| `login` | Open a headed browser to log in and save the session |
| `serve` | Start the MCP stdio server |
| `status` | Show browser status as JSON |

<details>
<summary><strong>verify options</strong></summary>

```bash
frontend-visualqa verify <url> --claims "claim1" "claim2" [options]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--claims` | *(required)* | One or more visual claims |
| `--navigation-hint` | | Interaction guidance before judging |
| `--width` / `--height` | 1280 / 800 | Viewport size |
| `--device-scale-factor` | 1.0 | DPR |
| `--headed` | off | Show the browser |
| `--browser-mode` | ephemeral | `ephemeral` or `persistent` |
| `--user-data-dir` | | Custom profile directory |
| `--session-key` | default | Named browser session |
| `--max-steps-per-claim` | 12 | Max actions per claim |
| `--claim-timeout-seconds` | 120 | Per-claim timeout |
| `--run-timeout-seconds` | 300 | Whole-run timeout |

</details>

<details>
<summary><strong>More examples</strong></summary>

Navigation hint for claims that require interaction:

```bash
frontend-visualqa verify http://localhost:8000/comprehensive_test.html \
  --claims "The dropdown label reads 'Priority: High'" \
  --navigation-hint "Open the Priority Selector dropdown and click High."
```

Mobile viewport:

```bash
frontend-visualqa verify http://localhost:8000/comprehensive_test.html \
  --claims "A hamburger menu button is visible" \
  --width 375 --height 812
```

</details>

## Browser modes

| Mode | Flag | Cookies persist? | Use case |
|------|------|-----------------|----------|
| Ephemeral *(default)* | — | No | Public pages, CI |
| Persistent | `--browser-mode persistent` | Yes | Auth-gated local dev |

<details>
<summary><strong>Persistent profile setup</strong></summary>

Log in once, reuse for all future runs:

```bash
# 1. One-time login — opens a headed browser, log in, press Enter to save
frontend-visualqa login http://localhost:3000/login

# 2. Subsequent runs reuse the saved session
frontend-visualqa verify http://localhost:3000/dashboard \
  --browser-mode persistent \
  --claims "The user avatar is visible in the header"
```

Profile stored at `~/.cache/frontend-visualqa/browser-profile/` by default. Override with `--user-data-dir`:

```bash
frontend-visualqa login http://localhost:3000/login \
  --user-data-dir /tmp/my-project-profile

frontend-visualqa verify http://localhost:3000/dashboard \
  --browser-mode persistent \
  --user-data-dir /tmp/my-project-profile \
  --claims "The dashboard loads without a login redirect"
```

</details>

## Writing good claims

Claims should be observable, scoped, and provable from pixels.

| Good | Weak |
|------|------|
| The modal title reads "Edit Task" | The modal works correctly |
| The Save button is visible without scrolling | The page looks polished |
| At 375px width, navigation collapses behind a menu button | The UI is intuitive |

If a claim requires interaction first, use `--navigation-hint` instead of encoding steps in the claim text.

## Result statuses

| Status | Meaning |
|--------|---------|
| `pass` | Claim matched the visual evidence |
| `fail` | Claim was visually false |
| `inconclusive` | Runner explored but couldn't determine confidently |
| `not_testable` | Environment blocked verification (server down, auth wall) |

## Development

```bash
uv sync
uv run playwright install chromium
uv run frontend-visualqa --help
```

Editable install:

```bash
uv pip install -e .
```
