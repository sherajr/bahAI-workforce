"""
The Secretary — Sheraj's personal assistant, powered by Claude Sonnet.

Phase 1: conversation with durable memory (private/memory/*.md + task list).
Phase 2: Google Calendar (create free on HER calendar, gated elsewhere),
         Bahá'í awareness from the hand-verified badi_dates table, and
         reminders fired by agents/scheduler.py.

Privacy (CLAUDE.md hard rules): everything she knows lives in private/ via
secretary_store. This module must never write personal content to
workforce.db, log_run, or job progress strings.

All side effects are DETERMINISTIC INTENTS: she emits tags, code parses and
executes them. Calendar ownership (hard rule): any write to a calendar other
than "bahAI Secretary" is queued as a pending action requiring Sheraj's
explicit approval — the gate is code, not prompt compliance.
"""

import json
import re
from datetime import date, datetime, timedelta

from agents import badi_dates
from agents.router import call_claude
from agents.system_prompt_builder import build_system_prompt
from agents import secretary_store as store

HISTORY_WINDOW = 20

# Deterministic intent tags (parsed then stripped before display). Attribute
# order is never assumed — every tag's attributes are parsed with _ATTR_RE so
# the model can write them in any order and it still works.
_REMEMBER_RE = re.compile(r'<remember(?:\s+note="([^"]*)")?\s*>(.*?)</remember>', re.DOTALL)
_TASK_RE = re.compile(r'<task(?:\s+due="([^"]*)")?\s*>(.*?)</task>', re.DOTALL)
_EVENT_RE = re.compile(r'<event\s+([^>]*?)>(.*?)</event>', re.DOTALL)
_EVENT_UPDATE_RE = re.compile(r'<event_update\s+([^>]*?)\s*/?>')
_EVENT_DELETE_RE = re.compile(r'<event_delete\s+([^>]*?)\s*/?>')
_REMIND_RE = re.compile(
    r'<remind\s+at="([^"]+)"(?:\s+recurrence="([^"]*)")?(?:\s+wake_me="([^"]*)")?\s*>(.*?)</remind>',
    re.DOTALL)
_REMIND_EVENT_RE = re.compile(r'<remind_event\s+ref="([^"]+)"\s+offsets="([^"]*)"\s*/?>')
_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')
# Defensive cleanup: if the model ever writes event_update/event_delete as a
# container (inner text + closing tag) despite the instructions, the main
# regex above only strips the opening tag — this mops up the stray remainder
# so raw markup never leaks into what Sheraj sees.
_STRAY_CLOSE_RE = re.compile(r'</event_update>|</event_delete>')

# Chat-approval of pending actions — handled deterministically BEFORE the LLM
_APPROVAL_RE = re.compile(r'^\s*(approve|confirm|reject)(?:\s+#?(\d+))?\s*[.!]?\s*$', re.IGNORECASE)

