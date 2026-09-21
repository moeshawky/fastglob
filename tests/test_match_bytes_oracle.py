"""Matcher differential vs the TRUE stdlib ``fnmatch`` — committed and reproducible.

Why this file exists
--------------------
``src/fastglob/src/matcher.rs`` is the semantic core of a compatibility-locked
engine: if it drifts from CPython 3.12's ``fnmatch.translate`` semantics, every
``glob()`` call in the engine is wrong in the same way at once. Its docstring
claimed a "400k random differential" run *"via out/dev/diff_match.py"* — but
``out/`` is gitignored and that file does not exist, so the strongest
correctness claim about the core could not be re-executed by anyone reading
this repository. This file replaces that unfalsifiable citation with a
runnable instrument, and extends it into the region the old claim never
reached.

What it establishes
-------------------
1. **str mode is faithful.** ``fastglob.match(pat, name)`` is compared against
   ``fnmatch.fnmatchcase(name=name, pat=pat)`` over (a) a targeted table of
   every construct in the real ``fnmatch.translate``, (b) an EXHAUSTIVE sweep
   of short patterns over the class-syntax alphabet, and (c) a seeded
   deterministic random sweep including non-ASCII str. Expected mismatches: 0.

2. **bytes mode diverges from its declared oracle in one bounded region.**
   ``matcher.rs`` ``decode_chars`` decodes UTF-8 (one code point per valid
   multi-byte sequence; ``0xDC00|b`` per invalid byte) because it is SHARED
   with the ``glob()`` walk, which must decode the filesystem's raw bytes the
   way ``os.fsdecode`` does. stdlib ``fnmatch._compile_pattern`` instead does
   ``str(pat, 'ISO-8859-1')`` — latin-1, one code point per byte. On a path
   containing a valid multi-byte UTF-8 sequence the two therefore disagree in
   TWO independent ways:

   * **Character count** — ``?``, ``[seq]``, ``[!seq]`` counts shift
     (``match(b"?", b"\xc3\xa9")`` -> True; stdlib -> False).
   * **Class membership / ordering** — one model orders code points and the
     other orders bytes, so a byte RANGE can contain a byte of the sequence
     while containing none of its decoded code points:
     ``match(b"*[\xc0-\xc3]*", b"\xc3\xa9")`` -> False while stdlib -> True,
     because the engine's class decodes to the surrogate range
     ``U+DCC0..U+DCC3`` which never contains ``U+00E9``, whereas stdlib
     compares the raw byte ``0xC3`` against ``[0xC0..0xC3]``. Here the ``*``
     wildcards absorb the count difference, so COUNT alone does not explain it.

   The count-only explanation was the original claim; an independent
   falsification pass broke it with the range witness above, which is why that
   witness is pinned here.

   This file does NOT assert the divergence away and does NOT assert it is
   correct: it PINS it. The witnesses below are asserted to diverge exactly as
   documented, so the behaviour cannot drift silently, and a separate
   invariant test asserts divergence NEVER occurs outside a multi-byte name.
   If the engine is ever changed to buy full ``fnmatchcase`` parity in bytes
   mode, these witness assertions fail loudly and force the documentation and
   this file to be updated together — which is the point.

Run wiring
----------
``python3 tests/test_match_bytes_oracle.py`` — exit 0 on pass, 1 on failure.
PYTHONPATH-independent: inserts ``<repo>/python`` onto ``sys.path`` itself. A
missing ``fastglob._core`` FAILS LOUDLY in ``setUpModule`` rather than
silently skipping (build it with ``pip install -e python``).

Assertion discipline
--------------------
No mocks. The oracle is the running interpreter's real ``fnmatch``, loaded BY
PATH from ``sysconfig``'s stdlib so it is shim-immune by construction (this
environment sets ``PYTHONPATH=/opt/fastglob-shim``; ``fnmatch`` is not shimmed
today, but a test that only happens to be safe is not safe). Every sweep
asserts that it actually REACHED the shape it claims to test, so no assertion
can pass vacuously.
"""

import importlib.util
import itertools
import random
import sys
import time
import unittest
from pathlib import Path
from sysconfig import get_paths

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "python"))


