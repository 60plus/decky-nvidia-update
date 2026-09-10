#!/bin/bash
#
# steamos-nvidia-build.sh — build an NVIDIA driver version that Arch never
# packaged (so archive.archlinux.org has nothing to pin) directly from Arch's
# own packaging repos, on an ALREADY INSTALLED, running SteamOS system.
#
# This is a separate, explicit action from steamos-nvidia-update.sh: it never
# touches the running system or installs anything — it only produces a set of
# .pkg.tar.zst files and drops them in a persistent cache on /home. Once
# built, steamos-nvidia-update.sh's own pin_pkg() picks a matching cached
# build up automatically instead of hitting the Arch archive, so installing
# it afterwards is the exact same pipeline as any Arch-provided version.
#
# Same throwaway-overlay mechanism as steamos-nvidia-update.sh (own workdir,
# never shared with a concurrent run of it) — the real rootfs is never
# unlocked here at all, a strictly smaller blast radius than an install.
#
#   sudo ./steamos-nvidia-build.sh --version 615.71.09      build a version
#   sudo ./steamos-nvidia-build.sh --list                   list cached builds
#   sudo ./steamos-nvidia-build.sh --delete 615.71.09       remove one
#
# Options:
#   --version VER       Bare pkgver to build, e.g. 615.71.09 (no pkgrel/dash
#                        suffix — pkgrel is always reset to 1 for a build).
#   --list               Print cached custom-built versions, one per line, and exit.
#   --delete VER         Remove one cached custom build and exit.
#   -y, --yes            Don't ask for confirmation before building.
#   --skip-sigcheck      Disable pacman signature checks in the build chroot.
#   --workdir DIR        Build dir (default: /home/.steamos-nvidia-build-work).
#
# Requires meaningfully more scratch space than an update: two cloned
# packaging repos plus NVIDIA's own installer payloads plus build output.

set -euo pipefail

log()  { printf '\e[1;35m[nvidia-build]\e[0m %s\n' "$*"; }
warn() { printf '\e[1;33m[warn]\e[0m %s\n' "$*" >&2; }
die()  { printf '\e[1;31m[fail]\e[0m %s\n' "$*" >&2; exit 1; }

TOTAL_STEPS=8
STEP=0
step() { STEP=$((STEP+1)); printf '\e[1;36m[%d/%d]\e[0m %s\n' "$STEP" "$TOTAL_STEPS" "$*"; }

# Persistent cache, read by steamos-nvidia-update.sh's pin_pkg() — a
# subfolder under /home, never loose files directly at /home/.
CACHE_DIR="/home/.steamos-nvidia-custom-builds"

# ------------------------------------------------------------------- args
VERSION=""
LIST_ONLY=0
DELETE_VERSION=""
ASSUME_YES=0
SKIP_SIG=0
WORKDIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)       VERSION="${2:?--version needs an argument}"; shift ;;
    --list)          LIST_ONLY=1 ;;
    --delete)        DELETE_VERSION="${2:?--delete needs an argument}"; shift ;;
    -y|--yes)        ASSUME_YES=1 ;;
    --skip-sigcheck) SKIP_SIG=1 ;;
    --workdir)       WORKDIR="${2:?--workdir needs an argument}"; shift ;;
    -h|--help)       sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)               die "Unknown option: $1" ;;
  esac
  shift
done

