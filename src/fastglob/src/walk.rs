//! Single-pass walker — a literal port of the installed
//! `/usr/lib/python3.12/glob.py` algorithm (Level A source of truth):
//!
//!   iglob -> _iglob -> _glob0 / _glob1 / _glob2 -> _rlistdir -> _iterdir
//!
//! Byte-exact (OsStr end to end; no lossy UTF-8). Design notes:
//!
//! * `DirEntry::file_type()` (d_type) decides directory-ness without lstat
//!   for the common case; symlinks are followed via stat (exactly what
//!   CPython's `DirEntry.is_dir()` does), and an ELOOP failure prunes the
//!   entry/branch the same way the oracle's swallowed OSError does.
//! * Broken symlinks ARE returned when they match: the no-magic branch uses
//!   lstat (`_lexists`) and `_glob1` has no existence check at all.
//! * Result multiplicity is inherited from algorithmic identity: CPython
//!   can yield the same path through several `**` expansion chains, and so
//!   can this port (never deduplicated, never ordered).
//! * `dir_fd` shifts the scan origin only (openat/fstatat); the yielded
//!   strings are built purely from pattern parts, as in CPython.

use crate::matcher;
use std::ffi::CString;
use std::os::unix::ffi::OsStrExt;
use std::sync::OnceLock;

/// TEST AFFORDANCE (inert when unset): `FASTGLOB_NO_FUSED` env var.
/// Non-empty value disables the fused fast path entirely, forcing the
/// verbatim CPython port for every pattern. Latched ONCE at first glob
/// call via `OnceLock<bool>` — reading `std::env::var` on every call
/// would tax the hot path. The latched value is process-lifetime.
/// With the var unset, output is byte-identical to the default fused path.
/// Note: `try_fast` already returns `None` when `dir_fd.is_some()`, so
/// this switch is a no-op for `--dir-fd` invocations.
static FUSED_DISABLED: OnceLock<bool> = OnceLock::new();

/// Returns `true` if the fused fast path is disabled via `FASTGLOB_NO_FUSED`.
/// Input: none. Output: `bool` — `true` iff env var is set non-empty.
/// Errors: none (`var_os` used, no panic). Invariant: value latched at
/// first call, process-lifetime (see `FUSED_DISABLED`).
fn fused_disabled() -> bool {
    *FUSED_DISABLED.get_or_init(|| {
        std::env::var_os("FASTGLOB_NO_FUSED")
            .map(|v| !v.to_string_lossy().is_empty())
            .unwrap_or(false)
    })
}

/// Option flags mirroring `glob.glob(...)` keyword arguments.
///
/// Fields:
///   recursive: `bool` — when `true`, `**` matches zero or more directories (Level A)
///   include_hidden: `bool` — when `true`, `*`/`?`/`**` match dot-prefixed names (Level A, 3.11+)
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Opts {
    /// When `true`, `**` matches zero or more directories (Level A).
    pub recursive: bool,
    /// When `true`, `*`/`?`/`**` match dot-prefixed names (Level A, 3.11+).
    pub include_hidden: bool,
}

/// `glob.glob(pathname, root_dir, dir_fd, recursive, include_hidden)`.
///
/// Inputs:
///   pattern: `&[u8]` — glob pattern, byte-exact (may contain NUL; truncated at C-string boundary in filesystem calls). Length constrained: >8192 bytes or >512 components is treated as no-match (defense-in-depth; CLI rejects with exit 2).
///   root_dir: `&[u8]` — filesystem origin shift (empty = cwd), byte-exact
///   dir_fd: `Option<i32>` — open directory fd for relative resolution (`None` = no fd); if `Some`, must be a valid directory fd or filesystem ops yield `[]` (CLI validates and returns exit 2)
///   opts: `Opts` — `recursive` and `include_hidden` flags
/// Output: `Vec<Vec<u8>>` — matching pathnames, duplicates preserved, order unspecified (Counter equality)
/// Errors: never panics on missing matches (returns empty vec); filesystem open/read errors pruned per compat; invalid `dir_fd` yields empty (openat failure -> `[]`); pattern too long (>8192 or >512 components) yields empty to prevent stack overflow
pub fn glob(pattern: &[u8], root_dir: &[u8], dir_fd: Option<i32>, opts: Opts) -> Vec<Vec<u8>> {
    const MAX_PATTERN_LEN: usize = 8192;
    const MAX_COMPONENTS: usize = 512;
    if pattern.len() > MAX_PATTERN_LEN {
        return Vec::new();
    }
    if !pattern.is_empty() && pattern.iter().filter(|&&b| b == b'/').count() + 1 > MAX_COMPONENTS {
        return Vec::new();
    }
    let mut out = Vec::new();
    iglob_collect(pattern, root_dir, dir_fd, opts, &mut out);
    out
}

/// Port of the public `iglob()` wrapper: runs `_iglob`, then — when the
/// pattern is empty or `recursive` with a `**`-prefixed pattern — drops the
/// leading empty string if the first result is `''` (`s = next(it); if s:`).
fn iglob_collect(
    pattern: &[u8],
    root: &[u8],
    dir_fd: Option<i32>,
    opts: Opts,
    out: &mut Vec<Vec<u8>>,
) {
    // Fused fast paths (hot pattern families): single-pass traversal with
    // byte-identical result multisets. `None` -> verbatim port below.
    // `FASTGLOB_NO_FUSED` (non-empty) disables the fused path entirely,
    // forcing the verbatim port for every pattern (test affordance, inert
    // when unset, latched once — see `fused_disabled`).
    if !fused_disabled() {
        if let Some(res) = try_fast(pattern, root, dir_fd, opts) {
            out.extend(res);
            return;
        }
    }
    let mut inner = _iglob(pattern, root, dir_fd, opts, false);
    if (pattern.is_empty() || (opts.recursive && pattern.len() >= 2 && &pattern[0..2] == b"**"))
        && inner.first().is_some_and(|s| s.is_empty())
    {
        inner.remove(0);
    }
    out.extend(inner);
}

// ---------------------------------------------------------------------------
// posixpath ports (byte-exact)
// ---------------------------------------------------------------------------

/// Strip trailing '/' bytes (slice `trim_end_matches` unavailable here).
fn rtrim_slashes(p: &[u8]) -> &[u8] {
    let n = p.iter().rev().take_while(|b| **b == b'/').count();
    &p[..p.len() - n]
}

/// Port of `posixpath.split`.
///
/// Input: `p: &[u8]` — path as bytes (may contain repeated slashes, may be all-slashes like b"/" or b"///")
/// Output: `(&[u8], &[u8])` — `(head, tail)` where `head` is dirname part with trailing slashes stripped unless head is all-slashes (posix semantics: head != "/"*len(head) => rstrip), `tail` is everything after the final slash.
/// Errors: never fails (pure slice operation)
/// Invariants: matches `posixpath.split` byte-for-byte; for `b"/"` returns `(b"/", b"")`, for `b"//"` returns `(b"//", b"")`, for `b"a//b"` returns `(b"a", b"b")`; `tail` never contains `/`.
pub fn p_split(p: &[u8]) -> (&[u8], &[u8]) {
    let i = match p.iter().rposition(|b| *b == b'/') {
        Some(idx) => idx + 1,
        None => 0,
    };
    let head = &p[..i];
    let tail = &p[i..];
    // posix: if head and head != sep*len(head): head = head.rstrip(sep)
    let dirname: &[u8] = if head.is_empty() || head.iter().all(|&b| b == b'/') {
        head
    } else {
        rtrim_slashes(head)
    };
    (dirname, tail)
}

/// Port of `posixpath.join(a, b)` (single extra part).
///
/// Inputs: `a: &[u8]`, `b: &[u8]` — path components, byte-exact
/// Output: `Vec<u8>` — joined path (`b` absolute overrides `a`; exactly one `/` inserted when needed)
/// Errors: never fails
pub fn p_join(a: &[u8], b: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(a.len() + b.len() + 1);
    if b.starts_with(b"/") {
        out.extend_from_slice(b);
    } else if a.is_empty() || a.ends_with(b"/") {
        out.extend_from_slice(a);
        out.extend_from_slice(b);
    } else {
        out.extend_from_slice(a);
        out.push(b'/');
        out.extend_from_slice(b);
    }
    out
}

/// Port of glob.py's private `_join` (root_dir combination).
///
/// Inputs: `root: &[u8]`, `x: &[u8]` — root and pattern part
/// Output: `Vec<u8>` — `p_join(root, x)` unless either is empty (then the non-empty one)
/// Errors: never fails
pub fn fj(root: &[u8], x: &[u8]) -> Vec<u8> {
    if root.is_empty() || x.is_empty() {
        if root.is_empty() {
            x.to_vec()
        } else {
            root.to_vec()
        }
    } else {
        p_join(root, x)
    }
}

