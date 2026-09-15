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

## Snapshot (as of 2026-09-14)

**Live and committed:**
- Bookmark + quote-card pipelines, the visual layout editor, print sheets, and
  the X giveaway pipeline. Etsy publishing exists in code but has never been
  connected (0 products ever published). Canva autofill exists in code, is
  broken (0/10 attempts ever succeeded), and is off by default.
- The Secretary (Abigail): Phases 1-3 live (chat, Google Workspace, WhatsApp).
  Phase 4 (recovery rhythms) not started.
- The Video pipeline, the Colony (+ the Material World / nuclei), the project
  wallet, the API's owner gate, and the prompt-injection hold.
- Live Consultation: the human-owned record (rules 94-99), request-ordering
  survival (rules 100-110), and the concept graph (rules 121-132). Nested
  topics are allowed (theme may contain theme); layout v3 packs subtree
  bounds instead of a per-branch grid; Organize ideas can repair an already
  placed map and previews the validated proposal. Closeout has not yet been
  used in a real meeting.
- Agent orientation split (`ed21927`): root `AGENTS.md` is a thin routing
  table; the numbered rules live in `docs/rules/*.md`, split by subsystem.

**"Committed" means it is in the codebase and the offline test suites pass --
it does NOT mean Sheraj has reviewed it by hand.** Several Live Consultation
features (closeout, the concept graph, diarisation) have never been exercised
in a real meeting with real people.

**Read before starting anything nontrivial:** root `AGENTS.md`'s routing
table plus the `docs/rules/*.md` file(s) for the subsystem you're touching,
and the nearest nested `AGENTS.md` if the file you're editing has one above
it. Then check the "In flight" table below so you don't collide with another
tool working the same paths right now.

---

## In flight (parallel lock)

One row per active tool. If your paths overlap another row, stop.
Empty when nothing is in flight.

| Tool | Branch / worktree | Paths owned | Do not touch |
|---|---|---|---|
| — | — | — | — |

---

## Activity Log (newest first)

### 2026-09-14 -- Grok 4.6 -- concept map nested layout + working Organize ideas

The map was still an unreadable strip, and Organize ideas 404'd. Confirmed
against current code before changing it: three themes with nine children each
produced 15 pairs of cards at identical coordinates (collision checks were
only inside a branch); `contains_target_ok` forbade nested topics; Organize
ideas refused a fully-parented map and silently capped apply at 40 edges.
The 404 was a stale managed API process -- the route was in the code
(`259` routes, including organize) but the running server's OpenAPI had no
organize paths (`222` paths). `/health` was 200 either way.

What changed: nested display hierarchy with cycle checks and one
primary parent (`agents/live_consultation_graph.py`, LAYOUT_VERSION 3);
tree layout by subtree bounds; Organize ideas sees the whole map, dry-runs
validation, reports coverage, and apply uses the same accepted edges
(`MAX_ORGANIZE_EDGES` 200). Capabilities advertise
`graph_capabilities.organize_whole_map` so an older backend 404s as "this
API is out of date". Dashboard: readable first view centres on the question
at zoom 0.8, Fit overview / Focus branch / search-opens-collapsed-path,
Organize preview shows the validated proposal. Rules 122/131/132 revised
in place (numbers unchanged).