# --------------------------------------------------------------- --list
if [[ $LIST_ONLY -eq 1 ]]; then
  for d in "$CACHE_DIR"/*/; do
    [[ -f "$d/build.json" ]] || continue
    basename "$d"
  done | sort -V
  exit 0
fi

# ------------------------------------------------------------- --delete
if [[ -n "$DELETE_VERSION" ]]; then
  TARGET="$CACHE_DIR/$DELETE_VERSION"
  [[ -d "$TARGET" ]] || die "No cached custom build for '$DELETE_VERSION'"
  rm -rf "$TARGET"
  log "Removed cached custom build $DELETE_VERSION"
  exit 0
fi

[[ -n "$VERSION" ]] || die "Pass --version VER, --list, or --delete VER (see --help)"
[[ "$VERSION" =~ ^[0-9]+(\.[0-9]+)*$ ]] \
  || die "--version takes a bare pkgver like 615.71.09 (no pkgrel/build suffix)"

# --------------------------------------------------------- step 1: preflight
step "Preflight checks"

[[ $EUID -eq 0 ]] || die "Run as root (sudo)."
grep -q '^ID=steamos' /etc/os-release 2>/dev/null || warn "This doesn't look like SteamOS — continuing anyway."

for tool in curl pacman chroot mount mkfs.ext4 tar zstd git useradd runuser findmnt df; do
  command -v "$tool" >/dev/null || die "Missing tool: $tool"
done

mountpoint -q /home || die "/home is not a separate mounted filesystem — refusing to continue (unexpected disk layout)"

# Meaningfully more than an update needs: two cloned repos, NVIDIA's own
# .run installer payloads (fetched fresh by each PKGBUILD), and the actual
# build output, on top of the same throwaway workdir image.
AVAIL_MB="$(df -m --output=avail /home | tail -1 | tr -d ' ')"
(( AVAIL_MB > 15000 )) || die "Not enough free space on /home: need ~15 GB, have ${AVAIL_MB} MB"

[[ -d "$CACHE_DIR/$VERSION" ]] && [[ $ASSUME_YES -ne 1 ]] && {
  read -rp "A custom build for $VERSION already exists in the cache — rebuild and replace it? [y/N] " ans
  [[ "$ans" =~ ^[Yy]$ ]] || { log "Aborted."; exit 0; }
}

CONFIRM_MSG="Build nvidia-utils/nvidia-open-dkms/lib32-nvidia-utils $VERSION from Arch's packaging repos now?\n\nThis only writes to a throwaway build area and the cache at $CACHE_DIR — the running system itself is never touched. Can take a long time (NVIDIA's installer payloads are hundreds of MB each)."
if [[ $ASSUME_YES -eq 0 ]]; then
  read -rp "$(printf '%b' "${CONFIRM_MSG//\\n/\n}") [y/N] " ans
  [[ "$ans" =~ ^[Yy]$ ]] || { log "Aborted."; exit 0; }
fi

[[ -n "$WORKDIR" ]] || WORKDIR="/home/.steamos-nvidia-build-work"
UPPER="$WORKDIR/upper"
OVLWORK="$WORKDIR/ovlwork"
MERGED="$WORKDIR/merged"
WORKIMG="$WORKDIR.img"   # ext4 loopback FILE on /home — own workdir, never
                          # shared with a concurrent steamos-nvidia-update.sh run.

# ---------------------------------------------------------------- cleanup
cleanup() {
  set +e
  for m in "$MERGED"/dev/pts "$MERGED"/dev "$MERGED"/sys "$MERGED"/proc "$MERGED" "$WORKDIR"; do
    mountpoint -q "$m" 2>/dev/null && { umount -R "$m" 2>/dev/null || umount -Rl "$m" 2>/dev/null; }
  done
  rm -rf "$WORKDIR" "$WORKIMG"
}
trap cleanup EXIT

rm -rf "$WORKDIR" "$WORKIMG"
mkdir -p "$(dirname "$WORKDIR")"
truncate -s 12G "$WORKIMG"
mkfs.ext4 -q -F "$WORKIMG"
mkdir -p "$WORKDIR"
mount -o loop "$WORKIMG" "$WORKDIR"
mkdir -p "$UPPER" "$OVLWORK" "$MERGED"

# ----------------------------------------------- step 2: build environment
step "Setting up the build environment (overlay chroot)"

# lowerdir is the LIVE running root — read-only access, never written here.
# All build residue lands in $UPPER; the real system is never touched at
# all by this script (unlike steamos-nvidia-update.sh, there is no later
# install-copy step). /home is its own mount, so it is NOT pulled in by
# lowerdir=/ — a builduser homedir created below never touches the real
# /home/deck, even in principle.
mount -t overlay overlay -o "index=off,lowerdir=/,upperdir=$UPPER,workdir=$OVLWORK" "$MERGED"
mount -t proc proc "$MERGED/proc"
mount --rbind /sys "$MERGED/sys";  mount --make-rslave "$MERGED/sys"
mount --rbind /dev "$MERGED/dev";  mount --make-rslave "$MERGED/dev"
rm -f "$MERGED/etc/resolv.conf"
cp -L /etc/resolv.conf "$MERGED/etc/resolv.conf"

in_chroot() { chroot "$MERGED" /bin/bash -c "$*"; }

# --overwrite='*': the overlay's lowerdir is the LIVE system, which already
# has a compiler toolchain (gcc, make, patch, libisl, libmpc, ...) — pacman
# refuses to clobber those untracked-by-its-own-db files by default. Exact
# same fix, same reasoning, as steamos-nvidia-update.sh's own PACOPTS: safe
# here since we're inside a throwaway overlay; nothing real is touched.
PACOPTS="--noconfirm --needed --overwrite='*'"
PACCONF="/etc/pacman.conf"
if [[ $SKIP_SIG -eq 1 ]]; then
  sed 's/^SigLevel.*/SigLevel = Never/' "$MERGED/etc/pacman.conf" > "$MERGED/tmp/pacman-nosig.conf"
  PACCONF="/tmp/pacman-nosig.conf"
  warn "pacman signature verification DISABLED for the build"
