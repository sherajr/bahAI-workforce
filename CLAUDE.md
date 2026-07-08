# CLAUDE.md — orientation for AI coding sessions

Read `README.md` for the project map and `docs/ARCHITECTURE.md` for diagrams.
This file covers what an AI changing this code must know.

## Commands

```bash
cd dashboard && npm run dev                # UI on :5173
cd dashboard && npx tsc --noEmit           # typecheck (no test suite exists)
python -c "import agents.api"              # fast backend sanity check
```

**The backend is already running as a Scheduled Task ("bahAI Secretary API",
added 2026-07-07) that auto-starts at Windows logon — don't run
`python agents/api.py` or `python -m agents.api` to start it.** That file's
own `__main__` block uses `host="0.0.0.0", reload=True`, and starting a
second instance alongside the managed one creates two processes both
listening on :8765 (Windows allows a wildcard bind and a loopback bind to
coexist) — this happened for real and looked like "WhatsApp stopped
responding" while the dashboard kept working. If you need to restart the
backend after a code change, kill whatever's on :8765
(`netstat -ano | grep 8765`, then check for orphaned child workers too —
`--reload`'s WatchFiles spawns a separate child PID that survives killing
the parent) and re-trigger the task
(`Start-ScheduledTask -TaskName "bahAI Secretary API"`), or run
`python -m uvicorn agents.api:app --host 127.0.0.1 --port 8765` (no
`--reload`) directly.

Verification pattern used throughout this repo: no formal test suite — test
logic offline with mocks first, then verify live against the real SQLite DB
(`agents.state.get_all_products()`) and real LLM calls (Ollama local, xAI Grok).
FastAPI's `TestClient` works well for endpoint checks without starting a server.

## The two pipelines

`_run_full_pipeline` in `agents/api.py` is the bookmark entry point:
create task → Librarian retrieves citations → Artist builds prompt + generates
image (xAI) → `_pipeline_write_approve_sync` (consultation → Scribe writes →
Reviewer scores → mechanical-edit revision loop) → save product → Compositor
renders front/back PNGs → Canva autofill. The dashboard polls
`/pipeline/status/{job_id}`; jobs run in a thread pool (`_start_job`).

`_run_card_pipeline` (same file) is the QUOTE CARD entry point — a giveaway
product, never sold: Librarian → Artist (card brief) → consultation with
`product="quote_card"` → optional translation (`translator.py`, Grok path) →
`card_compositor.render_quote_card` (3.5×2in, multi-script) →
`reviewer.score_quote_card` (sees the RENDERED front face) → requote/repaint
revision loop driven by the review's machine-readable `action` field.
Products carry `product_type`; bookmark-only endpoints reject cards via
`_require_bookmark`.

## Hard rules (violating these reintroduces fixed production bugs)

1. **Qwen has tight context.** Anything routed to Ollama (`router.py`:
   everything NOT in `GROK_TASK_TYPES`) must get lean prompts. Long prompts
   made Qwen burn its whole token budget thinking and return `{`.
2. **`bookmark_quote` is locked.** `apply_edits` rejects edits to it and must
   report them as `rejected_locked`, never silently drop them.
3. **Revision is forward-chaining.** The loop revises `cur_listing` with
   `cur_review` (the latest), tracks `best_*` separately, adopts ties (newer
   listing wins), and only counts strict regressions toward the 2-strike stall.
4. **Every revision path must end in `_sanitize_claims`.** It deterministically
   strips false claims (handcrafted, exact motif counts). LLM compliance is
   never trusted for honesty-critical text.
5. **Reviewer JSON can truncate at the token ceiling.** `_parse_review` tracks
   whether `_repair_truncated_json` fired and drops the last `edits` element
   if so. Keep `edits` early in the schema field order.
6. **Consultation scripture stays hand-curated.** `CONSULTATION_SCRIPTURE` in
   `consultation.py` maps each consultation moment to one short cited excerpt
   (≤40 words). No vector DB for this, per explicit owner decision.
7. **The consultation's round-2 decision is binding.** `reviewer.score()`
   receives `consultation_decision`; overrides must be named
   "REOPENING team decision: ..." — never silently contradicted.
