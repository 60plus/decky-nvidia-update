import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_standalone import d, VALVE


class RetainedPackages(unittest.TestCase):
    VERSION = '615.71.09'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / 'custom'
        self.folder = self.cache / self.VERSION
        self.folder.mkdir(parents=True)
        self.names = [f'{pkg}-{self.VERSION}-1-x86_64.pkg.tar.zst' for pkg in d.CORE_PACKAGES]
        for pkg, name in zip(d.CORE_PACKAGES, self.names):
            (self.folder / pkg).mkdir()
            (self.folder / pkg / name).write_bytes(('package ' + name).encode())
        self.data = dict(version=self.VERSION, packages=self.names, built_at='now',
                         sha256={name: d.file_hash(self.folder / pkg / name)
                                 for pkg, name in zip(d.CORE_PACKAGES, self.names)})
        self.save()
        for name, value in [('STATE', self.root), ('CUSTOM', self.cache)]:
            patcher = patch.object(d, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # Ownership/mode checks are covered on the Linux host, not Windows ACLs.
        for name in ('private_dir', 'secure_file'):
            patcher = patch.object(d, name)
            patcher.start()
            self.addCleanup(patcher.stop)

    def save(self):
        (self.folder / 'build.json').write_text(json.dumps(self.data))

    def test_complete_local_set_and_roundtrip_installed_receipt(self):
        data = d.custom_pins(self.VERSION)
        self.assertEqual(len(d.package_pins(data)), 3)
        root = self.root / 'target'
        post = root / 'usr/lib/rauc/post-install.sh'
        post.parent.mkdir(parents=True)
        post.write_text(VALVE)
        with patch.object(d, 'write_json', side_effect=lambda p, v: p.write_text(json.dumps(v))):
            d.record(root, data['version'], 'test-kernel', data['urls'])
        saved = json.loads((root / 'usr/lib/decky-nvidia-update/installed.json').read_text())
        self.assertEqual(saved['sha256'], data['sha256'])
        self.assertEqual(d.package_pins(saved), d.package_pins(data))

    def test_changed_or_missing_packages_fail_without_fallback(self):
        data = d.custom_pins(self.VERSION)
        path = self.folder / 'nvidia-utils' / self.names[0]
        path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            d.package_pins(data)
        path.unlink()
        with self.assertRaises(OSError):
            d.package_pins(data)

    def test_installed_checksum_cannot_be_changed_with_manifest(self):
        data = d.custom_pins(self.VERSION)
        (self.folder / 'nvidia-utils' / self.names[0]).write_bytes(b'new content')
        self.data['sha256'][self.names[0]] = d.file_hash(self.folder / 'nvidia-utils' / self.names[0])
        self.save()
        with self.assertRaisesRegex(ValueError, 'Saved custom package checksum'):
            d.package_pins(data)

    def test_traversal_foreign_files_and_mixed_versions_refused(self):
        data = d.custom_pins(self.VERSION)
        for bad in ('file:///tmp/nvidia-utils-615.71.09-1-x86_64.pkg.tar.zst',
                    data['urls'].split()[0].replace('/custom/', '/custom/../'),
                    data['urls'].split()[0].replace(self.names[0], 'other.pkg.tar.zst')):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                d.package_pins(dict(version=data['version'], urls=bad))

    def test_incomplete_manifest_is_not_offered(self):
        self.data['packages'].pop()
        self.save()
        self.assertEqual(d.list_custom(), [])

    def test_existing_build_is_verified_and_never_overwritten(self):
        with patch.object(d.subprocess, 'run') as run, patch.object(d, 'preflight') as preflight:
            d.build_custom(self.VERSION)
        run.assert_not_called()
        preflight.assert_not_called()
        (self.folder / 'nvidia-utils' / self.names[0]).write_bytes(b'changed')
        with patch.object(d.subprocess, 'run') as run, self.assertRaisesRegex(ValueError, 'checksum'):
            d.build_custom(self.VERSION)
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
