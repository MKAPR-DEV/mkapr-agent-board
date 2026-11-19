"""The two Status page diagrams, drawn server-side from status.json.

This is a port of the Status page's ``build.py`` figure code (handover copy,
SHA-256 in ``handover/MANIFEST.md``). The geometry, box text and arrow layout
are unchanged. The one difference: the Status page draws the figures with
placeholder classes and lets its JavaScript fill in the status at run time;
here the figures are drawn with the status already applied, because the board
renders its page server-side from the kept feed. The rules applied are the
Status page's own JavaScript rules, copied exactly:

- a box takes the status of its element (unknown when missing or unrecognised);
- ``design`` is shown as "Planned" (``PLANNED`` in the compact form);
- an arrow is solid only when every part in its ``data-needs`` is live or
  partial; otherwise dashed; an arrow into a parked part also fades;
- a box's ``short`` line is cut at the box's character limit.

Works on Python 3.8 or newer. Standard library only.
"""
import html

E = html.escape

STATUS = {
    'live': 'Live', 'partial': 'Partial', 'built': 'Built, not live',
    'design': 'Planned', 'parked': 'Parked', 'unknown': 'Not verified',
}
COMPACT = {
    'live': 'LIVE', 'partial': 'PARTIAL', 'built': 'NOT LIVE',
    'design': 'PLANNED', 'parked': 'PARKED', 'unknown': 'UNVERIFIED',
}


def norm(status):
    """Status word normalised the way the Status page does it."""
    s = str(status or '').lower().strip()
    return s if s in STATUS else 'unknown'


def cut(text, n):
    """Cut a ``short`` line at n characters, the Status page's way."""
    s = '' if text is None else str(text)
    if len(s) <= n:
        return s
    return s[:max(1, n - 1)].rstrip(' ,;:') + '…'


def marker(mid):
    return ('<defs><marker id="%s" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
            'markerHeight="7" orient="auto-start-reverse"><path class="ahf" d="M0 0L10 5L0 10z"/>'
            '</marker></defs>' % mid)


def plain(x, y, w, h, title, sub='', dash=False):
    cx = x + w / 2
    dash_cls = ' class="dash"' if dash else ''
    o = ['<g class="node s-none"><rect%s x="%s" y="%s" width="%s" height="%s" rx="6" fill="none"/>'
         % (dash_cls, x, y, w, h)]
    if title and sub:
        o.append('<text class="t" x="%s" y="%s" text-anchor="middle">%s</text>' % (cx, y + h / 2 - 2, E(title)))
        o.append('<text class="sub" x="%s" y="%s" text-anchor="middle">%s</text>' % (cx, y + h / 2 + 13, E(sub)))
    elif title:
        o.append('<text class="t" x="%s" y="%s" text-anchor="middle">%s</text>' % (cx, y + h / 2 + 4, E(title)))
    else:
        o.append('<text class="sub" x="%s" y="%s" text-anchor="middle">%s</text>' % (cx, y + h / 2 + 4, E(sub)))
    o.append('</g>')
    return '\n'.join(o)


def node(x, y, w, h, title, el, elements, sub='', dyn=True, maxc=24):
    cx = x + w / 2
    e = elements.get(el) or {}
    s = norm(e.get('status'))
    short = cut(e.get('short', ''), maxc) if dyn else (sub or '')
    # The data-short / data-max attributes are the Status template's: the
    # JS cut rule reads them. The content is filled server-side here
    # (the Status page leaves it empty for the JS to fill at runtime).
    attr = ' data-short="1" data-max="%d"' % maxc if dyn else ''
    return '\n'.join([
        '<g class="node s-%s" data-base="node" data-el="%s">' % (s, el),
        '<rect x="%s" y="%s" width="%s" height="%s" rx="6" fill="none"/>' % (x, y, w, h),
        '<text class="t" x="%s" y="%s" text-anchor="middle">%s</text>' % (cx, y + 18, E(title)),
        '<text class="sub" x="%s" y="%s" text-anchor="middle"%s>%s</text>' % (cx, y + 32, attr, E(short)),
        '<text class="st" x="%s" y="%s" text-anchor="middle" data-st="full">%s</text>' % (cx, y + h - 9, STATUS[s].upper()),
        '</g>'])


