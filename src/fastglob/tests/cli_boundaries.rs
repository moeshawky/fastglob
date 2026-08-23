//! Z1 family B — integer boundary tables (closes Maat TP47) and family C —
//! stdout errno discrimination (completes V14).
//!
//! Boundary semantics VERIFIED against main.rs source AND frozen by probing
//! the freshly built binary before these tests were written:
//! * MAX_PATTERN_LEN  = 8192 INCLUSIVE — reject iff `len > 8192`;
//! * MAX_COMPONENTS   = 512  INCLUSIVE — components = '/' count + 1 for a
//!   non-empty pattern (a trailing '/' adds one); reject iff > 512;
//! * --dir-fd window  = [0, 1073741823] inclusive both ends (parse_fd range
//!   check `0..1_073_741_824`); text that overflows i32 is a RANGE verdict,
//!   not a format verdict.
//!
//! The two dimensions are guarded independently (walk.rs carries a duplicate
//! defense-in-depth guard returning [] for oversize patterns).
//!
//! Family C evidence base (fault-injection feasibility matrix, measured on
//! rustc 1.98.0 with a minimal isolated repro BEFORE designing these tests):
//! * EPIPE  — real injection works: output sized far above the 64 KiB pipe
//!   buffer forces a blocked write; closing the reader yields exit 0 +
//!   silent stderr (documented pipe UX). Tested below.
// Justification for the allow (BANNED: `#[allow]` requires a reason):
// integration-test crate — unwrap()/expect() are the panic vehicle for
// fixture setup and child-process control, not production error paths.
#![allow(clippy::unwrap_used, clippy::expect_used)]
mod common;

use common::{assert_accept, assert_misuse, bin_path, run, tmpdir};
use std::fs;
use std::io::Read as _;
use std::path::Path;
use std::process::{Command, Output, Stdio};

/// Accepted rows: hermetic miss inside an empty root dir — exit 0, SILENT
/// stderr, EMPTY stdout (acceptance means the guard did not fire anywhere).
fn assert_boundary_accepted(label: &str, out: &Output) {
    assert_accept(label, out);
    assert!(
        out.stdout.is_empty(),
        "{label}: expected a clean miss, got stdout {:?}",
        String::from_utf8_lossy(&out.stdout)
    );
}

// ---------------------------------------------------------------------------
// Family B: pattern LENGTH boundary (MAX_PATTERN_LEN = 8192 inclusive)
// ---------------------------------------------------------------------------

#[test]
fn pattern_length_8191_and_8192_accepted() {
    let d = tmpdir("blen_ok");
    let root = d.to_str().unwrap().to_owned();
    // 8191: last byte below the guard. 8192: EXACT inclusive maximum — the
    // value that historically crashed via stack overflow before the guard.
    for n in [8191usize, 8192] {
        let pat = "a".repeat(n);
        let out = run(&["--root-dir", &root, "--", &pat]);
        assert_boundary_accepted(&format!("len_{n}"), &out);
    }
    let _ = fs::remove_dir_all(&d);
}

#[test]
fn pattern_length_8193_rejected_pattern_too_long() {
    let d = tmpdir("blen_bad");
    let root = d.to_str().unwrap().to_owned();
    let pat = "a".repeat(8193); // first byte past the inclusive maximum
    let out = run(&["--root-dir", &root, "--", &pat]);
    assert_misuse("len_8193", &out, "fastglob: pattern too long");
    let _ = fs::remove_dir_all(&d);
}

// ---------------------------------------------------------------------------
// Family B: COMPONENT-COUNT boundary (MAX_COMPONENTS = 512 inclusive;
// components = slashes + 1, patterns kept short so the length guard cannot
// interfere with the component verdict)
// ---------------------------------------------------------------------------

fn many_components(k: usize) -> String {
    vec!["a"; k].join("/")
}

#[test]
fn component_count_511_and_512_accepted() {
    let d = tmpdir("bcomp_ok");
    let root = d.to_str().unwrap().to_owned();
    for k in [511usize, 512] {
        let pat = many_components(k);
        debug_assert_eq!(pat.len(), 2 * k - 1, "keep well under 8192 bytes");
        let out = run(&["--root-dir", &root, "--", &pat]);
        assert_boundary_accepted(&format!("comps_{k}"), &out);
    }
    let _ = fs::remove_dir_all(&d);
}

#[test]
fn component_count_513_rejected_pattern_too_long() {
    let d = tmpdir("bcomp_bad");
    let root = d.to_str().unwrap().to_owned();
    let out = run(&["--root-dir", &root, "--", &many_components(513)]);
    assert_misuse("comps_513", &out, "fastglob: pattern too long");
    let _ = fs::remove_dir_all(&d);
}