_SECRETARY_INSTRUCTIONS = """
## How you work

- You are talking with Sheraj himself, in a private one-to-one chat. Be warm,
  brief, and natural — a trusted assistant, not a form or a chatbot.
- To act, include intent tags in your reply. They are invisible to Sheraj —
  still acknowledge what you did naturally in your own words. Never invent a
  tag format that isn't listed here.

### Memory and tasks
- Durable fact he tells you:  <remember note="topic_name">the fact</remember>
- Track a to-do:  <task due="YYYY-MM-DD">what to do</task>  (omit due if none)

### Calendar (only when "Upcoming events" appears below — otherwise say
Google Calendar isn't connected yet and point him to the Secretary tab setup)
- When he asks you to create, change, or delete an event, ALWAYS include the
  matching tag in that same reply — every single time, regardless of what
  happened earlier in this conversation. A past failure is not evidence the
  current attempt will fail; the connection may have been fixed since. Do
  not skip the tag, discuss whether it will work, or ask him to verify for
  you — including the tag correctly IS the whole job.
- Never state or guess whether the action succeeded, failed, or duplicated
  something — you don't know the outcome when you write your reply, so
  don't speculate about it either way (not "done", not "I can't confirm").
  Just say what you're doing, once, briefly ("Adding that now…"). The
  system checks the real result and tells him definitively right after
  your reply.
- Create an event (goes on YOUR calendar, "bahAI Secretary"). Attributes can
  appear in any order; the title is the text between the tags, never an
  attribute:
  <event start="YYYY-MM-DDTHH:MM" end="YYYY-MM-DDTHH:MM" location="optional" description="optional">Title</event>
  All-day: use start="YYYY-MM-DD" with no time.
- Change an existing event — reference it by its [E#] id from the upcoming
  list. This tag is ALWAYS self-closing (ends in "/>") with every field as an
  attribute, INCLUDING the title (unlike creating a new event, there is no
  inner text here and no separate closing tag):
  <event_update ref="E3" summary="new title" start="..." end="..." location="..." description="..."/>
  Include only the attributes that change. There is no "color" attribute —
  Google Calendar colors aren't controllable through this connection; say so
  plainly if asked and suggest he set it himself in Google Calendar.
- Delete an event: <event_delete ref="E3"/>
  RULE: events marked (yours) apply immediately. Events on any other calendar
  are ALWAYS queued for Sheraj's approval — when you edit one, tell him it
  needs his approval (he replies "approve" or taps Approve in the dashboard).
- Adjust an event's reminder lead-times: <remind_event ref="E3" offsets="120,30"/>
  (minutes before start; offsets="" turns them off)

### Reminders
- <remind at="YYYY-MM-DD HH:MM" recurrence="" wake_me="false">message</remind>
  recurrence: "" (one-off), "daily", "weekly", or "every:Nd" (e.g. every:19d).
  wake_me="true" only if he explicitly says to override quiet hours.
- Default lead-times already exist: 60+15 min before located events, and an
  evening-before nudge for Holy Days and Feasts. Don't duplicate those.

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
    """Upcoming events + the E# reference map used to resolve her intents."""
    from agents import gcal
    if not gcal.is_authorised():
        return "", {}
    try:
        events = gcal.list_events(days_ahead=14)[:25]
    except Exception:
        return "### Upcoming events\n(calendar temporarily unreachable)", {}
    if not events:
        return "### Upcoming events (next 14 days)\n(none)", {}
    event_map, lines = {}, []
    for i, ev in enumerate(events, 1):
        ref = f"E{i}"
        event_map[ref] = ev
        when = ev["start"].replace("T", " ")[:16]
        own = "yours" if ev["editable_by_secretary"] else f"cal: {ev['calendar_name'] or 'other'}"
        loc = f" @ {ev['location']}" if ev["location"] else ""
        lines.append(f"- [{ref}] {when} | {ev['summary']}{loc} | {','.join(ev['tags'])} | ({own})")
    return "### Upcoming events (next 14 days)\n" + "\n".join(lines), event_map


def _build_system_prompt() -> tuple[str, dict]:
    context_parts = [_SECRETARY_INSTRUCTIONS.strip()]
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

    pending = store.get_pending_actions()
    if pending:
        lines = "\n".join(f"- [#{p['id']}] {p['description']}" for p in pending)
        known.append("### Awaiting Sheraj's approval (he can reply 'approve <number>')\n" + lines)

    context_parts.append("## What you know\n\n" + "\n\n".join(known))
    return build_system_prompt("secretary", "assist",
                               extra_context="\n\n".join(context_parts)), event_map


# ── Pending-action execution (the ownership gate's second half) ────────────────

