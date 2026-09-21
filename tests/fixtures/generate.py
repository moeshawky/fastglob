#!/usr/bin/env python3
"""Deterministic compatibility fixture generator for Fast Glob (docs/compatibility-contract.md §8.1–8.9).

Produces tests/fixtures/tree/ — the ONLY tree the oracle capture globs over
(benchmark trees are separate and live under bench/).

Idempotent: wipes and rebuilds the tree from scratch on every run, so two
consecutive runs produce byte-identical structure. A manifest hash
(path, type, mode, symlink-target, size) is printed for idempotency auditing.

Design notes (evidence from the installed oracle, Python 3.12.3):
- Symlink cycles (cycle/self -> .., mutual/x <-> mutual/y) are REQUIRED (§8.5).
  CPython 3.12's ** traversal terminates on this kernel via ELOOP at
  SYMLOOP_MAX=40 symlink hops (scandir OSError silently pruned in _iterdir).
  Depth 41 is an OBSERVED kernel artifact, not a requirement — the hard
  requirement is only that no call hangs (capture enforces a 10s timeout).
- errors/unreadable is chmod 000 (mode 0, owner uid 1001). As non-root it is
  NOT readable: scandir raises PermissionError, which stdlib glob silently
  prunes to []. The committed capture was recorded as uid 1001 and its results
  are the non-root ones (re-measured 2026-09-21) — no error policy is
  fabricated (§8.6).
- The generator chmods errors/unreadable back to 755 before wiping, so a
  non-root re-run can still rebuild.
"""
import hashlib
import os
import shutil
import stat
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TREE = os.path.join(HERE, "tree")

# ---------------------------------------------------------------- layout
# Subtree names are part of the case patterns in tests/fixtures/cases.json.
SUBTREES = ("basic", "literal", "hidden", "recursive", "a", "symlinks",
            "errors", "weird")

FILES = {
    # §8.1 basic matching: single chars a-d + uppercase A (class boundaries),
    # 2-3 char combos, digit-suffixed, extensions, one empty dir.
    "basic/a": "alpha\n",
    "basic/b": "beta\n",
    "basic/c": "gamma\n",
    "basic/d": "delta\n",
    "basic/A": "upper\n",
    "basic/ab": "alpha-beta\n",
    "basic/ac": "alpha-charlie\n",
    "basic/abc": "alpha-beta-charlie\n",
    "basic/x1": "one\n",
    "basic/y2": "two\n",
    "basic/z3": "three\n",
    "basic/file.py": "print('basic')\n",
    "basic/script.sh": "#!/bin/sh\necho hi\n",
    "basic/notes.txt": "notes\n",
    # §8.2 literal path components
    "literal/exact_name.txt": "literal target file\n",
    "literal/sub/deep_target.txt": "deep literal target\n",
    "literal/target/inner.txt": "inner literal target\n",
    # §8.3 hidden files
    "hidden/visible": "visible file\n",
    "hidden/.hidden": "hidden file\n",
    "hidden/visible_dir/v1.txt": "in visible dir\n",
    "hidden/.hidden_dir/h1.txt": "in hidden dir\n",
    # §8.4 recursive ** (cycle-free on purpose — cycles live in symlinks/)
    "recursive/t.txt": "root file of recursive tree\n",
    "recursive/level1/one.py": "print('one')\n",
    "recursive/level1/two.txt": "two\n",
    "recursive/level1/level2/three.py": "print('three')\n",
    "recursive/level1/level2/level3/deep.py": "print('deep')\n",
    # §8.4 zero-directory ** matching: a/**/b must match a/b, a/c/b, a/d/e/b
    "a/b": "b at level 0\n",
    "a/c/b": "b at level 1\n",
    "a/d/e/b": "b at level 2\n",
    # §8.6 filesystem errors
    "errors/unreadable/secret.txt": "unreadable dir content\n",
    "errors/visible.txt": "visible error-tree file\n",
}

DIRS = (
    "basic/empty",
    "literal/sub",
    "literal/target",
    "hidden/visible_dir",
    "hidden/.hidden_dir",
    "recursive/level1/level2/level3",
    "a/c",
    "a/d/e",
    "errors/unreadable",
    "symlinks/chain",
    "symlinks/cycle",
    "symlinks/mutual",
    "weird",
)

