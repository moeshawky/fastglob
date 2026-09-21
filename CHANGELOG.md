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

### CI gate alignment + loader alias defect (2026-09-21, this pass)

- **Gates this push had newly broken, now fixed**: `ruff format --check` (G2 —
  the `bytes` overload of `match` was 93 chars) and `ruff check` (G1 — 6 new
  `PTH100/PTH109/PTH120` errors from `_real_stdlib_glob_module`'s `_os.path`
  calls, replaced with `pathlib`). Pre-existing G3 failures also cleared
  (`-> ModuleType | None` annotation; 2 dead `type: ignore`s removed).
- **Loader defect found while fixing G1** (real, not lint noise): the shim-dir
  path-strip compared `os.path.abspath()` forms. `abspath` never resolves
  symlinks, so one shim copy named twice on `sys.path` (real path + symlink
  alias) survived the strip, and the re-entrancy guard — which compared the same
  abspath forms — could not catch it: `exec_module` re-entered the shim from
  inside the shim. Measured: `_real_glob.__file__` == the shim copy. Now keyed on
  `Path.resolve()`, gated by `tests/test_shim_alias_path_strip.py` (4 checks,
  **2/4 red** against the abspath loader), wired into `make shim-test` so CI runs
  it. The other shim suites name one directory once, which is why they missed it.
- `python/.python-version` 3.12.13 -> 3.12.3: it contradicted CI's pin and the
  oracle's `_meta.python`, which the compat freshness gate compares **exactly** —
  so a `uv`-managed dev environment failed `make compat` by construction.
- `uv.lock` refreshed — it still recorded `fastglob 0.1.1` (one-line diff), so
  `uv lock --check` (G6) failed. Now green.
- **Withdrawn claim (my measurement error): "G4/G5 coverage was already red on
  main at 42%"**. That reading was an artifact of this box's
  `PYTHONPATH=/opt/fastglob-shim`: any process importing `glob` — including
  `coverage` itself, which imports `glob` at startup — drags in the shim, which
  imports `fastglob` *before* the tracer starts, so every import-time line is
  invisible. Under a CI-faithful environment the identical command reports
  **83.33% >= 80%** (G4/G5 pass; residuals are the 3.13-only `translate` branch
  and the guard/except paths).
- **Known issue, not fixed here (needs 3.13+ to verify)**: when the stdlib `glob`
  is already imported, `_real_stdlib_glob_module` adds the *stdlib* directory to
  `_shim_dirs`, strips it, and `find_spec` returns `None` — measured on 3.12
  (`_real_glob is None`; control without that anchor finds
  `/usr/lib/python3.12/glob.py`). On 3.12 the outcome coincides with the
  documented pre-3.13 "must not claim a value", so nothing user-visible changes;
  on 3.13+ the same path would suppress the advertised `fastglob.translate`,
  because the loader keys on the *directory* and cannot distinguish a stdlib
  `glob` from a shim copy.

### r4 CLOSED: gated deploy/rollback automation (2026-09-21, this pass)

- **`tools/deploy.sh` + `make deploy` / `rollback` / `deploy-status` / `deploy-verify` / `wheel`.**
  Deployment installs two artifacts that must stay in step — the `fastglob` wheel into
  site-packages and `shim/*.py` into `/opt/fastglob-shim` — so they are swapped together,
  never half-applied.
- **A red suite cannot reach the swap.** `apply` runs the full `make test` suite first and
  refuses to touch anything if it is red. The remedy's original gate (`make compat`) was
  widened on purpose: compat alone left `cargo fmt`, clippy, the harness self-test and the
  shim suites ungated. Mutation additionally requires `DRY_RUN=0`; the default `DRY_RUN=1`
  runs the gate, prints the exact plan, and stops.
- **Rollback is structural, not hopeful.** Both artifacts are snapshotted into
  `/var/backups/fastglob/<stamp>` before the first mutation and restored automatically on
  any later failure — verified by forcing an install failure and confirming the
  pre-existing shim came back byte-exactly (`sha256 3f0f2f6e1a50…`). `make rollback`
  restores the newest snapshot; `FROM=<stamp>` picks one.
- **The documented drift is now detectable.** `make deploy-status` is read-only and exits 1
  when the deployed artifacts differ from this tree. Its first run also found a **second**
  drift class: the installed wheel (`/usr/local/lib/python3.12/dist-packages/fastglob`,
  built 2026-08-28) has no `match` while the repo's package does (`grep -c '^def match'`
  → 0 vs 3).
