import argparse
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import workflow
from import_live_inventory import run as import_inventory
from prepare_plan import run as plan
from prepare_review import write_multiplayer_review
from workflow_config import load_config


class PortableWorkflowTests(unittest.TestCase):
    def fixture(self, root):
        shutil.copytree(ROOT / 'profiles', root / 'profiles')
        shutil.copy2(ROOT / 'classification_categories.md', root / 'classification_categories.md')
        (root / 'steam-mcp').mkdir()
        args = argparse.Namespace(account=42, steam_root=str(root / 'fake-steam'),
                                  node=sys.executable, npm_cli=sys.executable, port=8080)
        with patch('workflow.subprocess.check_output', return_value='v22.22.1\n'):
            with contextlib.redirect_stdout(io.StringIO()):
                workflow.initialize(root, args)
        return args

    def test_first_init_and_repeat_preserve_existing_decisions_and_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self.fixture(root)
            state_path = root / 'state/classifications.json'
            state = workflow.read(state_path)
            state['games']['10'] = {'primary_type': '解谜', 'review_status': 'confirmed'}
            workflow.save(state_path, state)
            before = state_path.read_bytes()
            env = root / 'steam-mcp/.env'
            env.write_text(env.read_text(encoding='utf-8') + 'STEAM_API_KEY=fixture-secret\n', encoding='utf-8')
            with patch('workflow.subprocess.check_output', return_value='v22.22.1\n'):
                with contextlib.redirect_stdout(io.StringIO()):
                    workflow.initialize(root, args)
            self.assertEqual(before, state_path.read_bytes())
            self.assertIn('STEAM_API_KEY=fixture-secret', env.read_text(encoding='utf-8'))
            self.assertIn('STEAM_MCP_ALLOW_WRITES="0"', env.read_text(encoding='utf-8'))
            self.assertEqual(load_config(root)['account_id'], 42)
            self.assertEqual(workflow.read(root / 'data/classification_discussion.json')['collection_policy']['retired_collections'], {})

    def test_account_change_rejected_before_state_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            before = (root / '.steam-workflow.local.json').read_bytes()
            with self.assertRaises(ValueError):
                workflow.bind_account(root, 43)
            self.assertEqual(before, (root / '.steam-workflow.local.json').read_bytes())

    def test_first_import_plan_and_multiplayer_without_legacy_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            live = {'account_id': 42, 'generated_at_utc': '2026-01-01',
                    'snapshot': {'cols': ['appid', 'name', 'kind', 'owned', 'borrowed', 'playtimeMinutes'],
                                 'rows': [[10, '空间解谜', 1, 1, 0, 0], [11, '家庭游戏', 1, 1, 1, 40]],
                                 'installed': [10]},
                    'app_types': {'10': 1, '11': 1},
                    'collections': [{'id': 'fixture-old', 'name': '策略', 'dynamic': False,
                                     'appids': [99], 'storage': {}}]}
            workflow.save(root / 'output/live_library.json', live)
            args = argparse.Namespace(live=root / 'output/live_library.json', baseline=root / 'output/inventory.json',
                scope=root / 'data/inventory_scope.json', output=root / 'output', include_previews=False)
            with contextlib.redirect_stdout(io.StringIO()):
                import_inventory(args)
                plan(root)
            inventory = workflow.read(root / 'output/inventory.json')
            self.assertEqual(inventory['stats']['family_game_count'], 1)
            self.assertEqual(inventory['stats']['uninstalled_game_count'], 1)
            with self.assertRaisesRegex(ValueError, 'classify'):
                workflow.ready(root)
            result = workflow.read(root / 'output/collection_plan.json')
            old = next(a for a in result['actions'] if a.get('id') == 'fixture-old')
            self.assertEqual(old['action'], 'preserve')
            state = workflow.read(root / 'state/classifications.json')
            state['games'] = {str(n): {'primary_type': '解谜', 'review_status': 'proposed'} for n in [10, 11]}
            workflow.save(root / 'state/classifications.json', state)
            network = workflow.read(root / 'state/multiplayer.json')
            network['games']['10'] = {'group': '可选合作', 'solo_supported': True, 'minimum_players': 1,
                                      'reason': '完整独玩与在线合作', 'eligibility': 'included'}
            workflow.save(root / 'state/multiplayer.json', network)
            workflow.save(root / 'data/duration_decisions.json', {'ongoing': {
                str(n): {'bucket': '时长-长期常驻', 'reason': 'fixture', 'sources': ['fixture']} for n in [10, 11]}})
            with contextlib.redirect_stdout(io.StringIO()):
                plan(root)
            write_multiplayer_review(root / 'output', inventory, state, [], network)
            self.assertEqual(workflow.ready(root)['game_count'], 2)
            from review_existing import run as review_existing
            with contextlib.redirect_stdout(io.StringIO()):
                review_existing(argparse.Namespace(inventory=root / 'output/inventory.json',
                    output=root / 'output', steam_root=str(root / 'fake-steam'), account=42))
            self.assertEqual(workflow.read(root / 'output/existing_collections.json'), [])
            live['collections'].append({'id': 'fixture-empty', 'name': '射击', 'dynamic': False,
                                        'appids': [10, 99], 'storage': {}})
            workflow.save(root / 'output/live_library.json', live)
            with contextlib.redirect_stdout(io.StringIO()):
                plan(root)
            action = next(a for a in workflow.read(root / 'output/collection_plan.json')['actions']
                          if a.get('id') == 'fixture-empty')
            self.assertEqual(action['remove_appids'], [10])
            self.assertEqual(action['desired_appids'], [99])

    def test_package_excludes_personal_data_and_rejects_leaked_account(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for file in workflow.PUBLIC_DOCS:
                (root / file).write_text('public', encoding='utf-8')
            for name in ['tools', 'tests', 'profiles', 'state', 'output']:
                (root / name).mkdir()
            workflow.save(root / workflow.CONFIG_NAME, {'account_id': 42})
            workflow.save(root / 'state/classifications.json', {'secret': 'private-owned-games'})
            (root / 'tools/public.py').write_text('print("public")', encoding='utf-8')
            mcp = root / 'steam-mcp'
            mcp.mkdir()
            for file in workflow.MCP_FILES:
                (mcp / file).write_text('public', encoding='utf-8')
            for name in workflow.MCP_DIRS:
                (mcp / name).mkdir()
            (mcp / '.env').write_text('STEAM_API_KEY=fixture-secret', encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()):
                output = workflow.make_package(root, root / 'share.zip')
            with zipfile.ZipFile(output) as archive:
                self.assertNotIn('state/classifications.json', archive.namelist())
                self.assertNotIn('steam-mcp/.env', archive.namelist())
                self.assertNotIn(workflow.CONFIG_NAME, archive.namelist())
                self.assertIn('tools/public.py', archive.namelist())
                self.assertNotIn(b'fixture-secret', b''.join(archive.read(n) for n in archive.namelist()))
            (root / 'tools/public.py').write_text('ACCOUNT = 42', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Personal account'):
                workflow.make_package(root, output)

    def test_disk_verification_detects_incomplete_save(self):
        item = {'id': 'fixture', 'name': '解谜', 'dynamic': False, 'appids': [10], 'storage': {}}
        receipt = {'account_id': 42, 'status': 'complete', 'before': [], 'after': [item],
                   'collections': [{'id': 'fixture', 'name': '解谜', 'expected_appids': [10]}]}
        live = {'account_id': 42, 'collections': [item]}
        with self.assertRaises(AssertionError):
            workflow.verify_receipt(ROOT, receipt, live, [{'id': 'fixture', 'explicit_appids': []}])
        workflow.verify_receipt(ROOT, receipt, live, [{'id': 'fixture', 'explicit_appids': [10]}])


if __name__ == '__main__':
    unittest.main()