8. **Translation disclaimers AND AI-artwork disclosures are code-appended,
   never LLM-written.** A quote card translation is always labeled
   AI-assisted/unofficial: fixed strings in `translator.LANGUAGES`, printed on
   the card by the compositor and stored in metadata. The artwork's AI
   provenance is disclosed the same way: `etsy.AI_ART_DISCLOSURE` is appended
   in code to every published listing description, `api.CARD_ART_DISCLOSURE`
   is stored in every card's metadata, and the Scribe's `_HONESTY_RULES`
   forbid implying hand illustration. Same class as `_sanitize_claims`.
9. **A new card language ships only after a human-viewed render.** PIL draws
   missing glyphs as tofu without erroring, and unshaped Arabic renders as
   disjointed LTR letters — `card_compositor` shapes RTL per line with
   arabic-reshaper + python-bidi, and every font in `LANGUAGES.font_paths` was
   verified by eye. Adding a language = config entry + a viewed sample PNG.
10. **Bookmark consultation prompts are frozen via `_PRODUCT_FRAMES`.** The
    "bookmark" frame values in `consultation.py` reproduce the original prompt
    strings character-for-character; card wording changes go in the
    "quote_card" frame, never inline in the shared prompt bodies.
11. **Quote cards may only quote Ruhi Book 1.** `librarian.retrieve_ruhi_book1()`
    (backed by `agents/ruhi_book1_source.py` + `scripts/ingest_ruhi_book1.py`'s
    own ChromaDB collection) is the ONLY retrieval path `_run_card_pipeline`
    may call — never `retrieve()` (the general 7-text index the bookmark
    pipeline uses). An empty result must raise, never fall back to the
    general index or let the consultation's Librarian free-associate a quote
    from memory.
12. **The bookmark quote's GROUNDED verdict is deterministically re-checked.**
    `api._check_quote_grounding` (word-overlap vs the retrieved citations, or
    `librarian.verify()` when retrieval was empty) gates `quote_grounded`
    before the quote gets locked — never reintroduce trust in the
    consultation Librarian's self-report alone. Unverifiable demotes to
    ungrounded; the demotion is logged and appended to the transcript.
13. **The Etsy price is policy-set, never parsed from LLM prose.**
    `etsy.BOOKMARK_PRICE` (env `ETSY_BOOKMARK_PRICE`) is the only price
    source; the Scribe's `price_note` is a display-only suggestion.
