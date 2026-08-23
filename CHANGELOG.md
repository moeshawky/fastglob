# Changelog

*Keep a one-line entry per user-visible change (newest first). This file is a
placeholder created during the public-snapshot usability pass (2026-08-23);
the repository previously had no changelog.*

## Unreleased

- Usability pass: `make compat`/`make oracle-capture` regenerate the fixture
  tree before capture (git cannot preserve the `000` mode of
  `tests/fixtures/tree/errors/unreadable` nor the empty
  `tests/fixtures/tree/basic/empty` directory, so a tree restored from a
  fresh clone previously failed the oracle freshness gate).
- Compat comparison now normalizes each side's own fixture-tree root before
  the multiset comparison, so the absolute-pattern case (l05 `@ROOT@/...`)
  passes on any clone path, not only the capture-time path.
- CI: `setup-python` pinned to `3.12.13` (the compat freshness gate requires
  an exact interpreter match) and the oracle is re-captured under the CI
  runner user (the committed capture was recorded as uid 0; the §8.6
  unreadable-dir fixture is uid-dependent by design).
- Added `LICENSE` (MIT placeholder — operator to confirm holder), this
  `CHANGELOG.md`, and `.mcp.json.example`.
- `tests/compat/candidate.json` is now gitignored (run artifact of
  `make compat`; regenerates on every run and embeds machine-specific
  absolute paths).