fn is_recursive(p: &[u8]) -> bool {
    p == b"**"
}

fn is_hidden(p: &[u8]) -> bool {
    p.first() == Some(&b'.')
}

// ---------------------------------------------------------------------------
// Filesystem primitives (port of _lexists / _isdir / _iterdir)
// ---------------------------------------------------------------------------

/// C string for a path: truncates at the first NUL byte (C-string boundary).
/// Note: CLI now rejects NUL bytes with exit 2 (matching Python ValueError), so this
/// truncation is a defense-in-depth fallback for library callers that bypass CLI validation.
/// Direct `walk::glob` callers with embedded NUL will see truncated paths (no panic), but
/// should validate inputs beforehand to match stdlib ValueError semantics.
fn cstr(p: &[u8]) -> CString {
    let n = p.iter().position(|&b| b == 0).unwrap_or(p.len());
    CString::new(p[..n].to_vec()).unwrap_or_default()
}

/// Port of `_lexists` (lstat, broken symlinks count as existing).
fn lexists(dir_fd: Option<i32>, p: &[u8]) -> bool {
    match dir_fd {
        None => std::fs::symlink_metadata(std::ffi::OsStr::from_bytes(p)).is_ok(),
        Some(fd) => {
            // SAFETY: fd is a caller-owned open directory fd; c is a valid
            // NUL-terminated C string; st is fully initialized by fstatat on
            // success (the == 0 result is checked before the stat is read).
            unsafe {
                let c = cstr(p);
                let mut st: libc::stat = std::mem::zeroed();
                libc::fstatat(fd, c.as_ptr(), &mut st, libc::AT_SYMLINK_NOFOLLOW) == 0
            }
        }
    }
}

/// Port of `_isdir` (stat, follows symlinks).
fn isdir(dir_fd: Option<i32>, p: &[u8]) -> bool {
    match dir_fd {
        None => std::fs::metadata(std::ffi::OsStr::from_bytes(p))
            .map(|m| m.is_dir())
            .unwrap_or(false),
        Some(fd) => {
            // SAFETY: fd is a caller-owned open directory fd; c is a valid
            // NUL-terminated C string; st is fully initialized by fstatat on
            // success (the != 0 result bails before the stat is read).
            unsafe {
                let c = cstr(p);
                let mut st: libc::stat = std::mem::zeroed();
                if libc::fstatat(fd, c.as_ptr(), &mut st, 0) != 0 {
                    return false;
                }
                (st.st_mode & libc::S_IFMT) == libc::S_IFDIR
            }
        }
    }
}

/// Follow-symlink directory check on an open directory fd + entry name
/// (mirrors CPython `scandir(fd)` + `entry.is_dir()`; ELOOP -> false).
fn fd_entry_is_dir(dir_fd: i32, name: &[u8], d_type: u8) -> bool {
    // SAFETY: dir_fd is an open directory fd owned by the caller for the
    // duration of the call; name is a valid C string (NUL-truncated above);
    // st buffers are fully initialized by fstatat on success.
    unsafe {
        let c = cstr(name);
        match d_type {
            libc::DT_DIR => true,
            libc::DT_LNK => {
                let mut st: libc::stat = std::mem::zeroed();
                libc::fstatat(dir_fd, c.as_ptr(), &mut st, 0) == 0
                    && (st.st_mode & libc::S_IFMT) == libc::S_IFDIR
            }
            _ => {
                let mut st: libc::stat = std::mem::zeroed();
                if libc::fstatat(dir_fd, c.as_ptr(), &mut st, libc::AT_SYMLINK_NOFOLLOW) != 0 {
                    return false;
                }
                let mode = st.st_mode & libc::S_IFMT;
                if mode == libc::S_IFDIR {
                    true
                } else if mode == libc::S_IFLNK {
                    let mut st2: libc::stat = std::mem::zeroed();
                    libc::fstatat(dir_fd, c.as_ptr(), &mut st2, 0) == 0
                        && (st2.st_mode & libc::S_IFMT) == libc::S_IFDIR
                } else {
                    false
                }
            }
        }
    }
}

/// Port of `_iterdir`/`_listdir`:
///
/// * dir_fd + non-empty dirname: `openat(dir_fd, dirname, O_RDONLY|O_DIRECTORY)`,
///   scan that fd, close it.
/// * dir_fd + empty dirname: scan dir_fd directly.
/// * no dir_fd: scandir(dirname or ".").
///
/// Errors: open failure -> `[]`; per-entry `is_dir()` failure (ELOOP) ->
/// entry skipped; mid-iteration read error -> stop (CPython's outer
/// `except OSError: return`).
fn listdir(dir_fd: Option<i32>, dirname: &[u8], dironly: bool) -> Vec<Vec<u8>> {
    match dir_fd {
        Some(fd) if !dirname.is_empty() => {
            // SAFETY: fd is a caller-owned open directory fd; cstr is valid.
            let fd2 = unsafe {
                libc::openat(
                    fd,
                    cstr(dirname).as_ptr(),
                    libc::O_RDONLY | libc::O_DIRECTORY,
                    0,
                )
            };
            if fd2 < 0 {
                return Vec::new();
            }
            scan_fd(fd2, dironly) // scan_fd closes fd2
        }
        Some(fd) => {
            // CPython _iterdir: os.scandir(dir_fd) scans the caller's fd
            // directly. Reproduce the observable contract: the caller's fd
            // is never closed, and every scan is a fresh full scan.
            // Use openat(fd, ".") to obtain an independent directory fd
            // with its own file offset — avoids the dup()+lseek TOCTOU where
            // dup shares the file offset and lseek races between threads.
            // SAFETY: fd is a valid open directory fd for the call duration.
            let d = unsafe {
                libc::openat(
                    fd,
                    cstr(b".").as_ptr(),
                    libc::O_RDONLY | libc::O_DIRECTORY,
                    0,
                )
            };
            if d < 0 {
                // Fallback: dup() if openat fails (e.g., fd not directory);
                // lseek rewinds the shared offset to preserve fresh-scan
                // semantics for the rescan regression (k06).
                // SAFETY: dup(fd) duplicates a valid open fd (checked by the
                // caller); a failed dup (< 0) is checked before d2 is used;
                // lseek runs only on our own fresh d2.
                let d2 = unsafe { libc::dup(fd) };
                if d2 < 0 {
                    return Vec::new();
                }
                unsafe {
                    libc::lseek(d2, 0, libc::SEEK_SET);
                }
                return scan_fd(d2, dironly);
            }
            scan_fd(d, dironly) // scan_fd closes d
        }
        None => {
            let path = if dirname.is_empty() {
                b".".as_ref()
            } else {
                dirname
            };
            let mut out: Vec<Vec<u8>> = Vec::new();
            let rd = match std::fs::read_dir(std::ffi::OsStr::from_bytes(path)) {
                Ok(rd) => rd,
                Err(_) => return out,
            };
            for e in rd {
                match e {
                    Ok(en) => {
                        if dironly && !std_entry_is_dir(&en) {
                            continue;
                        }
                        out.push(en.file_name().as_bytes().to_vec());
                    }
                    // CPython: OSError in the scandir iterator -> outer
                    // `except OSError: return` — keep what we have, stop.
                    Err(_) => return out,
                }
            }
            out
        }
    }
}

/// Follow-symlink directory check for a `std::fs::DirEntry` (d_type first;
/// lstat fallback is internal to `file_type()` only when d_type is unknown —
/// the same rule CPython's `DirEntry` implements).
fn std_entry_is_dir(en: &std::fs::DirEntry) -> bool {
    match en.file_type() {
        Ok(ft) => ft.is_dir() || (ft.is_symlink() && en.path().is_dir()),
        Err(_) => false,
    }
}

