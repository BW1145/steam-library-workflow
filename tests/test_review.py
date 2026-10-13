import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from prepare_review import build_rows, merge_decisions
from classification_policy import type_collections


class ReviewTests(unittest.TestCase):
    def test_repeat_import_preserves_confirmed_classification(self):
        state = {'rule_version': '0.3', 'games': {'1': {'primary_type': 'galgame', 'review_status': 'confirmed'}}}
        self.assertEqual(merge_decisions(state, [{'appid': 1, 'primary_type': '射击'}], {1}), (0, 1))
        self.assertEqual(state['games']['1']['primary_type'], 'galgame')

    def test_user_correction_survives_future_ai_proposals(self):
        state = {'rule_version': '0.3', 'games': {}}
        merge_decisions(state, [{'appid': 1, 'primary_type': '沙盒'}], {1}, True)
        merge_decisions(state, [{'appid': 1, 'primary_type': '开放世界'}], {1})
        self.assertEqual(state['games']['1']['primary_type'], '沙盒')
        self.assertTrue(state['games']['1']['user_override'])

    def test_only_new_games_enter_classification_queue(self):
        inventory = {'entries': [{'appid': 1, 'name': 'One', 'decision': 'game', 'playtime_minutes': None},
                                 {'appid': 2, 'name': 'Two', 'decision': 'game', 'playtime_minutes': 30}]}
        state = {'games': {'1': {'primary_type': '模拟', 'review_status': 'confirmed'}}}
        rows, queue = build_rows(inventory, state, [], {})
        self.assertEqual([item['appid'] for item in queue], [2])
        self.assertEqual(rows[0]['play_status'], '待核实')
        self.assertEqual(rows[1]['play_status'], '玩过')

    def test_reusing_old_proposals_keeps_approved_categories_out_of_candidates(self):
        inventory = {'entries': [{'appid': 1, 'name': 'One', 'decision': 'game'}]}
        state = {'approved_types': ['卡牌桌游'], 'games': {
            '1': {'primary_type': '卡牌桌游', 'candidate_new_category': '卡牌桌游'}}}
        rows, _ = build_rows(inventory, state, [], {})
        self.assertEqual(rows[0]['candidate_new_category'], '')
        self.assertEqual(rows[0]['primary_type'], '卡牌桌游')

    def test_shooter_routing_keeps_pvp_out_of_solo_shooting_collection(self):
        inventory = {'entries': [{'appid': 1, 'name': 'Competitive', 'decision': 'game'},
                                 {'appid': 2, 'name': 'PvE', 'decision': 'game'}]}
        state = {'games': {key: {'primary_type': '射击'} for key in ('1', '2')}}
        network = {'games': {'1': {'group': '多人对战', 'collection_policy': 'multiplayer_only'},
                             '2': {'group': '可选合作'}}}
        rows, _ = build_rows(inventory, state, [], {}, network)
        self.assertEqual(rows[0]['primary_type'], '射击')
        self.assertEqual(rows[0]['type_collections'], [])
        self.assertEqual(rows[1]['type_collections'], ['射击'])

    def test_adult_content_has_one_gameplay_collection(self):
        self.assertEqual(type_collections({'primary_type': '黄油', 'secondary_types': ['JRPG']}), ['黄油'])
        self.assertEqual(type_collections({'primary_type': '其他'}), [])

    def test_repeat_review_recognizes_newly_adopted_collection(self):
        inventory = {'entries': [{'appid': 1, 'name': 'Tactics', 'decision': 'game'}]}
        state = {'approved_types': ['战术指挥'], 'games': {'1': {'primary_type': '战术指挥'}}}
        rows, _ = build_rows(inventory, state, [{'name': '战术指挥', 'explicit_appids': [1]}], {})
        self.assertEqual(rows[0]['existing_types'], ['战术指挥'])

    def test_foreign_appid_does_not_enter_saved_state(self):
        state = {'rule_version': '0.3', 'games': {}}
        with self.assertRaises(ValueError):
            merge_decisions(state, [{'appid': 999, 'primary_type': '模拟'}], {1})
        self.assertFalse(state['games'])


if __name__ == '__main__':
    unittest.main()