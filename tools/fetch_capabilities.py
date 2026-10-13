"""Cache official Steam modes and content descriptors for current full games."""
import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from fetch_metadata import get_json

ROOT = Path(__file__).resolve().parents[1]


def fetch(appid):
    url = f'https://store.steampowered.com/api/appdetails?appids={appid}&l=english'
    record = {'source': url, 'checked_at_utc': datetime.now(timezone.utc).isoformat()}
    try:
        payload = get_json(url).get(str(appid), {})
        info = payload.get('data', {})
        if not payload.get('success') or info.get('steam_appid') != appid:
            return str(appid), dict(record, status='unavailable')
        record.update(status='ok')
        for key in ('name', 'categories', 'genres', 'content_descriptors',
                    'short_description', 'detailed_description'):
            record[key] = info.get(key)
    except (URLError, TimeoutError, ValueError) as error:
        record.update(status='error', error=str(error))
    return str(appid), record


def run(args):
    inventory = json.loads(Path(args.inventory).read_text(encoding='utf-8'))
    path = Path(args.cache)
    cache = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    ids = args.appids or [g['appid'] for g in inventory['entries'] if g['decision'] == 'game']
    ids = [appid for appid in dict.fromkeys(ids) if args.refresh or str(appid) not in cache]
    path.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for count, (key, record) in enumerate(pool.map(fetch, ids), 1):
            cache[key] = record
            if count % 25 == 0 or count == len(ids):
                path.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                print(f'Checked {count}/{len(ids)} public mode records', flush=True)
    print(json.dumps({'checked': len(ids), 'cached': len(cache)}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', default=str(ROOT / 'output/inventory.json'))
    parser.add_argument('--cache', default=str(ROOT / 'data/store_capabilities.json'))
    parser.add_argument('--appids', type=int, nargs='+')
    parser.add_argument('--refresh', action='store_true')
    run(parser.parse_args())