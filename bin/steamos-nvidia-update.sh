#!/bin/bash
# Standalone entry point; no installed steamos-nvidia-driver is required.
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DRIVER=latest
while [[ $# -gt 0 ]]; do
    case "$1" in
        --driver) DRIVER=${2:?version required}; shift ;;
        --list) exec /bin/bash "$HERE/standalone/payload.sh" --list ;;
        --status) exec /usr/bin/python3 -I "$HERE/standalone/driver.py" status ;;
        --preflight) exec /usr/bin/python3 -I "$HERE/standalone/driver.py" preflight ;;
        --rollback) exec /usr/bin/python3 -I "$HERE/standalone/driver.py" rollback ;;
        --force|-y|--yes) ;; # Kept for existing callers; all preparation is staged.
        -h|--help) echo 'Usage: sudo steamos-nvidia-update.sh --driver VERSION | --rollback | --status | --preflight'; exit 0 ;;
        *) echo "Unsupported option: $1" >&2; exit 2 ;;
    esac
    shift
done
exec /usr/bin/python3 -I "$HERE/standalone/driver.py" install "$DRIVER"
