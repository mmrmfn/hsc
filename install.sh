#!/bin/sh
# HSC 2.0 installer for Linux and macOS
#   ./install.sh               install to /usr/local  (asks for sudo if needed)
#   ./install.sh --uninstall   remove it
#   PREFIX=$HOME/.local ./install.sh    install just for you, no sudo
set -e

PREFIX="${PREFIX:-/usr/local}"
HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="$PREFIX/bin/hsc"
SHARE="$PREFIX/share/hsc"
MAGIC_FILE=/etc/magic
START="# >>> HSC magic >>>"
END="# <<< HSC magic <<<"
OS="$(uname -s)"

# Re-run with sudo if we can't write to the install location.
need_root() {
    target="$PREFIX"
    while [ ! -d "$target" ]; do target="$(dirname "$target")"; done
    if [ ! -w "$target" ] || { [ "$OS" = Linux ] && [ ! -w /etc ] && [ "$PREFIX" = /usr/local ]; }; then
        if [ "$(id -u)" -ne 0 ]; then
            echo "Need admin rights for $PREFIX - re-running with sudo..."
            exec sudo env PREFIX="$PREFIX" sh "$0" "$@"
        fi
    fi
}

remove_magic() {
    if [ -f "$MAGIC_FILE" ] && grep -qF "$START" "$MAGIC_FILE"; then
        tmp="$(mktemp)"
        awk -v s="$START" -v e="$END" '$0==s{skip=1} !skip{print} $0==e{skip=0}' "$MAGIC_FILE" > "$tmp"
        cat "$tmp" > "$MAGIC_FILE"
        rm -f "$tmp"
    fi
}

if [ "$1" = "--uninstall" ]; then
    need_root "$@"
    rm -f "$BIN"
    rm -rf "$SHARE"
    [ "$OS" = Linux ] && [ -w /etc ] && remove_magic
    echo "HSC removed."
    exit 0
fi

# --- Python check: HSC needs Python 3.8+
if ! command -v python3 >/dev/null 2>&1 || \
   ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
    echo "Python 3.8 or newer is required."
    if [ "$OS" = Darwin ]; then
        echo "  Install it with:  brew install python   (or from python.org)"
    else
        echo "  Install it with your package manager, e.g.:  sudo apt install python3"
    fi
    exit 1
fi

need_root "$@"

# --- install: hsc.py becomes the `hsc` command (its #! line runs it with python3)
mkdir -p "$PREFIX/bin" "$SHARE"
cp "$HERE/hsc.py" "$BIN"
chmod 755 "$BIN"
cp "$HERE/hsc.magic" "$SHARE/hsc.magic"

# --- teach `file` about .hsc (Linux; on macOS use: file -m $SHARE/hsc.magic x.hsc)
if [ "$OS" = Linux ] && [ -w /etc ]; then
    remove_magic
    { echo "$START"; cat "$HERE/hsc.magic"; echo "$END"; } >> "$MAGIC_FILE"
fi

echo "HSC installed: $BIN"
case ":$PATH:" in
    *":$PREFIX/bin:"*) echo "Try it:  hsc --help" ;;
    *) echo "Note: $PREFIX/bin isn't on your PATH yet. Add this to your shell profile:"
       echo "  export PATH=\"$PREFIX/bin:\$PATH\"" ;;
esac
