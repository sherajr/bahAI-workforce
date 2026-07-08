"""
Google Calendar for the Secretary — calendar CRUD + the deterministic Bahá'í
event tagger. OAuth/token mechanics live in agents/google_auth.py (shared
across Calendar/Gmail/Drive/Docs/Sheets/Slides — one consent screen, one
token file); this module only ever imports get_valid_token/is_authorised/
_headers from there.

Ownership hard rule: she creates/edits/deletes freely ONLY on the
"bahAI Secretary" calendar she creates on first connect. Callers must gate any
write to another calendar behind Sheraj's explicit per-event confirmation —
`is_her_calendar()` is the check.

The tagger is keyword rules on the event's own title. It is NOT an LLM call.
Holy Day/Feast tags never come from date-coincidence alone — badi_dates.py
still supplies the verified date list surfaced separately in her prompt.
"""

import re
from datetime import date, datetime, timedelta
from typing import Optional

import requests
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=str(Path(__file__).parent.parent / ".env"))

from agents import secretary_store as store
from agents.google_auth import get_valid_token, is_authorised, _headers  # noqa: F401 (re-exported)

CAL_API = "https://www.googleapis.com/calendar/v3"

SECRETARY_CALENDAR_NAME = "bahAI Secretary"


# ── Her calendar ───────────────────────────────────────────────────────────────

def ensure_secretary_calendar() -> str:
    """Find or create the 'bahAI Secretary' calendar; cache its id in the private DB."""
    store.init_db()
    cal_id = store.get_setting("secretary_calendar_id")
    if cal_id:
        return cal_id
    for cal in list_calendars():
        if cal.get("summary") == SECRETARY_CALENDAR_NAME:
            store.set_setting("secretary_calendar_id", cal["id"])
            return cal["id"]
    resp = requests.post(f"{CAL_API}/calendars", headers=_headers(),
                         json={"summary": SECRETARY_CALENDAR_NAME,
                               "description": "Events created by Sheraj's bahAI Secretary."},
                         timeout=30)
    resp.raise_for_status()
    cal_id = resp.json()["id"]
    store.set_setting("secretary_calendar_id", cal_id)
    return cal_id


def her_calendar_id() -> Optional[str]:
    store.init_db()
    return store.get_setting("secretary_calendar_id")


def is_her_calendar(calendar_id: str) -> bool:
    """The ownership gate: True only for the calendar she created herself."""
    return bool(calendar_id) and calendar_id == her_calendar_id()


# ── Calendar API ───────────────────────────────────────────────────────────────

