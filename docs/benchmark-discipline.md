# Benchmark Discipline

*Verbatim doctrine extracted from AGENTS.md (§10, §11, §12, §22, §23, §24). AGENTS.md routes here; this file is the full text.*

## 10. BENCHMARK DISCIPLINE

A benchmark that measures the wrong boundary is invalid.


### 10.1 Separate API and process startup costs

Measure at least:

### A. In-process Python API

Example conceptual benchmark:

    glob.glob(pattern, ...)

repeated inside an already-running Python interpreter.

This measures globbing rather than interpreter startup.


### B. Candidate library API

Measure the replacement in-process where a library API exists.


### C. End-to-end CLI

Measure:

    fastglob 'pattern'

including process startup.


### D. Fresh Python process

May be measured as a CLI comparison, but MUST NOT be used as proof that the underlying glob engine is faster than Python's in-process API.


### 10.2 Workload matrix

Benchmark multiple tree shapes.

At minimum:

### Small tree

Developer/project-sized tree.

Purpose:
startup and fixed overhead.


### Wide tree

Many entries per directory.

Purpose:
directory-enumeration throughput.


### Deep tree

Many directory levels.

Purpose:
recursive traversal overhead.


### Sparse match

Large tree, very few matches.

Purpose:
traversal dominates.


### Dense match

Large tree, many matches.

Purpose:
matching + result materialization dominates.


### Recursive glob

Examples:

    **/*.py
    **/*
    a/**/target*

Purpose:
recursive traversal.


### Hidden-heavy tree

Purpose:
hidden filtering overhead and correctness.


### Symlink-heavy tree

Purpose:
symlink traversal costs and pathological behavior.


## 11. BENCHMARK METHODOLOGY

For every benchmark:

- record machine information;
- record filesystem type;
- record Python version;
- record candidate version/commit;
- record tree statistics;
- perform warm-up iterations;
- perform enough repetitions to expose variance;
- report median;
- report p95 where useful;
- report min/max or dispersion;
- report speedup ratio;
- keep fixture identical between implementations.

Do not cherry-pick the fastest candidate run.

Do not compare:

    warm candidate

against:

    cold Python

or vice versa.

Filesystem cache state must be equivalent for comparative runs.


## 12. PERFORMANCE ACCEPTANCE

DO NOT invent an arbitrary numeric SLA before measuring the baseline.

The operator requested a faster replacement, not an agent-generated 50 ms or 3x requirement.

Final reporting must show:

    reference time
    candidate time
    speedup ratio
    variance

for every primary workload.

A candidate is not considered worthwhile merely because one run is microscopically faster.

If the measured improvement is within benchmark noise, report:

    NO MEANINGFUL SPEEDUP

If the replacement wins only because Python interpreter startup was included, report:

    PROCESS-STARTUP WIN, NOT GLOB-ENGINE WIN

If it is faster on some workloads and slower on others, report the tradeoff explicitly.

Never manufacture a global "faster" conclusion from a favorable subset.


## 22. IMPLEMENTATION QUALITY

Once an engine is selected:

- minimize allocations in hot traversal loops;
- compile/reuse matcher state where possible;
- avoid process spawning per directory;
- avoid shell loops over individual filesystem entries;
- avoid converting path bytes to lossy representations;
- preserve arbitrary valid Unix filenames;
- stream results where the API permits it;
- do not sort unless semantics or an explicit mode requires sorting;
- do not traverse directories that the pattern proves cannot match.

Performance optimization must be supported by profiling.

Do not optimize guessed hotspots.


## 23. PROFILING

Once a correct candidate exists:

profile it.

Determine whether time is spent in:

- directory enumeration;
- stat/lstat calls;
- matcher execution;
- path construction;
- allocation;
- UTF-8 conversion;
- result buffering;
- Python/native boundary crossings;
- process startup.

Optimization work must target measured hotspots.


## 24. OPTIONAL PARALLELISM

Parallel traversal is a candidate optimization, not an axiom.

Because output order is not part of the Python glob contract, parallelism is not forbidden merely because it reorders matches.

However evaluate:

- filesystem contention;
- task scheduling overhead;
- small-tree regressions;
- symlink handling;
- deterministic testing;
- memory pressure.

Use parallelism only if measurements justify it.
