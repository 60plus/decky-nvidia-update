#!/usr/bin/python3 -I
"""Conservative cache maintenance, called by driver.py while holding its lock.

Only this worker mounts the inactive root, read-only in a private namespace.
It never rewrites slot pins. Unknown metadata or active work prevents collection.
"""
import argparse
from contextlib import ExitStack, contextmanager
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

spec = importlib.util.spec_from_file_location('driver', Path(__file__).with_name('driver.py'))
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)

LEGACY = Path('/home/.steamos-nvidia-custom-builds')
UPDATE_LOCK = Path('/run/steamos-nvidia-update.lock')
REPAIR_LOCKS = ('/run/decky-nvidia-repair.lock', '/run/steamos-nvidia-repatch.lock')
PROC = Path('/proc')
BARE = re.compile(r'[0-9]+(?:\.[0-9]+)+')
PACKAGE = re.compile(r'([a-z0-9-]+)-([0-9.]+-[0-9.]+)-x86_64\.pkg\.tar\.zst')


def log(message):
    print('[cache] ' + message, file=sys.stderr, flush=True)


def owned(path, directory=False):
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Unsafe cache/metadata path: ' + str(path))
    if not directory and info.st_nlink != 1:
        raise ValueError('Hard-linked cache/metadata file: ' + str(path))
    return info


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate metadata field: ' + key)
        result[key] = value
    return result


def read_json(path):
    owned(path)
    data = json.loads(path.read_text(), object_pairs_hook=unique_object)
    if not isinstance(data, dict):
        raise ValueError('Expected an object: ' + str(path))
    return data


def safe_path(root, relative):
    """Reject symlinks, including missing files behind an unsafe parent."""
    path = root
    parts = Path(relative).parts
    for index, part in enumerate(parts):
        path = path / part
        try:
            owned(path, directory=index < len(parts) - 1)
        except FileNotFoundError:
            return None
    return path


def pin_references(version, urls):
    if not isinstance(version, str) or not d.VERSION.fullmatch(version) or '-' not in version:
        raise ValueError('Unknown saved driver version')
    if not isinstance(urls, str) or not urls.strip():
        raise ValueError('Missing saved package URLs')
    refs, names = set(), set()
    for url in urls.split():
        parsed = urlsplit(url)
        if parsed.query or parsed.fragment or '%' in url or '$' in url or '`' in url:
            raise ValueError('Ambiguous saved package URL')
        match = PACKAGE.fullmatch(parsed.path.rsplit('/', 1)[-1])
        if not match or match[1] in names:
            raise ValueError('Unknown or duplicate saved package')
        name, pkgver = match[1], match[2]
        names.add(name)
        if name == 'nvidia-utils' and pkgver != version:
            raise ValueError('Saved driver version mismatch')
        if name in d.CORE_PACKAGES and pkgver.rsplit('-', 1)[0] != version.rsplit('-', 1)[0]:
            raise ValueError('Mixed saved driver versions')
        if parsed.scheme == 'https' and parsed.netloc:
            continue
        if parsed.scheme != 'file' or parsed.netloc or url != 'file://' + parsed.path:
            raise ValueError('Unknown saved package source')
        path = Path(parsed.path)
        matched = False
        for base, depth in ((d.CUSTOM, 3), (LEGACY, 2)):
            try:
                parts = path.relative_to(base).parts
            except ValueError:
                continue
            if (len(parts) != depth or not BARE.fullmatch(parts[0]) or
                    parts[0] != pkgver.rsplit('-', 1)[0] or
                    (depth == 3 and parts[1] != name)):
                raise ValueError('Ambiguous local package reference')
            refs.add(base / parts[0])
            matched = True
            break
        if not matched:
            raise ValueError('Unknown local package directory')
    if not set(d.CORE_PACKAGES).issubset(names):
        raise ValueError('Incomplete saved package pins')
    return refs


def slot_references(root):
    # A readable SteamOS root is mandatory even if no NVIDIA pins are present.
    release = safe_path(root, 'usr/lib/os-release')
    if release is None or not re.search(r'^ID=(?:steamos|"steamos")$', release.read_text(), re.M):
        raise ValueError('Unreadable or unknown SteamOS slot')
    refs, found = set(), False
    receipt = safe_path(root, 'usr/lib/decky-nvidia-update/installed.json')
    if receipt is not None:
        data = read_json(receipt)
        refs.update(pin_references(data.get('version'), data.get('urls')))
        found = True
    native = safe_path(root, 'usr/lib/steamos-nvidia/driver.conf')
    if native is not None:
        values = {}
        for line in native.read_text().splitlines():
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            key, separator, value = line.partition('=')
            if not separator or not re.fullmatch(r'[A-Z][A-Z0-9_]*', key):
                raise ValueError('Unknown native driver config syntax')
            if key in values:
                raise ValueError('Duplicate native driver config field')
            tokens = shlex.split(value, comments=True)
            if len(tokens) != 1 or any(c in tokens[0] for c in ('$', '`', '\n')):
                raise ValueError('Ambiguous native driver config value')
            values[key] = tokens[0]
        refs.update(pin_references(values.get('DRIVER_VERSION'), values.get('PKG_URLS')))
        found = True
    if not found:
        # An old live-root install may have no receipt. Absence is not evidence
        # that its packages are unused. Only a plain, readable slot is empty.
        db = root / 'usr/lib/holo/pacmandb/local'
        for path in (root / 'usr/lib/holo', db.parent, db):
            owned(path, directory=True)
        if any(re.match(r'(?:nvidia|lib32-nvidia)', p.name) for p in db.iterdir()):
            raise ValueError('NVIDIA is installed without saved pins')
        if list((root / 'usr/lib/modules').glob('*/updates/dkms/nvidia*.ko*')):
            raise ValueError('NVIDIA modules exist without saved pins')
    return refs