14. **`log_run(passed_review=...)` moves agent trust — only pass it for
    JUDGED outcomes** (a review verdict, a deterministic check like the
    translator's script check or the grounding check). Mechanical success
    ("the API call returned a file") stays `None`, or clean-run stats become
    an uptime metric. Trust has a real consequence: `/etsy/publish` requires
    Reviewer trust level ≥ 2 or an explicit `confirm=true`.

## The Secretary (personal assistant — Phases 1–3 live, 4 pending)

Sheraj's personal assistant, chatting from the dashboard's Secretary tab on
Claude Sonnet (`router.call_claude`/`call_claude_agentic`, env
`ANTHROPIC_API_KEY`/`ANTHROPIC_MODEL`). Full spec:
`docs/fable5-briefing-secretary.md` — read it before building Phase 4
(recovery rhythms); the spec predates the Google Workspace expansion and
the tool-calling migration below, so treat this file as authoritative where
they differ. Phase 2 adds `gcal.py` (Google OAuth, since expanded into the
shared `google_auth.py` — env `GOOGLE_CLIENT_ID`/`SECRET`), `badi_dates.py`,
and `scheduler.py` (daemon thread from `api.on_startup`; ticks 30s; all
state in the private DB). Every action she can take — read or write — is a
real Claude tool call (`secretary_tools.py`, `router.call_claude_agentic` —
rule 22; migrated 2026-07-07 off an earlier custom-text-tag design), giving
her on-demand lookups across calendar/Gmail/Drive/Docs/Sheets/Slides
(`gmail.py`/`gdrive.py`/`gdocs.py`/`gsheets.py`/`gslides.py` — rules 23-25)
plus write access gated the same way calendar writes always were. Phase 3
adds `whatsapp.py` (Meta Cloud API — rules 26-28) so she lives on WhatsApp
via her own number, not just the dashboard. Hard rules already in force:

15. **Everything personal lives in `private/` and only there.**
    `agents/secretary_store.py` is the ONLY module that touches personal data
    at rest (`private/secretary.db` + `private/memory/*.md`). Nothing personal
    ever goes in `workforce.db`, `log_run` summaries, job progress strings,
    stdout, or any committed file. `private/` is git-ignored; message content
    renders only inside the Secretary tab.
16. **Sonnet is hers alone.** `call_claude`/`call_claude_agentic` exist for
    the Secretary; never route Artist/Scribe/Reviewer/Librarian task types
    to them. Every underlying API call — including each round of a
    multi-round tool-calling turn — is metered as `claude_chat` via the same
    `record_api_spend` chokepoint.
17. **She is not a therapist** — her system prompt says so and steers crisis
    signals toward human help. Keep that block when editing her prompt.
18. **Every write is a real Claude tool call** (`agents/secretary_tools.py`
    write tools, migrated 2026-07-07 off an earlier design where writes were
    custom `<remember>`/`<task>`/`<event>`/`<remind>` markup parsed out of
    her reply text — see rule 22 for why). The tool executor, not prompt
    compliance, enforces every ownership/approval gate before anything
    actually happens.
19. **Holy Day and Feast dates come from `badi_dates.py` and only there** —
    a hand-curated table with per-entry sources (bahai.org + UHJ tables),
    2026–2028. Never let an LLM supply a Bahá'í date; outside coverage she
    links the official calendar. Extending coverage = new verified entries.
20. **She owns only her own calendar** ("bahAI Secretary", created on first
    connect). `gcal.is_her_calendar()` gates writes: any edit/delete on
    another calendar becomes a `pending_actions` row requiring Sheraj's
    approval (dashboard buttons or "approve N" in chat — the approval path
    is regex + code, no LLM in the loop).
21. **Quiet hours are enforced in the scheduler** (default 22:30–07:30,
    `settings.quiet_hours`): held reminders deliver after the window ends;
    only `wake_me` reminders break through. Scheduler fires/failures surface
    as notifications → dashboard Activity Log, titles only.
22. **Every action, read or write, is a real Claude tool call**
    (`router.call_claude_agentic` + `agents/secretary_tools.py`) —
    migrated 2026-07-07 off an earlier design where writes were custom
    `<event>`/`<sheet_append>`/etc. markup embedded in her reply text and
    parsed afterward by regex. Live testing showed that design unreliable
    at the one thing it needed to be reliable at: in a long session she
    would repeatedly write a confident sentence ("Adding that now") with no
    markup behind it (or malformed markup) and nothing would happen —
    structured tool-calling is a far stronger guarantee than "remember to
    also type this exact syntax." Every ownership/approval gate (Calendar
    rule 20, Drive rule 24, Gmail rule 25) now lives inside each write
    tool's handler in `secretary_tools.make_executor` — the safety model is
    unchanged, only the trigger mechanism is. A write tool called twice
    with byte-identical arguments in one turn executes only once (dedup
    guard in `make_executor`) so a restated call never repeats the action.
    Capped at 6 rounds per turn; a round hitting the cap is forced to
    answer in text (`tool_choice: "none"`), never left to loop. The
    returned reply is EVERY round's text concatenated, never just the final
    round's — a real bug when writes were still text tags (an early
    round's tag would otherwise be silently discarded), kept because
    dropping any round's narration would still be a regression. A reply
    that narrates a commitment ("Adding that now") with literally no tool
    call behind it at all is still structurally possible (the model
    choosing not to call anything) — `secretary._finalize_reply`'s
    `_looks_like_uncommitted_action` heuristic catches that residual case
    and surfaces it as a visible error instead of silence.
