import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('driver', ROOT / 'bin/standalone/driver.py')
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)
VALVE = '''#!/bin/bash
steamos-chroot --partset $UPDATED_SLOT -- steamos-finalize-install --no-kernel
(( ERR == 0 )) && steamos-bootconf --image $UPDATED_SLOT set-mode reboot
'''
VERSION = '610.57.04-1'
URLS = ' '.join(f'https://archive.archlinux.org/packages/{p[0]}/{p}/{p}-{VERSION}-x86_64.pkg.tar.zst'
                for p in ('nvidia-utils', 'nvidia-open-dkms', 'lib32-nvidia-utils'))


class RootDevice(unittest.TestCase):
    FSID = '7c1d2c2b-46d3-41e7-b7ad-e58ecc100598'

    def registry(self, root, devices):
        directory = root / self.FSID / 'devices'
        directory.mkdir(parents=True)
        for name, device in devices.items():
            (directory / name).mkdir()
            (directory / name / 'dev').write_text(device + '\n')

    def test_btrfs_uses_backing_device_not_anonymous_mount_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.registry(root, {'sda4': '8:4'})
            with patch.object(d, 'BTRFS_SYSFS', root), patch.object(d, 'run', side_effect=['btrfs', self.FSID]) as run:
                self.assertEqual(d.mounted_root_device(), '8:4')
                self.assertNotIn('MAJ:MIN', str(run.call_args_list))

    def test_missing_or_multiple_devices_refused(self):
        for devices in ({}, {'sda4': '8:4', 'sdb4': '8:20'}):
            with self.subTest(devices=devices), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.registry(root, devices)
                with patch.object(d, 'BTRFS_SYSFS', root), patch.object(d, 'run', side_effect=['btrfs', self.FSID]):
                    with self.assertRaisesRegex(ValueError, 'exactly one'):
                        d.mounted_root_device()

    def test_unsupported_filesystem_and_invalid_uuid_refused(self):
        for values in (['ext4'], ['btrfs', '../devices']):
            with self.subTest(values=values), patch.object(d, 'run', side_effect=values):
                with self.assertRaises(ValueError):
                    d.mounted_root_device()


