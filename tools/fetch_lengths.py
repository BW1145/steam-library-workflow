"""Cache main-story completion statistics, matched by Steam AppID."""
import argparse
import json
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]


def parse_game(html, hltb_id, appid):
    script = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not script:
        raise ValueError('No completion statistics in the response')
    records = json.loads(script.group(1))['props']['pageProps']['game']['data']['game']
    game = next(item for item in records if int(item.get('game_id', 0)) == hltb_id)
    result = {'hltb_id': hltb_id, 'hltb_name': game['game_name'],
              'profile_steam': int(game.get('profile_steam') or 0),
              'main_seconds': int(game.get('comp_main') or 0),
              'main_reports': int(game.get('comp_main_count') or 0)}
    if result['profile_steam'] != appid:
        result['status'] = 'identity_to_review'
    elif result['main_seconds'] <= 0 or result['main_reports'] <= 0:
        result['status'] = 'no_main_story_statistics'
    else:
        result['status'] = 'matched'
    return result


def duration_bucket(main_seconds, mode='main_story'):
    if mode == 'ongoing':
        return '时长-长期常驻'
    if not main_seconds or main_seconds <= 0:
        return None
    if main_seconds <= 36000:
        return '时长-短篇'
    if main_seconds <= 108000:
        return '时长-中篇'
    return '时长-长篇'


class Fetcher:
    def __init__(self, interval, mapping=None):
        self.interval = interval
        self.mapping = mapping or {}
        self.lock = threading.Lock()
        self.next_request = 0
        self.rate_limited = threading.Event()

    def get(self, url):
        with self.lock:
            if self.rate_limited.is_set():
                raise RuntimeError('Rate limited; remaining requests postponed')
            wait = self.next_request - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self.next_request = time.monotonic() + self.interval
        request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                return response.url, response.read().decode('utf-8')
        except urllib.error.HTTPError as error:
            if error.code == 429:
                self.rate_limited.set()
            raise

    def fetch(self, game):
        appid = game['appid']
        result = {'appid': appid, 'name': game['name'],
                  'checked_at_utc': datetime.now(timezone.utc).isoformat()}
        try:
            mapped = self.mapping.get(str(appid), {})
            if mapped.get('hltb_url'):
                result['mapping_source'] = mapped.get('mapping_source', 'https://github.com/leinstay/steamdb')
                result['mapping_name'] = mapped['name']
                match = re.fullmatch(r'https://howlongtobeat.com/game/(\d+)/?', mapped['hltb_url'])
                if not match:
                    raise ValueError('Invalid completion-time mapping URL')
            else:
                url, html = self.get(f'https://videogamescritic.com/game/{appid}')
                result['mapping_source'] = url
                if not urlparse(url).path.endswith(f'-{appid}'):
                    raise ValueError('Steam AppID does not match the completion-time mapping page')
                match = re.search(r'https://howlongtobeat.com/game/(\d+)', html)
                if not match:
                    result['status'] = 'no_completion_time_mapping'
                    return result
            hltb_id = int(match.group(1))
            result['source'] = f'https://howlongtobeat.com/game/{hltb_id}'
            _, html = self.get(result['source'])
            result.update(parse_game(html, hltb_id, appid))
        except urllib.error.HTTPError as error:
            status = 'no_completion_time_mapping' if error.code == 404 and not result.get('mapping_source') else 'query_error'
            result.update(status=status, error=str(error))
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError, StopIteration, RuntimeError) as error:
            result.update(status='query_error', error=str(error))
        return result


def run(args):
    inventory = json.loads(Path(args.inventory).read_text(encoding='utf-8'))
    path = Path(args.cache)
    cache = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    mapping_path = Path(args.mapping)
    mapping = json.loads(mapping_path.read_text(encoding='utf-8')).get('games', {}) if mapping_path.exists() else {}
    overrides_path = Path(args.mapping_overrides)
    if overrides_path.exists():
        mapping.update(json.loads(overrides_path.read_text(encoding='utf-8')))
    games = [game for game in inventory['entries'] if game['decision'] == 'game'
             and (not args.appids or game['appid'] in args.appids)
             and (args.refresh or str(game['appid']) not in cache
                  or cache[str(game['appid'])].get('status') == 'query_error'
                  or (cache[str(game['appid'])].get('status') == 'no_completion_time_mapping'
                      and str(game['appid']) in mapping))]
    if args.limit:
        games = games[:args.limit]
    fetcher = Fetcher(args.interval, mapping)
    completed = 0

    def save():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.replace(path)

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(fetcher.fetch, game) for game in games]
        for future in as_completed(pending):
            result = future.result()
            if not fetcher.rate_limited.is_set() or result.get('source') or result['status'] != 'query_error':
                cache[str(result['appid'])] = result
            completed += 1
            if completed == 1 or completed % 10 == 0:
                save()
                print(json.dumps({'completed': completed, 'total': len(games),
                                  'matched': sum(item['status'] == 'matched' for item in cache.values()),
                                  'rate_limited': fetcher.rate_limited.is_set()}), flush=True)
    save()
    from collections import Counter
    print(json.dumps({'queried': completed, 'cache_status': dict(Counter(item['status'] for item in cache.values()))}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', default=str(ROOT / 'output/inventory.json'))
    parser.add_argument('--cache', default=str(ROOT / 'data/completion_times.json'))
    parser.add_argument('--mapping', default=str(ROOT / 'data/completion_mapping.json'))
    parser.add_argument('--mapping-overrides', default=str(ROOT / 'data/length_mapping_overrides.json'))
    parser.add_argument('--appids', type=int, nargs='+')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--interval', type=float, default=1.0)
    run(parser.parse_args())
