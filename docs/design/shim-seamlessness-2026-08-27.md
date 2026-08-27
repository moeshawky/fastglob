# Fast Glob — Shim Seamlessness & Repo-Consistency Audit (Design)

- **Author:** fglob-architect (design only — no source edits applied)
- **Date:** 2026-08-27
- **Scope:** Map intent vs reality for the transparent `glob` shim; audit seamless-proxy
  correctness across Python 3.12 (system) and 3.14 (uv venv); discover repo
  inconsistencies; produce an architecture-selection-table input and a remedy plan
  with file:line witnesses and evidence labels.
- **Iron Law honored:** ORACLE FIRST; NO DESIGN WITHOUT MEASUREMENT; ONE ENGINE,
  ONE CONTRACT; multiset (never ordered/set) comparison; no invented SLA/engine.
- **Permission note:** Only this design doc is written; `shim/`, `src/`, docs, and
  every other source file are unchanged (design-only mandate).

---

## 1. Runtime oracle identification (REQUIRED witnesses)

The oracle is the *installed* stdlib `glob`, identified at runtime — never hard-coded.
Because the shim is injected onto `PYTHONPATH` at every launch boundary, `import glob`
itself now returns the shim. The TRUE stdlib oracle is reachable via `gnu_glob` or by
loading stdlib with the shim dir stripped from `sys.path`.

### 3.12 (system)
```
$ command -v python3
/usr/bin/python3
$ python3 --version
Python 3.12.13
$ python3 -c 'import glob; print(glob.__file__)'
/opt/fastglob-shim/glob.py            # <-- the SHIM, not stdlib
$ python3 -c 'import gnu_glob; print(gnu_glob.__file__)'   # escape hatch (loads true stdlib)
/opt/fastglob-shim/gnu_glob.py        # reports shim path, but execs /usr/local/lib/python3.12/glob.py
# True stdlib oracle path: /usr/local/lib/python3.12/glob.py
# Verified: glob._engine == 'fastglob'  (wheel present in /usr/local/lib/python3.12/dist-packages/fastglob)
```

### 3.14 (uv venv — the operator's broken path)
```
$ /root/.local/share/uv/python/cpython-3.14.5-linux-x86_64-gnu/bin/python3 --version
Python 3.14.5
$ .../python3 -c 'import glob; print(glob.__file__)'
/opt/fastglob-shim/glob.py            # SHIM, engine == 'stdlib' (wheel ABSENT for 3.14)
# True stdlib oracle path: /root/.local/share/uv/python/cpython-3.14.5-linux-x86_64-gnu/lib/python3.14/glob.py
# Verified: glob._engine == 'stdlib'; pathlib.Path(...).glob('*.txt') works (no ImportError)
```

**Conclusion (OBSERVED):** The hotfix for the 3.14 `pathlib` `ImportError` WORKS —
but only because on 3.14 the shim has fallen back to stdlib (the `fastglob` wheel is
not installed for 3.14). Acceleration is therefore real on 3.12 and ABSENT on 3.14.

---

## 2. Intent vs reality map

| Layer | AGENTS.md intent (DOCUMENTED) | Current reality (OBSERVED) | Verdict |
|---|---|---|---|
| Engine | ONE Rust engine, fused single-pass, PyO3 in-process | Rust fused single-pass, PyO3 `_core` 0.1.2; `make compat` claims 133/133 | OK (doc-rot only) |
| Invisibility guarantee | 133-case multiset suite vs true stdlib | Candidate vs committed oracle is genuine (run_engine.py imports `fastglob` directly). **Oracle-regen + baseline are shim-corrupted** (see §4 E2/E3) | PARTIAL |
| Shim | `glob`-named module via PYTHONPATH/.pth/sitecustomize + gnu-glob escape hatch + auto-rollback | Deployed at `/opt/fastglob-shim`, injected via profile.d/PAM/bashrc; **auto-rollback unimplemented** | PARTIAL |
| 3.14 support | transparent acceleration for the interpreters agents use | 3.14 wheel missing -> falls back to stdlib (correct, not fast) | GAP |
| pathlib | agents "notice nothing" | pathlib uses stdlib `_StringGlobber` (correct) but is NOT accelerated | KNOWN LIMITATION |
| Docs | accurate to deployed state | README/architecture/api describe OLD subprocess arch + "deferred P1" + version 0.1.0 | DOC ROT |