/// Scan a directory fd that the CALLER no longer needs: scan_fd CLOSES `fd`
/// (glibc's fdopendir does NOT duplicate the fd, so closedir() closes it —
/// VERIFIED on this machine with /tmp/fdtest.c in the APPLY session).
/// `.` and `..` are NOT yielded (Python scandir hides them; readdir does not).
fn scan_fd(fd: i32, dironly: bool) -> Vec<Vec<u8>> {
    let mut out: Vec<Vec<u8>> = Vec::new();
    // SAFETY: fd is a valid open directory fd owned by this call;
    // fdopendir(glibc) adopts it without duplicating, so closedir closes it
    // — fd must NOT be closed again here; every readdir() pointer is valid
    // until the next readdir; d_name is NUL-terminated.
    unsafe {
        let dp = libc::fdopendir(fd);
        if dp.is_null() {
            libc::close(fd);
            return out;
        }
        loop {
            let ent = libc::readdir(dp);
            if ent.is_null() {
                break;
            }
            let name = std::ffi::CStr::from_ptr((*ent).d_name.as_ptr());
            let nb: Vec<u8> = name.to_bytes().to_vec();
            if nb == b"." || nb == b".." {
                continue;
            }
            if dironly && !fd_entry_is_dir(fd, &nb, (*ent).d_type) {
                continue;
            }
            out.push(nb);
        }
        libc::closedir(dp); // closes fd
    }
    out
}

// ---------------------------------------------------------------------------
// The algorithm (verbatim structure of glob.py)
// ---------------------------------------------------------------------------

/// Port of `_iglob`.
fn _iglob(
    pattern: &[u8],
    root: &[u8],
    dir_fd: Option<i32>,
    opts: Opts,
    dironly: bool,
) -> Vec<Vec<u8>> {
    let mut out: Vec<Vec<u8>> = Vec::new();
    let (dirname, basename) = p_split(pattern);

    if !matcher::has_magic(pattern) {
        // CPython: `assert not dironly`
        if !basename.is_empty() {
            if lexists(dir_fd, &fj(root, pattern)) {
                out.push(pattern.to_vec());
            }
        } else {
            // Patterns ending with a slash should match only directories.
            if isdir(dir_fd, &fj(root, dirname)) {
                out.push(pattern.to_vec());
            }
        }
        return out;
    }

    if dirname.is_empty() {
        if opts.recursive && is_recursive(basename) {
            out.extend(_glob2(root, basename, dir_fd, dironly, opts));
        } else {
            out.extend(_glob1(root, basename, dir_fd, dironly, opts, None));
        }
        return out;
    }

    let dirs = if dirname != pattern && matcher::has_magic(dirname) {
        _iglob(dirname, root, dir_fd, opts, true)
    } else {
        vec![dirname.to_vec()]
    };

    // The compiled matcher for a magic basename is built once per _iglob
    // level (CPython's fnmatch lru_cache is global; per-level is cheaper
    // enough and semantically identical).
    let prog: Option<matcher::Program> =
        if matcher::has_magic(basename) && !(opts.recursive && is_recursive(basename)) {
            Some(matcher::compile(basename))
        } else {
            None
        };

    for d in &dirs {
        let dir_path = fj(root, d);
        let names = if matcher::has_magic(basename) {
            if opts.recursive && is_recursive(basename) {
                _glob2(&dir_path, basename, dir_fd, dironly, opts)
            } else {
                _glob1(&dir_path, basename, dir_fd, dironly, opts, prog.as_ref())
            }
        } else {
            _glob0(&dir_path, basename, dir_fd)
        };
        for name in names {
            out.push(p_join(d, &name));
        }
    }
    out
}

/// Port of `_glob1` (magic basename inside a literal directory).
fn _glob1(
    dirname: &[u8],
    pattern: &[u8],
    dir_fd: Option<i32>,
    dironly: bool,
    opts: Opts,
    prog: Option<&matcher::Program>,
) -> Vec<Vec<u8>> {
    let mut names = listdir(dir_fd, dirname, dironly);
    if opts.include_hidden || !is_hidden(pattern) {
        names.retain(|x| opts.include_hidden || !is_hidden(x));
    }
    let p = prog.cloned().unwrap_or_else(|| matcher::compile(pattern));
    names.retain(|x| matcher::matches(&p, x));
    names
}

/// Port of `_glob0` (literal basename: existence only, no matching).
fn _glob0(dirname: &[u8], basename: &[u8], dir_fd: Option<i32>) -> Vec<Vec<u8>> {
    let mut out: Vec<Vec<u8>> = Vec::new();
    if !basename.is_empty() {
        if lexists(dir_fd, &p_join(dirname, basename)) {
            out.push(basename.to_vec());
        }
    } else {
        // 'q*x/' should match only directories.
        if isdir(dir_fd, dirname) {
            out.push(Vec::new());
        }
    }
    out
}

/// Port of `_glob2` (`**` handling; asserts `_isrecursive(pattern)`).
fn _glob2(
    dirname: &[u8],
    pattern: &[u8],
    dir_fd: Option<i32>,
    dironly: bool,
    opts: Opts,
) -> Vec<Vec<u8>> {
    debug_assert!(is_recursive(pattern));
    let mut out: Vec<Vec<u8>> = Vec::new();
    if dirname.is_empty() || isdir(dir_fd, dirname) {
        out.push(Vec::new());
    }
    out.extend(rlistdir(dir_fd, dirname, dironly, opts));
    out
}

/// Port of `_rlistdir`: recursive, non-hidden (unless include_hidden),
/// dironly-filtered listing. Recursion depth is bounded by the kernel: a
/// symlink cycle makes the child `scandir` fail with ELOOP, ending the
/// branch exactly as in CPython.
fn rlistdir(dir_fd: Option<i32>, dirname: &[u8], dironly: bool, opts: Opts) -> Vec<Vec<u8>> {
    let mut out: Vec<Vec<u8>> = Vec::new();
    let names = listdir(dir_fd, dirname, dironly);
    for x in names {
        if opts.include_hidden || !is_hidden(&x) {
            out.push(x.clone());
            let path = if dirname.is_empty() {
                x.clone()
            } else {
                p_join(dirname, &x)
            };
            for y in rlistdir(dir_fd, &path, dironly, opts) {
                out.push(p_join(&x, &y));
            }
        }
    }
    out
}

// ---------------------------------------------------------------------------
// Fused fast paths — single-pass traversal for the hot pattern families
// ---------------------------------------------------------------------------

/// Fused fast-path dispatcher. Returns `Some(results)` when `pattern` has an
/// authorized shape and the fused walk fully executed it; `None` means "run
/// the verbatim port" (every shape not listed below falls back).
///
/// Accepted shapes (byte-identical result MULTISETS to the legacy pipeline):
///
/// * (a) no `**`, exactly one component, containing magic (`*`, `*.dat`):
///   one listing of the root with `_glob1` filter semantics; `*`
///   short-circuits without matcher decode.
/// * (b) exactly one `**` component plus ONE trailing segment, magic or
///   literal (`**/*.py`, `/abs/dir/**/*.py`): one fused depth-first walk —
///   each visited directory is listed ONCE; entries are suffix-tested
///   (`_glob1` hidden rule) while visible subdirs are pushed for recursion
///   (`_rlistdir` hidden rule). Eliminates the legacy second full scan of
///   every directory yielded by `_glob2`.
/// * (c) exactly one `**` component plus TWO literal segments
///   (`**/b/m.py`): the same fused walk; at each visited directory the
///   joined suffix path is `_lexists`-checked (`_glob0` semantics — no
///   hidden filtering on literal components).
///
/// Fallbacks (`None`): any `dir_fd`; patterns containing `//`; zero or more
/// than one `**` components; `**` as the whole pattern; non-recursive
/// patterns containing `**`; magic in a two-segment suffix;
/// trailing-slash / empty-segment shapes. A LEADING `/` is accepted: the
/// raw prefix slice carries it byte-exact through `fj`/`p_join`/emission,
/// exactly like the legacy pipeline's dirs elements.
///
/// Inputs:
///   pattern: `&[u8]` — glob pattern bytes (length guards already applied)
///   root: `&[u8]` — filesystem origin shift (empty = cwd)
///   dir_fd: `Option<i32>` — always `None` here (fd shapes fall back)
///   opts: `Opts` — recursive/include_hidden flags
/// Output: `Option<Vec<Vec<u8>>>` — full result list (duplicates preserved,
/// order unspecified) or `None` to request the legacy pipeline
/// Errors: never panics; filesystem errors prune exactly like the legacy
/// port (open failure -> branch pruned; mid-iteration error -> stop and
/// keep the partial listing)
fn try_fast(pattern: &[u8], root: &[u8], dir_fd: Option<i32>, opts: Opts) -> Option<Vec<Vec<u8>>> {
    if dir_fd.is_some() {
        return None;
    }
    let mut prev_slash = false;
    for &b in pattern {
        if b == b'/' && prev_slash {
            return None; // "//" anywhere: posix-split quirks, legacy path
        }
        prev_slash = b == b'/';
    }
    // Component spans (start, end); the last component may be empty when the
    // pattern ends with '/', which the shape checks below reject.
    let mut spans: Vec<(usize, usize)> = Vec::new();
    let mut s = 0usize;
    for (i, &b) in pattern.iter().enumerate() {
        if b == b'/' {
            spans.push((s, i));
            s = i + 1;
        }
    }
    spans.push((s, pattern.len()));
    let is_ss = |sp: (usize, usize)| -> bool { &pattern[sp.0..sp.1] == b"**" };
    let ss_count = spans.iter().copied().filter(|&sp| is_ss(sp)).count();
    if ss_count == 0 {
        // Case (a): a single component carrying magic.
        if spans.len() != 1 || !matcher::has_magic(&pattern[spans[0].0..spans[0].1]) {
            return None;
        }
        return Some(fast_single_component(root, pattern, opts));
    }
    if !opts.recursive || ss_count > 1 || spans.len() < 2 {
        return None; // '**' alone, degraded '**', or multiple '**'
    }
    let k = spans.iter().position(|&sp| is_ss(sp))?;
    // Raw prefix slice up to the '**' span start: "" or e.g. "src/" — this
    // IS the legacy dirs-element string (p_join(prefix_component, "")).
    let prefix = &pattern[..spans[k].0];
    let rest = &spans[k + 1..];
    match rest.len() {
        1 => {
            let sp = rest[0];
            if sp.0 == sp.1 {
                return None; // trailing slash: directory-only semantics
            }
            let suffix = &pattern[sp.0..sp.1];
            if suffix == b"." || suffix == b".." {
                // Self/parent lookups are _glob0 lexists territory (no
                // readdir entry ever bears these names).
                return None;
            }
            Some(fast_walk_suffix(prefix, suffix, root, opts))
        }
        2 => {
            let sp1 = rest[0];
            let sp2 = rest[1];
            if sp1.0 == sp1.1 || sp2.0 == sp2.1 {
                return None;
            }
            let seg1 = &pattern[sp1.0..sp1.1];
            let seg2 = &pattern[sp2.0..sp2.1];
            if matcher::has_magic(seg1) || matcher::has_magic(seg2) {
                return None; // magic beyond the single '**': legacy path
            }
            if seg1 == b"." || seg1 == b".." {
                // Name-scanning cannot see "."/".." entries (readdir hides
                // them); the legacy two-stage pipeline resolves them via
                // path lookup, so fall back.
                return None;
            }
            Some(fast_walk_literal2(prefix, seg1, seg2, root, opts))
        }
        _ => None,
    }
}

