"""Renders the Command Center page (six tabs) from a kept feed.

The page is rendered server-side from the feed the service keeps
(PRD section 14 shape, v1). The Architecture and Delivery tabs reuse the
Status page's template, colour tokens, status words, SVG diagrams and
"What changed" rules (decision D8); the diagrams come from ``figures.py``,
a port of the Status page's ``build.py``.

Nothing here reads OneDrive or any other source: the only input is the feed
dict (plus ``agents.json`` for the card list and the last-good status copy
the service keeps when a feed arrives without one).

Works on Python 3.8 or newer. Standard library only.
"""
import html
import json
from datetime import datetime, timedelta, timezone

try:  # as a package (tests, `python -m ...`)
    from . import figures
    from .feed_states import (agent_card_state, feed_is_stale, in_hours,
                              minutes_between, parse_time)
except ImportError:  # as top-level modules (board_service puts src/ on the path)
    import figures
    from feed_states import (agent_card_state, feed_is_stale, in_hours,
                             minutes_between, parse_time)

E = html.escape
WIB = timezone(timedelta(hours=7))

STATUS = {'live': 'Live', 'partial': 'Partial', 'built': 'Built, not live',
          'design': 'Planned', 'parked': 'Parked', 'unknown': 'Not verified'}
TASK_STATE = {'pending': 'Pending', 'in_progress': 'In progress',
              'blocked': 'Waiting', 'done': 'Done',
              'not_started': 'Not started'}
STAGES = ['design', 'QC', 'build', 'import_prep', 'import', 'test',
          'active', 'done']
STAGE_LABEL = {'design': 'Design', 'QC': 'QC', 'build': 'Build',
               'import_prep': 'Import prep', 'import': 'Import',
               'test': 'Test', 'active': 'Active', 'done': 'Done'}

# Map-kind icons: small inline SVG marks, no external images. The key under
# the map explains them (PRD section 8.1).
# v0.1.5: card accent follows the platform theme, not the kind.
# Claude terracotta, Muse blue, Grok graphite-teal (#115e59, not
# black). Accent = card tint + 4px edge + header strip + icon circle
# + badge. State colours stay separate; every state keeps its word
# plus colour. Badge text is 4.5:1 or better on its tint, light and
# dark mode (see test_platform_badge_contrast).
MAP_KINDS = ('person', 'session', 'scheduled', 'manual')
# Which AI runs each agent, shown as a letter badge top-right of the
# map card and as the card's platform theme. The feed does not say
# which platform an agent runs on, so this fixed map is the source;
# tell Archie the map when it changes. Deniel has no platform, so
# his card stays neutral with no badge.
BRAND_BY_ID = {
    'archie': ('claude', 'C'),
    'operations': ('claude', 'C'),
    'validator': ('claude', 'C'),
    'support': ('claude', 'C'),
    'grok': ('grok', 'G'),
    'muse-ping': ('muse', 'M'),
    'muse-kb': ('muse', 'M'),
    'muse-feed': ('muse', 'M'),
    'muse-page': ('muse', 'M'),
}
BRAND_NAME = {'claude': 'Claude', 'muse': 'Muse', 'grok': 'Grok'}
KIND_ICON = {
    'person': ('<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">'
               '<circle cx="12" cy="8" r="4" fill="none" stroke="currentColor" stroke-width="2"/>'
               '<path d="M4 20c1-4 4.5-6 8-6s7 2 8 6" fill="none" stroke="currentColor" stroke-width="2"/></svg>'),
    'session': ('<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">'
                '<rect x="3" y="4" width="18" height="16" rx="2" fill="none" stroke="currentColor" stroke-width="2"/>'
                '<path d="M7 9l3 3-3 3M12 15h5" fill="none" stroke="currentColor" stroke-width="2"/></svg>'),
    'scheduled': ('<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">'
                  '<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/>'
                  '<path d="M12 7v5l3 2" fill="none" stroke="currentColor" stroke-width="2"/></svg>'),
    'manual': ('<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">'
               '<path d="M4 5h16v11H9l-5 4z" fill="none" stroke="currentColor" stroke-width="2"/></svg>'),
}
KIND_WORD = {'person': 'person', 'session': 'Claude Code session',
             'scheduled': 'scheduled chat', 'manual': 'manual chat'}
CARD_STATE_CLASS = {'working': 'dot-live', 'waiting': 'dot-partial',
                    'idle': 'dot-idle', 'needs a ping': 'dot-ping',
                    'stale': 'dot-stale', 'off hours': 'dot-off',
                    'not created': 'dot-none'}


def fmt_wib(dt, seconds=False):
    """Format an aware datetime as WIB, e.g. '19 Nov 2026, 09:58 WIB'."""
    if dt is None:
        return 'unknown'
    local = dt.astimezone(WIB)
    out = local.strftime('%d %b %Y, %H:%M')
    if seconds:
        out = local.strftime('%d %b %Y, %H:%M:%S')
    return out + ' WIB'


def fmt_clock(dt):
    """HH:MM:SS WIB for a datetime, or 'unknown'."""
    if dt is None:
        return 'unknown'
    return dt.astimezone(WIB).strftime('%H:%M:%S') + ' WIB'


def fmt_hm(dt):
    """HH:MM WIB for a datetime, or 'unknown'."""
    if dt is None:
        return 'unknown'
    return dt.astimezone(WIB).strftime('%H:%M') + ' WIB'


def fmt_age(minutes):
    """'12 min', '2 h', 'just now' for a minute count."""
    if minutes is None:
        return ''
    if minutes < 1:
        return 'just now'
    if minutes < 60:
        return '%d min' % minutes
    if minutes < 48 * 60:
        return '%d h' % round(minutes / 60)
    return '%d days' % round(minutes / 1440)


def parts_score(status_json):
    """Parts-working score, the Status page's definition.

    Returns (percent_text, live, partial, total, detail_text): live counts 1,
    partial counts 0.5, everything else 0, all parts count, two decimals.
    """
    els = (status_json or {}).get('elements') or {}
    total = len(els)
    live = sum(1 for e in els.values()
               if isinstance(e, dict) and e.get('status') == 'live')
    partial = sum(1 for e in els.values()
                  if isinstance(e, dict) and e.get('status') == 'partial')
    if not total:
        return ('\u2013', live, partial, total, 'No parts in the data')
    pct = (live + partial / 2) / total * 100
    detail = '%d live + %d partial \u00d7 \u00bd, of %d parts' % (live, partial, total)
    return ('%.2f%%' % pct, live, partial, total, detail)


def norm_status(status):
    s = str(status or '').lower().strip()
    return s if s in STATUS else 'unknown'


def status_pill(status):
    s = norm_status(status)
    return '<span class="pill s-%s">%s</span>' % (s, STATUS[s])


def task_pill(state):
    s = str(state or '').lower().strip()
    s = s if s in TASK_STATE else 'pending'
    return '<span class="pill t-%s">%s</span>' % (s, TASK_STATE[s])


def task_lookup(status_json):
    """Delivery tasks by id. When an id repeats, prefer the row that carries
    the D4 fields (stage / summary / waiting_on)."""
    out = {}
    for prio in (status_json or {}).get('delivery') or []:
        for task in (prio or {}).get('tasks') or []:
            if not isinstance(task, dict):
                continue
            tid = task.get('id')
            if not tid:
                continue
            old = out.get(tid)
            if old is None:
                out[tid] = task
            elif any(task.get(f) for f in ('stage', 'summary', 'waiting_on')) \
                    and not any(old.get(f) for f in ('stage', 'summary', 'waiting_on')):
                out[tid] = task
    return out



def doing_label(agent_id, feed, tasks_by_id):
    """The map label for one agent: 'TSK-012 short title', or None when
    nothing is known. The text comes from the feed's doing_task or from
    status.json (task id + title), never from message text.

    v0.1.7: the task is placed by whoever holds the ball, in this order:
    the feed's doing_task, an open thread with ball_with == agent, a
    status.json task with ball_with == agent. The old owner fallback is
    gone: owning an in-progress task is not holding the ball. A task
    whose record is closed (state or stage 'done') never shows, at any
    step. ball_with is optional on the task record and absent until Ops
    fills it; a task without it simply matches nothing here.
    """
    agents = {a.get('id'): a for a in feed.get('agents') or []
              if isinstance(a, dict)}
    info = agents.get(agent_id) or {}
    candidates = []
    doing = info.get('doing_task')
    if doing:
        candidates.append(doing)
    for thread in feed.get('threads') or []:
        if (thread.get('ball_with') == agent_id and thread.get('task')
                and _thread_state_word(thread) not in ('CLOSED', 'ANSWERED')):
            candidates.append(thread.get('task'))
    for task in tasks_by_id.values():
        if (task.get('ball_with') == agent_id
                and str(task.get('id') or '').startswith('TSK-')):
            candidates.append(task.get('id'))
    seen = set()
    for task_id in candidates:
        if task_id in seen:
            continue
        seen.add(task_id)
        task = tasks_by_id.get(task_id) or {}
        if task.get('state') == 'done' or task.get('stage') == 'done':
            continue
        title = (task.get('title') or '').strip()
        if title:
            short = title if len(title) <= 40 else title[:39].rstrip(' ,;:') + '…'
            return '%s %s' % (task_id, short)
        return task_id
    return None


