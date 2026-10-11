#!/bin/bash
# Build retained packages without changing either SteamOS slot.
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
VERSION=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="${2:?}"; shift ;;
    --list) exec python3 -I "$HERE/standalone/driver.py" custom-list ;;
    -y|--yes) ;;
    *) echo "Usage: $0 --version VERSION [-y] | --list" >&2; exit 1 ;;
  esac
  shift
done
exec python3 -I -u "$HERE/standalone/driver.py" build "$VERSION"
