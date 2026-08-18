# AGENTS.md — orientation for AI coding agents

The canonical, tool-agnostic instructions for anything changing this codebase —
Claude Code, Codex, Antigravity, Grok, or a human. `README.md` has the project
map and `docs/ARCHITECTURE.md` the diagrams. **`STATUS.md` is what is happening
right now: read it before you start, update it before you stop.**

**The numbered rules are the non-negotiable part of this file.** Each one exists
because of a real production bug; violating one reintroduces that bug. Over 170
code comments cite them by number, so **the numbers are permanent — never
renumber, only append.** They run in numeric order, grouped by subsystem:

| Rules | Subsystem |
| --- | --- |
| 1–14 | The product pipelines (bookmarks + quote cards) |
| 15–28 | The Secretary (Abigail) |
| 29 | The quote-card format |
| 30–34, 58 | The Video pipeline |
| 35–41b | The Colony |
| 42–49 | The project wallet |
| 50–54 | Abigail and the teams |
| 55–57 | Cancelling a run |
| 59–64 | The Real World (nuclei) |
| 65–68 | The Bahá'í Workforce on the Real World map |

## Working norms

Sheraj uses several AI coding tools on this repo. These norms apply to all of
them equally — don't assume your own tool's defaults.

- **This file is the single source of truth.** `CLAUDE.md` exists only because
  Claude Code looks for that filename; it imports this file (`@AGENTS.md`) and
  adds nothing. Edit **AGENTS.md**, never CLAUDE.md, or the two drift.
- **Read `STATUS.md` and `git status` / `git log -5` before anything
  nontrivial.** Another tool may have left uncommitted work in the tree. If you
  find substantial changes you didn't make, read them before adding more —
  don't overwrite or "clean up" work in progress.
- **Update `STATUS.md` when you finish a nontrivial chunk**: correct the
  Snapshot, prepend one Activity Log entry (date, tool/model, what changed and
  why, what's left). Point at file paths; git already has the diffs.
- **Only commit when Sheraj explicitly asks.** He reviews changes himself.
- **Sheraj is non-technical.** Dashboard-visible behaviour is the deliverable,
  and errors must surface where he'll see them (Activity Log, chat reply,
  visible UI state) — never fail silently. Canva autofill once failed silently
  for weeks. Report back in plain language.
- **Verify, don't trust a self-report** — your own or a dispatched agent's.
  Re-run the check: import the module, grep for the string that should be gone,
  read the whole `git diff`.

## Commands and verification

```bash
cd dashboard && npm run dev                # UI on :5173
cd dashboard && npx tsc --noEmit           # typecheck (no JS test suite exists)
python -c "import agents.api"              # fast backend sanity check
python scripts/test_colony.py              # Colony tab: 135 checks
python scripts/test_secretary_colony.py    # Abigail <-> the teams: 92 checks
python scripts/test_job_cancel.py          # Cancelling a run: 24 checks
python scripts/test_wallet.py              # Wallet: 90 checks, no network/keys
python scripts/test_video_pipeline.py      # Video pipeline: 288 checks
python scripts/test_nuclei.py              # Real World (nuclei): 257 checks
```

All of the suites above are offline and free. Check counts live **here only** —
sections below name their suite without a number, so the two can't drift apart.

There is no formal test framework: `scripts/test_*.py` are runnable checks.
Test logic offline with mocks first, then verify live against the real SQLite DB
(`agents.state.get_all_products()`) and real LLM calls (Ollama local, xAI Grok).
FastAPI's `TestClient` exercises endpoints without starting a server.

**The backend already runs as a Scheduled Task ("bahAI Secretary API") that
auto-starts at Windows logon — never start it with `python agents/api.py` or
`python -m agents.api`.** That file's `__main__` block binds `0.0.0.0` with
`reload=True`, and a second instance alongside the managed one leaves two
processes on :8765 (Windows lets a wildcard bind and a loopback bind coexist).
That happened for real and looked like "WhatsApp stopped responding" while the
dashboard kept working. To pick up a code change: kill whatever holds :8765
(`netstat -ano | grep 8765`; `--reload`'s WatchFiles also spawns a child PID
that survives killing the parent), then either
`Start-ScheduledTask -TaskName "bahAI Secretary API"` or
`python -m uvicorn agents.api:app --host 127.0.0.1 --port 8765` (no `--reload`).
Killing it mid-session is fine — the task only re-triggers at the next logon.
The Cloudflare Tunnel auto-starts the same way ("bahAI Secretary Tunnel"). Both
run `scripts/start_secretary_server.ps1` / `start_secretary_tunnel.ps1` and log
to `logs/*.out.log` / `*.err.log` (gitignored), since after a real reboot there
is no console to read — check `logs/` before assuming a code fault.

## The system in one page

Three product pipelines, all entered from `agents/api.py`, all running long work
through the same background job store (`_start_job` + `/pipeline/status/{job_id}`,
polled by the dashboard):

- **Bookmarks** (`_run_full_pipeline`) — sold on Etsy. Create task → Librarian
  retrieves citations → Artist builds the prompt and generates the image (xAI) →
  `_pipeline_write_approve_sync` (consultation → Scribe writes → Reviewer scores
  → mechanical-edit revision loop) → save product → Compositor renders
  front/back PNGs. A Canva autofill step exists but is **off by default and off
  in practice** (`CANVA_AUTOFILL_ENABLED`, unset): 0 of 10 attempts ever
  succeeded, so it was parked behind a switch rather than left to fail in every
  run. Don't describe it as part of the working pipeline.
- **Quote cards** (`_run_card_pipeline`) — given away, never sold. Librarian →
  Artist (card brief) → consultation with `product="quote_card"` → optional
  translation (`translator.py`, Grok path) → `card_compositor.render_quote_card`
  (3.5×2in, multi-script) → `reviewer.score_quote_card` (which sees the RENDERED
  front face) → requote/repaint loop driven by the review's machine-readable
  `action` field.
- **Video** (`video_pipeline.py`) — a scene or story becomes many simple 3–4s
  shots that assemble into one video. See rules 30–34, 58.

Products carry `product_type`; bookmark-only endpoints reject cards via
`_require_bookmark`. Around the pipelines sit the **Colony** (the workforce as
an organisation — rules 35–41b), the **Secretary** (Sheraj's personal
assistant, rules 15–28), the **wallet** (rules 42–49), and Abigail's bridge into
the teams (rules 50–54).

State: `workforce.db` holds everything about the work (products, task_runs,
spend, video, colony). `private/` holds everything personal and is git-ignored
(rule 15). Generated files live in `outputs/`; databases store paths, never
binaries.

## Rules 1–14 — the product pipelines

1. **Qwen has tight context.** Anything routed to Ollama (`router.py`:
   everything NOT in `GROK_TASK_TYPES`) must get lean prompts. Long prompts made
   Qwen burn its whole token budget thinking and return `{`.
2. **`bookmark_quote` is locked.** `apply_edits` rejects edits to it and must
   report them as `rejected_locked`, never silently drop them.
3. **Revision is forward-chaining.** The loop revises `cur_listing` with
   `cur_review` (the latest), tracks `best_*` separately, adopts ties (newer
   listing wins), and only counts strict regressions toward the 2-strike stall.
4. **Every revision path must end in `_sanitize_claims`.** It deterministically
   strips false claims (handcrafted, exact motif counts). LLM compliance is
   never trusted for honesty-critical text.
5. **Reviewer JSON can truncate at the token ceiling.** `_parse_review` tracks
   whether `_repair_truncated_json` fired and drops the last `edits` element if
   so. Keep `edits` early in the schema field order.
6. **Consultation scripture stays hand-curated.** `CONSULTATION_SCRIPTURE` in
   `consultation.py` maps each consultation moment to one short cited excerpt
   (≤40 words). No vector DB for this, per explicit owner decision.
7. **The consultation's round-2 decision is binding.** `reviewer.score()`
   receives `consultation_decision`; overrides must be named
   "REOPENING team decision: ..." — never silently contradicted.
8. **Translation disclaimers AND AI-artwork disclosures are code-appended,
   never LLM-written.** A card translation is always labelled
   AI-assisted/unofficial: fixed strings in `translator.LANGUAGES`, printed by
   the compositor and stored in metadata. Artwork provenance is disclosed the
   same way — `etsy.AI_ART_DISCLOSURE` appended in code to every published
   listing, `api.CARD_ART_DISCLOSURE` stored in every card's metadata, and the
   Scribe's `_HONESTY_RULES` forbid implying hand illustration. Same class as
   `_sanitize_claims`.
9. **A new card language ships only after a human-viewed render.** PIL draws
   missing glyphs as tofu without erroring, and unshaped Arabic renders as
   disjointed LTR letters — `card_compositor` shapes RTL per line with
   arabic-reshaper + python-bidi, and every font in `LANGUAGES.font_paths` was
   verified by eye. Adding a language = config entry + a viewed sample PNG.
