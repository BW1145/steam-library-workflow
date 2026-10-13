import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from prepare_plan import collection_action, duration_record


class PlanTests(unittest.TestCase):
    def test_membership_delta_preserves_absent_and_outside_scope_members(self):
        existing = [{'id': 'uc-one', 'name': '动作', 'dynamic': False, 'appids': [1, 2],
                     'storage': {'roamingData': {'added': [1, 2, 99], 'removed': []}}}]
        action = collection_action('动作', {2, 3}, existing, {1, 2, 3}, 'type')
        self.assertEqual(action['add_appids'], [3])
        self.assertEqual(action['remove_appids'], [1])
        self.assertEqual(action['preserved_outside_scope'], [99])
        self.assertEqual(action['desired_appids'], [2, 3, 99])
        self.assertEqual(action['id'], 'uc-one')

    def test_renaming_keeps_id_and_outside_scope_members(self):
        existing = [{'id': 'uc-original', 'name': '策略', 'dynamic': False,
                     'appids': [1, 99], 'storage': {}}]
        action = collection_action('战术指挥', {2}, existing, {1, 2}, 'type', previous_name='策略')
        self.assertEqual(action['action'], 'rename_and_update')
        self.assertEqual(action['previous_name'], '策略')
        self.assertEqual(action['id'], 'uc-original')
        self.assertEqual(action['desired_appids'], [2, 99])
        self.assertEqual(action['remove_appids'], [1])
        self.assertEqual(action['add_appids'], [2])

    def test_dynamic_migration_has_no_member_mutations(self):
        existing = [{'id': 'uc-dynamic', 'name': '没玩过', 'dynamic': True, 'appids': [1], 'storage': {}}]
        action = collection_action('没玩过', {1, 2}, existing, {1, 2}, 'play_status')
        self.assertEqual(action['action'], 'review_dynamic_migration')
        self.assertNotIn('remove_appids', action)
        self.assertNotIn('add_appids', action)

    def test_unapproved_category_remains_pending(self):
        self.assertEqual(collection_action('MOBA', {1}, [], {1}, 'type', False)['status'], 'category_pending')

    def test_duration_keeps_user_decision_and_requires_exact_identity(self):
        prior = {'bucket': '时长-长期常驻', 'review_status': 'confirmed'}
        stats = {'1': {'status': 'identity_to_review', 'main_seconds': 7200, 'source': 'test'}}
        self.assertEqual(duration_record({'appid': 1}, stats, {}, prior), prior)
        unresolved = duration_record({'appid': 1}, stats, {})
        self.assertIsNone(unresolved['bucket'])
        self.assertEqual(unresolved['query_status'], 'identity_to_review')

    def test_all_routes_estimate_waits_for_the_reading_policy(self):
        decisions = {'public_estimates': {'1': {'minimum_hours': 50, 'maximum_hours': 100,
                                               'scope': 'all_routes', 'reason': '全路线', 'source': 'test'}}}
        record = duration_record({'appid': 1}, {}, decisions)
        self.assertIsNone(record['bucket'])
        self.assertEqual(record['basis'], 'firsthand_review_estimate')
        self.assertEqual(record['minimum_hours'], 50)

    def test_official_range_crossing_boundary_is_not_forced_into_a_bucket(self):
        decisions = {'official_estimates': {'1': {'minimum_hours': 9, 'maximum_hours': 12,
                                                'reason': '开发者区间', 'source': 'test'}}}
        self.assertIsNone(duration_record({'appid': 1}, {}, decisions)['bucket'])


if __name__ == '__main__':
    unittest.main()
