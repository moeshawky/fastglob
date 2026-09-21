# TICKET 001 — Expose single-path match API + observable shim routing

- **Status:** DONE (2026-09-21) — both items implemented and verified
- **Filed:** 2026-09-21 by CEO (codegraph-ferrari session)
- **Source:** downstream need from `codegraph-ferrari/codegraph_ferrari/pipeline/stages/ingest/loss_ledger.py:28-39`
- **Resolution:** `fastglob.match(pattern, path)` added — PyO3 `r#match` (`src/fastglob/src/pyo3_ext.rs`), wrapper + type guards (`python/fastglob/__init__.py`), stub (`python/fastglob/_core.pyi`). Semantics = existing fnmatch-3.12 matcher on the WHOLE path (oracle-verified vs `fnmatch.fnmatchcase`: `*`/`**` cross `/`, `*` needs ≥1 char, literals span whole path, cross-type raises TypeError). Tests: `matcher::tests::whole_path_match_semantics` + `tests/test_package.py::MatchContract` (19-case oracle-agreed table). Shim loudness: `FASTGLOB_SHIM_LOUD=<non-empty>` → one stderr line per process at first interception (`shim/glob.py`), default silent, stdout untouched; tests `tests/test_shim_loud.py` (6 tests, 1 legit skip when fastglob is site-installed). README API + shim sections updated. Verified: `make test` green (cargo 17+7+10, compat 133/133, package 27/27).
  - Note: stdlib loader in `__init__.py` `_real_stdlib_glob_module` now excludes ALL shim dirs (incl. in-flight `sys.modules['glob'].__file__`) — fixes a real shim re-exec recursion found while testing ad-hoc shim copies.

## Context

CodeGraph Ferrari's ingest loss ledger classifies file paths as `in_repo` vs `external`
against vendor markers (`vendor/`, `node_modules/`, …). It currently does this with
hand-rolled substring checks — wrong tool. The correct fix is glob-pattern matching
(`**/vendor/**`) via fastglob. Two gaps block that:

## Item 1 — `fastglob.match(pattern, path)` Python API

The Python package exposes `glob` / `iglob` / `escape` / `has_magic` — all filesystem
traversal. There is no single-path match call, and README §8.9 has `translate`
deferred/not exposed. The Rust matcher (`src/fastglob/src/matcher.rs`) already exists;
it just isn't reachable from Python without hitting the filesystem.

**Acceptance:**
- `fastglob.match("**/vendor/**", "a/vendor/b.rs") is True`, same compat lock as the
  rest (oracle: stdlib `fnmatch`/`glob.translate` of the running interpreter).
- `has_magic`-consistent: literal patterns match literally.
- Tests in the style of `tests/` (compat cases, not adjectives).
- README API section updated with the new call.

## Item 2 — Make shim routing observable

Per operator (2026-09-21): machines in the fleet silently route stdlib `glob` to
fastglob via `PYTHONPATH=/opt/fastglob-shim`. Silent is fine for production, hostile
for measurement — an agent benchmarking or compat-testing `glob` cannot tell which
engine answered.

**Acceptance:**
- A loudness switch (env var, e.g. `FASTGLOB_SHIM_LOUD=1`) that logs one line per
  process on first interception (engine identity + shim version).
- Default stays silent (no behavior change unless opted in).
- Documented in README shim section.

## Non-goals

- No change to matcher semantics or compat contract.
- No new filesystem-traversal features.
- No default-on logging.
