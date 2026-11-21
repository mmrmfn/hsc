#!/bin/sh
# Build the per-OS release downloads.
#   tools/package.sh [hsc.py] [outdir]
# Uses the installers from this repo and the given hsc.py (default: ./hsc.py),
# so older versions can be packaged with today's installers.
#
# Output (asset names have no version, so README links can use
# https://github.com/mmrmfn/hsc/releases/latest/download/<name>):
#   hsc-windows.zip   hsc-macos.zip   hsc-linux.tar.gz   hsc.py   SHA256SUMS.txt
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${1:-$ROOT/hsc.py}"
OUT="${2:-$ROOT/dist}"
VER="$(python3 "$SRC" --version | awk '{print $2}')"
[ -n "$VER" ] || { echo "could not read version from $SRC" >&2; exit 1; }

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
rm -rf "$OUT" && mkdir -p "$OUT"
NAME="hsc-$VER"

# ---- Windows: flat folder, double-click install.cmd
W="$STAGE/win/$NAME"
mkdir -p "$W"
cp "$SRC" "$W/hsc.py"
cp "$ROOT/hsc.magic" "$ROOT/windows/install.cmd" "$ROOT/windows/install.ps1" "$ROOT/windows/uninstall.cmd" "$W/"
printf '%s\r\n' \
  "HSC $VER for Windows" \
  "" \
  "Needs Python 3.8+  (winget install Python.Python.3.13)" \
  "" \
  "Install:    double-click install.cmd, then open a NEW terminal and run: hsc --version" \
  "Uninstall:  double-click uninstall.cmd" \
  "" \
  "https://github.com/mmrmfn/hsc" > "$W/README.txt"
(cd "$STAGE/win" && zip -qr "$OUT/hsc-windows.zip" "$NAME")

# ---- macOS and Linux: install.sh
for os in macos linux; do
  U="$STAGE/$os/$NAME"
  mkdir -p "$U"
  cp "$SRC" "$U/hsc.py"
  cp "$ROOT/hsc.magic" "$ROOT/install.sh" "$U/"
  chmod 755 "$U/hsc.py" "$U/install.sh"
  printf '%s\n' \
    "HSC $VER for $os" \
    "" \
    "Needs Python 3.8+" \
    "" \
    "Install:    sh install.sh                       (system-wide, asks for sudo)" \
    "            PREFIX=\$HOME/.local sh install.sh   (just you, no sudo)" \
    "Uninstall:  sh install.sh --uninstall" \
    "" \
    "https://github.com/mmrmfn/hsc" > "$U/README.txt"
done
(cd "$STAGE/macos" && zip -qr "$OUT/hsc-macos.zip" "$NAME")
(cd "$STAGE/linux" && tar -czf "$OUT/hsc-linux.tar.gz" "$NAME")

# ---- the bare script, plus checksums
cp "$SRC" "$OUT/hsc.py"
(cd "$OUT" && sha256sum hsc-windows.zip hsc-macos.zip hsc-linux.tar.gz hsc.py > SHA256SUMS.txt)

echo "Packaged HSC $VER into $OUT:"
ls -l "$OUT"
