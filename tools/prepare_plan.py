"""Prepare a reviewable collection plan without connecting to or writing Steam."""
import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from fetch_lengths import duration_bucket
from classification_policy import type_collections

ROOT = Path(__file__).resolve().parents[1]


def read(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def duration_record(game, cache, decisions, previous=None):
    if previous and (previous.get('user_override') or previous.get('review_status') == 'confirmed'):
        return previous
    key = str(game['appid'])
    record = {'bucket': None, 'review_status': 'proposed', 'basis': 'unresolved', 'sources': []}
    if key in decisions.get('ongoing', {}):
        record.update(decisions['ongoing'][key], basis='ongoing_use')
        return record
    official = decisions.get('official_estimates', {}).get(key)
    public = decisions.get('public_estimates', {}).get(key)
    estimate = official or public
    if estimate:
        low, high = estimate['minimum_hours'], estimate['maximum_hours']
        bucket = duration_bucket(high * 3600) if duration_bucket(low * 3600) == duration_bucket(high * 3600) else None
        if estimate.get('scope', 'main_story') != 'main_story':
            bucket = None
        record.update(bucket=bucket, minimum_hours=low, maximum_hours=high,
                      basis='official_estimate' if official else 'firsthand_review_estimate',
                      estimate_scope=estimate.get('scope', 'main_story'),
                      reason=estimate['reason'], sources=[estimate['source']],
                      confidence=estimate.get('confidence', 'medium'),
                      review_notes=estimate.get('review_notes', []))
        return record
    estimate = decisions.get('edition_references', {}).get(key)
    stats = cache.get(key, {})
    if stats.get('status') != 'matched' and estimate:
        stats = cache.get(str(estimate.get('reference_appid', key)), {})
        if stats.get('main_seconds', 0) > 0 and stats.get('main_reports', 0) > 0:
            record.update(basis='related_edition_estimate', reason=estimate['reason'])
    if stats.get('status') == 'matched' or record['basis'] == 'related_edition_estimate':
        seconds = stats['main_seconds']
        record.update(bucket=duration_bucket(seconds), main_hours=round(seconds / 3600, 2),
                      main_reports=stats.get('main_reports'), sources=list(dict.fromkeys([stats['source'], *(estimate or {}).get('identity_sources', [])])))
        if record['basis'] == 'unresolved':
            record.update(basis='matched_main_statistics', reason='HLTB记录的Steam AppID一致；按主线统计估计。')
        flags = []
        if (stats.get('main_reports') or 0) < 5:
            flags.append('统计样本较少')
        if min(abs(seconds / 3600 - boundary) for boundary in (10, 30)) <= 1:
            flags.append('靠近10或30小时边界')
        record['review_notes'] = flags
    else:
        record.update(query_status=stats.get('status', 'not_queried'),
                      reason={'no_completion_time_mapping': '两条公开映射中未取得可核对的主线统计；保留未定时长。',
                              'identity_to_review': '统计对应的Steam版本不同，需要核对版本关系。',
                              'no_main_story_statistics': '对应页面没有有效主线样本。',
                              'query_error': '查询尚未成功，保留原始错误供补查。'}.get(stats.get('status'), '尚未取得可核对统计。'))
    if record['bucket'] is None and stats.get('research_note'):
        record.update(reason=stats['research_note'], sources=stats.get('research_sources', []),
                      checked_date=stats.get('research_checked_date'), basis='researched_without_main_sample')
    return record


def stored_members(collection):
    result = set(collection.get('appids', []))
    for key in ('roamingData', 'localData'):
        data = collection.get('storage', {}).get(key, {})
        result.update(data.get('added', []))
        result.difference_update(data.get('removed', []))
    return result


def collection_action(name, desired, existing, scope, dimension, approved=True, previous_name=None):
    old = next((c for c in existing if c['name'] == name), None)
    if old is None and previous_name:
        old = next((c for c in existing if c['name'] == previous_name), None)
    action = {'name': name, 'dimension': dimension, 'status': 'proposed' if approved else 'category_pending',
              'id': old['id'] if old else None, 'scope_appids': sorted(desired)}
    if old and old['dynamic']:
        action.update(action='review_dynamic_migration', current_storage=old['storage'],
                      reason=('30分钟规则需要静态成员；按认可方案使用原生转换保留原ID，再更新成员。'
                              if dimension == 'play_status' else
                              '按已核实的分类成员准备静态集合；按认可方案使用原生转换保留原ID。'))
        return action
    before = stored_members(old) if old else set()
    outside = before - scope
    action.update(action='update_membership' if old else 'create',
                  add_appids=sorted(desired - before), remove_appids=sorted((before & scope) - desired),
                  preserved_outside_scope=sorted(outside), desired_appids=sorted(desired | outside))
    if old and old['name'] != name:
        action.update(action='rename_and_update', previous_name=old['name'])
    elif old and not action['add_appids'] and not action['remove_appids']:
        action['action'] = 'preserve'
    return action


def write_category_audit(root, types, games, type_groups, overlaps):
    auxiliary = read(root / 'data/classification_discussion.json', {}).get('collection_policy', {}).get('auxiliary_collections', [])
    definitions = {}
    for line in (root / 'classification_categories.md').read_text(encoding='utf-8').splitlines():
        cells = [cell.strip() for cell in line.strip('|').split('|')]
        if line.startswith('|') and len(cells) == 2 and cells[0] in types['approved_types']:
            definitions[cells[0]] = cells[1]
    categories = []
    for name in types['approved_types']:
        ids = sorted(type_groups.get(name, set()))
        categories.append({'name': name, 'purpose': definitions.get(name, ''), 'count': len(ids),
                           'examples': [games[appid]['name'] for appid in ids[:4]]})
    data = {'rule_version': types['rule_version'], 'status': 'proposal_audit',
            'categories': categories, 'overlaps': overlaps,
            'auxiliary': auxiliary, 'source': '当前规则与实际收藏夹成员'}
    save(root / 'data/category_audit.json', data)
    lines = ['# 玩法入口与实际成员核对', '',
             '本页由当前规则和收藏夹提案生成；用户确认状态保存在逐游戏记录中。附加筛选按当前收藏集规则维护。', '',
             '| 入口 | 成员数 | 挑选用途 | 代表游戏 |', '| --- | ---: | --- | --- |']
    lines += [f"| {item['name']} | {item['count']} | {item['purpose']} | {'、'.join(item['examples'])} |" for item in categories]
    lines += ['', '## 玩法入口交叉', '', '交叉只比较玩法集合；多人、游玩状态和时长独立使用。', '']
    lines += [f"- {item['a']}／{item['b']}：共同{len(item['intersection'])}款，相似度{item['jaccard']:.0%}。" for item in overlaps[:10]]
    (root / 'output/category_audit.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def run(root=ROOT):
    inventory = read(root / 'output/inventory.json')
    live = read(root / 'output/live_library.json')
    if inventory['account_id'] != live['account_id']:
        raise ValueError('Inventory and live collection accounts differ')
    games = {g['appid']: g for g in inventory['entries'] if g['decision'] == 'game'}
    scope = set(games)
    types = read(root / 'state/classifications.json')
    manual_path = root / 'state/manual_memberships.json'
    manual = read(manual_path, {'memberships': {}, 'structural_changes': [], 'preserved_names': [], 'preserved_ids': []})
    if not manual_path.exists():
        save(manual_path, manual)
    discussion = read(root / 'data/classification_discussion.json', {})
    policy = discussion.get('collection_policy', {})
    multiplayer = read(root / 'state/multiplayer.json')
    cache = read(root / 'data/completion_times.json', {})
    decisions = read(root / 'data/duration_decisions.json', {})
    old_durations = read(root / 'state/durations.json', {'games': {}})
    durations = dict(old_durations.get('games', {}))
    groups = defaultdict(set)
    dimensions = {}
    primary_counts = Counter()
    for appid, game in games.items():
        key = str(appid)
        classification = types['games'].get(key, {})
        primary = classification.get('primary_type')
        if primary:
            primary_counts[primary] += 1
        for name in type_collections(classification, multiplayer['games'].get(key, {})):
            if name:
                groups[name].add(appid)
                dimensions[name] = 'type'
        minutes = game.get('playtime_minutes')
        if minutes is not None:
            name = '玩过' if minutes >= 30 else '没玩过'
            groups[name].add(appid)
            dimensions[name] = 'play_status'
        network = multiplayer['games'].get(key, {}).get('group')
        if network:
            groups[network].add(appid)
            dimensions[network] = 'multiplayer'
        duration = duration_record(game, cache, decisions, durations.get(key))
        durations[key] = duration
        if duration['bucket']:
            groups[duration['bucket']].add(appid)
            dimensions[duration['bucket']] = 'duration'
    save(root / 'state/durations.json', {'policy': '主线≤10小时／>10且≤30小时／>30小时；长期常驻单列', 'games': durations})
    custom = [c for c in live['collections'] if c['id'] not in ('favorite', 'hidden')]
    # Existing managed categories still need a delta when their last scoped member leaves.
    managed = {name: 'type' for name in types['approved_types']}
    managed.update({name: 'multiplayer' for name in multiplayer['groups']})
    managed.update({name: 'play_status' for name in ('玩过', '没玩过')})
    managed.update({name: 'duration' for name in ('时长-短篇', '时长-中篇', '时长-长篇', '时长-长期常驻')})
    for collection in custom:
        name = collection['name']
        if name in managed and name not in groups and stored_members(collection) & scope:
            groups[name] = set()
            dimensions[name] = managed[name]
    for name, override in manual.get('memberships', {}).items():
        if name in managed:
            groups[name] = (groups[name] | (set(override.get('include', [])) & scope)) - set(override.get('exclude', []))
            dimensions[name] = managed[name]
    for name in manual.get('preserved_names', []):
        groups.pop(name, None)
        dimensions.pop(name, None)
    actions = [collection_action(n, ids, custom, scope, dimensions[n],
                                dimensions[n] != 'type' or n in types['approved_types'],
                                policy.get('renamed_collections', {}).get(n))
               for n, ids in sorted(groups.items())]
    old_network = multiplayer.get('replace_collection')
    old = next((c for c in custom if old_network and c['id'] == old_network['id']), None)
    if old:
        if old['name'] != old_network['name']:
            raise ValueError('Previous multiplayer collection changed; refresh the decision')
        actions.append({'action': 'delete', 'id': old['id'], 'name': old['name'], 'status': 'user_requested',
                        'dimension': 'multiplayer', 'current_storage': old['storage'],
                        'reason': '采用多人对战、好友合作、可选合作三个入口。'})
    touched = {a['id'] for a in actions if a.get('id')}
    for name, reason in policy.get('retired_collections', {}).items():
        previous = next((c for c in custom if c['name'] == name and c['id'] not in touched), None)
        if previous:
            actions.append({'action': 'delete', 'id': previous['id'], 'name': name,
                            'dimension': 'type_migration', 'status': 'proposed',
                            'current_storage': previous['storage'], 'reason': reason})
            touched.add(previous['id'])
    actions = [a for a in actions if a['name'] not in manual.get('preserved_names', [])
               and a.get('id') not in manual.get('preserved_ids', [])]
    touched = {a['id'] for a in actions if a.get('id')}
    actions += [{'action': 'preserve', 'id': c['id'], 'name': c['name'], 'dimension': 'existing',
                 'current_storage': c['storage']} for c in live['collections'] if c['id'] not in touched]
    for action in actions:
        if action['name'] in policy.get('auxiliary_collections', []):
            action['dimension'] = 'auxiliary'
    if '想玩' not in manual.get('preserved_names', []) and not any(c['name'] == '想玩' for c in custom):
        actions.append({'action': 'create', 'name': '想玩', 'id': None, 'dimension': 'intention',
                        'status': 'proposed', 'desired_appids': [], 'reason': '由用户选择成员。'})
    type_groups = {n: ids for n, ids in groups.items() if dimensions[n] == 'type'}
    overlaps = []
    for i, (name, ids) in enumerate(sorted(type_groups.items())):
        for other, others in list(sorted(type_groups.items()))[i + 1:]:
            both = ids & others
            if both:
                overlaps.append({'a': name, 'b': other, 'intersection': sorted(both),
                                 'jaccard': round(len(both) / len(ids | others), 3)})
    overlaps.sort(key=lambda v: v['jaccard'], reverse=True)
    duration_counts = Counter(durations[str(appid)]['bucket'] or '时长未定' for appid in scope)
    counts = Counter(types['games'].get(str(appid), {}).get('review_status', 'unclassified') for appid in scope)
    inputs = ['output/inventory.json', 'output/live_library.json', 'state/classifications.json',
              'state/multiplayer.json', 'state/durations.json', 'data/duration_decisions.json', 'data/completion_times.json',
              'data/classification_discussion.json', 'state/manual_memberships.json']
    plan = {'status': 'awaiting_review', 'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'account_id': inventory['account_id'], 'rule_version': types['rule_version'],
            'scope': inventory['stats'], 'game_count': len(scope), 'type_status': dict(counts),
            'primary_type_counts': dict(primary_counts),
            'type_collection_counts': {name: len(ids) for name, ids in type_groups.items()},
            'duration_counts': dict(duration_counts),
            'input_sha256': {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in inputs},
            'actions': actions, 'type_overlaps': overlaps}
    save(root / 'output/collection_plan.json', plan)
    write_category_audit(root, types, games, type_groups, overlaps)
    has_changes = any(a['action'] != 'preserve' for a in actions)
    plan_status = '有变更，供用户审阅' if has_changes else '与当前Steam收藏集一致，差额0'
    report = ['# 收藏集变更预览', '', f'账号：{inventory["account_id"]}；完整游戏：{len(scope)}；状态：{plan_status}。', '',
              '现有静态集合采用增减成员，保留范围外历史成员和原ID。动态集合迁移单列；个人状态及系统集合保留。', '',
              '| 收藏集 | 方案 | 本次游戏数 | 新增 | 移出 | 范围外保留 |', '| --- | --- | ---: | ---: | ---: | ---: |']
    labels = {'create': '新增', 'update_membership': '增减成员', 'preserve': '保留',
              'delete': '移除原集合', 'rename_and_update': '改名并增减成员', 'review_dynamic_migration': '讨论动态迁移'}
    for action in actions:
        if action['action'] == 'preserve':
            continue
        suffix = '（类别待确认）' if action.get('status') == 'category_pending' else ''
        report.append(f'| {action["name"]}{suffix} | {labels[action["action"]]} | {len(action.get("scope_appids", []))} | '
                      f'{len(action.get("add_appids", []))} | {len(action.get("remove_appids", []))} | {len(action.get("preserved_outside_scope", []))} |')
    kept = [a['name'] for a in actions if a['action'] == 'preserve']
    report += ['', '保留的现有集合：' + '、'.join(kept) + '。', '',
               '## 应用方法', '',
               '讨论确定类别和成员后刷新预览，逐集合调用MCP的dry_run，再按确认范围应用。改名并增减成员先rename再add/remove，沿用原ID和范围外成员；新增集合使用create。', '',
               '动态集合按已认可方案使用Steam原生FreezeToStatic()与Save()转为静态，保留原ID和写前规则快照，再增减实际成员；批量操作见README_incremental.md。', '',
               '检测到的手动调整优先保存在state/manual_memberships.json；结构变化保留现状。完整AppID增减列表、原动态规则、输入指纹与交叉统计在collection_plan.json。正式应用前重新比对客户端和这份基线。']
    (root / 'output/collection_plan.md').write_text('\n'.join(report) + '\n', encoding='utf-8')
    missing = [games[appid] for appid in sorted(scope) if not durations[str(appid)]['bucket']]
    duration_report = ['# 时长提案', '', '按主要流程估计；长期常驻按主要用途提出。所有新结果仍为AI提案。', '',
                       '| 入口 | 游戏数 |', '| --- | ---: |']
    duration_report += [f'| {name} | {count} |' for name, count in sorted(duration_counts.items())]
    duration_report += ['', '多路线文字游戏注明统计覆盖范围；沿用主要流程口径。合集、DLC、完全收集和重复周目的时间分别保存。', '',
                        '## 时长未定', '', '逐项说明已查依据及限制，后续取得可靠主要流程记录时更新时长；游戏的玩法与游玩状态独立保存。', '',
                        '| 游戏 | 当前依据 |', '| --- | --- |']
    duration_report += [f'| {g["name"]} | {durations[str(g["appid"])]["reason"]} |' for g in missing]
    duration_report += ['', '逐游戏来源、统计样本和边界提示保存在state/durations.json；原始返回状态保存在data/completion_times.json。']
    (root / 'output/duration_review.md').write_text('\n'.join(duration_report) + '\n', encoding='utf-8')
    coop_pending = [games[appid]['name'] for appid in sorted(scope)
                    if multiplayer['games'].get(str(appid), {}).get('eligibility') in ('pending', 'conditional')]
    review = ['# 当前分类概览', '',
              f'本人及家庭完整游戏{len(scope)}款；已安装{sum(g["installed"] for g in games.values())}款，未安装{sum(not g["installed"] for g in games.values())}款。', '',
              f'具体类型：用户确认{counts["confirmed"]}款，AI提案{counts["proposed"]}款。尚未分类{counts["unclassified"]}款；下面列出规则参照与实际提案成员。', '',
              f'当前预览状态：{plan_status}。正式应用历史及核对结果见state/last_application.json；新变更按本轮具体授权处理。', '',
              '## 已有清晰边界的对照', '',
              '| 代表游戏 | 当前归属 | 原因 |', '| --- | --- | --- |',
              '| 双人成行／双影奇境 | 平台跳跃＋好友合作 | 完整流程需要两名玩家 |',
              '| 传送门2／星露谷／博德之门3 | 原玩法＋可选合作 | 完整单人体验和实质合作玩法并存 |',
              '| XCOM／破门而入／影子战术／WARNO | 战术指挥 | 直接部署单位、路线与战场行动 |',
              '| 奥日两作／空洞骑士 | 类银河城 | 连通地图、解锁能力、回访探索 |',
              '| 黑神话／仁王2 | ACT动作游戏／ARPG | 战斗系统与职业装备构筑分别作为主要挑选入口 |',
              '| 千恋万花／命运石之门／逆转裁判 | galgame／文字冒险／悬疑推理 | 恋爱路线、剧情阅读、主动调查推理分别作为入口 |',
              '| 杀戮尖塔／小丑牌／怪物火车 | 肉鸽＋卡牌桌游 | 默认规则中的有用交叉 |',
              '| POOLS／千禧梦 | 漫步探索 | 场景行走与空间氛围；POOLS额外恐怖标签仍是提案 |', '',
              '## 独立筛选入口', '',
              f'玩过{len(groups["玩过"])}款，没玩过{len(groups["没玩过"])}款（30分钟界限）；想玩由用户选。', '',
              '；'.join(f'{group}{len(groups[group])}款' for group in multiplayer['groups']) + '。合作按整个游戏判断；可选合作要求所持版本无需另购合作DLC即可进行正常互联网合作，具备完整流程或充实模式。少量附加合作及仅本地／局域网合作单独记录。人数资料空值仍表示未核实。', '',
              '时长：' + '；'.join(f'{n}{v}款' for n, v in sorted(duration_counts.items())) + '。', '',
              '## 待核实的资料', '',
              f'流程时长未定{duration_counts["时长未定"]}款，按现有依据保持未定。', '',
              ('合作模式或DLC条件需核实：' + '、'.join(coop_pending) + '。' if coop_pending else '当前在线合作入口已按所持版本与实质玩法核对。'), '',
              '## 玩法收藏夹成员', '', '| 类别 | 成员数量 | 代表游戏 |', '| --- | ---: | --- |']
    discussion = read(root / 'data/classification_discussion.json', {})
    topics = [item for item in discussion.get('review_topics', []) if item.get('status') == 'to_discuss']
    location = review.index('## 已有清晰边界的对照')
    values = {'rpg_count': primary_counts['其他RPG'], 'moba_count': primary_counts['MOBA'],
              'arcade_count': primary_counts['休闲街机']}
    if topics:
        review[location:location] = ['## 具体议题', ''] + [f'{i}. **{item["topic"]}**：{item["recommendation"].format(**values)}'
                                    for i, item in enumerate(topics, 1)] + ['']
    for name, ids in sorted(type_groups.items(), key=lambda item: (-len(item[1]), item[0])):
        count = len(ids)
        examples = [games[appid]['name'] for appid in sorted(ids)][:3]
        suffix = '（待采用）' if name not in types['approved_types'] else ''
        review.append(f'| {name}{suffix} | {count} | {"、".join(examples)} |')
    review += ['', '## 收藏集的重叠', '',
               '类型以一个主归属为主，额外入口单独记录。交叉统计仅比较玩法类型，游玩状态、联机与时长分别用于选择。', '']
    review += [f'- {v["a"]}／{v["b"]}：共同{len(v["intersection"])}款，集合相似度{v["jaccard"]:.0%}。' for v in overlaps[:5]]
    review += ['', '## 进一步查看', '',
               '- [收藏集增减预览](collection_plan.md)：每类新增与移出数量、动态迁移。',
               '- [时长依据和缺口](duration_review.md)：主线统计、版本关系与缺失项。',
               '- [联机代表游戏](multiplayer_review.md)：人数、独玩与主要用途。',
               '- [完整逐游戏结果](preclassification.md)：按需检索某款游戏。', '',
               '用户修订保存后重新生成差额；根据本轮具体授权批量预演、应用并核对。']
    (root / 'output/review_ready.md').write_text('\n'.join(review) + '\n', encoding='utf-8')
    print(json.dumps({'games': len(scope), 'actions': dict(Counter(a['action'] for a in actions)),
                      'duration_counts': dict(duration_counts)}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    run(parser.parse_args().root)
