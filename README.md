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

### Quick install (recommended)

1. Install the CLI and log in to [Yutori](https://yutori.com) (provides the n1 vision model):

    ```bash
    pip install yutori
    yutori auth login
    ```

    This opens your browser and saves your API key to `~/.yutori/config.json`.

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

2. Install the MCP server using [add-mcp](https://github.com/nicobailon/add-mcp) (works with all clients):

    ```bash
    npx add-mcp -n frontend-visualqa "uvx frontend-visualqa serve"
    ```

    Pick the clients you want to configure.

3. Install workflow skills using [skills.sh](https://skills.sh):

    ```bash
    npx skills add yutori-ai/frontend-visualqa -g
    ```

    Adds the `/frontend-visualqa` slash command for claim-based visual QA guidance.

    `-g` installs at user scope. Omit `-g` for project-local install.

4. Restart the agent client.

   <details>
   <summary>To list or remove later:</summary>

   ```bash
   npx skills ls -g
   npx skills remove -g frontend-visualqa
   ```

   `add-mcp` has no remove command. Delete the `frontend-visualqa` entry from the `.mcp.json` it wrote to (project-level or `~/.mcp.json`).
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
claude mcp add --scope user frontend-visualqa -- uvx frontend-visualqa serve
```

</details>

<details>
<summary><strong>Codex</strong></summary>

```bash
codex mcp add frontend-visualqa -- uvx frontend-visualqa serve
```

Skills can be installed via `npx skills add` above, or with `$skill-installer` inside Codex:

```
$skill-installer install https://github.com/yutori-ai/frontend-visualqa/tree/main/.agents/skills/frontend-visualqa
```

</details>

<details>
<summary><strong>Cursor / VS Code / other MCP hosts</strong></summary>

Use the checked-in `.mcp.json`, or point your client at `uvx frontend-visualqa serve`.

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

**Self-correcting navigation** — start on the wrong page and watch n1 find its way:

```bash
# n1 lands on the home page, clicks Tasks, then clicks Task #123
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
| `--headed` | off | Show the browser |
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
