//! fnmatch-exact pattern matching — a faithful port of the `translate()`
//! semantics of the installed `/usr/local/lib/python3.12/fnmatch.py` (Level A
//! source of truth), executed as a deterministic program instead of a regex.
//!
//! The translated regex has a fixed shape that this module exploits directly:
//!
//! ```text
//! (?s: PREFIX (?>.*?F1) (?>.*?F2) ... .*F? )\Z
//! ```
//!
//! where each `F` is a fixed-length element sequence (no stars inside).
//! Semantics (verified differentially vs `fnmatch.fnmatchcase`, 400k random
//! + targeted edge table, `out/dev/diff_match.py`):
//!
//!   * PREFIX must match the name start, element-for-element.
//!   * Each interior `(?>.*?F)` commits to the EARLIEST position where `F`
//!     matches (atomic: no re-entry from outside).
//!   * A trailing `*` always matches; a trailing `.*F` matches only if `F`
//!     matches EXACTLY at the end of the name (single position, `\Z`).
//!
//! Character model: names and patterns are byte strings (OsStr). Each valid
//! UTF-8 code point is one "character"; each invalid byte `b` is one
//! character `0xDC00|b` — exactly the surrogateescape mapping Python applies,
//! so `?`/class membership agree with the oracle on arbitrary bytes.
//!
//! Class parsing mirrors `re._parser` (L555-640 of the installed
//! `_parser.py`): `-` immediately after the just-read element forms a range
//! (end `]` gives literal X + literal `-`); `]` closes only a non-empty
//! set; escapes are single-literal.

// fmt import removed — Debug derived on Program

// ---------------------------------------------------------------------------
// Character model
// ---------------------------------------------------------------------------

/// Decode a byte string into "characters": UTF-8 code points, with each
/// invalid byte mapped to `0xDC00 | b` (Python surrogateescape parity).
///
/// Input: `bytes: &[u8]` — arbitrary byte string (may contain invalid UTF-8)
/// Output: `Vec<u32>` — decoded code points, one per character (valid UTF-8 -> code point, invalid -> 0xDC00|b)
/// Errors: never fails
pub fn decode_chars(bytes: &[u8]) -> Vec<u32> {
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        let b = bytes[i];
        if b < 0x80 {
            out.push(u32::from(b));
            i += 1;
            continue;
        }
        match utf8_char_at(bytes, i) {
            Some((cp, len)) => {
                out.push(cp);
                i += len;
            }
            None => {
                out.push(0xDC00u32 | u32::from(b));
                i += 1;
            }
        }
    }
    out
}

/// Strict well-formed UTF-8 sequence at `i` (no overlong, no surrogates).
fn utf8_char_at(bytes: &[u8], i: usize) -> Option<(u32, usize)> {
    let b = bytes[i];
    if (0xC2..=0xDF).contains(&b) {
        let b2 = *bytes.get(i + 1)?;
        if (0x80..=0xBF).contains(&b2) {
            return Some((((u32::from(b) & 0x1F) << 6) | (u32::from(b2) & 0x3F), 2));
        }
    } else if (0xE0..=0xEF).contains(&b) {
        let b2 = *bytes.get(i + 1)?;
        let b3 = *bytes.get(i + 2)?;
        if (0x80..=0xBF).contains(&b2) && (0x80..=0xBF).contains(&b3) {
            if b == 0xE0 && b2 < 0xA0 {
                return None;
            }
            if b == 0xED && b2 > 0x9F {
                return None;
            }
            let cp = ((u32::from(b) & 0x0F) << 12)
                | ((u32::from(b2) & 0x3F) << 6)
                | (u32::from(b3) & 0x3F);
            return Some((cp, 3));
        }
    } else if (0xF0..=0xF4).contains(&b) {
        let b2 = *bytes.get(i + 1)?;
        let b3 = *bytes.get(i + 2)?;
        let b4 = *bytes.get(i + 3)?;
        if (0x80..=0xBF).contains(&b2) && (0x80..=0xBF).contains(&b3) && (0x80..=0xBF).contains(&b4)
        {
            let cp = ((u32::from(b) & 0x07) << 18)
                | ((u32::from(b2) & 0x3F) << 12)
                | ((u32::from(b3) & 0x3F) << 6)
                | (u32::from(b4) & 0x3F);
            if (0x1_0000..=0x10_FFFF).contains(&cp) {
                return Some((cp, 4));
            }
        }
    }
    None
}

