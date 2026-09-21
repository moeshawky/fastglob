# Fast Glob — IN-PROCESS stdlib `glob` baseline

Generated: 2026-08-19T16:47:02+0000 by bench/bench.py
Scale factor at generation: 1.0

> **Provenance — re-measured 2026-09-21. These numbers were NOT produced on the machine
> that ships this repository.** They are a measurement *record*, kept because
> benchmark-discipline §11 requires a baseline to carry the conditions it was measured
> under. Do not quote them as this machine's performance, and do not diff them against a
> run on another architecture — §12's speedup ratio is only meaningful between runs on
> the SAME machine. Measured differences (both machines' conditions recorded):
>
> | condition | generating machine (2026-08-19) | current box (measured 2026-09-21) |
> |---|---|---|
> | architecture / host | x86_64, `d03433004e87` | **aarch64**, `forge-hive` |
> | kernel | 6.6.143+ | 6.17.0-1020-oracle |
> | nproc | 224 | **2** |
> | python | `/usr/local/bin/python3` (3.12.13) | `/usr/bin/python3` (3.12.3) |
> | glob module | `/usr/local/lib/python3.12/glob.py` | `/usr/lib/python3.12/glob.py` |
> | df free (GB) | 10.2 | 23 |
> | git head | `5668b01` | `aacfa41` |
> | uid | not recorded by bench.py | 1001 (non-root) |
>
> Both paths in the `python` / `glob module` rows are **absent on this box** (verified:
> `/usr/local/bin/python3` MISSING, `/usr/local/lib/python3.12/glob.py` MISSING). `5668b01`
> is **not a commit in this repository's available history** (7 commits, non-shallow,
> `fc087c7`…`aacfa41`), so this record is not traceable to any state of this repo —
> regenerate it here with `make bench` (README §Baseline) rather than quoting it.
>
> `bench/bench.py` is shim-neutral: under `PYTHONPATH=/opt/fastglob-shim` it resolves the
> TRUE stdlib oracle `/usr/lib/python3.12/glob.py` (verified 2026-09-21), where a naive
> `import glob` would instead resolve to the shim (`/opt/fastglob-shim/glob.py`).

## Environment

- nproc: 224
- uname: Linux d03433004e87 6.6.143+ #1 SMP Fri Aug 14 11:09:17 UTC 2026 x86_64 GNU/Linux
- filesystem: ext2/ext3
- df free (GB): 10.2
- python: /usr/local/bin/python3 (3.12.13)   # generating machine; absent here (this box: 3.12.3 at /usr/bin/python3)
- glob module: /usr/local/lib/python3.12/glob.py   # generating machine; absent here (this box: /usr/lib/python3.12/glob.py)
- include_hidden supported: True
- git head: 5668b01   # not a commit in this repository's history — see Provenance

## Tree stats (manifest vs live recount)

| shape | files (manifest/live) | dirs | du -sh | params |
|---|---|---|---|---|
| small | 1000/1000 | 9 | 4.0M | {} |
| wide | 100000/100000 | 1 | 395M | {} |
| deep | 500/500 | 501 | 4.0M | {} |
| sparse | 40000/40000 | 2000 | 165M | {} |
| dense | 40000/40000 | 2000 | 165M | {} |
| recursive | 3500/3500 | 22457 | 102M | {} |
| hidden | 20000/20000 | 1000 | 83M | {} |
| symlinks | 5602/5101 | 52 | 804K | {} |

## IN-PROCESS results (already-running interpreter; fixed ROW order)

median/p95/min/max in ms; n = timed reps; nres = results (first rep). `-` = timed-out rep (SIGALRM), counted in n.

| shape | pattern | api | kwargs | median ms | p95 ms | min ms | max ms | n | nres |
|---|---|---|---|---|---|---|---|---|---|
| small | `**/*.py` | glob | {"recursive": true} | 2.106 | 2.164 | 2.092 | 2.203 | 51 | 501 |
| small | `**/*` | glob | {"recursive": true} | 2.504 | 2.572 | 2.478 | 2.588 | 51 | 1008 |
| small | `*.md` | glob | {} | 0.029 | 0.038 | 0.029 | 0.050 | 51 | 1 |
| wide | `*` | glob | {} | 165.780 | 167.663 | 165.045 | 169.630 | 41 | 100000 |
| wide | `*` | iglob | {} | 163.933 | 166.473 | 154.113 | 169.563 | 41 | 100000 |
| wide | `*.dat` | glob | {} | 160.542 | 161.523 | 152.345 | 161.896 | 41 | 95000 |
| deep | `**/node_*.txt` | glob | {"recursive": true} | 169.448 | 172.108 | 168.475 | 173.444 | 41 | 500 |
| sparse | `**/*.py` | glob | {"recursive": true} | 116.766 | 119.006 | 110.948 | 119.594 | 41 | 40 |
| sparse | `*` | glob | {} | 3.102 | 3.128 | 3.078 | 3.141 | 51 | 2000 |
| dense | `**/*.py` | glob | {"recursive": true} | 138.962 | 141.472 | 130.719 | 142.048 | 41 | 24000 |
| dense | `**/*.py` | iglob | {"recursive": true} | 140.172 | 142.012 | 138.659 | 142.409 | 41 | 24000 |
| recursive | `**/b/m.py` | glob | {"recursive": true} | 730.711 | 735.316 | 724.837 | 737.784 | 31 | 1500 |
| recursive | `**/*.py` | glob | {"recursive": true} | 952.127 | 961.900 | 946.603 | 978.666 | 31 | 2000 |
| recursive | `a/**/zzz_*.py` | glob | {"recursive": true} | 935.322 | 949.398 | 922.379 | 950.533 | 31 | 0 |
| hidden | `**/vis_*.py` | glob | {"recursive": true} | 66.551 | 67.497 | 65.929 | 67.852 | 51 | 10000 |
| hidden | `**/.hid_*.py` | glob | {"recursive": true} | 64.042 | 64.886 | 62.655 | 66.506 | 51 | 10000 |
| hidden | `**/vis_*.py` | glob | {"include_hidden": true, "recursive": true} | 65.335 | 66.477 | 64.295 | 66.962 | 51 | 10000 |
| symlinks | `*` | glob | {} | 8.636 | 8.780 | 8.551 | 8.916 | 51 | 5551 |
| symlinks | `**` | glob | {"recursive": true} | 69.059 | 70.148 | 67.963 | 70.947 | 51 | 6734 |

