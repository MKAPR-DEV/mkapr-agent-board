"""Shared helpers for the board tests.

Test-only credentials: the password "test-only-password" and the token
"test-only-feed-token" are fake values used only by these tests, in the same
way the handed-over Status tests use fake phone numbers and dummy tokens.
They are never real credentials.
"""
import hashlib
import http.client
import json
import os
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))

import board_service as svc  # noqa: E402

TEST_PASSWORD = 'test-only-password'
TEST_PASSWORD_SHA256 = hashlib.sha256(TEST_PASSWORD.encode()).hexdigest()
TEST_TOKEN = 'test-only-feed-token'

NOW = '2026-11-19T10:00:00+07:00'  # Thursday, inside polling hours


def load_fixture(name):
    with open(os.path.join(ROOT, 'fixtures', name), encoding='utf-8') as f:
        return json.load(f)


def load_agents():
    with open(os.path.join(ROOT, 'config', 'agents.json'), encoding='utf-8') as f:
        return json.load(f)['agents']


def basic_auth_header(password=TEST_PASSWORD):
    import base64
    raw = ('user:%s' % password).encode()
    return 'Basic ' + base64.b64encode(raw).decode()


class Server(object):
    """A board service on 127.0.0.1, ephemeral port, throwaway data dir."""

    def __init__(self, seed=None):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = svc.FeedStore(self.tmp.name)
        if seed is not None:
            feed, _ = svc.validate_feed(seed)
            self.store.save(feed)
        handler = svc.make_handler(self.store, load_agents(),
                                   TEST_PASSWORD_SHA256, TEST_TOKEN)
        from http.server import ThreadingHTTPServer
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)
        self.thread.start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        hdrs = dict(headers or {})
        conn.request(method, path, body=body, headers=hdrs)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, dict(resp.getheaders()), data

    def get(self, path, password=TEST_PASSWORD):
        headers = {}
        if password is not None:
            headers['Authorization'] = basic_auth_header(password)
        return self.request('GET', path, headers=headers)

    def ingest(self, feed, token=TEST_TOKEN, raw_body=None):
        body = raw_body if raw_body is not None else json.dumps(feed).encode()
        headers = {'Content-Type': 'application/json'}
        if token is not None:
            headers['X-Feed-Token'] = token
        return self.request('POST', '/ingest', body=body, headers=headers)