def _true_stdlib_fnmatch():
    """Load the real stdlib ``fnmatch`` by path — never a shimmed import.

    Why: ``PYTHONPATH=/opt/fastglob-shim`` is set in this environment. We load
    the stdlib file directly from ``sysconfig``'s stdlib dir so the oracle can
    never be the shim, whatever the environment does later.
    """
    stdlib = Path(get_paths()["stdlib"])
    path = stdlib / "fnmatch.py"
    if not path.is_file():
        raise unittest.SkipTest(f"no stdlib fnmatch.py at {path}")
    spec = importlib.util.spec_from_file_location("_fastglob_true_fnmatch", path)
    if spec is None or spec.loader is None:
        raise unittest.SkipTest(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FN = _true_stdlib_fnmatch()

try:
    import fastglob
    import fastglob._core  # noqa: F401  (build-integration check)
except Exception as exc:  # pragma: no cover - build-integration failure
    raise AssertionError(
        "fastglob._core is not importable — build it with `pip install -e python` "
        "before running this file (this must fail loudly, not skip). "
        f"Underlying error: {exc!r}"
    ) from exc


def engine(pat, name):
    """Verdict of the engine, normalised to ``("val", bool)`` / ``("exc", type)``."""
    try:
        return ("val", fastglob.match(pat, name))
    except Exception as exc:  # the comparison is the VERDICT, not the value
        return ("exc", type(exc).__name__)


def oracle(pat, name):
    """Verdict of the true stdlib, same normalisation.

    Argument order is deliberately keyword-only: stdlib is
    ``fnmatchcase(name, pat)`` while the engine is ``match(pattern, path)``.
    An earlier hand-rolled probe of this comparison silently inverted both
    arguments and produced a confidently wrong conclusion.
    """
    try:
        return ("val", FN.fnmatchcase(name=name, pat=pat))
    except Exception as exc:
        return ("exc", type(exc).__name__)


# --- 1. str mode: targeted table -------------------------------------------
# One entry per construct with defined behaviour in the real fnmatch.translate:
# STAR compression, '?' (crosses '/'), class ranges, inverted ranges (the
# empty-range removal pass), '-' at both edges, ']' first, '!' negation,
# '^'/'[' escaped inside a class, set-difference '--', set-ops '&&'/'~~'/'||'
# escaped, unclosed '[', POSSIX backslash (NOT an escape), empty pattern/path.
STR_EDGE_TABLE = [
    # (pattern, [names...])
    ("*", ["", "a", "a/b", "a//b", ".", "..", "\u2713"]),
    ("**", ["", "a", "a/b/c", "**"]),
    ("?", ["", "a", "/", "ab", "\u2713"]),
    ("??", ["a", "ab", "a/b", "\u2713"]),
    ("a?c", ["abc", "a/c", "ac", "aXc"]),
    ("*a*b*", ["ab", "aXb", "XaYbZ", "b", "a"]),
    ("*.*", ["a.c", "ab", "a.b.c", ".c", "a."]),
    ("[abc]", ["a", "b", "c", "d", "ab", "]"]),
    ("[a-c]", ["a", "b", "c", "d", "-"]),
    ("[c-a]", ["a", "b", "c", "d", "-"]),          # inverted range
    ("[a-]", ["a", "-", "b"]),
    ("[-a]", ["a", "-", "b"]),
    ("[]]", ["]", "a", "[]]"]),
    ("[]", ["", "a", "]", "[]"]),
    ("[!]", ["a", "]", "[!]"]),
    ("[!]a", ["a", "]a", "[!]a"]),
    ("[!a]", ["a", "b", "]"]),
    ("[!a-c]", ["a", "b", "d", "-"]),
    ("[^a]", ["^", "a", "b"]),                      # '^' is escaped -> literal
    ("[[]", ["[", "a", "[]"]),
    ("[a--b]", ["a", "b", "-"]),                    # set difference
    ("[a&&b]", ["a", "&", "b"]),                    # '&' escaped
    ("[a~~b]", ["a", "~", "b"]),                    # '~' escaped
    ("[a||b]", ["a", "|", "b"]),                    # '|' escaped
    ("[a-", ["a", "[a-", "-"]),                     # unclosed -> literal '['
    ("[!a-", ["a", "!", "z"]),
    ("[", ["[", "a"]),
    ("a\\", ["a\\", "a"]),                          # POSIX: backslash is literal
    ("\\a", ["\\a", "a"]),
    ("a\\*b", ["a\\Xb", "a*b"]),
    ("", ["", "a"]),                                # empty pattern
    ("a//b", ["a//b", "a/b"]),                      # empty component collapses
    ("caf\u00e9", ["caf\u00e9", "cafe"]),
    ("caf\u00e9*", ["caf\u00e9.txt", "cafe.txt"]),
    ("[\u2713]", ["\u2713", "x"]),
    ("**/vendor/**", ["a/vendor/b", "vendor", "a/b"]),
]


class StrModeParity(unittest.TestCase):
    """str mode is the primary claim; it must be exactly faithful."""

    def test_edge_table_parity(self):
        reached = 0
        bad = []
        for pat, names in STR_EDGE_TABLE:
            for name in names:
                reached += 1
                e, s = engine(pat, name), oracle(pat, name)
                if e != s:
                    bad.append((pat, name, e, s))
        # Anti-vacuous guard: the table must actually have run.
        self.assertGreater(reached, 100, "edge table did not execute")
        self.assertEqual(bad, [], f"str-mode divergences vs fnmatch: {bad[:10]}")

    def test_exhaustive_short_patterns(self):
        """Exhaustive over the class-syntax alphabet (no sampling).

        PAT x NAME = 820 x 73 = 59,860 pairs; every 2- and 3-char combination
        of `* ? [ ] ! - ^ a \\` appears as a pattern, so no short syntax
        ambiguity is left to chance.
        """
        pat_alpha = list("a*?[]!-^\\")
        name_alpha = list("ab]^-[!\\")

        def words(alpha, maxlen):
            for L in range(maxlen + 1):
                for t in itertools.product(alpha, repeat=L):
                    yield "".join(t)

        pats = list(words(pat_alpha, 3))
        names = list(words(name_alpha, 2))
        pairs = len(pats) * len(names)
        self.assertEqual((len(pats), len(names)), (820, 73))
        bad = []
        started = time.time()
        for pat in pats:
            for name in names:
                e, s = engine(pat, name), oracle(pat, name)
                if e != s:
                    bad.append((pat, name, e, s))
        elapsed = time.time() - started
        print(
            f"    [str exhaustive] {len(pats)} patterns x {len(names)} names "
            f"= {pairs} pairs in {elapsed:.1f}s, mismatches={len(bad)}"
        )
        self.assertEqual(bad, [], f"str-mode divergences (exhaustive): {bad[:10]}")

    def test_seeded_random_including_non_ascii(self):
        rng = random.Random(20260921)
        alpha = list("ab*?[]!-^/&~|\\") + ["\u00e9", "\u03c0"]
        n = 20000
        bad = []
        for _ in range(n):
            pat = "".join(rng.choice(alpha) for _ in range(rng.randint(0, 7)))
            name = "".join(rng.choice(alpha) for _ in range(rng.randint(0, 5)))
            e, s = engine(pat, name), oracle(pat, name)
            if e != s:
                bad.append((pat, name, e, s))
        print(f"    [str random] {n} seeded pairs, mismatches={len(bad)}")
        self.assertEqual(bad, [], f"str-mode divergences (random): {bad[:10]}")


# --- 2. bytes mode: ASCII must be faithful --------------------------------
class BytesModeAsciiParity(unittest.TestCase):
    def test_seeded_random_ascii(self):
        rng = random.Random(4242)
        alpha = [bytes([ord(c)]) for c in "ab*?[]!-^/"]
        n = 4000
        bad = []
        for _ in range(n):
            pat = b"".join(rng.choice(alpha) for _ in range(rng.randint(0, 6)))
            name = b"".join(rng.choice(alpha) for _ in range(rng.randint(0, 4)))
            e, s = engine(pat, name), oracle(pat, name)
            if e != s:
                bad.append((pat, name, e, s))
        print(f"    [bytes ascii] {n} seeded pairs, mismatches={len(bad)}")
        self.assertEqual(bad, [], f"bytes-mode ASCII divergences: {bad[:10]}")


# --- 3. bytes mode: the multi-byte boundary is PINNED ----------------------
class BytesModeMultibyteBoundary(unittest.TestCase):
    """Pins the documented divergence. See the module docstring.

    `decode_chars` (matcher.rs) is fsdecode-consistent UTF-8+surrogateescape
    because the `glob()` walk shares it; stdlib fnmatch decodes bytes as
    latin-1 (fnmatch.py `_compile_pattern`). Character counts differ exactly
    when a valid multi-byte UTF-8 sequence is present.
    """

    # Asserted to diverge, with the exact direction. Locked to the docs.
    WITNESSES = [
        # (pattern, name, engine_verdict, stdlib_verdict)
        (b"?", b"\xc3\xa9", True, False),        # mechanism 1: 1 cp vs 2
        (b"??", b"\xc3\xa9", False, True),       # mechanism 1: 2 cp vs 1
        (b"[\xc3\xa9]", b"\xc3\xa9", True, False),
        # mechanism 2: class/range ORDERING, with `*` absorbing the count gap.
        # Engine decodes the class to U+DCC0..U+DCC3 (surrogates) which never
        # contains U+00E9; stdlib matches raw byte 0xC3 in [0xC0..0xC3].
        (b"*[\xc0-\xc3]*", b"\xc3\xa9", False, True),
        (b"*[\x80-\xff]*", b"\xc3\xa9", False, True),
    ]

    def test_documented_witnesses_diverge_exactly_as_documented(self):
        for pat, name, want_engine, want_stdlib in self.WITNESSES:
            e, s = engine(pat, name), oracle(pat, name)
            self.assertEqual(
                e, ("val", want_engine), f"engine changed at witness {pat!r} {name!r}"
            )
            self.assertEqual(
                s, ("val", want_stdlib), f"oracle changed at witness {pat!r} {name!r}"
            )
            self.assertNotEqual(
                e, s, f"witness {pat!r} {name!r} no longer diverges — update docs"
            )

    def test_invariant_divergence_never_escapes_a_multibyte_name(self):
        """Divergence is a strict SUBSET of names containing valid multi-byte UTF-8.

        Exhaustive: 400 patterns x 21 names = 8,400 pairs, alphabet mixing
        valid UTF-8 lead/continuation bytes (`\\xc3`,`\\xa9`), an invalid byte
        (`\\xff`), literals and magic. Asserts BOTH that divergences exist
        (non-vacuous) and that every one has a multi-byte name.
        """
        pat_alpha = [b"\xc3", b"\xa9", b"\xff", b"a", b"?", b"*", b"["]
        name_alpha = [b"\xc3", b"\xa9", b"\xff", b"a"]

        def words(alpha, maxlen):
            for L in range(maxlen + 1):
                for t in itertools.product(alpha, repeat=L):
                    yield b"".join(t)

        pats = list(words(pat_alpha, 3))
        names = list(words(name_alpha, 2))
        # 7 symbols len<=3 = 1+7+49+343 = 400; 4 symbols len<=2 = 1+4+16 = 21.
        # Pinned so a silent change to the sweep cannot make this vacuous.
        self.assertEqual((len(pats), len(names)), (400, 21))
        divergences = []
        for pat in pats:
            for name in names:
                e, s = engine(pat, name), oracle(pat, name)
                if e != s:
                    divergences.append((pat, name, e, s))
        # Anti-vacuous: if the region is ever closed, this test must say so
        # rather than pass because it reached nothing.
        self.assertGreater(
            len(divergences),
            0,
            "expected the documented bytes-mode divergence to be observable; "
            "if it is gone, update the docs and this file together",
        )
        escaped = [d for d in divergences if b"\xc3\xa9" not in d[1]]
        print(
            f"    [bytes boundary] {len(pats)}x{len(names)} pairs, "
            f"divergences={len(divergences)}, outside-multibyte={len(escaped)}"
        )
        self.assertEqual(
            escaped,
            [],
            f"divergence outside a multi-byte name — the characterisation in "
            f"docs/compatibility-contract.md is now WRONG: {escaped[:5]}",
        )


if __name__ == "__main__":
    print(
        f"matcher differential — engine={fastglob.__file__}, "
        f"oracle={FN.__file__} (Python {sys.version.split()[0]})"
    )
    unittest.main(verbosity=2)