10. **Product-specific consultation wording goes through `_PRODUCT_FRAMES`,
    never inline in the shared prompt bodies.** Two sub-invariants are
    machine-critical: the Librarian's round-turn
    VERDICT/VERIFIED QUOTE/SOURCE/REASONING block is parsed
    (`_parse_verdict_grounded` + the quote-extraction loop) and must stay exact,
    and the revision-consult decision JSON contract ({action, action_guidance,
    team_note} + the REOPENING literals) is executed. The dashboard renders the
    Librarian's block as a friendly verification card
    (`ConsultationTranscript.tsx`) — humanize the display there, never her
    output format. (The turns themselves were restyled to natural first-person
    sentences in 2026-07 at Sheraj's request: an owner decision, not drift.)
11. **Quote cards quote only the sources SELECTED for that run — default: Ruhi
    Book 1 alone.** (Widened from "Ruhi Book 1 only, ever" by owner decision
    2026-08-04; the discipline survives as tiered trust in
    `api._parse_card_sources` / `_card_retrieve` / `_resolve_pinned_quote_multi`.)
    - **Default unchanged:** no `sources` on the request = Ruhi Book 1 only, via
      `retrieve_ruhi_book1()` + the SHA-manifest gates, byte-for-byte the old
      behaviour.
    - **`lib:<slug>` (verified tier):** one of the ingested 7-text `bahai_texts`
      chunks. The printed text must be a boundary-honest verbatim span of an
      indexed chunk (`_find_verbatim_span` + `_span_boundary_ok` +
      `_assert_excerpt_of`): sentence-clean start (overlap chunking can open a
      chunk mid-sentence, and a fragment must never print as if complete),
      sentence-punctuation end, elision marks for any early stop.
      `quote_verified: true`.
    - **`web:<url>` (RISKY tier, explicitly opt-in):** prints as supplied, is
      NEVER grounded, and carries `quote_verified: false` +
      `quote_provenance: "web:<url>"` everywhere (the dashboard badges it; the
      code-owned per-tier strings in `consultation.py` never claim
      verification). Web-only runs require a pinned quote; requote never draws
      from the web.
    - **Never widen silently:** an empty result within the SELECTED sources
      raises — no fallback to unselected texts, the general index, or the
      Librarian's memory. She never free-associates a quote.
12. **The bookmark quote's GROUNDED verdict is deterministically re-checked.**
    `api._check_quote_grounding` (word-overlap against the retrieved citations,
    or `librarian.verify()` when retrieval was empty) gates `quote_grounded`
    before the quote is locked — never reintroduce trust in the consultation
    Librarian's self-report alone. Unverifiable demotes to ungrounded; the
    demotion is logged and appended to the transcript.
13. **The Etsy price is policy-set, never parsed from LLM prose.**
    `etsy.BOOKMARK_PRICE` (env `ETSY_BOOKMARK_PRICE`) is the only price source;
    the Scribe's `price_note` is a display-only suggestion.
14. **`log_run(passed_review=...)` moves agent trust — only pass it for JUDGED
    outcomes** (a review verdict, or a deterministic check like the translator's
    script check or the grounding check). Mechanical success ("the API call
    returned a file") stays `None`, or clean-run stats become an uptime metric.
    Trust has a real consequence: `/etsy/publish` requires Reviewer trust level
    ≥ 2 or an explicit `confirm=true`.

### The visual layout editor (both product types)

`agents/layout.py` is the single source of truth for every adjustable
presentation knob (font, text size/position, colour, gradient/vignette,
star/rule toggles) plus `sanitize()`, the boundary that clamps an untrusted
layout dict from the dashboard to safe ranges and drops unknown keys.
`compositor.render_bookmark_pair` / `card_compositor.render_quote_card` take an
optional `layout` (defaults reproduce the pre-editor render byte-for-byte) and
an optional `dest_stem` (the live preview reuses one file pair per product
instead of accumulating). Endpoints: `GET /products/{id}/layout`,
`POST .../layout/preview` (render, no save), `POST .../layout` (render +
persist to `front_image`/`back_image`/`layout_json`). UI: `LayoutEditor.tsx` in
the product drawer.

**Load-bearing invariant: the editor NEVER carries text.** The printed quote,
citation, translation and code-appended disclaimers are all read from the
product's stored data at render time inside `_render_product_faces` — a layout
request has no field that can reach them, so rules 2/8/9/11/12 hold by
construction, and a card's translation/disclaimer keep their script-verified
fonts (`_load_font`'s `override_paths` is only ever passed for the Latin English
quote + citation). Layout edits are mechanical (`log_run(..., passed_review=None)`,
rule 14) and never touch the review score.

**Manual `PATCH /products/{id}` (edit_product) is a deliberate owner override,
not a rule-2 violation.** The quote stays locked to every *agent/pipeline* path
(`apply_edits`); a human hand-edit is allowed but not free: `_sanitize_claims`
still runs on the edited marketing text, and changing `bookmark_quote` sets
`quote_verified=false` (the dashboard shows a "no longer verified" note) and
re-renders the printed face so the image can't silently disagree with the words.
Don't "restore" rule 2 by blocking it — the flag is the intended design.

**The roster's display names and avatars are dashboard-only.**
`dashboard/src/lib/utils.ts`'s `ROSTER` maps each stable backend id
(`librarian`, `artist`, `scribe`, `reviewer`, `steward`, `translator`,
`secretary`) to a friendly name (Ruth, Theo, Clara, Amos, Nora, Sofia, Abigail),
role label and avatar. Display layer only — the backend keys everything on the
lowercase ids (`state.AGENT_NAMES`, `log_run`, consultation turn labels), so
trust history and logs are unaffected. Avatars live in
`dashboard/public/roster/` (gitignored, private); `RosterAvatar` falls back to
an initial when absent.

## Rules 15–28 — the Secretary (Abigail)

Sheraj's personal assistant, chatting from the dashboard's Secretary tab on
Claude Sonnet (`router.call_claude` / `call_claude_agentic`, env
`ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL`). Phases 1–3 are live; Phase 4 (recovery
rhythms) is not started — read `docs/fable5-briefing-secretary.md` first, but
treat THIS file as authoritative where they differ (the spec predates the Google
Workspace expansion and the tool-calling migration).

Phase 2 adds `gcal.py` (Google OAuth, since expanded into the shared
`google_auth.py` — env `GOOGLE_CLIENT_ID`/`SECRET`), `badi_dates.py` and
`scheduler.py` (daemon thread from
`api.on_startup`, ticks 30s, all state in the private DB). Phase 3 adds
`whatsapp.py` (Meta Cloud API) so she lives on WhatsApp via her own number.
Google access spans Calendar/Gmail/Drive/Docs/Sheets/Slides
(`gmail.py`/`gdrive.py`/`gdocs.py`/`gsheets.py`/`gslides.py`).

15. **Everything personal lives in `private/` and only there.**
    `agents/secretary_store.py` is the ONLY module that touches personal data at
    rest (`private/secretary.db` + `private/memory/*.md`). Nothing personal ever
    goes in `workforce.db`, `log_run` summaries, job progress strings, stdout,
    or any committed file. `private/` is git-ignored; message content renders
    only inside the Secretary tab.
16. **Sonnet is hers alone.** `call_claude`/`call_claude_agentic` exist for the
    Secretary; never route Artist/Scribe/Reviewer/Librarian task types to them.
    Every underlying API call — including each round of a multi-round
    tool-calling turn — is metered as `claude_chat` through the same
    `record_api_spend` chokepoint.
17. **She is not a therapist** — her system prompt says so and steers crisis
    signals toward human help. Keep that block when editing her prompt.
18. **Every write is a real Claude tool call**, and the tool executor — not
    prompt compliance — enforces every ownership/approval gate before anything
    happens. See rule 22, which covers reads and writes alike.
19. **Holy Day and Feast dates come from `badi_dates.py` and only there** — a
    hand-curated table with per-entry sources (bahai.org + UHJ tables),
    2026–2028. Never let an LLM supply a Bahá'í date; outside that coverage she
    links the official calendar. Extending coverage = new verified entries.
20. **She owns only her own calendar** ("bahAI Secretary", created on first
    connect). `gcal.is_her_calendar()` gates writes: any edit/delete on another
    calendar becomes a `pending_actions` row requiring Sheraj's approval
    (dashboard buttons or "approve N" in chat — that path is regex + code, no
    LLM in the loop).
21. **Quiet hours are enforced in the scheduler** (default 22:30–07:30,
    `settings.quiet_hours`): held reminders deliver after the window ends; only
    `wake_me` reminders break through. Scheduler fires and failures surface as
    notifications in the dashboard Activity Log, titles only.
22. **Every action, read or write, is a real Claude tool call**
    (`router.call_claude_agentic` + `agents/secretary_tools.py`), migrated
    2026-07-07 off an earlier design where writes were custom `<event>` /
    `<sheet_append>` markup parsed out of her reply text by regex. That proved
    unreliable at the one thing it had to be reliable at: in a long session she
    would write a confident "Adding that now" with no markup behind it, and
    nothing happened. What the migration must keep:
    - Every ownership/approval gate (Calendar rule 20, Drive rule 24, Gmail rule
      25) lives inside its write tool's handler in
      `secretary_tools.make_executor`. The safety model is unchanged; only the
      trigger mechanism moved.
    - A write tool called twice with byte-identical arguments in one turn
      executes once (dedup guard), so a restated call never repeats the action.
    - Capped at 6 rounds per turn; a round hitting the cap is forced to answer
      in text (`tool_choice: "none"`), never left to loop.
    - The reply is EVERY round's text concatenated, never just the final
      round's — dropping a round's narration is a regression.
    - A reply narrating a commitment with no tool call behind it at all is still
      structurally possible; `secretary._finalize_reply`'s
      `_looks_like_uncommitted_action` heuristic catches that residual case and
      surfaces it as a visible error instead of silence.
23. **Google Workspace scopes come from one shared OAuth module**
    (`agents/google_auth.py`) — one consent screen, one
    `private/google_token.json`, covering Calendar/Gmail/Drive/Docs/Sheets/
    Slides. Full `calendar`/`drive`/`documents`/`spreadsheets`; Gmail is
    `gmail.readonly` + `gmail.send` only, never `gmail.modify`; Slides is
    `presentations.readonly` only — no Slides write functions exist. Every
    `g*.py` module imports `get_valid_token`/`_headers` from there rather than
    managing its own token.
24. **Drive has a sandbox too.** `gdrive.ensure_secretary_folder()` /
    `is_in_her_folder()` mirror `gcal.ensure_secretary_calendar()` /
    `is_her_calendar()` (rule 20): she creates Docs/Sheets/files freely only
    inside her own "bahAI Secretary" Drive folder; renaming, trashing, moving or
    editing anything outside it queues a `pending_actions` row, same approval
    path as a non-owned calendar edit.
25. **Gmail has no free tier at all.** There is no "her own inbox" to sandbox,
    so every `send_email` tool call becomes a `pending_actions` row of kind
    `gmail_send` unconditionally — the handler queues it and never sends.
    `gmail.send_message` is only ever called from
    `secretary.execute_pending_action` after Sheraj's explicit approval.
26. **The WhatsApp webhook (`POST /whatsapp/webhook`) is the one endpoint in
    this API meant to be reachable from the public internet** (via a Cloudflare
    Tunnel — the setup guide at `GET /whatsapp/setup` restricts the tunnel's
    ingress to that path alone, never the whole API).
    `whatsapp.verify_signature()` (HMAC-SHA256 over the raw body, keyed on
    `WHATSAPP_APP_SECRET`) is the ONLY authentication on it — no app secret
    configured means the check fails CLOSED, rejecting everything, rather than
    skipping verification. Never relax this or trust an unsigned payload.
27. **Only Sheraj's own WhatsApp number can COMMAND the Secretary.**
    `WHATSAPP_OWNER_NUMBER` + `whatsapp.is_owner()` gate the handler
    (`api._handle_whatsapp_message`) in three tiers (guest tier added by owner
    decision 2026-07-12): the owner reaches the full `secretary.chat()` (tools +
    memory); an ALLOWLISTED contact reaches `secretary.guest_chat()` — a
    structurally TOOL-LESS conversation (plain `call_claude`, never
    `call_claude_agentic`; no `read_all_memory_notes`, no personal context;
    history limited to that guest's own thread via `messages.sender`) so she can
    chat and take messages but can never act on Sheraj's systems or leak his
    data; anyone else never reaches ANY chat loop and gets a fixed canned reply.
    Sheraj sees guest threads in the Secretary tab, sender-labelled, plus a
    title-only notification. Never route a guest into `chat()` or add
    tools/memory to `guest_chat()`, and keep the owner thread's context
    `thread="owner"` (guest rows excluded).
28. **The WhatsApp allowlist is owner-controlled only, never LLM-writable.**
    `secretary_store.py`'s `contacts` table (`add_contact` /
    `set_contact_allowlisted` / `remove_contact`) is only ever touched from the
    dashboard's Trusted Contacts UI and its `/secretary/contacts*` endpoints —
    no tool exposes it to the model, unlike every other write in this file.
    `send_whatsapp` sends immediately only to the owner or an allowlisted
    contact (falling back to the pre-approved `WHATSAPP_UPDATE_TEMPLATE` if the
    24-hour free-form window per `whatsapp.within_24h_window()` has closed);
    anyone else queues as a `pending_actions` row of kind `whatsapp_send`, the
    same unified queue as rules 20/24/25.

## Rule 29 — the quote-card format

29. **The quote is ALWAYS on the front and NEVER on the back** (redesign
    2026-07-16, owner spec). The front is readability-first: Tahoma dark ink on
    a light wash of the heavily blurred artwork, thin gold border, and
    `card_compositor.MIN_QUOTE_PX` as a hard floor — a quote that can't fit at
    the readable minimum FAILS the render, never shrinks below it. The back is a
    reflection face (question + gentle call-to-action + ruled writing lines +
    share line); the share line and the per-language reflection DEFAULTS are
    code-owned fixed strings (same class as rule 8 — the quote-inspired
    question/action may be LLM-written via `api._card_reflection`, but never the
    share line or disclaimers). Translated runs render SEPARATE per-language
    card pairs, never English-front/translation-back: the variant card carries
    the translated quote + its code-appended disclaimer on ITS front, stored in
    `card_copy.variant_faces`. Tahoma is used only where it safely covers the
    script — languages with verified `font_paths` in `translator.LANGUAGES`
    (zh, ar) keep them, and rule 9 still applies to any new language.

## Rules 30–34, 58 — the Video pipeline

Turns a **scene, story, historical account or passage** into many simple 3–4
second shots that assemble into a coherent video. A bookmark or quote card can
be the SOURCE, but this is a general story-to-video tool, not a card-video tool
— keep that emphasis in any UI change. Verify with
`scripts/test_video_pipeline.py`.

**The governing design principle:** break a complex story into many simple shots
rather than asking a small video model for one complicated one. An 8GB-class
local model cannot resolve two simultaneous actions, a moving camera over a
moving crowd, or a location change mid-clip. Everything below follows from that.

Modules: `video_store.py` (persistence in `workforce.db` —
`video_projects`/`video_shots`/`video_assets`/`video_jobs`, paths only),
`video_director.py` (the LLM stages: story analysis → continuity bible →
per-BEAT shot planning, beat-by-beat so each prompt stays inside Qwen's context,
rule 1), `video_safety.py` (rule 30), `video_provider.py` (provider adapters,
capability detection, fallback selection), `video_pipeline.py` (orchestration:
`build_plan`, `generate_frames`, `generate_clips`, `generate_chained`,
`resume_state`), `video_assembly.py` (validation, ffmpeg assembly, export,
the finished-video shelf) and `videographer.py` (the ComfyUI HTTP client).

ComfyUI is **not part of this repo** — a separate portable install
(`C:\Users\Sheraj\ComfyUI_windows_portable`, its own Desktop shortcut) reached
over HTTP (`COMFYUI_URL`, default `http://127.0.0.1:8188`): `/prompt` → poll
`/history/{id}` → `/view`, with source images uploaded via `/upload/image`
rather than assuming shared filesystem access. `is_server_up()` gates every call
and raises `VideoGenerationError` telling the user to launch the shortcut,
rather than hanging. Local generation has no cloud cost so nothing meters it;
frame generation goes through `artist.generate_image` (xAI), which meters itself
at its own chokepoint.

Two invariants outside the numbered rules, both learned from real incidents:

- On the LTX graph, `LTXVScheduler`'s `terminal` is hardcoded to `0.1` and
  deliberately NOT exposed as a parameter — dropping it to `0.0` silently
  renders every frame pure black with no error (A/B confirmed 2026-08-12).
- **`state.init_db()` calls `init_video_db()` OUTSIDE its own `with _connect()`
  block.** Nested inside, SQLite refuses the second writer ("database is
  locked"), the video tables are silently skipped on a fresh database, and every
  video endpoint then fails with "no such table". The suite has a fresh-DB
  regression test for exactly this. `init_colony_db()` is called the same way,
  for the same reason.

30. **Manifestations of God are never depicted visually.** `video_safety.py`
    enforces this DETERMINISTICALLY in code — same class as `_sanitize_claims`
    (rule 4) and the code-appended disclaimers (rule 8) — because prompt
    compliance is never trusted for a reverence-critical guarantee. It is
    deliberately ASYMMETRIC: the VISUAL fields (`subject`, `primary_action`,
    `first/last_frame_prompt`, `motion_prompt`) are rewritten to an indirect
    treatment (reactions, environment, objects, an empty threshold), while
    `narration` is left ALONE — naming Them with reverence in narration is the
    intended outcome, not a violation. Every rewrite is reported, never silent.
    `'Abdu'l-Bahá` and Shoghi Effendi are NOT Manifestations and may be depicted
    normally. **Matching normalises text first** (`_normalize`: strip diacritics,
    remove apostrophe-likes) — the first version matched a raw ASCII `'` and so
    missed `Bahá’u’lláh` with the typographic apostrophe this repo and every
    real source actually use, while its own leak-check shared the broken matcher
    and reported all-clear. Never "simplify" that back to a plain regex over raw
    text.
30b. **Detail and complexity are SEPARATE axes — maximise one, minimise the
    other.** (Owner ask, 2026-08-12.) Prompts must be *extremely* detailed
    (materials, texture, light source/direction/colour temperature, atmosphere,
    depth, optics) while the shot stays visually simple: one subject, one
    action, one camera behaviour. LTX-Video's docs warn that short prompts
    "suffer greatly", and **neither encoder truncates** —
    `comfy/text_encoders/lt.py` and `wan.py` both set `max_length=99999999`
    (Wan pads to a 512-token minimum), so length is free quality. Do not undo:
    `build_frame_prompt`/`build_motion_prompt` emit flowing PROSE, not
    comma-separated tags (these models are trained on natural-language captions;
    tag soup reads as a short prompt however many tags it has), and
    `complexity_score` measures ONLY narrative/camera load, never verbosity —
    `_HANDS` matches hand-INTENSIVE work, not any mention of hands ("his
    weathered hands rest on the pommel" is texture); `_CROWD` is waived by
    `_CROWD_OK` for a distant/still/blurred crowd; and the action word-cap
    applies to `primary_action` alone, never to the intentionally verbose
    frame/motion prompts. Wire detail into `complexity_score` and every rich
    shot gets split for nothing.
31. **Shots are 3–4 seconds, enforced in code.** `clamp_duration` corrects
    whatever the model returns; `split_complex_shots` measures
    `complexity_score` (sequential actions, crowds, hand-work, complex camera,
    >2 characters) and mechanically splits anything over `COMPLEXITY_LIMIT`. A
    model asked to "keep it simple" regularly does not — the code is the
    guarantee, and it reports every split it makes.
32. **Never claim a provider capability it does not have.**
    `videographer.FLF_SUPPORT` is hardcoded from an EMPIRICAL probe, not from
    ComfyUI's node list, because both would lie: `WanFirstLastFrameToVideo`
    exists, submits, and completes in 21s against Wan 2.2 TI2V-5B — and returns
    corrupted garbage (probe 2026-08-12: mean abs difference from the
    conditioning image 97/255, vs ~23 for a working run; visually a smeared
    colour field). Node presence AND a clean exit both reported "supported";
    only inspecting pixels revealed the truth. `resolve_strategy` therefore
    falls back (native FLF → first-frame i2v → chain-extract → text-only) and
    returns the reason, which the UI SHOWS. Re-probe before flipping any of it.
    The mock provider labels every asset `is_mock` end to end and must never be
    presentable as real generation.
33. **Continuity comes from locked descriptions, not from hope.** The continuity
    bible assigns stable ids; `build_frame_prompt` assembles every frame prompt
    from the shot PLUS the locked descriptions of the ids it references, in
    code, so a regenerated frame cannot quietly lose them. A `continuous` shot
    reuses the previous shot's last frame as its own first frame (literally the
    same file — position, costume, lighting and screen direction cannot drift);
    an `editorial_cut` generates a new first frame. Locking a field stops
    REGENERATION from changing it, never the owner: a human edit passes
    `force_locked=True`, the same spirit as the manual `PATCH /products/{id}`
    override.
33a. **Chained generation is the DEFAULT way to render a video, because
    independent generation looks like a slideshow.** (Owner report, 2026-08-12:
    "kind of like a trippy slide show".) `generate_frames` + `generate_clips`
    render every shot from its own text prompt, so each invents its own version
    of the character and place. `generate_chained()` threads real output
    forward: shot 1's clip is rendered, its ACTUAL final frame extracted with
    `videographer.extract_last_frame`, and that file becomes shot 2's first
    frame — each clip starts on the pixels the previous one ended on. What holds
    it together:
    - A `continuous` shot REUSES the extracted frame directly. An
      `editorial_cut` must generate a new frame (the angle changes on purpose)
      but is anchored by `video_director.observe_frame()` — a vision read of
      what the previous clip *actually* showed, folded in via
      `build_continuation_prompt` — so identity carries across the cut.
      `adapt=False` skips that paid call; the chain still works, with weaker
      carry-over at cuts.
    - `ClipSpec.image_strength=1.0` for chained runs. LTX's template default of
      0.15 means "loosely inspired by this image", which is wrong when the image
      IS the previous clip's last frame. Non-chained runs keep 0.15.
    - **PyAV is a hard dependency of chaining**, not an optional extra: without
      it every link silently degrades to an independent shot — exactly the
      problem being fixed. `generate_chained` preflights the import and refuses
      to start rather than producing a slideshow and calling it a chain.
    - On a shot failure the carry frame is CLEARED, so the next shot restarts
      the chain honestly instead of appearing to continue from a clip that
      doesn't exist.
33b. **Planning must survive a failed beat, and its calls need long timeouts.**
    Shot planning makes one LLM call PER STORY BEAT, each asking for hundreds of
    words, so a 7-beat plan is 10–15 minutes on local Qwen — and the router's
    120s default produced a real mid-plan read timeout that discarded six
    completed beats (2026-08-12). Three consequences, none to be undone:
    `call_llm` takes a `timeout` override and the Director passes
    `ANALYSIS/BIBLE/SHOT_TIMEOUT_S`; `plan_beat_shots` retries once with a
    LEANER prompt (a repeat of the same oversized request just fails the same
    way); and `build_plan` catches a beat failure, inserts a placeholder shot
    flagged `needs_replanning`, and CONTINUES — the project ends
    `planned_with_gaps` with the reason in `notes`, never zero shots.
    `VIDEO_DIRECTOR_MODEL=grok` opts planning onto the paid API when speed
    matters more than cost; the default stays local and free.
33c. **The Video tab's UI state is persisted** (`settings.getVideoUi` /
    `patchVideoUi`, the same localStorage pattern as the Pipeline tab's active
    job). Switching tabs unmounts the panel, and a multi-stage pipeline that
    forgets the open project, the sub-tab and the running job every time the
    user looks elsewhere is unusable. `useVideoJob` reattaches to a persisted
    job id and treats a 404 as "the API restarted, the job is gone" rather than
    polling a dead id for ever.
33d. **A shot's movement description is repaired in CODE, never trusted from the
    planner.** (`video_director.repair_motion`, run by `build_plan` and exposed
    as `POST /video/projects/{id}/repair-motion`.) Measured on real finished
    projects 2026-08-13: of 17 shots, **8 told the video model that nothing
    moves**, 4 repeated the previous shot's motion text verbatim, and 6
    `continuous` shots declared a framing the frame they reuse cannot have. All
    three produce the symptom the owner called "trippy" — handed a real first
    frame at `image_strength=1.0` with no movement to render, the model holds
    the composition and dissolves the texture. So, in code:
    - Stillness clauses are stripped CLAUSE-BY-CLAUSE, never word-by-word
      (deleting "no" inverts the meaning instead of removing it), and background
      stillness is re-added by the code-owned tail so it can never contradict
      the subject's own action.
    - A motion prompt duplicated from the previous shot is rebuilt from THIS
      shot's action, keeping any trailing AMBIENT sentence (one that doesn't
      mention the subject) so texture isn't thrown away with the error.
    - A `continuous` shot inherits the previous shot's `framing` and
      `camera_angle`: it reuses that clip's final frame, so a different declared
      setup is a claim the pixels cannot honour.
    Every repair is REPORTED and locked fields are left alone. Story-level
    repetition (the same action planned four times) is only ever WARNED about
    (`repeated_action_warnings`) — deciding an action was meant to happen once
    is a story judgement, not a mechanical one. Honest limit: an A/B over
    2 shots × 2 seeds moved the real-motion / morphing ratio 0.21 → 0.24, inside
    the noise. These defects are fixed because they are objectively wrong, not
    because a metric proved a large win.
33e. **A chained run refuses SYNCHRONOUSLY** (`chain_preflight`, called by the
    endpoint before `_start_job`, returning HTTP 400). Raised inside the job
    thread instead, the message reached the dashboard as a job error that
    `useVideoJob`'s `onDone` cleared on the same tick — the owner saw a click
    that appeared to do nothing and worked around it by generating the first
    frame by hand (2026-08-13). Two halves, both load-bearing: the preflight
    stays ahead of the job, and a FINISHED job's error stays on screen until the
    next job starts. The chain has always been able to start cold, from no
    frames at all — `test_cold_start_chain` pins that so the two failure modes
    are never confused again.
33f. **Pacing is a planning MODE, and cut count — not shot count — is the pacing
    a viewer feels.** (`direction.pacing`: `standard` | `cinematic`; owner ask
    2026-08-13 for "fewer, longer, non-overlapping beats".) Chained `continuous`
    shots are ONE unbroken take, each clip starting on the previous clip's real
    final frame — four continuous 4s shots read as a single 16-second take, not
    four cuts. Measured before this existed: "Adam's New Day" cut every 4.7s
    across 122s. `cinematic` does three deterministic things:
    - `beat_shot_budget` treats the clock's share as a CEILING and caps it at
      the beat's `distinct_moments` (a new analysis field). The video comes in
      SHORTER than the target rather than padded with restatements, and says so
      in the notes. Do not "fix" that by refilling to the target.
    - `dedupe_shots` REMOVES a moment an earlier nearby shot already covers
      (window 4, same similarity measure as `repeated_action_warnings`); the
      survivor keeps the better text at the EARLIER position. Standard pacing
      only warns — deleting a shot the owner may have meant is a story decision,
      and cinematic is where that decision is explicit.
    - `enforce_cut_policy` cuts only at beat boundaries and at real changes of
      place or time. A location or time-of-day change ALWAYS cuts even inside a
      beat: a `continuous` shot reuses the previous frame, so claiming
      continuity across a real change is a lie the pixels cannot tell.
    `MAX_SHOT_SECONDS` is NOT raised for this — rule 31's ceiling is a hardware
    fact, not a preference; cinematic pins shots to the top of the existing
    window. `POST .../repair-motion {"recut": true}` applies the policy to an
    already-planned project (26 cuts → 10 on a real 35-shot project) and never
    deletes a shot, because a dropped shot may already have a rendered clip.
34. **Everything is resumable at shot granularity.** Assets are written the
    moment they exist and a re-run skips shots that already have them, so
    closing the app costs at most the shot in flight. `mark_interrupted_jobs()`
    runs at startup to flip jobs a killed process left as `running` — that is
    what makes "Resume" truthful rather than a guess. Clip generation is
    SEQUENTIAL on purpose: two clips in flight on an 8GB card is an
    out-of-memory error, not throughput.
58. **A finished video reaches the Products shelf DERIVED, never copied.**
    (Owner ask 2026-08-15.) `video_assembly.list_finished()` +
    `GET /video/finished` build each entry from the video tables on every read;
    the Products tab merges them into its grid. A video is deliberately NOT a
    `products` row: that would double-count it in the Steward's ledger, hand the
    print sheet / layout editor / Etsy publish a `product_type` they cannot act
    on, and drift the moment a project is re-assembled or deleted. Four
    guarantees, pinned by `test_finished_shelf`:
    - **Only a file that exists is shelved** — one whose mp4 was deleted drops
      off rather than showing a player that cannot play (`include_missing=True`
      reports the gap for callers that want it).
    - **The length is MEASURED, not planned** (122.5s of plan measured 110.4s on
      a real project). ffprobe costs seconds and this list is polled, so it runs
      ONCE per file and is remembered on the export record against the exact
      path it measured; a re-assembly writes a new filename, so a stored value
      can never describe a different video. Unmeasurable falls back to the plan
      flagged `duration_measured: false` (the UI prefixes "~") — a fallback is
      never stored as a measurement.
    - **A mock-built draft says so** (rule 32), from the clip assets' own
      `is_mock` labels.
    - **Videos drop out while a review-result filter is active**, and the bar
      says why: they are reviewed shot by shot, not scored out of 10 (rules
      14/35). The shelf otherwise filters by kind, search and sort; the chosen
      view persists, the typed SEARCH does not — returning to a nearly-empty
      shelf because of a forgotten query is the failure the bar prevents.

## Rules 35–41b — the Colony

The dashboard tab that treats the workforce as an organisation: performance,
handoffs, per-agent chat and settings, team goals, team consultation and the
approval queue. Replaced the Trust tab 2026-08-13. `agents/colony.py` owns the
teams, per-agent settings, goals, chat history and the queue (its own tables in
`workforce.db`); `colony_tools.py` holds the toolsets and the gate,
`colony_chat.py` the chat and team consultation, and
`dashboard/src/components/colony/` the UI. Verify with `scripts/test_colony.py`.

35. **The handoff graph is DERIVED from `task_runs`, never stored.** Within one
    `task_id`, consecutive rows in id order are consecutive steps, so
    agent(n) → agent(n+1) is a real handoff. Every pipeline already writes
    `task_runs`, so the lines on screen are a record of what happened rather
    than a drawing of what was intended — do not add a parallel "edges" table to
    make the picture tidier. A judged run and a mechanical one stay
    distinguishable all the way to the UI (`judged` flag, rule 14): showing a
    render step as "passed" would turn clean-run stats into an uptime metric.
36. **The workforce agents get their OWN tool-calling loop, never Claude's.**
    `router.call_llm_agentic` routes by `task_type` exactly like `call_llm`
    (Grok for the Artist/Reviewer, local Ollama for the rest) and exists
    precisely because rule 16 reserves Claude for the Secretary. It mirrors
    `call_claude_agentic`'s contract — same executor signature, hard round cap,
    forced final round, every round metered — with two provider-forced
    differences: OpenAI-style tool schemas, and `max_rounds` defaulting to 4
    rather than 6 (each round appends another tool result, and local Qwen's
    context is why rule 1 exists). `_normalize_tool_calls` is load-bearing: Grok
    returns tool arguments as a JSON STRING and Ollama as a dict, so dropping it
    silently breaks exactly one provider.
37. **Free reads run; anything paid or product-changing QUEUES.** The gate lives
    in `colony_tools.make_executor`, not in any prompt — an agent told "generate
    it now" queues anyway (verified live 2026-08-13: no file in `outputs/`,
    `image_gen` spend unmoved). `GATED_KINDS` is data so the suite can assert
    the whole paid surface is covered, and no gated kind may also have an entry
    in `IMMEDIATE_HANDLERS` — a gate with a bypass is not a gate. Approval is
    the only execution path (`colony_tools.run_approved_action`, called from the
    endpoint only, same shape as `secretary.execute_pending_action`). A queued
    action is appended to the reply in CODE, because the model may claim success
    without mentioning that nothing ran.