- **The driver never edits the provisioner-owned `PYTHONPATH` injection files**
  (`/etc/environment`, `/etc/profile.d/99-fastglob.sh`, `~/.bashrc`,
  `/opt/agent-bin/bash_noninteractive`) — they are `agent-bin-setup`'s and are re-applied
  on the next provision, so rewriting them would fight the provisioner. Falling back to
  stdlib is done by removing the shadow instead: an injection path whose directory holds no
  `glob.py` resolves to stdlib `glob` (measured: an empty and a nonexistent directory both
  yield `/usr/lib/python3.12/glob.py`). PyPI publishing is likewise out of scope.
- **`verify` checks the installed wheel in isolation (`-I`).** A plain `import fastglob`
  answers with the *repo* on this box (`~/.local/.../fastglob.pth` puts the source tree
  ahead of site-packages), which produced a false PASS until the check was isolated — the
  exact class of wrong-artifact measurement the design record is about (§0.2, E3/E4).
- **A snapshot is now only authoritative for the targets it recorded.** `restore_snapshot`
  reads "the snapshot recorded nothing installed" as "remove whatever is installed now", so
  replaying a snapshot taken against another `SHIM_DIR` used to delete an install the
  snapshot never described — which is exactly what happened while testing this driver on
  2026-09-21 (see the maintenance ledger: a rollback driven with a synthetic snapshot and
  the ambient default interpreter removed the live
  `/usr/local/lib/python3.12/dist-packages/fastglob{,-0.1.3.dist-info}`). `rollback` now
  refuses before touching anything when the snapshot has no `meta`, when its recorded
  `shim-dir` is not the `SHIM_DIR` being restored, or when it records no state for an
  interpreter in `PYTHONS`, and it restores from the snapshot's own recorded
  `interpreter`/`purelib` pairs rather than the ambient `PYTHONS`. Pinned by
  `tests/test_deploy_guard.py` (17 checks, including the failed-closed replay of the
  original accident); verified to fail 10/17 against a guard-less copy of the driver, so
  the test is not decoration.
- **`SUDO=` (empty) now means "already privileged, do not escalate"**, which is what a root
  container needs and what lets the rollback guards be exercised without root at all.
- **Deploy driver syntax + guards are gated** in `make test` and CI, so a broken or
  guard-less deploy script cannot be merged silently. `make wheel` fixed: maturin resolves
  `[tool.maturin] manifest-path` relative to `pyproject.toml`, so it must run from
  `python/` (from the repo root it looks for a root `Cargo.toml` and fails).
- Docs: `docs/architecture.md` §Deployment now describes the driver instead of asserting
  "no auto-rollback"; the stale `387KB` binary figure there and in `README.md` is corrected
  to **452KB** (463576 bytes); `README.md`'s "Transparent Replacement (planned)" heading —
  which contradicted its own DEPLOYED body — is fixed; the design record's R4 row moves
  OPEN → LANDED with the sandbox verification, and E11 is marked superseded (it remains true
  of the *shim*, which gained no in-code rollback and should not).

### Provenance + false-label correction in the measurement records (2026-09-21, this pass)

- **`bench/results/baseline.md` now carries a Provenance block.** Its numbers were
  measured on a DIFFERENT machine: x86_64 / nproc 224 / Python 3.12.13 / git `5668b01`.
  This box is aarch64 / nproc 2 / Python 3.12.3; both `/usr/local/bin/python3` and
  `/usr/local/lib/python3.12/glob.py` are absent here, and `5668b01` is not a commit in
  this repository's history (7 commits, non-shallow) — so the record is not traceable to
  any state of this repo. The recorded conditions are PRESERVED (rewriting them would
  falsify the measurement); the labels are qualified and this box's measured values are
  recorded beside them. Re-measurement also re-confirmed `bench/bench.py` is shim-neutral:
  under `PYTHONPATH=/opt/fastglob-shim` it resolves the true stdlib
  `/usr/lib/python3.12/glob.py`, where a naive `import glob` resolves to the shim.