def require_idle():
    state = d.read_state()
    if state.get('status') not in ('idle', 'ready', 'rollback-selected') or state.get('work'):
        raise ValueError('Unfinished or uncertain driver transaction; keeping all packages')
    if d.STATE.exists() and list(d.STATE.glob('prepare-*')):
        raise ValueError('A driver workspace remains; keeping all packages')
    if d.run('atomupd-manager', 'get-update-status') != 'idle':
        raise ValueError('SteamOS update is busy')
    if d.run('busctl', 'get-property', 'de.pengutronix.rauc', '/',
             'de.pengutronix.rauc.Installer', 'Operation') != 's "idle"':
        raise ValueError('RAUC is busy')
    # The original builder does not take our locks. Do not touch its cache while
    # a legacy entry point is running (also conservatively covers CLI parents).
    for process in PROC.iterdir():
        if not process.name.isdigit():
            continue
        try:
            args = (process / 'cmdline').read_bytes().split(b'\0')
        except FileNotFoundError:
            continue
        if any(arg.rsplit(b'/', 1)[-1] in (b'steamos-nvidia-build.sh', b'steamos-nvidia-update.sh') for arg in args):
            raise ValueError('A legacy driver command is running')


@contextmanager
def repair_locks():
    import fcntl
    with ExitStack() as stack:
        for name in REPAIR_LOCKS:
            lock = stack.enter_context(open(name, 'a'))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


@contextmanager
def slot_roots():
    source, target, devices = d.layout()
    superblock = d.run('btrfs', 'inspect-internal', 'dump-super', '--super', '0', devices[target + '/rootfs'])
    if re.findall(r'^log_root\s+(\d+)\s*$', superblock, re.M) != ['0']:
        # Skipping a dirty log could hide newer pins; replaying it would write
        # to the slot. Defer instead of treating older metadata as authoritative.
        raise ValueError('Inactive Btrfs log is dirty or unknown; keeping all packages')
    root = Path(tempfile.mkdtemp(prefix='decky-nvidia-cache-', dir='/run'))
    mounted = False
    try:
        # rescue=nologreplay also prevents log replay writes on a read-only mount.
        d.run('mount', '-t', 'btrfs', '-o', 'ro,rescue=nologreplay,nodev,nosuid,noexec',
              devices[target + '/rootfs'], str(root))
        mounted = True
        yield {source: Path('/'), target: root}
    finally:
        if mounted:
            d.run('umount', str(root))
        # Never recursively remove a mountpoint, including on unmount failure.
        root.rmdir()


def exact_tree(folder, files, directories=()):
    base = owned(folder, directory=True)
    if os.path.ismount(folder):
        raise ValueError('Mounted cache build: ' + str(folder))
    expected = set(files) | set(directories)
    seen = set()
    for path in folder.rglob('*'):
        relative = path.relative_to(folder).as_posix()
        info = owned(path, directory=relative in directories)
        if info.st_dev != base.st_dev or os.path.ismount(path):
            raise ValueError('Mounted cache content: ' + str(path))
        seen.add(relative)
    if seen != expected:
        raise ValueError('Unexpected or incomplete cache contents: ' + str(folder))


def legacy_manifest(folder):
    owned(LEGACY, directory=True)
    owned(folder, directory=True)
    version = folder.name
    if not BARE.fullmatch(version) or int(version.split('.')[0]) < 590:
        raise ValueError('Unsupported legacy version')
    data = read_json(folder / 'build.json')
    names = data.get('packages')
    core = [f'{pkg}-{version}-1-x86_64.pkg.tar.zst' for pkg in d.CORE_PACKAGES]
    if (data.get('version') != version or not isinstance(names, list) or
            not all(isinstance(n, str) for n in names) or len(set(names)) != len(names) or
            not set(core).issubset(names)):
        raise ValueError('Incomplete legacy build manifest')
    for name in names:
        match = PACKAGE.fullmatch(name)
        if not match or match[2] != version + '-1':
            raise ValueError('Unexpected legacy package filename')
    exact_tree(folder, ['build.json', *names])
    hashes = {}
    for name in names:
        match = PACKAGE.fullmatch(name)
        metadata = d.run('tar', '-xOf', str(folder / name), '.PKGINFO')
        for key, value in (('pkgname', match[1]), ('pkgver', version + '-1'), ('arch', 'x86_64')):
            if re.findall(r'^' + key + r' = (.+)$', metadata, re.M) != [value]:
                raise ValueError('Unexpected legacy package metadata: ' + name)
        hashes[name] = d.file_hash(folder / name)
    return dict(version=version, built_at=data.get('built_at', ''), packages=core,
                sha256={n: hashes[n] for n in core}), hashes


