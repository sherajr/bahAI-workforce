# bahAI Workforce — Briefing: The Secretary (personal assistant agent)

**To Fable 5:** this is a new agent and a new surface area — a personal
secretary for Sheraj himself, not a product pipeline. Read `README.md`,
`docs/ARCHITECTURE.md`, and `CLAUDE.md` first; this briefing assumes you know
the agent roster, the constitution's 9 principles, and this repo's hard rules
(especially: honesty-critical text is code-appended, trust moves only on
judged outcomes, errors surface in the Activity Log, spend is metered).
Follow the established discipline: propose your approach for anything
genuinely ambiguous before implementing, build in phases with a working,
dashboard-visible milestone at the end of each, and verify every piece live —
real calendar events, real WhatsApp messages, real Sonnet calls — never
"the code looks right."

This briefing touches Sheraj's private life (health, recovery, personal
schedule). The privacy rules below are hard rules of the same class as the
translation-disclaimer rule. Read them before writing any code.

## What this is

A **Secretary agent** — a real personal assistant Sheraj talks to in natural
language, powered by **Claude Sonnet** (new provider — see Phase 1). She:

- **Manages his Google Calendar**: reads all his calendars; creates events on
  her own dedicated "bahAI Secretary" calendar; pays particular attention to
  **Bahá'í core activities** (devotionals, study circles, children's classes,
  junior youth groups) and **institutional events** (Nineteen Day Feast, Holy
  Days, LSA/cluster events), which she recognizes, tags, and treats as
  first-class commitments.
- **Reminds him to get ready** — lead-time reminders before events, evening-
  before reminders for Holy Days and Feast, delivered over WhatsApp.