38. **The Secretary is a node in the Colony but never a chat in it.** (She
    reaches the teams a different way — rules 50–52.) `colony.NO_COLONY_CHAT`
    covers her and the instruments. Routing her here would drop her from Claude
    to Qwen/Grok (rule 16) and write her turns into `workforce.db` instead of
    her private store (rule 15); her node opens her own tab instead. The
    instruments (`compositor`, `consultation`) are real rows in `task_runs` —
    they carry the handoff graph — but are not people: no avatar, no chat, no
    settings.
39. **A team goal is injected in exactly ONE place, capped, and fails open.**
    `system_prompt_builder._goal_steer` appends it to every agent prompt in the
    codebase, so a goal reaches pipeline runs and chat alike and can never apply
    in one path but be forgotten in another (which is also why
    `colony_chat._agent_context` must NOT repeat it). It is one line, hard-capped
    at `colony.GOAL_NOTE_MAX_CHARS`, and a missing table or unreadable DB
    degrades to "no goal" rather than breaking a run — steering is an
    enhancement, not a dependency. Goal PROGRESS is counted from real finished
    products against a baseline taken when the goal was set, never self-reported
    (principle 2).
40. **Launching a goal reuses the real pipeline entry points**, so a goal-started
    run is indistinguishable from a hand-started one and every gate applies
    unchanged. The Film Crew is deliberately the exception: it CREATES a video
    project and stops, because video planning is reviewed before any clip is
    rendered (rules 31/33) and a one-line goal must not skip that look.
