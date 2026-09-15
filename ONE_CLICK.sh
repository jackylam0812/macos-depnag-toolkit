#!/bin/sh
# Explicit-scene wrapper. No arguments means help, never an installation.
# Only the existing core scripts may prepare or change native reminder state.
set -eu
umask 077
export LC_ALL=C

VERSION='v2.2.0'
LIVE_DATA='/System/Volumes/Data'
LIVE_WORK='/Users/Shared/mdm-nag-recovery'
VOLUMES_ROOT='/Volumes'
CURL='/usr/bin/curl'
TARGET_RELATIVE='private/var/db/ConfigurationProfiles/Settings/com.apple.mdm.depnag.plist'
GET_URL='https://raw.githubusercontent.com/jackylam0812/macos-depnag-toolkit/v2.2.0/GET_TOOLKIT.sh'
WORK=''
LOCK=''
LOCK_HELD=0
BOOTSTRAP=''
POINTER_TMP=''
CHILD_PID=''
LAUNCHING_CHILD=0
CHILD_SIGNAL_SENT=0
SIGNAL_RC=0

help() {
 /bin/cat <<'EOF'
Usage:
  sudo /bin/sh ONE_CLICK.sh prepare
  sudo /bin/sh ONE_CLICK.sh check
  /bin/sh ONE_CLICK.sh apply [/Volumes/Data]
  /bin/sh ONE_CLICK.sh rollback [/Volumes/Data [SESSION_NAME]]

prepare   Normal macOS: download/self-test and inspect state; no native write.
apply     Recovery only: fresh backup, apply, and verify; no automatic reboot.
check     Normal macOS: read-only state and boot-log verification.
rollback  Recovery only: restore the last successful apply, or named session.

Omitted Recovery DATA is selected only when exactly one valid volume exists.
SESSION_NAME is a basename, not a path; prepare never selects a rollback backup.
Existing caches must pass SELF_TEST. A stale .operation.lock is never removed
automatically: first confirm no operation is running, then rmdir that empty lock.
EOF
}

fail() { printf 'error=%s\n' "$1" >&2; exit "${2:-2}"; }

clean_absolute_path() {
 case "$1" in /*) ;; *) fail 'An absolute path is required.' ;; esac
 case "$1" in /|*'//'*|*'/./'*|*'/../'*|*/.|*/..)
  fail 'A path contains an empty, dot, or parent component.' ;;
 esac
 case "$1" in *'
'*|*''*) fail 'Newline and carriage-return paths are not supported.' ;; esac
}