def need_deniel_items(feed, status_json):
    """Items for the red 'N items need Deniel' strip: (kind, text, task).

    Sources: inbox/human entries, decision/halt actions in threads, and the
    status.json attention list. Slug and action only, never message text.
    """
    items = []
    for entry in feed.get('human_inbox') or []:
        if not isinstance(entry, dict):
            continue
        items.append(('human inbox',
                      '%s: %s' % (entry.get('action') or 'message',
                                  entry.get('slug') or '(no slug)'),
                      entry.get('task') or ''))
    for thread in feed.get('threads') or []:
        if not isinstance(thread, dict):
            continue
        for msg in thread.get('messages') or []:
            if str(msg.get('action') or '') in ('decision', 'halt'):
                items.append((msg.get('action'),
                              '%s: %s' % (msg.get('action'),
                                          thread.get('slug') or thread.get('thread_id') or ''),
                              thread.get('task') or ''))
                break
    for line in (status_json or {}).get('attention') or []:
        text = str(line or '')
        if text:
            short = text if len(text) <= 60 else text[:59].rstrip(' ,;:') + '…'
            items.append(('attention', short, ''))
    return items

CSS = """
/* Colour tokens and base rules are the Status page's, unchanged (decision D8). */
:root {
  --bg: #f2f4f6; --surface: #ffffff; --soft: #e8ecf0; --ink: #15202a; --muted: #55636f; --line: #cfd7de;
  --accent: #0d5f7d; --arrow: #55636f;
  --live: #166f43; --partial: #8f5400; --built: #2c50ad; --design: #6f42a3; --parked: #66717c; --unknown: #a3361f;
  --claude: #96421f; --muse: #2c50ad; --grok: #115e59;
  --danger: #a3361f; --amber-bg: #fdf3e0;
  --font: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --mono: ui-monospace, "Cascadia Mono", Consolas, "Liberation Mono", monospace;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0e141a; --surface: #161f28; --soft: #1e2933; --ink: #e3e9ef; --muted: #9ba8b5; --line: #2c3945;
    --accent: #63c2e1; --arrow: #9ba8b5;
    --live: #55c992; --partial: #e3a846; --built: #93abff; --design: #c3a6ee; --parked: #909ca9; --unknown: #f29380;
    --claude: #e8a583; --muse: #93abff; --grok: #5eead4;
    --danger: #f29380; --amber-bg: #3a2f16;
    color-scheme: dark;
  }
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.5 var(--font); }
.wrap { max-width: 1280px; margin: 0 auto; padding-block: 28px 56px; padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); display: flex; flex-direction: column; gap: 36px; }
h1 { font-size: 26px; line-height: 1.2; margin: 0; letter-spacing: -0.01em; text-wrap: balance; }
h2 { font-size: 18px; line-height: 1.3; margin: 0; text-wrap: balance; }
h3 { font-size: 12px; margin: 0; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); font-weight: 650; }
p { margin: 0; }
.lede { color: var(--muted); max-width: 68ch; }
section { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
.sec-head { display: flex; flex-direction: column; gap: 4px; }
header { display: flex; flex-direction: column; gap: 12px; }
.hd-top { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: flex-start; gap: 16px 28px; }
.hd-main { flex: 1 1 380px; min-width: 0; display: flex; flex-direction: column; gap: 12px; }
.score { flex: none; margin-left: auto; text-align: right; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 10px 16px; }
.score-n { font-size: 34px; line-height: 1.1; font-weight: 700; font-variant-numeric: tabular-nums; letter-spacing: -0.01em; }
.score-l { font-size: 13.5px; font-weight: 600; }
.score-s { color: var(--muted); font-size: 12.5px; max-width: 30ch; margin-left: auto; }
.meta { display: flex; flex-wrap: wrap; gap: 6px 18px; align-items: center; color: var(--muted); font-size: 13.5px; }
.meta b { color: var(--ink); font-weight: 600; }
button { font: inherit; font-size: 13.5px; font-weight: 600; color: var(--accent); background: var(--surface); border: 1px solid var(--line); border-radius: 6px; padding: 12px 16px; min-height: 44px; cursor: pointer; }
a.btn { display: inline-flex; align-items: center; min-height: 44px; font-size: 13.5px; font-weight: 600; color: var(--accent); background: var(--surface); border: 1px solid var(--line); border-radius: 6px; padding: 12px 16px; cursor: pointer; text-decoration: none; }
a.btn:hover { border-color: var(--accent); }
button:hover { border-color: var(--accent); }
button:focus-visible, a:focus-visible, tr:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.banner { border: 1px solid var(--partial); border-radius: 6px; padding: 10px 14px; background: var(--surface); color: var(--ink); font-size: 14px; }
.banner strong { color: var(--partial); }
.banner.red { border-color: var(--danger); }
.banner.red strong { color: var(--danger); }
.need-strip { border: 1px solid var(--danger); border-radius: 6px; padding: 10px 14px; background: var(--surface); color: var(--ink); font-size: 14px; display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center; }
.need-strip strong { color: var(--danger); }
.need-strip ul { margin: 0; padding: 0; list-style: none; display: flex; flex-wrap: wrap; gap: 4px 16px; }
.need-strip li { font-size: 13.5px; }
.pill { display: inline-flex; align-items: center; gap: 7px; font-size: 12px; font-weight: 650; letter-spacing: 0.02em; white-space: nowrap; color: var(--c); }
.pill::before { content: ""; width: 9px; height: 9px; border-radius: 50%; background: var(--c); flex: none; }
.s-live { --c: var(--live); } .s-partial { --c: var(--partial); } .s-built { --c: var(--built); }
.s-design { --c: var(--design); } .s-parked { --c: var(--parked); } .s-unknown { --c: var(--unknown); }
.s-design.pill::before, .s-parked.pill::before { background: transparent; border: 2px solid var(--c); }
.t-done { --c: var(--live); } .t-in_progress { --c: var(--partial); } .t-blocked { --c: var(--built); } .t-pending, .t-not_started { --c: var(--parked); }
.t-pending.pill::before, .t-not_started.pill::before { background: transparent; border: 2px solid var(--c); }
.tally { display: flex; flex-wrap: wrap; gap: 8px; }
.tally div { display: flex; align-items: baseline; gap: 10px; background: var(--surface); border: 1px solid var(--line); border-radius: 6px; padding: 8px 12px; }
.tally .n { font-size: 18px; font-weight: 700; font-variant-numeric: tabular-nums; }
.tally small { color: var(--muted); font-size: 12.5px; }
.rows { background: var(--surface); border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
.row { display: grid; grid-template-columns: 150px minmax(0, 1fr) minmax(0, 220px); gap: 4px 18px; padding: 11px 16px; border-top: 1px solid var(--line); align-items: start; }
.row:first-child { border-top: 0; }
.name { font-weight: 600; }
.note { color: var(--muted); font-size: 13.5px; }
.row .owner { color: var(--muted); font-size: 13px; }
.row .owner::before { content: "Owner: "; }
.row .owner:empty::before { content: ""; }
.intent { grid-template-columns: 34px minmax(0, 1fr) 150px; }
.intent .num { font: 600 13px var(--mono); color: var(--muted); padding-top: 2px; }
.task { grid-template-columns: 86px minmax(0, 1fr) 110px 120px; padding: 8px 16px; font-size: 14px; }
.task .id { font: 12.5px var(--mono); color: var(--muted); padding-top: 2px; }
.task .who { color: var(--muted); font-size: 13px; }
.groups { display: flex; flex-direction: column; gap: 22px; }
.group { display: flex; flex-direction: column; gap: 8px; }
.prio { display: flex; flex-direction: column; gap: 12px; }
.prio > li { list-style: none; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
.prio { padding: 0; margin: 0; }
.prio .head { display: grid; grid-template-columns: 34px minmax(0, 1fr) 130px; gap: 4px 18px; padding: 12px 16px; align-items: start; }
.prio .p { font: 600 13px var(--mono); color: var(--muted); padding-top: 2px; }
.prio .tasks .row:first-child { border-top: 1px solid var(--line); }
[hidden] { display: none !important; }
.chg { display: inline-block; margin-left: 8px; padding: 0 6px; border: 1px solid var(--accent); border-radius: 4px; color: var(--accent); font-size: 12px; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase; vertical-align: 1px; white-space: nowrap; }
.chg.down { border-color: var(--unknown); color: var(--unknown); }
.change { grid-template-columns: 90px minmax(0, 1fr) minmax(0, 330px); }
.change .kind { color: var(--muted); font-size: 13px; padding-top: 1px; }
.change .to { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 10px; }
.change .arrow { color: var(--muted); }
.change .tag { color: var(--muted); font-size: 12.5px; }
.chg-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 14px; }
figure { margin: 0; display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.scroll { overflow-x: auto; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 10px; }
.scroll svg { display: block; width: 100%; min-width: 1040px; height: auto; }
figcaption { color: var(--muted); font-size: 13.5px; max-width: 80ch; }
svg text { font-family: var(--font); fill: var(--ink); }
svg .col { font-size: 10.5px; font-weight: 650; letter-spacing: 0.08em; fill: var(--muted); }
svg .node > rect { fill: var(--bg); stroke: var(--line); stroke-width: 1.3; }
svg .node > .t { font-size: 12.5px; font-weight: 650; }
svg .node > .m { font-size: 11.5px; }
svg .node > .sub { font-size: 10.5px; fill: var(--muted); }
svg .node > .st { font-size: 9.5px; font-weight: 700; letter-spacing: 0.04em; fill: var(--c, var(--muted)); }
svg .node[data-el] > rect { stroke: var(--c); stroke-width: 1.6; fill: var(--surface); }
svg .s-design > rect, svg .s-parked > rect, svg .node > rect.dash { stroke-dasharray: 5 4; }
svg .s-parked > .t, svg .s-parked > .m { fill: var(--muted); }
svg .ar { stroke: var(--arrow); stroke-width: 1.4; fill: none; }
svg .ar.dashed { stroke-dasharray: 5 4; opacity: 0.7; }
svg .ar.faint { opacity: 0.35; }
svg .ahf { fill: var(--arrow); }
svg .al { font-size: 10px; fill: var(--muted); }
.key { display: flex; flex-wrap: wrap; gap: 6px 18px; color: var(--muted); font-size: 13px; }
.key span { display: inline-flex; align-items: center; gap: 8px; }
.key i { display: inline-block; width: 28px; border-top: 2px solid var(--arrow); }
.key i.d { border-top-style: dashed; }
ul.attn { margin: 0; padding: 4px 16px; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; list-style: none; }
ul.attn li { padding: 10px 0; border-top: 1px solid var(--line); }
ul.attn li:first-child { border-top: 0; }
footer { color: var(--muted); font-size: 13.5px; display: flex; flex-direction: column; gap: 6px; max-width: 80ch; }
code { font: 12.5px var(--mono); background: var(--soft); padding: 1px 5px; border-radius: 4px; color: var(--ink); }
/* tabs */
nav.tabs { display: flex; flex-wrap: wrap; gap: 4px; border-bottom: 2px solid var(--line); }
nav.tabs a { display: inline-flex; align-items: center; min-height: 44px; padding: 8px 14px; font-weight: 650; font-size: 14px; color: var(--muted); text-decoration: none; border-bottom: 2px solid transparent; margin-bottom: -2px; }
nav.tabs a[aria-selected="true"] { color: var(--ink); border-bottom-color: var(--accent); }
nav.tabs a:hover { color: var(--ink); }
/* live map */
.map-scroll { overflow-x: auto; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 14px; }
.map-inner { min-width: 900px; display: flex; flex-direction: column; gap: 10px; }
.map-row { display: flex; gap: 10px; align-items: stretch; }
.map-card { flex: 1 1 0; min-width: 0; border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; display: flex; flex-direction: column; gap: 6px; background: var(--surface); }
.map-card.not-created { border-style: dashed; opacity: 0.75; }
/* v0.1.5: platform theme accent (see the comment above BRAND_BY_ID). */
.map-card.p-claude { background: rgba(150,66,31,0.12); border-left: 4px solid var(--claude); }
.map-card.p-muse { background: rgba(44,80,173,0.12); border-left: 4px solid var(--muse); }
.map-card.p-grok { background: rgba(17,94,89,0.12); border-left: 4px solid var(--grok); }
.map-card .who { display: flex; gap: 8px; align-items: center; }
.map-card.p-claude .who { background: rgba(150,66,31,0.18); margin: -10px -12px 0; padding: 10px 12px; border-radius: 8px 8px 0 0; }
.map-card.p-muse .who { background: rgba(44,80,173,0.18); margin: -10px -12px 0; padding: 10px 12px; border-radius: 8px 8px 0 0; }
.map-card.p-grok .who { background: rgba(17,94,89,0.18); margin: -10px -12px 0; padding: 10px 12px; border-radius: 8px 8px 0 0; }
.map-card .who svg { flex: none; }
.map-card .kicon { flex: none; width: 40px; height: 40px; border-radius: 50%; display: flex; align-items: center; justify-content: center; background: var(--soft); color: var(--muted); }
.map-card.p-claude .kicon { background: rgba(150,66,31,0.20); color: var(--claude); }
.map-card.p-muse .kicon { background: rgba(44,80,173,0.20); color: var(--muse); }
.map-card.p-grok .kicon { background: rgba(17,94,89,0.20); color: var(--grok); }
.map-card .nm { font-weight: 700; font-size: 14px; flex: 1 1 auto; }
/* Brand badge, top-right of the card: which AI runs this agent. */
.brand { flex: none; width: 28px; height: 28px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; font-size: 13px; font-weight: 700; }
.brand-claude { background: rgba(150,66,31,0.18); color: var(--claude); }
.brand-muse { background: rgba(44,80,173,0.16); color: var(--muse); }
.brand-grok { background: rgba(17,94,89,0.16); color: var(--grok); }
.map-card .where { color: var(--muted); font-size: 12.5px; }
.map-state { display: flex; align-items: center; gap: 7px; font-size: 13px; font-weight: 600; }
.dot { width: 10px; height: 10px; border-radius: 50%; flex: none; }
.dot-live { background: var(--live); } .dot-partial { background: var(--partial); }
.dot-idle { background: transparent; border: 2px solid var(--muted); }
.dot-ping { background: var(--partial); } .dot-stale { background: var(--unknown); }
.dot-off { background: transparent; border: 2px solid var(--muted); }
.dot-none { background: transparent; border: 2px dashed var(--muted); }
.map-state.stale-word { color: var(--unknown); }
.chip { display: inline-block; font-size: 12.5px; font-weight: 650; background: var(--soft); color: var(--muted); border-radius: 20px; padding: 2px 9px; }
.chip.amber { background: var(--amber-bg); color: var(--partial); }
.map-label { display: flex; align-items: center; justify-content: center; min-height: 44px; border: 1px solid var(--line); border-radius: 20px; padding: 5px 11px; font-size: 12.5px; font-weight: 600; text-align: center; color: var(--ink); background: var(--soft); text-decoration: none; line-height: 1.35; overflow-wrap: anywhere; }
a.map-label:hover { border-color: var(--accent); }
.map-label.unknown { border-style: dashed; color: var(--muted); background: transparent; font-weight: 500; }
.map-bus { background: var(--accent); color: var(--surface); text-align: center; font-weight: 700; letter-spacing: 0.06em; border-radius: 8px; padding: 9px; font-size: 14px; }
.map-delegate { display: flex; align-items: center; gap: 8px; justify-content: center; color: var(--muted); font-size: 12.5px; }
.map-delegate .arr { font-weight: 700; color: var(--accent); }
/* tables */
table.board { width: 100%; border-collapse: collapse; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; overflow: hidden; font-size: 14px; }
table.board th, table.board td { text-align: left; padding: 12px 12px; border-top: 1px solid var(--line); vertical-align: top; }
table.board thead th { border-top: 0; color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em; }
table.board tr[data-task] { cursor: pointer; }
table.board tr[data-task]:hover { background: var(--soft); }
table.board tr.sel { background: var(--soft); }
table.board td.id, table.board th.id { font: 12.5px var(--mono); }
.table-scroll { overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }
.table-scroll table.board { border: 0; }
/* task detail */
.detail { background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px; display: flex; flex-direction: column; gap: 10px; }
.detail dl { margin: 0; display: grid; grid-template-columns: 130px minmax(0, 1fr); gap: 6px 14px; font-size: 14px; }
.detail dt { color: var(--muted); font-weight: 600; }
.detail dd { margin: 0; }
.stages { display: flex; flex-wrap: wrap; gap: 4px; }
.stages span { border: 1px solid var(--line); border-radius: 4px; padding: 2px 8px; font-size: 12px; color: var(--muted); }
.stages span.on { background: var(--accent); border-color: var(--accent); color: var(--surface); font-weight: 700; }
.timeline { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; font-size: 13.5px; }
.timeline li { display: flex; flex-wrap: wrap; gap: 4px 12px; }
.timeline .tm { font: 12.5px var(--mono); color: var(--muted); }
.tasks-layout { display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr); gap: 16px; align-items: start; }
.unreadable { border: 1px solid var(--partial); border-radius: 8px; padding: 10px 14px; background: var(--surface); font-size: 14px; }
.unreadable ul { margin: 6px 0 0; padding-left: 18px; }
.unreadable code { overflow-wrap: anywhere; }
@media (max-width: 900px) {
  .tasks-layout { grid-template-columns: minmax(0, 1fr); }
  .map-inner { min-width: 0; }
  .map-row { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); }
  .map-delegate { grid-column: 1 / -1; }
}
@media (max-width: 720px) {
  .row, .intent, .task, .change, .prio .head { grid-template-columns: minmax(0, 1fr); gap: 3px; }
  h1 { font-size: 22px; }
  .score { margin-left: 0; text-align: left; }
  .score-s { margin-left: 0; }
  .detail dl { grid-template-columns: minmax(0, 1fr); gap: 2px; }
  .map-row { grid-template-columns: 1fr; }
}
@media (prefers-reduced-motion: no-preference) { button { transition: border-color 0.15s; } }
"""