def execute_pending_action(action_id: int) -> str:
    """Run an approved action. Returns a short human-readable outcome."""
    from agents import gcal
    action = store.get_pending_action(action_id)
    if not action or action["status"] != "pending":
        return f"Action #{action_id} is not pending."
    payload = json.loads(action["payload"])
    try:
        if action["kind"] == "event_update":
            gcal.update_event(payload["calendar_id"], payload["event_id"], **payload["fields"])
        elif action["kind"] == "event_delete":
            gcal.delete_event(payload["calendar_id"], payload["event_id"])
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


# ── Intent application ─────────────────────────────────────────────────────────

def _apply_intents(reply: str, event_map: dict) -> tuple[str, dict]:
    from agents import gcal
    effects = {"remembered": [], "tasks_added": [], "events": [], "reminders": [],
               "queued_for_approval": [], "errors": []}

    for m in _REMEMBER_RE.finditer(reply):
        note, fact = (m.group(1) or "general"), m.group(2).strip()
        if fact:
            store.write_memory_note(note, fact)
            effects["remembered"].append(fact)

    for m in _TASK_RE.finditer(reply):
        due, desc = m.group(1), m.group(2).strip()
        if desc:
            store.add_task(desc, due=due or None)
            effects["tasks_added"].append(desc)

    for m in _EVENT_RE.finditer(reply):
        attrs = dict(_ATTR_RE.findall(m.group(1)))
        summary = m.group(2).strip()
        start = attrs.get("start")
        if not start:
            effects["errors"].append(f"event tag for '{summary}' is missing start")
            continue
        try:
            ev = gcal.create_event(summary, start, end_iso=attrs.get("end") or None,
                                   location=attrs.get("location", ""),
                                   description=attrs.get("description", ""))
            effects["events"].append(f"created: {ev['summary']} at {ev['start']}")
        except Exception as e:
            effects["errors"].append(f"couldn't create '{summary}': {type(e).__name__}")

    for m in _EVENT_UPDATE_RE.finditer(reply):
        attrs = dict(_ATTR_RE.findall(m.group(1)))
        ref = attrs.pop("ref", "")
        ev = event_map.get(ref)
        if not ev:
            effects["errors"].append(f"unknown event ref {ref}")
            continue
        fields = {"summary": attrs.get("summary"), "location": attrs.get("location"),
                  "description": attrs.get("description"),
                  "start_iso": attrs.get("start"), "end_iso": attrs.get("end")}
        fields = {k: v for k, v in fields.items() if v is not None}
        if not fields:
            effects["errors"].append(f"update for '{ev['summary']}' had no recognized fields "
                                     "(check the tag has real attributes, e.g. description=\"...\")")
            continue
        desc = f"Update '{ev['summary']}' ({ev['calendar_name'] or 'calendar'}): " + \
               ", ".join(f"{k}={v}" for k, v in fields.items())
        if ev["editable_by_secretary"]:
            try:
                gcal.update_event(ev["calendar_id"], ev["id"], **fields)
                effects["events"].append(f"updated: {ev['summary']} ({', '.join(fields)})")
            except Exception as e:
                effects["errors"].append(f"couldn't update '{ev['summary']}': {type(e).__name__}")
        else:
            aid = store.add_pending_action("event_update", desc, json.dumps(
                {"calendar_id": ev["calendar_id"], "event_id": ev["id"], "fields": fields}))
            effects["queued_for_approval"].append(f"#{aid} {desc}")

    for m in _EVENT_DELETE_RE.finditer(reply):
        attrs = dict(_ATTR_RE.findall(m.group(1)))
        ev = event_map.get(attrs.get("ref", ""))
        if not ev:
            effects["errors"].append(f"unknown event ref {attrs.get('ref', '')}")
            continue
        if ev["editable_by_secretary"]:
            try:
                gcal.delete_event(ev["calendar_id"], ev["id"])
                effects["events"].append(f"deleted: {ev['summary']}")
            except Exception as e:
                effects["errors"].append(f"couldn't delete '{ev['summary']}': {type(e).__name__}")
        else:
            desc = f"Delete '{ev['summary']}' from {ev['calendar_name'] or 'his calendar'}"
            aid = store.add_pending_action("event_delete", desc, json.dumps(
                {"calendar_id": ev["calendar_id"], "event_id": ev["id"]}))
            effects["queued_for_approval"].append(f"#{aid} {desc}")

    for m in _REMIND_RE.finditer(reply):
        at, recurrence, wake_me, msg = m.group(1), m.group(2) or "", m.group(3) or "", m.group(4).strip()
        try:
            fire_at = datetime.fromisoformat(at.replace(" ", "T", 1) if "T" not in at else at)
            store.add_reminder(msg, fire_at.strftime("%Y-%m-%d %H:%M:%S"),
                               recurrence=recurrence or None,
                               wake_me=wake_me.lower() == "true")
            effects["reminders"].append(f"{msg} at {at}" + (f" ({recurrence})" if recurrence else ""))
        except ValueError:
            effects["errors"].append(f"bad reminder time: {at}")

    for m in _REMIND_EVENT_RE.finditer(reply):
        ev = event_map.get(m.group(1))
        if ev:
            store.set_event_override(ev["id"], m.group(2))
            effects["reminders"].append(f"lead-times for '{ev['summary']}' -> {m.group(2) or 'off'}")
        else:
            effects["errors"].append(f"unknown event ref {m.group(1)}")

    clean = reply
    for rx in (_REMEMBER_RE, _TASK_RE, _EVENT_RE, _EVENT_UPDATE_RE, _EVENT_DELETE_RE,
               _REMIND_RE, _REMIND_EVENT_RE):
        clean = rx.sub("", clean)
    clean = _STRAY_CLOSE_RE.sub("", clean)  # mop up a stray closing tag if the model still writes one
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, effects


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
    for r in effects["reminders"]:
        lines.append(f"✅ Reminder set: {r}")
    for q in effects["queued_for_approval"]:
        lines.append(f"⏸️ Needs your approval: {q}")
    for err in effects["errors"]:
        lines.append(f"⚠️ Didn't go through — {err}")
    return "\n".join(lines)


