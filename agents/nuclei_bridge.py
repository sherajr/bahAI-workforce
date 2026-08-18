"""
Where the Material World and the Bahá'í Workforce meet.

The Colony tab has two skies. The Digital World is the agents; the
Material World is Sheraj's nuclei and friends. This module is the ONE place the two
touch, for the same reason `secretary_colony.py` is the one place Abigail
touches the teams (rule 50): everything on the Material World side is personal
and lives in private/nuclei.db (rules 15 / 59), and everything on the
workforce side lives in workforce.db, which the Steward's ledger reads.

Three jobs, and nothing else:

  * `workforce_picture()` — the shareable read: who works here (agents from
    workforce.db, people from the private store), what is running, what was
    finished lately. Facts go OUT to the dashboard; nothing comes back in.
  * `draft_message()` — Clara writes a WhatsApp message to a friend or to a
    nucleus's group, on the LOCAL model only (rule 67).
  * `send_to_contact()` — hands a finished message to Abigail's existing
    WhatsApp path, with rule 28's tiers unchanged.

Rule 68 is the invariant that matters: no friend's name, no group name and
no group link is ever written into workforce.db. Reads cross; writes do not.
"""

from __future__ import annotations

import json
import re

# Enough of the message for Sheraj to read at a glance and still be a message.
MESSAGE_MAX_CHARS = 900
# How many finished things the drafter is allowed to know about.
RECENT_WORK_LIMIT = 5

# Key names that must never appear in anything handed to the workforce side.
# Checked in code, not asked for in a prompt.
_FORBIDDEN_OUT = ("phone", "email", "address", "link", "invite", "recipient")


class BridgeError(RuntimeError):
    """Something the owner needs to read, not a stack trace."""


def _nuclei():
    from agents import nuclei_store
    nuclei_store.init_db()
    return nuclei_store


def assert_no_personal_leak(payload: dict):
    """Rule 68: the workforce side never learns who a message was for.

    Narrow on purpose, and it says so: it cannot judge whether an ordinary
    sentence names a person. It exists to stop the mechanical mistakes — a
    recipient, a number or a group invite link riding along in a job progress
    string or a run summary — the same discipline as
    `secretary_colony.assert_shareable`, which is the model for this.
    """
    for key, value in (payload or {}).items():
        if any(bad in str(key).lower() for bad in _FORBIDDEN_OUT):
            raise BridgeError(
                f"'{key}' belongs to the Material World and cannot cross into the workforce"
            )
        if isinstance(value, str) and re.search(r"chat\.whatsapp\.com|wa\.me/", value):
            raise BridgeError("a WhatsApp link cannot cross into the workforce")


# --- the read ----------------------------------------------------------------

def workforce_picture() -> dict:
    """Everything the workforce light opens into.

    The agents come from the Colony's own snapshot, so trust, live state and
    team membership are the SAME numbers the Digital World draws — never a
    second count that can drift (rule 35's discipline applied to a second
    view of the same facts).
    """
    from agents import colony
    ns = _nuclei()

    snap = colony.colony_snapshot()
    agents = [a for a in snap["agents"] if not a["is_instrument"]]
    instruments = [a for a in snap["agents"] if a["is_instrument"]]
    running = [
        dict(job, team=t["id"], team_name=t["name"])
        for t in snap["teams"] for job in (t.get("jobs") or [])
    ]

    return {
        "grouping_id": int(ns.workforce_grouping()["id"]),
        "name": ns.WORKFORCE_NAME,
        "agents": agents,
        "instruments": instruments,
        "teams": snap["teams"],
        "people": ns.workforce_members(),
        "running_jobs": running,
        "pending_actions": snap.get("pending_actions", 0),
        "recent_work": recent_work(),
    }


