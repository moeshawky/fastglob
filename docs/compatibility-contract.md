# Compatibility Contract

*Verbatim doctrine extracted from AGENTS.md (§8, §9, §20, §21). AGENTS.md routes here; this file is the full text. Do not summarize a rule away — if a rule changes, change it here and keep the pointer honest.*

## 8. PYTHON COMPATIBILITY CONTRACT

Build the compatibility suite FROM THE REFERENCE IMPLEMENTATION.

Do not write expected path lists from memory when Python can provide the oracle directly.

Test at minimum:


### 8.1 Basic matching

Patterns:

    *
    ?
    [abc]
    [a-z]
    [!abc]

and combinations.


### 8.2 Literal path components

Test:

- literal filenames;
- literal directories;
- patterns with no matches;
- relative paths;
- absolute paths;
- repeated separators where relevant;
- trailing separators.


### 8.3 Hidden files

Fixtures must contain:

    visible
    .hidden
    visible_dir/
    .hidden_dir/

Verify reference behavior for:

- wildcard patterns;
- explicitly dot-prefixed patterns;
- recursive patterns;
- `include_hidden=False`;
- `include_hidden=True` where supported.


### 8.4 Recursive `**`

Test:

    **
    **/
    **/*
    **/*.py
    a/**/b
    a/**/*
    patterns containing more than one **

Verify:

- zero-directory matches;
- nested matches;
- directory-only patterns;
- duplicate result multiplicity.


### 8.5 Symlinks

Fixtures must contain:

- symlink to file;
- symlink to directory;
- broken symlink;
- symlink chain;
- symlink cycle.

Requirements:

- broken symlinks that the Python reference returns must be returned;
- recursive traversal through directory symlinks must be compared with the reference;
- cycles must never hang the compatibility suite.

DO NOT invent a requirement to reproduce a particular kernel ELOOP depth or number of repeated cycle paths unless this is shown to be a documented/required behavior.

Safety timeout tests are mandatory for cycles.

### 8.5a Cycle-zone protocol (Level C tolerance for symlink-cycle expansion)

NOTE (2026-09-21, measured): the cycle-zone surface is **pattern-shaped, not
case-shaped**. Any pattern whose top-level `**` can reach the `symlinks/`
subtree is cycle-sensitive, because cycle-expansion depth is Level C. Measured
on this fixture tree: `s09` oracle 40 vs engine 141 paths, `r17` 41 vs 138,
`r19` 59 vs 156 — all with an EXACT non-cycle core (`zone_violations` empty),
so the divergence is purely cycle-redundant re-enumeration. The originally
declared set (s08/s09/r12/r13) was therefore incomplete; cases `r17` and `r19`
were added 2026-09-21 and flagged `unspecified_zone`. When adding a `**` pattern
that is not confined to a symlink-free subtree, declare `unspecified_zone` —
otherwise a Level C accident is reported as a contract failure.

The oracle capture is a ONE-SHOT snapshot. Symlink-cycle expansion depth is
Level C: the ELOOP/SYMLOOP_MAX pruning boundary depends on walk (readdir)
order, and the oracle ITSELF is nondeterministic on it. Measured on this
machine (VERIFIED 2026-08-20, Level C):

- s09 `symlinks/**/v1.txt`: 20 oracle runs -> 40 paths x19, 34 paths x1;
- r12 `**`: 20 oracle runs -> 551 paths x19, 312 paths x1;
- s08 `symlinks/**`: 30 oracle runs -> 492 paths x30 (modal; may drift under
  tree-state change — e.g. the transient case rmtrees `tree/errors/_transient`
  each pass, perturbing ext4 readdir order, and the engine's ELOOP boundary
  shifts with the same tree-state changes).

Strict multiset equality on cycle-expanded multiplicity is therefore
F-ORACLE-OVERFIT for cases flagged `unspecified_zone: true` in
tests/fixtures/cases.json (s08, s09, r12, r13). The fixture's only DIRECTORY
cycle is `symlinks/cycle/self -> ..`, so every cycle-redundant re-enumeration
carries repeated `cycle/self/` segments (the `mutual/x <-> y` links are FILE
symlinks and never expand).

A zone case PASSES iff ALL hold:

1. (a) TERMINATES — the 10 s timeout guard in tests/compat/run_engine.py
   (a timeout is an error record and fails via error-semantics comparison);
2. (b) NO FOREIGN PATHS — for every candidate path, removing all
   `cycle/self/` segments (`strip_cycle(p) = re.sub(r'(cycle/self/)+', '', p)`)
   yields a base that EXISTS in the capture's stripped path set. No candidate
   path may have no stripped base in the capture;
3. (c) NON-CYCLE CORE EXACT — after REMOVING from both multisets every path
   containing `cycle/self/`, the two Counters are exactly equal. This pins
   every real file/dir multiplicity to the oracle exactly; only
   cycle-redundant re-enumerations are zone-tolerated.

