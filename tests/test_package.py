"""Z1 family E — fastglob PACKAGE-level adversarial tests (closes TP46).

Scope: python/fastglob/__init__.py public surface — ``glob``, ``iglob``,
``escape``, ``has_magic`` — driven against the real in-process engine
(``fastglob._core``, PyO3) exactly the way the compatibility harness drives
it.

Assertion discipline (Z1): behavioral depth only — full Counter multisets
with multiplicity (AGENTS.md iron law), per-element TYPE assertions for the
str/bytes contract (Ct38), exact values for escape(), and iterator-type
assertions for iglob(). Never "isinstance(result, list)" alone.

Real fault injection where the platform allows (no mocks):
* NUL in pattern        -> ValueError from the engine's NUL guard (stdlib
                            parity);
* dir_fd < 0            -> ValueError from the package guard;
* dir_fd wrong type     -> TypeError from the package guard;
* CLOSED fd             -> ValueError from the fstat guard (fd opened then
                            closed for real);
* OPEN REGULAR-FILE fd  -> passes the package's fstat check, is passed
                            in-process to the engine, which rejects it ->
                            RuntimeError("... fd is not a directory ...") — a
                            full end-to-end chain test of the misuse path;
* pattern > 8192 bytes / > 512 components -> engine exit 2 ->
                           RuntimeError("fastglob exited 2: ... pattern too
                           long") — exercises the length guards through the
                           whole stack.

Run wiring: `make test` executes this file after `make build` + compat (the
in-process engine module must be importable; a missing ``fastglob._core``
FAILS LOUDLY in setUpModule rather than silently skipping — build it with
`pip install -e python`, which compiles the extension in-place). Standalone:
PYTHONPATH-independent — this file inserts <repo>/python onto sys.path itself.
"""

import os
import shutil
import sys
import tempfile
import unittest
from collections import Counter
from collections.abc import Iterator
from inspect import isgenerator
from pathlib import Path

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "python"))

import fastglob  # noqa: E402  (path set above)


def setUpModule():
    """Fail loudly if the in-process engine module is unavailable (no silent skips)."""
    if not hasattr(fastglob._core, "glob"):
        raise RuntimeError(
            "fastglob._core not importable or incomplete — "
            "install the package (`pip install -e python`)"
        )


class TreeFixture(unittest.TestCase):
    """pid-safe temp fixture tree; addCleanup guarantees removal."""

    TOP_FILES = ["a.py", "b.txt", "we\nird.txt", "café-✓.txt"]
    # visible top level entries returned by '*' (dirs included, dotfiles not)
    EXPECTED_STAR = ["a.py", "b.txt", "we\nird.txt", "café-✓.txt", "sub"]

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="fastglob_pkg_test_")
        self.addCleanup(shutil.rmtree, self.root, True)
        for name in self.TOP_FILES:
            with open(os.path.join(self.root, name), "wb"):
                pass
        os.mkdir(os.path.join(self.root, "sub"))
        with open(os.path.join(self.root, "sub", "c.py"), "wb"):
            pass
        os.mkdir(os.path.join(self.root, ".hdir"))
        with open(os.path.join(self.root, ".hdir", "e.txt"), "wb"):
            pass


class GlobIglobMultisetContract(TreeFixture):
    """TP46 core: iglob consumption equals glob, both modes, exact content."""

    def test_glob_vs_iglob_counter_equal_str_mode(self):
        got = fastglob.glob("*", root_dir=self.root)
        lazy = list(fastglob.iglob("*", root_dir=self.root))
        self.assertEqual(Counter(got), Counter(lazy))
        self.assertEqual(Counter(got), Counter(self.EXPECTED_STAR))

    def test_glob_vs_iglob_counter_equal_bytes_mode(self):
        got = fastglob.glob(b"*", root_dir=self.root)
        lazy = list(fastglob.iglob(b"*", root_dir=self.root))
        self.assertEqual(Counter(got), Counter(lazy))
        want = {os.fsencode(n) for n in self.EXPECTED_STAR}
        self.assertEqual(Counter(got), Counter(want))

    def test_recursive_pattern_exact_multiset(self):
        got = fastglob.glob("**/*.py", recursive=True, root_dir=self.root)
        self.assertEqual(Counter(got), Counter(["a.py", "sub/c.py"]))

    def test_miss_returns_empty_list_not_exception(self):
        self.assertEqual(fastglob.glob("zzz_*.nope", root_dir=self.root), [])

    def test_newline_and_unicode_names_round_trip_exactly(self):
        # --null chain proof: names with \n survive byte-exactly (the reason
        # the package always uses --null internally)
        got = fastglob.glob("*", root_dir=self.root)
        self.assertIn("we\nird.txt", got)
        self.assertIn("café-✓.txt", got)