def recent_work(limit: int = RECENT_WORK_LIMIT) -> list[dict]:
    """What the workforce actually finished — read from products, not claimed.

    Kept to titles and kinds. This is the only workforce fact the drafter is
    given, and it is given as DATA rather than asked for from the model,
    because a message naming a product that does not exist would be a false
    claim made in Sheraj's name.
    """
    from agents import state
    out = []
    for p in state.get_all_products()[: max(0, int(limit))]:
        out.append({
            "id": p.get("product_id"),
            "title": (p.get("title") or "").strip(),
            "kind": p.get("product_type") or "bookmark",
            "created_at": p.get("created_at"),
        })
    return out


# --- the draft ---------------------------------------------------------------

_SYSTEM = (
    "You are Clara, who writes for a small Bahá'í workforce. You are writing "
    "ONE short WhatsApp message for Sheraj to send in his own name.\n"
    "Rules you must follow:\n"
    "- Write as Sheraj writing to a friend. Warm, plain, unhurried. No "
    "marketing voice, no stacked exclamation marks, no emoji.\n"
    "- Keep it under 90 words. This is WhatsApp, not a newsletter.\n"
    "- Never claim anything you were not told. Do not invent a date, a time, a "
    "place, a price, a number, or a piece of work. If a detail is missing, "
    "leave a square-bracket blank like [time] for Sheraj to fill in — a blank "
    "is always better than a guess.\n"
    "- Do not quote scripture. If a quote is needed Sheraj will add it himself.\n"
    "- Do not sign off with a name or a title. No subject line, no greeting "
    "line that names a stranger.\n"
    "Return the message text and nothing else."
)


def draft_message(about: str, to_name: str = "", to_kind: str = "contact",
                  include_recent_work: bool = False,
                  nucleus_name: str = "") -> dict:
    """Clara drafts it; Sheraj edits it; nothing is sent from here.

    Runs on the LOCAL model through `router.call_local` (rule 67) because the
    prompt names one of Sheraj's friends or one of his nuclei.
    """
    about = (about or "").strip()
    if not about:
        raise BridgeError("Say what the message should be about")

    from agents import router

    who = (to_name or "").strip()
    lines = []
    if to_kind == "group":
        lines.append(
            f"The message goes to the WhatsApp group for {who or 'a nucleus'}"
            + (f", which gathers as {nucleus_name}." if nucleus_name else ".")
        )
        lines.append("Write to the whole group, not to one person.")
    else:
        lines.append(f"The message goes to {who or 'a friend'} — one person.")
    lines.append(f"What it is about: {about}")

    if include_recent_work:
        work = recent_work()
        if work:
            lines.append(
                "Things the workforce actually finished recently. You may mention "
                "these and only these, exactly as written:"
            )
            for w in work:
                kind = "quote card" if w["kind"] == "quote_card" else w["kind"]
                lines.append(f"- {w['title'] or w['id']} ({kind})")
        else:
            lines.append(
                "The workforce has not finished anything recently. Do not mention "
                "any work at all."
            )

    text = router.call_local(
        [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": "\n".join(lines)},
        ],
        temperature=0.7,
        max_tokens=700,
    )
    message = _clean(text)
    if not message:
        raise BridgeError(
            "The local model returned an empty draft. Is Ollama running?"
        )
    return {
        "message": message,
        "drafted_by": "scribe",
        "model": "local",
        "warnings": invented_specifics(message, about),
    }


