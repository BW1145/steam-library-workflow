"""Read Steam's local library entitlement caches; never change Steam files."""
import argparse
import csv
import hashlib
import json
import struct
from datetime import datetime, timezone
from pathlib import Path

from workflow_config import resolve_steam_args
INACTIVE = 0x04 | 0x08 | 0x10 | 0x20 | 0x400 | 0x800 | 0x40000


class Reader:
    def __init__(self, data, strings=None):
        self.data, self.pos, self.strings = data, 0, strings

    def take(self, size):
        if size < 0 or self.pos + size > len(self.data):
            raise ValueError('Truncated Steam cache')
        result = self.data[self.pos:self.pos + size]
        self.pos += size
        return result

    def number(self, fmt):
        return struct.unpack('<' + fmt, self.take(struct.calcsize('<' + fmt)))[0]

    def string(self):
        end = self.data.find(b'\0', self.pos)
        if end < 0:
            raise ValueError('Unterminated cache string')
        value = self.take(end - self.pos)
        self.take(1)
        # Preserve legacy non-UTF8 installation values internally.
        return value.decode('utf-8', errors='surrogateescape')

    def kv(self):
        result = {}
        while True:
            kind = self.number('B')
            if kind == 8:
                return result
            key = self.strings[self.number('I')] if self.strings is not None else self.string()
            if kind == 0:
                value = self.kv()
            elif kind == 1:
                value = self.string()
            elif kind in {2, 3, 4, 6, 7, 10}:
                value = self.number({2: 'i', 3: 'f', 4: 'I', 6: 'I', 7: 'Q', 10: 'q'}[kind])
            else:
                raise ValueError(f'Unsupported binary VDF type {kind}')
            result[key] = value


def decode_license(data, account):
    if not 0 < account < 2147483647:
        raise ValueError('Expected a Steam account ID (SteamID32)')
    seed = account
    table = [0] * 32
    for index in range(39, -1, -1):
        seed = 16807 * seed % 2147483647
        if index < 32:
            table[index] = seed
    value = table[0]
    decoded = bytearray()
    for byte in data:
        while True:
            seed = 16807 * seed % 2147483647
            index = value // 67108864
            value, table[index] = table[index], seed
            if value <= 2147483637:
                break
        decoded.append(byte ^ (32 + value % 95))
    return bytes(decoded)


def protobuf(data):
    reader = Reader(data)

    def varint():
        result = 0
        for shift in range(0, 70, 7):
            byte = reader.number('B')
            result |= (byte & 127) << shift
            if byte < 128:
                return result
        raise ValueError('Invalid protobuf varint')

    fields = []
    while reader.pos < len(data):
        key = varint()
        field, wire = key >> 3, key & 7
        if field == 0:
            raise ValueError('Invalid license protobuf; check the account ID')
        if wire == 0:
            value = varint()
        elif wire == 1:
            value = reader.number('Q')
        elif wire == 2:
            value = reader.take(varint())
        elif wire == 5:
            value = reader.number('I')
        else:
            raise ValueError(f'Unsupported protobuf wire type {wire}')
        fields.append((field, value))
    return fields


def licenses(data, account):
    payload = protobuf(decode_license(data, account)[:-4])
    if dict(payload).get(1) != 1:
        raise ValueError('Steam license cache does not report a successful result')
    result = []
    for field, value in payload:
        if field == 2:
            item = dict(protobuf(value))
            # Access tokens and raw license messages are never exported.
            result.append({'package_id': item[1], 'flags': item.get(7, 0),
                           'owner_id': item.get(12, 0)})
    return result


def app_cache(data):
    reader = Reader(data)
    magic, universe = reader.number('I'), reader.number('I')
    if magic != 0x07564429:
        raise ValueError(f'Unsupported appinfo format {magic:#x}; expected version 41')
    offset = reader.number('Q')
    table = Reader(data)
    table.pos = offset
    strings = [table.string() for _ in range(table.number('I'))]
    reader.strings = strings
    apps = {}
    while True:
        appid = reader.number('I')
        if appid == 0:
            break
        size = reader.number('I')
        end = reader.pos + size
        reader.take(60)
        item = reader.kv()
        if reader.pos > end or end > offset:
            raise ValueError('Invalid appinfo entry size')
        reader.pos = end
        apps[appid] = item.get('appinfo', {})
    return apps


