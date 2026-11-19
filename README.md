# Agent Activity Board — Command Center (TSK-016)

One read-only live page with six tabs — Live map, Architecture, Delivery,
Agents, Tasks, Feed — showing the feed that Muse-Feed's n8n "Board feed"
workflow posts once a minute. Built from the PRD v0.5
(Archie to Deniel, 2026-11-19) and Archie's Muse-Page brief of the same day.
Prepare-only: this package is the deliverable for Grok-Infra to install on
AI02 through the infra-ops gate. Nothing here installs anything, and nothing
here talks to OneDrive, n8n, AI01 or AI02.

Python 3.8+; standard library only; no pip installs; no outbound requests;
no external fonts, scripts or images anywhere in the page.

## What is in this repo

- `src/board_service.py` — the HTTP service (stdlib `http.server`).
- `src/board_render.py` — the server-side page renderer (six tabs).
- `src/feed_states.py` — the PRD §5/§7 state rules (pure functions).
- `src/figures.py` — the two Architecture diagrams, ported from the Status
  page's `build.py` (geometry unchanged; status applied server-side using
  the Status page's own rules).
- `src/validate.py` — the Status page's feed validator, carried over
  unchanged (used by the parity tests).
- `config/agents.json` — the 10 agents from PRD §7 / AGENTS.md v1.6.
- `fixtures/` — fixture feeds for the tests, plus `status.json`, the real
  2026-11-19 12:07 status file used for the parity test. The feed fixtures
  are fake data (PRD example ids like TSK-015/TSK-012, made-up msg_ids).
- `tests/` — the unittest suite (82 tests).
- `MANIFEST.txt` — contents of this package with a SHA-256 per file
  (except MANIFEST.txt itself, which cannot hash itself).

Not in this repo (working inputs, kept out of the push on purpose):
`docs/` (PRD, brief, agent registry), `handover/` (the Status page's
`.txt` copies — its test files contain fake test values that must not be
pushed), `tools/` (the fixture generator).

## Run it locally on fixtures

```sh
python3 src/board_service.py --bind 127.0.0.1 --dev --seed-feed fixtures/feed-basic.json
```

Then open `http://127.0.0.1:8766/` in a browser. `--dev` means no page
password and no feed token; it is for local fixture runs only and must
never be used on the server. The seed feed's `generated_at` is refreshed
to now at startup, so the page does not show the stale banner.

Other fixtures: `feed-criterion12.json` (the PRD §12 cases — a 3-hop
thread, a halt, a reply arriving before the request is archived, a broken
header, a started-by marker), `feed-canary.json` (contains the marker
`CANARY-BODY-123` in body fields the service must drop),
`feed-no-status.json` (no `status_json`, so the page shows the last good
copy), `feed-stale.json` (an old feed, so the red banner shows),
`feed-empty.json` (everything empty).

To post a feed by hand (dev mode needs no token):

```sh
curl -X POST -H 'Content-Type: application/json' \
  --data @fixtures/feed-criterion12.json http://127.0.0.1:8766/ingest
```

## Run the tests

```sh
python3 -m unittest discover -s tests -v
```

85 tests, all passing at v0.1.2:

- service: health, page/feed password (401 without or with a wrong
  password), ingest auth (401 without or with a wrong token), wrong
  version / bad JSON / bad types → 400 with the last good feed kept,
  oversize body → 413, any method other than GET/POST to the four
  endpoints → 405, unknown path → 404, feed survives a restart (the
  store reloads from disk), no outbound-request code in the service,
  `--dev` refuses a non-loopback bind, a stalled client is closed
  after the read timeout.
- canary: `CANARY-BODY-123` placed in a fixture body appears nowhere in
  the page, the served feed, the saved feed file or the logs; unknown
  feed fields are dropped at ingest, never stored.
- states: §5/§7 rules (manual agents never STALE, "needs a ping" after
  10 minutes, session poll stale after 25 minutes in hours, scheduled
  stale after 2 missed polls, IN PROGRESS only with a started-by marker,
  reply → ANSWERED, halt → NEEDS DENIEL, feed stale after 5 minutes)
  and the page's display of them.