Zone passes are reported as `PASS <id> (<section>) [zone]` and count as
passed in the `133 executed / N passed / M failed` summary. Zone failures
use the visible classification `zone_violation` with the offending paths in
the §21 report. Zone membership is the union of the oracle and candidate
record flags (the capture predates the s09 flag and is not re-captured).

Rationale: ELOOP boundary x readdir order makes single-snapshot strict
comparison of cycle multiplicity overfit to one walk order (§9: unspecified
behavior must not become a hard requirement; Level C artifacts must not be
fossilized).


### 8.6 Filesystem errors

Fixtures should test where possible:

- unreadable directories;
- disappearing files;
- disappearing directories;
- dangling symlinks.

Compare externally observable behavior with the reference.

Do not fabricate an error policy.


### 8.7 Pathological filenames

Include filenames containing:

- spaces;
- tabs;
- Unicode;
- leading `-`;
- quotes;
- glob metacharacters where legal;
- newline characters.

Programmatic APIs must preserve these exactly.

CLI `--null` must round-trip them safely.


### 8.8 root_dir / dir_fd / include_hidden

If supported by the installed Python reference, test them explicitly.

Do not silently omit newer API parameters because an earlier Python version did not have them.


### 8.9 escape / translate

If exposed by the installed reference version, build direct differential tests against them.

These functions may be separable from traversal performance.

Do not rewrite them unnecessarily if delegating to proven behavior is simpler and has negligible cost.


### 8.10 `match(pattern, path)` — bytes-mode decode boundary (MEASURED 2026-09-21)

`match` has two comparison modes, chosen by the pattern's type. **str** mode is
`fnmatch`-exact. In **bytes** mode one bounded divergence exists; it is declared and
pinned here, and must NOT be "fixed" without reading this section first.

**Mechanism.** The engine decodes bytes with `decode_chars`
(`src/fastglob/src/matcher.rs:54`): one code point per valid UTF-8 sequence, `0xDC00|b`
per invalid byte — the `os.fsdecode` model, shared with the `glob()` walk, which must
decode filesystem bytes the way Python does. stdlib `fnmatch._compile_pattern`
(`fnmatch.py:40-46`) instead decodes bytes patterns as **ISO-8859-1**: exactly one code
point per BYTE. On a path containing a valid multi-byte UTF-8 sequence the two models
disagree in **two independent ways**:

1. **Character count** — the constructs that count characters (`?`, `[seq]`, `[!seq]`)
   shift. `match(b"?", b"\xc3\xa9")` -> `True`; stdlib -> `False`.
2. **Class membership / ordering** — one model orders code points, the other orders
   bytes, so a byte RANGE can contain a byte of the sequence while containing none of
   its decoded code points. `match(b"*[\xc0-\xc3]*", b"\xc3\xa9")` -> `False` while
   stdlib -> `True`: the engine's class decodes to the surrogate range
   `U+DCC0..U+DCC3`, which never contains `U+00E9`, whereas stdlib compares the raw
   byte `0xC3` against `[0xC0..0xC3]`. The `*` wildcards absorb the count difference
   here, so count alone does NOT explain this one.

Mechanism 2 was not in the original count-only account of this divergence; it was
found by an independent falsification pass that broke that account, and it is pinned
by the witnesses above/below so it cannot be quietly forgotten again.

**Measured boundary** (exhaustive cartesian sweeps, `tests/test_match_bytes_oracle.py`):

| region | result |
|---|---|
| str mode | `fnmatch`-exact — 59,860 exhaustive + 20,000 random pairs, 0 mismatches |
| bytes mode, pure ASCII | `fnmatch`-exact — 4,000 pairs, 0 mismatches |
| bytes mode, invalid bytes only (`\xff`) | `fnmatch`-exact — same character count under both models |
| bytes mode, valid multi-byte UTF-8 present | **DIVERGENT** |

Pinned witnesses (engine vs stdlib `fnmatchcase(name=path, pat=pattern)`):

    match(b"?",          b"\xc3\xa9")  -> True    | fnmatchcase -> False
    match(b"??",         b"\xc3\xa9")  -> False   | fnmatchcase -> True
    match(b"[\xc3\xa9]", b"\xc3\xa9")  -> True    | fnmatchcase -> False
    match(b"*[\xc0-\xc3]*", b"\xc3\xa9") -> False  | fnmatchcase -> True
    match(b"*[\x80-\xff]*", b"\xc3\xa9") -> False  | fnmatchcase -> True

Divergence is a strict SUBSET of pairs whose path contains a valid multi-byte UTF-8
sequence — it never occurs otherwise. Measured: 126/126 divergences inside the region,
0 outside; of 1,813 multi-byte-name pairs, 1,687 still agreed because the pattern
often matches identically under either count.