41. **An action's outcome is read from the provider's REAL return shape.**
    Caught live: `run_approved_action` read `result["translation"]` when
    `translator.translate_quote` returns `text`, so an approved translation
    reported success with nothing in it. The suite now stubs these with the
    actual documented shapes (`text`/`disclaimer_en` for the translator,
    `image_url` for the artist) so a wrong key fails there, not in front of
    Sheraj. A translation surfaced in chat still carries its code-owned
    AI-assisted label (rule 8).
41a. **Per-agent model choice (`agents/models.py`, owner ask 2026-08-13) — the
    provider boundary is enforced in CODE, not by the dropdown.**
    `validate_choice` runs in the settings endpoint BEFORE storage: a workforce
    agent can never be saved onto Claude and Abigail can never be saved off it
    (rule 16 made structural rather than conventional). Also:
    - **No stored choice = today's routing, byte-for-byte.** `resolve()` with no
      override returns what task-type routing would have picked, so the feature
      is inert until used. Verified by test, not by inspection.
    - **`agent=` is passed EXPLICITLY at every call site**, never reverse-derived
      from the task type, because task types are shared: `creative_writing` is
      both the Artist and the Translator, `scribe` is both the Scribe and the
      X-post writer. The one exception is consultation's `"plan"` synthesis
      call, which belongs to the whole team.
    - **An unreachable provider is NOT evidence a model is gone.**
      `_is_known_missing` only reports absence when the provider list was
      fetched SUCCESSFULLY and the id was not in it; otherwise the stored id is
      trusted. Without that, a briefly-stopped Ollama would silently move every
      local agent onto a different model. A genuine fallback is always REPORTED.
    - **Model lists are discovered, never hardcoded** — and filtered:
      `nomic-embed-text` backs `get_embedding` and the citation index, and
      `grok-imagine-*` are image/video endpoints that would 404 a chat call.
    - **Paid means "not local".** Testing `provider == XAI` alone labelled
      Abigail's Claude default as FREE — a lie about money in the one place used
      to check it. Vision (`call_grok_vision`) and image generation are separate
      paid paths that a local model choice does NOT make free; the UI says so
      for the Reviewer and Artist.
