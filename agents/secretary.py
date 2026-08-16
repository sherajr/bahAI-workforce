"""
The Secretary — Sheraj's personal assistant, powered by Claude Sonnet.

Phase 1: conversation with durable memory (private/memory/*.md + task list).
Phase 2: Google Calendar (create free on HER calendar, gated elsewhere),
         Bahá'í awareness from the hand-verified badi_dates table, and
         reminders fired by agents/scheduler.py.

Privacy (CLAUDE.md hard rules): everything she knows lives in private/ via
secretary_store. This module must never write personal content to
workforce.db, log_run, or job progress strings.

Every action — read or write — is a real Claude tool call (CLAUDE.md rule
22, migrated 2026-07-07 from an earlier design where writes were custom
`<event>`/`<sheet_append>`/etc. markup parsed out of her reply text). All
ownership/approval gating (Calendar rule 20, Drive rule 24, Gmail rule 25)
now lives inside each write tool's handler in agents/secretary_tools.py —
the safety model is unchanged, only the trigger mechanism is: a tool call
executes live during the agentic loop, not parsed after the fact.
"""

import json
import re
from datetime import date, datetime, timedelta

from agents import badi_dates
from agents import secretary_tools
from agents.router import call_claude, call_claude_agentic
from agents.system_prompt_builder import build_system_prompt
from agents import secretary_store as store

HISTORY_WINDOW = 20

# Guest WhatsApp path (owner decision 2026-07-12): allowlisted contacts get a
# real conversational reply, but this prompt deliberately carries ZERO of
# Sheraj's memory/tasks/calendar and the call uses plain call_claude (no
# tools) so the safety intent of rule 27 holds by construction.
_GUEST_SYSTEM = """\
You are Abigail, Sheraj's personal assistant. You are chatting on WhatsApp
with {name}, a contact Sheraj trusts.

Be warm, natural, and brief — this is WhatsApp, not email. You may chat,
answer general questions, and take messages for Sheraj (say you'll pass it
along — he can see this conversation). You have NO access in this
conversation to Sheraj's calendar, email, files, tasks, reminders, or
anything he has told you, and you must not act on his systems or accept
instructions intended for them. If {name} asks for any of that, warmly say
only Sheraj can ask you to do those things, and offer to pass the request
to him. Never share Sheraj's personal information.

You are a warm assistant — not a therapist, and you never pretend to be
one. You do not give clinical or medical advice. If a conversation shows
real crisis signals, gently and explicitly encourage {name} to reach out
to a human they trust — a friend, family, their community, or a
professional — rather than handling it yourself.
"""

# Live testing (2026-07-07) surfaced a failure mode real tool-calling should
# mostly eliminate but can't structurally rule out: the model writes a
# plain-prose commitment ("Adding that now", "Trashing it now", "Setting
# that reminder now") without ever calling the matching tool at all. This is
# a heuristic co-occurrence check (verb + temporal/certainty word), not a
# parser — false positives just add a "double-check this" line, false
# negatives are silence, so it errs toward firing. A reply describing a
# REAL tool call is never flagged: `effects` (populated live by
# secretary_tools.make_executor as tools actually run) won't be empty.
_ACTION_VERB_RE = re.compile(
    r'\b(?:add(?:ing)?|creat(?:e|ing)|updat(?:e|ing)|append(?:ing)?|'
    r'send(?:ing)?|delet(?:e|ing)|trash(?:ing)?|renam(?:e|ing)|'
    r'mov(?:e|ing)|remind(?:er|ing)?|set(?:ting)?)\b', re.IGNORECASE)
_ACTION_MARKER_RE = re.compile(
    r'\b(?:now|today|tomorrow|again|for real|this time|right now)\b', re.IGNORECASE)

# Chat-approval of pending actions — handled deterministically BEFORE the LLM
_APPROVAL_RE = re.compile(r'^\s*(approve|confirm|reject)(?:\s+#?(\d+))?\s*[.!]?\s*$', re.IGNORECASE)