class ImageRelease(unittest.TestCase):
    FSID = RootDevice.FSID

    def test_detached_and_unregistered_image_passes(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(d, 'BTRFS_SYSFS', Path(tmp)), patch.object(d, 'run', return_value=''):
            d.require_image_released('/private/root.img', self.FSID, timeout=0)

    def test_loop_or_registered_filesystem_blocks_write(self):
        for loop, registered in (('/dev/loop1: [backing image]', False), ('', True)):
            with self.subTest(loop=loop, registered=registered), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                if registered:
                    (root / self.FSID).mkdir()
                with patch.object(d, 'BTRFS_SYSFS', root), patch.object(d, 'run', return_value=loop):
                    with self.assertRaisesRegex(ValueError, 'refusing to write'):
                        d.require_image_released('/private/root.img', self.FSID, timeout=0)

    def test_waits_for_deferred_loop_detach(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(d, 'BTRFS_SYSFS', Path(tmp)), patch.object(d, 'run', side_effect=['/dev/loop1', '']), patch.object(d.time, 'sleep') as sleep:
            d.require_image_released('/private/root.img', self.FSID)
            sleep.assert_called_once_with(0.1)

    def test_bad_uuid_rejected_before_system_calls(self):
        with patch.object(d, 'run') as run, self.assertRaises(ValueError):
            d.require_image_released('/private/root.img', '../devices')
        run.assert_not_called()


class Hooks(unittest.TestCase):
    def test_standalone_hook_is_before_activation_and_idempotent(self):
        result = d.hook_text(VALVE, '/bin/bash /usr/lib/decky-nvidia-update/repair.sh "$UPDATED_SLOT"')
        self.assertLess(result.index('NVIDIA preparation failed'), result.index('set-mode reboot'))
        self.assertIn('exec /usr/bin/unshare', result)
        self.assertEqual(d.hook_text(result, '/bin/bash /usr/lib/decky-nvidia-update/repair.sh "$UPDATED_SLOT"'), result)

    def test_unsupported_or_duplicate_activation_rejected(self):
        for text in (VALVE.replace('set-mode reboot', 'set-mode new-mode'),
                     VALVE + d.ANCHORS[0], VALVE.replace('steamos-finalize-install', 'unknown'),
                     '#!/bin/sh\n' + VALVE, '\n'.join(reversed(VALVE.splitlines()))):
            with self.subTest(text=text), self.assertRaises(ValueError):
                d.hook_text(text, '/bin/true')

    def test_native_policy_preserved_on_destination(self):
        native = VALVE.replace(d.ANCHORS[0], d.NATIVE_START + '/native/repair.sh other\n' + d.NATIVE_END + d.ANCHORS[0])
        self.assertEqual(d.hook_text(native, '/standalone'), native)
        manual = d.hook_text(native, '/standalone', temporary=True)
        self.assertNotIn('/native/repair.sh', manual)
        self.assertIn('/standalone', manual)
        self.assertIn('/native/repair.sh', native)

    def test_truncated_hook_rejected(self):
        with self.assertRaises(ValueError):
            d.hook_text(VALVE + d.HOOK_START, '/bin/true')


class Pins(unittest.TestCase):
    def test_exact_complete_set(self):
        values = d.package_pins(dict(version=VERSION, urls=URLS))
        self.assertEqual([v[0] for v in values], ['nvidia-utils', 'nvidia-open-dkms', 'lib32-nvidia-utils'])

    def test_reject_missing_duplicate_skew_and_local_urls(self):
        for urls in (' '.join(URLS.split()[:2]), URLS + ' ' + URLS.split()[0],
                     URLS.replace('lib32-nvidia-utils-610.57.04', 'lib32-nvidia-utils-610.99.00'),
                     URLS.replace('https://archive.archlinux.org', 'file:///tmp'),
                     URLS + '\n$(touch /tmp/oops)'):
            with self.subTest(urls=urls), self.assertRaises(ValueError):
                d.package_pins(dict(version=VERSION, urls=urls))

    def test_different_companion_pkgrel_allowed(self):
        urls = URLS.replace('lib32-nvidia-utils-610.57.04-1', 'lib32-nvidia-utils-610.57.04-2')
        self.assertEqual(d.package_pins(dict(version=VERSION, urls=urls))[2][1], '610.57.04-2')

    def test_record_preserves_native_config_and_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            native = root / 'usr/lib/steamos-nvidia/driver.conf'
            native.parent.mkdir(parents=True)
            native.write_text("DRIVER_SPEC=old\nDRIVER_VERSION=old\nPKG_URLS=old\nTRIM_CUDA=1\nADD_XPADNEO=1\nXPADNEO_VERSION=v0.10.4\nXPADNEO_SHA256='abc'\n")
            post = root / 'usr/lib/rauc/post-install.sh'
            post.parent.mkdir(parents=True)
            post.write_text(VALVE)
            with patch.object(d, 'write_json', side_effect=lambda p, v: p.write_text(json.dumps(v))):
                d.record(tmp, VERSION, '6.16-neptune', URLS)
            text = native.read_text()
            self.assertIn('TRIM_CUDA=1', text)
            self.assertIn('ADD_XPADNEO=1', text)
            self.assertIn("XPADNEO_SHA256='abc'", text)
            self.assertIn('DRIVER_VERSION=' + VERSION, text)
            self.assertIn(d.HOOK_START, post.read_text())
            self.assertTrue((root / 'usr/lib/decky-nvidia-update/driver.py').is_file())


class Pruning(unittest.TestCase):
    def setup_tree(self, root):
        lib = root / 'usr/lib'
        db = lib / 'holo/pacmandb/local'
        for package, files in [('nvidia-utils-1-1', ['usr/lib/old.so', 'usr/lib/shared.so', 'usr/lib/keep.so']),
                               ('other-1-1', ['usr/lib/shared.so'])]:
            directory = db / package
            directory.mkdir(parents=True)
            (directory / 'files').write_text('%FILES%\n' + '\n'.join(files) + '\n')
        for name in ('old.so', 'shared.so', 'keep.so', 'unrelated.so'):
            (lib / name).write_text('payload')
        wanted = root / 'wanted'
        wanted.write_text('usr/lib/keep.so\n')
        return lib, db, wanted

    def test_reclaims_only_obsolete_unshared_driver_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lib, db, wanted = self.setup_tree(root)
            d.prune(root, wanted)
            self.assertFalse((lib / 'old.so').exists())
            for name in ('shared.so', 'keep.so', 'unrelated.so'):
                self.assertTrue((lib / name).exists())

    def test_bad_manifest_fails_before_any_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lib, db, wanted = self.setup_tree(root)
            with (db / 'nvidia-utils-1-1/files').open('a') as out:
                out.write('../../outside\n')
            with self.assertRaises(ValueError):
                d.prune(root, wanted)
            self.assertTrue((lib / 'old.so').exists())


class Transactions(unittest.TestCase):
    def check_install_failure(self, wrote):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            previous = dict(status='ready', source='B', target='A')
            info = dict(enough_space=True, source='A', target='B', devices={'A/rootfs': '/dev/source'})
            def fail(command, **kwargs):
                if wrote:
                    (Path(command[-1]) / 'writing').touch()
                raise RuntimeError('injected staging failure')
            with patch.object(d, 'STATE', state), patch.object(d, 'private_dir'), patch.object(d, 'read_state', return_value=previous), patch.object(d, 'preflight', return_value=info), patch.object(d, 'run', return_value='source-uuid'), patch.object(d, 'root_identity', return_value='identity'), patch.object(d, 'write_json', side_effect=lambda p, v: p.write_text(json.dumps(v))), patch.object(d.subprocess, 'run', side_effect=fail), patch.object(d, 'protect') as protect:
                with self.assertRaisesRegex(RuntimeError, 'injected'):
                    d.install(VERSION)
                saved = json.loads((state / 'transaction.json').read_text())
                if wrote:
                    protect.assert_called_once_with('B')
                    self.assertEqual(saved['status'], 'failed')
                else:
                    protect.assert_not_called()
                    self.assertEqual(saved, previous)

    def test_failed_build_preserves_previous_rollback(self):
        self.check_install_failure(False)

    def test_failed_write_disables_target_and_records_failure(self):
        self.check_install_failure(True)

    def test_bad_driver_request_has_no_system_calls(self):
        with patch.object(d, 'preflight') as preflight:
            for value in ('../610', '610.1;reboot', '610.1\n', ''):
                with self.assertRaises(ValueError):
                    d.install(value)
            preflight.assert_not_called()

    def test_low_space_stops_before_creating_transaction(self):
        with patch.object(d, 'preflight', return_value={'enough_space': False, 'required_bytes': 17 * 1024**3}), patch.object(d, 'private_dir') as mkdir:
            with self.assertRaisesRegex(ValueError, 'Need'):
                d.install(VERSION)
            mkdir.assert_not_called()

    def test_rollback_refuses_replaced_layout(self):
        with patch.object(d, 'read_state', return_value=dict(status='ready', source='A', target='B', devices={'old': 'disk'})), patch.object(d, 'layout', return_value=('A', 'B', {'new': 'disk'})), patch.object(d.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'layout'):
                d.rollback()
            run.assert_not_called()

    def test_rollback_rejects_failed_transaction(self):
        with patch.object(d, 'read_state', return_value=dict(status='failed')), patch.object(d, 'layout') as layout:
            with self.assertRaises(ValueError):
                d.rollback()
            layout.assert_not_called()

    def test_identity_changes_with_modules_and_uuid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / d.MANIFEST
            manifest.parent.mkdir(parents=True)
            manifest.write_text('{"buildid":"test"}')
            module = root / 'usr/lib/modules/test/updates/dkms/nvidia.ko.zst'
            module.parent.mkdir(parents=True)
            module.write_bytes(b'old')
            before = d.root_identity(root, 'uuid-a')
            self.assertNotEqual(before, d.root_identity(root, 'uuid-b'))
            module.write_bytes(b'new')
            self.assertNotEqual(before, d.root_identity(root, 'uuid-a'))


if __name__ == '__main__':
    unittest.main()
