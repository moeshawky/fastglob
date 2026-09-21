#!/usr/bin/env bash
#
# fastglob — deployment driver. Remedy R4 of
# docs/design/shim-seamlessness-2026-08-27.md ("deploy/rollback automation that
# never swaps on a red suite"), whose witness was E11.
#
# WHY A SCRIPT AND NOT A ONE-LINE RECIPE
#   A deploy is not one command. It is: gate the tree → build → snapshot →
#   install two artifacts → verify live → auto-rollback on any failure. The
#   snapshot/restore pair and the failure path have to be readable and testable
#   on their own; inlined in a Makefile recipe they are neither.
#
# WHAT GETS DEPLOYED — two artifacts that must stay in step
#   1. the `fastglob` wheel  → site-packages of every interpreter in $PYTHONS
#   2. the shim (shim/*.py)  → $SHIM_DIR, reached via PYTHONPATH injection
#   The shim is a `glob`-named module on PYTHONPATH: it shadows stdlib `glob`
#   for every process that inherits that variable. Wheel alone = `fastglob` is
#   importable but nothing is accelerated. Shim alone = every `import glob`
#   degrades to the shim's own stdlib fallback. Both go together so the pair is
#   never half-applied.
#
# WHAT IS *NOT* THIS SCRIPT'S JOB
#   The PYTHONPATH injection belongs to the box provisioner ("agent-bin-setup":
#   /etc/environment, /etc/profile.d/99-fastglob.sh, ~/.bashrc,
#   /opt/agent-bin/bash_noninteractive). Those are provisioner-owned and are
#   re-applied on the next provision, so this script never rewrites them.
#   Falling back to stdlib is done by removing the *shadow* — the `glob.py` in
#   $SHIM_DIR. An injection path whose directory has no `glob.py` resolves to
#   stdlib `glob` (measured 2026-09-21: empty and nonexistent directories both
#   yield /usr/lib/python3.12/glob.py). That is why `disable`/`rollback` are
#   single-directory operations and not environment surgery.
#
# SAFETY MODEL
#   `apply` ALWAYS evaluates the gate — `make test`, the full local suite — and
#   refuses to touch anything if it is red. Mutation additionally requires
#   DRY_RUN=0; the default DRY_RUN=1 runs the gate and prints the exact actions
#   it would take, then stops. `plan`, `status` and `verify` never mutate.
#   `apply` snapshots both artifacts before the first mutation and restores
#   that snapshot automatically if any later step fails.
#
# USAGE
#   tools/deploy.sh status                    # read-only drift report (rc 1 on drift)
#   tools/deploy.sh plan                      # dry-run preconditions + exact plan
#   tools/deploy.sh apply                     # gate + plan  (DRY_RUN=1, default)
#   DRY_RUN=0 tools/deploy.sh apply           # gate + real swap + verify
#   DRY_RUN=0 tools/deploy.sh rollback        # restore newest snapshot
#   DRY_RUN=0 tools/deploy.sh rollback FROM=<stamp>   # (also FROM=<stamp> as an env var)
#   DRY_RUN=0 tools/deploy.sh disable         # un-shadow glob → stdlib
#   tools/deploy.sh verify                    # live post-deploy assertions
#
# ENV
#   SHIM_DIR     default /opt/fastglob-shim
#   PYTHONS      default "python3" (space-separated interpreters to install into)
#   SUDO         default "sudo"; set EMPTY when already root (containers) to skip escalation
#                (also what makes the rollback guard testable without root)
#   BACKUP_ROOT  default /var/backups/fastglob
#   DRY_RUN      default 1
#   FROM         snapshot stamp for `rollback` (default: newest)
#   WHEEL        pre-built wheel for `apply` (default: build one)
#
set -euo pipefail

SELF_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SELF_DIR/.." && pwd)

SHIM_DIR=${SHIM_DIR:-/opt/fastglob-shim}
PYTHONS=${PYTHONS:-python3}
SUDO=${SUDO:-sudo}
BACKUP_ROOT=${BACKUP_ROOT:-/var/backups/fastglob}
DRY_RUN=${DRY_RUN:-1}
FROM=${FROM:-}
WHEEL=${WHEEL:-}

SHIM_SRC=$(cd "$REPO_ROOT/shim" 2>/dev/null && pwd || echo "$REPO_ROOT/shim")

# ── output ────────────────────────────────────────────────────────────────
info()  { printf '%s\n' "$*"; }
step()  { printf '\n== %s ==\n' "$*"; }
note()  { printf '   %s\n' "$*"; }
kv()    { printf '   %-44s: %s\n' "$1" "$2"; }
warn()  { printf 'WARN: %s\n' "$*" >&2; }
die()   { printf 'ERROR: %s\n' "$*" >&2; exit "${2:-1}"; }

truthy() { case "${1:-}" in 1|true|yes|on|TRUE|YES|ON) return 0 ;; *) return 1 ;; esac; }
dry()    { truthy "$DRY_RUN"; }

