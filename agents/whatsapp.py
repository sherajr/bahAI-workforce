"""
WhatsApp for the Secretary — Meta (official) WhatsApp Business Cloud API.
Phase 3 (CLAUDE.md). Never an unofficial bridge (whatsapp-web.js/Baileys)
from Sheraj's personal number — that was explicitly rejected as a ToS/ban
risk; this is her OWN number via Meta's real Business API.

Two safety properties this module exists to guarantee, both load-bearing:
  1. Every inbound webhook call must carry a valid HMAC signature (Meta's
     `X-Hub-Signature-256`, keyed on WHATSAPP_APP_SECRET) — the tunnel that
     exposes the webhook to the internet has no other authentication, so an
     unsigned or mis-signed request must never reach the Secretary's chat
     loop. verify_signature() is the only gate; callers must check it BEFORE
     doing anything with the payload.
  2. The 24-hour free-form messaging window is real: WhatsApp only allows
     free-form text within 24 hours of the recipient's last message to us.
     Outside that window, only a pre-approved template message can be sent.
     within_24h_window() is the single source of truth callers check before
     choosing send_text vs send_template — guessing wrong means a silently
     failed send.

Setup guide: GET /whatsapp/setup on the running API (mirrors the Google/
Canva guided-setup pages already in agents/api.py).
"""

import hashlib
import hmac
import os

import requests

GRAPH_API_VERSION = os.getenv("WHATSAPP_GRAPH_API_VERSION", "v21.0")
GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")
WHATSAPP_OWNER_NUMBER = os.getenv("WHATSAPP_OWNER_NUMBER", "")
WHATSAPP_UPDATE_TEMPLATE = os.getenv("WHATSAPP_UPDATE_TEMPLATE", "secretary_update")


def is_configured() -> bool:
    return bool(WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID and WHATSAPP_VERIFY_TOKEN
                and WHATSAPP_APP_SECRET and WHATSAPP_OWNER_NUMBER)


def _headers() -> dict:
    return {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}


def _digits(phone: str) -> str:
    """Meta's webhook `from` field is always digits-only, no '+' (e.g.
    "15551234567"). A human-entered number (WHATSAPP_OWNER_NUMBER in .env,
    or one typed into the dashboard) naturally has a '+' and may have
    spaces/dashes — every comparison and every outbound `to` must go
    through this or a formatting difference reads as a different person."""
    return "".join(c for c in (phone or "") if c.isdigit())


def is_owner(phone: str) -> bool:
    """The ownership gate, same shape as gcal.is_her_calendar() (rule 20)
    and gdrive.is_in_her_folder() (rule 24): only Sheraj's own number gets
    full Secretary access over WhatsApp."""
    return bool(phone) and bool(WHATSAPP_OWNER_NUMBER) and _digits(phone) == _digits(WHATSAPP_OWNER_NUMBER)


# ── Outbound ─────────────────────────────────────────────────────────────────

def send_text(to: str, body: str) -> dict:
    """Free-form text — only valid within the 24-hour window (within_24h_window)."""
    resp = requests.post(
        f"{GRAPH_API_BASE}/{WHATSAPP_PHONE_NUMBER_ID}/messages",
        headers=_headers(),
        json={"messaging_product": "whatsapp", "to": _digits(to), "type": "text",
              "text": {"body": body}},
        timeout=30)
    resp.raise_for_status()
    return resp.json()


def send_template(to: str, template_name: str = None, params: list[str] = None,
                  lang: str = "en_US") -> dict:
    """Pre-approved template — the only legal send outside the 24-hour window."""
    template_name = template_name or WHATSAPP_UPDATE_TEMPLATE
    components = [{"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}] \
        if params else []
    resp = requests.post(
        f"{GRAPH_API_BASE}/{WHATSAPP_PHONE_NUMBER_ID}/messages",
        headers=_headers(),
        json={"messaging_product": "whatsapp", "to": _digits(to), "type": "template",
              "template": {"name": template_name, "language": {"code": lang},
                          "components": components}},
        timeout=30)
    resp.raise_for_status()
    return resp.json()


def send_best_effort(to: str, body: str) -> dict:
    """
    Sends free-form text if the 24-hour window is open, otherwise falls back
    to the update template with the message as its one variable — the
    behavior the briefing spec calls for so a scheduled reminder never
    silently fails to deliver just because the window happened to be closed.
    """
    from agents import secretary_store as store
    if within_24h_window(to, store=store):
        return send_text(to, body)
    return send_template(to, WHATSAPP_UPDATE_TEMPLATE, [body])


def within_24h_window(phone: str, store=None) -> bool:
    from datetime import datetime, timedelta
    if store is None:
        from agents import secretary_store as store
    contact = store.get_contact_by_phone(phone)
    if not contact or not contact.get("last_inbound_at"):
        return False
    last = datetime.fromisoformat(contact["last_inbound_at"])
    return datetime.now() - last < timedelta(hours=24)


# ── Inbound webhook ──────────────────────────────────────────────────────────

def verify_webhook_challenge(mode: str, token: str, challenge: str) -> str | None:
    """GET /whatsapp/webhook handshake. Returns the challenge to echo back,
    or None if the mode/token don't match (caller should 403)."""
    if mode == "subscribe" and token and WHATSAPP_VERIFY_TOKEN and token == WHATSAPP_VERIFY_TOKEN:
        return challenge
    return None


def verify_signature(payload_bytes: bytes, signature_header: str | None) -> bool:
    """
    HMAC-SHA256 over the raw request body, keyed on WHATSAPP_APP_SECRET,
    compared to Meta's `X-Hub-Signature-256: sha256=<hex>` header. This is
    the ONLY thing standing between the public internet (via the tunnel)
    and the Secretary's chat loop — never skip this check, and never treat
    a missing app secret as "signature checking optional."
    """
    if not WHATSAPP_APP_SECRET or not signature_header:
        return False
    try:
        algo, _, sig = signature_header.partition("=")
        if algo != "sha256" or not sig:
            return False
        expected = hmac.new(WHATSAPP_APP_SECRET.encode(), payload_bytes, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sig)
    except Exception:
        return False


def parse_webhook_messages(payload: dict) -> list[dict]:
    """
    Extracts inbound text messages from a Meta webhook POST body. Ignores
    status callbacks (delivered/read receipts) and any entry that isn't a
    message. Non-text message types (image, audio, ...) are still surfaced
    with a placeholder body so the caller can tell Sheraj plainly rather
    than silently dropping them.
    """
    out = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []):
                msg_type = msg.get("type", "")
                if msg_type == "text":
                    text = msg.get("text", {}).get("body", "")
                else:
                    text = f"[{msg_type or 'unsupported'} message — text only, please]"
                out.append({
                    "from": msg.get("from", ""),
                    "message_id": msg.get("id", ""),
                    "timestamp": msg.get("timestamp", ""),
                    "type": msg_type,
                    "text": text,
                })
    return out