#[test]
fn length_and_component_guards_are_independent_dimensions() {
    // Cross-dimension proof (Class-5 cross product): each guard rejects only
    // when ITS OWN bound is crossed while the other sits far inside it.
    let d = tmpdir("bcross");
    let root = d.to_str().unwrap().to_owned();
    // 512 components at 1023 bytes: crosses nothing -> accepted
    let ok_comps = run(&["--root-dir", &root, "--", &many_components(512)]);
    assert_boundary_accepted("cross_512comps_short", &ok_comps);
    // 8192 bytes in ONE component: crosses nothing -> accepted
    let ok_len = run(&["--root-dir", &root, "--", &"a".repeat(8192)]);
    assert_boundary_accepted("cross_8192bytes_1comp", &ok_len);
    // 513 components at only 1025 bytes: ONLY the component guard can reject
    let bad_comps = run(&["--root-dir", &root, "--", &many_components(513)]);
    assert_misuse(
        "cross_513comps_short",
        &bad_comps,
        "fastglob: pattern too long",
    );
    // 8193 bytes in one component: ONLY the length guard can reject
    let bad_len = run(&["--root-dir", &root, "--", &"a".repeat(8193)]);
    assert_misuse(
        "cross_8193bytes_1comp",
        &bad_len,
        "fastglob: pattern too long",
    );
    let _ = fs::remove_dir_all(&d);
}

// ---------------------------------------------------------------------------
// Family C: stdout delivery errno discrimination (V14 contract:
// BrokenPipe -> exit 0 silent; ANY OTHER errno -> diagnostic + exit 1)
// ---------------------------------------------------------------------------

#[test]
fn broken_pipe_downstream_close_exits_zero_silent() {
    // ~3000 x ~60B names ~= 185 KB of output, triple the default 64 KiB pipe
    // buffer: the writer MUST block mid-stream, so dropping the read end
    // forces a genuine EPIPE deterministically (no sleeps; wait() reaps).
    let d = tmpdir("epipe");
    for i in 0..3000u32 {
        let name = format!("f{i:04}_{}.txt", "x".repeat(48));
        fs::write(d.join(name), b"").unwrap();
    }
    let root = d.to_str().unwrap().to_owned();
    let mut child = Command::new(bin_path())
        .args(["--null", "--root-dir", &root, "*"])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn fastglob for EPIPE test");
    {
        let mut out = child.stdout.take().expect("piped stdout");
        let mut head = [0u8; 16];
        let n = out.read(&mut head).expect("read first output bytes");
        assert!(n > 0, "child produced no output before the pipe closed");
    } // ChildStdout dropped here => read end closed => writer sees EPIPE
    let mut err = Vec::new();
    child
        .stderr
        .take()
        .expect("piped stderr")
        .read_to_end(&mut err)
        .expect("drain stderr to EOF");
    let status = child.wait().expect("reap child");
    assert_eq!(
        status.code(),
        Some(0),
        "EPIPE (downstream closed early) is documented routine UX: exit 0"
    );
    assert!(
        err.is_empty(),
        "BrokenPipe exit must be SILENT, got: {:?}",
        String::from_utf8_lossy(&err)
    );
    let _ = fs::remove_dir_all(&d);
}

#[test]
fn stdout_enospc_devfull_exits_one_naming_the_errno() {
    if !Path::new("/dev/full").exists() {
        eprintln!("SKIP cli_boundaries::enospc: /dev/full not present on this host");
        return;
    }
    // escape emits a short deterministic record, so the failure happens on
    // the FIRST delivered write regardless of filesystem state.
    let devfull = fs::OpenOptions::new()
        .write(true)
        .open("/dev/full")
        .unwrap();
    let out = Command::new(bin_path())
        .args(["escape", "a*b"])
        .stdout(Stdio::from(devfull))
        .stderr(Stdio::piped())
        .output()
        .expect("spawn fastglob onto /dev/full");
    assert_eq!(
        out.status.code(),
        Some(1),
        "undelivered results are a RUNTIME error (V14): exit 1, stderr={}",
        String::from_utf8_lossy(&out.stderr)
    );
    let err = String::from_utf8_lossy(&out.stderr);
    assert!(
        err.starts_with("fastglob: stdout write failed:"),
        "diagnostic prefix wrong: {err:?}"
    );
    assert!(
        err.contains("No space left on device") && err.contains("os error 28"),
        "diagnostic must name the ACTUAL errno (ENOSPC=28), got: {err:?}"
    );
}