# --- "What changed" JavaScript -------------------------------------------------
# Copied from the Status page template (handover), with three adaptations,
# all marked ADAPTED below:
#  1. The Status page re-renders everything from live data after each fetch.
#     The board renders server-side, so applyLive() only diffs and tags.
#  2. addTag() tagged a node passed in by the renderer. Here tagRows() finds
#     the server-rendered rows by their data-chg-map / data-chg-key
#     attributes and tags their .chg-name element.
#  3. renderChanges() guards against a missing panel: the board's
#     Architecture tab has no panel when the feed carries no status data.
# stateOf() and diffStates() are byte-identical to the Status page's.
WHAT_CHANGED_JS = """
  var STATUS = { live: 'Live', partial: 'Partial', built: 'Built, not live', design: 'Planned', parked: 'Parked', unknown: 'Not verified' };
  var TASK = { pending: 'Pending', in_progress: 'In progress', blocked: 'Waiting', done: 'Done', not_started: 'Not started' };
  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function pill(status) { var s = String(status == null ? '' : status).toLowerCase().trim(); s = STATUS.hasOwnProperty(s) ? s : 'unknown'; return el('span', 'pill s-' + s, STATUS[s]); }
  function tpill(state) { var s = String(state == null ? '' : state).toLowerCase().trim(); s = TASK.hasOwnProperty(s) ? s : 'pending'; return el('span', 'pill t-' + s, TASK[s]); }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function fmtWIB(d) {
    try { return new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Jakarta', day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }).format(d) + ' WIB'; }
    catch (e) { return d.toISOString(); }
  }
  var SEEN_KEY = 'statusmap.seen.v1', MAX_CHANGES = 40;
  var WORKING = { live: true, partial: true };
  var KINDS = { els: 'Part', intents: 'Intent', prios: 'Priority', tasks: 'Task' };
  var WEIGHT = { els: 1, intents: 2, prios: 3, tasks: 4 };
  function emptyMaps() { return { els: Object.create(null), intents: Object.create(null), prios: Object.create(null), tasks: Object.create(null) }; }
  var changes = emptyMaps();
  function has(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }
  function taskKey(tk) { return String(tk.id) + '|' + String(tk.title); }
  function norm(s) { s = String(s == null ? '' : s).toLowerCase().trim(); return STATUS.hasOwnProperty(s) ? s : 'unknown'; }
  function tnorm(s) { s = String(s == null ? '' : s).toLowerCase().trim(); return TASK.hasOwnProperty(s) ? s : 'pending'; }
  function str(v) { return v == null ? '' : String(v); }
  function readSeen() {
    var raw;
    try { raw = window.localStorage.getItem(SEEN_KEY); } catch (e) { return { ok: false, value: null }; }
    if (raw == null) return { ok: true, value: null };
    try { var v = JSON.parse(raw); return { ok: true, value: v && v.v === 1 && v.els && typeof v.els === 'object' ? v : null }; }
    catch (e) { return { ok: true, value: null }; }
  }
  function writeSeen(s) { try { window.localStorage.setItem(SEEN_KEY, JSON.stringify(s)); return true; } catch (e) { return false; } }
  function stateOf(data) {
    var s = { v: 1, at: Date.now(), updated: str(data.updated), attn: [] }, m = emptyMaps();
    s.els = m.els; s.intents = m.intents; s.prios = m.prios; s.tasks = m.tasks;
    var els = data.elements || {};
    Object.keys(els).forEach(function (k) { var e = els[k] || {}; s.els[k] = { n: str(e.name) || k, s: norm(e.status) }; });
    (Array.isArray(data.intents) ? data.intents : []).forEach(function (it) { it = it || {}; s.intents[str(it.n)] = { n: str(it.name), s: norm(it.status) }; });
    (Array.isArray(data.delivery) ? data.delivery : []).forEach(function (p) {
      p = p || {};
      s.prios['P' + str(p.p)] = { n: str(p.name), s: tnorm(p.state || 'not_started') };
      (Array.isArray(p.tasks) ? p.tasks : []).forEach(function (tk) { tk = tk || {}; s.tasks[taskKey(tk)] = { n: str(tk.title), s: tnorm(tk.state) }; });
    });
    (Array.isArray(data.attention) ? data.attention : []).forEach(function (x) { s.attn.push(str(x)); });
    return s;
  }
  function diffStates(a, b) {
    var out = [];
    Object.keys(KINDS).forEach(function (map) {
      var A = a[map] || {}, B = b[map] || {};
      Object.keys(B).forEach(function (k) {
        if (!has(A, k)) out.push({ map: map, key: k, type: 'added', to: B[k].s, name: B[k].n });
        else if (A[k].s !== B[k].s) out.push({ map: map, key: k, type: 'changed', from: A[k].s, to: B[k].s, name: B[k].n,
          down: (map === 'els' || map === 'intents') && !!WORKING[A[k].s] && !WORKING[B[k].s] });
      });
      Object.keys(A).forEach(function (k) { if (!has(B, k)) out.push({ map: map, key: k, type: 'removed', from: A[k].s, name: A[k].n }); });
    });
    var ab = Array.isArray(a.attn) ? a.attn : [], bb = Array.isArray(b.attn) ? b.attn : [];
    bb.forEach(function (x) { if (ab.indexOf(x) === -1) out.push({ map: null, key: x, type: 'attn-added', name: x }); });
    ab.forEach(function (x) { if (bb.indexOf(x) === -1) out.push({ map: null, key: x, type: 'attn-removed', name: x }); });
    out.sort(function (x, y) { return ((x.down ? 0 : 1) + (x.map ? WEIGHT[x.map] : 5) / 10) - ((y.down ? 0 : 1) + (y.map ? WEIGHT[y.map] : 5) / 10); });
    return out;
  }
  function markChanged(list) { changes = emptyMaps(); list.forEach(function (c) { if (c.map) changes[c.map][c.key] = c; }); }
  /* ADAPTED: tag the server-rendered rows by their data-chg attributes. */
  function tagRows() {
    Object.keys(changes).forEach(function (map) {
      Object.keys(changes[map]).forEach(function (key) {
        var c = changes[map][key];
        var row = document.querySelector('[data-chg-map="' + map + '"][data-chg-key="' + key.replace(/"/g, '') + '"] .chg-name');
        if (!row) return;
        row.appendChild(document.createTextNode(' '));
        row.appendChild(el('span', 'chg' + (c.down ? ' down' : ''), c.type === 'added' ? 'New' : (c.down ? 'Stopped working' : 'Changed')));
      });
    });
  }
  function renderChanges(mode, list, seen) {
    var sec = $('changes-sec'), box = $('changes'), lede = $('chg-lede'), btn = $('mark-seen');
    /* ADAPTED: no panel exists when the feed carries no status data. */
    if (!sec) return;
    sec.hidden = false; clear(box); box.hidden = true; btn.hidden = true;
    var since = seen && seen.at ? fmtWIB(new Date(seen.at)) : '';
    if (mode === 'offline') lede.textContent = 'Changes cannot be shown because the live data could not be read.';
    else if (mode === 'nostore') lede.textContent = 'This browser is not keeping a record of what you saw, so changes cannot be shown.';
    else if (mode === 'first') lede.textContent = 'This is the first visit in this browser. From now on the page shows what changed since you last marked it as seen.';
    else if (mode === 'none') lede.textContent = 'No changes since you last marked this as seen on ' + since + '.';
    else {
      lede.textContent = list.length + (list.length === 1 ? ' change' : ' changes') + ' since you last marked this as seen on ' + since + '. Press "Mark as seen" when you have read them.';
      btn.hidden = false;
      list.slice(0, MAX_CHANGES).forEach(function (c) {
        var r = el('div', 'row change');
        r.appendChild(el('div', 'kind', c.map ? KINDS[c.map] : 'Attention'));
        var mid = el('div'), nm = el('div', 'name', c.name || c.key);
        if (c.down) { nm.appendChild(document.createTextNode(' ')); nm.appendChild(el('span', 'chg down', 'Stopped working')); }
        mid.appendChild(nm); r.appendChild(mid);
        var to = el('div', 'to'), mk = (c.map === 'prios' || c.map === 'tasks') ? tpill : pill;
        if (c.type === 'changed') { to.appendChild(mk(c.from)); to.appendChild(el('span', 'arrow', '→')); to.appendChild(mk(c.to)); }
        else if (c.type === 'added') { to.appendChild(el('span', 'tag', 'Added')); to.appendChild(mk(c.to)); }
        else if (c.type === 'removed') { to.appendChild(el('span', 'tag', 'Removed')); to.appendChild(mk(c.from)); }
        else to.appendChild(el('span', 'tag', c.type === 'attn-added' ? 'Added' : 'Cleared'));
        r.appendChild(to); box.appendChild(r);
      });
      if (list.length > MAX_CHANGES) box.appendChild(el('div', 'row', 'and ' + (list.length - MAX_CHANGES) + ' more.'));
      box.hidden = false;
    }
  }
  /* ADAPTED: no render() call; the page is already rendered server-side. */
  function applyLive(data) {
    var cur = stateOf(data), r = readSeen(), list = [], mode, seen = r.value;
    if (!r.ok) mode = 'nostore';
    else if (!r.value) { mode = writeSeen(cur) ? 'first' : 'nostore'; seen = cur; }
    else {
      try { list = diffStates(r.value, cur); mode = list.length ? 'changes' : 'none'; }
      catch (e) { list = []; mode = writeSeen(cur) ? 'first' : 'nostore'; seen = cur; }
    }
    markChanged(list);
    tagRows();
    renderChanges(mode, list, seen);
  }
"""

