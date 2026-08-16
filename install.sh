#!/bin/sh
# Install stw — a CLI workspace manager for agents.
#
#   curl -fsSL https://raw.githubusercontent.com/gr-ant/stw/main/install.sh | sh
#
# Installs the source tree under $PREFIX/share/stw and a launcher at
# $PREFIX/bin/stw. There are no dependencies to resolve: stw is stdlib-only, and
# numpy/duckdb stay optional (see README).
#
# Environment:
#   STW_INIT_DIR   initialize this dir as a workspace (default: $PWD)
#   STW_NO_INIT    set to 1 to skip workspace initialization
#   STW_PREFIX    install root            (default: $HOME/.local)
#   STW_REF       branch, tag, or sha     (default: main)
#   STW_REPO      owner/name              (default: gr-ant/stw)
#   GITHUB_TOKEN   auth, required while the repo is private
#
# Flags: --uninstall

set -eu

REPO="${STW_REPO:-gr-ant/stw}"
REF="${STW_REF:-main}"
PREFIX="${STW_PREFIX:-$HOME/.local}"
BIN="$PREFIX/bin"
LIB="$PREFIX/share/stw"
MIN_PY="3.11"

say()  { printf '%s\n' "$*"; }
die()  { printf 'install: %s\n' "$*" >&2; exit 1; }

uninstall() {
    # Note: every conditional here is an `if`, not `test && cmd`. Under `set -e`
    # a bare `test && cmd` whose test fails exits the script.
    removed=0
    if [ -e "$BIN/stw" ]; then rm -f "$BIN/stw"; say "removed $BIN/stw"; removed=1; fi
    if [ -d "$LIB" ]; then rm -rf "$LIB"; say "removed $LIB"; removed=1; fi
    if [ "$removed" = 0 ]; then say "stw is not installed under $PREFIX"; fi
    say "Workspaces are untouched — .stw/ directories are yours, not the installer's."
    exit 0
}

if [ "${1:-}" = "--uninstall" ]; then uninstall; fi

# -- python ----------------------------------------------------------------
# 3.11 is the floor: tomllib (config parsing) landed there.
#
# The launcher pins whatever we pick, so prefer a system interpreter over an
# activated virtualenv — pinning someone's project venv means stw breaks the day
# that venv is deleted. Only fall back to a venv if nothing else qualifies.
check_python() {
    # $1 interpreter, $2 "system" to also require it not be a venv
    "$1" -c '
import sys
ok = sys.version_info[:2] >= (3, 11)
if len(sys.argv) > 1 and sys.argv[1] == "system":
    ok = ok and sys.prefix == sys.base_prefix
raise SystemExit(0 if ok else 1)
' "${2:-}" 2>/dev/null
}

find_python() {
    for mode in system any; do
        for c in /usr/bin/python3 /usr/local/bin/python3 python3 \
                 python3.14 python3.13 python3.12 python3.11; do
            p=$(command -v "$c" 2>/dev/null) || continue
            if check_python "$p" "$mode"; then printf '%s' "$p"; return 0; fi
        done
    done
    return 1
}

PY=$(find_python) || die "no python $MIN_PY+ found. stw needs tomllib, which arrived in $MIN_PY."
PYV=$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')

# -- download --------------------------------------------------------------
# Two sources: codeload is unmetered but serves stale 404s for a while after a
# repo rename; the API tarball endpoint is immediately correct but rate-limited
# when unauthenticated. Try the cheap one, fall back to the reliable one.
TARBALL="https://codeload.github.com/$REPO/tar.gz/$REF"
TARBALL_ALT="https://api.github.com/repos/$REPO/tarball/$REF"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/stw-install.XXXXXX")
trap 'rm -rf "$TMP"' EXIT INT TERM

fetch() {
    # $1 url, $2 output path
    if command -v curl >/dev/null 2>&1; then
        if [ -n "${GITHUB_TOKEN:-}" ]; then
            curl -fsSL -H "Authorization: Bearer $GITHUB_TOKEN" -o "$2" "$1"
        else
            curl -fsSL -o "$2" "$1"
        fi
    elif command -v wget >/dev/null 2>&1; then
        if [ -n "${GITHUB_TOKEN:-}" ]; then
            wget -qO "$2" --header="Authorization: Bearer $GITHUB_TOKEN" "$1"
        else
            wget -qO "$2" "$1"
        fi
    else
        die "neither curl nor wget is available"
    fi
}

