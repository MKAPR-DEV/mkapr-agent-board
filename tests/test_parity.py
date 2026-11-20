"""Status parity test (PRD criterion 17).

With the same status.json (fixtures/status.json, the real 2026-11-19 12:07
file), the Architecture and Delivery tabs must show the same status word
and colour for every one of the 29 bound ids, the same solid/dashed arrows,
the same delivery rows, the same parts-working percentage and the same
"What changed" list as the current Status page.

Method: the reference Status page is built from the handed-over copies
(build.py + template.html) in a temporary folder. Its SVG figures carry
placeholder classes, so the test applies the Status page's own JavaScript
fill rules (from the template) to the reference SVG, then compares it with
the board's server-rendered SVG. Delivery rows, tally and score are compared
against the same data using the template's render rules. The "What changed"
diff functions are compared source-to-source and executed in node.

No browser is available in this VM (Playwright is not installed), so this
is a comparison on rendered HTML strings, not a browser test. That limit is
stated in the README.

The reference page is built from the handed-over Status copies in
handover/. That folder is a working input, not part of the pushed repo,
so these tests skip (they do not fail) when handover/ is absent.
Run:  python3 -m unittest discover -s tests -v   (from the workdir root)
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers as H
from helpers import load_fixture
sys.path.insert(0, os.path.join(H.ROOT, 'src'))
import board_render as R
from figures import BOUND_IDS

STATUS = {'live': 'Live', 'partial': 'Partial', 'built': 'Built, not live',
          'design': 'Planned', 'parked': 'Parked', 'unknown': 'Not verified'}

# The handover copies are working inputs, not in the pushed repo.
HANDOVER = os.path.join(H.ROOT, 'handover')
HANDOVER_PRESENT = os.path.exists(os.path.join(HANDOVER, 'build.py.txt'))
HANDOVER_REASON = 'handover/ Status copies not present (not in the pushed repo)'
COMPACT = {'live': 'LIVE', 'partial': 'PARTIAL', 'built': 'NOT LIVE',
           'design': 'PLANNED', 'parked': 'PARKED', 'unknown': 'UNVERIFIED'}
TASK = {'pending': 'Pending', 'in_progress': 'In progress',
        'blocked': 'Waiting', 'done': 'Done', 'not_started': 'Not started'}


def norm(status):
    s = str(status or '').lower().strip()
    return s if s in STATUS else 'unknown'


def cut(text, n):
    s = '' if text is None else str(text)
    if len(s) <= n:
        return s
    return s[:max(1, n - 1)].rstrip(' ,;:') + '…'


def build_reference_page():
    """Build the Status page from the handover copies. Returns the tmp dir
    and the built index.html text."""
    tmp = tempfile.mkdtemp()
    src = os.path.join(tmp, 'src')
    os.makedirs(src)
    hand = os.path.join(H.ROOT, 'handover')
    shutil.copy(os.path.join(hand, 'build.py.txt'), os.path.join(src, 'build.py'))
    shutil.copy(os.path.join(hand, 'template.html.txt'), os.path.join(src, 'template.html'))
    shutil.copy(os.path.join(H.ROOT, 'fixtures', 'status.json'),
                os.path.join(tmp, 'status.json'))
    r = subprocess.run([sys.executable, os.path.join(src, 'build.py')],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    with open(os.path.join(tmp, 'index.html'), encoding='utf-8') as f:
        return tmp, f.read()


def apply_status_fill(svg, elements):
    """Apply the Status page's JavaScript fill rules to a built SVG.

    This mirrors template.html's render(): boxes take their element's
    status, arrows are solid only when every part in data-needs is live or
    partial (and fade when one is parked), labels show data-on or data-off.
    """
    def fill_group(m):
        head, el_id, body = m.group(1), m.group(2), m.group(3)
        e = elements.get(el_id) or {}
        s = norm(e.get('status'))
        head = re.sub(r's-[a-z]+', 's-' + s, head, count=1)

        def fill_st(m2):
            kind = m2.group(2)
            text = COMPACT[s] if kind == 'compact' else STATUS[s].upper()
            return m2.group(1) + kind + m2.group(3) + text + m2.group(5)

        body = re.sub(
            r'(<text class="st"[^>]*data-st=")(compact|full)("[^>]*>)([^<]*)(</text>)',
            fill_st, body)

        def fill_short(m2):
            text = cut(e.get('short', ''), int(m2.group(2)))
            return m2.group(1) + m2.group(2) + m2.group(3) + text + m2.group(5)

        body = re.sub(
            r'(<text class="sub"[^>]*data-short="1" data-max=")(\d+)("[^>]*>)([^<]*)(</text>)',
            fill_short, body)
        return head + ' data-el="' + el_id + '">' + body + '</g>'

    svg = re.sub(r'(<g class="[^"]*" data-base="[^"]*") data-el="([^"]+)">(.*?)</g>',
                 fill_group, svg, flags=re.S)

    def fill_line(m):
        needs = m.group(1)
        ids = needs.split()
        sts = [norm((elements.get(i) or {}).get('status')) for i in ids]
        ok = all(s in ('live', 'partial') for s in sts)
        parked = 'parked' in sts
        cls = 'ar' + ('' if ok else ' dashed') + (' faint' if parked else '')
        # group(2) ends just before the closing "/>"; put it back.
        return ('<line class="' + cls + '" data-needs="' + needs + '"'
                + m.group(2) + '/>')

    svg = re.sub(r'<line class="ar" data-needs="([^"]+)"([^>]*)/>',
                 fill_line, svg)

    def fill_label(m):
        tag, _old = m.group(1), m.group(2)
        needs = re.search(r'data-needs="([^"]+)"', tag).group(1)
        on = re.search(r'data-on="([^"]+)"', tag).group(1)
        off_m = re.search(r'data-off="([^"]+)"', tag)
        off = off_m.group(1) if off_m else None
        ids = needs.split()
        sts = [norm((elements.get(i) or {}).get('status')) for i in ids]
        ok = all(s in ('live', 'partial') for s in sts)
        text = on if ok else (off if off is not None else on)
        return tag + text + '</text>'

    svg = re.sub(r'(<text class="al"[^>]*>)([^<]*)(</text>)', fill_label, svg)
    return svg


def extract_svg(html, index):
    svgs = re.findall(r'<svg.*?</svg>', html, flags=re.S)
    return svgs[index]


def extract_figure_svgs(html):
    """The Architecture figures only. The board page also has small kind
    icons (inline SVG in the map cards), which are not figures."""
    return [s for s in re.findall(r'<svg.*?</svg>', html, flags=re.S)
            if 'data-el=' in s]


def svg_elements(svg):
    """Per bound id: (class token, status texts, short texts)."""
    out = {}
    for m in re.finditer(r'<g class="([^"]*)" data-base="[^"]*" data-el="([^"]+)">(.*?)</g>',
                         svg, flags=re.S):
        cls, el_id, body = m.group(1), m.group(2), m.group(3)
        token = next((t for t in cls.split() if t.startswith('s-')), None)
        st = re.findall(r'<text class="st"[^>]*>([^<]*)</text>', body)
        sub = re.findall(r'<text class="sub"[^>]*>([^<]*)</text>', body)
        out[el_id] = (token, st, sub)
    return out


def svg_arrows(svg):
    """Per data-needs value: (line class, label text)."""
    out = {}
    for m in re.finditer(r'<line class="([^"]*)" data-needs="([^"]+)"', svg):
        out[m.group(2)] = (m.group(1), None)
    for m in re.finditer(r'<text class="al"[^>]*data-needs="([^"]+)"[^>]*>([^<]*)</text>', svg):
        needs, text = m.group(1), m.group(2)
        cls, _ = out.get(needs, (None, None))
        out[needs] = (cls, text)
    return out


def strip_status(svg):
    """Remove everything status-dependent, leaving the geometry."""
    svg = re.sub(r's-[a-z]+', 's-X', svg)
    svg = re.sub(r'(<text class="st"[^>]*>)[^<]*(</text>)', r'\1\2', svg)
    svg = re.sub(r'(<text class="sub"[^>]*data-short[^>]*>)[^<]*(</text>)', r'\1\2', svg)
    svg = re.sub(r'(<text class="al"[^>]*>)[^<]*(</text>)', r'\1\2', svg)
    svg = re.sub(r'class="ar[^"]*"', 'class="ar"', svg)
    svg = re.sub(r'\s+', ' ', svg)
    return svg


@unittest.skipUnless(HANDOVER_PRESENT, HANDOVER_REASON)
class ParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.reference = build_reference_page()
        cls.status = load_fixture('status.json')
        cls.feed = load_fixture('feed-basic.json')
        cls.agents = H.load_agents()
        cls.page = R.render_page(cls.feed, cls.agents)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_bound_ids_are_all_29(self):
        self.assertEqual(len(BOUND_IDS), 29)
        for el_id in BOUND_IDS:
            self.assertIn(el_id, self.status['elements'], el_id)

    def test_figures_match_the_reference_after_fill(self):
        els = self.status['elements']
        ref_figs = extract_figure_svgs(self.reference)
        my_figs = extract_figure_svgs(self.page)
        self.assertEqual(len(ref_figs), 2)
        self.assertEqual(len(my_figs), 2)
        for index in (0, 1):
            ref = apply_status_fill(ref_figs[index], els)
            mine = my_figs[index]
            # Same geometry (everything except status-dependent marks).
            self.assertEqual(strip_status(ref), strip_status(mine),
                             'figure %d geometry differs' % index)
            # Same status word and colour for every bound id in this figure.
            ref_els, my_els = svg_elements(ref), svg_elements(mine)
            for el_id in ref_els:
                self.assertIn(el_id, my_els, el_id)
                self.assertEqual(my_els[el_id], ref_els[el_id],
                                 'figure %d element %s' % (index, el_id))
            # Same solid/dashed arrows and labels.
            self.assertEqual(svg_arrows(mine), svg_arrows(ref),
                             'figure %d arrows differ' % index)

    def test_delivery_rows_match(self):
        expected = []
        for prio in self.status['delivery']:
            for task in prio['tasks']:
                expected.append((task['id'], task['title'], task['owner'],
                                 TASK[task['state']]))
        delivery = self.page.split('id="tab-delivery"')[1].split('</section>')[0]
        found = re.findall(
            r'<div class="id"><a href="#task=([^"]+)">([^<]+)</a></div>\s*'
            r'<div class="chg-name">([^<]*)</div><div class="who">([^<]*)</div>'
            r'<div><span class="pill [^"]+">([^<]+)</span></div>',
            delivery)
        got = [(f[1], f[2], f[3], f[4]) for f in found]
        self.assertEqual(got, expected)

    def test_tally_and_score_match(self):
        counts = Counter(norm(e['status']) for e in self.status['elements'].values())
        tally = self.page.split('id="tally"')[1].split('</div></section>')[0]
        for key, word in STATUS.items():
            m = re.search(r'<span class="n">(\d+)</span><span class="pill s-%s">%s</span>'
                          % (key, word), tally)
            self.assertIsNotNone(m, key)
            self.assertEqual(int(m.group(1)), counts[key], key)
        live, partial = counts['live'], counts['partial']
        total = len(self.status['elements'])
        self.assertIn('%.2f%%' % ((live + partial / 2) / total * 100), self.page)

    def test_design_is_shown_as_planned_everywhere(self):
        self.assertIn('PLANNED', self.page)  # compact form in the diagrams
        arch = self.page.split('id="tab-arch"')[1].split('id="tab-delivery"')[0]
        self.assertNotIn('>Design<', arch)
        self.assertIn('s-design">Planned', arch)

    def test_status_words_are_word_plus_colour(self):
        # Every status pill in the Architecture tab carries a word.
        arch = self.page.split('id="tab-arch"')[1].split('id="tab-delivery"')[0]
        pills = re.findall(r'<span class="pill s-[a-z]+">([^<]+)</span>', arch)
        self.assertTrue(pills)
        for word in pills:
            self.assertIn(word, STATUS.values(), word)


def _brace_match(text, start):
    """Return the function body starting at the '{' at index start."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise ValueError('unbalanced braces')


