#!/usr/bin/python3
"""Root-only SteamOS probe. Hides EFI in a child namespace; never migrates slots."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile

spec = importlib.util.spec_from_file_location('shutdown', Path(__file__).resolve().parents[1] / 'bin/standalone/shutdown.py')
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
assert os.geteuid() == 0, 'Run this hardware probe as root on SteamOS'
slot = subprocess.check_output(['holo-bootconf', 'this-image'], text=True).strip()
before = subprocess.check_output(['findmnt', '-rn', '-M', '/efi', '-o', 'ID,SOURCE,FSTYPE'])
prefix = s.COMMAND.removesuffix('exec /usr/lib/holo/holo-post-update-shutdown')
probe = prefix + 'holo-bootconf this-image'
body = r'''
from pathlib import Path
import subprocess
import sys
mode, command, temp, slot = sys.argv[1:]
if mode != 'mounted':
    for name in ('efi', 'esp'):
        empty = Path(temp) / name
        empty.mkdir()
        subprocess.run(['mount', '--bind', str(empty), '/' + name], check=True)
    original = subprocess.run(['holo-bootconf', 'this-image'], capture_output=True, text=True)
    assert original.returncode != 0 and "not found in efi dir '/efi'" in original.stderr
if mode == 'missing-device':
    command = command.replace('/dev/disk/by-partsets/self/efi', '/dev/decky-efi-probe-nonexistent')
result = subprocess.run(['/bin/bash', '-ec', command], capture_output=True, text=True)
if mode == 'missing-device':
    assert result.returncode != 0 and not result.stdout.strip(), result
else:
    assert result.returncode == 0 and result.stdout.strip() == slot, result
'''
for mode in ('mounted', 'hidden-mounts', 'missing-device'):
    temp = Path(tempfile.mkdtemp(prefix='decky-efi-probe-', dir='/run'))
    try:
        subprocess.run(['unshare', '--mount', '--propagation', 'private', 'python3', '-B', '-c',
                        body, mode, probe, str(temp), slot], check=True)
    finally:
        # The child namespace has exited. Never recurse through a mounted path.
        for name in ('efi', 'esp'):
            if (temp / name).exists():
                (temp / name).rmdir()
        temp.rmdir()
    print('PASS:', mode, flush=True)
assert before == subprocess.check_output(['findmnt', '-rn', '-M', '/efi', '-o', 'ID,SOURCE,FSTYPE'])
print('PASS: host EFI mount unchanged; no slot migration was run.', flush=True)
