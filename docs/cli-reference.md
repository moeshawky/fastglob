# Fast Glob — CLI Reference

**Source:** `src/fastglob/src/main.rs:12-19` `USAGE`, verified `src/target/release/fastglob --help` 2026-08-22
**Binary:** `src/target/release/fastglob` 385KB, `make build` 0.03s

---

## Synopsis

```bash
fastglob [OPTIONS] PATTERN          # glob pattern expansion
fastglob escape PATTERN             # escape special chars [*?[] -> [*][?][[]
fastglob -h | --help                # usage, exit 0
```

**Source:** `main.rs:12` header comment, `main.rs:32-40` `parse`

---

## Options

| Flag | Effect | Verified |
|------|--------|----------|
| `--recursive` | Enable `**` zero-or-more-dirs (Level A) | `fastglob --recursive -- '**/*.py'` |
| `--include-hidden` | Let `*`/`?`/`**` match dot-prefixed names | `fastglob --include-hidden '*'` includes `.hidden` |
| `--null` | NUL-delimit output (use for arbitrary filenames with newlines) | `fastglob --null '*' \| od -c` shows `\0` |
| `--root-dir PATH` | Shift filesystem origin; results stay pattern-built | `fastglob --root-dir /tmp '*'` |
| `--dir-fd N` | Resolve relative to open directory fd N | `fastglob --dir-fd 3 '*'` (fd 3→dir) |
| `-h`, `--help` | Print usage, exit 0 to stdout | `fastglob --help \| head -7` |
| `--` | End of options, next arg is PATTERN | `fastglob -- --recursive` treats as literal |

**Source:** `main.rs:55-170` `parse`, `main.rs:23-30` `USAGE`

---

## Behavior (Measured)

- **Output:** One pathname per line (default `sep=b"\n"`), or NUL with `--null` (`sep=b"\0"`) — `main.rs:267`. Closed-pipe EPIPE (`| head`) exits 0 silently — documented pipe UX (`main.rs:287-306`). Any OTHER stdout write failure prints `fastglob: stdout write failed: {e}` on stderr and **exits 1** (results were not delivered). **Verified** 2026-08-22: `fastglob '*' > /dev/full` → rc 1 + `No space left on device (os error 28)` on stderr; `fastglob '*' | head -1` → PIPESTATUS 0, stderr empty.
- **Empty:** No matches → exit 0, empty stdout (not exceptional) — **verified:** `fastglob "nope_*.xyz"` → `rc 0` empty
- **Diagnostics:** On `stderr`, misuse → exit 2 with usage — **verified:** `fastglob` (no args) → `fastglob: missing PATTERN` rc 2 stderr
- **NUL:** Bytes `0x00` in PATTERN or `--root-dir` → exit 2 `embedded null byte` (matches Python `ValueError`) — `main.rs:151`
- **Length guard:** `len>8192` or `components>512` → exit 2 `pattern too long` — `main.rs:159`, `walk.rs:44`
- **Dual-flag:** Both `--root-dir` and `--dir-fd` → exit 2 `cannot specify both` — `main.rs:168`

**Verification:**
```bash
./src/target/release/fastglob --help
# Expected: usage: fastglob [OPTIONS] PATTERN ... Options: --recursive ...

./src/target/release/fastglob "nope_*.xyz" --root-dir /tmp; echo exit:$?
# Expected: exit:0 (empty)

./src/target/release/fastglob '*' > /dev/full; echo exit:$?
# Expected: fastglob: stdout write failed: No space left on device (os error 28) — exit:1

./src/target/release/fastglob -- --recursive; echo exit:$?
# Expected: exit:0, empty output — '--' ends options; --recursive is consumed
# as a literal PATTERN operand (GNU convention), NOT parsed as a flag

./src/target/release/fastglob --bogus '*'; echo exit:$?
# Expected: fastglob: unknown option: --bogus (+ usage) — exit:2

python3 -c "print('a/'*34133)" | xargs -0 ./src/target/release/fastglob --
# Expected: fastglob: pattern too long exit 2 (was stack overflow pre-fix)
```

---

## Examples (Executable)

### Basic wildcard
```bash
mkdir -p /tmp/demo && touch /tmp/demo/a.py /tmp/demo/b.py
./src/target/release/fastglob --root-dir /tmp/demo '*.py'
# Expected: a.py\nb.py (order unspecified, compare via sort)
# Verify: ./src/target/release/fastglob --root-dir /tmp/demo '*.py' | sort
```

