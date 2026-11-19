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
        self.assertIn('href="#tasks/TSK-015"', page)
        # An agent with no known task gets a non-clickable "nothing known".
        self.assertIn('nothing known', page)
        self.assertNotIn('href="#tasks/"', page)
        # Label text never comes from message text: the slug of the "Other
        # work" thread must not appear as a map label (it may appear in the
        # Tasks timeline, which is where slugs belong).
        map_section = page.split('id="tab-map"')[1].split('id="tab-arch"')[0]
        self.assertNotIn('check-a-report', map_section)

    def test_not_created_agents_are_dashed_not_errors(self):
        page = self._page()
        self.assertIn('not created', page)
        self.assertIn('map-card not-created', page)

    def test_delegation_arrow_is_labelled(self):
        page = self._page()
        self.assertIn('Delegation: Archie delegates to Operations', page)

    def test_delivery_rows_link_to_tasks(self):
        page = self._page()
        self.assertIn('<a href="#tasks/TSK-012">TSK-012</a>', page)
        self.assertIn('<a href="#tasks/TSK-015">TSK-015</a>', page)
        self.assertIn('Close the KB loop', page)

    def test_task_rows_are_keyboard_selectable(self):
        page = self._page()
        self.assertIn('tr data-task="TSK-015" tabindex="0"', page)
        self.assertIn('data-task-detail="TSK-015"', page)
        # Stage boxes: the current stage is filled.
        self.assertIn('<span class="on">Test</span>', page)
        self.assertIn('<span class="on">Import prep</span>', page)

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

    def test_design_a_kind_colours_and_brand_badges(self):
        page = self._page()
        # v0.1.4 Design A: kind tint + 4px edge, stronger than the mock.
        self.assertIn('.map-card.k-session { background: rgba(22,111,67,0.15); border-left: 4px solid var(--live); }', page)
        # Kind icon in a tinted circle, coloured, not muted grey.
        self.assertIn('.map-card.k-person .kicon { background: rgba(44,80,173,0.20); color: var(--built); }', page)
        # Brand badges top-right: 2 Claude, 4 Muse, 1 Grok; none for
        # deniel, status or support.
        self.assertEqual(page.count('class="brand brand-claude"'), 2)
        self.assertEqual(page.count('class="brand brand-muse"'), 4)
        self.assertEqual(page.count('class="brand brand-grok"'), 1)
        self.assertEqual(page.count('class="brand '), 7)
        # Tablet: two cards per row, no sideways scroll; phone: one.
        self.assertIn('.map-row { display: grid; grid-template-columns: 1fr 1fr; }', page)
        self.assertIn('.map-inner { min-width: 0; }', page)

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
        rows = re.findall(r'<tr><td>', feed_section)
        self.assertEqual(len(rows), 50)

    def test_empty_feed_renders_without_breaking(self):
        page = self._page(seed=load_fixture('feed-empty.json'))
        self.assertIn('Agent Activity Board', page)
        self.assertIn('No status data in this feed', page)
        self.assertIn('No open threads in this feed.', page)
        self.assertIn('No events in this feed.', page)


if __name__ == '__main__':
    unittest.main(verbosity=1)
