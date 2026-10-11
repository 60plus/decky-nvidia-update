import asyncio
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
decky = types.ModuleType('decky')
decky.DECKY_PLUGIN_DIR = str(ROOT)
decky.DECKY_PLUGIN_LOG_DIR = str(ROOT / '.verify-work')
decky.DECKY_PLUGIN_SETTINGS_DIR = str(ROOT / '.verify-work')
decky.logger = types.SimpleNamespace(info=lambda *a: None, error=lambda *a: None)
decky.emit = AsyncMock()
sys.modules['decky'] = decky
spec = importlib.util.spec_from_file_location('standalone', ROOT / 'py_modules/nvidia_update/standalone.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Adapter(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.adapter = m.StandaloneMixin()
        self.adapter._lock = asyncio.Lock()
        self.adapter._state = dict(running=False, driver_spec=None)

    async def test_rejects_invalid_rpc_version_without_launch(self):
        with patch.object(m, 'command', new_callable=AsyncMock) as command:
            self.assertFalse(await self.adapter.start_update('../x'))
            self.assertFalse(await self.adapter.start_update('610.1\n'))
            command.assert_not_called()

    async def test_service_launch_has_no_pipe_to_decky(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(m, 'LOG', Path(tmp) / 'run.log'), patch.object(m, 'active', new_callable=AsyncMock, return_value=False), patch.object(m, 'command', new_callable=AsyncMock, return_value=(0, '', '')) as command, patch.object(self.adapter, '_monitor_standalone', new_callable=AsyncMock):
            self.assertTrue(await self.adapter.start_update('610.57.04-1'))
            await self.adapter._task
            args = command.call_args.args
            self.assertEqual(args[0], 'systemd-run')
            self.assertNotIn('--pipe', args)
            self.assertTrue(any(a.startswith('--property=StandardOutput=append:') for a in args))
            self.assertEqual(args[-2:], ('install', '610.57.04-1'))

    async def test_failure_does_not_reuse_old_ready_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'run.log'
            log.write_text('[fail] Not enough space\n')
            with patch.object(m, 'LOG', log), patch.object(m, 'active', new_callable=AsyncMock, return_value=False), patch.object(m, 'controller', new_callable=AsyncMock, return_value={'status': 'ready', 'version': 'old'}):
                await self.adapter._monitor_standalone()
                self.assertFalse(self.adapter._state['success'])
                self.assertEqual(self.adapter._state['error'], 'Not enough space')

    async def test_custom_selection_stays_in_one_durable_service(self):
        with patch.object(self.adapter, '_start_standalone', new_callable=AsyncMock, return_value=True) as start:
            self.assertTrue(await self.adapter.start_update('615.71.09', custom=True))
            start.assert_awaited_once_with('install-custom', '615.71.09')
            self.assertFalse(await self.adapter.start_update('latest', custom=True))
            self.assertFalse(await self.adapter.start_update('615.71.09-1', custom=True))

    async def test_custom_packages_cannot_be_deleted_through_rpc(self):
        self.assertFalse(await self.adapter.delete_custom_build('615.71.09'))

    async def test_missing_operation_receipt_is_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'run.log'
            log.write_text('[action] install\n')
            self.adapter._state['operation_id'] = 'new-operation'
            with patch.object(m, 'LOG', log), patch.object(m, 'active', new_callable=AsyncMock, return_value=False), patch.object(m, 'controller', new_callable=AsyncMock, return_value={'status': 'ready', 'operation_id': 'previous-operation'}):
                await self.adapter._monitor_standalone()
                self.assertFalse(self.adapter._state['success'])

    async def test_preflight_failure_is_explicit(self):
        with patch.object(m, 'controller', new_callable=AsyncMock, side_effect=RuntimeError('Unsupported layout')):
            result = await self.adapter.get_rootfs_space_info()
            self.assertFalse(result['available'])
            self.assertEqual(result['error'], 'Unsupported layout')

    async def test_plugin_exports_standalone_rpc_methods(self):
        sys.path.insert(0, str(ROOT / 'py_modules'))
        try:
            from nvidia_update import Plugin
            self.assertTrue(Plugin.start_update.__module__.endswith('.standalone'))
            self.assertTrue(Plugin.get_rootfs_space_info.__module__.endswith('.standalone'))
            self.assertTrue(Plugin._main.__module__.endswith('.standalone'))
            self.assertFalse(await Plugin().start_update('610;reboot'))
        finally:
            sys.path.pop(0)

    async def test_booted_transaction_does_not_offer_reboot_again(self):
        with patch.object(m, 'active', new_callable=AsyncMock, return_value=False), patch.object(self.adapter, 'get_transaction_status', new_callable=AsyncMock, return_value=dict(status='ready', pending_reboot=False)):
            await self.adapter._main()
            self.assertNotIn('done', self.adapter._state)


if __name__ == '__main__':
    unittest.main()