## Rep counts and rationale (deterministic band on oracle warmup median)

| shape | pattern | api | med warmup (s) | reps | rationale |
|---|---|---|---|---|---|
| small | `**/*.py` | glob | 0.0021 | 51 | med<0.1s: fast workload, 51 reps to expose variance |
| small | `**/*` | glob | 0.0025 | 51 | med<0.1s: fast workload, 51 reps to expose variance |
| small | `*.md` | glob | 0.0000 | 51 | med<0.1s: fast workload, 51 reps to expose variance |
| wide | `*` | glob | 0.1653 | 41 | med<0.5s: 41 reps |
| wide | `*` | iglob | 0.1669 | 41 | med<0.5s: 41 reps |
| wide | `*.dat` | glob | 0.1611 | 41 | med<0.5s: 41 reps |
| deep | `**/node_*.txt` | glob | 0.1680 | 41 | med<0.5s: 41 reps |
| sparse | `**/*.py` | glob | 0.1194 | 41 | med<0.5s: 41 reps |
| sparse | `*` | glob | 0.0031 | 51 | med<0.1s: fast workload, 51 reps to expose variance |
| dense | `**/*.py` | glob | 0.1402 | 41 | med<0.5s: 41 reps |
| dense | `**/*.py` | iglob | 0.1409 | 41 | med<0.5s: 41 reps |
| recursive | `**/b/m.py` | glob | 0.7286 | 31 | med<1s: 31 reps |
| recursive | `**/*.py` | glob | 0.9581 | 31 | med<1s: 31 reps |
| recursive | `a/**/zzz_*.py` | glob | 0.9372 | 31 | med<1s: 31 reps |
| hidden | `**/vis_*.py` | glob | 0.0664 | 51 | med<0.1s: fast workload, 51 reps to expose variance |
| hidden | `**/.hid_*.py` | glob | 0.0651 | 51 | med<0.1s: fast workload, 51 reps to expose variance |
| hidden | `**/vis_*.py` | glob | 0.0650 | 51 | med<0.1s: fast workload, 51 reps to expose variance |
| symlinks | `*` | glob | 0.0086 | 51 | med<0.1s: fast workload, 51 reps to expose variance |
| symlinks | `**` | glob | 0.0711 | 51 | med<0.1s: fast workload, 51 reps to expose variance |

## PROCESS-STARTUP (fresh `python3 -c` per shape — interpreter startup INCLUDED; NOT part of the in-process table, §10.1D)

| shape | pattern | api | kwargs | wall ms |
|---|---|---|---|---|
| small | `**/*.py` | glob | {"recursive": true} | 25.1 |
| wide | `*` | glob | {} | 195.1 |
| deep | `**/node_*.txt` | glob | {"recursive": true} | 197.5 |
| sparse | `**/*.py` | glob | {"recursive": true} | 146.3 |
| dense | `**/*.py` | glob | {"recursive": true} | 171.5 |
| recursive | `**/b/m.py` | glob | {"recursive": true} | 793.3 |
| hidden | `**/vis_*.py` | glob | {"recursive": true} | 92.6 |
| symlinks | `**` | glob | {"recursive": true} | 96.7 |

## Observations (this run)

- Symlink self-cycle under `**`: oracle TERMINATED (VERIFIED). n_results=6734, median=69.059 ms. A self-cycle follows exactly 41 symlinked levels before stopping (OBSERVED on this build; undocumented — Level C, not a requirement). **Corroborated 2026-09-21 on the aarch64 box:** the verbatim port (`FASTGLOB_NO_FUSED=1`) also stops at 41 levels (deepest materialized path 82 chars), i.e. this one figure is a kernel `SYMLOOP_MAX` artifact and is architecture-independent, matching `generate.py`'s SYMLOOP_MAX=40 note. The *timings* above remain machine-local.
- Cache state: one long-lived interpreter, fixed ROW order — candidate runs must reuse the same order for equivalence.