41b. **A body's FINISH on the Colony map is judged work only, and "not scored
    yet" must never look like "scored badly".** (Owner ask 2026-08-16: a
    well-performing agent's sphere should be shiny and glimmering, a poor one
    covered in dross and scratches.) `dashboard/src/components/colony/finish.ts`
    computes it and `Sphere.tsx` draws it, under rules 14/35: only JUDGED runs
    are evidence, so instruments (`compositor`, `consultation`) get NO finish at
    all — they stay flat — and an agent with `total_runs === 0` renders PLAIN,
    never dull. Polishing a body with mechanical success would turn the map into
    the uptime metric rule 14 exists to prevent.
    - **`trust_score` is not usable raw here**: it defaults to 50.0 with zero
      runs, so reading it directly would paint every new agent half-tarnished.
      The finish is computed from `clean_runs`/`total_runs` with `total_runs`
      gating it, plus a `consecutive_failures` penalty so a current slump shows
      before the lifetime average catches up.
    - **A thin record is pulled toward plain** (`CONFIDENCE_RUNS`), because one
      reviewed run is an anecdote and a first failure should not read as a
      verdict. It softens the PICTURE only — the exact counts are on the hover
      card and in the drawer, and every state is stated in WORDS there too,
      since the texture is ~15px on screen and cannot be the only carrier.
    - **Marks are deterministic per body and revealed in order** from a fixed
      set, so worsening work ADDS dross where dross already is. Grime that
      reshuffled every render would be the same bug the deterministic layout in
      `layout.ts` exists to avoid.
    - A team core is finished from its members' judged work ADDED UP (derived,
      never stored — rule 35), excluding its instruments, so a core can never
      read brighter than the people in it.

## Rules 42–49 — the project wallet (`agents/wallet.py`, Nora's domain)

A cross-chain wallet that receives giving, holds a treasury for the PeaceAntz
DAO idea, shows holdings in the Steward's report, and pays real expenses — with
Nora able to send within a hard cap (owner ask 2026-08-14). The irreversibility
risk was put to Sheraj plainly first and he chose the most capable option at
every step, so these rules exist to make that choice as safe as it honestly can
be. Verify with `scripts/test_wallet.py` (never touches a network or a key).

**This is the one part of the repo where a mistake cannot be undone.** Every
other failure can be re-run, re-scored or deleted. Weight changes accordingly.

42. **The destination allowlist is OWNER-ONLY and is the control that survives a
    prompt injection.** Exactly rule 28's discipline: no tool in `colony_tools`
    writes `wallet_allowlist`, and the suite asserts that no tool name contains
    "allowlist" and that none can create a wallet or read a key. A fully
    compromised Nora can still only move money to addresses Sheraj typed in
    himself. `bypass_limits=True` (for an owner-APPROVED queued payment) skips
    the caps but NEVER the allowlist or the token-contract check.
