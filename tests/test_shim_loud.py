"""Z1 family F — shim routing observability (ticket 001 item 2).

Scope: shim/glob.py — the deployed transparent shim. FASTGLOB_SHIM_LOUD is
an OPT-IN switch: default stays silent (no behavior change — the shim's
invisibility guarantee), any non-empty value makes the shim emit ONE stderr
line at first interception (module import) naming the engine that answered.

Assertion discipline (Z1): real fault injection, no mocks — every test loads
the ACTUAL shim file from disk in a fresh interpreter (subprocess) with a
controlled PYTHONPATH, capturing stdout/stderr separately so we assert BOTH
that the loud line appears on STDERR and that STDOUT stays untouched
(output purity is part of the contract).

The shim needs a `glob.py` module to intercept — the test harness gives it
the REAL stdlib glob via PYTHONPATH ordering (a shim must be on the path to
be imported as `glob`; here the shim file under test IS imported as `glob`
from the test's own shim_dir, and it loads true-stdlib glob by its internal
path-strip loader, exactly like the deployed /opt/fastglob-shim layout).

Run: `python3 tests/test_shim_loud.py` (standalone; no engine build needed —
the engine branch is what the loudness line reports, stdlib fallback if the
engine is not importable, and BOTH verdicts are asserted explicitly).
"""

import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIM_SRC = os.path.join(REPO, "shim", "glob.py")


def run_shim_import(env_extra):
    """Import the REAL shim file as `glob` in a fresh interpreter.

    Layout mirrors the deployed /opt/fastglob-shim: a directory containing
    glob.py, placed on PYTHONPATH so `import glob` resolves to the shim.
    Prints a marker on stdout and one on stderr AFTER the import so we can
    verify stream purity and ordering. Returns CompletedProcess.
    """
    with tempfile.TemporaryDirectory(prefix="fastglob_shim_test_") as shim_dir:
        shutil_copy = os.path.join(shim_dir, "glob.py")
        with open(SHIM_SRC, "rb") as f:
            payload = f.read()
        with open(shutil_copy, "wb") as f:
            f.write(payload)
        env = dict(os.environ)
        env.pop("FASTGLOB_SHIM_LOUD", None)  # hermetic: caller opts in explicitly
        env["PYTHONPATH"] = shim_dir
        env.update(env_extra)
        code = (
            "import glob; import sys;"
            "print('stdout-marker');"
            "sys.stderr.write('stderr-marker\\n')"
        )
        return subprocess.run(
            [sys.executable, "-c", code],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )


class ShimSilentByDefault(unittest.TestCase):
    """Default: no output on either stream (invisibility guarantee)."""

    def test_no_fastglob_and_no_loud_env_is_fully_silent(self):
        env = {"FASTGLOB_ISOLATED_TEST": "1"}
        r = run_shim_import(env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "stdout-marker\n")
        self.assertEqual(r.stderr, "stderr-marker\n")

    def test_loud_env_empty_string_stays_silent(self):
        # "set but empty" is NOT opted in: only non-empty values are loud.
        r = run_shim_import({"FASTGLOB_SHIM_LOUD": ""})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "stderr-marker\n")

    def test_interception_actually_happened(self):
        # Guard for the guards: the shim module must be the one imported as
        # glob (engine branch), proving the silence tests exercise a real
        # interception, not a stdlib fallback by accident.
        env = {"FASTGLOB_SHIM_LOUD": "1"}
        # Point PYTHONPATH at BOTH the shim dir and python/ so fastglob is
        # importable in-process (pure-Python surface works without the
        # compiled _core for this attribute check).
        with tempfile.TemporaryDirectory(prefix="fastglob_shim_test_") as shim_dir:
            with open(os.path.join(shim_dir, "glob.py"), "wb") as f:
                f.write(open(SHIM_SRC, "rb").read())
            e = dict(os.environ)
            e.pop("FASTGLOB_SHIM_LOUD", None)
            e["PYTHONPATH"] = os.pathsep.join([shim_dir, os.path.join(REPO, "python")])
            code = (
                "import glob, sys;"
                "sys.stderr.write('engine=' + str(getattr(glob, '_engine', None)) + '\\n')"
            )
            r = subprocess.run(
                [sys.executable, "-c", code],
                env=e,
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("engine=fastglob", r.stderr)


class ShimLoudOptIn(unittest.TestCase):
    """FASTGLOB_SHIM_LOUD=<non-empty>: ONE stderr line naming the engine."""

    def _assert_loud(self, r, engine_fragment):
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = [ln for ln in r.stderr.splitlines() if "fastglob-shim:" in ln]
        self.assertEqual(
            len(lines), 1, f"expected exactly ONE loud line, got {lines!r}"
        )
        self.assertIn(engine_fragment, lines[0])
        # stdout untouched — output purity
        self.assertEqual(r.stdout, "stdout-marker\n")
        # loud line comes BEFORE the test's own stderr marker (first interception)
        self.assertLess(
            r.stderr.index("fastglob-shim:"),
            r.stderr.index("stderr-marker"),
            "loud line must precede post-import stderr",
        )

    def test_loud_value_1_names_fastglob_engine(self):
        r = run_shim_import(
            {"FASTGLOB_SHIM_LOUD": "1", "FASTGLOB_ISOLATED_TEST": "1"}
        )
        self._assert_loud(r, "fastglob-shim: intercepting 'glob'")
        # engine fragment: fastglob (source PYTHONPATH=python) or the stdlib
        # fallback if the in-process import failed in this interpreter —
        # both verdicts are legitimate; assert the line is DECIDED, not vague.
        lines = [ln for ln in r.stderr.splitlines() if "fastglob-shim:" in ln]
        self.assertTrue(
            ("engine: fastglob" in lines[0]) or ("STDLIB FALLBACK" in lines[0]),
            f"loud line must name the actual engine: {lines!r}",
        )

    def test_loud_value_any_nonempty_word(self):
        # Any non-empty value opts in (documented: value content irrelevant)
        r = run_shim_import({"FASTGLOB_SHIM_LOUD": "yes-please"})
        self._assert_loud(r, "fastglob-shim: intercepting 'glob'")

    def test_stdlib_fallback_loud_verdict(self):
        # Real fault injection: hide the fastglob package so the shim MUST
        # fall back to stdlib — the loud line must say so explicitly.
        with tempfile.TemporaryDirectory(prefix="fastglob_shim_test_") as shim_dir:
            with open(os.path.join(shim_dir, "glob.py"), "wb") as f:
                f.write(open(SHIM_SRC, "rb").read())
            e = dict(os.environ)
            e.pop("FASTGLOB_SHIM_LOUD", None)
            e["FASTGLOB_SHIM_LOUD"] = "1"
            e["PYTHONPATH"] = shim_dir
            e["FASTGLOB_HIDE"] = "1"
            # SANITY for this injection: confirm fastglob is NOT importable
            probe = subprocess.run(
                [sys.executable, "-c", "import fastglob"],
                env=e,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if probe.returncode == 0:
                self.skipTest(
                    "fastglob installed site-wide; cannot simulate missing engine"
                )
            r = subprocess.run(
                [sys.executable, "-c", "import glob; import sys; sys.stderr.write('post\\n')"],
                env=e,
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = [ln for ln in r.stderr.splitlines() if "fastglob-shim:" in ln]
        self.assertEqual(len(lines), 1, f"expected one loud line, got {lines!r}")
        self.assertIn("STDLIB FALLBACK", lines[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