def _esc_attr(value):
    return html.escape(str(value or ''), quote=True)


def _thread_state_word(thread):
    """The state word the page shows for a thread (the feed's state)."""
    state = str(thread.get('state') or '').upper()
    if state in ('WAITING', 'IN_PROGRESS', 'ANSWERED', 'CLOSED', 'STALE',
                 'NEEDS_DENIEL', 'OFF_HOURS'):
        return state
    return state or 'WAITING'


def _state_label(thread, now):
    """Human display for a thread state, always a word (never colour alone)."""
    state = _thread_state_word(thread)
    ball = thread.get('ball_with') or ''
    since = parse_time(thread.get('since'))
    age = fmt_age(minutes_between(thread.get('since'),
                                  now.isoformat() if now else None))
    if state == 'WAITING':
        return 'Waiting for %s%s' % (ball or 'an agent',
                                     (', ' + age) if since else '')
    if state == 'IN_PROGRESS':
        return '%s working since %s' % (ball or 'An agent', fmt_hm(since))
    if state == 'ANSWERED':
        return 'Answered'
    if state == 'CLOSED':
        return 'Closed'
    if state == 'STALE':
        return 'Stale, check the agent'
    if state == 'NEEDS_DENIEL':
        return 'Needs Deniel'
    if state == 'OFF_HOURS':
        return 'Off hours'
    return state