**Why declared rather than repaired.** `decode_chars` is shared with the `glob()` walk.
Re-pointing it at latin-1 to buy verbatim `fnmatchcase` parity in bytes mode would
corrupt `glob()`'s non-ASCII handling — the primary surface — to fix a narrower
secondary one. The `os.fsdecode` model is the correct one for a filesystem glob engine;
`fnmatch`'s latin-1 choice is an artifact of its bytes-PATTERN support, not a property
of filenames.

**Status:** Level A (exact) for str mode and for bytes paths without valid multi-byte
UTF-8; declared bounded divergence for the remainder. Pinned by
`tests/test_match_bytes_oracle.py` — if the engine ever changes here, that test fails
loudly and this section must change with it.


### 8.11 `pathlib` is NOT accelerated (KNOWN LIMITATION — measured 2026-09-21)

`pathlib.Path.glob()` / `.rglob()` return correct results under the shim, but they do NOT
go through fastglob. This is a known, accepted limitation of the transparent layer, not a
compatibility defect: the engine accelerates stdlib `glob`, and `pathlib` is not
necessarily a consumer of `glob`.

**Mechanism — version-dependent. On 3.12, `pathlib` does not use `glob` at all.**

| interpreter | how `Path.glob` traverses | consumes `glob`? | accelerated? |
|---|---|---|---|
| **3.12** (measured here) | its own `_WildcardSelector` (`pathlib.py:190`), entry point `Path.glob` (`:1083`), driven by `path_cls._scandir` (`:167`) | **No** — `pathlib.py` contains no `glob` import | **No** |
| **3.14** (design record, E8) | `glob._StringGlobber`, imported from the shim-proxied `glob` | Yes — that one class | **No** |

On 3.14 the class is what keeps this safe: `_StringGlobber.scandir` / `.lexists` are bound
straight to `os.scandir` / `os.path.lexists`, so `Path.glob` never calls module-level
`glob.glob` and therefore never reaches the engine. Correctness holds in both rows; only
speed differs.

**Measured (2026-09-21, Python 3.12.3).** `Path.glob("**/*.txt")` over a two-file fixture
returns `['a.txt', 'sub/b.txt']` identically under the deployed shim
(`PYTHONPATH=/opt/fastglob-shim`), the repo shim (`shim/`), and no shim at all — so the
shim cannot break it either. 3.12 `glob.py` exposes privates `_dir_open_flags`, `_glob0`,
`_glob1`, `_glob2`, `_iglob`, `_isdir`, `_ishidden`, `_isrecursive`, `_iterdir`, `_join`,
`_lexists`, `_listdir`, `_rlistdir` with `__all__ = ['glob', 'iglob', 'escape']`;
**`_StringGlobber` does not exist before 3.13**. The shim's eager copy + PEP 562
`__getattr__` proxies those privates (verified: `_iterdir` and `_glob2` resolve through
both the repo and deployed shims) — that proxy is the mechanism 3.14 `pathlib` depends on.

**Not re-measured here:** the 3.14 row. It is the design record's finding
(`docs/design/shim-seamlessness-2026-08-27.md` E8, observed on 3.14.5); no 3.13/3.14
interpreter is reachable from uid 1001 on this box, so it is inherited as
OBSERVED-elsewhere rather than re-verified.

**Status:** correct but not accelerated. Do NOT "fix" this by making the shim rewrite
`pathlib`'s internals — returning correct results without engaging the engine is the
documented trade. Accelerating `pathlib` would be a new feature, not a compatibility gap.


## 9. UNSPECIFIED BEHAVIOR

Do NOT create compatibility requirements for behavior Python explicitly leaves unspecified.

Examples include:

- exact result ordering;
- whether a concurrently added file appears;
- whether a concurrently deleted matching path was observed before deletion;
- exact syscall ordering;
- exact internal recursion strategy.

Tests may observe these properties for diagnostic purposes.

They must not become hard requirements without operator approval.


## 20. TEST-FIRST ORACLE DEVELOPMENT

Before implementing the final engine:

1. create fixtures;
2. run Python stdlib `glob` against them;
3. capture structured oracle results;
4. build differential tests;
5. then implement candidate behavior.

Where output order is unspecified, compare multisets.

Where exact scalar behavior is defined, compare exactly.

Where the reference behavior itself is unspecified, do not fabricate an assertion.


## 21. DIFFERENTIAL TEST REPORT

For every failure report:

    pattern:
    options:
    fixture:
    Python result:
    candidate result:
    classification:
        documented incompatibility |
        implementation difference |
        ordering-only difference |
        duplicate-count difference |
        error-semantics difference |
        unknown

Zone cases (flag `unspecified_zone`, §8.5a) additionally fail as
`zone_violation` with the offending paths listed; the six categories above
remain the classifications for non-zone failures.

Do not reduce failures to "expected != actual" without classification.
