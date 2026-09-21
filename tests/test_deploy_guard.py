#!/usr/bin/env python3
"""Guard tests for tools/deploy.sh's rollback authority rules.

WHY THIS FILE EXISTS
--------------------
`restore_snapshot` reads "the snapshot recorded nothing installed" as "remove
whatever is installed now". That is correct only for the targets the snapshot
actually recorded. On 2026-09-21 a rollback was driven with a synthetic snapshot
and the ambient default interpreter, and it deleted the live
`/usr/local/lib/python3.12/dist-packages/fastglob{,-0.1.3.dist-info}` — an
install that snapshot had never described. A rollback that cannot name what it
is overwriting must refuse, so the tool now refuses on three conditions, and
this file pins all three:

  1. the snapshot has no `meta` (not written by this tool at all);
  2. the snapshot's recorded `shim-dir` is not the SHIM_DIR being restored;
  3. the snapshot records no state for an interpreter in PYTHONS.

Plus the positive control: a snapshot that *is* authoritative restores only its
recorded targets — never the ambient interpreter's site-packages.

SAFETY OF THE TEST ITSELF
-------------------------
Everything runs under a throwaway temp directory, `SUDO=` is empty so the driver
never escalates, and each case asserts the sandbox sentinel is unchanged. The
real `/opt`, `/usr/local` and `/var/backups` paths are never arguments to
anything here. Assert #3 additionally requests the REAL ambient `python3` while
the snapshot records a different interpreter, so it fails closed precisely in
the shape of the original accident.

Run: python3 tests/test_deploy_guard.py   (exit 0 = all guards hold)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# FASTGLOB_DEPLOY_DRIVER exists so this file can be pointed at a deliberately
# guard-less copy of the driver to prove these checks FAIL without the guards —
# a guard test that cannot fail is decoration.
DRIVER = Path(os.environ.get("FASTGLOB_DEPLOY_DRIVER", REPO_ROOT / "tools" / "deploy.sh"))

SENTINEL = "SENTINEL-DO-NOT-TOUCH\n"
NEW_SHIM = "# restored shim\n"

failures: list[str] = []
checks = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global checks
    checks += 1
    if cond:
        print(f"  PASS {label}")
    else:
        print(f"  FAIL {label}{f' — {detail}' if detail else ''}")
        failures.append(label)


def make_snapshot(root: Path, stamp: str, *, meta: str | None) -> Path:
    snap = root / "backups" / stamp
    (snap / "shim").mkdir(parents=True)
    (snap / "shim" / "glob.py").write_text(NEW_SHIM)
    if meta is not None:
        (snap / "meta").write_text(meta)
    return snap


def run_rollback(
    root: Path, stamp: str, interpreter: str
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(
        {
            "SHIM_DIR": str(root / "shim"),
            "BACKUP_ROOT": str(root / "backups"),
            "PYTHONS": interpreter,
            "SUDO": "",  # never escalate — the guards must hold unprivileged
            "DRY_RUN": "0",
        }
    )
    return subprocess.run(
        [str(DRIVER), "rollback", f"FROM={stamp}"],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


def new_sandbox() -> Path:
    root = Path(tempfile.mkdtemp(prefix="fastglob-deploy-guard-"))
    (root / "shim").mkdir()
    (root / "shim" / "glob.py").write_text(SENTINEL)
    (root / "fakesite" / "fastglob").mkdir(parents=True)
    (root / "fakesite" / "fastglob" / "__init__.py").write_text("# fake\n")
    (root / "fakesite" / "fastglob-0.1.3.dist-info").mkdir()
    return root


def case_refuses(
    label: str, meta: str | None, interpreter: str, expect_in_stderr: str, needle2: str = ""
) -> None:
    root = new_sandbox()
    try:
        make_snapshot(root, "20260101T000000Z", meta=meta)
        proc = run_rollback(root, "20260101T000000Z", interpreter)
        check(proc.returncode == 3, f"{label}: refuses with rc 3", f"rc={proc.returncode}")
        check(
            expect_in_stderr in proc.stderr,
            f"{label}: says why ({expect_in_stderr!r})",
            proc.stderr.strip()[:200],
        )
        if needle2:
            check(needle2 in proc.stderr, f"{label}: names the conflict", proc.stderr.strip()[:200])
        check(
            (root / "shim" / "glob.py").read_text() == SENTINEL,
            f"{label}: sandbox shim untouched",
        )
        check(
            (root / "fakesite" / "fastglob" / "__init__.py").exists(),
            f"{label}: nothing removed from the sandbox site dir",
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    print("rollback authority guards (tools/deploy.sh)")

    # 1. A directory without meta was not written by this tool.
    case_refuses("no meta", None, "python3", "has no meta")

    # 2. A snapshot taken against another SHIM_DIR is not authoritative for this one.
    case_refuses(
        "foreign shim-dir",
        "shim-dir /opt/somewhere-else\ninterpreter python3 purelib /opt/somewhere-else/site\n",
        "python3",
        "refusing to restore",
        "/opt/somewhere-else",
    )

    # 3. THE ORIGINAL ACCIDENT, FAILING CLOSED: the snapshot matches SHIM_DIR but
    #    records a different interpreter, while PYTHONS names the real ambient
    #    `python3`. Before the guard this deleted that interpreter's
    #    site-packages; now it must refuse before touching anything.
    root = new_sandbox()
    try:
        snap = make_snapshot(
            root,
            "20260101T000000Z",
            meta=(
                f"shim-dir {root / 'shim'}\n"
                f"interpreter /nonexistent/python3 purelib {root / 'fakesite'}\n"
            ),
        )
        proc = run_rollback(root, "20260101T000000Z", "python3")
        check(
            proc.returncode == 3,
            "unrecorded interpreter: refuses with rc 3 (names the REAL python3)",
            f"rc={proc.returncode}",
        )
        check(
            "records no state for interpreter python3" in proc.stderr,
            "unrecorded interpreter: says which interpreter is unrecorded",
            proc.stderr.strip()[:200],
        )
        check(
            (root / "shim" / "glob.py").read_text() == SENTINEL,
            "unrecorded interpreter: sandbox shim untouched",
        )
        check(snap.is_dir(), "unrecorded interpreter: snapshot left intact")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # Positive control: an authoritative snapshot restores ONLY its recorded
    # targets — the shim is replaced, the recorded purelib has its (unrecorded)
    # install removed, and nothing outside the sandbox is consulted.
    root = new_sandbox()
    try:
        fake_py = str(root / "fakebin" / "python3")
        (root / "fakebin").mkdir()
        make_snapshot(
            root,
            "20260202T000000Z",
            meta=(
                f"shim-dir {root / 'shim'}\n"
                f"interpreter {fake_py} purelib {root / 'fakesite'}\n"
            ),
        )
        proc = run_rollback(root, "20260202T000000Z", fake_py)
        check(proc.returncode == 0, "authoritative snapshot: rc 0", f"rc={proc.returncode}")
        check(
            (root / "shim" / "glob.py").read_text() == NEW_SHIM,
            "authoritative snapshot: recorded shim restored",
        )
        check(
            not (root / "fakesite" / "fastglob").exists(),
            "authoritative snapshot: recorded purelib's unrecorded install removed",
        )
        check(
            "import glob" in proc.stdout,
            "authoritative snapshot: reports the post-restore resolution",
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print(f"\n{checks - len(failures)}/{checks} checks passed")
    if failures:
        print("FAILED: " + ", ".join(failures))
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
