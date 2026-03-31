# frontend-visualqa

Gives coding agents eyes for frontend work — visual QA and verification powered by [Yutori n1](https://yutori.com/api).

## What it does

- Verifies explicit visual claims against a running localhost frontend
- Captures screenshots for quick visual inspection
- Reuses browser sessions across MCP tool calls for multi-step debugging
- Works as a CLI (`frontend-visualqa verify`), MCP server (`frontend-visualqa serve`), or agent skill (`/frontend-visualqa`)

Does not start your dev server. If the URL is unreachable, claims return `not_testable`.

## Why visualqa?

Playwright MCP can click, type, and assert against the DOM — but it cannot *see* the page. It can run cleanly on the wrong page, assert `modal.isVisible()` on a modal rendered off-screen, or miss a layout that broke on mobile.

n1 is a pixels-to-actions model trained with RL on live websites. Two capabilities matter here:

- **Self-correcting navigation** — Point the agent at the product catalog instead of a specific product page and n1 recognizes the wrong page, clicks through to the right one, and reports `trace.wrong_page_recovered: true`. Playwright MCP would run assertions on the wrong page and silently pass — garbage in, garbage out.

  <table border="0" cellspacing="0" cellpadding="8"><tr>
    <td align="center" width="47%"><img src="docs/images/nav-step0-wrong-page.webp" alt="Product catalog — wrong page" width="100%"><br><em>n1 lands on the product catalog</em></td>
    <td align="center" width="6%"><strong>→</strong></td>
    <td align="center" width="47%"><img src="docs/images/nav-step6-correct-page.webp" alt="Product detail — correct page" width="100%"><br><em>Navigated to the correct product page</em></td>
  </tr></table>

- **Rich visual evaluation** — On the cart page, both items show sale prices ($149.99 and $79.99) but n1 caught that the subtotal of $279.98 uses the original prices — the discount was never applied. On the API dashboard, the quota label reads "100%" but the progress bar is visibly only two-thirds full. Playwright MCP would pass both — the DOM text is consistent and the progress bar width is just a CSS value.

  <table border="0" cellspacing="0" cellpadding="8"><tr>
    <td align="center" width="50%"><img src="docs/images/cart-pricing-bug.webp" alt="Cart — sale prices shown but subtotal uses original prices" width="100%"><br><em>n1 catches the discount-not-applied bug</em></td>
    <td align="center" width="50%"><img src="docs/images/dashboard-quota.webp" alt="Dashboard — label says 100% but bar is 65%" width="100%"><br><em>Label says 100% but the bar is only at 2/3rds</em></td>
  </tr></table>

<details>
<summary><strong>Known limitation</strong></summary>

- **Native `<select>` dropdowns** — n1 cannot see or interact with native HTML `<select>` dropdown options because they render as OS-level widgets outside the browser viewport. If your page uses native selects, replace them with custom in-browser dropdown components for visual testing, or pre-fill the selection via URL parameters.

</details>

## Install

### Prerequisites

Install [uv](https://docs.astral.sh/uv/) if you don't already have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Quick install (recommended)

1. Install CLIs:

    ```bash
    uv tool install frontend-visualqa \
      --with-executables-from yutori \
      --with-executables-from playwright
    playwright install chromium
    ```

    This installs the `frontend-visualqa`, `yutori`, and `playwright` CLIs and downloads the Chromium browser binary.

2. Log into Yutori API:

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

4. Install skills using [skills.sh](https://skills.sh):

    ```bash
    npx skills add yutori-ai/frontend-visualqa -g
    ```

    Adds the `/frontend-visualqa` slash command for claim-based visual QA guidance.

    `-g` installs at user scope. Omit `-g` for project-local install.

5. Restart your agent (Codex, Claude Code, etc) so the installs are picked up.

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

## Examples

The repo includes demo pages you can use immediately — no dev server required:

```bash
# From the repo root, serve the included demo pages
cd /path/to/frontend-visualqa
lsof -ti:8000 | xargs kill 2>/dev/null; python3 -m http.server 8000 -d examples &
```

**Self-correcting navigation** — start on the wrong page and watch n1 find its way:

```bash
# n1 lands on the product catalog, clicks through to find the product detail page
# After each evidence screenshot, the Yutori overlay replays the last action
frontend-visualqa verify http://localhost:8000/ecommerce_store.html \
  --headed \
  --claims 'The product detail page shows Wireless Headphones Pro priced at $149.99'
```

**Catching regressions** — mix passing and failing claims:

```bash
frontend-visualqa verify http://localhost:8000/analytics_dashboard.html \
  --headed \
  --claims \
  'The API status indicator shows Active' \
  'The monthly quota progress bar is completely filled'
# → first claim passes, second fails (label says 100% but bar is ~65% full)
```

**Catching pricing bugs** — verify that discounts are actually applied:

```bash
frontend-visualqa verify 'http://localhost:8000/ecommerce_store.html#/cart' \
  --headed \
  --claims 'The displayed cart subtotal equals the sum of the visible sale prices'
# → fails: n1 sees the sale prices sum to $229.98 while the displayed subtotal is $279.98
```

**Autonomous form filling** — n1 fills a multi-step form, picks a date, and catches a timezone bug:

```bash
frontend-visualqa verify 'http://localhost:8000/booking_form.html' \
  --headed \
  --max-steps-per-claim 25 \
  --claims 'The date on the confirmation page matches the date selected on the calendar' \
  --navigation-hint "Fill out the form with example data (grayed text is showing example format, not filled out values)"
# → fails: n1 fills the form, picks a date, books the slot, and catches the off-by-one on the confirmation page
```

`--navigation-hint` gives n1 context it can't infer from pixels alone. Here, the booking form shows placeholder text like "John Doe" and "555-0123" — n1 can mistake these for already-filled values and skip the form. The hint tells it that grayed text is placeholder format, not real data, so it fills every field correctly.

**Login flow with visual bug detection** — use a persistent session to log in once, then verify the dashboard in a separate step without the navigation hint:

```bash
# Step 1: Log in (navigation hint tells n1 how to fill the form)
frontend-visualqa verify http://localhost:8000/yutori_login.html \
  --headed \
  --browser-mode persistent \
  --max-steps-per-claim 20 \
  --claims 'After logging in, the dashboard shows "Welcome back, Developer"' \
  --navigation-hint 'Type "test@yutori.com" in the email field, type "password123" in the password field, then click Continue. Wait for the dashboard to load.'

# Step 2: Verify dashboard (same persistent session — already logged in, no hint needed)
frontend-visualqa verify http://localhost:8000/yutori_login.html \
  --headed \
  --browser-mode persistent \
  --no-reset-between-claims \
  --max-steps-per-claim 20 \
  --claims \
  'The API Calls Today stat card shows the value 1,247' \
  'The Monthly Quota progress bar fill matches the percentage shown in the label'
# → first claim passes, second fails: label says "100% used" but the progress bar is ~40% filled
```

Use against your own frontend the same way — just swap the URL:

```bash
frontend-visualqa screenshot http://localhost:3000
frontend-visualqa verify http://localhost:3000/dashboard \
  --claims 'The revenue chart is visible without scrolling'
```

<details>
<summary><strong>More examples</strong></summary>

Navigation hint for claims that require interaction:

```bash
frontend-visualqa verify http://localhost:8000/ecommerce_store.html \
  --headed \
  --claims 'The cart badge shows 3 items' \
  --navigation-hint "Click 'Add to Cart' on the Mechanical Keyboard K7 product card."
```

Scrolling to find off-screen content:

```bash
frontend-visualqa verify http://localhost:8000/analytics_dashboard.html \
  --headed \
  --claims 'The /api/v1/webhooks endpoint returned a 200 OK status'
# → fails: n1 scrolls to the request table and finds a 500 Error
```

Form validation — triggering and verifying an error message:

```bash
frontend-visualqa verify http://localhost:8000/yutori_login.html \
  --headed \
  --claims 'The email field shows "Please enter a valid email address" after submitting the empty form' \
  --navigation-hint 'Click the Continue button without entering anything in the email or password fields.'
```

Persistent session — log in once, then verify the dashboard without repeating the hint:

```bash
# Login step (with hint)
frontend-visualqa verify http://localhost:8000/yutori_login.html \
  --headed \
  --browser-mode persistent \
  --max-steps-per-claim 20 \
  --claims 'After logging in, the dashboard shows "Welcome back, Developer"' \
  --navigation-hint 'Type "test@yutori.com" in the email field, type "password123" in the password field, then click Continue. Wait for the dashboard to load.'

# Dashboard step (already logged in, no hint)
frontend-visualqa verify http://localhost:8000/yutori_login.html \
  --headed \
  --browser-mode persistent \
  --no-reset-between-claims \
  --max-steps-per-claim 20 \
  --claims 'The API Calls Today stat card shows the value 1,247'
```

</details>

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
frontend-visualqa verify <url> --claims 'claim1' 'claim2' [options]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--claims` | *(required)* | One or more visual claims |
| `--navigation-hint` | | Interaction guidance before judging |
| `--width` / `--height` | 1280 / 800 | Viewport size |
| `--device-scale-factor` | 1.0 | DPR |
| `--headed` | off | Show the browser (implies `--visualize`) |
| `--visualize` / `--no-visualize` | on when headed | Show in-browser action overlay (cursor, click pulses, scroll dots, status chip) |
| `--browser-mode` | ephemeral | `ephemeral` or `persistent` |
| `--user-data-dir` | | Custom profile directory |
| `--session-key` | default | Named browser session. Persistent mode supports one named session at a time. |
| `--run-name` | | Optional label included in JSON output and reports |
| `--max-steps-per-claim` | 12 | Max actions per claim |
| `--claim-timeout-seconds` | 120 | Per-claim timeout |
| `--run-timeout-seconds` | 300 | Whole-run timeout |
| `--reporter` | native | Output reporter (`native`, `ctrf`). Repeat for multiple. |

</details>

## Browser modes and visualization

| Mode | Flag | Cookies persist? | Use case |
|------|------|-----------------|----------|
| Ephemeral *(default)* | — | No | Public pages, CI |
| Persistent | `--browser-mode persistent` | Yes | Auth-gated local dev |

Persistent mode uses one shared Playwright profile-backed context and supports one named session at a time. Use `--run-name` if you only want to tag CI output, and use ephemeral mode if you need multiple simultaneous named sessions.

<details>
<summary><strong>Persistent profile setup</strong></summary>

Log in once, reuse for all future runs:

```bash
# 1. One-time login — opens a headed browser, log in, press Enter to save
frontend-visualqa login http://localhost:3000/login

# 2. Subsequent runs reuse the saved session
frontend-visualqa verify http://localhost:3000/dashboard \
  --browser-mode persistent \
  --run-name dashboard-auth \
  --claims 'The user avatar is visible in the header'
```

Profile stored at `~/.cache/frontend-visualqa/browser-profile/` by default. Override with `--user-data-dir`:

```bash
frontend-visualqa login http://localhost:3000/login \
  --user-data-dir /tmp/my-project-profile

frontend-visualqa verify http://localhost:3000/dashboard \
  --browser-mode persistent \
  --user-data-dir /tmp/my-project-profile \
  --claims 'The dashboard loads without a login redirect'
```

</details>

<details>
<summary><strong>Action visualization</strong></summary>

When running in headed mode (`--headed`), the browser shows visual effects illustrating what n1 is doing:
- post-capture cursor replays for click, scroll, drag, and typing actions
- a compact thought card when a tool-using model turn includes reasoning text

To disable it, use `--no-visualize`:

```bash
frontend-visualqa verify http://localhost:3000 \
  --headed --no-visualize \
  --claims 'The API status indicator shows Active'
```

The MCP tool `verify_visual_claims` accepts a per-call `visualize` parameter to control this independently of the server's default.

Overlay elements are hidden for every evidence screenshot, and action replays are injected only after capture so no visualization appears before the screenshot sent to n1 or saved to artifacts.

</details>

## Writing good claims

Claims should be observable, scoped, and provable from pixels.
Prefer direct, falsifiable wording over broad interpretations like "is correct."

| Good | Weak |
|------|------|
| The cart total is $261.37 | The cart works correctly |
| The displayed subtotal equals the sum of the visible sale prices | The cart subtotal is correct |
| The product price shows $149.99 in monospace font | The page looks polished |
| At 375px width, the stat cards stack in a single column | The dashboard is responsive |

If a claim requires interaction first, use `--navigation-hint` instead of encoding steps in the claim text.

## Result statuses

| Status | Meaning |
|--------|---------|
| `passed` | Claim matched the visual evidence |
| `failed` | Claim was visually false |
| `inconclusive` | Runner explored but couldn't determine confidently |
| `not_testable` | Environment blocked verification (server down, auth wall) |

For the CLI, `frontend-visualqa verify` exits `0` only when every claim passes. It exits `1` if any claim is `failed`, `inconclusive`, or `not_testable`. Usage errors still exit with argparse's standard `2`.

## Reporters

Output format for persisted artifacts. Does not affect CLI stdout or MCP tool responses (always native JSON).

| Reporter | File | Description |
|----------|------|-------------|
| `native` *(default)* | `run_result.json` | Full domain-specific schema with all fields |
| `ctrf` | `ctrf-report.json` | [CTRF](https://ctrf.io/) standard JSON for CI/CD integration |

Each claim result contains:
- **`finding`** — the verdict explanation (what was observed)
- **`proof`** — the decisive artifact paths, step number, and a compact extracted-text preview
- **`page`** — URL and viewport where the claim was evaluated
- **`trace`** — the execution trace: actions taken, rich events, screenshot paths, and the saved trace path

<details>
<summary><strong>Example claim result</strong></summary>

```json
{
  "claim": "The monthly quota progress bar is completely filled",
  "status": "failed",
  "finding": "The quota label reads '100%' and '12,500 / 12,500 requests used', but the progress bar is visually only about 65% filled — the bar and the label disagree.",
  "proof": {
    "screenshot_path": "artifacts/run-.../claim-02/step-04.webp",
    "step": 4,
    "after_action": "scroll([640, 720], direction=down, amount=1)",
    "text": null,
    "text_path": null
  },
  "page": {
    "url": "http://localhost:8000/analytics_dashboard.html",
    "viewport": { "width": 1280, "height": 800, "device_scale_factor": 1.0 }
  },
  "trace": {
    "steps_taken": 4,
    "wrong_page_recovered": false,
    "screenshot_paths": ["..."],
    "actions": ["..."],
    "trace_path": "artifacts/run-.../claim-02/trace.json"
  }
}
```

`proof.screenshot_path` points to the screenshot n1 was examining when it rendered the verdict.
`proof.text` and `proof.text_path` are optional and are usually `null` unless a tool returns saved text output.
`trace.trace_path` points to `trace.json`, which contains the full machine-readable event trace with reasoning and verdict metadata. Events are excluded from the JSON output by default to keep it compact; access them programmatically via `result.trace.events` or read `trace.json` directly.

</details>

```bash
frontend-visualqa verify http://localhost:3000 \
  --claims 'The checkout total matches the sum of line items' \
  --reporter native --reporter ctrf
```

## CI / GitHub Actions

The repo includes a GitHub Actions workflow (`.github/workflows/visualqa.yml`) that runs visual QA checks on pull requests targeting `main` (and supports manual dispatch via `workflow_dispatch`). Use it as a template for adding frontend-visualqa to your own CI pipeline.

### What the workflow does

1. Installs `frontend-visualqa` via `uv tool install` and downloads Playwright's Chromium via `playwright install chromium --with-deps`
2. Serves the example pages with Python's built-in HTTP server
3. Runs visual claims against the login page — element checks, form validation, post-login dashboard
4. Verifies that known visual bugs are caught (progress bar mismatch)
5. Uploads screenshot artifacts and CTRF reports for inspection

### Setting up in your own repo

1. **Add your Yutori API key as a secret.** Go to your repo's Settings > Secrets and variables > Actions and add `YUTORI_TESTING_API_KEY` with your Yutori API key.

2. **Copy the workflow.** Adapt `.github/workflows/visualqa.yml` to your project — replace `python3 -m http.server` with your dev server start command and `wait-on` or a sleep until it's ready:

    ```yaml
    - name: Start dev server
      run: |
        npm start &
        npx wait-on http://localhost:3000 --timeout 60000
    ```

3. **Write claims for your pages.** Each `frontend-visualqa verify` step tests a set of visual claims against a URL:

    ```yaml
    - name: Visual QA — Dashboard
      run: |
        set -o pipefail
        frontend-visualqa verify http://localhost:3000/dashboard \
          --claims \
          'The revenue chart is visible without scrolling' \
          'The sidebar shows 5 navigation items' \
          --reporter native --reporter ctrf | tee visualqa-dashboard.json
    ```

4. **Upload artifacts** so screenshots and reports are available even when tests fail:

    ```yaml
    - name: Upload visual QA artifacts
      if: always()
      uses: actions/upload-artifact@v6
      with:
        name: visualqa-results
        path: |
          artifacts/
          visualqa-*.json
    ```

### Testing claims that should fail

To verify that frontend-visualqa catches known bugs, capture the exit code and validate the output contains a real failure (not a crash):

```yaml
- name: Visual QA — Catch known bug
  run: |
    set -o pipefail
    exit_code=0
    frontend-visualqa verify http://localhost:3000 \
      --claims 'The progress bar matches the displayed percentage' \
      --reporter native --reporter ctrf | tee visualqa-bug.json \
      || exit_code=$?

    if [ "$exit_code" -eq 0 ]; then
      echo "UNEXPECTED: claim passed" && exit 1
    fi

    # Verify tool produced a real failed claim, not just a crash
    if [ ! -s visualqa-bug.json ]; then
      echo "ERROR: no output — tool may have crashed" && exit 1
    fi

    if ! python3 -c "
    import json, sys
    data = json.load(open('visualqa-bug.json'))
    results = data.get('results', [])
    if not results or not any(r.get('status') == 'failed' for r in results):
        print('ERROR: output has no failed claim'); sys.exit(1)
    "; then exit 1; fi

    echo "Expected failure: visual bug detected"
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