---

## 3. Architecture selection table — input

The engine-selection question is **already settled** by ADR-001 (docs/architecture.md:111)
and re-verified here: no measurement demands a second production engine. The shim's
stdlib-fallback is the REQUIRED escape hatch, not a competing engine (no F-DUAL-ENGINE).

| Candidate | Correctness vs oracle | Speed | Role in design | Decision |
|---|---|---|---|---|
| stdlib `glob` (baseline + escape hatch) | Level A (reference) | 1.0x (baseline) | Oracle / fallback only | KEEP as oracle+fallback |
| Rust fused single-pass (PyO3 `_core` 0.1.2) | 133/133 (claimed) | 10–16x recursive (README) | ONE engine | SELECT (unchanged) |
| `fd` / `rg --files` / libc `glob()` / Python traversal | N/A (not the contract) | — | were rejected in ADR-001 | REJECT (no new evidence) |
| Shim stdlib-fallback path | = stdlib | 1.0x | safety net, not a product | KEEP (escape hatch only) |
| Dual engine (shim fast + shim stdlib both "production") | — | — | would violate §14 | REJECT (F-DUAL-ENGINE) |

**Recommendation:** ONE Rust engine + ONE thin proxy shim. Do **not** build a dual engine.
The defects found are in the DEPLOYMENT/MEASUREMENT layer, not the engine.

---

## 4. Evidence chain (E1–E11)

- **E1 — shim injection corrupts `import glob` everywhere (OBSERVED).**
  `/opt/fastglob-shim` is on `PYTHONPATH` via `/etc/environment:1`,
  `/etc/profile.d/99-fastglob.sh:6`, `~/.bashrc:111-115`, `~/.bash_noninteractive:8-12`.
  `python3 -c 'import glob; print(glob.__file__)'` returns `/opt/fastglob-shim/glob.py`
  for BOTH 3.12 and 3.14. Witness: see §1. **Implication:** any harness that does
  `import glob` to obtain the oracle/baseline actually gets the shim.

- **E2 — 3.14 not accelerated (OBSERVED).** On 3.14 `glob._engine == 'stdlib'`
  (witness: `python3.14 -c 'import glob; print(glob._engine)'`). Root cause: the
  0.1.2 wheel is only in `/usr/local/lib/python3.12/dist-packages/fastglob`; the 3.14
  uv venv (`/root/.local/share/uv/python/cpython-3.14.5-linux-x86_64-gnu`, and
  `/kaggle/working/capture-proxy/.venv`) has no wheel. Evidence label: OBSERVED.

- **E3 — oracle-capture harness is unsafe under shim (OBSERVED, HIGH).**
  `tests/oracle/capture.py:44` does `import glob as _glob`. In the deployed env this is
  the shim. The committed `capture.json` is currently **clean**
  (`_meta.glob_module == /usr/local/lib/python3.12/glob.py`, `_meta.python == 3.12.13`,
  `tree_manifest_sha256` present) — VERIFIED. But re-running `make oracle-capture` NOW
  would record shim/fastglob output as the "oracle" and silently invert the 133-case
  differential (false green / F-OVERCONFIDENCE). The freshness gate (compare.py:179-229)
  only compares the committed capture to the live interpreter+tree; it does NOT detect
  that the capture was made against the shim. Evidence label: VERIFIED (current clean) /
  HYPOTHESIS (re-capture would poison — follows directly from E1).

