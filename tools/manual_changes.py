"""Preserve collection edits observed between two live Steam reads."""
from prepare_plan import stored_members


def managed_names(types, multiplayer):
    names = {name: 'type' for name in types['approved_types']}
    names.update({name: 'multiplayer' for name in multiplayer['groups']})
    names.update({name: 'play_status' for name in ('玩过', '没玩过')})
    names.update({name: 'duration' for name in ('时长-短篇', '时长-中篇', '时长-长篇', '时长-长期常驻')})
    names['想玩'] = 'intention'
    return names


def reconcile(previous, current, overrides, managed, scope):
    result = {**overrides, 'memberships': dict(overrides.get('memberships', {})),
              'structural_changes': list(overrides.get('structural_changes', [])),
              'preserved_names': list(overrides.get('preserved_names', [])),
              'preserved_ids': list(overrides.get('preserved_ids', []))}
    if not previous:
        return result, []
    old = {c['id']: c for c in previous['collections']}
    new = {c['id']: c for c in current['collections']}
    changes = []
    for key, before in old.items():
        if before['name'] not in managed:
            continue
        after = new.get(key)
        structural = None
        if after is None:
            structural = 'deleted'
        elif before['name'] != after['name']:
            structural = 'renamed'
        elif before['dynamic'] != after['dynamic']:
            structural = 'dynamic_mode_changed'
        elif before['dynamic']:
            filters = lambda c: [c.get('storage', {}).get(part, {}).get('filterSpec')
                                 for part in ('roamingData', 'localData')]
            if filters(before) != filters(after):
                structural = 'dynamic_rule_changed'
        if structural:
            record = {'id': key, 'previous_name': before['name'],
                      'current_name': after['name'] if after else None, 'change': structural,
                      'observed_at': current['generated_at_utc']}
            result['structural_changes'].append(record)
            result['preserved_names'] += [before['name']] + ([after['name']] if after else [])
            result['preserved_ids'].append(key)
            changes.append(record)
        elif not before['dynamic']:
            added = (stored_members(after) - stored_members(before)) & scope
            removed = (stored_members(before) - stored_members(after)) & scope
            if added or removed:
                record_members(result, before['name'], added, removed)
                changes.append({'name': before['name'], 'added': sorted(added), 'removed': sorted(removed)})
    for key, after in new.items():
        if key not in old and after['name'] in managed and not after['dynamic']:
            members = stored_members(after) & scope
            record_members(result, after['name'], members, scope - members)
            changes.append({'name': after['name'], 'change': 'created', 'members': sorted(members)})
    result['preserved_names'] = sorted(set(result['preserved_names']))
    result['preserved_ids'] = sorted(set(result['preserved_ids']))
    return result, changes


def record_members(result, name, added, removed):
    old = result['memberships'].get(name, {})
    include = (set(old.get('include', [])) | added) - removed
    exclude = (set(old.get('exclude', [])) | removed) - added
    result['memberships'][name] = {'include': sorted(include), 'exclude': sorted(exclude)}