23. **Google Workspace scopes come from one shared OAuth module**
    (`agents/google_auth.py`) — one consent screen, one
    `private/google_token.json`, covering Calendar/Gmail/Drive/Docs/Sheets/
    Slides. Full `calendar`/`drive`/`documents`/`spreadsheets`; Gmail is
    `gmail.readonly` + `gmail.send` only, never `gmail.modify`; Slides is
    `presentations.readonly` only — no Slides write functions exist.
    `gcal.py`/`gmail.py`/`gdrive.py`/`gdocs.py`/`gsheets.py`/`gslides.py`
    import `get_valid_token`/`_headers` from there rather than managing
    their own tokens.
24. **Drive has a sandbox too.** `agents/gdrive.py`'s
    `ensure_secretary_folder()`/`is_in_her_folder()` mirror
    `gcal.ensure_secretary_calendar()`/`is_her_calendar()` (rule 20): she
    creates Docs/Sheets/files freely only inside her own "bahAI Secretary"
    Drive folder; touching (rename/trash/move/edit) anything outside it
    queues a `pending_actions` row, same approval path as a non-owned
    calendar edit.
25. **Gmail has no free tier at all.** Unlike Calendar/Drive, there is no
    "her own inbox" to sandbox — every `send_email` tool call becomes a
    `pending_actions` row of kind `gmail_send` unconditionally (the tool
    handler queues it; it never sends directly). `gmail.send_message` is
    only ever called from `secretary.execute_pending_action` after Sheraj's
    explicit approval.
26. **The WhatsApp webhook (`POST /whatsapp/webhook`, Phase 3) is the one
    endpoint in this API meant to be reachable from the public internet**
    (via a Cloudflare Tunnel — the setup guide at `GET /whatsapp/setup`
    restricts the tunnel's ingress to only that path, never the whole API).
    `agents/whatsapp.verify_signature()` (HMAC-SHA256 over the raw body,
    keyed on `WHATSAPP_APP_SECRET`) is the ONLY authentication on that
    endpoint — no app secret configured means the check fails closed
    (rejects everything) rather than skipping verification. Never relax
    this or trust an unsigned payload.
27. **Only Sheraj's own WhatsApp number gets Secretary access.**
    `WHATSAPP_OWNER_NUMBER` + `whatsapp.is_owner()` gate the webhook handler
    (`agents/api.py::_handle_whatsapp_message`): a message from any other
    number never reaches `secretary.chat()` (which would otherwise hand a
    stranger who texts the number full tool-calling access to Sheraj's
    calendar/Gmail/Drive) — it gets a fixed canned reply instead. The
    allowlist (rule 28) is a SEPARATE, narrower concept: which people SHE
    may message, not who may message and command her.
28. **The WhatsApp allowlist is owner-controlled only, never LLM-writable.**
    `agents/secretary_store.py`'s `contacts` table (`add_contact`/
    `set_contact_allowlisted`/`remove_contact`) is only ever touched from
    the dashboard's Trusted Contacts UI and its `/secretary/contacts*`
    endpoints — there is no tool exposing it to the model, unlike every
    other write in this file. `send_whatsapp` (secretary_tools.py) sends
    immediately only to the owner or an allowlisted contact (falling back
    to the pre-approved template, `WHATSAPP_UPDATE_TEMPLATE`, if the
    24-hour free-form window per `whatsapp.within_24h_window()` has
    closed); anyone else queues as a `pending_actions` row of kind
    `whatsapp_send`, same unified queue as rules 20/24/25.

## Gotchas

- Windows console is cp1252: use ASCII `->` not `→` in anything `print`ed by
  scripts; the API/dashboard themselves are UTF-8 safe.
- `state.py` migrations run on every startup (ALTER TABLE wrapped in
  try/except) — add new product columns there AND to the `update_product`
  allowlist.
- Ollama calls set `think: False` (Qwen3 hybrid-thinking would silently eat
  the output budget otherwise).
- The owner (Sheraj) is non-technical: dashboard-visible behavior is the
  deliverable, and errors must surface in the Activity Log, never vanish
  (Canva autofill once failed silently for weeks).
