"""Board service: keeps the latest feed and serves the Command Center page.

Endpoints (PRD section 10):

- ``GET /``           the six-tab page, rendered from the kept feed.
- ``GET /feed.json``  the kept feed as JSON (status filled from the last good
                      copy when the latest feed carried none).
- ``GET /health``     ``{"status":"ok"}``, no auth, no sensitive data.
- ``POST /ingest``    the one write path. Needs the ``X-Feed-Token`` header
                      (the token Muse-Feed sends; Deniel sets it at install).
                      Accepts only JSON matching feed shape v1 (PRD section
                      14), at most 1 MB. Anything else is rejected and the
                      last good feed stays.

Any other method returns 405. Any other path returns 404.

With ``--lan-bind ADDR`` (or ``BOARD_LAN_BIND``), the service also
listens on that LAN address, in the same process, sharing the same feed
state. The LAN listener is read-only: it serves only ``GET``/``HEAD`` on
``/``, ``/feed.json`` and ``/health`` (same page password on ``/`` and
``/feed.json``, ``/health`` unauthenticated). Everything else, including
``/ingest`` with any method, returns 404 there and never reaches the feed
token code. The LAN address must be a single IPv4 address, never
``0.0.0.0``; an empty or unset value means no LAN listener. If the LAN
address cannot bind, the service fails to start (no silent fallback).
``--dev`` with a non-loopback LAN address is refused, like ``--bind``.

Cache: ``/`` and ``/feed.json`` send ``Cache-Control: no-store``, so a
reverse proxy must not cache a logged-in page.

Auth, all set at install time, never in the repo:

- ``BOARD_PAGE_PASSWORD_SHA256``: hex SHA-256 of the shared page password.
  ``GET /`` and ``GET /feed.json`` check it (HTTP Basic auth, any username).
- ``BOARD_FEED_TOKEN``: the feed token, sent as the ``X-Feed-Token`` header.
- ``BOARD_BIND`` (or ``--bind``): the listen address. Required. The service
  refuses to start without one and refuses ``0.0.0.0`` and ``::`` (and any
  other unspecified address), because the host has no firewall (PRD
  section 10, criterion 10). The real address is set at install time from
  the host and is never written into the repo.
- ``BOARD_LAN_BIND`` (or ``--lan-bind``): the second, read-only listen
  address, a single IPv4 address. The command-line argument wins when
  both are set; empty or unset means no LAN listener.

``--dev`` runs with no auth, for local fixture runs only. Never use it on
the server.

There is no login throttling or lockout, and nothing keys on the client
IP: every page request is one SHA-256 compare, so putting the service
behind a reverse proxy (PRX01) changes nothing about login behaviour.
If a lockout is ever added, it must key on ``X-Forwarded-For`` only from
a trusted proxy address, never on the connecting address alone.

The service is a dumb viewer: it parses no OneDrive data itself, sends
nothing out, and never logs a feed body, a password or a token. Ingest
normalises the feed to the known shape v1 fields and drops anything else,
so a stray body field can never reach the page, the saved file or the log.

Works on Python 3.8 or newer. Standard library only.
"""
import argparse
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from board_render import render_page  # noqa: E402

VERSION = '0.1.8'
MAX_FEED_BYTES = 1024 * 1024  # 1 MB; a 10 MB body is rejected (criterion 11)
SOCKET_TIMEOUT = 30  # seconds; a stalled client must not hold a thread
WIB = timezone(timedelta(hours=7))

CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
       "img-src data:; connect-src 'self'; base-uri 'none'; form-action 'none'; "
       "frame-ancestors 'none'")

# Feed shape v1 (PRD section 14): the only fields the service keeps.
TOP_FIELDS = ('version', 'generated_at', 'agents', 'threads', 'reports',
              'human_inbox', 'unreadable', 'status_json')
AGENT_FIELDS = ('id', 'kind', 'last_seen', 'inbox_count', 'oldest_wait_min',
                'doing_task')
THREAD_FIELDS = ('thread_id', 'task', 'slug', 'state', 'ball_with', 'where',
                 'since', 'latest_action', 'messages')
MESSAGE_FIELDS = ('time', 'from', 'to', 'action', 'slug', 'msg_id',
                  'in_reply_to')
REPORT_FIELDS = ('time', 'from', 'to', 'type', 'topic')
HUMAN_FIELDS = ('time', 'from', 'action', 'slug', 'task')
UNREADABLE_FIELDS = ('file', 'reason')
LIST_FIELDS = {'agents': AGENT_FIELDS, 'threads': THREAD_FIELDS,
               'reports': REPORT_FIELDS, 'human_inbox': HUMAN_FIELDS,
               'unreadable': UNREADABLE_FIELDS}


