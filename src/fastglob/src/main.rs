//! fastglob CLI.
//!
//! Usage:
//!   fastglob [OPTIONS] PATTERN
//!   fastglob escape PATTERN
//!
//! Options:
//!   --recursive          enable `**` (zero-or-more directories)
//!   --include-hidden     let `*`/`?`/`**` match dot-prefixed names
//!   --null               NUL-delimit output (safe for newline/Unicode names)
//!   --root-dir PATH      shift the filesystem origin (results stay pattern-built)
//!   --dir-fd N           resolve relative to an open directory fd N
//!
//! Behavior (docs/compatibility-contract.md):
//!   * one pathname per line (or NUL with --null). Use --null for arbitrary
//!     filenames containing newlines; newline-delimited output splits on `\n`
//!     and cannot round-trip such names (see README notes on --null).
//!   * no matches -> exit 0, empty stdout (filesystem misses are not exceptional);
//!   * diagnostics on stderr; argument misuse -> exit 2.
//!   * stdout delivery: EPIPE (downstream reader closed early, e.g. `| head`)
//!     is routine -> exit 0; ANY OTHER stdout write error prints
//!     "fastglob: stdout write failed: {e}" on stderr and exits 1 (runtime
//!     error) — undelivered results must not report success (V14).
//!   * NUL bytes in PATTERN or --root-dir are rejected with exit 2 (matches
//!     Python ValueError: embedded null byte).
//!   * pattern length > 8192 bytes or >512 path components rejected with exit 2
//!     to bound recursion depth (prevents stack overflow on crafted patterns).

use fastglob::walk::Opts;
use std::ffi::OsString;
use std::io::Write;
use std::num::IntErrorKind;
use std::os::unix::ffi::OsStringExt;
use std::process::ExitCode;

const USAGE: &str = "usage: fastglob [OPTIONS] PATTERN\n\
                     usage: fastglob escape PATTERN\n\n\
                     Options:\n\
                     \x20 --recursive        enable ** (zero-or-more directories)\n\
                     \x20 --include-hidden   let * ? ** match dot-prefixed names\n\
                     \x20 --null             NUL-delimit output (use for arbitrary filenames with newlines)\n\
                     \x20 --root-dir PATH    shift the filesystem origin\n\
                     \x20 --dir-fd N         resolve relative to open directory fd N";

struct Args {
    escape: bool,
    recursive: bool,
    include_hidden: bool,
    null: bool,
    root_dir: Vec<u8>,
    dir_fd: Option<i32>,
    pattern: Vec<u8>,
}

fn err(msg: &str) -> ExitCode {
    eprintln!("fastglob: {msg}");
    eprintln!("{USAGE}");
    ExitCode::from(2)
}