say "fetching $REPO@$REF"
if ! fetch "$TARBALL" "$TMP/src.tar.gz" 2>/dev/null; then
    fetch "$TARBALL_ALT" "$TMP/src.tar.gz" 2>/dev/null || FETCH_FAILED=1
fi
if [ "${FETCH_FAILED:-}" = 1 ] || [ ! -s "$TMP/src.tar.gz" ]; then
    if [ -z "${GITHUB_TOKEN:-}" ]; then
        die "download failed. If $REPO is private, set GITHUB_TOKEN — e.g.
    curl -fsSL -H \"Authorization: Bearer \$(gh auth token)\" \\
        https://raw.githubusercontent.com/$REPO/main/install.sh \\
      | GITHUB_TOKEN=\$(gh auth token) sh"
    fi
    die "download failed. Check GITHUB_TOKEN and that $REF exists in $REPO."
fi

tar -xzf "$TMP/src.tar.gz" -C "$TMP" || die "the download is not a valid tarball (auth failure returns HTML)"
SRC=$(find "$TMP" -maxdepth 2 -type d -name stw -print -quit)
if [ -z "$SRC" ] || [ ! -f "$SRC/cli.py" ]; then
    die "unpacked tree has no stw/ package"
fi

# -- install ---------------------------------------------------------------
mkdir -p "$BIN" "$(dirname "$LIB")"
rm -rf "$LIB.new"
mkdir -p "$LIB.new"
cp -R "$SRC" "$LIB.new/stw"
find "$LIB.new" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

# Precompiling costs a second here and saves ~5ms on every invocation, which
# matters for a CLI an agent calls dozens of times per task.
"$PY" -m compileall -q "$LIB.new/stw" >/dev/null 2>&1 || true

rm -rf "$LIB.old"
if [ -d "$LIB" ]; then mv "$LIB" "$LIB.old"; fi
mv "$LIB.new" "$LIB"
rm -rf "$LIB.old"

# The launcher pins the interpreter it was installed with, so a later PATH
# change can't silently point stw at a python too old to run it.
cat > "$BIN/stw" <<LAUNCHER
#!/bin/sh
# generated by the stw installer
PYTHONPATH="$LIB\${PYTHONPATH:+:\$PYTHONPATH}" exec "$PY" -m stw "\$@"
LAUNCHER
chmod +x "$BIN/stw"

VERSION=$("$BIN/stw" --version 2>/dev/null || echo "?")

say ""
say "stw $VERSION installed"
say "  binary   $BIN/stw"
say "  library  $LIB"
say "  python   $PY ($PYV)"

case ":$PATH:" in
    *":$BIN:"*) ;;
    *)
        say ""
        say "$BIN is not on your PATH. Add it:"
        say "  echo 'export PATH=\"$BIN:\$PATH\"' >> ~/.bashrc && exec \$SHELL"
        ;;
esac

# -- initialize a workspace ------------------------------------------------
# Installing and initializing are different acts: the first puts a binary on
# disk, the second writes .stw/ and AGENTS.md into a directory and indexes
# everything in it. So this runs where the installer was invoked, but refuses
# $HOME and / — `curl | sh` is usually run from $HOME, and indexing a whole
# home directory (and appending to a CLAUDE.md that is already there) is not
# what anyone means by "install a CLI".
INIT_DIR="${STW_INIT_DIR:-$PWD}"

init_workspace() {
    if [ "${STW_NO_INIT:-}" = "1" ]; then
        say "skipped workspace init (STW_NO_INIT=1)"
        return 0
    fi
    if [ -d "$INIT_DIR/.stw" ]; then
        say "$INIT_DIR is already a workspace — left as is"
        return 0
    fi
    if [ -z "${STW_INIT_DIR:-}" ] && { [ "$INIT_DIR" = "$HOME" ] || [ "$INIT_DIR" = "/" ]; }; then
        say "not initializing $INIT_DIR — it is your home directory."
        say "  cd to a project and run \`stw init\`, or rerun with STW_INIT_DIR=/path/to/dir"
        return 0
    fi
    if ! [ -w "$INIT_DIR" ]; then
        say "not initializing $INIT_DIR — not writable"
        return 0
    fi
    say ""
    ( cd "$INIT_DIR" && "$BIN/stw" init ) || say "workspace init failed — run \`stw init\` yourself"
}

init_workspace

say ""
say "Next: \`stw help\`, or \`stw map\` to see what a workspace knows."