/// Filesystem scan origin: `fj(root, rel)` mapped to "." when both are empty
/// (the legacy `listdir` mapping of an empty dirname).
fn fast_scan_path(root: &[u8], rel: &[u8]) -> Vec<u8> {
    let j = fj(root, rel);
    if j.is_empty() {
        b".".to_vec()
    } else {
        j
    }
}

/// Case (a): `_glob1(root, pat)` in one listing. Hidden rule per `_glob1`:
/// dot-names dropped iff `!include_hidden` and the pattern is not itself
/// dot-prefixed. `*` matches without matcher decode.
///
/// Input: `root: &[u8]`, `pat: &[u8]` (single magic component), `opts`
/// Output: `Vec<Vec<u8>>` — matching names (bare, as p_join("", name))
/// Errors: open failure -> empty; mid-read error -> partial listing
fn fast_single_component(root: &[u8], pat: &[u8], opts: Opts) -> Vec<Vec<u8>> {
    let mut out: Vec<Vec<u8>> = Vec::new();
    let scan_at = fast_scan_path(root, b"");
    let rd = match std::fs::read_dir(std::ffi::OsStr::from_bytes(&scan_at)) {
        Ok(rd) => rd,
        Err(_) => return out,
    };
    let star = pat == b"*";
    let prog = if star {
        None
    } else {
        Some(matcher::compile(pat))
    };
    let skip_hidden = !opts.include_hidden && !is_hidden(pat);
    for ent in rd {
        let en = match ent {
            Ok(en) => en,
            Err(_) => break, // CPython outer `except OSError: return`
        };
        let nb = en.file_name();
        let b = nb.as_bytes();
        if skip_hidden && is_hidden(b) {
            continue;
        }
        let hit = match &prog {
            None => true,
            Some(p) => matcher::matches(p, b),
        };
        if hit {
            out.push(b.to_vec());
        }
    }
    out
}

/// Case (b)/(c) shared fd-DFS machinery. Dir-ness is proven BY the same
/// syscall that produces the listing (`openat(O_RDONLY|O_DIRECTORY)`,
/// following symlinks — exactly the observable of the legacy
/// is_dir()-follow + scandir pair, including ELOOP pruning of cycles and
/// EACCES/ENOENT pruning of unreadable/vanished branches). Names are
/// borrowed from readdir (zero allocation until a decision allocates);
/// getdents64 batches mean most iterations touch no syscall at all.
/// File descriptors are held ONLY along the active DFS chain (one per
/// level); past [`FAST_MAX_FD_DEPTH`] levels the subtree falls back to the
/// path-based walker (identical semantics, slower) so EMFILE can never
/// silently prune a deep-but-legitimate tree.
/// Depth beyond which fused walks switch a subtree to the path-based
/// fallback walker (bounds simultaneously-held directory fds).
const FAST_MAX_FD_DEPTH: usize = 256;

/// Upper bound on worker threads fanning out the fused walks. Held
/// directory fds are bounded by `FAST_MAX_THREADS * FAST_MAX_FD_DEPTH`
/// chains, far under any realistic RLIMIT_NOFILE.
const FAST_MAX_THREADS: usize = 12;

/// Minimum leaf count the frontier collector aims for before handing the
/// walk to the thread pool (>= 4x the thread cap keeps chunks balanced).
const FAST_FANOUT_WIDTH: usize = 64;

/// Depth cap for frontier collection. Beyond this, directories are handed
/// to workers unexpanded: their own `open` then hits the kernel ELOOP /
/// path limits and prunes exactly like the legacy recursion did.
const FAST_MAX_COLLECT_DEPTH: usize = 40;

/// Plausibly-descendable dirent type: DT_DIR is certain; DT_LNK may resolve
/// to a directory (openat follows it); DT_UNKNOWN forces the probe. Every
/// other type (reg/fifo/sock/chr/blk) can never be a directory — skipping
/// the probe for them is observable-equivalent to the legacy mode check
/// (`fd_entry_is_dir`'s `_ => false` arm) without the syscall.
fn dt_may_be_dir(d_type: u8) -> bool {
    matches!(d_type, libc::DT_DIR | libc::DT_LNK | libc::DT_UNKNOWN)
}

