# Export existing collections and local playtime candidates without changing Steam.
import argparse
import csv
import json
from pathlib import Path
from workflow_config import resolve_steam_args

ROOT = Path(__file__).resolve().parents[1]


def read_collections(path):
    collections = []
    for key, value in json.loads(path.read_text(encoding='utf-8')):
        if not str(key).startswith('user-collections.') or not isinstance(value, dict) or value.get('deleted'):
            continue
        raw = value.get('value')
        if not raw:
            continue
        info = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(info, dict):
            raise ValueError(f'Invalid collection value for {key}')
        added = {int(appid) for appid in info.get('added', [])}
        removed = {int(appid) for appid in info.get('removed', [])}
        collections.append({'id': info.get('id'), 'name': info.get('name'),
                            'explicit_appids': sorted(added - removed),
                            'dynamic_filter': info.get('filterSpec'),
                            'system': key in ('user-collections.favorite', 'user-collections.hidden')})
    return collections


def run(args):
    resolve_steam_args(args, ROOT)
    snapshot = json.loads(Path(args.inventory).read_text(encoding='utf-8'))
    if snapshot['account_id'] != args.account:
        raise ValueError('Inventory and collection account IDs differ')
    source = Path(args.steam_root) / f'userdata/{args.account}/config/cloudstorage/cloud-storage-namespace-1.json'
    collections = read_collections(source) if source.exists() else []
    games = [row for row in snapshot['entries'] if row['decision'] == 'game']
    completed = set().union(*(set(item['explicit_appids']) for item in collections if item['name'] == '已通关'))
    review = []
    for row in games:
        time = row['playtime_minutes']
        status = '待核实' if time is None else '玩过' if time >= 30 else '没玩过'
        review.append({'appid': row['appid'], 'name': row['name'], 'playtime_minutes': time,
                       'status_candidate': status, 'existing_completed': row['appid'] in completed,
                       'evidence': ('steam-client-live' if snapshot.get('source') == 'steam-client-live' else 'localconfig.Playtime') if time is not None else 'playtime missing'})
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'existing_collections.json').write_text(json.dumps(collections, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    with (output / 'playtime_review.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=['appid', 'name', 'playtime_minutes', 'status_candidate', 'existing_completed', 'evidence'])
        writer.writeheader()
        writer.writerows(review)
    custom = sum(not item['system'] for item in collections)
    system = sum(item['system'] for item in collections)
    counts = {name: sum(row['status_candidate'] == name for row in review) for name in ('玩过', '没玩过', '待核实')}
    report = ['# 现有收藏集与游玩记录核对', '',
              f'读取到 {custom} 个自定义收藏集和 {system} 个系统集合。', '',
              '动态集合的显式成员数与客户端动态筛选后的总数不同；动态规则保存在 existing_collections.json。', '',
              '| 名称 | 显式 AppID 数 | 动态筛选 |', '| --- | ---: | --- |']
    report += [f'| {item["name"]} | {len(item["explicit_appids"])} | {"是" if item["dynamic_filter"] is not None else "否"} |' for item in collections]
    report += ['', '## 游玩记录', '',
               f'按 30 分钟界限，玩过候选 {counts["玩过"]} 个，没玩过候选 {counts["没玩过"]} 个，游玩时间待核实 {counts["待核实"]} 个。', '',
               '时间空值表示尚无可靠记录。实时客户端导出与离线记录分别注明依据；有缺口时先补查，再讨论少量实际歧义。', '',
               '“没玩过”按项目的本人累计30分钟规则准备成员；动态或静态状态以上表的实际存储为准。调整前先生成具体变更预览。', '',
               'playtime_review.csv 保存候选状态与已有“已通关”线索，供分类阶段核对。']
    (output / 'existing_collections_report.md').write_text('\n'.join(report) + '\n', encoding='utf-8')
    print(json.dumps({'custom_collections': custom, 'system_collections': system, 'playtime_candidates': counts}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Export existing Steam collections and playtime review')
    parser.add_argument('--steam-root')
    parser.add_argument('--account', type=int)
    parser.add_argument('--inventory', default=str(ROOT / 'output/inventory.json'))
    parser.add_argument('--output', default=str(ROOT / 'output'))
    run(parser.parse_args())
