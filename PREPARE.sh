#!/bin/sh
# Create a fresh per-volume session. This script only reads the native state.
set -eu
case "$0" in */*) SCRIPT_PARENT=${0%/*} ;; *) SCRIPT_PARENT=. ;; esac
BASE=$(CDPATH= cd -P -- "$SCRIPT_PARENT" && pwd -P) || exit 2
. "$BASE/lib.sh"
umask 077

case "${1:-}" in
 --live)
  [ "$#" -eq 2 ] || fail 'Usage: PREPARE.sh --live /absolute/new-session-directory'
  MODE=live; DATA=$LIVE_DATA; TARGET=$LIVE_TARGET; SESSION=${2%/}
  inspect_volume "$DATA" || exit 2
  ;;
 --volume)
  [ "$#" -eq 3 ] || fail 'Usage: PREPARE.sh --volume /Volumes/Data /absolute/new-session-directory'
  MODE=offline; DATA=${2%/}; SESSION=${3%/}
  require_offline_volume "$DATA" || exit 2
  TARGET="$DATA/$TARGET_RELATIVE"
  ;;
 *) fail 'Usage: PREPARE.sh --live SESSION | PREPARE.sh --volume /Volumes/Data SESSION' ;;
esac
assert_session_location "$SESSION" || exit 2
[ ! -e "$SESSION" ] && [ ! -L "$SESSION" ] || fail 'Session path already exists; choose a new directory.'
SESSION_PARENT=${SESSION%/*}
[ -n "$SESSION_PARENT" ] || SESSION_PARENT=/
[ -d "$SESSION_PARENT" ] || fail 'Session parent directory does not exist.'
inspect_native "$TARGET" || exit 2
ORIGINAL_DISABLED=$NATIVE_DISABLED
SOURCE_HASH=$(sha256_file "$TARGET") || exit 2
SOURCE_ATTR=$(/usr/bin/stat -f '%u:%g:%Lp:%Sf' "$TARGET") || fail 'Native metadata read failed.'
/bin/mkdir "$SESSION" || fail 'Exclusive session creation failed.'
PREPARE_COMPLETE=0
finish_prepare() {
 PREPARE_CODE=$?
 trap - EXIT HUP INT TERM
 if [ "$PREPARE_COMPLETE" -eq 0 ]; then
  printf '%s\n' "incomplete_session=$SESSION" 'native_write_attempted=no' >&2
 fi
 exit "$PREPARE_CODE"
}
trap finish_prepare EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
ORIGINAL="$SESSION/ORIGINAL_FILE.plist"
MODIFIED="$SESSION/MODIFIED_FILE.plist"
MANIFEST="$SESSION/MANIFEST.plist"
/bin/cat "$TARGET" > "$ORIGINAL" || fail 'Native snapshot read failed.'
SNAPSHOT_HASH=$(sha256_file "$ORIGINAL") || exit 2
[ "$SNAPSHOT_HASH" = "$SOURCE_HASH" ] || fail 'Native state changed while reading the snapshot; choose a fresh session.'
inspect_native "$ORIGINAL" || exit 2
[ "$NATIVE_DISABLED" = "$ORIGINAL_DISABLED" ] || fail 'Native Disabled state changed while preparing.'
/bin/cat "$ORIGINAL" > "$MODIFIED" || fail 'Payload copy failed.'
case "$ORIGINAL_DISABLED" in
 absent) /usr/bin/plutil -insert Disabled -bool true "$MODIFIED" || fail 'Disabled insertion failed.' ;;
 false) /usr/bin/plutil -replace Disabled -bool true "$MODIFIED" || fail 'Disabled replacement failed.' ;;
 true) : ;; # Preserve identical bytes for an already-disabled source.
esac
check_only_disabled_change "$ORIGINAL" "$MODIFIED" || exit 2
MODIFIED_HASH=$(sha256_file "$MODIFIED") || exit 2
/usr/bin/plutil -convert xml1 -o "$SESSION/.original.xml" "$ORIGINAL" || fail 'Original XML normalization failed.'
/usr/bin/plutil -convert xml1 -o "$SESSION/.modified.xml" "$MODIFIED" || fail 'Modified XML normalization failed.'
DIFF_CODE=0
/usr/bin/diff -u "$SESSION/.original.xml" "$SESSION/.modified.xml" > "$SESSION/DIFF_FILE.diff" || DIFF_CODE=$?
[ "$DIFF_CODE" -le 1 ] || fail 'Diff generation failed.'
/bin/rm "$SESSION/.original.xml" "$SESSION/.modified.xml" || fail 'Temporary XML cleanup failed.'
AFTER_SOURCE_HASH=$(sha256_file "$TARGET") || exit 2
AFTER_SOURCE_ATTR=$(/usr/bin/stat -f '%u:%g:%Lp:%Sf' "$TARGET") || fail 'Native metadata recheck failed.'
[ "$AFTER_SOURCE_HASH" = "$SOURCE_HASH" ] || fail 'Native state changed during preparation; choose a fresh session.'
[ "$AFTER_SOURCE_ATTR" = "$SOURCE_ATTR" ] || fail 'Native metadata changed during preparation; choose a fresh session.'
PREPARED_AT=$(/bin/date -u '+%Y-%m-%dT%H:%M:%SZ') || fail 'Preparation clock read failed.'
/usr/bin/plutil -create xml1 "$MANIFEST" || fail 'Manifest creation failed.'
/usr/bin/plutil -insert SchemaVersion -integer 2 "$MANIFEST" || fail 'Manifest schema write failed.'
/usr/bin/plutil -insert DataVolumeUUID -string "$VOLUME_UUID" "$MANIFEST" || fail 'Manifest UUID write failed.'
/usr/bin/plutil -insert TargetRelativePath -string "$TARGET_RELATIVE" "$MANIFEST" || fail 'Manifest target write failed.'
/usr/bin/plutil -insert OriginalSHA256 -string "$SOURCE_HASH" "$MANIFEST" || fail 'Manifest original hash write failed.'
/usr/bin/plutil -insert ModifiedSHA256 -string "$MODIFIED_HASH" "$MANIFEST" || fail 'Manifest modified hash write failed.'
/usr/bin/plutil -insert OriginalMetadata -string "$SOURCE_ATTR" "$MANIFEST" || fail 'Manifest metadata write failed.'
/usr/bin/plutil -insert OriginalDisabled -string "$ORIGINAL_DISABLED" "$MANIFEST" || fail 'Manifest source state write failed.'
/usr/bin/plutil -insert PreparedFrom -string "$MODE" "$MANIFEST" || fail 'Manifest source mode write failed.'
/usr/bin/plutil -insert PreparedUTC -string "$PREPARED_AT" "$MANIFEST" || fail 'Manifest timestamp write failed.'
{
 printf '%s\n' 'MDM reminder-state session; native state was not changed by preparation.'
 printf '%s\n' "prepared_utc=$PREPARED_AT" "prepared_from=$MODE" "data_volume_uuid=$VOLUME_UUID" "native_path=$TARGET"
 printf '%s\n' 'changed_field=Disabled' "baseline_Disabled=$ORIGINAL_DISABLED" 'modified_Disabled=true'
 printf '%s\n' "original_sha256=$SOURCE_HASH" "modified_sha256=$MODIFIED_HASH" "metadata=$SOURCE_ATTR"
 printf '%s\n' 'snapshot_stable=yes' 'other_fields_preserved=yes' 'native_write_attempted=no'
 printf '%s\n' "ORIGINAL_FILE=$ORIGINAL" "MODIFIED_FILE=$MODIFIED" "DIFF_FILE=$SESSION/DIFF_FILE.diff" "MANIFEST=$MANIFEST"
 printf '%s\n' 'APPLY and ROLLBACK results are emitted to the invoking terminal; retain that output.'
 printf '%s\n' 'This session is bound to this volume and exact bytes, not reusable after erase or changed native state.'
} > "$SESSION/VERIFICATION.txt"
load_session "$SESSION" || exit 2
PREPARE_COMPLETE=1
trap - EXIT HUP INT TERM
printf '%s\n' 'result=prepared' "session=$SESSION" "data_volume_uuid=$VOLUME_UUID"
printf '%s\n' "original_sha256=$SOURCE_HASH" "modified_sha256=$MODIFIED_HASH"
printf '%s\n' "original_Disabled=$ORIGINAL_DISABLED" 'target_Disabled=true' 'native_write_attempted=no'
