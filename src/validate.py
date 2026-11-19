"""Checks status.json before it is saved or published. Read-only: it never changes a file.

Run:   python3 src/validate.py                      (checks ../status.json)
       python3 src/validate.py path/to/status.json  [--index path/to/index.html] [--strict]
Exit:  0 no errors (warnings allowed), 1 errors found, 2 file could not be read.
       With --strict, warnings also give exit code 1.
Works on Python 3.8 or newer. Standard library only.

ERROR   = the page would show something wrong or silently ignore it. Fix before saving.
WARN    = suspicious, but the page copes. The Architect decides.
"""
import json, os, re, sys
from datetime import datetime, timedelta, timezone

STATUS = ['live', 'partial', 'built', 'design', 'parked', 'unknown']
TASK = ['pending', 'in_progress', 'blocked', 'done']
PRIORITY = ['not_started', 'in_progress', 'done']
GROUPS = ['Routing', 'Handlers', 'KB pipeline', 'Background workers', 'Platform']
WORKING = ('live', 'partial')            # the page draws an arrow solid only for these
STALE_HOURS = 24                         # same limit as the page's own warning
TOP = ['updated', 'updated_by', 'architecture_version', 'intents', 'delivery', 'attention', 'elements']
EL_FIELDS = ['name', 'group', 'status', 'short', 'note', 'owner']
INTENT_FIELDS = ['n', 'name', 'path', 'status', 'note']
PRIO_FIELDS = ['p', 'name', 'state', 'note', 'tasks']
TASK_FIELDS = ['id', 'title', 'state', 'owner']
# Optional task fields (D4, 19 Nov 2026). The page does not show them. Absent is always fine.
TASK_OPTIONAL = ['stage', 'summary', 'waiting_on']
STAGES = ['design', 'QC', 'build', 'import_prep', 'import', 'test', 'active', 'done']
TASK_LIMITS = {'summary': 200, 'waiting_on': 100}      # characters
LANES = ['Next', 'Later', 'Backlog']      # task ids used for planned work that has no TSK number yet

# ASSUMPTION (mine, not in status.json): which parts each intent's path names.
# Intents only carry free text, so this table is kept here. If an intent's path
# changes in ARCHITECTURE.md, update this table in the same change.
INTENT_PARTS = {
    1: ['r4', 'aiti', 'aiti_intake'],
    2: ['r4', 'aiti', 'aiti_it_qa'],
    3: ['r4', 'aiti', 'aiti_it_tickets'],
    4: ['r2', 'kb_receiver'],
    5: ['r1', 'ping_ingress'],
}

ISO = re.compile(r'^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:\d{2})$')
# Data rules from README: no phone numbers, WhatsApp IDs, tokens or keys.
SECRET_RULES = [
    ('a number of 10 or more digits (phone number?)', re.compile(r'\d{10,}')),
    ('a WhatsApp ID', re.compile(r'@(c\.us|s\.whatsapp\.net|g\.us)')),
    ('something that looks like a token or key',
     re.compile(r'(?i)(bearer\s+[a-z0-9._-]{20,}|api[_-]?key\s*["\':=]+\s*[a-z0-9]{16,}|eyJ[a-zA-Z0-9_-]{20,})')),
]


class Report(object):
    def __init__(self):
        self.errors, self.warnings = [], []

    def error(self, where, msg):
        self.errors.append((where, msg))

    def warn(self, where, msg):
        self.warnings.append((where, msg))


def is_text(x):
    return isinstance(x, str) and x.strip() != ''


def one_of(value, allowed):
    return isinstance(value, str) and value in allowed


def parse_updated(value):
    """Returns an aware datetime, or None when the text is not ISO 8601 with a time zone."""
    m = ISO.match(value) if isinstance(value, str) else None
    if not m:
        return None
    tz = m.group(5)
    tz = '+00:00' if tz == 'Z' else tz
    clock = m.group(2) + (m.group(3) or ':00')
    try:
        return datetime.fromisoformat('{}T{}{}'.format(m.group(1), clock, tz))
    except ValueError:
        return None


def check_fields(r, where, obj, required, allowed_extra=()):
    """Every required field present; unknown fields are warned (usually a typo)."""
    for f in required:
        if f not in obj:
            r.error(where, 'missing field "{}"'.format(f))
    for f in obj:
        if f not in required and f not in allowed_extra:
            r.warn(where, 'unknown field "{}" is ignored by the page (typo?)'.format(f))


def scan_secrets(r, raw):
    for lineno, line in enumerate(raw.splitlines(), 1):
        for label, rx in SECRET_RULES:
            if rx.search(line):
                # Say where, never repeat the text: it may be a real secret.
                r.error('line {}'.format(lineno), 'contains {}. Remove it before saving.'.format(label))


