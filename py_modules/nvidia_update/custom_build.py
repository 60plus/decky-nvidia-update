import asyncio
import json
import os
import re
import shutil
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import decky

# The plugin ships its own copy of this CLI tool (bin/steamos-nvidia-build.sh)
# so it can be run by hand over SSH/terminal or driven from this UI — same
# split as plugin.py's own SCRIPT_PATH for steamos-nvidia-update.sh.
SCRIPT_PATH = Path(decky.DECKY_PLUGIN_DIR) / "bin" / "steamos-nvidia-build.sh"

LOG_FILE = Path(decky.DECKY_PLUGIN_LOG_DIR) / "nvidia-build-run.log"

# Written by steamos-nvidia-build.sh, read by both this mixin and
# bin/steamos-nvidia-update.sh's own pin_pkg() — the one shared contract
# between the build side and the install side.
CACHE_DIR = Path("/home/.steamos-nvidia-custom-builds")


def _clean_env() -> Dict[str, str]:
    """Same LD_LIBRARY_PATH fix as plugin.py's own helper — duplicated here
    (rather than imported) to avoid a circular import between the two
    modules, since plugin.py imports this file's mixin class."""
    env = os.environ.copy()
    env.pop("LD_LIBRARY_PATH", None)
    return env


STEP_RE = re.compile(r"^\[(\d+)/(\d+)\]\s*(.*)$")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
VERSION_SPLIT_RE = re.compile(r"(\d+)")


def _version_key(v: str):
    return [int(p) if p.isdigit() else p for p in VERSION_SPLIT_RE.split(v)]


# `version` arrives over RPC from the frontend and is used to build
# filesystem paths (CACHE_DIR / version) and a script argument — reject
# anything that isn't a bare pkgver before it touches either, same shape
# steamos-nvidia-build.sh itself validates on its side.
_VALID_VERSION_RE = re.compile(r"^[0-9]+(\.[0-9]+)*$")


def _is_valid_version(version: str) -> bool:
    return bool(_VALID_VERSION_RE.match(version))


# NVIDIA's own public download server — the actual source every nvidia-utils/
# lib32-nvidia-utils PKGBUILD fetches its .run installer from, so any version
# listed here is guaranteed buildable regardless of whether Arch ever
# packaged it. Ground truth for the version picker in the UI, independent of
# Arch's own archive/NVIDIA's separate (unofficial, ~1.5-year) classification
# lookup used elsewhere in this plugin.
DOWNLOAD_INDEX_URL = "https://download.nvidia.com/XFree86/Linux-x86_64/"
DIR_ENTRY_RE = re.compile(r"<a href='([0-9][^']*)/'>")
# Versions below this are ancient (pre-2025-ish) and not worth offering —
# keeps the dropdown to a reasonable, relevant length.
MIN_BUILDABLE_MAJOR = 590

# nvidia-utils and lib32-nvidia-utils both build from this exact .run —
# checking it once covers both packages.
RUN_URL_TEMPLATE = "https://download.nvidia.com/XFree86/Linux-x86_64/{v}/NVIDIA-Linux-x86_64-{v}.run"

# Files today's Arch nvidia-utils PKGBUILD unconditionally `install`s FROM
# the extracted .run payload (as opposed to from the PKGBUILD's own
# source=() array) — an old enough release that predates one of these fails
# makepkg's package() step partway through the build, after the expensive
# download/compile work already happened. Confirmed live: 595.99.02 lacks
# dlsnetparams.csv (added for nvidia-powerd's DLSS network-parameters
# support in a later release). This is necessarily a hardcoded, maintained
# list rather than something parsed out of the PKGBUILD automatically —
# `install` is invoked with too many different shapes (bare args, -t
# DESTDIR, globs, ${srcdir}-relative vs payload-relative paths) to do that
# reliably. Extend this list if a future Arch PKGBUILD change adds a
# similar hard requirement an older release doesn't ship.
REQUIRED_INSTALLER_FILES = ["dlsnetparams.csv"]


DEFAULT_BUILD_STATE: Dict[str, Any] = {
    "running": False,
    "step": 0,
    "total_steps": 8,
    "message": "",
    "log": [],
    "done": False,
    "success": None,
    "error": None,
    "version": None,
}


