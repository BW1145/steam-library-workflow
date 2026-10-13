import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from import_live_inventory import build_inventory


class LiveInventoryTests(unittest.TestCase):
    def fixture(self):
        return {'account_id': 42, 'generated_at_utc': '2026-10-07',
                'snapshot': {'cols': ['appid', 'name', 'kind', 'owned', 'borrowed', 'playtimeMinutes'],
                             'rows': [[10, 'Own', 1, 1, 0, 0], [11, 'Family', 1, 1, 1, 40],
                                      [12, 'Demo', 1, 1, 0, 0], [13, 'Installed', 1, 0, 0, None]],
                             'installed': [10, 13]},
                'app_types': {'10': 1, '11': 1, '12': 8, '13': 1}}, {
                    'account_id': 42, 'entries': [{'appid': 99, 'name': 'History', 'decision': 'game'}]}

    def test_borrowed_uninstalled_game_and_explicit_zero_are_retained(self):
        live, base = self.fixture()
        result = build_inventory(live, base)
        self.assertEqual(result['stats']['game_count'], 2)
        self.assertEqual(result['stats']['family_game_count'], 1)
        family = next(g for g in result['entries'] if g['appid'] == 11)
        self.assertEqual(family['ownership'], 'family')
        self.assertFalse(family['installed'])
        self.assertEqual(result['entries'][0]['playtime_minutes'], 0)

    def test_exceptions_and_history_are_preserved_separately(self):
        live, base = self.fixture()
        decisions = {g['appid']: g['decision'] for g in build_inventory(live, base)['entries']}
        self.assertEqual(decisions[12], 'preview_or_test')
        self.assertEqual(decisions[13], 'ownership_to_review')
        self.assertEqual(decisions[99], 'not_in_current_library')
        self.assertEqual(build_inventory(live, base, True)['stats']['game_count'], 3)

    def test_account_mismatch_is_rejected(self):
        live, base = self.fixture()
        base['account_id'] = 43
        with self.assertRaises(ValueError):
            build_inventory(live, base)

    def test_store_identity_can_identify_a_demo_marked_as_game(self):
        live, base = self.fixture()
        result = build_inventory(live, base, scope={'preview_appids': {'10': {'reason': 'Official demo'}}})
        self.assertEqual(result['stats']['game_count'], 1)
        self.assertEqual(result['entries'][0]['decision'], 'preview_or_test')
