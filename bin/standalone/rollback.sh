#!/bin/bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$HERE/common.sh"
RECORD=${1:?transaction required}
phase=$(python3 -I "$HERE/phase.py" rollback-env "$RECORD")
eval "$phase"
[[ -b ${OTHER_DEV:-} && -n ${SOURCE:-} && -n ${TARGET:-} ]] || die "Invalid rollback record"
ROOT=$(mktemp -d /run/decky-nvidia-rollback.XXXXXX)
cleanup() { mountpoint -q "$ROOT" && umount "$ROOT"; rmdir "$ROOT"; }
trap cleanup EXIT
mount -o ro "$OTHER_DEV" "$ROOT"
python3 -I "$HERE/phase.py" verify-rollback "$RECORD" "$ROOT"
if [[ "$CURRENT" == "$SOURCE" ]]; then
    steamos-bootconf --image "$TARGET" config --no-create --set image-invalid 1 --set boot-requested-at 0 --set boot-attempts 0
else
    steamos-bootconf --image "$SOURCE" set-mode reboot
fi
sync -f /esp/SteamOS/conf
echo 'Previous system selected. Reboot to return to it.'
