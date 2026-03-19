# Skill Ecosystem Notes

## Why this repo now has a skill-first layout

The project started as a runner with a CLI and MCP server, but the original product intent in ENG-3962 was an agent skill that fits naturally into coding-agent workflows.

The current ecosystem has converged on a few practical patterns:

- `SKILL.md` is the common denominator across Claude Code, Codex, and other Agent Skills-compatible clients.
- Supporting files are normal now. A short top-level `SKILL.md` plus referenced install/protocol docs is easier for retrieval than one long instruction blob.
- `skills.sh` is the broadest distribution layer for skill content across agents.
- Claude and Cursor plugins are the richest install path because they can bundle both skills and MCP configuration.

## Packaging choices in this repo

- `skills/frontend-visualqa/` is the canonical skill directory.
- `.agents/skills/frontend-visualqa/` is a compatibility wrapper for Codex and other OpenAI-compatible skill installers.
- `.claude-plugin/` and `.cursor-plugin/` are thin marketplace manifests for clients that support plugin bundles.
- `.mcp.json` is the checked-in server template used by plugin-aware clients.

## Practical implication

This repo now distributes the same skill body through multiple install surfaces instead of treating the CLI as the primary user interface.