def migrate(folder, manifest):
    target = d.CUSTOM / folder.name
    if os.path.lexists(target):
        current = d.custom_manifest(folder.name)
        d.custom_pins(folder.name)
        if current['sha256'] != manifest['sha256']:
            raise ValueError('Existing retained build differs; leaving both copies intact')
        return False
    with tempfile.TemporaryDirectory(prefix='.migrate-', dir=d.CUSTOM) as temp:
        output = Path(temp) / 'packages'
        output.mkdir(mode=0o700)
        for pkg, name in zip(d.CORE_PACKAGES, manifest['packages']):
            (output / pkg).mkdir(mode=0o700)
            destination = output / pkg / name
            shutil.copyfile(folder / name, destination)
            destination.chmod(0o600)
            if d.file_hash(destination) != manifest['sha256'][name]:
                raise ValueError('Legacy package changed during migration')
        d.write_json(output / 'build.json', manifest)
        d.run('sync', '-f', str(output))
        # Serialize with all bundled writers. Never replace an existing build.
        if os.path.lexists(target):
            raise ValueError('Retained build appeared during migration')
        os.rename(output, target)
        d.run('sync', '-f', str(d.CUSTOM))
    log('Migrated legacy build ' + folder.name + '; original files retained until unused')
    return True


def maintain(collect=True):
    result = dict(migrated=[], removed=[], kept=[])
    with repair_locks():
        require_idle()
        d.private_dir(d.STATE)
        d.private_dir(d.CUSTOM)
        legacy = {}
        uncertain_versions = set()
        if os.path.lexists(LEGACY):
            owned(LEGACY, directory=True)
            for folder in sorted(LEGACY.iterdir()):
                try:
                    manifest, hashes = legacy_manifest(folder)
                    migrated = migrate(folder, manifest)
                    legacy[folder] = hashes
                    if migrated:
                        result['migrated'].append(folder.name)
                except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
                    log(f'Keeping {folder}: {error}')
                    result['kept'].append(str(folder))
                    uncertain_versions.add(folder.name)
        if not collect:
            return result
        # Read BOTH slots and BOTH pin formats before deleting anything. The
        # selected/invalid flags never exempt a rollback or prepared slot.
        with slot_roots() as roots:
            if set(roots) != {'A', 'B'}:
                raise ValueError('Both slot roots are required')
            refs = set().union(*(slot_references(roots[s]) for s in ('A', 'B')))
            retained_refs = refs | {d.CUSTOM / p.name for p in refs if p.parent == LEGACY}
            candidates = []
            for folder in sorted(d.CUSTOM.iterdir()):
                if folder in retained_refs or folder.name in uncertain_versions or not BARE.fullmatch(folder.name):
                    continue
                try:
                    manifest = d.custom_manifest(folder.name)
                    d.custom_pins(folder.name)
                    files = ['build.json'] + [pkg + '/' + name for pkg, name in zip(d.CORE_PACKAGES, manifest['packages'])]
                    exact_tree(folder, files, d.CORE_PACKAGES)
                    candidates.append(folder)
                except (ValueError, OSError, KeyError, TypeError) as error:
                    log(f'Keeping {folder}: {error}')
                    result['kept'].append(str(folder))
            for folder, hashes in legacy.items():
                if folder not in refs:
                    # Validation covered all split packages, including OpenCL.
                    # Recheck bytes immediately before allowing legacy removal.
                    exact_tree(folder, ['build.json', *hashes])
                    if any(d.file_hash(folder / n) != digest for n, digest in hashes.items()):
                        raise ValueError('Legacy build changed; collection deferred')
                    candidates.append(folder)
            require_idle()
            if refs != set().union(*(slot_references(roots[s]) for s in ('A', 'B'))):
                raise ValueError('Slot pins changed; collection deferred')
            for folder in candidates:
                require_idle()
                shutil.rmtree(folder)
                log('Removed unused build ' + str(folder))
                result['removed'].append(str(folder))
    return result


def main():
    import fcntl
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock-fd', type=int, required=True)
    parser.add_argument('--migrate-only', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ValueError('Run through the root controller')
    inherited = os.fstat(args.lock_fd)
    expected = UPDATE_LOCK.stat()
    if (inherited.st_dev, inherited.st_ino) != (expected.st_dev, expected.st_ino):
        raise ValueError('Missing shared updater lock')
    fcntl.flock(args.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    print(json.dumps(maintain(collect=not args.migrate_only)))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        log('Maintenance skipped: ' + str(error))
        sys.exit(1)
