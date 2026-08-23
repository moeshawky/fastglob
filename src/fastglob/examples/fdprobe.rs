// Justification for the allow (BANNED: `#[allow]` requires a reason):
// development probe (not a production target) — main() uses unwrap() on its
// own argv as the panic vehicle for misuse, per the binary-entry-point
// convention; production code in the lib/bin targets carries no unwrap.
#![allow(clippy::unwrap_used, clippy::expect_used)]
// missing_docs: probe has no public API to document beyond main().
#![allow(missing_docs)]

use fastglob::walk::{glob, Opts};
fn main() {
    let d = std::env::args().nth(1).unwrap();
    let o = Opts {
        recursive: true,
        include_hidden: false,
    };
    // SAFETY: plain open of a caller-supplied directory (probe tool); d is a
    // valid C string (CString::new panics on an interior NUL first).
    let fd = unsafe {
        let c = std::ffi::CString::new(d).unwrap();
        libc::open(c.as_ptr(), libc::O_RDONLY | libc::O_DIRECTORY)
    };
    println!("fd={fd}");
    let r = glob(b"**/*", b"", Some(fd), o);
    println!("**/* -> {r:?}");
    // still valid?
    // SAFETY: zeroed() for libc::stat — a C struct with no invariants the
    // zero value violates (same pattern as the production code in walk.rs);
    // fstat runs on the fd opened above and fully initializes st on success
    // (the probe prints only the return code).
    let mut st: libc::stat = unsafe { std::mem::zeroed() };
    let rc = unsafe { libc::fstat(fd, &mut st) };
    println!("fstat after: {rc}");
}
