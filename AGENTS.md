# AGENTS.md — Fast Glob Entry Gate

*A faster pathname-globbing engine for Linux, compatibility-locked to Python's stdlib `glob` — measured, not assumed.*

## What This Is

Greenfield, local-only project: build a substantially faster pathname-globbing
implementation for Linux while preserving the documented semantics of Python's
standard-library `glob` module. Speed is the reason this project exists.
Correctness is not negotiable to obtain speed.

The compatibility oracle is the stdlib `glob` of the Python interpreter
installed on this machine — identified at runtime, never hard-coded:

    command -v python3
    python3 --version
    python3 -c 'import glob; print(glob.__file__)'

Project root:

    /kaggle/working/fastglob

This file is the entry gate: it routes. Full doctrine lives in `docs/` and the
referenced skills. If a rule exists here, it is short because it must survive
compaction; if it is long, its full text is one hop away in `docs/`.

## THE IRON LAW

```
EVIDENCE BEFORE ARCHITECTURE: measure before choosing; no invented SLA, language, or algorithm.
TWO GLOBs, ONE TARGET: only programmatic pattern expansion is in scope — the shell expands before your binary runs.
MULTISET, NOT ORDER: Python glob order is documented as unspecified — compare Counters, never lists, never sets.
DOCUMENTED OVER OBSERVED: source-of-truth levels A/B/C — do not fossilize an implementation accident into a requirement.
REASON THROUGH HIVE-MIND: structured reasoning only (productive_reason → sequential-thinking → scratchpad); memory_search before inferring prior state.
VIOLATE → STOP, re-read this file, record the violation with reason().
```

## LOAD ORDER (non-negotiable)

1. Load the `hive-mind` skill. READ every file under its `references/`.
2. `memory_search` the project and task BEFORE inferring prior state or
   repository truth. Memory is a lead, not authority — re-verify against source.
3. Load the domain skill for the task (routing table below). AFTER loading any
   skill: READ ALL of its `references/` files.
4. Record the reasoning lineage: flat `reason()` for thoughts you actually had;
   `productive_reason()` chain for architecture and compatibility-failure
   decisions; `scratchpad` for working state.
5. Load `graph-assisted-coding` when a code graph is connected (not expected on
   this greenfield repo).

## MCP COGNITIVE STACK

Servers are declared in `.mcp.json`.

| Server | Kind / lifecycle | Role |
|--------|------------------|------|
| `hive-mind` | remote, **eager** | Memory + reasoning lineage + capability discovery. 7 tools: `memory_search`, `memory_store`, `reason`, `capability_search` (flat) + `productive_reason`, `reason_meta`, `scratchpad` (gated). **Loaded first — see LOAD ORDER.** |
| `sequential-thinking` | local, **eager** | Reflective multi-step decomposition for decisions that exceed one step. |
| `searxng` | local, lazy | Documentation lookup: Python glob docs, `glob(3)`, `fd`/`rg` semantics. Verify hits against source before use. |

Usage rules:

- `productive_reason` (SEE→EXPLORE→CHALLENGE→CONVERGE→REFLECT) for every
  architecture decision and every compatibility-failure triage.
- `reason` records the model's actual thought — not protocol prose.
- `capability_search` before any uncommon `hive` CLI operation; never guess a
  tool name, command, or argument.
- `memory_store` only verified, reusable knowledge.
- Flat tools take flat parameters — no nested `input`, no JSON-encoded arrays.

## THE TWO GLOBs (read before touching a shell)

**Shell pathname expansion** (`echo *.py`) is expanded by the shell BEFORE the
command runs. A binary in PATH cannot accelerate it. Replacing or interposing
on shell globbing is OUT OF SCOPE. Shell globbing may be BENCHMARKED as a
comparison point only — it is not the compatibility target.

**Programmatic pathname expansion** — a program receives an unexpanded pattern
as data and resolves it itself (Python `glob`, libc `glob(3)`, search
libraries, quoted-pattern CLIs). **THIS is the layer this project targets.**

## SOURCE OF TRUTH

Every claim is classified:

- **Level A — Documented contract.** Explicitly documented by Python.
  Normative. The replacement MUST preserve it.
- **Level B — Stable observable behavior required for compatibility.**
  Undocumented but required, ONLY after: reproduced, consumer dependence
  demonstrated, and the reason recorded.
- **Level C — Implementation accident.** `readdir()` order, recursion
  internals, syscall sequence, timing artifacts. NOT requirements. Do not
  architect around them.

## ORDERING

DO NOT require byte-identical output ordering with Python `glob`.

Compatibility tests compare result MULTISETS:

    Counter(reference_results) == Counter(candidate_results)

NOT ordered lists, NOT plain sets — duplicates can legitimately occur, so set
comparison loses multiplicity.