// ---------------------------------------------------------------------------
// Program IR
// ---------------------------------------------------------------------------

const STAR: u32 = b'*' as u32;
const QM: u32 = b'?' as u32;
const LBR: u32 = b'[' as u32;
const RBR: u32 = b']' as u32;
const BANG: u32 = b'!' as u32;
const MINUS: u32 = b'-' as u32;
const BSL: u32 = b'\\' as u32;
const CARET: u32 = b'^' as u32;
const AMP: u32 = b'&' as u32;
const TILDE: u32 = b'~' as u32;
const PIPE: u32 = b'|' as u32;

/// A character-set item in a compiled character class.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CItem {
    /// Single literal code point.
    C(u32),
    /// Inclusive code-point range (re semantics).
    Range(u32, u32),
}

/// One compiled element of a pattern (fnmatch `translate()` vocabulary).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Elem {
    /// Literal code point.
    Lit(u32),
    /// Any single character (`?`).
    Any,
    /// Empty class contents: `(?!)` — never matches.
    Never,
    /// Character class `[...]` (possibly negated).
    Class {
        /// Negated class (`[!...]`).
        neg: bool,
        /// Ordered set items (literals and inclusive ranges).
        items: Vec<CItem>,
    },
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum Tok {
    Star,
    Elem(Elem),
}

impl Tok {
    fn elem(&self) -> Elem {
        match self {
            Tok::Star => panic!("Tok::Star has no element: called elem() on Star token (internal invariant violated)"),
            Tok::Elem(e) => e.clone(),
        }
    }
}

/// Trailing anchor shape of the translated regex.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Tail {
    /// No star in the pattern.
    None,
    /// Pattern ends with `*`: always matches.
    Star,
    /// Pattern ends `*...F`: `F` must match exactly at the end (`\Z`).
    Fixed(Vec<Elem>),
}

/// A compiled pattern: literal prefix + atomic `*`-separated fixed blocks + tail anchor.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Program {
    /// Literal elements that must match the name start.
    pub prefix: Vec<Elem>,
    /// Fixed element blocks separated by `*` (earliest-match, atomic).
    pub blocks: Vec<Vec<Elem>>,
    /// End anchor (none / trailing star / end-fixed suffix).
    pub tail: Tail,
}

// ---------------------------------------------------------------------------
// translate() port
// ---------------------------------------------------------------------------

/// Compile a pattern (fnmatch-3.12 translate semantics).
///
/// Input: `pat: &[u8]` — pattern bytes (may contain `*`, `?`, `[` with Python semantics)
/// Output: `Program` — deterministic matcher IR (prefix, blocks, tail)
/// Errors: never fails (all patterns compile, even unclosed classes -> literal `[`)
pub fn compile(pat: &[u8]) -> Program {
    let chars = decode_chars(pat);
    let mut toks: Vec<Tok> = Vec::new();
    let n = chars.len();
    let mut i = 0;
    while i < n {
        let c = chars[i];
        i += 1;
        match c {
            STAR => {
                // translate compresses consecutive '*' into one STAR
                if !matches!(toks.last(), Some(Tok::Star)) {
                    toks.push(Tok::Star);
                }
            }
            QM => toks.push(Tok::Elem(Elem::Any)),
            LBR => {
                let (e, next) = translate_class(&chars, i - 1);
                i = next;
                toks.push(Tok::Elem(e));
            }
            _ => toks.push(Tok::Elem(Elem::Lit(c))),
        }
    }
    group_stars(toks)
}

fn group_stars(toks: Vec<Tok>) -> Program {
    let mut prefix: Vec<Elem> = Vec::new();
    let mut blocks: Vec<Vec<Elem>> = Vec::new();
    let mut tail = Tail::None;
    let n = toks.len();
    let mut i = 0;
    // Fixed pieces at the start.
    while i < n && !matches!(toks[i], Tok::Star) {
        prefix.push(toks[i].elem());
        i += 1;
    }
    while i < n {
        debug_assert!(matches!(toks[i], Tok::Star));
        i += 1;
        if i == n {
            tail = Tail::Star;
            break;
        }
        let mut fixed: Vec<Elem> = Vec::new();
        while i < n && !matches!(toks[i], Tok::Star) {
            fixed.push(toks[i].elem());
            i += 1;
        }
        if i == n {
            tail = Tail::Fixed(fixed);
        } else {
            blocks.push(fixed);
        }
    }
    Program {
        prefix,
        blocks,
        tail,
    }
}

