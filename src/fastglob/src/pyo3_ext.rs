//! In-process PyO3 surface (compiled only with `--features pyo3`).
//!
//! Maturin builds this crate as the extension module `fastglob._core`
//! (module-name in public/python/pyproject.toml). It exposes the engine
//! byte-exact (`Vec<u8>` in, `Vec<Vec<u8>>` out) so the Python package can
//! own ALL str/bytes/PathLike typing — `os.fsencode` in, `os.fsdecode`
//! (surrogateescape) out — exactly as the removed subprocess transport did.
//!
//! Transport change (0.1.1): the package no longer shells out per call. The
//! caller's `dir_fd` is used in-process and NEVER closed by this module
//! (no `F_DUPFD`/`pass_fds` dance — that race window only existed because a
//! child process inherited the fd).
//!
//! Error contract mirrors the CLI verdicts (main.rs `parse`/
//! `validate_dir_fd`, docs/compatibility-contract.md) so the package's
//! tested error surface is unchanged:
//!   * NUL byte in pattern / root_dir      -> ValueError (stdlib parity:
//!     `ValueError: embedded null byte`)
//!   * pattern > 8192 bytes or > 512 path
//!     components                          -> RuntimeError "pattern too long"
//!     (walk::glob also guards this as defense-in-depth and would return []
//!     — the CLI/package contract is a misuse error, so it is reported)
//!   * `dir_fd` not an open directory      -> RuntimeError
//!     "fd is not a directory: {fd}" / "fd is not open or not a directory:
//!     {fd}"

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;

use crate::walk::{self, Opts};

/// Reject embedded NUL bytes and enforce the pattern length/component
/// bounds (same constants and wording as main.rs `parse`, RC-1/RC-3).
///
/// Input: `pattern: &[u8]` — raw glob pattern bytes
/// Output: `PyResult<()>` — `Ok(())` on success
/// Errors: ValueError "embedded null byte in PATTERN" or RuntimeError
/// "pattern too long"
fn check_pattern(pattern: &[u8]) -> PyResult<()> {
    if pattern.contains(&0) {
        return Err(PyValueError::new_err(
            "fastglob: embedded null byte in PATTERN",
        ));
    }
    const MAX_PATTERN_LEN: usize = 8192;
    const MAX_COMPONENTS: usize = 512;
    if pattern.len() > MAX_PATTERN_LEN {
        return Err(PyRuntimeError::new_err("fastglob: pattern too long"));
    }
    let components = if pattern.is_empty() {
        0
    } else {
        pattern.iter().filter(|&&b| b == b'/').count() + 1
    };
    if components > MAX_COMPONENTS {
        return Err(PyRuntimeError::new_err("fastglob: pattern too long"));
    }
    Ok(())
}

/// Verify `fd` is an open DIRECTORY fd (mirror of main.rs
/// `validate_dir_fd` — one accurate, distinguishable verdict per cause).
///
/// Input: `fd: i32` — caller-owned open fd (the shim already type-checked
/// it as a non-negative int and fstat-checked it as open)
/// Output: `PyResult<()>` — `Ok(())` if `fd` is a directory
/// Errors: RuntimeError "fd is not open or not a directory: {fd}" (fstat
/// failure) or "fd is not a directory: {fd}" (open but not a directory).
/// The fd is never closed.
fn check_dir_fd(fd: i32) -> PyResult<()> {
    // SAFETY: fstat on a caller-provided fd; st is fully initialized by
    // fstat on success (the != 0 result bails before any field is read).
    let mut st: libc::stat = unsafe { std::mem::zeroed() };
    let rc = unsafe { libc::fstat(fd, &mut st) };
    if rc != 0 {
        return Err(PyRuntimeError::new_err(format!(
            "fastglob: --dir-fd: fd is not open or not a directory: {fd}"
        )));
    }
    if (st.st_mode & libc::S_IFMT) != libc::S_IFDIR {
        return Err(PyRuntimeError::new_err(format!(
            "fastglob: --dir-fd: fd is not a directory: {fd}"
        )));
    }
    Ok(())
}

