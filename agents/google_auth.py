"""
Shared Google OAuth for the Secretary — Calendar, Gmail, Drive, Docs, Sheets,
and read-only Slides all go through ONE consent screen and ONE token file.

Mirrors etsy.py's raw-requests OAuth pattern (no Google SDK): PKCE +
localhost callback, token stored in private/google_token.json (hard rule:
everything personal lives in private/).

Why one shared token instead of per-service tokens: it's the same Google
account, the same local process, and the same trust boundary — splitting
into per-service OAuth flows and state files would multiply consent screens
and files on disk for zero real security benefit. Domain modules (gcal.py,
gmail.py, gdrive.py, gdocs.py, gsheets.py, gslides.py) import get_valid_token/
_headers from here and own their own API calls and business logic.

Env (.env): GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET — a "Web application"
OAuth client from Google Cloud Console with
http://localhost:8765/google/oauth/callback as an authorized redirect URI.

CLAUDE.md rule 23: scopes are widened here, not per-module. Full calendar/
drive/documents/spreadsheets; Gmail is readonly+send only (never modify);
Slides is readonly only.
"""

import base64
import hashlib
import json
import os
import secrets
import time
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
REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8765/google/oauth/callback")

_SCOPE_PREFIX = "https://www.googleapis.com/auth/"
SCOPES = [
    f"{_SCOPE_PREFIX}calendar",
    f"{_SCOPE_PREFIX}gmail.readonly",
    f"{_SCOPE_PREFIX}gmail.send",
    f"{_SCOPE_PREFIX}drive",              # full, not drive.file — see gdrive.py sandbox gate
    f"{_SCOPE_PREFIX}documents",
    f"{_SCOPE_PREFIX}spreadsheets",
    f"{_SCOPE_PREFIX}presentations.readonly",
]
SCOPE = " ".join(SCOPES)

TOKEN_FILE = store.PRIVATE_DIR / "google_token.json"
PKCE_STATE_FILE = store.PRIVATE_DIR / "google_pkce_state.json"


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
            "Google Workspace not connected. "
            "Visit http://localhost:8765/google/oauth/start to connect."
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


def exchange_code(code: str, state: str, on_connected=None) -> dict:
    """
    Exchanges the OAuth code for a token. `on_connected`, if given, runs
    AFTER the token is saved — the caller (api.py's /google/oauth/callback)
    uses it to create the Calendar/Drive sandboxes on first connect, keeping
    that orchestration out of this domain-agnostic module.
    """
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
    if on_connected:
        on_connected()
    return data


def _headers() -> dict:
    return {"Authorization": f"Bearer {get_valid_token()}",
            "Content-Type": "application/json"}
