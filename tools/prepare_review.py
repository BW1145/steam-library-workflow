"""Prepare library-wide classification review, reusing stored decisions."""
import argparse
import csv
import html
import json
import re
from collections import Counter
from pathlib import Path
from classification_policy import type_collections

ROOT = Path(__file__).resolve().parents[1]
EXISTING_PLAY_TYPES = {'模拟经营', 'galgame', '射击', 'ACT动作游戏', '开放世界', '其他', '即时策略',
                       'RPG', 'CRPG', '模拟', '沙盒', 'ARPG', 'JRPG', '潜行', '生存', '策略',
                       '肉鸽', '互动电影', '回合制', '文字冒险互动游戏等', '悬疑推理', '塔防'}


def read_json(path, default):
    path = Path(path)
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def plain_text(value):
    value = re.sub(r'<[^>]*>', ' ', value or '')
    return re.sub(r'\s+', ' ', html.unescape(value)).strip()


def merge_decisions(state, decisions, valid_appids, user_correction=False):
    for item in decisions:
        if int(item['appid']) not in valid_appids:
            raise ValueError(f"AppID is outside the current game inventory: {item['appid']}")
        if not isinstance(item.get('primary_type'), str) or not item['primary_type'].strip():
            raise ValueError(f"Missing primary_type: {item['appid']}")
        if not isinstance(item.get('secondary_types', []), list):
            raise ValueError(f"secondary_types must be a list: {item['appid']}")
    saved, skipped = 0, 0
    for item in decisions:
        key = str(int(item['appid']))
        existing = state['games'].get(key, {})
        if (existing.get('user_override') or existing.get('review_status') == 'confirmed') and not user_correction:
            skipped += 1
            continue
        record = dict(item)
        record.pop('appid', None)
        record['secondary_types'] = list(dict.fromkeys(record.get('secondary_types', [])))
        record['review_status'] = 'confirmed' if user_correction else 'proposed'
        record['user_override'] = bool(user_correction)
        record['rule_version'] = state['rule_version']
        state['games'][key] = record
        saved += 1
    return saved, skipped


def build_rows(inventory, state, collections, metadata, multiplayer=None):
    rows, queue = [], []
    play_types = EXISTING_PLAY_TYPES | set(state.get('approved_types', []))
    for game in inventory['entries']:
        if game['decision'] != 'game':
            continue
        key = str(game['appid'])
        decision = state['games'].get(key, {})
        network = (multiplayer or {}).get('games', {}).get(key, {})
        time = game.get('playtime_minutes')
        play_status = '待核实' if time is None else '玩过' if time >= 30 else '没玩过'
        row = {'appid': game['appid'], 'name': game['name'],
               'primary_type': decision.get('primary_type', ''),
               'type_collections': type_collections(decision, network),
               'existing_types': [item['name'] for item in collections
                                  if item['name'] in play_types and game['appid'] in item['explicit_appids']],
               'secondary_types': decision.get('secondary_types', []),
               'type_review': decision.get('review_status', 'unclassified'),
               'playtime_minutes': time, 'play_status': play_status,
               'network_group': network.get('group') or '',
               'network_review': network.get('review_status', 'not_reviewed'),
               'network_requirements': {key: network.get(key) for key in
                                        ('solo_supported', 'minimum_players', 'coop_minimum_players', 'public_lobby_or_matchmaking', 'mode_note', 'eligibility')},
               'reason': decision.get('reason', ''), 'sources': decision.get('sources', []),
               'confidence': decision.get('confidence', 'high' if decision.get('review_status') == 'confirmed' else ''),
               'review_note': decision.get('review_note', ''),
               'candidate_new_category': '' if decision.get('candidate_new_category') in state.get('approved_types', [])
               else decision.get('candidate_new_category', '')}
        rows.append(row)
        if not row['primary_type']:
            info = metadata.get(key, {})
            queue.append({'appid': game['appid'], 'name': game['name'], 'tags': game.get('tags', []),
                          'existing_types': [item['name'] for item in collections if game['appid'] in item['explicit_appids']],
                          'official_name': info.get('common', {}).get('name'),
                          'short_description': plain_text(info.get('short_description', '')),
                          'gameplay_description': plain_text(info.get('detailed_description', '')),
                          'description_source': info.get('source'), 'public_metadata_errors': info.get('errors', [])})
    return rows, queue


