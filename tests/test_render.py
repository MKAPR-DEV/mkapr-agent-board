"""Tests for the rendered page: six tabs, map labels, score, layout rules.

Covers PRD criteria 7 (task detail), 9 (no outside requests, 375 px,
light/dark), 15 (map labels) and 16 (tab hashes, keyboard).
Run:  python3 -m unittest discover -s tests -v   (from the workdir root)
"""
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers as H
from helpers import load_fixture
sys.path.insert(0, os.path.join(H.ROOT, 'src'))
import board_render as R


def _lum(hexs):
    """WCAG relative luminance of a #rrggbb colour."""
    r, g, b = (int(hexs[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = f(r), f(g), f(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b):
    """WCAG contrast ratio between two #rrggbb colours."""
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.srv = H.Server(seed=load_fixture('feed-basic.json'))

    def tearDown(self):
        self.srv.stop()

    def _page(self, seed=None):
        if seed is None:
            _, _, body = self.srv.get('/')
            return body.decode('utf-8')
        srv = H.Server(seed=seed)
        try:
            _, _, body = srv.get('/')
            return body.decode('utf-8')
        finally:
            srv.stop()

    def test_six_tabs_with_real_hash_links(self):
        page = self._page()
        for tab in ('map', 'arch', 'delivery', 'agents', 'tasks', 'feed'):
            self.assertIn('href="#%s"' % tab, page)
            self.assertIn('id="tab-%s"' % tab, page)

    def test_no_external_requests(self):
        page = self._page()
        self.assertNotIn('<link', page)
        self.assertNotIn('<script src', page)
        self.assertNotIn('<img', page)
        self.assertNotIn('http://', page)
        self.assertNotIn('https://', page)
        self.assertNotIn('@import', page)
        # url(#...) is an internal SVG fragment reference (arrowheads), not
        # an external request; only remote URLs are forbidden.
        self.assertNotIn('url(http', page)
        self.assertNotIn('url(//', page)
        self.assertIn('marker-end="url(#', page)

    def test_light_and_dark_mode(self):
        page = self._page()
        self.assertIn('prefers-color-scheme: dark', page)
        self.assertIn('color-scheme: light', page)
        self.assertIn('color-scheme: dark', page)

    def test_phone_width_rules(self):
        page = self._page()
        self.assertIn('max-width: 720px', page)
        self.assertIn('overflow-x: auto', page)  # wide diagrams scroll in their box
        self.assertIn('width=device-width, initial-scale=1', page)

    def test_score_is_parts_working_to_two_decimals(self):
        page = self._page()
        # Real status.json: 45 parts, 28 live + 5 partial -> 67.78%.
        self.assertIn('67.78%', page)
        self.assertIn('28 live + 5 partial', page)
        self.assertIn('of 45 parts', page)

    def test_header_meta_from_status_json(self):
        page = self._page()
        self.assertIn('19 Nov 2026, 12:07 WIB', page)
        self.assertIn('@operations (Ops)', page)
        self.assertIn('Architecture <b>v1.26</b>', page)

    def test_map_labels_come_from_tasks_not_message_text(self):
        page = self._page()
        # muse-ping's label is the task id + the status.json title.
        self.assertIn('TSK-015 Human inbox alert', page)
        self.assertIn('href="#task=TSK-015"', page)
        # An agent with no known task gets a non-clickable "nothing known".
        self.assertIn('nothing known', page)
        self.assertNotIn('href="#task="', page)
        # Label text never comes from message text: the slug of the "Other
        # work" thread must not appear as a map label (it may appear in the
        # Tasks timeline, which is where slugs belong).
        map_section = page.split('id="tab-map"')[1].split('id="tab-arch"')[0]
        self.assertNotIn('check-a-report', map_section)

    def test_not_created_agents_are_dashed_not_errors(self):
        # v0.1.7: the real config has no not-created agents any more
        # (feed and page are live, status is retired), so the live page
        # shows none. The dashed display is tested with a synthetic
        # agent: flip 'support' (in the map's bottom row) to
        # created:false and render directly.
        agents = H.load_agents()
        for a in agents:
            if a['id'] == 'support':
                a['created'] = False
        page = R.render_page(load_fixture('feed-basic.json'), agents)
        self.assertIn('not created', page)
        self.assertIn('map-card not-created', page)
        # And the live page (real config) has no dashed not-created card.
        # (The map key still explains the dashed dot; that is the legend,
        # not a card.)
        live = self._page()
        self.assertNotIn('map-card not-created', live)

    def test_agents_config_matches_agents_v18(self):
        # v0.1.7: config is a copy of AGENTS.md v1.8 — feed and page
        # live, status retired, validator added.
        with open(os.path.join(H.ROOT, 'config', 'agents.json'),
                  encoding='utf-8') as f:
            cfg = json.load(f)
        self.assertIn('AGENTS.md v1.8 (Archie, 2026-11-20)', cfg['source'])
        ids = [a['id'] for a in cfg['agents']]
        self.assertEqual(len(ids), 10)
        self.assertNotIn('status', ids)
        self.assertIn('validator', ids)
        self.assertTrue(all(a['created'] for a in cfg['agents']))
        val = next(a for a in cfg['agents'] if a['id'] == 'validator')
        self.assertEqual(val['kind'], 'session')
        self.assertEqual(val['inbox'], 'inbox/validator/')
        self.assertEqual(val['runs_where'],
                         'Subagent in the architect session')
        self.assertEqual(val['stale_minutes'], 25)

    def test_delegation_arrow_is_labelled(self):
        page = self._page()
        self.assertIn('Delegation: Archie delegates to Operations', page)

    def test_delivery_rows_link_to_tasks(self):
        page = self._page()
        self.assertIn('<a href="#task=TSK-012">TSK-012</a>', page)
        self.assertIn('<a href="#task=TSK-015">TSK-015</a>', page)
        self.assertIn('Close the KB loop', page)

    def test_task_rows_are_keyboard_selectable(self):
        page = self._page()
        self.assertIn('data-task="TSK-015" data-state="in_progress" tabindex="0"', page)
        self.assertIn('data-task-detail="TSK-015"', page)
        # Stage boxes: the current stage is filled.
        self.assertIn('<span class="on">Test</span>', page)
        self.assertIn('<span class="on">Import prep</span>', page)
        # v0.1.8: one row per delivery task, not per open thread. Every
        # delivery task has a row (14 in this fixture) plus Other work
        # for the untasked thread, plus TSK-016: Archie's doing_task,
        # with neither a thread nor a status record. TSK-012's only
        # thread is ANSWERED, so it still gets its row, with 0 open.
        tasks_section = page.split('id="tab-tasks"')[1].split('id="tab-feed"')[0]
        self.assertIn('One row per task.', tasks_section)
        self.assertEqual(tasks_section.count('<tr data-task='), 16)
        self.assertIn('tr data-task="TSK-012"', tasks_section)
        self.assertIn('TSK-012 KB pipeline main', tasks_section)
        self.assertIn('(1 thread, 0 open)', tasks_section)
        self.assertIn('(1 thread, 1 open)', tasks_section)
        self.assertIn('tr data-task="TSK-016"', tasks_section)
        self.assertIn('TSK-016 (no threads)', tasks_section)
        # The detail panel lists the task's threads, answered ones too.
        self.assertIn('<h3>Threads</h3>', tasks_section)
        self.assertIn('tsk-012-deploy-script', tasks_section)
        # A delivery task with no thread still gets its row, and its
        # detail panel says so.
        self.assertIn('(no threads)', tasks_section)
        self.assertIn('<p class="note">no threads</p>', tasks_section)
        # The state filter: In progress, Scheduled and Blocked checked,
        # Done unchecked, and every row carries its task state.
        self.assertIn('id="flt-all"', tasks_section)
        self.assertIn('class="flt-state" value="in_progress" checked', tasks_section)
        self.assertIn('class="flt-state" value="pending" checked', tasks_section)
        self.assertIn('class="flt-state" value="blocked" checked', tasks_section)
        self.assertIn('class="flt-state" value="done"', tasks_section)
        self.assertNotIn('class="flt-state" value="done" checked', tasks_section)
        self.assertIn('data-state="in_progress"', tasks_section)
        self.assertIn('data-state="done"', tasks_section)
        self.assertIn('id="tasks-no-match" hidden', tasks_section)
        # The State column shows the task's own state, not the thread's.
        self.assertIn('<td>In progress</td>', tasks_section)

    def test_task_detail_threads_capped_at_five(self):
        # Seven threads for one task: the detail shows the newest five
        # first, hides the other two behind a "Show all 7 threads"
        # control.
        threads = []
        for i in range(7):
            threads.append({'thread_id': 't%d' % i, 'task': 'TSK-016',
                            'slug': 'slug-%d' % i, 'state': 'WAITING',
                            'since': '2026-11-19T09:%02d:00+07:00' % (10 + i),
                            'messages': [{'time': '2026-11-19T09:%02d:00+07:00' % (10 + i),
                                          'from': 'archie', 'to': 'muse-page',
                                          'action': 'request', 'slug': 'slug-%d' % i,
                                          'msg_id': 'm%d' % i}]})
        feed = load_fixture('feed-basic.json')
        feed['threads'] = threads
        page = self._page(seed=feed)
        tasks_section = page.split('id="tab-tasks"')[1].split('id="tab-feed"')[0]
        # TSK-016 is a thread-only task here, so its panel is followed
        # by the Other work panel (the only one with an empty id).
        detail = tasks_section.split('data-task-detail="TSK-016"')[1] \
            .split('data-task-detail=""')[0]
        threads_block = detail.split('<h3>Threads</h3>')[1].split('<h3>Timeline</h3>')[0]
        self.assertEqual(threads_block.count('<li class="extra-thread" hidden>'), 2)
        self.assertIn('Show all 7 threads', threads_block)
        # Newest first: slug-6 (09:16) before slug-0 (09:10), and the
        # hidden two are the oldest.
        self.assertLess(threads_block.index('slug-6'), threads_block.index('slug-5'))
        self.assertLess(threads_block.index('slug-5'), threads_block.index('slug-2'))
        self.assertIn('slug-0', threads_block)
        self.assertIn('slug-1', threads_block)

    def test_feed_filters_are_present(self):
        page = self._page()
        feed_section = page.split('id="tab-feed"')[1]
        for fid in ('flt-time-from', 'flt-time-to', 'flt-from', 'flt-to',
                    'flt-action', 'flt-task', 'flt-clear'):
            self.assertIn('id="%s"' % fid, feed_section)
        self.assertIn('type="datetime-local"', feed_section)
        # Every row carries the data the filters read, and the no-match
        # row is there for when nothing matches.
        self.assertIn('data-time="', feed_section)
        self.assertIn('data-from="', feed_section)
        self.assertIn('data-to="', feed_section)
        self.assertIn('data-action="', feed_section)
        self.assertIn('data-task="', feed_section)
        self.assertIn('id="feed-no-match" hidden', feed_section)
        self.assertIn('No events match the filters.', feed_section)
        self.assertIn('function filterFeed()', page)
        self.assertIn('function filterTasks()', page)

    def test_map_labels_agree_with_task_rows(self):
        # v0.1.8 item 5 (Deniel's TSK-012 click): every task link on the
        # page — map labels, delivery links, the Needs strip — must have
        # both a Tasks row and a detail panel, so the clicked row and the
        # detail can never disagree.
        page = self._page()
        tasks_section = page.split('id="tab-tasks"')[1].split('id="tab-feed"')[0]
        ids = sorted(set(re.findall(r'href="#task=([A-Za-z0-9-]+)"', page)))
        self.assertTrue(ids)
        for tid in ids:
            self.assertIn('<tr data-task="%s"' % tid, tasks_section,
                          'map label %s has no Tasks row' % tid)
            self.assertIn('data-task-detail="%s"' % tid, tasks_section,
                          'map label %s has no detail panel' % tid)
        # Clicking selects the row (the 'sel' class) and scrolls to it.
        self.assertIn("tr.classList.toggle('sel'", page)
        self.assertIn("tr.scrollIntoView({ block: 'nearest' })", page)
        self.assertIn('Answered', tasks_section)

    def test_feed_tab_lists_newest_events_no_message_text(self):
        page = self._page()
        self.assertIn('tsk-015-repeat-test', page)
        self.assertIn('REPORT', page)
        self.assertEqual(page.count('<section aria-labelledby="h-feed"'),
                         1)

    def test_auto_refresh_every_30_seconds(self):
        page = self._page()
        self.assertIn('POLL_MS = 30000', page)
        self.assertIn("fetch('/feed.json'", page)
        self.assertIn('Refresh now', page)

    def test_mobile_layout_at_360px(self):
        page = self._page()
        # v0.1.4: the viewport meta so the page uses the device width.
        self.assertIn(
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            page)
        # Tabs wrap instead of forcing the page wide.
        self.assertIn('nav.tabs { display: flex; flex-wrap: wrap;', page)
        # Touch targets are at least 44 px: buttons, tab links and the
        # map pills all carry min-height 44px.
        self.assertGreaterEqual(page.count('min-height: 44px'), 4)
        # Table rows are at least 44 px tall (12px cell padding + text).
        self.assertIn('padding: 12px 12px;', page)
        # Wide content scrolls inside its own box, never the page:
        # diagrams, the Live map and tables each have overflow-x: auto.
        self.assertIn('.scroll { overflow-x: auto;', page)
        self.assertIn('.map-scroll { overflow-x: auto;', page)
        self.assertIn('.table-scroll { overflow-x: auto;', page)

    def test_platform_theme_and_brand_badges(self):
        page = self._page()
        # v0.1.5: card accent follows the platform, not the kind: tint +
        # 4px edge + header strip + tinted icon circle + badge.
        self.assertIn('.map-card.p-claude { background: rgba(150,66,31,0.12); border-left: 4px solid var(--claude); }', page)
        self.assertIn('.map-card.p-grok .who { background: rgba(17,94,89,0.18); margin: -10px -12px 0; padding: 10px 12px; border-radius: 8px 8px 0 0; }', page)
        self.assertIn('.map-card.p-muse .kicon { background: rgba(44,80,173,0.20); color: var(--muse); }', page)
        # Brand badges top-right: 4 Claude (archie, operations,
        # validator, support), 4 Muse, 1 Grok; deniel has none.
        self.assertEqual(page.count('class="brand brand-claude"'), 4)
        self.assertEqual(page.count('class="brand brand-muse"'), 4)
        self.assertEqual(page.count('class="brand brand-grok"'), 1)
        self.assertEqual(page.count('class="brand '), 9)
        # v0.1.7: the validator card sits in the slot status had; the
        # status card is gone.
        self.assertIn('<span class="nm">validator</span>', page)
        self.assertNotIn('<span class="nm">status</span>', page)
        self.assertIn('class="map-card p-claude"', page)
        self.assertIn('class="map-card p-grok"', page)
        # Tablet: auto-fill ~320px columns, so cards never take the whole
        # width; phone stays one column.
        self.assertIn('.map-row { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); }', page)
        self.assertIn('.map-inner { min-width: 0; }', page)

    def test_task_hash_opens_detail(self):
        page = self._page()
        # v0.1.5: #task=TSK-016 switches to the Tasks tab, scrolls to the
        # row and opens its detail; the old #tasks/<id> form still parses.
        self.assertIn("if (h.indexOf('task=') === 0) return { tab: 'tasks', task: h.slice(5) };", page)
        self.assertIn("if (tab === 'tasks' && task) { selectTask(task); scrollToTask(task); }", page)
        self.assertIn('tr.scrollIntoView({ block: \'nearest\' })', page)
        self.assertIn("var parts = h.split('/');", page)

    def test_platform_badge_contrast(self):
        # v0.1.5: every badge letter is 4.5:1 or better on its tint, in
        # light mode (white surface) and dark mode (#161f28 surface).
        # Colours are read out of the rendered page, not restated here.
        page = self._page()
        for brand in ('claude', 'muse', 'grok'):
            m = re.search(
                r'\.brand-%s \{ background: rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\); '
                r'color: var\(--(\w+)\); \}' % brand, page)
            self.assertIsNotNone(m, brand)
            r, g, b, alpha, var = int(m.group(1)), int(m.group(2)), int(m.group(3)), float(m.group(4)), m.group(5)
            light = re.search(r'--%s: (#[0-9a-f]{6});' % var, page).group(1)
            dark_part = page.split('prefers-color-scheme: dark')[1]
            dark = re.search(r'--%s: (#[0-9a-f]{6});' % var, dark_part).group(1)
            for surface, text in (((255, 255, 255), light), ((22, 31, 40), dark)):
                bg = tuple(round(alpha * c + (1 - alpha) * s) for c, s in zip((r, g, b), surface))
                bg_hex = '#%02x%02x%02x' % bg
                ratio = _contrast(text, bg_hex)
                self.assertGreaterEqual(ratio, 4.5, '%s %s on %s: %.2f' % (brand, text, bg_hex, ratio))
        # Grok is graphite-teal, clearly different from Muse blue, not black.
        grok = re.search(r'--grok: (#[0-9a-f]{6});', page).group(1)
        muse = re.search(r'--muse: (#[0-9a-f]{6});', page).group(1)
        self.assertEqual(grok, '#115e59')
        self.assertNotEqual(grok, muse)
        self.assertGreater(_lum(grok), 0.05)

    def test_what_changed_panel_uses_the_status_key(self):
        page = self._page()
        self.assertIn('statusmap.seen.v1', page)
        self.assertIn('id="mark-seen"', page)
        self.assertIn('id="chg-lede"', page)

    def test_poll_reload_keeps_scroll_and_tab(self):
        page = self._page()
        # QC round 2, item 1: a reload on a new feed must not lose the
        # scroll position or the open detail. The tab hash survives a
        # reload in the URL; the scroll position is saved in
        # sessionStorage (in try/catch) and restored after the reload.
        self.assertIn('reloadKeepingPlace', page)
        self.assertIn("var SCROLL_KEY = 'board.scrollY'", page)
        self.assertIn('sessionStorage.setItem(SCROLL_KEY', page)
        self.assertIn('sessionStorage.getItem(SCROLL_KEY', page)
        self.assertIn('window.scrollTo(0, parseInt(y, 10)', page)
        self.assertIn('restoreScroll();', page)

    def test_status_json_comment_opener_is_escaped(self):
        # QC round 2, item 2: a stray '<!--' in a status note must not
        # reach the page raw (it could swallow the rest as a comment),
        # and the embedded JSON must still parse back to the same value.
        feed = load_fixture('feed-basic.json')
        feed['status_json']['attention'][0] = (
            'note <!-- <script>alert(1)</script> tail')
        page = self._page(seed=feed)
        block = page.split(
            '<script type="application/json" id="status-json">')[1]
        block = block.split('</script>')[0]
        self.assertNotIn('<!--', block)
        # The closing tag is escaped, not raw: only '</script' can end a
        # script block, and the opening tag is harmless text inside JSON.
        self.assertIn('<\\/script>', block)
        self.assertNotIn('</script', block)
        parsed = json.loads(block)
        self.assertEqual(
            parsed['attention'][0],
            'note <!-- <script>alert(1)</script> tail')
        # The same note in the page markup is HTML-escaped, not a tag.
        self.assertIn('&lt;script&gt;alert', page)

    def test_feed_tab_is_capped_at_50(self):
        feed = load_fixture('feed-basic.json')
        for i in range(60):
            feed['reports'].append(
                {'time': '2026-11-19T09:%02d:00+07:00' % (i % 60),
                 'from': 'Ops', 'to': 'Deniel', 'type': 'REPORT',
                 'topic': 'bulk-%02d' % i})
        page = self._page(seed=feed)
        feed_section = page.split('id="tab-feed"')[1]
        rows = re.findall(r'<tr[^>]*><td>', feed_section)
        self.assertEqual(len(rows), 50)

    def test_empty_feed_renders_without_breaking(self):
        page = self._page(seed=load_fixture('feed-empty.json'))
        self.assertIn('Agent Activity Board', page)
        self.assertIn('No status data in this feed', page)
        self.assertIn('No tasks in this feed.', page)
        self.assertIn('No events in this feed.', page)


class DoingLabelTests(unittest.TestCase):
    """doing_label: the card label follows the ball, not the owner."""

    def _feed(self, agents=None, threads=None):
        return {'agents': agents or [], 'threads': threads or []}

    def _task(self, tid, **kw):
        task = {'id': tid, 'title': 'Test task', 'owner': 'Muse-Ping',
                'state': 'in_progress', 'stage': 'test'}
        task.update(kw)
        return task

    def test_owner_fallback_is_gone(self):
        # v0.1.7: owning an in-progress task, with no thread, no
        # doing_task and no ball_with, shows nothing.
        feed = self._feed()
        tasks = {'TSK-015': self._task('TSK-015')}
        self.assertIsNone(R.doing_label('muse-ping', feed, tasks))

    def test_ball_with_places_the_label_on_the_ball_holder(self):
        # v0.1.7: ball_with names the card, not the owner.
        feed = self._feed()
        tasks = {'TSK-015': self._task('TSK-015', ball_with='deniel')}
        self.assertIsNone(R.doing_label('muse-ping', feed, tasks))
        self.assertEqual(R.doing_label('deniel', feed, tasks),
                         'TSK-015 Test task')

    def test_ball_with_absent_matches_nothing(self):
        # v0.1.7 / Archie's timing rule: until Ops fills ball_with, a
        # task without it must give the same result as a feed with no
        # ball_with field at all — no label from the new step, for any
        # agent.
        feed = self._feed()
        tasks = {'TSK-015': self._task('TSK-015')}
        self.assertIsNone(R.doing_label('deniel', feed, tasks))
        self.assertIsNone(R.doing_label('archie', feed, tasks))
        self.assertIsNone(R.doing_label('muse-ping', feed, tasks))

    def test_closed_task_never_shows(self):
        # v0.1.7: state done or stage done suppresses the label at every
        # step, including doing_task.
        feed = self._feed(
            agents=[{'id': 'muse-ping', 'doing_task': 'TSK-001'}])
        done_state = {'TSK-001': self._task('TSK-001', state='done')}
        self.assertIsNone(R.doing_label('muse-ping', feed, done_state))
        done_stage = {'TSK-001': self._task('TSK-001', stage='done')}
        self.assertIsNone(R.doing_label('muse-ping', feed, done_stage))
        # A live task still shows via doing_task.
        live = {'TSK-015': self._task('TSK-015')}
        feed2 = self._feed(
            agents=[{'id': 'muse-ping', 'doing_task': 'TSK-015'}])
        self.assertEqual(R.doing_label('muse-ping', feed2, live),
                         'TSK-015 Test task')

    def test_answered_thread_does_not_place_the_label(self):
        # v0.1.7: the thread step takes open threads only.
        thread = {'ball_with': 'muse-kb', 'task': 'TSK-012',
                  'state': 'ANSWERED',
                  'since': '2026-11-19T09:10:00+07:00'}
        feed = self._feed(threads=[thread])
        tasks = {'TSK-012': self._task('TSK-012', owner='Muse-KB')}
        self.assertIsNone(R.doing_label('muse-kb', feed, tasks))
        feed2 = self._feed(threads=[dict(thread, state='WAITING')])
        self.assertEqual(R.doing_label('muse-kb', feed2, tasks),
                         'TSK-012 Test task')


class NeedDenielTests(unittest.TestCase):
    """need_deniel_items: only open things count, plain labels, full text."""

    def _thread(self, task, state, actions, slug='some-slug'):
        return {'task': task, 'state': state, 'slug': slug,
                'messages': [{'action': a, 'slug': slug, 'msg_id': 'm-%s' % a}
                             for a in actions]}

    def _task(self, tid, ball_with=None, state='in_progress',
              title='Test task'):
        t = {'id': tid, 'title': title, 'state': state}
        if ball_with is not None:
            t['ball_with'] = ball_with
        return t

    def test_closed_and_answered_decision_threads_are_excluded(self):
        feed = {'human_inbox': [],
                'threads': [self._thread('TSK-001', 'CLOSED', ['decision'], 'closed-one'),
                            self._thread('TSK-002', 'ANSWERED', ['halt'], 'answered-one'),
                            self._thread('TSK-003', 'WAITING', ['decision'], 'open-one')]}
        items = R.need_deniel_items(feed, {}, {})
        self.assertEqual(items, [('Decision', 'open-one', 'TSK-003')])

    def test_human_inbox_labels_by_action(self):
        feed = {'human_inbox': [{'action': 'decision', 'slug': 'decide-this', 'task': 'TSK-001'},
                                {'action': 'message', 'slug': 'read-this', 'task': ''}],
                'threads': []}
        items = R.need_deniel_items(feed, {}, {})
        self.assertEqual(items, [('Decision', 'decide-this', 'TSK-001'),
                                 ('Waiting on you', 'read-this', '')])

    def test_ball_with_deniel_task_counts_when_open(self):
        tasks = {'TSK-001': self._task('TSK-001', ball_with='deniel'),
                 'TSK-002': self._task('TSK-002', ball_with='deniel', state='done'),
                 'TSK-003': self._task('TSK-003', ball_with='muse-ping')}
        items = R.need_deniel_items({'human_inbox': [], 'threads': []}, tasks, {})
        self.assertEqual(items, [('Waiting on you', 'Test task', 'TSK-001')])

    def test_attention_lines_are_full_text(self):
        long_line = 'A very long attention line ' * 10
        items = R.need_deniel_items({'human_inbox': [], 'threads': []}, {},
                                    {'attention': [long_line]})
        self.assertEqual(items, [('Attention', long_line, '')])
        self.assertNotIn('…', items[0][1])


if __name__ == '__main__':
    unittest.main(verbosity=1)
