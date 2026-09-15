# Rules 15-28, 50-54 — the Secretary (Abigail)

Canonical numbered hard rules for this subsystem. **Numbers are permanent — never
renumber, only append.** Cited from code comments across the repo.
Loaded on demand from the root `AGENTS.md` routing table; do not copy these
into CLAUDE.md or the root AGENTS.md.

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

