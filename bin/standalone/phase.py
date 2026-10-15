#!/usr/bin/python3 -I
"""Internal staging operations; imported only by the bundled shell workers."""
import importlib.util
import json
from pathlib import Path
import shlex
import sys

spec = importlib.util.spec_from_file_location('driver', Path(__file__).with_name('driver.py'))
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


def emit(values):
    for key, value in values.items():
        print(key + '=' + shlex.quote(str(value)))


def main():
    action, *args = sys.argv[1:]
    if action == 'package-pins':
        data = json.loads(Path(args[0]).read_text())
        packages = d.package_pins(data)
        emit(dict(DRIVER_VERSION=data['version'], NV_PKGVER=data['version'].rsplit('-', 1)[0], PKG_URLS=data['urls']))
        for name, index in (('PKG_NAMES', 0), ('PKG_FILES', 2), ('PKG_URL_ARR', 3)):
            print(name + '=(' + ' '.join(shlex.quote(p[index]) for p in packages) + ')')
    elif action == 'trim':
        native = Path(args[0]) / 'usr/lib/steamos-nvidia/driver.conf'
        if native.exists():
            values = {}
            for line in native.read_text().splitlines():
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    values[key] = shlex.split(value)
            if values.get('TRIM_CUDA') == ['1']:
                path = Path(args[1])
                excluded = ('libcuda', 'libcudadebugger', 'libnvidia-nvvm', 'libnvidia-opencl',
                            'libnvoptix', 'nvidia-cuda-mps', 'OpenCL')
                path.write_text('\n'.join(line for line in path.read_text().splitlines()
                                          if not any(word in line for word in excluded)) + '\n')
    elif action == 'request':
        work = Path(args[0]).resolve()
        if work.parent != d.STATE or not work.name.startswith('prepare-'):
            raise ValueError('Unexpected staging directory')
        request = json.loads((work / 'request.json').read_text())
        current = d.preflight()
        if any(current[k] != request[k] for k in ('source', 'target', 'devices', 'target_bytes')):
            raise ValueError('A/B layout changed since preflight')
        emit(dict(SOURCE=request['source'], TARGET=request['target'],
                  SOURCE_DEV=request['devices'][request['source'] + '/rootfs'],
                  TARGET_DEV=request['devices'][request['target'] + '/rootfs'],
                  TARGET_BYTES=request['target_bytes'], DRIVER=request['requested'],
                  PINS=str(work / 'custom-pins.json') if (work / 'custom-pins.json').exists() else ''))
        command = '/bin/bash ' + shlex.quote(str(d.HERE / 'repair.sh')) + ' "$UPDATED_SLOT" --finish ' + shlex.quote(str(work))
        text = d.hook_text(Path('/usr/lib/rauc/post-install.sh').read_text(), command, temporary=True)
        (work / 'post-install.sh').write_text(text)
    elif action == 'image-released':
        d.require_image_released(*args)
    elif action == 'recheck':
        request = json.loads((Path(args[0]) / 'request.json').read_text())
        source, target, devices = d.layout()
        if (source, target, devices) != (request['source'], request['target'], request['devices']):
            raise ValueError('A/B layout changed before writing')
        if d.run('steamos-bootconf', 'selected-image') != source:
            raise ValueError('Another system was selected during preparation')
    elif action == 'target':
        slot = args[0]
        source, target, devices = d.layout()
        if slot != target:
            raise ValueError('Refusing to repair the active or an unknown slot')
        emit(dict(ROOT_DEV=devices[target + '/rootfs'], EFI_DEV=devices[target + '/efi']))
    elif action == 'pins':
        pins = json.loads((d.RUNTIME / 'installed.json').read_text())
        if not d.VERSION.fullmatch(pins['version']) or '-' not in pins['version']:
            raise ValueError('Invalid saved driver pin')
        emit(dict(DRIVER=pins['version']))
    elif action == 'installed':
        data = json.loads((Path(args[0]) / 'usr/lib/decky-nvidia-update/installed.json').read_text())
        emit(dict(VERSION=data['version'], KERNEL=data['kernel']))
    elif action == 'ready':
        work, root, device = args
        data = json.loads((Path(root) / 'usr/lib/decky-nvidia-update/installed.json').read_text())
        uuid = d.run('blkid', '-s', 'UUID', '-o', 'value', device)
        d.write_json(Path(work) / 'ready.json', dict(version=data['version'],
                     target_uuid=uuid, target_identity=d.root_identity(root, uuid)))
    elif action == 'rollback-env':
        data = json.loads(Path(args[0]).read_text())
        current, other, devices = d.layout()
        if data['devices'] != devices:
            raise ValueError('Saved devices changed')
        selected = d.run('steamos-bootconf', 'selected-image')
        if selected not in (current, data['target']):
            raise ValueError('An unrelated boot selection is pending')
        emit(dict(SOURCE=data['source'], TARGET=data['target'], CURRENT=current,
                  OTHER_DEV=devices[other + '/rootfs']))
    elif action == 'verify-rollback':
        data = json.loads(Path(args[0]).read_text())
        current = d.run('steamos-bootconf', 'this-image')
        for role in ('source', 'target'):
            slot = data[role]
            uuid = d.run('blkid', '-s', 'UUID', '-o', 'value', data['devices'][slot + '/rootfs'])
            root = '/' if current == slot else args[1]
            if uuid != data[role + '_uuid'] or d.root_identity(root, uuid) != data[role + '_identity']:
                raise ValueError('A saved system has changed; refusing stale rollback')
    else:
        raise ValueError('Unknown staging phase')


if __name__ == '__main__':
    main()
