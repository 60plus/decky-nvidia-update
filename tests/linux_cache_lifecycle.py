#!/usr/bin/env python3
"""Root-only cache regression using a disposable Btrfs image, never OS slots."""
from contextlib import ExitStack
import errno
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

from test_cache import CacheFixtures, c, d


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run as root')
    if '--isolated' not in sys.argv:
        os.execvp('unshare', ['unshare', '--mount', '--propagation', 'private',
                              sys.executable, __file__, '--isolated'])
    real_roots, real_idle, real_locks, real_run = c.slot_roots, c.require_idle, c.repair_locks, d.run
    fixture = CacheFixtures()
    fixture.setUp()
    loop = None
    staging = fixture.base / 'image-root'
    image = fixture.base / 'slot.img'
    failed_mounts = []
    try:
        fixture.make_custom('610.1')
        unused = fixture.make_custom('590.1')
        legacy = fixture.make_legacy('615.1', extras=('opencl-nvidia',))
        fixture.receipt('A', fixture.pins('610.1'))
        fixture.receipt('B', fixture.pins('615.1', legacy=True), native=True)
        d.write_json(fixture.state / 'transaction.json', dict(status='ready', source='A', target='B'))
        with image.open('wb') as stream:
            stream.truncate(256 * 1024**2)
        real_run('mkfs.btrfs', '-q', '-f', str(image))
        loop = real_run('losetup', '--find', '--show', str(image))
        staging.mkdir()
        real_run('mount', loop, str(staging))
        try:
            shutil.copytree(fixture.roots['B'], staging, dirs_exist_ok=True)
        finally:
            real_run('umount', str(staging))
        before = d.file_hash(image)
        real_refs = c.slot_references
        seen = []

        def references(root):
            if root == Path('/'):
                return real_refs(fixture.roots['A'])
            options = real_run('findmnt', '-rn', '--mountpoint', str(root), '-o', 'OPTIONS').split(',')
            assert {'ro', 'nodev', 'nosuid', 'noexec'}.issubset(options), options
            try:
                (root / 'must-not-write').write_text('test')
            except OSError as error:
                assert error.errno == errno.EROFS, error
            else:
                raise AssertionError('Inactive fixture was writable')
            seen.append(str(root))
            return real_refs(root)

        def command(*args):
            if args[0] == 'atomupd-manager':
                return 'idle'
            if args[0] == 'busctl':
                return 's "idle"'
            return real_run(*args)

        with ExitStack() as stack:
            for module, name, value in (
                    (d, 'run', command), (d, 'layout', lambda: ('A', 'B', {'B/rootfs': loop})),
                    (c, 'require_idle', real_idle), (c, 'repair_locks', real_locks),
                    (c, 'REPAIR_LOCKS', [str(fixture.base / 'decky.lock'), str(fixture.base / 'native.lock')]),
                    (c, 'slot_roots', real_roots), (c, 'slot_references', references)):
                stack.enter_context(patch.object(module, name, value))
            result = c.maintain()
            assert result['removed'] == [str(unused)], result
            assert legacy.is_dir() and (fixture.custom / '615.1').is_dir()
            assert seen and all(not Path(p).exists() for p in seen)
            assert d.file_hash(image) == before, 'read-only inspection changed the image bytes'
            # Failure while inspecting must release the mount without collection.
            with patch.object(c, 'slot_references', side_effect=ValueError('unreadable pins')):
                try:
                    c.maintain()
                except ValueError:
                    pass
                else:
                    raise AssertionError('unreadable pins were accepted')
            # A failed unmount must not fall through to recursive deletion.
            def fail_unmount(*args):
                if args[0] == 'umount':
                    failed_mounts.append(args[1])
                    raise subprocess.CalledProcessError(1, args)
                return command(*args)
            with patch.object(d, 'run', fail_unmount):
                try:
                    with c.slot_roots() as roots:
                        failed_root = roots['B']
                except subprocess.CalledProcessError:
                    pass
                else:
                    raise AssertionError('unmount failure was hidden')
            assert (failed_root / 'usr/lib/steamos-nvidia/driver.conf').is_file()
            for mount in failed_mounts:
                real_run('umount', mount)
                Path(mount).rmdir()
            failed_mounts.clear()
            assert d.file_hash(image) == before
        print('PASS: pending-reboot pins in both slots, legacy migration, unused build collection,')
        print('read-only Btrfs bytes unchanged, mount cleanup and failed-unmount safety.')
    finally:
        for mount in failed_mounts:
            real_run('umount', mount)
            Path(mount).rmdir()
        if loop:
            real_run('losetup', '-d', loop)
        # Tests create no OS-slot mounts. Only our private fixture is removed.
        fixture.doCleanups()


if __name__ == '__main__':
    main()
