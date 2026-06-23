#!/usr/bin/env bash
# Set up the Dahua NetSDK from its wheel. See README.md in this directory.
#
# Usage: paste NetSDK-*-linux_x86_64.whl into this folder, then run ./NetSDK/setup.sh
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"   # ./NetSDK/
cd "$DIR"

# 1. Locate the wheel.
shopt -s nullglob
whls=(NetSDK-*.whl)
shopt -u nullglob
if [ ${#whls[@]} -eq 0 ]; then
  echo "error: no NetSDK-*.whl found in $DIR" >&2
  echo "See ./NetSDK/README.md for instructions." >&2
  exit 1
fi
if [ ${#whls[@]} -gt 1 ]; then
  echo "error: multiple NetSDK-*.whl files found; keep only one:" >&2
  printf '  %s\n' "${whls[@]}" >&2
  exit 1
fi
WHL="${whls[0]}"

# 2. Extract (a wheel is a zip; use python's stdlib so no `unzip` dependency).
echo "Extracting ${WHL} ..."
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
python3 -m zipfile -e "$WHL" "$TMP"

if [ ! -d "$TMP/NetSDK" ]; then
  echo "error: wheel did not contain a top-level NetSDK/ package (unexpected layout)" >&2
  exit 1
fi

# 3. Move the package contents up into this directory (./NetSDK/NetSDK.py, ./NetSDK/Libs/...).
cp -r "$TMP/NetSDK/." "$DIR/"

# 4. Verify.
if [ -f "$DIR/NetSDK.py" ] && compgen -G "$DIR/Libs/linux64/*.so" >/dev/null; then
  echo "✓ NetSDK ready: NetSDK.py + $(ls "$DIR"/Libs/linux64/*.so | wc -l) .so libs in place."
  echo "  You can delete the .whl now if you like (it is git-ignored either way)."
else
  echo "✗ setup incomplete — expected NetSDK.py and Libs/linux64/*.so" >&2
  exit 1
fi
