#!/usr/bin/env python3
"""Multiset differential harness for Fast Glob (docs/compatibility-contract.md §20/§21).

Compares a candidate's results against the oracle capture
(tests/oracle/capture.json). Multiset comparison ONLY:

    Counter(python_results) == Counter(candidate_results)

Never ordered lists (ordering is documented-unspecified), never plain sets
(duplicates carry meaning).

Usage
-----
  compare.py --candidate candidate.json          # file
  compare.py --candidate - < candidate.json      # stdin
  compare.py --self-test                         # capture vs itself; must be 100% green

Candidate formats accepted:
  1. capture.json schema:  list of {_meta?} + {case_id, result|error, ...}
  2. minimal map:          {case_id: [paths...]}
     {case_id: {"result": [...]}} / {case_id: {"error": {...}}}
     {case_id: {"escape": "...", "glob_escaped": [...]}}
     {case_id: {"exposed": bool, "translate": "..."}}

Every FAIL is reported in the §21 format with one of the six classifications:
  documented incompatibility | implementation difference | ordering-only difference
  | duplicate-count difference | error-semantics difference | unknown

Zone cases (flag unspecified_zone) additionally fail as `zone_violation`
when the cycle-zone protocol (docs/compatibility-contract.md §8.5a) is
broken; zone passes are reported as `PASS <id> (<section>) [zone]` and
count as passed.

Exit code: 0 iff every case PASSes (self-test: 0 iff 100% green, including
the synthetic zone self-test cases); 1 iff genuine comparison failures;
2 iff the FRESHNESS GATE rejected the oracle capture (stale for this
interpreter or fixture tree — see check_oracle_freshness; maat C15).

Freshness gate (C15): before any comparison the committed oracle capture
must prove it describes THIS machine's oracle and THIS fixture tree:
_meta.python == running interpreter version AND _meta.tree_manifest_sha256
(stamped by tests/oracle/capture.py at capture time) == live manifest hash
from tests/fixtures/generate.py. Violations fail LOUDLY with the
regeneration command; staleness is never masked wholesale.
"""
import argparse
import json
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
DEFAULT_ORACLE = os.path.join(REPO, "tests", "oracle", "capture.json")

# freshness gate imports the fixture generator for the manifest hash — the
# single source of that algorithm (importing, not copying: anti-S22b)
sys.path.insert(0, os.path.join(REPO, "tests", "fixtures"))
import generate as fixture_generator  # noqa: E402

MAX_SHOWN = 12  # display truncation (diagnostic only — comparison is always full Counter)

# --- tree-root normalization (machine-portable absolute results) ---
# Absolute-pattern cases (e.g. l05 `@ROOT@/literal/exact_name.txt`) produce
# results that EMBED the fixture tree's root at run time: the committed
# capture carries the capturing machine's root; a candidate run on a fresh
# clone (any other path) carries that clone's root. git cannot know the clone
# location, so both sides are prefix-normalized with the same mapping before
# the multiset comparison (injective per side; relative results are
# untouched). Without this, every absolute-pattern case fails on any clone
# that is not at the capture-time path (F-ORACLE-OVERFIT of one machine's
# absolute prefix).
ROOT_SENTINEL = "$ROOT"
_CAPTURE_TREE = None    # capture-time tree root (set in main after the gate)
_CANDIDATE_TREE = None  # tree root the CANDIDATE ran against (main sets it:
                        # live tree for engine runs, capture root for --self-test,
                        # whose candidate values are the capture's own paths)


def normalize_tree_root(paths, root):
    """Replace an absolute tree-root prefix with ROOT_SENTINEL on str paths.

    `root` is the side's OWN fixture-tree root (capture-time for oracle
    records, live for candidate results). Non-str entries and paths that do
    not start with `root`/ are returned unchanged."""
    if not isinstance(paths, list) or not root:
        return paths
    pre = root + "/"
    out = []
    for p in paths:
        if isinstance(p, str) and p.startswith(pre):
            out.append(ROOT_SENTINEL + p[len(root):])
        else:
            out.append(p)
    return out