def sysnode(x, y, w, h, title, sub, el, elements):
    """A system box that takes its status from an element."""
    cx = x + w / 2
    e = elements.get(el) or {}
    s = norm(e.get('status'))
    return '\n'.join([
        '<g class="node s-%s" data-base="node" data-el="%s">' % (s, el),
        '<rect x="%s" y="%s" width="%s" height="%s" rx="6" fill="none"/>' % (x, y, w, h),
        '<text class="t" x="%s" y="%s" text-anchor="middle">%s</text>' % (cx, y + 15, E(title)),
        '<text class="sub" x="%s" y="%s" text-anchor="middle">%s</text>' % (cx, y + 28, E(sub)),
        '<text class="st" x="%s" y="%s" text-anchor="middle" data-st="full">%s</text>' % (cx, y + 41, STATUS[s].upper()),
        '</g>'])


def arrow_class(needs, elements):
    """The class an arrow gets: solid, dashed, and faint when parked."""
    ids = [i for i in needs.split() if i]
    sts = [norm((elements.get(i) or {}).get('status')) for i in ids]
    ok = all(s in ('live', 'partial') for s in sts)
    parked = 'parked' in sts
    return 'ar' + ('' if ok else ' dashed') + (' faint' if parked else '')


def arrow(x1, y1, x2, y2, needs, elements, on='', off=None, mid='ah1', lx=None, ly=None):
    cls = arrow_class(needs, elements)
    o = ['<line class="%s" data-needs="%s" x1="%s" y1="%s" x2="%s" y2="%s" marker-end="url(#%s)"/>'
         % (cls, needs, x1, y1, x2, y2, mid)]
    if on:
        lx = (x1 + x2) / 2 if lx is None else lx
        ly = (min(y1, y2) - 6) if ly is None else ly
        ok = 'dashed' not in cls
        label = on if ok else (off or on)
        # data-on / data-off are the Status template's: the label text is
        # resolved server-side here, the attributes stay for parity (and
        # for any JS that re-reads them).
        offa = ' data-off="%s"' % E(off) if off else ''
        o.append('<text class="al" x="%s" y="%s" text-anchor="middle" data-needs="%s" data-on="%s"%s>%s</text>'
                 % (lx, ly, needs, E(on), offa, E(label)))
    return '\n'.join(o)


def _route_box(x, y, w, h, title, el, elements, maxc=27):
    e = elements.get(el) or {}
    s = norm(e.get('status'))
    return ('<g class="node route s-%s" data-base="node route" data-el="%s">'
            '<rect x="%s" y="%s" width="%s" height="%s" rx="4" fill="none"/>'
            '<text class="t" x="%s" y="%s">%s</text>'
            '<text class="sub" x="%s" y="%s" data-short="1" data-max="27">%s</text>'
            '<text class="st" x="%s" y="%s" data-st="full">%s</text></g>'
            % (s, el, x, y, w, h, x + 10, y + 16, E(title),
               x + 10, y + 30, E(cut(e.get('short', ''), maxc)),
               x + 10, y + 45, STATUS[s].upper()))


def _mode_box(x, y, w, h, title, el, elements):
    e = elements.get(el) or {}
    s = norm(e.get('status'))
    return ('<g class="node mode s-%s" data-base="node mode" data-el="%s">'
            '<rect x="%s" y="%s" width="%s" height="%s" rx="4" fill="none"/>'
            '<text class="m" x="%s" y="%s">%s</text>'
            '<text class="st" x="%s" y="%s" text-anchor="end" data-st="compact">%s</text></g>'
            % (s, el, x, y, w, h, x + 12, y + 19, E(title),
               x + w - 10, y + 19, COMPACT[s]))


def _gateway_box(elements):
    e = elements.get('gateway') or {}
    s = norm(e.get('status'))
    return ('<g class="node s-%s" data-base="node" data-el="gateway">'
            '<rect x="420" y="50" width="190" height="420" rx="6" fill="none"/>'
            '<text class="t" x="432" y="72">Aiti Gateway</text>'
            '<text class="st" x="598" y="72" text-anchor="end" data-st="compact">%s</text>'
            '<text class="sub" x="432" y="89" data-short="1" data-max="30">%s</text></g>'
            % (s, COMPACT[s], E(cut(e.get('short', ''), 30))))


def _aiti_box(elements):
    e = elements.get('aiti') or {}
    s = norm(e.get('status'))
    return ('<g class="node s-%s" data-base="node" data-el="aiti">'
            '<rect x="690" y="50" width="270" height="150" rx="6" fill="none"/>'
            '<text class="t" x="702" y="72">Aiti helpdesk bot</text>'
            '<text class="st" x="948" y="72" text-anchor="end" data-st="compact">%s</text></g>'
            % (s, COMPACT[s]))