def write_discussion_review(output, rows, discussion):
    counts = Counter(row['type_review'] for row in rows)
    report = ['# 分类规则与代表游戏', '',
              f'当前游戏数：{len(rows)}；具体归属已确认：{counts["confirmed"]}；AI提案：{counts["proposed"]}。', '',
              '本页用于讨论类别与少量实际边界。AI负责查询玩法和完善提案；完整逐游戏清单保存在preclassification.csv/json/md，供按需核对。', '',
              '## 已采用的新增类别', '', '| 类别 | 代表游戏 | 边界 |', '| --- | --- | --- |']
    for item in discussion.get('approved_new_categories', []):
        report.append(f'| {item["name"]} | {item["examples"]} | {item["boundary"]} |')
    report += ['', '类别已采用；代表游戏按此口径归类，具体归属仍区分用户确认与AI提案。', '',
               '## 已确认的有用交叉', '']
    for item in discussion.get('confirmed_cross_memberships', []):
        report.append(f'- {item["name"]}：{item["primary_type"]}＋{"、".join(item["secondary_types"])}。')
    report += ['', '同样的卡牌肉鸽可按此规则提出双归属，游戏确认状态分别保存。', '']
    for item in discussion.get('approved_type_boundaries', []):
        report += [f'## {item["name"]}的已采用边界', '', item['boundary'], '', item['examples'], '']
    network = discussion.get('approved_multiplayer_rules')
    if network:
        report += ['## 联机入口', '', network['boundary'], '', network['examples'], '',
                   '联机入口为多人对战、好友合作、可选合作；仅本地合作单独记在说明。具体成员见multiplayer_review.md。', '']
    report += ['## 接下来讨论的具体规则', '']
    for item in discussion.get('review_topics', []):
        if item.get('status') == 'to_discuss':
            report.append(f'- {item["topic"]}：{item["recommendation"]}')
    report += ['', '## 整理步骤', '',
               '1. AI补查实际玩法，按已采用类别完善归属；每轮提供少量有建议和例子的边界问题。',
               '2. 补齐游玩记录及典型流程时长，按30分钟、10/30小时规则生成状态与时长候选。',
               '3. 展示每个收藏集成员变化、动态规则和个人状态迁移，确认具体变更后备份写入并验证。',
               '4. 完成实际整理后，用复跑结果定稿可重复使用的工作流。', '',
               '代表游戏来源和讨论状态保存在data/classification_discussion.json；补查资料保存在data/game_research.json。']
    (output / 'classification_review.md').write_text('\n'.join(report) + '\n', encoding='utf-8')