def package_cache(data):
    reader = Reader(data)
    magic, universe = reader.number('I'), reader.number('I')
    if magic not in (0x06565527, 0x06565528):
        raise ValueError(f'Unsupported packageinfo format {magic:#x}')
    packages = {}
    while True:
        package_id = reader.number('I')
        if package_id == 0xffffffff:
            break
        reader.take(24 if magic == 0x06565527 else 32)
        item = reader.kv().get(str(package_id), {})
        packages[package_id] = {int(value) for value in item.get('appids', {}).values()}
    return packages


def text_vdf(data):
    import re
    tokens = re.findall(r'"((?:\\.|[^"\\])*)"|([{}])', data)
    items = iter(('brace', brace) if brace else ('text', value.replace('\\\\', '\\').replace('\\"', '"'))
                 for value, brace in tokens)

    def block(nested=False):
        result = {}
        for kind, key in items:
            if kind == 'brace':
                if key == '}' and nested:
                    return result
                raise ValueError('Invalid text VDF structure')
            value_kind, value = next(items)
            result[key] = block(True) if value_kind == 'brace' and value == '{' else value
        if nested:
            raise ValueError('Truncated text VDF')
        return result
    return block()


def installed_apps(steam_root):
    path = steam_root / 'steamapps/libraryfolders.vdf'
    libraries = {steam_root}
    if path.exists():
        folders = text_vdf(path.read_text(encoding='utf-8'))['libraryfolders']
        libraries.update(Path(item['path']) for item in folders.values() if isinstance(item, dict) and 'path' in item)
    installed = set()
    for folder in libraries:
        for manifest in (folder / 'steamapps').glob('appmanifest_*.acf'):
            state = text_vdf(manifest.read_text(encoding='utf-8'))['AppState']
            if int(state.get('StateFlags', 0)) & 4:
                installed.add(int(state['appid']))
    return installed


def build_inventory(grants, packages, apps, installed, account, include_family):
    active = [item for item in grants if not item['flags'] & INACTIVE]
    selected = [item for item in active if include_family or item['owner_id'] == account]
    owners, missing_packages = {}, set()
    for item in selected:
        package_id = item['package_id']
        if package_id not in packages:
            missing_packages.add(package_id)
        for appid in packages.get(package_id, set()):
            owners.setdefault(appid, set()).add(item['owner_id'])
    rows = []
    for appid, owner_ids in sorted(owners.items()):
        common = apps.get(appid, {}).get('common', {})
        name = str(common.get('name', ''))
        name.encode('utf-8', errors='strict')
        app_type = str(common.get('type', '')).lower()
        ownership = 'own' if account in owner_ids else 'family'
        excluded = app_type == 'game' and ownership == 'family' and str(common.get('exfgls', '0')) not in ('0', '')
        if app_type == 'game' and not excluded:
            decision = 'game'
        elif not app_type:
            decision = 'pending_type'
        elif excluded:
            decision = 'family_excluded'
        else:
            decision = 'other_content'
        rows.append({'appid': appid, 'name': name, 'app_type': app_type or 'unknown',
                     'ownership': ownership, 'installed': appid in installed,
                     'decision': decision, 'store_url': f'https://store.steampowered.com/app/{appid}/'})
    stats = {'license_count': len(grants), 'active_license_count': len(active),
             'selected_license_count': len(selected), 'inactive_license_count': len(grants) - len(active),
             'missing_package_ids': sorted(missing_packages),
             'missing_app_ids': sorted(appid for appid in owners if appid not in apps)}
    return rows, stats


