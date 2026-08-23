//! Z1 family A — CLI misuse table (closes Maat TP43/TP49) and family D —
//! line-mode chain test (completes TP46).
//!
//! Family A: every parse()/parse_fd()/validate_dir_fd() error branch gets an
//! aimed case. Expectations are the DOCUMENTED wording (docs/api.md "Errors",
//! docs/cli-reference.md "Exit Codes"), frozen against a probe of the freshly
//! built binary before these tests were written. Each row asserts behavior,
//! not just exit codes: exact code 2, `fastglob: ` diagnostic prefix, exact
//! message fragment, usage-on-stderr, empty stdout.
//!
//! Honest coverage map — branches NOT reachable here:
//! * NUL-in-PATTERN / NUL-in---root-dir guards (parse()): execve argv entries
//!   are NUL-terminated by the kernel ABI, so no spawn can deliver an
//!   interior NUL byte to parse(); Rust's own Command rejects such args even
//!   earlier. These arms are defense-in-depth for library callers. Their
//!   user-observable contract (ValueError: embedded null byte) is exercised
//!   FOR REAL in tests/test_package.py, where subprocess raises it.
//! * stdout-EBADF classification: unobservable through rustc 1.98 buffered
//!   Stdout (see cli_boundaries.rs header evidence).
//!
// Justification for the allow (BANNED: `#[allow]` requires a reason):
// integration-test crate — unwrap()/expect() are the panic vehicle for
// fixture setup and child-process control, not production error paths.
#![allow(clippy::unwrap_used, clippy::expect_used)]
mod common;

use common::{
    assert_accept, assert_misuse, close_fd, counter, open_ro, records, run, run_with_fd_injection,
    tmpdir,
};
use std::collections::HashMap;
use std::fs;
use std::os::unix::io::AsRawFd;

/// (label, argv, expected diagnostic fragment). The fragment list mirrors
/// docs/api.md "Errors" one-to-one; dir-fd rows carry a PATTERN so each one
/// reaches its intended branch instead of tripping missing-PATTERN first
/// (probe finding: parse() completes before validate_dir_fd runs).
const MISUSE_TABLE: &[(&str, &[&str], &str)] = &[
    ("no_args", &[], "missing PATTERN"),
    ("flags_only", &["--null"], "missing PATTERN"),
    ("dd_then_nothing", &["--"], "missing PATTERN"),
    ("two_positionals", &["a", "b"], "multiple PATTERN arguments"),
    (
        "two_after_dd",
        &["--", "x", "y"],
        "multiple PATTERN arguments",
    ),
    (
        "root_dir_no_value",
        &["--root-dir"],
        "--root-dir requires PATH",
    ),
    ("dir_fd_no_value", &["--dir-fd"], "--dir-fd requires N"),
    (
        "dir_fd_not_number_space_form",
        &["--dir-fd", "abc", "*"],
        "--dir-fd: not a valid number: abc",
    ),
    (
        "dir_fd_not_number_equals_form",
        &["--dir-fd=abc", "*"],
        "--dir-fd: not a valid number: abc",
    ),
    (
        // empty value is IntErrorKind::Empty -> format verdict, not range
        "dir_fd_empty_equals_form",
        &["--dir-fd=", "*"],
        "--dir-fd: not a valid number:",
    ),
    (
        "dir_fd_negative",
        &["--dir-fd", "-5", "*"],
        "--dir-fd: fd out of range: -5 (valid 0..=1073741823)",
    ),
    (
        // text parses nowhere near i32 -> overflow gets a RANGE verdict
        "dir_fd_pos_overflow_text",
        &["--dir-fd", "3000000000", "*"],
        "--dir-fd: fd out of range: 3000000000 (valid 0..=1073741823)",
    ),
    (
        "dir_fd_window_max_plus_1",
        &["--dir-fd=1073741824", "*"],
        "--dir-fd: fd out of range: 1073741824 (valid 0..=1073741823)",
    ),
    (
        // i32::MAX parses fine but sits above the 2^30 window -> RANGE
        "dir_fd_i32_max_is_range",
        &["--dir-fd", "2147483647", "*"],
        "--dir-fd: fd out of range: 2147483647 (valid 0..=1073741823)",
    ),
    (
        "dir_fd_neg_overflow_text",
        &["--dir-fd=-99999999999999999999", "*"],
        "--dir-fd: fd out of range: -99999999999999999999 (valid 0..=1073741823)",
    ),
    (
        "unknown_option_long",
        &["--bogus", "x"],
        "unknown option: --bogus",
    ),
    ("unknown_option_short", &["-x"], "unknown option: -x"),
    (
        // dual-flag fires in parse(), BEFORE validate_dir_fd would see the
        // closed max fd — precedence pinned deliberately
        "dual_flag_beats_validation",
        &["--root-dir", "/tmp", "--dir-fd", "1073741823", "*"],
        "cannot specify both --root-dir and --dir-fd",
    ),
];

