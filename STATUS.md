# Project Status

This is the shared hand-off document for everyone working on this repo —
Sheraj and whichever AI coding tool is in the seat (Claude Code, Codex,
Antigravity, Grok). Read the Snapshot before starting anything nontrivial;
update it and add one Activity Log entry when you finish a chunk of work.
See `AGENTS.md` for the full technical orientation — this file is just
"what's true right now," not how the system works.

**How to keep this useful, not noise:**
- Snapshot = current reality, edited in place (don't accumulate old facts —
  delete what's no longer true).
- Activity Log = one short entry per session, newest first, prepended. A
  paragraph, not a diff — point at files/commits, don't paste code.
- Keep the log to roughly the last 15–20 entries. When it grows past that,
  trim the oldest ones off the bottom — full history is always in `git log`.
- Note which tool/model did the work; it helps everyone calibrate context
  ("was this reviewed by a human yet?", "which tool wrote this prompt?").

---

## Snapshot (as of 2026-08-17)

**Live and working** (committed, in production):
- **Bookmark pipeline** (Librarian → Artist → consultation → Scribe → Reviewer
  → Compositor) and the **quote-card giveaway pipeline** (owner-selectable
  sources, default Ruhi Book 1, optional translation). Etsy publishing is built
  but has never run; Canva autofill is built, broken (0/10) and now off by
  default.
- **The visual layout editor** for both product types, **print sheets**
  (single + multi-product gathering sheets), the **X-post giveaway pipeline**,
  and the **named roster + avatars** on the dashboard.
- **Abigail (the Secretary)** Phases 1–3: dashboard chat + WhatsApp (owner tier
  and a tool-less guest tier), real Claude tool-calling for every read/write,
  Google Workspace. Phase 4 (recovery rhythms) not started.

Also committed: the **Video Generation pipeline** (`58a785f`), the quote-card
redesign and exactness work, print sheets and the giving ledger. (Earlier
Snapshots claimed video and the card work were still uncommitted long after they
had landed — check `git status` rather than trusting a line like this.)

Also committed in `cf599aa` (2026-08-16), after this section spent three days
claiming otherwise: **the Colony** (rules 35–41a), **the project wallet** (42–49),
**Abigail ↔ the teams** (50–54), **run cancellation** (55–57) and the
**finished-video shelf** (58). Sheraj has still not reviewed that pile by hand —
"committed" is not "reviewed".

Also committed in `5ee4044` (2026-08-16): the **Real World view** of the Colony
(nuclei and friends) — `agents/nuclei_store.py`, `agents/nuclei_layout.py`,
`/nuclei/*` endpoints, the Colony tab's Digital/Real World toggle, rules 59–64.
Design lives in `private/nuclei/` (git-ignored). Not reviewed by hand yet.

**Uncommitted in the working tree**: the **Bahá'í Workforce light on the Real
World map** (rules 65–68, owner ask 2026-08-17). Clicking it fans the agents
out the way a family opens; real people can be put on the workforce and appear
in the Digital World too (derived, never a `workforce.db` row); a nucleus can
carry the WhatsApp group it already talks in; and a message can be drafted from
the drawer and either sent to one trusted contact or copied for a group. Files:
`agents/nuclei_bridge.py` (new — the one bridge), `nuclei_store.py`
(workforce grouping + `grouping_channels`), `nuclei_layout.py`
(`is_workforce_row`), `router.call_local` (new), `/nuclei/workforce*` +
`/nuclei/groupings/{id}/channel` endpoints, `WorkforceDrawer.tsx` (new),
`RealWorldGraph.tsx`, `ColonyGraph.tsx`, `ColonyPanel.tsx`, `GroupingDrawer.tsx`.
`scripts/test_nuclei.py` is now 225 checks. Not committed.

**Deferred / proposed, not started** (nobody should re-discover these from
scratch):
- **Devotional-gathering KIT pipeline** (N new cards + a program page) — the
  agreed flagship next per Sheraj's deeds-first direction.
- Abigail **Phase 4** (recovery rhythms) — read
  `docs/fable5-briefing-secretary.md` first.
- Grounding-bar tightening for bookmarks; retrieval enrichment; free
  share-image exports; multilingual packs.
- Relabel the Canva-autofill `log_run` entry from `"artist"` to whichever
  persona represents publishing (publish itself already logs under `steward`).
  Low value while autofill is switched off.
- Splitting `agents/api.py` (6,400 lines). Recommended only AFTER the tree
  above is committed, as pure extraction guarded by the test suites.

**Blocked on Sheraj:**
- **Create the WhatsApp `secretary_update` template** (UTILITY, body exactly
  `{{1}}`, English US, in WhatsApp Manager). Without it the outside-24h-window
  reminder fallback cannot send — it errored 132001 on 2026-07-11 and has not
  been re-checked. `GET /whatsapp/setup` has the walkthrough.
- Whether to move Abigail off Meta's sandbox test number (5-recipient limit,
  so every allowlisted guest must also sit in Meta's test-recipient list).
- **WhatsApp groups cannot be posted to by any software** — Meta's Cloud API
  has no group endpoint, so the workforce drafts a group message and Sheraj
  pastes it in. If automatic posting into nucleus groups ever matters more than
  staying on the official API, that is a decision about a different (unofficial,
  ban-risking) transport, not a code gap to fix.
- Reviewing and committing the pile above.

---

## Activity Log (newest first)

### 2026-08-17 — Claude Code (Opus 5) — the Workforce light opens
Owner ask: make the Bahá'í Workforce atom on the Real World map clickable —
agents fanning out like a family's petals, real people addable to the
workforce and visible in the Digital World too, a list of the agents and
what they are doing, and a way to write a WhatsApp message to a contact or a
nucleus's group. All of that is in, as rules 65–68.

The design decision worth knowing: the workforce is now a real *grouping* row
in `private/nuclei.db` of its own kind, so putting a person on it is an
ordinary membership and every existing drawer and query works unchanged — but
`nuclei_layout.is_workforce_row` keeps it off the table chairs entirely, so it
still draws at its own fixed light and a new nucleus cannot slide onto it. A
workforce-only person gets no second dot on the sky (rule 62); someone who
already gathers somewhere keeps the light they have and is reached by a thread.
The Digital World's people are merged into `GET /colony` at read time and are
never rows in `workforce.db` — `scripts/test_nuclei.py` proves it by reading
that database as raw bytes and requiring an added name to be absent.

**The honest limitation Sheraj needs to know:** Meta's WhatsApp Cloud API,
which the whole Secretary runs on, has no way to send into a group — only to
one person. So a group message ends in Copy plus a link that opens the group,
and the drawer says so on screen rather than showing a Send button that would
do nothing. Sending to one contact is real and reuses rule 28's tiers exactly:
an allowlisted contact goes out directly, anyone else queues for approval.
Drafting runs on the local Ollama model through the new `router.call_local`,
never a cloud one, because the prompt names a friend.

Drafting was exercised for real against local Qwen, not only stubbed, and the
first draft immediately invented a time ("around 7") from a brief that gave
none — so `invented_specifics()` now flags any time, day, date or amount the
message asserts that was not supplied, and the drawer shows it above the Send
button. It flags rather than edits: there is no safe mechanical rewrite of
free prose.

Then, second owner ask the same day: switching between Digital and Real World
now folds the sky on screen into the Bahá'í Workforce light and grows the other
one back out of it (rule 68b). The two views already share one 1440x720 space
and both draw that light, so it is the only body that survives the swap — it is
the hinge. One layout change was needed for it: the Real World's "add a nucleus"
and "add an institution" rows moved BELOW the map, because a control row above
it sat the workforce light ~110px lower in that world and the dot jumped at the
swap. Say so if you would rather have them back on top.

Left to do: Sheraj has not looked at any of this on screen yet — the fold in
particular is the kind of thing that has to be watched rather than reasoned
about — and the message tone is worth judging over a few more real drafts.
225/225 nuclei checks; colony/secretary/cancel/wallet/video suites all still
green; `npx tsc --noEmit` and a production `vite build` both clean.

### 2026-08-17 — Grok (grok-4.6), direct — Assembly people reach their family
Owner: clicking the Local Spiritual Assembly should line a friend to
their JY family name. A family is not a table seat, so it was missed.
Any institution or nucleus now continues from each person to their
household, same for every family.

### 2026-08-17 — Grok (grok-4.6), direct — a nucleus that reaches You stops there
Owner: clicking All You Can Eat lit You and then every other nucleus,
because You sit at all of them. From You, a nucleus now only continues
to walks recorded for that table — not to Scattered, JY, and the rest.
Other friends at that table still continue to their walks and tables.

### 2026-08-17 — Grok (grok-4.6), direct — LSA name; nucleus lines keep going
Owner: the map said Local Assembly — it is Local Spiritual Assembly
(two-line label). Clicking an institution or nucleus now continues
from each person there to who they walk with and the other tables
they sit at. JY still only shows walks recorded for JY. 130/130.

### 2026-08-17 — Grok (grok-4.6), direct — walks stay on their own work
Owner: clicking JY showed a friend who walks with You for a different
nucleus. A walk is for one work only. Clicking a table now shows only
walks recorded for that table — not every walk of everyone who sits there.

### 2026-08-17 — Grok (grok-4.6), direct — walk reaches a table through the friend
Owner: clicking Scattered drew a straight line to a friend who walks
with You. A walk is person-to-person; the table only joins people who
sit there. Path is now table -> You -> the friend, never table -> walker.

### 2026-08-17 — Grok (grok-4.6), direct — walking-with form starts empty
Owner: the three Walking with dropdowns were pre-filled (You, JY).
They now open on the placeholders: who walks with them, who they
walk with, and for which work.

### 2026-08-17 — Grok (grok-4.6), direct — walking-with threads only on click
Owner: a friend's line to You stayed lit. Accompaniment threads were
drawn all the time (and kept flowing when dimmed). They now appear
only when you click one of the two people or the work, same as every
other connection. `RealWorldGraph.tsx`.

### 2026-08-17 — Grok (grok-4.6), direct — accompaniment is a way to serve
Owner: walking with someone for a particular work (for example JY)
is service, not a seat at that table. Directed `accompanying` tie
names the work; the walker sits near it and is not made a member.
Person and grouping drawers record who walks with whom. Clicking JY
draws lines to both people. Rule 62. `scripts/test_nuclei.py` 129/129.
Official API restarted (scheduled task).

### 2026-08-17 — Grok (grok-4.6), direct — JY click reaches families and people
Clicking Junior Youth still links to each family, then also to the
people in those families (petals for family-only members, threads to
anyone who already sits elsewhere).

### 2026-08-17 — Grok (grok-4.6), direct — click any light to see connections
Clicking a person, family, nucleus or institution draws every link:
family, tables they sit at, institutions they serve, accompaniment.
Unrelated accompaniment threads quiet down while something is selected.

### 2026-08-17 — Grok (grok-4.6), direct — family lights bloom
Family-only people sit inside the household light. Click the family and
they open like petals; members who already sit at an institution stay
there and a gold thread joins them to the family. `scripts/test_nuclei.py`
110/110.

### 2026-08-17 — Grok (grok-4.6), direct — JY families and leaving institutions
Owner: list people in JY families; the same friend can be in a family
and on the Local Assembly; take someone off an institution without
taking them off the map. `household_members` table. Grouping drawer
adds someone already on the map and can end a membership. Household
drawer lists / adds / removes family members. Rule 62 one light.
`scripts/test_nuclei.py` 108/108.

### 2026-08-17 — Grok (grok-4.6), direct — institution orbits grow with people
Owner: institution rings should grow like nuclei. `institution_radius`
uses the same start-equal / grow-with-own-people rule (smaller base
and cap). People sit on that orbit. The map draws three rings so the
growth is visible. `scripts/test_nuclei.py` 98/98.

### 2026-08-17 — Grok (grok-4.6), direct — tighten institution spacing
Owner: people (especially around institutions) sat too far out. Seat
ring and name-pad pulled in; institution column restacked closer
(~100px apart). One-time clear of auto-saved institution chairs so
the live map picks it up. Names still must not cover. 94/94.

### 2026-08-17 — Grok (grok-4.6), direct — spread institution seats and names
People at a local institution were piling on its core. They now sit on
an even ring outside the light (wider as more friends arrive). After
seats are chosen, lights are separated so first-name labels do not
cover each other, not only the dots. `scripts/test_nuclei.py` 94/94.

### 2026-08-17 — Grok (grok-4.6), direct — Real World zoom and pan
Owner asked to zoom and move the map. Real World: scroll / pinch-as-wheel
zooms toward the cursor; drag empty sky to pan; + − and Show all in the
corner. Table-drag still places a nucleus. Camera remembered in
colony UI localStorage so switching tabs does not reset it.

### 2026-08-16 — Grok (grok-4.6), direct — local institutions, owner-added
Owner: House of Justice, National Assembly and Counsellors do not
belong on this map. Those seeded rows are archived on startup.
Local bodies (LSA, Regional Institute, Auxiliary Board, Area Teaching
Committee, or any name) are added from the Real World bar and rarely
removed like a nucleus. They sit in a column left of the Workforce.
`scripts/test_nuclei.py` 91/91.

### 2026-08-16 — Grok (grok-4.6), direct — institutions of the Faith
Owner asked for the institutions on the Real World map, left of the
Workforce light. Four seeded lights (House of Justice, National
Assembly, Counsellors, Local Assembly) sit in a fixed constellation —
not dragged, not archived, not in the table grid. Click one to note
who serves there; they sit close. You is not auto-seated. Rule 62.
`scripts/test_nuclei.py` 90/90.

### 2026-08-16 — Grok (grok-4.6), direct — people-dots squeeze, never cover
Owner: individual lights were stacking. After each person's seat is
chosen, `_separate_lights` nudges covering dots apart by the smallest
step (You moves less) and keeps them in a tight cluster — not a shuffle.
A neighbour who is not sitting on anyone leaves every other light where
it was (rule 62). `scripts/test_nuclei.py` 80/80.

### 2026-08-16 — Grok (grok-4.6), direct — take a friend off the map
Owner asked to remove people the same way a nucleus can leave the map.
`archive_actor` sets `archived_at` and ends live memberships; gatherings
stay; You cannot be archived (rule 62). Click a friend: "They no longer
sit here" if they sit at more than one table, and "Remove from the map…"
with the same confirm as a nucleus. `POST /nuclei/actors/{id}/archive`.
`scripts/test_nuclei.py` 76/76. API restarted on :8765 without --reload.
Verified live: You has no remove button; a friend's drawer does.

### 2026-08-16 — Grok (grok-4.6), direct — drag nuclei + Optimize locations
A seventh table was still covering a neighbour (`i % 6` stacked chairs).
Default chairs now use the 2x3 grid then the holes between them. You can
drag a table; the place is stored as `pos_x`/`pos_y` on that grouping
only. **Optimize locations** rearranges from size, shared people,
gatherings and ties (deterministic, owner excluded from affinity so his
seat at every nucleus cannot clump the map). Rule 62 updated.
`PATCH /nuclei/groupings/{id}/position`, `POST /nuclei/layout/optimize`.
`scripts/test_nuclei.py` 64/64. API restarted on :8765 without --reload.
Verified in the live dashboard: 7 distinct tables, button present, a
drag does not open the drawer.

### 2026-08-16 — Grok (grok-4.6), direct — Real World view of the Colony
Owner asked to implement the nuclei map after living with the
`private/nuclei/` sketch. Grammar A: each nucleus is a point of light
(the Vision in that place); people are smaller lights, once each;
distance to each nucleus is how they walk with *that* table.

`agents/nuclei_store.py` writes only to `private/nuclei.db` (rules 15/59).
No intimate-note column (rule 60). Layout in `nuclei_layout.py` (rule 62).
Colony tab: Digital World / Real World. Add a nucleus (you sit there);
click it to add a friend; click the friend to mark gathering / service
and "we sat together today." `scripts/test_nuclei.py` 45/45 (includes archive). API
restarted on :8765 without --reload. Dashboard HMR has the UI.

Left for later slices: gifts from the workforce, cycles, kinds editor,
Abigail's read-only snapshot tool, multiplication.

*Trimmed to the last 20 entries on 2026-08-16, per this file's own policy.
Everything older is in `git log` — `git log -p -- STATUS.md` shows every entry
back to 2026-07-07, and the lessons worth keeping were promoted into
`AGENTS.md` before the cut.*

### 2026-08-16 — Claude Code (Opus 5), direct — the Colony map shows performance as a sphere's finish
Owner ask: an agent doing well should be a shiny, glimmering, glorious sphere;
one doing badly should carry dross and scratches, as if it wants a polish.

New `dashboard/src/components/colony/finish.ts` (what the finish MAY be read
from) and `Sphere.tsx` (how it is drawn), used by `ColonyGraph.tsx` for both
agent bodies and team cores; two keyframe pairs in `index.css`. Rule 41b
records the invariants. Shine layers by viewing distance — aura and overall
lightness at a glance, gloss and grime up close, scratches and rim glints as
detail — and the hover card now states the finish in words, because the bodies
are ~15px on screen and texture must never be the only carrier.

Verified by RENDERING it, not by reading it: the real components were rendered
through `react-dom/server` against the LIVE `GET /colony` snapshot and
screenshotted with headless Chrome (harness written under `dashboard/.preview/`,
deleted afterwards — it lives outside `src` or `tsc` fails on its node imports).
Two rounds of fixes came directly out of looking at the first render: the rim
light read as a white crescent drawn on the ball (now a fading gradient stroke
on the silhouette), and the scratches read as white sticks lying on top (now
hairlines). `npx tsc --noEmit` and `npm run build` both clean.

**Finding for Sheraj, not acted on:** on the real data Amos (the Reviewer) is
the one tarnished body on the map — 115/396 clean. That is not a claim about
Amos's own work: `api.py` logs the Reviewer's `passed_review` as the verdict he
GAVE on someone else's listing (`review["passed"]`), so a strict reviewer scores
himself down. The map is faithful to the stored number and to what the Standing
panel has always shown (29%), so nothing was special-cased here — but the
attribution itself is arguably wrong, and changing it would move a trust level
that gates `/etsy/publish`. His call.

### 2026-08-16 — Claude Code (Opus 5), direct — STATUS.md harvested and trimmed; four lost facts promoted
The Activity Log had reached 52 entries against this file's own cap of 15–20,
making STATUS.md (24.6k tokens) a bigger per-session context cost than AGENTS.md
itself — and AGENTS.md tells every agent to read it first. Trimmed to 20 entries
(back to 2026-07-16): **13.7k tokens, ~10.9k saved per session.** Nothing is
lost — `git log -p -- STATUS.md` has every entry, and the dropped text was also
saved to this session's scratchpad.

Before cutting, the 32 doomed entries were read for knowledge that existed
NOWHERE else. Four things did, and are now in `AGENTS.md`:
- **Canva autofill is off by default and off in practice**
  (`CANVA_AUTOFILL_ENABLED` unset; 0/10 attempts ever succeeded). AGENTS.md had
  been describing it as the bookmark pipeline's final step, which would have
  misled anyone reading the pipeline map.
- **The WhatsApp `secretary_update` template does not exist in Meta** (error
  132001, found 2026-07-11), so the outside-24h-window reminder fallback has
  never worked. Now a Gotcha AND a Blocked-on-Sheraj item — it needs him to
  create the template, not a code change.
- **`card_compositor._SS = 2` supersampling**: every absolute pixel constant in
  that file must be multiplied by `_SS` or it renders at half its printed size.
- **Don't identify fresh records against a react-query cache** — the bug that
  made a correctly-rendered Spanish card pair invisible and read as "the run
  only did English".
Plus two smaller ones: Abigail's number is still Meta's 5-recipient sandbox
number (so allowlisted guests must sit in Meta's test-recipient list), and Codex
has corrupted files on Windows with BOM/cp1252 mojibake while otherwise
completing its task — read dispatched diffs for encoding damage, not just logic.

The Snapshot was also a month stale (dated 2026-07-09, describing the layout
editor as the newest work and listing mostly struck-through resolved items). It
now states current reality: what's live, the whole uncommitted pile, the
deferred backlog — including the **devotional-gathering kit pipeline**, which
was only recorded in a doomed entry despite being the agreed flagship next — and
what's genuinely blocked on Sheraj. It had also gone on calling the **Video
pipeline uncommitted** long after `58a785f` landed it, along with the card
redesign and exactness work; all three are committed. Verify against
`git status`, never against a previous Snapshot.

**Proposed commit breakdown, awaiting Sheraj's approval (nothing committed):**
The five uncommitted features are NOT cleanly separable — built consecutively
over four days without committing in between, they share the same `api.py` job
store (cancellation, `started_by`, and job adoption all edit `_start_job`), so
per-feature commits would need hunk surgery and could produce commits that don't
import. Three whole-file commits instead, no surgery, each coherent:
1. **Features** — all code and test scripts: the Colony (+ per-agent models,
   `TrustPanel.tsx` deleted), the wallet, Abigail's bridge to the teams, run
   cancellation, the finished-video shelf and product filters, and the
   post-commit video fixes.
2. **AGENTS.md + README.md** — the rules describing those features, plus the
   reorganisation.
3. **STATUS.md** — this hand-off log.
The real cure is upstream: commit per feature as it finishes, rather than
letting four days of interleaved work accumulate in one tree.

### 2026-08-15 — Claude Code (Opus 5), direct — AGENTS.md reorganised and compacted
Second pass the same day, at Sheraj's ask. `AGENTS.md` had grown to 1122 lines
(~18.6k tokens) in feature-arrival order, so the rules ran 55-57 → 30-34,58 →
1-14 → 35-41 → 50-54 → 42-49 → 15-29 and were out of order inside sections too.
It is now 993 lines (~16.6k tokens, -11%) with the rules in numeric order under
subsystem headings and a range table at the top, so "rule 24" is findable
without grepping.

**Nothing was renumbered** — over 170 code comments cite rules by number, so the
numbering is frozen; only the order of presentation changed. All 68 rules (58
numbers plus the 10 lettered ones) are verified present, and every backticked
identifier the old file mentioned still appears except six shortened forms and
one piece of dead archaeology.

The compaction came from redundancy, not substance: rules 18 and 22 were the
same rule stated twice (18 is now a one-line pointer to 22, keeping its number
live), the restart procedure and the `init_db()`-outside-`_connect()` warning
each appeared twice, and the Grok/Codex/agy invocation notes lost their
archaeology and moved to an appendix — they're needed rarely and were sitting
second in the file. Two stale check counts were corrected against real runs
(Colony said 94, is 135; wallet said 63, is 90) and counts now appear ONLY in
the Commands block so they can't drift in two places again. The "why" clause on
every rule was left intact — that's what stops an agent reintroducing the bug,
and it's most of the remaining length.

### 2026-08-15 — Claude Code (Opus 5), direct — finished videos on the Products page + product filters
Sheraj asked for two things: completed videos should show up on the Products
page, and the page should be easier to navigate.

**Videos on the shelf.** A finished video is now listed beside the bookmarks
and quote cards, DERIVED on every read rather than copied into a product row:
`video_assembly.list_finished()` builds each entry out of the video tables and
`GET /video/finished` adds the servable `/outputs/...` URLs (new rule 58 in
AGENTS.md). Deliberately not a `products` row — that would double-count videos
in the Steward's ledger and hand the print sheet / layout editor / Etsy publish
a product type none of them can act on, and it would drift the moment a project
is re-assembled or deleted. Clicking one opens a drawer with an inline player,
downloads for the mp4, the production record and the narration subtitles, and a
button that hands off to the Video tab (it sets the same persisted state the
tab restores itself from, rule 33c). A draft built from mock clips is badged as
such on the card and in the drawer (rule 32).

Two honesty details worth keeping: a project whose mp4 has been deleted from
`outputs/` drops off the shelf instead of showing a player that can't play, and
the length is the file's REAL length, not the plan's — the difference is not
academic ("Adam's New Day" plans 122.5s and measures 110.4s). ffprobe costs
seconds per call and this list is polled, so it runs once per file and the
result is remembered on the export record against the exact path it measured;
when ffprobe can't read a file, the plan's length is shown flagged and the UI
prefixes "~" rather than storing a guess as a measurement. The suite caught
that one — the first version labelled the fallback as measured.

**Filters.** New toolbar on the Products page: search across titles, themes,
quotes, citations and tags; kind chips (Everything / Bookmarks / Quote cards /
Videos, with counts); a review-result filter; and sort by newest, oldest,
score or title. "Showing X of Y" and a Clear button are always visible. The
chosen view persists in localStorage (`settings.getProductsUi`), the typed
search does not. Videos drop out while a review-result filter is active and
the bar says why — they're reviewed shot by shot, not scored out of 10, so
listing one under "Approved" would be a false claim about quality.

Verified: `scripts/test_video_pipeline.py` 288/288 (was 266 — 22 new checks in
`test_finished_shelf`, including the missing-file case, the measurement reuse
and its staleness guard, mock labelling, and that no video reaches the products
table); colony 135/135, job-cancel 24/24, secretary-colony 92/92; dashboard
typecheck and production build clean; and the live endpoint checked against the
real DB, which shelved Sheraj's three real videos (Grace Drop 60.2s/17 shots,
Adam's New Day 110.4s/35, Test Scene 1 50.7s/16). The API scheduled task was
restarted to pick up the new route — no jobs were running at the time. Nothing
committed; the tree is Sheraj's to review as usual.

### 2026-08-14 — Claude Code (Opus 5), direct — Abigail works with the teams
Sheraj asked for Abigail to be able to interact with all the teams: report back
what they did, request them to do jobs, and give them the information they need
to do a good job based on what he asked her for or set as her goal. Built as a
single bridge module, `agents/secretary_colony.py`, plus five Claude tools in
`secretary_tools.py` — `workforce_report`, `ask_agent`, `set_team_goal`,
`brief_agent`, `request_team_job`. New rules 50-52 in AGENTS.md.

The gate is **talking is immediate, making is approved**: reading what the
teams did, asking Ruth a question, setting a goal and writing a brief all
happen at once (an assistant who needs a permission click to ask a question is
not an assistant), while `request_team_job` always queues in her existing
approvals queue as kind `workforce_job` and starts nothing — a run spends real
money and saves a product. Approval runs `api.launch_team_pipeline`, extracted
so the Colony's goal-launch endpoint and her approved job share one
implementation (rule 40). She has no tool that approves anything; the suite
asserts that.

She is the only agent with personal data, so the crossing is checked in code:
`assert_shareable` refuses contact details and any 12+ word span copied
verbatim from her private memory notes before it can reach `workforce.db`. It
reads her memory notes ONLY — including his task list would have refused the
legitimate relay ("make twenty cards for the devotional") this feature exists
for. It is narrow on purpose and documented as narrow.

Two things fixed along the way. Standing instructions only ever reached Colony
*chat*, while the dashboard's own label promised "in the pipelines as well as
in chat" — they now ride the same single injection point as the team goal
(`system_prompt_builder._instructions_steer`, capped, fails open), so a brief
genuinely shapes pipeline work. And goals now record `set_by`, shown on the
team card, so a goal Abigail set on his behalf never looks like one he typed.

Verified live, not just offline: she briefed Clara (the instruction is in
`agent_settings` and appears in a real pipeline prompt), relayed a question to
Ruth who answered from the actual index (the exchange is in Ruth's Colony
history behind the relay label), reported honestly that the Print Studio had
been idle, and queued a card request that started nothing until it was
declined. One real bug fell out of that live run and is fixed: her
"did you actually act?" check tested "no effect recorded" rather than "no tool
called", so a read-only turn whose answer contained an action verb was wrongly
flagged as an action that silently failed — `effects["tool_calls"]` now makes
the real condition testable.

**Second pass the same day, after Sheraj used it.** Four things came out of
real use, and the first three share one root cause:

- *"It says see the Pipeline tab but the Pipeline tab shows nothing."* The
  panel only ever polled the job id it had created itself, so an approved
  run genuinely ran with the screen blank. Jobs now record `started_by`
  ("sheraj"/"abigail"/"colony") and the panel ADOPTS any running job of a kind
  it can display, with a line saying whose it is (rule 53).
- *"Approval should launch it automatically."* It already did — the record
  shows her run at 19:13:33 and his hand-started duplicate at 19:14:36. The
  invisibility above is what made it look otherwise, and it cost a card.
  Nothing to fix in the launch path; everything to fix in what the screen said.
- *"The Colony map should show what a team is doing."* `colony.team_activity`
  derives each team's live work from the same job store (`JOB_KIND_TEAM`), so
  the team core pulses, carries a "Working — making a batch of quote cards"
  line, and the drawer shows the live progress and who started it.
- *"The Pipeline tab says Bookmark while something else is running."* While a
  run is in flight the mode is read from the JOB, not from whatever this tab
  last had selected, and the two-way toggle becomes a readout — "Quote cards
  (batch) · running". A disabled control showing the wrong mode was a small lie
  about what the team was doing, in the one place he goes to find out.
- *"She said she can only do one card at a time."* `request_team_job` now takes
  `count` up to 19 and Ruth finds that many verified quotes BEFORE it queues —
  one request, one approval, one hands-free batch, and the approval names the
  actual quotes (rule 51b).

One more fault found while testing live: she listed two already-resolved
actions as still waiting, reading her own earlier reply instead of the queue.
Her prompt now always states the queue (even when empty), and because that
alone did NOT fix it, `_approval_ground_truth` corrects any `#id` she names
against the real pending list in code (rule 54) — verified live, and she
self-corrects on the following turn once the correction is in her history.

**Cancelling a run** (`POST /pipeline/status/{job_id}/cancel`, rules 55-57,
`scripts/test_job_cancel.py`). Cooperative, because a thread cannot be safely
killed: the job is flagged, a run paused for input is woken, and the worker
stops at its next `progress(...)` boundary. `JobCancelled` derives from
BaseException so the codebase's many `except Exception` blocks — the batch loop
above all — cannot swallow it and keep spending. A cancel closes out the task
row it was in the middle of and records no result, but never deletes
`task_runs`, saved products or generated artwork. Verified live against a real
card run: cancelled during the Artist's brief, task marked `cancelled`, zero
products saved, no team left lit in the Colony map.

Verify: `python scripts/test_secretary_colony.py` (92 checks, offline, free) —
plus the existing suites, all still green (colony 135, video 266, wallet 90).
The backend was restarted, so this is live now. A real 3-card batch (#15,
"service to humanity") is sitting in her approval queue as the end-to-end
demo — approving it spends money on three card runs.

### 2026-08-14 — Claude Code (Opus 5), direct — the project crypto wallet
Sheraj asked to connect Nora to a cross-chain crypto wallet. The
irreversibility risk was put to him plainly first (everything else in this repo
is free, reversible or approval-gated; a transfer is none of those). He then
chose the most capable option at every step: all four purposes, Nora able to
SEND within a hard cap, and a wallet created in code. That is his call and it
is built in full — the work went into making it as safe as it honestly can be
rather than into narrowing it.

`agents/wallet.py`: one EVM address across Base / Arbitrum / OP Mainnet plus
Base Sepolia. Reads are raw JSON-RPC (no web3.py); signing uses `eth-account`
alone, keeping the key-touching surface minimal. Controls, all in code:
owner-only destination allowlist (no tool can write it — rule 28's discipline),
three-tier caps computed from the on-chain ledger rather than the model's
claims, USDC-only for the agent, mainnet opt-in via `WALLET_ALLOW_MAINNET`
(default OFF, so it can be exercised for real with nothing at risk), a
watch-only treasury Nora has no key for, and `verify_token()` which checks
`symbol()`/`decimals()` on-chain before every transfer. The four USDC contract
addresses were verified live against their RPCs rather than trusted from
memory — a wrong token address destroys funds silently.

Nora gets `wallet_balances` and `wallet_send`; `wallet_send` is deliberately
NOT in `GATED_KINDS` (that would make every payment an approval and remove the
autonomy asked for) and instead has its own tiered gate. There is also a
dashboard send form with no LLM in the path at all, which is the safest way to
move money and is likely what gets used most. New Treasury view in the Colony
tab. `/steward/report?include_wallet=1` folds holdings into Nora's report;
it is opt-in because the routine P&L poll should not pay for an RPC round-trip.

`scripts/test_wallet.py` — 63 offline checks, weighted at the guarantees:
allowlist cannot be bypassed (including via `bypass_limits`), caps come from
the ledger, failed sends do not consume the daily cap, a non-Steward cannot
reach the money tool, guest WhatsApp paths stay tool-less, and mainnet is
unusable until enabled. Colony 124/124 (its "toolsets stay small" assertion
correctly caught the Steward's two new tools — carved out as a narrow
documented exception rather than raising the limit for everyone) and video
266/266.

**Signing verified later the same day.** Sheraj installed `eth-account`
(0.13.7) and set `WALLET_PASSPHRASE`. The suite now exercises the real thing on
a throwaway key in a temp dir: keystore encrypt/decrypt round-trip, the raw key
absent from the keystore file on disk, a wrong passphrase failing to open it,
refusal to create a second wallet over the first, a real signature that
recovers to its own address, and — the useful one — an actual `eth_estimateGas`
against the LIVE USDC contract on Base Sepolia, which parsed the transfer and
rejected it only for insufficient balance. That is proof the ERC-20 calldata
encoding is correct against the real contract rather than merely self-consistent.
80 checks, still offline-safe and free (nothing is ever broadcast).

That work also caught a genuine config bug: `wallet.py` read every cap at
module-import time but never called `load_dotenv` itself, so importing it
before `router.py` silently gave all of them their built-in defaults — a
tightened `WALLET_MAX_PER_TX_USDC` would not have applied. Fixed, with a
regression check on the load order.

**Testnet proven, then MAINNET SWITCHED ON (2026-08-14, Sheraj's call).** He
created the wallet (`0x5872AF78c94CF99D07e0B871f36DFd6103d92862`), funded it,
and sent a real 5 USDC transfer to his own Coinbase address on Base Sepolia —
the full path works end to end. `WALLET_ALLOW_MAINNET=true` is now set, so Base,
Arbitrum One and OP Mainnet are live. Real balance is still 0; the wallet needs
real USDC plus a little ETH for gas (Base is cheapest) before it does anything.
Treasury/multisig deferred — no permanent multisig for now.

Going to mainnet immediately exposed two bugs that only matter once money is
real, both fixed:
  - `spent_today_usdc()` counted TESTNET sends against the daily cap. His $5
    practice transfer had already consumed $5 of the real $50 budget. Play
    money is now excluded.
  - `balances()` summed testnet and mainnet into one `total_usdc`, so a wallet
    holding nothing but test tokens reported "15.00 USDC" of holdings. Real and
    play money are now totalled separately and never mixed, in the API, in
    Nora's tool output and in the UI — a false holdings figure is the one thing
    the Steward exists not to produce.

An earlier fix had a knock-on: because `wallet.py` now loads `.env` itself, the
test suite started asserting against Sheraj's live config and four checks
failed the moment he enabled mainnet. The suite now pins
`WALLET_ALLOW_MAINNET=false` before importing, and additionally checks the gate
is strict opt-in (blank, "0", "yes", "ture", unset all leave it off). 90 checks.

Operational reality worth restating for whoever reads this next: the hot
wallet's key sits on this machine, encrypted with a passphrase stored in `.env`
on the same machine. That protects against someone copying the keystore file
alone, NOT against anyone who has the machine. It should hold only a small
working float; anything else belongs in a wallet whose key is not here.
**Not committed.**

### 2026-08-13 — Claude Code (Opus 5), direct — per-agent model selection
Sheraj asked to be able to pick the model each agent uses. Two decisions were
his: Abigail is included but restricted to Claude models, and moving a free
agent onto a paid model warns clearly and saves on one click.

`agents/models.py` discovers the model list LIVE from all three providers
(Ollama `/api/tags`, xAI `/models`, Anthropic `/v1/models`) and filters what
isn't a chat model — `nomic-embed-text` (it backs the citation index) and
`grok-imagine-*` (image/video endpoints). Storage is a new `agent_settings.model`
column. The router became agent-aware: `call_llm`/`call_llm_agentic` take
`agent=`, `_call_ollama` gained the `model` param it used to hardcode, and
`agent=` was threaded through EVERY real call site (consultation, scribe,
reviewer, artist, translator, director, x_post, api's card reflection) so the
choice applies to pipeline runs and not just chat. An AST sweep confirms the
only remaining site without it is consultation's team-synthesis call, which
belongs to no single agent. Abigail's Claude path takes the same override via
`call_claude`/`call_claude_agentic`.

**The provider boundary is code, not UI.** Verified live: scribe→claude-opus-5
and librarian→claude-sonnet-5 both 422; secretary→grok-4.6 and
secretary→qwen3:8b both 422; a nonexistent model 422s. Also verified live
end-to-end that a choice actually takes effect — Clara was set to llama3.1:8b,
answered, and `ollama ps` showed **llama3.1:8b** loaded rather than the default
qwen3-16k.

Three real defects found and fixed along the way: a first pass at threading
`agent=` inserted it before the positional messages list and broke four modules
(rewritten to insert at the matching close-paren); Abigail's Claude default was
labelled **"(free)"** because "paid" was computed as `provider == xai`; and the
Settings tab's "Model routing" card was already wrong before this work — it
listed six task types as Grok-routed that have always run locally, and credited
image vision to Claude Haiku when it goes to Grok. All corrected.

`scripts/test_colony.py` now 124 checks, all passing; video suite still
266/266. **Not committed.** Server restarted to pick up the endpoints.

### 2026-08-13 — Claude Code (Opus 5), direct — the Trust tab became the Colony
Sheraj asked for the Trust tab to be replaced by something bigger: a visual
map of the whole workforce where he can see performance and how the agents
interact, chat with them individually, change their settings, "perform the
same functions that they would do in the UI", consult teams and give teams
goals. He supplied a reference image (dark orbital constellation). Four
decisions were his, asked up front: the tab is called **Colony**; goals both
steer and launch; agent chat can really act with writes confirmed; teams are
grouped **by pipeline**.

**New backend** — `agents/colony.py` (teams, per-agent settings, team goals,
per-agent chat history, the approval queue, and the handoff graph DERIVED
from `task_runs` so every line on screen is recorded work, not a diagram),
`agents/colony_tools.py` (small per-agent toolsets + the gate),
`agents/colony_chat.py` (per-agent chat + a lean team-consultation
round-robin), and `router.call_llm_agentic` — a tool-calling loop for **Grok
and Ollama**, which had to be written because rule 16 reserves Claude for
Abigail and the workforce agents therefore cannot borrow
`call_claude_agentic`. ~15 `/colony/*` endpoints in `api.py`. Goal steering
is injected in ONE place (`system_prompt_builder._goal_steer`) so it reaches
pipeline runs and chat alike; it is hard-capped at 240 chars and fails open,
because most agents run on local Qwen (rule 1).

**New frontend** — `dashboard/src/components/colony/`: a hand-rolled SVG
constellation (no new dependency), an agent drawer with performance /
chat / settings, a team drawer with goals and consultation, and the approval
queue. Nav `trust` → `colony`; `TrustPanel.tsx` deleted, its trust scores and
product-quality history preserved inside the Colony's Performance view.

**Verified live, not just in tests.** The graph renders real data (16 real
handoff edges out of the actual run log, including `director → videographer`
from the day before). Nora really called `spend_report` on local Qwen and
reported $27.19/$8.55 — matching the DB exactly. Theo was told "generate this
now" and QUEUED instead: no file appeared in `outputs/`, `image_gen` spend
stayed at $17.05, and only the $0.01 chat itself was metered. Four UI defects
were found by looking at screenshots and fixed (teams clipped below the fold;
single-member teams sitting on their own label; team names under the core
spheres; the canvas letterboxed into the middle third) — the team label now
sits ABOVE the core because that position is provably never occupied, while
bottom-centre always is for odd member counts. One real bug was caught by
live approval: `run_approved_action` read `result["translation"]` when
`translate_quote` returns `text`, so an approved translation reported success
with empty content; fixed and pinned by a test using the real return shapes.

`scripts/test_colony.py` — 94 offline checks, free, all passing;
`test_video_pipeline.py` still 266/266. **Not committed** — Sheraj reviews
first. The API server was restarted twice during the session to pick up the
new endpoints (it does not auto-reload).

### 2026-08-13 — Claude Code (Opus 5), direct — movement descriptions + the invisible chain error
Chaining worked, but Sheraj reported two things: it "didn't automatically work
by itself the first time — I had to generate the first clip", and the result
was "still kinda trippy and weird". Both were investigated against his REAL
finished project ("Grace Drop", 17 shots, `c5999fe9`) rather than guessed at.

**The first-clip problem was not the chain.** `test_cold_start_chain` proves a
chain starts from nothing — no frames, no clips — and generates its own
opening frame, including when shot 1 is (wrongly) marked `continuous`. What
actually failed was VISIBILITY: the endpoint returned 200 and a job id even
when the run was doomed, and `useVideoJob`'s `onDone` cleared the job on the
same tick it reported the error, so a failed chain flashed for one poll and
then looked like a dead button. Fixed both ends — `video_pipeline.chain_preflight`
now runs synchronously in the endpoint and refuses with a 400 and a plain
reason (ComfyUI down, no shots, no PyAV), and a finished job's error stays on
screen. Mutation errors render too (rule 33e).

**The "trippy" look had three measurable causes**, all in the stored plan, all
now repaired in code (rule 33d). Measured across his three projects: 8 of 17
shots in Grace Drop had motion prompts that told the model *nothing moves*
("No physical movement in the raindrop") while the shot's own action was the
drop descending; 4 shots repeated the previous shot's motion text verbatim;
and 6 `continuous` shots declared a framing/angle the frame they reuse cannot
have. `video_director.repair_motion` strips stillness clause-by-clause,
rebuilds duplicates from the shot's own action (keeping ambient detail), and
makes continuous shots inherit the previous camera setup — reporting every
change. `build_motion_prompt` was rewritten to state the movement, tie it to
the real clip length, name what holds still, and pin the camera; a chained
clip is now told it is CONTINUING a shot in progress rather than rendering a
new one. Existing projects don't need re-planning: "Check the movement
descriptions" in the Storyboard tab runs the same pass in place, free.

Honest on results: an A/B on real shots (2 shots x 2 seeds, ComfyUI/LTX,
numpy block-matching to separate real motion from morphing) moved the
real/morph ratio 0.21 -> 0.24 and raised real motion 22% — a modest gain
inside the noise, not a transformation. The defects were fixed because they
are objectively wrong, not because the metric proved a big win. Also flagged
but deliberately NOT auto-fixed: Grace Drop plans the raindrop landing four
separate times, which no amount of chaining can make coherent —
`repeated_action_warnings` surfaces that in the Review tab for Sheraj to
merge or re-plan, since it is a story judgement.

**Then, at Sheraj's go-ahead: cinematic pacing** (rule 33f). The flagged
next lever was fewer, longer, non-overlapping beats, and the measurement that
mattered turned out to be CUT count rather than shot count — a run of chained
`continuous` shots is one unbroken take, so "Adam's New Day" cutting every
4.7s across 122s was the restlessness, not its 35 shots. `direction.pacing`
("standard" | "cinematic", default unchanged) drives three deterministic
passes in `video_director`: `beat_shot_budget` caps each beat at its
`distinct_moments` (new analysis field) so the video ends when the story does
rather than padding to the target; `dedupe_shots` removes a moment an earlier
nearby shot already covers; `enforce_cut_policy` cuts only at beat boundaries
and real changes of place or time. `MAX_SHOT_SECONDS` was deliberately NOT
raised — rule 31's ceiling is a hardware fact.

Applied to the existing plans (simulation): Adam's 35 shots/26 cuts ->
28 shots/10 cuts, a cut every 11.2s instead of 4.7s. Grace Drop's cut count
went 7 -> 8, which is CORRECT rather than a regression: its shot 1 was marked
`continuous` with nothing before it, and two other "continuations" crossed a
beat AND a location/time change — false claims that made chained clips warp.
So existing projects don't need re-planning either: `POST .../repair-motion`
now takes `{"recut": true}` ("Check movement and recut" in the Storyboard tab)
and applies the cut policy in place. That path is deliberately NON-DESTRUCTIVE
— it never deletes a shot, because a dropped shot may already have a rendered
clip; duplicates stay as warnings for Sheraj to remove himself.

255/255 checks (was 172), typecheck and build clean, 204 existing project
assets verified intact after cleaning up 54 test renders. Nothing committed.

### 2026-08-12 — Claude Code (Opus 5), direct — chained generation (fixes the "slideshow" look)
Sheraj's first finished video came out "kind of like a trippy slide show" and
he correctly diagnosed why: shots were being generated INDEPENDENTLY, each
from its own text prompt, so every shot invented its own version of the
character and place. His proposed fix — render a clip, take its END FRAME as
the next clip's start image, and adapt descriptions from what the video
actually produced — is now the recommended path.
`video_pipeline.generate_chained()` runs shots sequentially: clip → extract
its REAL final frame (`videographer.extract_last_frame`) → that file becomes
the next shot's first frame. A `continuous` shot reuses it directly; an
`editorial_cut` must regenerate (the angle changes on purpose) but is
anchored by `video_director.observe_frame()`, a vision read of what the
previous clip really showed, folded in through `build_continuation_prompt` —
so the next prompt describes reality rather than the plan. New rule 33a in
`AGENTS.md`. Two things had to be fixed for it to work at all: **PyAV was not
installed**, so extraction would have silently returned None and every link
would have degraded back to an independent shot (now installed, in
requirements, and preflighted so the chain refuses to start rather than
faking it); and LTX's image adherence was the template's 0.15 ("loosely
inspired by this image"), wrong when the image IS the previous clip's last
frame — chained runs now pass `image_strength=1.0` while independent runs
keep 0.15. Measured on a real 3-shot render: the join between consecutive
clips differs by 3.8 and 3.3 out of 255, against ~90-100 for independently
generated frames — roughly 25x better, i.e. visually seamless. 172/172 in the
suite (new tests assert the byte-identical frame handoff, cut anchoring,
resume, and the strength plumbing), 16/16 regression, clean typecheck/build.
Open: chaining fixes continuity BETWEEN shots, not motion quality within a
clip, and a 7-beat story still cuts every 3-4s — if it still reads as choppy
the next lever is fewer/longer shots, not more chaining.

### 2026-08-12 — Claude Code (Opus 5), direct — plan timeouts, beat resilience, Video tab state
Two failures Sheraj hit in real use. (1) **Planning died mid-run**: a 7-beat
plan timed out on beat 2 against Ollama's hardcoded 120s and threw away six
completed beats. Directly caused by the detailed-prompt work earlier the same
day — richer prompts take longer than the old default allowed. Fixed at three
levels: `call_llm` gained a `timeout` override (default unchanged for every
other caller) with the Director passing 300-420s; `plan_beat_shots` retries
once with a deliberately LEANER prompt rather than repeating an oversized
request that will fail identically; and `build_plan` no longer aborts — a
failed beat becomes a placeholder shot flagged `needs_replanning` (badged in
the storyboard) and planning continues, ending `planned_with_gaps` with the
reason in the notes. Also added `VIDEO_DIRECTOR_MODEL=grok` as an opt-in for
when 10-15 minutes of local planning is too slow; default stays local/free.
(2) **The Video tab reset on every tab switch** — React unmounts the panel, so
the open project, sub-tab and running job were lost. Now persisted via
`settings.getVideoUi`/`patchVideoUi` (the pattern the Pipeline tab already
used for its active job), and `useVideoJob` reattaches to a running job,
treating a 404 as "the API restarted" instead of polling a dead id forever.
Fixed a related display bug while there: job step timestamps are naive UTC
from the backend, so the activity log rendered them ~7h adrift from the
entries beside them (5:59 PM next to 12:59 AM for one event). Verified
159/159 in the suite (new: timeout config, leaner retry, and a
failed-beat-survives test), 16/16 regression, clean typecheck and build, plus
a live multi-beat plan. Worth noting for anyone debugging slowness: the GPU
was at 7.7/8.2 GB with LM Studio, Ollama and ComfyUI all resident, which is
most of why planning was crawling — `scripts/watch_gpu.ps1` (added this
session) shows this in plain language.

### 2026-08-12 — Claude Code (Opus 5), direct — much more detailed video prompts
Owner ask: make the generator's prompts "extremely detailed but very simple"
— rich description, uncomplicated cinematography. Implemented as two
explicitly separate axes (now hard rule 30b in `AGENTS.md`). Prompts grew
from ~30 words to ~300: `plan_beat_shots` now demands 50–90-word frame
prompts and gained six detail fields (`subject_detail`, `setting_detail`,
`texture_notes`, `atmosphere`, `depth_notes`, `lens`); `build_frame_prompt`
and `build_motion_prompt` emit flowing PROSE instead of comma-joined tags;
the continuity bible's 30-word cap became a 15–35-word floor with named
materials. Confirmed from ComfyUI's source that neither encoder truncates
(`lt.py`/`wan.py` both `max_length=99999999`), so length is free quality
rather than a risk. Critically, `complexity_score` had to be DECOUPLED from
verbosity or every rich shot would be auto-split: `_HANDS` now matches
hand-intensive work rather than any mention of hands, `_CROWD_OK` waives a
distant/still crowd, and the word cap applies only to `primary_action`.
Two defects the richer output exposed and fixed: the continuity block was
emitting tag soup (`; ; ;`, pipes, run-together phrases) for a third of the
prompt, and shot-level detail CONTRADICTED the bible on the same character
("salt-and-pepper beard, indigo robe" vs "stubble, black hair, undyed wool
tunic") — the bible now wins for any referenced id, since contradiction is
worse than either description and it defeats the continuity the bible
exists to provide. Verified live twice on the real local model
(3 shots, 296–308-word frame prompts, complexity 0–2 against a limit of 3)
plus 141/141 in `scripts/test_video_pipeline.py`, 16/16 regression, clean
typecheck.

### 2026-08-12 — Claude Code (Opus 5), direct — the Video Generation pipeline
Built the third product pipeline on top of the morning's video engine work:
a scene, story or historical account becomes MANY simple 3–4 second shots
that assemble into a coherent video (bookmarks/quote cards are secondary
source options — deliberately not the identity of the feature). Staged as
source → direction → analysis → continuity bible → shot plan → first/last
frames → clips → review → export, reusing the existing background job store,
`workforce.db`, `outputs/` asset convention, `call_llm` routing and the
dashboard's component system rather than introducing anything parallel. New
modules: `video_store` (4 tables), `video_director` (beat-by-beat planning
so prompts stay inside local Qwen's context), `video_safety`,
`video_provider`, `video_pipeline`, `video_assembly`; ~30 `/video/*` routes;
a new dashboard Video tab. Four new hard rules (30–34 in `AGENTS.md`) came
out of it. **Three findings worth remembering.** (1) The sacred-figure
safeguard's first version matched a raw ASCII apostrophe and therefore
missed `Bahá’u’lláh` — the typographic spelling this repo and every real
source actually use — while its own leak-check, sharing the broken matcher,
reported all-clear; it now normalises (strip diacritics, drop
apostrophe-likes) before matching. (2) `WanFirstLastFrameToVideo` exists in
ComfyUI, submits cleanly and finishes in 21s against Wan 2.2 TI2V-5B, and
returns corrupted garbage — node presence and a zero exit code both claimed
"first-and-last-frame supported"; only inspecting pixels disproved it, so
capability is hardcoded from that probe and the pipeline shows its actual
fallback instead of pretending. (3) `init_video_db()` nested inside
`state.init_db()`'s open write transaction deadlocked SQLite and silently
skipped the video tables on a FRESH database (every endpoint would then fail
with "no such table"); moved outside, with a fresh-DB regression test.
Verified: `scripts/test_video_pipeline.py` 117/117 (offline, stubs the LLM,
mock provider), a 16-check regression pass over the pre-existing endpoints,
`npx tsc --noEmit` clean, `npm run build` clean, and a live end-to-end run
against the real API + local Qwen. Not committed. Open: real clip generation
is slow on an 8GB card (~3 min/clip on Wan), so a 60s video is a long
sequential run — worth a look at batching or a smaller draft pass.

### 2026-08-12 — Claude Code (Sonnet 5), direct — local video generation, infrastructure only
Set up local text/image-to-video generation outside the repo (ComfyUI
portable at `C:\Users\Sheraj\ComfyUI_windows_portable`, LTX-Video 2B
distilled-fp8 and Wan 2.2 TI2V-5B models, on the owner's 8GB RTX 4070) and
verified both work end-to-end. Along the way hit and root-caused a real
bug: LTX's `LTXVScheduler.terminal` at `0.0` silently renders pure black
video with no error — confirmed by A/B test (`terminal=0.0` → brightness 0,
`terminal=0.1` → normal), documented as a load-bearing constant. Asked
Sheraj how this should connect to the actual app; he chose "just wire in
the engine, no UI yet." Wrote `agents/videographer.py` — a pipeline-free
HTTP client (`generate_video(prompt, image_path=None, model="ltx"|"wan22")`)
against ComfyUI's `/prompt` → `/history` → `/view` API, verified live for
both models (non-black output, correct resolution/frame count, real
motion) via a throwaway smoke test, not just imported successfully. No
`log_run`/`record_spend` calls in the module by design — no task_id/product
context yet (not pipeline-wired) and no cloud cost to meter; a future
pipeline step should add both around the call site. Added `COMFYUI_URL` to
`.env` and a new "Local video generation" section to `AGENTS.md`. Nothing
committed (per the "only commit when asked" norm) — working tree has
`agents/videographer.py` (new), `.env`, `AGENTS.md`, `STATUS.md`. Open:
whether/how this becomes a real pipeline step (e.g. a promo-video pass on
finished products) is still Sheraj's call, not decided yet.

### 2026-08-05 — Claude Code (Fable 5), direct — owner-selectable quote-card sources (rule 11 rewrite)
Owner ask (2026-08-04): let quote cards draw from writings beyond Ruhi Book 1
— any of the 7 vector-DB texts (one, several, or all), plus a deliberately
risky web option. Built as a tiered-trust source selector; **rule 11 in
AGENTS.md is rewritten** (default is still Ruhi-only; expansion is explicit
per-run, never a fallback). Backend: `librarian.retrieve()` gained slug
filtering + `list_library_sources()`; `agents\api.py` gained
`_parse_card_sources` / `_card_retrieve` (origin-tagged citations) /
`_resolve_library_quote` + `_find_verbatim_span` + `_span_boundary_ok`
(verbatim-chunk tier — prints the CHUNK's exact chars, sentence-clean
boundaries; overlap fragments like "immensity of the heavens..." are
structurally rejected, caught in testing) / `_resolve_pinned_quote_multi`
(Ruhi → library → opt-in web) / `_lib_excerpt` / web fetch+rank helpers
(stdlib HTML parse, boilerplate + "¶1:"-marker filters, local-embedding
ranking). New `GET /quote-sources`; `sources` (+ per-quote citation labels)
on `/ruhi-quotes`, `/pipeline/run-card`, `/pipeline/run-card-batch`,
`regenerate-card-quote` (honors the card's stored sources). Cards store
`quote_verified`/`quote_provenance`/`quote_sources` in card_copy; web-tier
quotes are NEVER grounded and badge as "Web quote — wording not verified"
in the gallery and results. Consultation/reviewer wording stays code-owned
per tier (`CARD_PIN_LABEL_*`, `CARD_SOURCE_SCOPE_EXPANDED`,
`CARD_QUOTE_SOURCING_NOTE_EXPANDED`; a web pin is described as NOT
machine-verified). Dashboard: source checkboxes + "All library texts" /
"Ruhi only" buttons + amber risky-web row (URL input) in PipelinePanel;
finder items and quote boxes carry per-quote provenance labels.
VERIFIED: offline tier matrix (parse/resolve/reject, finder combos, batch
gate) + an ACCIDENTAL full paid run (TestClient batch test ran the real
pipeline, ~$0.18 spend, task b822a4c7) which end-to-end proved the library
tier — Paris Talks quote, correct provenance fields, clean render (viewed);
the junk product row + files were deleted after inspection. Web tier proven
against reference.bahai.org (old library works; bahai.org's new JS reader
returns no text — the UI hint says so). NOT yet exercised live: a
deliberate paid run with lib/web sources from the dashboard.

### 2026-08-02 — Claude Code (Fable 5), direct — Librarian quote-finder for the card form
Owner ask: a way to ask the Librarian for quotes on a topic and have them fill
the card form's Exact Quote boxes. New `GET /ruhi-quotes?topic=&count=`
(`agents\api.py`, next to the batch endpoint): semantic search via
`retrieve_ruhi_book1` ONLY (rule 11 — an unbuilt index or unreachable Ollama is
a 503 hard stop, never a fallback), free and local, no LLM/spend. Every result
is canonicalized through `_resolve_pinned_quote`, so a suggestion pasted into
the batch endpoint verifies by construction; a passage too long for the card's
readable minimum is shortened to its longest sentence-boundary prefix + " . . ."
(the resolver's own permitted transform) or skipped — currently all 67 corpus
entries fit unshortened, so that branch is future-proofing. Supporting refactor
in `agents\card_compositor.py`: the front-face fit loop moved verbatim into
`_fit_quote_block`, shared by the render and the new `quote_fits_card()`
pre-flight check — render verified byte-identical (sha256 front+back) before/
after. Dashboard: "Ask Ruth (the Librarian) to find N quotes about <topic>" row
above the quote boxes in `PipelinePanel.tsx` (topic falls back to the theme
field; results append to non-empty boxes, deduped, capped at BATCH_MAX, with a
plain-language note; errors in an ErrorNote), `suggestRuhiQuotes` in
`dashboard\src\lib\api.ts`, types in `types.ts`. Backend restarted; verified
live over HTTP (topics "deeds not words"/"prayer"/"the soul...", counts 3-19,
all items re-resolve through the batch gate; 422s for bad topic/count).

### 2026-07-31 — Claude Code (Opus 4.8), direct — quote-card batch cap 8 -> 19
Owner ask: raise the per-batch quote-card limit from 8 to 19. Two constants,
kept in sync: `_CARD_BATCH_MAX` in `agents\api.py` (the real spend-guard gate
in `pipeline_run_card_batch`) and its UI mirror `BATCH_MAX` in
`dashboard\src\components\PipelinePanel.tsx` (disables "add quote" past the
cap). Both now 19. Backend restarted to load it (the running uvicorn had the
old value cached in memory, same gotcha as the print-sheet fix below); Vite
dev server HMR picks up the dashboard side. Verified live: 20 quotes -> 422
"capped at 19", 19 quotes -> passes the cap (fails later only on quote
verification, using throwaway quotes so no paid run fired). Note the batch is
still strictly sequential and each card is a full paid pipeline run, so a full
19-card batch is ~1-1.5h of runs and ~19x the per-card spend — the cap is
higher now, not the per-card cost.

### 2026-07-31 — Claude Code (Opus 4.8), direct — gathering print sheets now paginate
Owner report: printing a gathering sheet capped out (~4 cards) and extras
vanished. Root cause in `agents\print_sheet.py`: `build_print_sheet` only ever
emitted ONE front page + ONE back page, and `_sheet_page` fills the grid by
cycling `card_imgs[i % n]` — so any pairs beyond one grid's worth were silently
dropped, never placed. A quote-card grid is 2×4 = 8 slots/page, but a bilingual
card contributes 2 pairs (Eng + Spanish variant), so 4 selected bilingual cards
= 8 pairs = one full page and the 5th spilled into nothing. Fix: `build_print_sheet`
now chunks the prepared pairs into `per_page = cols*rows` groups and emits a
front+back page per chunk, interleaved as [F1,B1,F2,B2,…] so each physical
sheet is self-contained (duplex column-mirror still aligns backs per sheet). A
partial last chunk still fills its grid by cycling its own pairs (no half-blank
page), and a single chunk reproduces the original 2-page output unchanged. No
API or dashboard change needed — `/print-sheet` (gathering) and
`/products/{id}/print-sheet` both flow through this. Verified by page-count
(1 pair→2pp, 8→2, 9→4, 20→6) and by eye (sheet 1 = 8 distinct cards, overflow
9th card lands on sheet 2).

### 2026-07-31 — Claude Code (Opus 4.8), direct — larger, consistent quote-card attribution
Owner ask: the speaker-name + source line under a quote card's quote was too
small, and it should be about twice as big and the same size across all cards.
Root cause in `agents\card_compositor.py`: the attribution ("citation") size
was `max(15*_SS, int(q_size*0.40))` — proportional to the fitted quote font,
so it (a) came out small and (b) varied card-to-card since `q_size` depends on
the quote's length. Fix: new module constant `CITATION_PX = 32` (1× px/300dpi)
gives every card a fixed attribution size (~1.6–2× the old typical), capped at
the quote size (`min(CITATION_PX*_SS, q_size)`) so a crowded card never shows
the attribution larger than the quote. Applies to English and translated
variant cards (same code path). Verified by rendering a short and a long quote:
both now show the identical attribution size where before they'd have differed.
No text/logic change — purely presentation, layout editor unaffected.

### 2026-07-17 — Claude Code (Fable 5), direct — hands-free multi-quote card batches
Owner ask: queue several different quotes as cards instead of one at a time,
skipping the human check-in for batch runs. New `POST /pipeline/run-card-batch`
(`agents/api.py`: `CardBatchRequest` + `_run_card_batch`): every quote is
resolved via `_resolve_pinned_quote` IN the endpoint (bad paste = 422 before
any paid work), then one job runs the cards strictly one at a time with
`request_human_input=None` — `run_consultation`'s round-2 pause simply never
fires, so a batch job never enters `waiting_for_input`. One card's mid-run
failure is recorded on its item + announced as a System turn and the batch
continues; capped at 8 cards/run (`_CARD_BATCH_MAX`, spend guard). Dashboard
(`PipelinePanel.tsx`): the pinned-quote field is now a list with "+ Add
another quote" — 2+ quotes switch the run to batch (button says "Generate N
quote cards") and results render as a per-card summary (`CardBatchResults`,
faces + score or the error). `api.runCardBatch` + `CardBatchItem`/
`CardBatchResult` types added. Verified offline (TestClient rejection paths,
stubbed loop: sequential, hands-free, failure-continues; tsc clean) and LIVE:
job cd4301d4 ran a real 2-quote batch end-to-end with no pause — products
f2ec1ffa + 8ca87934 saved with `quote_pinned: true` and byte-exact quotes;
front face eyeballed. Uncommitted — owner decides.

### 2026-07-16 — Claude Code (Fable 5), direct — print sheets go bilingual
Owner ask: "can the print sheet have half english and half spanish?" New
`api._print_pairs_for(product)`: a product's sheet contribution is its main
pair PLUS each variant_faces pair whose files exist (quote cards only;
bookmarks unchanged). Both endpoints use it — the drawer's single-product
GET (always) and the gathering POST (new `include_variants: bool = True`
body field for opting out). The sheet builder already cycles pairs
slot-by-slot (`i % len(pairs)`), so [English, Spanish] alternates → half/
half on any sheet; duplex mirroring keeps each back matched to its front
since the pair is the cycling unit. Verified live: sheet built for
bilingual card 8e53a216 → 2 pairs fed (EN + ES confirmed by filename),
2-page PDF (1.17MB) rendered via the real endpoint. Backend restarted.
Uncommitted — owner decides.

### 2026-07-16 — Claude Code (Fable 5), direct (Antigravity dispatch timed out
mid-task) — Spanish card pair was rendering but INVISIBLE; pause preview now
shows both faces
Sheraj's Spanish run "only did English": diagnosis showed the ES pair WAS
rendered and stored (files on disk, card_copy.variant_faces correct) — the
dashboard's QuoteCardPreview found variants by matching file names against a
STALE react-query products cache, which never contains a product created
seconds ago, so the variant rows silently skipped. Fix: data passed
explicitly — `_run_card_pipeline`'s result now includes `variant_faces`;
PipelinePanel passes it as a prop (cache-matching kept only as fallback);
the product drawer (ProductsGallery) reads its own product's
listing_copy.variant_faces and renders labeled per-language tiles directly.
Also per owner ask: the mid-consultation pause preview now renders BOTH
faces — `_preview_front` returns {front, back} web paths, consultation
attaches `image_back` to the ask turn (string returns from bookmark
previews unchanged — verified offline with stubbed rounds), and
ConsultationTranscript renders Front/Back side by side. tsc clean, backend
restarted. Existing card 8e53a216's Spanish pair now shows in its drawer.
Uncommitted — owner decides.