def _waha_box(elements):
    e = elements.get('waha') or {}
    s = norm(e.get('status'))
    return ('<g class="node s-%s" data-base="node" data-el="waha">'
            '<rect x="216" y="150" width="140" height="120" rx="6" fill="none"/>'
            '<text class="t" x="286" y="178" text-anchor="middle">WAHA</text>'
            '<text class="sub" x="286" y="195" text-anchor="middle">WhatsApp HTTP API</text>'
            '<text class="sub" x="286" y="211" text-anchor="middle" data-short="1" data-max="24">%s</text>'
            '<text class="st" x="286" y="252" text-anchor="middle" data-st="full">%s</text></g>'
            % (s, E(cut(e.get('short', ''), 24)), STATUS[s].upper()))


def figure1(elements):
    """How a message is routed. Geometry copied from the Status build.py."""
    elements = elements or {}
    W, H = 1200, 484
    f = ['<svg viewBox="0 0 %s %s" role="img" aria-label="Inbound routing. Every WhatsApp event enters WAHA and then the Aiti Gateway. The Gateway sends ordinary one-to-one chats to Aiti, group events to Ping Ingress, and quoted approval replies to the KB reply receiver. Each box shows its current status as a word.">' % (W, H), marker('ah1')]
    for x, t in [(16, 'WHO WRITES'), (216, 'TRANSPORT'), (420, 'ROUTER'), (690, 'HANDLERS'), (1050, 'SYSTEMS')]:
        f.append('<text class="col" x="%s" y="30">%s</text>' % (x, t))
    for y, t, s in [(70, 'Employees', '1:1 chat'), (130, 'IT staff', '1:1 chat'), (190, 'KB approver', '1:1 chat'), (298, 'Work groups', 'group chat')]:
        f.append(plain(16, y, 150, 44, t, s))
    f.append(_waha_box(elements))
    for y1, y2 in [(92, 180), (152, 198), (212, 216), (320, 246)]:
        f.append(arrow(166, y1, 216, y2, 'waha', elements))
    f.append(arrow(356, 210, 420, 210, 'waha gateway', elements, 'all events'))
    f.append(_gateway_box(elements))
    for y, t, el in [(99, 'R4 helpdesk', 'r4'), (216, 'R2 KB approval', 'r2'), (286, 'R1 ping', 'r1'), (348, 'R3 IT 1:1 to Ping', 'r3'), (408, 'R0 drop', 'r0')]:
        f.append(_route_box(432, y, 166, 52, t, el, elements))
    f.append(_aiti_box(elements))
    for y, t, el in [(86, 'Employee ticket intake', 'aiti_intake'), (122, 'IT general questions', 'aiti_it_qa'), (158, 'IT ticket status, search', 'aiti_it_tickets')]:
        f.append(_mode_box(702, y, 246, 30, t, el, elements))
    f.append(node(690, 214, 270, 56, 'KB reply receiver', 'kb_receiver', elements, maxc=38))
    f.append(node(690, 284, 270, 56, 'Ping Ingress', 'ping_ingress', elements, maxc=38))
    f.append(node(690, 346, 270, 56, 'Ping 1:1 chat', 'ping_dm', elements, maxc=38))
    f.append(plain(690, 414, 270, 40, '', 'discarded, only the reason is logged', dash=True))
    f.append(arrow(610, 125, 690, 125, 'gateway r4 aiti', elements, 'other 1:1', 'not live'))
    f.append(arrow(610, 242, 690, 242, 'gateway r2 kb_receiver', elements, 'quoted reply', 'not live'))
    f.append(arrow(610, 312, 690, 312, 'gateway r1 ping_ingress', elements, 'groups', 'not live'))
    f.append(arrow(610, 374, 690, 374, 'gateway r3 ping_dm', elements, 'IT 1:1', 'off'))
    f.append(arrow(610, 434, 690, 434, 'gateway r0', elements))
    f.append(sysnode(1050, 48, 134, 48, 'osTicket', 'MySQL', 'osticket', elements))
    f.append(sysnode(1050, 101, 134, 48, 'LiteLLM', 'LLM proxy', 'litellm', elements))
    f.append(sysnode(1050, 154, 134, 48, 'PostgreSQL', 'helpdesk database', 'postgres', elements))
    f.append(sysnode(1050, 218, 134, 48, 'SharePoint', 'KB Replies list', 'kb_replies', elements))
    f.append(sysnode(1050, 288, 134, 48, 'PostgreSQL', 'gateway log table', 'gateway_log', elements))
    f.append(arrow(960, 84, 1050, 72, 'aiti osticket', elements, 'tickets', lx=1005, ly=68))
    f.append(arrow(960, 125, 1050, 125, 'aiti litellm', elements, 'prompts', lx=1005, ly=119))
    f.append(arrow(960, 168, 1050, 178, 'aiti postgres', elements, 'chat state', lx=1005, ly=164))
    f.append(arrow(960, 242, 1050, 242, 'kb_receiver kb_replies', elements, 'inserts reply', 'not live'))
    f.append(arrow(960, 312, 1050, 312, 'ping_ingress gateway_log', elements, 'log row', 'not live'))
    f.append('</svg>')
    return '\n'.join(f)


