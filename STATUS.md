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

## Snapshot (as of 2026-07-09)

Sheraj approved committing and pushing the current working tree on
2026-07-09. After that push, start from `git status` as usual before
making more changes.

**What's live and working** (committed, in production):
- Bookmark pipeline (Librarian → Artist → consultation → Scribe → Reviewer →
  Compositor → Canva autofill → Etsy draft) and the Quote Card giveaway
  pipeline (Ruhi Book 1 only, optional translation).
- Abigail (the Secretary) — Phases 1–3: dashboard chat + WhatsApp, real
  Claude tool-calling for every read/write, Google Workspace integration.
  Phase 4 (recovery rhythms) not started. Guest WhatsApp messages are styled
  and labeled by sender name.
- Trust/Steward reporting, print sheets, X-post giveaway pipeline.

**What's new and committed/pushed on 2026-07-09:**
- **Native visual layout editor** (both bookmarks and quote cards) —
  `agents/layout.py`, threaded through both compositors, 3 new API endpoints
  (`GET/POST /products/{id}/layout`, `.../layout/preview`), and
  `LayoutEditor.tsx` in the product drawer. Adjusts font/size/position/
  colour/shading only — never the printed text (verified: locked quote,
  Ruhi Book 1 restriction, and script-verified translation fonts all hold
  through it). Built in response to Sheraj's ask for Canva-like editing;
  Sheraj explicitly chose "build it yourself, skip the Canva round-trip."
  Grok has already exercised this live in production (see Activity Log
  2026-07-08 20:0x below) — it works.