/// Port of the `c == '['` branch of fnmatch.translate. `i0` indexes `'['`.
/// Returns the element and the index to continue scanning from.
fn translate_class(pat: &[u32], i0: usize) -> (Elem, usize) {
    let n = pat.len();
    let mut j = i0 + 1;
    if j < n && pat[j] == BANG {
        j += 1;
    }
    if j < n && pat[j] == RBR {
        j += 1;
    }
    let mut k = j;
    while k < n && pat[k] != RBR {
        k += 1;
    }
    if k >= n {
        // Unterminated: literal '[', continue right after it.
        return (Elem::Lit(LBR), i0 + 1);
    }
    let cstart = i0 + 1; // content start (pat[cstart..k])
    let content: Vec<u32> = pat[cstart..k].to_vec();
    let has_minus = content.contains(&MINUS);
    let mut stuff: Vec<u32> = Vec::new();
    if !has_minus {
        for c in &content {
            if *c == BSL {
                stuff.push(BSL);
                stuff.push(BSL);
            } else {
                stuff.push(*c);
            }
        }
    } else {
        let mut chunks: Vec<Vec<u32>> = Vec::new();
        let first = pat[cstart];
        let mut k2 = if first == BANG {
            cstart + 2
        } else {
            cstart + 1
        };
        let mut i_cur = cstart;
        loop {
            let pos = (k2..k).find(|&idx| pat[idx] == MINUS);
            match pos {
                Some(pos) => {
                    chunks.push(pat[i_cur..pos].to_vec());
                    i_cur = pos + 1;
                    k2 = pos + 3;
                }
                None => break,
            }
        }
        let chunk: Vec<u32> = pat[i_cur..k].to_vec();
        if !chunk.is_empty() {
            chunks.push(chunk);
        } else {
            // translate: chunks[-1] += '-'
            // Invariant (proven): this arm is reachable only when the loop
            // above found a '-' in the content — the first such find always
            // pushes a chunk — so `chunks` is non-empty here.
            let last = match chunks.last_mut() {
                Some(last) => last,
                None => unreachable!("a '-' in class content guarantees >= 1 chunk"),
            };
            last.push(MINUS);
        }
        // Remove empty ranges — invalid in RE (translate's exact loop).
        let mut m = chunks.len();
        while m > 1 {
            let a = m - 1;
            let prev_last = chunks[a - 1][chunks[a - 1].len() - 1];
            let cur_first = chunks[a][0];
            if prev_last > cur_first {
                let mut merged: Vec<u32> = chunks[a - 1].clone();
                merged.pop();
                merged.extend_from_slice(&chunks[a][1..]);
                chunks[a - 1] = merged;
                chunks.remove(a);
            }
            m -= 1;
        }
        // Rejoin: escape backslashes and hyphens per chunk, join with bare '-'.
        for (idx, ch) in chunks.iter().enumerate() {
            if idx > 0 {
                stuff.push(MINUS);
            }
            for c in ch {
                match *c {
                    BSL => {
                        stuff.push(BSL);
                        stuff.push(BSL);
                    }
                    MINUS => {
                        stuff.push(BSL);
                        stuff.push(MINUS);
                    }
                    _ => stuff.push(*c),
                }
            }
        }
    }
    // Escape set operations (& ~ |) — re.sub(r'([&~|])', r'\\\1', stuff).
    {
        let mut s3: Vec<u32> = Vec::with_capacity(stuff.len() + 8);
        for c in &stuff {
            if *c == AMP || *c == TILDE || *c == PIPE {
                s3.push(BSL);
            }
            s3.push(*c);
        }
        stuff = s3;
    }
    if stuff.is_empty() {
        // Empty range: never matches — translate adds '(?)'.
        return (Elem::Never, k + 1);
    }
    if stuff.len() == 1 && stuff[0] == BANG {
        // Negated empty range: match any character.
        return (Elem::Any, k + 1);
    }
    let neg = stuff[0] == BANG;
    let mut body: Vec<u32> = if neg {
        stuff[1..].to_vec()
    } else {
        stuff.clone()
    };
    if !neg && !body.is_empty() && (body[0] == CARET || body[0] == LBR) {
        let mut b2: Vec<u32> = Vec::with_capacity(body.len() + 1);
        b2.push(BSL);
        b2.extend_from_slice(&body);
        body = b2;
    }
    let items = parse_class_body(&body);
    (Elem::Class { neg, items }, k + 1)
}

