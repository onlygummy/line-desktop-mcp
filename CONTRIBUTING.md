# Contributing

Thanks for helping with `line-desktop-mcp`.

This project reads a personal LINE account through the unofficial OkLine SDK. Treat session files, the local archive, and downloaded media as sensitive. Never attach them to an issue, pull request, or test fixture.

## Development

You need Python 3.10 or newer and [uv](https://docs.astral.sh/uv/). Node.js and a desktop session are only needed to exercise a real login, not for the test suite.

```bash
uv sync
uv run line-desktop-mcp
uv run pytest
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy
```

The tests fake the LINE client, so they run without Node, Tk, or a network connection. Add tests for behaviour changes; a bug fix should include a test that fails on the old code.

## Pull requests

- Open an issue first for anything larger than a typo or a small documentation fix.
- Keep one logical change per pull request.
- Write commit messages in the imperative mood, for example `fix: keep the QR fallback private`.
- Describe what changed, why, and how you tested it.
- Update the relevant page under `docs/` when user-visible behaviour changes.
- Run the full local checks above before pushing.

CI runs the suite on Linux, Windows, and macOS for Python 3.10 and 3.14. A change that only works on one platform is not ready.

## Security

Do not open a public issue for a vulnerability. Use the repository's [private security advisory](https://github.com/onlygummy/line-desktop-mcp/security/advisories/new) instead. The threat model is in [SECURITY.md](SECURITY.md).

Do not commit:

- `session.json`, `qr.txt`, `tokens.json`, or any file containing LINE tokens
- `archive.db`, `archive.db-wal`, `archive.db-shm`, or `media/`
- screenshots of a QR code or a private chat

## Releases

Releases are built from `v*` tags by `.github/workflows/release.yml`.

- A tag builds and checks the distributions, creates a GitHub release, and uploads the artifacts.
- PyPI publishing is opt-in with the repository variable `PYPI_PUBLISH=true` and the protected `pypi` environment.
- Do not push a release tag until the version in `pyproject.toml`, `src/line_desktop_mcp/__init__.py`, and the release notes agree.

## Questions

Open an issue with the `question` label. Do not paste secrets or private chat content.
