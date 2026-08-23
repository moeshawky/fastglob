#!/usr/bin/env python3
"""IN-PROCESS baseline benchmark for Python stdlib `glob` — 8-shape matrix.

Oracle (identified at runtime, never hard-coded):
    python3, `import glob` of the running interpreter.

METHOD (docs/benchmark-discipline.md §10.1A, §11):
  * All timed reps run inside THIS already-running interpreter (one long-lived
    process, fixed ROW order) so the page-cache state is identical across rows.
  * Candidate runs MUST reuse this file's ROWS order (import ROWS / re-run this
    harness) to keep cache state equivalent — do not benchmark a candidate in
    a fresh process per shape and call it in-process.
  * Warm-up reps per row (<=5; early-stop at 2 when reps exceed 5 s).
  * Adaptive rep count, a deterministic function of the ORACLE warmup median
    (never of any candidate), chosen BEFORE timed reps start:
        med < 0.1 s -> 51 | < 0.5 s -> 41 | < 1 s -> 31
        < 5 s -> 21 | < 20 s -> 11 | else -> 9
    capped so that reps * med <= 240 s. The choice + rationale are recorded
    per workload; ALL raw rep times are persisted (no cherry-picking).
  * Per-rep SIGALRM timeout = max(60 s, 10 * med). A timed-out rep is stored
    as null + timed_out flag, never as 0. (Pre-verified on this oracle: a
    self-symlink cycle under `**` terminates after 41 symlinked levels —
    see observations in baseline.md — so a timeout here is an anomaly to
    report, not a hidden floor.)
  * PROCESS-STARTUP rows: one fresh `python3 -c` per shape (subprocess wall
    time, interpreter startup INCLUDED). Clearly labeled; never mixed into
    the in-process table (§10.1D).

Outputs: bench/results/baseline.json (raw reps + config) and
         bench/results/baseline.md (human-readable report).
"""

import glob
import json
import math
import os
import platform
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

BENCH = Path(__file__).resolve().parent
TREE = BENCH / "trees"
RESULTS = BENCH / "results"
MANIFEST = TREE / "MANIFEST.json"

SUPPORTS_INCLUDE_HIDDEN = "include_hidden" in (glob.glob.__kwdefaults__ or {}) \
    or "include_hidden" in glob.glob.__code__.co_varnames


# --------------------------------------------------------------- workload rows
# (shape, pattern_suffix, api, kwargs). Pattern is joined under
# bench/trees/<shape>. api: "glob" | "iglob".
#
# Pattern choice per shape (oracle semantics VERIFIED on the installed
# interpreter before freezing):
#   * `*` never matches dot-prefixed names (documented);
#   * `**` does not descend into hidden dirs unless include_hidden=True;
#   * a final-component dot pattern (e.g. `**/.hid_*.py`) matches dotfiles.
ROWS = [
    # small: startup + fixed overhead
    ("small", "**/*.py", "glob", {}),
    ("small", "**/*", "glob", {}),
    ("small", "*.md", "glob", {}),
    # wide: directory-enumeration throughput
    ("wide", "*", "glob", {}),
    ("wide", "*", "iglob", {}),
    ("wide", "*.dat", "glob", {}),
    # deep: recursive traversal overhead (500 levels)
    ("deep", "**/node_*.txt", "glob", {}),
    # NOTE: rows need not list recursive=True — effective_kwargs() derives it
    # from '**' in the pattern (Level A).
    # sparse: traversal dominates, ~0.1% matches
    ("sparse", "**/*.py", "glob", {}),
    ("sparse", "*", "glob", {}),
    # dense: matching + result materialization dominate (60% matches)
    ("dense", "**/*.py", "glob", {}),
    ("dense", "**/*.py", "iglob", {}),
    # recursive: ** layouts, zero-match probe included
    ("recursive", "**/b/m.py", "glob", {}),
    ("recursive", "**/*.py", "glob", {}),
    ("recursive", "a/**/zzz_*.py", "glob", {}),
    # hidden: hidden filtering overhead + include_hidden variant
    ("hidden", "**/vis_*.py", "glob", {}),
    ("hidden", "**/.hid_*.py", "glob", {}),
    ("hidden", "**/vis_*.py", "glob", {"include_hidden": True}),
    # symlinks: traversal costs + the ONE self-cycle (timeout-guarded)
    ("symlinks", "*", "glob", {}),
    ("symlinks", "**", "glob", {}),
]

# primary pattern per shape for the PROCESS-STARTUP probe
PRIMARY = {
    "small": ("**/*.py", {"recursive": True}),
    "wide": ("*", {}),
    "deep": ("**/node_*.txt", {"recursive": True}),
    "sparse": ("**/*.py", {"recursive": True}),
    "dense": ("**/*.py", {"recursive": True}),
    "recursive": ("**/b/m.py", {"recursive": True}),
    "hidden": ("**/vis_*.py", {"recursive": True}),
    "symlinks": ("**", {"recursive": True}),
}


