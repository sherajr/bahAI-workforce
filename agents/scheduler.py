"""
The Secretary's scheduler — a daemon thread started on FastAPI startup.

Ticks every ~30s and:
  1. fires due one-off/recurring reminders from private/secretary.db,
  2. schedules lead-time reminders for upcoming calendar events
     (defaults: 60 + 15 min before events with a location; evening-before
     19:00 for Holy Days and Feasts; per-event overrides via chat),
  3. respects quiet hours (default 22:30-07:30): non-wake_me reminders HOLD
     and deliver when quiet hours end — never lost, never fired at 2am.

All state lives in the private DB, so restarts lose nothing (hard rule).
Every fire and every failure is surfaced as a notification the dashboard
turns into an Activity Log entry — event names only, never check-in content
(hard rule 8: a silently-dead scheduler is the Canva bug all over again).

Delivery: a notification row + an assistant chat message, always (Phase 2).
Phase 3 adds a WhatsApp send to Sheraj's own number on top of the same fire
path, best-effort — a WhatsApp failure (not connected, API error) never
blocks the dashboard delivery, since the dashboard notification is the
hard guarantee (hard rule 8: a fire must never vanish silently).
"""

import json
import threading
import traceback
from datetime import datetime, timedelta, time as dtime

from agents import secretary_store as store

TICK_SECONDS = 30
_CAL_REFRESH_SECONDS = 300          # how often we rescan Google Calendar
DEFAULT_OFFSETS_MIN = [60, 15]      # lead times for located events
EVENING_BEFORE_HOUR = 19            # 7pm the evening before Holy Day / Feast

_thread: threading.Thread | None = None
_stop = threading.Event()
_last_cal_scan = datetime.min


def _now() -> datetime:
    return datetime.now()


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ── Quiet hours ────────────────────────────────────────────────────────────────

def _quiet_window() -> tuple[dtime, dtime]:
    raw = store.get_setting("quiet_hours", "22:30-07:30") or "22:30-07:30"
    try:
        start_s, end_s = raw.split("-")
        h1, m1 = map(int, start_s.strip().split(":"))
        h2, m2 = map(int, end_s.strip().split(":"))
        return dtime(h1, m1), dtime(h2, m2)
    except Exception:
        return dtime(22, 30), dtime(7, 30)


def in_quiet_hours(now: datetime = None) -> bool:
    now = now or _now()
    start, end = _quiet_window()
    t = now.time()
    if start <= end:            # window inside one day (unusual but valid)
        return start <= t < end
    return t >= start or t < end  # crosses midnight (the normal case)


# ── Recurrence ("daily" | "weekly" | "every:<N>d") ─────────────────────────────

def next_occurrence(fire_at: datetime, recurrence: str) -> datetime | None:
    rec = (recurrence or "").strip().lower()
    step = None
    if rec == "daily":
        step = timedelta(days=1)
    elif rec == "weekly":
        step = timedelta(days=7)
    elif rec.startswith("every:") and rec.endswith("d"):
        try:
            step = timedelta(days=max(1, int(rec[6:-1])))
        except ValueError:
            return None
    if not step:
        return None
    nxt = fire_at + step
    now = _now()
    while nxt <= now:           # catch up after downtime without firing N times
        nxt += step
    return nxt


# ── Delivery (dashboard always; WhatsApp to Sheraj's own number if connected) ──

def _deliver(title: str, kind: str = "reminder"):
    store.add_notification(kind, title)
    store.add_message("assistant", f"⏰ Reminder: {title}", channel="dashboard")
    try:
        from agents import whatsapp
        if whatsapp.is_configured():
            whatsapp.send_best_effort(whatsapp.WHATSAPP_OWNER_NUMBER, f"⏰ Reminder: {title}")
    except Exception as e:
        # Best-effort only — the dashboard delivery above already happened,
        # so a WhatsApp hiccup is a notice, not a lost reminder.
        store.add_notification("scheduler_error", f"WhatsApp delivery failed: {type(e).__name__}")