/// Grow a balanced frontier of subtree locations from the seed directory.
///
/// Breadth-first expansion (one listing per expanded directory — the same
/// single-listing discipline as the walk itself) until [`FAST_FANOUT_WIDTH`]
/// DIRECTORIES have been confirmed-and-expanded or the collection depth cap
/// is reached. An `openat(O_DIRECTORY)` success is what confirms dir-ness:
/// once the width budget is spent, further confirmed directories are closed
/// UNREAD and become distribution leaves (the workers re-open them), so the
/// bulk of any wide tree is walked in parallel instead of serially here.
/// Every entry of every EXPANDED directory goes to `visit` for match-side
/// handling (emissions carry full pattern-space rel strings); entries of
/// leaf directories are matched inside the workers — each entry is seen
/// exactly once overall. Non-directories fail the probe and are discarded
/// (the legacy inline-descent probe behaved identically). Hidden-descent
/// gating is delegated to `descend`.
///
/// Inputs:
///   root / seed_rel: origin mapping as in `fast_open_root`
///   visit: `(dir_rel, entry_name)` callback for match-side filtering
///   descend: entry-name -> whether the descent side may recurse into it
/// Output: `Vec<Vec<u8>>` — leaf rel strings ready for distribution
/// Errors: never panics; open failures prune branches (legacy parity)
fn collect_frontier(
    root: &[u8],
    seed_rel: &[u8],
    visit: &mut dyn FnMut(&[u8], &[u8]),
    descend: &dyn Fn(&[u8]) -> bool,
) -> Vec<Vec<u8>> {
    use std::collections::VecDeque;
    let mut pending: VecDeque<(Vec<u8>, usize)> = VecDeque::new();
    pending.push_back((seed_rel.to_vec(), 0));
    let mut leaves: Vec<Vec<u8>> = Vec::new();
    let mut confirmed_dirs: usize = 0;
    while let Some((rel, depth)) = pending.pop_front() {
        if depth >= FAST_MAX_COLLECT_DEPTH {
            // Unexpanded by depth cap: its own open would hit ELOOP-style
            // limits anyway; the worker's failed open prunes identically.
            leaves.push(rel);
            continue;
        }
        let (fd, _) = match fast_open_root(root, &rel) {
            Some(x) => x,
            None => continue, // not a directory / unreachable: pruned
        };
        confirmed_dirs += 1;
        if confirmed_dirs > FAST_FANOUT_WIDTH {
            // Width budget spent: hand this confirmed directory over
            // UNREAD (close now; the worker reopens and walks it).
            // SAFETY: fd is our own open directory fd.
            unsafe {
                libc::close(fd);
            }
            leaves.push(rel);
            continue;
        }
        // SAFETY: fd is our owned open directory fd adopted by fdopendir.
        let dp = unsafe { libc::fdopendir(fd) };
        if dp.is_null() {
            // SAFETY: fdopendir failed without adopting fd.
            unsafe {
                libc::close(fd);
            }
            continue;
        }
        loop {
            // SAFETY: live DIR stream; d_name copied before further use.
            let ent = unsafe { libc::readdir(dp) };
            if ent.is_null() {
                break;
            }
            // SAFETY: valid dirent from readdir; NUL-terminated d_name.
            let nb = unsafe { std::ffi::CStr::from_ptr((*ent).d_name.as_ptr()) }.to_bytes();
            if nb == b"." || nb == b".." {
                continue;
            }
            visit(&rel, nb);
            // SAFETY: ent is a valid dirent; d_type read before next readdir.
            let d_type = unsafe { (*ent).d_type };
            if dt_may_be_dir(d_type) && descend(nb) {
                pending.push_back((p_join(&rel, nb), depth + 1));
            }
        }
        // SAFETY: dp came from fdopendir; closedir closes the adopted fd.
        unsafe {
            libc::closedir(dp);
        }
    }
    leaves
}

/// Distribute leaf subtrees across scoped threads (sequential when there
/// is nothing to gain). Each worker opens its assigned rel strings by path
/// (`fast_open_root`) and runs `walk_one` on private accumulators; results
/// merge by concatenation (multiset-preserving; ordering is
/// documented-unspecified). A panic in any worker propagates (never
/// swallowed: losing a subtree would silently break multiplicity).
///
/// Inputs:
///   root: origin for `fast_open_root`
///   leaves: pattern-space rel strings of unvisited subtrees
///   walk_one: `(leaf_rel, deep_out, hits_out)` fused subtree walker
/// Output: `(deep_paths, out_hits)` accumulated across all workers
/// Errors: never panics itself; worker panics resume unwinding
fn fan_out_walks(leaves: Vec<Vec<u8>>, walk_one: &WalkOneFn<'_>) -> (Vec<Vec<u8>>, Vec<Vec<u8>>) {
    let mut deep_all: Vec<Vec<u8>> = Vec::new();
    let mut out_all: Vec<Vec<u8>> = Vec::new();
    if leaves.is_empty() {
        return (deep_all, out_all);
    }
    let threads = leaves
        .len()
        .min(std::thread::available_parallelism().map_or(1, |n| n.get()))
        .min(FAST_MAX_THREADS);
    if threads <= 1 {
        for rel in &leaves {
            walk_one(rel, &mut deep_all, &mut out_all);
        }
        return (deep_all, out_all);
    }
    let chunk = leaves.len().div_ceil(threads);
    std::thread::scope(|scope| {
        let mut handles = Vec::new();
        for part in leaves.chunks(chunk) {
            handles.push(scope.spawn(move || {
                let mut deep_t: Vec<Vec<u8>> = Vec::new();
                let mut out_t: Vec<Vec<u8>> = Vec::new();
                for rel in part {
                    walk_one(rel, &mut deep_t, &mut out_t);
                }
                (deep_t, out_t)
            }));
        }
        for h in handles {
            match h.join() {
                Ok((d, o)) => {
                    deep_all.extend(d);
                    out_all.extend(o);
                }
                Err(e) => std::panic::resume_unwind(e),
            }
        }
    });
    (deep_all, out_all)
}

/// Open `name` under `dir_fd` as a directory, following symlinks.
///
/// Input: `dir_fd: i32` — open directory fd; `name: &[u8]` — entry name
/// Output: `Option<i32>` — `Some(new_fd)` (ownership transfers to caller,
/// close via closedir) or `None` when the entry is not an openable
/// directory (ENOTDIR/ENOENT/EACCES/ELOOP/EMFILE — prune, legacy parity)
/// Errors: never panics
fn open_subdir(dir_fd: i32, name: &[u8]) -> Option<i32> {
    // SAFETY: dir_fd is an open directory fd owned by the caller for the
    // duration of the call; c is a valid NUL-terminated C string; a failed
    // openat (< 0) is checked before any use.
    let fd = unsafe {
        libc::openat(
            dir_fd,
            cstr(name).as_ptr(),
            libc::O_RDONLY | libc::O_DIRECTORY,
            0,
        )
    };
    if fd < 0 {
        None
    } else {
        Some(fd)
    }
}

/// Open the scan origin for a fused walk seed: `fj(root, prefix)` mapped to
/// "." when both are empty (the legacy `listdir` empty-dirname mapping).
///
/// Input: `root`/`prefix` bytes
/// Output: `Option<(i32, Vec<u8>)>` — `(fd, prefix_bytes)`; `None` when the
/// origin cannot be opened as a directory (legacy `_glob2` location gate +
/// `rlistdir` open-failure prune produce the same empty result set)
/// Errors: never panics
fn fast_open_root(root: &[u8], prefix: &[u8]) -> Option<(i32, Vec<u8>)> {
    let at = fast_scan_path(root, prefix);
    // SAFETY: open of a caller-supplied path; negative result checked.
    let fd = unsafe { libc::open(cstr(&at).as_ptr(), libc::O_RDONLY | libc::O_DIRECTORY, 0) };
    if fd < 0 {
        None
    } else {
        Some((fd, prefix.to_vec()))
    }
}

/// Fused subtree-walker callback shape: `(leaf_rel, deep_out, hits_out)` —
/// the per-leaf entry point `fan_out_walks` runs on its workers.
type WalkOneFn<'a> = dyn Fn(&[u8], &mut Vec<Vec<u8>>, &mut Vec<Vec<u8>>) + Sync + 'a;

/// Deferred path-based work for subtrees beyond the fd-depth cap: the exact
/// pre-cap walker semantics, driven by relative path strings.
enum DeepTask {
    /// Case (b): suffix-test every entry under the subtree locations.
    Suffix {
        stack: Vec<Vec<u8>>,
        prog: Option<matcher::Program>,
        star: bool,
        skip_hidden_matches: bool,
        descend_hidden: bool,
    },
    /// Case (c): `_lexists` the joined two-segment literal per location.
    Literal2 {
        stack: Vec<Vec<u8>>,
        seg1: Vec<u8>,
        seg2: Vec<u8>,
        descend_hidden: bool,
    },
}

impl DeepTask {
    fn drain(&mut self, root: &[u8], out: &mut Vec<Vec<u8>>) {
        loop {
            let rel = match self.stack_ref_mut().pop() {
                Some(r) => r,
                None => return,
            };
            let scan_at = fast_scan_path(root, &rel);
            let rd = match std::fs::read_dir(std::ffi::OsStr::from_bytes(&scan_at)) {
                Ok(rd) => rd,
                Err(_) => continue, // open failure prunes the branch
            };
            for ent in rd {
                let en = match ent {
                    Ok(en) => en,
                    Err(_) => break, // keep partial listing (CPython parity)
                };
                let nb = en.file_name();
                let b = nb.as_bytes();
                let dotted = is_hidden(b);
                match self {
                    DeepTask::Suffix {
                        prog,
                        star,
                        skip_hidden_matches,
                        ..
                    } => {
                        if !*skip_hidden_matches || !dotted {
                            let hit = if *star {
                                true
                            } else {
                                match prog {
                                    Some(p) => matcher::matches(p, b),
                                    None => true,
                                }
                            };
                            if hit {
                                out.push(p_join(&rel, b));
                            }
                        }
                    }
                    DeepTask::Literal2 { seg1, seg2, .. } => {
                        // Same composition + `_glob0` lexists parity as the
                        // fd worker above (order differs; multiset equal).
                        let cand = p_join(&p_join(&rel, seg1), seg2);
                        if lexists(None, &fj(root, &cand)) {
                            out.push(cand);
                        }
                    }
                }
                let descend_hidden = self.descend_hidden();
                if descend_hidden || !dotted {
                    let is_dir = match en.file_type() {
                        Ok(ft) => ft.is_dir() || (ft.is_symlink() && en.path().is_dir()),
                        Err(_) => false,
                    };
                    if is_dir {
                        self.stack_push(p_join(&rel, b));
                    }
                }
            }
        }
    }

