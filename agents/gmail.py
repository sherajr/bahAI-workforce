"""
Gmail for the Secretary — search/read only (agents/secretary_tools.py's
read tools) plus a single write, send_message, which is NEVER called
directly from the tool loop — only from secretary.py's
execute_pending_action after Sheraj explicitly approves (CLAUDE.md rule 25:
Gmail has no free tier at all; there's no "her own inbox" to gate against
the way Calendar/Drive have sandboxes, so every send queues for approval,
unconditionally).
"""

import base64
import re
from email.mime.text import MIMEText

import requests

from agents.google_auth import get_valid_token, _headers  # noqa: F401 (re-exported)

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"


def _decode_part(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def _extract_plain_text(payload: dict) -> str:
    """Walk the MIME tree for the first text/plain part; falls back to
    text/html stripped of tags if no plain part exists."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return _decode_part(payload["body"]["data"])
    for part in payload.get("parts", []) or []:
        text = _extract_plain_text(part)
        if text:
            return text
    if payload.get("mimeType") == "text/html" and payload.get("body", {}).get("data"):
        html = _decode_part(payload["body"]["data"])
        return re.sub(r"<[^>]+>", " ", html)
    return ""


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def search_messages(query: str, max_results: int = 10) -> list[dict]:
    """Read is unrestricted (mirrors gcal.list_events/gdrive.search_files)."""
    resp = requests.get(f"{GMAIL_API}/messages", headers=_headers(),
                        params={"q": query, "maxResults": max_results}, timeout=30)
    resp.raise_for_status()
    ids = [m["id"] for m in resp.json().get("messages", [])]
    out = []
    for mid in ids:
        meta_resp = requests.get(f"{GMAIL_API}/messages/{mid}", headers=_headers(),
                                 params={"format": "metadata",
                                         "metadataHeaders": ["From", "Subject", "Date"]},
                                 timeout=30)
        if meta_resp.status_code != 200:
            continue
        msg = meta_resp.json()
        headers_list = msg.get("payload", {}).get("headers", [])
        out.append({
            "id": mid,
            "from": _header(headers_list, "From"),
            "subject": _header(headers_list, "Subject"),
            "date": _header(headers_list, "Date"),
            "snippet": msg.get("snippet", ""),
        })
    return out


def get_message(message_id: str) -> dict:
    resp = requests.get(f"{GMAIL_API}/messages/{message_id}", headers=_headers(),
                        params={"format": "full"}, timeout=30)
    resp.raise_for_status()
    msg = resp.json()
    headers_list = msg.get("payload", {}).get("headers", [])
    return {
        "id": message_id,
        "from": _header(headers_list, "From"),
        "to": _header(headers_list, "To"),
        "subject": _header(headers_list, "Subject"),
        "date": _header(headers_list, "Date"),
        "body": _extract_plain_text(msg.get("payload", {}))[:4000],
    }


def send_message(to: list[str], subject: str, body: str, cc: list[str] = None) -> dict:
    """Gated, unconditionally — only ever called from
    secretary.py::execute_pending_action after Sheraj approves the
    "gmail_send" pending action. Never call this from the read-only tool
    loop or any other path."""
    mime = MIMEText(body)
    mime["to"] = ", ".join(to)
    if cc:
        mime["cc"] = ", ".join(cc)
    mime["subject"] = subject
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")
    resp = requests.post(f"{GMAIL_API}/messages/send", headers=_headers(),
                         json={"raw": raw}, timeout=30)
    resp.raise_for_status()
    return resp.json()