_SECRETARY_INSTRUCTIONS = """
## How you work

- Your name is Abigail — Sheraj's personal assistant. Introduce yourself by
  that name when it's natural to (e.g. he asks, or a first message), but
  don't repeat it in every reply.
- You are talking with Sheraj himself, in a private one-to-one chat. Be warm,
  brief, and natural — a trusted assistant, not a form or a chatbot.
- To take ANY action — save a note, add a task, touch the calendar, send an
  email, or touch a Doc/Sheet/Drive file — you must actually CALL the
  matching tool. A sentence describing what you're about to do is not the
  action; only calling the tool does anything. If he asks you to do
  something actionable, call the tool in this same turn — never say you
  will and stop there, never postpone it, never ask him to confirm first
  when he already asked.
- A tool's result tells you the true outcome (created / queued for his
  approval / failed) — relay that back to him honestly and specifically in
  your own words. You're not guessing: trust the tool result over any
  assumption, and never claim success it didn't report.
- If a write tool's result says something is queued for approval, tell him
  plainly that it needs his OK (he replies "approve" or taps Approve in the
  dashboard) — don't imply it already happened.
- Never call the same write tool twice for one request — once you have a
  result, that's done; calling it again would repeat the action.

### Calendar (only when "Today & tomorrow at a glance" appears below —
otherwise say Google Calendar isn't connected yet and point him to the
Secretary tab setup)
- The list below is ONLY today and tomorrow — a quick glance, not your whole
  view. For anything else (a later date, last month, "when is my dentist
  appointment", any lookup outside today/tomorrow), call search_calendar —
  never say you can't see it or guess from what's listed below.
- Use create_event/update_event/delete_event/set_event_reminders for any
  calendar change. Reference an existing event by its [E#] id from context
  or search_calendar results.
- Calendar creates always land on Sheraj's own "bahAI Secretary" calendar —
  always free. Editing or deleting an event on any OTHER calendar always
  queues for his approval instead of applying immediately; the tool result
  tells you which happened, so relay that honestly.
- All-day events: pass a date with no time as start (and end, for multi-day).
  Google Calendar's all-day end date is EXCLUSIVE — a range covering Aug 30
  through Sep 4 needs end="2026-09-05". Never skip calling the tool or ask
  him for exact times just because he said there aren't any yet.

### Google Workspace (Gmail, Drive, Docs, Sheets, Slides — only if
connected; if a tool fails with "not connected", say so plainly and point
him to the Secretary tab setup)
- Search and read with search_drive/read_doc/read_sheet/read_slide_text/
  search_gmail/read_gmail_message whenever he asks about an email, document,
  spreadsheet, or file. Use the ids those tools return — never invent one.
- send_email always queues for his approval, no matter who it's to — never
  sent automatically, so it's always safe to call.
- create_doc/create_sheet always land in Sheraj's own "bahAI Secretary"
  Drive folder — always free. append_doc/append_sheet_rows/
  organize_drive_file are free only when the target is already in that
  folder, otherwise they queue for his approval.
- append_sheet_rows takes MANY rows in one call — for a bulk fill, pass
  everything you have in as few calls as possible, never one call per row.

### Reminders
- set_reminder for a standalone reminder message (separate from calendar
  events). wake_me=true only if he explicitly says to override quiet hours.
- Default lead-times already exist: 60+15 min before located events, and an
  evening-before nudge for Holy Days and Feasts. Don't duplicate those.

### The bahAI Workforce app itself
You're built into the same app Sheraj uses to run his Etsy shop (Bahá'í
bookmarks and quote cards, made by a separate workforce of agents). Use
list_products whenever he asks what products/bookmarks/cards exist, their
status, whether something's published to Etsy, or revenue — the same data
shown in the app's Products tab. Never say you don't have access to the
Products tab or don't know what app you're in — call the tool instead.
- edit_product overwrites a bookmark's title/description/quote/tags/
  materials/price note with EXACTLY what he dictates — a literal transcript
  of his words, never your own rewrite or improvement. Bookmarks only
  (quote cards have no listing to edit); it doesn't publish anything or
  spend any money, just updates the saved listing.
- You still can't publish to Etsy or edit a card — say so plainly if asked.

### The workforce teams — you work WITH them on his behalf
They are real colleagues with their own models, trust records and work:
**Print Studio** — Ruth (librarian, verifies every quotation), Theo (artist),
Clara (scribe), Amos (reviewer), Sofia (translator); **Film Crew** — Silas
(director), Marta (videographer); **Ledger** — Nora (steward: money, trust,
the project wallet). You are the Office. Four things you can do:
- **workforce_report** — what they've actually done: trust, current goals and
  real progress, the last recorded steps, what finished, what's running, what
  is waiting for his approval. ALWAYS call this before telling him how a team
  is doing. Never describe their work from memory, never estimate progress,
  and keep the distinction it gives you between a JUDGED step (a real review
  verdict) and a mechanical one — calling a render step "passed" would be a
  false claim about quality.
- **ask_agent** — put a question or instruction to one of them and relay their
  real answer. Use it freely: to find something out (ask Ruth to verify a
  quotation, Nora what things have cost), to pass on what Sheraj wants, or to
  hear how they'd approach something. He can read the exchange in the Colony
  tab. Never invent what an agent "would say" — ask them.
- **set_team_goal** / **brief_agent** — this is how you give a team what they
  need to do the work well. Both reach every prompt those agents run,
  pipeline work included, so write them as instructions to a colleague: what
  the work is for, who it's for, what to favour, what to avoid. When Sheraj
  gives you a goal or a standing preference that belongs to their work, put it
  there in your own words rather than keeping it to yourself. Free, immediate,
  and they do not start any work.
- **request_team_job** — ask a team to actually MAKE something (a quote card,
  a bookmark, a video project). This always queues for his approval and never
  starts on its own, because a run spends real money on artwork and review.
  Call it whenever he asks for one, put everything the team needs into the
  theme and detail, and tell him plainly that it's waiting for his OK. For
  cards you are NOT limited to one: pass count for however many he wants (up
  to 19 in a single run) and Ruth finds that many verified quotes before it
  queues, so one request covers the lot. Never tell him you can only do one at
  a time. Once he approves, it starts immediately and shows in the Pipeline
  tab with the team lit up in the Colony map — so say where to watch it, and
  never suggest he go and start it himself.

**Their records are not private the way yours are.** Everything you write into
a goal, a brief, a question or a job brief is stored in the workforce database
and visible in the Colony tab, so send only what the WORK needs. Never pass on
anything from your notes about his personal life, his health, his family, his
money or his messages — the code refuses the worst of it outright, but the
judgement is yours. Describe the work, not the man.

### Bahá'í dates — Trustworthiness rule
The "Bahá'í dates" list below comes from a hand-verified table (2026–2028).
Use ONLY those dates. If he asks about a date not shown or outside 2026–2028,
say you don't have it verified and point him to the official calendar at
https://www.bahai.org/action/devotional-life/calendar — never guess a Holy
Day or Feast date from memory.

## What you are not

You are a warm accountability partner and assistant — not a therapist, and you
never pretend to be one. You do not give clinical or medical advice. If a
conversation shows real crisis signals, gently and explicitly encourage Sheraj
to reach out to a human he trusts — a friend, family, his community, or a
professional — rather than handling it yourself.
"""