# Concrete things a message can assert that a friend will act on. Times are
# first because a small local model reliably supplies one: asked only to
# "invite them to the devotional on Friday", Qwen wrote "around 7" (seen
# 2026-08-17, first real draft).
_SPECIFIC_PATTERNS = (
    (r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)\b", "a time"),
    (r"\b\d{1,2}:\d{2}\b", "a time"),
    (r"\b(?:around|at|by|from)\s+\d{1,2}(?:\s*o'?clock)?\b", "a time"),
    (r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", "a day"),
    (r"\b(?:january|february|march|april|may|june|july|august|september|"
     r"october|november|december)\b", "a date"),
    (r"[$£€]\s?\d+(?:\.\d{2})?", "an amount of money"),
)


def invented_specifics(message: str, about: str) -> list[str]:
    """Flag concrete details the draft asserts that were never supplied.

    The prompt already forbids these, and prompt compliance is not trusted for
    anything a friend will act on — same reasoning as `_sanitize_claims`
    (rule 4). It cannot EDIT the message, because there is no safe mechanical
    rewrite of free prose; it points at what to look at, and Sheraj is reading
    the draft in an editable box either way.
    """
    haystack = (about or "").lower()
    seen: dict[str, str] = {}
    for pattern, label in _SPECIFIC_PATTERNS:
        for match in re.finditer(pattern, message or "", re.I):
            token = match.group(0).strip()
            if token.lower() in haystack:
                continue
            seen.setdefault(token.lower(), f"{label} you did not give it: \"{token}\"")
    return list(seen.values())


def _clean(text: str) -> str:
    """Strip the wrappers a small local model puts around a plain answer."""
    out = (text or "").strip()
    fence = re.match(r"^```[a-zA-Z]*\s*\n(.*?)\n?```$", out, re.S)
    if fence:
        out = fence.group(1).strip()
    out = re.sub(r"^here(?:'s| is) (?:the|a|your) (?:draft|message)[:\-—]\s*",
                 "", out, flags=re.I)
    out = out.strip()
    if len(out) > 1 and out[0] == '"' and out[-1] == '"':
        out = out[1:-1]
    return out.strip()[:MESSAGE_MAX_CHARS]


# --- the send ----------------------------------------------------------------

def send_to_contact(contact_id: int, message: str) -> dict:
    """Send a finished message on Abigail's WhatsApp, rule 28's tiers unchanged.

    Sheraj typed or approved this text on screen, so this is the same class of
    owner-driven action as the Trusted Contacts UI — but the TIERS are not
    relaxed for it: the owner and an allowlisted contact are sent to directly,
    and anyone else queues in the existing pending_actions queue exactly as a
    tool call would. No new path to an un-allowlisted number is opened here.
    """
    from agents import secretary_store, whatsapp
    secretary_store.init_db()

    message = (message or "").strip()
    if not message:
        raise BridgeError("There is no message to send")
    if len(message) > MESSAGE_MAX_CHARS:
        raise BridgeError(f"Keep the message under {MESSAGE_MAX_CHARS} characters")

    contact = next(
        (c for c in secretary_store.list_contacts() if int(c["id"]) == int(contact_id)),
        None,
    )
    if not contact:
        raise BridgeError("No such contact")
    phone = (contact.get("phone") or "").strip()
    if not phone:
        raise BridgeError(f"{contact['name']} has no WhatsApp number saved")

    if not whatsapp.is_configured():
        raise BridgeError(
            "WhatsApp is not set up — check WHATSAPP_TOKEN and "
            "WHATSAPP_PHONE_NUMBER_ID in .env"
        )

    if not (whatsapp.is_owner(phone) or secretary_store.is_allowlisted(phone)):
        desc = f"Send a WhatsApp message to {contact['name']}"
        action_id = secretary_store.add_pending_action(
            "whatsapp_send", desc, json.dumps({"to": phone, "body": message})
        )
        return {
            "status": "queued",
            "action_id": action_id,
            "to": contact["name"],
            "note": (
                f"{contact['name']} is not on your trusted contacts list, so this "
                f"is waiting for your approval as action #{action_id} rather than "
                "going out."
            ),
        }

    open_window = whatsapp.within_24h_window(phone, store=secretary_store)
    whatsapp.send_best_effort(phone, message)
    return {
        "status": "sent",
        "to": contact["name"],
        "as_template": not open_window,
        "note": (
            f"Sent to {contact['name']}."
            if open_window else
            f"The 24-hour window with {contact['name']} has closed, so this went "
            "out through the pre-approved template — the wording they see may "
            "differ. (That template has never been confirmed working; check that "
            "it arrived.)"
        ),
    }
