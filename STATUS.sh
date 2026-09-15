#!/bin/sh
# Read-only. Exit 0: Disabled=true; 1: not disabled; 2: inconclusive/error.
set -eu
BASE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
die() { printf '%s\n' "$1" >&2; echo 'native_write_attempted=no'; exit 2; }
MODE=live
DATA=/System/Volumes/Data
if [ "$#" -ne 0 ]; then
 [ "$#" -eq 2 ] && [ "$1" = --volume ] || die 'Usage: STATUS.sh [--volume /Volumes/Data]'
 MODE=volume; DATA=$2
 case "$DATA" in /Volumes/*) ;; *) die 'Expected a mounted volume below /Volumes.' ;; esac
fi
[ -d "$DATA" ] || die 'result=data_volume_missing'
TARGET="$DATA/private/var/db/ConfigurationProfiles/Settings/com.apple.mdm.depnag.plist"
echo "mode=$MODE"
echo "data_volume=$DATA"
[ -f "$TARGET" ] && [ ! -L "$TARGET" ] || die 'result=state_file_missing_or_symlink'
/usr/bin/plutil -lint "$TARGET" >/dev/null 2>&1 || die 'result=invalid_plist'
XML=$(/usr/bin/plutil -convert xml1 -o - "$TARGET") || die 'result=invalid_plist'
case "$XML" in
 *'<plist version="1.0">
<dict>'*|*'<plist version="1.0">
<dict/>'*) ;;
 *) die 'result=invalid_plist_root' ;;
esac
INFO=$(/usr/sbin/diskutil info -plist "$DATA") || die 'result=volume_info_failed'
UUID=$(printf '%s\n' "$INFO" | /usr/bin/plutil -extract VolumeUUID raw -o - -) || die 'result=volume_uuid_failed'
echo "data_volume_uuid=$UUID"
[ -x "$BASE/sha256-file" ] || die 'result=sha256_helper_missing_or_not_executable'
HASH=$("$BASE/sha256-file" "$TARGET") || die 'result=sha256_failed'
[ "${#HASH}" -eq 64 ] || die 'result=invalid_sha256_output'
case "$HASH" in *[!0123456789abcdef]*) die 'result=invalid_sha256_output' ;; esac
echo "native_sha256=$HASH"
if TYPE=$(/usr/bin/plutil -type Disabled "$TARGET" 2>/dev/null); then
 [ "$TYPE" = bool ] || die 'result=Disabled_has_non_boolean_type'
 VALUE=$(/usr/bin/plutil -extract Disabled raw -o - "$TARGET") || die 'result=Disabled_read_failed'
else
 VALUE=absent
fi
AFTER_HASH=$("$BASE/sha256-file" "$TARGET") || die 'result=sha256_recheck_failed'
[ "$AFTER_HASH" = "$HASH" ] || die 'result=state_changed_during_read'
echo "Disabled=$VALUE"
echo 'state_read_stable=yes'
echo 'native_write_attempted=no'
case "$VALUE" in
 true) echo 'result=disabled'; exit 0 ;;
 false|absent) echo 'result=not_disabled'; exit 1 ;;
 *) die 'result=invalid_Disabled_value' ;;
esac