# ── Context builders ───────────────────────────────────────────────────────────

def _badi_context(today: date = None) -> str:
    today = today or date.today()
    if not badi_dates.covered(today):
        return ("### Bahá'í dates\n(none — today is outside the verified 2026–2028 table; "
                "refer him to the official calendar)")
    events = badi_dates.events_between(today, today + timedelta(days=40))
    lines = [f"- {e['date'].strftime('%a %d %b %Y')}: {e['name']}"
             + (" (work suspended)" if e["work_suspended"] else "")
             for e in events[:8]]
    return "### Bahá'í dates (verified table, next ~40 days)\n" + "\n".join(lines)


def _calendar_context() -> tuple[str, dict]:
    """
    Today/tomorrow at a glance + the E# reference map used to resolve her
    tool calls. Deliberately small — this is ambient awareness for natural
    chat flow ("what's today look like"), not her only view of the
    calendar. Anything else (later dates, past events, search by name) goes
    through the search_calendar tool (agents/secretary_tools.py) in the
    tool-calling loop below, which can reach any date range on demand.
    """
    from agents import gcal
    if not gcal.is_authorised():
        return "", {}
    try:
        events = gcal.list_events(days_ahead=2)[:10]
    except Exception:
        return "### Today & tomorrow at a glance\n(calendar temporarily unreachable)", {}
    if not events:
        return "### Today & tomorrow at a glance\n(none)", {}
    event_map, lines = {}, []
    for i, ev in enumerate(events, 1):
        ref = f"E{i}"
        event_map[ref] = ev
        when = ev["start"].replace("T", " ")[:16]
        own = "yours" if ev["editable_by_secretary"] else f"cal: {ev['calendar_name'] or 'other'}"
        loc = f" @ {ev['location']}" if ev["location"] else ""
        lines.append(f"- [{ref}] {when} | {ev['summary']}{loc} | {','.join(ev['tags'])} | ({own})")
    return "### Today & tomorrow at a glance\n" + "\n".join(lines), event_map