#[test]
fn misuse_table_exit_code_and_stderr_class_per_row() {
    for (label, argv, fragment) in MISUSE_TABLE {
        let out = run(argv);
        assert_misuse(label, &out, fragment);
    }
}

#[test]
fn help_prints_usage_to_stdout_exit0_silent_stderr() {
    // stream-routing contract: --help is the ONLY usage path on stdout
    let out = run(&["--help"]);
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert_eq!(out.status.code(), Some(0));
    assert!(
        out.stderr.is_empty(),
        "stderr must stay silent: {:?}",
        out.stderr
    );
    assert!(
        stdout.contains("usage: fastglob [OPTIONS] PATTERN")
            && stdout.contains("--recursive")
            && stdout.contains("--null"),
        "usage content incomplete: {stdout:?}"
    );
}

#[test]
fn escape_happy_path_exact_stdout_bytes() {
    let out = run(&["escape", "a*b"]);
    assert_accept("escape", &out);
    assert_eq!(out.stdout, b"a[*]b\n", "exact escape output");
}

#[test]
fn miss_is_success_exit0_empty_streams() {
    // documented: filesystem misses are not exceptional (exit 0, silent)
    let d = tmpdir("miss");
    let root = d.to_str().unwrap().to_owned();
    let out = run(&["nope_*.xyz", "--root-dir", &root]);
    assert_accept("miss", &out);
    assert!(out.stdout.is_empty());
    let _ = fs::remove_dir_all(&d);
}

#[test]
fn dir_fd_rlimit_distant_closed_reports_not_open() {
    // 2^30 - 1 is inside the accepted window but no fd table reaches it:
    // fstat EBADF must produce the NOT-OPEN verdict (not a number-format lie)
    let out = run(&["--dir-fd", "1073741823", "*"]);
    assert_misuse(
        "dir_fd_max_closed",
        &out,
        "--dir-fd: fd is not open or not a directory: 1073741823",
    );
}

#[test]
fn dir_fd_low_closed_via_dup2_then_close_reports_not_open() {
    // Real injection at a realistic LOW fd number: dup an auxiliary file onto
    // 42 in the child, then close 42 again — deterministic EBADF at 42.
    let aux = fs::File::open("/dev/null").unwrap();
    let raw = aux.as_raw_fd();
    let out = run_with_fd_injection(&["--dir-fd", "42", "*"], move || {
        // SAFETY: raw is open in this child (inherited across fork).
        if unsafe { libc::dup2(raw, 42) } == -1 {
            return Err(std::io::Error::last_os_error());
        }
        if unsafe { libc::close(42) } == -1 {
            return Err(std::io::Error::last_os_error());
        }
        Ok(())
    });
    drop(aux);
    assert_misuse(
        "dir_fd_low_closed",
        &out,
        "--dir-fd: fd is not open or not a directory: 42",
    );
}

#[test]
fn dir_fd_open_regular_file_reports_not_a_directory() {
    let d = tmpdir("notdir");
    let f = d.join("plain.txt");
    fs::write(&f, b"x").unwrap();
    let src = open_ro(&f);
    assert!(src >= 0, "open fixture file");
    let out = run_with_fd_injection(&["--dir-fd", "42", "*"], move || {
        // SAFETY: src is open in this child (inherited across fork); dup2
        // clears CLOEXEC on 42 so it survives into the engine image.
        if unsafe { libc::dup2(src, 42) } == -1 {
            return Err(std::io::Error::last_os_error());
        }
        Ok(())
    });
    close_fd(src);
    let _ = fs::remove_dir_all(&d);
    assert_misuse(
        "dir_fd_open_file",
        &out,
        "--dir-fd: fd is not a directory: 42",
    );
}

