# Install And Setup

## Requirements

- A running local frontend
- Yutori API key — run `yutori auth login` after install, or set `YUTORI_API_KEY`
- Playwright Chromium — run `playwright install chromium` after install

## Quick Install

```bash
uv tool install frontend-visualqa \
  --with-executables-from yutori \
  --with-executables-from playwright
playwright install chromium
yutori auth login
```

MCP server (works with all clients):

```bash
npx add-mcp -g -n frontend-visualqa "frontend-visualqa serve"
```

Skill (cross-agent):

```bash
npx skills add yutori-ai/frontend-visualqa -g
```

Restart the agent client after setup.

## CLI Fallback

If the MCP server is not installed, the same runner can be used directly:

```bash
frontend-visualqa screenshot http://localhost:3000
frontend-visualqa verify http://localhost:3000 --claims "The heading reads Dashboard"
```

## Persistent Login

For auth-gated apps, populate the persistent browser profile once:

```bash
frontend-visualqa login http://localhost:3000/login
```

Subsequent runs can use persistent mode:

```bash
frontend-visualqa verify http://localhost:3000/dashboard \
  --browser-mode persistent \
  --run-label dashboard-auth \
  --claims "The user avatar is visible in the header"
```

Persistent mode reuses a single profile-backed browser context, so it supports one named session at a time. Use `--run-label` if you only want to tag output, and use ephemeral mode if you need multiple simultaneous named sessions.

## If Tools Are Missing

If the skill is installed but the `frontend-visualqa` tools are unavailable in the current client:

1. Install or register the MCP server.
2. Restart the client so it rescans skills and MCP servers.
3. Retry with `take_screenshot` before writing claims.
