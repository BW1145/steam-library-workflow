"""Download public candidate completion-time mappings for this library."""
import argparse
import gzip
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = 'https://github.com/leinstay/steamdb'
DOWNLOAD = SOURCE + '/releases/latest/download/steamdb.min.json.gz'


def run(refresh=False):
    path = ROOT / 'data/completion_mapping.json'
    if path.exists() and not refresh:
        print('Using cached mapping; use --refresh to download a current snapshot.')
        return
    games = json.loads((ROOT / 'output/inventory.json').read_text(encoding='utf-8'))['entries']
    appids = {g['appid'] for g in games if g['decision'] == 'game'}
    request = urllib.request.Request(DOWNLOAD, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=90) as response:
        records = json.loads(gzip.decompress(response.read()))
    fields = ['name', 'steam_appid', 'steam_url', 'hltb_url', 'hltb_main_hours', 'updated_at']
    selected = {str(g['steam_appid']): {k: g.get(k) for k in fields} for g in records
                if g.get('steam_appid') in appids and g.get('hltb_url')}
    license_url = 'https://raw.githubusercontent.com/leinstay/steamdb/main/LICENSE'
    with urllib.request.urlopen(license_url, timeout=25) as response:
        license_text = response.read().decode('utf-8')
    license_path = ROOT / 'data/sources/steamdb-LICENSE.txt'
    license_path.parent.mkdir(parents=True, exist_ok=True)
    license_path.write_text(license_text, encoding='utf-8')
    data = {'source': SOURCE, 'download_source': DOWNLOAD, 'license': 'GPL-3.0',
            'checked_at_utc': datetime.now(timezone.utc).isoformat(),
            'use': '候选AppID映射；统计须再核对HLTB的profile_steam', 'games': selected}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'mapped_games': len(selected)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh', action='store_true')
    run(parser.parse_args().refresh)
