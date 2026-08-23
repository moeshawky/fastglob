//! fastglob — a single-pass pathname-globbing engine for Linux,
//! compatibility-locked to the installed Python stdlib `glob`
//! (`/usr/local/lib/python3.12/glob.py`, identified at runtime by the
//! harness, never hard-coded here).
//!
//! Public surface:
//!
//! * [`walk::glob`] — the engine: `glob(pathname, root_dir, dir_fd, opts)`
//!   returning byte-exact result lists (duplicates preserved, order
//!   documented-unspecified).
//! * [`matcher`] — fnmatch-3.12-exact pattern compilation and matching.
//! * [`escape`], [`has_magic`] — ports of `glob.escape` / `glob.has_magic`.
//!
//! Byte-exactness: everything is `&[u8]` / `Vec<u8>` end to end (OsStr on
//! Unix). No lossy UTF-8 conversions anywhere on the hot path; the
//! surrogateescape character model lives inside the matcher only.
//!
//! Python surface: built with `--features pyo3`, the crate additionally
//! compiles the extension module `fastglob._core` (see `pyo3_ext`) — the
//! in-process transport the `fastglob` Python package binds to (maturin,
//! public/python).

pub mod matcher;
#[cfg(feature = "pyo3")]
mod pyo3_ext;
pub mod walk;

use std::ffi::{OsStr, OsString};
use std::os::unix::ffi::{OsStrExt, OsStringExt};

/// Convenience wrapper for `glob.glob(pathname, root_dir, dir_fd, recursive, include_hidden)`.
///
/// Inputs:
///   pathname: pattern as `&OsStr` (byte-exact, may contain `*`, `?`, `[`, `**`)
///   root_dir: optional filesystem origin shift (`Some(&OsStr)` or `None` for cwd)
///   dir_fd: optional open directory fd (`Some(i32)` or `None`); must be a valid directory fd when `Some`
///   recursive: when `true`, `**` matches zero or more directories (Level A)
///   include_hidden: when `true`, `*`/`?`/`**` match dot-prefixed names
/// Output: `Vec<OsString>` — matching pathnames, duplicates preserved, order unspecified
/// Errors: never panics on missing matches (returns empty vec); filesystem errors pruned per compat semantics
pub fn glob(
    pathname: &OsStr,
    root_dir: Option<&OsStr>,
    dir_fd: Option<i32>,
    recursive: bool,
    include_hidden: bool,
) -> Vec<OsString> {
    let opts = walk::Opts {
        recursive,
        include_hidden,
    };
    walk::glob(
        pathname.as_bytes(),
        root_dir.map_or(b"" as &[u8], |r| r.as_bytes()),
        dir_fd,
        opts,
    )
    .into_iter()
    .map(OsString::from_vec)
    .collect()
}

/// Port of `glob.escape`.
///
/// Input: `pathname: &OsStr` — arbitrary byte string
/// Output: `OsString` — with `*`, `?`, `[` wrapped as `[*]`, `[?]`, `[[]` (Level A)
/// Errors: never fails (pure byte transformation)
pub fn escape(pathname: &OsStr) -> OsString {
    OsString::from_vec(matcher::escape(pathname.as_bytes()))
}

/// Port of `glob.has_magic`.
///
/// Input: `s: &OsStr` — pattern to scan
/// Output: `bool` — `true` if `s` contains any of `*`, `?`, `[` (Level A)
/// Errors: never fails
pub fn has_magic(s: &OsStr) -> bool {
    matcher::has_magic(s.as_bytes())
}