# ── The conversation turn ──────────────────────────────────────────────────────

def chat(user_message: str, channel: str = "dashboard") -> dict:
    store.init_db()
    store.add_message("user", user_message, channel=channel)

    # Deterministic approval path — no LLM between his "approve" and the action
    shortcut = _handle_approval_shortcut(user_message)
    if shortcut is not None:
        store.add_message("assistant", shortcut, channel=channel)
        return {"reply": shortcut, "remembered": [], "tasks_added": [], "actions": []}

    system, event_map = _build_system_prompt()
    history = store.get_recent_messages(HISTORY_WINDOW)
    messages = [{"role": m["role"], "content": m["content"]} for m in history]

    raw_reply = call_claude(messages, system=system, max_tokens=1500)
    reply, effects = _apply_intents(raw_reply, event_map)

    # Ground truth wins: append a deterministic, code-authored confirmation
    # for anything calendar/reminder-related — never trust the model's own
    # guess about whether its action succeeded (it can't know yet).
    confirmation = _ground_truth_confirmation(effects)
    if confirmation:
        reply = (reply + "\n\n" + confirmation) if reply else confirmation
    if not reply:
        reply = "Noted."

    store.add_message("assistant", reply, channel=channel)
    actions = effects["events"] + effects["reminders"] + \
        [f"queued for approval: {q}" for q in effects["queued_for_approval"]]
    return {"reply": reply, "remembered": effects["remembered"],
            "tasks_added": effects["tasks_added"], "actions": actions}


if __name__ == "__main__":
    import sys
    msg = " ".join(sys.argv[1:]) or "Hello! Introduce yourself in one sentence."
    result = chat(msg)
    print(result["reply"].encode("ascii", "replace").decode())
    for k in ("remembered", "tasks_added", "actions"):
        if result[k]:
            print(f"[{k}: {result[k]}]")
