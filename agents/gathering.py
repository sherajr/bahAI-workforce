"""
Gatherings: from preparing, through consulting, to reflecting (rule 117).

A **project** is the thread that ties a piece of community service together —
the question the group sat down with, the consultation they held, what they
decided, who took something on, the materials they printed, and what they
learned afterwards. A devotional gathering is the first worked example; nothing
here is devotional-specific, because a study circle, a children's class, a
service project or a neighbourhood visit has the same shape.

WHAT THIS IS NOT
----------------
It is not a second task database. Commitments are the consultation's OWN action
items (rules 95/101) — the same rows, with the same acceptance semantics, read
through a project instead of copied into one. A project that has held no
consultation has no commitments yet, and that is the truthful answer rather than
a parallel list that can disagree with the record.

It is not a formula. The stages below are a description of how this work
actually goes, not a prescription, and a project may sit in any of them for as
long as the group needs. Nothing here scores anybody, ranks anybody, or counts a
streak: the numbers this application keeps point at what was made and given, and
never at a person's spiritual condition (rule 61).

It is not a decision-maker. Only outcomes a human APPROVED at a closeout carry
forward into the project, and only the ones somebody explicitly selected.
"""

from __future__ import annotations

from typing import Optional

# Statuses that still represent work somebody expects to happen. The same list
# the consultation uses, re-exported rather than restated so a project view and
# a session view can never disagree about what "still open" means.
from agents.live_consultation import LIVE_ACTION_STATUSES as LIVE_STATUSES

# How a piece of service actually moves. Data, not a funnel: a project can go
# back a stage (a gathering that raised a new question goes back to consulting),
# and `closed` is a resting place rather than a success condition.
STAGES: dict[str, dict] = {
    "preparing": {
        "label": "Preparing",
        "blurb": "Working out the purpose, the question, and what is needed.",
    },
    "consulting": {
        "label": "Consulting",
        "blurb": "The group is deciding together what to do.",
    },
    "ready": {
        "label": "Ready",
        "blurb": "Decided, and the materials are prepared.",
    },
    "held": {
        "label": "Held",
        "blurb": "The gathering happened.",
    },
    "reflecting": {
        "label": "Reflecting",
        "blurb": "Looking at what was learned and what to try next.",
    },
    "closed": {
        "label": "Closed",
        "blurb": "Finished, and kept as a record.",
    },
}
DEFAULT_STAGE = "preparing"

# What a project may carry forward from a consultation. Deliberately short:
# raw transcript and private observations are NOT on this list and never become
# standing context for a later meeting (rule 118).
OUTCOME_KINDS: dict[str, str] = {
    "decision": "A decision the group confirmed",
    "question": "A question left open, chosen for follow-up",
    "concern": "A concern the group wants kept in view",
}

# One entry in a gathering's programme. `kind` decides how it prints, and
# `reading` is the only one that may carry a verified passage.
PROGRAM_KINDS: dict[str, str] = {
    "reading": "A verified passage",
    "prayer": "A prayer (named, not generated)",
    "reflection": "A question for the group",
    "music": "Music",
    "welcome": "Welcome and introductions",
    "refreshments": "Refreshments and conversation",
    "note": "A note for whoever is hosting",
}
DEFAULT_PROGRAM_KIND = "note"

# A programme is a page somebody prints and holds. These are the practical
# limits of that, not arbitrary caps.
MAX_PROGRAM_ITEMS = 40
MAX_TITLE = 200
MAX_BODY = 4000


def normalize_stage(value: str) -> str:
    stage = (value or "").strip().lower()
    return stage if stage in STAGES else DEFAULT_STAGE


def normalize_program_kind(value: str) -> str:
    kind = (value or "").strip().lower()
    return kind if kind in PROGRAM_KINDS else DEFAULT_PROGRAM_KIND


def normalize_outcome_kind(value: str) -> Optional[str]:
    kind = (value or "").strip().lower()
    return kind if kind in OUTCOME_KINDS else None


def program_minutes(items: list[dict]) -> Optional[int]:
    """The programme's length, or None when nobody supplied timings.

    None is a real answer. A gathering whose host did not put times against the
    activities does not have a length, and inventing one — by assuming five
    minutes an item, say — would put a number on the printed page that nobody
    chose and the group would then feel measured against.
    """
    supplied = [int(i.get("minutes") or 0) for i in items if i.get("minutes")]
    return sum(supplied) if supplied else None


def kit_readiness(items: list[dict], program: list[dict]) -> dict:
    """What the kit has, what it is missing, and whether it can be produced.

    Says what is WRONG rather than only whether it is ready: an empty wizard
    that reports "not ready" is the failure this is written against.
    """
    printable = [i for i in items if i.get("front_image") and i.get("back_image")]
    missing_faces = [i for i in items
                     if not (i.get("front_image") and i.get("back_image"))]
    kinds = {(i.get("product_type") or "bookmark") for i in printable}
    problems = []
    if missing_faces:
        problems.append(
            f"{len(missing_faces)} selected item(s) have not been rendered yet, so they "
            "cannot go on a print sheet.")
    if len(kinds) > 1:
        # Same constraint the existing mixed print-sheet endpoint enforces.
        problems.append(
            "Bookmarks and quote cards cannot share one sheet — they are different "
            "sizes. Print them as two sheets.")
    return {
        "cards": len(printable),
        "program_items": len(program),
        "can_print_cards": bool(printable) and len(kinds) <= 1,
        "can_print_program": bool(program),
        "problems": problems,
    }