def _build_system_prompt() -> tuple[str, dict]:
    context_parts = [_SECRETARY_INSTRUCTIONS.strip()]
    custom = (store.get_setting("custom_instructions", "") or "").strip()
    if custom:
        context_parts.append("## Sheraj's personal instructions for you\n\n" + custom)
    known = [f"### Now\n{datetime.now().strftime('%A %d %B %Y, %H:%M')} (local time)"]

    notes = store.read_all_memory_notes()
    if notes:
        known.append(f"### Your saved notes about Sheraj\n{notes}")
    tasks = store.get_open_tasks()
    if tasks:
        lines = "\n".join(
            f"- [{t['id']}] {t['description']}" + (f" (due {t['due']})" if t["due"] else "")
            for t in tasks)
        known.append(f"### His open tasks\n{lines}")

    known.append(_badi_context())

    cal_ctx, event_map = _calendar_context()
    if cal_ctx:
        known.append(cal_ctx)

    reminders = store.get_pending_reminders()
    if reminders:
        lines = "\n".join(f"- [{r['id']}] {r['fire_at']}"
                          + (f" ({r['recurrence']})" if r["recurrence"] else "")
                          + f": {r['message']}" for r in reminders[:10])
        known.append(f"### Pending reminders\n{lines}")

    # ALWAYS stated, including when empty. Omitting the section when nothing is
    # pending left her with no ground truth at all, and she filled the gap from
    # earlier turns in the conversation — telling Sheraj three actions were
    # waiting when two had already been approved or declined (real, 2026-08-14).
    # A queue is exactly the kind of state that changes behind the conversation,
    # so the current list has to outrank anything she remembers saying.
    pending = store.get_pending_actions()
    if pending:
        lines = "\n".join(f"- [#{p['id']}] {p['description']}" for p in pending)
        known.append(
            "### Awaiting Sheraj's approval (he can reply 'approve <number>')\n"
            "This is the COMPLETE current queue. Anything not listed here has already "
            "been approved, declined or run — never describe it as still waiting.\n"
            + lines)
    else:
        known.append(
            "### Awaiting Sheraj's approval\nNothing is waiting right now. Everything "
            "queued earlier in this conversation has since been approved, declined or "
            "run — never tell him an older action is still waiting. (Anything you queue "
            "during THIS reply is new and does count.)")

    context_parts.append("## What you know\n\n" + "\n\n".join(known))
    return build_system_prompt("secretary", "assist",
                               extra_context="\n\n".join(context_parts)), event_map


# ── Pending-action execution (the ownership gate's second half) ────────────────

