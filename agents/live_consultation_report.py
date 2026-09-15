"""
The end-of-meeting report (owner ask, 2026-08-25).

What the room gets when the consultation finishes: one readable page they can
copy or download and send to someone who was not there. It replaces reading the
consultation map, which Sheraj found "far too long to read" — and it is not the
map with a nicer heading. The map is a working structure for the reasoner; this
is prose for a person.

**Hybrid, and the split is the whole point (rule 90).** Two kinds of content go
into this document and they are produced in completely different ways:

  * The NARRATIVE — what was discussed, how the group got where it got, what is
    still open — is written by the reasoning model, which condenses. Ten lists
    of fragments become a few paragraphs. That is the part a model is good at
    and a template is bad at.

  * The RECORD — the confirmed decision, the action items, their owners and
    their deadlines, the verified passages — is copied VERBATIM out of the
    store. No model writes it, edits it, or is even asked to return it.

The reason is not tidiness. A report is the thing people act on afterwards, and
a model asked to write a decision section will smooth "we were leaning towards
Saturday" into "the group decided Saturday", and will give an unowned action a
plausible owner. Rules 81 and 83 already say a decision needs a human and an
owner is only ever what someone actually said; this module is where those rules
would be quietly undone if the whole report were generated. So the model is
never given the opportunity: `_narrative` returns prose fields only, and
anything it says about a decision or an owner is discarded.

A failed or unreachable model costs the PROSE, never the report: the deterministic
half is assembled first and stands on its own, with a visible note saying the
summary could not be written. Same discipline as rule 79.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from agents.live_consultation import (
    ACTION_STATUS_LABELS, CLOSEOUT_OUTCOMES, DECISION_METHODS, FRAMEWORKS,
    REASONING_MODEL, ASSISTANT_NAME,
)

MAX_TOKENS = int(os.getenv("CONSULTATION_REPORT_MAX_TOKENS", "1600"))
TIMEOUT_S = int(os.getenv("CONSULTATION_REPORT_TIMEOUT_S", "120"))

# How much of the map the narrative call is allowed to see. A two-hour meeting
# must cost about what a ten-minute one costs (rule 79's reasoning), and a
# summary written from forty fragments is not better than one written from
# twelve — it is just more expensive and longer.
ITEMS_PER_LIST = int(os.getenv("CONSULTATION_REPORT_ITEMS_PER_LIST", "12"))

# The lists worth narrating, in the order a reader wants them. Deliberately
# fewer than the map's ten: "assumptions not yet established" and "questions to
# investigate" are working categories that earn their place while the group is
# consulting and clutter a report afterwards. They remain in the full record.
NARRATED_LISTS = (
    ("agreements", "Where the group agreed"),
    ("tensions", "Where it did not"),
    ("ideas", "Ideas considered"),
    ("needs_and_concerns", "Concerns raised"),
    ("possible_syntheses", "Possible ways through"),
    ("unresolved_questions", "Open questions"),
    ("facts", "Facts established"),
    ("principles", "Principles brought to bear"),
)


def _items(state: dict, key: str, cap: int = ITEMS_PER_LIST) -> list[str]:
    out = []
    for item in (state.get(key) or []):
        if isinstance(item, dict) and (item.get("text") or "").strip():
            out.append(item["text"].strip())
    return out[:cap]


def _map_digest(state: dict) -> str:
    """The map, compressed, for the narrative call only."""
    blocks = []
    for key, label in NARRATED_LISTS:
        items = _items(state, key)
        if items:
            blocks.append(label + ":\n" + "\n".join("- " + i for i in items))
    return "\n\n".join(blocks)


def _minutes(session: dict, turns: list[dict]) -> str:
    started, ended = session.get("started_at"), session.get("ended_at")
    if not (started and ended):
        return ""
    from datetime import datetime
    try:
        a = datetime.strptime(started, "%Y-%m-%d %H:%M:%S")
        b = datetime.strptime(ended, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return ""
    mins = max(0, int((b - a).total_seconds() // 60))
    return f"{mins} minutes" if mins else "under a minute"


# ── The narrative half (a model writes this) ────────────────────────────────

_NARRATIVE_SYSTEM = """You are writing the record of a consultation for the people who took part
and for anyone they send it to. Write plainly, in the past tense, in British English.

WHAT YOU ARE DOING
Condensing. You will be given the working notes of a meeting as lists of fragments. Turn
them into a few short paragraphs that someone can read in a minute. Group related points
instead of listing them. Leave out anything trivial. Never write a bulleted restatement of
the lists you were given -- that is the thing this replaces.

WHAT YOU MUST NOT DO
- Do NOT state, imply or summarise any DECISION. You will not be told what was decided,
  and you must not guess. If the group appeared to be converging, say they were converging.
- Do NOT assign any action, owner or deadline to anybody. Not even one that seems obvious.
- Do NOT attribute any idea, view or concern to a named person. Ideas belong to the group.
- Do NOT invent detail that is not in the notes. If the notes are thin, write less.
- Do NOT quote scripture or any religious text.
- Do NOT address the reader, and do not refer to yourself.