def extract_function(text, name):
    m = re.search(r'function %s\([^)]*\)\s*\{' % name, text)
    if not m:
        return None
    start = text.index('{', m.start())
    return text[m.start():start] + _brace_match(text, start)


def normalise(js):
    js = re.sub(r'//[^\n]*', '', js)
    return re.sub(r'\s+', ' ', js).strip()


@unittest.skipUnless(HANDOVER_PRESENT, HANDOVER_REASON)
class WhatChangedTests(unittest.TestCase):
    """The 'What changed' diff is the Status page's, unchanged."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(H.ROOT, 'handover', 'template.html.txt'),
                  encoding='utf-8') as f:
            cls.template = f.read()
        cls.mine = R.WHAT_CHANGED_JS

    def test_stateof_and_diffstates_are_identical_to_the_status_page(self):
        for name in ('stateOf', 'diffStates', 'readSeen', 'writeSeen',
                     'markChanged'):
            ref = extract_function(self.template, name)
            got = extract_function(self.mine, name)
            self.assertIsNotNone(ref, name)
            self.assertIsNotNone(got, name)
            self.assertEqual(normalise(got), normalise(ref), name)
        # renderChanges is identical except the documented guard line
        # (adaptation 3: no panel exists when the feed has no status data).
        ref = extract_function(self.template, 'renderChanges')
        got = extract_function(self.mine, 'renderChanges')
        got = got.replace("if (!sec) return;", "")
        got = got.replace("/* ADAPTED: no panel exists when the feed carries no status data. */", "")
        self.assertEqual(normalise(got), normalise(ref), 'renderChanges')

    def test_diff_behaviour_in_node(self):
        # Execute the copied functions in node: a live part that stops
        # working must be listed first with "Stopped working".
        block = self.mine
        driver = """
var a = stateOf(%s);
var b = JSON.parse(JSON.stringify(a));
b.els.waha.s = 'built';
b.attn.push('A new attention sentence.');
var list = diffStates(a, b);
console.log(JSON.stringify(list));
""" % json.dumps(load_fixture('status.json'))
        proc = subprocess.run(['node', '-e', block + driver],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        changes = json.loads(proc.stdout)
        self.assertTrue(changes)
        first = changes[0]
        self.assertEqual(first['map'], 'els')
        self.assertEqual(first['key'], 'waha')
        self.assertTrue(first['down'])
        self.assertEqual(first['from'], 'live')
        self.assertEqual(first['to'], 'built')
        attn = [c for c in changes if c['type'] == 'attn-added']
        self.assertEqual(len(attn), 1)
        self.assertEqual(attn[0]['name'], 'A new attention sentence.')


if __name__ == '__main__':
    unittest.main(verbosity=1)
