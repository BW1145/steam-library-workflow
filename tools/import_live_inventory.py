"""Build a review inventory from a live Steam snapshot, preserving saved classifications."""
import argparse
import json
from collections import Counter
from pathlib import Path
from read_inventory import write_csv

ROOT = Path(__file__).resolve().parents[1]


def build_inventory(live, baseline, include_previews=False, scope=None):
    if live['account_id'] != baseline['account_id']:
        raise ValueError('Live account differs from the baseline account')
    snap = live['snapshot']
    old = {g['appid']: g for g in baseline['entries']}
    installed = set(snap['installed'])
    entries = []
    for values in snap['rows']:
        game = dict(zip(snap['cols'], values))
        if game['kind'] != 1:
            continue
        appid = game['appid']
        raw_type = live['app_types'].get(str(appid), 0)
        owned = bool(game['owned'] or game['borrowed'])
        if not owned:
            decision = 'ownership_to_review'
        elif raw_type == 1 or include_previews and raw_type in (8, 65536):
            decision = 'game'
        else:
            decision = 'preview_or_test' if raw_type in (8, 65536) else 'type_to_review'
        if not include_previews and str(appid) in (scope or {}).get('preview_appids', {}):
            decision = 'preview_or_test'
        previous = old.get(appid, {})
        entries.append({**previous, 'appid': appid, 'name': game['name'],
                        'app_type': {1: 'game', 8: 'demo', 65536: 'beta'}.get(raw_type, 'unknown'),
                        'ownership': 'family' if game['borrowed'] else 'own' if game['owned'] else 'unknown',
                        'installed': appid in installed, 'decision': decision,
                        'store_url': f'https://store.steampowered.com/app/{appid}/',
                        'playtime_minutes': game['playtimeMinutes'],
                        'tag_ids': previous.get('tag_ids', snap.get('appTags', {}).get(str(appid), [])),
                        'tags': previous.get('tags', []), 'developer': previous.get('developer', ''),
                        'publisher': previous.get('publisher', ''), 'metadata_source': 'steam-client-live'})
    live_ids = {g['appid'] for g in entries}
    for game in old.values():
        if game['decision'] in ('game', 'not_in_current_library') and game['appid'] not in live_ids:
            entries.append({**game, 'decision': 'not_in_current_library'})
    games = [g for g in entries if g['decision'] == 'game']
    stats = {'game_count': len(games),
             'own_game_count': sum(g['ownership'] == 'own' for g in games),
             'family_game_count': sum(g['ownership'] == 'family' for g in games),
             'installed_game_count': sum(g['installed'] for g in games),
             'uninstalled_game_count': sum(not g['installed'] for g in games),
             'playtime_known_count': sum(g['playtime_minutes'] is not None for g in games),
             'playtime_missing_count': sum(g['playtime_minutes'] is None for g in games),
             'played_at_least_30_count': sum((g['playtime_minutes'] or 0) >= 30 for g in games),
             'review_counts': dict(Counter(g['decision'] for g in entries if g['decision'] != 'game'))}
    return {'generated_at_utc': live['generated_at_utc'], 'account_id': live['account_id'],
            'include_family': True, 'include_previews': include_previews,
            'source': 'steam-client-live', 'stats': stats, 'entries': entries}


def run(args):
    live = json.loads(Path(args.live).read_text(encoding='utf-8'))
    baseline_path = Path(args.baseline)
    baseline = json.loads(baseline_path.read_text(encoding='utf-8')) if baseline_path.exists() else {'account_id': live['account_id'], 'entries': []}
    scope_path = Path(args.scope)
    scope = json.loads(scope_path.read_text(encoding='utf-8')) if scope_path.exists() else {}
    inventory = build_inventory(live, baseline, args.include_previews, scope)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'inventory.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    games = [g for g in inventory['entries'] if g['decision'] == 'game']
    exceptions = [g for g in inventory['entries'] if g['decision'] != 'game']
    write_csv(output / 'inventory.csv', games)
    write_csv(output / 'inventory_exceptions.csv', exceptions)
    report = ['# 实时库存核对', '', f"完整游戏候选：{len(games)}；本人：{inventory['stats']['own_game_count']}；家庭：{inventory['stats']['family_game_count']}。", '',
              f"已安装：{inventory['stats']['installed_game_count']}；未安装：{inventory['stats']['uninstalled_game_count']}；游玩时间缺失：{inventory['stats']['playtime_missing_count']}。", '',
              '候选范围与分类保存分别处理。历史分类与用户修订继续保留；以下条目单列核对。', '',
              '| 游戏 | AppID | 核对原因 |', '| --- | ---: | --- |']
    labels = {'ownership_to_review': '客户端未标记为本人或家庭拥有', 'preview_or_test': '试玩版或测试客户端',
              'type_to_review': '应用类型待核对', 'not_in_current_library': '历史清单中存在，当前库中未列出'}
    report += [f"| {g['name']} | {g['appid']} | {labels[g['decision']]} |" for g in exceptions]
    (output / 'inventory_report.md').write_text('\n'.join(report) + '\n', encoding='utf-8')
    print(json.dumps(inventory['stats'], ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', default=str(ROOT / 'output/live_library.json'))
    parser.add_argument('--baseline', default=str(ROOT / 'output/inventory.json'))
    parser.add_argument('--output', default=str(ROOT / 'output'))
    parser.add_argument('--scope', default=str(ROOT / 'data/inventory_scope.json'))
    parser.add_argument('--include-previews', action='store_true')
    run(parser.parse_args())
