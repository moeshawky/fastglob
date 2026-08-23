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
