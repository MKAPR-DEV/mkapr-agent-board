"""Tests for the state rules (PRD sections 5 and 7) and the page's display
of them. Covers criteria 4, 5, 12, 13 and 14.

Run:  python3 -m unittest discover -s tests -v   (from the workdir root)
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers as H
from helpers import load_fixture
sys.path.insert(0, os.path.join(H.ROOT, 'src'))
from feed_states import agent_card_state, feed_is_stale, in_hours, thread_state

NOW = datetime(2026, 11, 19, 10, 0, tzinfo=timezone(timedelta(hours=7)))
NOW_ISO = NOW.isoformat()
AFTER_HOURS = datetime(2026, 11, 19, 20, 0, tzinfo=timezone(timedelta(hours=7)))


def thread(**kw):
    base = {'thread_id': 't1', 'task': 'TSK-015', 'slug': 's',
            'state': 'WAITING', 'ball_with': 'muse-ping',
            'where': 'inbox/muse/', 'since': '2026-11-19T09:50:00+07:00',
            'latest_action': 'request',
            'messages': [{'time': '2026-11-19T09:50:00+07:00', 'from': 'archie',
                          'to': 'muse-ping', 'action': 'request', 'slug': 's',
                          'msg_id': 'm1'}]}
    base.update(kw)
    return base


class StateRuleTests(unittest.TestCase):
    def test_waiting_for_a_manual_agent_is_never_stale(self):
        t = thread(since='2026-11-19T08:00:00+07:00')  # 2 hours waiting
        state, _ = thread_state(t, 'manual', NOW)
        self.assertEqual(state, 'WAITING')

    def test_manual_agent_needs_a_ping_after_10_minutes(self):
        feed = load_fixture('feed-criterion12.json')
        threads = feed['threads']
        muse_kb = next(a for a in feed['agents'] if a['id'] == 'muse-kb')
        feed_agents = {a['id']: a for a in feed['agents']}
        word, detail = agent_card_state(muse_kb, feed_agents, threads, NOW)
        self.assertEqual(word, 'needs a ping')
        self.assertEqual(detail, '80 min')  # oldest open thread: 08:40

    def test_session_agent_stale_after_25_minutes_inside_hours(self):
        t = thread(ball_with='operations', since='2026-11-19T09:30:00+07:00')
        state, _ = thread_state(t, 'session', NOW)
        self.assertEqual(state, 'STALE')

    def test_session_agent_not_stale_within_25_minutes(self):
        t = thread(ball_with='operations', since='2026-11-19T09:40:00+07:00')
        state, _ = thread_state(t, 'session', NOW)
        self.assertEqual(state, 'WAITING')

    def test_polling_agent_outside_hours_is_off_hours_never_red(self):
        t = thread(ball_with='operations', since='2026-11-19T09:30:00+07:00')
        state, _ = thread_state(t, 'session', AFTER_HOURS)
        self.assertEqual(state, 'OFF_HOURS')
        self.assertFalse(in_hours(AFTER_HOURS))
        self.assertTrue(in_hours(NOW))

    def test_scheduled_agent_stale_after_two_missed_polls(self):
        # Grok polls every 10 min: stale after 20 min.
        t = thread(ball_with='grok', since='2026-11-19T09:35:00+07:00')
        state, _ = thread_state(t, 'scheduled', NOW)
        self.assertEqual(state, 'STALE')
        t = thread(ball_with='grok', since='2026-11-19T09:45:00+07:00')
        state, _ = thread_state(t, 'scheduled', NOW)
        self.assertEqual(state, 'WAITING')
        # Status polls hourly: stale after 2 hours.
        t = thread(ball_with='status', since='2026-11-19T07:30:00+07:00')
        state, _ = thread_state(t, 'scheduled', NOW)
        self.assertEqual(state, 'STALE')

    def test_started_marker_turns_waiting_into_in_progress(self):
        t = thread(ball_with='muse-page', since='2026-11-19T09:10:00+07:00')
        state, _ = thread_state(t, 'manual', NOW, started_markers=())
        self.assertEqual(state, 'WAITING')
        state, detail = thread_state(t, 'manual', NOW, started_markers={'m1'})
        self.assertEqual(state, 'IN_PROGRESS')
        self.assertEqual(detail, '2026-11-19T09:10:00+07:00')

    def test_reply_means_answered(self):
        req = '20261119-091500_archie-to-muse-kb_tsk-012-early-reply'
        t = thread(messages=[
            {'time': '2026-11-19T09:15:00+07:00', 'from': 'archie',
             'to': 'muse-kb', 'action': 'request', 'slug': 's', 'msg_id': req},
            {'time': '2026-11-19T09:18:00+07:00', 'from': 'muse-kb',
             'to': 'archie', 'action': 'reply', 'slug': 's', 'msg_id': 'r1',
             'in_reply_to': req}])
        state, _ = thread_state(t, 'manual', NOW)
        self.assertEqual(state, 'ANSWERED')

    def test_three_hop_thread_is_tracked(self):
        feed = load_fixture('feed-criterion12.json')
        t = next(x for x in feed['threads'] if x['slug'] == 'tsk-012-three-hops')
        self.assertEqual(len(t['messages']), 3)
        state, _ = thread_state(t, 'manual', NOW)
        self.assertEqual(state, 'WAITING')

    def test_halt_needs_deniel(self):
        feed = load_fixture('feed-criterion12.json')
        t = next(x for x in feed['threads'] if x['slug'] == 'tsk-015-halt')
        state, _ = thread_state(t, 'session', NOW)
        self.assertEqual(state, 'NEEDS_DENIEL')

    def test_feed_stale_after_five_minutes(self):
        self.assertFalse(feed_is_stale('2026-11-19T09:58:00+07:00', NOW))
        self.assertTrue(feed_is_stale('2026-11-19T09:00:00+07:00', NOW))
        self.assertTrue(feed_is_stale(None, NOW))
        self.assertTrue(feed_is_stale('garbage', NOW))


class RenderedStateTests(unittest.TestCase):
    """The page shows the states, and never says 'in progress' without a
    started marker (criteria 4, 5, 13, 14)."""

    def setUp(self):
        # The fixture was generated at 09:58; refresh generated_at so the
        # feed is 2 minutes old at render time (no stale banner). Thread
        # times stay as written; the state words still hold at real now.
        feed = load_fixture('feed-criterion12.json')
        wib = timezone(timedelta(hours=7))
        feed['generated_at'] = (
            datetime.now(wib) - timedelta(minutes=2)).isoformat(
                timespec='seconds')
        self.srv = H.Server(seed=feed)

    def tearDown(self):
        self.srv.stop()

    def _page(self):
        _, _, body = self.srv.get('/')
        return body.decode('utf-8')

    def test_stale_banner_and_needs_deniel_strip(self):
        # The criterion-12 feed is 2 minutes old: the stale banner is
        # present in the page but hidden; the halt, the human-inbox
        # decision and the attention line must show in the strip
        # (3 items: human inbox + halt + attention).
        page = self._page()
        self.assertIn('3 items need Deniel', page)
        self.assertIn('tsk-012-deploy-or-hold', page)
        self.assertIn('tsk-015-halt', page)
        self.assertIn('<div class="banner red" id="feed-banner" role="alert" hidden>',
                      page)

    def test_stale_feed_shows_the_red_banner(self):
        srv = H.Server(seed=load_fixture('feed-stale.json'))
        try:
            _, _, body = srv.get('/')
            page = body.decode('utf-8')
            self.assertIn('<div class="banner red" id="feed-banner" role="alert">',
                          page)
            self.assertIn('What you see is old', page)
        finally:
            srv.stop()

    def test_in_progress_only_with_a_started_marker(self):
        page = self._page()
        # The IN_PROGRESS thread (TSK-016, muse-page) shows as working.
        self.assertIn('working since 09:10', page)
        # With a started marker present, the "not recorded" note is gone.
        self.assertNotIn('In progress is not recorded', page)
        # The WAITING threads never say "in progress" themselves.
        self.assertNotIn('muse-ping working', page)

    def test_manual_agent_never_stale_shows_needs_a_ping(self):
        page = self._page()
        self.assertIn('needs a ping', page)
        # No "Stale" for the manual agents; the stale one is Operations.
        self.assertIn('Stale, check the agent', page)

    def test_unreadable_messages_are_shown_not_hidden(self):
        page = self._page()
        self.assertIn('Unreadable messages', page)
        self.assertIn('20261119-090000_muse-ping-to-ops_bad-header.md', page)
        self.assertIn('hop_count is 0', page)

    def test_task_detail_shows_stage_summary_waiting_on(self):
        page = self._page()
        self.assertIn('TSK-012', page)
        self.assertIn('Import prep', page)
        self.assertIn('Check that the new KB code can go to the Consolidator host.', page)
        self.assertIn('Archie', page)  # waiting_on text (first words)
        # The Later task has no stage/summary/waiting_on: "not recorded".
        self.assertIn('not recorded', page)

    def test_no_progress_without_markers_in_basic_feed(self):
        srv = H.Server(seed=load_fixture('feed-basic.json'))
        try:
            _, _, body = srv.get('/')
            page = body.decode('utf-8')
            self.assertNotIn('working since', page)
            self.assertIn('In progress is not recorded', page)
        finally:
            srv.stop()


if __name__ == '__main__':
    unittest.main(verbosity=1)
