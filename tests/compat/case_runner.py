#!/usr/bin/env python3
"""Shared case runner for the Fast Glob compatibility harness (W2 unification).

ONE implementation of the concern previously duplicated mirror-by-copy
between tests/oracle/capture.py and tests/compat/run_engine.py — the copy
drift that motivated this module is audited as maat STRUCTURE S22b/S28:

    execute every tests/fixtures/cases.json case under a hard timeout,
    expand sentinels, run the transient/escape/translate probes, and emit
    capture-schema records consumable by tests/compat/compare.py.

The two capture programs are thin wrappers parameterized by a Subject
(the glob implementation under test):

    subject.glob(pattern, **kwargs)   -> list[str]        results (data)
    subject.escape(pattern)           -> str              escaped pattern
    subject.has_translate()           -> bool             translate exposed?
    subject.translate(pattern)        -> str              translated regex
    subject.label                     -> str              "glob" / "engine"

Record schema (LOAD-BEARING for compare.py — field names never change):
  {case_id, section, kind, pattern, kwargs, note,
   [unspecified_zone],                                   opt-in flag
   result: [...]  |  error: {type, message, timed_out: bool},
   dup_counts: {path: n>1}          glob/transient,
   duration_ms                      diagnostic only,
   base_match: bool|None            cases with a `base` id; None iff the
                                    base case produced no result,
   ops: [...]                       transient audit trail,
   escape + glob_escaped            escape kind (success only),
   exposed / translate [/skipped_not_exposed]           translate kind}

Unified-edge decisions (each was a divergent, unreachable-in-practice
diagnostic edge between the twins; unified here and documented):
  * escape() runs INSIDE the timeout guard so both subjects get the same
    error-as-data envelope; rec["escape"] is recorded on success only.
  * ONE timer mechanism for both sides: signal.setitimer(ITIMER_REAL)
    (SIGALRM), with per-call handler save/restore for process-global
    signal hygiene — replaces the alarm() vs setitimer() split.
  * results_by_id[case_id] is populated for BOTH subjects on success, so
    base_match is a real Counter comparison on both sides. This fixes the
    audited candidate-side drift (base_match=None for k09-k18).
  * progress lines show base_match when present (stderr diagnostics only;
    JSON records are unaffected).
"""
import json
import os
import signal
import sys
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
TREE = os.path.join(REPO, "tests", "fixtures", "tree")
CASES = os.path.join(REPO, "tests", "fixtures", "cases.json")
TIMEOUT_S = 10  # hard per-call cap (dispatch requirement; README/docs cite it)

ROOT_SENTINEL = "@ROOT@"
TREE_SENTINEL = "TREE"


class _Timeout(Exception):
    """Internal control-flow exception raised by the SIGALRM handler."""


def guarded(fn, label, timer_noun="call"):
    """Run fn() under a TIMEOUT_S hard cap; return (value|None, error|None, ms).

    Mechanism (single, shared by both capture sides): SIGALRM via
    signal.setitimer(ITIMER_REAL). The previous SIGALRM handler is saved
    before and restored after each call so harnesses sharing one process
    do not contend on the process-global signal disposition.

    Contract: a timeout surfaces as
        {"type": "Timeout", "message": ..., "timed_out": True}
    any other exception as
        {"type": <exception name>, "message": ..., "timed_out": False}
    — subject errors are data (§8.6), never harness failures. `label` names
    the case in the one-line stderr diagnostic; `timer_noun` completes the
    timeout message ("glob call exceeded Ns..." / "engine call exceeded...").
    """
    def _on_alarm(signum, frame):  # noqa: ARG001
        raise _Timeout(f"{timer_noun} call exceeded {TIMEOUT_S}s hard timeout")

    old = signal.signal(signal.SIGALRM, _on_alarm)
    t0 = time.monotonic()
    try:
        signal.setitimer(signal.ITIMER_REAL, TIMEOUT_S)
        value = fn()
        err = None
    except _Timeout as e:
        value, err = None, {"type": "Timeout", "message": str(e), "timed_out": True}
    except Exception as e:  # subject errors are data (§8.6)
        value, err = None, {"type": type(e).__name__, "message": str(e), "timed_out": False}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)
        ms = (time.monotonic() - t0) * 1000.0
    if err is not None:
        print(f"  {label}: {err['type']} {err['message'][:80]}", file=sys.stderr)
    return value, err, ms


def expand(pattern, kwargs):
    """Resolve sentinels to concrete values; returns (pattern, kwargs, to_close).

    "@ROOT@" in patterns -> absolute fixture-tree path; root_dir=="TREE" ->
    tree path; string dir_fd resolves to a real directory fd opened here
    (relative to cwd=tree, or the TREE sentinel) — caller MUST close every
    fd in `to_close` (run_case does, in its finally block).
    """
    p = pattern.replace(ROOT_SENTINEL, TREE)
    k = dict(kwargs)
    close = []
    if k.get("root_dir") == TREE_SENTINEL:
        k["root_dir"] = TREE
    if isinstance(k.get("dir_fd"), str):
        # string dir_fd = path (relative to cwd=tree, or the TREE sentinel):
        # resolve to a real fd at run time, close it after the call
        path = TREE if k["dir_fd"] == TREE_SENTINEL else k["dir_fd"]
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        k["dir_fd"] = fd
        close.append(fd)
    return p, k, close