# ------------------------------------------------------------------ helpers

class _Timeout(Exception):
    pass


def effective_kwargs(pattern: str, kwargs: dict) -> dict:
    """In this oracle `**` is recursive ONLY with recursive=True (Level A,
    docstring: "If recursive is true, the pattern '**' will match any files
    and zero or more directories"). Without it, `**` degrades to a single
    wildcard level — a silent semantics change, so derive it from the pattern."""
    kw = dict(kwargs)
    if "**" in pattern:
        kw.setdefault("recursive", True)
    return kw


def _on_alarm(_signum, _frame):
    raise _Timeout()


def timed_call(api: str, pattern: str, kwargs: dict, timeout: float):
    """Run one glob call under a SIGALRM watchdog. Returns (seconds, n_results).

    Uses ITIMER_REAL (SIGALRM) consistent with tests/compat/run_engine.py.
    The per-call handler is saved/restored to avoid contention when both
    harnesses run in the same process (Level A: signal handler is process-global).
    """
    def run():
        if api == "glob":
            return len(glob.glob(pattern, **kwargs))
        return len(list(glob.iglob(pattern, **kwargs)))

    old = signal.signal(signal.SIGALRM, _on_alarm)
    try:
        signal.setitimer(signal.ITIMER_REAL, timeout)
        try:
            t0 = time.perf_counter()
            n = run()
            dt = time.perf_counter() - t0
        except _Timeout:
            return None, None
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
    finally:
        signal.signal(signal.SIGALRM, old)
    return dt, n


