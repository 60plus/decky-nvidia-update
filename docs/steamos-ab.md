# SteamOS A/B driver preparation

A Decky Loader interface for preparing an NVIDIA driver in SteamOS's inactive
A/B system slot. The ZIP includes its controller, package builder, slot staging,
rollback and OS-update repair code. **No separately installed
`steamos-nvidia-driver` or NVIDIA installer is required.**

This edition adapts the A/B method used by the desktop driver manager in
steamos-nvidia-installer. It replaces the original plugin's live-root copy.
The UI and driver version discovery originate in moi952/decky-nvidia-update.

## Changes from the original plugin

- Replace live-root driver installation with preparation of the inactive SteamOS
  A/B slot. Compressed staging avoids requiring an enlarged 8 GiB root partition.
- Bundle the controller, isolated package builder, rollback and OS repair runtime.
  Work continues in a systemd service when the Decky panel closes.
- Preserve the recognized native steamos-nvidia update hook and driver settings.
- Offer original Arch packages and additional NVIDIA releases, with source/channel
  labels, counts and a scrollable version picker that fits the SteamOS panel.

## Storage and supported systems

- An installed SteamOS A/B layout with Btrfs root filesystems and separate /home.
  Both slot sets must be on the same disk; ambiguous or mounted targets are refused.
- Modern `steamos-bootconf`, `steamos-chroot`, RAUC and the recognized Valve
  post-install finalization sequence. Unknown layouts/scripts are refused.
- NVIDIA GeForce GTX 16 or RTX GPUs with a documented match in the selected
  NVIDIA release. Legacy proprietary-module GPUs are outside this edition's scope.
- Decky Loader with root backend support, Python 3.10+ and SteamOS's normal build,
  filesystem and update utilities. Missing prerequisites are reported.
- Free space on /home: **the inactive root partition's size plus 12 GiB**
  (about 17 GiB for a 5 GiB root slot). Temporary images and build files live here.
- **No fixed 8 GiB root requirement and no partition or filesystem resize.**
  The root image has the existing inactive partition's size. Btrfs Zstd compression,
  removal of obsolete driver files and actual post-write space checks determine
  whether the chosen system/driver fits. At least 256 MiB must remain on root.
  This is not a promise that every future driver fits every 5 GiB installation.

The version list also includes NVIDIA releases from 590 onward that Arch has not
packaged. They are marked **Needs build**. Selecting one builds the three required
packages from Arch packaging recipes using NVIDIA's downloads, then prepares the
inactive slot in the same systemd job. Closing Decky does not interrupt this chain.
Old releases may be incompatible with current recipes, the GPU or SteamOS glibc;
these checks fail before writing the inactive slot. A catalog entry is not a
recommendation; NVIDIA's historical "Recommended" label is omitted from the list.

**Recommended (Arch)** filters all original Arch packages. NVIDIA-source entries
use NFB/Beta when NVIDIA provides that classification, otherwise **Other NVIDIA**.
Every option names its source (`Arch`, `Arch testing`, or `NVIDIA`); NVIDIA builds
also show `NFB`, `Beta` or `Other` and `Needs build`/`Compiled`. The filters show
counts of distinct versions. Versions already packaged by Arch appear only in
Recommended (Arch), so an NVIDIA channel can legitimately have zero entries.
Build status is a label, not a second filter hiding the chosen channel.
The update-channel
setting uses the same classification, so historical NVIDIA labels cannot select
an older driver as the recommended Arch update.

Custom packages are retained under `/home/.decky-nvidia-update/custom`, owned by
root, with package metadata and SHA-256 checks. Completed builds are never
overwritten or removed through the plugin, because either OS slot can need them
for a later SteamOS update or rollback. Keep this directory when removing Decky.
They use /home space, not additional root space. Missing or modified retained
packages stop preparation instead of silently substituting an Arch version.

## How preparation works

1. Validate the booted root, both A/B slot sets, updater state, supported Valve
   migration script and available /home space. Share the desktop updater's lock.
2. Copy the active root into a fresh compressed Btrfs image on /home with a new
   filesystem UUID, sized to the inactive root partition.
3. Resolve matching driver packages, check the GPU and glibc requirements, and
   compile against the exact target kernel in a disposable overlay on /home.
4. Replace obsolete NVIDIA files in the image, copy the new payload, preserve
   any native integration configuration, and verify all four modules and 32/64-bit
   graphics-library dependencies. Build tools stay in the disposable overlay.
5. Mark the inactive slot invalid, write the prepared image to that slot, and let
   Valve migrate settings and boot files. Reapply NVIDIA boot settings, regenerate
   initramfs, and validate the target before Valve may select it for the next boot.

**Preparation replaces the previous contents of the inactive root/var/EFI slot.**
The active root is not unlocked or overwritten. The current system is retained as
rollback. Games and user files are not installation targets. A/B boot metadata is
updated; this is not a file-only operation.

