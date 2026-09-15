#!/bin/sh
# Shared POSIX helpers. Callers set BASE to the physical toolkit directory.
# No test bypasses, system-policy changes, enrollment deletion, or network edits.

TARGET_RELATIVE='private/var/db/ConfigurationProfiles/Settings/com.apple.mdm.depnag.plist'
LIVE_DATA='/System/Volumes/Data'
LIVE_TARGET='/private/var/db/ConfigurationProfiles/Settings/com.apple.mdm.depnag.plist'

fail() { printf '%s\n' "error=$1" >&2; exit "${2:-2}"; }

valid_sha256() {
 [ "${#1}" -eq 64 ] || return 1
 case "$1" in *[!0123456789abcdef]*) return 1 ;; *) return 0 ;; esac
}

sha256_file() (
 [ -x "$BASE/sha256-file" ] || {
  printf '%s\n' 'error=Bundled sha256-file is missing or not executable.' >&2
  exit 2
 }
 DIGEST=$("$BASE/sha256-file" "$1") || {
  printf '%s\n' "error=SHA-256 provider failed: $1" >&2
  exit 2
 }
 valid_sha256 "$DIGEST" || {
  printf '%s\n' 'error=SHA-256 provider returned an invalid digest.' >&2
  exit 2
 }
 printf '%s\n' "$DIGEST"
)

assert_clean_absolute() (
 case "$1" in /*) ;; *) fail 'An absolute path is required.' ;; esac
 case "$1" in *'//'*|*'/./'*|*'/../'*|*/.|*/..) fail 'Path contains an ambiguous component.' ;; esac
 case "$1" in *'
'*|*''*) fail 'Newline or carriage-return in a path is not supported.' ;; esac
)

