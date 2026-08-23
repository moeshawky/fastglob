#!/usr/bin/env python3
"""Candidate capture runner for the fastglob engine (D4a deliverable).

Thin wrapper (W2 unification): all runner mechanics live in the shared
module tests/compat/case_runner.py. This file binds them to the engine
under test — the python/fastglob compat package, which calls the
in-process native engine (fastglob._core) — and writes a candidate capture
in the SAME schema as
tests/oracle/capture.json, for consumption by tests/compat/compare.py:

    python3 tests/compat/run_engine.py --out tests/compat/candidate.json
    python3 tests/compat/compare.py --candidate tests/compat/candidate.json

Results are byte-exact: the engine emits NUL-delimited raw bytes and the
package fsdecodes with surrogateescape, matching the oracle's str
representation of arbitrary byte names. translate: the installed 3.12
reference does not expose glob.translate, so the candidate records
exposed=False / skipped_not_exposed (compare.py rejects any claimed value
when the oracle reports the same).

_meta carries candidate provenance (candidate, candidate_version,
candidate_bin — the in-process engine module path since 0.1.1) alongside
the shared fields; record schema is identical to the oracle's (case_runner
docstring) — compare.py consumes both.
"""
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
DEFAULT_OUT = os.path.join(HERE, "candidate.json")

sys.path.insert(0, os.path.join(REPO, "python"))
import fastglob  # noqa: E402  (the compat package under test)
import case_runner  # noqa: E402  (shared runner; script dir = tests/compat)


class _FastglobSubject:
    """Subject binding case_runner to the fastglob compat package.

    label="engine" so timeout messages read "engine call exceeded ..."
    exactly as the pre-unification candidate capture did.
    """

    label = "engine"

    @staticmethod
    def glob(pattern, **kwargs):
        return fastglob.glob(pattern, **kwargs)

    @staticmethod
    def escape(pattern):
        return fastglob.escape(pattern)

    @staticmethod
    def has_translate():
        return hasattr(fastglob, "translate")

    @staticmethod
    def translate(pattern):
        return fastglob.translate(pattern)


def main():
    out_path = DEFAULT_OUT
    argv = sys.argv[1:]
    if "--out" in argv:
        out_path = argv[argv.index("--out") + 1]
    out_path = os.path.abspath(out_path)

    cases = case_runner.prepare()
    meta = {
        "_meta": True,
        "candidate": "fastglob",
        "candidate_version": fastglob.__version__,
        "candidate_bin": f"in-process {getattr(fastglob._core, '__file__', 'fastglob._core')}",
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "uid": os.getuid(),
        "tree": case_runner.TREE,
        "cwd": os.getcwd(),
        "timeout_s": case_runner.TIMEOUT_S,
        "cases": len(cases),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    print(f"candidate: {meta['candidate_bin']} (via python package {meta['candidate_version']})",
          file=sys.stderr)
    records = [meta] + case_runner.run_all(cases, _FastglobSubject())
    with open(out_path, "w") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
        f.write("\n")
    n_err = sum(1 for r in records[1:] if "error" in r)
    print(f"wrote {out_path}: {len(cases)} cases, {n_err} candidate errors", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
