"""
The realtime connection: ephemeral credentials, session configuration, cost.

The master `OPENAI_API_KEY` never leaves this machine (rule 73). The browser is
handed a short-lived client secret minted here and does its own WebRTC
handshake with OpenAI; the key itself is never in a bundle, a page, or a
response body.

    browser --(owner-gated request through Vite's proxy)--> this API
    this API --(OPENAI_API_KEY)--> POST /v1/realtime/client_secrets
    browser --(ek_... secret, SDP)--> POST /v1/realtime/calls

The session configuration is where rule 75 becomes structural rather than
hopeful. `create_response: false` means the server's own turn detector may tell
us that a turn appears to have ended, and CANNOT itself start the model
talking. That decision belongs to the governor.

Checked against OpenAI's current Realtime docs on 2026-08-21 (GA interface: no
`OpenAI-Beta` header, `client_secrets` for the credential, `/realtime/calls`
for the SDP exchange, turn detection under `session.audio.input.turn_detection`).
"""

from __future__ import annotations

import hashlib
import os
import time
from typing import Optional

import requests

from agents.live_consultation import (
    DEFAULT_FRAMEWORK, DEFAULT_MODE, MODES, REALTIME_MODEL, TRANSCRIBE_MODEL, VOICE,
    session_instructions,
)

OPENAI_BASE = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
CLIENT_SECRET_URL = f"{OPENAI_BASE}/realtime/client_secrets"

# The browser POSTs its SDP offer here with the ephemeral secret. Served to the
# client rather than hardcoded there, so a docs change is one edit in Python.
CALLS_URL = f"{OPENAI_BASE}/realtime/calls"

# Long enough to cover a connection attempt and a retry, short enough that a
# leaked one is worth little. The credential authorises a realtime session, not
# the account.
SECRET_TTL_S = int(os.getenv("CONSULTATION_SECRET_TTL_S", "600"))

# How readily the semantic detector believes a turn has ended. This is the
# single biggest contributor to how long she takes to answer a direct question,
# because nothing downstream can start until the turn is reported as over.
#
# `low` was the first default and it was the main reason Sheraj found her
# unresponsive (2026-08-21). `medium` is the default now. Note what this
# setting can and cannot do: it changes when the detector REPORTS, never
# whether she may speak — `create_response` stays false and the governor still
# decides (rule 75). Turning it up makes her quicker to answer; it can never
# make her interrupt.
VAD_EAGERNESS = os.getenv("CONSULTATION_VAD_EAGERNESS", "medium")


def _key() -> str:
    return os.getenv("OPENAI_API_KEY", "").strip()


def available() -> bool:
    return bool(_key())


def safety_identifier(session_id: str = "") -> str:
    """
    A stable, opaque identifier for OpenAI's abuse tooling.

    Never an email, a name or a phone number: it is a hash of the machine's own
    owner key plus the session id, so OpenAI can correlate traffic from one
    installation without this repo handing over anything about who Sheraj is
    (rule 73, same instinct as rule 15).
    """
    try:
        from agents.auth import get_or_create_key
        seed = get_or_create_key()
    except Exception:
        seed = os.getenv("DASHBOARD_API_KEY", "bahai-workforce")
    digest = hashlib.sha256(f"bahai-consultation:{seed}:{session_id}".encode("utf-8")).hexdigest()
    return f"bw_{digest[:32]}"


# Model-availability answers, cached: the capabilities endpoint is polled and a
# metadata lookup per poll would be silly. (id -> (checked_at, ok, note)).
_MODEL_CACHE: dict[str, tuple[float, bool, str]] = {}
_MODEL_TTL_S = 600


