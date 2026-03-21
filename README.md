# frontend-visualqa

Gives coding agents eyes for frontend work — visual QA and verification powered by [Yutori n1](https://yutori.com/api).

## What it does

- Verifies explicit visual claims against a running localhost frontend
- Captures screenshots for quick visual inspection
- Reuses browser sessions across MCP tool calls for multi-step debugging
- Works as a CLI (`frontend-visualqa verify`), MCP server (`frontend-visualqa serve`), or agent skill (`/frontend-visualqa`)

Does not start your dev server. If the URL is unreachable, claims return `not_testable`.

## Why n1

Playwright MCP can click, type, and assert against the DOM — but it cannot *see* the page. It can run cleanly on the wrong page, assert `modal.isVisible()` on a modal rendered off-screen, or miss a layout that broke on mobile.

n1 is a pixels-to-actions model trained with RL on live websites. Two capabilities matter here:

- **Self-correcting navigation** — Point the agent at `/tasks` instead of `/tasks/123` and n1 recognizes the wrong page, clicks through to the right one, and reports `wrong_page_recovered: true`. Playwright MCP would run assertions on the wrong page and silently pass — garbage in, garbage out.

  <table border="0" cellspacing="0" cellpadding="8"><tr>
    <td align="center" width="47%"><img src="docs/images/nav-step0-wrong-page.png" alt="Dashboard — wrong page, overlay active" width="100%"><br><em>n1 analyzing the wrong page</em></td>
    <td align="center" width="6%"><strong>→</strong></td>
    <td align="center" width="47%"><img src="docs/images/nav-step6-correct-page.png" alt="Task #123 — correct page" width="100%"><br><em>Landed on the correct page</em></td>
  </tr></table>

- **Rich visual evaluation** — On the task detail page for Task #123, after clicking "Mark Complete", n1 reported three changes: status badge "In Progress"→"Done", button label→"Completed", toast notification appeared. Playwright MCP would need three hand-written assertions.

  <table border="0" cellspacing="0" cellpadding="8"><tr>
    <td align="center" width="47%"><img src="docs/images/mark-complete-before.png" alt="Before — In Progress, overlay active" width="100%"><br><em>n1 analyzing before clicking</em></td>
    <td align="center" width="6%"><strong>→</strong></td>
    <td align="center" width="47%"><img src="docs/images/mark-complete-after.png" alt="After — Done + toast" width="100%"><br><em>Result after "Mark Complete"</em></td>
  </tr></table>

## Install

### Quick install (recommended)

1. Install:

    ```bash
    uv tool install frontend-visualqa \
      --with-executables-from yutori \
      --with-executables-from playwright
    playwright install chromium
    ```

    This installs the `frontend-visualqa`, `yutori`, and `playwright` CLIs and downloads the Chromium browser binary.

2. Authenticate:

    ```bash
    yutori auth login
    ```

    This opens your browser to save your Yutori API key to `~/.yutori/config.json`.

    <details>
    <summary>Or, manually add your API key</summary>

    Go to [platform.yutori.com](https://platform.yutori.com) and add your key to the config file:
    ```bash
    mkdir -p ~/.yutori
    cat > ~/.yutori/config.json << 'EOF'
    {"api_key": "yt-your-api-key"}
    EOF
    ```
    </details>

3. Register the MCP server using [add-mcp](https://github.com/nicobailon/add-mcp) (works with all clients):

    ```bash
    npx add-mcp -g -n frontend-visualqa "frontend-visualqa serve"
    ```

    Pick the clients you want to configure.

4. Install workflow skills using [skills.sh](https://skills.sh):

    ```bash
    npx skills add yutori-ai/frontend-visualqa -g
    ```

    Adds the `/frontend-visualqa` slash command for claim-based visual QA guidance.

    `-g` installs at user scope. Omit `-g` for project-local install.

5. Restart the agent client.

   <details>
   <summary>To uninstall later:</summary>

   ```bash
   uv tool uninstall frontend-visualqa
   npx skills remove -g frontend-visualqa
   ```

   `add-mcp` has no remove command. Delete the `frontend-visualqa` entry from your client's MCP config (e.g. `~/.mcp.json`).
   </details>

### Manual per-client setup

<details>
<summary><strong>Claude Code</strong></summary>

**Plugin (recommended)** — installs MCP tools + skill together:

```
/plugin marketplace add yutori-ai/frontend-visualqa
/plugin install frontend-visualqa@frontend-visualqa-plugins
```

**MCP only** (if you prefer not to use the plugin):

```bash
claude mcp add --scope user frontend-visualqa -- frontend-visualqa serve
```

</details>

<details>
<summary><strong>Codex</strong></summary>

```bash
codex mcp add frontend-visualqa -- frontend-visualqa serve
```

Skills can be installed via `npx skills add` above, or with `$skill-installer` inside Codex:

```
$skill-installer install https://github.com/yutori-ai/frontend-visualqa/tree/main/.agents/skills/frontend-visualqa
```

</details>

<details>
<summary><strong>Cursor / VS Code / other MCP hosts</strong></summary>

Use the checked-in `.mcp.json`, or point your client at `frontend-visualqa serve`.

</details>

<details>
<summary><strong>From source</strong></summary>

```bash
uv sync
uv run playwright install chromium
```

Register the MCP server with your client using `uvx --from /absolute/path/to/frontend-visualqa frontend-visualqa serve` as the command.

</details>

### Uninstall

<details>
<summary><strong>Claude Code plugin</strong></summary>

```
/plugin uninstall frontend-visualqa@frontend-visualqa-plugins -s user
```

</details>

<details>
<summary><strong>Codex</strong></summary>

Remove the MCP server entry from `~/.codex/config.toml`, then delete the skill directory:

```bash
rm -rf ~/.agents/skills/frontend-visualqa
```

Restart Codex after removing.

</details>

## Quick start

The repo includes a test page you can use immediately — no dev server required:

```bash
# From the repo root, serve the included test pages
cd /path/to/frontend-visualqa
lsof -ti:8000 | xargs kill 2>/dev/null; python3 -m http.server 8000 -d examples &
```

**Self-correcting navigation** — start on the wrong page and watch n1 find its way. In headed mode, you'll see click ripples, scroll indicators, and a status chip showing what n1 is doing:

```bash
# n1 lands on the home page, clicks Tasks, then clicks Task #123
# Green click ripples and a status HUD show each action as it happens
frontend-visualqa verify http://localhost:8000/multi_page_app.html \
  --headed \
  --claims "The task detail heading reads 'Task #123: Landing page polish'"
```

**Catching regressions** — mix passing and failing claims:

```bash
frontend-visualqa verify http://localhost:8000/comprehensive_test.html \
  --headed \
  --claims \
  "The sidebar contains links labeled Dashboard, Tasks, and Settings" \
  "The progress bar shows 100%"
# → first claim passes, second fails (actual value is 65%)
```

Use against your own frontend the same way — just swap the URL:

```bash
frontend-visualqa screenshot http://localhost:3000
frontend-visualqa verify http://localhost:3000/tasks/123 \
  --claims "The Save button is visible without scrolling"
```

## MCP tools

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
| `--headed` | off | Show the browser (implies `--visualize`) |
| `--visualize` / `--no-visualize` | on when headed | Show in-browser action overlay (click ripples, scroll indicators, status chip) |
| `--browser-mode` | ephemeral | `ephemeral` or `persistent` |
| `--user-data-dir` | | Custom profile directory |
| `--session-key` | default | Named browser session |
| `--max-steps-per-claim` | 12 | Max actions per claim |
| `--claim-timeout-seconds` | 120 | Per-claim timeout |
| `--run-timeout-seconds` | 300 | Whole-run timeout |
| `--reporter` | native | Output reporter (`native`, `ctrf`). Repeat for multiple. |

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

## Action visualization

When running in headed mode (`--headed`), the browser shows visual effects illustrating what n1 is doing (clicking, scrolling, typing). To disable it, use `--no-visualize`:

```bash
frontend-visualqa verify http://localhost:3000 \
  --headed --no-visualize \
  --claims "The heading reads 'Dashboard'"
```

The MCP tool `verify_visual_claims` accepts a per-call `visualize` parameter to control this independently of the server's default.

Overlay elements are automatically hidden during screenshot capture so they never appear in evidence sent to n1 or saved artifacts.

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
| `passed` | Claim matched the visual evidence |
| `failed` | Claim was visually false |
| `inconclusive` | Runner explored but couldn't determine confidently |
| `not_testable` | Environment blocked verification (server down, auth wall) |

## Reporters

Output format for persisted artifacts. Does not affect CLI stdout or MCP tool responses (always native JSON).

| Reporter | File | Description |
|----------|------|-------------|
| `native` *(default)* | `run_result.json` | Full domain-specific schema with all fields |
| `ctrf` | `ctrf-report.json` | [CTRF](https://ctrf.io/) standard JSON for CI/CD integration |

```bash
frontend-visualqa verify http://localhost:3000 \
  --claims "The heading reads 'Dashboard'" \
  --reporter native --reporter ctrf
```

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

## Skill packaging

The canonical skill lives in [skills/frontend-visualqa/SKILL.md](skills/frontend-visualqa/SKILL.md).

- `skills/frontend-visualqa/` is the source of truth.
- `.agents/skills/frontend-visualqa/` is a compatibility wrapper for Codex and other OpenAI-compatible installers.
- `.claude-plugin/` and `.cursor-plugin/` contain plugin marketplace manifests.
- `docs/skill-ecosystem.md` records the packaging rationale.
