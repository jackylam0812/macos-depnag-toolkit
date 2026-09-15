#!/bin/sh
# Apply only a freshly prepared, UUID-bound native reminder-state payload.
set -eu
case "$0" in */*) SCRIPT_PARENT=${0%/*} ;; *) SCRIPT_PARENT=. ;; esac
BASE=$(CDPATH= cd -P -- "$SCRIPT_PARENT" && pwd -P) || exit 2
. "$BASE/lib.sh"
[ "$#" -eq 2 ] || fail 'Usage: APPLY_IN_RECOVERY.sh /Volumes/Data /absolute/session-directory'
run_transaction apply "$1" "$2"
