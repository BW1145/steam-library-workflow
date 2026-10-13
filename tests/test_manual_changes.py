import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from manual_changes import reconcile
from prepare_plan import run as plan
from workflow import read, save
import test_workflow


class ManualChangesTests(unittest.TestCase):
    def collection(self, name='解谜', ids=(10,), dynamic=False):
        return {'id': 'fixture', 'name': name, 'appids': list(ids), 'dynamic': dynamic, 'storage': {}}

    def snapshot(self, collections):
        return {'collections': collections, 'generated_at_utc': '2026-01-02'}

    def test_manual_add_remove_and_undo(self):
        before = self.snapshot([self.collection(ids=(10,))])
        after = self.snapshot([self.collection(ids=(11,))])
        overrides, changes = reconcile(before, after, {}, {'解谜': 'type'}, {10, 11})
        self.assertEqual(overrides['memberships']['解谜'], {'include': [11], 'exclude': [10]})
        self.assertEqual(len(changes), 1)
        undo, _ = reconcile(after, before, overrides, {'解谜': 'type'}, {10, 11})
        self.assertEqual(undo['memberships']['解谜'], {'include': [10], 'exclude': [11]})
        same, changes = reconcile(before, before, undo, {'解谜': 'type'}, {10, 11})
        self.assertEqual(changes, [])
        self.assertEqual(same, undo)

    def test_manual_rename_delete_and_dynamic_rules_are_preserved(self):
        before = self.snapshot([self.collection()])
        for after in [self.snapshot([]), self.snapshot([self.collection(name='我的解谜')])]:
            overrides, _ = reconcile(before, after, {}, {'解谜': 'type'}, {10})
            self.assertIn('解谜', overrides['preserved_names'])
            self.assertIn('fixture', overrides['preserved_ids'])
        first = self.collection(dynamic=True)
        second = self.collection(dynamic=True)
        first['storage'] = {'roamingData': {'filterSpec': {'tag': 1}}}
        second['storage'] = {'roamingData': {'filterSpec': {'tag': 2}}}
        overrides, _ = reconcile(self.snapshot([first]), self.snapshot([second]), {}, {'解谜': 'type'}, {10})
        self.assertEqual(overrides['structural_changes'][0]['change'], 'dynamic_rule_changed')

    def test_override_has_priority_in_plan_and_deleted_category_is_not_recreated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            test_workflow.PortableWorkflowTests().fixture(root)
            inventory = {'account_id': 42, 'stats': {}, 'entries': [
                {'appid': 10, 'name': '测试游戏', 'decision': 'game', 'installed': False, 'playtime_minutes': 0}]}
            save(root / 'output/inventory.json', inventory)
            save(root / 'output/live_library.json', {'account_id': 42, 'collections': [self.collection(ids=())]})
            state = read(root / 'state/classifications.json')
            state['games']['10'] = {'primary_type': '解谜', 'review_status': 'proposed'}
            save(root / 'state/classifications.json', state)
            save(root / 'state/manual_memberships.json', {'memberships': {'解谜': {'include': [], 'exclude': [10]}}})
            with contextlib.redirect_stdout(io.StringIO()):
                plan(root)
            result = read(root / 'output/collection_plan.json')
            action = next(a for a in result['actions'] if a.get('id') == 'fixture')
            self.assertEqual(action['action'], 'preserve')
            save(root / 'state/manual_memberships.json', {'preserved_names': ['解谜'], 'memberships': {}})
            save(root / 'output/live_library.json', {'account_id': 42, 'collections': []})
            with contextlib.redirect_stdout(io.StringIO()):
                plan(root)
            self.assertFalse(any(a['name'] == '解谜' for a in read(root / 'output/collection_plan.json')['actions']))