def list_calendars() -> list[dict]:
    resp = requests.get(f"{CAL_API}/users/me/calendarList", headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json().get("items", [])


def _merged_events(time_min: str, time_max: str, query: str = None,
                   max_results: int = 100) -> list[dict]:
    """
    Merged events across ALL his calendars for an arbitrary [time_min, time_max)
    window (read is unrestricted; only writes are gated). Each event gains
    calendar_id/calendar_name and deterministic tags. `query`, if given, is
    passed to Google's own full-text search (`q`) AND re-checked locally
    against summary/description — Google's `q` can match description/
    location/attendee fields loosely, so the local pass keeps results
    actually relevant to what was asked.
    """
    merged = []
    for cal in list_calendars():
        params = {"timeMin": time_min, "timeMax": time_max,
                  "singleEvents": "true", "orderBy": "startTime", "maxResults": max_results}
        if query:
            params["q"] = query
        resp = requests.get(
            f"{CAL_API}/calendars/{requests.utils.quote(cal['id'], safe='')}/events",
            headers=_headers(), params=params, timeout=30,
        )
        if resp.status_code != 200:
            continue  # a single broken calendar must not sink the merged view
        for ev in resp.json().get("items", []):
            ev["calendar_id"] = cal["id"]
            ev["calendar_name"] = cal.get("summary", "")
            merged.append(ev)
    merged.sort(key=lambda e: e.get("start", {}).get("dateTime") or e.get("start", {}).get("date") or "")
    slim = [_slim(ev) for ev in merged]
    if query:
        needle = query.strip().lower()
        slim = [ev for ev in slim
               if needle in ev["summary"].lower() or needle in ev.get("description", "").lower()]
    return slim[:max_results]


def list_events(days_ahead: int = 14) -> list[dict]:
    """
    Merged upcoming events across ALL his calendars, from now to now+days_ahead.
    """
    now = datetime.now().astimezone()
    time_min = now.isoformat()
    time_max = (now + timedelta(days=days_ahead)).isoformat()
    return _merged_events(time_min, time_max, max_results=100)


def search_events(start_date: str, end_date: str, query: str = None,
                  max_results: int = 60) -> list[dict]:
    """
    Arbitrary-range calendar search (past, future, or spanning both),
    optionally filtered by keyword — what the Secretary's search_calendar
    tool calls so she can actually look things up instead of being limited
    to the fixed "next N days" window `list_events` provides. Dates are
    YYYY-MM-DD, inclusive on both ends. Capped to 400 days to keep tool
    results bounded — a wider request is clamped, never rejected outright.
    """
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if end < start:
        start, end = end, start
    if (end - start).days > 400:
        end = start + timedelta(days=400)
    tz = datetime.now().astimezone().tzinfo
    time_min = datetime.combine(start, datetime.min.time(), tzinfo=tz).isoformat()
    time_max = (datetime.combine(end, datetime.min.time(), tzinfo=tz) + timedelta(days=1)).isoformat()
    return _merged_events(time_min, time_max, query=query, max_results=max_results)


def _to_local(iso: str | None) -> str | None:
    """Google returns dateTimes in UTC/calendar tz; show Sheraj HIS clock."""
    if not iso or "T" not in iso:
        return iso
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().isoformat()
    except ValueError:
        return iso


def _slim(ev: dict) -> dict:
    start = ev.get("start", {})
    end = ev.get("end", {})
    slim = {
        "id": ev.get("id"),
        "summary": ev.get("summary", "(no title)"),
        "start": _to_local(start.get("dateTime")) or start.get("date"),
        "end": _to_local(end.get("dateTime")) or end.get("date"),
        "all_day": "date" in start,
        "location": ev.get("location", ""),
        "description": ev.get("description", ""),
        "calendar_id": ev.get("calendar_id", ""),
        "calendar_name": ev.get("calendar_name", ""),
    }
    slim["tags"] = tag_event(slim)
    slim["editable_by_secretary"] = is_her_calendar(slim["calendar_id"])
    return slim


def _dt_block(iso: str) -> dict:
    """
    RFC3339 dateTime with an explicit UTC offset. Windows' tzinfo names
    ("Pacific Daylight Time") are not IANA ids and Google 400s on them, so we
    never send timeZone — a naive local time gets its numeric offset attached.
    """
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.astimezone()  # interpret as local time, attach +/-HH:MM
    return {"dateTime": dt.isoformat()}


def create_event(summary: str, start_iso: str, end_iso: str = None,
                 location: str = "", description: str = "",
                 calendar_id: str = None) -> dict:
    """Create an event. Defaults to HER calendar; callers gate anything else."""
    cal_id = calendar_id or ensure_secretary_calendar()
    body = {"summary": summary}
    if location:
        body["location"] = location
    if description:
        body["description"] = description
    if "T" in start_iso:
        if not end_iso:
            end_iso = (datetime.fromisoformat(start_iso) + timedelta(hours=1)).isoformat()
        body["start"] = _dt_block(start_iso)
        body["end"] = _dt_block(end_iso)
    else:  # all-day
        body["start"] = {"date": start_iso}
        body["end"] = {"date": end_iso or start_iso}
    resp = requests.post(
        f"{CAL_API}/calendars/{requests.utils.quote(cal_id, safe='')}/events",
        headers=_headers(), json=body, timeout=30)
    resp.raise_for_status()
    ev = resp.json()
    ev["calendar_id"] = cal_id
    return _slim(ev)


def update_event(calendar_id: str, event_id: str, **fields) -> dict:
    """
    PATCH an event. Ownership gating happens in the caller (secretary/api).
    Raises ValueError if no recognized field was supplied — an empty PATCH
    body is a silent no-op that Google answers with 200, which would
    otherwise look identical to a real update succeeding.
    """
    body = {}
    if "summary" in fields and fields["summary"]:
        body["summary"] = fields["summary"]
    if "location" in fields and fields["location"] is not None:
        body["location"] = fields["location"]
    if "description" in fields and fields["description"] is not None:
        body["description"] = fields["description"]
    if fields.get("start_iso"):
        body["start"] = (_dt_block(fields["start_iso"])
                         if "T" in fields["start_iso"] else {"date": fields["start_iso"]})
    if fields.get("end_iso"):
        body["end"] = (_dt_block(fields["end_iso"])
                       if "T" in fields["end_iso"] else {"date": fields["end_iso"]})
    if not body:
        raise ValueError("update_event called with no recognized fields to change")
    resp = requests.patch(
        f"{CAL_API}/calendars/{requests.utils.quote(calendar_id, safe='')}/events/{event_id}",
        headers=_headers(), json=body, timeout=30)
    resp.raise_for_status()
    ev = resp.json()
    ev["calendar_id"] = calendar_id
    return _slim(ev)


def delete_event(calendar_id: str, event_id: str):
    resp = requests.delete(
        f"{CAL_API}/calendars/{requests.utils.quote(calendar_id, safe='')}/events/{event_id}",
        headers=_headers(), timeout=30)
    if resp.status_code not in (200, 204, 410):
        resp.raise_for_status()


# ── Deterministic Bahá'í tagger (keyword rules + badi_dates — never an LLM) ────

_CORE_ACTIVITY_RE = re.compile(
    r"devotional|study circle|ruhi|children'?s class|junior youth|\bjy\b|home visit|firesides?",
    re.IGNORECASE)
_INSTITUTIONAL_RE = re.compile(
    r"\blsa\b|spiritual assembly|cluster|reflection (gathering|meeting)|unit convention|"
    r"national convention|training institute|institute campaign|assembly meeting",
    re.IGNORECASE)
_FEAST_RE = re.compile(r"nineteen day feast|19[- ]day feast|\bfeast\b", re.IGNORECASE)
_HOLY_DAY_RE = re.compile(
    r"holy day|naw[- ]?r[uú]z|ri[dḍ]v[aá]n|declaration of the b[aá]b|ascension of|"
    r"martyrdom of the b[aá]b|birth of the b[aá]b|birth of bah|day of the covenant",
    re.IGNORECASE)
_PROFESSIONAL_RE = re.compile(
    r"\bwork\b|\bshift\b|client|business|etsy|bahai workforce|bahAI", re.IGNORECASE)


def _event_date(slim: dict) -> Optional[date]:
    try:
        return datetime.fromisoformat(slim["start"].replace("Z", "+00:00")).date() \
            if "T" in slim["start"] else date.fromisoformat(slim["start"])
    except Exception:
        return None


def tag_event(slim: dict) -> list[str]:
    """
    Deterministic tags, most specific first. A Holy Day/Feast tag requires the
    event's OWN title to say so (_HOLY_DAY_RE / _FEAST_RE) — matching by date
    alone would tag every unrelated personal event that happens to fall on a
    Holy Day (e.g. an exercise class on Martyrdom of the Báb) as itself being
    the observance, which misrepresents his calendar. badi_dates still drives
    the separate "Bahá'í dates" list in her prompt regardless of this tag.
    """
    title = slim.get("summary", "")
    tags = []
    if _HOLY_DAY_RE.search(title):
        tags.append("holy_day")
    if _FEAST_RE.search(title):
        tags.append("feast")
    if _CORE_ACTIVITY_RE.search(title):
        tags.append("core_activity")
    if _INSTITUTIONAL_RE.search(title) or "feast" in tags:
        if "institutional" not in tags:
            tags.append("institutional")
    if not tags:
        tags.append("professional" if _PROFESSIONAL_RE.search(title) else "personal")
    return tags
