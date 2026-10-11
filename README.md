# decky-nvidia-update

![GitHub Downloads](https://img.shields.io/github/downloads/moi952/decky-nvidia-update/total)
![Downloads latest](https://img.shields.io/github/downloads/moi952/decky-nvidia-update/latest/total)
![Stars](https://img.shields.io/github/stars/moi952/decky-nvidia-update)

A [Decky Loader](https://decky.xyz/) plugin that installs or changes the NVIDIA driver
from a running SteamOS system. It prepares the inactive A/B system slot without a USB
stick, repair image or OS reinstall. Reboot when you're ready to use the new driver.

![Plugin overview](screenshots/overview.jpeg)

## Requirements

Requires a supported SteamOS A/B installation with Btrfs root filesystems, separate
`/home`, Decky Loader with root backend support, and Python 3.10 or newer.

- **No fixed 8 GiB root requirement.** Driver preparation uses a compressed image on
  `/home`, sized to the actual inactive root partition. Existing 8 GiB partitions
  remain supported; resizing is not required. The prepared system must still fit.
- Free space on `/home`: the inactive root partition's size plus 12 GiB, about
  17 GiB for a 5 GiB root slot. Build tools and temporary files stay on `/home`.
- NVIDIA GTX 16 or RTX hardware supported by the selected driver. The plugin checks
  the GPU, libraries, kernel and system layout before preparing the driver.

The backend is bundled with the plugin; a separate NVIDIA installer is not required.
See [SteamOS A/B details](docs/steamos-ab.md) for compatibility, rollback and checks.

## What it does

- Pick any NVIDIA driver version available in the [Arch Linux
  archive](https://archive.archlinux.org/packages/n/nvidia-utils/) and prepare it in
  the inactive slot, or just hit the button to grab the latest.
- Each version shows its source: **Arch**, **Arch testing**, or **NVIDIA**.
  **Recommended (Arch)** contains the original Arch packages. Additional NVIDIA
  versions use **New Feature Branch (NFB)**, **Beta**, or **Other NVIDIA** labels;
  versions without Arch packages are marked **Needs build** and built before use.

  <img src="screenshots/version-dropdown.jpeg" width="420" alt="Driver version dropdown">

- Filter by source/channel, see how many versions each contains, and choose which
  channel counts as "latest" when you open the plugin. The scrollable list displays
  up to eight versions at once.

  <img src="screenshots/channel-selector.jpeg" width="420" alt="Update channel selector">

- Read the real release notes for any version before installing — fetched live, not
  bundled or guessed.

  <img src="screenshots/release-notes.jpeg" width="420" alt="Release notes modal">

- Live step-by-step progress and a scrollback log while it runs, and a **Reboot now**
  button once it's done.
- Fully localized UI (English, French, Spanish).
- Built-in plugin update checks, notifications and release selection are retained.
  Official releases update from the author's repository; fork releases use the fork.

## Safety

Games and user files are not installation targets. Driver preparation replaces the
previous contents of the **inactive root/var/EFI slot**, while retaining the running
system for rollback:

- Downloads and builds run in disposable workspaces on `/home`. A fresh compressed
  Btrfs image holds the prepared system; the active root is not unlocked or overwritten.
- The target is checked before writing and validated before selection for the next
  boot. Unknown layouts and insufficient space stop preparation.
- The operation runs in a systemd service, so closing Decky does not interrupt it.
  Plugin installation from Settings is unavailable while a driver operation is running.
- A reboot is required to load the prepared driver; nothing forces one on you.
- Rollback checks that the saved systems have not been replaced. See the
  [A/B documentation](docs/steamos-ab.md) for details and retained custom packages.

## Building

```bash
pnpm install
./package.sh
```

`package.sh` builds the frontend and zips a ready-to-install plugin into `packages/`
(auto-incrementing filename, never overwrites a previous build). Install the zip from the
Decky Loader menu (Quick Access → the flask icon → the plus/install button) in Desktop or
Game Mode.

No Docker or Decky CLI needed. `package.sh` vendors the pinned Python
`decky-plugin-toolkit` dependency and includes the A/B backend. Build checks and release
configuration are documented in [SteamOS A/B details](docs/steamos-ab.md#build-and-checks).

### Faster iteration over SSH (VS Code tasks)

If you're actively developing and want to skip re-zipping/re-installing on every change:

1. Run the **settingscheck** task once — it creates `.vscode/settings.json` from
   `.vscode/defsettings.json`. Edit it with your Deck's IP, SSH user/password/key.
   `.vscode/settings.json` is gitignored — it holds your credentials, never commit it.
2. Run the **builddeploy** task: builds the frontend, rsyncs the plugin folder to
   `~/homebrew/plugins/decky-nvidia-update/` on the Deck, and restarts `plugin_loader`.
3. Enable the plugin from the Decky Loader menu.

## Architecture

- `bin/steamos-nvidia-update.sh` is the CLI entry point. It runs standalone too:
  `sudo ./bin/steamos-nvidia-update.sh --driver 610.57.04 -y`.
- `bin/standalone/` contains the controller, isolated builder, A/B staging, rollback
  and SteamOS update repair runtime.
- `py_modules/nvidia_update/plugin.py` and `standalone.py` provide the backend RPCs,
  start and reconnect to the systemd job, and emit `nvidia_update_progress` events.
  The backend also fetches Arch versions and NVIDIA channel information and retains
  the original toolkit's plugin self-update checks.
- `src/index.tsx`, `src/VersionDropdown.tsx`, `src/ReleaseNotesModal.tsx` — the UI.

## Credits

This plugin wraps and builds on
[steamos-nvidia-installer](https://github.com/28allday/steamos-nvidia-installer) by
[28allday](https://github.com/28allday) — the underlying driver-install mechanism (overlay,
loopback scratch space, DKMS build) originates there. This project adapts it into a Decky
Loader plugin with a UI.

Compressed A/B preparation was adapted by [60plus](https://github.com/60plus) from
the desktop driver updater approach in
[SteamOS NVIDIA Installer](https://github.com/60plus/SteamOS_Nvidia_Installer).

## License

MIT.