def render_header(feed, status_json, status_stale, status_as_of, now):
    """The always-visible header: title, score, meta, banners, Needs strip."""
    generated = parse_time(feed.get('generated_at'))
    now_dt = now or datetime.now(WIB)
    age_min = minutes_between(feed.get('generated_at'), now_dt.isoformat())
    stale = feed_is_stale(feed.get('generated_at'), now_dt)
    no_feed = not feed.get('generated_at')
    pct, live, partial, total, detail = parts_score(status_json)
    items = need_deniel_items(feed, status_json)

    out = ['<header>']
    out.append('<div class="hd-top"><div class="hd-main">')
    out.append('<h1>Agent Activity Board</h1>')
    out.append('<p class="lede">One command center for the AI IT Assistant project: who is doing what, where it is, and how far each task is.</p>')
    out.append('</div>')
    out.append('<div class="score" role="group" aria-label="Parts working">'
               '<div class="score-n" id="score-n">%s</div>'
               '<div class="score-l">of parts working</div>'
               '<div class="score-s" id="score-s">%s</div></div>' % (E(pct), E(detail)))
    out.append('</div>')
    out.append('<div class="meta">')
    if generated:
        out.append('<span>Work data as of <b id="m-feed">%s</b> <span id="m-feed-age">(%s)</span></span>'
                   % (E(fmt_clock(generated)), E(fmt_age(age_min))))
    else:
        out.append('<span>Work data as of <b>no feed received yet</b></span>')
    if status_json:
        upd = parse_time(status_json.get('updated'))
        out.append('<span>Status data updated <b>%s</b></span>' % E(fmt_wib(upd)))
        out.append('<span>by <b>%s</b></span>' % E(status_json.get('updated_by') or 'unknown'))
        out.append('<span>Architecture <b>v%s</b></span>' % E(status_json.get('architecture_version') or 'unknown'))
        if status_stale:
            out.append('<span>(last good copy, %s)</span>' % E(fmt_hm(parse_time(status_as_of))))
    else:
        out.append('<span>Status data: <b>no status data yet</b></span>')
    out.append('<span>Page checked <b id="m-checked">%s</b></span>' % E(fmt_clock(now_dt)))
    out.append('<button type="button" id="refresh">Refresh now</button>')
    out.append('</div>')
    if no_feed:
        out.append('<div class="banner red" role="alert"><strong>Feed stopped.</strong> No feed has been received yet. What you see is empty.</div>')
    elif stale:
        out.append('<div class="banner red" id="feed-banner" role="alert"><strong>Feed stopped.</strong> What you see is old. The last feed arrived %s.</div>'
                   % E(fmt_age(age_min)))
    else:
        out.append('<div class="banner red" id="feed-banner" role="alert" hidden><strong>Feed stopped.</strong> What you see is old.</div>')
    if items:
        out.append('<div class="need-strip" role="alert"><strong>%d items need Deniel</strong><ul>' % len(items))
        for kind, text, _task in items[:2]:
            out.append('<li>%s: %s</li>' % (E(kind), E(text)))
        out.append('</ul><a class="btn" href="#feed">Open</a></div>')
    out.append('</header>')
    return '\n'.join(out)


def render_map(feed, agents, tasks_by_id, now):
    """Tab 1, Live map: one card per agent around the Agent bus bar."""
    feed_agents = {a.get('id'): a for a in feed.get('agents') or []
                   if isinstance(a, dict)}
    threads = [t for t in feed.get('threads') or [] if isinstance(t, dict)]
    started = set()
    for t in threads:
        if str(t.get('state') or '').upper() == 'IN_PROGRESS':
            msgs = t.get('messages') or []
            if msgs:
                started.add(msgs[-1].get('msg_id'))
    has_progress = any(str(t.get('state') or '').upper() == 'IN_PROGRESS'
                       for t in threads)

    def card(agent):
        aid = agent['id']
        info = feed_agents.get(aid) or {}
        word, detail = agent_card_state(agent, feed_agents, threads, now, started)
        inbox_count = info.get('inbox_count') or 0
        oldest = info.get('oldest_wait_min')
        chip_cls = 'chip amber' if inbox_count else 'chip'
        chip_txt = ('%d · %s' % (inbox_count, fmt_age(oldest))
                    if inbox_count else '0')
        label = doing_label(aid, feed, tasks_by_id)
        if agent.get('created', True) is False:
            word = 'not created'
        dot_cls = CARD_STATE_CLASS.get(word, 'dot-idle')
        state_txt = word
        if word == 'waiting' and detail:
            state_txt = 'waiting %s' % detail
        elif word == 'needs a ping' and detail:
            state_txt = 'needs a ping (%s)' % detail
        icon = KIND_ICON.get(agent.get('kind'), KIND_ICON['manual'])
        brand = BRAND_BY_ID.get(aid)
        plat_cls = (' p-' + brand[0]) if brand else ''
        brand_html = ('<span class="brand brand-%s" title="Runs on %s">%s</span>'
                      % (brand[0], BRAND_NAME[brand[0]], brand[1])) if brand else ''
        out = ['<div class="map-card%s%s">' % (' not-created' if word == 'not created' else '', plat_cls)]
        out.append('<div class="who"><span class="kicon">%s</span><span class="nm">%s</span>%s</div>'
                   % (icon, E(aid), brand_html))
        out.append('<div class="where">%s · %s</div>' % (E(KIND_WORD.get(agent.get('kind'), 'agent')),
                                                          E(agent.get('runs_where') or '')))
        out.append('<div class="map-state%s"><span class="dot %s"></span>%s</div>'
                   % (' stale-word' if word == 'stale' else '', dot_cls, E(state_txt)))
        out.append('<div><span class="%s" title="Messages waiting for this agent">%s</span></div>' % (chip_cls, E(chip_txt)))
        if label:
            task_id = label.split(' ', 1)[0]
            out.append('<a class="map-label" href="#task=%s">%s</a>' % (E(task_id), E(label)))
        else:
            out.append('<span class="map-label unknown">nothing known</span>')
        out.append('</div>')
        return '\n'.join(out)

    by_id = {a['id']: a for a in agents}
    top = ['deniel', 'archie', 'operations', 'grok', 'validator']
    bottom = ['muse-ping', 'muse-kb', 'muse-feed', 'muse-page', 'support']
    out = ['<section aria-labelledby="h-map" id="tab-map" role="tabpanel">',
           '<div class="sec-head"><h2 id="h-map">Live map</h2>',
           '<p class="lede">Who is doing what, and who is waiting. Labels come from the task list, never from message text.</p></div>',
           '<div class="map-scroll"><div class="map-inner">']
    out.append('<div class="map-row">')
    for aid in top:
        if aid in by_id:
            out.append(card(by_id[aid]))
            if aid == 'archie':
                out.append('<div class="map-delegate" aria-label="Delegation: Archie delegates to Operations">'
                           '<span class="arr">&rarr;</span><span>delegates</span></div>')
    out.append('</div>')
    out.append('<div class="map-bus">Agent bus</div>')
    out.append('<div class="map-row">')
    for aid in bottom:
        if aid in by_id:
            out.append(card(by_id[aid]))
    out.append('</div>')
    out.append('</div></div>')
    out.append('<div class="key"><span><i></i>waiting work</span><span><i class="d"></i>not live yet</span>'
               '<span>Dot: green working, amber waiting, red stale, hollow idle or off hours, dashed not created.</span>'
               '<span>Icons: person, Claude Code session, scheduled chat, manual chat.</span>'
               '<span>Badges: C runs on Claude, M on Muse, G on Grok.</span></div>')
    if not has_progress:
        out.append('<p class="note">In progress is not recorded &mdash; no started marker was seen in this feed. '
                   'The board shows Waiting and Answered only; it never guesses that work is in progress.</p>')
    out.append('</section>')
    return '\n'.join(out)