# §8.5 symlinks: (link path relative to tree, target string)
SYMLINKS = (
    ("symlinks/to_file", "../basic/a"),            # symlink to file
    ("symlinks/to_dir", "../hidden/visible_dir"),  # symlink to directory
    ("symlinks/broken", "does_not_exist_target"),  # broken symlink
    ("symlinks/chain/one", "two"),                 # chain: one -> two -> to_file
    ("symlinks/chain/two", "../to_file"),
    ("symlinks/cycle/self", ".."),                 # cycle: self -> parent
    ("symlinks/mutual/x", "y"),                    # cycle: x <-> y
    ("symlinks/mutual/y", "x"),
)

# §8.7 pathological filenames (names written verbatim; raw_byte is bytes-only)
WEIRD_TEXT = {
    "with space.txt": "space name\n",
    "tab\there.txt": "tab name\n",
    "üñíçødé_ünïcode.txt": "unicode name\n",
    "emoji_🚀.txt": "emoji name\n",
    "-leading-dash.txt": "leading dash\n",
    "single'quote.txt": "single quote\n",
    'double"quote.txt': "double quote\n",
    "star*and?bracket[.txt": "literal metachars in name\n",
    "new\nline.txt": "newline in name\n",
    "back\\slash.txt": "literal backslash in name\n",
    "glob**all.txt": "literal ** in name\n",
}
WEIRD_BYTES = {"raw_byte_\xff_file.txt": b"raw byte name\n"}


def wipe():
    if os.path.lexists(TREE):
        # restore the 000 dir so a non-root can delete its children
        up = os.path.join(TREE, "errors", "unreadable")
        if os.path.isdir(up):
            os.chmod(up, 0o755)
        shutil.rmtree(TREE)
    os.makedirs(TREE)


def build():
    wipe()
    for rel, content in FILES.items():
        p = os.path.join(TREE, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
    for rel in DIRS:
        os.makedirs(os.path.join(TREE, rel), exist_ok=True)
    for rel, target in SYMLINKS:
        p = os.path.join(TREE, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        os.symlink(target, p)
    for rel, content in WEIRD_TEXT.items():
        p = os.path.join(TREE, "weird", rel)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
    for rel, content in WEIRD_BYTES.items():
        p = os.path.join(TREE, "weird", rel)
        with open(p, "wb") as f:
            f.write(content)
    # §8.6: unreadable directory (000). As non-root (uid 1001) the capture sees
    # scandir PermissionError -> []; no error policy is fabricated.
    os.chmod(os.path.join(TREE, "errors", "unreadable"), 0o000)


def manifest():
    """Sorted (relpath, type, mode, symlink-target, size) list."""
    rows = []
    for dirpath, dirnames, filenames in os.walk(TREE):
        for name in dirnames + filenames:
            p = os.path.join(dirpath, name)
            rel = os.path.relpath(p, TREE)
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode):
                kind, target = "link", os.readlink(p)
            elif stat.S_ISDIR(st.st_mode):
                kind, target = "dir", ""
            else:
                kind, target = "file", ""
            rows.append((rel, kind, stat.S_IMODE(st.st_mode), target, st.st_size))
    rows.sort()
    return rows


def manifest_hash(rows):
    h = hashlib.sha256()
    for r in rows:
        h.update("|".join(map(str, r)).encode("utf-8", "surrogateescape"))
        h.update(b"\n")
    return h.hexdigest()


def main():
    build()
    rows = manifest()
    n_files = sum(1 for r in rows if r[1] == "file")
    n_dirs = sum(1 for r in rows if r[1] == "dir")
    n_links = sum(1 for r in rows if r[1] == "link")
    size = sum(r[4] for r in rows if r[1] == "file")
    print(f"tree: {TREE}")
    print(f"entries: {len(rows)} (files={n_files} dirs={n_dirs} symlinks={n_links})")
    print(f"file bytes: {size}")
    print(f"manifest sha256: {manifest_hash(rows)}")
    # disk budget gate: <= 300 MB
    st = os.stat(TREE)
    approx_mb = (st.st_blocks * 512) / 1e6
    print(f"du approx: {approx_mb:.2f} MB (budget 300 MB)")
    if approx_mb > 300:
        print("FAIL: tree exceeds 300 MB budget", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
