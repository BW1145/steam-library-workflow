"""Cache public app types and gameplay descriptions for the inventory."""
import argparse
import json
import urllib.error
import urllib.request
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE_INTERVAL = 1.8
STORE_LOCK = threading.Lock()
STORE_NEXT = 0.0


def get_json(url):
    global STORE_NEXT
    if url.startswith('https://store.steampowered.com/'):
        with STORE_LOCK:
            time.sleep(max(0, STORE_NEXT - time.monotonic()))
            STORE_NEXT = time.monotonic() + STORE_INTERVAL
    request = urllib.request.Request(url, headers={'User-Agent': 'SteamLibraryInspector/0.1'})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def fetch(appid):
    errors = []
    store_url = f'https://store.steampowered.com/api/appdetails?appids={appid}&l=schinese&filters=basic,developers,publishers,genres'
    try:
        item = get_json(store_url).get(str(appid), {})
        info = item.get('data', {})
        if item.get('success') and info.get('type') and info.get('steam_appid') == appid:
            return str(appid), {'common': {'name': info.get('name', ''), 'type': info['type']},
                                'source': store_url, 'fetched_at_utc': datetime.now(timezone.utc).isoformat(),
                                'short_description': info.get('short_description', ''),
                                'detailed_description': info.get('detailed_description', ''),
                                'developers': info.get('developers', []),
                                'publishers': info.get('publishers', []),
                                'genres': info.get('genres', [])}
        errors.append('Steam store: no matching public metadata')
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        errors.append(f'Steam store: {error}')
    # This is an independent public service, not a Valve-operated endpoint.
    cmd_url = f'https://api.steamcmd.net/v1/info/{appid}'
    try:
        payload = get_json(cmd_url)
        info = payload.get('data', {})
        info = info.get(str(appid), {}) if isinstance(info, dict) else {}
        common = info.get('common', {})
        if common.get('type'):
            return str(appid), {'common': {key: common[key] for key in ('name', 'type', 'exfgls') if key in common},
                                'source': cmd_url, 'fetched_at_utc': datetime.now(timezone.utc).isoformat(), 'errors': errors}
        errors.append('SteamCMD API: no public metadata')
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        errors.append(f'SteamCMD API: {error}')
    return str(appid), {'common': {}, 'errors': errors,
                        'fetched_at_utc': datetime.now(timezone.utc).isoformat()}


def run(args):
    global STORE_INTERVAL
    STORE_INTERVAL = args.store_interval
    snapshot = json.loads(Path(args.inventory).read_text(encoding='utf-8'))
    path = Path(args.cache)
    cache = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    ids = [row['appid'] for row in snapshot['entries']
           if (row['decision'] == 'pending_type' or (args.all_games and row['decision'] == 'game'))
           and (args.refresh or str(row['appid']) not in cache
                or (args.retry_descriptions and row['decision'] == 'game'
                    and not (cache.get(str(row['appid']), {}).get('short_description')
                             or cache.get(str(row['appid']), {}).get('detailed_description'))))]
    if args.appids:
        ids = [appid for appid in args.appids
               if args.refresh or str(appid) not in cache
               or not (cache.get(str(appid), {}).get('short_description') or cache.get(str(appid), {}).get('detailed_description'))]
    path.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for count, (appid, item) in enumerate(pool.map(fetch, ids), 1):
            cache[appid] = item
            path.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            if count % 10 == 0 or count == len(ids):
                print(f'Checked {count}/{len(ids)} public AppIDs', flush=True)
    resolved = sum(bool(item.get('common', {}).get('type')) for item in cache.values())
    print(json.dumps({'checked': len(ids), 'cached': len(cache), 'resolved_types': resolved}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', default=str(ROOT / 'output/inventory.json'))
    parser.add_argument('--cache', default=str(ROOT / 'data/public_metadata.json'))
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--retry-descriptions', action='store_true', help='Query cached games that still lack store descriptions')
    parser.add_argument('--appids', nargs='+', type=int, help='Only query these public AppIDs')
    parser.add_argument('--store-interval', type=float, default=1.8, help='Minimum seconds between Steam store requests')
    parser.add_argument('--all-games', action='store_true', help='Cache public gameplay descriptions for all inventory games')
    run(parser.parse_args())