def write_csv(path, rows):
    fields = ['appid', 'name', 'app_type', 'ownership', 'installed', 'decision', 'store_url', 'playtime_minutes', 'tag_ids', 'tags', 'developer', 'publisher', 'metadata_source']
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            exported = dict(row)
            for key in ('tag_ids', 'tags'):
                exported[key] = '; '.join(str(value) for value in exported.get(key, []))
            writer.writerow(exported)


def run(args):
    resolve_steam_args(args, Path(__file__).resolve().parents[1])
    steam_root = Path(args.steam_root)
    paths = {'licenses': steam_root / f'userdata/{args.account}/config/licensecache',
             'packages': steam_root / 'appcache/packageinfo.vdf',
             'apps': steam_root / 'appcache/appinfo.vdf',
             'history': steam_root / f'userdata/{args.account}/config/localconfig.vdf'}
    data = {key: path.read_bytes() for key, path in paths.items()}
    apps = app_cache(data['apps'])
    public_path = Path(args.public_metadata)
    public = json.loads(public_path.read_text(encoding='utf-8')) if public_path.exists() else {}
    supplemented = []
    for appid, item in public.items():
        if item.get('common', {}).get('type') and not apps.get(int(appid), {}).get('common', {}).get('type'):
            apps.setdefault(int(appid), {})['common'] = item['common']
            supplemented.append(int(appid))
    local = text_vdf(data['history'].decode('utf-8'))
    history = local['UserLocalConfigStore']['Software']['Valve']['Steam'].get('apps', {})
    tags_path = Path(__file__).resolve().parents[1] / 'data/steam_tags_schinese.json'
    tags = {}
    if tags_path.exists():
        tags = {int(item['tagid']): item['name'] for item in json.loads(tags_path.read_text(encoding='utf-8'))['tags']}
    rows, stats = build_inventory(licenses(data['licenses'], args.account),
                                 package_cache(data['packages']), apps,
                                 installed_apps(steam_root), args.account, args.include_family)
    for row in rows:
        info = apps.get(row['appid'], {})
        common, extended = info.get('common', {}), info.get('extended', {})
        time = history.get(str(row['appid']), {}).get('Playtime')
        row['playtime_minutes'] = int(time) if time is not None else None
        row['tag_ids'] = list(common.get('store_tags', {}).values())
        row['tags'] = [tags.get(int(tag), str(tag)) for tag in row['tag_ids']]
        row['developer'] = extended.get('developer', '')
        row['publisher'] = extended.get('publisher', '')
        row['metadata_source'] = public[str(row['appid'])]['source'] if row['appid'] in supplemented else 'local_appinfo'
    games = [row for row in rows if row['decision'] == 'game']
    pending = [row for row in rows if row['decision'] == 'pending_type']
    excluded = [row for row in rows if row['decision'] == 'family_excluded']
    stats.update({'game_count': len(games), 'own_game_count': sum(row['ownership'] == 'own' for row in games),
                  'family_game_count': sum(row['ownership'] == 'family' for row in games),
                  'installed_game_count': sum(row['installed'] for row in games),
                  'uninstalled_game_count': sum(not row['installed'] for row in games),
                  'pending_type_count': len(pending), 'family_excluded_count': len(excluded),
                  'public_metadata_supplemented_ids': supplemented,
                  'playtime_known_count': sum(row['playtime_minutes'] is not None for row in games),
                  'played_at_least_30_count': sum((row['playtime_minutes'] or 0) >= 30 for row in games),
                  'playtime_missing_count': sum(row['playtime_minutes'] is None for row in games)})
    snapshot = {'generated_at_utc': datetime.now(timezone.utc).isoformat(),
                'account_id': args.account, 'include_family': args.include_family,
                'source_sha256': {key: hashlib.sha256(value).hexdigest() for key, value in data.items()},
                'stats': stats, 'entries': rows}
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / 'inventory.csv', games)
    write_csv(output / 'pending_inventory.csv', pending)
    write_csv(output / 'licensed_content.csv', rows)
    (output / 'inventory.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    examples = [row for row in games if not row['installed']][:10]
    report = f'''# Steam 库存读取报告

读取时间（UTC）：{snapshot['generated_at_utc']}  
账号编号：{args.account}  
范围：{'本人和家庭共享' if args.include_family else '本人拥有'}

| 项目 | 数量 |
| --- | ---: |
| 缓存中的许可证记录 | {stats['license_count']} |
| 当前有效许可证记录 | {stats['active_license_count']} |
| 已明确为游戏、按 AppID 去重 | {len(games)} |
| 本人游戏 | {stats['own_game_count']} |
| 家庭共享游戏 | {stats['family_game_count']} |
| 已安装游戏 | {stats['installed_game_count']} |
| 未安装游戏 | {stats['uninstalled_game_count']} |
| 类型待核实的授权条目 | {len(pending)} |
| 家庭授权关联、但标有共享排除的游戏 | {len(excluded)} |
| 找不到包映射的有效许可证 | {len(stats['missing_package_ids'])} |
| 找不到应用缓存的授权 AppID | {len(stats['missing_app_ids'])} |
| 本地游玩时间达到 30 分钟的游戏 | {stats['played_at_least_30_count']} |
| 本地缺少游玩时间的游戏 | {stats['playtime_missing_count']} |

## 读取依据

从账号的 licensecache 读取本机缓存的授权记录，通过 packageinfo.vdf 将包映射为 AppID，再使用 appinfo.vdf 的应用类型和名称识别游戏。安装状态另从各游戏库的 appmanifest 文件读取，因此未安装游戏也能进入清单。

家庭记录代表本机缓存的借用授权，ownership=family 不表示账号本人购买了这些游戏。本人和家庭重复持有的游戏只计一次，本人持有优先。

许可证缓存使用账号编号生成的字节流还原；该编号不是密码。导出只保留应用、归属和安装状态，不保存授权令牌。

这些数量描述本次本机缓存快照。类型待核实的条目单独保存；尚未与 Steam 客户端界面的库总数逐项对照。共享排除条目保留在 licensed_content.csv，判断依据为本地 common.exfgls 非零。

游玩时间来自该账号 localconfig.vdf。本机有记录且达到 30 分钟的游戏可作为“玩过”的初始候选；缺失记录以空值保存，不能据此断定从未游玩。

公开元数据补全只接纳查询与返回 AppID 一致的结果。缺少类型或返回了其他版本的条目单独核实。正常整理使用实时客户端导出，本地许可证读取作为补充核对。

## 文件

- inventory.csv：已明确类型的游戏，UTF-8 BOM，可用 Excel 打开。
- pending_inventory.csv：授权存在、但本地缓存未提供应用类型的条目。
- licensed_content.csv：全部授权关联应用，包括 DLC、工具、软件、音乐和排除项，保留筛选依据。
- inventory.json：机器可读快照、统计和来源文件哈希。

## 未安装游戏示例

'''
    report += '\n'.join(f"- {row['name']}（{row['appid']}，{'本人' if row['ownership'] == 'own' else '家庭'}）" for row in examples)
    report += '''

## 格式参考

- [许可证缓存读取参考](https://github.com/julianxhokaxhiu/Depressurizer/blob/master/src/Depressurizer.Core/Models/SteamLicenseParser.cs)
- [Steam 消息协议](https://github.com/SteamDatabase/Protobufs/blob/master/steam/steammessages_clientserver.proto)
- [许可证状态定义](https://github.com/SteamRE/SteamKit/blob/master/Resources/SteamLanguage/enums.steamd)

读取脚本只写本项目的导出目录。分类结果和 Steam 收藏集写入属于后续阶段。
'''
    (output / 'inventory_report.md').write_text(report, encoding='utf-8')
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--steam-root')
    parser.add_argument('--account', type=int)
    parser.add_argument('--include-family', action='store_true')
    parser.add_argument('--public-metadata', default=str(Path(__file__).resolve().parents[1] / 'data/public_metadata.json'))
    parser.add_argument('--output', default=str(Path(__file__).resolve().parents[1] / 'output'))
    run(parser.parse_args())