43. **Caps are computed from the on-chain ledger, never from the model.**
    `check_limits` reads `spent_today_usdc()` out of `wallet_txs` (excluding
    failed sends). Three tiers, all decided in code: at or under
    `WALLET_AUTO_SEND_USDC` Nora sends directly; above that up to
    `WALLET_MAX_PER_TX_USDC` it queues in the existing `colony_actions` queue;
    over that, or over `WALLET_DAILY_CAP_USDC`, it is refused. `wallet_send` is
    deliberately NOT in `GATED_KINDS` — that would turn every payment into an
    approval and remove the autonomy Sheraj asked for; it has its own tiered
    gate in `_h_wallet_send` and lives in `MONEY_KINDS` instead.
44. **The agent may only ever send USDC.** A stablecoin makes a dollar cap mean
    what it says with no price feed to go stale or be manipulated. There is NO
    native-token send at all — not for the agent and not for the owner;
    `send_usdc` is the only spending path in the module, and the native balance
    exists solely to pay gas. (Consequence learned in practice: a wallet funded
    only with test ETH cannot transfer yet — testnet USDC has to be obtained
    separately before the send path can be exercised.) Only the Steward has a
    money tool; the suite asserts no other agent does, and that a non-Steward
    calling `wallet_send` is refused by the executor's tool-membership check.
45. **Token contracts are verified ON-CHAIN before every transfer.**
    `verify_token()` calls `symbol()`/`decimals()` and refuses unless it is
    really USDC with 6 decimals. The addresses in `CHAINS` were checked live
    against each RPC on 2026-08-14 — but a hardcoded token address is exactly
    the thing never to trust from memory, because a wrong one means transferring
    to something that is not the token and the funds are gone with no error.
46. **Mainnet is opt-in** (`WALLET_ALLOW_MAINNET=true`). Default off, so only
    testnets are selectable and the whole feature can be exercised for real with
    nothing at risk. `get_chain` refuses a disabled mainnet chain with an
    explanation rather than silently falling back to a testnet.
47. **Two wallets, and only one is reachable by an agent.** `hot` holds a small
    float and its key lives encrypted in `private/wallet/` (gitignored,
    `WALLET_PASSPHRASE` from .env). `treasury` is a list of WATCH-ONLY addresses
    Sheraj controls elsewhere — Nora reads them and has no key, so the DAO
    treasury has a home outside the LLM's blast radius. Never merge the two.
48. **An unreachable chain is reported as unreachable, never as zero.** A
    balance of 0 that actually means "the RPC was down" would make the Steward's
    report quietly wrong, which is the one thing the Steward exists not to be.
    Holds in `balances()`, in Nora's tool output, and in the UI.
49. **Signing is a HARD dependency, declared up front.** `eth-account` only (not
    full web3.py — reads are raw JSON-RPC, so the key-touching surface stays
    minimal). `sending_available()` reports its absence, a missing wallet, or a
    missing passphrase BEFORE anything is attempted — the same preflight
    discipline as PyAV in rule 33a. Never hand-roll the crypto.

## Rules 50–54 — Abigail and the teams (`agents/secretary_colony.py`)

Added 2026-08-14 so she can interact with all the teams: report what they did,
request jobs, and give them what they need to do the job well. Her tools live in
`secretary_tools.py` (`workforce_report`, `ask_agent`, `set_team_goal`,
`brief_agent`, `request_team_job`) and everything they do goes through the one
bridge module — she is the only agent holding personal data and the only one on
Claude, so this is a boundary crossing, not just another feature. Verify with
`scripts/test_secretary_colony.py`.

50. **Everything crossing between her world and the workforce goes through
    `secretary_colony.py`, and every string that crosses is checked in CODE.**
    `assert_shareable` refuses any email address or phone number, and any span
    of 12+ consecutive words copied verbatim out of her private memory notes,
    before it can reach `workforce.db` (rule 15). It is deliberately NARROW and
    says so: it cannot judge whether an ordinary sentence is personal — the
    prompt carries that instruction, and the length caps bound how much can
    cross at all. Two things are load-bearing about what it reads: memory notes
    ONLY, never her chat history or his task list, because he asks for work in
    the same words his tasks are written in, and a wider check would refuse the
    exact relay this feature exists to perform. A refusal comes back to her as
    an explanation she can act on, never as a tool failure.
51. **Talking is immediate; MAKING is approved.** Reading what the teams did,
    setting a goal, writing a brief and asking an agent a question happen at
    once — an assistant who needs a permission click to ask Ruth a question is
    not an assistant. `request_team_job` ALWAYS queues in her existing
    `pending_actions` queue (kind `workforce_job`) and starts nothing, because a
    run spends real money on artwork and review and saves a product. She has no
    tool that approves anything — approval stays Sheraj's, through the dashboard
    or by replying "approve", and the suite asserts no tool name contains
    "approve". Approval executes through `api.launch_team_pipeline`, the SAME
    entry point the dashboard's own buttons use (rule 40).
51b. **Many cards are ONE request, and the quotes are found BEFORE it queues.**
    `count` up to `MAX_CARDS_PER_RUN` (mirroring `api._CARD_BATCH_MAX`) runs the
    real hands-free batch. `find_quotes` calls the same `/ruhi-quotes` finder the
    dashboard's "Find quotes" button uses, so what is queued is already
    canonicalised through the resolvers the batch endpoint verifies with — an
    unfindable theme fails while it is still free, instead of 422-ing after
    Sheraj has approved it, and the approval names the exact quotes rather than
    asking him to approve a number. Fewer found than asked for is REPORTED and
    the run shrinks; it is never padded to hit the number.
52. **She is a caller into the workforce, never a stand-in for it.** `ask_agent`
    goes through `colony_chat.chat`, so the agent answers on its own model with
    its own tools and its own gate (rule 37) — Claude is never lent out (rule
    16). The turn is written into that agent's Colony history behind
    `RELAY_PREFIX`, so Sheraj can read exactly what was asked in his name. Her
    report is assembled from `task_runs`, products and goals only, and keeps
    judged runs distinguishable from mechanical ones all the way into the prose
    she speaks (rules 14/35) — calling a render step "passed" would be a false
    claim about quality.
52b. **A brief has to reach the work, not just the conversation.** Standing
    instructions are injected by `system_prompt_builder._instructions_steer` —
    the same single place the team goal is injected (rule 39) — so they apply to
    pipeline runs and chat alike, capped at `colony.INSTRUCTIONS_NOTE_MAX_CHARS`
    for local Qwen (rule 1) and failing open. Until this existed they only ever
    reached Colony chat, while the dashboard's own label promised "in the
    pipelines as well as in chat". `colony_chat._agent_context` must NOT repeat
    them, for the same reason it must not repeat the goal.
53. **A job records who started it, and the Pipeline tab adopts any running
    job.** `_start_job(..., started_by=)` is "sheraj" / "abigail" / "colony", and
    `ADOPTABLE_KINDS` in `PipelinePanel.tsx` takes over any live run of a kind it
    can display, labelling whose it is. Not cosmetic: the panel used to poll only
    the job id it had just created itself, so a run Abigail launched on approval
    progressed with the screen blank — it read as "nothing happened", and the
    same card was paid for twice (real, 2026-08-14). The Colony map shows the
    same fact from the same source: `colony.team_activity` derives each team's
    live work from the job store via `JOB_KIND_TEAM`, so a lit team always means
    a real running job and never a drawn decoration.
54. **Her claims about the approval queue are corrected from the record.** The
    queue is stated in her prompt ALWAYS, including when empty — omitting the
    section left her filling the gap from earlier turns. That alone wasn't
    enough: she still repeated a stale queue from her own previous reply, so
    `secretary._approval_ground_truth` compares every `#id` she names against
    `get_pending_actions()` and appends a code-authored correction when one is
    already resolved. Same class as `_ground_truth_confirmation` — a wrong queue
    invites Sheraj to approve the same work twice, the exact failure this whole
    area exists to prevent.

## Rules 55–57 — cancelling a run (`POST /pipeline/status/{job_id}/cancel`)

Added 2026-08-14 (owner ask: "a way to cancel the run if I want to start over"
that "clears out incomplete stuff and doesn't create issues with job numbers").
Verify with `scripts/test_job_cancel.py` (real worker threads, fake runners).

55. **Cancellation is COOPERATIVE, and `JobCancelled` derives from
    `BaseException`.** A Python thread cannot be killed from outside without
    leaving half-written files and held locks — precisely the mess "start over"
    is meant to avoid. Every pipeline already narrates itself through
    `progress(...)` between stages, so that callback (and `on_turn`, and the
    wake-up from a human-input pause) is the checkpoint. The BaseException
    inheritance is load-bearing, not style: this codebase is full of deliberate
    `except Exception` blocks that turn a failed stage into a survivable
    recorded error — `_run_card_batch` logs a failed card and moves to the next
    — and every one of them would swallow a cancellation and keep spending. The
    suite pins this with a runner that wraps its work in `except Exception`.
56. **A cancel stops the work; it never rewrites what the work already did.**
    `task_runs` rows stay (the handoff graph is derived from them, rule 35 —
    deleting them would falsify history), products already saved stay (a card
    finished before the cancel is finished and paid for), and files in
    `outputs/` stay (paid artwork, and a thread may still hold the handle). What
    IS closed out: the task row the run was in the middle of becomes `cancelled`
    (a task it had already completed stays completed), the pending-input
    rendezvous is cleared, and no result is recorded — so nothing half-made can
    be read off the job. A cancelled run is never reported as an error and never
    moves any agent's trust (rule 14).
