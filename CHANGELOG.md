# Changelog

*Keep a one-line entry per user-visible change (newest first). This file is a
placeholder created during the public-snapshot usability pass (2026-08-23);
the repository previously had no changelog.*

## 0.1.3

- Shim: seamless proxy for all stdlib `glob` private symbols (`_StringGlobber`, `_PathGlobber`, `_no_recurse_symlinks` via eager copy + PEP 562) — fixes `pathlib` `ImportError` on Python 3.14 and makes `PYTHONPATH=/opt/fastglob-shim` invisible to `3.14 pathlib`.
- Python: expose `glob.translate` on 3.13+ via shim-immune loader (mirrors stdlib, lifts 3.14 candidate 127/133 → 133/133, 3.12 stays `skipped_not_exposed`).
- Harness: `bench/bench.py` and `tests/oracle/capture.py` now load true stdlib via path-strip (shim-immune) — baseline/oracle no longer measures the shim when `PYTHONPATH` is set.
- Shim source added at `shim/` (canonical for `/opt/fastglob-shim`) with `gnu_glob` escape hatch.
- Compat: fix `argparse` help string containing `%` that broke `--self-test` on Python 3.14.
- Docs: mark transparent replacement `DEPLOYED` (PyO3 in-process `_core` 0.1.2, `PYTHONPATH` injection) instead of planned/deferred.

## 0.1.2

- `pip install fastglob` now just works: the Python package ships the engine
  in-process as the `fastglob._core` PyO3 module (maturin build, abi3-py38
  wheel, sdist carries the Rust sources) — the subprocess transport
  (`_bin()`/`$FASTGLOB_BIN`, `F_DUPFD`+`pass_fds`) is removed, killing the
  per-call process spawn (measured 1353.8us -> 49.4us median on a small
  pattern; the package is now faster than stdlib `glob` on both the small
  and recursive probes).
- Fused single-pass for `**` hot patterns — 10-16x recursive.
- Added `fastglob.has_magic` (port of the stdlib's pattern-magic scan).
- Error contract preserved: embedded NUL -> ValueError, pattern >8192 bytes
  or >512 components -> RuntimeError "pattern too long", `dir_fd` not an
  open directory -> RuntimeError "fd is not a directory: N" (same verdicts
  as the CLI; the CLI binary is unchanged and remains a supported surface).
- `mypy` strict now checks the native surface via `fastglob/_core.pyi`.

## 0.1.1

- Initial PyO3 in-process bindings.

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