def choose_reps(med: float):
    """Deterministic rep-count band from the oracle warmup median."""
    if med < 0.1:
        reps, why = 51, "med<0.1s: fast workload, 51 reps to expose variance"
    elif med < 0.5:
        reps, why = 41, "med<0.5s: 41 reps"
    elif med < 1.0:
        reps, why = 31, "med<1s: 31 reps"
    elif med < 5.0:
        reps, why = 21, "med<5s: 21 reps"
    elif med < 20.0:
        reps, why = 11, "med<20s: 11 reps"
    else:
        reps, why = 9, "med>=20s: 9 reps (slow workload)"
    while reps * med > 240.0 and reps > 5:
        reps = max(5, reps // 2)
        why += f"; capped to keep reps*med<=240s -> {reps}"
    return reps, why


def pct(values, p):
    """Nearest-rank percentile on a list of floats."""
    vs = sorted(values)
    k = max(0, min(len(vs) - 1, int(math.ceil(p / 100.0 * len(vs))) - 1))
    return vs[k]


def sh(cmd, cwd=None):
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return r.stdout.strip()


def machine_info() -> dict:
    return {
        "nproc": os.cpu_count(),
        "uname": sh(["uname", "-a"]),
        "filesystem": sh(["stat", "-f", "-c", "%T", str(TREE.parent)]),
        "df_free_gb": round(shutil_free_gb(), 2),
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "glob_module": glob.__file__,
        "include_hidden_supported": SUPPORTS_INCLUDE_HIDDEN,
        "git_head": sh(["git", "rev-parse", "--short", "HEAD"],
                       cwd=str(BENCH.parent)) or "n/a",
    }


def shutil_free_gb() -> float:
    import shutil
    return shutil.disk_usage("/kaggle/working").free / 1e9


def tree_stats(manifest: dict) -> dict:
    """Manifest stats + live recount (integrity check)."""
    out = {}
    for name, st in manifest.get("shapes", {}).items():
        p = TREE / name
        nfiles = 0
        for _r, _d, files in os.walk(p, followlinks=False):
            nfiles += len(files)
        out[name] = {
            "manifest_files": st.get("files"),
            "manifest_dirs": st.get("dirs"),
            "manifest_du_sh": st.get("du_sh"),
            "live_files": nfiles,
            "params": {k: v for k, v in st.items()
                       if k not in ("files", "dirs", "du_sh", "walk_files")},
        }
    return out


# ------------------------------------------------------------------ benching

def bench_row(shape: str, pattern: str, api: str, kwargs: dict) -> dict:
    # pattern may contain '**' segments; plain path join is correct
    full = os.path.join(str(TREE / shape), pattern)
    kwargs = effective_kwargs(pattern, kwargs)

    # warm-up (unmeasured), early-stop on slow workloads
    warm = []
    for _ in range(5):
        dt, n = timed_call(api, full, kwargs, timeout=300.0)
        if dt is None:
            return {
                "shape": shape, "pattern": pattern, "api": api, "kwargs": kwargs,
                "status": "WARMUP_TIMEOUT", "note": "timed out during warm-up",
            }
        warm.append(dt)
        if len(warm) >= 2 and warm[-1] > 5.0 and warm[-2] > 5.0:
            break
    med = statistics.median(warm)
    reps, rationale = choose_reps(med)
    timeout = max(60.0, 10.0 * med)

    times, counts, timeouts = [], [], 0
    for _ in range(reps):
        dt, n = timed_call(api, full, kwargs, timeout=timeout)
        if dt is None:
            times.append(None)
            counts.append(None)
            timeouts += 1
        else:
            times.append(dt)
            counts.append(n)

    vals = [t for t in times if t is not None]
    rec = {
        "shape": shape, "pattern": pattern, "api": api, "kwargs": kwargs,
        "status": "OK" if timeouts == 0 else ("TIMEOUTS" if vals else "ALL_TIMEOUT"),
        "warmup_reps_s": warm,
        "med_warmup_s": med,
        "reps_chosen": reps,
        "reps_rationale": rationale,
        "timeout_s": timeout,
        "n_timeouts": timeouts,
        "reps_s": times,
        "n_results": counts,
        "n_results_first": next((c for c in counts if c is not None), None),
    }
    if vals:
        rec.update({
            "median_s": statistics.median(vals),
            "p95_s": pct(vals, 95),
            "min_s": min(vals),
            "max_s": max(vals),
            "stdev_s": statistics.pstdev(vals) if len(vals) > 1 else 0.0,
        })
    return rec


def process_startup_rows() -> list:
    """One FRESH python3 per shape; includes interpreter startup. Labeled."""
    rows = []
    for shape in (PRIMARY):
        pattern, kwargs = PRIMARY[shape]
        full = os.path.join(str(TREE / shape), pattern)
        kw = {**kwargs}
        kwsrc = ", ".join(f"{k}={v!r}" for k, v in kw.items())
        code = (
            "import glob\n"
            f"glob.glob({full!r}{', ' + kwsrc if kwsrc else ''})\n"
        )
        t0 = time.perf_counter()
        subprocess.run([sys.executable, "-c", code], check=True,
                       capture_output=True)
        dt = time.perf_counter() - t0
        rows.append({
            "shape": shape, "pattern": pattern, "api": "glob", "kwargs": kw,
            "note": "PROCESS-STARTUP: fresh python3 -c, interpreter startup INCLUDED",
            "wall_s": dt,
        })
    return rows


# ------------------------------------------------------------------- report

def write_md(path: Path, machine: dict, tstats: dict, rows: list,
             startup: list, manifest: dict) -> None:
    def ms(x):
        return "  -  " if x is None else f"{x * 1000:.3f}"

    L = []
    L.append("# Fast Glob — IN-PROCESS stdlib `glob` baseline")
    L.append("")
    L.append(f"Generated: {time.strftime('%Y-%m-%dT%H:%M:%S%z')} by bench/bench.py")
    L.append(f"Scale factor at generation: {manifest.get('scale')}")
    L.append("")
    L.append("## Environment")
    L.append("")
    L.append(f"- nproc: {machine['nproc']}")
    L.append(f"- uname: {machine['uname']}")
    L.append(f"- filesystem: {machine['filesystem']}")
    L.append(f"- df free (GB): {machine['df_free_gb']}")
    L.append(f"- python: {machine['python_executable']} ({machine['python_version']})")
    L.append(f"- glob module: {machine['glob_module']}")
    L.append(f"- include_hidden supported: {machine['include_hidden_supported']}")
    L.append(f"- git head: {machine['git_head']}")
    L.append("")
    L.append("## Tree stats (manifest vs live recount)")
    L.append("")
    L.append("| shape | files (manifest/live) | dirs | du -sh | params |")
    L.append("|---|---|---|---|---|")
    for name, st in tstats.items():
        params = json.dumps(st["params"])
        L.append(f"| {name} | {st['manifest_files']}/{st['live_files']} "
                 f"| {st['manifest_dirs']} | {st['manifest_du_sh']} | {params} |")
    L.append("")
    L.append("## IN-PROCESS results (already-running interpreter; fixed ROW order)")
    L.append("")
    L.append("median/p95/min/max in ms; n = timed reps; nres = results (first rep). "
             "`-` = timed-out rep (SIGALRM), counted in n.")
    L.append("")
    L.append("| shape | pattern | api | kwargs | median ms | p95 ms | min ms | max ms | n | nres |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        if r.get("status") != "OK" and r.get("status") != "TIMEOUTS":
            L.append(f"| {r['shape']} | {r['pattern']} | {r['api']} | "
                     f"{r['kwargs']} | **{r['status']}** | - | - | - | - | - |")
            continue
        kw = json.dumps(r["kwargs"]) if r["kwargs"] else "{}"
        L.append(
            f"| {r['shape']} | `{r['pattern']}` | {r['api']} | {kw} "
            f"| {ms(r.get('median_s'))} | {ms(r.get('p95_s'))} "
            f"| {ms(r.get('min_s'))} | {ms(r.get('max_s'))} "
            f"| {r['reps_chosen']} | {r.get('n_results_first')} |"
        )
    L.append("")
    L.append("## Rep counts and rationale (deterministic band on oracle warmup median)")
    L.append("")
    L.append("| shape | pattern | api | med warmup (s) | reps | rationale |")
    L.append("|---|---|---|---|---|---|")
    for r in rows:
        if "reps_chosen" not in r:
            continue
        L.append(f"| {r['shape']} | `{r['pattern']}` | {r['api']} "
                 f"| {r['med_warmup_s']:.4f} | {r['reps_chosen']} | {r['reps_rationale']} |")
    L.append("")
    L.append("## PROCESS-STARTUP (fresh `python3 -c` per shape — interpreter "
             "startup INCLUDED; NOT part of the in-process table, §10.1D)")
    L.append("")
    L.append("| shape | pattern | api | kwargs | wall ms |")
    L.append("|---|---|---|---|---|")
    for r in startup:
        kw = json.dumps(r["kwargs"]) if r["kwargs"] else "{}"
        L.append(f"| {r['shape']} | `{r['pattern']}` | {r['api']} | {kw} "
                 f"| {r['wall_s'] * 1000:.1f} |")
    L.append("")
    L.append("## Observations (this run)")
    L.append("")
    cyc = next((r for r in rows if r["shape"] == "symlinks"
                and r["pattern"] == "**"), None)
    if cyc:
        if cyc.get("status") == "OK":
            L.append(f"- Symlink self-cycle under `**`: oracle TERMINATED "
                     f"(VERIFIED). n_results={cyc.get('n_results_first')}, "
                     f"median={ms(cyc.get('median_s'))} ms. A self-cycle follows "
                     f"exactly 41 symlinked levels before stopping (OBSERVED on "
                     f"this build; undocumented — Level C, not a requirement).")
        else:
            L.append(f"- Symlink self-cycle under `**`: {cyc.get('status')} "
                     f"(timeout={cyc.get('timeout_s')}s) — ANOMALY vs pre-verified "
                     f"terminating behavior; investigate before citing numbers.")
    L.append("- Cache state: one long-lived interpreter, fixed ROW order — "
             "candidate runs must reuse the same order for equivalence.")
    L.append("")
    path.write_text("\n".join(L))


# --------------------------------------------------------------------- main

def main() -> int:
    if not MANIFEST.exists():
        print(f"missing {MANIFEST} — run bench/generate_tree.py first",
              file=sys.stderr)
        return 1
    manifest = json.loads(MANIFEST.read_text())
    signal.signal(signal.SIGALRM, _on_alarm)
    machine = machine_info()
    tstats = tree_stats(manifest)

    print("in-process benchmark: stdlib glob oracle")
    print(f"machine: nproc={machine['nproc']} fs={machine['filesystem']} "
          f"py={machine['python_version']}")
    rows = []
    for shape, pattern, api, kwargs in ROWS:
        r = bench_row(shape, pattern, api, kwargs)
        rows.append(r)
        if "median_s" in r:
            print(f"  {shape:<10} {pattern:<16} {api:<5} "
                  f"med={r['median_s'] * 1000:9.3f} ms  n={r['reps_chosen']:<3} "
                  f"nres={r['n_results_first']}  status={r['status']}")
        else:
            print(f"  {shape:<10} {pattern:<16} {api:<5} status={r['status']}")

    print("process-startup probes (fresh python3, labeled PROCESS-STARTUP):")
    startup = process_startup_rows()
    for r in startup:
        print(f"  {r['shape']:<10} {r['pattern']:<16} {r['wall_s'] * 1000:8.1f} ms")

    RESULTS.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "harness": "bench/bench.py",
        "oracle": machine,
        "manifest": manifest,
        "tree_stats": tstats,
        "method": {
            "in_process": True,
            "fixed_row_order": True,
            "warmup": "<=5 reps, early-stop at 2 when >5s",
            "adaptive_reps": "band on oracle warmup median: 51/41/31/21/11/9, "
                             "cap reps*med<=240s",
            "per_rep_timeout": "max(60s, 10*med_warm) via SIGALRM",
            "percentile": "nearest-rank",
            "process_startup": "one fresh python3 -c per shape, startup included",
        },
        "rows": rows,
        "process_startup": startup,
    }
    (RESULTS / "baseline.json").write_text(json.dumps(payload, indent=2))
    write_md(RESULTS / "baseline.md", machine, tstats, rows, startup, manifest)
    print(f"\nwrote {RESULTS / 'baseline.json'} and {RESULTS / 'baseline.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