def render_arch(status_json, status_stale):
    """Tab 2, Architecture: the Status page's diagrams, tally, What changed,
    intents, inventory and attention, with the same data and rules."""
    if not status_json:
        return ('<section aria-labelledby="h-arch" id="tab-arch" role="tabpanel" hidden>'
                '<div class="sec-head"><h2 id="h-arch">Architecture</h2></div>'
                '<p>No status data in this feed, and no last good copy is kept yet. '
                'The Architecture and Delivery tabs will appear when a feed with status data arrives.</p></section>')
    els = status_json.get('elements') or {}
    counts = {}
    for k in STATUS:
        counts[k] = 0
    for e in els.values():
        if isinstance(e, dict):
            counts[norm_status(e.get('status'))] += 1
    out = ['<section aria-labelledby="h-arch" id="tab-arch" role="tabpanel" hidden>']
    if status_stale:
        out.append('<div class="banner"><strong>Check: </strong>this is the last good status copy; the latest feed carried no status data.</div>')
    out.append('<section aria-labelledby="h-changes" id="changes-sec" hidden>'
               '<div class="sec-head"><h2 id="h-changes">What changed</h2>'
               '<p class="lede" id="chg-lede" role="status"></p></div>'
               '<div class="chg-bar"><button type="button" id="mark-seen" hidden>Mark as seen</button></div>'
               '<div class="rows" id="changes" hidden></div></section>')
    out.append('<section aria-labelledby="h-tally"><h3 id="h-tally">All parts by status</h3><div class="tally" id="tally">')
    meanings = {'live': 'working in production', 'partial': 'running, but limited',
                'built': 'exists, switched off or not deployed', 'design': 'designed or not started, nothing built',
                'parked': 'deliberately on hold', 'unknown': 'state not confirmed'}
    for k in STATUS:
        out.append('<div><span class="n">%d</span>%s<small>%s</small></div>'
                   % (counts[k], status_pill(k), E(meanings[k])))
    out.append('</div></section>')
    out.append('<section aria-labelledby="h-intents"><div class="sec-head"><h2 id="h-intents">What the assistant must do</h2>'
               '<p class="lede">Four core jobs and group listening, all on the same number.</p></div><div class="rows" id="intents">')
    for it in status_json.get('intents') or []:
        if not isinstance(it, dict):
            continue
        sub = '. '.join(x for x in (it.get('path'), it.get('note')) if x)
        out.append('<div class="row intent" data-chg-map="intents" data-chg-key="%s">'
                   '<div class="num">%s</div><div><div class="name chg-name">%s</div>%s</div><div>%s</div></div>'
                   % (_esc_attr(it.get('n')), E(str(it.get('n'))), E(it.get('name')),
                      ('<div class="note">%s</div>' % E(sub)) if sub else '',
                      status_pill(it.get('status'))))
    out.append('</div></section>')
    out.append('<section aria-labelledby="h-fig1"><div class="sec-head"><h2 id="h-fig1">How a message is routed</h2>'
               '<p class="lede">The Gateway decides on facts only: who sent it, whether it is a group, and whether it is a quoted approval reply. Aiti decides what the person means.</p></div>'
               '<figure><div class="scroll" tabindex="0">%s</div>'
               '<div class="key"><span><i></i>works today</span><span><i class="d"></i>not live yet</span><span>Grey boxes have no status of their own.</span></div>'
               '<figcaption>Every message enters through WAHA and the Aiti Gateway. A message that no other route claims goes to Aiti, so a failed lookup never loses a chat.</figcaption>'
               '</figure></section>' % figures.figure1(els))
    out.append('<section aria-labelledby="h-fig2"><div class="sec-head"><h2 id="h-fig2">The Knowledge Base approval loop</h2>'
               '<p class="lede">A fact goes out to the approver as a WhatsApp digest. The approver\'s reply comes back and the fact is published. The loop is closed only when every arrow is solid.</p></div>'
               '<figure><div class="scroll" tabindex="0">%s</div>'
               '<div class="key"><span><i></i>works today</span><span><i class="d"></i>not live yet</span></div>'
               '<figcaption>Python holds every approval rule. The n8n workflows only carry messages between SharePoint and WhatsApp.</figcaption>'
               '</figure></section>' % figures.figure2(els))
    out.append('<section aria-labelledby="h-inv"><div class="sec-head"><h2 id="h-inv">All parts</h2>'
               '<p class="lede">Every component, with its status, what is known about it, and who may change it.</p></div><div class="groups" id="inventory">')
    groups = ['Routing', 'Handlers', 'KB pipeline', 'Background workers', 'Platform']
    by_group = {}
    for k, e in els.items():
        if isinstance(e, dict):
            by_group.setdefault(e.get('group') or 'Other', []).append((k, e))
    for gname in groups + [g for g in by_group if g not in groups]:
        items = by_group.get(gname) or []
        if not items:
            continue
        out.append('<div class="group"><h3>%s (%d)</h3><div class="rows">' % (E(gname), len(items)))
        for k, e in items:
            out.append('<div class="row" data-chg-map="els" data-chg-key="%s"><div>%s</div>'
                       '<div><div class="name chg-name">%s</div>%s</div><div class="owner">%s</div></div>'
                       % (_esc_attr(k), status_pill(e.get('status')),
                          E(e.get('name') or '(no name)'),
                          ('<div class="note">%s</div>' % E(e.get('note'))) if e.get('note') else '',
                          E(e.get('owner') or '')))
        out.append('</div></div>')
    out.append('</div></section>')
    out.append('<section aria-labelledby="h-attn"><h2 id="h-attn">Needs attention</h2><ul class="attn" id="attention">')
    attention = status_json.get('attention') or []
    if attention:
        for line in attention:
            out.append('<li>%s</li>' % E(line))
    else:
        out.append('<li>Nothing is waiting.</li>')
    out.append('</ul></section>')
    out.append('</section>')
    return '\n'.join(out)


def render_delivery(status_json):
    """Tab 3, Delivery: the Status page's delivery order."""
    if not status_json:
        return ('<section aria-labelledby="h-delivery" id="tab-delivery" role="tabpanel" hidden>'
                '<div class="sec-head"><h2 id="h-delivery">Delivery order</h2></div>'
                '<p>No status data in this feed, and no last good copy is kept yet.</p></section>')
    out = ['<section aria-labelledby="h-delivery" id="tab-delivery" role="tabpanel" hidden>',
           '<div class="sec-head"><h2 id="h-delivery">Delivery order</h2>',
           '<p class="lede">Priorities are worked in this order.</p></div>',
           '<ol class="prio" id="delivery">']
    for prio in status_json.get('delivery') or []:
        if not isinstance(prio, dict):
            continue
        out.append('<li><div class="head" data-chg-map="prios" data-chg-key="P%s">'
                   '<div class="p">P%s</div><div><div class="name chg-name">%s</div>%s</div><div>%s</div></div>'
                   % (_esc_attr(prio.get('p')), E(str(prio.get('p'))), E(prio.get('name')),
                      ('<div class="note">%s</div>' % E(prio.get('note'))) if prio.get('note') else '',
                      task_pill(prio.get('state') or 'not_started')))
        tasks = prio.get('tasks') or []
        if tasks:
            out.append('<div class="tasks">')
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                key = '%s|%s' % (task.get('id'), task.get('title'))
                out.append('<div class="row task" data-chg-map="tasks" data-chg-key="%s">'
                           '<div class="id"><a href="#task=%s">%s</a></div>'
                           '<div class="chg-name">%s</div><div class="who">%s</div><div>%s</div></div>'
                           % (_esc_attr(key), _esc_attr(task.get('id')), E(task.get('id')),
                              E(task.get('title')), E(task.get('owner')), task_pill(task.get('state'))))
            out.append('</div>')
        out.append('</li>')
    out.append('</ol></section>')
    return '\n'.join(out)

