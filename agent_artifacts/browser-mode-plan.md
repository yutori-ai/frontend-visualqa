# Browser Mode Abstraction — Implementation Plan

## Context

frontend-visualqa currently hardcodes `playwright.chromium.launch()` + fresh `browser.new_context()` sessions (`browser.py:62`, `browser.py:169`). This means every run starts with a blank browser — no cookies, no localStorage, no extensions. Auth-gated pages are `not_testable`.

Front-end developers already use Playwright CLI and Playwright MCP. We should reuse Playwright primitives, interoperate with Playwright MCP, and not duplicate its product surface. See the [Linear ticket (ENG-3962)](https://linear.app/yutori/issue/ENG-3962/frontend-visualqa) and `MERGED_IMPLEMENTATION_PLAN.md` for full project context.

## Design Principles

- **Reuse Playwright** (the library) — we already depend on it
- **Interoperate with Playwright MCP** (the product) — match terminology, design for future CDP attach
- **Don't embed Playwright MCP as our internal control plane** — the n1 vision loop needs direct `page`/`context` access; routing through another MCP process adds complexity for no gain
- **Forward-compatible mode abstraction** — implement Tier 1 behind a mode enum so Tier 2 doesn't require a second refactor

## Browser Modes

| Mode | Launch method | Sessions | Cookies persist? | Extensions? | Status |
|------|-------------|----------|-----------------|-------------|--------|
| `ephemeral` | `chromium.launch()` + `new_context()` | Multi-session | No | No | Current default |
| `persistent` | `launch_persistent_context(user_data_dir=...)` | Single-session | Yes (across runs) | No | **Tier 1 (this plan)** |
| `cdp_attach` | `connect_over_cdp(endpoint)` | Single-session | Yes (user's browser) | Yes | Tier 2 (future) |

## Tier 1 Scope: `persistent` mode

### What it solves

- "I have to log in again every run" — user logs in once to the persistent profile, cookies survive across runs
- Matches Playwright MCP's default behavior (persistent profile at a known path)

### What it does NOT solve

- Reusing the user's existing Chrome sessions (that's `cdp_attach`)
- Password manager extensions (that's `cdp_attach`)

### Architecture change

The key difference: `launch_persistent_context()` returns a `BrowserContext` directly, not a `Browser`. You cannot call `browser.new_context()` on it. This collapses the session model to single-session in persistent mode.

```
ephemeral:    Playwright → Browser → BrowserContext (per session) → Page
persistent:   Playwright → BrowserContext (is the session) → Page
cdp_attach:   Playwright → Browser (remote) → BrowserContext → Page
```

### Implementation

#### 1. `schemas.py` — Add `BrowserMode` enum and config

```python
class BrowserMode(str, Enum):
    ephemeral = "ephemeral"
    persistent = "persistent"
    # cdp_attach = "cdp_attach"  # Tier 2

class BrowserConfig(BaseModel):
    mode: BrowserMode = BrowserMode.ephemeral
    user_data_dir: str | None = None  # Required for persistent mode
    # cdp_endpoint: str | None = None  # Tier 2
    headless: bool = True
    navigation_timeout_ms: int = 20_000
    settle_delay_seconds: float = 1.0
```

Default `user_data_dir` when persistent mode is used but no path specified: `~/.cache/frontend-visualqa/browser-profile/` (matches the Playwright MCP pattern of a well-known cache path).

#### 2. `browser.py` — Refactor `BrowserManager`

Main changes:

- `__init__` accepts `BrowserConfig` instead of individual kwargs
- `ensure_browser()` branches on mode:
  - `ephemeral`: current behavior (`chromium.launch()`)
  - `persistent`: `chromium.launch_persistent_context(user_data_dir=..., viewport=..., device_scale_factor=...)`
- `get_session()` in persistent mode:
  - Validates `session_key`: only `"default"` is allowed. Any other key raises `ValueError` with a clear message explaining that persistent mode supports a single session. This prevents silent state aliasing where two callers believe they have isolated sessions but share the same browser state.
  - Uses a dedicated automation page strategy (see below)
  - Viewport changes use `page.set_viewport_size()` (DPR changes require relaunch)
- `_create_session()` in persistent mode:
  - Does NOT call `browser.new_context()` — uses the context from launch
  - **Dedicated automation page**: creates a fresh `context.new_page()` and navigates it to `about:blank`, rather than reusing whatever tabs the persistent profile may have restored. This avoids nondeterministic starting state from previously-restored tabs. Any pre-existing pages from profile restore are left alone but not used.
- `close()` in persistent mode:
  - Closes the context (which also closes the browser)
  - Does NOT delete the `user_data_dir` (that's the point)

Key constraint: in persistent mode, `self._browser` is `None` and `self._persistent_context` holds the `BrowserContext`. The rest of the code interacts with `BrowserSession` which already wraps `context` + `page`, so downstream code (`claim_verifier.py`, `actions.py`) should not need changes.

#### 3. `cli.py` — Surface the config

```
# Normal headless verification with persistent profile
frontend-visualqa verify http://localhost:3000/tasks \
  --browser-mode persistent \
  --user-data-dir ~/.cache/frontend-visualqa/browser-profile \
  --claims "The task list shows 5 items"

# Headed bootstrap: log in interactively, then close
frontend-visualqa login \
  --user-data-dir ~/.cache/frontend-visualqa/browser-profile \
  http://localhost:3000/login
```

Flags added to `verify`, `screenshot`, and `serve`:

- `--browser-mode`: `ephemeral` (default) or `persistent`
- `--user-data-dir`: path to persistent profile (defaults to `~/.cache/frontend-visualqa/browser-profile/` if omitted with `persistent` mode)
- `--headed` / `--no-headed`: run the browser in headed (visible) mode. Default: `--no-headed` (headless). When `--headed` is passed, `BrowserConfig.headless` is set to `False`.

New `login` subcommand (see section below).

#### Headed mode as a first-class CLI concern

Currently `headless=True` is buried in `runner.py:40` and `browser.py:47` as a constructor default with no CLI surface. This means users can never watch n1 interact with the page, which hurts debugging and makes it harder to understand failures.

Elevating `--headed` to the CLI:

```
# Watch n1 verify claims in real time
frontend-visualqa verify http://localhost:3000 \
  --headed \
  --claims "The sidebar shows 3 links"

# Headed + persistent: watch verification AND keep session cookies
frontend-visualqa verify http://localhost:3000 \
  --headed --browser-mode persistent \
  --claims "The task list loads after login"

# MCP server in headed mode (useful during development)
frontend-visualqa serve --headed

# Login is always headed (implicit, --headed is ignored if passed)
frontend-visualqa login http://localhost:3000/login
```

This is orthogonal to browser mode — you can use `--headed` with both `ephemeral` and `persistent` modes. The `login` subcommand is always headed regardless of the flag.

#### 4. `login` subcommand — Headed bootstrap for persistent profiles

**This solves the "how do you populate the profile" problem.** The current config defaults to `headless=True`, and the runner is headless by default, so without this there is no practical way to perform the first login.

```
frontend-visualqa login http://localhost:3000/login \
  --user-data-dir ~/.cache/frontend-visualqa/browser-profile
```

Behavior:
1. Checks `sys.stdin.isatty()`. If not a TTY, prints an error and exits with code 1: `"login requires an interactive terminal (stdin must be a TTY)."`
2. Launches Chromium in **headed mode** (`headless=False`) with `launch_persistent_context(user_data_dir=...)`
3. Navigates to the provided URL
4. Prints: "Browser is open. Log in, then press Enter here to close and save the session."
5. Starts a dedicated **daemon thread** that calls `sys.stdin.readline()` and sets a `threading.Event` on completion. Also registers `context.on("close", lambda: event.set())` so the same event fires if the user closes the browser window.
6. Polls `event.is_set()` from the async event loop via `asyncio.sleep(0.2)`. No executor threads involved.
7. When the event is set (from either source): close the browser context gracefully. The daemon thread dies automatically on process exit since it's a daemon.
8. Cookies/localStorage are persisted to `user_data_dir`.
9. Subsequent `--browser-mode persistent` runs reuse that profile.

**Why polling**: Playwright is async, so bare `input()` on the main thread blocks the event loop. The naive fix — `asyncio.to_thread(input)` or `asyncio.to_thread(event.wait)` — uses the default executor, and `asyncio.run()` joins default executor threads on shutdown. If the browser closes first and the blocking call never returns, shutdown hangs.

The clean solution: a daemon thread for stdin + polling the `threading.Event` from the event loop. No default executor involvement at all.

```python
# Sketch
import threading

done = threading.Event()
browser_closed = False

def _read_stdin():
    sys.stdin.readline()
    done.set()

reader = threading.Thread(target=_read_stdin, daemon=True)
reader.start()

context.on("close", lambda: done.set())  # unblock poll if browser closes

# Poll from the event loop — no executor threads involved
while not done.is_set():
    await asyncio.sleep(0.2)

if not browser_closed:
    await context.close()
```

The key insight: `context.on("close", lambda: done.set())` means the `done` event gets set regardless of which side fires first. The `asyncio.sleep(0.2)` poll loop involves no executor threads, so `asyncio.run()` has nothing to join on shutdown.

**Edge cases**:
- Non-TTY stdin: hard error at step 1, before launching the browser
- User closes browser window before pressing Enter: `context.on("close")` sets the event, poll exits, we print "Browser closed." and exit cleanly. The daemon stdin thread is abandoned (not joined).
- User presses Enter after browser is already closed: `context.close()` is a no-op, exits cleanly
- User presses Enter while browser is still open: normal path, we close the context ourselves

This is a one-time setup step. The profile persists until the user deletes the `user_data_dir`.

No n1, no claims, no screenshots — just a thin wrapper around Playwright's persistent context in headed mode. Implementation is ~40 lines in `cli.py`.

#### 5. `mcp_server.py` — Server-level config injection

Browser mode is a session-level concern, not a per-claim concern. It should be set at server startup.

**Current problem**: `_get_runner()` at `mcp_server.py:56` creates `VisualQARunner()` with no configuration hook — there's no way to pass `BrowserConfig` from `serve` CLI args into the runner.

**Fix**: Add a module-level `_server_browser_config: BrowserConfig | None` that `_handle_serve` sets before starting the MCP server, and that `_get_runner()` reads when constructing the runner.

```python
# mcp_server.py — new config plumbing
_server_browser_config: BrowserConfig | None = None
_config_frozen: bool = False

def configure_server(browser_config: BrowserConfig) -> None:
    """Set server-level browser config. Must be called before first tool invocation.

    This is called from synchronous CLI code before the event loop starts,
    so it cannot depend on asyncio.get_running_loop(). Instead it uses a
    simple frozen flag that _get_runner() sets on first runner creation.
    """
    global _server_browser_config, _config_frozen
    if _config_frozen:
        raise RuntimeError(
            "Cannot change browser config after runner has been created. "
            "Call configure_server() before the first tool invocation."
        )
    _server_browser_config = browser_config

async def _get_runner() -> Any:
    global _config_frozen
    # ... existing double-checked lock pattern ...
    config = _server_browser_config or BrowserConfig()
    runner = VisualQARunner(browser_config=config)
    _config_frozen = True
    # ...
```

**Lifecycle guard**: `configure_server()` is called from synchronous CLI code before the MCP event loop starts, so it cannot use `_loop_key()` (which requires `asyncio.get_running_loop()`). Instead, a simple `_config_frozen` flag is set by `_get_runner()` on first runner creation. `configure_server()` checks this flag synchronously.

**Teardown**: `_close_all_runners()` resets all three globals: the runner cache, `_config_frozen = False`, and `_server_browser_config = None`. This prevents stale config from leaking across tests or in-process reuse.

`cli.py`'s `_handle_serve` parses `--browser-mode` and `--user-data-dir`, builds a `BrowserConfig`, and calls `configure_server()` before `mcp.run()`.

The `serve` subcommand then looks like:

```
frontend-visualqa serve --browser-mode persistent \
  --user-data-dir ~/.cache/frontend-visualqa/browser-profile
```

#### 6. `manage_browser` tool — Report mode in status

`status` action should include `browser_mode` and `user_data_dir` (if persistent) in its response so the calling agent knows the current configuration.

#### 7. `runner.py` — Accept `BrowserConfig`

`VisualQARunner.__init__` accepts an optional `BrowserConfig` and passes it to `BrowserManager`. The current `headless` kwarg is subsumed by `BrowserConfig.headless`.

```python
class VisualQARunner:
    def __init__(
        self,
        *,
        browser_manager: BrowserManager | None = None,
        browser_config: BrowserConfig | None = None,  # new
        # ... rest unchanged
    ) -> None:
        config = browser_config or BrowserConfig()
        self.browser_manager = browser_manager or BrowserManager(config=config)
        # ...
```

### Session key enforcement in persistent mode

**Rule**: In persistent mode, any `BrowserManager` method that accepts a `session_key` rejects non-`"default"` keys with a `ValueError`. This applies uniformly to:

- `get_session(session_key=...)`
- `close_session(session_key=...)`
- `restart_session(session_key=...)`
- `set_viewport(session_key=...)`

The validation lives in a single private method `_validate_session_key(session_key)` called at the top of each public method, so the rule cannot be bypassed by calling one entrypoint but not another. The `manage_browser` MCP tool routes through these same methods, so it inherits the guard automatically.

| Mode | `session_key="default"` | `session_key="other"` |
|------|------------------------|----------------------|
| `ephemeral` | Works (current behavior) | Works — creates separate context |
| `persistent` | Works — uses the single persistent context | **Raises `ValueError`** everywhere |

Error message: `"Persistent browser mode supports only the 'default' session. Use ephemeral mode for multiple sessions, or omit session_key."`

### Files changed

| File | Change | Effort |
|------|--------|--------|
| `schemas.py` | Add `BrowserMode` enum, `BrowserConfig` model | Small |
| `browser.py` | Refactor `BrowserManager` to support ephemeral/persistent modes, dedicated automation page, session_key validation | Medium — main refactor |
| `cli.py` | Add `--browser-mode`, `--user-data-dir` to verify/screenshot/serve; add `login` subcommand | Medium |
| `mcp_server.py` | Add `configure_server()`, wire `_handle_serve` args to runner config | Small-medium |
| `runner.py` | Accept `BrowserConfig`, pass through to `BrowserManager` | Small |
| `tests/test_browser.py` | Tests for persistent mode, session_key validation, automation page strategy | Medium |

### Files NOT changed

| File | Why |
|------|-----|
| `claim_verifier.py` | Works with `BrowserSession`, unchanged |
| `actions.py` | Works with `Page`, unchanged |
| `n1_client.py` | No browser interaction |
| `prompts.py` | No browser interaction |
| `artifacts.py` | No browser interaction |

### Testing

**BrowserManager — persistent mode:**
1. Creates context with `user_data_dir`, returns valid session
2. `get_session(session_key="other")` raises `ValueError`
3. `close_session(session_key="other")` raises `ValueError`
4. `restart_session(session_key="other")` raises `ValueError`
5. `set_viewport(session_key="other")` raises `ValueError`
6. Creates a dedicated new page (not reusing restored tabs)
7. Viewport changes work via `set_viewport_size`
8. DPR changes trigger relaunch with same `user_data_dir`
9. `close()` closes context but preserves `user_data_dir` on disk
10. `status()` reports `browser_mode` and `user_data_dir`

**MCP server config:**
11. `configure_server()` before runner creation succeeds
12. `configure_server()` after runner creation raises `RuntimeError`
13. `_close_all_runners()` resets state so `configure_server()` works again
14. `serve --browser-mode persistent` sets config that reaches the runner

**CLI:**
15. `--browser-mode persistent` flag is accepted and propagated
16. `--headed` flag sets `headless=False` in `BrowserConfig`
17. `login` subcommand opens headed browser at specified URL
18. `login` exits with error when stdin is not a TTY

**Integration:**
19. Launch persistent, navigate to a page that sets a cookie, close, relaunch persistent with same `user_data_dir`, verify cookie survives

### Terminology alignment with Playwright MCP

| Playwright MCP | frontend-visualqa | Notes |
|---------------|-------------------|-------|
| `--user-data-dir` | `--user-data-dir` | Same name, same semantics |
| `--cdp-endpoint` | (Tier 2) `--cdp-endpoint` | Will match when implemented |
| Default profile path: `~/Library/Caches/ms-playwright/mcp-chrome-profile` | `~/.cache/frontend-visualqa/browser-profile/` | Separate profile, similar pattern |

## Tier 2 Preview: `cdp_attach` mode (not in scope)

For reference, the future `cdp_attach` mode would:
- Use `playwright.chromium.connect_over_cdp(endpoint)` to attach to a user-managed browser
- Get full access to logged-in sessions AND extensions (password managers)
- Require the user to launch Chrome with `--remote-debugging-port=9222` or use a Chrome extension relay (like Playwright MCP extension mode, Playwriter, or BrowserMCP)
- Change lifecycle semantics: we don't own the browser process, `browser.close()` only disconnects
- `connect_over_cdp` is lower-fidelity than Playwright's normal protocol — tab selection matters, default context is special, and `browser.close()` semantics differ. The action layer should mostly survive, but `BrowserManager` session ownership logic will need real work.

The mode abstraction built in Tier 1 will accommodate this without another refactor — `ensure_browser()` gets a third branch, `BrowserConfig` gets a `cdp_endpoint` field.
