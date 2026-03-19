# Agent skills (Codex / OpenAI compatible clients)

The canonical skill lives at `skills/frontend-visualqa/`.

`.agents/skills/frontend-visualqa/` is only a compatibility wrapper:

- `SKILL.md` is a symlink to the canonical skill body
- `references/` is a symlink to the canonical support files
- `agents/` is a symlink to the canonical agent metadata

Edit the canonical files under `skills/frontend-visualqa/` so the content does not drift across install paths.
