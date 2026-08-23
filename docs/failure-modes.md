# Failure Modes — Autonomous Agents

*Verbatim doctrine extracted from AGENTS.md (§28). AGENTS.md routes here; this file is the full text.*

## 28. AUTONOMOUS-AGENT FAILURE MODES

Watch specifically for these failures.


### F-ASSUMPTION

Signal:

An observation becomes an architectural requirement without evidence.

Response:

Surface it, classify it, test it.


### F-SEMANTIC

Signal:

The implementation matches syntax but changes documented glob meaning.

Response:

Compare directly with the Python oracle.


### F-BENCHMARK

Signal:

A benchmark proves process startup is faster rather than globbing.

Response:

Split in-process and end-to-end measurements.


### F-ORDER

Signal:

An agent starts reproducing current CPython filesystem ordering.

Response:

STOP. Re-read the documented ordering contract.


### F-SCOPE

Signal:

A probe or experimental implementation becomes a permanent production component without justification.

Response:

Remove it unless a verified requirement needs it.


### F-DUAL-ENGINE

Signal:

The agent decides to ship several engines because they already exist.

Response:

Return to architecture selection. Sunk work is not a requirement.


### F-SHELL-CONFLATION

Signal:

The agent claims a standalone binary can accelerate:

    command *.txt

by replacing "glob".

Response:

STOP. That pattern is expanded by the shell before command execution.


### F-ORACLE-OVERFIT

Signal:

The agent attempts to reproduce unspecified incidental behavior from one reference run.

Response:

Return to the documented contract.


### F-OVERCONFIDENCE

Signal:

Architecture is selected from reasoning without benchmark evidence.

Response:

Measure first.