- **E4 — benchmark baseline corrupted (OBSERVED, HIGH).**
  `bench/bench.py:33` `import glob` is documented as the "IN-PROCESS baseline benchmark
  for Python stdlib `glob`" (docstring line 5). Under the shim this measures **fastglob**
  on 3.12 (`engine=='fastglob'`) or stdlib-fallback on 3.14 — NOT true stdlib. This
  inflates/confuses the speedup ratio and violates benchmark-discipline §10.1
  (no startup-cost misrepresentation; baseline must be the real oracle). Evidence: see
  `bench/bench.py:33,50-51`. Label: OBSERVED.

- **E5 — candidate side is genuine (OBSERVED, reassuring).**
  `tests/compat/run_engine.py:35-36` inserts `REPO/python` and `import fastglob` (the
  compat package), NOT `glob`. So the 133/133 green compares fastglob vs the committed
  (clean) oracle — valid today. `python/fastglob/_core.abi3.so` exists (501 KB, built
  today), so the candidate harness is viable. Label: VERIFIED.

- **E6 — public-signature parity (OBSERVED, reassuring).**
  `fastglob.glob`/`iglob` expose `pathname, root_dir, dir_fd, recursive, include_hidden`
  — the parameter SET equals stdlib (inspect.signature strings differ only by type
  annotations: `_PathArg` vs bare). So the shim's `glob/iglob/escape/has_magic` hand-off
  is seamless; no blocker. `escape`/`has_magic` also match. Label: VERIFIED.

- **E7 — `translate` spurious `None` on 3.12 (OBSERVED, MEDIUM).**
  `shim/glob.py:68`: `translate = getattr(_fg, "translate", getattr(_stdlib_glob, "translate", None))`.
  `fastglob` has no `translate` (dir(fastglob) lacks it) and 3.12 stdlib has no
  `translate` -> `glob.translate is None`. stdlib 3.12 raises `AttributeError` on
  `glob.translate`. Divergence: spurious attribute. On 3.13+ it correctly falls back to
  stdlib `translate` (present). Label: OBSERVED.

- **E8 — private-symbol proxy is sound (OBSERVED, reassuring).**
  On 3.14, `glob._StringGlobber`, `glob._no_recurse_symlinks`, `glob._PathGlobber` are
  all present (eager copy at `shim/glob.py:76-83` + `__getattr__` at `:86-93`).
  Critically, `pathlib` imports `_StringGlobber` whose `scandir`/`lexists` are
  `staticmethod`s bound to `os.scandir`/`os.path.lexists` (3.14 glob.py:545-560) — they
  do NOT delegate to module-level `glob.glob`, so pathlib stays correct regardless of
  engine. The hotfix resolves the reported ImportError. Label: VERIFIED.

- **E9 — `shim/` source not version-controlled (OBSERVED, MEDIUM).**
  `git status` shows `shim/` untracked; `.gitignore` ignores `python/fastglob/*.so`
  (line 50) but NOT `shim/`. `diff -q shim/glob.py /opt/fastglob-shim/glob.py` => exit 0
  (byte-identical today). Risk: future edits to `/opt` can silently diverge from the
  repo. Label: VERIFIED.

- **E10 — doc/reality rot (OBSERVED, MEDIUM).**
  `README.md:197` "Python shim … shells out per call (subprocess.run…)" — stale; actual
  is in-process PyO3 `_core`. `README.md:231` "Planned transparent layer (deferred P1) …
  Not yet implemented" — contradicts the live deploy. `docs/architecture.md:20,103,105`
  describe subprocess shell-out + "deferred P1". `docs/api.md:9,104` say version 0.1.0;
  actual `python/pyproject.toml:41` and `src/Cargo.toml:6` are 0.1.2 (VERIFIED).
  `docs/api.md:131` "RuntimeError if binary exit≠0" — subprocess-era, stale.
  Label: OBSERVED/VERIFIED.

