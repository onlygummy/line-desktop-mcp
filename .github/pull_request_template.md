## Summary

<!-- What changed, in one or two sentences. -->

## Why

<!-- What problem does this solve? Link the issue if there is one. -->

Closes #

## Test plan

<!-- Commands or steps a reviewer can run. -->

```bash
uv run pytest
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy
```

## Checklist

- [ ] I added or updated a test for the behaviour change.
- [ ] I updated the relevant page under `docs/` if user-visible behaviour changed.
- [ ] I did not include session files, tokens, QR codes, archive contents, media, or private chat text.
- [ ] The change keeps the server read-only toward LINE.