/// Parse CLI arguments into `Args`.
///
/// Input: `args: &[Vec<u8>]` — raw CLI args as byte-exact vectors (no UTF-8 loss; each element is one argv entry's raw bytes via `OsStrExt::as_bytes`).
/// Output: `Result<Args, ExitCode>` — `Ok(Args)` on success, `Err(ExitCode(2))` on misuse (diagnostic on stderr), `Err(ExitCode(0))` on `--help`.
/// Errors: returns `ExitCode(2)` for unknown options, missing PATTERN, multiple PATTERNs, invalid `--dir-fd`, NUL bytes in PATTERN/root_dir, pattern too long (>8192 bytes or >512 components), or both --root-dir and --dir-fd specified.
/// Invariants: `root_dir` and `pattern` are byte-exact (no lossy UTF-8 conversion); caller must have converted `OsString` via `OsStringExt::into_vec`.
/// Note (F-003): the `--root-dir`/`--dir-fd` exclusion is tracked by a separate `root_dir_supplied` flag, because an EMPTY `root_dir` is the engine's "use cwd" sentinel and therefore cannot distinguish `--root-dir=` from an omitted flag.
fn parse(args: &[Vec<u8>]) -> Result<Args, ExitCode> {
    let mut escape = false;
    let mut recursive = false;
    let mut include_hidden = false;
    let mut null = false;
    let mut root_dir: Vec<u8> = Vec::new();
    let mut root_dir_supplied = false;
    let mut dir_fd: Option<i32> = None;
    let mut pattern: Vec<u8> = Vec::new();
    let mut have_pattern = false;

    let mut i = 0;
    while i < args.len() {
        let a = &args[i];
        if a == b"--" {
            i += 1;
            if i >= args.len() {
                return Err(err("missing PATTERN"));
            }
            if have_pattern {
                return Err(err("multiple PATTERN arguments"));
            }
            pattern = args[i].clone();
            have_pattern = true;
            i += 1;
            continue;
        }
        match a.as_slice() {
            b"--recursive" => recursive = true,
            b"--include-hidden" => include_hidden = true,
            b"--null" => null = true,
            // The bare `--` terminator is handled above (and always
            // `continue`s), so no arm here is reachable for it — F-006 removed
            // the dead `b"--" => {}` arm that implied otherwise.
            b"--root-dir" => {
                i += 1;
                if i >= args.len() {
                    return Err(err("--root-dir requires PATH"));
                }
                root_dir = args[i].clone();
                root_dir_supplied = true;
                i += 1;
                continue;
            }
            b"--dir-fd" => {
                i += 1;
                if i >= args.len() {
                    return Err(err("--dir-fd requires N"));
                }
                let s = std::str::from_utf8(&args[i]).map_err(|_| {
                    err(&format!(
                        "--dir-fd: not a valid number: {}",
                        String::from_utf8_lossy(&args[i])
                    ))
                })?;
                dir_fd = Some(parse_fd(s)?);
                i += 1;
                continue;
            }
            s if s.starts_with(b"--root-dir=") => {
                root_dir = s[b"--root-dir=".len()..].to_vec();
                // `--root-dir=` with an empty value still SUPPLIES the flag
                // (F-003): the guard below is about flag provenance.
                root_dir_supplied = true;
            }
            s if s.starts_with(b"--dir-fd=") => {
                let fd_str = std::str::from_utf8(&s[b"--dir-fd=".len()..]).map_err(|_| {
                    err(&format!(
                        "--dir-fd: not a valid number: {}",
                        String::from_utf8_lossy(&s[b"--dir-fd=".len()..])
                    ))
                })?;
                dir_fd = Some(parse_fd(fd_str)?);
            }
            b"-h" | b"--help" => {
                println!("{USAGE}");
                return Err(ExitCode::SUCCESS);
            }
            s if s.starts_with(b"-") && s.len() > 1 => {
                return Err(err(&format!(
                    "unknown option: {}",
                    String::from_utf8_lossy(s)
                )));
            }
            _ => {
                if a == b"escape" && !have_pattern && i == 0 {
                    escape = true;
                    i += 1;
                    continue;
                }
                if have_pattern {
                    return Err(err("multiple PATTERN arguments"));
                }
                pattern = a.clone();
                have_pattern = true;
            }
        }
        i += 1;
    }
    if !have_pattern {
        return Err(err("missing PATTERN"));
    }
    // RC-1: NUL byte rejection (match Python ValueError: embedded null byte)
    if pattern.contains(&0) {
        return Err(err("embedded null byte in PATTERN"));
    }
    if root_dir.contains(&0) {
        return Err(err("embedded null byte in --root-dir"));
    }
    // RC-3: pattern length / component-count guard to prevent unbounded recursion stack overflow
    const MAX_PATTERN_LEN: usize = 8192;
    const MAX_COMPONENTS: usize = 512;
    if pattern.len() > MAX_PATTERN_LEN {
        return Err(err("pattern too long"));
    }
    let components = if pattern.is_empty() {
        0
    } else {
        pattern.iter().filter(|&&b| b == b'/').count() + 1
    };
    if components > MAX_COMPONENTS {
        return Err(err("pattern too long"));
    }
    // Dual-flag precedence — both specified is misuse. Keyed on whether the
    // caller SUPPLIED `--root-dir`, not on the value being non-empty (F-003):
    // `--root-dir= --dir-fd N` used to slip past this guard because an empty
    // root_dir is indistinguishable from an omitted one.
    if root_dir_supplied && dir_fd.is_some() {
        return Err(err("cannot specify both --root-dir and --dir-fd"));
    }
    Ok(Args {
        escape,
        recursive,
        include_hidden,
        null,
        root_dir,
        dir_fd,
        pattern,
    })
}

/// Parse `--dir-fd N` value.
///
/// Input: `s: &str` — decimal fd string (UTF-8, no NUL)
/// Output: `Result<i32, ExitCode>` — `Ok(fd)` if valid, else `Err(ExitCode(2))`
///
/// Errors (one accurate, distinguishable diagnostic per cause, all exit 2 — Ct41a):
///
/// * text is not a decimal integer
///   -> "--dir-fd: not a valid number: {s}"
/// * numeric but outside the valid window (i32 overflow, negative, or above 2^30)
///   -> "--dir-fd: fd out of range: {arg} (valid 0..=1073741823)"
///
/// Invariants: never names a cause that did not fire; overflow is a RANGE
/// verdict (the text is a valid decimal that cannot be an fd), not a syntax one.
fn parse_fd(s: &str) -> Result<i32, ExitCode> {
    match s.parse::<i32>() {
        Ok(n) => {
            if !(0..1_073_741_824).contains(&n) {
                return Err(err(&format!(
                    "--dir-fd: fd out of range: {n} (valid 0..=1073741823)"
                )));
            }
            Ok(n)
        }
        Err(e) => {
            // Pos/NegOverflow: text IS a decimal number, just outside every
            // representable fd — report range, keep InvalidDigit/Empty as
            // format failures.
            if matches!(
                e.kind(),
                IntErrorKind::PosOverflow | IntErrorKind::NegOverflow
            ) {
                return Err(err(&format!(
                    "--dir-fd: fd out of range: {s} (valid 0..=1073741823)"
                )));
            }
            Err(err(&format!("--dir-fd: not a valid number: {s}")))
        }
    }
}

