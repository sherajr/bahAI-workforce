"""
Google Calendar for the Secretary — OAuth + calendar CRUD + the deterministic
Bahá'í event tagger.

Mirrors etsy.py's raw-requests OAuth pattern (no Google SDK): PKCE + localhost
callback, token stored in private/google_token.json (hard rule: everything
personal lives in private/).

Ownership hard rule: she creates/edits/deletes freely ONLY on the
"bahAI Secretary" calendar she creates on first connect. Callers must gate any
write to another calendar behind Sheraj's explicit per-event confirmation —
`is_her_calendar()` is the check.

The tagger is keyword rules on the event's own title. It is NOT an LLM call.
Holy Day/Feast tags never come from date-coincidence alone — badi_dates.py
still supplies the verified date list surfaced separately in her prompt.

Env (.env): GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET — a "Desktop app" or "Web
application" OAuth client from Google Cloud Console with
http://localhost:8765/gcal/oauth/callback as an authorized redirect URI.
"""

import base64
import hashlib
import json
import os
import re
import secrets
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=str(Path(__file__).parent.parent / ".env"))

from agents import secretary_store as store

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
CAL_API = "https://www.googleapis.com/calendar/v3"
REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8765/gcal/oauth/callback")
SCOPE = "https://www.googleapis.com/auth/calendar"

TOKEN_FILE = store.PRIVATE_DIR / "google_token.json"
PKCE_STATE_FILE = store.PRIVATE_DIR / "google_pkce_state.json"

SECRETARY_CALENDAR_NAME = "bahAI Secretary"


# ── PKCE + token storage (etsy.py pattern) ─────────────────────────────────────

def _generate_pkce() -> dict:
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return {"code_verifier": code_verifier, "code_challenge": code_challenge}


def _load_token() -> Optional[dict]:
    if TOKEN_FILE.exists():
        return json.loads(TOKEN_FILE.read_text())
    return None


def _save_token(token_data: dict) -> None:
    store.PRIVATE_DIR.mkdir(exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(token_data, indent=2))


def _refresh_token(refresh_token: str) -> dict:
    resp = requests.post(
        GOOGLE_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "refresh_token",
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    data["refresh_token"] = refresh_token  # Google omits it on refresh
    data["expires_at"] = time.time() + data.get("expires_in", 3600)
    _save_token(data)
    return data


def get_valid_token() -> str:
    data = _load_token()
    if not data:
        raise RuntimeError(
            "Google Calendar not connected. "
            "Visit http://localhost:8765/gcal/oauth/start to connect."
        )
    if time.time() >= data.get("expires_at", 0) - 300:
        data = _refresh_token(data["refresh_token"])
    return data["access_token"]


def is_authorised() -> bool:
    try:
        get_valid_token()
        return True
    except Exception:
        return False


# ── OAuth flow ─────────────────────────────────────────────────────────────────

def build_auth_url() -> str:
    if not GOOGLE_CLIENT_ID:
        raise RuntimeError("GOOGLE_CLIENT_ID not set in .env")
    pkce = _generate_pkce()
    state = secrets.token_urlsafe(16)
    store.PRIVATE_DIR.mkdir(exist_ok=True)
    PKCE_STATE_FILE.write_text(json.dumps({
        "code_verifier": pkce["code_verifier"],
        "state": state,
    }))
    from urllib.parse import urlencode
    return f"{GOOGLE_AUTH_URL}?" + urlencode({
        "response_type": "code",
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "state": state,
        "code_challenge": pkce["code_challenge"],
        "code_challenge_method": "S256",
        "access_type": "offline",   # refresh token
        "prompt": "consent",        # force refresh token even on re-consent
    })


def exchange_code(code: str, state: str) -> dict:
    if not PKCE_STATE_FILE.exists():
        raise RuntimeError("No PKCE state found. Restart the OAuth flow.")
    saved = json.loads(PKCE_STATE_FILE.read_text())
    if saved["state"] != state:
        raise ValueError("OAuth state mismatch — possible CSRF. Restart the flow.")

    resp = requests.post(
        GOOGLE_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "authorization_code",
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": REDIRECT_URI,
            "code": code,
            "code_verifier": saved["code_verifier"],
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    data["expires_at"] = time.time() + data.get("expires_in", 3600)
    _save_token(data)
    PKCE_STATE_FILE.unlink(missing_ok=True)
    ensure_secretary_calendar()
    return data


def _headers() -> dict:
    return {"Authorization": f"Bearer {get_valid_token()}",
            "Content-Type": "application/json"}


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


def list_events(days_ahead: int = 14) -> list[dict]:
    """
    Merged upcoming events across ALL his calendars (read is unrestricted;
    only writes are gated). Each event gains calendar_id/calendar_name and
    deterministic tags.
    """
    now = datetime.now().astimezone()
    time_min = now.isoformat()
    time_max = (now + timedelta(days=days_ahead)).isoformat()
    merged = []
    for cal in list_calendars():
        resp = requests.get(
            f"{CAL_API}/calendars/{requests.utils.quote(cal['id'], safe='')}/events",
            headers=_headers(),
            params={"timeMin": time_min, "timeMax": time_max,
                    "singleEvents": "true", "orderBy": "startTime", "maxResults": 100},
            timeout=30,
        )
        if resp.status_code != 200:
            continue  # a single broken calendar must not sink the merged view
        for ev in resp.json().get("items", []):
            ev["calendar_id"] = cal["id"]
            ev["calendar_name"] = cal.get("summary", "")
            merged.append(ev)
    merged.sort(key=lambda e: e.get("start", {}).get("dateTime") or e.get("start", {}).get("date") or "")
    return [_slim(ev) for ev in merged]


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
