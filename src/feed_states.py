"""Display-state rules for the Command Center (PRD sections 5 and 7).

This is a pure reference implementation of the rules, used by the page
renderer and by the tests. The feed itself (built by Muse-Feed) already
carries a ``state`` per thread; the renderer shows the feed's state. These
functions exist so the rules can be checked against fixture feeds, and so a
feed whose thread carries no state can still be shown truthfully.

Rules, from the PRD:

- WAITING: the latest message of the thread is in an inbox; the ball is
  with that agent.
- IN PROGRESS: a ``started-by`` marker exists for the latest message and no
  reply has arrived yet. Without a marker the page NEVER says "in progress".
- ANSWERED: a later message has ``in_reply_to`` equal to the latest request
  and the thread has no open request.
- CLOSED: the message was archived and its action is ``report``, or no reply
  is expected.
- STALE: WAITING longer than the agent's threshold (session poll: more than
  25 minutes inside hours; scheduled chat: 2 missed polls inside hours).
  A manual-chat agent is NEVER stale.
- NEEDS DENIEL: any file in ``inbox/human/``, or an ``action: decision`` or
  ``action: halt`` anywhere.
- OFF HOURS: a polling agent outside its hours, so waiting is expected.
  Never red.
- Manual-chat agents show "Needs a ping" after 10 minutes waiting, never
  STALE.

All times are ISO 8601 with an offset. "Now" is passed in so tests can use a
fixed clock. Working hours are weekdays 08:00-18:50 WIB (Asia/Jakarta).
"""
from datetime import datetime, time, timedelta, timezone

WIB = timezone(timedelta(hours=7))
WORK_START = time(8, 0)
WORK_END = time(18, 50)
SESSION_STALE_MIN = 25
MANUAL_PING_MIN = 10
SCHEDULED_MISSED_POLLS = 2
POLL_MIN = {'grok': 10, 'status': 60, 'muse': 10}

THREAD_STATES = ('WAITING', 'IN_PROGRESS', 'ANSWERED', 'CLOSED', 'STALE',
                 'NEEDS_DENIEL', 'OFF_HOURS')

# Feed states the page accepts (PRD section 14 shape, v1).
FEED_THREAD_STATES = THREAD_STATES


def parse_time(value):
    """Parse an ISO 8601 time with offset; None when it cannot be parsed."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt


def minutes_between(earlier, later):
    """Whole minutes from ``earlier`` to ``later``; None when either is bad."""
    a, b = parse_time(earlier), parse_time(later)
    if a is None or b is None:
        return None
    return int((b - a).total_seconds() // 60)


def in_hours(now):
    """True when ``now`` is inside polling hours (weekday 08:00-18:50 WIB)."""
    if now is None:
        return False
    local = now.astimezone(WIB)
    return local.weekday() < 5 and WORK_START <= local.time() < WORK_END


def thread_state(thread, agent_kind, now, started_markers=()):
    """Derive the display state of one thread from the feed data.

    ``thread`` is a feed thread dict (messages in time order, latest last).
    ``agent_kind`` is one of ``session``, ``scheduled``, ``manual``,
    ``person`` (from agents.json). ``started_markers`` is the set of msg_ids
    that have a ``started-by`` marker. Returns ``(state, detail)`` where
    ``detail`` is a short human word for the display, e.g. the agent the
    ball is with or the time work started.
    """
    messages = thread.get('messages') or []
    if not messages:
        return ('CLOSED', '')
    latest = messages[-1]
    latest_action = str(latest.get('action') or '')
    ball_with = thread.get('ball_with') or latest.get('to') or ''
    since = thread.get('since') or latest.get('time') or ''
    age_min = minutes_between(since, now.isoformat() if now else None)

    # A decision or a halt anywhere in the thread needs Deniel.
    if any(str(m.get('action') or '') in ('decision', 'halt') for m in messages):
        return ('NEEDS_DENIEL', latest_action)

    # A reply (in_reply_to naming the latest request) with no open request.
    # The latest request is the last message with action "request"; a later
    # message answering it means ANSWERED even if the request file is still
    # in the inbox (the reply arrived before the request was archived).
    requests = [m for m in messages if str(m.get('action') or '') == 'request']
    target = requests[-1] if requests else messages[0]
    target_id = target.get('msg_id')
    if target_id and any(m.get('in_reply_to') == target_id
                         for m in messages[messages.index(target) + 1:]):
        return ('ANSWERED', str(messages[-1].get('from') or ''))

    # Archived with action report, or no reply expected: closed.
    if thread.get('state') == 'CLOSED' or latest_action in ('report',):
        return ('CLOSED', '')

    # In progress only with a started-by marker for the latest message.
    latest_id = latest.get('msg_id')
    if latest_id and latest_id in started_markers:
        return ('IN_PROGRESS', since)

    # Still waiting. Which word depends on the agent's kind and the clock.
    if agent_kind in ('session', 'scheduled'):
        if not in_hours(now):
            return ('OFF_HOURS', ball_with)
        threshold = SESSION_STALE_MIN
        if agent_kind == 'scheduled':
            threshold = POLL_MIN.get(ball_with, 10) * SCHEDULED_MISSED_POLLS
        if age_min is not None and age_min > threshold:
            return ('STALE', ball_with)
        return ('WAITING', ball_with)

    # Manual-chat agents and people are never stale.
    return ('WAITING', ball_with)


def needs_ping(agent_kind, age_min):
    """True when a manual-chat agent has waited long enough to need a ping."""
    return (agent_kind == 'manual' and age_min is not None
            and age_min > MANUAL_PING_MIN)


def agent_card_state(agent, feed_agents, threads, now, started_markers=()):
    """The state word for one agent's Live map card.

    Returns ``(word, detail)``: ``word`` is one of ``working``,
    ``waiting``, ``idle``, ``needs a ping``, ``stale``, ``off hours``,
    ``not created``. ``detail`` carries the waiting age or the task id.
    """
    if not agent.get('created', True):
        return ('not created', '')
    info = feed_agents.get(agent['id'], {}) if isinstance(feed_agents, dict) else {}
    open_threads = [t for t in threads
                    if t.get('ball_with') == agent['id']
                    and t.get('state') not in ('CLOSED', 'ANSWERED')]
    for t in open_threads:
        state, _ = thread_state(t, agent.get('kind'), now, started_markers)
        if state == 'IN_PROGRESS':
            return ('working', t.get('task') or '')
        if state == 'STALE':
            return ('stale', t.get('task') or '')
        if state == 'OFF_HOURS':
            return ('off hours', t.get('task') or '')
    if open_threads:
        oldest = max((minutes_between(t.get('since'), now.isoformat() if now else None)
                      or 0) for t in open_threads)
        if needs_ping(agent.get('kind'), oldest):
            return ('needs a ping', '%d min' % oldest)
        return ('waiting', '%d min' % oldest)
    inbox_count = info.get('inbox_count') or 0
    if inbox_count:
        oldest = info.get('oldest_wait_min') or 0
        if needs_ping(agent.get('kind'), oldest):
            return ('needs a ping', '%d min' % oldest)
        return ('waiting', '%d min' % oldest)
    return ('idle', '')


def feed_is_stale(generated_at, now, limit_min=5):
    """True when the feed is older than ``limit_min`` minutes (PRD: 5)."""
    age = minutes_between(generated_at, now.isoformat() if now else None)
    if age is None:
        return True
    return age > limit_min