- **Named roster + avatars** — `dashboard/src/lib/utils.ts`'s `ROSTER` maps
  backend ids to display names (Ruth/Librarian, Theo/Artist, Clara/Scribe,
  Amos/Reviewer, Nora/Steward, Sofia/Translator, alongside Abigail), wired
  into the Trust tab and consultation transcript. Display layer only —
  backend still keys on lowercase ids. **Avatars approved by Sheraj
  2026-07-10** ("pics are good, more zoomed in on their faces though") —
  all six were re-cropped tight on the faces (originals preserved in
  `dashboard/public/roster/originals/`; re-crop script pattern: crop from
  the ORIGINAL each time, per-face centers, so it's idempotent). Avatars
  live in `dashboard/public/roster/` (gitignored, private, like Abigail's
  photo).
- **Manual-edit honesty fix** — hand-editing a bookmark's quote via
  `PATCH /products/{id}` now flags `quote_verified: false` (dashboard shows
  a warning) and re-renders the printed face, and the honesty scrub
  (`_sanitize_claims`) now runs on every hand-edited field, closing a
  pre-existing gap. Owner decision: keep the field editable, don't lock it —
  just make the edit visibly honest.
- **Multi-coder infra** (this session, in progress) — `AGENTS.md` created as
  the canonical tool-agnostic instructions file; `CLAUDE.md` reduced to an
  `@AGENTS.md` import so Claude Code still auto-loads full context without
  duplicating it; this file (`STATUS.md`) created; `requirements.txt` fixed
  (was missing `chromadb`, `chonkie`, `beautifulsoup4`, which are real
  runtime/setup-script dependencies).

**Deferred / proposed but not started** (from
`docs/improvement-plan-2026-07-08.md`'s Part 2 audit — Sheraj hasn't asked
for these yet, listed so nobody re-discovers them from scratch):
- ~~Retire the vestigial "Operator" and "Producer" labels~~ — **done
  2026-07-10 by Grok** (assignee → `pipeline`, publish log → `steward`,
  removed from `AGENT_NAMES` + dead trust-row cleanup on init, dead
  persona/`plan`/`produce` prompt-builder entries removed).
- ~~Hide non-persona rows (`compositor`, `consultation`) from the Trust tab~~ — **done
  2026-07-10 by Antigravity** (filtered to active personas with runs).
- Relabel the Canva-autofill `log_run` entry from `"artist"` to whichever
  persona ends up representing publishing (currently misattributed —
  publish itself now logs under `steward`).
- ~~Remove the dead `framing_contribution` scripture entry and unused
  `GROK_TASK_TYPES` entries~~ — **done, see Activity Log below** (dispatched
  to Grok as the first real orchestration test).
- ~~Fix Codex's local-model routing~~ — **worked around 2026-07-09** without
  touching his config: per-invocation CLI overrides route dispatches to the
  cloud (`gpt-5.5`); exact command in `AGENTS.md`. The config file itself
  still points at the never-pulled `gemma4` — only matters if Sheraj wants
  the desktop app's local mode working.
- ~~Rule-4 gap in Abigail's `edit_product` tool~~ — **FIXED 2026-07-09**
  (Sheraj approved; implemented by Grok under Claude supervision, verified
  live, backend restarted — see Activity Log). Her tool now mirrors the
  dashboard PATCH path: `_sanitize_claims` scrub, `quote_verified=false`
  demotion on quote change, re-render that degrades to a note.
- ~~corrupted non-dict `layout_json` crash on GET layout~~ — **done
  2026-07-10 by Grok** (`sanitize` treats any non-dict as `{}`).
- ~~New (UX, advisory): LayoutEditor improvements~~ — **done
  2026-07-10 by Antigravity** (fixed Saved badge reset, close guard, and confirm on Reset).
- ~~etsy_publish docstring "price parsed from price_note"~~ — **done
  2026-07-10 by Grok** (now correctly documents policy price / rule 13).

**Uncommitted in the working tree (2026-07-11):** quote-card exactness work —
pinned quotes, quote lock, elision marks, tightened verbatim gate, non-italic
English card default, dashboard "Exact quote" field — plus a follow-up that
makes the consultation/Reviewer coherent when a quote is owner-pinned (they
stop re-picking/critiquing the quote and score artwork + framing only). See
Activity Log. Awaiting Sheraj's review/commit decision.

**Blocked on Sheraj:**
- Whether to proceed with the Canva-autofill log_run relabel (last remaining
  deferred item).

---

## Activity Log (newest first)

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

### 2026-07-16 (follow-up ×3) — Claude Code (Fable 5), direct — card back
tuned per owner over three iterations (each render-viewed, rule 9; final
values): artwork shows through the ENTIRE back — edges nearly unveiled
(cream alpha 28), writing panel opaque-enough-for-pen-ink (alpha 214), and
the opaque→clear shift is one LONG feathered gradient (mask blur h*0.075 —
no visible panel edge); exactly 3 ruled writing lines spread evenly.
Backend restarted, 9c71184e re-rendered + persisted. Applies to both
language variants' backs.

### 2026-07-16 — Claude Code (Fable 5), compositor direct + Grok (pipeline) +
Antigravity (dashboard) — QUOTE CARD REDESIGN: devotional/reflection format
Owner spec executed in full. `card_compositor.py` rewritten (Claude, all
faces human-viewed per rule 9): FRONT = quote in Tahoma dark ink on a light
wash (artwork Gaussian-blurred under a pale scrim — palette survives as
texture, no more poster look), thin double gold border, citation under a
small rule, hard readability floor `MIN_QUOTE_PX=26px/300dpi` (below it the
render REFUSES — no tiny/crowded/clipped quotes); BACK = reflection face on
cream (bold question, gentle action label, ruled writing lines, small
code-owned share line) — the quote NEVER prints on a back. Translated runs
now render SEPARATE per-language card pairs (variant front = translated
quote + code-appended disclaimer; variant back = reflection in that
language; hand-written Spanish defaults, English fallback for unverified
languages; zh/ar keep verified fonts — Tahoma only where script-safe).
Pipeline (Grok, diff verified): new `_card_reflection` (local Scribe writes
a quote-inspired question+action, json-parsed defensively, word-boundary
capped, translated via the checked translator path for native cards, {} →
code defaults on any failure), regenerated on every requote, tracked in
`best`, persisted (`reflection_*`, `variant_faces`) and passed through ALL
re-render sites (manual requote/repaint + layout endpoints); reviewer +
consultation wording updated to the new faces (machine contracts untouched).
Dashboard (Antigravity): variant card pairs shown/downloadable in the
drawer + pipeline results; layout editor dropped the dead placement select
and hides text-colour for cards (renderer ignores it now). layout.py:
default card font "tahoma" (new FONTS entry), translation_placement
REMOVED. AGENTS.md rule 29 records the format. Verified: imports, mocked
reflection tests (good/garbage/truncation), offline renders of 6 face types
viewed, tsc clean, backend restarted, then a REAL Spanish run end-to-end
(job 5615de05 → product 9c71184e): reflection "Did I trust God's timing in
my prayers?" + Spanish natives, both pairs persisted, 7.4 ship — final
faces viewed. Known minors: print sheets still print the ENGLISH pair only
(variant pairs downloadable individually — follow-up if wanted); LLM-
translated reflection text can carry small spelling quirks (a requote
regenerates it). Uncommitted — owner decides.

### 2026-07-12 — Antigravity (dispatched by Claude/Fable 5) — Ruth's display
title is now "Bahá'í Librarian" (owner ask). One line in
`dashboard/src/lib/utils.ts` ROSTER (`role` string only — key/backend ids/
AGENT_COLORS untouched; `rosterFor` matches on the lowercase KEY so
transcript mapping is unaffected). Verified: diff read, 1 grep hit, tsc
clean. Display-only; no restart needed.

### 2026-07-12 — Claude Code (Fable 5), direct + Antigravity for the editor UI —
long-quote cards split across faces: English front, translation on the back
Sheraj asked for long quotes (his 639-char "O thou who art turning thy face
towards God" card, 44ab19cb) to print English-only on the front with the
translation on the back — "it used the space much more effectively". New
layout knob `translation_placement` (layout.py TRANSLATION_PLACEMENTS:
auto|front|back, default auto; sanitize + options() extended; cards only):
"auto" routes the translation to the back when the English alone exceeds
`card_compositor.AUTO_BACK_QUOTE_CHARS` (320 — the approved 251-char
tabernacle card stays bilingual-front; 639-char cards split). New
`_composite_card_back`: without a routed translation it renders exactly as
always (light edge vignette); with one, a moderate scrim + the translation
and its NATIVE DISCLAIMER — the disclaimer always travels with the
translation, whichever face (rule 8), and fonts stay script-verified
(rule 9; no Latin override on the back). Reviewer's two-image intro updated
(back face is no longer promised "unobstructed"; legibility covers both
faces). LayoutEditor gained a "Translation" dropdown (Antigravity, guarded
on backend support). Rule-9 verified by eye: long card front (big, roomy
English) + back (Spanish + disclaimer over the artwork) + short card
(unchanged bilingual front) all viewed; sanitize accepts/falls-back
correctly; tsc clean; backend restarted; 44ab19cb re-rendered + persisted
via the layout endpoint (English front / Spanish back). Uncommitted — owner
decides.

### 2026-07-12 — Antigravity (Gemini 3.5 Flash) — LayoutEditor "Translation" placement control
Added a new dropdown selector for quote cards to determine translation placement (Auto / Front / Back). The selector only displays for quote cards when `translation_placements` options are supplied by the backend. The state round-trips correctly through preview and save payloads, updates dirty status, and is restored to its default ("auto") on reset.
Files: [LayoutEditor.tsx](file:///C:/Users/Sheraj/Documents/bahAI-workforce/dashboard/src/components/LayoutEditor.tsx), [types.ts](file:///C:/Users/Sheraj/Documents/bahAI-workforce/dashboard/src/lib/types.ts).

### 2026-07-12 — Claude Code (Fable 5), orchestrating Grok + Antigravity —
WhatsApp guest tier: allowlisted contacts reach a TOOL-LESS Abigail
Sheraj asked for his friend Garrett's messages to actually reach Abigail and
be visible in his chat. Design preserves rule 27's intent: the webhook is now
three tiers — owner → full `secretary.chat` (unchanged); ALLOWLISTED contact
→ new `secretary.guest_chat` (plain `call_claude`, structurally no tools, no
memory notes/tasks/calendar in context, history limited to that guest's own
thread via new `messages.sender` column, title-only notification to Sheraj);
anyone else → canned reply (unchanged). Sheraj's own chat context now filters
`thread="owner"` so guest rows can never leak into it. Dashboard: guest
bubbles labeled "Garrett · WhatsApp" / "Abigail → Garrett" with a distinct
tint (Antigravity). AGENTS.md rule 27 rewritten to record the owner decision.
Verified by Claude: diffs read, AST check that guest_chat calls only
{add_message, add_notification, call_claude, get_recent_messages}, migration
+ thread-isolation tests green, tsc clean, backend restarted, then a LIVE
signed-webhook test (real HMAC, fake allowlisted contact, probing question
about Sheraj's calendar): Abigail replied warmly, REFUSED the personal-data
request, offered to pass a message; rows stored sender-tagged; owner thread
clean; all test artifacts deleted. ALSO FIXED THE ACTUAL BLOCKER: Garrett's
contact row existed but `allowlisted` was FALSE (dashboard toggle never
flipped) — set true via the owner endpoint at Sheraj's request. Note:
Sheraj must also keep Garrett's number in Meta's test-number recipient list
(done, per Sheraj) or her replies can't deliver. Uncommitted — owner decides.
In the Secretary chat, added support to label and visually distinguish messages from allowlisted guest WhatsApp contacts. Guest user messages are styled with a distinct violet border and slate background (subtly different from Sheraj's golden user bubbles) and labeled with the sender's name (e.g. "Garrett · WhatsApp"). Assistant responses to guests are labeled "Abigail → Garrett". Declared the `SecretaryMessage` type locally in the panel component to add the `sender` field cleanly without modifying out-of-scope files.
Files: `dashboard/src/components/SecretaryPanel.tsx`.

### 2026-07-11 (late) — Claude Code (Fable 5) — WhatsApp outage: tunnel was
down (fixed + hardened); template fallback found never-created
Sheraj reported WhatsApp dead. Diagnosis: exactly one healthy API listener on
:8765 (no double-server), but NO cloudflared process — the Cloudflare Tunnel
(only inbound path for Meta webhooks) had died silently; its scheduled task
only fires at logon and its err-log gets truncated on start, so time/cause of
death are unknown. Fixed: `Start-ScheduledTask "bahAI Secretary Tunnel"`,
verified 4 registered QUIC connections, then probed the PUBLIC url end-to-end:
GET/unsigned-POST to /whatsapp/webhook return OUR 403s (fail-closed sig check,
rule 26, through the tunnel) and /products is 404 (ingress still restricted).
Outbound verified with a real send (accepted by Meta, free-form text).
Hardened: tunnel task now has RestartCount=5 / RestartInterval=2min so a
crashed cloudflared self-heals instead of dying silently. SEPARATE finding:
`WHATSAPP_UPDATE_TEMPLATE` ("secretary_update") does not exist in Meta's
system (132001 in every language; hello_world sends fine) — so the outside-
24h-window fallback (`send_best_effort` → `send_template`, used by scheduler
reminders) has been broken since Phase 3 and just never surfaced. Needs
Sheraj to create/approve a UTILITY template named `secretary_update`, body
exactly "{{1}}", English (US), in WhatsApp Manager — no code change needed.
Also noted: the "her own number" is still Meta's sandbox TEST number
(+1 555-156-8050, 5-recipient limit) — fine for now, a real number is a
future decision. Token confirmed permanent + valid. No code changed.

### 2026-07-11 — Claude Code (Fable 5), direct fix — card text render quality
(supersampling + size-scaled shadows)
Sheraj zoomed into a card's small Spanish text and the glyphs wore a blocky
black halo. Two causes in `card_compositor.py`: the text shadow is four
hard-offset near-opaque stamps at a FIXED 2px (huge relative to ~18px
translation glyphs), and faces were composed at final resolution with no
supersampling. Fix, contained to that file: `_SS = 2` — both faces compose at
2100×1200 and LANCZOS-downscale to the same 1050×600 @ 300dpi on save (art
round-trips unchanged; text/shadows get true anti-aliasing), absolute pixel
constants (font size ranges/floors, disclaimer size, rule width) scaled by
_SS so printed sizes are identical, and shadow offsets now scale with glyph
size (~size/26 — matches the old look on large text, proportionally thinner
on small). Verified by eye (rule 9): before/after 4× zoom crops of the same
Spanish line — old render shows the ragged stamp halo, new one is clean;
full face layout unchanged (minor word-wrap shift from 2× font hinting).
Backend restarted; test card 231eb62c re-rendered+persisted via the normal
layout endpoint so it carries the new quality. Every future card render
(pipeline, layout saves, requote/repaint, redo) picks it up automatically.
Bookmarks still render the old way (their text is much larger; offered as a
follow-up). Uncommitted — owner decides.

### 2026-07-11 — Claude Code (Fable 5), direct fix — pause preview restored on
pinned runs; redo-repin proven live
Sheraj reported the Reviewer's mid-run pause no longer showed the front-face
preview image. Root cause: a regression from the pinned-consult change — the
Librarian's deterministic confirmation turns emit no verified_quote, so
`run_consultation`'s `preview_quote` came back empty and the preview render
was silently skipped (guard `if render_preview and preview_quote`). Fix in
`consultation.py`: when `fixed_quote` is set, preview with the pinned text
directly (it IS the printed quote); non-pinned runs untouched. Verified live
via "redo everything" on test card 231eb62c (job d62977c2), which also
exercised the redo-repin path end-to-end for the first time: pause turn
carried the preview image + "as it would print" note, redone card's quote is
SHA256-identical to the original pin, quote_pinned persisted, and the stale
mis-scored 6.4 review was replaced by an honest 7.8/ship (artwork_fit 8,
two-image Reviewer). Backend restarted. Uncommitted — owner decides.

### 2026-07-11 — Claude Code (Fable 5), orchestrating Grok — the card Reviewer
now SEES the artwork it judges (front + clean back face)
Sheraj flagged a real mis-score on product 231eb62c: artwork_fit 3/10 with
"lotus artwork diverges from the settled agreement" — but the saved artwork IS
the agreed hand-withdrawing-from-valuables scene; the card's front-face
vignette + printed quote simply cover it, and the Reviewer only ever saw the
front. Fix (Grok, diff fully re-verified): `router.call_grok_vision` now
accepts an ordered list of images (backward compatible — every single-image
caller unchanged); `reviewer.score_quote_card` and the revision-consult
Artist turn (`run_card_revision_consultation`) take `back_image_path` and,
when it exists, attach the clean back face as a SECOND image — legibility is
judged from the front, the artwork itself from the unobstructed back; all
four api.py call sites (pipeline score, revision consult, manual requote,
manual repaint) pass `rendered["back_path"]`. Verified by Claude: full diffs
read (no mojibake), offline monkeypatched block test (2 imgs → image,image,
text; 1 img → image,text), and a LIVE re-score of the same product through
the new path: artwork_fit 3→7 ("reaching hand … evokes temptation and
restraint"), newcomer 4→8, overall 7.6 ship — the score now describes the
real artwork. Backend restarted. Note: 231eb62c's STORED review still holds
the old wrong 6.4 (test card; delete or redo it from the dashboard).
Also this session, prior entry's Part A/B: redo-repin + pinned-consult
coherence. Uncommitted — owner decides.

### 2026-07-11 — Grok (dispatched by Claude/Fable 5) — pinned-quote consultation coherence
Live bug: with an owner-pinned quote the three-round consult still proposed/
critiqued/swapped quotes and the Reviewer scored 5.6 with "requote" for
"diverging from the agreed quote" even though the pipeline correctly printed
the pin. Additive, gated changes only: `consultation.run_consultation` /
`_run_round` / `_synthesize_brief` take `fixed_quote`; when set, Scribe frames
artwork/tone only, Reviewer challenges art not quote, Librarian skips the
LLM and emits a deterministic confirmation, Artist/synth get a fixed-quote
note. `reviewer.score_quote_card(..., quote_pinned=)` drops requote from the
action list and rewrites quote_citation / newcomer criteria for presentation
only. `_run_card_pipeline` wires both from the resolved pin (not the mid-run
pause note). Non-pinned paths and the VERDICT parser left unchanged.
Files: `agents/consultation.py`, `agents/reviewer.py`, `agents/api.py` (two
call sites). Acceptance checks (import/signature/parser/ast) all ok.
Claude also added directly (Part A of the same ask): "redo everything" on a
pinned card now re-supplies the stored quote as `pinned_quote`, so a redo
re-verifies and re-locks it (`_redo_card`) — a redo changes artwork/framing,
never the owner's chosen words. Verified by Claude beyond Grok's self-report:
full diffs of all three files read (in scope, no mojibake/BOM), non-pinned
reviewer branches byte-identical, earlier exactness suite still green, backend
restarted, and a REAL pinned run end-to-end (job 90ee99c0 → product 231eb62c,
tabernacle quote + Spanish): transcript now reads sensibly — Scribe does
"framing & tone", Reviewer challenges the ARTWORK (soft lotus vs. the quote's
hard refusal), Librarian confirms provenance, revision chose REPAINT not
requote; printed quote SHA256-identical to the pin, front face human-viewed
(regular English, italic Spanish, full quote, disclaimer). Score 6.4 now
reflects a genuine art-fit note, not the old self-inflicted "diverged from the
agreed quote" penalty.

### 2026-07-11 — Claude Code (Fable 5), orchestrating Grok + Antigravity —
quote-card exactness: pinned quotes, honest elision marks, quote lock, non-italic English
Sheraj's ask after a real run overrode his mid-run "use the full quote please"
guidance (product 7131d403 shipped a different quote than he pinned at the
pause). Backend (Grok, diff fully re-verified by Claude): (1) new
`pinned_quote` on the card pipeline — an owner-supplied quote is resolved
against the 67-entry verified Ruhi corpus (lenient matching for apostrophe/
spacing paste differences, but the PRINTED text is always the corpus's exact
characters), SHA256-checked against the frozen manifest on input AND re-hashed
against the saved quote before the product writes, failing loudly before any
paid call if it can't verify; (2) quote LOCK — a pinned run, or any run where
Sheraj gave pause guidance, makes the revision loop's "requote" structurally
impossible (consult prompt drops the option AND api.py hard-gates it with a
visible transcript note); (3) shortened quotes now carry the book's own
" . . ." elision marks and `_assert_ruhi_verbatim` was tightened from
any-prefix to exact-full-match OR marked sentence-boundary elision — unmarked
prefixes, mid-sentence cuts, and missing punctuation all now hard-fail;
(4) manual requote endpoint gained the same gate (was ungated — found by
Claude in review); (5) card English default font is now Palatino REGULAR
(layout.py CARD_DEFAULTS; bookmarks unchanged; translations keep their
script-verified stacks — Spanish stays italic). Frontend (Antigravity):
optional "Exact quote" textarea on the card form → `pinned_quote`.
Verified by Claude: full diffs read, offline suite (gate accepts exact/
elided, rejects prefixes/mid-sentence/dropped-punctuation; resolver handles
messy pastes + rejects garbage), tsc clean, backend restarted, then a REAL
pinned run end-to-end (job 47f2b82b → product 05582ef5, the tabernacle quote
+ Spanish): printed quote SHA256-identical to the pinned input, the revision
team tried to swap it again and the lock forced ship-as-is, front face
human-viewed (regular English, italic Spanish, full quote fits, disclaimer
on). One earlier attempt failed honestly on a transient xAI connection reset
mid-translation (~$0.07 spent, no product). Known minor gap: "redo
everything" on a pinned card doesn't re-pin (noted, not built). Uncommitted —
owner decides.

### 2026-07-10 — Claude Code (Fable 5), orchestrating Grok ×3 + Antigravity —
audit plan executed: safety, honesty, and the deeds-first reorientation (Phases 1–2)
Sheraj greenlit the audit plan. Four sequential edit waves, each diff-reviewed
and re-verified by Claude before the next: **W1 Grok (Secretary safety)** —
rule-24 Drive move now queues for approval like rename/trash (+ approval-time
executor), all-day events get Google's exclusive end date, WhatsApp webhook
dedupes on message_id (`wa_seen` in private DB, ids only), quiet-hours
calendar reminders persist via the reminders table instead of dropping.
**W2 Grok (honesty+hygiene)** — X disclosure is now " · AI-assisted art",
never silently dropped (over-budget drafts fail visibly; no auto-truncation
of quotes), `get_spend_summary` returns an `error` field instead of fake $0,
Canva autofill parked behind `CANVA_AUTOFILL_ENABLED` (default OFF, visible
skip note, relabeled steward), rule-14 `log_run` alignment (mechanical steps
→ None; reviewer/grounding/translator judgments kept), dead code removed
(`compositor.render_bookmark`, `"copy"` task type, reviewer "round 2" label).
Note: W2's first dispatch made zero edits — Grok stopped at the uncommitted
tree per the multi-coder norm; re-dispatched with explicit authorization.
**W3 Grok (deeds backend)** — `distributions` table + `add_distribution`/
`get_deeds_summary` in state.py; `POST/GET /deeds`; Steward report now leads
with a `deeds` key; `print_sheet` accepts multiple products per sheet
(cycled grid) + `duplex` column-mirroring for long-edge flip; new
`POST /print-sheet {product_ids, duplex}` (PDF response, 422s on mixed
types/missing faces). **W4 Antigravity (dashboard)** — Steward panel shows
"Deeds for the Betterment of the World" ABOVE the Financial Ledger (+ recent
deeds, red note on ledger error), "Record a gift" in the product drawer,
multi-select + "Print gathering sheet" with duplex toggle, ErrorNotes on
SecretaryPanel/XPostsPanel queries, Canva skip reason visible in
PipelinePanel, Nav uses RosterAvatar. All verified: imports, offline ledger
test, TestClient (deeds 200/422, mixed sheet PDFs 1.5MB real renders, 422
paths), `tsc --noEmit` clean, backend restarted, live /deeds + steward-with-
deeds + /products 200. Remaining from the audit plan (deliberately deferred,
need their own sessions): devotional-gathering KIT pipeline (N new cards +
program page), Abigail Phase 4 recovery rhythms, grounding-bar tightening
for bookmarks, retrieval enrichment, free share-image exports, multilingual
packs. Nothing committed yet — owner decides.

### 2026-07-10 — Claude Code (Fable 5), orchestrating all four coders — full
read-only codebase audit against the mission (deeds first, money as byproduct)
Sheraj set the direction explicitly: the workforce prioritizes pure and goodly
deeds for the betterment of the world; money is a byproduct. Four parallel
read-only investigations (Grok: pipelines; Codex: Secretary+integrations;
Antigravity: dashboard; a Claude subagent: output/economy/docs). Full reports
in the session scratchpad; synthesis + plan delivered to Sheraj in chat.
**Two claims verified by Claude as REAL, both unfixed as of this entry:**
(1) **rule-24 violation** — `secretary_tools.py` `organize_drive_file`'s
`move_to_mine` calls `gdrive.move_file` with NO `is_in_her_folder` gate
(rename/trash gate correctly), and `move_file` strips all old parents — an
LLM tool call can relocate ANY Drive file without approval; (2) the X post's
AI-art disclosure is a lone " 🤖" emoji, silently omitted when the tweet is
long (`x_post.py::_with_disclosure`) — weakest disclosure on the only live
public channel. Other headline themes: Canva autofill is 10/10 broken and
still runs every pipeline; Steward returns $0 on DB error; several dashboard
panels swallow query errors; quiet-hours can permanently drop calendar
reminders; all-day events get an invalid end date; WhatsApp webhook has no
message_id dedupe; Steward/dashboard speak P&L while the working deed-path
(print sheets, giveaways, feedback) has no headline metrics. No code changed
this session (audit only).

### 2026-07-10 — Claude Code (Fable 5), orchestrating Grok + Antigravity —
avatar face-crops, small-fix batch, Operator/Producer retirement (integration note)
Sheraj approved all three pending decision items in one go. Claude scoped
everything against the real code first, then split: avatars done directly
(cropping needs eyes — all six re-cropped ~55% tighter on the faces with
per-face centers, before/after viewed, originals kept in
`roster/originals/`); backend batch dispatched to Grok and frontend batch
to Antigravity (their entries below). Both dispatches came back clean
(no repeat of Codex's 2026-07-10 encoding corruption) and every claim was
re-verified independently: full diffs read, imports + sanitize edge cases
re-run, `tsc --noEmit` clean, backend restarted, live `/agents` confirms
the operator/producer trust rows are deleted. **One real regression caught
in review and fixed by Claude:** Antigravity's LayoutEditor rework re-seeds
the controls from the cached layout query on reopen, but a save never
updated that cache — save→close→reopen would have shown pre-save knobs.
Fixed in `save.onSuccess` via `queryClient.setQueryData` (keeps the cached
`current` honest). Dashboard-visible: tighter avatar faces everywhere,
honest Saved badge, discard/reset confirms, Trust tab shows only the six
named personas + Abigail.

### 2026-07-10 — Antigravity — LayoutEditor UX fixes + TrustPanel persona filtering
Implemented four owner-approved dashboard fixes: (1) LayoutEditor "Saved." badge now resets via `save.reset()` on control change (`set()`) or default `reset()`; (2) added a `dirty` state that prompts via `window.confirm` if trying to close the LayoutEditor with unsaved changes; (3) added `window.confirm` before resetting LayoutEditor controls; (4) filtered the TrustPanel agent roster to show only real personas (`rosterFor(a.name)` is truthy and `total_runs > 0`), aligning both the grid rows and the empty state check.
Files: [LayoutEditor.tsx](file:///C:/Users/Sheraj/Documents/bahAI-workforce/dashboard/src/components/LayoutEditor.tsx), [TrustPanel.tsx](file:///C:/Users/Sheraj/Documents/bahAI-workforce/dashboard/src/components/TrustPanel.tsx).

### 2026-07-10 — Grok — three small backend audit fixes
Closed three audited items: (1) `layout.sanitize` now treats any non-dict
`layout_json` as `{}` so `GET /products/{id}/layout` no longer crashes on
corrupted data; (2) `etsy_publish` docstring corrected to match rule 13
(price from `etsy.BOOKMARK_PRICE`, not LLM prose); (3) retired vestigial
operator/producer labels — task assignee is `pipeline`, Etsy publish
`log_run` is under `steward`, both names removed from `AGENT_NAMES` with
a one-time `DELETE` on init, and unused persona/`plan`/`produce` entries
dropped from `system_prompt_builder.py` after confirming no callers.
Files: `agents/layout.py`, `agents/api.py`, `agents/state.py`,
`agents/system_prompt_builder.py`. No git commit; orchestrator restarts.

### 2026-07-10 — Claude Code (Fable 5), orchestrating (Codex dispatch rejected) —
card quotes now machine-verified character-exact against the official Ruhi Book 1 PDF
Sheraj supplied the official PDF (edition 4.1.2.PE, Downloads folder — kept
out of the repo, copyrighted) and asked for exact-match assurance on card
quotes. Claude verified all 67 `agents/ruhi_book1_source.py` entries against
the PDF: 61 already exact, 2 pure extraction artifacts, 2 honestly-marked
elisions — and **2 real silent splices fixed** (entries "world of the womb"
and "bird which soareth" now carry the book's own ". . ." elision marks;
ChromaDB re-ingested afterwards). New: `scripts/verify_ruhi_book1.py`
(repeatable PDF verification + freezes a SHA256 manifest,
`agents/ruhi_book1_manifest.json`) and `api._assert_ruhi_verbatim` — a
render-time gate at BOTH quote-pick sites (initial + requote) that fails the
job loudly if the about-to-print quote isn't a verbatim prefix of a
manifest-verified corpus entry (catches stale index / unverified corpus
edits). Prompt honesty tightened: the card frame's "adapted at most lightly"
became word-for-word (`consultation.py` source_scope + quote_spec), and the
Reviewer's quote_citation criterion now says the text is machine-verified —
judge selection, never propose rewording. Verified by Claude: 67/67 both
script modes, imports clean, gate positive/prefix/tampered/empty/stale tests
all correct, live retrieval→trim→gate pass, backend restarted + health 200.
**Dispatch note:** the Codex worker completed this task logically but
re-wrote `agents/api.py`/`requirements.txt` with a BOM + cp1252 mojibake
(every em dash/arrow corrupted) — its output was reverted wholesale and
re-implemented by Claude directly, reusing its gate logic. Watch for this on
any future Codex dispatch that edits files on Windows; `git diff` caught it.

### 2026-07-10 — Claude Code (Fable 5) — Codex now defaults to GPT-5.5
Sheraj reported Codex still using `gemma4` and asked for GPT-5 as an
option. Edited `~/.codex/config.toml` directly (backup first:
`~/.codex/config.toml.backup-2026-07-10`): `model = "gpt-5.5"`,
`model_provider = "openai"`, `model_catalog_json` → `merged-models.json`.
Nothing else in the file touched (desktop-app sections, plugins, MCP
intact). Verified with a no-override `codex exec` — answered on gpt-5.5,
and the old "model metadata not found" warning is gone. Caveat recorded in
AGENTS.md: the Codex desktop app also rewrites this file; if it reverts,
re-apply or use the per-invocation overrides (which always win).

### 2026-07-09 — Claude Code (Fable 5), orchestrating Codex + Antigravity —
consultation transcripts now read as natural speech
Per Sheraj's ask ("the consultation conversations are bullet points that
don't really make sense to me"), Claude first mapped the machine-parsed
anchors in `agents/consultation.py` itself (only two exist: the
Librarian's VERDICT/VERIFIED QUOTE block, and the revision-consult
decision JSON — everything else is read only by other LLMs), then
dispatched in parallel: **Codex** rewrote every turn instruction from
"exactly N bullet points" to 2-4 natural first-person sentences (same
content requirements, honesty cautions, scripture references, and word
caps +≤15; max_tokens/temperatures untouched; the Scribe's quote now
arrives on a cued "My proposal:" line) and rebuilt the code-built "final
call" turns as sentences ("My call: find a different quote — ..."), for
the 3-round consultation AND both card/x-post revision consults;
**Antigravity** (accept-edits mode, first agy write dispatch) taught
`ConsultationTranscript.tsx` to render Ruth's rigid VERDICT block as a
friendly verification card (verdict chip, styled quote, source, reasoning
as a sentence; non-matching turns render exactly as before). Verified by
Claude: both diffs read in full (only the two intended files), parsers
asserted unchanged, `tsc --noEmit` clean, backend restarted, then a REAL
card pipeline run end-to-end (theme "the healing power of prayer", job
9bdf69ec → product 8b7517a9): turns read as genuine conversation, the
Reviewer held twice with reasons, the human pause worked, the revision
consult requoted to a verbatim 'Abdu'l-Bahá passage, and the final card is
quote_grounded=True, 6.2/10 best-effort (kept in the DB — Sheraj can
delete it from the dashboard if unwanted; ~$0.15 metered spend). AGENTS.md
hard rule 10 rewritten to record the owner-approved restyle and the two
surviving machine contracts. Uncommitted, awaiting Sheraj's review.

### 2026-07-09 — Claude Code (Fable 5), orchestrating ALL THREE workers —
full app scan + honest README rewrite
Per Sheraj's ask ("update the README so it reflects the actual app right
now — no Etsy integration, you download a file and take it to a printer"),
Claude first verified the central claim itself (no `etsy_token.json`
exists; 0 of 84 products have an `etsy_listing_id` — Etsy publishing has
NEVER run), then dispatched three parallel read-only scans: Codex on
principles-as-code + architecture + grounded future directions; Grok on a
reality audit (what actually works vs built-but-dormant, with DB/file
evidence); Antigravity on a tab-by-tab dashboard walkthrough. Load-bearing
claims were re-verified by Claude against the DB before use: Canva
autofill has failed ALL 10 attempts ever made (same 400, last 2026-07-05)
despite an authorised token; the X giveaway is real (2 live posts on
@peaceAntz); no product has ever hit the 9.0 target (median bookmark
score 7.6); total metered+legacy spend ≈ $13.72, revenue $0. `README.md`
was fully rewritten from these findings: leads with what the app actually
produces today (300 DPI faces + cut-tolerant print-sheet PDF → print
shop), an "Honest status: what's real, what's not" table (Etsy built but
never connected; Canva built but broken; pipelines/editor/Secretary/X all
real), why-this-exists (principles enforced as code, with the concrete
mechanisms), the named roster, the user-facing workflow, corrected run
instructions (backend runs as a Scheduled Task — the old README's
`python agents/api.py` advice reintroduced the double-server bug), an
updated file map, and code-grounded future directions. Grok also surfaced
one tiny docs bug (see Snapshot). No code changed — README.md and this
file only.

### 2026-07-09 - Codex - committed and pushed current work
Sheraj asked Codex to push the latest changes to GitHub, then explicitly
approved committing the current working tree. Codex updated this handoff note
so it no longer describes the work as waiting for commit approval, then
packaged the accumulated layout editor, roster/dashboard, Secretary honesty,
multi-coder docs, architecture, and requirements changes into one commit for
push to `origin/master`.

### 2026-07-09 — Claude Code (Fable 5), orchestrating Grok — first WRITE
dispatch: fixed the Secretary rule-4 gap
Sheraj approved fixing the gap found earlier today. Claude scoped the fix
itself first (read `api.py::edit_product` 2358-2432 as the reference
implementation), then dispatched a precisely-specified edit to Grok
(foreground, `--permission-mode acceptEdits`, Edit/Write allowed, all
git-mutating commands denied). Grok's diff was exactly in scope: only
`agents/secretary_tools.py`'s `edit_product` branch, now mirroring the
dashboard path — `_sanitize_claims` on every edit, `quote_verified=false`
+ face re-render (degrading to a note on failure) on a real quote change,
and the post-scrub title persisted instead of the raw edit value. Claude
verified independently: full diff read, `import agents.secretary_tools` +
`import agents.api` clean (no circular imports from the new lazy imports),
and a live behavioral test through the REAL tool executor on a throwaway
product — scrub fired ("handcrafted" → "made-to-order"), demotion + reply
wording correct, missing-artwork re-render degraded to a note without
blocking, test row deleted after. Backend restarted per the documented
procedure (killed PID on :8765, `Start-ScheduledTask`, `/products` → 200)
so the LIVE Abigail process carries the fix. Note: Grok's handoff text
didn't come back through the pipe this time (output ended after its
progress lines) — didn't matter, since verification never trusts the
handoff anyway.

### 2026-07-09 — Claude Code (Fable 5), orchestrating ALL THREE workers —
full three-agent orchestration confirmed working
Per Sheraj's ask to "get them all working and confirm they work": unblocked
Codex without touching his config file (per-invocation `-c
model_provider=openai -m gpt-5.5` overrides — the only cloud slugs his
ChatGPT account accepts are `gpt-5.5`/`gpt-5.4`/`gpt-5.4-mini`; the
`gpt-*-codex` names are rejected), then dispatched three independent
READ-ONLY tasks in parallel (chosen read-only deliberately — the tree is
full of uncommitted work, so no dispatch could collide with it or with each
other). Grok audited hard rule 4 across every listing write path; Codex
reviewed `layout.py::sanitize()` (it live-probed the function with real
Python calls, not just reading); Antigravity UX-reviewed `LayoutEditor.tsx`.
Antigravity's FIRST dispatch misfired instructively: `--print` takes the
prompt as its own flag value, so `--print --mode plan "<prompt>"` fed it the
literal string `--mode` as the prompt, and it ran in its own scratch dir
instead of the repo — both fixed (`-p "<prompt>"` last + `--add-dir`),
documented in `AGENTS.md`. Every worker claim was independently re-verified
by Claude reading the actual code before being accepted. Results: **one
real rule-4 violation found** (Abigail's `edit_product` tool skips the
honesty scrub — see Snapshot), one low-severity crash edge, five UX
recommendations (top one verified). No repo code was changed by any worker
(verified via `git status` — only Claude's own doc edits to AGENTS.md +
STATUS.md this session). Fixes not yet applied; recommended as next step.

### 2026-07-09 — Claude Code (Sonnet), orchestrating Grok — first real
multi-agent dispatch test
Per Sheraj's request to have Claude act as an orchestration layer for
Grok/Codex/Antigravity ("give them grunt work while you monitor them and
make sure the goal is achieved"), and his answers to 3 follow-up questions
(Moderate autonomy — agents may edit + run safe local commands without
asking, never git push/destructive ops; install Codex now; test it for real
today): installed the Codex CLI (`npm install -g @openai/codex`) — its
ChatGPT auth actually works (confirmed via its own log), but local dispatch
is currently blocked by a pre-existing config pointing at a never-pulled
Ollama model (`gemma4`) — not something Claude created, left for Sheraj to
decide. Confirmed Grok and Antigravity (`agy`) are both already installed
and authenticated on this machine.

Dispatched a real, precisely-scoped task to Grok: remove the dead
`framing_contribution` entry from `consultation.py`'s `CONSULTATION_SCRIPTURE`
and the three genuinely-unused entries from `router.py`'s `GROK_TASK_TYPES`
(Claude first re-verified the original claim with a proper multiline grep
across every `call_llm()` call site — the real dead set turned out to be
`{"copywriting", "review", "complex_analysis"}`, not the slightly-wrong set
noted in an earlier session's audit; `"copy"` stayed because it's used by
`router.py`'s own manual self-test). First dispatch attempt (backgrounded,
`--permission-mode acceptEdits`) was correctly blocked by Claude Code's own
safety classifier for running unsupervised with edit permissions — re-ran in
the **foreground** instead so a human (Claude, actively) was watching it
complete. Grok's result was independently re-verified (not just trusted):
`git diff` read in full, a fresh import check, and a repo-wide grep for the
removed strings — all confirmed exactly the intended two-file, two-change
diff, nothing else touched, and Claude's own earlier uncommitted edit to
`router.py` (the docstring fix, see below) survived untouched.

**Real finding, not assumed going in**: Grok's `--worktree <name>` flag did
NOT actually isolate the session when combined with headless `--prompt-file`
mode — `git worktree list` showed no new worktree was created; Grok edited
the main working tree directly. Isolation can't be assumed and must be
checked with `git status`/`git diff` after every dispatch — documented as a
hard caveat in `AGENTS.md`'s new "Dispatching grunt work" section, along
with the exact working command pattern for future sessions (any tool) to
reuse rather than rediscover.

### 2026-07-09 — Claude Code (Sonnet)
Set up multi-coder infrastructure per Sheraj's request (he now has Claude
Code, Codex, Antigravity, and Grok all working on this repo). Created
`AGENTS.md` as the single canonical, tool-agnostic instructions file
(previously `CLAUDE.md`'s content, which was already written generically);
reduced `CLAUDE.md` to an `@AGENTS.md` import so Claude Code keeps
auto-loading full context without a second copy to drift. Created this file
(`STATUS.md`) as the living snapshot + hand-off log, seeded with reconstructed
history from `git log` and this session's own findings. Audited
`requirements.txt` against actual imports in `agents/`/`scripts/` and found
three real gaps (`chromadb`, `chonkie`, `beautifulsoup4` are imported but
were never listed) — fixed. No application code changed.

### 2026-07-09 — Claude Code (Sonnet) — Part 2 of the editor/roster plan
Per Sheraj's answers to 4 clarifying questions: (1) manual quote edits stay
allowed but now flag `quote_verified: false` and re-render + always run the
honesty scrub (`agents/api.py::edit_product`); (2) named the roster with
everyday names (Ruth/Theo/Clara/Amos/Nora/Sofia) alongside Abigail; (3)
generated six avatar portraits via the Artist's own xAI image pipeline in a
consistent Persian-miniature style (~$0.30 metered spend) — sent to Sheraj
as a montage for approval, **response still pending**; (4) avatars kept
private/gitignored like Abigail's photo. Wired the roster into
`TrustPanel.tsx` and `ConsultationTranscript.tsx` via a new `RosterAvatar`
component (falls back to an initial if the image is missing) and a
`ROSTER`/`rosterFor()` registry in `dashboard/src/lib/utils.ts` — display
layer only, backend trust/log keys unchanged. Also fixed two small stale-
docs issues found during the earlier audit: `router.py`'s docstring falsely
claiming the Secretary's tool-calling path is read-only, and Reviewer
prompts saying "the team consulted in two rounds" when it's actually three.
Verified live: dashboard typecheck clean, `edit_product` tested end-to-end
against a real product (no-op edit, quote-change flag + re-render, then
fully restored to original state).

### 2026-07-08 ~20:00–20:03 — Grok (discovered via DB inspection, not
observed live)
While unattended, Grok exercised the newly-built layout editor on a real
quote card (`0eaf3ea5`, "The betterment of the world…") — saved a custom
layout (Palatino regular, 75% text size, stronger vignette) that rendered
correctly, including the Chinese translation correctly keeping its own
script-verified font untouched by the English-side font/colour change (rule
9 held). Also ran a fresh card pipeline end-to-end, producing a new card
(`ccfbf8f4`, "Beware, O people of Bahá…", Spanish translation, requoted once
during revision). Both actions went through the running app cleanly with no
code changes — this was live validation that the layout editor works in
production, found by a later Claude Code session reviewing `workforce.db`
timestamps and `task_runs` after the fact.

### 2026-07-08 — Claude Code (Fable 5 / Sonnet) — visual layout editor
(Part 1) + full roster/consultation audit (Part 2, written up as a plan)
Researched Canva's Connect API capabilities (autofill, editor return-
navigation, import/export — confirmed no server-side design-editing API
exists for partner integrations) and wrote
`docs/improvement-plan-2026-07-08.md` covering: a proposed editor
architecture, a full audit of every real agent (Librarian/Artist/Scribe/
Reviewer/Translator/Abigail, plus debunking "Operator"/"Producer"/"Steward"
as non-agents), and a consultation-logic health check (all 6 scripture
citations verified authentic and correctly attributed; rules 11/12's Ruhi
Book 1 restriction and grounding re-check confirmed airtight). Sheraj's
response: skip the Canva round-trip entirely, "build it out yourself." Built
the native editor: `agents/layout.py` (font registry, defaults, the
`sanitize()` boundary that clamps untrusted input and never carries text),
threaded `layout`/`dest_stem` params through `compositor.py` and
`card_compositor.py` (defaults reproduce the pre-editor render byte-for-
byte — verified), added `layout_json` column + 3 API endpoints, and built
`LayoutEditor.tsx`. Verified offline (render tests both product types) and
live (real HTTP calls against the running server, save + persistence + a
visual read-back of the rendered PNGs).

### 2026-07-07 — commit `bada6fc`
"Migrate Secretary to real tool-calling, add WhatsApp + Google Workspace,
and give her a Products-tab presence." Replaced the earlier custom
`<remember>`/`<task>`/`<event>`/`<remind>` text-tag design with real Claude
tool-calling (`router.call_claude_agentic` + `agents/secretary_tools.py`) —
the text-tag approach was unreliable in long sessions (Abigail would narrate
an action with no tag behind it). Added WhatsApp (Meta Cloud API, her own
number) and the full Google Workspace suite (Gmail, Drive, Docs, Sheets,
Slides) behind one shared OAuth module.

### 2026-07-06 — commits `dcbf5af`, `a0430d9`
Print sheet generation (`agents/print_sheet.py`, cut-tolerant multi-up PDF).
Secretary Phases 1–2 (calendar, badí dates, scheduler) plus hardening of
Etsy honesty/pricing rules and the deterministic quote-grounding re-check.

### 2026-07-05 — commit `b692419`
Added the Quote Cards giveaway product line (Ruhi Book 1 restricted
sourcing, multi-script rendering, translation) and improved the multi-agent
consultation to its current 3-round-with-human-pause structure.

*(Earlier history — n8n retirement, the original async pipeline, Etsy
integration, initial Phases 1–4 — is in `git log`; not reproduced here to
keep this file readable. Run `git log --oneline` for the full list.)*