fi

[[ -d "$MERGED/etc/pacman.d/gnupg/private-keys-v1.d" ]] \
  || in_chroot "pacman-key --init && pacman-key --populate" \
  || die "Keyring init failed — rerun with --skip-sigcheck if you accept unsigned installs"

log "Installing base-devel (fakeroot, patch, git, pacman-contrib...)"
in_chroot "pacman --config $PACCONF -Sy"
in_chroot "pacman --config $PACCONF -S $PACOPTS base-devel git pacman-contrib" \
  || die "Could not install base-devel (see pacman output above for the exact reason — a signature/keyring error can be worked around with --skip-sigcheck, but check the actual message first)."

# ------------------------------------------- step 3: multilib availability
step "Checking multilib availability (needed for lib32-nvidia-utils)"

# lib32-nvidia-utils's own dependencies (lib32-glibc, ...) live in the
# multilib repo — SteamOS/Holo may not enable it by default. The install
# pipeline requires all three packages at matching versions, so a build
# missing this one isn't installable anyway: fail fast and clearly instead
# of succeeding on two packages and only discovering this on the third.
# Substring match, not exact: SteamOS/Holo's own pacman.conf names its
# mirrored repos with a "-main" suffix (multilib-main, core-main, ...), not
# vanilla Arch's plain names — confirmed live on a real Deck.
in_chroot "pacman-conf --repo-list" | grep -q multilib \
  || die "The 'multilib' repository isn't enabled on this system's pacman.conf — lib32-nvidia-utils (needed for 32-bit game/Proton support) can't be built without it. Enable [multilib] in /etc/pacman.conf first."

# ------------------------------------------------- step 4: clone packaging
step "Cloning Arch packaging repositories"

# Only two actual PKGBUILD repos exist — confirmed live against
# gitlab.archlinux.org (a naive third "nvidia-open-dkms" repo 401s, it
# doesn't exist as its own repo). nvidia-utils' own PKGBUILD is a split
# package: pkgname=('nvidia-utils' 'opencl-nvidia' 'nvidia-open-dkms') — a
# single build of this repo produces all three, nvidia-open-dkms included.
REPOS=(nvidia-utils lib32-nvidia-utils)
# The actual package names we need in the cache afterwards, for the final
# verification step below — distinct from REPOS since nvidia-utils' single
# repo yields multiple package names.
WANT_PKGS=(nvidia-utils nvidia-open-dkms lib32-nvidia-utils)
BUILD_ROOT="/home/builduser/build"
in_chroot "mkdir -p $BUILD_ROOT"
for repo in "${REPOS[@]}"; do
  log "  $repo"
  in_chroot "git clone --depth 1 https://gitlab.archlinux.org/archlinux/packaging/packages/$repo.git $BUILD_ROOT/$repo" \
    || die "Could not clone the $repo packaging repo — check network connectivity"