- **Keeps track of his personal and professional life** — tasks and errands
  he tells her about, plus a light professional view from the workforce
  itself (drafts awaiting Etsy activation, this month's spend) via the
  existing API.
- **Supports his recovery** — proactive, warm accountability for two named
  struggles: **vaping** and **self-isolation**. Morning intention + evening
  reflection check-ins, streak tracking, and she actively watches the
  calendar for isolation patterns and nudges him toward human contact and
  core activities.
- **Lives on WhatsApp** via her **own number** (official Meta WhatsApp
  Business Cloud API — decision already made, see below) and in a new
  **"Secretary" tab** on the dashboard.
- **Schedules tasks** — one-off and recurring reminders ("remind me every
  Sunday evening to prep for the week"), stored durably and fired by a
  background scheduler.

## Decisions already made (don't re-litigate these)

Sheraj was asked and chose, 2026-07-05:

1. **WhatsApp = her own number** via the official Meta WhatsApp Business
   Cloud API. He saves her as a contact and messages her like a person; she
   messages others from *her* number, identified as Sheraj's assistant.
   **Never** build an unofficial bridge (whatsapp-web.js / Baileys) that
   sends from his personal account — ToS violation, ban risk, explicitly
   rejected.
2. **Outbound autonomy = auto for a trusted list.** She may message contacts
   on an explicit allowlist without asking (RSVPs, "running late" notes);
   anyone not on the list requires Sheraj's approval per message. The
   allowlist lives in the private DB and is editable in the dashboard.
3. **Recovery support = proactive check-ins.** Morning and evening check-ins,
   streak tracking, isolation-pattern watching. Not on-demand-only, not
   silent calendar engineering — she brings it up, kindly.
4. **Model = Claude Sonnet** via a new `ANTHROPIC_API_KEY`. Metered into the
   Steward's spend report like every other paid call.

## Hard rules (same weight as CLAUDE.md's — add them there when done)

1. **Everything personal lives in `private/` and only there.** Create
   `private/` at the repo root, add it to `.gitignore` in the same commit
   that creates it, and put ALL of this inside: `private/secretary.db` (her
   own SQLite — conversations, check-ins, streaks, tasks, reminders,
   contacts, approval queue), `private/memory/*.md` (her long-term notes
   about Sheraj), `private/google_token.json`, and any WhatsApp state.
   Nothing personal ever goes in `workforce.db`, in `log_run` summaries, in
   job progress strings, or in any committed file. Before finishing Phase 1,
   run `git status` and `git check-ignore private/` and prove it's ignored.
2. **Recovery content goes to Sheraj only.** Check-ins, streaks, urges,
   slips — this content may appear in exactly two places: the direct
   WhatsApp thread with Sheraj and the Secretary tab. Never in a message to
   any other contact (even allowlisted), never in the Activity Log, never in
   an LLM prompt for any other agent. Deterministic guard, not prompt
   compliance: the send path itself must refuse recovery-tagged content to
   any recipient that isn't Sheraj.
3. **She owns only her own calendar.** She creates/edits/deletes freely ONLY
   on the "bahAI Secretary" calendar she creates. Any change to an event on
   any other calendar (his primary, shared ones) requires his explicit
   per-event confirmation — same pattern as the Etsy trust gate: return
   `requires_confirmation`, let him approve in chat or the dashboard.
4. **Holy Day and Feast dates come from a hand-curated table, never model
   memory.** Create `agents/badi_dates.py` with Gregorian dates for Nineteen
   Day Feasts and the 11 Holy Days for 2026–2028, each entry carrying its
   source, verified by a human against bahai.org before shipping (same
   discipline as `CONSULTATION_SCRIPTURE`). An LLM hallucinating a Holy Day
   date is a Trustworthiness failure in the one domain this project exists
   to honor. If the table doesn't cover a requested date, she says so and
   links the official calendar — she never guesses.
5. **Outbound messages to non-allowlisted people queue for approval.** The
   queue is in the private DB; approval happens in the dashboard or by
   Sheraj replying in WhatsApp ("send it" / "approve 3"). The send path
   enforces this mechanically — her prompt is never the only thing standing
   between a draft and a stranger's phone.
6. **Every Sonnet call is metered.** New `EST_COST_USD` entry (suggest
   `"claude_chat": 0.01`) recorded via the existing `record_spend`
   chokepoint pattern. The Steward report and monthly ceiling must include
   her spend from day one.
7. **Quiet hours.** No proactive messages 22:30–07:30 (configurable in the
   private DB settings) except reminders Sheraj explicitly marked "wake me".
   Moderation applies to notifications too — a secretary who pings at 2am
   gets muted, and then she's useless.
8. **Scheduler failures surface.** The background scheduler logs every fire
   and every failure to the Activity Log (event names only — "reminder
   fired: Feast prep", never check-in content, per rule 2). A reminder that
   silently never fires is the Canva-silent-failure bug all over again.
9. **She is not a therapist and never pretends to be.** Warm accountability
   partner, yes; clinical advice, no. If a conversation shows real crisis
   signals, her job is to gently and explicitly encourage reaching out to a
   human — a friend, family, his community, or a professional — not to
   handle it herself. This goes in her system prompt verbatim-ish and gets
   reviewed by Sheraj before Phase 4 ships.

## Architecture at a glance

New modules (flat siblings, matching repo convention):

- `agents/secretary.py` — the brain: system prompt (built on
  `build_system_prompt` + a secretary role in `AGENT_ROLE_DESCRIPTIONS` +
  private context), conversation loop, tool-style intents (create event,
  set reminder, add task, draft message, log check-in). Register
  `"secretary"` in `state.AGENT_NAMES`; `log_run` with `passed_review` only
  for judged outcomes (CLAUDE.md rule 14).
- `agents/secretary_store.py` — everything `private/`: schema + CRUD for
  `private/secretary.db`, memory-file read/write. The ONLY module that
  touches personal data at rest.
- `agents/gcal.py` — Google Calendar OAuth (mirror `etsy.py`'s raw-requests
  PKCE/localhost-callback pattern; scope `https://www.googleapis.com/auth/calendar`;
  token in `private/google_token.json`), dedicated-calendar creation,
  event list/create/update/delete, and the deterministic Bahá'í event
  tagger (keyword rules + `badi_dates.py` — not an LLM call).
- `agents/whatsapp.py` — Meta Cloud API client: send text, send template,
  webhook payload parsing, 24-hour-window tracking.
- `agents/scheduler.py` — daemon thread started on FastAPI startup: ticks
  every ~30s, reads due reminders/check-ins/tasks from the private DB,
  fires WhatsApp/dashboard notifications, reschedules recurrences. All
  state in the DB so restarts lose nothing.
- Anthropic provider in `agents/router.py` — a `call_claude()` sibling to
  `_call_grok` (env: `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` defaulting to
  the current Sonnet model id — check the docs for the exact current id
  rather than trusting memory), metered per hard rule 6. Route secretary
  task types to it; everything existing stays exactly where it is.

Dashboard: new **"Secretary" tab** (add to `Nav.tsx`) with panels for Chat,
Today & Upcoming (tagged events + reminders), Pending approvals, Check-ins
(discreet — content renders only inside this tab), Trusted contacts, and
Setup status (Google / WhatsApp connection state with guided setup, Canva/
Etsy-settings style).

## Phase 1 — Foundations (Sonnet + private store + chat)

Anthropic provider in the router (metered), `private/` + `.gitignore` entry,
`secretary_store.py` with the full schema, `secretary.py` with her system
prompt and conversation loop, and the Secretary tab with a working chat
panel. Her memory: recent conversation window from the DB plus
`private/memory/*.md` notes she updates when Sheraj tells her something
durable ("my brother's name is…", "I work Tuesdays"). No vector DB — at
personal scale, markdown + recency is enough and stays inspectable.

**Verify:** real Sonnet conversation from the dashboard; facts told to her
in one session recalled in the next (restart the API between); spend visible
in the Steward report; `git check-ignore private/` passes; `grep` the
Activity Log output for anything personal (must find nothing).

## Phase 2 — Google Calendar + Bahá'í awareness + reminders

OAuth flow (`/gcal/oauth/start` → localhost callback, token in `private/`),
create the "bahAI Secretary" calendar on first connect, merged upcoming view
in the tab with deterministic tags (Core activity / Institutional / Holy Day
/ Feast / personal / professional), `badi_dates.py` (human-verified, per
hard rule 4), event create/edit/delete through chat (own calendar free,
other calendars gated per hard rule 3), and the scheduler firing lead-time
reminders (defaults: 60 + 15 min before located events, evening-before for
Holy Days and Feast; per-event overrides via chat). Reminders land in the
dashboard until Phase 3 adds WhatsApp.

**Verify:** live against his real calendar — create, edit, delete on her
calendar via chat and see it in Google Calendar's own UI; attempt an edit to
a primary-calendar event and confirm it gates; confirm a known upcoming
Feast/Holy Day appears tagged with the date matching bahai.org; set a
reminder 2 minutes out and watch it fire; restart the API and confirm
pending reminders survive.

## Phase 3 — WhatsApp (her own number)

Meta setup is the fiddly part and Sheraj is non-technical — build a guided
setup screen (env keys: `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`,
`WHATSAPP_VERIFY_TOKEN`; step-by-step like the Etsy/Canva OAuth pages) and
start on Meta's free test number before a real one. Inbound needs a public
webhook (`GET /whatsapp/webhook` hub.challenge verification + `POST` for
messages) — recommend a free named Cloudflare Tunnel to `localhost:8765`;
document the setup for him.

Honest constraint to design around: WhatsApp only allows free-form messages
within 24 hours of the *recipient's* last message. For Sheraj this is nearly
moot (daily check-ins keep the window open), but the scheduler must detect a
closed window and fall back to a pre-approved template (submit a generic
utility template like "Update from Sheraj's assistant: {{1}}" for review —
and note in the setup screen that Meta may take a day to approve it). For
other people it means: contacts who have never messaged her can only receive
template messages — say so in the UI rather than letting sends silently fail.

Outbound-to-others ships here too: allowlist auto-send, everyone else queued
per hard rule 5, approvals from dashboard or by replying in the thread.

**Verify:** end to end with real phones — he messages her, she answers; a
2-minute reminder arrives on WhatsApp; a message to a non-allowlisted number
queues, gets approved, arrives; quiet hours actually suppress (set quiet
hours to now, schedule a reminder, watch it hold and deliver after).

## Phase 4 — Rhythms: recovery, check-ins, weekly review, scheduled tasks

The proactive layer, all scheduler-driven: morning intention + evening
reflection check-ins (short — one or two questions, not a form), streak
tracking for vape-free days (slips logged as data, streaks restart without
drama), isolation watch (scan the coming 7 days; if there are days with zero
human-contact events, she says so and proposes something concrete — a
devotional, a call, a walk with someone), and a Sunday weekly review that
merges personal (week ahead, commitments, streaks) with professional
(products awaiting Etsy activation, month's metered spend — via the existing
endpoints). Recurring/one-off task scheduling through chat ("every 19 days,
remind me two evenings before Feast to prepare").

Tone spec for her prompt (Sheraj reviews before this ships, per hard rule 9):
warm, brief, never shaming, celebrates small wins, treats a slip as
information not failure, consistently nudges toward human connection and the
community — and knows she's an assistant, not a clinician.

**Verify:** live check-ins over at least two real days (not simulated
timestamps) including one deliberately missed check-in (she follows up once,
gently — not repeatedly); a fake empty week triggers the isolation nudge; a
recovery-content send to a non-Sheraj recipient is refused by the guard
(test it deliberately); Sheraj reads and approves the tone prompt.

## What NOT to do

- No unofficial WhatsApp bridges from his personal account (decided; see
  above).
- No personal data outside `private/` — not in `workforce.db`, not in
  `log_run`, not in job progress text, not in comments or test fixtures.
- No LLM-guessed Holy Day/Feast dates, ever (hard rule 4).
- Don't give her write access to calendars she doesn't own without the
  per-event gate (hard rule 3).
- No vector DB for personal memory — markdown + the private DB is the
  design, deliberately inspectable by its owner.
- Don't route existing pipeline tasks to Sonnet — the Artist/Scribe/
  Reviewer/Librarian routing in `router.py` stays untouched. Sonnet is hers.
- Don't build all four phases before showing Phase 1 working — this repo
  ships in verified milestones (owner feedback, long-standing).

## Acceptance (the whole feature)

- Sheraj messages his Secretary on WhatsApp like a person; she answers with
  real knowledge of his calendar, tasks, and history.
- Bahá'í core activities and institutional events are recognized, tagged,
  and reminded about, with Feast/Holy Day dates provably matching bahai.org.
- She gets him ready for things: reminders fire reliably, survive restarts,
  respect quiet hours, and failures are visible in the Activity Log.
- She messages trusted contacts autonomously and queues everyone else for
  his approval — verified with real sends.
- Recovery support is proactive, kind, streak-aware, isolation-aware, and
  physically cannot leak beyond his own thread and tab.
- `git status` on a working install shows no personal file, ever.
- Every Sonnet call shows up in the Steward's metered spend.