/// Parse a class body exactly like `re._parser` (installed `_parser.py`
/// L555-640): `-` right after the just-read element starts a range; `]`
/// closes only a non-empty set; `X-]` yields literal X and literal '-';
/// escapes yield single literals. Translate pre-processing guarantees
/// ranges are well-formed (lo <= hi) for every string it can produce.
fn parse_class_body(body: &[u32]) -> Vec<CItem> {
    let mut items: Vec<CItem> = Vec::new();
    let n = body.len();
    let mut i = 0;
    while i < n {
        if body[i] == RBR && !items.is_empty() {
            break;
        }
        let lit: u32;
        if body[i] == BSL && i + 1 < n {
            lit = body[i + 1];
            i += 2;
        } else {
            lit = body[i];
            i += 1;
        }
        if i < n && body[i] == MINUS {
            i += 1;
            if i < n && body[i] == RBR {
                items.push(CItem::C(lit));
                items.push(CItem::C(MINUS));
                break;
            }
            if i >= n {
                // re would raise "unterminated"; translate never produces it.
                items.push(CItem::C(lit));
                break;
            }
            let lit2: u32;
            if body[i] == BSL && i + 1 < n {
                lit2 = body[i + 1];
                i += 2;
            } else {
                lit2 = body[i];
                i += 1;
            }
            items.push(CItem::Range(lit, lit2));
        } else {
            items.push(CItem::C(lit));
        }
    }
    items
}

// ---------------------------------------------------------------------------
// Deterministic matcher
// ---------------------------------------------------------------------------

/// Does `name` (bytes) match the compiled `pat`?
///
/// Inputs: `prog: &Program` — compiled pattern; `name: &[u8]` — filename bytes to test
/// Output: `bool` — `true` if `name` matches `prog` per fnmatch-3.12 atomic semantics
/// Errors: never fails
pub fn matches(prog: &Program, name: &[u8]) -> bool {
    let name_chars = decode_chars(name);
    let n = name_chars.len();
    if !seq_ok(&prog.prefix, &name_chars, 0) {
        return false;
    }
    let mut pos = prog.prefix.len();
    if prog.blocks.is_empty() {
        return match &prog.tail {
            Tail::None => pos == n,
            Tail::Star => true,
            Tail::Fixed(f) => tail_fixed_ok(f, &name_chars, n, pos),
        };
    }
    for blk in &prog.blocks {
        let blen = blk.len();
        if n < blen {
            return false;
        }
        let limit = n - blen;
        let mut found = false;
        let start = pos;
        for q in start..=limit {
            if seq_ok(blk, &name_chars, q) {
                pos = q + blen;
                found = true;
                break;
            }
        }
        if !found {
            return false;
        }
    }
    match &prog.tail {
        Tail::Star => true,
        Tail::Fixed(f) => tail_fixed_ok(f, &name_chars, n, pos),
        Tail::None => pos == n,
    }
}

/// Trailing `.*F\Z`: `F` (fixed length) may end only at the very end.
fn tail_fixed_ok(f: &[Elem], name: &[u32], n: usize, pos: usize) -> bool {
    let flen = f.len();
    n >= flen && n - flen >= pos && seq_ok(f, name, n - flen)
}

fn seq_ok(seq: &[Elem], name: &[u32], p: usize) -> bool {
    if p + seq.len() > name.len() {
        return false;
    }
    for (k, e) in seq.iter().enumerate() {
        if !elem_ok(e, name[p + k]) {
            return false;
        }
    }
    true
}

fn elem_ok(e: &Elem, c: u32) -> bool {
    match e {
        Elem::Lit(x) => c == *x,
        Elem::Any => true,
        Elem::Never => false,
        Elem::Class { neg, items } => {
            let m = items.iter().any(|it| match it {
                CItem::C(x) => c == *x,
                CItem::Range(a, b) => *a <= c && c <= *b,
            });
            m != *neg
        }
    }
}

// ---------------------------------------------------------------------------
// glob-level helpers (ports of glob.py)
// ---------------------------------------------------------------------------

