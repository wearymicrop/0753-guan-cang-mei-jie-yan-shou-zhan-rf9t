from app.clock import instant
from app.errors import DomainError

FIELDS = {'objects', 'replicas', 'at'}


def parse(body):
    if not isinstance(body, dict) or not set(body) <= FIELDS:
        raise DomainError('BAD_SUPPLEMENT', '补件仅支持 objects、replicas 与 at 字段')
    objects, replicas = body.get('objects', []), body.get('replicas', [])
    if not isinstance(objects, list) or not isinstance(replicas, list):
        raise DomainError('BAD_SUPPLEMENT', '补件与补测必须为记录数组')
    if any(not isinstance(row, dict) for row in (*objects, *replicas)):
        raise DomainError('BAD_SUPPLEMENT', '补件与补测必须为记录数组')
    if not objects and not replicas:
        raise DomainError('EMPTY_SUPPLEMENT', '补件与补测至少一行')
    return {'objects': objects, 'replicas': replicas, 'at': body.get('at')}


def merge(source, addition):
    at = addition['at']
    if at is not None:
        try:
            later = instant(at)
        except ValueError as error:
            raise DomainError('BAD_VALUE', '请检查日期和数据格式') from error
        if later < instant(source['at']):
            raise DomainError('AT_REGRESSION', '验收截止不能早于前版', 409)
    return {**source, 'at': at if at is not None else source['at'],
            'objects': [*source['objects'], *addition['objects']],
            'replicas': [*source['replicas'], *addition['replicas']]}


def lines(source, merged):
    return {'objects': list(range(len(source['objects']) + 1, len(merged['objects']) + 1)),
            'replicas': list(range(len(source['replicas']) + 1, len(merged['replicas']) + 1))}


def view(row):
    return {'state': row['state'], 'copies': {copy['tier']: {key: copy[key] for key in
            ('state', 'code', 'sha256', 'bytes', 'observed')} for copy in row['copies']}}


def altered(before, after):
    if before['state'] != after['state']:
        return True
    old, new = before['copies'], after['copies']
    for tier in set(old) | set(new):
        former, current = old.get(tier, {}), new.get(tier, {})
        if former.get('state') != current.get('state') or former.get('code') != current.get('code'):
            return True
    return False


def diff(base, result, parent, supplement):
    def balance(section):
        return {'status': section['reconcile']['status'], 'totals': section['reconcile']['totals']}
    before = {row['object_id']: row for row in base['drift']['rows']}
    after = {row['object_id']: row for row in result['drift']['rows']}
    changes, summary = [], {'added': 0, 'changed': 0, 'removed': 0, 'unchanged': 0}
    for ident in sorted(set(before) | set(after)):
        old, new = before.get(ident), after.get(ident)
        if new is None:
            kind = 'removed'
        elif old is None:
            kind = 'added'
        else:
            kind = 'changed' if altered(view(old), view(new)) else 'unchanged'
        summary[kind] += 1
        if kind == 'unchanged':
            continue
        row = new or old
        changes.append({'object_id': ident, 'path': row['path'], 'kind': kind,
                        'source_lines': row['source_lines'],
                        'before': view(old) if old else None, 'after': view(new) if new else None})
    return {'parent': parent, 'summary': summary, 'supplement': supplement, 'changes': changes,
            'reconcile': {'before': balance(base), 'after': balance(result)}}
