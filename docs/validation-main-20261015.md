# SteamOS Main follow-up validation, 2026-10-15

The follow-up addresses the empty Arch `--pins` argument, Valve's Main finalizer
loop, and missing EFI mounts during Main's late shutdown migration.

## Hardware run

- RTX 5060; SteamOS Main 3.10, build `20261013.1000`.
- Kernel `7.2.7-valve2-1-neptune-72-g31504582a073`.
- Native NVIDIA integration 0.2.5; Decky Loader 3.2.10.
- Stock 5 GiB Btrfs root partitions, with no resize or active-root unlock.

Returned to the retained Main 610.57.04 slot and rebooted. Removed the previous
manually installed EFI workaround after saving and verifying its exact contents.
Confirmed no effective shutdown-service drop-ins before starting the test.

Installed the candidate ZIP, verified its 39 files, and called `start_update`
through the running Decky backend. The plugin automatically armed the temporary
guard, prepared 615.78.08 in the inactive slot, and reported success at step 8/8.
Reboot was also requested through the running Decky backend.

After boot:

- Loaded NVIDIA module and 64/32-bit userspace packages matched 615.78.08.
- Root remained 5 GiB and read-only, with approximately 683 MiB free.
- Steam, Gamescope and Decky were active; no NVIDIA Xid was found in this boot.
- Native driver/addon checks passed. Both saved slot identities matched, including
  the retained Main 610 slot, inspected read-only.
- Shutdown migration finished successfully, without the missing `/efi` error or
  `Failed to migrate the configuration changes!` warning.
- The journal recorded host `/efi` and `/esp` unmounting while the guarded service
  was running. Migration completed at 01:23:38 BST.
- The temporary `/run` drop-in disappeared after reboot; the manual `/etc`
  workaround was absent in both slots. No update/reboot trigger remained.
- All 39 installed plugin files still matched the candidate receipt.

Candidate ZIP SHA-256:
`7e3a17755ac42045533e09f7b49f6673816d3520836c3ef95d44a5ccd980243d`.

## Automated checks

- 109 Python tests passed as root on SteamOS, with no skips, including guard
  failures before staging, custom override refusal, unknown vendor code,
  idempotence, failed verification cleanup and rollback preservation.
- Bash syntax checks passed for the bundled scripts.
- The root-only namespace probe reproduced the missing EFI lookup, recovered it
  with the actual guard command, refused a missing device, and left the host
  mount unchanged. It did not run slot migration.
- TypeScript, all three frontend regression scripts, and the production build
  passed. UI-kit and upstream release/update configuration are unchanged.

The earlier Stable 3.8.28 / `20260922.1` run reproduced the empty `--pins` failure
and passed a real corrected 610 to 615 update/reboot with 91 tests. The new guard
is a no-op for that saved Stable post-install layout; the latest 39-file package
was hardware-tested on Main, not reinstalled on Stable.

## Limits and remaining warnings

This was a live Decky backend API test, not a UI click. It qualifies this Main
build and the recognized vendor unit/helper hashes, not future Main builds.
Fresh installation without native integration, a new custom driver build,
suspend/resume, and a subsequent Main-to-Main OS update were not repeated.
The temporary guard protects updates initiated through the plugin; it is not
a permanent OS-wide shutdown workaround.

Preparation still logged existing warnings about the missing rootfs caibx index
(the previous index is retained), ignored GnuPG sockets, and initramfs firmware/
helper discovery. The tested EFI migration errors are gone; this is not a claim
that every preparation log is warning-free.
