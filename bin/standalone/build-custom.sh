#!/bin/bash
# Build packages only. The controller holds the shared updater lock.
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$HERE/common.sh"
VERSION=${1:?version required}
WORK=${2:?private workspace required}
[[ "$VERSION" =~ ^[0-9]+(\.[0-9]+)+$ ]] || die "Invalid custom version"
[[ "$WORK" == /home/.decky-nvidia-update/prepare-custom-* && -d "$WORK/packages" ]] || die "Invalid build workspace"
python3 -I "$HERE/compat.py" "$VERSION"
build_payload "$HERE/custom-build.sh" "$WORK/build" --version "$VERSION" --output-dir "$WORK/packages" -y
