#!/usr/bin/env python3
"""The shim-dir path-strip must treat a symlinked shim dir as the SAME directory.

Scope: `fastglob._real_stdlib_glob_module()` — the loader that recovers the TRUE
stdlib `glob` while a shim copy owns `import glob`. The loader works by
(1) stripping every directory that holds a shim copy from `sys.path`, then
(2) `PathFinder.find_spec("glob", sys.path)` + `exec_module` to get the real
stdlib module, with (3) a re-entrancy guard that refuses any spec whose origin
is inside a known shim dir.

Why this file exists (regression, 2026-09-21): steps 1 and 3 compared
`os.path.abspath()` forms. `abspath` normalizes lexically and NEVER resolves
symlinks, so when the SAME shim directory appeared on `sys.path` twice under two
names — a real path plus a symlink alias to it — only one form matched
`_shim_dirs`, the alias survived the strip, `find_spec` resolved to the
surviving copy, and `exec_module` re-entered the shim from inside the shim. The
guard could not catch it either: the surviving path was not a string-prefix of
any `_shim_dirs` entry.

Measured (2026-09-21) on the arrangement below — alias listed before the real
path, shim copy first on `PYTHONPATH`:

    OLD loader (abspath)   -> `_real_glob.__file__` == <real>/glob.py (a SHIM copy)
    current loader (resolve) -> `_real_glob.__file__` == <stdlib>/glob.py

so the module's own claim ("the true stdlib glob") was false in exactly the
ad-hoc-copy case the loader comments say it exists for. Symlink-free
arrangements behave identically under both loaders, which is why
`tests/test_shim_parity.py` and `tests/test_shim_loud.py` never caught it: they
put one shim dir on `PYTHONPATH` under one name.

Mechanism: each test execs the shim FROM DISK in a fresh interpreter, with a
`PYTHONPATH` that names one temporary shim copy twice — once through a symlink
alias, once through its real path. No engine install required: only
`python/` (for `fastglob`) and the repo `shim/glob.py` are used.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
SHIM_SRC = os.path.join(REPO, "shim", "glob.py")
PY_PKG = os.path.join(REPO, "python")

# The child reports the in-flight `glob` (the shim, if the arrangement held), the
# module the loader recovered, and where the TRUE stdlib glob lives.
_PROBE = r"""
import glob, json, os, fastglob

out = {
    "inflight_glob": glob.__file__,
    "recovered": getattr(fastglob._real_glob, "__file__", None),
    "stdlib_glob": os.path.join(os.path.dirname(os.__file__), "glob.py"),
    "glob_call_works": isinstance(glob.glob("*"), list),
}
print(json.dumps(out))
"""


def run_probe(pythonpath_dirs: list[str], python_pkg: str = PY_PKG) -> dict:
    """Exec the probe in a fresh interpreter under the given `PYTHONPATH`.

    Inputs:
        pythonpath_dirs: entries in order; the FIRST one that holds a `glob.py`
            wins `import glob`.
        python_pkg: directory holding the `fastglob` package to import.
    Output:
        dict — the probe's JSON payload.
    Errors:
        AssertionError if the child could not produce the payload (stderr is
        included, so a re-entrancy crash in the loader is readable).
    """
    env = dict(os.environ)
    env.pop("FASTGLOB_SHIM_LOUD", None)
    env["PYTHONPATH"] = os.pathsep.join([*pythonpath_dirs, python_pkg])
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"probe failed rc={proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    return json.loads(proc.stdout.strip().splitlines()[-1])


class ShimAliasPathStrip(unittest.TestCase):
    """One shim copy, two `sys.path` names (symlink alias + real path)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.mkdtemp(prefix="fastglob-shim-alias-")
        cls.real_dir = os.path.join(cls.tmp, "shim-copy")
        os.makedirs(cls.real_dir)
        shutil.copy2(SHIM_SRC, os.path.join(cls.real_dir, "glob.py"))
        cls.alias_dir = os.path.join(cls.tmp, "shim-alias")
        os.symlink(cls.real_dir, cls.alias_dir)
        # Alias FIRST so the in-flight `glob` is bound through the alias name.
        cls.probe = run_probe([cls.alias_dir, cls.real_dir])
        if os.path.realpath(cls.probe["inflight_glob"]) != os.path.join(
            cls.real_dir, "glob.py"
        ):
            raise unittest.SkipTest(
                f"another `glob` shadow won the import: {cls.probe['inflight_glob']}"
            )

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_premise_alias_is_the_shim_and_two_names_are_on_the_path(self):
        """The arrangement under test must actually hold, or the rest proves nothing.

        Alias != real path as strings, but the same directory on disk — that
        asymmetry is the entire subject of this file.
        """
        self.assertEqual(
            self.probe["inflight_glob"], os.path.join(self.alias_dir, "glob.py")
        )
        self.assertEqual(os.path.realpath(self.alias_dir), self.real_dir)
        self.assertNotEqual(self.alias_dir, self.real_dir)

    def test_recovered_module_is_the_true_stdlib(self):
        """The loader's stated contract: `_real_glob` IS the stdlib glob module.

        FAILS on the abspath loader, which returned the surviving shim copy.
        """
        self.assertEqual(
            os.path.realpath(self.probe["recovered"]),
            os.path.realpath(self.probe["stdlib_glob"]),
            msg=(
                "loader recovered a shim copy, not the stdlib: "
                f"{self.probe['recovered']} (stdlib is {self.probe['stdlib_glob']})"
            ),
        )

    def test_recovered_module_is_not_the_second_shim_copy(self):
        """Re-entrancy: `_real_glob` must never be a module from a shim dir.

        This is the failure the guard exists for — exec'ing a second shim copy
        from inside the shim.
        """
        self.assertNotEqual(
            os.path.realpath(self.probe["recovered"]),
            os.path.join(self.real_dir, "glob.py"),
        )

    def test_shim_still_serves_glob_under_the_alias(self):
        """The user-visible property survives: `glob.glob` works, shim in place.

        The loader fix must not cost the shadow its function.
        """
        self.assertTrue(self.probe["glob_call_works"])
        self.assertTrue(self.probe["recovered"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