**Source:** `tests/fixtures/cases.json:b01` (`basic/*` → 15 entries)

### Recursive `**`
```bash
mkdir -p /tmp/demo/a/b && touch /tmp/demo/a/b/file.py /tmp/demo/file.py
./src/target/release/fastglob --recursive --root-dir /tmp/demo '**/*.py'
# Expected: a/b/file.py\na/file.py\nfile.py (Counter equality vs glob.glob('**/*.py', recursive=True))
```

**Source:** `tests/fixtures/cases.json:r01` etc., `docs/compatibility-contract.md:8.4`

### Hidden files
```bash
touch /tmp/demo/.hidden && mkdir -p /tmp/demo/.hidden_dir
./src/target/release/fastglob --root-dir /tmp/demo '*' | sort
# Expected: file without dotfiles

./src/target/release/fastglob --include-hidden --root-dir /tmp/demo '*' | sort
# Expected: includes .hidden .hidden_dir
```

**Source:** `walk.rs:409` `_glob1` pattern-sensitive, `walk.rs:460` `rlistdir` unconditional

### NUL-delimited (arbitrary filenames)
```bash
touch $'/tmp/demo/a\nb.txt'
./src/target/release/fastglob --root-dir /tmp/demo '*' | od -c
# Splits: a\nb.txt → two fake lines

./src/target/release/fastglob --null --root-dir /tmp/demo '*' | od -c
# NUL-delimited: a\nb.txt\0 normal.txt\0 correct

PYTHONPATH=python python3 -c "import fastglob; print(fastglob.glob('*', root_dir='/tmp/demo'))"
# Always uses --null internally: ['a\nb.txt','normal.txt'] correct
```

**Source:** `python/fastglob/__init__.py:91` (`--null`), `main.rs:168` sep

### Escape
```bash
./src/target/release/fastglob escape "a*b"
# Expected: a[*]b

./src/target/release/fastglob escape --null "a*b" | od -c
# Expected: a[*]b\0
```

**Source:** `matcher.rs:557-569` `escape`, verified `fastglob.escape("a*b") == "a[*]b"`

### Root-dir and dir-fd
```bash
./src/target/release/fastglob --root-dir /tmp/demo '*'
# Pattern-built: a.py (not /tmp/demo/a.py)

python3 <<'PY'
import os, fastglob
fd = os.open("/tmp/demo", os.O_RDONLY | os.O_DIRECTORY)
print(fastglob.glob("*", dir_fd=fd))
os.close(fd)
PY
# Expected: same as root_dir
```

**Source:** `walk.rs:220-289` `listdir` `openat`/`scan_fd`, `python/__init__.py:76-106` `F_DUPFD`

---

## Exit Codes

| Code | Meaning | Example |
|------|---------|---------|
| 0 | Success (even with 0 matches), `--help`, or closed-pipe EPIPE (`\| head -1`; silent) | `fastglob --help` |
| 1 | Runtime error — stdout results could not be delivered (diagnostic on stderr names the errno) | `fastglob '*' > /dev/full` → rc 1 |
| 2 | Misuse (missing PATTERN, unknown flag, NUL, too long, dual-flag; bad `--dir-fd`: `not a valid number` / `fd out of range` / `fd is not open or not a directory` / `fd is not a directory`) | `fastglob --unknown` → `unknown option` rc 2 |

**Verification:** `fastglob 2>&1; echo $?` → `2`, `fastglob --help; echo $?` → `0`, `fastglob '*' > /dev/full; echo $?` → `1`

---

## Limits

| Limit | Value | Behavior |
|-------|-------|----------|
| Pattern bytes | 8192 | `fastglob: pattern too long` rc 2 |
| Path components | 512 | same |
| Timeout | 10s per compat case | `SIGALRM` `tests/compat/run_engine.py:63` |
| Ordering | Unspecified | `Counter` equality, duplicates preserved |

---

## Verification (Living Docs)

```bash
./src/target/release/fastglob --help | grep -q "Options:" && echo ok
cargo clippy -- -D warnings && echo clippy ok
python3 tests/compat/compare.py --self-test 2>&1 | tail -1
# Expected: ok, clippy ok, SELF-TEST 100% GREEN
```

**Last Verified:** 2026-08-22 22:22 UTC