- **E11 — auto-rollback unimplemented (OBSERVED, MEDIUM).**
  AGENTS.md requires "auto-rollback to stdlib on suite failure (never an unverified
  default)". `Makefile:34-37` (`compat`) rebuilds fixtures + runs candidate vs committed
  oracle but has NO shim-deploy or rollback target. The shim only falls back at import
  time if `import fastglob` fails (engine missing) — NOT if fastglob returns wrong
  results. Label: OBSERVED.

---

## 5. Seamlessness assessment of the current shim

**Correct (keep):**
- Eager copy + PEP 562 `__getattr__`/`__dir__` (`shim/glob.py:76-93`) proxies all stdlib
  attributes, including future private names — fixes the 3.14 `pathlib` ImportError (E8).
- `glob`/`iglob`/`escape`/`has_magic` hand-off matches stdlib signatures (E6).
- `gnu_glob` escape hatch loads true stdlib (E1).

**Defects to fix (see §7 R1–R7):**
- Harness neutrality (E3, E4) — the shim measures itself.
- 3.14 wheel rollout (E2).
- `translate=None` pollution on <3.13 (E7).
- `shim/` not tracked (E9); doc rot (E10); no rollback automation (E11).
- `gnu_glob.__file__` cosmetic misreport (minor; gnu_glob execs true stdlib but keeps
  shim `__file__`).

---

## 6. Design recommendation

Keep the **ONE Rust engine** and the **thin proxy shim**; do NOT build a dual engine.
The shim design (eager copy + `__getattr__`) is the correct seamless-proxy shape.
Remediate the deployment/measurement gaps with minimal, targeted changes. The single
load-bearing invariant for measurement integrity:

> Any code that needs the ORACLE or the BASELINE must load the TRUE stdlib glob, never
> `import glob` (which is the shim). Provide one helper, `load_true_stdlib_glob()`,
> that strips the shim dir from `sys.path` and uses `importlib.util` to load the real
> `glob` (this is exactly the technique already in `shim/glob.py:26-47` and
> `shim/gnu_glob.py:10-24`). Use it in `capture.py`, `bench.py`, and future harnesses.

This is the cleanest fix and satisfies the oracle-first iron law at the harness layer.

### 6.1 Architecture selection table (final)

| Item | Selected | Rejected | Evidence |
|---|---|---|---|
| Engine | ONE Rust fused single-pass (PyO3 0.1.2) | fd/rg/libc/Python/dual-engine | ADR-001; no new evidence (§3) |
| Shim shape | eager copy + PEP562 proxy | explicit per-name allowlist | E8 (works for 3.14 + future privates) |
| Shim fallback | stdlib (escape hatch) | second production engine | §3 dual-engine row |
| Oracle/baseline load | `load_true_stdlib_glob()` (path-strip+importlib) | `import glob` in harnesses | E3, E4 |
| Wheel rollout | all target interpreters (abi3) | 3.12-only | E2 |

---

## 7. Remedy plan (design; file:line + evidence labels)

