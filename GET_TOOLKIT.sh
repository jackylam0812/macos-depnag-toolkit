#!/bin/sh
# Fixed-release HTTPS downloader. This does not run PREPARE or APPLY.
# Initial trust is HTTPS, this GitHub account, and the fixed release tag.
# The downloaded SHA helper and digest are NOT an independent signing root.
set -eu
umask 077
export LC_ALL=C

VERSION='v2.2.0'
TOP='macos-depnag-toolkit'
ASSET="${TOP}-${VERSION}.tar.gz"
RELEASE="https://github.com/jackylam0812/${TOP}/releases/download/${VERSION}"
CURL='/usr/bin/curl'
TAR='/usr/bin/tar'
WORK=''
CREATED_DEST=0
COMMITTED=0

fail() { printf 'error=%s\n' "$1" >&2; exit 2; }

cleanup() {
 CODE=$?
 trap - EXIT
 trap '' HUP INT TERM
 if [ "$CREATED_DEST" -eq 1 ] && [ "$COMMITTED" -eq 0 ]; then
  /bin/rm -rf -- "$DEST" || CODE=3
 fi
 if [ -n "$WORK" ]; then
  /bin/rm -rf -- "$WORK" || CODE=3
 fi
 exit "$CODE"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

clean_absolute_path() {
 case "$1" in /*) ;; *) fail 'An absolute destination path is required.' ;; esac
 case "$1" in /|*'//'*|*'/./'*|*'/../'*|*/.|*/..)
  fail 'The destination contains an empty, dot, or parent component.' ;;
 esac
 case "$1" in *'
'*|*''*) fail 'Newline and carriage-return paths are not supported.' ;; esac
}

