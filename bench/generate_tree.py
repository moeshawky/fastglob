#!/usr/bin/env python3
"""Shared benchmark workload-tree generator — 8-shape matrix (docs/benchmark-discipline.md §10.2).

Idempotent: wipes bench/trees/ and rebuilds it. Pure stdlib. No external deps.

AUTO-SHRINK: if `df` (shutil.disk_usage) reports < 1.5 GB free on /kaggle/working,
large shapes are scaled by S = max(0.25, free_gb / 3.0); small/deep/symlinks are
too small to shrink. The applied scale is recorded in MANIFEST.json.

HARD DISK BUDGET: total generated size must be <= 1.2 GB (checked, not assumed).
Generation aborts with exit code 2 above budget — no silent trim.

TARGET SIZES (full scale S=1):
  small     ~1,000 files in a project-like layout (src/tests/docs/data/...)
  wide      100,000 files in ONE directory (95% .dat, 5% .py)
  deep      500 directory levels, one node_i.txt per level
  sparse    40,000 files in 2,000 dirs; only ~40 are .py (traversal-dominated)
  dense     40,000 files in 2,000 dirs; 60% are .py (>=50% matches)
  recursive 1,500 nested chains a/c*/c*/.../b/{m.py,notes.txt} (depths 3..25)
            + 500 top_*.py at the shape root
  hidden    20,000 files in 1,000 dirs; 50% dot-prefixed (.hid_*.py)
  symlinks  ~5,650 entries: 100 real files in 50 leaf dirs, 5,000 file symlinks
            (10% broken), 500 dir symlinks to leaf dirs, ONE self-cycle
            (cyc/self -> cyc) for behavior observation under a timeout.

Bench patterns exercised per shape live in bench/bench.py (ROWS).

Output: bench/trees/<shape>/..., bench/trees/MANIFEST.json,
plus a per-subtree file count and `du -sh` table on stdout.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

BENCH = Path(__file__).resolve().parent
ROOT = BENCH / "trees"
MOUNT = "/kaggle/working"
FREE_SHRINK_THRESHOLD_GB = 1.5
BUDGET_BYTES = int(1.2 * 1024 ** 3)
CONTENT = b"0123456789abcdef"  # 16 bytes/file keeps the fixture small


# ----------------------------------------------------------------- utilities

def free_gb() -> float:
    return shutil.disk_usage(MOUNT).free / 1e9


def scale_factor() -> float:
    free = free_gb()
    if free >= FREE_SHRINK_THRESHOLD_GB:
        return 1.0
    return max(0.25, free / 3.0)


class Tree:
    """Accumulates per-shape statistics while files are written."""

    def __init__(self) -> None:
        self.stats: dict[str, dict] = {}
        self._files = 0
        self._dirs = 0

    def begin(self, shape: str) -> None:
        self._files = 0
        self._dirs = 0

    def dir(self, path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        self._dirs += 1
        return path

    def file(self, path: Path, content: bytes = CONTENT) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(content)
        self._files += 1

    def link(self, path: Path, target: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, path)
        self._files += 1  # symlinks count as entries

    def end(self, shape: str, **params) -> None:
        self.stats[shape] = {
            "files": self._files,
            "dirs": self._dirs,
            **params,
        }


# ----------------------------------------------------------------- shapes

def gen_small(t: Tree, s: float) -> None:
    base = t.dir(ROOT / "small")
    for n in ("README.md", "LICENSE", "pyproject.toml", "setup.py", ".gitignore"):
        t.file(base / n)
    src = t.dir(base / "src" / "pkg")
    for i in range(250):
        t.file(src / f"mod_{i:03d}.py")
    sub = t.dir(src / "sub")
    for i in range(50):
        t.file(sub / f"sub_{i:03d}.py")
    tests = t.dir(base / "tests")
    for i in range(200):
        t.file(tests / f"test_{i:03d}.py")
    docs = t.dir(base / "docs")
    for i in range(50):
        t.file(docs / f"doc_{i:03d}.md")
    data = t.dir(base / "data")
    for i in range(200):
        t.file(data / f"data_{i:03d}.json")
    scripts = t.dir(base / "scripts")
    for i in range(50):
        t.file(scripts / f"run_{i:03d}.sh")
    notes = t.dir(base / "notes")
    for i in range(190):
        t.file(notes / f"note_{i:03d}.txt")
    cfg = t.dir(base / "config")
    for i in range(5):
        t.file(cfg / f"cfg_{i}.ini")
    t.end("small", layout="project-like", note="unscaled (already small)")


def gen_wide(t: Tree, s: float) -> None:
    n = int(100_000 * s)
    base = t.dir(ROOT / "wide")
    for i in range(n):
        ext = ".py" if i % 20 == 0 else ".dat"
        t.file(base / f"file_{i:06d}{ext}")
    t.end("wide", entries=n, py=round(n / 20), dat=n - round(n / 20))


def gen_deep(t: Tree, s: float) -> None:
    levels = 500
    base = ROOT / "deep"
    d = t.dir(base)
    for i in range(levels):
        d = t.dir(d / f"d{i:03d}")
        t.file(d / f"node_{i:03d}.txt")
    t.end("deep", levels=levels, note="unscaled (already small)")


def gen_sparse(t: Tree, s: float) -> None:
    ndir = max(1, int(2_000 * s))
    per = 20
    py_every = 50  # one .py per 50 dirs
    base = ROOT / "sparse"
    for i in range(ndir):
        d = t.dir(base / f"dir_{i:04d}")
        for j in range(per):
            if j == 0 and i % py_every == 0:
                t.file(d / f"rare_{i:04d}.py")
            else:
                t.file(d / f"data_{i:04d}_{j:02d}.dat")
    t.end("sparse", dirs=ndir, files=ndir * per, py=ndir // py_every)


def gen_dense(t: Tree, s: float) -> None:
    ndir = max(1, int(2_000 * s))
    per = 20
    per_py = 12  # 60% of files are .py
    base = ROOT / "dense"
    for i in range(ndir):
        d = t.dir(base / f"dir_{i:04d}")
        for j in range(per):
            if j < per_py:
                t.file(d / f"mod_{i:04d}_{j:02d}.py")
            else:
                t.file(d / f"data_{i:04d}_{j:02d}.dat")
    t.end("dense", dirs=ndir, files=ndir * per, py=ndir * per_py, dat=ndir * (per - per_py))


def gen_recursive(t: Tree, s: float) -> None:
    chains = max(1, int(1_500 * s))
    tops = max(1, int(500 * s))
    base = t.dir(ROOT / "recursive")
    a = t.dir(base / "a")
    for i in range(chains):
        depth = 3 + (i % 23)  # 3..25 intermediate dirs, then 'b'
        d = a
        for l in range(depth):
            d = t.dir(d / f"c{i:04d}_{l:02d}")
        b = t.dir(d / "b")
        t.file(b / "m.py")
        t.file(b / "notes.txt")
    for i in range(tops):
        t.file(base / f"top_{i:04d}.py")
    t.end("recursive", chains=chains, depths="3..25", top_py=tops,
          b_files=chains * 2)


def gen_hidden(t: Tree, s: float) -> None:
    ndir = max(1, int(1_000 * s))
    per = 20
    per_hid = 10  # 50% dot-prefixed
    base = ROOT / "hidden"
    for i in range(ndir):
        d = t.dir(base / f"dir_{i:04d}")
        for j in range(per):
            if j < per_hid:
                t.file(d / f".hid_{i:04d}_{j:02d}.py")
            else:
                t.file(d / f"vis_{i:04d}_{j:02d}.py")
    t.end("hidden", dirs=ndir, files=ndir * per, hidden=ndir * per_hid,
          visible=ndir * (per - per_hid))


def gen_symlinks(t: Tree, s: float) -> None:
    nreal_dir = 50
    nreal_files = 100
    nfile_links = int(5_000 * s)
    ndir_links = int(500 * s)
    base = t.dir(ROOT / "symlinks")
    # real leaf dirs, two files each
    for i in range(nreal_dir):
        d = t.dir(base / f"d{i:04d}")
        t.file(d / f"real_{i:04d}_a.txt")
        t.file(d / f"real_{i:04d}_b.txt")
    # file symlinks; 10% point at missing targets (broken)
    for i in range(nfile_links):
        tgt = f"d{i % nreal_dir:04d}/real_{i % nreal_dir:04d}_a.txt"
        if i % 10 == 7:
            tgt = f"missing_{i:04d}.txt"
        t.link(base / f"link_{i:04d}.txt", tgt)
    # dir symlinks into the leaf dirs (bounded re-traversal for **)
    for i in range(ndir_links):
        t.link(base / f"dlink_{i:03d}", f"d{i % nreal_dir:04d}")
    # ONE self-cycle, for behavior observation under a timeout
    cyc = t.dir(base / "cyc")
    t.file(cyc / "inner.txt")
    t.link(cyc / "self", str(cyc))
    t.end("symlinks", real_dirs=nreal_dir, real_files=nreal_files,
          file_links=nfile_links, broken_file_links=nfile_links // 10,
          dir_links=ndir_links, cycle="cyc/self -> cyc")


# ----------------------------------------------------------------- main

def main() -> int:
    s = scale_factor()
    t0 = time.time()
    print(f"free on {MOUNT}: {free_gb():.2f} GB -> scale S={s:.3f}")
    shutil.rmtree(ROOT, ignore_errors=True)

    t = Tree()
    shapes = [
        ("small", gen_small), ("wide", gen_wide), ("deep", gen_deep),
        ("sparse", gen_sparse), ("dense", gen_dense), ("recursive", gen_recursive),
        ("hidden", gen_hidden), ("symlinks", gen_symlinks),
    ]
    for name, fn in shapes:
        t.begin(name)
        st = time.time()
        fn(t, s)
        t.end(name)
        print(f"  {name:<10} files={t.stats[name]['files']:>7} "
              f"dirs={t.stats[name]['dirs']:>6}  ({time.time() - st:.1f}s)")

    # per-subtree stats: file counts + du -sh
    print("\nper-subtree stats (file counts, du -sh):")
    for name, _ in shapes:
        p = ROOT / name
        nfiles = 0
        for _root, _dirs, files in os.walk(p, followlinks=False):
            nfiles += len(files)
        du = subprocess.run(["du", "-sh", str(p)], capture_output=True, text=True)
        size_b = du.stdout.split()[0] if du.stdout.strip() else "?"
        t.stats[name]["du_sh"] = size_b
        t.stats[name]["walk_files"] = nfiles
        print(f"  {name:<10} walk_files={nfiles:>7}  du={size_b}")

    total = subprocess.run(["du", "-sb", str(ROOT)], capture_output=True, text=True)
    total_bytes = int(total.stdout.split()[0]) if total.stdout.strip() else 0
    total_sh = subprocess.run(["du", "-sh", str(ROOT)], capture_output=True, text=True)
    print(f"\nTOTAL: {total_bytes / 1e9:.3f} GB ({total_sh.stdout.strip()})  "
          f"budget {BUDGET_BYTES / 1e9:.1f} GB")
    if total_bytes > BUDGET_BYTES:
        print("ERROR: generated tree exceeds 1.2 GB budget — fix sizes, do not trim silently.",
              file=sys.stderr)
        return 2

    import platform
    import glob as globmod
    manifest = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "generator": "bench/generate_tree.py",
        "scale": s,
        "free_gb_at_gen": free_gb(),
        "budget_bytes": BUDGET_BYTES,
        "total_bytes": total_bytes,
        "python": {
            "executable": sys.executable,
            "version": platform.python_version(),
            "glob_module": globmod.__file__,
        },
        "shapes": t.stats,
        "gen_seconds": round(time.time() - t0, 1),
    }
    with open(ROOT / "MANIFEST.json", "w") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"MANIFEST: {ROOT / 'MANIFEST.json'}")
    print(f"done in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