57. **"cancelled" is a terminal status everywhere it matters.** It is in
    `_start_job`'s eviction set (otherwise cancelled jobs accumulate for ever),
    it drops out of `colony.team_activity` so the map stops showing the team as
    working, and the dashboard keeps polling until the job REALLY reports
    cancelled before unlocking the form — the button says "Stopping…", never
    "stopped", because the step in flight has to finish first. Job ids are
    random 8-char uuids, never a sequence, so a cancelled run can never collide
    with or renumber a later one.

## Rules 59–64 — the Real World (nuclei)

The Colony tab's other sky: Sheraj's nuclei and friends. Design in
`private/nuclei/`. Data in `private/nuclei.db`. Verify with
`scripts/test_nuclei.py`.

59. **Community data lives in `private/nuclei.db` via `nuclei_store.py` and only there.** Follows `secretary_store.py`: one git-ignored SQLite file. Nothing personal in `workforce.db`, `log_run` summaries, job progress strings, stdout, or any committed file — including seeds, fixtures, tests and screenshots. Tests build invented people in a temp directory and must refuse to open `private/nuclei.db`. `assert_test_db(path)` is the gate.

60. **Models may read a shareable snapshot; they may not read private detail, and they may not write.** The store holds only what a depicted friend could also see: chosen name, groupings, the two lists, gatherings (who / kind / when), study as sentences, ties, gifts by theme. No phone, email, address, or intimate-note column exists. An unprompted model write is a bug. `assert_shareable` still runs on anything that later crosses into `workforce.db`.

61. **Never score a person's spiritual condition.** Count actions, not interior states. The largest numbers point at the owner (his consistency), never at a ranking of friends. No grade, percentage, heat-map of receptiveness, or "spiritual growth" figure on any human being.

62. **One light per person. Each nucleus is its own point of light; distance to each is about that nucleus.** Default chairs are by `created_at` then `id` (a seventh table spirals out — never `i % 6` on top of the first). `pos_x`/`pos_y` is an owner override: a drag, or Arrange / Optimize locations, which is deterministic from size, shared people, recorded gatherings and ties — not a physics sim on every load. The owner is excluded from that affinity so his seat at every nucleus cannot collapse the map (rule 61). A person is placed once: a target on each grouping's own rings (core service close, connected far); the seat is the engagement-weighted average, boosted by recorded gatherings. They are not copied. A household lists its people (`household_members`); the same person may sit in a family and serve on an institution. A family-only person lives inside the household light until that family is opened (petals). Someone who already sits elsewhere stays there; opening the family draws a thread. Walking with someone is service for a particular grouping (a directed tie), not a seat at that table — the one who walks sits near the work and is not made a member. Leaving an institution ends that membership only. A neighbour arriving does not change anyone's seat; lights that would cover each other (dots or names) are then squeezed apart by the smallest step that keeps every label readable — never a physics shuffle on load, never a score. Friends who serve a local institution sit on an even ring outside its light, not in a pile on the core. Taking a friend off the map archives them (ends live memberships, keeps gatherings); the owner's light cannot be archived. Local institutions of the Faith (LSA, Regional Institute, Auxiliary Board, Area Teaching Committee, and any the owner names) sit in a column left of the Workforce. The owner adds and rarely archives them; worldwide bodies are not on this map. Nuclei are points of light (the Vision in that place), not a single central sphere and not Colony agent-bodies.

62b. **A name is a display label and nothing is keyed on it, so anyone and
    anything on this map can be renamed.** (Owner ask 2026-08-17.) People,
    nuclei and institutions all rename through the store's existing
    `update_actor` / `update_grouping` — including the owner's own light, which
    `ensure_owner` creates as "You" and has always described as renameable; it
    is only ARCHIVING him that is refused. A rename keeps the id, so every
    membership, facet, tie, gathering and chair survives it and no seat moves.
    An empty or whitespace-only name is refused at the store, and the endpoint
    returns 400 with the reason rather than saving a nameless light.
    `RenameField.tsx` is the shared control; the one thing it must keep doing is
    resetting its draft when the drawer is pointed at someone else, or you open
    a second person and find the first one's name in the box, one Enter away
    from renaming the wrong light. The workforce label on the map reads its name
    from the snapshot for the same reason — hardcoding it would let a rename
    leave the map and the drawer disagreeing.
63. **Grouping kinds, axes, facet kinds, tie kinds, activity kinds and institute units are DATA.** Adding a relationship type a year from now is an `INSERT`. Participation and service are two lists: service sits closer; core-activity kinds carry `is_core`. Do not hardcode the spreadsheet's six columns as an enum or a linear funnel.

64. **A workforce gift stores a `product_id` and a theme, never a friend's name, and `workforce.db` never learns who a gathering is for.** Job progress strings stay mechanical.

## Rules 65–68 — the Bahá'í Workforce on the Real World map

The workforce light on the Real World sky opens like a family opens: the agents
fan out, real people can be put on it, and a WhatsApp message can be written
from it to a friend or to a nucleus's group. Added 2026-08-17 (owner ask).
`agents/nuclei_bridge.py` is the one module where the two worlds touch;
`dashboard/src/components/colony/WorkforceDrawer.tsx` is the UI. Verify with
`scripts/test_nuclei.py`.

65. **The workforce is a real grouping, and never a table on the sky.** It is
    one row in `private/nuclei.db` of its own kind (`WORKFORCE_KIND` /
    `WORKFORCE_SLUG`), created by `init_db` and only there — `create_grouping`
    refuses a second one. Making it a grouping is what lets a person join it
    through the ordinary `add_membership` path, so every existing drawer, query
    and gate works unchanged. What it must never become is a table:
    `nuclei_layout.is_workforce_row` makes `assign_slots` skip it *before any
    chair index is spent* (otherwise a nucleus would slide onto the next chair
    for a light that is drawn somewhere else entirely), `optimize_layout`
    excludes it so Arrange can never move it, and it keeps the fixed
    `WORKFORCE` position it has always had.
    - **Its people get no second light** (rule 62). A workforce-only person has
      no seat on the sky at all and lives inside the workforce light until it is
      opened — exactly the family-only case. Someone who already gathers
      somewhere keeps that light and is reached by a thread when the workforce
      opens, never redrawn.
    - **The Digital World shows the same people, DERIVED.** `GET /colony`
      merges `nuclei_store.workforce_members()` in as `humans` on every read;
      they are never rows in `workforce.db` (rule 68), the same shape as the
      finished-video shelf (rule 58). They draw without trust, without a finish
      and without a team ring — polishing a person would be a score on a human
      being (rules 61 / 41b).
66. **A nucleus's WhatsApp GROUP is data; posting into one is not possible.**
    `grouping_channels` notes the group a table already talks in (a name and an
    invite link) so a message can be written *for* it. Two halves:
    - **Rule 60 is not relaxed.** No phone, email or address column exists on a
      channel and none may be added; a group invite link is what every member of
      that group can already see. One-to-one numbers stay in Abigail's
      `contacts` table and only there (rule 28). Only `whatsapp_group` is an
      accepted kind, and only a real `chat.whatsapp.com` / `wa.me` link is
      accepted — a link that is not one is refused rather than stored.
    - **The Cloud API has no group endpoint at all.** Meta's WhatsApp Cloud API
      — the whole basis of the Secretary — can only message one person. So a
      group draft ends in Copy plus a link that opens the group, and the UI SAYS
      SO on screen. Never add a Send button for a group: a button that silently
      does nothing is the Canva-autofill failure, and this one would look like a
      message went out to a whole community.