def figure2(elements):
    """The Knowledge Base approval loop. Geometry copied from the Status build.py."""
    elements = elements or {}
    W2, H2, P, NW = 1296, 340, 162, 124
    X = lambda i: 16 + i * P
    g = ['<svg viewBox="0 0 %s %s" role="img" aria-label="Knowledge Base approval loop. A submitted fact goes through the KB Inbox, the Consolidator, the waiting list, the digest builder, the KB Outbox and the Outbox sender to the approver on WhatsApp. The approver\'s quoted reply returns through Gateway route R2, the KB reply receiver, the KB Replies list and apply-replies to the waiting list, and the Publisher writes approved facts into the Knowledge Base. Each box shows its current status as a word.">' % (W2, H2), marker('ah2')]
    g.append('<text class="col" x="16" y="24">OUT: FROM A SUBMITTED FACT TO A WHATSAPP DIGEST</text>')
    ty, by = 58, 224
    top = [('KB connector', 'kb_connector'), ('KB Inbox', 'kb_inbox'), ('Consolidator', 'consolidator'), ('KB Waiting', 'kb_waiting'),
           ('Digest builder', 'digest_builder'), ('KB Outbox', 'kb_outbox'), ('Outbox sender', 'outbox_sender')]
    for i, (t, el) in enumerate(top):
        g.append(node(X(i), ty, NW, 66, t, el, elements, maxc=21))
    tl = [('submits', 'kb_connector kb_inbox'), ('reads', 'kb_inbox consolidator'), ('writes', 'consolidator kb_waiting'), ('ranks', 'kb_waiting digest_builder'),
          ('queues', 'digest_builder kb_outbox'), ('polls', 'kb_outbox outbox_sender'), ('sends digest', 'outbox_sender')]
    for i, (l, needs) in enumerate(tl):
        g.append(arrow(X(i) + NW, ty + 33, X(i + 1), ty + 33, needs, elements, l, mid='ah2', ly=ty - 8))
    g.append('<g class="node s-none"><rect x="%s" y="%s" width="%s" height="232" rx="6" fill="none"/>'
             '<text class="t" x="%s" y="%s" text-anchor="middle">Approver</text>'
             '<text class="sub" x="%s" y="%s" text-anchor="middle">on WhatsApp</text></g>'
             % (X(7), ty, NW, X(7) + NW / 2, ty + 112, X(7) + NW / 2, ty + 128))
    g.append('<text class="col" x="16" y="%s">BACK: FROM THE APPROVER\'S REPLY TO A PUBLISHED FACT</text>' % (by + 100))
    bot = [('Knowledge Base', 'kb_library', None), ('Publisher', 'publisher', None), ('KB Waiting', 'kb_waiting', 'Approved, Dropped'),
           ('apply-replies', 'apply_replies', None), ('KB Replies', 'kb_replies', None), ('KB reply receiver', 'kb_receiver', None), ('Gateway route R2', 'r2', 'in the Aiti Gateway')]
    for i, (t, el, static) in enumerate(bot):
        g.append(node(X(i), by, NW, 66, t, el, elements, sub=static or '', dyn=static is None, maxc=21))
    bl = [('writes file', 'publisher kb_library'), ('reads', 'kb_waiting publisher'), ('decides', 'apply_replies kb_waiting'), ('reads', 'kb_replies apply_replies'),
          ('inserts', 'kb_receiver kb_replies'), ('forwards', 'r2 kb_receiver'), ('quoted reply', 'r2')]
    for i, (l, needs) in enumerate(bl):
        g.append(arrow(X(i + 1), by + 33, X(i) + NW, by + 33, needs, elements, l, mid='ah2', ly=by - 8))
    g.append('</svg>')
    return '\n'.join(g)


# The 29 element ids the diagrams are bound to (Status README; do not rename).
BOUND_IDS = [
    'waha', 'gateway', 'r4', 'r2', 'r1', 'r3', 'r0', 'aiti', 'aiti_intake',
    'aiti_it_qa', 'aiti_it_tickets', 'kb_receiver', 'ping_ingress', 'ping_dm',
    'kb_connector', 'kb_inbox', 'consolidator', 'kb_waiting', 'digest_builder',
    'kb_outbox', 'outbox_sender', 'kb_replies', 'apply_replies', 'publisher',
    'kb_library', 'osticket', 'litellm', 'postgres', 'gateway_log',
]