assert_no_symlink_chain() (
 CHAIN=$1
 assert_clean_absolute "$CHAIN" || exit 2
 while [ "$CHAIN" != / ]; do
  [ ! -L "$CHAIN" ] || fail "Symlink in path: $CHAIN"
  CHAIN=${CHAIN%/*}
  [ -n "$CHAIN" ] || CHAIN=/
 done
)

assert_session_location() (
 assert_clean_absolute "$1" || exit 2
 case "$1" in
  /private/var/db/ConfigurationProfiles|/private/var/db/ConfigurationProfiles/*|\
  /var/db/ConfigurationProfiles|/var/db/ConfigurationProfiles/*|\
  */private/var/db/ConfigurationProfiles|*/private/var/db/ConfigurationProfiles/*|\
  */var/db/ConfigurationProfiles|*/var/db/ConfigurationProfiles/*)
   fail 'Session directory must be outside ConfigurationProfiles.' ;;
 esac
 assert_no_symlink_chain "$1" || exit 2
)

manifest_get() {
 /usr/bin/plutil -extract "$1" raw -expect "${3:-string}" -o - "$2"
}

# Sets VOLUME_UUID after checking that the supplied path is the actual mount point.
inspect_volume() {
 assert_no_symlink_chain "$1" || return 2
 [ -d "$1" ] || fail 'Data volume is not mounted.'
 VOLUME_INFO=$(/usr/sbin/diskutil info -plist "$1") || fail 'Data volume lookup failed.'
 VOLUME_UUID=$(printf '%s\n' "$VOLUME_INFO" | /usr/bin/plutil -extract VolumeUUID raw -expect string -o - -) || fail 'Data Volume UUID lookup failed.'
 VOLUME_MOUNT=$(printf '%s\n' "$VOLUME_INFO" | /usr/bin/plutil -extract MountPoint raw -expect string -o - -) || fail 'Data volume mount-point lookup failed.'
 [ "$VOLUME_MOUNT" = "$1" ] || fail 'The supplied Data path is not the actual volume mount point.'
 case "$VOLUME_UUID" in ????????-????-????-????-????????????) ;; *) fail 'Invalid Data Volume UUID.' ;; esac
 case "$VOLUME_UUID" in *[!0123456789abcdefABCDEF-]*) fail 'Invalid Data Volume UUID.' ;; esac
}

# Deliberately not satisfied merely by running sudo in the normal OS.
require_offline_volume() {
 [ "$(/usr/bin/id -u)" -eq 0 ] || fail 'Use Terminal in macOS Recovery as root; no native write attempted.'
 case "$1" in /Volumes/*) ;; *) fail 'Expected an offline Data volume under /Volumes.' ;; esac
 assert_no_symlink_chain "$1" || return 2
 if [ -d "$LIVE_DATA" ] && [ "$1" -ef "$LIVE_DATA" ]; then
  fail 'Live Data volume detected. Use macOS Recovery; no native write attempted.'
 fi
 inspect_volume "$1"
}

# Sets NATIVE_DISABLED to absent, false, or true. Unknown fields are retained.
inspect_native() {
 assert_no_symlink_chain "$1" || return 2
 [ -f "$1" ] || fail 'Expected native state file is missing; no file will be created.'
 NATIVE_XML=$(/usr/bin/plutil -convert xml1 -o - "$1") || fail 'Native plist validation failed.'
 # plutil has no root -type keypath. Its canonical XML identifies the root tag.
 case "$NATIVE_XML" in
  *'<plist version="1.0">
<dict>'*|*'<plist version="1.0">
<dict/>'*) ;;
  *) fail 'Native plist root must be a dictionary.' ;;
 esac
 if NATIVE_TYPE=$(/usr/bin/plutil -type Disabled "$1" 2>/dev/null); then
  [ "$NATIVE_TYPE" = bool ] || fail 'Disabled exists but is not a boolean; no change made.'
  NATIVE_DISABLED=$(/usr/bin/plutil -extract Disabled raw -expect bool -o - "$1") || fail 'Disabled boolean read failed.'
  case "$NATIVE_DISABLED" in true|false) ;; *) fail 'Invalid Disabled boolean value.' ;; esac
 else
  case "$NATIVE_XML" in *'
	<key>Disabled</key>'*) fail 'Disabled type lookup failed.' ;; esac
  NATIVE_DISABLED=absent
 fi
 case "$NATIVE_XML" in *'
	<key>History</key>'*)
  NATIVE_HISTORY_TYPE=$(/usr/bin/plutil -type History "$1") || fail 'History type lookup failed.'
  [ "$NATIVE_HISTORY_TYPE" = array ] || fail 'History exists but is not an array; no change made.' ;;
 esac
}

canonical_modified_xml() (
 inspect_native "$1" || exit 2
 case "$NATIVE_DISABLED" in
  absent) printf '%s\n' "$NATIVE_XML" | /usr/bin/plutil -insert Disabled -bool true -o - - ;;
  false) printf '%s\n' "$NATIVE_XML" | /usr/bin/plutil -replace Disabled -bool true -o - - ;;
  true) printf '%s\n' "$NATIVE_XML" ;;
 esac
)

check_only_disabled_change() (
 EXPECTED_XML=$(canonical_modified_xml "$1") || exit 2
 inspect_native "$2" || exit 2
 [ "$NATIVE_DISABLED" = true ] || fail 'Modified payload Disabled must be true.'
 [ "$EXPECTED_XML" = "$NATIVE_XML" ] || fail 'Payload contains changes other than Disabled=true.'
)

# Sets SESSION, ORIGINAL, MODIFIED, MANIFEST, *_HASH, EXPECTED_UUID,
# ORIGINAL_DISABLED and ORIGINAL_METADATA. Manifests are data, never shell code.
load_session() {
 SESSION=${1%/}
 assert_session_location "$SESSION" || return 2
 [ -d "$SESSION" ] || fail 'Session directory is missing.'
 ORIGINAL="$SESSION/ORIGINAL_FILE.plist"
 MODIFIED="$SESSION/MODIFIED_FILE.plist"
 MANIFEST="$SESSION/MANIFEST.plist"
 for SESSION_FILE in "$ORIGINAL" "$MODIFIED" "$MANIFEST"; do
  assert_no_symlink_chain "$SESSION_FILE" || return 2
  [ -f "$SESSION_FILE" ] || fail "Session file is missing: $SESSION_FILE"
 done
 SCHEMA=$(manifest_get SchemaVersion "$MANIFEST" integer) || fail 'Manifest schema is missing or not an integer.'
 [ "$SCHEMA" = 2 ] || fail 'Unsupported session schema.'
 EXPECTED_UUID=$(manifest_get DataVolumeUUID "$MANIFEST") || fail 'Manifest DataVolumeUUID is missing or invalid.'
 EXPECTED_RELATIVE=$(manifest_get TargetRelativePath "$MANIFEST") || fail 'Manifest target path is missing or invalid.'
 [ "$EXPECTED_RELATIVE" = "$TARGET_RELATIVE" ] || fail 'Manifest target path differs from the native reminder-state file.'
 ORIGINAL_HASH=$(manifest_get OriginalSHA256 "$MANIFEST") || fail 'Manifest OriginalSHA256 is missing or invalid.'
 MODIFIED_HASH=$(manifest_get ModifiedSHA256 "$MANIFEST") || fail 'Manifest ModifiedSHA256 is missing or invalid.'
 valid_sha256 "$ORIGINAL_HASH" && valid_sha256 "$MODIFIED_HASH" || fail 'Manifest contains an invalid SHA-256 digest.'
 ORIGINAL_METADATA=$(manifest_get OriginalMetadata "$MANIFEST") || fail 'Manifest OriginalMetadata is missing or invalid.'
 [ -n "$ORIGINAL_METADATA" ] || fail 'Manifest metadata is empty.'
 ORIGINAL_DISABLED=$(manifest_get OriginalDisabled "$MANIFEST") || fail 'Manifest OriginalDisabled is missing or invalid.'
 case "$ORIGINAL_DISABLED" in absent|false|true) ;; *) fail 'Invalid manifest OriginalDisabled.' ;; esac
 PREPARED_FROM=$(manifest_get PreparedFrom "$MANIFEST") || fail 'Manifest PreparedFrom is missing or invalid.'
 case "$PREPARED_FROM" in live|offline) ;; *) fail 'Invalid manifest PreparedFrom.' ;; esac
 PREPARED_UTC=$(manifest_get PreparedUTC "$MANIFEST") || fail 'Manifest PreparedUTC is missing or invalid.'
 [ -n "$PREPARED_UTC" ] || fail 'Manifest preparation time is empty.'
 BACKUP_DIGEST=$(sha256_file "$ORIGINAL") || return 2
 [ "$BACKUP_DIGEST" = "$ORIGINAL_HASH" ] || fail 'Original backup integrity check failed; no native write attempted.'
 PAYLOAD_DIGEST=$(sha256_file "$MODIFIED") || return 2
 [ "$PAYLOAD_DIGEST" = "$MODIFIED_HASH" ] || fail 'Modified payload integrity check failed; no native write attempted.'
 inspect_native "$ORIGINAL" || return 2
 [ "$NATIVE_DISABLED" = "$ORIGINAL_DISABLED" ] || fail 'Manifest original Disabled state differs from the backup.'
 check_only_disabled_change "$ORIGINAL" "$MODIFIED" || return 2
}

print_result() {
 printf '%s\n' "result=$1" "sha256=$TO_HASH" "Disabled=$TO_DISABLED"
}

rollback_verified() {
 RB_CURRENT=$(sha256_file "$TARGET") || RB_CURRENT=unknown
 if [ "$RB_CURRENT" != "$FROM_HASH" ]; then
  RB_UNDO=$(sha256_file "$UNDO") || return 1
  [ "$RB_UNDO" = "$FROM_HASH" ] || return 1
  /bin/cat "$UNDO" > "$TARGET" || return 1
 fi
 RB_HASH=$(sha256_file "$TARGET") || return 1
 [ "$RB_HASH" = "$FROM_HASH" ] || return 1
 /usr/bin/plutil -lint "$TARGET" >/dev/null 2>&1 || return 1
 RB_ATTR=$(/usr/bin/stat -f '%u:%g:%Lp:%Sf' "$TARGET") || return 1
 [ "$RB_ATTR" = "$ATTR" ] || return 1
 return 0
}

finish_transaction() {
 CODE=$?
 trap - EXIT
 # Ignore further catchable termination signals while recovery is in progress.
 trap '' HUP INT TERM
 if [ "$TRANSACTION" -eq 1 ] && [ "$COMMITTED" -eq 0 ]; then
  if rollback_verified; then
   printf '%s\n' 'rollback=verified' "restored_sha256=$FROM_HASH" "restored_Disabled=$FROM_DISABLED" >&2
  else
   printf '%s\n' 'rollback=failed; inspect the native file and retained backup before reboot.' >&2
   exit 3
  fi
 fi
 exit "$CODE"
}

# Shared offline apply/restore transaction. Does not edit file attributes.
run_transaction() {
 MODE=$1
 DATA=${2%/}
 require_offline_volume "$DATA" || return 2
 load_session "$3" || return 2
 [ "$VOLUME_UUID" = "$EXPECTED_UUID" ] || fail 'Data Volume UUID differs; create a new session for this restored volume.'
 TARGET="$DATA/$TARGET_RELATIVE"
 inspect_native "$TARGET" || return 2
 if [ "$MODE" = apply ]; then
  FROM_HASH=$ORIGINAL_HASH; TO_HASH=$MODIFIED_HASH
  PAYLOAD=$MODIFIED; UNDO=$ORIGINAL
  FROM_DISABLED=$ORIGINAL_DISABLED; TO_DISABLED=true
 else
  FROM_HASH=$MODIFIED_HASH; TO_HASH=$ORIGINAL_HASH
  PAYLOAD=$ORIGINAL; UNDO=$MODIFIED
  FROM_DISABLED=true; TO_DISABLED=$ORIGINAL_DISABLED
 fi
 CURRENT=$(sha256_file "$TARGET") || return 2
 ATTR=$(/usr/bin/stat -f '%u:%g:%Lp:%Sf' "$TARGET") || fail 'Native file metadata read failed; no native write attempted.'
 [ "$ATTR" = "$ORIGINAL_METADATA" ] || fail 'Native metadata changed since preparation; no native write attempted.'
 if [ "$CURRENT" = "$TO_HASH" ]; then
  [ "$NATIVE_DISABLED" = "$TO_DISABLED" ] || fail 'Native Disabled state differs despite matching hash.'
  print_result "already_$MODE"
  printf '%s\n' 'native_write_attempted=no'
  return 0
 fi
 [ "$CURRENT" = "$FROM_HASH" ] || fail 'Native state changed since preparation. Create a fresh session; no native write attempted.'
 /usr/bin/plutil -lint "$PAYLOAD" >/dev/null || fail 'Payload plist validation failed; no native write attempted.'
 # Recheck after preflight to reduce stale-state races. Offline use is required.
 FINAL_PREWRITE_HASH=$(sha256_file "$TARGET") || return 2
 [ "$FINAL_PREWRITE_HASH" = "$FROM_HASH" ] || fail 'Native state changed during preflight; no native write attempted.'
 FINAL_PREWRITE_ATTR=$(/usr/bin/stat -f '%u:%g:%Lp:%Sf' "$TARGET") || fail 'Native metadata recheck failed; no native write attempted.'
 [ "$FINAL_PREWRITE_ATTR" = "$ATTR" ] || fail 'Native metadata changed during preflight; no native write attempted.'
 TRANSACTION=0
 COMMITTED=0
 trap finish_transaction EXIT
 trap 'exit 129' HUP
 trap 'exit 130' INT
 trap 'exit 143' TERM
 # The shell truncates before cat executes: mark possibly-written first.
 TRANSACTION=1
 /bin/cat "$PAYLOAD" > "$TARGET" || fail 'Native write failed; attempting verified rollback.' 1
 AFTER=$(sha256_file "$TARGET") || fail 'Post-write SHA-256 verification failed.' 1
 [ "$AFTER" = "$TO_HASH" ] || fail 'Post-write content verification failed.' 1
 /usr/bin/plutil -lint "$TARGET" >/dev/null || fail 'Post-write plist validation failed.' 1
 AFTER_ATTR=$(/usr/bin/stat -f '%u:%g:%Lp:%Sf' "$TARGET") || fail 'Post-write metadata read failed.' 1
 [ "$AFTER_ATTR" = "$ATTR" ] || fail 'File metadata differs after write.' 1
 if [ "$MODE" = apply ] || [ "$TO_DISABLED" != absent ]; then
  AFTER_DISABLED=$(/usr/bin/plutil -extract Disabled raw -expect bool -o - "$TARGET") || fail 'Post-write Disabled field read failed.' 1
  [ "$AFTER_DISABLED" = "$TO_DISABLED" ] || fail 'Post-write Disabled field differs.' 1
 fi
 COMMITTED=1
 TRANSACTION=0
 trap - EXIT HUP INT TERM
 print_result "$MODE"
 printf '%s\n' 'native_write_attempted=yes' 'metadata=owner/group/mode/flags-preserved'
}