def render_agents_tab(feed, agents, tasks_by_id, now):
    """Tab 4, Agents: one row per agent."""
    feed_agents = {a.get('id'): a for a in feed.get('agents') or []
                   if isinstance(a, dict)}
    threads = [t for t in feed.get('threads') or [] if isinstance(t, dict)]
    started = set()
    for t in threads:
        if str(t.get('state') or '').upper() == 'IN_PROGRESS':
            msgs = t.get('messages') or []
            if msgs:
                started.add(msgs[-1].get('msg_id'))
    out = ['<section aria-labelledby="h-agents" id="tab-agents" role="tabpanel" hidden>',
           '<div class="sec-head"><h2 id="h-agents">Agents</h2>',
           '<p class="lede">One row per agent. "Last seen" is the time of the last file it wrote (bus or Reports).</p></div>',
           '<div class="table-scroll"><table class="board"><thead><tr>'
           '<th>Agent</th><th>Runs where</th><th>Doing now</th><th>Waiting for it</th>'
           '<th>Last seen</th><th>Schedule</th><th>State</th></tr></thead><tbody>']
    for agent in agents:
        aid = agent['id']
        info = feed_agents.get(aid) or {}
        word, detail = agent_card_state(agent, feed_agents, threads, now, started)
        if agent.get('created', True) is False:
            word = 'not created'
        label = doing_label(aid, feed, tasks_by_id)
        inbox_count = info.get('inbox_count') or 0
        oldest = info.get('oldest_wait_min')
        waiting = ('%d message%s, oldest %s' % (inbox_count, '' if inbox_count == 1 else 's', fmt_age(oldest))
                   if inbox_count else '—')
        last_seen = parse_time(info.get('last_seen'))
        state_txt = word
        if word in ('waiting', 'needs a ping') and detail:
            state_txt = '%s (%s)' % (word, detail)
        out.append('<tr><td><strong>%s</strong><br><span class="note">%s</span></td>'
                   '<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>'
                   '<td><span class="dot %s"></span> %s</td></tr>'
                   % (E(aid), E(KIND_WORD.get(agent.get('kind'), 'agent')),
                      E(agent.get('runs_where') or ''),
                      E(label) if label else 'nothing known',
                      E(waiting),
                      E(fmt_hm(last_seen)) if last_seen else '—',
                      E(agent.get('schedule') or ''),
                      CARD_STATE_CLASS.get(word, 'dot-idle'), E(state_txt)))
    out.append('</tbody></table></div></section>')
    return '\n'.join(out)


def _task_threads(feed):
    """Threads grouped by task id; threads with no task go under ''."""
    groups = {}
    order = []
    for thread in feed.get('threads') or []:
        if not isinstance(thread, dict):
            continue
        key = thread.get('task') or ''
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(thread)
    return groups, order


def _next_text(thread, tasks_by_id):
    """The 'Next' column: waiting_on when set, else what the thread waits on."""
    task = tasks_by_id.get(thread.get('task') or '') or {}
    if task.get('waiting_on'):
        return task.get('waiting_on')
    state = _thread_state_word(thread)
    if state in ('WAITING', 'IN_PROGRESS', 'STALE'):
        return 'Waiting on %s' % (thread.get('ball_with') or 'a reply')
    if state == 'ANSWERED':
        return 'Answered; nothing open'
    if state == 'CLOSED':
        return 'Closed'
    if state == 'NEEDS_DENIEL':
        return 'Deniel decides'
    if state == 'OFF_HOURS':
        return 'Waits for working hours'
    return '—'


def render_tasks(feed, status_json, tasks_by_id, now):
    """Tab 5, Tasks: open tasks on the left, task detail on the right."""
    groups, order = _task_threads(feed)
    # Open means a thread whose state is not CLOSED or ANSWERED: the same
    # open definition the Live map uses. A task with no open thread gets
    # no row; its threads still show in its detail panel.
    open_tasks = []
    for key in order:
        threads = groups[key]
        open_ts = [t for t in threads
                   if _thread_state_word(t) not in ('CLOSED', 'ANSWERED')]
        if open_ts:
            open_tasks.append((key, threads, open_ts))
    has_progress = any(_thread_state_word(t) == 'IN_PROGRESS'
                       for t in feed.get('threads') or [])
    default_task = open_tasks[0][0] if open_tasks else ''
    out = ['<section aria-labelledby="h-tasks" id="tab-tasks" role="tabpanel" hidden>',
           '<div class="sec-head"><h2 id="h-tasks">Tasks</h2>',
           '<p class="lede">One row per task. Select a row to see the task detail and its threads. '
           'A missing field says "not recorded" &mdash; the page never invents one.</p></div>']
    if not has_progress:
        out.append('<p class="note">In progress is not recorded &mdash; no started marker was seen in this feed.</p>')
    out.append('<div class="tasks-layout"><div>')
    out.append('<div class="table-scroll"><table class="board"><thead><tr>'
               '<th>What</th><th>Who has the ball</th><th>Where</th><th>Since</th><th>State</th><th>Next</th>'
               '</tr></thead><tbody>')
    for key, threads, open_ts in open_tasks:
        task = tasks_by_id.get(key) or {}
        if key:
            title = task.get('title') or ''
            what = '%s %s' % (key, title) if title else key
        else:
            what = 'Other work'
        count = '%d thread%s, %d open' % (
            len(threads), '' if len(threads) == 1 else 's', len(open_ts))
        what = '%s (%s)' % (what, count)
        # The row shows the oldest open thread (the longest wait); the
        # count covers the rest.
        dated = [t for t in open_ts if t.get('since')]
        oldest = (min(dated, key=lambda t: str(t.get('since')))
                  if dated else open_ts[0])
        since = parse_time(oldest.get('since'))
        out.append('<tr data-task="%s" tabindex="0"><td><strong>%s</strong></td><td>%s</td><td>%s</td>'
                   '<td>%s</td><td>%s</td><td>%s</td></tr>'
                   % (_esc_attr(key), E(what), E(oldest.get('ball_with') or ''),
                      E(oldest.get('where') or ''),
                      E(fmt_hm(since)) if since else '—',
                      E(_state_label(oldest, now)), E(_next_text(oldest, tasks_by_id))))
    if not open_tasks:
        out.append('<tr><td colspan="6">No open tasks in this feed.</td></tr>')
    out.append('</tbody></table></div></div>')
    out.append('<div id="task-details">')
    for key in order:
        threads = groups[key]
        task = tasks_by_id.get(key) or {}
        title = task.get('title') or ''
        owner = task.get('owner') or ''
        stage = task.get('stage') or ''
        summary = task.get('summary') or ''
        waiting_on = task.get('waiting_on') or ''
        hidden = '' if key == default_task else ' hidden'
        out.append('<div class="detail" data-task-detail="%s"%s>' % (_esc_attr(key), hidden))
        out.append('<h3>%s</h3>' % E('%s %s' % (key, title) if key else 'Other work'))
        out.append('<dl>')
        out.append('<dt>Task id</dt><dd>%s</dd>' % (E(key) if key else 'not recorded'))
        out.append('<dt>Title</dt><dd>%s</dd>' % (E(title) if title else 'not recorded'))
        out.append('<dt>Owner</dt><dd>%s</dd>' % (E(owner) if owner else 'not recorded'))
        boxes = []
        for s in STAGES:
            boxes.append('<span%s>%s</span>' % (' class="on"' if s == stage else '', STAGE_LABEL[s]))
        out.append('<dt>Stage</dt><dd><div class="stages">%s</div>%s</dd>'
                   % (''.join(boxes), '' if stage else ' <span class="note">not recorded</span>'))
        out.append('<dt>What it is about</dt><dd>%s</dd>' % (E(summary) if summary else 'not recorded'))
        out.append('<dt>Waiting on</dt><dd>%s</dd>' % (E(waiting_on) if waiting_on else 'not recorded'))
        out.append('</dl>')
        out.append('<h3>Threads</h3><ul class="timeline">')
        for thread in threads:
            ts = parse_time(thread.get('since'))
            out.append('<li><span class="tm">%s</span><span>%s</span><span>%s</span><span>%s</span><span>%s</span></li>'
                       % (E(fmt_hm(ts)) if ts else '—',
                          E(thread.get('slug') or thread.get('thread_id') or ''),
                          E(_state_label(thread, now)),
                          E(thread.get('ball_with') or ''),
                          E(thread.get('where') or '')))
        out.append('</ul>')
        msgs = []
        for thread in threads:
            for msg in thread.get('messages') or []:
                msgs.append(msg)
        msgs.sort(key=lambda m: str(m.get('time') or ''))
        out.append('<h3>Timeline</h3><ul class="timeline">')
        for msg in msgs:
            mt = parse_time(msg.get('time'))
            out.append('<li><span class="tm">%s</span><span>%s &rarr; %s</span><span>%s</span><span>%s</span></li>'
                       % (E(fmt_hm(mt)) if mt else '—',
                          E(msg.get('from') or ''), E(msg.get('to') or ''),
                          E(msg.get('action') or ''), E(msg.get('slug') or '')))
        out.append('</ul>')
        related = [r for r in feed.get('reports') or []
                   if isinstance(r, dict) and key and key in str(r.get('topic') or '')]
        out.append('<h3>Latest Reports files</h3>')
        if related:
            out.append('<ul class="timeline">')
            for rep in related[:5]:
                rt = parse_time(rep.get('time'))
                out.append('<li><span class="tm">%s</span><span>%s &rarr; %s</span><span>%s</span><span>%s</span></li>'
                           % (E(fmt_hm(rt)) if rt else '—',
                              E(rep.get('from') or ''), E(rep.get('to') or ''),
                              E(rep.get('type') or ''), E(rep.get('topic') or '')))
            out.append('</ul>')
        else:
            out.append('<p class="note">not recorded</p>')
        out.append('</div>')
    if not order:
        out.append('<div class="detail"><p class="note">No threads in this feed.</p></div>')
    out.append('</div></div>')
    unreadable = feed.get('unreadable') or []
    if unreadable:
        out.append('<div class="unreadable"><strong>Unreadable messages</strong> &mdash; '
                   'these files do not match the bus protocol, so the board cannot place them in a thread. '
                   'They are shown here, never hidden.<ul>')
        for item in unreadable:
            if not isinstance(item, dict):
                continue
            out.append('<li><code>%s</code> &mdash; %s</li>'
                       % (E(item.get('file') or ''), E(item.get('reason') or 'unreadable header')))
        out.append('</ul></div>')
    out.append('</section>')
    return '\n'.join(out)


