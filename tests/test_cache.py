"""Cache policy fixtures; Linux root runs additionally exercise real trust checks."""
from contextlib import nullcontext
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import types
import unittest
from unittest.mock import patch

from test_standalone import d, URLS, VERSION

spec = importlib.util.spec_from_file_location('cache', d.HERE / 'cache.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
c.d = d
ROOT_LINUX = os.name == 'posix' and os.geteuid() == 0


@unittest.skipUnless(os.name == 'posix', 'fixtures use Linux file URLs')
class CacheFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.state = self.base / 'state'
        self.custom = self.state / 'custom'
        self.legacy = self.base / 'legacy'
        for path in (self.state, self.custom, self.legacy):
            path.mkdir(mode=0o700)
        self.roots = {}
        for slot in ('A', 'B'):
            root = self.base / slot
            (root / 'usr/lib/holo/pacmandb/local').mkdir(parents=True)
            (root / 'usr/lib/os-release').write_text('ID=steamos\n')
            self.roots[slot] = root
        self.mocks = {}
        for module, name, value in ((d, 'STATE', self.state), (d, 'CUSTOM', self.custom),
                                     (c, 'LEGACY', self.legacy)):
            self.mock(module, name, value)
        self.mock(c, 'require_idle')
        self.mock(c, 'repair_locks', side_effect=nullcontext)
        self.mock(c, 'slot_roots', side_effect=lambda: nullcontext(self.roots))
        self.real_run = d.run
        self.mock(d, 'run', side_effect=self.command)
        # Windows ACLs / unprivileged CI cannot represent the root trust policy.
        # The same fixtures run WITHOUT these mocks in the root Linux test run.
        if not ROOT_LINUX:
            self.mock(c, 'owned', side_effect=lambda p, directory=False: p.lstat())
            self.mock(d, 'private_dir', side_effect=lambda p: p.mkdir(parents=True, exist_ok=True))
            self.mock(d, 'secure_file')
            self.mock(d, 'write_json', side_effect=lambda p, v: p.write_text(json.dumps(v)))

    def mock(self, module, name, *args, **kwargs):
        p = patch.object(module, name, *args, **kwargs)
        self.addCleanup(p.stop)
        result = p.start()
        self.mocks[name] = result
        return result

    def command(self, *args):
        if args[0] == 'tar':
            with tarfile.open(args[2]) as archive:
                return archive.extractfile('.PKGINFO').read().decode()
        if args[0] == 'sync':
            return ''
        raise AssertionError(args)

    def make_legacy(self, version='615.1', extras=()):
        folder = self.legacy / version
        folder.mkdir(mode=0o700)
        names = []
        for pkg in (*d.CORE_PACKAGES, *extras):
            name = f'{pkg}-{version}-1-x86_64.pkg.tar.zst'
            names.append(name)
            metadata = f'pkgname = {pkg}\npkgver = {version}-1\narch = x86_64\n'.encode()
            with tarfile.open(folder / name, 'w') as archive:
                entry = tarfile.TarInfo('.PKGINFO')
                entry.size = len(metadata)
                archive.addfile(entry, io.BytesIO(metadata))
        (folder / 'build.json').write_text(json.dumps(dict(version=version, packages=names, built_at='test')))
        return folder

    def make_custom(self, version):
        folder = self.make_legacy(version)
        manifest, _ = c.legacy_manifest(folder)
        c.migrate(folder, manifest)
        import shutil
        shutil.rmtree(folder)
        return self.custom / version

    def pins(self, version, legacy=False):
        if not legacy:
            return d.custom_pins(version)
        urls = ['file://' + (self.legacy / version / f'{pkg}-{version}-1-x86_64.pkg.tar.zst').as_posix()
                for pkg in d.CORE_PACKAGES]
        return dict(version=version + '-1', urls=' '.join(urls))

    def receipt(self, slot, data, native=False):
        root = self.roots[slot]
        if native:
            path = root / 'usr/lib/steamos-nvidia/driver.conf'
            text = f'DRIVER_VERSION="{data["version"]}"\nPKG_URLS="{data["urls"]}"\nTRIM_CUDA=1\nEMPTY=""\n'
        else:
            path = root / 'usr/lib/decky-nvidia-update/installed.json'
            text = json.dumps(data)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_both_slots_preserved_and_only_unreferenced_build_removed(self):
        for version in ('610.1', '615.1', '590.1'):
            self.make_custom(version)
        self.receipt('A', self.pins('610.1'))
        self.receipt('B', self.pins('615.1'))
        result = c.maintain()
        self.assertEqual(result['removed'], [str(self.custom / '590.1')])
        for version in ('610.1', '615.1'):
            self.assertTrue((self.custom / version).exists())

    def test_native_only_and_different_native_and_decky_pins_are_all_retained(self):
        for version in ('610.1', '615.1', '590.1'):
            self.make_custom(version)
        self.receipt('A', self.pins('610.1'), native=True)
        self.receipt('B', self.pins('615.1'), native=True)
        self.receipt('B', self.pins('590.1'))
        self.assertEqual(c.maintain()['removed'], [])

    def test_bad_or_missing_slot_metadata_prevents_all_collection(self):
        folder = self.make_custom('615.1')
        path = self.receipt('B', self.pins('615.1'))
        for content in ('{bad', '[]', '{}', '{"version":"a","version":"b"}'):
            path.write_text(content)
            with self.subTest(content=content), self.assertRaises(ValueError):
                c.maintain()
            self.assertTrue(folder.exists())
        (self.roots['B'] / 'usr/lib/os-release').unlink()
        with self.assertRaisesRegex(ValueError, 'slot'):
            c.maintain()
        self.assertTrue(folder.exists())

    def test_unreadable_slot_prevents_all_collection(self):
        folder = self.make_custom('615.1')
        with patch.object(c, 'slot_references', side_effect=[set(), PermissionError('unreadable slot')]):
            with self.assertRaises(PermissionError):
                c.maintain()
        self.assertTrue(folder.exists())

    def test_missing_receipt_on_old_nvidia_install_is_uncertain(self):
        folder = self.make_custom('615.1')
        (self.roots['B'] / 'usr/lib/holo/pacmandb/local/nvidia-utils-615.1-1').mkdir()
        with self.assertRaisesRegex(ValueError, 'without saved pins'):
            c.maintain()
        self.assertTrue(folder.exists())

    def test_incomplete_native_pins_override_otherwise_valid_receipt(self):
        folder = self.make_custom('615.1')
        data = self.pins('615.1')
        self.receipt('B', data)
        path = self.receipt('B', data, native=True)
        path.write_text('DRIVER_VERSION=615.1-1\n')
        with self.assertRaises(ValueError):
            c.maintain()
        self.assertTrue(folder.exists())

    def test_ambiguous_native_shell_syntax_is_never_executed(self):
        self.make_custom('615.1')
        path = self.receipt('A', self.pins('615.1'), native=True)
        original = path.read_text()
        for line in ('PKG_URLS="$(touch /tmp/unsafe)"', 'source /tmp/unknown', 'DRIVER_VERSION=615.1-1'):
            path.write_text(original + line + '\n')
            with self.subTest(line=line), self.assertRaises(ValueError):
                c.slot_references(self.roots['A'])

    def test_legacy_copy_is_checksummed_and_original_kept_when_pinned(self):
        folder = self.make_legacy(extras=('opencl-nvidia', 'lib32-opencl-nvidia'))
        data = self.pins('615.1', legacy=True)
        original_receipt = self.receipt('A', data, native=True)
        before = original_receipt.read_bytes()
        result = c.maintain()
        self.assertEqual(result['removed'], [])
        self.assertTrue(folder.exists())
        self.assertEqual(original_receipt.read_bytes(), before)
        d.custom_pins('615.1')
        self.assertEqual(set(d.custom_manifest('615.1')['sha256']),
                         {f'{pkg}-615.1-1-x86_64.pkg.tar.zst' for pkg in d.CORE_PACKAGES})
        self.assertEqual(c.maintain()['removed'], [])  # repeat migration is idempotent

    def test_unreferenced_legacy_set_and_split_packages_are_collected(self):
        folder = self.make_legacy(extras=('opencl-nvidia', 'lib32-opencl-nvidia'))
        self.receipt('A', dict(version=VERSION, urls=URLS))
        result = c.maintain()
        self.assertCountEqual(result['removed'], [str(folder), str(self.custom / '615.1')])
        self.assertFalse(folder.exists())

    def test_startup_migrates_without_mounting_or_deleting(self):
        folder = self.make_legacy()
        self.assertEqual(c.maintain(collect=False)['removed'], [])
        self.mocks['slot_roots'].assert_not_called()
        self.assertTrue(folder.exists())
        d.custom_pins('615.1')

    def test_unreadable_slot_does_not_remove_legacy_original_after_copy(self):
        folder = self.make_legacy()
        with patch.object(c, 'slot_roots', side_effect=OSError('mount failed')):
            with self.assertRaises(OSError):
                c.maintain()
        self.assertTrue(folder.exists())
        d.custom_pins('615.1')

    def test_existing_different_build_never_overwritten_or_legacy_removed(self):
        self.make_custom('615.1')
        folder = self.make_legacy()
        name = f'nvidia-utils-615.1-1-x86_64.pkg.tar.zst'
        with (folder / name).open('ab') as stream:
            stream.write(b'different')
        before = (self.custom / '615.1/nvidia-utils' / name).read_bytes()
        self.assertEqual(c.maintain()['removed'], [])
        self.assertEqual((self.custom / '615.1/nvidia-utils' / name).read_bytes(), before)
        self.assertTrue(folder.exists())

    def test_unknown_legacy_contents_and_corrupt_packages_preserved(self):
        folder = self.make_legacy()
        extra = folder / 'user-data.txt'
        extra.write_text('keep')
        self.assertEqual(c.maintain()['removed'], [])
        self.assertTrue(extra.exists())
        extra.unlink()
        with patch.object(d, 'run', side_effect=subprocess.CalledProcessError(2, 'tar')):
            self.assertEqual(c.maintain()['removed'], [])
        self.assertTrue(folder.exists())
        self.assertFalse((self.custom / '615.1').exists())

    def test_wrong_legacy_package_identity_and_traversal_are_preserved(self):
        folder = self.make_legacy()
        with patch.object(d, 'run', return_value='pkgname = wrong\npkgver = 615.1-1\narch = x86_64\n'):
            self.assertEqual(c.maintain()['removed'], [])
        path = folder / 'build.json'
        data = json.loads(path.read_text())
        data['packages'].append('../escape.pkg.tar.zst')
        path.write_text(json.dumps(data))
        self.assertEqual(c.maintain()['removed'], [])
        self.assertTrue(folder.exists())

    def test_partial_copy_never_publishes_build(self):
        folder = self.make_legacy()
        with patch.object(c.shutil, 'copyfile', side_effect=OSError('out of space')):
            c.maintain(collect=False)
        self.assertTrue(folder.exists())
        self.assertEqual(list(self.custom.iterdir()), [])

    def test_legacy_only_pin_in_second_slot_keeps_original_and_migrated_copy(self):
        folder = self.make_legacy()
        self.receipt('B', self.pins('615.1', legacy=True))
        self.assertEqual(c.maintain()['removed'], [])
        self.assertTrue(folder.exists())
        self.assertTrue((self.custom / '615.1').exists())

    def test_missing_second_slot_prevents_collection(self):
        folder = self.make_custom('615.1')
        self.roots.pop('B')
        with self.assertRaisesRegex(ValueError, 'Both slot'):
            c.maintain()
        self.assertTrue(folder.exists())

    def test_unknown_local_pin_directory_prevents_collection(self):
        folder = self.make_custom('615.1')
        data = self.pins('615.1')
        data['urls'] = data['urls'].replace(self.custom.as_posix(), '/unknown/cache')
        self.receipt('A', data)
        with self.assertRaisesRegex(ValueError, 'Unknown local'):
            c.maintain()
        self.assertTrue(folder.exists())

    def test_cache_delete_failure_does_not_remove_other_candidates(self):
        for version in ('610.1', '615.1'):
            self.make_custom(version)
        with patch.object(c.shutil, 'rmtree', side_effect=OSError('read only')):
            with self.assertRaises(OSError):
                c.maintain()
        self.assertTrue((self.custom / '610.1').exists())
        self.assertTrue((self.custom / '615.1').exists())

    def test_unknown_retained_files_and_bad_hashes_preserved(self):
        folder = self.make_custom('615.1')
        (folder / 'unknown.txt').write_text('keep')
        self.assertEqual(c.maintain()['removed'], [])
        (folder / 'unknown.txt').unlink()
        name = f'nvidia-utils-615.1-1-x86_64.pkg.tar.zst'
        (folder / 'nvidia-utils' / name).write_bytes(b'changed')
        self.assertEqual(c.maintain()['removed'], [])

    def test_changed_pins_prevent_collection(self):
        folder = self.make_custom('615.1')
        with patch.object(c, 'slot_references', side_effect=[set(), set(), {folder}, set()]):
            with self.assertRaisesRegex(ValueError, 'pins changed'):
                c.maintain()
        self.assertTrue(folder.exists())

    def test_busy_or_locked_operations_prevent_migration_and_collection(self):
        folder = self.make_legacy()
        with patch.object(c, 'repair_locks', side_effect=BlockingIOError('busy')):
            with self.assertRaises(BlockingIOError):
                c.maintain()
        with patch.object(c, 'require_idle', side_effect=ValueError('busy')):
            with self.assertRaises(ValueError):
                c.maintain()
        self.assertTrue(folder.exists())
        self.assertEqual(list(self.custom.iterdir()), [])

    @unittest.skipUnless(ROOT_LINUX, 'requires Linux root ownership and symlinks')
    def test_real_trust_checks_keep_symlinks_hardlinks_and_writable_files(self):
        folder = self.make_legacy()
        outside = self.base / 'outside'
        outside.write_bytes(b'keep')
        name = f'nvidia-utils-615.1-1-x86_64.pkg.tar.zst'
        package = folder / name
        original = package.read_bytes()
        package.unlink()
        package.symlink_to(outside)
        self.assertEqual(c.maintain()['removed'], [])
        package.unlink()
        os.link(outside, package)
        self.assertEqual(c.maintain()['removed'], [])
        package.unlink()
        package.write_bytes(original)
        package.chmod(0o666)
        self.assertEqual(c.maintain()['removed'], [])
        self.assertEqual(outside.read_bytes(), b'keep')

    @unittest.skipUnless(ROOT_LINUX, 'requires Linux tar and root permissions')
    def test_real_tar_hash_and_atomic_publication(self):
        folder = self.make_legacy()
        with patch.object(d, 'run', side_effect=self.real_run):
            c.maintain(collect=False)
        for pkg in d.CORE_PACKAGES:
            name = f'{pkg}-615.1-1-x86_64.pkg.tar.zst'
            self.assertEqual((folder / name).read_bytes(), (self.custom / '615.1' / pkg / name).read_bytes())
        d.custom_pins('615.1')

    @unittest.skipUnless(ROOT_LINUX, 'requires Linux ownership and symlinks')
    def test_symlinked_or_writable_slot_metadata_prevents_collection(self):
        folder = self.make_custom('615.1')
        path = self.receipt('B', self.pins('615.1'))
        original = path.read_bytes()
        path.chmod(0o666)
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            c.maintain()
        path.chmod(0o644)
        path.unlink()
        outside = self.base / 'receipt'
        outside.write_bytes(original)
        path.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            c.maintain()
        self.assertTrue(folder.exists())

    def test_mounted_build_preserved(self):
        folder = self.make_custom('615.1')
        with patch.object(c.os.path, 'ismount', side_effect=lambda p: p == folder):
            self.assertEqual(c.maintain()['removed'], [])
        self.assertTrue(folder.exists())


class CacheGuards(unittest.TestCase):
    def test_dirty_or_unknown_btrfs_log_is_never_mounted(self):
        for superblock in ('log_root 123\n', 'unrecognized', 'log_root 0\nlog_root 0\n'):
            with self.subTest(superblock=superblock), \
                 patch.object(d, 'layout', return_value=('A', 'B', {'B/rootfs': '/fixture'})), \
                 patch.object(d, 'run', return_value=superblock) as run:
                with self.assertRaisesRegex(ValueError, 'log is dirty'), c.slot_roots():
                    self.fail('must not mount')
                self.assertEqual(run.call_count, 1)

    def test_collection_only_follows_successful_install_or_rollback(self):
        import sys
        fake_fcntl = types.SimpleNamespace(flock=lambda *args: None, LOCK_EX=2, LOCK_NB=4)
        lock = types.SimpleNamespace(fileno=lambda: 9)
        for action in ('install', 'install-custom', 'rollback', 'build'):
            for fails in (False, True):
                argv = ['driver.py', action] + (['615.1'] if action != 'rollback' else [])
                order = []
                def complete(*args, **kwargs):
                    order.append('operation')
                    if fails:
                        raise ValueError('injected failure')
                def maintain(*args, collect=True):
                    order.append('collect' if collect else 'migrate')
                operation = 'rollback' if action == 'rollback' else 'build_custom' if action == 'build' else 'install'
                with self.subTest(action=action, fails=fails), patch.object(sys, 'argv', argv), \
                     patch.dict(sys.modules, {'fcntl': fake_fcntl}), \
                     patch.object(d.os, 'geteuid', return_value=0, create=True), \
                     patch('builtins.open', return_value=nullcontext(lock)), \
                     patch.object(d, 'build_custom'), patch.object(d, operation, side_effect=complete), \
                     patch.object(d, 'cache_maintenance', side_effect=maintain):
                    if fails:
                        with self.assertRaises(ValueError):
                            d.main()
                        self.assertNotIn('collect', order)
                    else:
                        d.main()
                        if action == 'build':
                            self.assertEqual(order, ['migrate', 'operation'])
                        else:
                            self.assertEqual(order[-2:], ['operation', 'collect'])

    def test_transaction_and_updater_states(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(d, 'STATE', Path(temp)), \
             patch.object(c, 'PROC', Path(temp)):
            for status in ('preparing', 'failed', 'unknown', None):
                with self.subTest(status=status), patch.object(d, 'read_state', return_value=dict(status=status)), \
                     self.assertRaises(ValueError):
                    c.require_idle()
            for status in ('idle', 'ready', 'rollback-selected'):
                with self.subTest(status=status), patch.object(d, 'read_state', return_value=dict(status=status)), \
                     patch.object(d, 'run', side_effect=['idle', 's "idle"']):
                    c.require_idle()
            with patch.object(d, 'read_state', return_value=dict(status='ready')), \
                 patch.object(d, 'run', return_value='installing'), self.assertRaises(ValueError):
                c.require_idle()
            with patch.object(d, 'read_state', return_value=dict(status='idle')), \
                 patch.object(d, 'run', side_effect=['idle', 's "installing"']), self.assertRaises(ValueError):
                c.require_idle()
            (Path(temp) / 'prepare-abandoned').mkdir()
            with patch.object(d, 'read_state', return_value=dict(status='ready')), self.assertRaises(ValueError):
                c.require_idle()

    def test_legacy_process_defers_maintenance(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(d, 'STATE', Path(temp)), \
             patch.object(c, 'PROC', Path(temp)), patch.object(d, 'read_state', return_value=dict(status='idle')), \
             patch.object(d, 'run', side_effect=['idle', 's "idle"']):
            process = Path(temp) / '123'
            process.mkdir()
            (process / 'cmdline').write_bytes(b'/bin/bash\0/old/bin/steamos-nvidia-build.sh\0')
            with self.assertRaisesRegex(ValueError, 'legacy'):
                c.require_idle()

    def test_remote_pins_and_ambiguous_sources(self):
        self.assertEqual(c.pin_references(VERSION, URLS), set())
        for urls in ('', URLS + ' ' + URLS.split()[0], URLS.replace('https:', 'ftp:'),
                     URLS.replace('nvidia-utils-610.57.04', 'nvidia-utils-610.99.99')):
            with self.subTest(urls=urls), self.assertRaises(ValueError):
                c.pin_references(VERSION, urls)

    def test_cache_failure_is_best_effort_and_does_not_write_transaction(self):
        lock = types.SimpleNamespace(fileno=lambda: 9)
        with patch.object(d.subprocess, 'run', side_effect=OSError('mount failed')), patch.object(d, 'write_json') as write:
            self.assertTrue(d.cache_maintenance(lock)['deferred'])
            write.assert_not_called()

    def test_worker_inherits_update_lock_and_uses_private_namespace(self):
        lock = types.SimpleNamespace(fileno=lambda: 9)
        with patch.object(d.subprocess, 'run', return_value=types.SimpleNamespace(stdout='{}')) as run:
            self.assertEqual(d.cache_maintenance(lock, collect=False), {})
            args, kwargs = run.call_args
            self.assertEqual(args[0][:4], ['unshare', '--mount', '--propagation', 'private'])
            self.assertEqual(kwargs['pass_fds'], (9,))
            self.assertEqual(args[0][-3:], ['--lock-fd', '9', '--migrate-only'])

    @unittest.skipUnless(ROOT_LINUX, 'requires Linux flock')
    def test_real_repair_lock_contention_releases_other_lock(self):
        import fcntl
        with tempfile.TemporaryDirectory() as temp:
            paths = [str(Path(temp) / name) for name in ('decky.lock', 'native.lock')]
            with patch.object(c, 'REPAIR_LOCKS', paths), open(paths[1], 'a') as other:
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaises(BlockingIOError), c.repair_locks():
                    self.fail('must not enter')
                with open(paths[0], 'a') as first:
                    fcntl.flock(first, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(c, 'REPAIR_LOCKS', paths), c.repair_locks():
                with open(paths[1], 'a') as contender, self.assertRaises(BlockingIOError):
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)


if __name__ == '__main__':
    unittest.main()