def execute_pending_action(action_id: int) -> str:
    """Run an approved action. Returns a short human-readable outcome."""
    from agents import gcal, gdrive, gdocs, gsheets, gmail, whatsapp
    action = store.get_pending_action(action_id)
    if not action or action["status"] != "pending":
        return f"Action #{action_id} is not pending."
    payload = json.loads(action["payload"])
    try:
        if action["kind"] == "workforce_job":
            # A real pipeline run she asked a team for. Its outcome is a job
            # that takes minutes, so return the launch confirmation and let the
            # Pipeline tab report the rest — same as any hand-started run.
            from agents import secretary_colony
            outcome = secretary_colony.run_approved_job(payload)
            store.resolve_pending_action(action_id, "done")
            store.add_notification("approval", f"Approved & started: {action['description'][:80]}")
            return outcome
        if action["kind"] == "event_update":
            gcal.update_event(payload["calendar_id"], payload["event_id"], **payload["fields"])
        elif action["kind"] == "event_delete":
            gcal.delete_event(payload["calendar_id"], payload["event_id"])
        elif action["kind"] == "gmail_send":
            gmail.send_message(payload["to"], payload["subject"], payload["body"],
                               cc=payload.get("cc"))
        elif action["kind"] == "whatsapp_send":
            whatsapp.send_best_effort(payload["to"], payload["body"])
        elif action["kind"] == "drive_write":
            # move_to_mine is queued by organize_drive_file when the file is
            # outside her sandbox (rule 24); resolve parent at approval time.
            if payload.get("action") == "move_to_mine":
                gdrive.move_file(payload["file_id"], gdrive.ensure_secretary_folder())
            else:
                gdrive.apply_write(payload)
        elif action["kind"] == "docs_write":
            gdocs.apply_write(payload)
        elif action["kind"] == "sheets_write":
            rows = payload.get("rows") or [payload["row"]]  # "row": pre-batch queue entries
            gsheets.append_rows(payload["spreadsheet_id"], rows,
                                payload.get("range_a1", "A1"))
        else:
            store.resolve_pending_action(action_id, "failed")
            return f"Unknown action kind: {action['kind']}"
        store.resolve_pending_action(action_id, "done")
        store.add_notification("approval", f"Approved & done: {action['description'][:80]}")
        return f"Done: {action['description']}"
    except Exception as e:
        store.resolve_pending_action(action_id, "failed")
        store.add_notification("scheduler_error", f"Approved action failed: {type(e).__name__}")
        return f"That didn't work ({type(e).__name__}): {action['description']}"


def _handle_approval_shortcut(text: str) -> str | None:
    """Deterministic approve/reject — runs BEFORE the LLM, no prompt in the loop."""
    m = _APPROVAL_RE.match(text)
    if not m:
        return None
    pending = store.get_pending_actions()
    if not pending:
        return None  # nothing pending — let the LLM answer normally
    verb, num = m.group(1).lower(), m.group(2)
    if num:
        target = next((p for p in pending if p["id"] == int(num)), None)
        if not target:
            return f"I don't have a pending action #{num}. Pending: " + \
                   "; ".join(f"#{p['id']} {p['description']}" for p in pending)
    elif len(pending) == 1:
        target = pending[0]
    else:
        return "Which one? " + "; ".join(f"#{p['id']} {p['description']}" for p in pending)
    if verb == "reject":
        store.resolve_pending_action(target["id"], "rejected")
        return f"Okay, cancelled: {target['description']}"
    return execute_pending_action(target["id"])


# ── Reply finalization ──────────────────────────────────────────────────────────

def _looks_like_uncommitted_action(reply: str) -> bool:
    return bool(_ACTION_VERB_RE.search(reply) and _ACTION_MARKER_RE.search(reply))


def _log_suspected_miss(raw_reply: str) -> None:
    """
    Best-effort diagnostic trail for "she said she did it but nothing
    happened": kept in private/ per the personal-data rule (CLAUDE.md #15)
    — never stdout, never a committed file.
    """
    try:
        path = store.PRIVATE_DIR / "secretary_tag_debug.log"
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n--- {datetime.now().isoformat()} ---\n{raw_reply}\n")
    except Exception:
        pass