- **`docs/design/shim-seamlessness-2026-08-27.md` gains a dated §0 correction/status
  block.** Its 2026-08-27 witnesses are preserved verbatim (it is a dated transcript, so
  rewriting them would destroy their evidentiary value); §0 corrects every claim it makes
  about the *committed* repository: the capture `_meta` now reads python 3.12.3 /
  `/usr/bin/python3` / `/usr/lib/python3.12/glob.py` / uid 1001 / 139 cases (E3's
  `3.12.13` + `/usr/local/lib/…` parenthetical is superseded, though E3's conclusion — the
  capture is clean, not shim-poisoned — still holds and is now directly checkable),
  `133` → **139** cases, and E9's "byte-identical" shim claim, which no longer holds
  (repo `shim/glob.py` 199 lines vs deployed 129; `FASTGLOB_SHIM_LOUD` 2× vs 0×). §0.3
  records remedy status R1–R8 as measured: R1, R2, R3, R5, R8 LANDED; R4 OPEN; R6 and R7
  LANDED in follow-ups (see the next two bullets).
- **R6 (doc rot) closed:** removed the last subprocess-era claims — `docs/architecture.md`
  dependency row (`python/__init__.py` → `fastglob._core`, in-process, `__init__.py:141`),
  `fd_DUPFD` in the kernel row (`F_DUPFD` existed only for the removed `pass_fds`
  transport; it now survives solely as a comment at `pyo3_ext.rs:11`), the "Python deps:
  stdlib only (`os`, `subprocess`, `fcntl`, `pathlib`)" line (0 hits for all three), and
  `docs/api.md:201` (`Python level, subprocess arg check`) — plus `README.md:142`'s "The
  Python wrapper meets this boundary in `subprocess`", which the earlier sweep had missed
  because the file's other subprocess mentions correctly describe the 0.1.1 removal. The
  same pass corrected the drifted citations in that "Verified" table (`main.rs:277`→`:310`,
  `lib.rs:18-19`→`:23,26,42-43`, `walk.rs:380-422`→`:429,453,462-464,494,500`,
  `walk.rs:169-368`→ per-syscall lines), `__init__.py` 462→**465** lines, `docs/api.md`'s
  `grep "16 passed"`→`"17 passed"` (the old command matched nothing), binary 385→**452KB**,
  and `walk.rs:177-208`→`:245-260`.
- **R7 (`pathlib` not accelerated) is now contract §8.11 — and the design record's
  premise needed splitting.** E8 describes `pathlib` consuming `glob._StringGlobber`, but
  that is a 3.13+/3.14 fact: `_StringGlobber` does not exist before 3.13, and **3.12
  `pathlib` imports `glob` nowhere at all** — it globs with its own `_WildcardSelector`
  (`pathlib.py:190`). So the note records two rows instead of asserting one mechanism:
  3.12 (measured here — no `glob` consumer, therefore never accelerated) and 3.14
  (inherited from E8, labelled not-re-measured, since no 3.13/3.14 interpreter is
  reachable from uid 1001). `Path.glob("**/*.txt")` measured identical under the deployed
  shim, the repo shim, and no shim — the limitation costs speed, never correctness.
- **Measured, and recorded rather than "fixed":** the NUL `ValueError`'s MESSAGE text
  differs from stdlib's — `fastglob: embedded null byte in PATTERN` / `... in --root-dir`
  vs the bare `embedded null byte` — while the exception TYPE matches. The compatibility
  contract is silent on message text (no `message` / `null byte` clause), so this is an
  observation, not a declared divergence; `README.md:142` and `docs/api.md:201` had been
  quoting stdlib's wording as if it were the engine's.
- **The `uid 0` case annotations were false, not merely stale.** The committed capture
  ran as **uid 1001** and `errors/unreadable` is mode 0 owned by uid 1001, so the notes
  asserted the OPPOSITE of the results they annotate: `e01` (`errors/unreadable/*` → `[]`)
  read "uid 0 (VERIFIED): readable, secret.txt listed"; `e06` (`…/secret.txt` → `[]`) read
  "matched (uid 0)" (lstat of the child needs search `x` on the parent, which mode 0
  denies); `e08` (`**` → only the self entry) read "traverses". Corrected in
  `tests/fixtures/cases.json` + `tests/fixtures/generate.py`, and propagated by
  re-recording `tests/oracle/capture.json` (139 cases, 0 oracle errors) and
  `tests/compat/candidate.json`. No test could have caught this — notes are metadata and
  are never asserted.
