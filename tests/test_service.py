"""Tests for the board service: ingest, auth, methods, bind rule, canary.

Covers PRD criteria 3 (canary), 8 (methods/auth), 10 (bind address),
11 (broken feed), 19 (last good status) and 20 (feed failure keeps data).
Run:  python3 -m unittest discover -s tests -v   (from the workdir root)
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers as H
from helpers import load_fixture
sys.path.insert(0, os.path.join(H.ROOT, 'src'))
import board_service as svc


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.srv = H.Server()

    def tearDown(self):
        self.srv.stop()

    def test_health_needs_no_password(self):
        status, _, body = self.srv.request('GET', '/health')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {'status': 'ok'})

    def test_page_and_feed_need_the_password(self):
        for path in ('/', '/feed.json'):
            status, headers, _ = self.srv.request('GET', path)
            self.assertEqual(status, 401, path)
            self.assertIn('WWW-Authenticate', headers)
            status, _, _ = self.srv.get(path, password='wrong-password')
            self.assertEqual(status, 401, path)
            status, _, _ = self.srv.get(path)
            self.assertEqual(status, 200, path)

    def test_no_feed_yet_says_so(self):
        status, _, body = self.srv.get('/')
        self.assertEqual(status, 200)
        self.assertIn(b'No feed has been received yet', body)
        status, _, body = self.srv.get('/feed.json')
        self.assertEqual(json.loads(body)['generated_at'], None)

    def test_ingest_good_feed_then_page_and_feed_show_it(self):
        feed = load_fixture('feed-basic.json')
        status, _, body = self.srv.ingest(feed)
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)['status'], 'ok')
        status, _, body = self.srv.get('/feed.json')
        kept = json.loads(body)
        self.assertEqual(kept['generated_at'], feed['generated_at'])
        self.assertEqual(len(kept['threads']), 3)
        status, _, body = self.srv.get('/')
        self.assertIn(b'Agent Activity Board', body)
        self.assertIn(b'TSK-015', body)
        self.assertIn(b'Work data as of', body)

    def test_ingest_without_token_is_401_and_changes_nothing(self):
        feed = load_fixture('feed-basic.json')
        status, _, _ = self.srv.ingest(feed, token=None)
        self.assertEqual(status, 401)
        status, _, _ = self.srv.ingest(feed, token='wrong-token')
        self.assertEqual(status, 401)
        status, _, body = self.srv.get('/feed.json')
        self.assertEqual(json.loads(body)['generated_at'], None)

    def test_ingest_wrong_version_and_bad_json_are_rejected(self):
        good = load_fixture('feed-basic.json')
        self.srv.ingest(good)
        for bad in ({'version': 2, 'generated_at': good['generated_at']},
                    {'version': 1},
                    {'version': 1, 'generated_at': 'not a time'},
                    {'version': 1, 'generated_at': good['generated_at'],
                     'agents': 'not a list'},
                    {'version': 1, 'generated_at': good['generated_at'],
                     'status_json': 'not an object'}):
            status, _, _ = self.srv.ingest(bad)
            self.assertEqual(status, 400, bad)
        status, _, body = self.srv.ingest(good, raw_body=b'{oops')
        self.assertEqual(status, 400)
        # The last good feed is still on screen.
        status, _, body = self.srv.get('/feed.json')
        self.assertEqual(json.loads(body)['generated_at'],
                         good['generated_at'])

    def test_ingest_oversize_body_is_rejected_without_reading_it(self):
        good = load_fixture('feed-basic.json')
        self.srv.ingest(good)
        big = b'{"version": 1, "generated_at": "2026-11-19T09:58:00+07:00", "pad": "' \
            + b'x' * (svc.MAX_FEED_BYTES + 100) + b'"}'
        status, _, _ = self.srv.ingest(good, raw_body=big)
        self.assertEqual(status, 413)
        status, _, body = self.srv.get('/feed.json')
        self.assertEqual(json.loads(body)['generated_at'],
                         good['generated_at'])

    def test_only_get_and_the_one_post_exist(self):
        for method in ('PUT', 'DELETE', 'PATCH', 'OPTIONS', 'HEAD', 'TRACE'):
            status, _, _ = self.srv.request(method, '/')
            self.assertEqual(status, 405, method)
            status, _, _ = self.srv.request(method, '/feed.json')
            self.assertEqual(status, 405, method)
        status, _, _ = self.srv.request('POST', '/', body=b'{}')
        self.assertEqual(status, 405)
        status, _, _ = self.srv.request('POST', '/feed.json', body=b'{}')
        self.assertEqual(status, 405)

    def test_unknown_path_is_404(self):
        status, _, _ = self.srv.get('/nope')
        self.assertEqual(status, 404)

    def test_canary_body_appears_nowhere(self):
        # Criterion 3: a feed that smuggles a body field (CANARY-BODY-123)
        # must not leak it into the page, the feed, or the saved file.
        canary = load_fixture('feed-canary.json')
        self.assertIn('CANARY-BODY-123', json.dumps(canary))
        status, _, _ = self.srv.ingest(canary)
        self.assertEqual(status, 200)
        for path in ('/', '/feed.json'):
            _, _, body = self.srv.get(path)
            self.assertNotIn(b'CANARY-BODY-123', body, path)
        saved = open(os.path.join(self.srv.tmp.name, 'latest-feed.json'),
                     encoding='utf-8').read()
        self.assertNotIn('CANARY-BODY-123', saved)
        # And the stored feed no longer carries the extra fields at all.
        kept = json.load(open(os.path.join(self.srv.tmp.name,
                                           'latest-feed.json'),
                              encoding='utf-8'))
        self.assertNotIn('note', kept)
        self.assertNotIn('body', kept['threads'][0]['messages'][0])
        self.assertNotIn('note', kept['agents'][4])

    def test_feed_without_status_keeps_the_last_good_copy(self):
        # Criterion 19: a feed with status_json null shows the last good
        # status with its time; the rest of the page still works.
        good = load_fixture('feed-basic.json')
        self.srv.ingest(good)
        no_status = load_fixture('feed-no-status.json')
        status, _, _ = self.srv.ingest(no_status)
        self.assertEqual(status, 200)
        status, _, body = self.srv.get('/feed.json')
        kept = json.loads(body)
        self.assertIsInstance(kept['status_json'], dict)
        self.assertEqual(kept['status_json']['architecture_version'], '1.26')
        status, _, body = self.srv.get('/')
        self.assertIn(b'last good copy', body)
        self.assertIn(b'TSK-012', body)  # the rest of the page still works

    def test_feed_survives_a_restart(self):
        feed = load_fixture('feed-basic.json')
        self.srv.ingest(feed)
        data_dir = self.srv.tmp.name
        self.srv.httpd.shutdown()
        self.srv.httpd.server_close()
        store2 = svc.FeedStore(data_dir)
        self.assertEqual(store2.effective_feed()['generated_at'],
                         feed['generated_at'])

    def test_no_outbound_requests_are_made(self):
        # The service has no HTTP client code at all.
        import board_service as _svc
        source = open(_svc.__file__, encoding='utf-8').read()
        for token in ('urlopen', 'requests.', 'httpx', 'urllib.request',
                      'socket.create_connection'):
            self.assertNotIn(token, source)


class DevModeTests(unittest.TestCase):
    """--dev mode (no password, no token) must accept an ingest with no
    token, or a local fixture run cannot feed the page at all. This is a
    regression test: check_feed_token used to return False when no token
    was configured, so every dev-mode ingest got 401."""

    def setUp(self):
        import http.client
        import tempfile
        import threading
        from http.server import ThreadingHTTPServer
        self._http = http.client
        self.tmp = tempfile.TemporaryDirectory()
        self.store = svc.FeedStore(self.tmp.name)
        handler = svc.make_handler(self.store, H.load_agents(), None, None)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def _request(self, method, path, body=None, headers=None):
        conn = self._http.HTTPConnection('127.0.0.1', self.port, timeout=10)
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    def test_dev_mode_ingest_needs_no_token(self):
        feed = load_fixture('feed-basic.json')
        status, body = self._request(
            'POST', '/ingest', body=json.dumps(feed).encode(),
            headers={'Content-Type': 'application/json'})
        self.assertEqual(status, 200, body)
        status, body = self._request('GET', '/feed.json')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['generated_at'],
                         feed['generated_at'])


class BindTests(unittest.TestCase):
    def test_bind_address_is_required(self):
        for bad in (None, '', '   '):
            with self.assertRaises(ValueError, msg=repr(bad)):
                svc.validate_bind(bad)

    def test_bind_refuses_all_interfaces(self):
        for bad in ('0.0.0.0', '::', '0:0:0:0:0:0:0:0'):
            with self.assertRaises(ValueError, msg=bad):
                svc.validate_bind(bad)

    def test_bind_refuses_hostnames(self):
        with self.assertRaises(ValueError):
            svc.validate_bind('localhost')

    def test_bind_accepts_a_specific_address(self):
        self.assertEqual(svc.validate_bind('127.0.0.1'), '127.0.0.1')
        self.assertEqual(svc.validate_bind('192.168.1.10'), '192.168.1.10')

    def test_main_refuses_to_start_without_a_bind(self):
        self.assertEqual(svc.main(['--port', '0']), 2)

    def test_is_loopback(self):
        self.assertTrue(svc.is_loopback('127.0.0.1'))
        self.assertTrue(svc.is_loopback('127.0.0.2'))
        self.assertTrue(svc.is_loopback('::1'))
        self.assertFalse(svc.is_loopback('192.168.1.10'))
        self.assertFalse(svc.is_loopback('10.0.0.5'))

    def test_dev_mode_refuses_non_loopback_bind(self):
        # QC round 1, item 1: --dev has no auth, so it must never serve
        # beyond this machine. A tailnet address in --dev would serve the
        # page and /ingest to the whole tailnet with no password/token.
        self.assertEqual(svc.main(['--dev', '--bind', '192.168.1.10',
                                   '--port', '0']), 2)


class SocketTimeoutTests(unittest.TestCase):
    """A stalled client must not hold a worker thread forever: the handler
    sets a read timeout on the connection (QC round 1, item 3)."""

    def test_stalled_client_is_closed(self):
        import socket
        old = svc.SOCKET_TIMEOUT
        svc.SOCKET_TIMEOUT = 0.5
        try:
            srv = H.Server()
            try:
                s = socket.create_connection(('127.0.0.1', srv.port),
                                             timeout=5)
                s.sendall(b'GET / HTTP/1.1\r\nHost: x\r\n')
                # No blank line: the request is never complete. The server
                # must close the connection once the read times out.
                s.settimeout(5)
                self.assertEqual(s.recv(1024), b'')
                s.close()
            finally:
                srv.stop()
        finally:
            svc.SOCKET_TIMEOUT = old


class LanBindTests(unittest.TestCase):
    """v0.1.6: the LAN address must be one IPv4 address, never 0.0.0.0;
    empty or unset means no LAN listener."""

    def test_empty_means_no_lan_listener(self):
        for empty in (None, '', '   '):
            self.assertIsNone(svc.validate_lan_bind(empty), msg=repr(empty))

    def test_accepts_a_single_ipv4_address(self):
        self.assertEqual(svc.validate_lan_bind('192.168.2.10'),
                         '192.168.2.10')
        self.assertEqual(svc.validate_lan_bind('127.0.0.1'), '127.0.0.1')

    def test_rejects_all_interfaces(self):
        for bad in ('0.0.0.0', '::'):
            with self.assertRaises(ValueError, msg=bad):
                svc.validate_lan_bind(bad)

    def test_rejects_empty_with_colon(self):
        for bad in (':', ':8080', '192.168.2.10:'):
            with self.assertRaises(ValueError, msg=bad):
                svc.validate_lan_bind(bad)

    def test_rejects_hostnames_ipv6_and_cidr(self):
        for bad in ('example.com', 'localhost', '2001:db8::1',
                    '10.0.0.0/24', '192.168.2.10/32', '192.168.2',
                    '999.1.1.1'):
            with self.assertRaises(ValueError, msg=bad):
                svc.validate_lan_bind(bad)

    def test_main_refuses_to_start_with_a_bad_lan_bind(self):
        for bad in ('0.0.0.0', '::', ':', 'example.com'):
            self.assertEqual(
                svc.main(['--bind', '127.0.0.1', '--lan-bind', bad,
                          '--port', '0']), 2, bad)

    def test_dev_mode_refuses_a_non_loopback_lan_bind(self):
        self.assertEqual(svc.main(['--dev', '--bind', '127.0.0.1',
                                   '--lan-bind', '192.168.2.5',
                                   '--port', '0']), 2)


class LanEnvTests(unittest.TestCase):
    """v0.1.6 amendment: BOARD_LAN_BIND carries the LAN address; the
    --lan-bind argument wins when both are set."""

    def test_env_var_is_the_default(self):
        import unittest.mock as mock
        with mock.patch.dict(os.environ,
                             {'BOARD_LAN_BIND': '192.168.2.5'}):
            self.assertEqual(svc.parse_args([]).lan_bind, '192.168.2.5')

    def test_command_line_wins_over_the_env_var(self):
        import unittest.mock as mock
        with mock.patch.dict(os.environ,
                             {'BOARD_LAN_BIND': '192.168.2.5'}):
            self.assertEqual(
                svc.parse_args(['--lan-bind', '192.168.2.9']).lan_bind,
                '192.168.2.9')

    def test_empty_env_var_means_no_lan_listener(self):
        import unittest.mock as mock
        with mock.patch.dict(os.environ, {'BOARD_LAN_BIND': ''}):
            self.assertIsNone(
                svc.validate_lan_bind(svc.parse_args([]).lan_bind))

    def test_unset_env_var_means_no_lan_listener(self):
        import unittest.mock as mock
        env = dict(os.environ)
        env.pop('BOARD_LAN_BIND', None)
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertIsNone(svc.parse_args([]).lan_bind)


class LanListenerTests(unittest.TestCase):
    """v0.1.6: the second listener is read-only and shares the feed state."""

    def setUp(self):
        self.srv = H.Server(seed=load_fixture('feed-basic.json'), lan=True)

    def tearDown(self):
        self.srv.stop()

    def test_version_string_is_0_1_7(self):
        self.assertEqual(svc.VERSION, '0.1.7')
        _, headers, _ = self.srv.request('GET', '/health')
        self.assertEqual(headers.get('Server', '').strip(), 'AgentBoard/0.1.7')

    def test_lan_serves_page_feed_and_health(self):
        for path in ('/', '/feed.json'):
            status, headers, body = self.srv.lan_get(path)
            self.assertEqual(status, 200, path)
            self.assertEqual(headers.get('Cache-Control'), 'no-store', path)
            if path == '/':
                self.assertIn(b'Agent Activity Board', body)
        status, _, body = self.srv.lan_request('GET', '/health')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {'status': 'ok'})

    def test_page_is_no_store_on_both_listeners(self):
        # Archie's proxy question: the page used to send no-cache; both
        # listeners must now send no-store, like /feed.json.
        for get in (self.srv.get, self.srv.lan_get):
            _, headers, _ = get('/')
            self.assertEqual(headers.get('Cache-Control'), 'no-store')

    def test_lan_needs_the_same_password(self):
        for path in ('/', '/feed.json'):
            status, headers, _ = self.srv.lan_request('GET', path)
            self.assertEqual(status, 401, path)
            self.assertIn('WWW-Authenticate', headers)
            status, _, _ = self.srv.lan_get(path, password='wrong-password')
            self.assertEqual(status, 401, path)
            status, _, _ = self.srv.lan_get(path)
            self.assertEqual(status, 200, path)
        status, _, _ = self.srv.lan_request('GET', '/health')
        self.assertEqual(status, 200)

    def test_lan_head_serves_headers_only(self):
        for path in ('/', '/feed.json', '/health'):
            headers = {}
            if path != '/health':
                headers['Authorization'] = H.basic_auth_header()
            status, resp_headers, body = self.srv.lan_request(
                'HEAD', path, headers=headers)
            self.assertEqual(status, 200, path)
            self.assertEqual(body, b'', path)
            self.assertIn('Content-Length', resp_headers)

    def test_lan_ingest_is_404_with_any_method_and_any_token(self):
        feed = load_fixture('feed-basic.json')
        # The correct token still 404s: the request never reaches the
        # token code, so the token cannot leak a right/wrong signal.
        for token in (H.TEST_TOKEN, 'wrong-token', None):
            status, _, _ = self.srv.lan_ingest(feed, token=token)
            self.assertEqual(status, 404, repr(token))
        for method in ('GET', 'PUT', 'DELETE', 'PATCH', 'OPTIONS', 'TRACE'):
            status, _, _ = self.srv.lan_request(method, '/ingest')
            self.assertEqual(status, 404, method)
        # The feed on the main listener is unchanged by those attempts.
        status, _, body = self.srv.get('/feed.json')
        self.assertEqual(json.loads(body)['generated_at'],
                         feed['generated_at'])

    def test_lan_rejects_everything_else(self):
        for method, path in (('POST', '/'), ('POST', '/feed.json'),
                             ('PUT', '/'), ('DELETE', '/feed.json'),
                             ('GET', '/nope'), ('GET', '/favicon.ico'),
                             ('GET', '/ingest'), ('POST', '/health')):
            status, _, _ = self.srv.lan_request(method, path)
            self.assertEqual(status, 404, '%s %s' % (method, path))

    def test_main_listener_still_takes_ingest(self):
        # Parity: the same request that 404s on the LAN listener works
        # on the main listener, against the same feed state.
        feed = load_fixture('feed-basic.json')
        status, _, body = self.srv.ingest(feed)
        self.assertEqual(status, 200, body)
        status, _, body = self.srv.lan_get('/feed.json')
        self.assertEqual(json.loads(body)['generated_at'],
                         feed['generated_at'])

    def test_no_password_or_token_appears_in_the_log(self):
        import base64
        import io
        old = sys.stderr
        sys.stderr = io.StringIO()
        try:
            self.srv.lan_get('/', password=H.TEST_PASSWORD)
            self.srv.lan_ingest(load_fixture('feed-basic.json'),
                                token=H.TEST_TOKEN)
            self.srv.get('/', password=H.TEST_PASSWORD)
        finally:
            logged = sys.stderr.getvalue()
            sys.stderr = old
        self.assertNotIn(H.TEST_PASSWORD, logged)
        self.assertNotIn(H.TEST_TOKEN, logged)
        self.assertNotIn(base64.b64encode(
            ('user:%s' % H.TEST_PASSWORD).encode()).decode(), logged)


if __name__ == '__main__':
    unittest.main(verbosity=1)