def _finalize_reply(reply: str, effects: dict) -> str:
    """
    Actions are now real tool calls executed live during the agentic loop
    (secretary_tools.make_executor), not markup embedded in this text — so
    there is nothing left here to parse or strip. The one remaining check:
    a reply that READS like a commitment to act ("Adding that now") when she
    called NO TOOL AT ALL that turn — see _looks_like_uncommitted_action.

    "No tool at all" is the real condition, and `effects["tool_calls"]` is
    what makes it testable. Before 2026-08-14 this tested "no effect was
    recorded", which is a different thing: a read-only turn records no
    effect, so an ordinary lookup answer containing an action verb was
    flagged as an action that silently failed. A warning that fires when
    nothing is wrong teaches him to ignore the warning.
    """
    clean = re.sub(r"\n{3,}", "\n\n", reply).strip()
    if _looks_like_uncommitted_action(reply) and not any(effects.values()):
        _log_suspected_miss(reply)
        effects["errors"].append(
            "that sounded like I was taking an action, but no tool actually "
            "ran — nothing happened; ask me again")
    return clean


# An action she names by number, and the words that mean she is describing the
# approval queue. Used by _approval_ground_truth below.
_ACTION_REF_RE = re.compile(r"#(\d{1,6})\b")
_APPROVAL_WORD_RE = re.compile(r"\b(approv\w*|waiting|queued?|pending)\b", re.IGNORECASE)


def _approval_ground_truth(reply: str) -> str:
    """
    Correct, in code, any claim that a finished action is still waiting.

    The pending queue is stated in her system prompt, and she still listed two
    already-resolved actions as waiting because her OWN earlier reply in the
    conversation said so (real, 2026-08-14). A queue changes behind the
    conversation, so what she remembers saying about it is worth less than the
    table — and a wrong queue is not a cosmetic error: it invites Sheraj to
    approve something twice, which is exactly how a card got made twice.

    Same class as _ground_truth_confirmation: the record overrules the model.
    Silent when every number she named is genuinely pending, so a correct reply
    is never cluttered with a redundant footer.
    """
    if not _APPROVAL_WORD_RE.search(reply):
        return ""
    referenced = {int(n) for n in _ACTION_REF_RE.findall(reply)}
    if not referenced:
        return ""
    pending = store.get_pending_actions()
    live = {p["id"] for p in pending}
    stale = sorted(r for r in referenced if r not in live)
    if not stale:
        return ""
    named = ", ".join(f"#{s}" for s in stale)
    waiting = ", ".join(f"#{p['id']}" for p in pending)
    return (f"ℹ️ From the record: {named} {'is' if len(stale) == 1 else 'are'} NOT "
            "waiting — already approved, declined or run. "
            + (f"Waiting now: {waiting}." if pending else "Nothing is waiting now."))


def _ground_truth_confirmation(effects: dict) -> str:
    """
    A factual, code-authored status line for anything calendar/reminder-
    related — never the model's own guess about whether it worked. The
    model can't know the outcome when it writes its reply (the tags execute
    afterward), so trusting its wording here would repeat the same false
    hedge/false-confidence problem _sanitize_claims exists to prevent
    elsewhere in this codebase.
    """
    lines = []
    for e in effects["events"]:
        lines.append(f"✅ Calendar: {e}")
    for w in effects["workspace"]:
        lines.append(f"✅ Workspace: {w}")
    for w in effects["workforce"]:
        lines.append(f"✅ Workforce: {w}")
    for r in effects["reminders"]:
        lines.append(f"✅ Reminder set: {r}")
    for q in effects["queued_for_approval"]:
        lines.append(f"⏸️ Needs your approval: {q}")
    for err in effects["errors"]:
        lines.append(f"⚠️ Didn't go through — {err}")
    return "\n".join(lines)


# ── The conversation turn ──────────────────────────────────────────────────────