67. **A draft that names a friend runs on the LOCAL model, and cannot be routed
    off it.** `router.call_local` exists for exactly this: `call_llm` picks a
    provider from the task type *and* from the per-agent model saved in the
    Colony tab (rule 41a), so anything going through it can be moved onto a paid
    cloud API by a dropdown. That is right for product work and wrong for a
    prompt containing one of Sheraj's friends' names — the Real World lives in
    `private/` precisely so those names stay on this machine. `call_local` takes
    no `agent=` parameter, so there is nothing to override; the suite asserts
    the routed call is never reached. Claude is not an option here either
    (rule 16). Two things follow from the model being small and local:
    - **A draft is never sent as written.** It lands in an editable box and
      Sheraj sends it, so nothing here is autonomous.
    - **Invented specifics are FLAGGED in code**, because prompt compliance is
      not trusted for anything a friend will act on (rule 4's reasoning). Asked
      only to "invite them on Friday", Qwen wrote "around 7" on the very first
      real draft. `invented_specifics()` points at any time, day, date or
      amount the message asserts that was not supplied, and the drawer shows
      it. It does NOT edit — there is no safe mechanical rewrite of free
      prose — and the prompt asks for a `[time]` blank rather than a guess.
68. **Reads cross the bridge; writes do not.** No friend's name, no group name
    and no group link is ever written into `workforce.db`.
    `nuclei_bridge.assert_no_personal_leak` refuses a recipient, a number or a
    link riding along in anything handed to the workforce side — narrow on
    purpose and it says so, the same discipline as
    `secretary_colony.assert_shareable` (rule 50). The suite proves the
    invariant the strong way: it adds a person over HTTP, exercises `/colony`
    and `/nuclei/workforce`, then reads `workforce.db` as BYTES and requires
    the name to be absent. Two consequences:
    - **Sending reuses rule 28's tiers untouched.** `send_to_contact` sends
      directly only to the owner or an allowlisted contact; anyone else becomes
      a `pending_actions` row of kind `whatsapp_send`, the same unified queue.
      Sheraj clicking Send in the drawer is owner action, not a relaxation — no
      new path to an un-allowlisted number is opened.
    - **No model has a door to any of this.** Nothing in `colony_tools` or
      `secretary_tools` exposes the nuclei, the channels or the workforce
      roster, and the suite asserts no tool name reaches them — the same
      control that makes the wallet allowlist (rule 42) and the WhatsApp
      allowlist (rule 28) survive a prompt injection.
68b. **Switching worlds folds one sky into the workforce light and grows the
    other out of it** (owner ask 2026-08-17). Both skies use the same
    1440x720 space and both draw that light, so it is the only body that
    survives the swap and is therefore the anchor. Four things are
    load-bearing:
    - **The map is the FIRST child in both worlds.** The Real World's "add a
      nucleus" rows were moved BELOW its map for this: a control row above it
      sat the workforce light ~110px lower in one world than in the other, and
      the dot visibly jumped at the swap. Do not move them back on top.
    - **The Digital World is handed the Real World's SCREEN anchor**
      (`workforceScreenAnchor` — the light's fixed position pushed through that
      view's saved camera), because the Real World pans and zooms and the
      Digital World has no camera of its own. The hinge dot is drawn OUTSIDE
      the camera in both, so it is the same size however far the Real World is
      zoomed.
    - **The incoming sky opens itself one PAINTED frame after mount**
      (`mounted` + `requestAnimationFrame`), the same pattern as the family
      bloom. Setting the open state in the same tick as the mount gives the
      browser only the end state and the transition has nothing to run from.
    - **`WORLD_MORPH_MS` and the CSS duration are one timing**, and the fold is
      what sequences the swap — change `colony/layout.ts` and
      `.colony-world-morph` in `index.css` together, or the world flips before
      the fold has finished. The opacity fade is deliberately DELAYED behind
      the scale so the bodies are seen travelling rather than winking out, and
      the whole thing is stilled under `prefers-reduced-motion`.

## Gotchas

- **Windows console is cp1252**: use ASCII `->` not `→` in anything a script
  `print`s. The API and dashboard are UTF-8 safe.
- **`state.py` migrations run on every startup** (ALTER TABLE wrapped in
  try/except) — add new product columns there AND to the `update_product`
  allowlist. `secretary_store.py` follows the same pattern, but a NEW CONSTRAINT
  on an existing column needs its own migration: `CREATE TABLE IF NOT EXISTS` is
  a no-op on a table that already exists on disk, so editing the inline
  constraint does nothing for any DB created earlier. Add a
  `CREATE UNIQUE INDEX IF NOT EXISTS` alongside it — this bit us for real, when
  `contacts.phone` shipped without its UNIQUE applying and every inbound
  WhatsApp message crashed `record_inbound_contact`'s `ON CONFLICT(phone)`
  upsert before it reached the Secretary.
- **Ollama calls set `think: False`** — Qwen3's hybrid thinking would silently
  eat the output budget otherwise.
- **Card faces are composed at `_SS`× and downscaled on save**
  (`card_compositor._SS = 2`, for true anti-aliasing on small translated text).
  Every absolute pixel constant in that file — font sizes, floors, rule widths,
  insets — must be multiplied by `_SS`, or it renders at half its intended
  printed size. Shadow offsets scale with glyph size (~size/26) for the same
  reason: a fixed 2px shadow was a blocky halo on ~18px glyphs.
- **Don't identify freshly-created records by matching against a react-query
  cache** — the cache never contains a product created seconds ago. A card run's
  Spanish pair rendered and stored correctly but showed nothing on screen,
  because the preview matched variant filenames against a stale products cache;
  it read as "the run only did English". Pass the data explicitly from the
  result that created it (cache-matching is a fallback at best).
- **Cloud spend is METERED**: every paid call records itself via
  `state.record_spend` (chokepoints: `router._call_grok` / `call_grok_vision`,
  and `artist.generate_image`). The Steward reports actuals plus a soft monthly
  ceiling (`MONTHLY_SPEND_CEILING_USD`). Products created before
  `api.METERING_EPOCH` carry a flat `LEGACY_COST_PER_PRODUCT` estimate (labelled
  `legacy_estimate` in `spend_by_kind`) so pre-metering work never reads as $0.
  New paid call paths must meter themselves the same way.
- **Products persist `target_reached`/`attempts`**; one saved below its target
  wears the BEST EFFORT badge on the dashboard. Any pipeline that saves or
  overwrites a product must set both.
- **Uvicorn's `--reload` can serve a STALE env var** after editing `.env`, even
  across what look like full restarts (new PIDs). If a `.env` change doesn't
  take effect, kill every process on the port (Windows may leave a phantom
  LISTENING socket) and start once without `--reload` to confirm.
- **`WHATSAPP_TOKEN` must be a permanent System User token**, not the temporary
  one from Meta's API Setup page — that one expires in ~24h and silently breaks
  both messaging and any Graph API call, looking exactly like a code regression.
  Generate via Business Settings → System Users → a system user with the WABA
  asset assigned → Generate New Token → expiration "Never". Check with
  `GET /v21.0/debug_token?input_token=<token>&access_token=<token>`
  (`expires_at: 0` and `is_valid: true` confirm the permanent kind).
- **Abigail's number is still Meta's sandbox TEST number** (5-recipient limit),
  so an allowlisted guest (rule 27) also has to be in Meta's test-recipient list
  or her replies silently fail to deliver. Moving to a real number is a future
  owner decision.
- **The outside-24h-window template fallback has never been proven to work.**
  `send_best_effort` → `send_template` uses `WHATSAPP_UPDATE_TEMPLATE`
  (default `secretary_update`), and on 2026-07-11 that template did not exist in
  Meta's system — error 132001 in every language, while `hello_world` sent fine.
  It needs a UTILITY template of that exact name, body exactly `{{1}}`, English
  (US), approved in WhatsApp Manager; no code change. Unverified since, so treat
  scheduler reminders sent outside the 24-hour window as unproven until someone
  checks. `GET /whatsapp/setup` walks through creating it.
- **A WABA sends webhook events to whichever Meta app is in its
  `subscribed_apps` list** — a separate, API-level link from the App Dashboard's
  Callback URL/Verify Token and from the per-field "Subscribe" toggle. All of
  those can look correct while the WABA is subscribed to a different app (ours
  pointed at Meta's own "WA DevX Webhook Events 1P App" after reconnecting), and
  Meta's "Check test webhooks" log will show real inbound messages that never
  reach our server. Check `GET /{waba_id}/subscribed_apps`, fix with
  `POST /{waba_id}/subscribed_apps` (bearer `WHATSAPP_TOKEN`), whenever real
  messages stop arriving after a reconnect or app change.
- **`requirements.txt` covers the backend's direct third-party imports** but
  there is no lockfile and no venv checked in — a `ModuleNotFoundError` after a
  fresh `pip install -r requirements.txt` is a real gap in the file, not a local
  environment issue. Add the missing package.

## Appendix — dispatching work to the Grok / Codex / Antigravity CLIs

`grok`, `codex` and `agy` are installed and authenticated on this machine, so
Claude Code can act as an orchestration layer: scope a task precisely against
the real code yourself, dispatch it headlessly, then re-verify the result
independently. An imprecisely-scoped task is the most likely way a dispatched
agent does the wrong thing confidently, and a dispatched agent's own "verified"
claim is never the last word — re-run the check and read the whole `git diff`.

```bash
# Grok — run in the FOREGROUND for any dispatch carrying edit/write permission:
# acceptEdits disables its own approval prompts, so a human has to be waiting.
grok --prompt-file <task-prompt> --worktree <name> \
  --allow "Edit" --allow "Write" --allow "Bash(python -c*)" --allow "Bash(grep*)" \
  --deny "Bash(git push*)" --deny "Bash(git commit*)" --deny "Bash(rm*)" --deny "Bash(git reset*)" \
  --permission-mode acceptEdits --max-turns 20 --output-format plain

# Codex (cloud is the default; valid slugs: gpt-5.5, gpt-5.4, gpt-5.4-mini —
# the gpt-*-codex names are rejected on a ChatGPT account).
# Sandbox: read-only | workspace-write | danger-full-access.
codex exec -s read-only - < <prompt-file>

# Antigravity — --mode plan is read-only, --mode accept-edits is scoped
# auto-approval (no --allow/--deny, no --worktree: isolate with git worktree).
agy --mode plan --print-timeout 9m --add-dir <repo> -p "<prompt>"
```

Three hard-won invocation gotchas:

- **`--worktree` did NOT actually isolate** a headless `--prompt-file` run
  (grok 0.2.91): no worktree was created and Grok edited the main tree directly.
  Never assume isolation — check `git status`/`git diff` immediately after.
  (The pre-existing worktree at `.grok/worktrees/...` is unrelated; leave it.)
- **`agy -p/--print` takes the prompt as its own value**, so
  `agy --print --mode plan "<prompt>"` feeds the literal string `--mode` to the
  model. Put `-p "<prompt>"` LAST.
- **`agy` doesn't treat the shell's cwd as its workspace** — without
  `--add-dir <repo>` it runs in a scratch directory and cannot see the repo.
- **Codex has corrupted files on Windows**: one dispatch rewrote `api.py` and
  `requirements.txt` with a BOM and cp1252 mojibake (every em dash and arrow
  mangled) while completing the task correctly otherwise. `git diff` caught it
  and the whole output was reverted. Read the diff for encoding damage, not just
  for logic, after any Codex dispatch that writes files.

Codex's model routing lives in `~/.codex/config.toml` (`model = "gpt-5.5"`,
`model_provider = "openai"`); the desktop app also writes that file, so if a
dispatch suddenly fails, re-apply those lines or pass per-invocation overrides
(`-c model_provider=openai -m gpt-5.5`), which always win.