/// Port of `glob.has_magic`: regex `([*?[])` search.
///
/// Input: `s: &[u8]` — pattern to scan
/// Output: `bool` — `true` if `s` contains `*`, `?`, or `[`
/// Errors: never fails
pub fn has_magic(s: &[u8]) -> bool {
    s.iter().any(|&b| b == b'*' || b == b'?' || b == b'[')
}

/// Port of `glob.escape`: wrap each `*`, `?`, `[` in `[...]` (posix
/// splitdrive leaves the drive part empty, so the whole name is scanned).
///
/// Input: `pathname: &[u8]` — arbitrary path bytes
/// Output: `Vec<u8>` — escaped pattern where `*`->`[*]`, `?`->`[?]`, `[`->`[[]`
/// Errors: never fails
pub fn escape(pathname: &[u8]) -> Vec<u8> {
    let mut out: Vec<u8> = Vec::with_capacity(pathname.len() + 8);
    for &b in pathname {
        if b == b'*' || b == b'?' || b == b'[' {
            out.push(b'[');
            out.push(b);
            out.push(b']');
        } else {
            out.push(b);
        }
    }
    out
}

// ---------------------------------------------------------------------------
// Unit tests — edge cases verified against the installed fnmatch (3.12.13)
// via out/dev/diff_match.py (400k random + targeted table, 0 mismatches).
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    fn m(name: &str, pat: &str) -> bool {
        let p = compile(pat.as_bytes());
        matches(&p, name.as_bytes())
    }

    #[test]
    fn basic_wildcards() {
        assert!(m("abc", "*"));
        assert!(m("", "*"));
        assert!(m("a", "?"));
        assert!(!m("ab", "?"));
        assert!(m("?", "?"));
        assert!(m("a", "[abc]"));
        assert!(!m("d", "[abc]"));
        assert!(m("m", "[a-z]"));
        assert!(!m("M", "[a-z]"));
        assert!(m("d", "[!abc]"));
        assert!(!m("a", "[!abc]"));
        assert!(m("x1", "?[123]"));
        assert!(m("file.py", "*.py"));
    }

    #[test]
    fn class_edge_cases() {
        // ']' first, 'a', literal — translate j-adjustment
        assert!(m("]", "[]a]"));
        assert!(m("a", "[]a]"));
        assert!(!m("x", "[]a]"));
        // trailing '-' is literal
        assert!(m("a", "[a-]"));
        assert!(m("-", "[a-]"));
        assert!(!m("b", "[a-]"));
        assert!(m("-", "[-a]"));
        assert!(m("a", "[-a]"));
        // translate's descending-range removal quirks
        assert!(m("b", "[a--b]"));
        assert!(!m("a", "[a--b]"));
        assert!(!m("x", "[a--]"));
        // 3.12 quirk: "[!]" is UNCLOSED (the ]-adjustment eats the closer)
        // -> literal "[", "!", "]" — matches only the 3-char name "[!]"
        assert!(m("[!]", "[!]"));
        assert!(!m("x", "[!]"));
        assert!(!m("a", "[!]"));
        assert!(!m("", "[!]"));
        // "[]!]" = class {'!', ']'} — the ]-adjustment puts ']' INSIDE the class.
        // (The `stuff == '!'` "negated empty -> any char" branch is dead code
        // in 3.12.13: the adjustment makes stuff always >= 2 chars. Ported as-is.)
        assert!(m("]", "[]!]"));
        assert!(m("!", "[]!]"));
        assert!(!m("x", "[]!]"));
        assert!(!m("a", "[]!]"));
        // "[!]]" = negated class {']'} -> any char except ']'
        assert!(m("x", "[!]]"));
        assert!(!m("]", "[!]]"));
        // unclosed class = literal '['
        assert!(m("[", "["));
        assert!(m("a[", "a["));
        assert!(!m("abc", "[abc"));
        // escapes
        assert!(m("\\", "\\"));
        assert!(!m("\\", "a"));
        assert!(m("a\\b", "a\\b"));
        assert!(!m("ab", "a\\b"));
        assert!(!m("]", "[\\]]"));
        assert!(!m("\\", "[\\]]"));
        assert!(m("\\]", "[\\]]"));
        assert!(!m("x", "[\\]]"));
        // set-operation escaping
        assert!(m("&", "[&]"));
        assert!(m("|", "[|]"));
        assert!(m("~", "[~]"));
        assert!(m("&", "[a&b]"));
        assert!(m("a", "[a&b]"));
        // escaped hyphen is a literal member
        assert!(!m("-", "[a\\-b]"));
        assert!(m("a", "[a\\-b]"));
        assert!(!m("m", "[a\\-b]"));
        // re._parser range rules: '-' after a range is literal
        assert!(m("b", "[a-c-e]"));
        assert!(m("-", "[a-c-e]"));
        assert!(m("e", "[a-c-e]"));
        assert!(!m("d", "[a-c-e]"));
        // '^' / '[' as first members
        assert!(m("^", "^"));
        assert!(m("^", "[^a]"));
        assert!(m("a", "[^a]"));
        assert!(!m("b", "[^a]"));
        assert!(m("[", "[[]"));
        // '\]' inside content ends the class early (translate scans raw ']')
        assert!(!m("a", "[a\\]b]"));
        assert!(!m("b", "[a\\]b]"));
        assert!(!m("]", "[a\\]b]"));
        assert!(!m("x", "[a\\]b]"));
        assert!(m("\\b]", "[a\\]b]"));
    }

    #[test]
    fn star_semantics() {
        assert!(m("", "*"));
        assert!(m("ab", "**"));
        assert!(!m("", "*?"));
        assert!(m("?", "*?"));
        assert!(m("x", "?*"));
        assert!(m("aXXb", "a**b"));
        // trailing fixed is end-anchored
        assert!(m("aab", "*ab"));
        assert!(m("ab", "*ab"));
        assert!(!m("aba", "*ab"));
        assert!(m("abcd", "a*b*c*d"));
        assert!(!m("acbd", "a*b*c*d"));
        assert!(!m("adbc", "a*b*c*d"));
        // 3.12 ATOMIC interior groups: naive backtracking would differ
        assert!(m("aa", "*a*a"));
        assert!(!m("ba", "*a*a"));
        assert!(!m("b", "*a*a"));
        assert!(!m("a", "[a]*[a]"));
        assert!(m("aa", "[a]*[a]"));
        // escaped metachars are literal
        assert!(!m("?", "*\\?*"));
        assert!(!m("a?a", "*\\?*"));
        assert!(m("a\\?b", "*\\?*"));
        assert!(!m("axa", "*\\?*"));
    }

    #[test]
    fn program_shape() {
        let p = compile(b"*ab");
        assert!(p.prefix.is_empty());
        assert!(p.blocks.is_empty());
        assert_eq!(
            p.tail,
            Tail::Fixed(vec![Elem::Lit(b'a' as u32), Elem::Lit(b'b' as u32)])
        );

        let p = compile(b"a*b*c");
        assert_eq!(p.prefix, vec![Elem::Lit(b'a' as u32)]);
        assert_eq!(p.blocks, vec![vec![Elem::Lit(b'b' as u32)]]);
        assert_eq!(p.tail, Tail::Fixed(vec![Elem::Lit(b'c' as u32)]));

        let p = compile(b"");
        assert_eq!(
            p,
            Program {
                prefix: vec![],
                blocks: vec![],
                tail: Tail::None,
            }
        );
    }

    #[test]
    fn byte_model() {
        // raw 0xFF byte: one character, matched by '?'
        let name = b"a\xffb";
        assert!(matches(&compile(b"a?b"), name));
        assert!(!matches(&compile(b"a?"), name));
        // invalid byte is NOT in an ASCII class
        assert!(!matches(&compile(b"[a-z]"), b"\xff"));
        // valid multi-byte utf-8 is one character
        assert!(matches(&compile(b"?"), "\u{1F680}".as_bytes()));
        assert!(!matches(&compile(b"??"), "\u{1F680}".as_bytes()));
    }

    #[test]
    fn has_magic_and_escape() {
        assert!(has_magic(b"*"));
        assert!(has_magic(b"?"));
        assert!(has_magic(b"["));
        assert!(!has_magic(b"abc"));
        assert!(!has_magic(b""));
        assert_eq!(escape(b"*"), b"[*]");
        assert_eq!(escape(b"a*b?c[d].txt"), b"a[*]b[?]c[[]d].txt".as_ref());
        assert_eq!(escape(b"new\nline.txt"), b"new\nline.txt");
        assert_eq!(escape(b"back\\slash"), b"back\\slash");
        assert_eq!(escape(b"**"), b"[*][*]");
    }
}