- Cloud spend is METERED: every paid call records itself via
  `state.record_spend` (chokepoints: `router._call_grok` / `call_grok_vision`
  and `artist.generate_image`). The Steward reports actuals plus a soft
  monthly ceiling (`MONTHLY_SPEND_CEILING_USD`). Products created before
  `api.METERING_EPOCH` carry a flat `LEGACY_COST_PER_PRODUCT` estimate
  (labeled `legacy_estimate` in `spend_by_kind`) so pre-metering work never
  reads as $0. New paid call paths must meter themselves the same way.
- Products persist `target_reached`/`attempts`; a product saved below its
  target wears the BEST EFFORT badge on the dashboard. Pipelines that save or
  overwrite a product must set both fields.
- `secretary_store.py`'s migrations follow `state.py`'s pattern (try/except
  `ALTER TABLE`) for new columns, but a NEW CONSTRAINT on an existing column
  (e.g. adding `UNIQUE` to `contacts.phone`) needs its own migration too —
  `CREATE TABLE IF NOT EXISTS` is a no-op on a table that already exists on
  disk, so editing the inline constraint in the `CREATE TABLE` block does
  nothing for any DB created before that edit. Add a
  `CREATE UNIQUE INDEX IF NOT EXISTS` (or equivalent) alongside it — this bit
  us for real: `contacts` shipped without the constraint applying, so every
  inbound WhatsApp message crashed `record_inbound_contact`'s
  `ON CONFLICT(phone)` upsert before it ever reached the Secretary.
- Uvicorn's `--reload` (WatchFiles) has been observed serving a STALE
  environment variable value after editing `.env`, even across what looked
  like full restarts (new PIDs). If a `.env` change doesn't seem to take
  effect, fully kill every process on the port (watch for Windows leaving a
  phantom LISTENING socket behind) and start once with
  `python -m uvicorn agents.api:app --host 127.0.0.1 --port 8765` (no
  `--reload`) to confirm — then decide whether to keep running that way.
- **The API server and Cloudflare Tunnel auto-start at Windows logon**
  (Scheduled Tasks "bahAI Secretary API" and "bahAI Secretary Tunnel", added
  2026-07-07) via `scripts/start_secretary_server.ps1` /
  `start_secretary_tunnel.ps1` — no `--reload`, bound to `127.0.0.1`, logging
  to `logs/*.out.log`/`*.err.log` (gitignored) since there's no interactive
  console to read after a real reboot. If you kill the server process
  mid-session to pick up a code change, that's fine — the tasks only
  re-trigger at the next logon, they won't fight a manual restart. Check
  `logs/` first if the Secretary seems down after a reboot rather than
  assuming it needs code changes.
- **`WHATSAPP_TOKEN` must be a permanent System User token, not the default
  temporary one from Meta's API Setup page** — the temporary one expires in
  ~24h and silently breaks both messaging AND any Graph API call (e.g. the
  `subscribed_apps` check above), looking exactly like a code regression.
  Generated via Business Settings → System Users → a system user with the
  WABA asset assigned → Generate New Token → expiration "Never". Check
  validity any time with `GET /v21.0/debug_token?input_token=<token>&access_token=<token>`
  (`expires_at: 0` and `is_valid: true` confirm it's the permanent kind).
- **WhatsApp: a WhatsApp Business Account (WABA) sends its webhook events to
  whichever Meta app is in its `subscribed_apps` list — a separate, API-level
  link from the App Dashboard's Callback URL/Verify Token and from the
  per-field "Subscribe" toggle on the Configure Webhooks page.** All of those
  can show green/correct while the WABA is actually still subscribed to a
  different app (we found ours pointed at Meta's own
  "WA DevX Webhook Events 1P App" after reconnecting the app in Meta's UI) —
  Meta's own "Check test webhooks" log will show real inbound messages even
  though they never reach our server, which is misleading during debugging.
  Check with `GET /{waba_id}/subscribed_apps` and fix with
  `POST /{waba_id}/subscribed_apps` (both using `WHATSAPP_TOKEN` as the
  bearer token) if real messages stop arriving after any reconnect/app change.