def write_multiplayer_review(output, inventory, state, collections, multiplayer):
    games = {game['appid']: game for game in inventory['entries'] if game['decision'] == 'game'}
    records = {int(key): value for key, value in multiplayer.get('games', {}).items() if int(key) in games}
    if not records:
        return
    previous = multiplayer.get('replace_collection')
    existing = next((item for item in collections if previous and item['id'] == previous['id']), None)
    actions = []
    if existing:
        if existing['name'] != previous['name']:
            raise ValueError('The collection to replace has a different name; refresh the plan')
        actions.append({'action': 'remove_collection', 'id': existing['id'], 'name': existing['name'],
                        'previous_dynamic_filter': existing['dynamic_filter']})
    report = ['# 联机收藏集预览', '',
              '多人对战：玩家之间争胜。好友合作：完整主要流程需要搭档。可选合作：完整单人体验之外，所持版本无需另购合作DLC即可正常互联网合作，有完整流程或充实模式。', '',
              '按整个游戏判断能否独玩；原生在线合作或可远程协作的独立双端谜题符合合作用途，少量附加合作、DLC限定的小部分合作和仅本地／局域网合作单独记在说明。主要PvP射击通过多人对战收藏夹寻找，射击玩法保存在记录中。', '',
              '## 收藏集变更', '', '| 操作 | 收藏集 | 当前提案成员数 |', '| --- | --- | ---: |']
    if existing:
        report.append('| 移除 | 多人（现有动态收藏集） | 动态筛选 |')
    for group in multiplayer['groups']:
        appids = sorted(appid for appid, item in records.items() if item.get('group') == group)
        old = next((item for item in collections if item['name'] == group), None)
        actions.append({'action': 'set_collection', 'id': old['id'] if old else None, 'name': group,
                        'collection_type': 'static', 'appids': appids})
        report.append(f'| 更新' + ('现有' if old else '新增') + f' | {group} | {len(appids)} |')
    checked = sum(item.get('eligibility') not in (None, 'pending') for item in records.values())
    report += ['', f'当前保存{len(records)}款记录，其中{checked}款有本轮模式核对结果，{len(records)-checked}款尚待资料核实。Steam实际应用由整体变更预览确定。', '',
               '## 代表游戏', '', '| 游戏 | 玩法主类型 | 联机入口 | 独玩 | 人数与方式 |', '| --- | --- | --- | --- | --- |']
    for appid in [1426210, 1222700, 1515210, 620, 413150, 2868840, 728880, 3527290, 248610, 1239080, 526870, 435150, 1086940, 730, 359550, 972660]:
        if appid not in records:
            continue
        item = records[appid]
        solo = {True: '支持', False: '完整流程不支持', None: '待核实'}[item.get('solo_supported')]
        if not item.get('group'):
            solo = '按所述模式'
        note = item.get('mode_note') or item['reason']
        primary = state['games'].get(str(appid), {}).get('primary_type', '')
        report.append(f'| {games[appid]["name"]} | {primary} | {item.get("group") or item.get("eligibility_label", "待核实")} | {solo} | {note} |')
    report += ['', '资料及逐游戏判断保存在state/multiplayer.json。最低人数指真实参与者；机器人练习与完整玩家对局分别记录。资料未核实的字段保持空值，AI继续补查。', '']
    unresolved = [(appid, item) for appid, item in records.items() if item.get('eligibility') in ('pending', 'conditional')]
    report += ['## 模式与授权待核实', '', '| 游戏 | 具体资料缺口 |', '| --- | --- |']
    report += [f'| {games[appid]["name"]} | {item["mode_note"]} |' for appid, item in unresolved]
    report.append('')
    plan = {'rule_version': multiplayer['rule_version'], 'account_id': inventory['account_id'],
            'status': 'preview', 'actions': actions,
            'reviewed_games': checked, 'remaining_inventory_games': len(games) - checked,
            'unresolved_mode_appids': [appid for appid, _ in unresolved]}
    (output / 'multiplayer_plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (output / 'multiplayer_review.md').write_text('\n'.join(report), encoding='utf-8')


def run(args):
    inventory = read_json(args.inventory, {})
    metadata = read_json(ROOT / 'data/public_metadata.json', {})
    collections = read_json(ROOT / 'output/existing_collections.json', [])
    state = read_json(args.state, {'rule_version': '0.4', 'games': {}})
    game_ids = {game['appid'] for game in inventory['entries'] if game['decision'] == 'game'}
    if args.decisions:
        decisions = read_json(args.decisions, None)
        if not isinstance(decisions, list):
            raise ValueError('Decision input must be a JSON list')
        saved, skipped = merge_decisions(state, decisions, game_ids, args.user_correction)
        path = Path(args.state)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'saved_decisions': saved, 'protected_decisions': skipped}))
    multiplayer = read_json(ROOT / 'state/multiplayer.json', {})
    rows, queue = build_rows(inventory, state, collections, metadata, multiplayer)
    manual = read_json(ROOT / 'state/manual_memberships.json', {})
    for row in rows:
        appid = row['appid']
        row['manual_collections'] = {name: appid in override.get('include', [])
            for name, override in manual.get('memberships', {}).items()
            if appid in override.get('include', []) or appid in override.get('exclude', [])}
        for name, included in row['manual_collections'].items():
            if name in state.get('approved_types', []):
                if included and name not in row['type_collections']:
                    row['type_collections'].append(name)
                elif not included and name in row['type_collections']:
                    row['type_collections'].remove(name)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'preclassification.json').write_text(json.dumps({'rule_version': state['rule_version'], 'account_id': inventory['account_id'], 'games': rows}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    fields = ['appid', 'name', 'existing_types', 'primary_type', 'type_collections', 'secondary_types', 'type_review', 'playtime_minutes', 'play_status', 'reason', 'sources', 'confidence', 'review_note', 'candidate_new_category', 'network_group', 'network_review', 'network_requirements', 'manual_collections']
    with (output / 'preclassification.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            exported = dict(row)
            for key in ('existing_types', 'type_collections', 'secondary_types', 'sources'):
                exported[key] = '; '.join(str(value) for value in exported[key])
            exported['network_requirements'] = json.dumps(exported['network_requirements'], ensure_ascii=False)
            exported['manual_collections'] = json.dumps(exported['manual_collections'], ensure_ascii=False)
            writer.writerow(exported)
    with (output / 'to_classify.jsonl').open('w', encoding='utf-8') as handle:
        for item in queue:
            handle.write(json.dumps(item, ensure_ascii=False) + '\n')
    discussion = read_json(args.discussion, {})
    write_discussion_review(output, rows, discussion)
    write_multiplayer_review(output, inventory, state, collections, multiplayer)
    counts = Counter(row['type_review'] for row in rows)
    type_counts = Counter(row['primary_type'] for row in rows if row['primary_type'])
    report = ['# 全库预分类核对清单', '',
              'AI在聊天说明本轮修改与代表游戏；本文件保存完整逐游戏结果，供检索和核对。', '', f'游戏总数：{len(rows)}；已确认类型：{counts["confirmed"]}；类型提案：{counts["proposed"]}；待分类：{len(queue)}。', '',
              '已保存的确认结果和用户修订直接复用。待分类条目的已缓存玩法资料在 to_classify.jsonl。', '',
              '## 当前类型分布', '', '| 主类型 | 数量 |', '| --- | ---: |']
    report += [f'| {name} | {count} |' for name, count in type_counts.most_common()]
    candidates = Counter(row['candidate_new_category'] for row in rows if row['candidate_new_category'])
    if candidates:
        report += ['', '## 新类型候选', '', '| 候选名称 | 当前提案数 |', '| --- | ---: |']
        report += [f'| {name} | {count} |' for name, count in candidates.items()]
    notes = [row for row in rows if row['review_note']]
    if notes:
        report += ['', '## 需要核对的边界', '', '| 游戏 | 提案 | 核对原因 |', '| --- | --- | --- |']
        report += [f'| {row["name"]} | {row["primary_type"]} | {row["review_note"]} |' for row in notes]
    changed = [row for row in rows if row['primary_type'] and row['existing_types']
               and row['primary_type'] not in row['existing_types']]
    if changed:
        report += ['', '## 与原有类型不同的提案', '', '| 游戏 | 原类型 | 当前主类型 | 理由 |', '| --- | --- | --- | --- |']
        report += [f'| {row["name"]} | {"、".join(row["existing_types"])} | {row["primary_type"]} | {row["reason"]} |' for row in changed]
    report += ['', '## 已有结果', '', '| 游戏 | 玩法记录 | 玩法收藏夹 | 核对状态 |', '| --- | --- | --- | --- |']
    status_labels = {'confirmed': '已确认', 'proposed': '提案'}
    report += [f'| {row["name"]} | {row["primary_type"]} | {"、".join(row["type_collections"]) or ("多人对战" if row["network_group"] == "多人对战" else "待归类")} | {status_labels.get(row["type_review"], row["type_review"])} |' for row in rows if row['primary_type']]
    report += ['', '## 后续处理', '', 'AI逐批核实剩余游戏的主要体验，保存决策JSON，再导入本脚本。类型提案保留为proposed，用户明确修订的结果通过--user-correction保存并优先复用。', '',
               '游玩时间空值保持待核实。具体时长估计、想玩选择和实际Steam变更在后续步骤处理。']
    (output / 'preclassification.md').write_text('\n'.join(report) + '\n', encoding='utf-8')
    print(json.dumps({'games': len(rows), 'type_status': dict(counts), 'to_classify': len(queue)}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', default=str(ROOT / 'output/inventory.json'))
    parser.add_argument('--state', default=str(ROOT / 'state/classifications.json'))
    parser.add_argument('--output', default=str(ROOT / 'output'))
    parser.add_argument('--discussion', default=str(ROOT / 'data/classification_discussion.json'))
    parser.add_argument('--decisions', help='Import an AI decision list')
    parser.add_argument('--user-correction', action='store_true', help='Import explicit user corrections and preserve them')
    run(parser.parse_args())