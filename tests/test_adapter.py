import asyncio
import importlib.util
import json
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
        self.adapter.check_plugin_update_on_load = AsyncMock(return_value=None)

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
            self.assertEqual(Plugin.check_plugin_update_now.__module__, 'decky_plugin_toolkit.plugin_updater')
            self.assertEqual(Plugin.check_plugin_update_on_load.__module__, 'decky_plugin_toolkit.plugin_updater')
            self.assertFalse(await Plugin().start_update('610;reboot'))
        finally:
            sys.path.pop(0)

    async def test_booted_transaction_does_not_offer_reboot_again(self):
        with patch.object(m, 'active', new_callable=AsyncMock, return_value=False), patch.object(self.adapter, 'get_transaction_status', new_callable=AsyncMock, return_value=dict(status='ready', pending_reboot=False)):
            await self.adapter._main()
            self.assertNotIn('done', self.adapter._state)


class PluginUpdates(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        sys.path.insert(0, str(ROOT / 'py_modules'))
        from nvidia_update import Plugin
        from nvidia_update import standalone
        from decky_plugin_toolkit import plugin_updater, project_config
        sys.path.pop(0)
        self.plugin = Plugin()
        self.plugin._state = dict(running=False, driver_spec=None)
        self.standalone = standalone
        self.updater = plugin_updater
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for module, key, filename in (
                (plugin_updater, 'PLUGIN_JSON_PATH', 'plugin.json'),
                (plugin_updater, 'PACKAGE_JSON_PATH', 'package.json'),
                (project_config, '_CONFIG_PATH', 'project.config.json')):
            mock = patch.object(module, key, self.root / filename)
            mock.start()
            self.addCleanup(mock.stop)
        self.identity('moi952', '0.4.0')

    def identity(self, owner, version):
        for name, value in (
                ('plugin.json', dict(name='Decky NVIDIA Update', version=version)),
                ('package.json', dict(name='decky-nvidia-update')),
                ('project.config.json', dict(githubOwner=owner, githubRepo='decky-nvidia-update',
                                             displayName='NVIDIA Driver Update'))):
            (self.root / name).write_text(json.dumps(value), encoding='utf-8')

    async def test_original_to_ab_and_next_update_use_configured_repository(self):
        for owner in ('moi952', '60plus'):
            for current, latest in (('0.3.0', '0.4.0'), ('0.4.0', '0.4.1'),
                                    ('0.4.0-standalone.8', '0.4.1')):
                with self.subTest(owner=owner, current=current):
                    self.identity(owner, current)
                    base = f'https://github.com/{owner}/decky-nvidia-update'
                    asset = f'{base}/releases/download/v{latest}/decky-nvidia-update-v{latest}.zip'
                    with patch.object(self.updater.http_json, 'resolve_redirect', new_callable=AsyncMock,
                                      return_value=f'{base}/releases/download/v{latest}/_') as redirect, \
                         patch.object(self.updater.http_json, 'url_exists', new_callable=AsyncMock,
                                      return_value=True) as exists:
                        info = await self.plugin.check_plugin_update_now()
                        self.assertTrue(info['has_update'])
                        self.assertTrue(info['checked_ok'])
                        self.assertEqual(info['current_version'], current)
                        self.assertEqual(info['asset_url'], asset)
                        self.assertEqual(info['plugin_display_name'], 'Decky NVIDIA Update')
                        self.assertEqual(await self.plugin.check_plugin_update_on_load(), info)
                        redirect.assert_awaited_with(f'{base}/releases/latest/download/_')
                        exists.assert_awaited_with(asset)

    async def test_equal_or_older_release_has_no_notification(self):
        for latest in ('0.4.0', '0.3.0'):
            with patch.object(self.updater, 'resolve_latest_release', new_callable=AsyncMock,
                              return_value=dict(version=latest, url='release', asset_url='asset', sha256='')):
                self.assertIsNone(await self.plugin.check_plugin_update_on_load())
                self.assertFalse((await self.plugin.check_plugin_update_now())['has_update'])

    async def test_unavailable_github_or_missing_zip_is_not_success(self):
        with patch.object(self.updater.http_json, 'resolve_redirect', new_callable=AsyncMock,
                          side_effect=OSError('offline')):
            self.assertFalse((await self.plugin.check_plugin_update_now())['checked_ok'])
        with patch.object(self.updater.http_json, 'resolve_redirect', new_callable=AsyncMock,
                          return_value='https://github.com/moi952/decky-nvidia-update/releases/download/v0.4.1/_'), \
             patch.object(self.updater.http_json, 'url_exists', new_callable=AsyncMock, return_value=False):
            self.assertFalse((await self.plugin.check_plugin_update_now())['checked_ok'])
            self.assertIsNone(await self.plugin.check_plugin_update_on_load())

    async def test_startup_notifies_without_losing_pending_reboot(self):
        info = dict(latest_version='0.4.1', has_update=True)
        with patch.object(self.standalone, 'active', new_callable=AsyncMock, return_value=False), \
             patch.object(self.plugin, 'get_transaction_status', new_callable=AsyncMock,
                          return_value=dict(pending_reboot=True, version='615.78.08')), \
             patch.object(self.plugin, 'check_plugin_update_on_load', new_callable=AsyncMock, return_value=info), \
             patch.object(self.standalone.decky, 'emit', new_callable=AsyncMock) as emit:
            await self.plugin._main()
            emit.assert_awaited_once_with('plugin_update_available', info)
            self.assertTrue(self.plugin._state['success'])
            self.assertEqual(self.plugin._state['driver_spec'], '615.78.08')

    async def test_failed_startup_check_does_not_stop_running_job_recovery(self):
        log = self.root / 'run.log'
        log.write_text('[action] rollback\n[operation] existing-operation\n')
        with patch.object(self.standalone, 'active', new_callable=AsyncMock, return_value=True), \
             patch.object(self.standalone, 'LOG', log), \
             patch.object(self.plugin, '_monitor_standalone', new_callable=AsyncMock) as monitor, \
             patch.object(self.plugin, 'check_plugin_update_on_load', new_callable=AsyncMock,
                          side_effect=OSError('offline')):
            await self.plugin._main()
            await self.plugin._task
            monitor.assert_awaited_once_with('rollback')
            self.assertTrue(self.plugin._state['running'])
            self.assertEqual(self.plugin._state['operation_id'], 'existing-operation')


if __name__ == '__main__':
    unittest.main()