def bound_ids(index_path):
    """From the built index.html: the element ids the diagrams are bound to, and the
    text limit of each box's 'short' line. Returns (None, None) when index.html is absent."""
    if not index_path or not os.path.isfile(index_path):
        return None, None
    with open(index_path, encoding='utf-8') as f:
        html = f.read()
    ids = set(re.findall(r'data-el="([^"]+)"', html))
    for needs in re.findall(r'data-needs="([^"]*)"', html):
        ids.update(needs.split())
    limits = {}
    for m in re.finditer(r'data-el="([^"]+)"', html):
        group = html[m.end():html.find('</g>', m.end())]
        mx = re.search(r'data-short="1"[^>]*data-max="(\d+)"|data-max="(\d+)"[^>]*data-short="1"', group)
        if mx:
            n = int(mx.group(1) or mx.group(2))
            limits[m.group(1)] = min(n, limits.get(m.group(1), n))
    return ids, limits


def validate(data, raw, index_path=None, now=None):
    r = Report()
    now = now or datetime.now(timezone.utc)
    scan_secrets(r, raw)
    if not isinstance(data, dict):
        r.error('top level', 'status.json must be a JSON object')
        return r
    check_fields(r, 'top level', data, TOP)

    # header
    upd = parse_updated(data.get('updated'))
    if 'updated' in data:
        if upd is None:
            r.error('updated', 'must be ISO 8601 with a time zone, for example 2026-11-13T09:30:00+07:00')
        elif upd > now + timedelta(minutes=5):
            r.error('updated', 'is in the future')
        elif now - upd > timedelta(hours=STALE_HOURS):
            r.warn('updated', 'is more than {} hours old; the page will show a stale-data warning'.format(STALE_HOURS))
    for f in ('updated_by', 'architecture_version'):
        if f in data and not is_text(data[f]):
            r.error(f, 'must be a non-empty text')

    # elements
    els = data.get('elements')
    if isinstance(els, dict):
        for k, e in els.items():
            w = 'elements.{}'.format(k)
            if not isinstance(e, dict):
                r.error(w, 'must be an object')
                continue
            check_fields(r, w, e, EL_FIELDS)
            if 'status' in e and not one_of(e['status'], STATUS):
                r.error(w + '.status', '"{}" is not one of {}. The page would show "Not verified".'.format(e['status'], ', '.join(STATUS)))
            elif e.get('status') == 'unknown':
                r.warn(w + '.status', 'is "unknown" (shown as Not verified)')
            if 'group' in e and e['group'] not in GROUPS:
                r.warn(w + '.group', '"{}" is not one of the standard groups; it appears under its own heading'.format(e['group']))
            for f in ('name', 'owner'):
                if f in e and not is_text(e[f]):
                    r.warn('{}.{}'.format(w, f), 'is empty')
    elif 'elements' in data:
        r.error('elements', 'must be an object keyed by element id')
        els = {}
    else:
        els = {}

    ids, limits = bound_ids(index_path)
    if ids is None:
        r.warn('index.html', 'not found, so the diagram-id check was skipped')
    else:
        for i in sorted(ids):
            if i not in els:
                r.error('elements.{}'.format(i), 'is used by a diagram but is missing here. The page would show it as Not verified.')

    for k, n in sorted((limits or {}).items()):
        e = els.get(k)
        if isinstance(e, dict) and isinstance(e.get('short'), str) and len(e['short']) > n:
            r.warn('elements.{}.short'.format(k), '{} characters; its diagram box cuts the text at {}'.format(len(e['short']), n))

    # intents
    intents = data.get('intents')
    status_of = lambda i: (els.get(i) or {}).get('status') if isinstance(els.get(i), dict) else None
    if isinstance(intents, list):
        seen = set()
        for idx, it in enumerate(intents):
            w = 'intents[{}]'.format(idx)
            if not isinstance(it, dict):
                r.error(w, 'must be an object')
                continue
            check_fields(r, w, it, INTENT_FIELDS)
            n = it.get('n')
            if n in seen:
                r.error(w + '.n', 'duplicate intent number {}'.format(n))
            seen.add(n)
            if 'status' in it and not one_of(it['status'], STATUS):
                r.error(w + '.status', '"{}" is not one of {}'.format(it['status'], ', '.join(STATUS)))
                continue
            parts = INTENT_PARTS.get(n)
            if parts and all(p in els for p in parts) and 'status' in it:
                sts = [status_of(p) for p in parts]
                bad = [p for p, s in zip(parts, sts) if s not in WORKING]
                label = 'Intent {} ({})'.format(n, it['status'])
                if it['status'] in WORKING and bad:
                    r.warn(w + '.status', '{} says it works, but these parts are not live or partial: {}'.format(label, ', '.join(bad)))
                elif it['status'] not in WORKING and not bad:
                    r.warn(w + '.status', '{}, but all its parts are live or partial: {}'.format(label, ', '.join(parts)))
                elif it['status'] == 'partial' and all(s == 'live' for s in sts):
                    r.warn(w + '.status', '{}, but all its parts are fully live'.format(label))
    elif 'intents' in data:
        r.error('intents', 'must be a list')

    # delivery
    deliv = data.get('delivery')
    task_ids = set()
    if isinstance(deliv, list):
        seen = set()
        for idx, p in enumerate(deliv):
            w = 'delivery[{}]'.format(idx)
            if not isinstance(p, dict):
                r.error(w, 'must be an object')
                continue
            check_fields(r, w, p, PRIO_FIELDS)
            if p.get('p') in seen:
                r.error(w + '.p', 'duplicate priority number {}'.format(p.get('p')))
            seen.add(p.get('p'))
            if 'state' in p and not one_of(p['state'], PRIORITY):
                r.error(w + '.state', '"{}" is not one of {}'.format(p['state'], ', '.join(PRIORITY)))
            tasks = p.get('tasks')
            if not isinstance(tasks, list):
                if 'tasks' in p:
                    r.error(w + '.tasks', 'must be a list')
                continue
            states = []
            for ti, t in enumerate(tasks):
                tw = '{}.tasks[{}]'.format(w, ti)
                if not isinstance(t, dict):
                    r.error(tw, 'must be an object')
                    continue
                check_fields(r, tw, t, TASK_FIELDS, TASK_OPTIONAL)
                states.append(t.get('state'))
                if 'state' in t and not one_of(t['state'], TASK):
                    r.error(tw + '.state', '"{}" is not one of {}'.format(t['state'], ', '.join(TASK)))
                tid = t.get('id')
                if is_text(tid):
                    if tid in LANES:
                        pass                      # planned work without a number yet; may repeat
                    elif re.match(r'^TSK-\d+$', tid):
                        if tid in task_ids:
                            r.error(tw + '.id', 'duplicate task id "{}"'.format(tid))
                        task_ids.add(tid)
                    else:
                        r.warn(tw + '.id', '"{}" is neither TSK-nnn nor one of {}'.format(tid, ', '.join(LANES)))
                elif 'id' in t:
                    r.error(tw + '.id', 'must be a non-empty text')
                if 'owner' in t and not is_text(t['owner']):
                    r.warn(tw + '.owner', 'is empty')
                if 'stage' in t and not one_of(t['stage'], STAGES):
                    r.error(tw + '.stage', '"{}" is not one of {}'.format(t['stage'], ', '.join(STAGES)))
                for f, limit in TASK_LIMITS.items():
                    if f in t:
                        if not isinstance(t[f], str):
                            r.error(tw + '.' + f, 'must be text')
                        elif len(t[f]) > limit:
                            r.error(tw + '.' + f, 'is {} characters; the limit is {}'.format(len(t[f]), limit))
            st = p.get('state')
            if st == 'done' and any(s != 'done' for s in states):
                r.warn(w + '.state', 'priority is done, but not every task is done')
            if st == 'not_started' and any(s != 'pending' for s in states):
                r.warn(w + '.state', 'priority is not_started, but a task has moved on')
            if st == 'in_progress' and states and all(s == 'done' for s in states):
                r.warn(w + '.state', 'every listed task is done; is the priority finished?')
    elif 'delivery' in data:
        r.error('delivery', 'must be a list')

    # attention
    att = data.get('attention')
    if isinstance(att, list):
        for idx, a in enumerate(att):
            if not is_text(a):
                r.error('attention[{}]'.format(idx), 'must be a non-empty sentence')
    elif 'attention' in data:
        r.error('attention', 'must be a list of sentences')
    return r


