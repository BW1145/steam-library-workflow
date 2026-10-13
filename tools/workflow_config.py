"""Local machine configuration, kept outside shareable workflow packages."""
import json
from pathlib import Path

CONFIG_NAME = '.steam-workflow.local.json'


def load_config(root):
    path = Path(root) / CONFIG_NAME
    if not path.exists():
        raise ValueError('Run tools/workflow.py init before using the local workflow')
    config = json.loads(path.read_text(encoding='utf-8'))
    if Path(config['workspace_root']).resolve() != Path(root).resolve():
        raise ValueError('Workspace moved; run init again to update local paths')
    return config


def resolve_steam_args(args, root):
    if args.steam_root is None or args.account is None:
        config = load_config(root)
        args.steam_root = args.steam_root or config['steam_root']
        args.account = args.account if args.account is not None else config['account_id']
    return args