def feed_events(feed, limit=50):
    """Newest events for the Feed tab: bus messages, Reports, human inbox."""
    events = []
    for thread in feed.get('threads') or []:
        if not isinstance(thread, dict):
            continue
        for msg in thread.get('messages') or []:
            if not isinstance(msg, dict):
                continue
            events.append({'time': msg.get('time') or '', 'from': msg.get('from') or '',
                           'to': msg.get('to') or '', 'kind': msg.get('action') or '',
                           'slug': msg.get('slug') or '', 'task': thread.get('task') or ''})
    for rep in feed.get('reports') or []:
        if not isinstance(rep, dict):
            continue
        events.append({'time': rep.get('time') or '', 'from': rep.get('from') or '',
                       'to': rep.get('to') or '', 'kind': rep.get('type') or '',
                       'slug': rep.get('topic') or '', 'task': ''})
    for entry in feed.get('human_inbox') or []:
        if not isinstance(entry, dict):
            continue
        events.append({'time': entry.get('time') or '', 'from': entry.get('from') or '',
                       'to': 'Deniel', 'kind': entry.get('action') or '',
                       'slug': entry.get('slug') or '', 'task': entry.get('task') or ''})
    events.sort(key=lambda e: str(e.get('time') or ''), reverse=True)
    return events[:limit]


def render_feed_tab(feed):
    """Tab 6, Feed: newest 50 events. No message text, ever."""
    events = feed_events(feed)
    out = ['<section aria-labelledby="h-feed" id="tab-feed" role="tabpanel" hidden>',
           '<div class="sec-head"><h2 id="h-feed">Feed</h2>',
           '<p class="lede">Newest %d events from the bus and Reports. Time, who wrote to whom, action, slug, task &mdash; no message text.</p></div>' % len(events),
           '<div class="table-scroll"><table class="board"><thead><tr>'
           '<th>Time</th><th>From</th><th>To</th><th>Action</th><th>Slug</th><th>Task</th>'
           '</tr></thead><tbody>']
    for ev in events:
        et = parse_time(ev.get('time'))
        out.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>'
                   % (E(fmt_hm(et)) if et else '—',
                      E(ev.get('from') or ''), E(ev.get('to') or ''),
                      E(ev.get('kind') or ''), E(ev.get('slug') or ''),
                      E(ev.get('task') or '')))
    if not events:
        out.append('<tr><td colspan="6">No events in this feed.</td></tr>')
    out.append('</tbody></table></div></section>')
    return '\n'.join(out)


BOARD_JS = """
  var TABS = ['map', 'arch', 'delivery', 'agents', 'tasks', 'feed'];
  function parseHash() {
    var h = location.hash.replace(/^#/, '');
    if (h.indexOf('task=') === 0) return { tab: 'tasks', task: h.slice(5) };
    var parts = h.split('/');
    var tab = parts[0] || 'map';
    if (TABS.indexOf(tab) === -1) tab = 'map';
    return { tab: tab, task: parts[1] || '' };
  }
  function selectTask(id) {
    var rows = document.querySelectorAll('tr[data-task]');
    rows.forEach(function (tr) {
      tr.classList.toggle('sel', tr.getAttribute('data-task') === id);
    });
    document.querySelectorAll('[data-task-detail]').forEach(function (d) {
      d.hidden = d.getAttribute('data-task-detail') !== id;
    });
  }
  function scrollToTask(id) {
    var rows = document.querySelectorAll('tr[data-task]');
    rows.forEach(function (tr) {
      if (tr.getAttribute('data-task') === id) tr.scrollIntoView({ block: 'nearest' });
    });
  }
  function showTab(tab, task) {
    TABS.forEach(function (t) {
      var sec = document.getElementById('tab-' + t);
      if (sec) sec.hidden = (t !== tab);
      var link = document.querySelector('nav.tabs a[href="#' + t + '"]');
      if (link) link.setAttribute('aria-selected', t === tab ? 'true' : 'false');
    });
    if (tab === 'tasks' && task) { selectTask(task); scrollToTask(task); }
  }
  function applyHash() { var h = parseHash(); showTab(h.tab, h.task); }
  window.addEventListener('hashchange', applyHash);
  document.querySelectorAll('tr[data-task]').forEach(function (tr) {
    tr.addEventListener('click', function () {
      var id = tr.getAttribute('data-task');
      if (location.hash === '#task=' + id) selectTask(id);
      else location.hash = '#task=' + id;
    });
    tr.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        location.hash = '#task=' + tr.getAttribute('data-task');
      }
    });
  });
  var POLL_MS = 30000;
  var SCROLL_KEY = 'board.scrollY';
  function reloadKeepingPlace() {
    // location.reload() keeps the URL, so the tab hash and any open
    // detail survive, but the scroll position does not: save it and
    // restore it after the reload.
    try { sessionStorage.setItem(SCROLL_KEY, String(window.scrollY)); } catch (e) {}
    location.reload();
  }
  function restoreScroll() {
    try {
      var y = sessionStorage.getItem(SCROLL_KEY);
      if (y !== null) {
        sessionStorage.removeItem(SCROLL_KEY);
        window.scrollTo(0, parseInt(y, 10) || 0);
      }
    } catch (e) {}
  }
  function poll() {
    fetch('/feed.json', { cache: 'no-store' }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).then(function (data) {
      var seen = document.body.getAttribute('data-generated') || '';
      if (data.generated_at && data.generated_at !== seen) reloadKeepingPlace();
    }).catch(function () { /* keep showing the last good data; the banner covers it */ });
  }
  setInterval(poll, POLL_MS);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) poll(); });
  $('refresh').addEventListener('click', function () { reloadKeepingPlace(); });
  var STATUS_DATA = null;
  try { STATUS_DATA = JSON.parse(document.getElementById('status-json').textContent || 'null'); } catch (e) { STATUS_DATA = null; }
  if (STATUS_DATA) { applyLive(STATUS_DATA); }
  else { renderChanges('offline', [], null); }
  $('mark-seen').addEventListener('click', function () {
    if (STATUS_DATA && writeSeen(stateOf(STATUS_DATA))) applyLive(STATUS_DATA);
  });
  applyHash();
  restoreScroll();
"""


def render_page(feed, agents, last_good_status=None, last_good_at=None, now=None):
    """The whole page as one HTML string, rendered from the kept feed."""
    now = now or datetime.now(WIB)
    feed = feed or {}
    status_json = feed.get('status_json')
    status_stale = False
    status_as_of = None
    if isinstance(status_json, dict) and status_json:
        pass
    elif last_good_status:
        status_json = last_good_status
        status_stale = True
        status_as_of = last_good_at
    else:
        status_json = None
    tasks_by_id = task_lookup(status_json)
    embedded = json.dumps(status_json, ensure_ascii=False).replace('</', '<\\/').replace('<!--', '<\\u0021--')
    body = []
    body.append('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">')
    body.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    body.append('<title>Agent Activity Board</title>')
    body.append('<style>%s</style></head>' % CSS)
    body.append('<body data-generated="%s"><div class="wrap">' % _esc_attr(feed.get('generated_at') or ''))
    body.append(render_header(feed, status_json, status_stale, status_as_of, now))
    body.append('<nav class="tabs" aria-label="Board tabs">'
                '<a href="#map" aria-selected="true">Live map</a>'
                '<a href="#arch" aria-selected="false">Architecture</a>'
                '<a href="#delivery" aria-selected="false">Delivery</a>'
                '<a href="#agents" aria-selected="false">Agents</a>'
                '<a href="#tasks" aria-selected="false">Tasks</a>'
                '<a href="#feed" aria-selected="false">Feed</a></nav>')
    body.append(render_map(feed, agents, tasks_by_id, now))
    body.append(render_arch(status_json, status_stale))
    body.append(render_delivery(status_json))
    body.append(render_agents_tab(feed, agents, tasks_by_id, now))
    body.append(render_tasks(feed, status_json, tasks_by_id, now))
    body.append(render_feed_tab(feed))
    body.append('<footer><p>This page shows the last feed the board service received. '
                'It does not probe any system: every status comes from the feed, and the feed '
                'carries headers only, never message text.</p>'
                '<p>Statuses: <code>live</code>, <code>partial</code>, <code>built</code>, '
                '<code>design</code> (shown as "Planned"), <code>parked</code>, <code>unknown</code> '
                '(shown as "Not verified"). Anything else is shown as "Not verified".</p></footer>')
    body.append('</div>')
    body.append('<script type="application/json" id="status-json">%s</script>' % embedded)
    body.append('<script>(function () {\n  \'use strict\';\n%s\n%s\n})();</script>' % (WHAT_CHANGED_JS, BOARD_JS))
    body.append('</body></html>')
    return '\n'.join(body)
