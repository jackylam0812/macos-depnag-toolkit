#!/bin/sh
# Restore the exact bytes in this session; root/offline/UUID checks still apply.
set -eu
case "$0" in */*) SCRIPT_PARENT=${0%/*} ;; *) SCRIPT_PARENT=. ;; esac
BASE=$(CDPATH= cd -P -- "$SCRIPT_PARENT" && pwd -P) || exit 2
. "$BASE/lib.sh"
[ "$#" -eq 2 ] || fail 'Usage: ROLLBACK.sh /Volumes/Data /absolute/session-directory'
run_transaction restore "$1" "$2"