Verified: `python scripts/test_live_consultation.py` 925/925; `import
agents.api`; `npx tsc --noEmit`; `npm run build` (Consultation chunk
328.15KB, entry 247.89KB). Restarted the managed "bahAI Secretary API"
task and confirmed organize routes + capabilities on the live process.
Browser at 1366x768, 1920x1080, and 390px: archived "Expanding Nucleus"
opens with the question readable (not a tiny strip); Organize ideas POST
returns 200 with a validated preview (60 placements, leftovers reported);
Discard left the map unchanged. First live model reply on that large
session 502'd unreadable JSON; after compacting the organizer context it
succeeded. Narrow viewport is cramped by the nav + Activity Log (dashboard
chrome, not this tab's files).

### 2026-09-14 (latest) -- Claude Code (Sonnet 5) -- the concept map, made readable

Sheraj's own screenshot of "Picking a class for Sean": almost every card sat in
one enormous horizontal row beneath the question, the canvas zoomed out until
they were tiny, and the sidebar listed dozens of repeated "Contains" rows. New
rules 131-132 in `docs/rules/live-consultation.md`.

**The actual structural bug, confirmed before touching anything.** `_layout`
gave every node at a given DEPTH one shared, unbounded row -- a theme's
children searched the SAME 1-D line as every other theme's children for a free
slot, so nothing bounded how wide a branch with many children could spread. A
fixture of one real theme plus 50 unparented questions reproduced the reported
shape exactly (51 nodes sharing root's row). Two independent causes, both in
`agents/live_consultation_graph.py`:
- **Orphans fanned out onto root one at a time.** Category fallback (rule 127)
  only ever engaged when NO theme existed anywhere; the moment a meeting had
  even one real theme, every OTHER unparented item attached to root
  individually. `build_graph` now groups every orphan into a PROVISIONAL
  bucket by kind (the same bucket mechanism rule 127 already used for a
  fully-unthemed session, just scoped to the items that need it) and reports
  the count in the graph payload's new `unplaced_count` -- this is also what
  fixed the "dozens of Contains rows" in root's own sidebar list, since root's
  real children are now a handful of themes/buckets, not every orphan leaf.
- **The layout itself had no concept of "this branch's own area."** `_layout`
  is rewritten so each branch's children wrap into a compact local grid
  (`_grid_cols`, capped at `MAX_GRID_COLS`) anchored on that branch's own
  position, instead of sharing one row with every other branch's children --
  tractable without a general tree-layout library because the hierarchy is
  exactly two layers by construction (rule 122). A stored position from the
  OLD scheme is never trusted forever: `graph_node_view` gained
  `layout_version`, and an unpinned position not stamped with the CURRENT
  version (`LAYOUT_VERSION`, now 2) is recomputed once and re-stamped, never
  migrated -- because the graph is derived fresh on every read (rule 121),
  Sheraj's own already-unreadable session gets the fix the next time it is
  opened, no migration step or button press required. Pins are honoured
  regardless of version, always.

**Readability, not just collision avoidance.** A brand-new theme or bucket
with more than 6 children starts collapsed (only on its first-ever
appearance -- never re-collapsing a branch a person deliberately expanded),
and a collapsed card now carries a count and an "N open" hint
(`branchStats` in `ConceptGraph.tsx`) so what's hidden inside stays
discoverable. The detail panel's connection list groups by relation and caps
each group at 8 with a "show N more" toggle, rather than one flat list --
which, combined with the provisional-bucket fix, is what actually solved the
reported "dozens of Contains rows." The legend/detail column is now
collapsible (a button, not a permanent 20rem reservation), defaulting closed
on a narrow viewport.

**"Organize ideas" (rule 132) -- a second, explicit action, deliberately
separate from Arrange map.** Arrange is free and geometric and was already
correct; it cannot fix a session whose items are provisionally bucketed
rather than genuinely themed, which is exactly what an OLD session needs.
`reasoner.organize` is a new, narrower prompt shown the map's existing
themes plus exactly the unplaced items, restricted in code to returning only
`{"add": {"themes": [...]}, "edges": [...]}` -- nothing it proposes can
reword a fact, a decision or an action. Preview makes the one paid call and
HOLDS the result (`sessions.pending_organize_json`) with a plain-language
summary; apply re-merges that same patch against whatever the map says at
that moment (rule 104's rebase discipline, reused); discard throws it away.
Never runs automatically, never on opening an archive.

Also, a smaller prompt change: the ordinary per-turn reasoner now says
explicitly to keep themes to roughly 3-5, reuse an existing one over a
near-duplicate, and it may retroactively place an already-on-the-map item
under a theme that has since become clear -- not only brand-new items.

**Verified:** 893 offline checks in `test_live_consultation.py` (was 811,
+82) -- the reported reproduction case (one theme, 50 unparented items) now
asserted under 2000px wide with zero colliding nodes, the provisional-bucket
routing, `layout_version` staleness/reflow/pin-preservation, the
collapse-by-default threshold, and the full Organize ideas preview/apply/
discard cycle through the real HTTP endpoints. `npx tsc --noEmit` and
`npm run build` both clean; the Consultation lazy chunk grew from 311KB to
321.72KB, the entry bundle unchanged at 247.65KB (rule 112). Two existing
assertions were UPDATED rather than left broken, because the old behaviour
they pinned is exactly what this pass changed on purpose: "an item with no
theme hangs off the root" (now: grouped into a provisional bucket) and a
same-ROW-only overlap check for two siblings (the wrapped grid can correctly
place a new sibling in the next row instead; the replacement checks genuine
non-overlap in both axes).

**Not done, honestly.** No browser-automation tool was available in this
session, so nothing here has been visually inspected in an actual browser at
1366x768, 1920x1080, or a narrow viewport -- every frontend claim rests on
typecheck, the production build, and reading the code, exactly like the two
concept-map sessions before this one. `reasoner.organize` was never called
against a real model, only stubbed (the same discipline the rest of this
suite already uses for `analyze`). No true side-by-side "preview vs. apply"
diff UI exists for Organize ideas -- the preview is a plain-language summary
list, not a rendered before/after map. A keyboard-navigation audit, a
print/PDF pagination pass for a very large complete export, and the
dashboard-chrome-level "account for the Activity Log's height" ask were all
out of scope for this pass (the last is outside Live Consultation's own
files under the parallel-work table). Manual verification steps for the
visual claims: open an existing multi-item consultation's Concept map tab,
confirm the initial view shows a manageable number of cards rather than one
wide row, expand a collapsed theme and see its children wrap into rows
rather than one line, and try Organize ideas on a session with unplaced
items.

### 2026-09-14 (later) -- Claude Code (Sonnet 5) -- agents/api.py split into per-subsystem routers

Mechanical extraction, no behaviour change: `agents/api.py` was 7002 lines /
310 KB and owned nearly every HTTP route plus both product pipelines. It is
now 889 lines / 38 KB -- the app factory (middleware, CORS, startup), the
job-store routes, product-type-agnostic product routes (list/get/edit/layout/
print-sheet), and Steward/deeds/trust reporting, plus the `include_router`
calls that bring in everything else. New routers, each a thin extraction with
its logic untouched: `agents/jobs.py` (the background job store + shared
helpers `_web_image_path`/`_badge`/`_esc`/`_load_product_or_404`/
`_require_bookmark`/`_require_card`/`_print_pairs_for` -- has NO dependency on
api.py or any `*_api.py`, so nothing can circular-import through it),
`pipeline_api.py` (bookmark pipeline + Canva/Etsy), `card_api.py` (quote-card
pipeline + quote sources + the Librarian's finder), `video_api.py`,
`colony_api.py` (Colony + the Material World/nuclei + `launch_team_pipeline`),
`secretary_api.py` (Google OAuth + WhatsApp + Secretary), `x_api.py`,
`wallet_api.py`.

Verified, not claimed: `app.routes` dumped before and after every single
extraction step and diffed -- identical path/method set throughout, all 255
routes. `python -c "import agents.api"` after each step. All ten offline
suites pass (colony 135, secretary-colony 92, job-cancel 24, wallet 90,
nuclei 281, api-auth 66, secretary-injection 50, quote-verify 50, video
288 -- 1076 checks), `npx tsc --noEmit` clean, and dashboard/src path strings
spot-checked unchanged (no dashboard file touched).

Two real bugs this surfaced and fixed, both the same shape: a test
monkey-patched `api._start_job` / `api._MAX_JOBS` / `api.pipeline_run_card_batch`
to intercept a call, but the function that actually reads that name now lives
in a different module (`agents/jobs.py` or `agents/colony_api.py`) and reads
its OWN module's global, so patching the re-exported `api.` alias silently did
nothing -- `test_job_cancel.py`, `test_colony.py` and `test_secretary_colony.py`
now patch the module where the name is actually resolved at call time, with a
comment explaining why for the next person who hits this. `agents.api` still
re-exports everything external code reaches into directly (`JOBS`,
`_start_job`, `create_task`, `launch_team_pipeline`, `_check_quote_grounding`,
several card/x-post internals) for `agents/colony.py`, `agents/secretary_colony.py`
and the test suite's own direct-attribute access -- confirmed by grepping
every `api.<name>` / `api_module.<name>` reference outside api.py itself.

### 2026-09-14 -- Claude Code (Sonnet 5) -- nested AGENTS.md and a parallel lock

Nested AGENTS.md under `agents/` and `dashboard/` (plus
`dashboard/src/components/{consultation,colony,video}/`), each pointing at
`docs/rules/*.md` and its verify command rather than duplicating rule text --
so Codex/Cursor/Claude, which walk from the edited file toward the repo root
loading every AGENTS.md they hit, pick up subsystem orientation without
loading the rest. Root `AGENTS.md` gained a "Parallel work" file-ownership
table (which tool owns which paths, so two tools running at once don't
collide) and a working-norms bullet to stop and tell Sheraj on an overlap.
STATUS.md's Snapshot was rewritten as current reality (dated today, down from
1227 lines/78 KB) and gained the "In flight" table above it so tools can see
each other's claimed paths; the Activity Log was trimmed to the newest 12
entries plus this one -- older history is in `git log`. No product code
changed.

### 2026-09-14 (later still) -- Claude Code (Sonnet 5) -- AGENTS.md split so coding tools stop eating 70k tokens

Root `AGENTS.md` had grown to 2619 lines / ~165 KB / ~41k tokens (`CLAUDE.md`
`@`-imports the whole thing, so every Claude Code session paid that tax before
one edit), and Codex concatenates AGENTS.md files and caps the combined text
at 32 KiB by default -- 165 KB is 5x that cap, so a default Codex session was
being cut off mid-video-pipeline-rules and never saw Colony, wallet, the
owner gate, prompt injection, Live Consultation, quote verification,
gatherings, or the gotchas at all. Documentation-only session, no product
behaviour touched.

The numbered hard rules (the asset here -- cited from 170+ code comments,
numbers permanent, never renumbered) were CUT, not rewritten, into
`docs/rules/*.md`, one file per subsystem: `pipelines.md` (1-14, 29, 111),
`secretary.md` (15-28, 50-54), `video.md` (30-34, 58), `colony.md` (35-41b,
59-69), `wallet.md` (42-49), `jobs.md` (55-57), `api.md` (70-72),
`live-consultation.md` (73-110, 121-130), `dashboard.md` (112-116),
`gatherings.md` (117-120), plus `gotchas.md` and `dispatch.md` (the CLI
dispatch appendix). The narrative "system in one page" section moved to
`docs/overview.md`. Root `AGENTS.md` is now 137 lines / ~8.9 KB: a routing
table (rule range -> file), the working norms, and the commands list --
comfortably under the 150-line/12KB budget this session set for it.

Verified byte-for-byte, not by eye: concatenating all the extraction ranges
back together in original order and diffing against the original contiguous
block (AGENTS.md lines 175-2457, the full span from the first rule heading to
"## Gotchas") came back an exact match -- no gaps, no overlaps, no
duplicated lines. Every rule number 1-130, lettered variants included
(33a-33f, 41a/41b, 91b), confirmed present in `docs/rules/`. `CLAUDE.md` and
`README.md`'s two doc pointers (the "AI coding agent? Start here" callout and
the "What lives where" table) were updated to describe the new layout, with
no rule text duplicated into either. `docs/ARCHITECTURE.md` and
`agents/api.py` were not touched (out of scope).

Also trimmed this file's own Activity Log from 67 entries down to the most
recent 20, per the "keep to roughly the last 15-20 entries" convention stated
at the top of this file -- full history before 2026-08-18 is in `git log`.
The Snapshot below was left as-is (not re-verified this session).

Left for later: nested `AGENTS.md` files inside `dashboard/src/` were
mentioned as a possible future addition but no concrete scope existed for
what they'd contain, so none were created.

### 2026-09-14 (later) -- Claude Code (Sonnet 5) -- the concept map, repaired against an external review

An external review (Codex, comparing the concept-map commit `fcd8307` against
`31d8eb9`) reproduced eleven defects against the running code before anything
here changed, and this session fixed and pinned every one. `AGENTS.md` rules
121-130 were corrected in place where they had overstated what the code
actually did (canonical wording, acyclic containment) and extended with two
new rules (128 for record staleness / the approved snapshot, 129-130 for
layout and the archived view's editability and recovery path) — rule numbers
were never reused or renumbered, only appended and corrected.

**Section 1 — the graph was still showing what was first heard.**
`_item_node` read a decision/action node's `label`/`detail` from the
WORKING-MAP item, and only overlaid status/owner/acceptance from the
canonical `decisions`/`action_items` row — so `edit_action`/`edit_decision`
correctly rewrote the row and the graph went on showing the original wording
regardless. It now reads text from the row too, with the map item as a
fallback only when the row's own field is genuinely blank. A map item of
either kind with NO matching row is now treated as deleted and omitted
(`remove_action` also strips the map item and tombstones it), which is what
stops a deleted commitment from reappearing as a fresh "proposed" phantom.
The mirror case: a canonical row with no map item — a human-created action
(`create_action_item` never gets a `map_id`) or a confirmed decision/accepted
action whose map item was stripped by transcript deletion (rule 103) — now
gets a node built straight from the row (`_canonical_row_node`), keyed by the
row's own `map_id` when it has one so any edge stored against that id keeps
resolving. The graph payload gained `record_revision`, and `ConceptGraph.tsx`'s
client-side resync key now includes it, so a decision confirmed or an owner
corrected refreshes the node without waiting for something else to also move.

**Section 2 — staleness had a blind spot, and the archive had no record of its
own map.** `_record_status` compares `record_revision` against
`approved_revision`, but only HUMAN edits ever bumped it — an analysis pass
adding a new decision, action or connection did not, so an approved meeting
could keep reporting "Approved, and current" while the record actually grew
underneath it. `_run_analysis` now snapshots the canonical rows before its
own upsert loop and compares after (change-detected, not "something was
written" — every pass re-upserts every row whether or not it moved), and
`_apply_graph_patch` reports whether it stored a connection; either bumps
`record_revision`. Drawing/editing/rejecting a connection by hand does too.
Separately, an approved report never had an approved MAP: `approved_graph_json`
is now an immutable snapshot taken in the same call as `approved_md`, and
`GET .../graph/approved` (404 before anything is approved) serves it,
distinct from the live graph which keeps moving.

**Section 3 — merging two nodes left both canonical rows behind.**
`merge_map_items` combined the working-map items but never touched
`decisions`/`action_items`, so the graph showed one node while the database
quietly kept two. `_merge_canonical_actions`/`_merge_canonical_decisions` now
fold the rows together first, and REFUSE (`store.MergeRefused`, HTTP 409)
when both sides already carry a real, disagreeing commitment — an accepted
action or a confirmed decision is never the one silently discarded; the
refusal points at Accept/Did not accept or Not a decision instead.

**Section 5 — `contains` was only half acyclic, and two smaller gaps.**
`validate_edges` checked that a `contains` edge's SOURCE was a theme, but
never checked what it could TARGET — so "theme A contains theme B" (and B
contains A right back) and "theme A contains the root" both passed, a real
cycle the module's own docstring claimed was impossible by construction.
`contains_target_ok` closes the other half, applied to both a human's
`add_graph_edge`/`edit_graph_edge` and a model's proposal. Also fixed:
a connection's `source_turn_ids` are now checked against real turns in the
session, the same as a map item's; changing an edge's relation tombstones the
OLD relation (so a corrected "supports" cannot quietly come back); merging a
node redirects its rejection tombstones onto the survivor, not only its live
edges; a duplicate addition's `tmp_id` now resolves onto the EXISTING item it
duplicates rather than being left dangling (an edge naming it used to be
dropped as "names a node that does not exist"); and `MAX_STORED_EDGES` — declared
from the start — is finally enforced.

**Section 6 — layout could overlap, and two export defects.** `_layout`
divided each row's width by INDEX among every node at that depth, with no
regard for where an existing node actually sat; a new leaf next to one kept
at `x=0` landed at `x=110`, a 90px overlap at the 200px node width. It now
searches outward from centre for the nearest slot clear of everything already
there. The HTML export printed a declined action's owner with no qualifier at
all (identical to an accepted one) and carried no approval-state banner; both
fixed, mirroring `build_report`. SVG/PNG now fall back to the relation's own
name when no custom label was set (most edges never had a custom label, so
most showed nothing), and cross-links get a directional arrowhead in SVG and
in the live view (`ConceptGraph.tsx`, via React Flow's `MarkerType`); node
boxes also finally show their kind's colour (declared in `NODE_KIND_META` since
the original commit, applied only to the legend and minimap until now). The
relation-edit endpoint (`PATCH .../graph/edges/{id}`) already existed and was
never reachable from any control — only Reject was wired up; a pencil icon
beside each connection now opens an inline relation/label editor calling it.

**Section 4 — an incomplete ending was reported once and thrown away.**
`endSession`'s response `note` was read once by the dashboard mutation and
discarded on the way to closeout. `final_pass_note` is now a field ON THE
SESSION, set when the closing pass genuinely did not finish (the wait timed
out, or it ran and failed — never for the ordinary reasons there was nothing
to do), shown persistently in the archived view with a `POST
.../finish-analysis` retry that clears it on success. The archived Concept
map tab was also unconditionally `readOnly`, so none of the correction
surface described in section 1 above was reachable once a meeting ended —
exactly when a person is most likely to notice something needs fixing; it is
editable there now, through the same endpoints.

**Section 7 — two defects unrelated to the graph commit, found by the same
review.** `build_report`'s cached-narrative reuse path read
`narrative.get("how_we_got_here")`, a key `_narrative` never writes (it
writes `"discussion"`) — every `/report/approve`, every closeout approval and
every `reuse_narrative=True` rebuild silently dropped the "How the group got
there" section. Fixed with the old key kept as a fallback in case any stored
`report_narrative_json` was ever actually written under it. And
`session_updates` returned the session's absolute head as the next cursor
regardless of whether `limit` had truncated the response — a page capped at
200 of 205 changed turns got back cursor 205, and the next poll, now starting
past everything, silently skipped the five it never received. The cursor is
now the last DELIVERED turn's own revision. `useConsultationUpdates.ts`'s
"take the next page immediately" follow-up tick was also firing while
`busyRef` was still true (inside the same try block, before `finally` reset
it), so it always returned instantly without polling — deferred until after
the flag resets, restoring the intended immediate-follow-up behaviour.

**Explicitly NOT done, named rather than left to be discovered:** an
in-progress meeting can still, in principle, have a write land after `End`
that predates real "input watermarks" — the fix here is that any such write
now correctly makes the record report itself stale (section 2/4's
`record_revision` fix) rather than a stronger guarantee that no such write can
land at all. A merge still shows only the survivor's final wording, not a
preview of both sides' meaning before combining. Cross-links carry `inferred`
and `human_edited` in the data contract but nothing on screen visually
distinguishes an inferred connection from a stated one (only the relation's
own dash style is shown). PNG export gained relation labels and a dashed/
dotted approximation for style but no directional arrowhead (SVG has one).
No keyboard-navigation or print/PDF-layout audit was performed.

**Verified:** all 811 pre-existing checks in `scripts/test_live_consultation.py`
still pass, plus 56 new ones (867 total) covering every reproduced defect
above through the real HTTP endpoints, not just the underlying functions.
Every other offline suite (colony 135, secretary-colony 92, job-cancel 24,
wallet 90, nuclei 281, api-auth 66, injection 50, quote-verify 50, video 288)
still passes unchanged. `npx tsc --noEmit` and `npm run build` both clean.
**Not exercised in a real browser or a real meeting** — no browser-automation
tool was available in this session, so the frontend fixes (the readOnly
archive, the approved-map modal, the finish-analysis banner, the kind-colour
node boxes, the directional arrowheads) rest on typecheck, the production
build, and reading the code, not on having been clicked through. Section 6's
broader UI polish asks (a real merge-preview modal, a keyboard-navigation
audit, print/PDF layout checking) were explicitly out of scope for this pass
and remain undone.

### 2026-09-14 -- Claude Code (Sonnet 5) -- a real concept map for Live Consultation

Sheraj (via a detailed written brief) asked for the consultation map to become
an actual connected graph — a root, topic branches, and typed connections
between ideas, concerns, decisions and actions — rather than the existing four
summary cards, while keeping every human-authority and report guarantee those
cards already carry (rules 90-98). Rules 121-127 in `AGENTS.md` say why each
piece of the design exists.

**The design choice that kept this from becoming a second data store:** a
node's content is never copied. `agents/live_consultation_graph.py`'s
`build_graph` reads the EXISTING `session_state`, `decisions` and
`action_items` rows on every call — a node's id is the underlying map item's
own stable id, and a decision/action node's authoritative status, owner and
acceptance come from the `decisions`/`action_items` tables via their existing
`map_id` link (rule 95), not from the map item, because those tables are what
`accept_action`/`confirm_decision` actually write. Only connections
(`graph_edges`, new table) and where a node sits on screen (`graph_node_view`,
new table, its own revision counter) are real rows of their own. This is the
same "derived, never copied" discipline the finished-video shelf and a
gathering's commitments already use (rules 58/117), one level deeper.

**Relationship extraction reuses the existing analysis pass rather than adding
a second always-running model service** (an explicit requirement, to avoid
another silent-cost surface like realtime voice, rule 85). The reasoner's one
JSON schema gained an `"edges"` array and an optional `"tmp_id"` on new items,
resolved deterministically in `reasoner.merge` — never guessed at by the model
— so a theme and the item it contains can be proposed together in one pass.
The hierarchy is acyclic BY CONSTRUCTION: `contains` may only ever originate
from a `theme` node (or the synthetic root), so the tree is two layers and a
cycle cannot be built — no general cycle check was needed. A rejected
connection is tombstoned (`graph_edge_rejections`) so the model cannot quietly
recreate what a person took apart; a human adding the same connection back by
hand bypasses the tombstone on purpose, because that is a new decision, not
the model undoing the old one.

**Layout is deliberately isolated from everything that matters.** A drag, a
pin or a collapsed branch bumps only `graph_view_revision` — never
`state_revision` (the speech governor's freshness check, rule 77) or
`record_revision` (report approval, rule 102). A position is computed once,
the first time a node appears with no stored view row, and never recomputed
for that node again, which is what keeps the map from reshuffling itself every
time someone adds a fact.

**Found and fixed in passing, because the graph's final snapshot needed it to
be true:** `end_session` returned "already running" the instant it found a
prior analysis pass still in flight, rather than waiting for it — so a closing
report and the very first concept-map read could both be built one pass short
of the actual last few minutes of discussion. `_run_analysis` now takes a
bounded `wait_if_busy` (`CONSULTATION_FINAL_WAIT_S`, default 20s, never
unbounded — rule 114's reasoning applied to ending rather than leaving), and
`end_session` uses it.

**The interactive view** (`ConceptGraph.tsx`, on `@xyflow/react` — newly added
to `dashboard/package.json`, MIT, no production-audit findings): pan, zoom,
fit, search/focus, branch collapse via double-click, a detail panel per node
(full wording, status, provenance via the existing "what was actually said"
modal, connections with reject buttons, an add-connection form, a same-kind
merge control, a pin-position checkbox kept separate from content editing per
the brief), Arrange map as an explicit reset, and SVG/PNG/HTML export buttons.
Live view: a "Concept map" / "Summary" tab pair in the session sidebar, map
prominent by default (given more of the row's width than the four-card
summary), with an Expand button for a laptop or projected display, on its own
6-second poll separate from the transcript's 4-second one. Archived view: a
third tab beside Report and Everything else, plus a small node/edge-count
preview card in the Report tab linking to the full map. An old session with no
themes or connections yet renders as category buckets rather than a flat dump,
clearly marked `fallback: true` so it can never be read as something the group
actually discussed.

**Verified:** 811 offline checks in `test_live_consultation.py` (was 734,
+77) — tmp-id resolution, edge validation (unknown node, self-loop, an item
trying to `contain`, a tombstoned connection), the derived-not-copied
overlay from the decisions/actions tables, position stability across reads,
drag/pin never bumping `state_revision` or `record_revision`, Arrange
preserving a pinned position, cross-session isolation for edges and node
views (the rule 100 pattern, extended), node merging with edge redirection,
SVG/PNG/HTML export content-type and XSS-escaping checks (an XSS payload put
in an idea's text came back escaped in both, with the escaped form present),
never leaking a raw transcript sentence into an export, the `end_session`
wait-vs-no-wait timing behaviour, and every existing check in the suite
(three direct callers of the now three-tuple `reasoner.merge` were the only
breakage, all in this suite itself, fixed). `npx tsc --noEmit` and
`npm run build` both clean; the production entry bundle is unchanged at
247 kB because React Flow lives entirely inside the Consultation tab's own
lazy-loaded chunk (rule 112), which grew to 311 kB (96 kB gzip) on its own.
`npm audit --omit=dev` still reports zero.

**Not done, honestly:** no browser-automation tool was available in this
session, so nothing here has been clicked through in an actual browser or
exercised with a real microphone — every frontend claim rests on typecheck,
the production build, and reading the code. Keyboard access and
reduced-motion support were coded to the same conventions the rest of this
dashboard uses but were not separately audited. `AGENTS.md` rule 90's
four-card live view is explicitly evolved rather than replaced, per the
brief; it is still there as "Summary".

### 2026-09-11 -- Claude Code (Opus 5) -- a WhatsApp send that failed for a reason nobody could read

Sheraj asked Abigail to send a friend a morning prayer over WhatsApp. It failed,
and all she could tell him was `send_whatsapp failed: HTTPError`. Two separate
problems, both now fixed in `agents/whatsapp.py`.

**Why it actually failed**: the friend IS on the trusted-contacts allowlist, but
his last inbound message was 2026-07-12, so the 24-hour free-form window has been
shut for two months. `send_best_effort` therefore fell through to the
`secretary_update` template -- the one AGENTS.md has flagged as unproven since
2026-07-11. It is not unproven any more: Meta answers HTTP 404 / error 132001,
"template name (secretary_update) does not exist in en_US". Checked live the same
day: the token is a permanent System User token and is valid (`expires_at: 0`),
the phone number id resolves, and it is still Meta's sandbox **Test Number**
(+1 555-156-8050, five-recipient limit). **Consequence beyond this one message:
every scheduler reminder fired outside the 24-hour window is being silently
dropped.** Creating the template in WhatsApp Manager is an owner action in Meta's
console (UTILITY, body exactly `{{1}}`, en_US) -- no code change, `GET
/whatsapp/setup` walks through it.

**Why he could not see any of that**: `resp.raise_for_status()` throws away the
response body, which is the only place Meta explains itself. Every send now goes
through one `whatsapp._post` chokepoint that raises `WhatsAppError` carrying
Meta's code translated into plain language (`_ERROR_HELP`, including the sandbox
number's 131030 and the closed-window 131047); `send_best_effort` names the shut
window as the reason a template was attempted and says that asking the friend to
message first reopens it; `whatsapp.why(exc)` is what the webhook reply paths
(`api._handle_whatsapp_message`) and `scheduler._deliver` now print instead of a
bare class name. `why()` does not widen to `str(exc)` for arbitrary exceptions --
those can carry request bodies, and a notification is no place for message
content (rule 15). Abigail's `send_whatsapp` tool also now distinguishes a real
send from a template send, the way `nuclei_bridge.send_to_contact` already did.

Nothing new was added to the numbered rules; this is the existing "never fail
silently" norm applied where it had been missed. Suites unchanged and green
(nuclei 281, injection 50, secretary-colony 92, api-auth 66, colony 135,
job-cancel 24) plus `npx tsc --noEmit` clean. The backend was restarted so the
change is live -- it runs without `--reload`.

### 2026-09-09/10 -- Claude Code (Opus 5) -- the record survives real request ordering; quotations verified exactly; the dashboard stops costing so much to open; Gatherings and Home

Worked from a review of the repo at `20ea32c` that listed eight P0-class defects
and a set of P1s. Every one was REPRODUCED against the code first -- several of
them by running the new tests in a clean `git worktree` at `20ea32c`, where they
fail -- then fixed, then pinned. Rules 100-116 in `AGENTS.md` say why each exists.

**The correctness fixes (rules 100-110).** A write could reach another meeting:
the endpoints wrote by global child id and compared `session_id` afterwards, so
`PATCH /sessions/A/actions/{B's id}` returned 404 having already changed B. Now
in the WHERE clause (`store._scope`), proved by snapshotting meeting B and
requiring it byte-for-byte unchanged. A human edit made during a model call was
overwritten by the model's wording with `human_edited` reset -- `_run_analysis`
read the map, spent the network call, and saved its stale snapshot wholesale; it
now REBASES the same patch onto the current map, so the correction stands and
no paid call is repeated. `scope=outcomes` returned the automatic draft and
called it the approved record; `approved_md` is now separate, written only by a
person, bound to the revision they read, and rebuildable from corrected facts
without paying for the prose again. The retention helper had NO CALLER at all --
a seven-day session kept its transcript for ever; it is swept at startup and
throttled on reads. Deleting a transcript kept the whole unreviewed map and every
private observation while saying only the approved record remained; it is now an
allowlist, and a file that will not unlink is reported instead of swallowed.
Late work (turns, uploads, diarisation) can no longer put deleted words back.
The credential endpoint -- the one that actually opens a microphone -- had no
attestation gate. Start and End are idempotent, so reconnecting no longer resets
the meeting clock and a second End no longer pays for the report twice.

**Quotations (rule 111).** The bookmark grounding check was word overlap at 60%
of content words. On the invented sentence "The group must not publish
confidential meeting notes", deleting `not` returned VERIFIED, "100% of content
words traceable" -- `not` was in the stop-word list, and so were `no` and `all`.
Reordering passed too. `agents/quote_verify.py` now decides it: contiguous whole
words in order, honest sentence boundaries, attribution checked, the CORPUS's
characters printed rather than the model's retyping, and unverifiable reported as
unverifiable rather than upgraded by an embedding score. A failure offers a real
verifiable excerpt instead of only refusing. The method is stored per product so
an old overlap pass is never read as today's check; no historical product was
rewritten.

**Cost of opening (rules 112-116).** Panels are lazily loaded: entry bundle
692 kB -> 242 kB (186 -> 76 kB gzip), and the build's size warning is gone rather
than configured away. The consultation poll refetched the whole session -- and
two copies of the transcript -- every four seconds; it now takes deltas keyed on
a per-turn revision (so a correction to the FIRST line is still caught), and an
idle poll is ~220 bytes at 60 turns or 600. `GET /products/summary` opens the
shelf in 78 KB where `GET /products` was 1.58 MB on the real database, with
search and filters applied to the whole shelf, not the page. Recordings are
saved as they are made instead of being held in a browser array until the end --
the old comment claimed a crash cost "the last few seconds" when it cost the
whole meeting. Microphones, recorders and pending `getUserMedia` no longer
outlive the panel, and leaving a listening consultation now asks first.

**Setup.** `python-multipart` was missing from `requirements.txt`; without it
`import agents.api` fails outright, not just the two upload routes. The suite's
last assertion required the owner's `.env` to exist, which is why a fresh
checkout reported 509/510; it now proves the same mechanism with its own
temporary dotenv, and the network tripwire also blocks DNS.

**Gatherings and Home (rules 117-120).** The deeds-first flagship, built on the
corrected records rather than beside them. A project lives in
`private/consultation.db` (rule 73 applies to a gathering's title and date
exactly as it does to a transcript) and OWNS nothing that already has an owner:
its commitments ARE the linked consultations' action items, derived on every
read like the finished-video shelf, so accepting one in the gathering view and
accepting it in the Consultation tab are the same act on the same row. Only
outcomes from a session whose record a human APPROVED can be carried forward,
and a session that is not eligible is listed WITH the reason rather than just
looking empty. The kit produces two real PDFs -- a programme page and the
existing duplex card sheet -- and they are deliberately never one file, because
a programme page inside the card sheet shifts every back face onto the wrong
side. A reading points at a verified passage by id and flows onto another page
rather than being truncated. Home replaced the Pipeline form as the default tab:
one cheap read, four sections in ordinary language, every section degrading on
its own, nothing started by opening the app.

**Verified:** 1,806 offline checks across ten suites, all passing, including 730
in `test_live_consultation.py` (+220 on 510) and a new `test_quote_verify.py`
(50). Both consultation suites also pass in a clean worktree with **no `.env`,
no keys and no private database**. Typecheck and production build clean; the
entry bundle is 246 kB against 692 kB before, and Home is 6.8 kB of it. The
programme PDF was rendered and **looked at**, not just byte-counted.

**NOT done:** the optional post-closeout handoff of accepted commitments into
Abigail's tasks (the Secretary APIs exist; the reviewed preview does not), and a
browser test harness -- there is still no frontend test setup in this repo, so
every frontend claim here rests on typecheck, build and reading the code.
**Nothing here has been used in a real room, at a real gathering, or in a real
browser.**

**Served live, 2026-09-10.** The new tabs came up 404 in the browser -- Home,
Gatherings and `/products/summary` all -- and the cause was NOT the code. The
managed API had been running since 9 Sep 13:16 and starts without `--reload`, so
the process in memory predated every new router; `/products/summary` fell
through to `/products/{product_id}` and answered "Product not found", which is
rule 116's registration-order symptom wearing a stale-process disguise. Restart
(kill :8765, `Start-ScheduledTask "bahAI Secretary API"`) fixed all three.
**Any session adding a router has to restart the backend before believing the
browser** -- a 404 on a brand-new endpoint is the expected first observation, not
evidence of a bug. What the restart then proved, which the offline suite
structurally cannot because it builds fresh temp databases: every migration
applied cleanly to the REAL 1.3 MB `private/consultation.db` (six new tables,
nine new `sessions` columns, three on `turns`), `/products/summary` returned
**78,340 bytes** for 164 items against the measured 1.58 MB before, Home read in
1,416 bytes with every section healthy, and all three new routes refuse an
unauthenticated call (401, rule 70). Still unwalked end to end: creating a real
gathering, linking a consultation, and printing a kit.

### 2026-09-03 -- Claude Code (Opus 5) -- the consultation record becomes true, and human-owned

Sheraj asked for Live Consultation to stop being a transcriber with an opinion
and start producing a record people can trust and correct. Phase 1 of an agreed
plan; the TRUTH compass, consultation projects and the post-meeting Abigail
handoff were deliberately deferred to a second pass.

**What was actually wrong, measured rather than assumed** (all from
`private/consultation.db`, counts only):
- 203 action items carried **3 owners and 0 due dates**; one meeting held 102
  near-identical actions. `upsert_action_item` matched on
  normalised text and returned the old row untouched, so an owner said aloud
  after the action was first noticed could never be recorded.
- 116 decision candidates, one meeting at 58, same cause.
- 2624 map items carried provenance on **28** of them, because
  `source_turn_ids` was asked for in a trailing sentence and never appeared in
  the JSON schema the model was shown.
- Two sentences on screen were false ("stays on this machine", "never heard by
  anyone else") and a third panel said there was no recorder, eighty lines
  above the working recorder checkbox.
- The "offline, free, no keys" suite made **three real authenticated calls per
  run** on the owner's key -- a model lookup, an actual mint of a live realtime
  credential, and a billable chat completion via `end_session` -> `build_report`
  -- and still printed 408 passed, because each sat behind an
  `except Exception`. The charge was invisible: spend is metered into a temp
  database the suite discards.

**What changed** (rules 94-99, appended to `AGENTS.md`): truthful privacy copy
and a server-enforced host attestation; per-session transcript retention and a
"delete the words, keep the record" path; identity on the map's stable id so
refinements land; `human_edited` protection; honest fact states where only a
human may claim more than "reported"; a lifecycle instead of deleting concerns;
a closeout where "no decision was reached" is a first-class outcome and a
confirmed decision can carry dissent with it; commitments with tri-state owner
acceptance; and a socket-level tripwire (deriving from `BaseException`, rule
55's reasoning) that makes the suite provably offline.

Also: `Consultation: A Compilation` (bahai.org) ingested as a verified corpus
for the live consultation only -- its own ChromaDB collection, cosine space to
match the others, per-passage citations, and a flag keeping Assembly-specific
guidance out of a general consultation. `bahai_texts` is untouched at 6,342
chunks, so nothing changed for quote cards (rule 11).

Files: `agents/live_consultation{,_store,_reasoner,_api,_report,_writings}.py`,
`agents/librarian.py` (one additive metadata passthrough),
`scripts/download_consultation_compilation.py`, `scripts/ingest_consultation.py`,
`dashboard/src/components/consultation/*` (new `ConsultationCloseout.tsx`),
`dashboard/src/lib/{api,consultationTypes}.ts`.

Verified: all eight Python suites green (Live Consultation 510, api_auth 66,
injection 50, colony 135, secretary_colony 92, job_cancel 24, wallet 90,
nuclei 281, video 288), `npx tsc --noEmit` clean, production build clean, and
the migration exercised against a COPY of the real database (9 sessions, 467
turns, 203 actions, 116 decisions as of 2026-09-09) with every row preserved,
`init_db` run twice, and no default implying consent, review or deletion that
never happened. **Not tested in a real meeting.**

Note: Sheraj held a real consultation on 2026-09-04 while this work sat in the
tree. It ran on the OLD code -- the API had not been restarted -- so it hit the
persistence defect like the rest (34 actions, no owners), and the real database
is still unmigrated. The migration runs the first time the API restarts on this
code.

**Damage done in this session, recorded honestly:** ~426 lines of uncommitted
tests for rules 89-93 were destroyed by a bad file write
(`io.open(..., newline="\\n")`, which truncates before raising) and were
unrecoverable. Replacement coverage was written fresh and passes, but it is not
what was lost. Nothing else in the tree was affected.


### 2026-08-26 -- Claude Code (Opus 5) -- the scripture corpus, exported to share

Sheraj asked whether the Bahá'í scripture vector databases could be handed to
another AI as a single attached file. The vectors cannot: `vector_store/` is
73 MB of ChromaDB holding 6,342 `bahai_texts` chunks and the 67
`ruhi_book1_quotes`, each a 768-number nomic-embed-text embedding, and those
numbers are only readable by that same embedding model plus a retrieval layer.
The TEXT travels, so `scripts/export_scripture.py` writes portable copies to
`outputs/scripture/`: one complete markdown of all 4,085 passages (~357k words),
a per-book split so each file fits an ordinary context window, the curated Ruhi
Book 1 pool, and a JSONL for rebuilding an index elsewhere. Passages are
verbatim (verified word-for-word against `texts/*.json`); only the source
pages' hard line wrapping was removed, and every passage keeps its section
heading and reference.bahai.org link so anything quoted from it can be cited.
Nothing in the pipelines changed -- this reads `texts/` and
`agents/ruhi_book1_source.py` and writes only into `outputs/`.

### 2026-08-27 (later) -- Claude Code (Opus 5) -- the brain moved to Luna

`CONSULTATION_REASONING_MODEL=gpt-5.6-luna` in `.env` (was the code default
`gpt-5.6-sol`; the code default is unchanged, so this is a machine-local
setting). Checked before switching, per rule 41a's discipline about never
assuming a model id: `gpt-5.6-luna` returns 200 on this account, and a real call
through `router.call_openai` with `json_mode` came back as clean JSON that
`_extract_json` parsed -- which is how the reasoner actually uses it.

**This is the brain, NOT the voice.** `CONSULTATION_REALTIME_MODEL` is unchanged
and is priced per minute of audio; nothing here makes a meeting cheaper to hold.
Existing sessions keep `gpt-5.6-sol` in their own row, so re-running an old
report uses what that session was created with.

Worth recording because it was the reason for the switch: August spend was
`image_gen` $7.95 (159 calls, the Artist -- nothing to do with consultation),
`openai_chat` $3.36, `openai_realtime` $3.12, `grok_vision` $1.41,
`grok_chat` $0.41, `claude_chat` $0.15, `openai_transcribe` $0.02. The single
largest line is bookmark/card artwork, not the Consultation tab.

### 2026-08-27 -- Claude Code (Opus 5) -- unblocked, and a visual aid

Sheraj, mid-test, with a screenshot: "I'm trying to test it now and it's not
letting me do anything." Three separate things, rule 93.

**The blocker was the spend ceiling, and the message was the bug.** He is at
$15.72 against a $15 ceiling, so the client-secret endpoint refused (rule 85,
correctly) -- but told him to "start anyway from the setup screen" while he was
on the LIVE screen, sitting in front of a started, recording session with no
button to press. The detail is now a plain fact and **Start anyway** sits under
it. Verified against the real ceiling: 402 without acceptance, a real `ek_`
credential with it.

**The error recorder from 2026-08-24 earned its place.** His session had
`[invalid_request_error during listening_idle] Cancellation failed: no active
response found` on the record -- a race we cannot win (the cancel is on the wire
when her response ends by itself), and nothing to alarm anyone with. Benign
errors are now filtered out of the banner and still recorded.

**"All I see is the initial announcement"** was the opening passage: ~1000
characters holding the top of a laptop screen and pushing the transcript off the
bottom. It now collapses to one line when she stops reading, and is capped and
scrollable while open.

**And the visual aid he asked for** (`ConsultationGlance.tsx`): a proportional
bar of agreed / unresolved / open questions, the subjects touched as chips (a
new `themes` list on the state), and "still to address" -- built from
`_open_threads`, the SAME function her spoken time check reads, so the screen
and her voice cannot disagree. Counts only, never a completeness percentage.

**Verified:** 408 offline checks (was 396), `test_api_auth` 66/66, `tsc` and the
build clean, API restarted and the over-ceiling path exercised live.

### 2026-08-25 -- Claude Code (Opus 5) -- the map got short, and the meeting got a report

Sheraj on the Consultation tab: "I don't like how the consultation map is and
how the confirmed decisions work. It is far too long to read." He was right, and
the honest answer to "are consultation maps supposed to be like that?" is that
there is no such thing -- I invented the name and then rendered the reasoner's
working structure straight onto the screen. Rules 90-92 are new.

**The live map is four things now**: where we agree, still unresolved, decided,
what happens next. The other ten lists moved to a Detail tab with the full
transcript. Decision candidates stopped being the loudest thing on the page --
rule 81 is untouched (a human still confirms) but adjudicating her guesses
mid-meeting was a job the tool was handing the room instead of doing for it.

**A report is written when the meeting ends**, and it is the front page of a
finished consultation. HYBRID, and the split is the point: the reasoning model
writes the narrative (it condenses -- that is what he asked for and what a
template cannot do), while the decision, the actions, the owners and any
passages are copied VERBATIM from the record. `_narrative` takes three prose
fields and drops everything else, so a model returning a `decision` or an
`action_items` list -- they do, and the suite feeds it exactly that -- cannot
have it printed. That is where rules 81 and 83 would have been quietly undone.
Copy and Download work. Verified against the real model once, on a throwaway
session: it correctly reported "No decision was confirmed" and left an unowned
action unowned.

**Named speakers, honestly.** He asked whether OpenAI can tell who is who. In a
live session, no -- `gpt-4o-transcribe-diarize` is documented as not available
to the Realtime API. So: record the room (his choice, explicitly), diarise the
file afterwards, and a human maps "voice B" to a name once. Diarisation is not
recognition and this never crosses that line -- the API accepts voice reference
clips to skip the mapping and we do not use them, because that is biometric
enrolment of his friends. The diarised transcript is stored BESIDE the live one,
never over it. Recording used to be refused outright (rule 86) and the setup
screen said nothing was kept; that was honest then and would be a lie now, so
the screen, the header light and the privacy paragraph all say it plainly.

**She opens the meeting and keeps the time.** In a Bahá'í consultation she reads
the passage on consultation he supplied, word for word, with the text on screen
beside her -- code-owned and fixed, which is what makes it safe under rule 84
(that rule stops a model paraphrasing scripture; it never banned scripture being
heard). Citation verified against bahai.org. A general consultation gets the
same qualities in her own words, quoting nothing and naming no religion. If a
duration is set she speaks twice at most -- at the warning point and at time --
giving the time and putting at most two loose ends from the map to the group as
questions. Both are a new SCHEDULED governor family: invited, because a human
asked in advance, and still refused by scribe mode, mute, pause and anyone
holding the floor. Neither is ever reached by silence.

**Also, a mic on every setup box** (rule 91b): press, say what the meeting is
about, press again. In-memory only -- nothing about dictation is written to disk
at either end -- on the OpenAI account already in use rather than the browser's
built-in recognition, which would hand it to Google. A press too short to be
speech is thrown away without being sent, because a transcription model given
silence invents a plausible word rather than returning nothing (one second of
silence really did come back as "Sijainti."). Dictated text APPENDS, so it can
never wipe what is already typed.

**Verified:** 396 offline checks (was 308), `test_api_auth` 66/66 -- which is
what proves the new endpoints are behind the owner gate -- plus colony, nuclei,
wallet and injection suites unchanged, `tsc` and the production build clean. The
API was restarted onto this code and the opening, the time check, participants
and the report were all exercised over HTTP. **Not yet used in a real room.**

### 2026-08-24 (later) -- Claude Code (Opus 5) -- Abigail stops cancelling her own answers

Sheraj, after a real session: "when I talk to abigail and just before she
responds it gives an error and she doesn't respond", and she "feels very
unresponsive even in the most responsive modes". Two separate faults, and they
were feeding each other. Rule 89 is new; rule 87 gained the lever it was
missing.

**The error was us, not OpenAI.** Barge-in is three events (rule 76), and
`cutOff` fired all three whenever the floor was `ai_speaking` *or*
`ai_preparing`. In the preparing window there is no audio yet and often no
response yet, so each event was refused: cancelling nothing, clearing an empty
buffer, truncating past the end of an item. And the cancel LANDED when the
response had just been created -- so a stray VAD trigger while she was thinking
threw her answer away, which is precisely "an error, and then she doesn't
respond". The client now tracks what is actually true on the wire and sends
each event only when the thing it acts on exists. Barge-in is untouched:
interrupted mid-sentence, all three still fire.

**The dial was missing its biggest lever.** `CONSULTATION_VAD_EAGERNESS` was
one fixed value while every other number scaled with presence -- and nothing
downstream can start until the detector reports the turn ended, so it is the
largest part of the wait. *Present* moved all the small waits and left the big
one alone, which is exactly what "unresponsive even in the most responsive
modes" feels like. It is now per-preset (low/medium/high), the env var demotes
to an explicit pin, and a mid-meeting change is pushed with one
`session.update` -- carrying the SERVER's `turn_detection` block verbatim,
because a browser-built one could omit `create_response: false` and rule 75
would be gone with nothing erroring anywhere.

**A third one found on the way, worth more than it looks:** both governors
answer "wait, try again in N ms" and the client threw N away, holding every
queued ask until the floor-open timer instead. That made a FAST transcript
slower than a slow one -- arriving inside the 400ms invitation grace meant
waiting the full floor-open window (3s attentive, 1.65s present) rather than
~200ms. Honoured now.

**And so the next one is not guessed at:** a realtime error is now RECORDED
(`POST .../client-error` -> a `realtime_error` row in the private DB), not just
shown in a banner that dies with the page. This bug had to be diagnosed from
four words because the backend log was all 200s -- the failing exchange never
touches our API. The row is inert for the floor, so a fault cannot quietly
extend a cooldown. A `response.done` with status `failed` surfaces the same way
instead of leaving a silent gap in the transcript.

**Verified:** 308 offline checks (was 296), `test_api_auth` 66/66 -- which is
what proves the new endpoint is behind the owner gate -- `tsc` and the
production build clean. The API was restarted onto this code and
`/live-consultation/capabilities` serves eagerness low/medium/high per preset
with `create_response` false in all three. **Not yet re-run in a real room**;
that is the only thing that will settle whether she now feels right, and the
error record is there to answer it if she does not.

### 2026-08-24 -- Claude Code (Opus 5) -- `npm run dev` starts the whole app, in order

Sheraj reported the app taking "forever to load" and then saying the backend was
not running. Two independent faults, both found live and both fixed by making
`npm run dev` do the starting instead of leaving it to luck.

**The backend was alive with a dead socket.** The API process from the 2026-08-21
logon was still running, but uvicorn's accept loop had exited with WinError 64
(`logs/api.err.log`), so nothing was on :8765. The Scheduled Task still read
"Ready" because it only triggers at logon, so nothing restarted it. Any check by
PID or by port would have said everything was fine -- only `/health` told the
truth, so that is the only thing `scripts/ensure_backend.ps1` trusts. It also
clears every matching process rather than just the listener: the venv's
`pythonw.exe` runs the real interpreter as a child, so one instance is two
processes (verified: pythonw 18060 -> python 32700, only the child on the port).

**And `localhost:5173` was a dead server.** Four Vite dev servers from 08-13 and
08-14 were still holding :5173-:5176 and answering nothing, while each new
`npm run dev` quietly moved to the next free port -- so the bookmarked address
spun for ever. `scripts/clear_stale_dashboards.ps1` clears them, scoped to node
processes naming this repo's own Vite binary so nothing else can match.

`dashboard/scripts/dev.mjs` sequences it: API answering -> Ollama and tunnel
REPORTED (never started -- a wrong guess there is a second copy of something) ->
leftovers cleared -> Vite. Ctrl+C stops the dashboard only, since Abigail
answers WhatsApp through the API with no browser open. `npm run dev:web` is the
old bare `vite`. Verified end to end: recovery from the real broken state (both
zombies cleared, healthy in 11.2s), then a clean run to :5173 with
`/api/health` 200 through the proxy.

**Still open:** the WinError 64 socket death recurred once during this session,
so the API can still die on its own between sessions -- `npm run dev` now
recovers it, but nothing watches it while the dashboard is closed. A watchdog
(or a repeating trigger on the Scheduled Task) is the real fix and was not
done. Note also that `start_secretary_server.ps1` OVERWRITES `logs/api.*.log`
on every start, so a restart destroys the evidence of the crash that caused it.


