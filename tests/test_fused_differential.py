"""Fused vs verbatim differential test.

Drives the compiled fastglob CLI binary with FASTGLOB_NO_FUSED unset (fused
fast path) and set to "1" (verbatim CPython port), asserting Counter
multiset equality for a matrix of patterns over a purpose-built tree.

The switch is latched once per process (walk.rs OnceLock), so each run
must be a separate subprocess invocation.

Required sections:
1. Per-pattern strict-equality test (fused == verbatim multisets).
2. Bypass inert when unset (fused run 1 == fused run 2).
3. Bypass actually wired (prove switch is not silently ignored).
4. Temp tree cleanup.

Symlink cycles: EXCLUDED from the strict matrix. The fused path bounds
cycle expansion structurally (`FAST_MAX_FD_DEPTH` = 256); the verbatim port
relies on kernel ELOOP, so the two enumerate different cycle depths.
Divergence is declared Level C in docs/compatibility-contract.md §8.5a and is
CYCLE-ONLY (the non-cycle core is identical). A self-referential cycle is
therefore also the only known input that DISCRIMINATES the two code paths —
which is what makes it the honest basis for the "switch is wired" proof in
Section 3. On ordinary trees the two paths are indistinguishable (measured: no
divergence on tests/fixtures/tree, on a 300-level-deep tree, or on a
7,000-file wide tree), so an equality assertion there proves nothing.

Anti-vacuity: every strict row must match at least one path. Empty multisets
compare equal, so a pattern that silently matches nothing (or an invocation
that exits non-zero) would otherwise "pass". `run_fastglob` raises on a
non-zero exit for the same reason: an earlier revision passed the non-existent
`--no-recursive` flag, which exited 2 and emptied 3 of 17 rows.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from collections import Counter

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(REPO, "src", "target", "release", "fastglob")


def build_tree(tmpdir):
    """Create a purpose-built fixture tree."""
    # Deep nesting
    os.makedirs(os.path.join(tmpdir, "a", "b", "c", "d"), exist_ok=True)
    # Hidden dirs and files
    os.makedirs(os.path.join(tmpdir, ".hidden_dir"), exist_ok=True)
    os.makedirs(os.path.join(tmpdir, "a", ".dot"), exist_ok=True)
    # Vendor-style subtrees
    os.makedirs(os.path.join(tmpdir, "vendor", "lib"), exist_ok=True)
    os.makedirs(os.path.join(tmpdir, "node_modules", "pkg"), exist_ok=True)
    # Files at various depths
    with open(os.path.join(tmpdir, "m.py"), "w") as f:
        f.write("")
    with open(os.path.join(tmpdir, "a", "b.py"), "w") as f:
        f.write("")
    with open(os.path.join(tmpdir, "a", "b", "c.py"), "w") as f:
        f.write("")
    with open(os.path.join(tmpdir, "a", "b", "c", "d.py"), "w") as f:
        f.write("")
    with open(os.path.join(tmpdir, ".hidden_dir", "h.py"), "w") as f:
        f.write("")
    with open(os.path.join(tmpdir, "a", ".dot", "x.py"), "w") as f:
        f.write("")
    with open(os.path.join(tmpdir, "vendor", "lib", "v.py"), "w") as f:
        f.write("")
    with open(os.path.join(tmpdir, "node_modules", "pkg", "n.py"), "w") as f:
        f.write("")
    # Two-literal-segment targets (shape **/SEG1/SEG2 — zero coverage in cases.json)
    os.makedirs(os.path.join(tmpdir, "sub", "dir"), exist_ok=True)
    with open(os.path.join(tmpdir, "sub", "dir", "target.txt"), "w") as f:
        f.write("")
    os.makedirs(os.path.join(tmpdir, "a", "sub", "dir"), exist_ok=True)
    with open(os.path.join(tmpdir, "a", "sub", "dir", "target.txt"), "w") as f:
        f.write("")
    # Special names: spaces, non-ASCII, glob chars, newline
    with open(os.path.join(tmpdir, "file with space.txt"), "w") as f:
        f.write("")
    with open(os.path.join(tmpdir, "file_star*.txt"), "w") as f:
        f.write("")
    with open(os.path.join(tmpdir, "file_question?.txt"), "w") as f:
        f.write("")
    with open(os.path.join(tmpdir, "file_brack[et].txt"), "w") as f:
        f.write("")
    # Broken symlink
    os.symlink(os.path.join(tmpdir, "no_such_target"), os.path.join(tmpdir, "broken_link"))
    # NOTE: deliberately NO symlink cycle in this tree. A cycle reachable from
    # the strict-matrix patterns (`**`, `**/*.py`) would inject the Level C
    # depth divergence (§8.5a) into rows that are supposed to be cycle-free,
    # turning the strict matrix red for a declared difference. Cycles live in
    # their own tree — see `build_cycle_tree`.


def build_cycle_tree(tmpdir):
    """A self-referential symlink cycle, isolated from the strict matrix.

    `sub/up -> ..` makes the tree its own ancestor, so `**/*.py` walks it
    repeatedly. The fused path bounds this structurally (`FAST_MAX_FD_DEPTH`),
    the verbatim port relies on kernel ELOOP — declared Level C (§8.5a), and
    the only known input that DISCRIMINATES the two code paths.
    """
    os.makedirs(os.path.join(tmpdir, "sub"), exist_ok=True)
    with open(os.path.join(tmpdir, "sub", "f.py"), "w") as f:
        f.write("")
    os.symlink("..", os.path.join(tmpdir, "sub", "up"))


def run_fastglob(tmpdir, pattern, recursive=True, include_hidden=False, fused=True,
                 env_extra=None):
    """Run the fastglob CLI binary with given options.

    `recursive` maps to the PRESENCE of `--recursive`; the CLI has only
    `--recursive` (opt-in) and NO `--no-recursive`, so an unknown option must
    never be quietly tolerated. A non-zero exit therefore RAISES: the CLI
    reports misuse on stderr and exits 2, and swallowing that would compare two
    empty Counters and report a pass.

    Returns a Counter of output lines (multiset).
    """
    args = [CLI]
    if recursive:
        args.append("--recursive")
    if include_hidden:
        args.append("--include-hidden")
    args.append(pattern)

    env = dict(os.environ)
    if not fused:
        env["FASTGLOB_NO_FUSED"] = "1"
    else:
        env.pop("FASTGLOB_NO_FUSED", None)
    if env_extra:
        env.update(env_extra)

    r = subprocess.run(args, cwd=tmpdir, env=env, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise AssertionError(
            f"CLI exited {r.returncode} for pattern {pattern!r} "
            f"(recursive={recursive}, include_hidden={include_hidden}, fused={fused}); "
            f"stderr={r.stderr!r}. A non-zero exit is a TEST BUG, not a comparison."
        )
    lines = r.stdout.splitlines() if r.stdout else []
    return Counter(lines)


class FusedDifferential(unittest.TestCase):
    """Differential test: fused fast path vs verbatim CPython port."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory(prefix="fastglob_fused_diff_")
        cls.tmp = cls.tmpdir.name
        build_tree(cls.tmp)
        # Cycles live in a SEPARATE tree so strict-matrix patterns cannot reach
        # them (see build_tree / build_cycle_tree).
        cls.cycdir = tempfile.TemporaryDirectory(prefix="fastglob_fused_cycle_")
        cls.cyc = cls.cycdir.name
        build_cycle_tree(cls.cyc)

    @classmethod
    def tearDownClass(cls):
        cls.tmpdir.cleanup()
        cls.cycdir.cleanup()

    # --- Section 1: Per-pattern strict equality ---

    def _assert_equal(self, pattern_id, pattern, fused_out, verbatim_out,
                      recursive=True, include_hidden=False):
        """Compare fused and verbatim outputs as Counter multisets."""
        if fused_out != verbatim_out:
            diff_keys = set(fused_out) ^ set(verbatim_out)
            # `sum(Counter)` would sum the string KEYS — use .values().
            print(f"  Pattern {pattern_id}: {pattern}")
            print(f"  Fused ({sum(fused_out.values())}): {sorted(fused_out)}")
            print(f"  Verbatim ({sum(verbatim_out.values())}): {sorted(verbatim_out)}")
            print(f"  Diff keys: {diff_keys}")
        self.assertEqual(
            fused_out, verbatim_out,
            f"Pattern {pattern_id} ({pattern!r}): fused != verbatim "
            f"(recursive={recursive}, include_hidden={include_hidden})"
        )
        # Anti-vacuous: two empty multisets compare equal and prove nothing.
        self.assertGreater(
            sum(fused_out.values()), 0,
            f"Pattern {pattern_id} ({pattern!r}) matched NOTHING in fused mode "
            f"(recursive={recursive}, include_hidden={include_hidden}) — an empty "
            f"comparison is vacuous and is a TEST BUG, not a pass"
        )
        print(f"  PASS {pattern_id}: {pattern!r} ({sum(fused_out.values())} matches)")

    def test_literal_suffix_forms(self):
        """Literal-suffix forms: **/*.py, **/*.txt, **/m.py"""
        for pid, pat in [("ls1", "**/*.py"), ("ls2", "**/*.txt"), ("ls3", "**/m.py")]:
            fused = run_fastglob(self.tmp, pat)
            verbatim = run_fastglob(self.tmp, pat, fused=False)
            self._assert_equal(pid, pat, fused, verbatim)

    def test_two_literal_segment_form(self):
        """Two-literal-segment form **/SEG1/SEG2 — zero coverage in cases.json."""
        tests = [
            ("t2s1", "**/sub/dir"),             # bare SEG1/SEG2: the uncovered shape
            ("t2s2", "**/sub/dir/target.txt"),  # SEG1/SEG2 + literal file suffix
        ]
        for pid, pat in tests:
            fused = run_fastglob(self.tmp, pat)
            verbatim = run_fastglob(self.tmp, pat, fused=False)
            self._assert_equal(pid, pat, fused, verbatim)

    def test_prefix_starstar(self):
        """PREFIX**/SUFFIX and PREFIX**/SEG1/SEG2 forms."""
        tests = [
            ("pp1", "a/**/*.py"),
            ("pp2", "a**/*.py"),
            ("pp3", "**/sub/dir/*.txt"),
        ]
        for pid, pat in tests:
            fused = run_fastglob(self.tmp, pat)
            verbatim = run_fastglob(self.tmp, pat, fused=False)
            self._assert_equal(pid, pat, fused, verbatim)

    def test_bare_starstar(self):
        """Bare **, **/**, **/**/*.py (overlapping-** multiplicity)."""
        tests = [
            ("bs1", "**"),
            ("bs2", "**/**"),
            ("bs3", "**/**/*.py"),
        ]
        for pid, pat in tests:
            fused = run_fastglob(self.tmp, pat)
            verbatim = run_fastglob(self.tmp, pat, fused=False)
            self._assert_equal(pid, pat, fused, verbatim)

    def test_include_hidden(self):
        """--include-hidden variants and hidden-name patterns."""
        tests = [
            ("ih1", "**/*.py", True),
            ("ih2", ".hidden_dir/**/*.py", True),
            ("ih3", "a/.dot/**/*.py", True),
        ]
        for pid, pat, inc in tests:
            fused = run_fastglob(self.tmp, pat, include_hidden=inc)
            verbatim = run_fastglob(self.tmp, pat, fused=False, include_hidden=inc)
            self._assert_equal(pid, pat, fused, verbatim, include_hidden=inc)

    def test_single_component_magic(self):
        """Single component with magic (case a)."""
        tests = [
            ("sc1", "*.py"),
            ("sc2", "*"),
            ("sc3", "file_*.txt"),
        ]
        for pid, pat in tests:
            fused = run_fastglob(self.tmp, pat, recursive=False)
            verbatim = run_fastglob(self.tmp, pat, recursive=False, fused=False)
            self._assert_equal(pid, pat, fused, verbatim, recursive=False)

    # --- Section 2: Bypass inert when unset ---

    def test_bypass_inert_when_unset(self):
        """FASTGLOB_NO_FUSED unset: two identical fused runs must match."""
        fused1 = run_fastglob(self.tmp, "**/*.py")
        fused2 = run_fastglob(self.tmp, "**/*.py")
        self.assertEqual(
            fused1, fused2,
            "Two fused runs without FASTGLOB_NO_FUSED must be identical"
        )
        print(f"  PASS bypass_inert: fused run1 == fused run2 ({sum(fused1.values())} matches)")

    # --- Section 3: Bypass actually wired ---

    def test_bypass_is_wired(self):
        """Prove the switch changes the CODE PATH, not merely the environment.

        An earlier revision asserted `fused == verbatim` and printed "proves
        switch is wired" — which is exactly backwards: equality is what you
        would see if the switch were silently ignored. On ordinary trees the
        two paths are genuinely indistinguishable, so equality there cannot
        discriminate, and that revision's claim was unsupported.

        A self-referential symlink cycle DOES discriminate: the fused path
        bounds cycle expansion structurally (`FAST_MAX_FD_DEPTH` = 256) while
        the verbatim port relies on kernel ELOOP, so their enumeration depth
        differs (declared Level C, contract §8.5a). This test asserts the
        divergence EXISTS, that it is CYCLE-ONLY, and that the default run
        (var unset) equals fused — which can only hold if the switch works.
        """
        fused = run_fastglob(self.cyc, "**/*.py")
        verbatim = run_fastglob(self.cyc, "**/*.py", fused=False)
        default = run_fastglob(self.cyc, "**/*.py")

        # 1. The discriminator must actually discriminate.
        self.assertNotEqual(
            sum(fused.values()), sum(verbatim.values()),
            "expected the fused path to bound cycle depth differently from the "
            "verbatim port (contract §8.5a). If they now agree, this test can no "
            "longer prove the switch is wired and a different discriminator is "
            "required — do NOT weaken this to an equality assertion."
        )
        self.assertTrue(sum(fused.values()) > 0 and sum(verbatim.values()) > 0,
                        "both modes must produce output on the cycle tree")

        # 2. The switch must be INERT when unset (default == fused).
        self.assertEqual(fused, default,
                         "FASTGLOB_NO_FUSED unset must reproduce the fused result")

        # 3. The divergence must be cycle-only: the non-cycle core is identical.
        def core(counter):
            return Counter({k: n for k, n in counter.items()
                            if not k.startswith("sub/up/")})

        self.assertEqual(
            core(fused), core(verbatim),
            "non-cycle core must be identical in both modes (the divergence is "
            "declared cycle-only in contract §8.5a)"
        )
        print(f"  PASS bypass_wired: cycle discriminates the code path "
              f"(fused={sum(fused.values())} vs verbatim={sum(verbatim.values())}, "
              f"non-cycle core identical)")

    # --- Symlink cycle: lenient check ---

    def test_symlink_cycle_lenient(self):
        """Symlink cycle: fused and verbatim may differ in cycle depth.

        Both must complete and visit 'cycle/self' at least once. Do not
        assert strict equality (Level C divergence declared).
        """
        fused = run_fastglob(self.cyc, "**/*.py")
        verbatim = run_fastglob(self.cyc, "**/*.py", fused=False)

        # Both must visit the cycle's own file (never assert equality here).
        self.assertTrue(any(line.endswith("sub/f.py") for line in fused),
                        "fused mode must visit the cycle's own file")
        self.assertTrue(any(line.endswith("sub/f.py") for line in verbatim),
                        "verbatim mode must visit the cycle's own file")
        print(f"  PASS symlink_cycle: fused={sum(fused.values())} "
              f"verbatim={sum(verbatim.values())} (Level C divergence allowed)")


if __name__ == "__main__":
    print(f"CLI: {CLI}")
    print(f"Exists: {os.path.exists(CLI)}")
    print(f"Running from: {os.getcwd()}")
    print()
    unittest.main(verbosity=2)
