"""Decky RPC adapter for the bundled systemd-backed A/B transaction."""
import asyncio
import json
from pathlib import Path
import re
import uuid

import decky

UNIT = 'decky-nvidia-update.service'
CONTROLLER = Path(decky.DECKY_PLUGIN_DIR) / 'bin/standalone/driver.py'
LOG = Path(decky.DECKY_PLUGIN_LOG_DIR) / 'nvidia-update-run.log'


async def command(*args):
    # Avoid Decky's library path for all host utilities.
    import os
    env = os.environ.copy()
    env.pop('LD_LIBRARY_PATH', None)
    env.pop('LD_PRELOAD', None)
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                               stderr=asyncio.subprocess.PIPE, env=env)
    out, err = await proc.communicate()
    return proc.returncode, out.decode(errors='replace'), err.decode(errors='replace')


async def controller(action):
    rc, out, err = await command('/usr/bin/python3', '-I', str(CONTROLLER), action)
    if rc:
        raise RuntimeError(err.strip() or out.strip() or 'Controller failed')
    return json.loads(out)


async def active():
    _, out, _ = await command('systemctl', 'show', UNIT, '--property=ActiveState', '--value')
    return out.strip() in ('active', 'activating', 'deactivating', 'reloading')


class StandaloneMixin:
    async def get_rootfs_space_info(self):
        try:
            info = await controller('preflight')
            return dict(available=True, error='', total_mb=info['target_bytes'] // 1048576,
                        avail_mb=info['available_bytes'] // 1048576,
                        required_mb=info['required_bytes'] // 1048576,
                        undersized=not info['enough_space'])
        except Exception as error:
            return dict(available=False, error=str(error), total_mb=0, avail_mb=0,
                        required_mb=0, undersized=False)

    async def get_transaction_status(self):
        try:
            return await controller('status')
        except Exception as error:
            return dict(status='unknown', error=str(error))

    async def start_update(self, driver_spec, force=False, custom=False):
        if not isinstance(driver_spec, str) or (driver_spec != 'latest' and
                not re.fullmatch(r'[0-9]+(?:\.[0-9]+)*(?:-[0-9]+(?:\.[0-9]+)*)?', driver_spec)):
            return False
        if custom and not re.fullmatch(r'[0-9]+(?:\.[0-9]+)+', driver_spec):
            return False
        return await self._start_standalone('install-custom' if custom else 'install', driver_spec)

    async def rollback_driver(self):
        return await self._start_standalone('rollback')

    async def _start_standalone(self, action, version=None):
        async with self._lock:
            if self._state['running'] or await active():
                return False
            LOG.parent.mkdir(parents=True, exist_ok=True)
            operation = uuid.uuid4().hex
            LOG.write_text('[action] ' + action + '\n[operation] ' + operation + '\n')
            args = ['/usr/bin/python3', '-I', '-u', str(CONTROLLER), '--operation-id', operation, action]
            if version:
                args.append(version)
            # systemd owns the process and log descriptor across Decky reloads.
            rc, out, err = await command('systemd-run', '--unit=' + UNIT, '--collect',
                                        '--service-type=exec', '--property=StandardOutput=append:' + str(LOG),
                                        '--property=StandardError=append:' + str(LOG), '--', *args)
            if rc:
                self._state.update(running=False, done=True, success=False, error=err or out)
                await decky.emit('nvidia_update_progress', dict(self._state))
                return False
            self._state.update(running=True, done=False, success=None, error=None,
                               error_code=None, step=0, total_steps=8, log=[],
                               driver_spec=version, action=action, operation_id=operation, message='Preparing inactive system')
            self._task = asyncio.create_task(self._monitor_standalone(action))
            return True

    async def _monitor_standalone(self, action='install'):
        try:
            while True:
                running = await active()
                lines = LOG.read_text(errors='replace').splitlines() if LOG.exists() else []
                lines = [re.sub(r'\x1b\[[0-9;]*m', '', line) for line in lines]
                self._state['log'] = lines[-200:]
                for line in lines:
                    match = re.match(r'^\[(\d+)/(\d+)\]\s*(.*)$', line)
                    if match:
                        self._state.update(step=int(match[1]), total_steps=int(match[2]), message=match[3])
                self._state['running'] = running
                if not running:
                    transaction = await controller('status')
                    wanted = 'rollback-selected' if action == 'rollback' else 'ready'
                    failures = [line[7:] for line in lines if line.startswith('[fail] ')]
                    ok = (transaction.get('status') == wanted and not failures and
                          transaction.get('operation_id') == self._state.get('operation_id') and
                          bool(self._state.get('operation_id')))
                    self._state.update(done=True, success=ok,
                                       driver_spec=transaction.get('version', self._state.get('driver_spec')),
                                       error=None if ok else (failures[-1] if failures else 'Preparation did not complete; inspect the log.'))
                await decky.emit('nvidia_update_progress', dict(self._state))
                if not running:
                    break
                await asyncio.sleep(1)
        except Exception as error:
            self._state.update(running=False, done=True, success=False, error=str(error))
            await decky.emit('nvidia_update_progress', dict(self._state))

    async def _main(self):
        decky.logger.info('Standalone NVIDIA A/B updater loaded')
        if await active():
            self._state['running'] = True
            action = 'rollback' if LOG.exists() and LOG.read_text(errors='replace').startswith('[action] rollback') else 'install'
            self._state['action'] = action
            lines = LOG.read_text(errors='replace').splitlines() if LOG.exists() else []
            self._state['operation_id'] = next((line.removeprefix('[operation] ') for line in lines if line.startswith('[operation] ')), None)
            self._task = asyncio.create_task(self._monitor_standalone(action))
        else:
            transaction = await self.get_transaction_status()
            if transaction.get('pending_reboot'):
                self._state.update(done=True, success=True, driver_spec=transaction.get('version'))

        # Keep the toolkit's normal plugin updater, independently of the
        # NVIDIA transaction. A failed release check must not stop job recovery.
        try:
            update_info = await self.check_plugin_update_on_load()
            if update_info:
                await decky.emit('plugin_update_available', update_info)
        except Exception as error:
            decky.logger.error(f'Plugin update check failed: {error}')

    async def list_custom_builds(self):
        return await controller('custom-list')

    async def start_custom_build(self, *args, **kwargs):
        # UI uses start_update(custom=True): one durable build + install job.
        return False

    async def delete_custom_build(self, *args, **kwargs):
        # Either OS slot may pin these packages for future repair. Keep them.
        return False
