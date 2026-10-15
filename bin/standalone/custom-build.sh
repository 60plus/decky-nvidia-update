#!/bin/bash
# Internal Arch recipe worker; called only by build-custom.sh in a PID namespace.
set -euo pipefail

log()  { printf '\e[1;35m[nvidia-build]\e[0m %s\n' "$*"; }
warn() { printf '\e[1;33m[warn]\e[0m %s\n' "$*" >&2; }
die()  { printf '\e[1;31m[fail]\e[0m %s\n' "$*" >&2; exit 1; }

TOTAL_STEPS=8
STEP=0
step() { STEP=$((STEP+1)); printf '\e[1;36m[%d/%d]\e[0m %s\n' "$STEP" "$TOTAL_STEPS" "$*"; }

VERSION=""
WORKDIR=""
OUTPUT_DIR=""
SKIP_SIG=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="${2:?}"; shift ;;
    --workdir) WORKDIR="${2:?}"; shift ;;
    --output-dir) OUTPUT_DIR="${2:?}"; shift ;;
    -y|--yes) ;;
    *) die "Unknown internal build option: $1" ;;
  esac
  shift
done
[[ $$ -eq 1 && ${DECKY_NVIDIA_BUILD_NS:-0} == 1 ]] || die "Build requires its private PID namespace"
[[ "$VERSION" =~ ^[0-9]+(\.[0-9]+)+$ ]] || die "Invalid NVIDIA version"
WORKDIR=$(readlink -m -- "$WORKDIR")
[[ "$WORKDIR" == /home/.decky-nvidia-update/prepare-custom-*/build ]] || die "Invalid build workspace"
[[ "$OUTPUT_DIR" == "${WORKDIR%/build}/packages" && -d "$OUTPUT_DIR" ]] || die "Invalid output directory"
[[ ! -e "$WORKDIR" && ! -L "$WORKDIR" && ! -e "$WORKDIR.img" && ! -L "$WORKDIR.img" ]] || die "Workspace already exists"

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

UPPER="$WORKDIR/upper"
OVLWORK="$WORKDIR/ovlwork"
MERGED="$WORKDIR/merged"
WORKIMG="$WORKDIR.img"   # ext4 loopback FILE on /home — own workdir, never
                          # shared with a concurrent steamos-nvidia-update.sh run.

# The parent reaps the namespace and removes backing files after loop teardown.
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
cp -a /etc/. "$MERGED/etc/"
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

# Export only the three packages consumed by the standalone controller.
step "Collecting custom driver packages"
for pkg in "${WANT_PKGS[@]}"; do
  file="${pkg}-${VERSION}-1-x86_64.pkg.tar.zst"
  [[ -s "$MERGED$OUT_DIR_CHROOT/$file" ]] || die "Missing package: $file"
  install -d -m 700 "$OUTPUT_DIR/$pkg"
  install -m 600 "$MERGED$OUT_DIR_CHROOT/$file" "$OUTPUT_DIR/$pkg/$file"
done
step "Package build complete"
