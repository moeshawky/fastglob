# Final Gate

*Verbatim doctrine extracted from AGENTS.md (§13, §14, §25, §26, §27, §29, §31, §32). AGENTS.md routes here; this file is the full text.*

## 13. ARCHITECTURE SELECTION GATE

Before selecting the final engine, produce a table:

| Candidate | Correctness | Small | Wide | Deep | Recursive | Notes |
|-----------|-------------|-------|------|------|-----------|-------|

A candidate may advance only if:

1. its compatibility differences are understood;
2. any repair layer is feasible;
3. its measured performance justifies further work.

The simplest candidate satisfying correctness and performance wins.

Do not select architecture because the implementation work already exists.


## 14. NO DUAL-ENGINE BY DEFAULT

Do not build both a shell engine and a Rust engine merely because both were explored.

Do not maintain:

    shell implementation
        +
    Rust implementation
        +
    runtime selector

unless evidence demonstrates that maintaining both solves a real requirement.

Exploration artifacts are not automatically production components.

Prefer ONE final engine with ONE compatibility contract.


## 25. DELIVERABLES

Final project should contain approximately:

    AGENTS.md
    README.md

    src/ or equivalent implementation tree

    tests/
        compatibility/oracle tests
        fixtures or fixture generator

    bench/
        benchmark runner
        fixture generator
        result/report tooling

    scripts/
        environment/probe helpers if needed

Exact language-specific structure depends on the selected implementation.

Do not create directories merely to satisfy this sketch if they serve no purpose.


## 26. REQUIRED COMMANDS

Provide simple project-level commands for:

    test
    compat
    bench
    profile

A Makefile, justfile, task runner, Cargo aliases, Python tooling, or equivalent may provide these.

The exact mechanism should follow the selected stack.

The important contract is that another agent can run the project without reconstructing the workflow from prose.


## 27. FINAL VERIFICATION GATE

Do not declare the project complete until all are true:

- [ ] Reference Python version identified.
- [ ] Python glob documented semantics mapped.
- [ ] Compatibility fixtures exist.
- [ ] Differential compatibility suite passes for all required documented behavior.
- [ ] Ordering is NOT incorrectly enforced where unspecified.
- [ ] Duplicate multiplicity is verified.
- [ ] Hidden-file behavior is verified.
- [ ] Recursive `**` behavior is verified.
- [ ] Broken symlinks are covered.
- [ ] Symlink cycles cannot hang tests or implementation.
- [ ] root_dir tested when supported.
- [ ] dir_fd tested when supported.
- [ ] include_hidden tested when supported.
- [ ] Pathological Unix filenames are covered.
- [ ] In-process reference benchmark exists.
- [ ] End-to-end CLI benchmark exists if a CLI is shipped.
- [ ] Performance results include variance and speedup ratios.
- [ ] No startup-cost benchmark is misrepresented as engine performance.
- [ ] Final engine was chosen from measurements rather than preference.
- [ ] Final architecture contains no unearned fallback engine.
- [ ] Profiling was performed before low-level optimization.
- [ ] No global machine files were replaced.
- [ ] README explains exact compatibility scope and benchmark results.


## 29. AUTONOMOUS EXECUTION LOOP

Use this loop:

    SEE
        inspect environment and reference behavior

    MAP
        map documented semantics and performance boundaries

    EXPLORE
        test candidate engines

    CHALLENGE
        attempt to falsify candidate assumptions

    CONVERGE
        choose simplest evidence-supported architecture

    EXECUTE
        implement

    VERIFY
        differential tests + benchmarks + profiling

    REFLECT
        check for scope drift and assumption leakage

If VERIFY invalidates an architectural premise:

    return to MAP/EXPLORE

Do NOT continue forward merely because implementation has begun.


## 31. FIRST ACTIONS

On a clean restart, do these in order:

1. Read this entire AGENTS.md.
2. Inspect the machine and project state.
3. Identify the installed Python glob reference.
4. Read the official documentation corresponding to that Python version.
5. Build the compatibility fixture generator and oracle harness.
6. Build the baseline benchmark.
7. Measure Python glob.
8. Explore candidate engines with SMALL throwaway probes.
9. Compare correctness and performance.
10. Select ONE architecture.
11. Implement it.
12. Run complete compatibility tests.
13. Benchmark again.
14. Profile.
15. Optimize measured hotspots.
16. Re-run compatibility and benchmarks.
17. Produce the final evidence report.

Do not start with the production implementation.


## 32. FINAL REPORT

The completion report must contain:

## Environment

- OS
- kernel
- filesystem
- CPU
- Python executable/version
- Python glob source path


## Compatibility

- cases executed
- cases passed
- documented incompatibilities
- unspecified implementation differences
- duplicate handling
- symlink behavior
- hidden-file behavior


## Performance

For each workload:

    Python stdlib median
    candidate median
    p95
    speedup ratio
    variance/dispersion

Separate:

    in-process engine benchmark
    end-to-end CLI benchmark


## Architecture

- candidates tested
- candidate selected
- why it won
- alternatives rejected with evidence


## Profiling

- measured hotspots
- optimizations applied
- before/after result


## Confidence

State explicitly:

    VERIFIED
    INFERRED
    UNVERIFIED

for any remaining material claims.
