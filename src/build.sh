#!/bin/sh
# Optional developer build on normal macOS with Xcode command-line tools.
# A rebuilt binary changes SHA256SUMS; ordinary use needs no build.
set -eu
BASE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
[ "$#" -eq 1 ] || { echo 'Usage: build.sh /absolute/output/sha256-file' >&2; exit 2; }
OUT=$1
case "$OUT" in /*) ;; *) echo 'Output must be absolute.' >&2; exit 2 ;; esac
[ ! -e "$OUT" ] || { echo 'Choose a new output file; no overwrite performed.' >&2; exit 2; }
TMP=$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/mdm-sha-build.XXXXXX")
trap '/bin/rm -rf "$TMP"' EXIT
/usr/bin/clang -std=c99 -O2 -Wall -Wextra -Wpedantic -Werror -arch arm64 -mmacosx-version-min=11.0 "$BASE/sha256-file.c" -o "$TMP/sha.arm64"
/usr/bin/clang -std=c99 -O2 -Wall -Wextra -Wpedantic -Werror -arch x86_64 -mmacosx-version-min=10.13 "$BASE/sha256-file.c" -o "$TMP/sha.x86_64"
/usr/bin/lipo -create "$TMP/sha.arm64" "$TMP/sha.x86_64" -output "$TMP/sha256-file"
/usr/bin/codesign --force --sign - --timestamp=none "$TMP/sha256-file"
"$TMP/sha256-file" --self-test
/bin/cp "$TMP/sha256-file" "$OUT"
/bin/chmod 755 "$OUT"
echo "built=$OUT"