def _her_model() -> str | None:
    """
    The Claude model Sheraj picked for her in the Colony tab, or None for the
    ANTHROPIC_MODEL default. Fails open — a model registry that can't be read
    must never stop her answering. models.validate_choice guarantees at SAVE
    time that this can only ever be a Claude model (rule 16), so nothing here
    needs to re-police the provider.
    """
    try:
        from agents.colony import get_agent_settings
        return (get_agent_settings("secretary").get("model") or "").strip() or None
    except Exception:
        return None


def chat(user_message: str, channel: str = "dashboard") -> dict:
    store.init_db()
    store.add_message("user", user_message, channel=channel)

    # Deterministic approval path — no LLM between his "approve" and the action
    shortcut = _handle_approval_shortcut(user_message)
    if shortcut is not None:
        store.add_message("assistant", shortcut, channel=channel)
        return {"reply": shortcut, "remembered": [], "tasks_added": [], "actions": []}

    system, event_map = _build_system_prompt()
    # Owner thread only — guest WhatsApp turns must never enter Sheraj's context.
    history = store.get_recent_messages(HISTORY_WINDOW, thread="owner")
    messages = [{"role": m["role"], "content": m["content"]} for m in history]

    effects = {"remembered": [], "tasks_added": [], "events": [], "reminders": [],
               "workspace": [], "workforce": [], "queued_for_approval": [], "errors": [],
               "tool_calls": []}
    executor = secretary_tools.make_executor(event_map, effects)
    # max_tokens is per ROUND; bulk Workspace work (many rows in one
    # append_sheet_rows call) needs room for a large tool_use input block.
    raw_reply = call_claude_agentic(messages, system=system, tools=secretary_tools.ALL_TOOLS,
                                    executor=executor, max_tokens=4000, max_rounds=6,
                                    model=_her_model())
    reply = _finalize_reply(raw_reply, effects)

    # Ground truth wins: append a deterministic, code-authored confirmation
    # for anything calendar/reminder-related — never trust the model's own
    # guess about whether its action succeeded (it can't know yet).
    confirmation = _ground_truth_confirmation(effects)
    if confirmation:
        reply = (reply + "\n\n" + confirmation) if reply else confirmation
    correction = _approval_ground_truth(reply)
    if correction:
        reply += "\n\n" + correction
    if not reply:
        reply = "Noted."

    store.add_message("assistant", reply, channel=channel)
    actions = effects["events"] + effects["workspace"] + effects["workforce"] + \
        effects["reminders"] + \
        [f"queued for approval: {q}" for q in effects["queued_for_approval"]]
    return {"reply": reply, "remembered": effects["remembered"],
            "tasks_added": effects["tasks_added"], "actions": actions}


def guest_chat(contact: dict, user_message: str) -> dict:
    """
    Tool-less WhatsApp conversation with an allowlisted contact.

    Isolation (owner decision 2026-07-12 refining rule 27):
      - plain call_claude only — structurally no tools
      - no read_all_memory_notes / tasks / calendar / personal data
      - history is only this guest's own thread (sender = contact name)
      - Sheraj's owner chat() never sees these rows
    """
    name = contact["name"]
    store.init_db()
    store.add_message("user", user_message, channel="whatsapp", sender=name)

    guest_system = _GUEST_SYSTEM.format(name=name)
    history = store.get_recent_messages(HISTORY_WINDOW, thread=name)
    messages = [{"role": m["role"], "content": m["content"]} for m in history]

    reply = call_claude(messages, system=guest_system, model=_her_model())
    if not reply:
        reply = "Hi — I'll let Sheraj know you messaged."

    store.add_message("assistant", reply, channel="whatsapp", sender=name)
    # Title only — never message content in notifications (rule 15).
    store.add_notification(
        "whatsapp",
        f"{name} messaged Abigail on WhatsApp — see the Secretary tab")
    return {"reply": reply}


if __name__ == "__main__":
    import sys
    msg = " ".join(sys.argv[1:]) or "Hello! Introduce yourself in one sentence."
    result = chat(msg)
    print(result["reply"].encode("ascii", "replace").decode())
    for k in ("remembered", "tasks_added", "actions"):
        if result[k]:
            print(f"[{k}: {result[k]}]")
