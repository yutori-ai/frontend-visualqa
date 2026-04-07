# Contributing to frontend-visualqa

Thanks for your interest in contributing! Here's how to get started.

## Development setup

```bash
git clone https://github.com/yutori-ai/frontend-visualqa.git
cd frontend-visualqa
uv sync
uv run playwright install chromium
```

Run the tests:

```bash
uv run pytest tests/
```

Lint:

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

## Making changes

1. Fork the repo and create a branch from `main`.
2. Make your changes. Add tests for new functionality.
3. Ensure `uv run pytest tests/` passes.
4. Ensure `uv run ruff check src/ tests/` and `uv run ruff format --check src/ tests/` pass.
5. Open a pull request against `main`.

## Pull request guidelines

- Keep PRs focused — one logical change per PR.
- Write a clear description of what changed and why.
- Add or update tests for any new behavior.
- Update the README if your change affects user-facing behavior.

## Reporting bugs

Open an issue at [github.com/yutori-ai/frontend-visualqa/issues](https://github.com/yutori-ai/frontend-visualqa/issues) with:

- What you expected to happen
- What actually happened
- Steps to reproduce
- `frontend-visualqa --version` output

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