no_symlink_chain() {
 CHAIN=$1
 while [ "$CHAIN" != / ]; do
  [ ! -L "$CHAIN" ] || fail "Symlink in destination path: $CHAIN"
  CHAIN=${CHAIN%/*}
  [ -n "$CHAIN" ] || CHAIN=/
 done
}

valid_digest() {
 [ "${#1}" -eq 64 ] || return 1
 case "$1" in *[!0123456789abcdef]*) return 1 ;; esac
 return 0
}

download() {
 "$CURL" --fail --location --silent --show-error \
  --proto '=https' --proto-redir '=https' \
  --connect-timeout 30 --max-time 300 --retry 2 \
  --output "$2" "$1" || fail 'HTTPS download failed; no toolkit was installed.'
 [ -s "$2" ] || fail 'The downloaded file is empty.'
}

[ "$#" -eq 1 ] || fail 'Usage: /bin/sh GET_TOOLKIT.sh /absolute/new-directory'
DEST=$1
while [ "$DEST" != / ] && [ "${DEST%/}" != "$DEST" ]; do DEST=${DEST%/}; done
clean_absolute_path "$DEST"
no_symlink_chain "$DEST"
[ ! -e "$DEST" ] && [ ! -L "$DEST" ] || fail 'The destination already exists; choose a new directory.'
PARENT=${DEST%/*}
[ -n "$PARENT" ] || PARENT=/
[ -d "$PARENT" ] || fail 'The destination parent directory must already exist.'
WORK=$(/usr/bin/mktemp -d "$PARENT/.mdm-toolkit-download.XXXXXXXX") || fail 'Temporary directory creation failed.'
ARCHIVE="$WORK/$ASSET"
CHECKSUM="$WORK/$ASSET.sha256"
download "$RELEASE/$ASSET" "$ARCHIVE"
download "$RELEASE/$ASSET.sha256" "$CHECKSUM"

# Parse exactly one sha256sum-compatible entry for this fixed asset.
LINES=0
EXPECTED=''
while read -r HASH NAME EXTRA || [ -n "$HASH$NAME$EXTRA" ]; do
 LINES=$((LINES + 1))
 [ "$LINES" -eq 1 ] || fail 'The release checksum must contain exactly one entry.'
 valid_digest "$HASH" || fail 'Invalid release SHA-256 digest.'
 [ "$NAME" = "$ASSET" ] && [ -z "$EXTRA" ] || fail 'The release checksum names a different asset.'
 EXPECTED=$HASH
done < "$CHECKSUM"
[ "$LINES" -eq 1 ] || fail 'The release checksum is empty.'

# Inspect both names and entry types before extraction. No archive path may
# escape the fixed top directory; links, devices, and special modes are denied.
"$TAR" -tzf "$ARCHIVE" > "$WORK/names.txt" || fail 'Archive listing failed.'
"$TAR" -tvzf "$ARCHIVE" > "$WORK/types.txt" || fail 'Archive type listing failed.'
NAME_COUNT=$(/usr/bin/awk -v top="$TOP" '
 BEGIN { count=0; bad=0 }
 {
  name=$0
  if (name !~ /^[A-Za-z0-9_.\/-]+$/ || name ~ /\/\// ||
      name ~ /(^|\/)\.\.?(\/|$)/ ||
      (name != top && name != top "/" && index(name, top "/") != 1)) bad=1
  sub(/\/$/, "", name)
  if (seen[name]++) bad=1
  if (++count > 1000) bad=1
 }
 END { if (bad || !count) exit 2; print count }
' "$WORK/names.txt") || fail 'Archive contains an invalid, duplicate, or unsafe path.'
TYPE_COUNT=$(/usr/bin/awk '
 BEGIN { count=0; bad=0 }
 {
  kind=substr($0,1,1)
  if ((kind != "-" && kind != "d") || substr($0,1,10) ~ /[sStT]/) bad=1
  count++
 }
 END { if (bad || !count) exit 2; print count }
' "$WORK/types.txt") || fail 'Archive contains a link, special file, or special permission mode.'
[ "$NAME_COUNT" = "$TYPE_COUNT" ] || fail 'Archive listing counts do not agree.'

/bin/mkdir "$WORK/extracted" || fail 'Extraction directory creation failed.'
"$TAR" -xzf "$ARCHIVE" -C "$WORK/extracted" \
 --no-same-owner --no-same-permissions --no-xattrs --no-acls --no-fflags || fail 'Archive extraction failed.'
PAYLOAD="$WORK/extracted/$TOP"
[ -d "$PAYLOAD" ] && [ ! -L "$PAYLOAD" ] || fail 'The archive is missing its toolkit directory.'
[ -f "$PAYLOAD/sha256-file" ] && [ ! -L "$PAYLOAD/sha256-file" ] && [ -x "$PAYLOAD/sha256-file" ] || fail 'The bundled SHA-256 helper is missing or not executable.'

# This is the first execution of downloaded code; see the trust note above.
"$PAYLOAD/sha256-file" --self-test || fail 'The bundled SHA-256 helper self-test failed.'
ACTUAL=$("$PAYLOAD/sha256-file" "$ARCHIVE") || fail 'Archive hashing failed.'
valid_digest "$ACTUAL" || fail 'The bundled helper returned an invalid digest.'
[ "$ACTUAL" = "$EXPECTED" ] || fail 'The downloaded archive SHA-256 does not match the release checksum.'
[ -f "$PAYLOAD/SELF_TEST.sh" ] && [ ! -L "$PAYLOAD/SELF_TEST.sh" ] || fail 'SELF_TEST.sh is missing.'
/bin/sh "$PAYLOAD/SELF_TEST.sh" || fail 'The full toolkit self-test failed.'

# Reserve the new destination atomically; mv alone can silently nest a payload
# inside a directory created between an existence check and the final move.
no_symlink_chain "$DEST"
[ ! -e "$DEST" ] && [ ! -L "$DEST" ] || fail 'The destination appeared during download; nothing was overwritten.'
/bin/mkdir "$DEST" || fail 'The destination could not be reserved; nothing was overwritten.'
CREATED_DEST=1
for ITEM in "$PAYLOAD"/* "$PAYLOAD"/.[!.]* "$PAYLOAD"/..?*; do
 [ -e "$ITEM" ] || continue
 /bin/mv -- "$ITEM" "$DEST/" || fail 'Moving the verified toolkit failed.'
done
COMMITTED=1
printf 'archive_sha256=%s\ntoolkit_path=%s\nnative_write_attempted=no\nresult=toolkit_download_ok\n' "$ACTUAL" "$DEST"
