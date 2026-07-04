"""
Canva Connect API client — OAuth, asset upload, brand template autofill.

One-time setup:
  1. Add CANVA_CLIENT_ID + CANVA_CLIENT_SECRET to .env
  2. Add CANVA_TEMPLATE_ID (brand template ID from Canva URL) to .env
  3. Visit http://localhost:8765/canva/oauth/start in a browser to authorize
  4. Tokens auto-refresh after that.
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

load_dotenv(dotenv_path=str(Path(__file__).parent.parent / ".env"), override=True)

CANVA_CLIENT_ID     = os.getenv("CANVA_CLIENT_ID", "")
CANVA_CLIENT_SECRET = os.getenv("CANVA_CLIENT_SECRET", "")
CANVA_TEMPLATE_ID   = os.getenv("CANVA_TEMPLATE_ID", "")
CANVA_REDIRECT_URI  = os.getenv("CANVA_REDIRECT_URI", "http://localhost:8765/canva/oauth/callback")
CANVA_IMAGE_FIELD   = os.getenv("CANVA_IMAGE_FIELD", "bookmark_image")

CANVA_AUTH_URL  = "https://www.canva.com/api/oauth/authorize"
CANVA_TOKEN_URL = "https://api.canva.com/rest/v1/oauth/token"
CANVA_API_BASE  = "https://api.canva.com/rest/v1"

_PROJECT_ROOT  = Path(__file__).parent.parent
TOKEN_FILE     = _PROJECT_ROOT / "canva_token.json"
PKCE_STATE_FILE = _PROJECT_ROOT / "canva_pkce_state.json"

SCOPES = [
    "asset:read",
    "asset:write",
    "brandtemplate:meta:read",
    "brandtemplate:content:read",
    "design:content:write",
]


# ── PKCE ─────────────────────────────────────────────────────────────────────

def _generate_pkce() -> dict:
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return {"code_verifier": code_verifier, "code_challenge": code_challenge}


# ── Token storage ─────────────────────────────────────────────────────────────

def _load_token() -> Optional[dict]:
    if TOKEN_FILE.exists():
        return json.loads(TOKEN_FILE.read_text())
    return None


def _save_token(token_data: dict) -> None:
    TOKEN_FILE.write_text(json.dumps(token_data, indent=2))


def _refresh_token(refresh_token: str) -> dict:
    credentials = base64.b64encode(
        f"{CANVA_CLIENT_ID}:{CANVA_CLIENT_SECRET}".encode()
    ).decode()
    resp = requests.post(
        CANVA_TOKEN_URL,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
    )
    resp.raise_for_status()
    data = resp.json()
    data["expires_at"] = time.time() + data.get("expires_in", 14400)
    _save_token(data)
    return data


def get_valid_token() -> str:
    """Return a valid access token, auto-refreshing if within 5 min of expiry."""
    data = _load_token()
    if not data:
        raise RuntimeError(
            "Canva not authorised. "
            "Visit http://localhost:8765/canva/oauth/start to connect."
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


# ── OAuth flow ────────────────────────────────────────────────────────────────

def build_auth_url() -> str:
    """Build the Canva OAuth URL and save PKCE state for the callback."""
    if not CANVA_CLIENT_ID:
        raise RuntimeError("CANVA_CLIENT_ID not set in .env")
    pkce = _generate_pkce()
    state = secrets.token_urlsafe(16)
    PKCE_STATE_FILE.write_text(json.dumps({
        "code_verifier": pkce["code_verifier"],
        "state": state,
    }))
    scope_str = "%20".join(SCOPES)
    return (
        f"{CANVA_AUTH_URL}"
        f"?code_challenge={pkce['code_challenge']}"
        f"&code_challenge_method=S256"
        f"&scope={scope_str}"
        f"&response_type=code"
        f"&client_id={CANVA_CLIENT_ID}"
        f"&redirect_uri={CANVA_REDIRECT_URI}"
        f"&state={state}"
    )


def exchange_code(code: str, state: str) -> dict:
    """Exchange authorization code for tokens. Called from /canva/oauth/callback."""
    if not PKCE_STATE_FILE.exists():
        raise RuntimeError("No PKCE state found. Restart the OAuth flow.")
    saved = json.loads(PKCE_STATE_FILE.read_text())
    if saved["state"] != state:
        raise ValueError("OAuth state mismatch — possible CSRF. Restart the flow.")

    credentials = base64.b64encode(
        f"{CANVA_CLIENT_ID}:{CANVA_CLIENT_SECRET}".encode()
    ).decode()
    resp = requests.post(
        CANVA_TOKEN_URL,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": saved["code_verifier"],
            "redirect_uri": CANVA_REDIRECT_URI,
        },
    )
    resp.raise_for_status()
    data = resp.json()
    data["expires_at"] = time.time() + data.get("expires_in", 14400)
    _save_token(data)
    PKCE_STATE_FILE.unlink(missing_ok=True)
    return data


# ── Asset upload ──────────────────────────────────────────────────────────────

def upload_image(image_path: str) -> str:
    """Upload a local image file to Canva. Returns the Canva asset ID."""
    token = get_valid_token()
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    # Name must be base64-encoded, max 50 chars
    name_b64 = base64.b64encode(path.name[:50].encode()).decode()

    resp = requests.post(
        f"{CANVA_API_BASE}/asset-uploads",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/octet-stream",
            "Asset-Upload-Metadata": json.dumps({"name_base64": name_b64}),
        },
        data=path.read_bytes(),
        timeout=60,
    )
    resp.raise_for_status()
    job = resp.json()["job"]

    # Poll until complete (binary uploads usually finish immediately)
    for _ in range(20):
        status = job.get("status")
        if status == "success":
            return job["asset"]["id"]
        if status == "failed":
            raise RuntimeError(f"Canva asset upload failed: {job.get('error', {})}")
        time.sleep(2)
        poll = requests.get(
            f"{CANVA_API_BASE}/asset-uploads/{job['id']}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        poll.raise_for_status()
        job = poll.json()["job"]

    raise TimeoutError("Canva asset upload timed out after 40 seconds")


# ── Template inspection ───────────────────────────────────────────────────────

def get_template_fields(template_id: str = None) -> dict:
    """Return the autofill field names and types defined in the brand template."""
    token = get_valid_token()
    tid = template_id or CANVA_TEMPLATE_ID
    if not tid:
        raise RuntimeError("CANVA_TEMPLATE_ID not set in .env")
    resp = requests.get(
        f"{CANVA_API_BASE}/brand-templates/{tid}/dataset",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("dataset", {})


# ── Autofill ──────────────────────────────────────────────────────────────────

def autofill_bookmark(image_path: str, template_id: str = None) -> dict:
    """
    Full pipeline: upload image → autofill brand template → return design URL.
    Returns: {design_url, design_id, thumbnail_url, asset_id}
    """
    tid = template_id or CANVA_TEMPLATE_ID
    if not tid:
        raise RuntimeError("CANVA_TEMPLATE_ID not set in .env")

    asset_id = upload_image(image_path)
    token = get_valid_token()

    resp = requests.post(
        f"{CANVA_API_BASE}/autofills",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "brand_template_id": tid,
            "data": {
                CANVA_IMAGE_FIELD: {"type": "image", "asset_id": asset_id}
            },
        },
        timeout=30,
    )
    resp.raise_for_status()
    job = resp.json()["job"]

    # Poll until the new design is ready
    for _ in range(30):
        status = job.get("status")
        if status == "success":
            result = job.get("result", {})
            design = result.get("design", {})
            return {
                "design_url": design.get("url"),
                "design_id": design.get("id"),
                "thumbnail_url": design.get("thumbnail", {}).get("url"),
                "asset_id": asset_id,
            }
        if status == "failed":
            raise RuntimeError(f"Canva autofill failed: {job.get('error', {})}")
        time.sleep(2)
        poll = requests.get(
            f"{CANVA_API_BASE}/autofills/{job['id']}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        poll.raise_for_status()
        job = poll.json()["job"]

    raise TimeoutError("Canva autofill timed out after 60 seconds")