# run <cmd...> — execute, or print under DRY_RUN.
run() {
  if dry; then printf '   WOULD: %s\n' "$*"; else printf '   RUN:   %s\n' "$*"; "$@"; fi
}
# runsh "<shell>" — same, for redirections/pipelines.
runsh() {
  if dry; then printf '   WOULD: %s\n' "$1"; else printf '   RUN:   %s\n' "$1"; bash -c "$1"; fi
}

# ── facts ─────────────────────────────────────────────────────────────────
sha()    { sha256sum "$1" 2>/dev/null | awk '{print $1}'; }
nlines() { wc -l < "$1" 2>/dev/null | tr -d ' '; }

cargo_version()     { sed -n 's/^version = "\(.*\)"/\1/p' "$REPO_ROOT/src/Cargo.toml" | head -1; }
pyproject_version() { sed -n 's/^version = "\(.*\)"/\1/p' "$REPO_ROOT/python/pyproject.toml" | head -1; }
pkg_version()       { sed -n 's/^__version__ = "\(.*\)"/\1/p' "$REPO_ROOT/python/fastglob/__init__.py" | head -1; }

# Every privileged operation goes through sudo_run: it skips escalation when
# SUDO is empty, which is what a root container needs and what lets the rollback
# guard be exercised without root.
sudo_run() { if [ -n "$SUDO" ]; then "$SUDO" "$@"; else "$@"; fi; }
priv()    { if [ -n "$SUDO" ]; then printf '%s ' "$SUDO"; fi; printf '%s ' "$@"; }
run_priv() { if dry; then printf '   WOULD: %s\n' "$(priv "$@")"; else sudo_run "$@"; fi; }

sudo_ok() { [ -z "$SUDO" ] && return 0; "$SUDO" -n true >/dev/null 2>&1; }

_purelib_py='import sysconfig; print(sysconfig.get_paths()["purelib"])'
py_purelib() {
  local py=$1 out=""
  if sudo_ok; then
    out=$(sudo_run "$py" -c "$_purelib_py" 2>/dev/null || true)
  fi
  if [ -z "$out" ]; then
    out=$("$py" -c "$_purelib_py" 2>/dev/null || true)
  fi
  [ -n "$out" ] || return 1
  printf '%s\n' "$out"
}

dist_info_of() {   # $1 = purelib → prints the fastglob dist-info dir, if any
  local d
  for d in "$1"/fastglob-[0-9]*.dist-info; do
    if [ -d "$d" ]; then printf '%s\n' "$d"; return 0; fi
  done
  return 1
}
ver_of_dist_info() { basename "$1" | sed 's/^fastglob-//; s/\.dist-info$//'; }

pkg_has_match() { grep -q '^def match' "$1/__init__.py" 2>/dev/null; }

