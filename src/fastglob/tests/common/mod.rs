//! Shared helpers for fastglob CLI integration tests.
//!
//! These tests drive the REAL binary (`CARGO_BIN_EXE_fastglob` — cargo
//! rebuilds it from current sources before integration tests run, so every
//! assertion below is made against post-W3 behavior, never a stale binary).
//!
//! Assertion discipline (Z1): every misuse row asserts ALL of — exact exit
//! code, `fastglob: `-prefixed diagnostic containing the documented fragment,
//! usage block on stderr, and EMPTY stdout (no partial results on misuse).
//! Accepted runs assert exit 0 AND silent stderr. Multiset comparisons use
//! HashMap counters with multiplicity, never ordered lists.
//!
//! fd-injection helper: `run_with_fd_injection` places a raw fd at a chosen
//! number inside the child via `pre_exec` (runs after stdio setup, before
//! execve; `dup2` clears CLOEXEC on the target so it survives exec). This is
//! real kernel-level fault injection — closed fds and wrong-type fds are
//! forced for real, not simulated.
//!
//! NOTE: this module is compiled separately into EACH test crate. Helpers
//! used by only one crate carry #[allow(dead_code)] so the other crate does
//! not emit dead-code warnings for them.
// Justification for the allow (BANNED: `#[allow]` requires a reason):
// shared test helpers — unwrap()/expect() are the panic vehicle for fixture
// setup and child-process control, not production error paths.
#![allow(clippy::unwrap_used, clippy::expect_used)]

use std::collections::HashMap;
use std::ffi::CString;
use std::os::unix::process::CommandExt;
use std::path::Path;
use std::process::{Command, Output};

/// Absolute path of the freshly-built `fastglob` binary under test.
pub fn bin_path() -> &'static str {
    env!("CARGO_BIN_EXE_fastglob")
}

/// Run the binary with plain-text args, capturing stdout/stderr/exit code.
pub fn run(args: &[&str]) -> Output {
    Command::new(bin_path())
        .args(args)
        .output()
        .expect("spawn fastglob")
}

/// Run the binary with `inject` executed inside the child after fork+stdio
/// setup and before execve. Used to force fd-table states (closed fd, fd of
/// the wrong file type) that cannot be arranged through argv alone.
// dead_code: used by the cli_misuse crate only (see header note).
#[allow(dead_code)]
pub fn run_with_fd_injection(
    args: &[&str],
    inject: impl FnMut() -> std::io::Result<()> + Send + Sync + 'static,
) -> Output {
    let mut cmd = Command::new(bin_path());
    cmd.args(args);
    // SAFETY: the closure only dup2/close's caller-provided fds inside the
    // forked child before exec; no async-signal-unsafe allocation happens.
    unsafe {
        cmd.pre_exec(inject);
    }
    cmd.output().expect("spawn fastglob with fd injection")
}

/// libc open(2) read-only; returns raw fd or -1. Caller closes.
// dead_code: used by the cli_misuse crate only (see header note).
#[allow(dead_code)]
pub fn open_ro(path: &Path) -> i32 {
    let c = CString::new(path.as_os_str().as_encoded_bytes()).expect("path without NUL");
    // SAFETY: c is a valid NUL-terminated path for the duration of the call.
    unsafe { libc::open(c.as_ptr(), libc::O_RDONLY) }
}

/// libc close(2); ignore result in test cleanup paths.
// dead_code: used by the cli_misuse crate only (see header note).
#[allow(dead_code)]
pub fn close_fd(fd: i32) {
    // SAFETY: closing an fd this test opened itself.
    unsafe {
        libc::close(fd);
    }
}

/// pid-keyed tempdir matching walk.rs test conventions: wipe-then-create,
/// explicit `remove_dir_all` at test end (pid suffix keeps parallel runs
/// isolated even if a panicking test leaks one).
pub fn tmpdir(tag: &str) -> std::path::PathBuf {
    let p = std::env::temp_dir().join(format!("fastglob_cli_test_{tag}_{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&p);
    std::fs::create_dir_all(&p).unwrap();
    p
}

/// Assert the full misuse contract: exit EXACTLY 2, empty stdout (misuse must
/// never emit results), first stderr line is `fastglob: {diagnostic}` carrying
/// `expected_fragment`, and the usage block follows on STDERR (err() prints
/// diagnostic + usage both to stderr; --help is the only stdout-usage path).
pub fn assert_misuse(label: &str, out: &Output, expected_fragment: &str) {
    assert_eq!(
        out.status.code(),
        Some(2),
        "{label}: exit must be 2 (misuse), got {:?}; stderr={}",
        out.status.code(),
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(
        out.stdout.is_empty(),
        "{label}: misuse emitted stdout: {:?}",
        String::from_utf8_lossy(&out.stdout)
    );
    let err = String::from_utf8_lossy(&out.stderr);
    let first = err.lines().next().unwrap_or_default();
    assert!(
        first.starts_with("fastglob: "),
        "{label}: diagnostic missing 'fastglob: ' prefix: {first:?}"
    );
    assert!(
        first.contains(expected_fragment),
        "{label}: expected diagnostic fragment {expected_fragment:?} in {first:?}"
    );
    assert!(
        err.contains("usage: fastglob [OPTIONS] PATTERN"),
        "{label}: usage block must follow the diagnostic on stderr"
    );
}

/// Assert the accepted-run contract: exit 0 and SILENT stderr (an accepted run
/// leaking any diagnostic would be a contract break even with exit 0).
pub fn assert_accept(label: &str, out: &Output) {
    assert_eq!(
        out.status.code(),
        Some(0),
        "{label}: exit must be 0, got {:?}; stderr={}",
        out.status.code(),
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(
        out.stderr.is_empty(),
        "{label}: accepted run wrote to stderr: {:?}",
        String::from_utf8_lossy(&out.stderr)
    );
}

/// Split engine output into records on `sep`, dropping exactly ONE trailing
/// empty piece (the engine terminates every record with the separator,
/// including the last — mirrors python/fastglob/_paths).
// dead_code: used by the cli_misuse crate only (see header note).
#[allow(dead_code)]
pub fn records(raw: &[u8], sep: u8) -> Vec<Vec<u8>> {
    let mut parts: Vec<Vec<u8>> = raw.split(|&b| b == sep).map(<[u8]>::to_vec).collect();
    if parts.last().is_some_and(|p| p.is_empty()) {
        parts.pop();
    }
    parts
}

/// Multiset counter WITH multiplicity (AGENTS.md iron law: Counter, never
/// ordered list, never plain set).
// dead_code: used by the cli_misuse crate only (see header note).
#[allow(dead_code)]
pub fn counter(items: &[Vec<u8>]) -> HashMap<Vec<u8>, usize> {
    let mut m: HashMap<Vec<u8>, usize> = HashMap::new();
    for x in items {
        *m.entry(x.clone()).or_insert(0) += 1;
    }
    m
}