- rendering: six tabs with real hash links, no external requests, light
  and dark mode, phone-width rules, score to two decimals, map labels
  from tasks (never from message text), not-created agents as dashed
  cards, the delegation arrow, delivery links, keyboard-selectable task
  rows, the feed tab capped at 50 events, the 30-second refresh.
- parity (PRD criterion 17): against the real `status.json`, the
  Architecture figures match the Status page's build output exactly
  (same geometry, same status word and colour for all 29 bound ids,
  same solid/dashed arrows, `design` shown as "Planned"), the delivery
  rows match, the tally and the parts-working percentage match
  (67.78%: 28 live + 5 partial, of 45 parts), and the "What changed"
  functions are byte-identical to the Status page's except two marked
  adaptations (server-rendered page, missing-panel guard). The diff
  behaviour was executed in Node against the copied functions.
- carried over: the Status validator's own 18 tests, run against
  `src/validate.py`.

The parity tests, the carried validator tests and the status-fixture
tests build their reference page from the handed-over Status copies in
`handover/`. That folder is a working input, not part of this repo, so
from a bare checkout those tests skip (they do not fail) and the rest
of the suite passes. In the full workdir, where `handover/` is present,
all 82 tests run.

### What was NOT tested

- **No browser tests were run.** Playwright/Chromium is not installed
  on this machine, so the parity comparison runs on rendered HTML
  strings, not in a browser. The Status page's `test_changes.py`
  browser tests were not carried over for the same reason. Before the
  page goes live, run it in a real browser at 375 px and with a keyboard
  and check the tabs, the map labels and the "What changed" panel by hand.
- **No live-network check was done.** The n8n-to-AI02 connect test
  (Muse-Feed posting to this service) needs Deniel's typed OK and has
  not happened. Nothing in this package has contacted n8n, AI01 or AI02.

## The service interface (for Muse-Feed)

One write path only:

```
POST /ingest
Content-Type: application/json
X-Feed-Token: <the feed token Deniel sets at install time>
```

Body: feed shape v1 (PRD §14) — `version` must be 1, `generated_at`
parseable, the lists present, `status_json` an object or null. Unknown
top-level and per-item fields are dropped, never stored or logged.
Limits: 1 MB body (larger → 413). Wrong version, bad JSON or wrong types
→ 400, and the last good feed is kept. Wrong or missing token → 401 and
nothing changes. Any other method on any path → 405.

Reads:

```
GET /          the page (Basic auth, the shared page password)
GET /feed.json the kept feed, as JSON (same password)
GET /health    {"status":"ok"}, no auth, no sensitive data
```

The kept feed lives in one file, `data/latest-feed.json` (default
`./data`, override with `--data-dir`). The service keeps the latest feed
in memory and on disk; on restart it reloads the file. When a feed has
no `status_json`, the page keeps showing the last good status copy and
says so ("(last good copy)").

## Install-time settings (set by Deniel / Grok-Infra, never in this repo)

| Setting | How it is set | Notes |
|---|---|---|
| Bind address | `--bind` or `BOARD_BIND` | Required. The service refuses to start without one and refuses `0.0.0.0` and `::`. Set it from the host at install time. |
| Port | `--port` or `BOARD_PORT` | Default 8766 is for local runs only. The install-time port is chosen by Grok-Infra and Deniel (not 22, not an already-used port). |
| Page password hash | `BOARD_PAGE_PASSWORD_SHA256` | SHA-256 hex of the shared page password. Deniel sets the password; only the hash is stored on AI02. Never put the password or its hash in the repo. |
| Feed token | `BOARD_FEED_TOKEN` | The token Muse-Feed sends as `X-Feed-Token`. Set at install time; never in the repo. |
| Data dir | `--data-dir` | Default `./data` (created on first run). |
| Agents file | `--agents` | Default `config/agents.json`. |

