# AGENTS.md

## Cursor Cloud specific instructions

`frontend-visualqa` is a single Python product (managed with `uv`, requires Python >=3.11): a CLI plus an MCP (stdio) server that drives a headless Playwright Chromium browser and uses the **Yutori Navigator cloud API** to verify natural-language visual claims about a running frontend. There is no local backend/database of its own. Standard dev commands live in `CONTRIBUTING.md` (`uv sync`, `uv run pytest tests/`, `uv run ruff check src/ tests/`); CLI/run flags live in `README.md`.

Services and how to exercise them:

- **CLI** — entry point `frontend-visualqa` (run via `uv run frontend-visualqa <cmd>`). Subcommands: `serve`, `verify`, `screenshot`, `login`, `status`.
- **MCP server** — `uv run frontend-visualqa serve` (stdio transport; reads MCP messages on stdin and exits cleanly on EOF).
- **Demo frontend under test** — static pages in `examples/`. Serve them with `python3 -m http.server 8000 -d examples`, then target e.g. `http://localhost:8000/analytics_dashboard.html`. CI uses `examples/login_flow_claims.md` (see `.github/workflows/visualqa.yml`).

Non-obvious caveats:

- **Yutori auth is required only for `verify`** (and the MCP `verify_visual_claims` tool). Provide it via the `YUTORI_API_KEY` env var or `~/.yutori/config.json` (`{"api_key": "..."}`). Without it, `verify` exits 1 with `Yutori authentication is required for verify`. `screenshot`, `status`, and booting `serve` all work **without** a key, since they only drive the local browser.
- The browser runs **headless** by default; `--headed`/`login` need a display and are not useful in this VM.
- `uv run pytest tests/` runs fully offline — the suite uses fakes (`tests/fakes.py`) and never calls the live Yutori API or the network.
- `ruff format --check` currently reports several existing files "would reformat" under the installed ruff version; `ruff check` (the lint gate) passes clean. Do not reformat existing files unless explicitly asked.
- `uv` is symlinked into `/usr/local/bin`, so it resolves on PATH in any shell (login or not).
