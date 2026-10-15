#!/usr/bin/python3 -I
"""Keep EFI available to the tested Valve late-shutdown migration.

The override lives only in /run: it protects this update's shutdown and expires
at reboot, so it cannot override a future OS version's service definition.
"""
import hashlib
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile

UNIT = 'holo-post-update-shutdown.service'
POST = '/usr/lib/rauc/post-install.sh'
TRIGGER = '/run/steamos-atomupd/holo-booted-slot-sync-trigger'
VENDOR = '/usr/lib/systemd/system/' + UNIT
DROPIN = '/run/systemd/system/' + UNIT + '.d/60-decky-nvidia-efi.conf'
# SteamOS Main 3.10 / steamos-customizations 20260916.1-2. Do not guess at
# unknown shutdown implementations or replace a user's service override.
KNOWN = {
    VENDOR: 'e6cac8540994f30e43b9338c7f0032dc1b12ea46016e1c9b3dfed2ad3658c23b',
    '/usr/lib/holo/holo-post-update-shutdown':
        'ccf727a15b0faa68a500319aced44c90b647f63a8bc062e89fa8be6048e68803',
}
COMMAND = (
    'test -f /efi/SteamOS/partsets/self || '
    'mount -t vfat -o umask=0077 /dev/disk/by-partsets/self/efi /efi; '
    'test -d /esp/SteamOS/conf || '
    'mount -t vfat -o umask=0077 /dev/disk/by-partsets/shared/esp /esp; '
    'test -f /efi/SteamOS/partsets/self; test -d /esp/SteamOS/conf; '
    'exec /usr/lib/holo/holo-post-update-shutdown'
)
CONTENT = (
    '# Temporary Decky NVIDIA update guard; removed by reboot.\n'
    '[Service]\nExecStart=\n'
    "ExecStart=/usr/bin/unshare --mount --propagation private /bin/bash -ec '"
    + COMMAND + "'\n"
)


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def safe_path(path, directory=False):
    """Reject symlinks and writable/unowned components before writing as root."""
    for component in reversed((path, *path.parents)):
        if not component.exists() and not component.is_symlink():
            continue
        info = component.lstat()
        kind = stat.S_ISDIR if component != path or directory else stat.S_ISREG
        if not kind(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError(f'Unsafe shutdown guard path: {component}')


def check():
    if TRIGGER not in Path(POST).read_text():
        return False  # Older Stable layout has no late shutdown migration.
    for name, expected in KNOWN.items():
        path = Path(name)
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Unsupported Valve shutdown implementation; update stopped before staging: ' + name)
    safe_path(Path(DROPIN))
    path = Path(DROPIN)
    if path.exists() and path.read_text() != CONTENT:
        raise ValueError('Existing shutdown guard has different contents; refusing to overwrite it')
    state = dict(line.split('=', 1) for line in run('systemctl', 'show', UNIT,
                 '-p', 'FragmentPath', '-p', 'DropInPaths', '-p', 'ExecStart').splitlines())
    if state.get('FragmentPath') != VENDOR:
        raise ValueError('Valve shutdown service is masked or overridden')
    drops = shlex.split(state.get('DropInPaths', ''))
    if any(name != DROPIN for name in drops) or (drops and not path.exists()):
        raise ValueError('Custom shutdown service drop-ins found; refusing to replace them')
    executable = '/usr/bin/unshare' if drops else '/usr/lib/holo/holo-post-update-shutdown'
    if f'path={executable} ;' not in state.get('ExecStart', ''):
        raise ValueError('Unexpected effective shutdown command')
    for name in ('/usr/bin/unshare', '/bin/bash', '/usr/bin/mount'):
        if not os.access(name, os.X_OK):
            raise ValueError('Missing shutdown guard tool: ' + name)
    return True


def install():
    if not check():
        return
    # Refresh the manager before checking again; pick up external changes too.
    run('systemctl', 'daemon-reload')
    check()
    path = Path(DROPIN)
    created = False
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        safe_path(path.parent, directory=True)
        fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w') as out:
                out.write(CONTENT)
                out.flush()
                os.fchmod(out.fileno(), 0o644)
                os.fsync(out.fileno())
            # Link atomically without overwriting a file created concurrently.
            os.link(temporary, path)
            created = True
        finally:
            Path(temporary).unlink()
    try:
        run('systemctl', 'daemon-reload')
        run('systemd-analyze', 'verify', UNIT)
        check()
        drops = shlex.split(run('systemctl', 'show', UNIT, '-p', 'DropInPaths', '--value'))
        if drops != [DROPIN]:
            raise ValueError('Temporary shutdown guard was not loaded')
    except BaseException:
        # Never remove a pre-existing file or a concurrently changed override.
        if created and not path.is_symlink() and path.read_text() == CONTENT:
            path.unlink()
            run('systemctl', 'daemon-reload')
        raise
    print('EFI shutdown guard ready (temporary until reboot).', flush=True)


if __name__ == '__main__':
    if sys.argv[1:] == ['check']:
        check()
    elif sys.argv[1:] == ['install']:
        install()
    else:
        raise SystemExit('Expected check or install')