Without `--dev`, the service exits (code 2) if the bind address is
missing/invalid or if either secret is unset. `--dev` is local-only: it
listens on loopback only and refuses any other bind address.

The page password must be long and random (20+ characters, generated, not
a word or a date). It is checked as an unsalted SHA-256 hash, and there is
no lockout after failed attempts — so a short or guessable password can be
brute-forced. Deniel sets it at install time.

## The page, briefly

- Header: title, parts-working score (Live = 1, Partial = 0.5, else 0,
  all parts count, two decimals), "Work data as of HH:MM:SS WIB",
  "Status data updated HH:MM by \<who\>" and "Architecture v\<version\>"
  from `status.json`, a Refresh button, a red banner when the feed is
  older than 5 minutes, and a red "N items need Deniel" strip (first two
  items + Open button) when anything needs him — human-inbox decisions,
  halts, and the status.json attention list; slug and action only, never
  a message body.
- Live map: one card per agent around the Agent bus bar; icon per kind
  (inline SVG), state dot + word, waiting chip, and a pill label with
  what the agent is doing (task id + short title from `status.json`,
  never message text; click/Enter opens the Tasks tab). Agents not yet
  created (muse-feed, muse-page until live) are dashed "not created"
  cards. Delegation arrow Archie → Operations, labelled.
- Architecture: the Status page's two diagrams with the same data and
  rules (29 bound ids, `design` shown as "Planned", arrow solid only
  when every part in its `data-needs` is Live or Partial), the tally by
  status, and the "What changed" panel with "Mark as seen"
  (`localStorage` key `statusmap.seen.v1`, unchanged).
- Delivery: the Status page's delivery order (priority name/note/state,
  task rows, NEW and CHANGED tags); task ids link to the Tasks tab.
- Agents: one row per agent (runs where, doing now, waiting for it,
  last seen, schedule, state).
- Tasks: rows (task id + short title, who has the ball, where, since,
  state, next) and a detail panel per task (stage as a row of stage
  boxes with the current one filled, summary, waiting on, timeline of
  every message in its threads in time order, latest Reports files).
  A missing field says "not recorded" — nothing is invented.
- Feed: the newest 50 events (time, from → to, action/type, slug, task).
  No message text anywhere on the page, in the feed, or in the logs.

States follow PRD §5: WAITING, IN PROGRESS (only with a started-by
marker — otherwise the page says "in progress not recorded"), ANSWERED,
CLOSED, STALE, NEEDS DENIEL, OFF HOURS. Manual-chat agents show
"Needs a ping" after 10 minutes and are never STALE. Files with
unreadable headers appear in an "Unreadable messages" box (file name +
fixed short reason), never hidden, never breaking the page.

Tabs are real hash links (`#map`, `#arch`, `#delivery`, `#agents`,
`#tasks`, `#feed`), so they bookmark and the back button works.
The page polls `/feed.json` every 30 seconds and reloads when a new
feed arrives. Light and dark mode follow the system setting; the page
works at 375 px with no sideways scroll (wide diagrams scroll inside
their own box).

## Open points (for Archie / Ops)

- Feed shape v1 is Archie's to freeze after one round of questions from
  Muse-Page and Muse-Feed. This build follows PRD §14 as it stands
  (including the `unreadable` list). If a field needs to change, that
  goes to Archie in a Reports file — the shape is not changed here.
- The wireframe (artifact "Agent Board Wireframe" v3) has not been seen
  by Muse-Page. Where PRD and wireframe differ, the PRD rules.
- The n8n-to-AI02 connect test (Muse-Feed posting a real feed to this
  service) still needs Deniel's typed OK.
- Status page retirement date: the board replaces the Status page after
  a week in which both match (PRD D8).
- The `muse-feed` and `muse-page` agents are shown as "not created"
  until they are live. Muse-Coord (the `inbox/muse/` dispatch point)
  has no map card in PRD v0.5 — open question for Archie.
- History retention for past feeds is undecided (PRD §17).
- Whether TASKS.md should be read in a later phase is undecided
  (PRD §17).
