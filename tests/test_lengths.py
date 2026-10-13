import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from fetch_lengths import Fetcher, duration_bucket, parse_game
from unittest.mock import patch
from urllib.error import HTTPError


class LengthTests(unittest.TestCase):
    def html(self, steam_appid=400, seconds=36000, count=20):
        data = {'props': {'pageProps': {'game': {'data': {'game': [
            {'game_id': 7, 'game_name': 'One', 'profile_steam': steam_appid,
             'comp_main': seconds, 'comp_main_count': count},
            {'game_id': 8, 'game_name': 'Related', 'profile_steam': 888,
             'comp_main': 200000, 'comp_main_count': 30}], 'relationships': [{'game_id': 7, 'comp_main': 999999}]}}}}}
        return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + '</script>'

    def test_exact_main_story_boundaries(self):
        self.assertEqual(duration_bucket(36000), '时长-短篇')
        self.assertEqual(duration_bucket(36001), '时长-中篇')
        self.assertEqual(duration_bucket(108000), '时长-中篇')
        self.assertEqual(duration_bucket(108001), '时长-长篇')

    def test_missing_statistics_are_not_a_short_game(self):
        self.assertIsNone(duration_bucket(0))
        self.assertIsNone(duration_bucket(None))
        self.assertEqual(parse_game(self.html(count=0), 7, 400)['status'], 'no_main_story_statistics')

    def test_different_steam_version_requires_identity_review(self):
        self.assertEqual(parse_game(self.html(steam_appid=401), 7, 400)['status'], 'identity_to_review')

    def test_related_game_does_not_replace_requested_game(self):
        record = parse_game(self.html(), 7, 400)
        self.assertEqual(record['status'], 'matched')
        self.assertEqual(record['main_seconds'], 36000)


    def test_fallback_mapping_still_checks_the_steam_identity(self):
        mapping = {'400': {'name': 'One', 'hltb_url': 'https://howlongtobeat.com/game/7'}}
        with patch.object(Fetcher, 'get', return_value=('https://howlongtobeat.com/game/7', self.html(401))):
            result = Fetcher(0, mapping).fetch({'appid': 400, 'name': 'One'})
        self.assertEqual(result['status'], 'identity_to_review')
        self.assertEqual(result['mapping_source'], 'https://github.com/leinstay/steamdb')

    def test_missing_mapping_is_cached_without_a_retry_error(self):
        missing = HTTPError('https://videogamescritic.com/game/400', 404, 'Not Found', None, None)
        with patch.object(Fetcher, 'get', side_effect=missing):
            result = Fetcher(0).fetch({'appid': 400, 'name': 'One'})
        self.assertEqual(result['status'], 'no_completion_time_mapping')


if __name__ == '__main__':
    unittest.main()