# --- cycle-zone protocol (docs/compatibility-contract.md §8.5a) ---
# Symlink-cycle expansion depth is Level C: ELOOP/SYMLOOP_MAX pruning depends
# on walk (readdir) order and the oracle itself is nondeterministic on it
# (VERIFIED 2026-08-20: s09 20 runs -> 40 x19 / 34 x1; r12 -> 551 x19 /
# 312 x1; s08 -> 492 x30 modal). The fixture's only DIRECTORY cycle is
# symlinks/cycle/self -> .., so every cycle-redundant re-enumeration carries
# repeated "cycle/self/" segments (mutual/x <-> y are FILE symlinks; they
# never expand). Strict multiset equality on cycle-expanded multiplicity is
# F-ORACLE-OVERFIT; the zone protocol tolerates ONLY cycle-redundant
# re-enumeration and pins the non-cycle core exactly:
#   (a) TERMINATES        — enforced upstream (run_engine.py 10 s timeout
#                           guard -> error record; errors never reach zone);
#   (b) NO FOREIGN PATHS  — every candidate path, with all "cycle/self/"
#                           segments removed (strip_cycle), has its base in
#                           the capture's stripped path set;
#   (c) NON-CYCLE CORE EXACT — after removing every path containing
#                           "cycle/self/" from BOTH multisets, the Counters
#                           are exactly equal.
CYCLE_ZONE_RE = re.compile(r"(cycle/self/)+")


def strip_cycle(p):
    """Remove all repeated cycle/self/ segments (cycle-zone protocol, §8.5a)."""
    return CYCLE_ZONE_RE.sub("", p)


def zone_active(rec, cand):
    """Zone membership = oracle OR candidate record flag.

    Post-W2 this is defense-in-depth only, NOT a stale-capture compensation
    (the old comment admitted the committed capture predated the s09 flag):
    check_oracle_freshness has already proven the capture current for THIS
    interpreter and fixture tree before any comparison runs, and regenerated
    captures carry unspecified_zone on s09 themselves. Either-side
    authorization remains so synthetic/self-test records need no flag
    duplication.
    """
    if rec.get("unspecified_zone"):
        return True
    return isinstance(cand, dict) and bool(cand.get("unspecified_zone"))


def zone_violations(o_vals, c_vals):
    """Cycle-zone protocol checks (b) and (c). Returns [violation, ...];
    empty list = zone pass. (a) TERMINATES is enforced upstream."""
    violations = []
    o_stripped = {strip_cycle(p) for p in o_vals}
    foreign = Counter()
    for p in c_vals:
        if strip_cycle(p) not in o_stripped:
            foreign[p] += 1
    if foreign:
        violations.append(
            "foreign paths (stripped base absent from capture): "
            + fmt_list(list(foreign.elements())[:MAX_SHOWN])
            + f" (total {sum(foreign.values())})")
    core_o = Counter(p for p in o_vals if "cycle/self/" not in p)
    core_c = Counter(p for p in c_vals if "cycle/self/" not in p)
    if core_o != core_c:
        missing = core_o - core_c
        extra = core_c - core_o
        violations.append(
            "non-cycle core mismatch: missing/undercounted="
            + fmt_list(list(missing.elements())[:MAX_SHOWN])
            + " extra/overcounted="
            + fmt_list(list(extra.elements())[:MAX_SHOWN]))
    return violations


def fmt_list(values):
    """Human display of a result list (truncated; comparison never is)."""
    if values is None:
        return "null"
    if isinstance(values, list):
        if len(values) <= MAX_SHOWN:
            return json.dumps(values, ensure_ascii=False)
        shown = json.dumps(values[:MAX_SHOWN], ensure_ascii=False)
        return f"{shown[:-1]} … (+{len(values) - MAX_SHOWN} more, total {len(values)})"
    return json.dumps(values, ensure_ascii=False)