no_symlink_chain() {
 clean_absolute_path "$1"
 CHAIN=$1
 while [ "$CHAIN" != / ]; do
  [ ! -L "$CHAIN" ] || fail "Symlink in path: $CHAIN"
  CHAIN=${CHAIN%/*}
  [ -n "$CHAIN" ] || CHAIN=/
 done
}

# Shared is intentionally public, but an existing root-run cache must not be
# replaceable by an unprivileged account before its SELF_TEST is executed.
trusted_directory() {
 no_symlink_chain "$1"
 [ -d "$1" ] || fail "Expected directory is missing: $1"
 OWNER_MODE=$(/usr/bin/stat -f '%u:%Lp' "$1") || fail 'Directory metadata read failed.'
 case "$OWNER_MODE" in 0:[0-7][0145][0145]) ;; *)
  fail "Directory must be root-owned and not group/world-writable: $1" ;;
 esac
}

trusted_file() {
 no_symlink_chain "$1"
 [ -f "$1" ] || fail "Expected file is missing: $1"
 OWNER_MODE=$(/usr/bin/stat -f '%u:%Lp' "$1") || fail 'File metadata read failed.'
 case "$OWNER_MODE" in 0:[0-7][0145][0145]) ;; *)
  fail "Cached runtime files must be root-owned and not group/world-writable: $1" ;;
 esac
}

inspect_volume() {
 no_symlink_chain "$1"
 [ -d "$1" ] || fail "Data volume is not mounted: $1"
 INFO=$(/usr/sbin/diskutil info -plist "$1") || fail 'Data volume lookup failed.'
 MOUNT=$(printf '%s\n' "$INFO" | /usr/bin/plutil -extract MountPoint raw -expect string -o - -) || fail 'Volume MountPoint lookup failed.'
 [ "$MOUNT" = "$1" ] || fail 'The Data path is not the actual volume mount point.'
 UUID=$(printf '%s\n' "$INFO" | /usr/bin/plutil -extract VolumeUUID raw -expect string -o - -) || fail 'Volume UUID lookup failed.'
 case "$UUID" in ????????-????-????-????-????????????) ;; *) fail 'Invalid volume UUID.' ;; esac
 case "$UUID" in *[!0123456789abcdefABCDEF-]*) fail 'Invalid volume UUID.' ;; esac
 no_symlink_chain "$1/$TARGET_RELATIVE"
 [ -f "$1/$TARGET_RELATIVE" ] || fail 'Native reminder state is missing; no state file will be invented.'
}

select_recovery_volume() {
 if [ -n "${1:-}" ]; then
  DATA=${1%/}
  case "$DATA" in "$VOLUMES_ROOT"/*) ;; *) fail 'Recovery DATA must be a mounted volume below /Volumes.' ;; esac
  inspect_volume "$DATA"
  return
 fi
 COUNT=0
 CANDIDATES=''
 MOUNTED=''
 for CANDIDATE in "$VOLUMES_ROOT"/*; do
  [ -d "$CANDIDATE" ] || continue
  MOUNTED="${MOUNTED}mounted_volume=$CANDIDATE
"
  if (inspect_volume "$CANDIDATE") >/dev/null 2>&1; then
   COUNT=$((COUNT + 1))
   DATA=$CANDIDATE
   CANDIDATES="${CANDIDATES}candidate_data_volume=$CANDIDATE
"
  fi
 done
 if [ "$COUNT" -ne 1 ]; then
  if [ "$COUNT" -eq 0 ]; then
   printf '%s\n' 'candidate_data_volume=none' >&2
   [ -z "$MOUNTED" ] || printf '%s' "$MOUNTED" >&2
  else
   printf '%s' "$CANDIDATES" >&2
  fi
  fail 'Select one mounted Data volume explicitly; unlock/mount it first if necessary.'
 fi
 inspect_volume "$DATA"
 printf 'selected_data_volume=%s\n' "$DATA"
}

# Forward one TERM to the direct child, then leave its own transaction cleanup
# uninterrupted. INT/HUP also use TERM for the child because noninteractive
# background shells may inherit SIGINT ignored. The wrapper returns the user's
# original signal status, except that core rollback-failed status 3 wins.
signal_child_once() {
 [ -n "$CHILD_PID" ] && [ "$CHILD_SIGNAL_SENT" -eq 0 ] || return 0
 CHILD_SIGNAL_SENT=1
 /bin/kill -TERM "$CHILD_PID" 2>/dev/null || :
}

on_signal() {
 [ "$SIGNAL_RC" -eq 0 ] || return 0
 SIGNAL_RC=$1
 if [ -n "$CHILD_PID" ]; then
  trap '' HUP INT TERM
  signal_child_once
 elif [ "$LAUNCHING_CHILD" -eq 0 ]; then
  exit "$SIGNAL_RC"
 fi
 # During the fork/PID-assignment window, defer exit and forwarding. Keep the
 # handlers (rather than SIG_IGN) until the child exists so an about-to-launch
 # core shell does not inherit TERM ignored and lose its rollback trap.
 return 0
}

run_child() {
 CHILD_SIGNAL_SENT=0
 LAUNCHING_CHILD=1
 "$@" &
 CHILD_PID=$!
 LAUNCHING_CHILD=0
 if [ "$SIGNAL_RC" -ne 0 ]; then
  trap '' HUP INT TERM
  signal_child_once
 fi
 CHILD_RC=0
 if wait "$CHILD_PID"; then CHILD_RC=0; else CHILD_RC=$?; fi
 if [ "$SIGNAL_RC" -ne 0 ]; then
  # wait may have returned because the wrapper caught a signal, not because
  # the core exited. Reap its final result before releasing the operation lock.
  while /bin/kill -0 "$CHILD_PID" 2>/dev/null; do
   if wait "$CHILD_PID"; then CHILD_RC=0; else CHILD_RC=$?; fi
  done
  if wait "$CHILD_PID" 2>/dev/null; then FINAL_CHILD_RC=0; else FINAL_CHILD_RC=$?; fi
  [ "$FINAL_CHILD_RC" -eq 127 ] || CHILD_RC=$FINAL_CHILD_RC
 fi
 CHILD_PID=''
 [ "$CHILD_RC" -ne 3 ] || return 3
 [ "$SIGNAL_RC" -eq 0 ] || return "$SIGNAL_RC"
 return "$CHILD_RC"
}

finish() {
 CODE=$?
 trap - EXIT
 trap '' HUP INT TERM
 if [ -n "$CHILD_PID" ]; then
  /bin/kill -TERM "$CHILD_PID" 2>/dev/null || :
  if wait "$CHILD_PID"; then FINISH_CHILD_RC=0; else FINISH_CHILD_RC=$?; fi
  [ "$FINISH_CHILD_RC" -ne 3 ] || CODE=3
 fi
 if [ -n "$POINTER_TMP" ]; then /bin/rm -f -- "$POINTER_TMP" || CODE=3; fi
 if [ -n "$BOOTSTRAP" ]; then /bin/rm -rf -- "$BOOTSTRAP" || CODE=3; fi
 if [ "$LOCK_HELD" -eq 1 ]; then /bin/rmdir -- "$LOCK" || CODE=3; fi
 exit "$CODE"
}

verify_cached_toolkit() {
 trusted_directory "$TOOLKIT"
 # A root-owned directory alone does not prevent a non-root file owner from
 # changing file bytes in place. Check every runtime executable/input before
 # running even SELF_TEST, and retain these checks after a fresh download.
 for TRUSTED_FILE in SELF_TEST.sh sha256-file SHA256SUMS VERSION lib.sh \
  PREPARE.sh APPLY_IN_RECOVERY.sh ROLLBACK.sh STATUS.sh VERIFY_AFTER_BOOT.sh; do
  trusted_file "$TOOLKIT/$TRUSTED_FILE"
 done
 if run_child /bin/sh "$TOOLKIT/SELF_TEST.sh"; then :; else exit "$?"; fi
 CACHE_VERSION=$(/bin/cat "$TOOLKIT/VERSION") || fail 'Cached toolkit VERSION read failed.'
 [ "$CACHE_VERSION" = "$VERSION" ] || fail 'The cached toolkit has a different VERSION; keep it and choose the matching release.'
}

ensure_toolkit() {
 if [ -e "$TOOLKIT" ] || [ -L "$TOOLKIT" ]; then
  verify_cached_toolkit
  printf '%s\n' 'toolkit_cache=verified'
  return
 fi
 BOOTSTRAP=$(/usr/bin/mktemp -d "$WORK/.bootstrap.XXXXXXXX") || fail 'Bootstrap directory creation failed.'
 if run_child "$CURL" --fail --location --silent --show-error \
  --proto '=https' --proto-redir '=https' --connect-timeout 30 --max-time 300 --retry 2 \
  --output "$BOOTSTRAP/GET_TOOLKIT.sh" "$GET_URL"; then :; else exit "$?"; fi
 [ -s "$BOOTSTRAP/GET_TOOLKIT.sh" ] || fail 'Downloaded GET_TOOLKIT.sh is empty.'
 if run_child /bin/sh -n "$BOOTSTRAP/GET_TOOLKIT.sh"; then :; else exit "$?"; fi
 if run_child /bin/sh "$BOOTSTRAP/GET_TOOLKIT.sh" "$TOOLKIT"; then :; else exit "$?"; fi
 verify_cached_toolkit
 /bin/rm -rf -- "$BOOTSTRAP" || fail 'Bootstrap cleanup failed.'
 BOOTSTRAP=''
 printf '%s\n' 'toolkit_cache=downloaded_and_verified'
}

valid_session_name() {
 case "$1" in ''|.|..|*[!ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-]*)
  fail 'SESSION_NAME must be a basename using only letters, digits, dot, underscore, or hyphen.' ;;
 esac
 [ "${#1}" -le 120 ] || fail 'SESSION_NAME is too long.'
}

read_last_applied_name() {
 no_symlink_chain "$POINTER"
 [ -f "$POINTER" ] || fail 'No successful apply is recorded; supply an existing session basename explicitly.'
 trusted_file "$POINTER"
 SESSION_NAME=''
 LINE_COUNT=0
 while IFS= read -r LINE || [ -n "$LINE" ]; do
  LINE_COUNT=$((LINE_COUNT + 1))
  [ "$LINE_COUNT" -eq 1 ] || fail 'Last-applied session pointer has multiple lines.'
  SESSION_NAME=$LINE
 done < "$POINTER"
 [ "$LINE_COUNT" -eq 1 ] || fail 'Last-applied session pointer is empty.'
 valid_session_name "$SESSION_NAME"
}

check_pointer_target() {
 no_symlink_chain "$POINTER"
 [ ! -e "$POINTER" ] || [ -f "$POINTER" ] || fail 'Last-applied pointer is not a regular file.'
 if [ -e "$POINTER" ]; then trusted_file "$POINTER"; fi
}

pointer_update_failed() {
 printf 'result=applied_pointer_update_failed\nnative_applied=yes\nsession=%s\n' "$SESSION" >&2
 fail "$1; use this retained session basename explicitly for rollback: $SESSION_NAME" 4
}

write_last_applied_name() {
 # The preflight already checked this path before PREPARE/APPLY. Recheck it
 # before publishing, and distinguish post-commit bookkeeping failures from a
 # zero-write preflight rejection. Do not claim the native apply was undone.
 if (check_pointer_target); then :; else pointer_update_failed 'Apply succeeded but the pointer path changed'; fi
 POINTER_TMP=$(/usr/bin/mktemp "$WORK/.last-applied.XXXXXXXX") || pointer_update_failed 'Apply succeeded but pointer creation failed'
 printf '%s\n' "$SESSION_NAME" > "$POINTER_TMP" || pointer_update_failed 'Apply succeeded but pointer write failed'
 /bin/mv -f -- "$POINTER_TMP" "$POINTER" || pointer_update_failed 'Apply succeeded but pointer update failed'
 POINTER_TMP=''
}

if [ "$#" -eq 0 ]; then help; exit 0; fi
SCENE=$1
case "$SCENE" in
 help|--help|-h) [ "$#" -eq 1 ] || fail 'Help takes no extra arguments.'; help; exit 0 ;;
 prepare|check) [ "$#" -eq 1 ] || fail 'prepare and check take no DATA or SESSION_NAME argument.' ;;
 apply) [ "$#" -le 2 ] || fail 'Usage: ONE_CLICK.sh apply [/Volumes/Data]' ;;
 rollback) [ "$#" -le 3 ] || fail 'Usage: ONE_CLICK.sh rollback [/Volumes/Data [SESSION_NAME]]' ;;
 *) help >&2; fail 'Unknown scene; choose prepare, apply, check, or rollback.' ;;
esac
[ "$(/usr/bin/id -u)" -eq 0 ] || fail 'Run prepare/check with sudo, or apply/rollback as root in Recovery.'
case "$SCENE" in
 prepare|check)
  [ -d "$LIVE_DATA" ] || fail 'prepare/check require the normally booted installed macOS.'
  DATA=$LIVE_DATA
  inspect_volume "$DATA"
  WORK=$LIVE_WORK
  ;;
 apply|rollback)
  [ ! -e "$LIVE_DATA" ] && [ ! -L "$LIVE_DATA" ] || fail 'apply/rollback run only in Recovery; the installed live Data volume is present.'
  select_recovery_volume "${2:-}"
  WORK="$DATA/Users/Shared/mdm-nag-recovery"
  ;;
esac
if [ "$SCENE" = rollback ] && [ "$#" -eq 3 ]; then
 valid_session_name "$3"
 REQUESTED_SESSION=$3
else
 REQUESTED_SESSION=''
fi

# All volume/path checks precede any mkdir or download.
no_symlink_chain "$WORK"
WORK_PARENT=${WORK%/*}
[ -d "$WORK_PARENT" ] || fail 'The installed Users/Shared directory is missing.'
if [ ! -e "$WORK" ]; then /bin/mkdir "$WORK" || fail 'Persistent work directory creation failed.'; fi
trusted_directory "$WORK"
LOCK="$WORK/.operation.lock"
no_symlink_chain "$LOCK"
if ! /bin/mkdir "$LOCK" 2>/dev/null; then
 printf 'operation_lock=%s\n' "$LOCK" >&2
 fail 'Another operation or a stale lock exists. Confirm no operation is running before removing the empty lock with rmdir.'
fi
LOCK_HELD=1
trap finish EXIT
trap 'on_signal 129' HUP
trap 'on_signal 130' INT
trap 'on_signal 143' TERM
TOOLKIT="$WORK/toolkit-$VERSION"
SESSIONS="$WORK/sessions"
POINTER="$WORK/LAST_APPLIED_SESSION_NAME.txt"
printf 'scene=%s\ndata_volume=%s\ndata_volume_uuid=%s\nwork_directory=%s\n' "$SCENE" "$DATA" "$UUID" "$WORK"
ensure_toolkit

case "$SCENE" in
 check)
  if run_child /bin/sh "$TOOLKIT/VERIFY_AFTER_BOOT.sh"; then exit 0; else exit "$?"; fi
  ;;
 prepare)
  if run_child /bin/sh "$TOOLKIT/STATUS.sh"; then STATE_RC=0; else STATE_RC=$?; fi
  [ "$STATE_RC" -le 1 ] || exit "$STATE_RC"
  if [ "$STATE_RC" -eq 0 ]; then
   printf '%s\n' 'result=already_disabled' 'native_write_attempted=no'
  else
   printf '%s\n' 'result=ready_for_recovery' 'native_write_attempted=no'
  fi
  exit 0
  ;;
 apply)
  if run_child /bin/sh "$TOOLKIT/STATUS.sh" --volume "$DATA"; then STATE_RC=0; else STATE_RC=$?; fi
  [ "$STATE_RC" -le 1 ] || exit "$STATE_RC"
  if [ "$STATE_RC" -eq 0 ]; then
   printf '%s\n' 'result=already_disabled' 'last_applied_session_pointer=unchanged' 'native_write_attempted=no'
   exit 0
  fi
  check_pointer_target
  no_symlink_chain "$SESSIONS"
  if [ ! -e "$SESSIONS" ]; then /bin/mkdir "$SESSIONS" || fail 'Session parent creation failed.'; fi
  trusted_directory "$SESSIONS"
  SESSION_NAME="install-$(/bin/date -u '+%Y%m%dT%H%M%SZ')-$$"
  valid_session_name "$SESSION_NAME"
  SESSION="$SESSIONS/$SESSION_NAME"
  printf 'session=%s\n' "$SESSION"
  if run_child /bin/sh "$TOOLKIT/PREPARE.sh" --volume "$DATA" "$SESSION"; then :; else exit "$?"; fi
  if run_child /bin/sh "$TOOLKIT/APPLY_IN_RECOVERY.sh" "$DATA" "$SESSION"; then :; else exit "$?"; fi
  write_last_applied_name
  printf 'result=one_click_applied\nlast_applied_session_name=%s\nsession=%s\n' "$SESSION_NAME" "$SESSION"
  printf '%s\n' 'next=Restart normally, then run ONE_CLICK.sh check.'
  ;;
 rollback)
  trusted_directory "$SESSIONS"
  if [ -n "$REQUESTED_SESSION" ]; then SESSION_NAME=$REQUESTED_SESSION; else read_last_applied_name; fi
  SESSION="$SESSIONS/$SESSION_NAME"
  trusted_directory "$SESSION"
  printf 'session=%s\n' "$SESSION"
  if run_child /bin/sh "$TOOLKIT/ROLLBACK.sh" "$DATA" "$SESSION"; then :; else exit "$?"; fi
  printf 'result=one_click_rolled_back\nretained_session=%s\nlast_applied_session_pointer=unchanged\n' "$SESSION"
  ;;
esac