def prepare():
    """Common wrapper prologue: verify the fixture tree exists, chdir into it
    (relative pattern forms must resolve against the tree — documented §8.2
    semantics), and load cases.json. Returns the parsed case list."""
    assert os.path.isdir(TREE), f"tree missing: {TREE} (run tests/fixtures/generate.py)"
    os.chdir(TREE)
    with open(CASES) as f:
        return json.load(f)


def run_case(subject, case, results_by_id):
    """Execute one case against `subject`; emit its capture-schema record.

    results_by_id maps case_id -> successful result list and is POPULATED
    HERE FOR EVERY SUBJECT (the S22b drift fix): base_match compares the
    case's result multiset against its `base` case's stored multiset via
    Counter equality; None means the base case had no result to compare.
    Filesystem misses and subject errors are data, not exceptions; the
    transient probe restores the tree byte-identically even on error.
    """
    kind = case.get("kind", "glob")
    rec = {
        "case_id": case["id"],
        "section": case["section"],
        "kind": kind,
        "pattern": case["pattern"],
        "kwargs": case["kwargs"],
        "note": case.get("note", ""),
    }
    if case.get("unspecified_zone"):
        # documented-unspecified zone (e.g. ELOOP cycle depth, Level C): membership
        # differences here classify as 'documented incompatibility', not violations
        rec["unspecified_zone"] = True
    pattern, kwargs, close = expand(case["pattern"], case.get("kwargs", {}))
    try:
        if kind in ("glob", "transient"):
            ops = []
            if kind == "transient":
                # §8.6 disappearing-file probe: create marker, DELETE it, then glob.
                scratch = os.path.join(TREE, "errors", "_transient")
                marker = os.path.join(scratch, "marker.txt")
                os.makedirs(scratch, exist_ok=True)
                with open(marker, "w") as f:
                    f.write("transient\n")
                ops.append("create errors/_transient/marker.txt")
                os.remove(marker)
                ops.append("delete marker.txt")

            def call():
                return subject.glob(pattern, **kwargs)

            value, err, ms = guarded(call, case["id"], subject.label)
            if kind == "transient":
                ops.append("glob after delete -> %r" % (value if err is None else err["type"]))
            if err is None:
                # populated for BOTH subjects — the audited S22b drift fix
                results_by_id[case["id"]] = value
                rec["result"] = value
                dups = {p: n for p, n in Counter(value).items() if n > 1}
                rec["dup_counts"] = dups
                rec["duration_ms"] = round(ms, 3)
                if "base" in case:
                    base = results_by_id.get(case["base"])
                    rec["base_match"] = (Counter(base) == Counter(value)) if base is not None else None
            else:
                rec["error"] = err
                rec["duration_ms"] = round(ms, 3)
            if kind == "transient":
                rec["ops"] = ops
                # restore tree byte-identically (runs even on error); the ops
                # list is aliased by rec["ops"], so this append lands in it
                import shutil
                shutil.rmtree(os.path.join(TREE, "errors", "_transient"))
                ops.append("rmtree errors/_transient (tree restored)")
        elif kind == "escape":
            # escape inside the guard: both subjects get the error-as-data
            # envelope; rec["escape"]/rec["glob_escaped"] on success only
            def call():
                esc = subject.escape(pattern)
                globbed = subject.glob(esc, **kwargs)
                return esc, globbed

            value, err, ms = guarded(call, case["id"], subject.label)
            if err is None:
                rec["escape"] = value[0]
                rec["glob_escaped"] = value[1]
                rec["duration_ms"] = round(ms, 3)
            else:
                rec["error"] = err
                rec["duration_ms"] = round(ms, 3)
        elif kind == "translate":
            if subject.has_translate():
                rec["exposed"] = True
                rec["translate"] = subject.translate(pattern)
            else:
                # not exposed by this subject (installed reference 3.12:
                # __all__ = glob, iglob, escape); record must not claim a value
                rec["exposed"] = False
                rec["translate"] = None
                rec["skipped_not_exposed"] = True
        else:
            rec["error"] = {"type": "UnknownKind", "message": f"unknown kind {kind}", "timed_out": False}
    finally:
        for fd in close:
            os.close(fd)
    return rec


def describe_record(rec):
    """Progress-line projection of a record: returns (status, extra) where
    status is 'ok', the error type, or 'skipped_not_exposed', and extra
    carries n=/dups=/base_match= diagnostics. stderr display only — never
    consumed from the JSON records."""
    if "result" in rec:
        status, nres = "ok", len(rec["result"])
    elif rec.get("kind") == "escape" and "glob_escaped" in rec:
        status, nres = "ok", len(rec["glob_escaped"])
    else:
        status, nres = rec.get("error", {}).get("type", "?"), 0
    extra = f" n={nres}"
    if rec.get("dup_counts"):
        extra += f" dups={rec['dup_counts']}"
    if rec.get("base_match") is not None:
        extra += f" base_match={rec['base_match']}"
    if rec.get("skipped_not_exposed"):
        status = "skipped_not_exposed"
    return status, extra


def run_all(cases, subject):
    """Run every case against `subject`; return the records list WITHOUT the
    _meta element (wrappers prepend their own provenance). Prints one
    '[i/N] id (section) status' progress line per case to stderr."""
    records = []
    results_by_id = {}
    for i, case in enumerate(cases, 1):
        rec = run_case(subject, case, results_by_id)
        records.append(rec)
        status, extra = describe_record(rec)
        print(f"[{i:3d}/{len(cases)}] {case['id']} ({case['section']}) {status}{extra}",
              file=sys.stderr)
    return records