shim_files() {   # repo shim modules this tool manages
  local f
  for f in "$SHIM_SRC"/*.py; do [ -e "$f" ] && basename "$f"; done
}

# Injection points owned by the provisioner. Read-only here — see the header.
injection_points() {
  printf '%s\n' \
    /etc/environment \
    /etc/profile.d/99-fastglob.sh \
    "$HOME/.bashrc" \
    /opt/agent-bin/bash_noninteractive
}

# ── status ────────────────────────────────────────────────────────────────
cmd_status() {
  local drift=0 ver pkgver cargo_ver pyproj_ver
  ver=$(pkg_version); cargo_ver=$(cargo_version); pyproj_ver=$(pyproject_version)

  step "repo (measured, not asserted)"
  kv "workspace version (src/Cargo.toml)" "${cargo_ver:-<unreadable>}"
  kv "package version (python/pyproject.toml)" "${pyproj_ver:-<unreadable>}"
  kv "package version (__init__.py, source of truth)" "${ver:-<unreadable>}"
  if [ -n "$cargo_ver" ] && [ "$cargo_ver" = "$ver" ] && [ "$pyproj_ver" = "$ver" ]; then
    kv "version agreement" "OK"
  else
    kv "version agreement" "DRIFT (the three must be equal)"
    drift=1
  fi
  local f
  for f in $(shim_files); do
    kv "shim/$f" "$(nlines "$SHIM_SRC/$f") lines, sha256 $(sha "$SHIM_SRC/$f" | cut -c1-12)"
  done

  step "deployed shim (SHIM_DIR=$SHIM_DIR)"
  if [ ! -d "$SHIM_DIR" ]; then
    kv "$(basename "$SHIM_DIR")" "ABSENT — the shadow is not deployed on this box"
  else
    for f in $(shim_files); do
      if [ -f "$SHIM_DIR/$f" ]; then
        local dh rh
        dh=$(sha "$SHIM_DIR/$f"); rh=$(sha "$SHIM_SRC/$f")
        if [ "$dh" = "$rh" ]; then
          kv "$f" "IN SYNC ($(nlines "$SHIM_DIR/$f") lines, sha256 ${dh:0:12})"
        else
          kv "$f" "DRIFT  repo=$(nlines "$SHIM_SRC/$f")L/${rh:0:12}  deployed=$(nlines "$SHIM_DIR/$f")L/${dh:0:12}  ($(diff "$SHIM_SRC/$f" "$SHIM_DIR/$f" 2>/dev/null | wc -l | tr -d ' ') diff lines)"
          drift=1
        fi
      else
        kv "$f" "ABSENT from the deployed directory"
        drift=1
      fi
    done
    local extra
    for extra in "$SHIM_DIR"/*.py; do
      [ -e "$extra" ] || continue
      local bn; bn=$(basename "$extra")
      local known=0 g
      for g in $(shim_files); do [ "$g" = "$bn" ] && known=1; done
      [ "$known" = 0 ] && kv "$bn" "present ONLY in the deployed directory (not a repo shim module)"
    done
  fi

  step "installed wheel (per interpreter)"
  local py
  for py in $PYTHONS; do
    local purelib di iv
    if ! purelib=$(py_purelib "$py"); then
      note "$py: UNRESOLVABLE (sysconfig failed)"; drift=1; continue
    fi
    if di=$(dist_info_of "$purelib"); then
      iv=$(ver_of_dist_info "$di")
      local matched="no"
      [ -f "$purelib/fastglob/__init__.py" ] && pkg_has_match "$purelib/fastglob" && matched="yes"
      kv "$py" "$iv installed at $purelib"
      if [ "$iv" != "$ver" ]; then
        note "version DRIFT — the repo builds $ver, the installed wheel is $iv"
        drift=1
      fi
      if [ "$matched" = "no" ] && pkg_has_match "$REPO_ROOT/python/fastglob"; then
        note "feature DRIFT — the repo's package exposes fastglob.match, the installed copy does not"
        drift=1
      fi
    else
      kv "$py" "fastglob NOT installed in $purelib"
    fi
  done

  step "PYTHONPATH injection (provisioner-owned; reported, never rewritten)"
  local p found=0
  while IFS= read -r p; do
    [ -e "$p" ] || continue
    if grep -q "$SHIM_DIR" "$p" 2>/dev/null; then
      kv "$p" "references $SHIM_DIR"
      found=1
    else
      kv "$p" "no reference"
    fi
  done < <(injection_points)
  [ "$found" = 1 ] || kv "injection" "no injection point references $SHIM_DIR — nothing is shadowing glob"

  step "shadow resolution (what 'import glob' actually gets)"
  local with_shim without_shim
  with_shim=$(PYTHONPATH="$SHIM_DIR" python3 -c 'import glob; print(glob.__file__)' 2>/dev/null || echo "<import failed>")
  without_shim=$(env -u PYTHONPATH python3 -c 'import glob; print(glob.__file__)' 2>/dev/null || echo "<import failed>")
  kv "glob, with PYTHONPATH=$SHIM_DIR" "$with_shim"
  kv "glob, without PYTHONPATH" "$without_shim"
  if [ "$with_shim" != "$SHIM_DIR/glob.py" ]; then
    note "the shadow is NOT active — stdlib answers even with $SHIM_DIR on PYTHONPATH"
  fi
  # `import fastglob` is NOT a reliable probe of the installed artifact: a
  # user-site fastglob.pth can put the repo source tree ahead of site-packages
  # (measured on this box: /home/ubuntu/.local/.../fastglob.pth → the repo).
  # Report both resolutions per interpreter so 'which artifact answered?' is a
  # measurement, never a guess.
  local py
  for py in $PYTHONS; do
    kv "$py: import fastglob, in-place" "$(PYTHONPATH="$SHIM_DIR" "$py" -c 'import fastglob; print(fastglob.__file__)' 2>/dev/null || echo '<not importable>')"
    kv "$py: import fastglob, isolated (-I)" "$("$py" -I -c 'import fastglob; print(fastglob.__file__)' 2>/dev/null || echo '<not importable>')"
  done

  step "snapshots (BACKUP_ROOT=$BACKUP_ROOT)"
  if [ -d "$BACKUP_ROOT" ] && [ -n "$(ls -1 "$BACKUP_ROOT" 2>/dev/null)" ]; then
    ls -1 "$BACKUP_ROOT" | sort | while IFS= read -r s; do kv "snapshot" "$s"; done
  else
    kv "snapshot" "none (nothing has been deployed from this repo yet)"
  fi

  step "verdict"
  if [ "$drift" = 0 ]; then
    info "IN SYNC"
    return 0
  fi
  info "DRIFT — the deployed artifacts do not match this tree. Deploying is an operator"
  info "decision: DRY_RUN=0 make deploy (see docs/architecture.md §Deployment)."
  return 1
}

# ── preconditions ─────────────────────────────────────────────────────────
check_preconditions() {
  local problems=0 py
  info "preconditions:"

  if command -v maturin >/dev/null 2>&1; then
    note "maturin: $(maturin --version 2>/dev/null)"
  else
    note "maturin: MISSING — needed to build the wheel (pip install maturin)"; problems=1
  fi

  if sudo_ok; then
    note "$SUDO: passwordless OK"
  else
    note "$SUDO: NOT passwordless — mutating commands need a password prompt or root"; problems=1
  fi

  local missing=0 f
  for f in $(shim_files); do
    [ -f "$SHIM_SRC/$f" ] || { note "shim/$f: MISSING"; missing=1; }
  done
  if [ "$missing" = 1 ]; then problems=1; else kv "shim sources" "$(shim_files | tr '\n' ' ')"; fi

  local shim_parent; shim_parent=$(dirname "$SHIM_DIR")
  if [ -d "$SHIM_DIR" ]; then
    note "SHIM_DIR exists: $SHIM_DIR (files: $(ls -1 "$SHIM_DIR"/*.py 2>/dev/null | wc -l | tr -d ' '))"
  elif [ -d "$shim_parent" ]; then
    note "SHIM_DIR absent but parent is writable: $shim_parent"
  else
    note "SHIM_DIR parent missing: $shim_parent"; problems=1
  fi

  for py in $PYTHONS; do
    if command -v "$py" >/dev/null 2>&1; then
      note "interpreter $py: $(py_purelib "$py" 2>/dev/null || echo '<purelib unresolvable>')"
    elif sudo_ok && sudo_run "$py" -c 'pass' >/dev/null 2>&1; then
      note "interpreter $py: root-only, $(py_purelib "$py" 2>/dev/null || echo '<purelib unresolvable>')"
    else
      note "interpreter $py: NOT FOUND"; problems=1
    fi
  done

  local cv pv v
  cv=$(cargo_version); pv=$(pyproject_version); v=$(pkg_version)
  if [ "$cv" = "$v" ] && [ "$pv" = "$v" ]; then
    note "versions agree: $v"
  else
    note "VERSION DRIFT: cargo=$cv pyproject=$pv package=$v"; problems=1
  fi

  return "$problems"
}

# ── plan ──────────────────────────────────────────────────────────────────
cmd_plan() {
  step "plan (dry run — nothing is executed, no gate is run)"
  check_preconditions || warn "one or more preconditions failed; see above"
  cat <<'EOF'

  the swap, in order (each step aborts the whole deploy on failure):
    1. gate        make test                       # red suite = nothing is touched
    2. wheel       cd python && maturin build --release --out ../dist
    3. snapshot    $BACKUP_ROOT/<stamp>/{shim,site,meta}
    4. install     sudo <py> -m pip install --force-reinstall --no-deps dist/<wheel>
    5. sync shim   sudo install -m0644 shim/*.py $SHIM_DIR/  (+ drop __pycache__)
    6. verify      live: glob resolves to $SHIM_DIR/glob.py, hashes match repo,
                   FASTGLOB_SHIM_LOUD emits exactly one stderr line, and
                   `from glob import *` stays satisfiable (F-001 regression)
    7. on failure  DRY_RUN=0 tools/deploy.sh rollback FROM=<stamp>   (automatic)
    8. summary     deployed hashes + the version now installed

  not done by this tool (deliberately):
    - PYTHONPATH injection edits — provisioner-owned, re-applied on provision
    - rollback that rewrites those injection files — see `disable` instead
    - publishing to PyPI — a separate release authority with its own credentials

  to apply:  DRY_RUN=0 make deploy
  to revert: DRY_RUN=0 make rollback
EOF
}

# ── apply ─────────────────────────────────────────────────────────────────
snapshot_path_for() { printf '%s\n' "$BACKUP_ROOT/$1"; }

take_snapshot() {   # $1 = stamp → records the CURRENT deployed state
  local stamp=$1 snap py key purelib di
  snap=$(snapshot_path_for "$stamp")
  run_priv mkdir -p "$snap/shim" "$snap/site"

  local f
  : > /tmp/fastglob-deploy-meta.$$
  for f in $(shim_files); do
    if [ -f "$SHIM_DIR/$f" ]; then
      sudo_run cp -a "$SHIM_DIR/$f" "$snap/shim/$f"
      printf 'shim-present %s %s\n' "$f" "$(sha "$SHIM_DIR/$f")" >> /tmp/fastglob-deploy-meta.$$
    else
      printf 'shim-absent %s -\n' "$f" >> /tmp/fastglob-deploy-meta.$$
    fi
  done
  printf 'shim-dir %s\n' "$SHIM_DIR" >> /tmp/fastglob-deploy-meta.$$
  printf 'git-head %s\n' "$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)" >> /tmp/fastglob-deploy-meta.$$
  printf 'repo-version %s\n' "$(pkg_version)" >> /tmp/fastglob-deploy-meta.$$
  printf 'timestamp %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> /tmp/fastglob-deploy-meta.$$
  printf 'user %s\n' "$(id -un)" >> /tmp/fastglob-deploy-meta.$$

  for py in $PYTHONS; do
    key=$(basename "$py")
    purelib=$(py_purelib "$py" || true)
    [ -n "$purelib" ] || continue
    printf 'interpreter %s purelib %s\n' "$py" "$purelib" >> /tmp/fastglob-deploy-meta.$$
    if di=$(dist_info_of "$purelib"); then
      printf 'installed-version %s %s\n' "$py" "$(ver_of_dist_info "$di")" >> /tmp/fastglob-deploy-meta.$$
      sudo_run mkdir -p "$snap/site/$key"
      sudo_run cp -a "$purelib/fastglob" "$snap/site/$key/" 2>/dev/null || true
      sudo_run cp -a "$di" "$snap/site/$key/" 2>/dev/null || true
    else
      printf 'installed-version %s none\n' "$py" >> /tmp/fastglob-deploy-meta.$$
    fi
  done

  sudo_run cp /tmp/fastglob-deploy-meta.$$ "$snap/meta"
  rm -f /tmp/fastglob-deploy-meta.$$
  info "   snapshot: $snap"
}

build_wheel() {
  local out
  # dist/ is created as the INVOKING user, not root: maturin runs as the
  # invoking user and cannot write into a root-owned directory.
  mkdir -p "$REPO_ROOT/dist"
  # maturin resolves [tool.maturin] manifest-path relative to the directory
  # holding pyproject.toml, so it must be invoked from python/ — running it from
  # the repo root makes it search for a repo-root Cargo.toml and fail
  # (measured 2026-09-21: "Can't find /home/ubuntu/fastglob/Cargo.toml").
  if ! bash -c "cd '$REPO_ROOT/python' && maturin build --release --out ../dist" >/tmp/fastglob-maturin.log 2>&1; then
    warn "maturin build failed; last lines:"; tail -5 /tmp/fastglob-maturin.log >&2
    return 1
  fi
  out=$(ls -1t "$REPO_ROOT"/dist/*.whl 2>/dev/null | head -1)
  [ -n "$out" ] || return 1
  printf '%s\n' "$out"
}

install_wheel() {
  local wheel=$1 py
  for py in $PYTHONS; do
    if ! sudo_run "$py" -m pip install --force-reinstall --no-deps "$wheel" >/tmp/fastglob-pip.log 2>&1; then
      warn "pip install failed for $py:"; tail -5 /tmp/fastglob-pip.log >&2
      return 1
    fi
  done
  return 0
}

sync_shim() {
  local f
  for f in $(shim_files); do
    sudo_run install -m 0644 "$SHIM_SRC/$f" "$SHIM_DIR/$f" || return 1
  done
  sudo_run rm -rf "$SHIM_DIR/__pycache__" || return 1
  return 0
}

verify_live() {
  local rc=0 py rc2
  for py in $PYTHONS; do
    local resolved
    resolved=$(PYTHONPATH="$SHIM_DIR" "$py" -c 'import glob; print(glob.__file__)' 2>/dev/null || echo "")
    if [ "$resolved" = "$SHIM_DIR/glob.py" ]; then
      info "   $py: import glob → $resolved"
    else
      warn "$py: import glob → '${resolved:-<failed>}', expected $SHIM_DIR/glob.py"; rc=1
    fi

    if PYTHONPATH="$SHIM_DIR" FASTGLOB_SHIM_LOUD=1 "$py" -c 'import glob' 2>/tmp/fastglob-loud.$$ >/dev/null; then
      if [ -s /tmp/fastglob-loud.$$ ]; then
        info "   $py: FASTGLOB_SHIM_LOUD emitted $(wc -l < /tmp/fastglob-loud.$$ | tr -d ' ') stderr line(s)"
      else
        warn "$py: FASTGLOB_SHIM_LOUD emitted nothing — loudness switch absent from the deployed shim?"; rc=1
      fi
    else
      warn "$py: import glob failed under the deployed shim"; rc=1
    fi
    rm -f /tmp/fastglob-loud.$$

    if PYTHONPATH="$SHIM_DIR" "$py" -c 'exec("from glob import *", {})' 2>/dev/null; then
      info "   $py: 'from glob import *' satisfiable (F-001 regression holds)"
    else
      warn "$py: 'from glob import *' raised under the deployed shim"; rc=1
    fi

    # The installed wheel is checked in ISOLATION (-I: no PYTHONPATH, no user
    # site). This is not decoration: this box carries a fastglob.pth in the user
    # site-packages pointing at the repo source tree, so a plain `import
    # fastglob` answers with the REPO and would report a feature the installed
    # wheel does not have (measured 2026-09-21: -I resolves
    # /usr/local/lib/python3.12/dist-packages/fastglob/__init__.py, which has no
    # `match`; the repo's package does). A verify that passes by importing the
    # wrong artifact is worse than no verify.
    local purelib resolved_pkg
    purelib=$(py_purelib "$py" 2>/dev/null || echo "")
    resolved_pkg=$("$py" -I -c 'import fastglob; print(fastglob.__file__)' 2>/dev/null || echo "")
    if [ -z "$resolved_pkg" ]; then
      warn "$py: the installed wheel does not import at all (isolated)"; rc=1
    elif [ -n "$purelib" ] && [ "${resolved_pkg#"$purelib"/}" != "$resolved_pkg" ]; then
      if "$py" -I -c 'import fastglob; fastglob.match("**/v/**", "a/v/b.rs")' 2>/dev/null; then
        info "   $py: installed wheel exposes fastglob.match ($resolved_pkg)"
      else
        warn "$py: installed wheel has no working fastglob.match ($resolved_pkg)"; rc=1
      fi
    else
      warn "$py: isolated 'import fastglob' did not resolve to the installed wheel ($resolved_pkg)"; rc=1
    fi
  done

  local f dh rh
  for f in $(shim_files); do
    dh=$(sha "$SHIM_DIR/$f"); rh=$(sha "$SHIM_SRC/$f")
    if [ -n "$dh" ] && [ "$dh" = "$rh" ]; then
      info "   $f: deployed hash == repo hash (${dh:0:12})"
    else
      warn "$f: deployed copy does not match the repo"; rc=1
    fi
  done
  return "$rc"
}

cmd_apply() {
  local stamp snap wheel
  stamp=$(date -u +%Y%m%dT%H%M%SZ)

  step "1/8 gate: make test (the full local suite)"
  info "   the gate is not optional: a red suite must not reach the swap (R4)"
  if ! bash -c "cd '$REPO_ROOT' && make test" >/tmp/fastglob-gate.log 2>&1; then
    warn "GATE RED — nothing was touched. Last lines:"
    tail -20 /tmp/fastglob-gate.log >&2
    die "deploy refused: red suite" 1
  fi
  info "   GATE GREEN ($(grep -c 'test result: ok' /tmp/fastglob-gate.log) cargo result line(s))"
  # Show the gate's own summary lines rather than make's directory chatter.
  grep -E 'test result:|^OK' /tmp/fastglob-gate.log | tail -4 | sed 's/^/   /'

  step "2/8 preconditions"
  check_preconditions || die "preconditions failed — nothing was touched" 1

  step "3/8 wheel"
  if [ -n "$WHEEL" ]; then
    [ -f "$WHEEL" ] || die "WHEEL=$WHEEL does not exist"
    wheel=$WHEEL; info "   using provided wheel: $wheel"
  elif dry; then
    info "   WOULD build: cd python && maturin build --release --out ../dist"
    wheel="<wheel>"
  else
    wheel=$(build_wheel) || die "wheel build failed — nothing was touched"
    info "   built: $wheel"
  fi

  if dry; then
    step "4/8 snapshot (dry run — would be taken BEFORE the first mutation)"
    info "   WOULD create: $(snapshot_path_for "$stamp")"
  else
    step "4/8 snapshot"
    take_snapshot "$stamp"
    snap=$(snapshot_path_for "$stamp")
  fi

  step "5/8 install wheel"
  if dry; then
    local py
    for py in $PYTHONS; do
      info "   WOULD: $(priv "$py" -m pip install --force-reinstall --no-deps "$wheel")"
    done
  elif ! install_wheel "$wheel"; then
    warn "wheel install failed after the snapshot → rolling back"
    DRY_RUN=0 "$SELF_DIR/deploy.sh" rollback FROM="$stamp" || warn "AUTO-ROLLBACK FAILED — recover from $snap by hand"
    die "deploy failed and was rolled back" 1
  fi

  step "6/8 sync shim"
  if dry; then
    local f
    for f in $(shim_files); do
      info "   WOULD: $(priv install -m0644 "$SHIM_SRC/$f" "$SHIM_DIR/$f")"
    done
    info "   WOULD: $(priv rm -rf "$SHIM_DIR/__pycache__")"
  elif ! sync_shim; then
    warn "shim sync failed after the snapshot → rolling back"
    DRY_RUN=0 "$SELF_DIR/deploy.sh" rollback FROM="$stamp" || warn "AUTO-ROLLBACK FAILED — recover from $snap by hand"
    die "deploy failed and was rolled back" 1
  fi

  step "7/8 verify (live)"
  if dry; then
    info "   WOULD run the live assertions (see tools/deploy.sh verify)"
  elif ! verify_live; then
    warn "post-deploy verification failed → rolling back"
    DRY_RUN=0 "$SELF_DIR/deploy.sh" rollback FROM="$stamp" || warn "AUTO-ROLLBACK FAILED — recover from $snap by hand"
    die "deploy was rolled back because verification failed" 1
  fi

  step "8/8 summary"
  if dry; then
    info "DRY RUN — nothing was installed, copied, or modified."
    info "To perform the swap:  DRY_RUN=0 make deploy"
  else
    info "deployed fastglob $(pkg_version) to: $PYTHONS"
    for f in $(shim_files); do
      info "shim $f: $(nlines "$SHIM_DIR/$f") lines, sha256 $(sha "$SHIM_DIR/$f" | cut -c1-12)"
    done
    info "snapshot for rollback: $snap"
    info "check state any time:   make deploy-status"
  fi
}

# ── rollback ──────────────────────────────────────────────────────────────
latest_snapshot() { { ls -1 "$BACKUP_ROOT" 2>/dev/null || true; } | sort | tail -1; }

# A snapshot is AUTHORITATIVE only for the targets it recorded.
#
# This guard exists because its absence was destructive: `restore_snapshot`
# treats "the snapshot recorded nothing installed" as "remove whatever is
# installed now", so replaying a snapshot taken against another SHIM_DIR (or
# another BACKUP_ROOT) deletes an install the snapshot never described. That is
# exactly what happened on 2026-09-21 — a rollback driven with a synthetic
# snapshot and the default PYTHONS removed the live
# site-packages/fastglob for a target the snapshot had never captured. A
# rollback that cannot name what it is overwriting must refuse, not guess.
snapshot_meta_shim_dir()     { sed -n 's/^shim-dir //p'                     "$1/meta" 2>/dev/null; }
snapshot_meta_targets()      { sed -n 's/^interpreter \([^ ]*\) purelib \(.*\)/\1 \2/p' "$1/meta" 2>/dev/null; }
snapshot_meta_interpreter()  { awk -v p="$2" '$1=="interpreter" && $2==p {print $4}' "$1/meta" 2>/dev/null; }

assert_snapshot_authority() {   # $1 = snap
  local snap=$1 recorded py
  if [ ! -f "$snap/meta" ]; then
    warn "$snap has no meta — refusing to restore."
    warn "  A directory without meta was not written by this tool, so its contents are not"
    warn "  authoritative for anything here; restoring it would overwrite state it never recorded."
    exit 3
  fi
  recorded=$(snapshot_meta_shim_dir "$snap")
  if [ "$recorded" != "$SHIM_DIR" ]; then
    warn "snapshot was taken against SHIM_DIR=$recorded, not $SHIM_DIR — refusing to restore."
    warn "  Re-run with SHIM_DIR=$recorded if that is the intent."
    exit 3
  fi
  for py in $PYTHONS; do
    if [ -z "$(snapshot_meta_interpreter "$snap" "$py")" ]; then
      warn "snapshot records no state for interpreter $py — refusing."
      warn "  That interpreter's pre-deploy state is unknown, and guessing means deleting an"
      warn "  install the snapshot never described."
      exit 3
    fi
  done
  return 0
}

restore_snapshot() {
  local snap=$1 py purelib key
  local f
  for f in $(shim_files); do
    if [ -f "$snap/shim/$f" ]; then
      sudo_run install -m 0644 "$snap/shim/$f" "$SHIM_DIR/$f" || return 1
    else
      sudo_run rm -f "$SHIM_DIR/$f" || return 1
    fi
  done
  sudo_run rm -rf "$SHIM_DIR/__pycache__" || return 1

  # Drive restoration from the snapshot's OWN recorded targets (py + purelib),
  # never from the ambient $PYTHONS: the snapshot knows where its files came from.
  while read -r py purelib; do
    [ -n "$py" ] || continue
    key=$(basename "$py")
    if [ -d "$snap/site/$key" ]; then
      sudo_run rm -rf "$purelib/fastglob" "$purelib"/fastglob-[0-9]*.dist-info || return 1
      sudo_run cp -a "$snap/site/$key/." "$purelib/" || return 1
    else
      sudo_run rm -rf "$purelib/fastglob" "$purelib"/fastglob-[0-9]*.dist-info || return 1
    fi
  done < <(snapshot_meta_targets "$snap")
  return 0
}

cmd_rollback() {
  local stamp=$FROM snap
  if [ -z "$stamp" ]; then stamp=$(latest_snapshot); fi
  if [ -z "$stamp" ]; then
    info "no snapshot under $BACKUP_ROOT"
    info "nothing this repo deployed can be restored from here. To fall back to stdlib"
    info "for the shim the provisioner installed, use: DRY_RUN=0 tools/deploy.sh disable"
    return 3
  fi
  snap=$(snapshot_path_for "$stamp")
  [ -d "$snap" ] || die "snapshot not found: $snap" 2
  assert_snapshot_authority "$snap"   # refuses BEFORE anything is touched

  step "rollback from $snap"
  if [ -f "$snap/meta" ]; then sed 's/^/   meta: /' "$snap/meta"; fi
  info "   restores: shim/*.py (or removes them when the snapshot recorded none) plus the"
  info "             installed fastglob package for each interpreter, from this snapshot"

  if dry; then
    local f py key purelib
    for f in $(shim_files); do
      if [ -f "$snap/shim/$f" ]; then
        info "   WOULD: $(priv install -m0644 "$snap/shim/$f" "$SHIM_DIR/$f")"
      else
        info "   WOULD: $(priv rm -f "$SHIM_DIR/$f")   (absent before the deploy → stdlib)"
      fi
    done
    while read -r py purelib; do
      [ -n "$py" ] || continue
      key=$(basename "$py")
      if [ -d "$snap/site/$key" ]; then
        info "   WOULD: restore the pre-deploy fastglob package and its dist-info into $purelib"
        info "          (from $snap/site/$key)"
      else
        info "   WOULD: remove the installed fastglob package and dist-info from $purelib"
        info "          (the snapshot recorded nothing installed there)"
      fi
    done < <(snapshot_meta_targets "$snap")
    info "DRY RUN — nothing was restored. To restore: DRY_RUN=0 make rollback"
    return 0
  fi

  if ! restore_snapshot "$snap"; then
    warn "restore failed part-way — the snapshot is intact; re-run after fixing the cause"
    die "rollback incomplete" 1
  fi

  local f dh rh rc=0
  for f in $(shim_files); do
    if [ -f "$snap/shim/$f" ]; then
      dh=$(sha "$SHIM_DIR/$f"); rh=$(sha "$snap/shim/$f")
      if [ "$dh" = "$rh" ]; then
        info "   $f: restored (sha256 ${dh:0:12})"
      else
        warn "$f: restored copy does not match the snapshot"; rc=1
      fi
    elif [ -e "$SHIM_DIR/$f" ]; then
      warn "$f: still present but the snapshot recorded it as absent"; rc=1
    else
      info "   $f: absent, as recorded — 'import glob' resolves to stdlib"
    fi
  done

  local py resolved purelib di iv
  while read -r py purelib; do
    [ -n "$py" ] || continue
    resolved=$(PYTHONPATH="$SHIM_DIR" "$py" -c 'import glob; print(glob.__file__)' 2>/dev/null || echo '<failed>')
    info "   $py: import glob → $resolved"
    iv=""
    if di=$(dist_info_of "$purelib" 2>/dev/null); then iv=$(ver_of_dist_info "$di"); fi
    info "   $py: installed fastglob → ${iv:-<none>}"
  done < <(snapshot_meta_targets "$snap")

  [ "$rc" = 0 ] || die "rollback did not reproduce the snapshot exactly" 1
  info "rollback complete"
}

# ── disable ───────────────────────────────────────────────────────────────
cmd_disable() {
  local stamp f moved=0
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  step "disable the shadow (→ stdlib glob)"
  for f in $(shim_files); do
    if [ -f "$SHIM_DIR/$f" ]; then
      run_priv mv "$SHIM_DIR/$f" "$SHIM_DIR/$f.disabled-$stamp"
      moved=1
    fi
  done
  [ "$moved" = 1 ] || { info "   nothing to disable in $SHIM_DIR"; return 0; }
  run_priv rm -rf "$SHIM_DIR/__pycache__"
  info "   re-enable with: DRY_RUN=0 make deploy   (or mv the .disabled-* files back)"
  if ! dry; then
    local resolved
    resolved=$(PYTHONPATH="$SHIM_DIR" python3 -c 'import glob; print(glob.__file__)' 2>/dev/null || echo "<failed>")
    info "   now: import glob → $resolved"
  fi
}

# ── verify ────────────────────────────────────────────────────────────────
cmd_verify() {
  step "live verification against SHIM_DIR=$SHIM_DIR"
  if verify_live; then
    info "verify: OK"
    return 0
  fi
  info "verify: FAILED"
  return 1
}

usage() { awk 'NR>1 && $0 !~ /^#/ { exit } NR>1 { print }' "$0" | sed 's/^# \{0,1\}//'; }

main() {
  local cmd=${1:-help}
  shift || true
  # `FROM=<stamp>` is accepted as an env var AND as a trailing argument, because
  # the usage line reads that way and the argument form was silently ignored:
  # `rollback FROM=<older>` then restored the NEWEST snapshot. Restoring a
  # snapshot other than the one named is the worst failure this tool has, so an
  # unusable argument is now an error rather than a shrug.
  local extra
  for extra in "$@"; do
    case "$extra" in
      FROM=*) FROM=${extra#FROM=} ;;
      '')    ;;
      *)     die "unexpected argument: $extra (the only accepted extra argument is FROM=<stamp>)" 2 ;;
    esac
  done
  case "$cmd" in
    status)   cmd_status ;;
    plan)     cmd_plan ;;
    apply)    cmd_apply ;;
    rollback) cmd_rollback ;;
    disable)  cmd_disable ;;
    verify)   cmd_verify ;;
    help|-h|--help) usage ;;
    *) die "unknown command: $cmd (try: status | plan | apply | rollback | disable | verify)" 2 ;;
  esac
}

main "$@"