- **README's baseline "Verified" stamp named a file that cannot exist:** it asserted
  `bench/results/baseline.json` exists, but `.gitignore:41-43` ignores
  `bench/results/*.json` and un-ignores only `baseline.md`. Corrected to `baseline.md`.

### Fused-path bypass, matcher differential, gate alignment (2026-09-21, later pass)

- **`FASTGLOB_NO_FUSED` (new, test affordance):** the fused fast path had no
  bypass, so the verbatim CPython port was UNREACHABLE for fused shapes and its
  equivalence to the fused walk was asserted by nothing — the compat suite cannot
  isolate either implementation. A non-empty value now disables the fused path for
  the whole process (latched once via `OnceLock`; inert when unset; no-op under
  `--dir-fd`, where `try_fast` already returns `None`). New
  `tests/test_fused_differential.py` (9 tests) drives the CLI in separate
  subprocesses and compares Counter multisets, and proves the switch is wired via
  the one input that discriminates the two paths (a self-referential symlink
  cycle: fused 148 vs verbatim 41, non-cycle core identical).
- **Matcher verification is now committed and reproducible:** `matcher.rs` cited
  a "400k differential … via `out/dev/diff_match.py`" as its evidence, but `out/`
  is gitignored and that file does not exist — the claim could not be re-executed
  by any reader. Replaced by `tests/test_match_bytes_oracle.py`: 59,860 exhaustive
  + 20,000 seeded-random **str** pairs and 4,000 ASCII **bytes** pairs, all with
  **0 mismatches** against the true stdlib `fnmatch.fnmatchcase`.
- **`match()` in BYTES mode diverges from `fnmatchcase` in one bounded region —
  declared and pinned, deliberately NOT "fixed":** the engine decodes bytes as
  `os.fsdecode` (UTF-8 + surrogateescape, shared with the `glob()` walk) while
  stdlib decodes bytes patterns as latin-1. Declared in
  `docs/compatibility-contract.md` §8.10 with the measured boundary: divergence is
  a strict SUBSET of paths containing a valid multi-byte UTF-8 sequence (0
divergences outside it), via TWO mechanisms — character count
  (`match(b"?", b"\xc3\xa9")` -> True, stdlib -> False) and class
  membership/ordering (`match(b"*[\xc0-\xc3]*", b"\xc3\xa9")` -> False, stdlib ->
  True, with `*` absorbing the count gap). `glob()` is unaffected (verified).
  Re-pointing `decode_chars` at latin-1 would corrupt `glob()`'s non-ASCII
  handling, which is the primary surface, so this is documented rather than
  changed.
- **`cargo fmt --check` and `cargo clippy -D warnings` were missing from
  `make test`:** a locally green `make test` could coexist with a red CI. New
  `lint` target runs exactly CI's flags and `test` now depends on it; the full
  gate matrix (including what deliberately remains CI-only and why) is documented
  in the `Makefile` header. `make test` also runs `compare.py --self-test` and the
  matcher differential, and CI runs the shim suite plus both differentials.
- **CI could not pass the compat gate by construction:** `setup-python` was pinned
  to `3.12.13` while the committed oracle capture records `_meta.python = 3.12.3`,
  and `check_oracle_freshness` requires an EXACT interpreter match — so the compat
  job raised before comparing anything. Pin corrected to `3.12.3` in both jobs;
  stale `3.12.13` labels in `tests/fixtures/generate.py`, `tests/test_package.py`
  and the committed case annotations corrected, and the oracle re-captured.
- **Doc citations re-measured:** `matcher.rs` citations were stale by 13-21 lines
  in README / `docs/api.md` / `docs/architecture.md` / `docs/cli-reference.md`
  (`compile` cited at `:177`/`:184`, actual `:208`; `escape`/`has_magic` at
  `:515-543`, actual `:547-569`); `walk.rs` 1689 → **1717**;
  `python/fastglob/__init__.py` 327 → **462** (and its "shell-out, F_DUPFD"
  description removed — the package dropped the subprocess transport in 0.1.2);
  `walk.rs:1-819` → `:1-1717`.

### Maintenance pass 2026-09-21 (measured, evidence in `.maat/2026-09-21/`)