def main(argv):
    args = [a for a in argv if not a.startswith('--')]
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    path = args[0] if args else os.path.join(root, 'status.json')
    index = os.path.join(os.path.dirname(os.path.abspath(path)), 'index.html')
    if '--index' in argv:
        i = argv.index('--index')
        if i + 1 >= len(argv):
            print('--index needs a path')
            return 2
        index = argv[i + 1]
        args = [a for a in args if a != index]
        path = args[0] if args else path
    try:
        with open(path, encoding='utf-8') as f:
            raw = f.read()
    except (OSError, UnicodeDecodeError) as e:
        print('Cannot read {}: {}'.format(path, e))
        return 2
    try:
        data = json.loads(raw)
    except ValueError as e:
        print('ERROR   {} is not valid JSON: {}'.format(os.path.basename(path), e))
        print('Result: 1 error. Do not save or publish this file.')
        return 1
    r = validate(data, raw, index)
    for where, msg in r.errors:
        print('ERROR   {}: {}'.format(where, msg))
    for where, msg in r.warnings:
        print('WARN    {}: {}'.format(where, msg))
    n_el = len(data.get('elements', {})) if isinstance(data, dict) and isinstance(data.get('elements'), dict) else 0
    print('Checked {}: {} elements. Result: {} error(s), {} warning(s).'.format(
        os.path.basename(path), n_el, len(r.errors), len(r.warnings)))
    if r.errors:
        print('Do not save or publish this file until the errors are fixed.')
    if '--strict' in argv:
        return 1 if (r.errors or r.warnings) else 0
    return 1 if r.errors else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