    fn stack_ref_mut(&mut self) -> &mut Vec<Vec<u8>> {
        match self {
            DeepTask::Suffix { stack, .. } | DeepTask::Literal2 { stack, .. } => stack,
        }
    }

    fn stack_push(&mut self, p: Vec<u8>) {
        self.stack_ref_mut().push(p);
    }

    fn descend_hidden(&self) -> bool {
        match self {
            DeepTask::Suffix { descend_hidden, .. } | DeepTask::Literal2 { descend_hidden, .. } => {
                *descend_hidden
            }
        }
    }
}

/// Case (b): one fused fd-DFS for `PREFIX**/SUFFIX` (SUFFIX single segment,
/// magic or literal). Every visible entry is suffix-tested with the
/// `_glob1` hidden rule (`*` short-circuits without matcher decode); a
/// successful `openat(O_DIRECTORY)` both proves dir-ness and yields the
/// child stream, so descent costs no separate stat. Hidden rules and
/// error pruning are byte-for-byte the legacy observable contract.
///
/// Input: `prefix`/`suffix`/`root` bytes, `opts`
/// Output: `Vec<Vec<u8>>` — p_join(dir_location, name) hits
/// Errors: prune/partial semantics identical to the legacy port
fn fast_walk_suffix(prefix: &[u8], suffix: &[u8], root: &[u8], opts: Opts) -> Vec<Vec<u8>> {
    let mut out: Vec<Vec<u8>> = Vec::new();
    let star = suffix == b"*";
    let prog = if star {
        None
    } else {
        Some(matcher::compile(suffix))
    };
    let skip_hidden_matches = !opts.include_hidden && !is_hidden(suffix);
    let descend_hidden = opts.include_hidden;
    // Frontier collection: match side runs for every expanded directory's
    // entries here (full pattern-space strings); leaf subtrees are matched
    // inside the workers — each entry is seen exactly once overall.
    let mut leaves = collect_frontier(
        root,
        prefix,
        &mut |dir_rel, nb| {
            let dotted = is_hidden(nb);
            if !skip_hidden_matches || !dotted {
                let hit = if star {
                    true
                } else {
                    match prog.as_ref() {
                        Some(p) => matcher::matches(p, nb),
                        None => true,
                    }
                };
                if hit {
                    out.push(p_join(dir_rel, nb));
                }
            }
        },
        &|nb| descend_hidden || !is_hidden(nb),
    );
    let (deep, hits) = fan_out_walks(std::mem::take(&mut leaves), &|rel, d, o| {
        if let Some((cfd, _)) = fast_open_root(root, rel) {
            walk_fd_suffix(
                cfd,
                rel,
                0,
                star,
                prog.as_ref(),
                skip_hidden_matches,
                descend_hidden,
                d,
                o,
            );
        }
    });
    out.extend(hits);
    if !deep.is_empty() {
        let mut task = DeepTask::Suffix {
            stack: deep,
            prog,
            star,
            skip_hidden_matches,
            descend_hidden,
        };
        task.drain(root, &mut out);
    }
    out
}

/// Recursive fd-chain worker for case (b). Holds one directory fd (its own)
/// per active level; children are opened from the STREAM's fd, so the
/// kernel reuses the parent's dentry instead of re-walking the path.
/// SAFETY (callers): `fd` is an owned open directory fd transferred into
/// this call and always closed here (via closedir, or manually when
/// fdopendir fails); `deep`/`out` are append-only accumulators.
#[allow(clippy::too_many_arguments)]
fn walk_fd_suffix(
    fd: i32,
    rel: &[u8],
    depth: usize,
    star: bool,
    prog: Option<&matcher::Program>,
    skip_hidden_matches: bool,
    descend_hidden: bool,
    deep: &mut Vec<Vec<u8>>,
    out: &mut Vec<Vec<u8>>,
) {
    // SAFETY: fd is a valid open directory fd owned by this call until
    // closedir; glibc fdopendir adopts it without duplicating (see scan_fd).
    let dp = unsafe { libc::fdopendir(fd) };
    if dp.is_null() {
        // SAFETY: fdopendir failed without adopting fd; close it ourselves.
        unsafe {
            libc::close(fd);
        }
        return;
    }
    loop {
        // SAFETY: dp is a live DIR stream from fdopendir; each d_name pointer
        // is valid until the next readdir call and copied before recursion.
        let ent = unsafe { libc::readdir(dp) };
        if ent.is_null() {
            break; // EOF (error treated as end: scan_fd parity)
        }
        // SAFETY: valid dirent from readdir; d_name is NUL-terminated.
        let nb = unsafe { std::ffi::CStr::from_ptr((*ent).d_name.as_ptr()) }.to_bytes();
        if nb == b"." || nb == b".." {
            continue;
        }
        let dotted = is_hidden(nb);
        // Match side (`_glob1` rule).
        if !skip_hidden_matches || !dotted {
            let hit = if star {
                true
            } else {
                match prog {
                    Some(p) => matcher::matches(p, nb),
                    None => true,
                }
            };
            if hit {
                out.push(p_join(rel, nb));
            }
        }
        // Descent side (`_rlistdir` rule, dironly): the openat IS the
        // is_dir probe (symlink-following; failures prune the branch) —
        // but only for dirent types that can possibly be directories.
        if descend_hidden || !dotted {
            // SAFETY: ent is a valid dirent; d_type read before next readdir.
            let d_type = unsafe { (*ent).d_type };
            if dt_may_be_dir(d_type) {
                if depth < FAST_MAX_FD_DEPTH {
                    if let Some(cfd) = open_subdir(fd, nb) {
                        let crel = p_join(rel, nb);
                        walk_fd_suffix(
                            cfd,
                            &crel,
                            depth + 1,
                            star,
                            prog,
                            skip_hidden_matches,
                            descend_hidden,
                            deep,
                            out,
                        );
                    }
                } else {
                    deep.push(p_join(rel, nb));
                }
            }
        }
    }
    // SAFETY: dp came from fdopendir; closedir closes the adopted fd.
    unsafe {
        libc::closedir(dp);
    }
}

/// Case (c): one fused fd-DFS for `PREFIX**/SEG1/SEG2` (both literal). At
/// each visited directory, SEG1 is located BY NAME within the listing
/// itself (free — no per-directory lstat); when present and openable as a
/// directory, SEG2's existence is probed with one `fstatat(AT_SYMLINK_NOFOLLOW)`
/// (`_glob0` lexists parity: broken symlinks count, literals get no hidden
/// filtering). The emitted string is the same byte composition the legacy
/// two-stage pipeline produces. Degenerate SEG1 ("" / "." / "..") is
/// rejected upstream (try_fast) because name-scanning cannot reproduce
/// parent/self lookups.
///
/// Input: `prefix`, `seg1`, `seg2`, `root` bytes, `opts`
/// Output: `Vec<Vec<u8>>` — existing candidates under visited directories
/// Errors: prune/partial semantics identical to the legacy port
fn fast_walk_literal2(
    prefix: &[u8],
    seg1: &[u8],
    seg2: &[u8],
    root: &[u8],
    opts: Opts,
) -> Vec<Vec<u8>> {
    let mut out: Vec<Vec<u8>> = Vec::new();
    let descend_hidden = opts.include_hidden;
    // Frontier collection; the match-side visit handles the SEG1 presence
    // scan + `_glob0` candidate emission for every EXPANDED directory
    // (literals: no hidden filtering, broken symlinks count).
    let mut leaves = collect_frontier(
        root,
        prefix,
        &mut |dir_rel, nb| {
            if nb == seg1 {
                if let Some((sfd, _)) = fast_open_root(root, &p_join(dir_rel, seg1)) {
                    // SAFETY: sfd is our open directory fd; st fully
                    // initialized by fstatat on success (== 0 checked).
                    let mut st: libc::stat = unsafe { std::mem::zeroed() };
                    // SAFETY: valid C string for SEG2.
                    let hit = unsafe {
                        libc::fstatat(sfd, cstr(seg2).as_ptr(), &mut st, libc::AT_SYMLINK_NOFOLLOW)
                            == 0
                    };
                    if hit {
                        out.push(p_join(&p_join(dir_rel, seg1), seg2));
                    }
                    // SAFETY: sfd is our own fd (open_subdir transfer).
                    unsafe {
                        libc::close(sfd);
                    }
                }
            }
        },
        &|nb| descend_hidden || !is_hidden(nb),
    );
    let (deep, hits) = fan_out_walks(std::mem::take(&mut leaves), &|rel, d, o| {
        if let Some((cfd, _)) = fast_open_root(root, rel) {
            walk_fd_literal2(cfd, rel, 0, seg1, seg2, descend_hidden, d, o);
        }
    });
    out.extend(hits);
    if !deep.is_empty() {
        let mut task = DeepTask::Literal2 {
            stack: deep,
            seg1: seg1.to_vec(),
            seg2: seg2.to_vec(),
            descend_hidden,
        };
        task.drain(root, &mut out);
    }
    out
}