- **Shim `__all__` (MAJOR, live bug):** `from glob import *` raised
  `AttributeError: module 'glob' has no attribute 'match'` because the shim
  adopted `fastglob.__all__` (which names `match`, an engine-package API) while
  binding only `glob/iglob/escape/has_magic`. `__all__` now mirrors the stdlib
  contract, so it is satisfiable by construction. New regression suite
  `tests/test_shim_parity.py` (5 tests) — verified to FAIL on the pre-patch shim
  (4 failures + 1 error) and pass after.
- **Gate wiring:** `tests/test_shim_loud.py` and `tests/test_shim_parity.py` were
  gated NOWHERE in CI; `compare.py --self-test` was in CI but missing from
  `make test`. Both are now wired (`make shim-test` added to CI, self-test added
  to the `test` target), so the shim surface and the harness self-check are
  gated in both places.
- **`cargo fmt --check` was RED on a clean clone:** `walk.rs` and `matcher.rs`
  contained code not formatted at the repo's `max_width = 100` (10 diff hunks), so
  CI's first step ("Format check") could not pass. Formatted; semantics unchanged
  (re-verified: 34 cargo tests, 139/139 compat, clippy clean).
- **CLI guard (`--root-dir=`):** the documented `--root-dir` / `--dir-fd` mutual
  exclusion was keyed on `root_dir.is_empty()`, so an explicitly-supplied empty
  `--root-dir=` aliased "unset" and silently bypassed the guard (exit 0 instead of
  exit 2). Now tracked by flag provenance. Also removed a dead `b"--"` match arm.
- **Compete coverage:** `cases.json` 133 → **139**, adding the fused fast path's
  two-literal-segment shape (`**/SEG1/SEG2`, previously **0** cases) plus
  multiplicity stress, with the oracle re-captured. r16/r18 cover the shape
  symlink-free and pass strictly; r17/r19 traverse the `symlinks/` cycle subtree
  and are declared `unspecified_zone` (same class as r12/r13) — the zone protocol
  proves the non-cycle core is exact (verified `zone_violations = NONE`), so the
  only divergence is cycle-redundant re-enumeration (§8.5a Level C).
- **Cycle-zone surface is broader than the 4 declared cases:** every top-level
  `**` pattern that reaches `symlinks/` is cycle-sensitive (measured: s09 oracle
  40 vs engine 141; r17 41 vs 138; r19 59 vs 156). Future case authors should
  declare `unspecified_zone` for such patterns.
- **Docs:** README / `docs/architecture.md` / `docs/api.md` corrected to measured
  values. The 2026-08-23 stamp claimed 33 cargo tests (actual 34), package 20/20
  (actual 27), `walk.rs` 819 lines (actual 1689 post-format), `matcher.rs` 724 (779),
  `lib.rs` 72 (79), `main.rs` 306 (339 post-format), `bench.py` 463 (486), oracle 3.12.13
  (actual 3.12.3), "capture recorded as uid 0" (actual uid 1001), and `match`
  "0.1.4" (still `0.1.3`, unreleased).
- **Deployment drift (operator action required):** the installed
  `/opt/fastglob-shim/glob.py` is not the repo's `shim/glob.py` (`diff | wc -l` =
  280) and lacks `FASTGLOB_SHIM_LOUD`. No drift guard exists. Not fixed here —
  deploying is an operator action and no deploy authorization was given.

- Usability pass: `make compat`/`make oracle-capture` regenerate the fixture
  tree before capture (git cannot preserve the `000` mode of
  `tests/fixtures/tree/errors/unreadable` nor the empty
  `tests/fixtures/tree/basic/empty` directory, so a tree restored from a
  fresh clone previously failed the oracle freshness gate).
- Compat comparison now normalizes each side's own fixture-tree root before
  the multiset comparison, so the absolute-pattern case (l05 `@ROOT@/...`)
  passes on any clone path, not only the capture-time path.
- CI: `setup-python` pinned to the oracle interpreter (the compat freshness gate
  requires an exact interpreter match) and the oracle is re-captured under the CI
  runner user (the §8.6 unreadable-dir fixture is uid-dependent by design).
  NOTE: the pin was set to `3.12.13`, which does NOT match the committed capture
  (`_meta.python = 3.12.3`) — corrected to `3.12.3` in the later pass above.
- Added `LICENSE` (MIT placeholder — operator to confirm holder), this
  `CHANGELOG.md`, and `.mcp.json.example`.
- `tests/compat/candidate.json` is now gitignored (run artifact of
  `make compat`; regenerates on every run and embeds machine-specific
  absolute paths).
