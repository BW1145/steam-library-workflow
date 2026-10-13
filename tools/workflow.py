"""Portable Steam library workflow. AI supplies decisions; Steam changes use the MCP."""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from workflow_config import CONFIG_NAME, load_config
from prepare_plan import stored_members

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DOCS = ['README.md', 'README_incremental.md', 'classification_categories.md',
               'steam_collection_agent_generic.md', 'AGENTS.md', '.gitignore']
MCP_FILES = ['package.json', 'package-lock.json', 'LICENSE', 'README.md', 'AGENTS.md',
             'tsconfig.json', 'tsconfig.check.json', 'vitest.config.ts',
             'vitest.integration.config.ts', 'eslint.config.mjs', 'knip.jsonc',
             '.env.example', '.prettierrc.json', '.prettierignore', '.editorconfig',
             '.gitattributes', '.gitignore', '.nvmrc']
MCP_DIRS = ['src', 'scripts', 'test', 'assets', 'plugins']


def read(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def registry_value(key, name):
    if os.name != 'nt':
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            return winreg.QueryValueEx(handle, name)[0]
    except FileNotFoundError:
        return None


def active_account():
    return registry_value(r'Software\Valve\Steam\ActiveProcess', 'ActiveUser')


def bind_account(root, account):
    if not 0 < int(account) < 2**32:
        raise ValueError('account must be a SteamID32, not a SteamID64')
    for relative in [CONFIG_NAME, 'output/inventory.json', 'output/live_library.json']:
        current = read(root / relative, {})
        if current.get('account_id') is not None and int(current['account_id']) != int(account):
            raise ValueError('Workspace belongs to another account; extract a fresh package for the new account')


def initialize(root, args):
    previous = read(root / CONFIG_NAME, {})
    account = args.account or previous.get('account_id') or active_account()
    steam = args.steam_root or previous.get('steam_root') or registry_value(r'Software\Valve\Steam', 'SteamPath')
    node = args.node or previous.get('node') or shutil.which('node')
    npm = args.npm_cli or previous.get('npm_cli')
    if not account or not steam or not node:
        raise ValueError('Provide --account (SteamID32), --steam-root and --node when automatic detection is unavailable')
    bind_account(root, account)
    node = str(Path(node).resolve())
    if not npm:
        candidates = [Path(node).parent / 'node_modules/npm/bin/npm-cli.js',
                      Path(node).parent.parent / 'node_modules/npm/bin/npm-cli.js']
        npm = next((str(p) for p in candidates if p.exists()), None)
    if not npm or not Path(npm).is_file():
        raise ValueError('Provide --npm-cli pointing to the npm installation npm-cli.js')
    version = subprocess.check_output([node, '--version'], text=True).strip()
    if tuple(map(int, version.lstrip('v').split('.'))) < (22, 22, 1):
        raise ValueError('Node.js 22.22.1 or newer is required')
    config = {'workspace_root': str(root.resolve()), 'steam_root': str(Path(steam).resolve()),
              'account_id': int(account), 'node': node, 'python': sys.executable,
              'npm_cli': str(Path(npm).resolve()), 'debug_port': args.port}
    profile = read(root / 'profiles/default.json')
    templates = {
        'state/classifications.json': {'rule_version': profile['rule_version'],
                                      'approved_types': profile['approved_types'], 'games': {}},
        'state/multiplayer.json': {'rule_version': profile['rule_version'],
                                  'groups': profile['groups'], 'replace_collection': None, 'games': {}},
        'state/durations.json': {'games': {}},
        'state/manual_memberships.json': {'memberships': {}, 'structural_changes': [], 'preserved_names': [], 'preserved_ids': []},
        'data/classification_discussion.json': {'rule_version': profile['rule_version'],
            'collection_policy': {'renamed_collections': {}, 'retired_collections': {},
                                  'auxiliary_collections': []}, 'review_topics': []},
        'data/inventory_scope.json': {'preview_appids': {}},
        'data/duration_decisions.json': {'ongoing': {}, 'edition_references': {},
                                       'official_estimates': {}, 'public_estimates': {}},
        'data/completion_times.json': {}, 'data/public_metadata.json': {},
        'data/game_research.json': {}}
    for relative, value in templates.items():
        if not (root / relative).exists():
            save(root / relative, value)
    (root / 'output').mkdir(exist_ok=True)
    env_path = root / 'steam-mcp/.env'
    env_text = env_path.read_text(encoding='utf-8') if env_path.exists() else ''
    replacements = {'STEAM_ROOT': config['steam_root'].replace('\\', '/'),
        'STEAM_ACCOUNT_ID': str(account), 'STEAM_DEBUG_PORT': str(args.port),
        'STEAM_MCP_ALLOW_WRITES': '0',
        'STEAM_MCP_CACHE_DIR': (root / '.steam-mcp-cache').as_posix()}
    lines = [line for line in env_text.splitlines()
             if not re.match(r'^\s*(?:export\s+)?(' + '|'.join(replacements) + r')\s*=', line)]
    lines += [f'{key}={json.dumps(value, ensure_ascii=False)}' for key, value in replacements.items()]
    env_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    save(root / CONFIG_NAME, config)
    save(root / 'mcp.local.json', {'mcpServers': {'steam': {'command': node,
        'args': ['--env-file=' + str(env_path), str(root / 'steam-mcp/dist/index.js')],
        'cwd': str(root / 'steam-mcp'), 'env': {'STEAM_MCP_ALLOW_WRITES': '0'}}}})
    print(json.dumps({'initialized': True, 'existing_state_preserved': True}, ensure_ascii=False))


def runtime_env(config):
    env = os.environ.copy()
    # Explicit values take precedence over stale caller or client settings.
    env.update(STEAM_ROOT=config['steam_root'], STEAM_ACCOUNT_ID=str(config['account_id']),
               STEAM_DEBUG_PORT=str(config['debug_port']), STEAM_MCP_ALLOW_WRITES='0',
               STEAM_MCP_CACHE_DIR=str(Path(config['workspace_root']) / '.steam-mcp-cache'),
               PYTHONUTF8='1', HUSKY='0')
    env['PATH'] = str(Path(config['node']).parent) + os.pathsep + env.get('PATH', '')
    return env


def execute(config, command, cwd):
    subprocess.run(command, cwd=cwd, env=runtime_env(config), check=True)


def python_tool(root, config, name, *args):
    execute(config, [config['python'], '-X', 'utf8', str(root / 'tools' / name), *map(str, args)], root)


def node_script(root, config, name, *args):
    execute(config, [config['node'], '--env-file-if-exists=.env',
                     'scripts/' + name, *map(str, args)], root / 'steam-mcp')


def refresh(root, config):
    active = active_account()
    if active and int(active) != config['account_id']:
        raise ValueError('Steam is logged into another account; switch back before refreshing')
    # Stage the snapshot so a failed export cannot replace the last usable baseline.
    temporary = root / 'output/live_library.pending.json'
    node_script(root, config, 'export-library.mjs', temporary)
    live = read(temporary)
    bind_account(root, live['account_id'])
    from manual_changes import reconcile, managed_names
    inventory = read(root / 'output/inventory.json', {'entries': []})
    scope = {g['appid'] for g in inventory['entries'] if g['decision'] == 'game'}
    for values in live['snapshot']['rows']:
        game = dict(zip(live['snapshot']['cols'], values))
        if (game.get('kind') == 1 and (game.get('owned') or game.get('borrowed'))
                and live['app_types'].get(str(game['appid'])) == 1):
            scope.add(game['appid'])
    manual, changes = reconcile(read(root / 'output/live_library.json'), live,
        read(root / 'state/manual_memberships.json', {}),
        managed_names(read(root / 'state/classifications.json'), read(root / 'state/multiplayer.json')), scope)
    save(root / 'state/manual_memberships.json', manual)
    save(root / 'output/manual_changes.json', changes)
    if changes:
        print(json.dumps({'manual_collection_changes_preserved': len(changes)}, ensure_ascii=False))
    temporary.replace(root / 'output/live_library.json')
    python_tool(root, config, 'import_live_inventory.py')
    python_tool(root, config, 'review_existing.py')
    python_tool(root, config, 'prepare_review.py')


def make_plan(root, config):
    bind_account(root, config['account_id'])
    python_tool(root, config, 'prepare_review.py')
    python_tool(root, config, 'prepare_plan.py')


def ready(root):
    inventory = read(root / 'output/inventory.json')
    state = read(root / 'state/classifications.json')
    networks = read(root / 'state/multiplayer.json')
    durations = read(root / 'state/durations.json')
    gaps = []
    for game in inventory['entries']:
        if game['decision'] != 'game':
            continue
        key = str(game['appid'])
        classification = state['games'].get(key, {})
        names = [classification.get('primary_type'), *classification.get('secondary_types', [])]
        allowed = set(state['approved_types'])
        if classification.get('primary_type') == '其他' and classification.get('reason'):
            allowed.add('其他')  # Descriptive records without a gameplay collection.
        if any(not name or name not in allowed for name in names):
            gaps.append(f"{game['name']}: classify or approve the category")
        network = networks['games'].get(key, {})
        if network.get('eligibility') in ('pending', 'conditional'):
            gaps.append(f"{game['name']}: verify online cooperation requirements")
        duration = durations['games'].get(key, {})
        if duration.get('basis') == 'unresolved' or not duration:
            gaps.append(f"{game['name']}: research main-story duration")
    if gaps:
        raise ValueError('AI research is incomplete:\n' + '\n'.join(gaps[:12])
                         + (f'\n... {len(gaps)} gaps total' if len(gaps) > 12 else ''))
    return read(root / 'output/collection_plan.json')


def verify_receipt(root, receipt, live, disk):
    if receipt['status'] != 'complete' or receipt['account_id'] != live['account_id']:
        raise ValueError('Incomplete receipt or wrong account')
    actual = {item['id']: item for item in live['collections']}
    on_disk = {item['id']: item for item in disk}
    for expected in receipt['collections']:
        if 'expected_appids' not in expected:
            continue  # Deletions are covered by the complete storage comparison below.
        item = actual.get(expected['id'])
        assert item and not item['dynamic'] and item['name'] == expected['name'], expected['name']
        assert stored_members(item) == set(expected['expected_appids']), expected['name']
        saved = on_disk.get(expected['id'])
        assert saved and set(saved['explicit_appids']) == set(expected['expected_appids']), expected['name']
    compact = lambda rows: {c['id']: c['storage'] for c in rows}
    assert compact(receipt['after']) == compact(live['collections']), 'Fresh client read differs from receipt'
    for old in receipt['before']:
        if old['id'] not in actual:
            assert old['id'] not in on_disk, 'Deleted collection remains on disk'


def batch(root, config, mode):
    active = active_account()
    if active and int(active) != config['account_id']:
        raise ValueError('Steam is logged into another account')
    plan = ready(root)
    if int(plan['account_id']) != config['account_id']:
        raise ValueError('Plan belongs to another account')
    if not any(a['action'] != 'preserve' for a in plan['actions']):
        print('No Steam collection changes required.')
        return
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    receipt_path = root / f'output/{mode}_receipt_{stamp}.json'
    if mode == 'apply':
        snapshot = root / f'data/snapshots/application-{stamp}'
        snapshot.mkdir(parents=True)
        shutil.copy2(root / 'output/collection_plan.json', snapshot / 'collection_plan.json')
        for relative, expected in plan['input_sha256'].items():
            source = root / relative
            if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
                raise ValueError('Plan inputs changed; regenerate the preview')
            target = snapshot / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    node_script(root, config, 'apply-plan.mjs', mode, root / 'output/collection_plan.json', receipt_path)
    if mode == 'apply':
        receipt = read(receipt_path)
        node_script(root, config, 'export-library.mjs', root / 'output/live_library.json')
        from review_existing import read_collections
        namespace = Path(config['steam_root']) / f"userdata/{config['account_id']}/config/cloudstorage/cloud-storage-namespace-1.json"
        verify_receipt(root, receipt, read(root / 'output/live_library.json'), read_collections(namespace))
        make_plan(root, config)
        remaining = [a for a in read(root / 'output/collection_plan.json')['actions'] if a['action'] != 'preserve']
        if remaining:
            raise ValueError('Applied changes verified, but regenerated plan still has differences')
        save(root / 'state/last_application.json', {'account_id': config['account_id'],
             'rule_version': plan['rule_version'], 'status': 'verified',
             'receipt': str(receipt_path.relative_to(root)), 'snapshot': str(snapshot.relative_to(root)),
             'backup_path': receipt['backup_path'], 'remaining_changes': 0})


def package_paths(root):
    paths = [root / name for name in PUBLIC_DOCS]
    paths += sorted((root / 'tools').glob('*.py'))
    paths += sorted((root / 'tests').glob('*.py'))
    paths += sorted((root / 'profiles').glob('*.json'))
    mcp = root / 'steam-mcp'
    paths += [mcp / name for name in MCP_FILES]
    for name in MCP_DIRS:
        paths += sorted(p for p in (mcp / name).rglob('*') if p.is_file()
                        and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.log'))
    return paths


def privacy_check(root, paths):
    # Also inspect the files actually placed in the archive, not just ignore rules.
    private = read(root / CONFIG_NAME, {})
    account = private.get('account_id') or read(root / 'output/inventory.json', {}).get('account_id')
    forbidden = [str(root.resolve())]
    if os.name == 'nt':
        forbidden.append(Path.home().name)
    loginusers = Path(private.get('steam_root', '.')) / 'config/loginusers.vdf'
    if account and loginusers.exists():
        login = loginusers.read_text(encoding='utf-8-sig')
        steamid = str(76561197960265728 + int(account))
        match = re.search(r'"' + steamid + r'"\s*\{([^}]+)', login)
        if match:
            name = re.search(r'"AccountName"\s*"([^"]+)"', match[1], re.I)
            if name:
                forbidden.append(name[1])
    if account:
        forbidden += [str(account), str(76561197960265728 + int(account))]
    for path in paths:
        content = path.read_bytes()
        if b'\0' in content or path.suffix.lower() in ('.png', '.jpg', '.gz'):
            continue
        text = content.decode('utf-8-sig')
        if any(value in text for value in forbidden):
            raise ValueError(f'Personal account or workspace path in package: {path.relative_to(root)}')
        if re.search(r'[A-Za-z]:[\\/]+Users[\\/]+(?!Public\b|<)[^\\/\s"\x27]+', text):
            raise ValueError(f'Personal home directory in package: {path.relative_to(root)}')


def make_package(root, output):
    paths = package_paths(root)
    privacy_check(root, paths)
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.tmp')
    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(path, path.relative_to(root).as_posix())
    temporary.replace(output)
    with zipfile.ZipFile(output) as archive:
        assert sorted(archive.namelist()) == sorted(p.relative_to(root).as_posix() for p in paths)
        assert archive.testzip() is None
    print(json.dumps({'package': str(output), 'files': len(paths), 'personal_data_included': False}, ensure_ascii=False))
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init')
    init.add_argument('--account', type=int)
    init.add_argument('--steam-root')
    init.add_argument('--node')
    init.add_argument('--npm-cli')
    init.add_argument('--port', type=int, default=8080)
    for name in ['setup', 'status', 'connect', 'refresh', 'plan', 'preview', 'apply']:
        sub.add_parser(name)
    package = sub.add_parser('package')
    package.add_argument('--output')
    args = parser.parse_args()
    root = args.root.resolve()
    if args.command == 'init':
        initialize(root, args)
    elif args.command == 'package':
        make_package(root, args.output or root / 'release/steam-library-workflow.zip')
    else:
        config = load_config(root)
        if args.command == 'setup':
            npm = [config['node'], config['npm_cli']]
            execute(config, [*npm, 'ci'], root / 'steam-mcp')
            execute(config, [*npm, 'run', 'build'], root / 'steam-mcp')
            node_script(root, config, 'smoke-stdio.mjs')
        elif args.command == 'status':
            node_script(root, config, 'call-tool.mjs', 'steam_status', '{}')
        elif args.command == 'connect':
            node_script(root, config, 'call-tool.mjs', 'steam_restart', json.dumps({'confirm': True, 'port': config['debug_port']}))
        elif args.command == 'refresh':
            refresh(root, config)
        elif args.command == 'plan':
            make_plan(root, config)
        else:
            batch(root, config, args.command)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, AssertionError) as error:
        sys.exit(str(error))