Never add sorting for convenience (except an explicit separate CLI mode). Do
not spend effort reproducing an unspecified `readdir()` ordering artifact.

## OPERATOR INTENT

Asked for: faster than stdlib `glob`; compatible semantics; empirical proof via
benchmarks; agent-usable implementation; simple final architecture; no semantic
sacrifices hidden behind benchmark numbers.

**Transparent replacement (the real point, operator clarification 2026-08-20):**
this repo is the *glob* member of the `agent-bin-setup` family — the same
machine already runs `find`→bfs and `grep`→ugrep shadowed on PATH with
`gnu-find`/`gnu-grep` escape hatches, verified differentially, and agents never
register the swap. Fast Glob must be a drop-in, faster, behaviorally identical
glob deployed the same way: agents and their Python code keep calling `glob`
and notice nothing. The 133-case multiset suite is the *invisibility guarantee*
(same role as agent-bin-setup's Phase E differential suites). Deliverables end
with a transparent layer: a `glob`-named Python module injected at the same
launch boundaries agent-bin-setup owns (bashrc/profile.d/PAM/BASH_ENV →
PYTHONPATH/.pth/sitecustomize for Python), a `gnu-glob` escape hatch (real
stdlib stays importable), and auto-rollback to stdlib on suite failure
(never an unverified default). Order: engine → 133/133 → bench/profile → shim.

NOT specified — DO NOT silently invent: a 50 ms SLA, a 3x minimum speedup,
exact CPython output order, a mandatory Rust implementation, a mandatory shell
implementation, two production engines, a specific traversal algorithm, a
specific matching library.

## IMPLEMENTATION POLICY

Architecture is EVIDENCE-DRIVEN. "This must be Bash" / "this must be Rust" /
"fd must be the backend" / "libc glob() must be fastest" / "parallel traversal
must win" are hypotheses. Measure them.

- **Shell** is permitted as a prototype, capability probe, benchmark candidate,
  or glue around a faster primitive — NOT privileged.
- **Compiled** (Rust, C, C++, Zig, …) may be selected if evidence supports it.
  Rust is acceptable and likely worth evaluating (traversal, matching, low
  startup, memory safety) — but NOT preselected.
- **Existing engines** — investigate where useful: `fd`, `rg --files`,
  POSIX/libc `glob()`, optimized Python traversal, Rust filesystem-walking /
  matching libraries. Do not assume CLI semantics are compatible; a backend can
  be useful internally even when its public behavior differs.

## REQUIRED SURFACES

Two surfaces unless evidence shows one unnecessary:

- **CLI** (working name `fastglob`): `fastglob [OPTIONS] PATTERN`, minimum
  useful options `--recursive`, `--include-hidden`, `--null`, `--root-dir`
  PATH. Normal mode: one pathname per line. `--null`: NUL-delimited for
  arbitrary filenames. Filesystem misses are not exceptional. Diagnostics on
  stderr. Misuse → non-zero exit. Traversal errors follow the reference's
  compatibility semantics where applicable.
- **Programmatic API**: express the relevant Python glob contract — at minimum
  investigate compatibility with `glob.glob(...)` and `glob.iglob(...)`, and
  `glob.escape(...)` / `glob.translate(...)` where the installed reference
  exposes them. Do not shadow or replace the system stdlib `glob.py` during
  development: build a separate package/module first; create an import-level
  compatibility shim only after the engine passes the oracle suite.

## SKILL ROUTING

| Skill | When |
|-------|------|
| `new-repo-ceo` | Entering this repo, after compaction, whenever intent feels lost. |
| `general-reasoning` | Novel problem or architecture decision. |
| `advanced-design` + `seshat` | Designing engine/API/test architecture from scratch; build-vs-buy (`fd`, `rg`, libc `glob()`). |
| `elegantify` | Choosing the simplest implementation that satisfies the contract. |
| `greenfield-design-workflow` | Full subagent chain for building the engine. |
| `advanced-debugging` + `advanced-patching` | Oracle-suite failures; applying verified fixes with blast radius. |
| `adversarial-test-design` + `llm-testing-patterns` | Designing fixtures/tests that break the engine. |
| `subagents` + `puppeteer-prompter` | Dispatching probes through `subagent.sh`. |

Discovery protocol: scan-and-match against the table; if a skill MIGHT apply,
load it (1% rule). Detect rationalization ("I'll skip it, I know this area").
Stress-test any conclusion before persisting it (`dialectical-challenging`).

## DISPATCH PROTOCOL

- Session kickoff: the operator pastes `.pi/LEAD_PROMPT.md` as the first
  message. It governs the LEAD's session conduct only; the append governs the
  dispatched. Neither restates the other's duties.
- `subagent.sh` dispatches subagents with `cwd=/kaggle/working/fastglob` (derived
  from the script's own location), so they inherit the skills, `.mcp.json`, and
  this gate.
- EVERY dispatch prompt MUST prepend the full contents of
  `.pi/APPEND_SYSTEM.md` — the append does not self-discover (P-DISCOVER).
- The append may add dispatch scope; it must never weaken the Iron Law or BANNED.
- Default dispatch model is `subagent.sh`'s default; override per dispatch only
  when justified and recorded.

## EXECUTION LOOP

    SEE → MAP → EXPLORE → CHALLENGE → CONVERGE → EXECUTE → VERIFY → REFLECT

This is the `productive_reason` phase chain. If VERIFY invalidates an
architectural premise: return to MAP/EXPLORE. Do NOT continue forward merely
because implementation has begun. Full text: `docs/final-gate.md` §29.

## CORE DOCTRINE — FULL TEXT IN docs/

| Topic | Full text |
|-------|-----------|
| Python compatibility contract (test families 8.1–8.9), unspecified behavior, test-first oracle development, differential failure report | `docs/compatibility-contract.md` |
| Benchmark boundaries (in-process vs end-to-end), 8-shape workload matrix, methodology, performance acceptance, implementation quality, profiling, parallelism | `docs/benchmark-discipline.md` |
| Autonomous-agent failure modes F-ASSUMPTION … F-OVERCONFIDENCE (signal + response) | `docs/failure-modes.md` |
| Architecture selection gate, no dual-engine rule, deliverables, required commands (`test`/`compat`/`bench`/`profile`), final verification gate, first actions, final report format | `docs/final-gate.md` |

## EVIDENCE LABELS

For important technical claims, label: DOCUMENTED, OBSERVED, VERIFIED,
INFERRED, or HYPOTHESIS. Do not convert OBSERVED into DOCUMENTED.

## COURSE CORRECTION

If new evidence invalidates a design premise: STOP. Do not patch around the
invalid premise. Return to the most recent valid decision point. Previous
reasoning is sunk cost.

## INDEPENDENT VERIFICATION

The agent that proposes an architecture is not evidence for it. Before
implementation, independently reproduce the measurements carrying the decision:
reference semantics that affect architecture, performance baseline, candidate
performance, symlink behavior, hidden-file behavior, and any claimed
incompatibility used to reject a simpler solution. Artifact completeness is not
verification.

## RELEASE HYGIENE — GITHUB

`origin` is a **private gogs** — as long as the remote remains this
private gogs server, agentic artifacts and local secrets STAY in git
history and are committed as-is (e.g., `code_aid_query.sh` carries a
live Lemonade API key). Do not scrub, re-ignore, or rewrite history
while on gogs. When it is time to release on **public GitHub**: add
`code_aid_query.sh` and ALL agentic artifacts (`.ix/`, `.sniper/`,
`out/`, `probes/`, `fastglob.jsonl`, …) to `.gitignore` and scrub
them from history before publishing.

## BANNED

- Inventing requirements: SLA numbers, minimum speedups, engine language,
  traversal algorithm, matcher library (not specified — measure, don't assume).
- Comparing ordered lists or plain sets for glob results (order is
  unspecified; duplicates are legitimate).
- Claiming engine speedup from a fresh-process benchmark (process-startup win
  is not a glob-engine win).
- Reproducing CPython `readdir()` ordering as a requirement (F-ORACLE-OVERFIT).
- Shipping more than one production engine without measured justification
  (F-DUAL-ENGINE; sunk work is not a requirement).
- "Solving" shell globbing via a binary in PATH (the shell expands before your
  process exists — F-SHELL-CONFLATION).
- Global machine mutation: Bash, `/bin/sh`, `/usr/bin/find`, `/usr/bin/grep`,
  Python stdlib files, in-place system package changes.
- Silent scope additions: caching, indexing, daemons, databases, watchers,
  network services, config languages, plugin systems, shell replacements or
  patches.
- Free-form reasoning that bypasses the hive-mind structured stack.
- Guessing a hive tool, CLI operation, or argument (`capability_search` first).
- Wrapping flat hive-mind parameters in nested `input` objects or JSON strings.
- Storing unverified conclusions or sensitive data in memory.
- Declaring completion without the final verification gate
  (`docs/final-gate.md` §27).

## STOP CONDITIONS

Stop and report rather than manufacturing success if:

1. no candidate is materially faster than Python glob;
2. compatibility requires sacrificing the measured speed advantage;
3. benchmark variance is too high to establish a winner;
4. the target layer turns out not to be Python/programmatic globbing;
5. environment restrictions make valid benchmarking impossible.

A negative result is a valid project result. Do not build complexity to avoid
admitting one.

---

*"Realize the operator's intent — not the previous agent's architecture. Do not optimize for preserving work already performed. Do not turn historical implementation details into present-day requirements. Do not confuse ceremony with evidence. The shortest correct fast implementation wins."*