class TypePreservationContract(TreeFixture):
    """Ct38: the PATTERN decides str-vs-bytes; root_dir type independent."""

    def assert_all_type(self, seq, typ):
        for item in seq:
            self.assertIs(type(item), typ, f"element {item!r} is {type(item).__name__}")

    def test_str_pattern_returns_str_elements(self):
        got = fastglob.glob("*", root_dir=self.root)
        self.assertGreater(len(got), 0)
        self.assert_all_type(got, str)

    def test_bytes_pattern_returns_bytes_elements_with_str_root_dir(self):
        # mixed types are supported: only the pattern picks the result type
        got = fastglob.glob(b"*", root_dir=self.root)
        self.assertGreater(len(got), 0)
        self.assert_all_type(got, bytes)

    def test_iglob_item_types_follow_pattern_both_modes(self):
        self.assert_all_type(list(fastglob.iglob("*", root_dir=self.root)), str)
        self.assert_all_type(list(fastglob.iglob(b"*", root_dir=self.root)), bytes)

    def test_escape_type_and_value_preserved(self):
        out_s = fastglob.escape("a*b")
        self.assertIs(type(out_s), str)
        self.assertEqual(out_s, "a[*]b")
        out_b = fastglob.escape(b"a*b")
        self.assertIs(type(out_b), bytes)
        self.assertEqual(out_b, b"a[*]b")

    def test_escape_pathlike_follows_fspath_typing(self):
        out = fastglob.escape(Path("a*b"))  # str-fspath PathLike -> str
        self.assertIs(type(out), str)
        self.assertEqual(out, "a[*]b")


class IglobLazinessContract(TreeFixture):
    """iglob() was invoked ZERO times repo-wide before Z1 (TP46 evidence)."""

    def test_returns_lazy_iterator_not_a_list(self):
        it = fastglob.iglob("*", root_dir=self.root)
        self.assertIsInstance(it, Iterator, "documented: yields lazily")
        self.assertNotIsInstance(it, list)
        self.assertTrue(isgenerator(it), "documented mechanic: generator")

    def test_consumed_iterator_matches_glob(self):
        it = fastglob.iglob("**/*.py", recursive=True, root_dir=self.root)
        consumed = []
        for item in it:  # consume one at a time through the iterator protocol
            consumed.append(item)
        self.assertEqual(
            Counter(consumed),
            Counter(fastglob.glob("**/*.py", recursive=True, root_dir=self.root)),
        )


class DirFdContract(TreeFixture):
    """dir_fd validation guards (__init__.py:110-135) + engine chain."""

    def test_happy_path_lists_via_real_dir_fd(self):
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            got = fastglob.glob("*", dir_fd=fd)
        finally:
            os.close(fd)
        self.assertEqual(Counter(got), Counter(self.EXPECTED_STAR))

    def test_negative_dir_fd_raises_valueerror(self):
        with self.assertRaises(ValueError) as ctx:
            fastglob.glob("*", dir_fd=-5)
        self.assertIn("non-negative", str(ctx.exception))

    def test_non_int_dir_fd_raises_typeerror(self):
        with self.assertRaises(TypeError):
            fastglob.glob("*", dir_fd="3")

    def test_closed_dir_fd_raises_valueerror(self):
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        os.close(fd)  # real fault injection: the fd genuinely no longer exists
        with self.assertRaises(ValueError) as ctx:
            fastglob.glob("*", dir_fd=fd)
        self.assertIn("not a valid open fd", str(ctx.exception))

    def test_regular_file_dir_fd_engine_rejects_end_to_end(self):
        # The file fd PASSES the package fstat guard, is F_DUPFD'd, inherited
        # via pass_fds, and rejected by the ENGINE ("fd is not a directory",
        # exit 2 -> RuntimeError). Full trigger->output chain, no mocking.
        probe = os.path.join(self.root, "plain.txt")
        with open(probe, "wb"):
            pass
        fd = os.open(probe, os.O_RDONLY)
        try:
            with self.assertRaises(RuntimeError) as ctx:
                fastglob.glob("*", dir_fd=fd)
            self.assertIn("fd is not a directory", str(ctx.exception))
        finally:
            os.close(fd)


class MisuseThroughEngine(TreeFixture):
    """Engine-side guards surfaced as RuntimeError through the in-process core."""

    def assert_engine_misuse(self, pattern, **kwargs):
        # In-process transport: the engine reports misuse as a RuntimeError
        # directly (no "exited 2" subprocess wording); each test below
        # asserts the specific engine verdict text.
        with self.assertRaises(RuntimeError) as ctx:
            fastglob.glob(pattern, **kwargs)
        return ctx.exception

    def test_pattern_over_8192_bytes_runtime_error(self):
        err = self.assert_engine_misuse("a" * 9000, root_dir=self.root)
        self.assertIn("pattern too long", str(err))

    def test_pattern_over_512_components_runtime_error(self):
        err = self.assert_engine_misuse("/".join(["a"] * 600), root_dir=self.root)
        self.assertIn("pattern too long", str(err))

    def test_nul_in_pattern_raises_valueerror_before_walk(self):
        # the in-process engine guard rejects embedded NULs for real (stdlib
        # ValueError parity); main.rs carries the same defense-in-depth NUL
        # guard for the CLI (see cli_misuse.rs coverage map).
        with self.assertRaises(ValueError):
            fastglob.glob("a\0b")


if __name__ == "__main__":
    unittest.main(verbosity=2)