def check_model(model_id: str, get=None) -> tuple[bool, str]:
    """
    Does this model id actually exist on this account?

    Same discipline as rule 41a's `_is_known_missing`: absence is only reported
    when the lookup SUCCEEDED and said 404. A network failure or a missing key
    means "not known", never "gone" — otherwise a moment of bad wifi would tell
    the owner his configured model had disappeared.

    The check earns its place: `gpt-5.6` reads like a real id, is in this repo's
    own documented-alias list, and 404s. Without this the first analysis pass of
    a live meeting would be where that was discovered.
    """
    if not model_id:
        return True, ""
    if not available():
        return True, ""
    hit = _MODEL_CACHE.get(model_id)
    if hit and (time.time() - hit[0]) < _MODEL_TTL_S:
        return hit[1], hit[2]
    sender = get or requests.get
    try:
        resp = sender(f"{OPENAI_BASE}/models/{model_id}",
                      headers={"Authorization": f"Bearer {_key()}"}, timeout=12)
    except Exception:
        return True, ""
    status = getattr(resp, "status_code", 0)
    if status == 200:
        _MODEL_CACHE[model_id] = (time.time(), True, "")
        return True, ""
    if status == 404:
        note = (f"The configured model '{model_id}' is not available on this OpenAI "
                "account. Set CONSULTATION_REASONING_MODEL in .env to one that is.")
        _MODEL_CACHE[model_id] = (time.time(), False, note)
        return False, note
    return True, ""


def session_config(session: dict, instructions: Optional[str] = None) -> dict:
    """
    The realtime session object.

    Two properties carry the whole floor design:
      * `create_response: false` — the detector reports, it does not act.
      * `interrupt_response: true` — a human starting to speak cuts the model
        off at the server as well as locally, so an interrupted response cannot
        keep generating audio nobody wants. The client cancels too; both halves
        exist because either alone leaves a window.
    """
    mode = session.get("mode") or DEFAULT_MODE
    text = instructions if instructions is not None else session_instructions(
        question=session.get("question") or "",
        context=session.get("context") or "",
        framework=session.get("framework") or DEFAULT_FRAMEWORK,
        mode=mode,
        decision_method=session.get("decision_method") or "unspecified",
    )
    return {
        "type": "realtime",
        "model": session.get("realtime_model") or REALTIME_MODEL,
        "instructions": text,
        "output_modalities": ["audio"],
        "audio": {
            "input": {
                "transcription": {
                    "model": session.get("transcribe_model") or TRANSCRIBE_MODEL,
                },
                "turn_detection": {
                    "type": "semantic_vad",
                    "eagerness": VAD_EAGERNESS,
                    # Rule 75, in the one place a mistake would be invisible.
                    "create_response": False,
                    "interrupt_response": True,
                },
            },
            "output": {
                "voice": session.get("voice") or VOICE,
            },
        },
    }


class RealtimeError(RuntimeError):
    """Raised with a sentence a non-technical owner can act on."""


def create_client_secret(session: dict, instructions: Optional[str] = None,
                         post=None) -> dict:
    """
    Mint a short-lived credential for this browser.

    `post` is injectable so the suite can assert the exact request body without
    a network call — and so no test ever spends money proving the shape is right.
    """
    if not available():
        raise RealtimeError(
            "Live Consultation needs an OpenAI API key for realtime voice. "
            "Add OPENAI_API_KEY to .env and restart the API.")
    body = {
        "expires_after": {"anchor": "created_at", "seconds": SECRET_TTL_S},
        "session": session_config(session, instructions=instructions),
    }
    headers = {
        "Authorization": f"Bearer {_key()}",
        "Content-Type": "application/json",
        "OpenAI-Safety-Identifier": safety_identifier(session.get("id") or ""),
    }
    sender = post or requests.post
    try:
        resp = sender(CLIENT_SECRET_URL, headers=headers, json=body, timeout=30)
    except Exception as e:
        raise RealtimeError(
            f"Could not reach OpenAI to start the session ({type(e).__name__}). "
            "Check the network and try again.") from e
    if getattr(resp, "status_code", 500) >= 400:
        detail = ""
        try:
            detail = (resp.json().get("error") or {}).get("message", "")
        except Exception:
            detail = (getattr(resp, "text", "") or "")[:300]
        raise RealtimeError(f"OpenAI refused to start a realtime session: {detail}")
    data = resp.json()
    value = data.get("value") or (data.get("client_secret") or {}).get("value")
    if not value:
        raise RealtimeError("OpenAI returned no client secret. Nothing was started.")
    returned = data.get("session") or {}
    return {
        "client_secret": value,
        "expires_at": data.get("expires_at"),
        "calls_url": CALLS_URL,
        "model": returned.get("model") or body["session"]["model"],
        "voice": ((returned.get("audio") or {}).get("output") or {}).get("voice")
                 or body["session"]["audio"]["output"]["voice"],
        # Echoed back so the dashboard can SHOW that automatic responses are off
        # rather than asking anyone to trust that they are.
        "turn_detection": ((returned.get("audio") or {}).get("input") or {}).get("turn_detection")
                          or body["session"]["audio"]["input"]["turn_detection"],
    }