done

# Throwaway, unprivileged build user — makepkg refuses to run as root.
# Exists only inside this chroot's overlay upper layer (see the /home note
# above); a passwordless-sudo scope confined to this throwaway chroot is
# what lets plain `makepkg -s` resolve build dependencies itself, the
# standard Arch way, without hand-parsing each PKGBUILD's dependency arrays.
in_chroot "id -u builduser >/dev/null 2>&1 || useradd -m builduser"
echo 'builduser ALL=(ALL) NOPASSWD: ALL' > "$MERGED/etc/sudoers.d/builduser"
chmod 440 "$MERGED/etc/sudoers.d/builduser"
in_chroot "chown -R builduser:builduser $BUILD_ROOT"

# ------------------------------------------- step 5: patch pkgver, checksums
step "Patching pkgver=$VERSION and refreshing checksums"

for repo in "${REPOS[@]}"; do
  in_chroot "cd $BUILD_ROOT/$repo && sed -i 's/^pkgver=.*/pkgver=$VERSION/; s/^pkgrel=.*/pkgrel=1/' PKGBUILD"
done

# --------------------------------------------------------- step 6: build
step "Building the driver packages (this can take a long time)"

OUT_DIR_CHROOT="/tmp/build-out"
mkdir -p "$MERGED$OUT_DIR_CHROOT"
# makepkg runs as builduser (below), which needs write access here — this
# dir was just created from the host side as root.
in_chroot "chown builduser:builduser $OUT_DIR_CHROOT"
for repo in "${REPOS[@]}"; do
  log "Building $repo (downloads NVIDIA's own installer payload — can be hundreds of MB)"
  in_chroot "cd $BUILD_ROOT/$repo && runuser -u builduser -- bash -c 'updpkgsums && PKGDEST=$OUT_DIR_CHROOT makepkg -s --noconfirm'" \
    || die "Building $repo failed (see output above) — the Arch packaging repo's current PKGBUILD may not support version $VERSION"
done

compgen -G "$MERGED$OUT_DIR_CHROOT/*.pkg.tar.zst" >/dev/null \
  || die "Build finished but produced no packages — something went wrong silently"

# ---------------------------------------------- step 7: copy to the cache
step "Copying build output to the cache"

CACHE_TARGET="$CACHE_DIR/$VERSION"
rm -rf "$CACHE_TARGET"
mkdir -p "$CACHE_TARGET"
cp "$MERGED$OUT_DIR_CHROOT"/*.pkg.tar.zst "$CACHE_TARGET/"

PACKAGES_JSON="$(cd "$CACHE_TARGET" && printf '"%s"\n' *.pkg.tar.zst | paste -sd, -)"
cat > "$CACHE_TARGET/build.json" <<EOF
{
  "version": "$VERSION",
  "built_at": "$(date -Iseconds)",
  "packages": [$PACKAGES_JSON]
}
EOF

log "Cached: $CACHE_TARGET"

# ----------------------------------------------- step 8: final verification
step "Final verification"

for pkg in "${WANT_PKGS[@]}"; do
  compgen -G "$CACHE_TARGET/${pkg}-${VERSION}-*-x86_64.pkg.tar.zst" >/dev/null \
    || die "Post-build check failed: no cached $pkg package for $VERSION"
done
log "All checks passed."

log "DONE — $VERSION built and cached at $CACHE_TARGET."
log "It will now show up in steamos-nvidia-update.sh's version list, ready to install."