/// Validate that `fd` is an open directory fd.
///
/// Input: `fd: i32` — candidate fd number (already format+range-checked by `parse_fd`)
/// Output: `Result<(), ExitCode>` — `Ok(())` if `fstat(fd)` succeeds and fd is a directory, `Err(ExitCode(2))` otherwise
///
/// Errors (one accurate, distinguishable diagnostic per cause, all exit 2 — Ct41a):
///
/// * fstat fails (EBADF: closed or never-open fd)
///   -> "--dir-fd: fd is not open or not a directory: {fd}"
/// * fd is open but not a directory
///   -> "--dir-fd: fd is not a directory: {fd}"
///
/// Invariants: does not close fd; uses `libc::fstat` directly; the EBADF
/// branch must NOT claim a numeric-format failure — the number already
/// passed parsing, so the old "invalid fd number" wording lied about cause.
fn validate_dir_fd(fd: i32) -> Result<(), ExitCode> {
    // SAFETY: fstat on a caller-provided fd; st is fully initialized by
    // fstat on success (the != 0 result bails before any field is read).
    unsafe {
        let mut st: libc::stat = std::mem::zeroed();
        if libc::fstat(fd, &mut st) != 0 {
            return Err(err(&format!(
                "--dir-fd: fd is not open or not a directory: {fd}"
            )));
        }
        if (st.st_mode & libc::S_IFMT) != libc::S_IFDIR {
            return Err(err(&format!("--dir-fd: fd is not a directory: {fd}")));
        }
    }
    Ok(())
}

fn main() -> ExitCode {
    // RC-1: byte-exact argv via args_os + OsStringExt (no UTF-8 panic on non-UTF8, no lossy conversion)
    let args_os: Vec<OsString> = std::env::args_os().skip(1).collect();
    let args_bytes: Vec<Vec<u8>> = args_os.into_iter().map(|s| s.into_vec()).collect();
    let a = match parse(&args_bytes) {
        Ok(a) => a,
        Err(e) => return e,
    };

    // RC-4: validate dir_fd is an open directory before traversal (uniform exit 2 on EBADF/closed)
    if let Some(fd) = a.dir_fd {
        if let Err(e) = validate_dir_fd(fd) {
            return e;
        }
    }

    let stdout = std::io::stdout();
    let mut out = stdout.lock();
    let sep: &[u8] = if a.null { b"\0" } else { b"\n" };

    let results: Vec<Vec<u8>> = if a.escape {
        // fastglob escape PATTERN -> emit the escaped pattern as one item.
        vec![fastglob::matcher::escape(&a.pattern)]
    } else {
        let opts = Opts {
            recursive: a.recursive,
            include_hidden: a.include_hidden,
        };
        fastglob::walk::glob(&a.pattern, &a.root_dir, a.dir_fd, opts)
    };

    // Deliver results (V14). Rust ignores SIGPIPE, so a downstream reader
    // closing early (`fastglob '*' | head -1`) surfaces here as EPIPE: that
    // is routine and part of the documented pipe UX — exit 0. ANY OTHER
    // stdout error (ENOSPC on /dev/full, EIO, EBADF, ...) means results were
    // NOT delivered: print a diagnostic naming the actual errno and exit 1
    // (runtime-error row of docs/cli-reference.md "Exit Codes") instead of
    // reporting success for a failed delivery.
    let report_write_err = |e: std::io::Error| -> ExitCode {
        if e.kind() == std::io::ErrorKind::BrokenPipe {
            return ExitCode::SUCCESS;
        }
        eprintln!("fastglob: stdout write failed: {e}");
        ExitCode::from(1)
    };
    for r in &results {
        if let Err(e) = out.write_all(r) {
            return report_write_err(e);
        }
        if let Err(e) = out.write_all(sep) {
            return report_write_err(e);
        }
    }
    if let Err(e) = out.flush() {
        return report_write_err(e);
    }
    ExitCode::SUCCESS
}
