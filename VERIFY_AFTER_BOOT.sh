#!/bin/sh
# Read-only evidence, not a promise about future OS/server changes.
set -eu
BASE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
die() { printf '%s\n' "$1" >&2; echo 'native_write_attempted=no'; exit 2; }
SINCE=''
if [ "$#" -ne 0 ]; then
 [ "$#" -eq 2 ] && [ "$1" = --since ] || die "Usage: VERIFY_AFTER_BOOT.sh [--since 'YYYY-MM-DD HH:MM:SS']"
 SINCE=$2
fi
[ -d /System/Volumes/Data ] || die 'Run this read-only verification in the normally booted installed macOS.'
if [ -z "$SINCE" ]; then
 BOOT=$(/usr/sbin/sysctl -n kern.boottime) || die 'result=boot_time_read_failed'
 EPOCH=${BOOT#*sec = }; EPOCH=${EPOCH%%,*}
 case "$EPOCH" in ''|*[!0-9]*) die 'result=boot_time_parse_failed' ;; esac
 SINCE=$(/bin/date -r "$EPOCH" '+%Y-%m-%d %H:%M:%S') || die 'result=boot_time_format_failed'
fi
echo "observed_from=$SINCE"
if STATE=$(/bin/sh "$BASE/STATUS.sh" 2>&1); then STATE_RC=0; else STATE_RC=$?; fi
printf '%s\n' "$STATE"
[ "$STATE_RC" -le 1 ] || die 'result=state_read_incomplete'
if LOGS=$(/usr/bin/log show --start "$SINCE" --style compact --info --debug --predicate 'process == "mdmclient" AND eventMessage CONTAINS[c] "DEPNag:"' 2>&1); then
 LOG_RC=0
else
 LOG_RC=$?
fi
echo '--- DEPNag log evidence ---'
printf '%s\n' "$LOGS"
[ "$LOG_RC" -eq 0 ] || die 'result=log_query_incomplete'
REMOVALS=0
INDICATORS=0
while IFS= read -r LINE; do
 case "$LINE" in *'DEPNag: Removing CoreFollowUp'*) REMOVALS=$((REMOVALS + 1)) ;; esac
 case "$LINE" in
  *'DEPNag: Scheduling timer'*|*'DEPNag: Next UI:'*|*'DEPNag: Launching:'*) INDICATORS=$((INDICATORS + 1)) ;;
 esac
done <<EOF
$LOGS
EOF
PS=$(/bin/ps -axo comm=) || die 'result=process_query_incomplete'
MINIBUDDY=0
SETUP_ASSISTANT=0
while IFS= read -r LINE; do
 case "$LINE" in */MiniBuddy|MiniBuddy) MINIBUDDY=$((MINIBUDDY + 1)) ;; esac
 case "$LINE" in *'/Setup Assistant'|'Setup Assistant') SETUP_ASSISTANT=$((SETUP_ASSISTANT + 1)) ;; esac
done <<EOF
$PS
EOF
echo "corefollowup_removal_log_count=$REMOVALS"
echo "schedule_or_launch_log_count=$INDICATORS"
echo "MiniBuddy_processes=$MINIBUDDY"
echo "Setup_Assistant_processes=$SETUP_ASSISTANT"
echo 'native_write_attempted=no'
if [ "$STATE_RC" -eq 0 ] && [ "$INDICATORS" -eq 0 ] && [ "$MINIBUDDY" -eq 0 ] && [ "$SETUP_ASSISTANT" -eq 0 ]; then
 if [ "$REMOVALS" -gt 0 ]; then
  echo 'result=disabled_with_matching_boot_log'
 else
  echo 'result=disabled_no_matching_boot_log_available'
 fi
 echo 'verification=point_in_time_only; observe the prior reminder time and a later normal reboot'
 exit 0
fi
echo 'result=review_required'
echo 'A record can predate a change; Setup Assistant can have unrelated uses. Inspect timestamps and UI context.'
exit 1
