"""
The Speech Governor and the floor state machine (rules 75-77).

This module answers one question — MAY THE ASSISTANT SPEAK RIGHT NOW? — and its
default answer is no. It is deliberately dependency-free, deterministic and
pure: no clock of its own, no database, no model. Everything it needs arrives
in the request, so every rule in it can be tested by calling it.

The rule the whole feature stands on:

    SILENCE IS NOT PERMISSION FOR THE AI TO SPEAK.

There is no branch below in which elapsed silence alone yields allowed=True for
an unsolicited contribution — no matter how long. Silence can be thought,
prayer, grief, courtesy or a person gathering a difficult sentence. The
assistant waits. Turn detection produces EVIDENCE that a turn ended; this
module decides what may be done about it, and the reasoning model has no vote:
an observation saying `should_request_floor: true` means "this might be useful",
never "speak".

WHERE THIS RUNS. The browser holds the realtime events, so the enforcement that
matters — cancelling AI audio the instant a human starts talking — is in
`dashboard/src/lib/consultationGovernor.ts`, which mirrors this file. This one
is the specification and the second gate: the dashboard must also get an
`allow` from `POST /live-consultation/sessions/{id}/speech-permission` before
it triggers a response, and the policy numbers the client uses are SERVED from
here (`policy()`), never written down twice.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

from agents.live_consultation import DEFAULT_PRESENCE, MODES, PRESENCE_LEVELS

# ── Floor states ────────────────────────────────────────────────────────────

DISCONNECTED = "disconnected"
LISTENING_IDLE = "listening_idle"
HUMAN_SPEAKING = "human_speaking"
HUMAN_REFLECTIVE_PAUSE = "human_reflective_pause"
FLOOR_OPEN = "floor_open"
AI_REQUEST_QUEUED = "ai_request_queued"
AI_PERMISSION_PENDING = "ai_permission_pending"
AI_PREPARING = "ai_preparing"
AI_SPEAKING = "ai_speaking"
LISTENING_PAUSED = "listening_paused"
RECONNECTING = "reconnecting"

FLOOR_STATES = (
    DISCONNECTED, LISTENING_IDLE, HUMAN_SPEAKING, HUMAN_REFLECTIVE_PAUSE, FLOOR_OPEN,
    AI_REQUEST_QUEUED, AI_PERMISSION_PENDING, AI_PREPARING, AI_SPEAKING,
    LISTENING_PAUSED, RECONNECTING,
)

# States in which a human is understood to hold the floor. A reflective pause is
# in here on purpose: the person who stopped talking has not given the floor up.
HUMAN_HOLDS_FLOOR = (HUMAN_SPEAKING, HUMAN_REFLECTIVE_PAUSE)


def _ms(name: str, default: int) -> int:
    try:
        return max(0, int(float(os.getenv(name, str(default)))))
    except (TypeError, ValueError):
        return default


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# ── Policy (configurable, never magic numbers in the branches) ──────────────
#
# These are the ATTENTIVE baseline; a session's `presence` scales them (rule
# 87). They were loosened on 2026-08-21 after the first real session — Sheraj
# found the originals "a little too unresponsive", and he is right that a
# six-second wait before a floor could even be considered open is a formal
# body's pace, not a working meeting's. The `reserved` preset keeps the
# original feel for anyone who wants it.

# A pause this long stops being a breath and becomes a pause. Nothing is
# allowed at this point — it only changes what the screen says, so the room can
# see that the silence is deliberate.
REFLECTIVE_PAUSE_MS = _ms("CONSULTATION_REFLECTIVE_PAUSE_MS", 1200)

# The EARLIEST moment at which a floor may be considered open. Reaching it is
# necessary and nowhere near sufficient.
FLOOR_OPEN_MS = _ms("CONSULTATION_FLOOR_OPEN_MS", 3000)

# After someone says "Abigail, ...", still wait a beat: people often carry on.
# Short, because this is the delay a person FEELS when they have just asked her
# a direct question and are waiting for her to answer.
INVITED_GRACE_MS = _ms("CONSULTATION_INVITED_GRACE_MS", 400)

# A button press while someone is talking waits for the floor, then this.
QUEUED_ASK_GRACE_MS = _ms("CONSULTATION_QUEUED_ASK_GRACE_MS", 900)

# No unsolicited anything in the opening minutes; a consultation needs to find
# its own feet before an assistant offers to help it.
UNSOLICITED_WARMUP_MS = _ms("CONSULTATION_UNSOLICITED_WARMUP_MS", 45_000)

# Between one permission request and the next.
UNSOLICITED_COOLDOWN_MS = _ms("CONSULTATION_UNSOLICITED_COOLDOWN_MS", 120_000)

# Extra quiet after a no, or after a request nobody answered.
DENIED_COOLDOWN_MS = _ms("CONSULTATION_DENIED_COOLDOWN_MS", 300_000)

# How long an unanswered request for the floor stands before it expires. It is
# never repeated: silence after a request for permission is a no (rule 75).
PERMISSION_TIMEOUT_MS = _ms("CONSULTATION_PERMISSION_TIMEOUT_MS", 15_000)

# How good an observation has to be before she may even ask.
MIN_IMPORTANCE = {
    "facilitator": _f("CONSULTATION_MIN_IMPORTANCE", 0.62),
    "active": _f("CONSULTATION_MIN_IMPORTANCE_ACTIVE", 0.45),
}

# How far the consultation may have moved on since an observation was formed.
# 0 means the state must not have changed at all: the meeting is authoritative
# and a point about a conversation that has already moved is worse than nothing.
STALE_REVISIONS = int(_f("CONSULTATION_STALE_REVISIONS", 0))


def resolve_policy(presence: str = DEFAULT_PRESENCE) -> dict:
    """
    The baseline numbers with this session's presence applied (rule 87).

    One function, used by `evaluate` AND served to the browser, so the two
    governors cannot disagree about how long a pause is. Every preset runs the
    same predicate in the same order — presence changes how long she waits and
    how good an observation must be, never WHETHER silence can permit speech.
    """
    level = PRESENCE_LEVELS.get(presence) or PRESENCE_LEVELS[DEFAULT_PRESENCE]
    waits, cools = float(level["waits"]), float(level["cooldowns"])
    shift = float(level["importance"])
    return {
        "presence": presence if presence in PRESENCE_LEVELS else DEFAULT_PRESENCE,
        "reflective_pause_ms": int(REFLECTIVE_PAUSE_MS * waits),
        "floor_open_ms": int(FLOOR_OPEN_MS * waits),
        "invited_grace_ms": int(INVITED_GRACE_MS * waits),
        "queued_ask_grace_ms": int(QUEUED_ASK_GRACE_MS * waits),
        "unsolicited_warmup_ms": int(UNSOLICITED_WARMUP_MS * cools),
        "unsolicited_cooldown_ms": int(UNSOLICITED_COOLDOWN_MS * cools),
        "denied_cooldown_ms": int(DENIED_COOLDOWN_MS * cools),
        "permission_timeout_ms": PERMISSION_TIMEOUT_MS,
        "min_importance": {mode: max(0.0, min(1.0, base + shift))
                           for mode, base in MIN_IMPORTANCE.items()},
        "stale_revisions": STALE_REVISIONS,
    }


REQUEST_KINDS = ("invited", "queued_ask", "unsolicited", "permission_granted")


def policy(presence: str = DEFAULT_PRESENCE) -> dict:
    """The numbers, for the client governor and the capabilities endpoint. The
    browser must not carry its own copy of these — one source, served."""
    return resolve_policy(presence)


# ── The request and the answer ──────────────────────────────────────────────

@dataclass
class SpeechRequest:
    """Everything the decision may depend on. Times are milliseconds, measured
    by the caller; this module never reads a clock, which is what makes every
    rule below reproducible in a test."""
    kind: str = "unsolicited"
    mode: str = "facilitator"
    muted: bool = False
    listening_paused: bool = False
    connected: bool = True
    floor_state: str = LISTENING_IDLE
    human_speaking: bool = False
    ms_since_human_speech_ended: Optional[int] = None
    ms_since_session_start: int = 0
    ms_since_invitation: Optional[int] = None
    permission_pending: bool = False
    ms_since_last_intervention: Optional[int] = None
    ms_since_last_denial: Optional[int] = None
    observation_importance: Optional[float] = None
    observation_should_request_floor: bool = False
    observation_status: str = "open"
    observation_revision: Optional[int] = None
    current_revision: Optional[int] = None
    # How quick she is, for this session (rule 87). Never changes the ORDER of
    # the checks below, only the numbers a few of them compare against.
    presence: str = DEFAULT_PRESENCE

    @classmethod
    def from_dict(cls, data: dict) -> "SpeechRequest":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


@dataclass
class Decision:
    allowed: bool
    action: str            # "speak" | "request_permission" | "wait" | "refuse"
    code: str
    reason: str
    retry_after_ms: Optional[int] = None
    checks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed, "action": self.action, "code": self.code,
            "reason": self.reason, "retry_after_ms": self.retry_after_ms,
            "checks": list(self.checks),
        }


def _refuse(code: str, reason: str, checks: list[str]) -> Decision:
    return Decision(False, "refuse", code, reason, None, checks)


def _wait(code: str, reason: str, checks: list[str], retry_ms: int | None = None) -> Decision:
    return Decision(False, "wait", code, reason, retry_ms, checks)


def evaluate(req: SpeechRequest | dict) -> Decision:
    """
    The gate. Ordered so that the categorical refusals come first: a muted or
    scribe-mode session is answered without ever consulting the floor, because
    no amount of good conversational evidence should be able to reach that path.
    """
    if isinstance(req, dict):
        req = SpeechRequest.from_dict(req)
    checks: list[str] = []
    kind = req.kind if req.kind in REQUEST_KINDS else "unsolicited"
    pol = resolve_policy(req.presence)

    # 1. Categorical: this session, or this moment, has no voice at all.
    mode = MODES.get(req.mode, MODES["facilitator"])
    if not mode.get("speaks", True):
        return _refuse("scribe_mode",
                       "This session is in scribe mode: she never speaks.", checks)
    checks.append("mode_speaks")
    if req.muted:
        return _refuse("muted", "She is muted. She is still listening.", checks)
    checks.append("not_muted")
    if req.listening_paused:
        return _refuse("listening_paused",
                       "Listening is paused, so there is nothing to answer.", checks)
    checks.append("listening_on")
    if not req.connected or req.floor_state in (DISCONNECTED, RECONNECTING):
        return _refuse("not_connected", "Not connected to the realtime service.", checks)
    checks.append("connected")

    # 2. A human holds the floor. Nothing gets past this — not an invitation,
    #    not a button, not the best observation ever formed.
    if req.human_speaking or req.floor_state == HUMAN_SPEAKING:
        return _wait("human_speaking", "Someone is speaking.", checks)
    checks.append("no_human_speaking")
    if req.floor_state == AI_SPEAKING and kind != "permission_granted":
        return _wait("already_speaking", "She already has the floor.", checks)

    since_human = req.ms_since_human_speech_ended
    reflective = (since_human is not None and since_human < pol["floor_open_ms"])

    # 3. An answer to an invitation, or to a granted permission. The person
    #    asked; the only question left is whether the floor is actually free.
    if kind in ("invited", "queued_ask", "permission_granted"):
        grace = pol["invited_grace_ms"] if kind == "invited" else pol["queued_ask_grace_ms"]
        if kind == "permission_granted":
            grace = pol["invited_grace_ms"]
        if since_human is not None and since_human < grace:
            return _wait("grace", "Waiting a moment in case the speaker continues.",
                         checks, grace - since_human)
        checks.append("grace_elapsed")
        if kind == "invited" and req.ms_since_invitation is not None \
                and req.ms_since_invitation < pol["invited_grace_ms"]:
            return _wait("invitation_grace",
                         "Waiting a moment after the invitation in case they carry on.",
                         checks, pol["invited_grace_ms"] - req.ms_since_invitation)
        return Decision(True, "speak", "invited",
                        "A person asked her to speak.", None, checks)

    # 4. Unsolicited. Everything from here is about restraint.
    if not mode.get("unsolicited", False):
        return _refuse("mode_no_unsolicited",
                       "In this mode she only speaks when asked.", checks)
    checks.append("mode_allows_unsolicited")

    if req.permission_pending or req.floor_state == AI_PERMISSION_PENDING:
        return _refuse("permission_pending",
                       "She has already asked for the floor and is waiting.", checks)
    checks.append("no_pending_request")

    if req.ms_since_session_start < pol["unsolicited_warmup_ms"]:
        return _wait("warmup", "Too early in the session to offer anything unasked.",
                     checks, pol["unsolicited_warmup_ms"] - req.ms_since_session_start)
    checks.append("warmed_up")

    # The denial is checked BEFORE the ordinary cooldown, even though both
    # refuse: asking again is refused for a better reason if the group has
    # already said no, and the reason is what the room is shown.
    if req.ms_since_last_denial is not None \
            and req.ms_since_last_denial < pol["denied_cooldown_ms"]:
        return _wait("denied_cooldown",
                     "The group did not want the last offer; she leaves it longer.",
                     checks, pol["denied_cooldown_ms"] - req.ms_since_last_denial)
    checks.append("denial_cooldown_elapsed")

    if req.ms_since_last_intervention is not None \
            and req.ms_since_last_intervention < pol["unsolicited_cooldown_ms"]:
        return _wait("cooldown", "She spoke recently, so she waits.",
                     checks, pol["unsolicited_cooldown_ms"] - req.ms_since_last_intervention)
    checks.append("cooldown_elapsed")

    # The observation itself. THIS is what makes an intervention possible —
    # never the passage of time.
    if not req.observation_should_request_floor:
        return _refuse("nothing_to_say",
                       "Nothing has been noticed that is worth interrupting for.", checks)
    if req.observation_status not in ("open",):
        return _refuse("observation_not_open",
                       "That observation has already been dealt with.", checks)
    threshold = pol["min_importance"].get(req.mode, pol["min_importance"]["facilitator"])
    if (req.observation_importance or 0.0) < threshold:
        return _refuse("below_threshold",
                       "What was noticed is not important enough to interrupt for.", checks)
    checks.append("observation_material")

    # Staleness (rule 77): the meeting is authoritative. A point formed against
    # an older reading of the consultation is discarded, not spoken.
    if req.observation_revision is not None and req.current_revision is not None:
        if (req.current_revision - req.observation_revision) > pol["stale_revisions"]:
            return _refuse("stale",
                           "The consultation has moved on since that was noticed.", checks)
    checks.append("fresh")

    # The floor. Note the ORDER: this is the LAST check, and it can only ever
    # withhold permission — passing it grants nothing on its own, because
    # everything above had to pass first.
    if since_human is None:
        return _wait("floor_unknown", "Waiting for a natural opening.", checks)
    if reflective:
        return _wait("reflective_pause",
                     "Someone is thinking. A pause belongs to the person who paused.",
                     checks, pol["floor_open_ms"] - since_human)
    checks.append("floor_open")

    return Decision(True, "request_permission", "may_request_floor",
                    "She may briefly ask whether what she noticed would help.",
                    None, checks)


# ── The floor state machine ─────────────────────────────────────────────────

# Events that can arrive from the realtime connection or from a button.
EVENTS = (
    "connected", "disconnected", "reconnecting",
    "human_speech_started", "human_speech_stopped",
    "reflective_elapsed", "floor_open_elapsed",
    "ask_queued", "permission_requested", "permission_granted", "permission_denied",
    "permission_expired", "ai_preparing", "ai_speech_started", "ai_speech_done",
    "ai_cancelled", "listening_paused", "listening_resumed",
)


def advance(state: str, event: str) -> str:
    """
    The transition table. Written as one function, not a dict of dicts, because
    the precedence between events is the whole point: human speech is checked
    FIRST and overrides every other state, which is the barge-in guarantee
    (rule 76) rather than a row in a table someone can later reorder.
    """
    if state not in FLOOR_STATES:
        state = LISTENING_IDLE

    # Connection and listening come before anything conversational.
    if event == "disconnected":
        return DISCONNECTED
    if event == "reconnecting":
        return RECONNECTING
    if event == "connected":
        return LISTENING_IDLE if state in (DISCONNECTED, RECONNECTING) else state
    if event == "listening_paused":
        return LISTENING_PAUSED
    if event == "listening_resumed":
        return LISTENING_IDLE
    if state in (DISCONNECTED, LISTENING_PAUSED):
        return state

    # A human starting to speak wins from ANY state, including mid-AI-sentence.
    if event == "human_speech_started":
        return HUMAN_SPEAKING
    if event == "human_speech_stopped":
        return HUMAN_REFLECTIVE_PAUSE if state == HUMAN_SPEAKING else state
    if event == "reflective_elapsed":
        return HUMAN_REFLECTIVE_PAUSE if state == HUMAN_SPEAKING else state
    if event == "floor_open_elapsed":
        # Only ever a change of LABEL. Reaching this state grants nothing —
        # evaluate() still has to say yes, and for an unsolicited contribution
        # it says no unless something material was noticed.
        return FLOOR_OPEN if state in (HUMAN_REFLECTIVE_PAUSE, LISTENING_IDLE) else state

    if event == "ask_queued":
        return AI_REQUEST_QUEUED if state in HUMAN_HOLDS_FLOOR else state
    if event == "permission_requested":
        return AI_PERMISSION_PENDING
    if event == "permission_granted":
        return AI_PREPARING
    if event in ("permission_denied", "permission_expired"):
        return LISTENING_IDLE if state == AI_PERMISSION_PENDING else state
    if event == "ai_preparing":
        return AI_PREPARING
    if event == "ai_speech_started":
        return AI_SPEAKING
    if event in ("ai_speech_done", "ai_cancelled"):
        return LISTENING_IDLE
    return state


# Human-readable state, for the indicator that tells the room the silence is
# deliberate rather than a crash.
STATE_LABELS = {
    DISCONNECTED: "Not connected",
    RECONNECTING: "Reconnecting",
    LISTENING_PAUSED: "Listening paused",
    LISTENING_IDLE: "Listening silently",
    HUMAN_SPEAKING: "Someone is speaking",
    HUMAN_REFLECTIVE_PAUSE: "Reflective pause — she will not interrupt",
    FLOOR_OPEN: "Listening silently",
    AI_REQUEST_QUEUED: "Will answer when the floor is free",
    AI_PERMISSION_PENDING: "Waiting for permission",
    AI_PREPARING: "Preparing a short response",
    AI_SPEAKING: "Speaking",
}


# ── Direct address (rule 76) ───────────────────────────────────────────────

# Deliberately conservative. "I think AI is going to transform education" is
# meeting content and must never be read as a command, so a wake word only
# counts at the start of an utterance or sentence, and must be followed by
# punctuation or an actual request.
_WAKE = r"(?:ai|assistant|consultation assistant)"
_LEAD = r"(?:hey|ok|okay|so)\s+"
_ASK_VERB = (r"(?:can|could|would|will|please|what|which|where|when|how|why|who|"
             r"summar\w+|tell|give|help|find|list|remind|show|read|do|is|are|any)")
_DIRECT_ADDRESS = re.compile(
    rf"(?:^|[.!?]\s+)(?:{_LEAD})?{_WAKE}\s*(?:,|:|\s+{_ASK_VERB}\b)",
    re.IGNORECASE,
)


def is_direct_address(text: str) -> bool:
    """True only for something genuinely aimed at the assistant."""
    if not text:
        return False
    return bool(_DIRECT_ADDRESS.search(text.strip()))


_YES = re.compile(r"^\s*(?:yes|yeah|yep|sure|ok|okay|go ahead|please do|please|"
                  r"go on|do it|let's hear it|i'd like that|that would help)\b", re.IGNORECASE)
_NO = re.compile(r"^\s*(?:no|nope|not now|not yet|later|hold on|hold off|"
                 r"let's not|maybe later|wait)\b", re.IGNORECASE)


def permission_answer(text: str) -> Optional[bool]:
    """
    True for yes, False for no, None for anything else — and None is the common
    case on purpose. Only a clear answer counts; an ambiguous reply leaves the
    request standing until it expires unanswered, which is itself a no.
    """
    if not text:
        return None
    if _YES.search(text):
        return True
    if _NO.search(text):
        return False
    return None