class FeedError(ValueError):
    """The feed does not match shape v1."""


def validate_bind(address):
    """The bind address: required, an IP literal, never unspecified.

    Returns the address. Raises ValueError when it is missing, not an IP
    address, or the unspecified address (0.0.0.0 or ::).
    """
    if address is None or str(address).strip() == '':
        raise ValueError('no bind address given: pass --bind or set BOARD_BIND')
    text = str(address).strip()
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        raise ValueError('bind address %r is not an IP address' % text)
    if ip.is_unspecified:
        raise ValueError('bind address %r is not allowed: the service must '
                         'listen on one explicit address, never all interfaces'
                         % text)
    return text


def is_loopback(address):
    """True when the address is a loopback address (127.0.0.0/8 or ::1)."""
    return ipaddress.ip_address(address).is_loopback


def validate_lan_bind(address):
    """The LAN bind address: empty means no LAN listener.

    Returns the address, or None when it is unset or empty. Raises
    ValueError when it is not a single IPv4 address (a hostname, an IPv6
    address, a CIDR, a port suffix, or a bare colon are all rejected), or
    when it is 0.0.0.0.
    """
    if address is None or str(address).strip() == '':
        return None
    text = str(address).strip()
    try:
        ip = ipaddress.IPv4Address(text)
    except ValueError:
        raise ValueError('LAN bind address %r is not a single IPv4 address: '
                         'give one address, no port, no hostname, no CIDR'
                         % text)
    if ip.is_unspecified:
        raise ValueError('LAN bind address %r is not allowed: the service '
                         'must listen on one explicit address, never all '
                         'interfaces' % text)
    return text


