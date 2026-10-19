"""Exercise the staging script's real rsync command against configuration trees."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'posix' and shutil.which('bash') and shutil.which('rsync'),
                     'Requires Linux Bash and rsync')
class ConfigurationClone(unittest.TestCase):
    def clone(self, live, work):
        # Substitute only the live source path so the production command can run
        # without mounts, disk writes, or access to the host's real /etc.
        lines = [line for line in (ROOT / 'bin/standalone/stage.sh').read_text().splitlines()
                 if line.startswith('rsync ') and ' /etc/ ' in line]
        self.assertEqual(len(lines), 1)
        command = lines[0].replace(' /etc/ ', ' "$LIVE_ETC/" ')
        return subprocess.run(['bash', '-eu', '-c', command],
                              env={**os.environ, 'LIVE_ETC': str(live), 'WORK': str(work)},
                              capture_output=True, text=True)

    def test_deleted_base_configuration_does_not_return_in_clone(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            live = base / 'live'
            destination = base / 'work/root/etc'
            live.mkdir()
            destination.mkdir(parents=True)
            (live / 'kept.conf').write_text('current setting\n')
            (destination / 'kept.conf').write_text('old setting\n')
            (destination / 'deleted.conf').write_text('removed by the user\n')
            (destination / 'removed.d').mkdir()
            (destination / 'removed.d/stale.conf').write_text('obsolete\n')
            outside = base / 'outside.conf'
            outside.write_text('must remain unchanged\n')
            (live / 'linked.conf').symlink_to(outside)
            result = self.clone(live, base / 'work')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(sorted(p.name for p in destination.iterdir()), ['kept.conf', 'linked.conf'])
            self.assertEqual((destination / 'kept.conf').read_text(), 'current setting\n')
            self.assertTrue((destination / 'linked.conf').is_symlink())
            self.assertEqual((destination / 'linked.conf').readlink(), outside)
            self.assertEqual(outside.read_text(), 'must remain unchanged\n')
            self.assertEqual((live / 'kept.conf').read_text(), 'current setting\n')

    def test_unreadable_source_fails_without_deleting_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            destination = base / 'work/root/etc'
            destination.mkdir(parents=True)
            retained = destination / 'retained.conf'
            retained.write_text('preserve on source failure\n')
            result = self.clone(base / 'missing-source', base / 'work')
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(retained.read_text(), 'preserve on source failure\n')
