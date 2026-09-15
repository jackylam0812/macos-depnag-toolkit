#!/bin/sh
# Verify immutable toolkit files; does not prepare/apply any native state.
set -eu
BASE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
die() { printf '%s\n' "$1" >&2; exit 2; }
[ "$#" -eq 0 ] || die 'Usage: SELF_TEST.sh'
[ -x "$BASE/sha256-file" ] || die 'sha256-file is missing or not executable.'
"$BASE/sha256-file" --self-test || die 'Native SHA-256 self-test failed.'
for SCRIPT in lib.sh PREPARE.sh APPLY_IN_RECOVERY.sh ROLLBACK.sh STATUS.sh VERIFY_AFTER_BOOT.sh SELF_TEST.sh GET_TOOLKIT.sh ONE_CLICK.sh; do
 /bin/sh -n "$BASE/$SCRIPT" || die "Shell syntax failed: $SCRIPT"
done
echo 'shell_syntax=ok'
[ -f "$BASE/SHA256SUMS" ] || die 'SHA256SUMS is missing; retain the complete distribution.'
REQUIRED_FILES='README.md
LICENSE
VERSION
lib.sh
PREPARE.sh
APPLY_IN_RECOVERY.sh
ROLLBACK.sh
STATUS.sh
VERIFY_AFTER_BOOT.sh
SELF_TEST.sh
GET_TOOLKIT.sh
ONE_CLICK.sh
sha256-file
src/sha256-file.c
src/LICENSE
src/build.sh
tests/run_tests.py
tests/test_network_download.py
tests/test_one_click.py
scripts/build_release.py
scripts/update_checksums.py
scripts/check_public.py
examples/README.md
examples/ORIGINAL_FILE.plist
examples/MODIFIED_FILE.plist
examples/DIFF_FILE.diff
docs/VALIDATION.md
docs/ADVANCED.md
.github/workflows/test.yml
.gitignore'
SEEN='
'
COUNT=0
while read -r EXPECTED REL; do
 [ -n "$EXPECTED" ] || continue
 [ "${#EXPECTED}" -eq 64 ] || die 'Invalid checksum entry.'
 case "$EXPECTED" in *[!0123456789abcdef]*) die 'Invalid checksum entry.' ;; esac
 case "$REL" in ''|/*|../*|*/../*|*/..) die 'Invalid checksum path.' ;; esac
 KNOWN=0
 for REQUIRED in $REQUIRED_FILES; do
  [ "$REL" != "$REQUIRED" ] || KNOWN=1
 done
 [ "$KNOWN" -eq 1 ] || die "Unknown checksum entry: $REL"
 case "$SEEN" in *"
$REL
"*) die "Duplicate checksum entry: $REL" ;; esac
 SEEN="$SEEN$REL
"
 [ -f "$BASE/$REL" ] && [ ! -L "$BASE/$REL" ] || die "Missing or symlinked distribution file: $REL"
 ACTUAL=$("$BASE/sha256-file" "$BASE/$REL") || die "Digest failed: $REL"
 [ "$ACTUAL" = "$EXPECTED" ] || die "Checksum differs: $REL"
 COUNT=$((COUNT + 1))
done < "$BASE/SHA256SUMS"
[ "$COUNT" -gt 0 ] || die 'Empty checksum manifest.'
for REQUIRED in $REQUIRED_FILES; do
 case "$SEEN" in *"
$REQUIRED
"*) ;; *) die "Required checksum entry missing: $REQUIRED" ;; esac
done
echo 'checksums=ok'
echo 'native_write_attempted=no'
echo 'result=toolkit_self_test_ok'