/// Recursive fd-chain worker for case (c); see `walk_fd_suffix` for the
/// fd/discipline contract.
#[allow(clippy::too_many_arguments)]
fn walk_fd_literal2(
    fd: i32,
    rel: &[u8],
    depth: usize,
    seg1: &[u8],
    seg2: &[u8],
    descend_hidden: bool,
    deep: &mut Vec<Vec<u8>>,
    out: &mut Vec<Vec<u8>>,
) {
    // SAFETY: fd is a valid open directory fd owned by this call until
    // closedir; glibc fdopendir adopts it without duplicating (see scan_fd).
    let dp = unsafe { libc::fdopendir(fd) };
    if dp.is_null() {
        // SAFETY: fdopendir failed without adopting fd; close it ourselves.
        unsafe {
            libc::close(fd);
        }
        return;
    }
    let mut seg1_fd: Option<i32> = None;
    loop {
        // SAFETY: dp is a live DIR stream from fdopendir; each d_name pointer
        // is valid until the next readdir call and copied before recursion.
        let ent = unsafe { libc::readdir(dp) };
        if ent.is_null() {
            break;
        }
        // SAFETY: valid dirent from readdir; d_name is NUL-terminated.
        let nb = unsafe { std::ffi::CStr::from_ptr((*ent).d_name.as_ptr()) }.to_bytes();
        if nb == b"." || nb == b".." {
            continue;
        }
        if seg1_fd.is_none() && nb == seg1 {
            // SEG1 exists here (any type); only directory-or-symlink-to-dir
            // forms can contain SEG2, and openat proves that for free.
            seg1_fd = open_subdir(fd, nb);
        }
        let dotted = is_hidden(nb);
        if descend_hidden || !dotted {
            // SAFETY: ent is a valid dirent; d_type read before next readdir.
            let d_type = unsafe { (*ent).d_type };
            if dt_may_be_dir(d_type) {
                if depth < FAST_MAX_FD_DEPTH {
                    if let Some(cfd) = open_subdir(fd, nb) {
                        let crel = p_join(rel, nb);
                        walk_fd_literal2(
                            cfd,
                            &crel,
                            depth + 1,
                            seg1,
                            seg2,
                            descend_hidden,
                            deep,
                            out,
                        );
                    }
                } else {
                    deep.push(p_join(rel, nb));
                }
            }
        }
    }
    if let Some(sfd) = seg1_fd {
        // _glob0 parity: lstat (NOFOLLOW) — broken symlinks count as existing.
        // SAFETY: sfd is our open directory fd; st is fully initialized by
        // fstatat on success (the == 0 result is checked before any field
        // would be read).
        let mut st: libc::stat = unsafe { std::mem::zeroed() };
        // SAFETY: c is a valid NUL-terminated C string for the SEG2 name.
        let hit = unsafe {
            libc::fstatat(sfd, cstr(seg2).as_ptr(), &mut st, libc::AT_SYMLINK_NOFOLLOW) == 0
        };
        if hit {
            out.push(p_join(&p_join(rel, seg1), seg2));
        }
        // SAFETY: sfd is our own open fd (open_subdir transfer).
        unsafe {
            libc::close(sfd);
        }
    }
    // SAFETY: dp came from fdopendir; closedir closes the adopted fd.
    unsafe {
        libc::closedir(dp);
    }
}

// ---------------------------------------------------------------------------
// Unit tests — walker semantics (cycle, hidden, broken symlink, **
// multiplicity, byte-exactness), mirroring the fixture-tree behaviors
// recorded in tests/oracle/capture.json.
// ---------------------------------------------------------------------------