def parse_time(value):
    """Parse an ISO 8601 time with offset; None when it cannot be parsed."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else None


def _pick(item, fields):
    """A dict kept to the known fields only. Returns (kept, dropped_count)."""
    kept, dropped = {}, 0
    for key, value in item.items():
        if key in fields:
            kept[key] = value
        else:
            dropped += 1
    return kept, dropped


def validate_feed(raw):
    """Validate and normalise a feed to shape v1.

    Returns ``(feed, dropped)``: the normalised feed and how many unknown
    fields were dropped. Raises FeedError when the feed does not match.
    Unknown fields are dropped (never stored, logged or shown), so a stray
    message-body field can never leak into the page or the saved file.
    """
    if not isinstance(raw, dict):
        raise FeedError('feed must be a JSON object')
    if raw.get('version') != 1:
        raise FeedError('feed version must be 1')
    if parse_time(raw.get('generated_at')) is None:
        raise FeedError('generated_at must be ISO 8601 with a time zone')
    feed, dropped = _pick(raw, TOP_FIELDS)
    for key in ('agents', 'threads', 'reports', 'human_inbox', 'unreadable'):
        items = feed.get(key)
        if items is None:
            feed[key] = []
            continue
        if not isinstance(items, list):
            raise FeedError('%s must be a list' % key)
        fields = LIST_FIELDS[key]
        clean = []
        for item in items:
            if not isinstance(item, dict):
                raise FeedError('%s entries must be objects' % key)
            kept, n = _pick(item, fields)
            dropped += n
            if key == 'threads':
                msgs = kept.get('messages')
                if msgs is None:
                    kept['messages'] = []
                elif not isinstance(msgs, list):
                    raise FeedError('thread messages must be a list')
                else:
                    clean_msgs = []
                    for msg in msgs:
                        if not isinstance(msg, dict):
                            raise FeedError('messages must be objects')
                        mkept, mn = _pick(msg, MESSAGE_FIELDS)
                        dropped += mn
                        clean_msgs.append(mkept)
                    kept['messages'] = clean_msgs
            clean.append(kept)
        feed[key] = clean
    for agent in feed['agents']:
        if not isinstance(agent.get('id'), str) or not agent['id']:
            raise FeedError('every agent needs a text id')
    for thread in feed['threads']:
        if not isinstance(thread.get('thread_id'), str) or not thread['thread_id']:
            raise FeedError('every thread needs a text thread_id')
    status = feed.get('status_json')
    if status is None:
        feed['status_json'] = None
    elif not isinstance(status, dict):
        raise FeedError('status_json must be an object or null')
    return feed, dropped


class FeedStore(object):
    """The kept feed on disk: one file, plus the last good status copy."""

    def __init__(self, data_dir):
        self.data_dir = os.path.abspath(data_dir)
        self.feed_path = os.path.join(self.data_dir, 'latest-feed.json')
        self.status_path = os.path.join(self.data_dir, 'last-status.json')
        self.lock = threading.Lock()
        self.feed = None
        self.last_status = None
        self.last_status_at = None
        self._load()

    def _load(self):
        try:
            with open(self.feed_path, encoding='utf-8') as f:
                feed, _ = validate_feed(json.load(f))
            self.feed = feed
        except (OSError, ValueError):
            self.feed = None
        try:
            with open(self.status_path, encoding='utf-8') as f:
                saved = json.load(f)
            if isinstance(saved, dict) and isinstance(saved.get('status_json'), dict):
                self.last_status = saved['status_json']
                self.last_status_at = saved.get('generated_at')
        except (OSError, ValueError):
            self.last_status = None
            self.last_status_at = None

    def _atomic_write(self, path, text):
        os.makedirs(self.data_dir, exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(text)
        os.replace(tmp, path)

    def save(self, feed):
        """Store a validated feed; remember its status copy when present."""
        with self.lock:
            self._atomic_write(self.feed_path,
                               json.dumps(feed, ensure_ascii=False))
            self.feed = feed
            status = feed.get('status_json')
            if isinstance(status, dict) and status:
                self.last_status = status
                self.last_status_at = feed.get('generated_at')
                self._atomic_write(self.status_path, json.dumps(
                    {'status_json': status,
                     'generated_at': feed.get('generated_at')},
                    ensure_ascii=False))

    def effective_feed(self):
        """The feed to serve: status filled from the last good copy when the
        latest feed carried none (PRD criterion 19)."""
        with self.lock:
            if self.feed is None:
                return {'version': 1, 'generated_at': None, 'agents': [],
                        'threads': [], 'reports': [], 'human_inbox': [],
                        'unreadable': [], 'status_json': self.last_status}
            feed = dict(self.feed)
            if feed.get('status_json') is None and self.last_status is not None:
                feed['status_json'] = self.last_status
            return feed

    def last_good(self):
        with self.lock:
            return self.last_status, self.last_status_at


def check_page_password(handler, password_sha256):
    """HTTP Basic auth against the configured password hash.

    Any username is accepted; only the password matters. Returns True when
    the password matches."""
    if password_sha256 is None:
        return True
    auth = handler.headers.get('Authorization') or ''
    if not auth.startswith('Basic '):
        return False
    try:
        decoded = base64.b64decode(auth[6:], validate=True).decode('utf-8', 'replace')
    except (ValueError, UnicodeDecodeError):
        return False
    _user, sep, password = decoded.partition(':')
    if not sep:
        return False
    digest = hashlib.sha256(password.encode('utf-8')).hexdigest()
    return hmac.compare_digest(digest, password_sha256)


def check_feed_token(handler, feed_token):
    """The X-Feed-Token header, compared in constant time. No token
    configured (--dev mode) means no auth, like the page password."""
    if feed_token is None:
        return True
    token = handler.headers.get('X-Feed-Token') or ''
    return hmac.compare_digest(token, feed_token)


def make_handler(store, agents, password_sha256, feed_token, read_only=False):
    """Build the request handler. ``password_sha256`` / ``feed_token`` are
    None in --dev mode (no auth), which must never be used on the server.

    ``read_only`` builds the LAN listener: only GET/HEAD on /, /feed.json
    and /health; everything else returns 404 before any auth or token code
    runs, so /ingest can never be reached through it."""

    class Handler(BaseHTTPRequestHandler):
        server_version = 'AgentBoard/%s' % VERSION
        sys_version = ''
        # A stalled client must not hold a worker thread forever: the
        # stdlib sets this read timeout on the connection in setup().
        timeout = SOCKET_TIMEOUT

        def log_message(self, fmt, *args):
            # Log method, path and status only. Never a body or a header:
            # a header could carry a password or a token.
            sys.stderr.write('%s %s\n' % (self.command,
                                           self.path.split('?', 1)[0][:120]))

        def _reply(self, code, body=b'', ctype='text/plain; charset=utf-8',
                   cache='no-store', head=False, extra=None):
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', cache)
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            if extra:
                for key, value in extra.items():
                    self.send_header(key, value)
            if ctype.startswith('text/html'):
                self.send_header('Content-Security-Policy', CSP)
            self.end_headers()
            if not head and code != 204:
                self.wfile.write(body)

        def _unauthorised(self):
            self._reply(401, b'Password required.\n',
                        extra={'WWW-Authenticate': 'Basic realm="Agent Activity Board"'})

        def _serve_page(self, head=False):
            if not check_page_password(self, password_sha256):
                return self._unauthorised()
            # The raw stored feed: render_page does the last-good status
            # merge itself, so it can say "(last good copy)" when it does.
            with store.lock:
                raw = dict(store.feed) if store.feed is not None else None
            last_status, last_at = store.last_good()
            html = render_page(raw or {}, agents,
                               last_good_status=last_status,
                               last_good_at=last_at,
                               now=datetime.now(WIB))
            self._reply(200, html.encode('utf-8'),
                        'text/html; charset=utf-8', 'no-store', head=head)

        def _serve_feed(self, head=False):
            if not check_page_password(self, password_sha256):
                return self._unauthorised()
            feed = store.effective_feed()
            self._reply(200, json.dumps(feed, ensure_ascii=False).encode('utf-8'),
                        'application/json; charset=utf-8', 'no-store', head=head)

        def _do_get(self, head=False):
            path = self.path.split('?', 1)[0].split('#', 1)[0]
            if read_only and path not in ('/', '/index.html', '/feed.json',
                                          '/health'):
                # The LAN listener exposes only these paths; /ingest and
                # anything else 404s here, before any token code runs.
                return self._reply(404, b'Not found.\n')
            if path in ('/', '/index.html'):
                return self._serve_page(head=head)
            if path == '/feed.json':
                return self._serve_feed(head=head)
            if path == '/health':
                return self._reply(200, b'{"status":"ok"}',
                                   'application/json; charset=utf-8',
                                   'no-store', head=head)
            if path == '/favicon.ico':
                return self._reply(204)
            return self._reply(404, b'Not found.\n')

        def do_GET(self):
            return self._do_get(head=False)

        def do_HEAD(self):
            if read_only:
                return self._do_get(head=True)
            return self._no()

        def do_POST(self):
            if read_only:
                return self._reply(404, b'Not found.\n')
            path = self.path.split('?', 1)[0].split('#', 1)[0]
            if path != '/ingest':
                return self._reply(405, b'Method not allowed.\n')
            if not check_feed_token(self, feed_token):
                return self._reply(401, b'Feed token required.\n')
            try:
                length = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                return self._reply(400, b'Bad Content-Length.\n')
            if length <= 0:
                return self._reply(400, b'Empty feed.\n')
            if length > MAX_FEED_BYTES:
                # Drain the body in chunks (never stored) so the client
                # gets a clean 413 instead of a reset connection, then reject.
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                return self._reply(413, b'Feed too large.\n')
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode('utf-8'))
            except (ValueError, UnicodeDecodeError):
                return self._reply(400, b'Feed is not valid JSON.\n')
            try:
                feed, dropped = validate_feed(data)
            except FeedError as e:
                return self._reply(400, ('Invalid feed: %s\n' % e).encode('utf-8'))
            store.save(feed)
            sys.stderr.write('ingest ok: %d bytes, %d unknown fields dropped\n'
                             % (length, dropped))
            self._reply(200, json.dumps(
                {'status': 'ok',
                 'generated_at': feed.get('generated_at')}).encode('utf-8'),
                'application/json; charset=utf-8', 'no-store')

        def _no(self):
            if read_only:
                return self._reply(404, b'Not found.\n')
            self._reply(405, b'Method not allowed.\n')

        do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_TRACE = _no

    return Handler


def load_agents(path):
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    agents = data.get('agents') if isinstance(data, dict) else None
    if not isinstance(agents, list) or not agents:
        raise ValueError('agents.json has no agent list')
    return agents


def parse_args(argv=None):
    """The command line. The --lan-bind default comes from BOARD_LAN_BIND,
    so the command-line argument wins when both are set."""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    ap = argparse.ArgumentParser(
        description='Agent Activity Board service (TSK-016). Serves the kept '
                    'feed as a six-tab page. Stdlib only.')
    ap.add_argument('--bind', default=os.environ.get('BOARD_BIND'),
                    help='Listen address (required; never 0.0.0.0 or ::). '
                         'Set at install time from the host.')
    ap.add_argument('--lan-bind', default=os.environ.get('BOARD_LAN_BIND'),
                    help='Second, read-only listen address (a single IPv4 '
                         'address; the command-line argument wins over '
                         'BOARD_LAN_BIND). Empty or unset means no LAN '
                         'listener. Set at install time from the host.')
    ap.add_argument('--port', type=int,
                    default=int(os.environ.get('BOARD_PORT', '8766')),
                    help='Listen port for both listeners (default 8766 for '
                         'local runs; the install-time port is set by '
                         'Grok-Infra and Deniel).')
    ap.add_argument('--data-dir', default=os.path.join(root, 'data'),
                    help='Folder for latest-feed.json (default ./data).')
    ap.add_argument('--agents',
                    default=os.path.join(root, 'config', 'agents.json'),
                    help='Path to agents.json.')
    ap.add_argument('--dev', action='store_true',
                    help='Local runs only: no password and no feed token. '
                         'Never use on the server.')
    ap.add_argument('--seed-feed',
                    help='Load this fixture feed at startup, with its '
                         'generated_at refreshed to now (local runs only).')
    return ap.parse_args(argv)


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    a = parse_args(argv)
    try:
        bind = validate_bind(a.bind)
    except ValueError as e:
        print('Cannot start: %s' % e, file=sys.stderr)
        return 2
    try:
        lan_bind = validate_lan_bind(a.lan_bind)
    except ValueError as e:
        print('Cannot start: %s' % e, file=sys.stderr)
        return 2
    if a.dev and not is_loopback(bind):
        # --dev has no auth at all, so it must never serve beyond this
        # machine: a tailnet address in --dev would serve the page and
        # /ingest to the whole tailnet with no password and no token.
        print('Cannot start: --dev only listens on loopback (127.0.0.0/8 '
              'or ::1); %s is not loopback.' % bind, file=sys.stderr)
        return 2
    if a.dev and lan_bind is not None and not is_loopback(lan_bind):
        # Same rule for the second listener: no auth in --dev.
        print('Cannot start: --dev only listens on loopback (127.0.0.0/8 '
              'or ::1); the LAN address %s is not loopback.' % lan_bind,
              file=sys.stderr)
        return 2
    password_sha256 = os.environ.get('BOARD_PAGE_PASSWORD_SHA256')
    feed_token = os.environ.get('BOARD_FEED_TOKEN')
    if not a.dev:
        missing = []
        if not password_sha256:
            missing.append('BOARD_PAGE_PASSWORD_SHA256')
        if not feed_token:
            missing.append('BOARD_FEED_TOKEN')
        if missing:
            print('Cannot start: %s not set. Set them at install time '
                  '(or use --dev for a local fixture run only).'
                  % ' and '.join(missing), file=sys.stderr)
            return 2
    try:
        agents = load_agents(a.agents)
    except (OSError, ValueError) as e:
        print('Cannot start: %s' % e, file=sys.stderr)
        return 2
    store = FeedStore(a.data_dir)
    if a.seed_feed:
        try:
            with open(a.seed_feed, encoding='utf-8') as f:
                raw = json.load(f)
            feed, _ = validate_feed(raw)
            feed['generated_at'] = datetime.now(WIB).isoformat(timespec='seconds')
            store.save(feed)
            print('Seeded feed from %s (generated_at refreshed to now).'
                  % a.seed_feed)
        except (OSError, ValueError) as e:
            print('Cannot seed: %s' % e, file=sys.stderr)
            return 2
    handler = make_handler(store, agents,
                           None if a.dev else password_sha256,
                           None if a.dev else feed_token)
    lan_handler = make_handler(store, agents,
                               None if a.dev else password_sha256,
                               None if a.dev else feed_token,
                               read_only=True)
    servers = []
    try:
        server = ThreadingHTTPServer((bind, a.port), handler)
        servers.append(server)
    except OSError as e:
        print('Cannot listen on %s:%d: %s' % (bind, a.port, e), file=sys.stderr)
        return 1
    lan_server = None
    if lan_bind is not None:
        try:
            lan_server = ThreadingHTTPServer((lan_bind, a.port), lan_handler)
            servers.append(lan_server)
        except OSError as e:
            # No silent fallback: if the LAN address cannot bind, the
            # service does not start at all.
            print('Cannot listen on %s:%d: %s' % (lan_bind, a.port, e),
                  file=sys.stderr)
            server.server_close()
            return 1
    print('Agent Activity Board %s is running at http://%s:%d/'
          % (VERSION, bind, a.port))
    if lan_server is not None:
        print('Read-only LAN listener at http://%s:%d/ (page, feed and '
              'health only; /ingest returns 404 there)' % (lan_bind, a.port))
        lan_thread = threading.Thread(target=lan_server.serve_forever,
                                      daemon=True)
        lan_thread.start()
    print('Reading the kept feed from: %s' % store.data_dir)
    if a.dev:
        print('WARNING: --dev mode, no password and no feed token. Local runs only.')
    print('Press Ctrl+C to stop.')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopped.')
    finally:
        for s in servers:
            s.shutdown()
            s.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
