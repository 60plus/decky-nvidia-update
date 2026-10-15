import hashlib
import importlib.util
import os
from pathlib import Path
import shlex
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('shutdown', ROOT / 'bin/standalone/shutdown.py')
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)


class ShutdownGuard(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.post = self.root / 'post-install.sh'
        self.vendor = self.root / s.UNIT
        self.script = self.root / 'holo-post-update-shutdown'
        self.drop = self.root / 'run/systemd/system' / (s.UNIT + '.d') / '60-decky-nvidia-efi.conf'
        self.post.write_text(s.TRIGGER)
        self.vendor.write_text('vendor unit fixture')
        self.script.write_text('vendor script fixture')
        self.drops = []
        self.fragment = str(self.vendor)
        self.fail_verify = False
        self.ignore_drop = False
        for key, value in dict(POST=str(self.post), VENDOR=str(self.vendor), DROPIN=str(self.drop),
                               KNOWN={str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                      for p in (self.vendor, self.script)}).items():
            self.enterContext(patch.object(s, key, value))
        self.path_check = self.enterContext(patch.object(s, 'safe_path'))
        self.enterContext(patch.object(s.os, 'access', return_value=True))
        self.calls = self.enterContext(patch.object(s, 'run', side_effect=self.manager))

    def manager(self, *args):
        if args == ('systemctl', 'daemon-reload'):
            self.drops = [str(self.drop)] if self.drop.exists() and not self.ignore_drop else []
            return ''
        if args[0] == 'systemd-analyze':
            if self.fail_verify:
                raise subprocess.CalledProcessError(1, args)
            return ''
        if args[-1] == '--value':
            return ' '.join(self.drops)
        executable = '/usr/bin/unshare' if self.drops else '/usr/lib/holo/holo-post-update-shutdown'
        return (f'FragmentPath={self.fragment}\nDropInPaths={" ".join(self.drops)}\n'
                f'ExecStart={{ path={executable} ; argv[]=fixture ; }}')

    def test_stable_has_no_override_or_manager_calls(self):
        self.post.write_text('steamos-finalize-install --no-kernel')
        s.install()
        self.assertFalse(self.drop.exists())
        self.calls.assert_not_called()

    def test_check_is_read_only(self):
        self.assertTrue(s.check())
        self.assertFalse(self.drop.exists())
        self.assertFalse(any(c.args[1] == 'daemon-reload' for c in self.calls.call_args_list))

    def test_install_and_repeat_are_idempotent(self):
        s.install()
        self.assertEqual(self.drop.read_text(), s.CONTENT)
        before = self.drop.stat().st_mtime_ns
        s.install()
        self.assertEqual(self.drop.stat().st_mtime_ns, before)
        self.assertEqual(list(self.drop.parent.iterdir()), [self.drop])

    def test_unknown_vendor_unit_is_refused(self):
        self.vendor.write_text('updated unit')
        with self.assertRaisesRegex(ValueError, 'Unsupported Valve'):
            s.install()
        self.assertFalse(self.drop.exists())

    def test_unknown_vendor_script_is_refused(self):
        self.script.write_text('updated script')
        with self.assertRaisesRegex(ValueError, 'Unsupported Valve'):
            s.install()
        self.assertFalse(self.drop.exists())

    def test_masked_service_is_refused(self):
        self.fragment = '/dev/null'
        with self.assertRaisesRegex(ValueError, 'masked or overridden'):
            s.install()

    def test_custom_dropins_are_not_replaced(self):
        self.drops = ['/etc/systemd/system/' + s.UNIT + '.d/custom.conf']
        with self.assertRaisesRegex(ValueError, 'Custom shutdown'):
            s.install()
        self.assertFalse(self.drop.exists())

    def test_conflicting_file_is_preserved(self):
        self.drop.parent.mkdir(parents=True)
        self.drop.write_text('user content')
        with self.assertRaisesRegex(ValueError, 'different contents'):
            s.install()
        self.assertEqual(self.drop.read_text(), 'user content')

    def test_failed_verification_removes_only_new_override(self):
        self.fail_verify = True
        with self.assertRaises(subprocess.CalledProcessError):
            s.install()
        self.assertFalse(self.drop.exists())
        self.assertEqual(self.drops, [])

    def test_failed_verification_preserves_existing_override(self):
        s.install()
        self.fail_verify = True
        with self.assertRaises(subprocess.CalledProcessError):
            s.install()
        self.assertEqual(self.drop.read_text(), s.CONTENT)

    def test_unloaded_override_is_failure(self):
        self.ignore_drop = True
        with self.assertRaisesRegex(ValueError, 'was not loaded'):
            s.install()
        self.assertFalse(self.drop.exists())

    def test_missing_tool_is_failure(self):
        with patch.object(s.os, 'access', return_value=False):
            with self.assertRaisesRegex(ValueError, 'Missing shutdown guard tool'):
                s.install()
        self.assertFalse(self.drop.exists())


class PathSafety(unittest.TestCase):
    def check_mode(self, mode, uid=0):
        info = os.stat_result((mode, 0, 0, 1, uid, 0, 0, 0, 0, 0))
        with patch.object(Path, 'exists', return_value=True), patch.object(Path, 'lstat', return_value=info):
            s.safe_path(Path('/'), directory=True)

    def test_safe_root_directory(self):
        self.check_mode(stat.S_IFDIR | 0o755)

    def test_writable_directory_refused(self):
        with self.assertRaisesRegex(ValueError, 'Unsafe shutdown guard path'):
            self.check_mode(stat.S_IFDIR | 0o777)

    def test_unowned_directory_refused(self):
        with self.assertRaisesRegex(ValueError, 'Unsafe shutdown guard path'):
            self.check_mode(stat.S_IFDIR | 0o755, uid=1000)

    def test_symlink_directory_refused(self):
        with self.assertRaisesRegex(ValueError, 'Unsafe shutdown guard path'):
            self.check_mode(stat.S_IFLNK | 0o777)


class Invocation(unittest.TestCase):
    def test_namespace_wraps_original_valve_script(self):
        line = next(x for x in s.CONTENT.splitlines() if x.startswith('ExecStart=/'))
        argv = shlex.split(line.split('=', 1)[1])
        self.assertEqual(argv[:6], ['/usr/bin/unshare', '--mount', '--propagation', 'private', '/bin/bash', '-ec'])
        self.assertEqual(argv[6], s.COMMAND)
        self.assertTrue(s.COMMAND.endswith('exec /usr/lib/holo/holo-post-update-shutdown'))
        self.assertNotIn('RequiresMountsFor', s.CONTENT)


if __name__ == '__main__':
    unittest.main()