Return ONLY a JSON object with these string fields, and nothing else:
  "in_short":   2-4 sentences. What this meeting was about and where it got to.
  "discussion": 1-3 short paragraphs. How the consultation actually went: what was weighed,
                where it converged, where it did not. Plain prose, no bullet points.
  "still_open": 1 short paragraph, or "" if nothing is open. What remains unresolved."""


def _narrative_messages(session: dict, state: dict, digest: str) -> list[dict]:
    frame = FRAMEWORKS.get(session.get("framework") or "", "")
    header = [
        f"Title: {session.get('title') or 'Consultation'}",
        f"The question before the group: {session.get('question') or '(not stated)'}",
    ]
    if frame:
        header.append(f"Framework: {frame}")
    if (session.get("context") or "").strip():
        header.append(f"Context the group gave beforehand: {session['context'].strip()}")
    summary = (state.get("summary") or "").strip()
    if summary:
        header.append(f"Running summary kept during the meeting: {summary}")
    body = "\n".join(header) + "\n\nTHE WORKING NOTES\n\n" + (digest or "(no notes were captured)")
    return [
        {"role": "system", "content": _NARRATIVE_SYSTEM},
        {"role": "user", "content": body},
    ]


def _narrative(session: dict, state: dict, call=None) -> tuple[dict, str]:
    """Returns (fields, note). A failure returns empty fields and a plain reason."""
    digest = _map_digest(state)
    if not digest and not (state.get("summary") or "").strip():
        return {}, "There was not enough in the record to summarise."
    messages = _narrative_messages(session, state, digest)
    if call is None:
        from agents.router import call_openai as _default_call

        def call(msgs):  # noqa: E306
            return _default_call(msgs,
                                 model=session.get("reasoning_model") or REASONING_MODEL,
                                 temperature=0.3, max_tokens=MAX_TOKENS,
                                 json_mode=True, timeout=TIMEOUT_S)
    try:
        raw = call(messages)
    except Exception as e:
        return {}, f"The summary could not be written ({type(e).__name__}). Everything else is exact."
    from agents.live_consultation_reasoner import _extract_json
    data = _extract_json(raw or "")
    if not isinstance(data, dict):
        return {}, "The summary came back unreadable. Everything else is exact."
    # Only the three prose fields are ever taken. Anything else the model chose
    # to return -- a decisions list, an actions list -- is dropped on the floor
    # rather than trusted, which is the guarantee this module exists for.
    fields = {}
    for key in ("in_short", "discussion", "still_open"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            fields[key] = value.strip()
    if not fields:
        return {}, "The summary came back empty. Everything else is exact."
    return fields, ""


# ── The whole document ──────────────────────────────────────────────────────

def build_report(session: dict, state: dict, decisions: list[dict], actions: list[dict],
                 writings: list[dict], turns: list[dict], participants: list[dict],
                 call=None, narrative: dict | None = None) -> dict:
    """
    Assemble the report. Returns {markdown, note, narrated, narrative}.

    The deterministic half is built first and completely, so a model failure
    subtracts prose from a working document rather than producing nothing.

    `narrative` re-uses prose that was already written and paid for (rule 102).
    The RECORD half -- decisions, commitments, owners, acceptance, passages --
    is copied verbatim from the store on every build, so correcting an owner and
    rebuilding gives a correct report with no model call at all. Without this
    the only way to get a changed fact into the report was to pay for the prose
    again, which is why a "Write it again" button had become load-bearing for
    something that is not a writing problem.
    """
    if narrative:
        # `_narrative` writes "discussion"; this reuse path read
        # "how_we_got_here" — a key that field was never written under — so
        # every reuse (every `/report/approve`, every closeout approval,
        # every `reuse_narrative=True` rebuild) silently dropped the "How the
        # group got there" section. The old name is kept as a fallback in case
        # any already-stored `report_narrative_json` was ever written under
        # it, rather than assumed to have never existed.
        fields = {
            "in_short": str(narrative.get("in_short") or "").strip(),
            "discussion": str(narrative.get("discussion")
                             or narrative.get("how_we_got_here") or "").strip(),
            "still_open": str(narrative.get("still_open") or "").strip(),
        }
        note = ""
    else:
        fields, note = _narrative(session, state, call=call)
    title = session.get("title") or "Consultation"
    lines: list[str] = [f"# {title}", ""]

    meta = []
    when = session.get("started_at") or session.get("created_at") or ""
    if when:
        meta.append(when)
    length = _minutes(session, turns)
    if length:
        meta.append(length)
    frame = FRAMEWORKS.get(session.get("framework") or "", "")
    if frame:
        meta.append(frame)
    if meta:
        lines += ["*" + " · ".join(meta) + "*", ""]

    if (session.get("question") or "").strip():
        lines += ["**The question before the group**", "", session["question"].strip(), ""]

    named = [p["name"] for p in participants if (p.get("name") or "").strip()]
    if named:
        lines += ["**Present:** " + ", ".join(named), ""]

    if fields.get("in_short"):
        lines += ["## In short", "", fields["in_short"], ""]

    # ── The record: verbatim, never model-written ───────────────────────────
    # A consultation may settle more than one thing, so this is a LIST now. The
    # old singular read of `next(... confirmed)` silently printed only the
    # first (rule 98).
    confirmed = [d for d in decisions if d.get("status") == "confirmed"]
    lines += ["## What was decided", ""]
    if confirmed:
        method = DECISION_METHODS.get(session.get("decision_method") or "", "")
        for d in confirmed:
            lines.append(("- " if len(confirmed) > 1 else "") + d.get("text", "").strip())
            if (d.get("rationale") or "").strip():
                lines += ["", "*Why:* " + d["rationale"].strip()]
            # A decision can be confirmed AND leave real dissent standing. It
            # is printed WITH the decision, not in a footnote: burying it under
            # "unity" is the specific failure this whole feature exists to
            # avoid (rule 98).
            retained = [c for c in (d.get("retained_concerns") or []) if str(c).strip()]
            if retained:
                lines += ["", "*Concerns the group chose to carry forward with this "
                              "decision:*"]
                lines += [f"  - {str(c).strip()}" for c in retained]
            lines.append("")
        if method:
            lines += [f"*How the group decided:* {method}", ""]
    else:
        outcome = session.get("closeout_outcome")
        # The group's OWN account of how it ended, when they gave one. "No
        # decision was reached" is a truthful outcome, and saying which kind of
        # nothing happened is more useful than a blank.
        lines.append(CLOSEOUT_OUTCOMES.get(outcome or "", "")
                     if outcome else "No decision was confirmed in this meeting.")
        if (session.get("closeout_note") or "").strip():
            lines += ["", session["closeout_note"].strip()]
        pending = [d for d in decisions if d.get("status") == "candidate"]
        if pending:
            lines += ["", "Something that sounded like a decision was noticed but never "
                          "confirmed by the group:"]
            lines += ["", *[f"- {d.get('text','').strip()}" for d in pending if d.get("text")]]
    lines.append("")

    lines += ["## What happens next", ""]
    listed = [a for a in actions
              if (a.get("action") or "").strip() and a.get("status") != "dropped"]
    if listed:
        for a in listed:
            owner = (a.get("owner") or "").strip() or "Owner not assigned"
            # A name is a PROPOSAL until somebody records that the person
            # accepted (rule 95). The three states are printed differently
            # because they are three different facts, and reading "Tara" with
            # no qualifier as agreement is exactly the mistake to prevent.
            # Read by VALUE, not identity. SQLite hands back 1/0 rather than
            # True/False, and `1 is True` is False in Python -- which printed
            # the self-contradicting "Tara - not yet accepted - accepted".
            # None still has to stay distinct from False: "nobody has recorded
            # an answer" is a different fact from "asked, and declined".
            accepted = a.get("owner_accepted")
            if (a.get("owner") or "").strip():
                if accepted is None:
                    owner += " — not yet accepted"
                elif accepted:
                    by = (a.get("accepted_by") or "").strip()
                    owner += " — accepted" + (f" (recorded by {by})" if by else "")
                else:
                    owner += " — did not accept"
            due = (a.get("due") or "").strip()
            status = a.get("status") or "proposed"
            mark = f" *({ACTION_STATUS_LABELS.get(status, status).lower()})*" \
                if status not in ("proposed", "accepted") else ""
            lines.append(f"- **{a['action'].strip()}** — {owner}"
                         + (f", due {due}" if due else "") + mark)
            if (a.get("success_criteria") or "").strip():
                lines.append(f"  - *Done means:* {a['success_criteria'].strip()}")
            if (a.get("blocker") or "").strip():
                lines.append(f"  - *Blocked:* {a['blocker'].strip()}")
            if (a.get("support_needed") or "").strip():
                lines.append(f"  - *Support needed:* {a['support_needed'].strip()}")
    else:
        lines.append("No action items were recorded.")
    lines.append("")

    if fields.get("discussion"):
        lines += ["## How the group got there", "", fields["discussion"], ""]

    if fields.get("still_open"):
        lines += ["## Still open", "", fields["still_open"], ""]

    if writings:
        lines += ["## Passages referred to", ""]
        for w in writings:
            text = (w.get("text") or "").strip()
            if not text:
                continue
            lines.append("> " + text.replace("\n", "\n> "))
            source = " — ".join([x for x in [w.get("source"), w.get("section")] if x])
            if source:
                lines += ["", f"*{source}*"]
            lines.append("")

    lines += ["---", ""]
    footnote = (
        f"Written by {ASSISTANT_NAME} from the record of the meeting. The decision, the "
        "action items and any passages above are exactly as they were recorded; the "
        "summarising sections were written from the working notes."
    )
    if session.get("transcript_deleted_at"):
        footnote += (" The transcript of this meeting was deleted on "
                     f"{session['transcript_deleted_at']}; this record is what remains.")
    if note:
        footnote += f" **{note}**"
    lines += [footnote, ""]

    return {
        "markdown": "\n".join(lines).rstrip() + "\n",
        "note": note,
        "narrated": bool(fields),
        # Handed back so it can be stored and re-used: rebuilding the record
        # sections must never require paying for the prose again (rule 102).
        "narrative": dict(fields),
    }