| ID | File:line (witness) | Severity | Recommended fix | Evidence |
|---|---|---|---|---|
| R1 | `tests/oracle/capture.py:44` (+`:96` glob_module stamp) | HIGH | Replace `import glob as _glob` with `import gnu_glob as _glob` OR call `load_true_stdlib_glob()` (path-strip + importlib, mirroring `shim/glob.py:26-47`). Stamp `glob_module` from the loaded module's true `__file__`. | E3 OBSERVED |
| R2 | `bench/bench.py:33` (+`:50-51`) | HIGH | Same fix: load true stdlib as the baseline; keep candidate side importing `fastglob`. Re-label docstring "true stdlib glob (shim-neutralized)". | E4 OBSERVED |
| R3 | `shim/glob.py:68` | MEDIUM | Only assign `translate` when the resolved value is not `None`; otherwise `del`/leave undefined so stdlib-matching `AttributeError` propagates on <3.13. Apply same guard to any future deferred public name. | E7 OBSERVED |
| R4 | `Makefile:34-37` | MEDIUM | Add `deploy`/`rollback` targets: build wheel → `make compat` → on success install shim+wheel into all interpreters and set PYTHONPATH; on failure remove `/opt/fastglob-shim` from injection points (roll back to stdlib). Never swap on a red suite. | E11 OBSERVED |
| R5 | `.gitignore:50` (note) + `git status` (`shim/` untracked) | MEDIUM | `git add shim/`; treat `shim/glob.py` + `shim/gnu_glob.py` as the canonical source; deploy = copy to `/opt/fastglob-shim`. Do NOT gitignore `shim/`. | E9 VERIFIED |
| R6 | `README.md:197,231`; `docs/architecture.md:20,103,105`; `docs/api.md:9,104,131` | MEDIUM | Regenerate to: PyO3 in-process `_core`, version 0.1.2, shim DEPLOYED (not deferred P1); drop subprocess/`binary exit` language; correct version strings. | E10 OBSERVED/VERIFIED |
| R7 | `docs/compatibility-contract.md` (add note) | LOW | Document that `pathlib.Path.glob()` uses stdlib `_StringGlobber` (correct, self-contained) and is therefore NOT accelerated by fastglob — a known, acceptable limitation of the transparent layer. | E8 VERIFIED |
| R8 | `shim/gnu_glob.py:1-78` (minor) | LOW | Set `gnu_glob.__file__ = _mod.__file__` after load for truthful diagnostics. | E1 OBSERVED |

**Falsification condition (from CONVERGE):** if, after R1, a freshly re-captured oracle
differs from the committed `capture.json` beyond the cycle-zone tolerance, the engine
has a hidden compatibility gap — investigate before shipping (do not mask).

---

## 8. Verification matrix for the remediation

```
# R1/R2 — harness neutrality (must show TRUE stdlib, not shim)
python3 -c 'import gnu_glob, glob; print(gnu_glob.__file__)'     # still shim path, but execs stdlib
# After R1: capture.py must stamp glob_module == /usr/local/lib/python3.12/glob.py (or 3.14 equiv)
python3 tests/oracle/capture.py && python3 -c 'import json;print(json.load(open("tests/oracle/capture.json"))[0]["glob_module"])'
#   expected: /usr/local/lib/python3.12/glob.py  (NOT /opt/fastglob-shim/glob.py)
# After R2: bench.py baseline must time true stdlib (compare against a manual `import gnu_glob` baseline)

# R3 — translate purity on 3.12
python3 -c 'import glob; print(hasattr(glob,"translate"))'        # expected: False (AttributeError on access)
# On 3.14: expected True and callable (stdlib translate)

# R4 — rollback
make deploy   # only swaps if `make compat` is green; `make rollback` restores stdlib

# R5 — shim tracked
git status --short shim/   # expected: tracked (A  shim/glob.py, A shim/gnu_glob.py)

# Full gate
make compat    # 133 executed / 133 passed / 0 failed (candidate vs TRUE stdlib oracle)
make test      # cargo test + compat + python package
make bench     # in-process (true stdlib baseline) + end-to-end, separate tables
```

---

## 9. Acceptance evidence labels (per claim)

- "Hotfix resolves 3.14 pathlib ImportError": **VERIFIED** (E8; 3.14 `pathlib.glob` works).
- "Acceleration real on 3.14": **FALSE / OBSERVED-GAP** (E2; engine=stdlib on 3.14).
- "133/133 green is genuine": **VERIFIED for candidate-vs-committed-oracle** (E5);
  **AT RISK for oracle re-capture** (E3) and **baseline** (E4).
- "Shim proxy seamless for glob/iglob/escape/has_magic": **VERIFIED** (E6).
- "One engine, no dual engine": **VERIFIED**, design preserved (§3, §6.1).
- "Docs match reality": **FALSE / OBSERVED** (E10).
- "Auto-rollback implemented": **FALSE / OBSERVED** (E11).

*No source files were modified. This document is the deliverable; remedies R1–R8 are
proposed designs with file:line witnesses, not applied changes.*
