#!/bin/bash
# Root-only regression test. Uses disposable files/loop devices, never OS slots.
set -euo pipefail
backend=$(realpath "${1:?path to bin/standalone}")
[[ $EUID == 0 ]] || { echo 'Run as root' >&2; exit 1; }
if [[ ${2:-} != --isolated ]]; then
    exec unshare --mount --propagation private /bin/bash "$0" "$backend" --isolated
fi
source "$backend/common.sh"
base=$(mktemp -d /home/.decky-nvidia-repair.regression.XXXXXXXX)
leaked=; original=; clone=; varloop=
cleanup() {
    local rc=$?
    set +e
    if [[ -n $leaked && -r /proc/$leaked/cmdline ]] && grep -zFq "decky-regression-$base" "/proc/$leaked/cmdline"; then
        kill "$leaked"
    fi
    for path in "$base/etc" "$base/clone" "$base/root" "$base/var"; do
        mountpoint -q "$path" && umount -R "$path"
    done
    for device in "$original" "$clone" "$varloop"; do
        [[ -z $device ]] || losetup -d "$device"
    done
    # Keep failed fixtures available for diagnosis instead of deleting mounts.
    if [[ $rc == 0 ]]; then rm -rf -- "$base"; else echo "Fixture retained: $base" >&2; fi
    return "$rc"
}
trap cleanup EXIT
mkdir "$base/root" "$base/clone" "$base/var" "$base/etc"
truncate -s 128M "$base/var.img"
mkfs.ext4 -q -F "$base/var.img"
varloop=$(losetup --find --show "$base/var.img")
mount "$varloop" "$base/var"
cat > "$base/worker.sh" <<'WORKER'
#!/bin/bash
set -euo pipefail
[[ $1 == --workdir ]]; work=$2; target=$3; marker=$4; result=$5
truncate -s 128M "$work.img"
mkfs.ext4 -q -F "$work.img"
mkdir "$work"
mount -o loop "$work.img" "$work"
mkdir "$work/upper" "$work/ovlwork" "$work/merged"
mount -t overlay overlay -o "index=off,lowerdir=$target,upperdir=$work/upper,workdir=$work/ovlwork" "$work/merged"
mount --rbind /dev "$work/merged/dev"
mount --make-rslave "$work/merged/dev"
setsid /bin/bash -c 'cd "$1"; exec -a "$2" sleep 600' sh "$work/merged/dev" "$marker" >/dev/null 2>&1 &
echo $! > "$(dirname "$work")/helper.pid"
sleep 0.2
exit "$result"
WORKER
new_image() {
    truncate -s 512M "$base/root.img"
    mkfs.btrfs -q -f "$base/root.img"
    original=$(losetup --find --show "$base/root.img")
    mount "$original" "$base/root"
    mkdir -p "$base/root/etc" "$base/root/dev"
    echo lower > "$base/root/etc/example"
    fsid=$(blkid -s UUID -o value "$original")
}
detach_original() {
    sync -f "$base/root"
    umount "$base/root"
    losetup -d "$original"
}
clone_image() {
    cp --sparse=always --reflink=never "$base/root.img" "$base/clone.img"
    clone=$(losetup --find --show "$base/clone.img")
    mount "$clone" "$base/clone"
}
mount_overlay() {
    mount -t overlay overlay -o "index=on,lowerdir=$base/clone/etc,upperdir=$base/var/upper,workdir=$base/var/work" "$base/etc"
}
new_overlay() { mkdir "$base/var/upper" "$base/var/work"; }
reset_overlay() { rm -rf -- "$base/var/upper" "$base/var/work"; }
release_guard() { python3 -I "$backend/phase.py" image-released "$base/root.img" "$fsid"; }

# Reproduce the old mount-only worker leaving a helper behind. The duplicate
# Btrfs image gets a changing temporary FSID, breaking overlay origin remounts.
new_image
unshare --mount --propagation slave /bin/bash "$base/worker.sh" --workdir "$base/build" "$base/root" "decky-regression-$base" 0
leaked=$(cat "$base/helper.pid")
detach_original
python3 -I - "$backend" "$base/root.img" "$fsid" <<'PY'
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location('driver', pathlib.Path(sys.argv[1]) / 'driver.py')
d = importlib.util.module_from_spec(spec); spec.loader.exec_module(d)
try:
    d.require_image_released(sys.argv[2], sys.argv[3], timeout=0)
except ValueError as error:
    assert 'refusing to write' in str(error)
else:
    raise AssertionError('Leaked build image was accepted')
PY
clone_image
new_overlay
mount_overlay
echo preserved > "$base/etc/user-setting"
umount "$base/etc"
umount "$base/clone"
mount "$clone" "$base/clone"
if mount_overlay 2>"$base/expected-error.log"; then
    die 'Old lifecycle did not reproduce the overlay failure on this kernel'
fi
grep -q 'Stale file handle' "$base/expected-error.log"
echo 'PASS: reproduced old /etc Stale file handle and blocked the leaked image'
umount "$base/clone"
losetup -d "$clone"; clone=
kill "$leaked"; leaked=
release_guard
original=
for ((i=0; i<100; i++)); do [[ -z $(losetup -j "$base/build.img") ]] && break; sleep 0.1; done
[[ -z $(losetup -j "$base/build.img") ]]
rm -rf -- "$base/build" "$base/build.img"
reset_overlay

# Both successful and failed workers must reap helpers and release mounts.
for expected in 0 23; do
    new_image
    actual=0
    build_payload "$base/worker.sh" "$base/build" "$base/root" "decky-regression-$base" "$expected" || actual=$?
    [[ $actual == "$expected" ]]
    [[ ! -e "$base/build" && ! -e "$base/build.img" ]]
    detach_original
    release_guard
    original=
    clone_image
    new_overlay
    mount_overlay
    echo preserved > "$base/etc/user-setting"
    umount "$base/etc"
    # Simulate NVIDIA finalization changing lower /etc between Valve mounts.
    echo nvidia > "$base/clone/etc/nvidia.conf"
    umount "$base/clone"
    mount "$clone" "$base/clone"
    mount_overlay
    [[ $(cat "$base/etc/user-setting") == preserved ]]
    [[ $(cat "$base/etc/nvidia.conf") == nvidia ]]
    umount "$base/etc"
    umount "$base/clone"
    losetup -d "$clone"; clone=
    reset_overlay
    echo "PASS: isolated worker exit $expected; no leaked image; /etc remount preserved settings"
done
umount "$base/var"
losetup -d "$varloop"; varloop=
echo 'PASS: all build lifecycle regression checks'