# ── Cost (rule 85) ──────────────────────────────────────────────────────────

# USD per 1M tokens, from OpenAI's published pricing on 2026-08-21. These are
# ESTIMATES for the Steward's ledger, in the same spirit as EST_COST_USD in
# router.py: consistent enough that a long meeting reads as more expensive than
# a short one. They are not an invoice.
RATES_PER_MTOK = {
    "gpt-realtime-2.1": {"text_in": 4.00, "audio_in": 32.00,
                         "text_out": 24.00, "audio_out": 64.00},
    "gpt-realtime": {"text_in": 4.00, "audio_in": 32.00,
                     "text_out": 16.00, "audio_out": 64.00},
    "gpt-realtime-2.1-mini": {"text_in": 0.60, "audio_in": 10.00,
                              "text_out": 2.40, "audio_out": 20.00},
    "gpt-realtime-mini": {"text_in": 0.60, "audio_in": 10.00,
                          "text_out": 2.40, "audio_out": 20.00},
}
DEFAULT_RATES = RATES_PER_MTOK["gpt-realtime-2.1"]

SPEND_KIND = "openai_realtime"


def estimate_cost(usage: dict, model: str = "") -> Optional[float]:
    """
    USD for one realtime response, from the `usage` block OpenAI puts on
    `response.done`.

    Returns None when the event did not carry enough detail. None means "not
    known" and is recorded as nothing at all — a made-up number in the ledger
    would be worse than a gap, and the UI says the figure is partial.
    """
    if not isinstance(usage, dict) or not usage:
        return None
    rates = RATES_PER_MTOK.get(model or "", DEFAULT_RATES)
    in_details = usage.get("input_token_details") or {}
    out_details = usage.get("output_token_details") or {}
    audio_in = in_details.get("audio_tokens")
    text_in = in_details.get("text_tokens")
    audio_out = out_details.get("audio_tokens")
    text_out = out_details.get("text_tokens")
    if audio_in is None and text_in is None and audio_out is None and text_out is None:
        # Only totals: bill them at the audio rate, which is the dominant cost
        # in a voice session, and say so where it is reported.
        total_in = usage.get("input_tokens")
        total_out = usage.get("output_tokens")
        if total_in is None and total_out is None:
            return None
        audio_in, audio_out = total_in or 0, total_out or 0
        text_in = text_out = 0
    cost = (
        (text_in or 0) * rates["text_in"]
        + (audio_in or 0) * rates["audio_in"]
        + (text_out or 0) * rates["text_out"]
        + (audio_out or 0) * rates["audio_out"]
    ) / 1_000_000
    return round(cost, 6)


def record_usage(usage: dict, model: str = "") -> dict:
    """Meter one realtime response into the Steward's ledger, at the same
    chokepoint discipline as every other paid call in this repo."""
    cost = estimate_cost(usage, model)
    if cost is None:
        return {"recorded": False, "reason": "the event carried no usage detail", "cost": None}
    try:
        from agents.state import record_spend
        record_spend(SPEND_KIND, cost)
    except Exception:
        return {"recorded": False, "reason": "the ledger could not be written", "cost": cost}
    return {"recorded": True, "cost": cost, "kind": SPEND_KIND}


def spend_snapshot() -> dict:
    """This month's metered spend against the soft ceiling the Steward reports.
    Starting a realtime meeting is a paid, explicit act; the setup screen shows
    this before anyone presses Start (rule 85)."""
    ceiling = float(os.getenv("MONTHLY_SPEND_CEILING_USD", "15"))
    try:
        from agents.state import get_spend_summary
        summary = get_spend_summary() or {}
        if summary.get("error"):
            raise RuntimeError(summary["error"])
        month = float(summary.get("month") or 0.0)
    except Exception:
        return {"month_total": None, "monthly_ceiling": ceiling, "over_ceiling": False,
                "known": False}
    return {"month_total": round(month, 2), "monthly_ceiling": ceiling,
            "over_ceiling": month > ceiling, "known": True}