#[test]
fn dir_fd_open_directory_lists_tree_guard_satisfied_path() {
    // Symmetry (guard-branch class): the SAME injection mechanism must also
    // prove the guard-satisfied path works end-to-end through fd 42.
    let d = tmpdir("happyfd");
    fs::write(d.join("alpha"), b"").unwrap();
    fs::write(d.join("beta"), b"").unwrap();
    fs::create_dir(d.join("gamma_dir")).unwrap();
    let src = open_ro(&d);
    assert!(src >= 0);
    let out = run_with_fd_injection(&["--null", "--dir-fd", "42", "--", "*"], move || {
        // SAFETY: as above — inherited fd dup'd onto the target slot.
        if unsafe { libc::dup2(src, 42) } == -1 {
            return Err(std::io::Error::last_os_error());
        }
        Ok(())
    });
    close_fd(src);
    let _ = fs::remove_dir_all(&d);
    assert_accept("dir_fd_happy", &out);
    let got = records(&out.stdout, b'\0');
    assert_eq!(
        counter(&got),
        counter(&[b"alpha".to_vec(), b"beta".to_vec(), b"gamma_dir".to_vec()]),
        "listing via injected dir fd must match tree contents (multiset)"
    );
}

// ---------------------------------------------------------------------------
// Family D (TP46 completion): default LINE mode was chain-tested zero times
// before Z1 (the package always passes --null). Drive both separator modes
// over identical trees and require multiset equality WITH multiplicity.
//
// Newline-containing filenames are deliberately excluded from THIS pair:
// line mode cannot round-trip them by documentation (main.rs header), so
// including one would make the counters legitimately differ. That behavior
// is covered at the package layer (test_package.py) where --null always runs
// and 'we\nird.txt' round-trips exactly.
// ---------------------------------------------------------------------------

fn build_chain_tree(tag: &str) -> std::path::PathBuf {
    let d = tmpdir(tag);
    fs::write(d.join("a.txt"), b"").unwrap();
    fs::write(d.join("brack [x].txt"), b"").unwrap();
    fs::write(d.join("café-✓.txt"), b"").unwrap();
    fs::create_dir_all(d.join("sub/deep")).unwrap();
    fs::write(d.join("sub/c.txt"), b"").unwrap();
    fs::write(d.join("sub/deep/d.txt"), b"").unwrap();
    d
}

fn split_and_count(raw: &[u8], sep: u8) -> HashMap<Vec<u8>, usize> {
    let recs = records(raw, sep);
    assert!(!recs.is_empty(), "engine produced zero records");
    assert!(
        recs.iter().all(|r| !r.is_empty()),
        "empty record piece => doubled separator or empty name: {recs:?}"
    );
    counter(&recs)
}

#[test]
fn line_mode_multiset_equals_null_mode_plain_star() {
    let d = build_chain_tree("chain1");
    let root = d.to_str().unwrap().to_owned();
    let null_out = run(&["--null", "*", "--root-dir", &root]);
    let line_out = run(&["*", "--root-dir", &root]);
    assert_accept("null_mode", &null_out);
    assert_accept("line_mode", &line_out);
    // separator contract: every record terminated => trailing sep present
    assert!(null_out.stdout.ends_with(b"\0"));
    assert!(line_out.stdout.ends_with(b"\n"));
    assert_eq!(
        split_and_count(&line_out.stdout, b'\n'),
        split_and_count(&null_out.stdout, b'\0'),
        "line-mode output must be the same multiset as NUL-mode output"
    );
    let _ = fs::remove_dir_all(&d);
}

#[test]
fn line_mode_multiset_equals_null_mode_recursive_starstar() {
    let d = build_chain_tree("chain2");
    let root = d.to_str().unwrap().to_owned();
    let null_out = run(&["--null", "--recursive", "**/*.txt", "--root-dir", &root]);
    let line_out = run(&["--recursive", "**/*.txt", "--root-dir", &root]);
    assert_accept("null_mode_rec", &null_out);
    assert_accept("line_mode_rec", &line_out);
    assert_eq!(
        split_and_count(&line_out.stdout, b'\n'),
        split_and_count(&null_out.stdout, b'\0'),
        "recursive line-vs-NUL multisets must agree"
    );
    let _ = fs::remove_dir_all(&d);
}