# ── Tick parts ─────────────────────────────────────────────────────────────────

def _fire_due_reminders():
    now = _now()
    quiet = in_quiet_hours(now)
    for rem in store.get_due_reminders(_fmt(now)):
        if quiet and not rem["wake_me"]:
            continue  # hold: stays unfired, delivers when quiet hours end
        try:
            _deliver(rem["message"])
            nxt = next_occurrence(datetime.fromisoformat(rem["fire_at"]), rem["recurrence"])
            if nxt:
                store.reschedule_reminder(rem["id"], _fmt(nxt))
            else:
                store.mark_reminder_fired(rem["id"])
        except Exception as e:
            store.add_notification("scheduler_error", f"Reminder failed: {type(e).__name__}")


def _scan_calendar():
    """Turn upcoming tagged events into one-off reminder fires (deduped)."""
    from agents import gcal
    if not gcal.is_authorised():
        return
    now = _now()
    for ev in gcal.list_events(days_ahead=2):
        try:
            _schedule_event_reminders(ev, now)
        except Exception:
            store.add_notification("scheduler_error",
                                   f"Event reminder failed: {ev.get('summary', '?')[:60]}")


def _schedule_event_reminders(ev: dict, now: datetime):
    start_raw = ev.get("start") or ""
    quiet = in_quiet_hours(now)

    # Evening-before for Holy Days and Feasts (all-day or timed)
    if "holy_day" in ev["tags"] or "feast" in ev["tags"]:
        day = datetime.fromisoformat(start_raw.replace("Z", "+00:00")).date() \
            if "T" in start_raw else datetime.fromisoformat(start_raw).date()
        eve = datetime.combine(day - timedelta(days=1), dtime(EVENING_BEFORE_HOUR, 0))
        key = f"{ev['id']}:eve"
        if eve <= now < eve + timedelta(hours=27) and not store.event_reminder_already_fired(key):
            if not quiet:
                _deliver(f"Tomorrow: {ev['summary']}")
                store.mark_event_reminder_fired(key)

    # Lead-time reminders for timed events with a location (default 60 + 15 min)
    if "T" not in start_raw:
        return
    start = datetime.fromisoformat(start_raw.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    override = store.get_event_override(ev["id"])
    if override is not None:
        offsets = [int(x) for x in override.split(",") if x.strip().isdigit()]
    elif ev.get("location"):
        offsets = DEFAULT_OFFSETS_MIN
    else:
        offsets = []
    for minutes in offsets:
        at = start - timedelta(minutes=minutes)
        key = f"{ev['id']}:{minutes}m"
        if at <= now < start and not store.event_reminder_already_fired(key):
            title = (f"In {minutes} min: {ev['summary']}"
                     + (f" @ {ev['location']}" if ev.get("location") else ""))
            if quiet:
                # Don't drop: hold via the normal reminders table so
                # _fire_due_reminders delivers after quiet hours (same path
                # as chat-created reminders). Mark the event key now so the
                # next calendar scan never enqueues a duplicate.
                store.add_reminder(title, _fmt(now), wake_me=False)
                store.mark_event_reminder_fired(key)
            else:
                _deliver(title)
                store.mark_event_reminder_fired(key)


def _tick():
    global _last_cal_scan
    _fire_due_reminders()
    if (_now() - _last_cal_scan).total_seconds() >= _CAL_REFRESH_SECONDS:
        _last_cal_scan = _now()
        _scan_calendar()


# ── Lifecycle ──────────────────────────────────────────────────────────────────

def _run():
    store.init_db()
    store.add_notification("scheduler", "Scheduler started")
    while not _stop.wait(TICK_SECONDS):
        try:
            _tick()
        except Exception as e:
            # Failures must surface, never vanish (hard rule 8)
            try:
                store.add_notification("scheduler_error", f"Scheduler tick failed: {type(e).__name__}")
            except Exception:
                traceback.print_exc()


def start():
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_run, name="secretary-scheduler", daemon=True)
    _thread.start()


def stop():
    _stop.set()
