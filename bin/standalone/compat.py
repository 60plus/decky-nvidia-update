#!/usr/bin/python3 -I
"""Check the installed GPU against NVIDIA's table for the selected release.

GPU matching adapted from steamos-nvidia-installer/scripts/driver-manager.py.
"""
import html
from pathlib import Path
import re
import subprocess
import sys


def supported_devices(document):
    section = re.split(r'<h3[^>]*>\s*Current NVIDIA GPUs\s*</h3>', document, maxsplit=1, flags=re.I)
    if len(section) != 2:
        raise ValueError('NVIDIA GPU support table is unavailable')
    result = []
    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', section[1].split('</table>', 1)[0], re.S | re.I):
        cells = [html.unescape(re.sub(r'<[^>]+>', '', cell)).strip()
                 for cell in re.findall(r'<td[^>]*>(.*?)</td>', row, re.S | re.I)]
        if len(cells) < 2 or not re.search(r'GeForce (GTX 16|RTX [2-9]0)[0-9]{2}', cells[0]):
            continue
        ids = tuple(cells[1].upper().split())
        if len(ids) in (1, 3) and all(re.fullmatch(r'[0-9A-F]{4}', x) for x in ids):
            result.append(ids)
    return result


def check(version, root=Path('/sys/bus/pci/devices')):
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]+)+', version):
        raise ValueError('Invalid NVIDIA version')
    devices = []
    for path in root.iterdir():
        if (path / 'vendor').read_text().strip() == '0x10de' and (path / 'class').read_text().startswith('0x03'):
            devices.append(tuple((path / f).read_text().strip().removeprefix('0x').upper()
                                 for f in ('device', 'subsystem_vendor', 'subsystem_device')))
    if not devices:
        raise ValueError('No NVIDIA display adapter found')
    document = subprocess.check_output(['curl', '--fail', '--silent', '--show-error', '--location',
                                       '--connect-timeout', '15', '--max-time', '60', '--retry', '2',
                                       f'https://download.nvidia.com/XFree86/Linux-x86_64/{version}/README/supportedchips.html'], text=True)
    supported = supported_devices(document)
    if not all(any(ids == device or (len(ids) == 1 and ids[0] == device[0])
                   for ids in supported) for device in devices):
        raise ValueError('Selected driver has no verified match for this GPU (GeForce GTX 16 / RTX scope)')


if __name__ == '__main__':
    try:
        check(sys.argv[1])
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f'[fail] {error}', file=sys.stderr)
        sys.exit(1)