/// In-process `glob.glob(pathname, root_dir, dir_fd, recursive,
/// include_hidden)`.
///
/// Inputs (byte-exact; the Python shim has already fsencoded everything):
///   pattern: `Vec<u8>` — glob pattern
///   root_dir: `Option<Vec<u8>>` — filesystem origin shift (None/empty = cwd)
///   dir_fd: `Option<i32>` — open directory fd for relative resolution
///   recursive: `bool` — `**` matches zero or more directories
///   include_hidden: `bool` — `*`/`?`/`**` match dot-prefixed names
/// Output: `PyResult<Vec<Vec<u8>>>` — matching pathnames, duplicates
/// preserved, order unspecified
/// Errors: ValueError/ RuntimeError per the module-level contract above
#[pyfunction]
#[pyo3(
    signature = (pattern, root_dir = None, dir_fd = None, recursive = false, include_hidden = false)
)]
fn glob(
    pattern: Vec<u8>,
    root_dir: Option<Vec<u8>>,
    dir_fd: Option<i32>,
    recursive: bool,
    include_hidden: bool,
) -> PyResult<Vec<Vec<u8>>> {
    check_pattern(&pattern)?;
    if let Some(root) = &root_dir {
        if root.contains(&0) {
            return Err(PyValueError::new_err(
                "fastglob: embedded null byte in --root-dir",
            ));
        }
    }
    if let Some(fd) = dir_fd {
        check_dir_fd(fd)?;
    }
    let opts = Opts {
        recursive,
        include_hidden,
    };
    let root: &[u8] = root_dir.as_deref().unwrap_or(b"");
    Ok(walk::glob(&pattern, root, dir_fd, opts))
}

/// In-process `glob.iglob(...)` — same engine call as [`glob`] (the engine
/// IS the iglob wrapper; the Python shim materializes one call and iterates
/// lazily, as the subprocess transport did).
///
/// Inputs/Output/Errors: identical to [`glob`].
#[pyfunction]
#[pyo3(
    signature = (pattern, root_dir = None, dir_fd = None, recursive = false, include_hidden = false)
)]
fn iglob(
    pattern: Vec<u8>,
    root_dir: Option<Vec<u8>>,
    dir_fd: Option<i32>,
    recursive: bool,
    include_hidden: bool,
) -> PyResult<Vec<Vec<u8>>> {
    glob(pattern, root_dir, dir_fd, recursive, include_hidden)
}

/// In-process `glob.escape(pathname)` — port of `glob.escape`.
///
/// Input: `pattern: Vec<u8>` — arbitrary byte string
/// Output: `PyResult<Vec<u8>>` — with `*`, `?`, `[` wrapped as `[*]`, `[?]`,
/// `[[]`
/// Errors: ValueError on embedded NUL, RuntimeError "pattern too long"
/// (same guards as the CLI's `fastglob escape` subcommand)
#[pyfunction]
fn escape(pattern: Vec<u8>) -> PyResult<Vec<u8>> {
    check_pattern(&pattern)?;
    Ok(crate::matcher::escape(&pattern))
}

/// In-process `glob.has_magic(pathname)`.
///
/// Input: `pattern: Vec<u8>` — pattern to scan
/// Output: `bool` — `true` if it contains any of `*`, `?`, `[`
/// Errors: never (pure byte scan)
#[pyfunction]
fn has_magic(pattern: Vec<u8>) -> bool {
    crate::matcher::has_magic(&pattern)
}

/// In-process single-path match: does `path` match `pattern`?
///
/// Semantics: the existing engine matcher (`crate::matcher::matches`, the
/// fnmatch-3.12 translate port) applied to the WHOLE path string — no
/// filesystem access, no component-by-component glob walk. Consequences of
/// that documented contract (matches stdlib fnmatch/glob.translate, NOT the
/// component walk of `glob()`):
///   * `*` crosses `/` (`match("a*b", "a/x/b")` is true)
///   * `**` is two stars — identical to `*` (also crosses `/`)
///   * pattern components must match path components 1:1 — a pattern
///     without `/` never matches a path containing `/`
///
/// The path argument is matched as pure DATA (never opened, never stat'ed,
/// never split) — this is the function the loss-ledger-style callers want.
///
/// Inputs (byte-exact; the Python package has already fsencoded both):
///   pattern: `Vec<u8>` — glob pattern
///   path: `Vec<u8>` — the pathname to test
/// Output: `PyResult<bool>` — true iff `path` matches `pattern`
/// Errors: ValueError/RuntimeError per the module-level contract (NUL in
/// pattern, pattern too long) — same guards as [`glob`]/[`escape`]; the
/// PATH argument carries no guards (it is data, not a pattern or fs handle).
#[pyfunction]
fn r#match(pattern: Vec<u8>, path: Vec<u8>) -> PyResult<bool> {
    check_pattern(&pattern)?;
    Ok(crate::matcher::matches(
        &crate::matcher::compile(&pattern),
        &path,
    ))
}

/// Module `fastglob._core` — the in-process engine surface.
#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(glob, m)?)?;
    m.add_function(wrap_pyfunction!(iglob, m)?)?;
    m.add_function(wrap_pyfunction!(escape, m)?)?;
    m.add_function(wrap_pyfunction!(has_magic, m)?)?;
    m.add_function(wrap_pyfunction!(r#match, m)?)?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
