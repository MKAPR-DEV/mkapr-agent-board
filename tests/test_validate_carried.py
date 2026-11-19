"""Tests for validate.py, carried over from the Status page handover.

The test bodies are unchanged from the handed-over file
(handover/test_validate.py.txt, SHA-256 in handover/MANIFEST.md), with one
exception: the fake phone number in test_secret_rules_and_not_echoed was
a realistic-looking number in the original and is '1111111111111' here,
so the pushed repo carries no real-looking number (the rule it trips is
any run of 10+ digits). Only the paths changed otherwise: validate.py now lives in src/,
and the built index.html the fixture reads its bound ids from is built
from the handover copies into a temporary folder by setUpModule (the
Status page's index.html is not part of this repo).
Run:  python3 -m unittest discover -s tests -v
"""
import copy, json, os, shutil, subprocess, sys, tempfile, unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
INDEX = os.environ.get('BOARD_REFERENCE_INDEX',
                       os.path.join(ROOT, 'index.html'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
import validate as V

_TMP = None


def setUpModule():
    """Build the reference Status page from the handover copies, so the
    fixture can read the ids the diagrams bind to. The handover copies
    are working inputs, not part of the pushed repo, so this module
    skips when handover/ is absent."""
    global INDEX, _TMP
    if not os.path.exists(os.path.join(ROOT, 'handover', 'build.py.txt')):
        raise unittest.SkipTest(
            'handover/ Status copies not present (not in the pushed repo)')
    _TMP = tempfile.mkdtemp()
    src = os.path.join(_TMP, 'src')
    os.makedirs(src)
    hand = os.path.join(ROOT, 'handover')
    shutil.copy(os.path.join(hand, 'build.py.txt'), os.path.join(src, 'build.py'))
    shutil.copy(os.path.join(hand, 'template.html.txt'), os.path.join(src, 'template.html'))
    shutil.copy(os.path.join(ROOT, 'fixtures', 'status.json'),
                os.path.join(_TMP, 'status.json'))
    r = subprocess.run([sys.executable, os.path.join(src, 'build.py')],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    INDEX = os.path.join(_TMP, 'index.html')


def tearDownModule():
    if _TMP is not None:
        shutil.rmtree(_TMP, ignore_errors=True)

NOW = datetime(2026, 11, 13, 10, 0, tzinfo=timezone(timedelta(hours=7)))


def fixture():
    ids, _ = V.bound_ids(INDEX)
    els = {}
    for i in sorted(ids):
        els[i] = {'name': i, 'group': 'Platform', 'status': 'live', 'short': 'ok', 'note': 'n', 'owner': 'Deniel'}
    return {
        'updated': '2026-11-13T09:30:00+07:00', 'updated_by': 'Architect', 'architecture_version': '1.6',
        'intents': [{'n': n, 'name': 'i%d' % n, 'path': 'p', 'status': 'live', 'note': ''} for n in range(1, 6)],
        'delivery': [
            {'p': 1, 'name': 'Baseline', 'state': 'in_progress', 'note': '', 'tasks': [
                {'id': 'TSK-001', 'title': 't', 'state': 'done', 'owner': 'Muse'},
                {'id': 'Next', 'title': 't', 'state': 'pending', 'owner': 'Muse'},
                {'id': 'Next', 'title': 't2', 'state': 'pending', 'owner': 'Muse'}]},
            {'p': 2, 'name': 'KB', 'state': 'not_started', 'note': '', 'tasks': []}],
        'attention': ['One sentence.'], 'elements': els}


def write(path, text):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def run(data, raw=None):
    raw = raw if raw is not None else json.dumps(data, indent=1)
    return V.validate(data, raw, INDEX, now=NOW)


def msgs(items):
    return ' | '.join('{}: {}'.format(w, m) for w, m in items)


class Tests(unittest.TestCase):
    def test_good_fixture_is_clean(self):
        r = run(fixture())
        self.assertEqual(r.errors, [], msgs(r.errors))
        self.assertEqual(r.warnings, [], msgs(r.warnings))

    def test_bad_status_word(self):
        d = fixture(); d['elements']['waha']['status'] = 'lve'
        self.assertIn('elements.waha.status', msgs(run(d).errors))

    def test_status_is_case_sensitive(self):
        d = fixture(); d['elements']['waha']['status'] = 'Live'
        self.assertIn('elements.waha.status', msgs(run(d).errors))

    def test_diagram_id_missing(self):
        d = fixture(); del d['elements']['r2']
        self.assertIn('elements.r2', msgs(run(d).errors))

    def test_missing_field_and_typo_field(self):
        d = fixture(); del d['elements']['waha']['note']; d['elements']['waha']['staus'] = 'x'
        r = run(d)
        self.assertIn('missing field "note"', msgs(r.errors))
        self.assertIn('unknown field "staus"', msgs(r.warnings))

    def test_date_rules(self):
        for bad in ('2026-11-13 09:30', '2026-11-13T09:30:00', '13/11/2026', 123):
            d = fixture(); d['updated'] = bad
            self.assertIn('updated', msgs(run(d).errors), bad)
        d = fixture(); d['updated'] = '2026-11-14T09:30:00+07:00'
        self.assertIn('future', msgs(run(d).errors))
        d = fixture(); d['updated'] = '2026-11-13T02:30:00Z'
        self.assertEqual(run(d).errors, [])

    def test_stale_is_warning_only(self):
        d = fixture(); d['updated'] = '2026-11-11T09:30:00+07:00'
        r = run(d)
        self.assertEqual(r.errors, [])
        self.assertIn('hours old', msgs(r.warnings))

    def test_secret_rules_and_not_echoed(self):
        secret = '1111111111111'  # 13 repeated digits: trips the 10+ digit
        # rule, obviously fake. (The handed-over original used a
        # realistic-looking number here; changed so the pushed repo
        # carries no real-looking phone number.)
        d = fixture(); d['elements']['waha']['note'] = 'call ' + secret
        r = run(d)
        self.assertIn('10 or more digits', msgs(r.errors))
        self.assertNotIn(secret, msgs(r.errors))
        d = fixture(); d['elements']['waha']['note'] = 'sender 1234@c.us'
        self.assertIn('WhatsApp ID', msgs(run(d).errors))
        d = fixture(); d['elements']['waha']['note'] = 'Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123'
        self.assertIn('token or key', msgs(run(d).errors))

    def test_task_ids(self):
        d = fixture(); d['delivery'][0]['tasks'].append({'id': 'TSK-001', 'title': 'x', 'state': 'done', 'owner': 'M'})
        self.assertIn('duplicate task id "TSK-001"', msgs(run(d).errors))
        d = fixture(); d['delivery'][0]['tasks'][0]['id'] = 'tsk 1'
        r = run(d)
        self.assertEqual(r.errors, [])
        self.assertIn('neither TSK-nnn', msgs(r.warnings))

    def test_optional_task_fields(self):
        base = msgs(run(fixture()).warnings)
        d = fixture(); d['delivery'][0]['tasks'][0].update(stage='QC', summary='s', waiting_on='Deniel')
        r = run(d)
        self.assertEqual(r.errors, [])
        self.assertEqual(msgs(r.warnings), base)                      # no warning for the three new fields
        for stage in V.STAGES:
            d = fixture(); d['delivery'][0]['tasks'][1]['stage'] = stage
            self.assertEqual(run(d).errors, [], stage)
        d = fixture(); d['delivery'][0]['tasks'][0].update(summary='x' * 200, waiting_on='y' * 100)
        self.assertEqual(run(d).errors, [])
        d = fixture(); d['delivery'][0]['tasks'][0].update(summary='', waiting_on='')
        self.assertEqual(run(d).errors, [])                          # empty text is allowed

    def test_optional_task_fields_are_checked(self):
        for bad in ('qc', 'Design', 'QC ', '', 5, None, ['QC']):
            d = fixture(); d['delivery'][0]['tasks'][0]['stage'] = bad
            self.assertIn('delivery[0].tasks[0].stage', msgs(run(d).errors), repr(bad))
        for f, limit in (('summary', 200), ('waiting_on', 100)):
            d = fixture(); d['delivery'][0]['tasks'][0][f] = 'x' * (limit + 1)
            self.assertIn('is %d characters; the limit is %d' % (limit + 1, limit), msgs(run(d).errors), f)
            for bad in (5, None, ['a'], {'a': 1}):
                d = fixture(); d['delivery'][0]['tasks'][0][f] = bad
                self.assertIn('tasks[0].' + f + ': must be text', msgs(run(d).errors), f + repr(bad))
        d = fixture(); d['delivery'][0]['tasks'][0]['stag'] = 'QC'                    # a typo still warns
        r = run(d); self.assertEqual(r.errors, [])
        self.assertIn('unknown field "stag"', msgs(r.warnings))
        d = fixture(); d['delivery'][0]['tasks'][0]['summary'] = 'call 1111111111111 now'   # the secret scan covers new fields
        self.assertIn('phone number', msgs(run(d).errors))

    def test_state_words(self):
        d = fixture(); d['delivery'][0]['tasks'][0]['state'] = 'finished'
        self.assertIn('state', msgs(run(d).errors))
        d = fixture(); d['delivery'][0]['state'] = 'started'
        self.assertIn('delivery[0].state', msgs(run(d).errors))

    def test_priority_consistency(self):
        d = fixture(); d['delivery'][0]['state'] = 'done'
        self.assertIn('not every task is done', msgs(run(d).warnings))
        d = fixture(); d['delivery'][1]['state'] = 'not_started'; d['delivery'][1]['tasks'] = [{'id': 'TSK-9', 'title': 't', 'state': 'done', 'owner': 'M'}]
        self.assertIn('has moved on', msgs(run(d).warnings))

    def test_intent_vs_parts(self):
        d = fixture(); d['elements']['aiti_intake']['status'] = 'design'
        self.assertIn('Intent 1', msgs(run(d).warnings))
        d = fixture(); d['intents'][3]['status'] = 'design'      # intent 4: r2 + kb_receiver, both live here
        self.assertIn('Intent 4', msgs(run(d).warnings))
        d = fixture(); d['elements']['ping_ingress']['status'] = 'partial'; d['intents'][4]['status'] = 'partial'
        self.assertEqual(run(d).warnings, [])

    def test_every_non_working_word_counts_against_a_live_intent(self):
        for word in ('built', 'design', 'parked', 'unknown'):
            d = fixture(); d['elements']['aiti_intake']['status'] = word
            self.assertIn('Intent 1', msgs(run(d).warnings), word)
        for word in ('live', 'partial'):
            d = fixture(); d['elements']['aiti_intake']['status'] = word
            self.assertEqual([w for w in run(d).warnings if 'Intent 1' in w[1]], [], word)

    def test_box_text_limit_comes_from_page(self):
        _, limits = V.bound_ids(INDEX)
        k, n = sorted(limits.items())[0]
        d = fixture(); d['elements'][k]['short'] = 'x' * (n + 1)
        self.assertIn('elements.{}.short'.format(k), msgs(run(d).warnings))
        d['elements'][k]['short'] = 'x' * n
        self.assertEqual(run(d).warnings, [])

    def test_wrong_types_do_not_crash(self):
        for mutate in (lambda d: d.update(elements=[]), lambda d: d.update(intents={}), lambda d: d.update(delivery='x'),
                       lambda d: d.update(attention='x'), lambda d: d['elements'].update(waha='x'),
                       lambda d: d['delivery'][0].update(tasks=[1]), lambda d: d.update(updated_by='')):
            d = fixture(); mutate(d)
            self.assertTrue(run(d).errors)
        self.assertTrue(V.validate([], '[]', INDEX, now=NOW).errors)

    def test_command_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = fixture()
            d['updated'] = datetime.now(timezone.utc).isoformat(timespec='seconds')   # real clock for the CLI
            good = os.path.join(tmp, 'status.json'); write(good, json.dumps(d))
            idx = os.path.join(tmp, 'index.html'); write(idx, read(INDEX))
            run_cli = lambda *a: subprocess.run([sys.executable, '-I', os.path.join(ROOT, 'src', 'validate.py')] + list(a),
                                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
            r = run_cli(good, '--index', idx)
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertIn('0 error(s), 0 warning(s)', r.stdout)
            d['elements']['waha']['owner'] = ''                                          # a warning only
            write(good, json.dumps(d))
            self.assertEqual(run_cli(good, '--index', idx).returncode, 0)
            self.assertEqual(run_cli(good, '--index', idx, '--strict').returncode, 1)
            d['elements']['waha']['status'] = 'lve'                                      # an error
            write(good, json.dumps(d))
            self.assertEqual(run_cli(good, '--index', idx).returncode, 1)
            bad = os.path.join(tmp, 'bad.json'); write(bad, '{oops')
            self.assertEqual(run_cli(bad).returncode, 1)
            self.assertEqual(run_cli(os.path.join(tmp, 'nope.json')).returncode, 2)


if __name__ == '__main__':
    unittest.main(verbosity=1)