#[cfg(test)]
// Justification for the allow (BANNED: `#[allow]` requires a reason):
// test fixtures use unwrap() as the panic vehicle — a failed setup
// (fs::create_dir / symlink) IS the test failure; production code in
// this file carries no unwrap/expect at all.
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use std::collections::HashMap;
    use std::ffi::OsString;
    use std::fs;
    use std::os::unix::ffi::OsStrExt;
    use std::os::unix::fs::symlink;

    fn tmpdir(tag: &str) -> std::path::PathBuf {
        let p = std::env::temp_dir().join(format!("fastglob_test_{}_{}", tag, std::process::id()));
        let _ = fs::remove_dir_all(&p);
        fs::create_dir_all(&p).unwrap();
        p
    }

    fn root(d: &std::path::Path) -> Vec<u8> {
        d.as_os_str().as_bytes().to_vec()
    }

    fn write(p: &std::path::Path, content: &str) {
        fs::write(p, content).unwrap();
    }

    fn multiset(r: &[Vec<u8>]) -> HashMap<Vec<u8>, usize> {
        let mut m: HashMap<Vec<u8>, usize> = HashMap::new();
        for x in r {
            *m.entry(x.clone()).or_insert(0) += 1;
        }
        m
    }

    // Glob with `d` as the filesystem origin (root_dir). Results are
    // pattern-built relative strings, isolating the tmpdir from process cwd.
    fn gb(d: &std::path::Path, p: &[u8], o: Opts) -> Vec<Vec<u8>> {
        glob(p, &root(d), None, o)
    }

    fn no() -> Opts {
        Opts {
            recursive: false,
            include_hidden: false,
        }
    }
    fn rec() -> Opts {
        Opts {
            recursive: true,
            include_hidden: false,
        }
    }
    fn hid() -> Opts {
        Opts {
            recursive: false,
            include_hidden: true,
        }
    }

    #[test]
    fn basic_and_literal() {
        let d = tmpdir("basic");
        write(&d.join("a.txt"), "");
        write(&d.join("b.txt"), "");
        fs::create_dir(d.join("sub")).unwrap();
        let o = no();
        let r = gb(&d, b"*.txt", o);
        assert_eq!(
            multiset(&r),
            multiset(&[b"a.txt".to_vec(), b"b.txt".to_vec()])
        );
        assert!(gb(&d, b"*.nope", o).is_empty());
        assert_eq!(gb(&d, b"a.txt", o), vec![b"a.txt".to_vec()]);
        assert!(gb(&d, b"zzz", o).is_empty());
        assert_eq!(gb(&d, b"sub/", o), vec![b"sub/".to_vec()]);
        assert!(gb(&d, b"a.txt/", o).is_empty());
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn dir_fd_rescan_is_fresh() {
        // k06 regression: CPython's os.scandir(dir_fd) gives a FRESH full
        // scan on every call even when the same caller fd is scanned
        // repeatedly (directory stream position is shared via dup()).
        use std::collections::HashMap;
        use std::os::unix::ffi::OsStrExt;
        let d = tmpdir("rescan");
        fs::create_dir(d.join("c")).unwrap();
        write(&d.join("c/b"), "");
        fs::create_dir_all(d.join("d/e")).unwrap();
        write(&d.join("d/e/b"), "");
        write(&d.join("b"), "");
        // SAFETY: plain open of our own tmpdir.
        let fd = unsafe {
            libc::open(
                cstr(d.as_os_str().as_bytes()).as_ptr(),
                libc::O_RDONLY | libc::O_DIRECTORY,
            )
        };
        assert!(fd >= 0);
        let o = Opts {
            recursive: true,
            include_hidden: false,
        };
        let r1 = glob(b"**/*", b"", Some(fd), o);
        let r2 = glob(b"**/*", b"", Some(fd), o);
        assert_eq!(r1, r2, "rescan of the same dir_fd must be identical");
        let mut c1: HashMap<Vec<u8>, i64> = HashMap::new();
        for x in &r1 {
            *c1.entry(x.clone()).or_insert(0) += 1;
        }
        let mut want: HashMap<Vec<u8>, i64> = HashMap::new();
        for x in [
            b"c".to_vec(),
            b"d".to_vec(),
            b"b".to_vec(),
            b"c/b".to_vec(),
            b"d/e".to_vec(),
            b"d/e/b".to_vec(),
        ] {
            *want.entry(x).or_insert(0) += 1;
        }
        assert_eq!(c1, want);
        // caller's fd must still be open afterwards
        let mut st = std::mem::MaybeUninit::<libc::stat>::uninit();
        // SAFETY: fstat on our own fd.
        let rs = unsafe { libc::fstat(fd, st.as_mut_ptr()) };
        assert_eq!(rs, 0, "caller's dir_fd must not be closed by glob");
        // SAFETY: close our own fd.
        unsafe {
            libc::close(fd);
        }
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn hidden_rules() {
        let d = tmpdir("hidden");
        write(&d.join("visible"), "");
        write(&d.join(".hidden"), "");
        fs::create_dir_all(d.join(".hidden_dir")).unwrap();
        write(&d.join(".hidden_dir/x"), "");
        fs::create_dir(d.join("vdir")).unwrap();
        let o = no();
        let r = gb(&d, b"*", o);
        assert_eq!(
            multiset(&r),
            multiset(&[b"visible".to_vec(), b"vdir".to_vec()])
        );
        let r = gb(&d, b".*", o);
        assert_eq!(
            multiset(&r),
            multiset(&[b".hidden".to_vec(), b".hidden_dir".to_vec()])
        );
        let r = gb(&d, b"*", hid());
        assert_eq!(
            multiset(&r),
            multiset(&[
                b"visible".to_vec(),
                b"vdir".to_vec(),
                b".hidden".to_vec(),
                b".hidden_dir".to_vec()
            ])
        );
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn broken_symlink_returned() {
        let d = tmpdir("broken");
        symlink(d.join("no_such_target"), d.join("broken")).unwrap();
        write(&d.join("real"), "");
        let o = no();
        assert_eq!(gb(&d, b"broken", o), vec![b"broken".to_vec()]);
        let r = gb(&d, b"br*", o);
        assert_eq!(r, vec![b"broken".to_vec()]);
        let r = gb(&d, b"**", rec());
        assert!(r.contains(&b"broken".to_vec()));
        assert!(!r.iter().any(|x| x.starts_with(b"broken/")));
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn symlink_cycle_terminates() {
        let d = tmpdir("cycle");
        fs::create_dir(d.join("cycle")).unwrap();
        symlink(d.join("cycle"), d.join("cycle/self")).unwrap();
        let o = rec();
        let t0 = std::time::Instant::now();
        let r = gb(&d, b"cycle/**", o);
        let el = t0.elapsed();
        assert!(el < std::time::Duration::from_secs(5), "took {el:?}");
        assert!(r.iter().any(|x| x.ends_with(b"self")));
        let n_self = r.iter().filter(|x| x.ends_with(b"self")).count();
        assert!((2..=45).contains(&n_self), "self count {n_self}");
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn recursive_starstar_semantics() {
        let d = tmpdir("rec");
        fs::create_dir_all(d.join("a/c")).unwrap();
        fs::create_dir_all(d.join("a/d/e")).unwrap();
        write(&d.join("a/b"), "");
        write(&d.join("a/c/b"), "");
        write(&d.join("a/d/e/b"), "");
        let r = gb(&d, b"a/**", rec());
        assert_eq!(
            multiset(&r),
            multiset(&[
                b"a/".to_vec(),
                b"a/c".to_vec(),
                b"a/c/b".to_vec(),
                b"a/d".to_vec(),
                b"a/d/e".to_vec(),
                b"a/d/e/b".to_vec(),
                b"a/b".to_vec()
            ])
        );
        let r = gb(&d, b"a/**/b", rec());
        assert_eq!(
            multiset(&r),
            multiset(&[b"a/b".to_vec(), b"a/c/b".to_vec(), b"a/d/e/b".to_vec()])
        );
        // without recursive, '**' is just a fnmatch pattern (matches all)
        let r = gb(&d, b"a/**", no());
        assert_eq!(
            multiset(&r),
            multiset(&[b"a/b".to_vec(), b"a/c".to_vec(), b"a/d".to_vec()])
        );
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn starstar_multiplicity() {
        // A double ** visits 'a/c' through two expansion chains, so paths
        // under it must appear twice (multiplicity is contract, not accident).
        let d = tmpdir("mult");
        fs::create_dir(d.join("a")).unwrap();
        write(&d.join("a/b"), "");
        fs::create_dir_all(d.join("a/c")).unwrap();
        write(&d.join("a/c/b"), "");
        let r = gb(&d, b"a/**/**/b", rec());
        let counts = multiset(&r);
        assert_eq!(*counts.get(b"a/b".as_slice()).unwrap_or(&0), 1);
        assert_eq!(*counts.get(b"a/c/b".as_slice()).unwrap_or(&0), 2);
        let r = gb(&d, b"a/**/b", rec());
        let counts = multiset(&r);
        assert_eq!(*counts.get(b"a/b".as_slice()).unwrap_or(&0), 1);
        assert_eq!(*counts.get(b"a/c/b".as_slice()).unwrap_or(&0), 1);
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn byte_exact_names() {
        let d = tmpdir("bytes");
        write(&d.join("new\nline.txt"), "");
        let raw: OsString = std::ffi::OsStr::from_bytes(b"raw_\xff.txt").to_os_string();
        write(&d.join(&raw), "");
        let o = no();
        let r = gb(&d, b"*", o);
        assert!(r.contains(&b"new\nline.txt".to_vec()));
        assert!(r.contains(&b"raw_\xff.txt".to_vec()));
        let r = gb(&d, b"raw_?.txt", o);
        assert_eq!(r, vec![b"raw_\xff.txt".to_vec()]);
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn path_quirks() {
        let d = tmpdir("quirks");
        fs::create_dir_all(d.join("l/sub")).unwrap();
        write(&d.join("l/sub/t.txt"), "");
        let o = no();
        assert_eq!(gb(&d, b"l//sub//t.txt", o), vec![b"l//sub//t.txt".to_vec()]);
        assert_eq!(gb(&d, b"l//", o), vec![b"l//".to_vec()]);
        assert_eq!(p_split(b"a/"), (b"a".as_ref(), b"".as_ref()));
        assert_eq!(p_split(b"/"), (b"/".as_ref(), b"".as_ref()));
        assert_eq!(p_split(b"//"), (b"//".as_ref(), b"".as_ref()));
        assert_eq!(p_split(b"///"), (b"///".as_ref(), b"".as_ref()));
        assert_eq!(p_split(b"/a"), (b"/".as_ref(), b"a".as_ref()));
        assert_eq!(p_split(b"a/b"), (b"a".as_ref(), b"b".as_ref()));
        assert_eq!(p_split(b"a//b"), (b"a".as_ref(), b"b".as_ref()));
        assert_eq!(p_join(b"a", b"").as_slice(), b"a/");
        assert_eq!(p_join(b"", b"a").as_slice(), b"a");
        assert_eq!(p_join(b"a/", b"b").as_slice(), b"a/b");
        assert_eq!(p_join(b"a", b"/x").as_slice(), b"/x");
        assert_eq!(fj(b"", b"p").as_slice(), b"p");
        assert_eq!(fj(b"R", b"").as_slice(), b"R");
        assert_eq!(fj(b"R", b"p").as_slice(), b"R/p");
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn root_dir_and_dir_fd() {
        let d = tmpdir("rootfd");
        fs::create_dir(d.join("basic")).unwrap();
        write(&d.join("basic/x.txt"), "");
        let o = no();
        // root_dir shifts the fs origin; results stay pattern-built
        let r = gb(&d, b"basic/*.txt", o);
        assert_eq!(r, vec![b"basic/x.txt".to_vec()]);
        // dir_fd: open the tmpdir and scan relative to that fd
        // SAFETY: plain open of our own tmpdir (as in dir_fd_rescan_is_fresh).
        let fd = unsafe {
            libc::open(
                cstr(d.as_os_str().as_bytes()).as_ptr(),
                libc::O_RDONLY | libc::O_DIRECTORY,
            )
        };
        assert!(fd >= 0);
        let r = glob(b"*.txt", b"", Some(fd), o);
        assert!(r.is_empty()); // no *.txt directly in d
        let r = glob(b"basic/*.txt", b"", Some(fd), o);
        assert_eq!(r, vec![b"basic/x.txt".to_vec()]);
        // SAFETY: close our own fd.
        unsafe {
            libc::close(fd);
        }
        let _ = fs::remove_dir_all(&d);
    }
}