def check_oracle_freshness(raw, oracle_path):
    """Freshness gate for the oracle capture (maat COHERENCE C15).

    Refuse to compare against a capture that cannot prove it describes THIS
    machine's oracle and THIS fixture tree. Two checks, both mandatory:

      1. INTERPRETER — _meta.python equals the RUNNING interpreter version.
         The oracle is the installed stdlib glob identified at runtime
         (AGENTS.md); a capture from a different interpreter is not this
         machine's oracle.
      2. FIXTURE TREE — _meta.tree_manifest_sha256 (stamped by
         tests/oracle/capture.py at capture time) equals the LIVE manifest
         hash from tests/fixtures/generate.py manifest()/manifest_hash().

    The oracle side must be the canonical list-with-_meta form; anything
    else is unverifiable and rejected. On any violation: loud stderr banner
    with the regeneration command, exit code 2. Captures predating the
    stamp fail loudly too — absence of proof of freshness is staleness.

    Returns the _meta dict when fresh.
    """
    problems = []
    meta = None
    if isinstance(raw, list) and raw and isinstance(raw[0], dict) and raw[0].get("_meta"):
        meta = raw[0]
    else:
        problems.append("not a canonical capture (list with a leading _meta record)")

    if meta is not None:
        live_py = sys.version.split()[0]
        if meta.get("python") != live_py:
            problems.append(f"captured under Python {meta.get('python')!r}, "
                            f"running interpreter is {live_py!r}")
        stamp = meta.get("tree_manifest_sha256")
        if stamp is None:
            problems.append("no tree_manifest_sha256 stamp (capture predates the freshness gate)")
        else:
            live_hash = fixture_generator.manifest_hash(fixture_generator.manifest())
            if stamp != live_hash:
                problems.append(f"fixture-tree manifest mismatch: capture={stamp[:16]}… "
                                f"live={live_hash[:16]}…")

    if problems:
        print("STALE ORACLE CAPTURE: " + str(oracle_path), file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print("run: python3 tests/oracle/capture.py && regenerate fixture tree\n"
              "     (python3 tests/fixtures/generate.py), or: make oracle-capture",
              file=sys.stderr)
        sys.exit(2)
    return meta


def extract_candidate_value(cand):
    """Normalize a candidate entry to ('result', list) | ('error', dict) | ('escape', dict) | ('translate', dict) | ('missing', None)."""
    if cand is None:
        return ("missing", None)
    if isinstance(cand, list):
        return ("result", cand)
    if isinstance(cand, dict):
        if "error" in cand and "result" not in cand:
            return ("error", cand["error"])
        if "result" in cand:
            return ("result", cand["result"])
        if "escape" in cand or "glob_escaped" in cand:
            return ("escape", cand)
        if "exposed" in cand or "translate" in cand:
            return ("translate", cand)
    return ("unknown", cand)


def extract_oracle_payload(rec):
    kind = rec.get("kind", "glob")
    if kind == "escape":
        return ("escape", {"escape": rec.get("escape"), "glob_escaped": rec.get("glob_escaped"),
                           "error": rec.get("error")})
    if kind == "translate":
        return ("translate", {"exposed": rec.get("exposed", False),
                              "translate": rec.get("translate"),
                              "skipped": rec.get("skipped_not_exposed", False)})
    if "error" in rec:
        return ("error", rec["error"])
    return ("result", rec.get("result"))


def classify_membership_diff(o_vals, c_vals, rec):
    co, cc = Counter(o_vals), Counter(c_vals)
    if set(co.keys()) == set(cc.keys()):
        return "duplicate-count difference"
    if rec.get("unspecified_zone"):
        return "documented incompatibility"
    return "implementation difference"


def evaluate_case(rec, cand):
    """THE single failure classifier (§21): returns (failures, note).

    failures is a list of (classification, python_detail, candidate_detail)
    tuples — empty means PASS. `note` is None on failure, else a pass
    annotation: 'zone' (cycle-zone protocol satisfied) or the
    ordering-differs diagnostic. Multiset comparison only. Both the verdict
    path (compare_case) and the §21 display path (derive_detail) project
    this ONE implementation — no second classifier exists (maat S28).
    """
    o_tag, o_payload = extract_oracle_payload(rec)
    c_tag, c_payload = extract_candidate_value(cand)
    failures = []
    note = None

    def fail(cls, py, candtxt):
        failures.append((cls, py, candtxt))

    if c_tag == "missing":
        fail("unknown", fmt_list(o_payload if o_tag == "result" else o_payload),
             "<case missing in candidate>")
    elif o_tag == "translate" or c_tag == "translate":
        if o_tag != "translate":
            fail("implementation difference", fmt_list(o_payload),
                 f"candidate kind mismatch: {c_tag}")
        elif c_tag != "translate":
            fail("implementation difference",
                 "translate " + ("exposed" if o_payload["exposed"] else "NOT exposed")
                 + (f" = {o_payload['translate']}" if o_payload["exposed"] else " (skipped_not_exposed)"),
                 f"candidate did not provide translate record: {c_tag}")
        elif o_payload["exposed"]:
            if c_payload.get("exposed") is not True or c_payload.get("translate") != o_payload["translate"]:
                fail("implementation difference",
                     f"translate = {o_payload['translate']!r}",
                     f"candidate: {json.dumps(c_payload, ensure_ascii=False)}")
        else:
            # reference does not expose translate; candidate must not claim a value
            if c_payload.get("exposed") is True or (
                    c_payload.get("translate") is not None and not c_payload.get("skipped")):
                fail("implementation difference",
                     "translate NOT exposed (skipped_not_exposed)",
                     f"candidate claims: {json.dumps(c_payload, ensure_ascii=False)}")
    elif o_tag == "escape" or c_tag == "escape":
        if o_tag != "escape":
            fail("implementation difference", fmt_list(o_payload),
                 f"candidate kind mismatch: {c_tag}")
        else:
            if c_tag == "escape":
                c_escaped, c_scalar = c_payload.get("glob_escaped"), c_payload.get("escape")
            elif c_tag == "result":
                # minimal-map candidate gave a bare list: treat as glob_escaped multiset
                c_escaped, c_scalar = c_payload, None
            elif c_tag == "error":
                c_escaped, c_scalar = None, None
            else:
                c_escaped, c_scalar = None, None
                fail("implementation difference",
                     f"escape = {o_payload['escape']!r}, glob(escaped) = {fmt_list(o_payload['glob_escaped'])}",
                     f"candidate kind mismatch: {c_tag}")
            if o_payload.get("error") is not None:
                fail("error-semantics difference", f"error: {o_payload['error']}",
                     f"candidate: {json.dumps(c_payload, ensure_ascii=False)}")
            elif c_scalar is not None and c_scalar != o_payload["escape"]:
                fail("implementation difference",
                     f"escape = {o_payload['escape']!r}", f"candidate escape = {c_scalar!r}")
            elif c_escaped is None:
                fail("unknown", f"glob(escaped) = {fmt_list(o_payload['glob_escaped'])}",
                     "<glob_escaped missing>")
            elif Counter(o_payload["glob_escaped"]) != Counter(c_escaped):
                fail("implementation difference",
                     f"escape = {o_payload['escape']!r}, glob(escaped) = {fmt_list(o_payload['glob_escaped'])}",
                     f"glob(escaped) = {fmt_list(c_escaped)}")
    else:
        # --- glob / transient: multiset comparison (Counter; never lists, never sets) ---
        if o_tag == "error":
            o_desc = (f"error: {o_payload['type']} (timed_out={o_payload.get('timed_out', False)}) "
                      f"{o_payload.get('message', '')}")
            if c_tag != "error" or c_payload.get("type") != o_payload["type"]:
                fail("error-semantics difference", o_desc,
                     (f"error: {c_payload.get('type')} {c_payload.get('message', '')}"
                      if c_tag == "error" else fmt_list(c_payload)))
        elif c_tag == "error":
            fail("error-semantics difference", fmt_list(o_payload),
                 f"error: {c_payload.get('type')} {c_payload.get('message', '')}")
        elif c_tag != "result":
            fail("unknown", fmt_list(o_payload), f"candidate entry not a result: {c_tag}")
        else:
            # Absolute results embed each side's OWN tree root (capture-time
            # for the oracle, live for the candidate) — normalize both sides
            # before the multiset comparison (see normalize_tree_root).
            # Diagnostics keep the raw payloads.
            o_norm = normalize_tree_root(o_payload, _CAPTURE_TREE)
            c_norm = normalize_tree_root(c_payload, _CANDIDATE_TREE)
            co, cc = Counter(o_norm), Counter(c_norm)
            if zone_active(rec, cand):
                zv = zone_violations(o_norm, c_norm)
                if not zv:
                    note = "zone"
                else:
                    fail("zone_violation",
                         fmt_list(o_payload) + "  [zone: " + " | ".join(zv) + "]",
                         fmt_list(c_payload))
            elif co == cc:
                if list(o_norm) != list(c_norm):
                    note = "ordering differs (documented-unspecified — not required)"
            else:
                fail(classify_membership_diff(o_norm, c_norm, rec),
                     fmt_list(o_payload), fmt_list(c_payload))

    return failures, note


def compare_case(rec, cand_map):
    """Verdict projection of evaluate_case (the single classifier):
    returns (pass, note, classification|None). Multiset comparison only."""
    failures, note = evaluate_case(rec, cand_map.get(rec["case_id"]))
    if failures:
        return (False, None, failures[0][0])
    return (True, note, None)


def zone_self_test():
    """Synthetic zone cases proving the cycle-zone protocol fires both ways.
    Not part of the 139; reported separately and hard-gated on the self-test
    exit code. Returns (n_ok, n_total)."""
    rec = {
        "case_id": "zz01",
        "section": "8.5",
        "kind": "glob",
        "pattern": "symlinks/**/v1.txt",
        "kwargs": {"recursive": True},
        "note": "synthetic self-test zone case (not part of the 139)",
        "unspecified_zone": True,
        "result": [
            "basic/a",
            "symlinks/to_dir/v1.txt",
            "symlinks/cycle/self/cycle/self/to_dir/v1.txt",
        ],
    }
    # stripped capture set = {"basic/a", "symlinks/to_dir/v1.txt"}
    cases = [
        ("zz01 zone-pass", [
            "basic/a",
            "symlinks/to_dir/v1.txt",
            "symlinks/cycle/self/to_dir/v1.txt",
            "symlinks/cycle/self/cycle/self/cycle/self/to_dir/v1.txt",
        ], True, None),
        ("zz02 zone-foreign", [
            "basic/a",
            "symlinks/to_dir/v1.txt",
            "symlinks/cycle/self/ghost.txt",
        ], False, "zone_violation"),
        ("zz03 zone-core-mismatch", [
            "basic/a",
            "symlinks/cycle/self/to_dir/v1.txt",
        ], False, "zone_violation"),
    ]
    n_ok = 0
    for label, cand, expect_pass, expect_cls in cases:
        ok, _note, cls = compare_case(rec, {rec["case_id"]: cand})
        good = (ok == expect_pass) and (cls is None if expect_pass else cls == expect_cls)
        print(f"{'OK  ' if good else 'BAD '} {label}: pass={ok} cls={cls} "
              f"(expected pass={expect_pass} cls={expect_cls})")
        n_ok += int(good)
    return n_ok, len(cases)


def self_candidate_value(rec):
    """Project an oracle record back into a candidate entry (self-test identity)."""
    if "result" in rec:
        return rec["result"]
    if "error" in rec:
        return {"error": rec["error"]}
    if rec.get("kind") == "escape":
        return {"escape": rec.get("escape"), "glob_escaped": rec.get("glob_escaped")}
    if rec.get("kind") == "translate":
        return {"exposed": rec.get("exposed", False), "translate": rec.get("translate")}
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--candidate", help="candidate results file, or '-' for stdin")
    ap.add_argument("--oracle", default=DEFAULT_ORACLE, help="oracle capture.json (default: repo capture)")
    ap.add_argument("--self-test", action="store_true",
                    help="compare the oracle capture against itself (must be 100 percent green)")
    args = ap.parse_args()

    # FRESHNESS GATE first (C15): refuse stale/unverifiable oracle captures
    # before any comparison (applies to --self-test and --candidate alike).
    with open(args.oracle) as f:
        raw = json.load(f)
    meta = check_oracle_freshness(raw, args.oracle)  # exits 2 on violation
    global _CAPTURE_TREE, _CANDIDATE_TREE
    _CAPTURE_TREE = meta.get("tree")
    _CANDIDATE_TREE = meta.get("tree") if args.self_test else fixture_generator.TREE
    oracle = {r["case_id"]: r for r in raw if not r.get("_meta")}
    oracle_ordered = [r["case_id"] for r in raw if not r.get("_meta")]
    tree = meta.get("tree", "?")

    if args.self_test:
        candidate = {cid: self_candidate_value(rec) for cid, rec in oracle.items()}
        label = "self-test"
    else:
        if not args.candidate:
            ap.error("--candidate required unless --self-test")
        src = sys.stdin if args.candidate == "-" else open(args.candidate)
        with src:
            candidate = load_capture_stream(src)
        label = args.candidate

    n_pass = n_fail = 0
    notes = []
    for cid in oracle_ordered:
        rec = oracle[cid]
        ok, note, _ = compare_case(rec, candidate)
        if ok:
            n_pass += 1
            line = f"PASS {cid} ({rec.get('section', '?')})"
            if note == "zone":
                line += " [zone]"
            elif note:
                line += f"  [{note}]"
            print(line)
        else:
            n_fail += 1
            print(f"FAIL {cid} ({rec.get('section', '?')})")
            # re-derive the §21 failure detail via the single classifier
            detail = derive_detail(rec, candidate.get(cid))
            print(f"  pattern: {rec.get('pattern')}")
            print(f"  options: {json.dumps(rec.get('kwargs', {}), ensure_ascii=False)}")
            print(f"  fixture: {tree} (§{rec.get('section', '?')} case {cid})")
            print(f"  Python result: {detail['py']}")
            print(f"  candidate result: {detail['cand']}")
            print(f"  classification: {detail['cls']}")
    zone_ok = zone_total = 0
    if args.self_test:
        print("\nzone self-test (synthetic; not part of the 139):")
        zone_ok, zone_total = zone_self_test()

    print(f"\n{n_pass + n_fail} executed / {n_pass} passed / {n_fail} failed  [{label}]")
    if args.self_test:
        green = (n_fail == 0) and zone_ok == zone_total
        if green:
            print(f"SELF-TEST 100% GREEN ({n_pass}/{n_pass}; zone self-test {zone_ok}/{zone_total})")
        else:
            print(f"SELF-TEST RED (oracle {n_pass}/{n_pass + n_fail} passed; "
                  f"zone self-test {zone_ok}/{zone_total})")
        return 0 if green else 1
    return 0 if n_fail == 0 else 1


def load_capture_stream(fp):
    data = json.load(fp)
    return data if isinstance(data, dict) else {r["case_id"]: r for r in data if not r.get("_meta")}


def derive_detail(rec, cand):
    """§21 detail lines for a failing case — a projection of evaluate_case
    (the single classifier), never a second implementation (maat S28)."""
    failures, _note = evaluate_case(rec, cand)
    if failures:
        cls, py, candtxt = failures[0]
        return {"cls": cls, "py": py, "cand": candtxt}
    # Defensive: reached only if a case re-evaluates as passing after the
    # verdict (e.g. nondeterministic cycle zone) — report the raw multisets
    # without inventing a classification.
    _o_tag, o_payload = extract_oracle_payload(rec)
    _c_tag, c_payload = extract_candidate_value(cand)
    return {"cls": "ordering-only difference",
            "py": fmt_list(o_payload), "cand": fmt_list(c_payload)}


if __name__ == "__main__":
    raise SystemExit(main())