The process runs as a systemd service and logs to Decky's plugin log directory.
Closing/reloading Decky does not own or terminate that service. Reboot is explicit.
The UI distinguishes the current system from the driver prepared for the next boot.

Rollback validates both saved filesystem UUIDs, OS manifests, driver package
metadata and module contents. It refuses a stale transaction after either system
was replaced. Cancellation before the first reboot disables the prepared slot.

## Subsequent SteamOS updates

On a plain supported SteamOS installation, a small bundled runtime is saved in
`/usr/lib/decky-nvidia-update` in the prepared system. Its RAUC hook restores the
exact saved package versions for the new target kernel before slot activation.
If the packages, exact headers or supported update script are unavailable, the
new slot is not activated. The runtime propagates to the new system.

On systems with the recognized steamos-nvidia shared update hook, that integration
continues to own OS repair. Its `driver.conf` pins are updated while preserving
`TRIM_CUDA`, xpadneo and other settings. The manual transaction temporarily replaces
its repair invocation inside a private mount namespace to avoid installing the old
pin again. The installed native hook is preserved.

Removing the Decky UI does not remove the installed driver or its OS repair runtime.

## Plugin updates

The original toolkit's plugin updater is enabled: startup notifications, manual
checks and the release picker use the repository in `project.config.json`.
Decky Loader still handles download, confirmation, installation and reload.
The Settings page waits for driver status and does not expose plugin installation
while a driver operation is running, because that operation uses the bundled files.

Local builds synchronize the frontend configuration from `project.config.json`.
The release workflow sets both copies to its publishing `$GITHUB_REPOSITORY` before
building, so releases from `moi952/decky-nvidia-update` continue updating from
upstream, while fork releases use the fork. Keep the plugin name and display name
unchanged and publish a newer stable version with the workflow's asset name
`decky-nvidia-update-v<VERSION>.zip`. Automatic checks use the latest stable release;
prereleases remain available in the manual release picker. Earlier standalone
development builds with self-updates disabled need a manual update first.

## CLI

```bash
sudo bash bin/steamos-nvidia-update.sh --preflight
sudo bash bin/steamos-nvidia-update.sh --driver 610.57.04-1
sudo bash bin/steamos-nvidia-update.sh --status
sudo bash bin/steamos-nvidia-update.sh --rollback
sudo bash bin/steamos-nvidia-build.sh --version 615.71.09
sudo python3 -I bin/standalone/driver.py install-custom 615.71.09
```

`steamos-nvidia-update.sh --list` lists archived versions without root.
`steamos-nvidia-build.sh` only builds retained packages; `install-custom` builds
if needed and then prepares the inactive slot. The internal shell workers under
`bin/standalone` are invoked only by the controller.

## Build and checks

```bash
pnpm install --frozen-lockfile
python3 -m pip install --target py_modules --no-deps "git+https://github.com/moi952/decky-plugin-toolkit.git@v0.1.0"
python3 -m unittest discover -s tests -v
for script in bin/steamos-nvidia-update.sh bin/standalone/*.sh; do bash -n "$script"; done
pnpm exec tsc --noEmit
node tests/driver_dropdown.cjs
node tests/dropdown_geometry.cjs
node tests/plugin_updates.cjs
bash package.sh
```

The backend's only vendored dependency is decky-plugin-toolkit, pinned to v0.1.0.
`package.sh` bundles it and produces a ZIP in `packages/`.

Build helpers run in a separate PID and mount namespace. Exiting the worker
terminates package-hook daemons and releases their overlay mounts before the
prepared Btrfs image is copied to the inactive slot. The write is refused if
the image still has a loop device or remains registered as mounted in Btrfs.
This fixes the `/etc` overlay `Stale file handle` failure caused by temporary
Btrfs filesystem IDs while the build image was still mounted.

The root-only regression test uses disposable image files under `/home`, with
no writes to OS partitions. On an affected SteamOS kernel it reproduces the old
failure, then verifies helper cleanup and successful `/etc` remounts after both
successful and failed workers:

```bash
sudo bash tests/linux_build_lifecycle.sh "$PWD/bin/standalone"
```

This is a development implementation. Unit tests, syntax checks and frontend builds
are not SteamOS runtime acceptance. Before release, validate on an isolated 5 GiB
A/B SteamOS installation: upgrade, downgrade, insufficient space, interrupted write,
Decky reload, reboot, rollback, and a subsequent SteamOS update. Then check actual
NVIDIA graphics, Gaming/Desktop mode transitions and suspend on hardware.

## Credits and license

Original plugin by [moi952](https://github.com/moi952/decky-nvidia-update).
Standalone SteamOS adaptation by [60plus](https://github.com/60plus/decky-nvidia-update),
using the driver staging method from
[28allday/steamos-nvidia-installer](https://github.com/28allday/steamos-nvidia-installer).
The upstream installer
MIT notice is included in `bin/standalone/LICENSE.steamos-nvidia` and is propagated
with the installed runtime. The original plugin declares the MIT license.
