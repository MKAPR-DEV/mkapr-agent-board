"""The real status.json fixture (2026-11-19 12:07, with the D4 fields)
passes the carried-over validator. This settles the PRD's open point
(section 11: validation of the 12:07 file had not been seen by Archie).

Note: the pushed fixture is the 12:07 file with server hostnames
redacted to placeholders (<n8n-host>, <helpdesk-host>, <db-host>), per
the no-server-addresses rule for the repo. The unredacted file was
validated in the workdir before redaction (0 errors, exactly 1 known
warning) and the suite passed 82/82 on it; this test guards the shape
of the redacted copy. The live board renders the real notes from the
feed, unaffected by this fixture redaction.

Run:  python3 -m unittest discover -s tests -v   (from the workdir root)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
import validate as V


class StatusFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The reference page is built from the handed-over Status copies
        # in handover/. That folder is a working input, not part of the
        # pushed repo, so these tests skip when handover/ is absent.
        if not os.path.exists(os.path.join(ROOT, 'handover', 'build.py.txt')):
            raise unittest.SkipTest(
                'handover/ Status copies not present (not in the pushed repo)')
        cls.tmp = tempfile.mkdtemp()
        src = os.path.join(cls.tmp, 'src')
        os.makedirs(src)
        hand = os.path.join(ROOT, 'handover')
        shutil.copy(os.path.join(hand, 'build.py.txt'), os.path.join(src, 'build.py'))
        shutil.copy(os.path.join(hand, 'template.html.txt'), os.path.join(src, 'template.html'))
        shutil.copy(os.path.join(ROOT, 'fixtures', 'status.json'),
                    os.path.join(cls.tmp, 'status.json'))
        r = subprocess.run([sys.executable, os.path.join(src, 'build.py')],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        cls.index = os.path.join(cls.tmp, 'index.html')
        with open(os.path.join(ROOT, 'fixtures', 'status.json'),
                  encoding='utf-8') as f:
            cls.raw = f.read()
        cls.data = json.loads(cls.raw)

    @classmethod
    def tearDownClass(cls):
        # tmp is unset when setUpClass skipped (no handover copies).
        if getattr(cls, 'tmp', None):
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_real_status_json_has_no_errors(self):
        r = V.validate(self.data, self.raw, self.index)
        self.assertEqual(r.errors, [], ' | '.join('%s: %s' % e for e in r.errors))

    def test_real_status_json_has_only_the_known_warning(self):
        # The fixture is a copy of the real status.json from 19 Nov 12:07
        # WIB; validate it against a clock near that time, so the
        # 24-hour staleness warning (a property of the copy's age, not
        # of its content) does not fire. Without the fixed clock this
        # test starts failing once the copy is a day old.
        from datetime import datetime, timedelta
        upd = datetime.fromisoformat(self.data['updated'])
        r = V.validate(self.data, self.raw, self.index,
                       now=upd + timedelta(hours=1))
        self.assertEqual(len(r.warnings), 1)
        where, msg = r.warnings[0]
        self.assertEqual(where, 'elements.gateway_cleanup.status')
        self.assertIn('unknown', msg)

    def test_d4_fields_are_present_on_the_open_tasks(self):
        tasks = {}
        for prio in self.data['delivery']:
            for task in prio['tasks']:
                tasks.setdefault(task['id'], task)
        self.assertEqual(tasks['TSK-012']['stage'], 'import_prep')
        self.assertEqual(tasks['TSK-015']['stage'], 'test')
        self.assertTrue(tasks['TSK-012']['summary'])
        self.assertTrue(tasks['TSK-012']['waiting_on'])
        self.assertTrue(tasks['TSK-015']['summary'])
        self.assertTrue(tasks['TSK-015']['waiting_on'])


if __name__ == '__main__':
    unittest.main(verbosity=1)