class CustomBuildMixin:
    """Builds an NVIDIA driver version Arch never packaged, from Arch's own
    packaging repos, via bin/steamos-nvidia-build.sh — a separate, explicit
    action from the PluginUpdaterMixin-adjacent install flow in plugin.py.
    Mixed into Plugin so its methods are callable from the frontend the same
    way as any other Plugin method."""

    _build_state: Dict[str, Any] = dict(DEFAULT_BUILD_STATE)
    _build_task: Optional[asyncio.Task] = None

    async def list_custom_builds(self) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        if not CACHE_DIR.is_dir():
            return results
        try:
            for entry in CACHE_DIR.iterdir():
                manifest = entry / "build.json"
                if not manifest.is_file():
                    continue
                try:
                    results.append(json.loads(manifest.read_text(encoding="utf-8")))
                except Exception as e:
                    decky.logger.error(f"[list_custom_builds] {manifest}: {e}")
        except Exception as e:
            decky.logger.error(f"[list_custom_builds] {e}")
            return []
        results.sort(key=lambda b: _version_key(str(b.get("version", ""))), reverse=True)
        return results

    async def list_buildable_versions(self) -> List[str]:
        """Every NVIDIA Linux driver version (>= MIN_BUILDABLE_MAJOR) on
        NVIDIA's own download server, newest first — populates the version
        dropdown in CustomBuildSection. Best-effort: an empty list here just
        means the dropdown is empty, same degrade-gracefully treatment as
        every other external lookup in this plugin."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "curl", "-sfL", DOWNLOAD_INDEX_URL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=_clean_env(),
            )
            out, _ = await proc.communicate()
            if proc.returncode != 0:
                return []
            versions = []
            for v in DIR_ENTRY_RE.findall(out.decode(errors="replace")):
                major = v.split(".", 1)[0]
                if major.isdigit() and int(major) >= MIN_BUILDABLE_MAJOR:
                    versions.append(v)
            versions.sort(key=_version_key, reverse=True)
            return versions
        except Exception as e:
            decky.logger.error(f"[list_buildable_versions] {e}")
            return []

    async def check_buildable(self, version: str) -> Dict[str, Any]:
        """Best-effort pre-flight: does this release actually contain the
        files today's Arch PKGBUILD needs, WITHOUT downloading or building
        anything. NVIDIA's .run is a makeself archive — a small shell header
        (its size varies release to release, confirmed live, never assume a
        fixed offset) followed by a zstd-compressed tar of the real payload.
        Reading just the header to find that offset, then streaming (never
        writing to disk) the decompressed tar's file listing, answers the
        question without the ~400MB+ installer ever touching disk — lighter
        than a real build regardless (skips the whole chroot/makepkg
        machinery), even on a fast link where it isn't necessarily lighter
        on bandwidth than the download the build would do anyway.

        `checked_ok: false` means the check itself failed (network, no zstd,
        unexpected header shape, ...) — treat that as "unknown", not as
        "not buildable"; only checked_ok=true + buildable=false is an actual
        answer."""
        empty = {"checked_ok": False, "buildable": False, "missing_files": []}
        if not _is_valid_version(version):
            return empty
        url = RUN_URL_TEMPLATE.format(v=version)
        try:
            proc = await asyncio.create_subprocess_exec(
                "curl", "-sfL", "-r", "0-4096", url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=_clean_env(),
            )
            header, _ = await proc.communicate()
            if proc.returncode != 0:
                return empty
            m = re.search(rb"^skip=(\d+)", header, re.MULTILINE)
            if not m:
                return empty
            skip = m.group(1).decode()

            # Single streamed pass regardless of how many files are being
            # checked — `tar tf -` never touches disk, and the listing
            # itself is inherently small (a few tens of KB of filenames at
            # most). A byte-capping `head -c` here was tried and dropped:
            # it intermittently truncated the listing mid-stream (observed
            # live — a real build's own file was wrongly reported missing),
            # almost certainly from head's own pipe buffering racing the
            # upstream writers. A wall-clock timeout around the whole
            # `communicate()` call below is the safety net instead.
            pipeline = (
                f"curl -sfL '{url}' | tail -n +{skip} | zstd -dc 2>/dev/null "
                "| tar tf - 2>/dev/null"
            )
            proc = await asyncio.create_subprocess_shell(
                pipeline,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=_clean_env(),
            )
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=90)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return empty
            listing = set(out.decode(errors="replace").splitlines())
            missing = [f for f in REQUIRED_INSTALLER_FILES if f not in listing]
            return {
                "checked_ok": True,
                "buildable": not missing,
                "missing_files": missing,
            }
        except Exception as e:
            decky.logger.error(f"[check_buildable] {e}")
            return empty

    async def get_build_status(self) -> Dict[str, Any]:
        return dict(CustomBuildMixin._build_state)

    async def start_custom_build(self, version: str) -> bool:
        if not _is_valid_version(version):
            return False
        if CustomBuildMixin._build_state["running"]:
            return False
        CustomBuildMixin._build_state = dict(DEFAULT_BUILD_STATE)
        CustomBuildMixin._build_state["running"] = True
        CustomBuildMixin._build_state["version"] = version
        CustomBuildMixin._build_state["message"] = "Starting…"
        CustomBuildMixin._build_task = asyncio.create_task(self._run_build(version))
        return True

    async def _run_build(self, version: str) -> None:
        args = ["bash", str(SCRIPT_PATH), "--version", version, "-y"]
        decky.logger.info(f"[nvidia-build] launching: {' '.join(args)}")

        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(LOG_FILE, "w", encoding="utf-8") as logf:
                logf.write(
                    f"=== nvidia-build {datetime.now().isoformat(timespec='seconds')} "
                    f"— version={version} ===\n"
                )
                logf.flush()

                proc = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env=_clean_env(),
                )
                assert proc.stdout is not None
                while True:
                    raw = await proc.stdout.readline()
                    if not raw:
                        break
                    line = ANSI_RE.sub("", raw.decode(errors="replace")).rstrip()
                    if not line:
                        continue

                    logf.write(line + "\n")
                    logf.flush()

                    CustomBuildMixin._build_state["log"].append(line)
                    CustomBuildMixin._build_state["log"] = CustomBuildMixin._build_state["log"][-200:]

                    m = STEP_RE.match(line)
                    if m:
                        CustomBuildMixin._build_state["step"] = int(m.group(1))
                        CustomBuildMixin._build_state["total_steps"] = int(m.group(2))
                        CustomBuildMixin._build_state["message"] = m.group(3)

                    await decky.emit("nvidia_build_progress", dict(CustomBuildMixin._build_state))

                rc = await proc.wait()
                logf.write(f"=== exited with code {rc} ===\n")

            CustomBuildMixin._build_state["running"] = False
            CustomBuildMixin._build_state["done"] = True
            CustomBuildMixin._build_state["success"] = rc == 0
            if rc != 0:
                fail_lines = [l for l in CustomBuildMixin._build_state["log"] if l.startswith("[fail]")]
                CustomBuildMixin._build_state["error"] = (
                    fail_lines[-1][len("[fail] "):] if fail_lines else f"Script exited with code {rc}."
                )
            await decky.emit("nvidia_build_progress", dict(CustomBuildMixin._build_state))
            decky.logger.info(f"[nvidia-build] finished rc={rc}")
        except Exception as e:
            decky.logger.error(f"[nvidia-build] exception: {e}\n{traceback.format_exc()}")
            CustomBuildMixin._build_state["running"] = False
            CustomBuildMixin._build_state["done"] = True
            CustomBuildMixin._build_state["success"] = False
            CustomBuildMixin._build_state["error"] = str(e)
            await decky.emit("nvidia_build_progress", dict(CustomBuildMixin._build_state))

    async def delete_custom_build(self, version: str) -> bool:
        if not _is_valid_version(version):
            return False
        target = CACHE_DIR / version
        try:
            if target.is_dir():
                shutil.rmtree(target)
            return True
        except Exception as e:
            decky.logger.error(f"[delete_custom_build] {e}")
            return False
