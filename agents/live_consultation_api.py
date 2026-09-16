"""
The Live Consultation HTTP surface — an APIRouter that `agents/api.py` includes.

Kept out of `api.py` on purpose: that file is already 6,900 lines, and a
subsystem with its own private store, its own gate and its own model deserves
its own file. The owner gate still covers every route here, because
`auth.api_key_middleware` sits on the app rather than on any router (rule 70) —
these paths are not in `PUBLIC_PATHS`, so they refuse an unauthenticated call
like everything else, and `scripts/test_api_auth.py` walks the route table and
proves it without anyone adding them to a list.

Nothing personal crosses out of here: transcripts, state and observations live
in `private/consultation.db` and are returned to the dashboard, never written to
`workforce.db`, a job progress string, or stdout (rules 15/73). The one thing
that does cross is money — a realtime response's token usage is metered into the
Steward's ledger with no meeting content attached.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import PlainTextResponse, Response
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel

from agents import live_consultation as core
from agents import live_consultation_audio as audio
from agents import live_consultation_governor as governor
from agents import live_consultation_graph as graph
from agents import live_consultation_report as report
from agents import live_consultation_realtime as realtime
from agents import live_consultation_reasoner as reasoner
from agents import live_consultation_store as store
from agents import live_consultation_writings as writings

router = APIRouter(prefix="/live-consultation", tags=["live-consultation"])

# One analysis at a time per session. Two overlapping passes would both read the
# same "unanalysed" turns and pay twice for one thought.
_ANALYSIS_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_LAST_ANALYSIS: dict[str, float] = {}

# How long `end_session` waits for an analysis already in flight before giving
# up and building the report from whatever stands. Bounded on purpose — see
# `_run_analysis`'s `wait_if_busy`.
FINAL_ANALYSIS_WAIT_S = int(os.getenv("CONSULTATION_FINAL_WAIT_S", "20"))


def _lock_for(session_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _ANALYSIS_LOCKS.setdefault(session_id, threading.Lock())


def _session_or_404(session_id: str) -> dict:
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="That consultation session does not exist.")
    return session


def _ms_since(stamp: Optional[str]) -> Optional[int]:
    if not stamp:
        return None
    try:
        then = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None
    return max(0, int((datetime.now() - then).total_seconds() * 1000))


# ── Request bodies ──────────────────────────────────────────────────────────

class SessionIn(BaseModel):
    title: str = ""
    question: str = ""
    context: str = ""
    framework: str = core.DEFAULT_FRAMEWORK
    mode: str = core.DEFAULT_MODE
    decision_method: str = core.DEFAULT_DECISION_METHOD
    presence: str = core.DEFAULT_PRESENCE
    record_audio: bool = False
    duration_minutes: int = 0
    warn_minutes: int = 10
    participants: list[str] = []
    retention_policy: str = core.DEFAULT_RETENTION
    # The host's word that the room has been told Abigail is listening. It is
    # an ATTESTATION, not proof of anyone's consent, and the UI says exactly
    # that -- claiming more would be worse than claiming nothing (rule 94).
    participants_informed: bool = False


class SessionPatch(BaseModel):
    title: Optional[str] = None
    question: Optional[str] = None
    context: Optional[str] = None
    framework: Optional[str] = None
    mode: Optional[str] = None
    decision_method: Optional[str] = None
    presence: Optional[str] = None
    duration_minutes: Optional[int] = None
    warn_minutes: Optional[int] = None
    # Retention is described on screen as a per-session choice, so it has to be
    # settable outside the closeout (rule 106) -- it was only reachable there,
    # which meant a meeting still in progress could not be told to forget
    # itself. Validated below; an unknown value is refused, never stored.
    retention_policy: Optional[str] = None


class ParticipantIn(BaseModel):
    name: str = ""


class SpeakerMapIn(BaseModel):
    speaker_key: Optional[str] = None


class OpeningIn(BaseModel):
    floor_state: str = governor.LISTENING_IDLE
    human_speaking: bool = False
    ms_since_human_speech_ended: Optional[int] = None
    muted: bool = False
    listening_paused: bool = False
    connected: bool = True


class TimeWarningIn(BaseModel):
    minutes_left: int = 0
    final: bool = False
    floor_state: str = governor.LISTENING_IDLE
    human_speaking: bool = False
    ms_since_human_speech_ended: Optional[int] = None
    muted: bool = False
    listening_paused: bool = False
    connected: bool = True


class TurnIn(BaseModel):
    text: str = ""
    realtime_item_id: Optional[str] = None
    role: str = "human"
    speaker_label: Optional[str] = None
    is_final: bool = False
    started_at: Optional[str] = None
    ended_at: Optional[str] = None


class LabelIn(BaseModel):
    speaker_label: Optional[str] = None


class AnalyzeIn(BaseModel):
    force: bool = False


class SpeechPermissionIn(BaseModel):
    """The browser's view of the room. The server fills in everything it can
    know better (the mode, the cooldowns, the current revision) and refuses to
    take the client's word for those."""
    kind: str = "unsolicited"
    floor_state: str = governor.LISTENING_IDLE
    human_speaking: bool = False
    ms_since_human_speech_ended: Optional[int] = None
    ms_since_invitation: Optional[int] = None
    muted: bool = False
    listening_paused: bool = False
    connected: bool = True
    observation_id: Optional[str] = None


class AskIn(BaseModel):
    text: str = ""
    floor_state: str = governor.LISTENING_IDLE
    human_speaking: bool = False
    ms_since_human_speech_ended: Optional[int] = None
    muted: bool = False
    listening_paused: bool = False
    connected: bool = True
    invited_by_voice: bool = False
    ms_since_invitation: Optional[int] = None


class AnswerIn(BaseModel):
    granted: bool = False
    ignored: bool = False


class ObservationStatusIn(BaseModel):
    status: str = "dismissed"


class WritingsIn(BaseModel):
    theme: str = ""


class UsageIn(BaseModel):
    usage: dict = {}
    model: str = ""


class ClientSecretIn(BaseModel):
    session_id: str
    accept_over_ceiling: bool = False


class ClientErrorIn(BaseModel):
    message: str = ""
    event_type: str = ""
    floor_state: str = ""


class ActionStatusIn(BaseModel):
    status: str = "proposed"


# ── Human authority over the map (rules 95-98) ──────────────────────────────

class MapItemIn(BaseModel):
    """A human correcting what the assistant understood. Every field here is
    optional: a correction is usually one field."""
    text: Optional[str] = None
    note: Optional[str] = None
    status: Optional[str] = None
    evidence_note: Optional[str] = None
    lifecycle: Optional[str] = None
    resolution_note: Optional[str] = None


class ReviewIn(BaseModel):
    reviewed: bool = True


class TurnTextIn(BaseModel):
    text: str = ""


class ActionIn(BaseModel):
    action: str = ""
    owner: Optional[str] = None
    due: Optional[str] = None


class ActionPatchIn(BaseModel):
    action: Optional[str] = None
    owner: Optional[str] = None
    due: Optional[str] = None
    status: Optional[str] = None
    success_criteria: Optional[str] = None
    support_needed: Optional[str] = None
    blocker: Optional[str] = None
    progress_note: Optional[str] = None


class AcceptIn(BaseModel):
    """Recording that the owner accepted — or did not.

    `accepted_by` exists because the host usually records this ON SOMEBODY'S
    BEHALF, and that is a different fact from the person accepting themselves.
    The UI says which it was rather than letting the record imply the stronger
    one."""
    accepted: Optional[bool] = None
    accepted_by: str = ""


class DecisionPatchIn(BaseModel):
    text: Optional[str] = None
    rationale: Optional[str] = None
    support: Optional[str] = None
    concerns: Optional[list[str]] = None


class ConfirmIn(BaseModel):
    """Concerns the group chose to carry forward with the decision rather than
    settle. A decision can be confirmed AND leave real dissent standing; hiding
    that would be using "unity" to bury it."""
    retained_concerns: list[str] = []


class CloseoutIn(BaseModel):
    outcome: str = core.DEFAULT_CLOSEOUT_OUTCOME
    note: str = ""
    reflection_at: Optional[str] = None
    retention_policy: Optional[str] = None
    # The revision the host was looking at. Closing out APPROVES the record, so
    # it carries the same binding as `/report/approve` (rule 102): if the record
    # moved between the preview and the button, the approval is refused rather
    # than applied to words nobody read. Omitted means "approve whatever it says
    # now", which is what an older client will send.
    approve_revision: Optional[int] = None
    # A host may close a meeting out without approving a record at all -- the
    # outcome and the note are still worth having.
    approve_record: bool = True


class ApproveIn(BaseModel):
    revision: Optional[int] = None


class NodeViewIn(BaseModel):
    x: Optional[float] = None
    y: Optional[float] = None
    pinned: Optional[bool] = None
    collapsed: Optional[bool] = None


class EdgeIn(BaseModel):
    from_id: str
    to_id: str
    relation: str
    label: str = ""


class EdgePatchIn(BaseModel):
    relation: Optional[str] = None
    label: Optional[str] = None


class MergeNodesIn(BaseModel):
    list_name: str
    keep_id: str
    remove_id: str
    text: Optional[str] = None


# ── Capabilities (rule 86) ──────────────────────────────────────────────────

@router.get("/capabilities")
def capabilities():
    """
    What this installation can actually do right now, so the UI degrades
    visibly instead of failing at the moment someone presses Start. Returns no
    secrets — model names and booleans only.
    """
    realtime_ok = realtime.available()
    reasoning_ok, model_note = (realtime.check_model(core.REASONING_MODEL)
                                if realtime_ok else (False, ""))
    try:
        writings_ok = writings.available()
    except Exception:
        writings_ok = False
    return {
        "realtime_available": realtime_ok,
        # Same account, but a configured model id that does not exist is a
        # separate way for this to be unavailable, and it is worth saying which.
        "reasoning_available": realtime_ok and reasoning_ok,
        "reasoning_note": model_note,
        "writings_available": writings_ok,
        # There IS a recorder now (rule 91, owner decision 2026-08-25), and it
        # exists for exactly one purpose: the live API cannot tell voices apart,
        # so named speakers have to be worked out from a finished file. Reported
        # honestly rather than hardcoded either way -- without a key nothing
        # could come of a recording, and a checkbox that quietly does nothing is
        # the Canva-autofill failure this repo has already had once.
        "recording_supported": realtime_ok,
        # What the recording limit actually means in minutes, so the setup
        # screen can say it BEFORE an hour is recorded and refused (rule 108).
        "recording_budget": audio.upload_budget(),
        "diarize_model": core.DIARIZE_MODEL,
        "diarize_available": realtime_ok,
        # The opening passage, so the tab can show the exact words while she
        # reads them and never has to carry a copy of its own (rule 92).
        "consultation_passage": core.CONSULTATION_PASSAGE,
        "consultation_passage_source": core.CONSULTATION_PASSAGE_SOURCE,
        "realtime_model": core.REALTIME_MODEL,
        "reasoning_model": core.REASONING_MODEL,
        "transcribe_model": core.TRANSCRIBE_MODEL,
        "voice": core.VOICE,
        "calls_url": realtime.CALLS_URL,
        "modes": [{"id": k, **v} for k, v in core.MODES.items()],
        "frameworks": [{"id": k, "label": v} for k, v in core.FRAMEWORKS.items()],
        "decision_methods": [{"id": k, "label": v} for k, v in core.DECISION_METHODS.items()],
        "presence_levels": [{"id": k, **v} for k, v in core.PRESENCE_LEVELS.items()],
        # The vocabularies the dashboard renders. Served rather than duplicated
        # in TypeScript, for the same reason the floor policy is (rule 87): two
        # copies of a list of states will disagree eventually.
        "retention_policies": [{"id": k, **v} for k, v in core.RETENTION_POLICIES.items()],
        "default_retention": core.DEFAULT_RETENTION,
        "closeout_outcomes": [{"id": k, "label": v}
                              for k, v in core.CLOSEOUT_OUTCOMES.items()],
        "fact_states": [{"id": s, "label": core.FACT_STATE_LABELS[s],
                         "human_only": s not in core.MODEL_FACT_STATES}
                        for s in core.FACT_STATES],
        "item_lifecycle": [{"id": s, "label": core.LIFECYCLE_LABELS[s],
                            "human_only": s not in core.MODEL_LIFECYCLE}
                           for s in core.ITEM_LIFECYCLE],
        "action_statuses": [{"id": s, "label": core.ACTION_STATUS_LABELS[s],
                             "human_only": s not in core.MODEL_ACTION_STATUSES}
                            for s in core.ACTION_STATUSES],
        "map_lists": list(core.ITEM_LISTS.keys()),
        # The concept map's vocabulary, served rather than duplicated in
        # TypeScript for the same reason as every other list on this page
        # (rule 87): a colour, a label and a legend that live in two places
        # disagree eventually.
        "graph_schema_version": graph.GRAPH_SCHEMA_VERSION,
        "layout_version": graph.LAYOUT_VERSION,
        "graph_capabilities": {**graph.GRAPH_CAPABILITIES, "layout_version": graph.LAYOUT_VERSION},
        "node_kinds": [{"id": k, **v} for k, v in graph.NODE_KIND_META.items()],
        "edge_relations": [{"id": k, **v} for k, v in graph.RELATION_META.items()],
        # The question-led reading roles (rule 133): a coarser lens over the
        # kinds above, so a future focused view can group "what answers this
        # question" without duplicating the mapping in TypeScript.
        "node_roles": [{"id": k, **v} for k, v in graph.ROLE_META.items()],
        "role_of_kind": graph.NODE_ROLE,
        "default_mode": core.DEFAULT_MODE,
        "default_framework": core.DEFAULT_FRAMEWORK,
        "default_presence": core.DEFAULT_PRESENCE,
        "assistant_name": core.ASSISTANT_NAME,
        "assistant_avatar": core.ASSISTANT_AVATAR,
        # One resolved set of numbers per preset, so the browser's governor
        # never computes a timing of its own (rule 87).
        "floor_policy": governor.policy(),
        "floor_policies": {level: governor.policy(level) for level in core.PRESENCE_LEVELS},
        "analysis_policy": reasoner.policy(),
        "floor_states": list(governor.FLOOR_STATES),
        "state_labels": governor.STATE_LABELS,
        "spend": realtime.spend_snapshot(),
        "missing_key_message": (
            "Live Consultation needs an OpenAI API key for realtime voice. Add "
            "OPENAI_API_KEY to .env and restart the API. Past sessions can still "
            "be read without it."
        ) if not realtime_ok else "",
    }


# ── Sessions ────────────────────────────────────────────────────────────────

@router.get("/sessions")
def list_sessions(limit: int = 100):
    retention_sweep()
    store.init_db()
    return {"sessions": store.list_sessions(limit=limit)}


@router.post("/sessions")
def create_session(req: SessionIn):
    store.init_db()
    if req.mode not in core.MODES:
        raise HTTPException(status_code=400, detail=f"Unknown participation mode: {req.mode}")
    if req.framework not in core.FRAMEWORKS:
        raise HTTPException(status_code=400, detail=f"Unknown framework: {req.framework}")
    if req.decision_method not in core.DECISION_METHODS:
        raise HTTPException(status_code=400,
                            detail=f"Unknown decision method: {req.decision_method}")
    if req.presence not in core.PRESENCE_LEVELS:
        raise HTTPException(status_code=400, detail=f"Unknown presence: {req.presence}")
    # Recording used to be refused outright, because no recorder existed and a
    # flag that reads on screen as "you are being recorded" must never be
    # accepted when nothing is saved. There IS one now (rule 91), so this is a
    # real per-meeting choice -- and the honesty moved rather than went away:
    # the setup screen says it plainly and the room has to be told.
    if req.record_audio and not audio.available():
        raise HTTPException(
            status_code=400,
            detail=("Recording is for working out who said what afterwards, which needs "
                    "an OpenAI API key. Without one nothing would come of the recording, "
                    "so it has not been switched on."))
    if req.retention_policy not in core.RETENTION_POLICIES:
        raise HTTPException(status_code=400,
                            detail=f"Unknown retention choice: {req.retention_policy}")
    session = store.create_session(
        title=req.title, question=req.question, context=req.context,
        framework=req.framework, mode=req.mode, decision_method=req.decision_method,
        presence=req.presence, record_audio=bool(req.record_audio),
        duration_minutes=max(0, min(600, int(req.duration_minutes or 0))),
        warn_minutes=max(0, min(120, int(req.warn_minutes or 0))),
        retention_policy=req.retention_policy,
        participants_informed=bool(req.participants_informed),
        realtime_model=core.REALTIME_MODEL, reasoning_model=core.REASONING_MODEL,
        transcribe_model=core.TRANSCRIBE_MODEL, voice=core.VOICE,
    )
    for name in (req.participants or [])[:40]:
        store.add_participant(session["id"], name)
    return store.get_session(session["id"])


# ── Retention, actually enforced (rule 106) ─────────────────────────────────
#
# `sessions_due_for_transcript_deletion` existed, was correct, was tested, and
# had NO CALLER anywhere in the application. A seven-day session aged past its
# deadline, was correctly identified as due by the helper, and kept its
# transcript through every list and detail read -- so the retention choice on
# screen was a setting that did nothing.
#
# Two things make it real without making it expensive:
#
#   * a catch-up at startup, because the deletion is defined against a clock
#     that keeps running while the application is closed, and
#   * a THROTTLED sweep on read, because a machine can stay on for weeks and
#     the four-second consultation poll must not scan the whole database.
#
# The deadline is computed from the meeting's own end time, so nothing here
# depends on how often the sweep happens to run -- only on it running at all.
_RETENTION_SWEEP_EVERY_S = int(os.getenv("CONSULTATION_RETENTION_SWEEP_S", "900"))
_last_retention_sweep = 0.0
_retention_lock = threading.Lock()


def retention_sweep(force: bool = False) -> dict:
    """Delete the transcripts whose time is up. Idempotent and safe to repeat.

    A failure on one session is recorded and the sweep continues: one row that
    will not delete must not leave every later one undeleted.
    """
    global _last_retention_sweep
    now = time.time()
    if not force and (now - _last_retention_sweep) < _RETENTION_SWEEP_EVERY_S:
        return {"ran": False}
    if not _retention_lock.acquire(blocking=False):
        return {"ran": False}
    try:
        _last_retention_sweep = now
        done, failed = [], []
        for row in store.sessions_due_for_transcript_deletion():
            try:
                result = store.delete_transcript(row["id"])
                done.append({"session_id": row["id"],
                             "policy": row.get("retention_policy"),
                             "turns": result.get("turns", 0),
                             "cleanup_failed": result.get("cleanup_failed") or []})
            except Exception as e:                       # one bad row, not the sweep
                failed.append({"session_id": row["id"], "error": type(e).__name__})
        return {"ran": True, "deleted": done, "failed": failed}
    finally:
        _retention_lock.release()

def _assert_may_listen(session: dict) -> None:
    """Everything that has to be true before a microphone may open (rule 105).

    `/start` checked this and the credential endpoint did not, so the actual
    side effect -- minting a live realtime credential and connecting a
    microphone to a paid cloud service -- could be reached for a draft session
    whose host had attested nothing. A disabled button is not a gate and neither
    is a gate on the endpoint next to the one that matters, so both call this.
    """
    if session.get("status") == "ended":
        raise HTTPException(status_code=400,
                            detail="That consultation has already ended.")
    if session.get("transcript_deleted_at"):
        raise HTTPException(status_code=409, detail=(
            "The transcript of this consultation was deleted. Start a new "
            "consultation rather than listening into this one again."))
    if not session.get("participants_informed_at"):
        raise HTTPException(
            status_code=400,
            detail=("Before this starts, everyone in the room has to be told that "
                    f"{core.ASSISTANT_NAME} is listening and transcribing"
                    + (", and that the meeting is being recorded"
                       if session.get("record_audio") else "")
                    + ". Confirm that on the setup screen."))

def _touch_record(session_id: str) -> int:
    """Mark that the record a person may approve has changed (rule 102)."""
    return store.bump_record_revision(session_id)

async def _read_bounded(file: UploadFile, max_bytes: int, what: str) -> bytes:
    """Read an upload, refusing it the moment it is too big (rule 110).

    Both upload routes did `await file.read()` -- the WHOLE thing -- and checked
    the size afterwards, so the way to make this process an arbitrary amount of
    data was to send an arbitrary amount of data. Starlette spools a large
    multipart body to a temporary file, so "it is only an UploadFile" is not a
    memory guarantee either; the cap has to be applied while the bytes are
    arriving.

    Reading in chunks is also what makes the dictation promise true. That one is
    documented as memory-only, and it is: the bytes are held for one request and
    never written anywhere.
    """
    chunks, total = [], 0
    while True:
        chunk = await file.read(_UPLOAD_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            await file.close()
            raise HTTPException(status_code=413, detail=(
                f"That {what} is larger than the {max_bytes // (1024 * 1024)} MB limit, "
                "so it was refused without being read any further."))
        chunks.append(chunk)
    await file.close()
    if not total:
        raise HTTPException(status_code=400, detail=f"That {what} was empty.")
    return b"".join(chunks)


_UPLOAD_CHUNK = 256 * 1024

def _refuse_if_deleted(session: dict, what: str) -> None:
    """Nothing may put words back into a meeting whose words were deleted.

    Rule 100. Clearing the screen is not deletion: a turn still in flight when
    the delete landed, a retried chunk upload, a diarisation started minutes
    earlier -- each of them arrives afterwards and recreates part of exactly
    what somebody asked to be gone. Observed for real: a late turn was accepted
    and displayed while the session still reported `transcript_deleted=true`,
    and an audio upload rebuilt a recording that had been removed.
    """
    if session.get("transcript_deleted_at"):
        raise HTTPException(status_code=409, detail=(
            f"The transcript of this consultation was deleted on "
            f"{session['transcript_deleted_at']}, so {what} cannot be added to it. "
            "The approved record is still here."))

def _detail(session: dict) -> dict:
    sid = session["id"]
    state = store.get_state(sid)
    decisions = store.list_decisions(sid)
    live_turns = store.list_turns(sid)
    return {
        "session": session,
        "state": state,
        "turns": live_turns,
        "observations": store.list_observations(sid),
        "decisions": decisions,
        # Kept singular for every existing caller and every old session; the
        # list beside it is the truthful one now that a consultation may settle
        # more than one thing (rule 98).
        "confirmed_decision": next((d for d in decisions if d["status"] == "confirmed"), None),
        "confirmed_decisions": [d for d in decisions if d["status"] == "confirmed"],
        "transcript_deleted": bool(session.get("transcript_deleted_at")),
        "action_items": store.list_action_items(sid),
        "writings": store.list_writings(sid),
        "speech_events": store.list_speech_events(sid, limit=25),
        "participants": store.list_participants(sid),
        # What is still hanging, for the glance panel. The SAME function the
        # spoken time check reads from, so the screen and her voice can never
        # disagree about what is outstanding.
        "open_threads": _open_threads(sid),
        # The best transcript there is: the speaker-separated one once it exists,
        # the live one until then. `turns` above stays the LIVE record so nothing
        # that already read it changes meaning (rule 80).
        #
        # Until a speaker pass exists these two are the SAME rows, and both were
        # being serialised on every four-second poll -- the transcript, twice,
        # for the whole meeting (rule 115). The field keeps its meaning for every
        # existing caller; it is simply the same list rather than a second copy
        # fetched separately, and the polling path no longer asks for either.
        "final_turns": (store.list_turns(sid, source="diarized")
                        if store.has_diarized(sid) else live_turns),
        "has_diarized": store.has_diarized(sid),
        "report": session.get("report_md") or "",
        # Draft and approved are DIFFERENT things and the UI must be able to say
        # which it is showing (rule 102). `report` above stays the draft, for
        # every existing caller; these three are the truthful additions.
        "approved_report": session.get("approved_md") or "",
        "record": _record_status(session),
        # Files a deletion could not remove. A flag in a database cannot prove a
        # file left the disk, so what could not be cleaned up is SHOWN and can
        # be retried rather than swallowed (rule 103).
        "cleanup_pending": session.get("cleanup_pending") or "",
        # The cursor a delta poll starts from (rule 115). Sent with the full
        # read so the dashboard never has to fetch everything twice to find out
        # where it is.
        "turns_rev": store.turns_head(sid)["rev"],
        "record_revision": int(session.get("record_revision") or 0),
        "deletion_generation": int(session.get("deletion_generation") or 0),
        "mode_info": core.MODES.get(session.get("mode") or core.DEFAULT_MODE, {}),
        # Whether an immutable approved MAP snapshot exists, distinct from the
        # live one — a cheap boolean rather than the snapshot itself (which can
        # be fetched from its own endpoint), so a full session read stays
        # cheap even once a meeting has been approved several times over.
        "has_approved_graph": bool(session.get("approved_graph_json")),
        # A PERSISTENT record that the closing analysis pass did not finish —
        # not just this one response's `note`, which used to be the only place
        # it appeared and was routinely discarded by the caller navigating
        # away (section 4). Empty string means nothing is outstanding.
        "final_pass_note": session.get("final_pass_note") or "",
    }


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    retention_sweep()
    return _detail(_session_or_404(session_id))


@router.get("/sessions/{session_id}/updates")
def session_updates(session_id: str, turns_rev: int = 0, state_revision: int = -1,
                    record_revision: int = -1, source: str = "live",
                    limit: int = 200):
    """
    What has CHANGED, for a poll that runs every few seconds (rule 115).

    The dashboard used to refetch the whole session four times a minute --
    and, since the diarised transcript landed, TWO copies of it: `turns` and
    `final_turns` are the same rows until a speaker pass exists. Measured on
    synthetic meetings, that payload was 68 KB at 60 turns and 679 KB at 600, so
    the cost of listening grew with how long the group had been talking.

    The cursor is a per-turn revision, not the greatest turn id, because the
    changes that matter most here happen to turns that ALREADY EXIST: a line
    that finalises late, and a line a person corrects. An id-based cursor would
    never send either again.

    An unchanged poll answers with the cursor and nothing else. Callers that
    want everything still call `GET /sessions/{id}`, which is unchanged.
    """
    session = _session_or_404(session_id)
    if source not in ("live", "diarized", "best"):
        raise HTTPException(status_code=400, detail=f"Unknown transcript: {source}")

    head = store.turns_head(session_id, source=source)
    now_state = int(store.get_state(session_id).get("state_revision") or 0)
    now_record = int(session.get("record_revision") or 0)
    generation = int(session.get("deletion_generation") or 0)

    # A cursor from before a deletion describes turns that no longer exist, and
    # a client holding them has to be told to start again rather than being sent
    # a quiet "nothing changed" while it displays deleted words (rule 100).
    if session.get("transcript_deleted_at") and turns_rev > head["rev"]:
        return {"resync": True, "reason": "transcript_deleted",
                "transcript_deleted": True, "deletion_generation": generation,
                "turns_rev": head["rev"], "turns_total": head["count"]}

    changed = store.turns_since(session_id, since_rev=turns_rev, source=source, limit=limit)
    more = len(changed) >= limit
    # The cursor handed back is the revision of the LAST turn actually
    # delivered, not the session's absolute head. Returning the head
    # regardless of `limit` sent a cursor describing rows the caller had never
    # received: a request capped at 200 of 205 changed turns got back cursor
    # 205 (the head), and the next poll — now starting AFTER everything, with
    # nothing left `> 205` — silently skipped the five it never got. When
    # `changed` is non-empty and nothing was truncated, its last row's revision
    # already equals the head, so this is never a smaller cursor than before.
    cursor_rev = changed[-1]["turn_rev"] if changed else head["rev"]
    state_changed = state_revision != now_state
    record_changed = record_revision != now_record
    out = {
        "resync": False,
        "turns_rev": cursor_rev, "turns_total": head["count"],
        "turns_source": head["source"],
        "turns": changed,
        "more": more,
        "state_revision": now_state, "record_revision": now_record,
        "deletion_generation": generation,
        "transcript_deleted": bool(session.get("transcript_deleted_at")),
        "changed": bool(changed) or state_changed or record_changed,
        "has_diarized": store.has_diarized(session_id),
    }
    # Only sent when they actually moved. This is what keeps an idle meeting's
    # poll the same size at turn 600 as at turn 6.
    if state_changed:
        out["state"] = store.get_state(session_id)
        out["open_threads"] = _open_threads(session_id)
    if record_changed or state_changed:
        decisions = store.list_decisions(session_id)
        out["decisions"] = decisions
        out["confirmed_decisions"] = [d for d in decisions if d["status"] == "confirmed"]
        out["action_items"] = store.list_action_items(session_id)
        out["record"] = _record_status(session)
    if record_changed:
        out["participants"] = store.list_participants(session_id)
        out["writings"] = store.list_writings(session_id)
    return out


@router.patch("/sessions/{session_id}")
def patch_session(session_id: str, req: SessionPatch):
    _session_or_404(session_id)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if "mode" in fields and fields["mode"] not in core.MODES:
        raise HTTPException(status_code=400, detail=f"Unknown participation mode: {fields['mode']}")
    if "presence" in fields and fields["presence"] not in core.PRESENCE_LEVELS:
        raise HTTPException(status_code=400, detail=f"Unknown presence: {fields['presence']}")
    if "retention_policy" in fields             and fields["retention_policy"] not in core.RETENTION_POLICIES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown retention choice: {fields['retention_policy']}")
    updated = store.update_session(session_id, **fields)
    return _detail(updated)


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    """Delete the meeting and everything in it. There is no archive copy: a
    consultation transcript is the most private thing this application holds."""
    _session_or_404(session_id)
    _LAST_ANALYSIS.pop(session_id, None)
    with _LOCKS_GUARD:
        _ANALYSIS_LOCKS.pop(session_id, None)
    return store.delete_session(session_id)


@router.post("/sessions/{session_id}/start")
def start_session(session_id: str):
    """
    Begin the meeting.

    Refused until the host has attested that the room was told (rule 94). The
    gate is here, at the server, and not only in the setup screen's disabled
    button -- a page can be reloaded, and the honest thing to guard is the
    moment the microphone starts, not the moment a checkbox is drawn.
    """
    session = _session_or_404(session_id)
    _assert_may_listen(session)
    return _detail(store.start_session(session_id))


@router.post("/sessions/{session_id}/inform")
def confirm_informed(session_id: str):
    """The host's attestation, recorded with a timestamp. It says the host said
    so — never that anybody consented, which this application cannot know."""
    _session_or_404(session_id)
    return _detail(store.update_session(
        session_id,
        participants_informed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")))


_BUSY_NOTE = "An analysis pass is already running."


def _run_final_pass(session: dict) -> tuple[str, bool]:
    """
    Make the closing analysis pass. Returns `(note, incomplete)`.

    `incomplete` is TRUE only when the closing summary is not trustworthy as
    a finished thing — the bounded wait timed out because a pass was already
    in flight, or the pass ran and genuinely failed. It is FALSE for the
    ordinary, honest reasons there was nothing to do (nothing new was said, no
    key is configured) — those are not an incomplete state, they are the
    correct outcome, and must not raise a persistent warning that never
    clears.
    """
    if not realtime.available():
        return "", False
    try:
        result = _run_analysis(session, final_pass=True, wait_if_busy=FINAL_ANALYSIS_WAIT_S)
    except Exception as e:
        return (f"The closing summary could not be made ({type(e).__name__}). "
                "Everything said is saved."), True
    note = result.get("note", "")
    if not result.get("ran"):
        return note, (note == _BUSY_NOTE)
    if not result.get("ok"):
        return note, True
    return note, False


def _write_report(session_id: str, note: str) -> str:
    """
    Build and save the DRAFT report from whatever the record says right now,
    appending to `note` rather than replacing it — a failed final pass and a
    failed report write are two different things that can both be true at
    once, and each must be readable on its own.
    """
    try:
        current = store.get_session(session_id)
        built = report.build_report(
            session=current, state=store.get_state(session_id),
            decisions=store.list_decisions(session_id),
            actions=store.list_action_items(session_id),
            writings=store.list_writings(session_id),
            turns=store.list_turns(session_id, source="best"),
            participants=store.list_participants(session_id))
        store.update_session(
            session_id, report_md=built["markdown"],
            report_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            # Kept so the factual half can be rebuilt later without paying for
            # the prose again (rule 102). This is a DRAFT: nothing here approves.
            report_narrative_json=json.dumps(built.get("narrative") or {},
                                             ensure_ascii=False))
        if built["note"]:
            note = (note + " " + built["note"]).strip()
    except Exception as e:
        note = (note + f" The report could not be written ({type(e).__name__}); "
                       "it can be written by hand from the record.").strip()
    return note


@router.post("/sessions/{session_id}/end")
def end_session(session_id: str, final_pass: bool = True):
    """
    Close the meeting and, if there is anything unread and a key configured,
    make one last analysis pass so the record is complete.

    A failed or incomplete final pass is reported, never fatal — the
    transcript and the map as they stand are already saved — but it is no
    longer reported ONLY in this one HTTP response, which used to be thrown
    away the moment the room navigated to closeout (section 4: "the current
    End mutation ignores the response note as it navigates away"). `note`
    that means "the closing pass did not finish" is now ALSO written to
    `final_pass_note`, a persistent field the archived session can go on
    showing — with a `finish_analysis` retry (section 4's "recovery path")
    — until it clears.
    """
    session = _session_or_404(session_id)

    # Pressing End again is not a second ending (rule 107). It used to re-run
    # the closing analysis AND rewrite the report -- two paid calls, on a
    # meeting that was already over, for a record that does not change.
    if store.already_ended(session_id):
        detail = _detail(store.get_session(session_id))
        detail["note"] = ("This consultation had already ended, so nothing was run "
                          "again. The record is as it was.")
        return detail

    ended = store.end_session(session_id)
    note, incomplete = ("", False)
    if final_pass:
        note, incomplete = _run_final_pass(ended or session)
    # The report is what the room actually wants when the meeting stops, so it
    # is written here rather than waiting for someone to ask (owner ask
    # 2026-08-25). It is built AFTER the final analysis pass, so it summarises
    # the finished map rather than the map as it stood a minute before the end.
    note = _write_report(session_id, note)
    store.update_session(session_id, final_pass_note=(note if incomplete else ""))
    detail = _detail(store.get_session(session_id))
    detail["note"] = note
    return detail


@router.post("/sessions/{session_id}/finish-analysis")
def finish_analysis(session_id: str):
    """
    The recovery path for an incomplete closing pass (section 4) — a retry,
    not a second ending: `end_session` itself is untouched by this and stays
    idempotent (rule 107). Safe to press more than once: it is bounded exactly
    like the original attempt and clears `final_pass_note` only on an actual
    success, so a still-busy pass leaves the persistent warning standing
    rather than clearing it on a guess.
    """
    session = _session_or_404(session_id)
    if not session.get("final_pass_note"):
        detail = _detail(session)
        detail["note"] = "There was nothing incomplete to finish."
        return detail
    note, incomplete = _run_final_pass(session)
    note = _write_report(session_id, note)
    store.update_session(session_id, final_pass_note=(note if incomplete else ""))
    detail = _detail(store.get_session(session_id))
    detail["note"] = note
    return detail


# ── Turns (rule 80) ─────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/turns")
def add_turn(session_id: str, req: TurnIn):
    """
    Upsert one transcript turn. Idempotent on the realtime item id, so a
    retried completion event updates the row it already made rather than
    doubling the speaker.

    Partials are not persisted by the dashboard — only finalised turns and the
    assistant's own spoken turns arrive here.
    """
    _refuse_if_deleted(_session_or_404(session_id), "another line of transcript")
    turn = store.upsert_turn(
        session_id, text=req.text, realtime_item_id=req.realtime_item_id,
        role=req.role if req.role in ("human", "assistant") else "human",
        speaker_label=req.speaker_label, is_final=req.is_final,
        started_at=req.started_at, ended_at=req.ended_at,
    )
    return {"turn": turn}


@router.post("/sessions/{session_id}/turns/{turn_id}/label")
def label_turn(session_id: str, turn_id: int, req: LabelIn):
    """A human typing in who was speaking. Nothing infers this (rule 80)."""
    _session_or_404(session_id)
    turn = store.label_turn(turn_id, req.speaker_label, session_id=session_id)
    if not turn or turn.get("session_id") != session_id:
        raise HTTPException(status_code=404, detail="No such turn in this session.")
    _touch_record(session_id)
    return {"turn": turn}


# ── Analysis ────────────────────────────────────────────────────────────────

def _apply_graph_patch(session_id: str, resolved_edges: list[dict],
                       saved_state: dict, limit: Optional[int] = None) -> tuple[list[str], bool]:
    """
    Validate the reasoner's proposed connections against the map as it ACTUALLY
    stands after this pass, and store the ones that pass. Deliberately run
    AFTER `save_state` rather than alongside the item rebase above: `saved_state`
    is already whatever the map says now (rebased or not), so this is correct
    in both cases without a second rebase of its own.

    Returns `(notes, changed)` — the caller needs to know whether a connection
    was actually added or refined, because that is part of what
    `_record_status` (rule 102) has to treat as a change to the record: an
    export includes the map's semantic relationships, and a connection arriving
    after the record was approved must be able to make it STALE, the same as a
    corrected owner does.
    """
    if not resolved_edges:
        return [], False
    node_ids, node_kind = graph.node_universe(saved_state)
    rejected = store.rejected_edge_keys(session_id)
    valid_turns = _valid_turn_id_set(session_id)
    existing_parents, human_parented = graph.existing_parent_index(
        store.list_graph_edges(session_id))
    accepted, notes = graph.validate_edges(resolved_edges, {}, node_ids, node_kind, rejected,
                                           valid_turn_ids=valid_turns,
                                           existing_parents=existing_parents,
                                           human_parented=human_parented,
                                           limit=limit)
    changed = False
    for e in accepted:
        if e.get("replaces_from"):
            store.drop_inferred_contains(session_id, e["to_id"], keep_from_id=e["from_id"])
        row = store.upsert_graph_edge(
            session_id, e["from_id"], e["to_id"], e["relation"], label=e.get("label", ""),
            inferred=e.get("inferred", True), source_turn_ids=e.get("source_turn_ids"))
        if row:
            changed = True
    if changed:
        store.bump_graph_revision(session_id)
    return notes, changed


def _run_analysis(session: dict, force: bool = False, final_pass: bool = False,
                  wait_if_busy: float = 0.0) -> dict:
    sid = session["id"]
    lock = _lock_for(sid)
    # A closing pass waits BRIEFLY for one already in flight, rather than
    # skipping straight past it. Skipping was a real seam: the concept map and
    # the report are both built from the record right after this returns, and
    # an end request that continued past a busy lock could finish the meeting
    # without the discussion that was mid-analysis at that exact moment. The
    # wait is bounded (never indefinite — a person leaving must not be made to
    # wait for ever, rule 114's reasoning applied to ending rather than
    # unmounting) and this thread's OWN pass still runs afterwards regardless,
    # reading whatever is left unanalyzed by then.
    got = (lock.acquire(blocking=True, timeout=wait_if_busy) if wait_if_busy > 0
          else lock.acquire(blocking=False))
    if not got:
        return {"ran": False, "note": "An analysis pass is already running."}
    try:
        new_turns = store.unanalyzed_turns(sid)
        if final_pass and not new_turns and not store.list_turns(sid, final_only=True):
            # A meeting where nobody said anything has nothing to summarise, and
            # a closing pass on it would be a paid call for an empty transcript.
            return {"ran": False, "note": "Nothing was said, so there is nothing to summarise."}
        since = None
        if _LAST_ANALYSIS.get(sid):
            since = time.time() - _LAST_ANALYSIS[sid]
        ok, why = reasoner.should_analyze(new_turns, since, force=force or final_pass)
        if not ok:
            return {"ran": False, "note": why}
        if not realtime.available():
            return {"ran": False, "note": (
                "No OpenAI API key is configured, so the consultation map cannot be "
                "updated. The transcript is still being saved.")}
        state = store.get_state(sid)
        base_revision = int(state.get("state_revision") or 0)
        generation = store.deletion_generation(sid)
        recent = store.list_turns(sid, final_only=True, limit=reasoner.RECENT_WINDOW)
        edges_rows = store.list_graph_edges(sid)

        # THE NETWORK CALL. Nothing is held across it -- no lock on the database
        # and no assumption that the map will still say what it said. A pass can
        # take tens of seconds, and the person who is sitting there correcting
        # the record is doing it during exactly that window (rule 104).
        result = reasoner.analyze(session, state, new_turns, recent, final_pass=final_pass,
                                  edges_rows=edges_rows)
        _LAST_ANALYSIS[sid] = time.time()
        if not result.ok:
            return {"ran": True, "ok": False, "note": result.note}

        # The words this was built from may have been deleted while it ran.
        if store.deletion_generation(sid) != generation:
            return {"ran": True, "ok": False, "note": (
                "The transcript was deleted while the map was being updated, so the "
                "result was discarded rather than written back.")}

        # REBASE, never overwrite. `result.state` was merged against the map as
        # it stood BEFORE the call; saving it wholesale is what silently put a
        # model's wording back over a human correction and reset `human_edited`
        # to false. If the map has moved, the patch is re-merged onto what it
        # says NOW -- so the correction stands and the model's reading of the new
        # turns is still applied. No paid call is repeated to do it.
        fresh = store.get_state(sid)
        fresh_revision = int(fresh.get("state_revision") or 0)
        rebased = False
        if fresh_revision != base_revision:
            merged, merge_notes, rebased_edges = reasoner.merge(fresh, result.patch)
            validated, problem = reasoner.validate_state(merged)
            if problem:
                return {"ran": True, "ok": False, "note": (
                    "Someone edited the record while the map was being updated, and the "
                    "result would not re-apply cleanly to the edited version, so the "
                    "edit stands and the pass was dropped. " + problem)}
            result.state = validated
            result.notes = list(result.notes) + list(merge_notes)
            result.notes.append(
                f"re-applied onto revision {fresh_revision} after an edit during the pass")
            # The FIRST merge's resolved edges named ids from the map as it
            # stood before the edit; only this rebased set is safe to apply.
            result.resolved_edges = rebased_edges
            rebased = True

        # Anything a person took out stays out, however the map got here.
        result.state, resurrected = store.strip_removed(sid, result.state)
        if resurrected:
            result.notes.append(
                f"{resurrected} item(s) a person had deleted were not written back")

        # Provenance has to point at turns that exist IN THIS SESSION, or a
        # "supporting conversation" link opens nothing and the citation is
        # decoration. Checked here because this is the first place the
        # session's real turn ids are known.
        result.state, dropped = _validate_provenance(sid, result.state)
        saved = store.save_state(sid, result.state)
        store.mark_turns_analyzed(sid, [t["id"] for t in new_turns])
        if dropped:
            result.notes.append(f"{dropped} source reference(s) dropped as unrecognised")

        # Decision candidates and action items get their own rows so a human can
        # act on them one at a time — and so `confirmed_decision` has exactly one
        # writable path (rule 81).
        # `map_id` is what makes these UPDATES rather than write-once inserts
        # (rule 95). The map has always assigned stable ids; they were simply
        # dropped here, so an action that later learned an owner matched the
        # old row by text and was handed back unchanged. Real cost, measured on
        # this database before the fix: 169 action items, 2 owners, 0 due dates.
        #
        # Snapshotted BEFORE the upserts below, so the analysis pass can tell
        # whether anything it wrote actually changed the record (rule 102) —
        # every existing decision/action is re-upserted on every pass whether
        # or not its wording moved, so a naive "something was upserted" flag
        # would mark the record changed on almost every analysis, which is not
        # the honest signal `_record_status` needs.
        before_decisions = {d["id"]: (d.get("text"), d.get("rationale"), d.get("support"))
                            for d in store.list_decisions(sid)}
        before_actions = {a["id"]: (a.get("action"), a.get("owner"), a.get("due"))
                          for a in store.list_action_items(sid)}
        for cand in saved.get("decision_candidates", []):
            store.upsert_decision_candidate(
                sid, cand.get("text", ""), cand.get("rationale", ""),
                cand.get("support", ""), cand.get("concerns") or [],
                map_id=cand.get("id") or None)
        for act in saved.get("action_items", []):
            store.upsert_action_item(sid, act.get("action", ""), act.get("owner"),
                                     act.get("due"), map_id=act.get("id") or None)
        after_decisions = {d["id"]: (d.get("text"), d.get("rationale"), d.get("support"))
                          for d in store.list_decisions(sid)}
        after_actions = {a["id"]: (a.get("action"), a.get("owner"), a.get("due"))
                        for a in store.list_action_items(sid)}
        record_changed = (before_decisions != after_decisions or before_actions != after_actions)

        # The concept map's proposed connections — same "validate against what
        # is ACTUALLY there, right now" discipline as the rebase above, which is
        # exactly what makes a separate edge-rebase unnecessary: `saved` is
        # already the final, post-rebase map, so validating against it is
        # correct whether or not a rebase happened.
        graph_notes, edges_changed = _apply_graph_patch(sid, result.resolved_edges, saved)
        result.notes = list(result.notes) + graph_notes
        record_changed = record_changed or edges_changed

        # A new decision or action, or a semantic connection, is part of what
        # an approved export covers (rule 102) — same class as a human editing
        # the record directly. Without this, a late-arriving analysis result
        # (one still in flight when the meeting ended, say — rule 126) could
        # add content to an ALREADY-APPROVED record while `_record_status` kept
        # reporting "Approved, and current," because nothing here had ever told
        # it the record moved.
        if record_changed:
            _touch_record(sid)

        added = []
        for obs in result.observations:
            row = store.add_observation(
                sid, kind=obs.kind, summary=obs.summary, detail=obs.detail,
                importance=obs.importance, should_request_floor=obs.should_request_floor,
                permission_request=obs.permission_request, speech_brief=obs.speech_brief,
                state_revision=saved.get("state_revision", 0))
            if row:
                added.append(row)

        found = None
        if result.writings_theme:
            found = _lookup_writings(sid, result.writings_theme)

        return {
            "ran": True, "ok": True, "note": "", "why": why,
            "state": saved, "observations": added, "turns_analyzed": len(new_turns),
            "writings": found, "merge_notes": result.notes, "rebased": rebased,
        }
    finally:
        lock.release()


def _valid_turn_id_set(session_id: str) -> set[str]:
    """Every real turn id in this session, as strings — shared between map-item
    provenance (`_validate_provenance`) and connection provenance
    (`_apply_graph_patch`), so a citation on either one is held to the same
    "must open something real" standard."""
    return {str(t["id"]) for t in store.list_turns(session_id)}


def _validate_provenance(session_id: str, state: dict) -> tuple[dict, int]:
    """Drop any `source_turn_ids` entry that is not a real turn in this session.

    A source reference is only ever evidence of what was SAID in this meeting —
    never evidence that the thing said is true — and the UI is careful to say
    so. What it must at least be is real: a citation that opens nothing is
    worse than none, because it looks like corroboration.
    """
    valid = _valid_turn_id_set(session_id)
    dropped = 0
    for name in core.ITEM_LISTS:
        for item in state.get(name) or []:
            if not isinstance(item, dict):
                continue
            ids = item.get("source_turn_ids") or []
            kept = [i for i in ids if str(i) in valid]
            if len(kept) != len(ids):
                dropped += len(ids) - len(kept)
            item["source_turn_ids"] = kept
    return state, dropped


def _find_map_item(state: dict, list_name: str, item_id: str) -> Optional[dict]:
    for item in state.get(list_name) or []:
        if isinstance(item, dict) and item.get("id") == item_id:
            return item
    return None


def _map_list_or_400(list_name: str) -> str:
    if list_name not in core.ITEM_LISTS:
        raise HTTPException(
            status_code=400,
            detail=f"There is no '{list_name}' in the consultation map.")
    return list_name


def _lookup_writings(session_id: str, theme: str) -> dict:
    """Verified passages only, stored against the session (rule 84).

    The session's FRAMEWORK is passed through, and it is not cosmetic: much of
    the consultation compilation is addressed to elected Bahá'í institutions,
    and handing an Assembly's guidance to a school board or a family as though
    it applied to them would be exactly the over-generalisation this feature
    must not commit. Without this argument the filter in `writings.search`
    would sit there defaulting to "bahai" and never fire.
    """
    session = store.get_session(session_id) or {}
    result = writings.search(theme,
                             framework=session.get("framework") or core.DEFAULT_FRAMEWORK)
    stored = []
    for passage in result.get("passages", []):
        row = store.add_writing(
            session_id, text=passage["text"], source=passage.get("source", ""),
            section=passage.get("section", ""), link=passage.get("link", ""),
            theme=theme, score=passage.get("score", 0.0))
        if row:
            stored.append(row)
    return {"theme": theme, "available": result.get("available", False),
            "note": result.get("note", ""), "passages": stored}


@router.post("/sessions/{session_id}/analyze")
def analyze(session_id: str, req: AnalyzeIn):
    session = _session_or_404(session_id)
    return _run_analysis(session, force=req.force)


@router.post("/sessions/{session_id}/writings")
def find_writings(session_id: str, req: WritingsIn):
    _session_or_404(session_id)
    theme = (req.theme or "").strip()
    if not theme:
        raise HTTPException(status_code=400, detail="Give a theme to look up.")
    return _lookup_writings(session_id, theme)


# ── The floor (rules 75-77) ─────────────────────────────────────────────────

def _server_side_request(session: dict, body: dict, kind: str,
                         observation: Optional[dict] = None) -> governor.SpeechRequest:
    """
    Build the governor's request from what the SERVER knows plus what only the
    browser can know.

    The mode, the cooldowns, the pending request and the current revision are
    read here rather than accepted from the client: those are the facts a
    compromised or simply buggy page could get wrong in the direction of
    speaking more.
    """
    sid = session["id"]
    state = store.get_state(sid)
    last_spoken = store.last_allowed_speech(sid, kinds=("intervention", "permission_request"))
    denials = [e for e in store.list_speech_events(sid, limit=25)
               if e["kind"] in ("permission_denied", "permission_expired")]
    pending = any(o["status"] == "surfaced" and o["should_request_floor"]
                  for o in store.list_observations(sid, status="surfaced"))
    started = session.get("started_at") or session.get("created_at")
    return governor.SpeechRequest(
        kind=kind,
        mode=session.get("mode") or core.DEFAULT_MODE,
        muted=bool(body.get("muted")),
        listening_paused=bool(body.get("listening_paused")),
        connected=bool(body.get("connected", True)),
        floor_state=body.get("floor_state") or governor.LISTENING_IDLE,
        human_speaking=bool(body.get("human_speaking")),
        ms_since_human_speech_ended=body.get("ms_since_human_speech_ended"),
        ms_since_session_start=_ms_since(started) or 0,
        ms_since_invitation=body.get("ms_since_invitation"),
        permission_pending=pending,
        ms_since_last_intervention=_ms_since(last_spoken["created_at"]) if last_spoken else None,
        ms_since_last_denial=_ms_since(denials[0]["created_at"]) if denials else None,
        observation_importance=(observation or {}).get("importance"),
        observation_should_request_floor=bool((observation or {}).get("should_request_floor")),
        observation_status=(observation or {}).get("status", "open"),
        observation_revision=(observation or {}).get("state_revision"),
        current_revision=state.get("state_revision"),
        presence=session.get("presence") or core.DEFAULT_PRESENCE,
    )


@router.post("/sessions/{session_id}/speech-permission")
def speech_permission(session_id: str, req: SpeechPermissionIn):
    """
    The second gate. The browser's own governor has already decided; this one
    decides again on the server's facts, and the dashboard will not trigger a
    response without an allow from here.

    Two gates rather than one because they fail differently: the client's is
    immediate but lives in code a page reload can restart mid-cooldown, and the
    server's knows the whole history but cannot cancel audio in 30ms.
    """
    session = _session_or_404(session_id)
    observation = None
    if req.observation_id:
        observation = store.get_observation(req.observation_id)
        if not observation or observation.get("session_id") != session_id:
            raise HTTPException(status_code=404, detail="No such observation in this session.")
    kind = req.kind if req.kind in governor.REQUEST_KINDS else "unsolicited"
    decision = governor.evaluate(_server_side_request(session, req.model_dump(), kind, observation))
    store.log_speech_event(
        session_id,
        kind=("permission_request" if decision.action == "request_permission"
              else "intervention" if decision.allowed else f"refused:{decision.code}"),
        allowed=decision.allowed, reason=decision.reason,
        observation_id=req.observation_id)
    payload = decision.to_dict()
    if decision.allowed and decision.action == "request_permission" and observation:
        # Only the request itself is handed over — never the substance. Slipping
        # the observation into the question would be taking the floor while
        # appearing to ask for it (rule 75).
        payload["say"] = observation["permission_request"]
        payload["observation_id"] = observation["id"]
        store.set_observation_status(observation["id"], "surfaced")
    elif decision.allowed and observation:
        payload["say"] = ""
        payload["instructions"] = _speech_instructions(
            session, brief=observation.get("speech_brief") or observation.get("detail") or "")
        payload["observation_id"] = observation["id"]
    return payload


def _speech_instructions(session: dict, brief: str = "", asked: str = "") -> str:
    """
    Per-response instructions for the realtime model — code-owned, so what the
    assistant is told when it finally gets the floor cannot be steered by the
    room. Carries the structured map, because the realtime model's own memory of
    a long meeting is the weakest thing in the system.
    """
    state = store.get_state(session["id"])
    parts = [reasoner.speech_context(state, question=session.get("question") or "")]
    if asked:
        parts.append(f"You have been asked, directly: {asked}")
    if brief:
        parts.append(f"Say this, in your own words and in two or three sentences: {brief}")
    parts.append(
        "Speak now, briefly. Do not greet anyone, do not summarise what you are about "
        "to do, and stop when you have said the useful thing. If someone begins "
        "speaking, stop immediately.")
    return "\n\n".join(p for p in parts if p)


@router.post("/sessions/{session_id}/ask")
def ask(session_id: str, req: AskIn):
    """
    Someone asked the assistant something — by pressing Ask AI, or by saying
    "AI, ...".

    A press while a person is talking does NOT interrupt: the governor answers
    "wait", the dashboard shows "AI will answer when the floor is free", and it
    tries again when the floor is actually free (rule 76).
    """
    session = _session_or_404(session_id)
    kind = "invited" if req.invited_by_voice else "queued_ask"
    decision = governor.evaluate(_server_side_request(session, req.model_dump(), kind))
    store.log_speech_event(
        session_id, kind="intervention" if decision.allowed else f"refused:{decision.code}",
        allowed=decision.allowed, reason=decision.reason)
    payload = decision.to_dict()
    if decision.allowed:
        payload["instructions"] = _speech_instructions(session, asked=(req.text or "").strip())
        payload["modalities"] = (
            ["audio"] if core.MODES.get(session.get("mode") or "", {}).get("speaks", True)
            else ["text"])
    return payload


@router.post("/sessions/{session_id}/observations/{observation_id}/answer")
def answer_permission(session_id: str, observation_id: str, req: AnswerIn):
    """
    The group's answer to a request for the floor — spoken, clicked, or never
    given at all.

    Silence here is the one place silence DOES mean something: an ignored
    request expires and is never repeated (rule 75).
    """
    session = _session_or_404(session_id)
    observation = store.get_observation(observation_id)
    if not observation or observation.get("session_id") != session_id:
        raise HTTPException(status_code=404, detail="No such observation in this session.")
    if req.granted and not req.ignored:
        store.log_speech_event(session_id, kind="permission_granted", allowed=True,
                               reason="a person said yes", observation_id=observation_id)
        return {
            "granted": True,
            "instructions": _speech_instructions(
                session, brief=observation.get("speech_brief") or observation.get("detail") or ""),
            "observation": store.set_observation_status(observation_id, "spoken"),
        }
    kind = "permission_expired" if req.ignored else "permission_denied"
    reason = ("nobody answered, which is a no" if req.ignored else "the group said no")
    store.log_speech_event(session_id, kind=kind, allowed=False, reason=reason,
                           observation_id=observation_id)
    return {"granted": False, "reason": reason,
            "observation": store.set_observation_status(observation_id, "expired")}


@router.post("/sessions/{session_id}/observations/{observation_id}/dismiss")
def dismiss_observation(session_id: str, observation_id: str):
    _session_or_404(session_id)
    observation = store.get_observation(observation_id)
    if not observation or observation.get("session_id") != session_id:
        raise HTTPException(status_code=404, detail="No such observation in this session.")
    return {"observation": store.set_observation_status(observation_id, "dismissed")}


@router.post("/sessions/{session_id}/observations/{observation_id}/status")
def set_observation_status(session_id: str, observation_id: str, req: ObservationStatusIn):
    _session_or_404(session_id)
    if req.status not in ("open", "dismissed", "surfaced", "spoken", "expired"):
        raise HTTPException(status_code=400, detail=f"Unknown status: {req.status}")
    observation = store.get_observation(observation_id)
    if not observation or observation.get("session_id") != session_id:
        raise HTTPException(status_code=404, detail="No such observation in this session.")
    return {"observation": store.set_observation_status(observation_id, req.status)}


# ── Decisions and actions (rules 81-83) ─────────────────────────────────────

def _decision_or_404(session_id: str, decision_id: str) -> dict:
    for d in store.list_decisions(session_id):
        if d["id"] == decision_id:
            return d
    raise HTTPException(status_code=404, detail="No such decision in this session.")


@router.post("/sessions/{session_id}/decisions/{decision_id}/confirm")
def confirm_decision(session_id: str, decision_id: str, req: ConfirmIn = ConfirmIn()):
    """
    A human confirming a decision. This is the ONLY way `confirmed_decision`
    is ever populated — no analysis pass, no model, and no amount of apparent
    agreement can do it (rule 81).
    """
    _session_or_404(session_id)
    _decision_or_404(session_id, decision_id)
    decision = store.set_decision_status(decision_id, "confirmed",
                                         retained_concerns=req.retained_concerns,
                                         session_id=session_id)
    state = store.get_state(session_id)
    state["confirmed_decision"] = {
        "id": decision["id"], "text": decision["text"], "rationale": decision.get("rationale", ""),
        "support": decision.get("support", ""), "concerns": decision.get("concerns", []),
        "status": "confirmed",
    }
    for cand in state.get("decision_candidates", []):
        if cand.get("text", "").strip().lower() == (decision["text"] or "").strip().lower():
            cand["status"] = "confirmed"
    saved = store.save_state(session_id, state)
    _touch_record(session_id)
    return {"decision": decision, "state": saved}


@router.post("/sessions/{session_id}/decisions/{decision_id}/reject")
def reject_decision(session_id: str, decision_id: str):
    """"Not decided." The candidate stays in the record; it just is not a
    decision, which is a truthful thing for a meeting to end with."""
    _session_or_404(session_id)
    _decision_or_404(session_id, decision_id)
    decision = store.set_decision_status(decision_id, "rejected",
                                         session_id=session_id)
    _touch_record(session_id)
    state = store.get_state(session_id)
    if (state.get("confirmed_decision") or {}).get("id") == decision_id:
        state["confirmed_decision"] = None
    saved = store.save_state(session_id, state)
    _touch_record(session_id)
    return {"decision": decision, "state": saved}


@router.post("/sessions/{session_id}/actions/{action_id}/status")
def set_action_status(session_id: str, action_id: str, req: ActionStatusIn):
    _session_or_404(session_id)
    if req.status not in ("open", "done"):
        raise HTTPException(status_code=400, detail=f"Unknown status: {req.status}")
    item = store.set_action_status(action_id, req.status, session_id=session_id)
    if not item or item.get("session_id") != session_id:
        raise HTTPException(status_code=404, detail="No such action item in this session.")
    _touch_record(session_id)
    return {"action_item": item}


# ── Realtime credential and metering ────────────────────────────────────────

@router.post("/realtime/client-secret")
def client_secret(req: ClientSecretIn):
    """
    Mint the browser's short-lived realtime credential. The master key stays
    here (rule 73).

    Refuses over the Steward's monthly ceiling unless the caller explicitly
    accepts it — realtime voice is the most expensive thing in this repo per
    minute, and a soft ceiling nobody is shown is not a ceiling (rule 85).
    """
    session = _session_or_404(req.session_id)
    # The same gate as `/start`, at the place the money and the microphone
    # actually are (rule 105).
    _assert_may_listen(session)
    spend = realtime.spend_snapshot()
    if spend.get("over_ceiling") and not req.accept_over_ceiling:
        # Stated as a fact, with no instruction attached. It used to end "start
        # anyway from the setup screen", which was read on the LIVE screen --
        # naming a door the reader had already walked through. The way through
        # belongs next to the message, and the dashboard puts it there.
        raise HTTPException(status_code=402, detail=(
            f"This month's metered API spend (${spend['month_total']}) is already over "
            f"the ${spend['monthly_ceiling']} ceiling."))
    try:
        credential = realtime.create_client_secret(session)
    except realtime.RealtimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    credential["session_id"] = session["id"]
    credential["spend"] = spend
    return credential


@router.post("/sessions/{session_id}/client-error")
def client_error(session_id: str, req: ClientErrorIn):
    """
    Something the realtime service refused, recorded where it can be read later.

    This exists because a wire-level error was diagnosed once from nothing but
    "it gives an error and she doesn't respond" (2026-08-24). The browser showed
    the message and then lost it; the backend log was all 200s, because the
    failing exchange never touched this API at all. An error nobody can quote
    afterwards costs a whole session to reproduce.

    It goes in the private DB with the rest of the meeting (rule 73) and is
    truncated: an error message is a sentence from OpenAI, not a transcript, and
    there is no reason for it to be long.
    """
    _session_or_404(session_id)
    message = (req.message or "").strip()[:500]
    where = (req.event_type or "realtime").strip()[:60]
    floor = (req.floor_state or "").strip()[:40]
    store.log_speech_event(
        session_id, kind="realtime_error", allowed=False,
        reason=f"[{where}{(' during ' + floor) if floor else ''}] {message}")
    return {"recorded": True}


@router.post("/sessions/{session_id}/usage")
def record_usage(session_id: str, req: UsageIn):
    """
    Meter one realtime response from the `usage` block on `response.done`.

    Only numbers cross into `workforce.db` — never a word of the meeting. When
    the event carries no usage detail, nothing is recorded and the reply says
    so: an invented figure in the ledger is worse than a gap (rule 85).
    """
    _session_or_404(session_id)
    return realtime.record_usage(req.usage or {}, req.model or core.REALTIME_MODEL)




# ── Participants (rule 91) ──────────────────────────────────────────────────

@router.get("/sessions/{session_id}/participants")
def get_participants(session_id: str):
    _session_or_404(session_id)
    return {"participants": store.list_participants(session_id)}


@router.post("/sessions/{session_id}/participants")
def add_participant(session_id: str, req: ParticipantIn):
    _session_or_404(session_id)
    person = store.add_participant(session_id, req.name)
    if not person:
        raise HTTPException(status_code=400, detail="A participant needs a name.")
    return person


@router.delete("/sessions/{session_id}/participants/{participant_id}")
def delete_participant(session_id: str, participant_id: str):
    _session_or_404(session_id)
    if not store.remove_participant(participant_id, session_id=session_id):
        raise HTTPException(status_code=404, detail="No such participant.")
    return {"removed": True}


@router.post("/sessions/{session_id}/participants/{participant_id}/speaker")
def map_participant_speaker(session_id: str, participant_id: str, req: SpeakerMapIn):
    """
    Say which diarised voice this person turned out to be.

    This is the ONLY way a name reaches the transcript. Nothing infers it: the
    model separated the voices and called them A and B, and a human here says
    which is which (rule 91).
    """
    _session_or_404(session_id)
    person = store.set_participant_speaker(participant_id, req.speaker_key,
                                           session_id=session_id)
    if not person:
        raise HTTPException(status_code=404, detail="No such participant.")
    applied = store.apply_speaker_names(session_id)
    return {"participant": person, "turns_labelled": applied,
            "final_turns": store.list_turns(session_id, source="best")}


# ── The opening and the clock (rule 92) ─────────────────────────────────────

@router.post("/sessions/{session_id}/opening")
def opening(session_id: str, req: OpeningIn):
    """
    What she says to open the meeting.

    Scheduled speech: the group asked for this when they set the meeting up, so
    it goes through the governor as an invitation rather than as something
    unsolicited -- but it still waits for the floor and is still refused
    outright in scribe mode or when muted.

    Fires ONCE. The guard is the stored speech-event record, not a flag in the
    browser, so a page reload cannot make her open the meeting twice.
    """
    session = _session_or_404(session_id)
    if any(e["kind"] == "opening" for e in store.list_speech_events(session_id, limit=200)):
        return {"allowed": False, "action": "refuse", "code": "already_opened",
                "reason": "She has already opened this meeting.", "retry_after_ms": None}
    decision = governor.evaluate(
        _server_side_request(session, req.model_dump(), "opening"))
    payload = decision.to_dict()
    if decision.allowed:
        store.log_speech_event(session_id, kind="opening", allowed=True,
                               reason="She opened the meeting.")
        payload["instructions"] = core.opening_instructions(
            framework=session.get("framework") or core.DEFAULT_FRAMEWORK,
            participants=[p["name"] for p in store.list_participants(session_id)],
            question=session.get("question") or "")
        payload["modalities"] = ["audio"]
        # The passage goes on screen at the same moment she reads it, so the
        # room sees the exact words rather than only hearing them (rule 92).
        if (session.get("framework") or "") == "bahai":
            payload["passage"] = core.CONSULTATION_PASSAGE
            payload["passage_source"] = core.CONSULTATION_PASSAGE_SOURCE
    return payload


def _open_threads(session_id: str) -> dict:
    """What the map says is still hanging, for the wrap-up."""
    state = store.get_state(session_id)
    decisions = store.list_decisions(session_id)

    def _still_open(key: str) -> list[str]:
        # An item the group has settled is not outstanding. Since rule 97 these
        # stay in the map for ever rather than being deleted, so "still open"
        # has to be read from the lifecycle -- otherwise the time check would
        # read out concerns that were dealt with an hour ago.
        return [i.get("text", "") for i in (state.get(key) or [])
                if isinstance(i, dict) and i.get("text")
                and i.get("lifecycle", "open") in core.OPEN_LIFECYCLE][:4]

    return {
        "unresolved": _still_open("tensions"),
        "questions": _still_open("unresolved_questions"),
        "unconfirmed": [d["text"] for d in decisions
                        if d.get("status") == "candidate" and d.get("text")][:3],
        "confirmed": any(d.get("status") == "confirmed" for d in decisions),
        # An action with nobody against it, OR one whose named owner has not
        # actually accepted -- a name is a proposal until somebody says yes
        # (rule 95), and the wrap-up should ask about both.
        "unowned": [a["action"] for a in store.list_action_items(session_id)
                    if a.get("action") and a.get("status") not in ("dropped", "completed")
                    and (not (a.get("owner") or "").strip()
                         or a.get("owner_accepted") is None)][:3],
    }


def _time_warning_instructions(session: dict, minutes_left: int, final: bool) -> str:
    """
    One short spoken intervention: the time, then the loose threads as questions.

    Everything she names here comes from the map, so she cannot warn about work
    that was never recorded -- and she asks, never decides. A meeting with
    nothing outstanding gets the time and nothing else, because inventing a
    loose end to sound useful is worse than saying little.
    """
    threads = _open_threads(session["id"])
    when = ("The time the group set for this meeting has now run out."
            if final else f"There are about {minutes_left} minutes left.")
    lines = [
        "You have been asked to give the group a time check. Speak now, briefly -- "
        "no more than four sentences in total.",
        "",
        f"Say this first, in your own words: {when}",
    ]
    asks = []
    if threads["unconfirmed"]:
        asks.append("something that sounded like a decision has not been confirmed by the "
                    "group: " + "; ".join(threads["unconfirmed"]))
    if threads["questions"]:
        asks.append("these questions are still open: " + "; ".join(threads["questions"]))
    if threads["unresolved"]:
        asks.append("this was left unresolved: " + "; ".join(threads["unresolved"]))
    if threads["unowned"]:
        asks.append("these actions have nobody against them: " + "; ".join(threads["unowned"]))
    if asks:
        lines += [
            "",
            "Then put AT MOST TWO of the following to the group, as questions they can "
            "answer in the time left -- choose the ones that matter most, and ask them "
            "plainly. Do not read the whole list out, do not answer them yourself, and "
            "do not suggest what the group should conclude:",
            "",
            *["- " + a for a in asks],
        ]
    else:
        lines += ["", "Nothing in the record is outstanding, so say only that, and stop. "
                      "Do not invent something for the group to settle."]
    if not threads["confirmed"] and final:
        lines += ["", "Nothing has been confirmed as a decision. You may say so plainly. "
                      "Do not press the group to decide, and do not decide anything "
                      "yourself."]
    lines += ["", "Then stop and hand the meeting back."]
    return "\n".join(lines)


@router.post("/sessions/{session_id}/time-warning")
def time_warning(session_id: str, req: TimeWarningIn):
    """
    The clock, spoken (owner ask 2026-08-25).

    Twice at most in a meeting -- once at the warning point, once when the time
    the group set has run out -- and each is recorded, so the guard against
    repeating survives a page reload. This is not silence being treated as
    permission (rule 75): a human set a duration, which is an invitation made in
    advance, and she still waits for the floor before taking it.
    """
    session = _session_or_404(session_id)
    if not int(session.get("duration_minutes") or 0):
        raise HTTPException(status_code=400,
                            detail="This meeting has no time set, so there is nothing to warn about.")
    kind = "time_up" if req.final else "time_warning"
    if any(e["kind"] == kind for e in store.list_speech_events(session_id, limit=200)):
        return {"allowed": False, "action": "refuse", "code": "already_warned",
                "reason": "She has already given this time check.", "retry_after_ms": None}
    decision = governor.evaluate(
        _server_side_request(session, req.model_dump(), "time_warning"))
    payload = decision.to_dict()
    if decision.allowed:
        store.log_speech_event(session_id, kind=kind, allowed=True,
                               reason=f"Time check with {req.minutes_left} minutes left.")
        payload["instructions"] = _time_warning_instructions(
            session, max(0, int(req.minutes_left or 0)), bool(req.final))
        payload["modalities"] = ["audio"]
        payload["threads"] = _open_threads(session_id)
    return payload


# ── The recording, and who said what (rule 91) ──────────────────────────────

@router.post("/sessions/{session_id}/audio")
async def upload_audio(session_id: str, file: UploadFile = File(...)):
    """Take the room's recording. Private, git-ignored, deleted with the session."""
    session = _session_or_404(session_id)
    _refuse_if_deleted(session, "a recording")
    if not session.get("record_audio"):
        raise HTTPException(status_code=400,
                            detail="This meeting was not set to be recorded.")
    data = await _read_bounded(file, audio.MAX_UPLOAD_BYTES, "recording")
    try:
        path = await run_in_threadpool(
            audio.save_recording, session_id, data, file.filename or "meeting.webm")
    except audio.AudioError as e:
        store.update_session(session_id, audio_status="failed", audio_note=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    store.update_session(session_id, audio_status="uploaded", audio_note="")
    return {"saved": True, "bytes": path.stat().st_size,
            "session": store.get_session(session_id)}


@router.post("/sessions/{session_id}/audio/chunk")
async def upload_audio_chunk(session_id: str, recording_id: str, seq: int,
                             file: UploadFile = File(...)):
    """
    One piece of a recording, saved as it is made (rule 113).

    This is what makes "the recording is safe" true. Before it, every chunk sat
    in a browser array until the meeting ended, so a crashed tab lost the whole
    recording -- while the code's own comment said a timeslice meant a crash
    cost only a few seconds.
    """
    session = _session_or_404(session_id)
    _refuse_if_deleted(session, "a recording")
    if not session.get("record_audio"):
        raise HTTPException(status_code=400,
                            detail="This meeting was not set to be recorded.")
    data = await _read_bounded(file, audio.MAX_UPLOAD_BYTES, "recording chunk")
    try:
        result = await run_in_threadpool(
            audio.append_chunk, session_id, recording_id, seq, data)
    except audio.AudioError as e:
        store.update_session(session_id, audio_status="failed", audio_note=str(e))
        raise HTTPException(status_code=409, detail=str(e))
    store.update_session(session_id, audio_status="recording", audio_note="")
    return result


@router.post("/sessions/{session_id}/audio/finalize")
async def finalize_audio(session_id: str, recording_id: str):
    """Say the recording is complete. Until this, a part file is not a recording."""
    session = _session_or_404(session_id)
    _refuse_if_deleted(session, "a recording")
    try:
        path = await run_in_threadpool(audio.finalize_recording, session_id, recording_id)
    except audio.AudioError as e:
        store.update_session(session_id, audio_status="failed", audio_note=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    store.update_session(session_id, audio_status="uploaded", audio_note="")
    return {"saved": True, "bytes": path.stat().st_size,
            "session": store.get_session(session_id)}


@router.get("/sessions/{session_id}/audio/progress")
def audio_progress(session_id: str, recording_id: str):
    """How much of the recording the SERVER actually holds.

    The only honest answer to "is it saved?". A browser knowing it emitted a
    blob is not the same fact, and that is the difference this endpoint exists
    to expose -- it is also how a reconnecting page finds out where to resume.
    """
    _session_or_404(session_id)
    return audio.recording_progress(session_id, recording_id)


@router.post("/dictate")
async def dictate(file: UploadFile = File(...)):
    """
    Say it instead of typing it (owner ask 2026-08-25).

    Not tied to a session: this is used on the setup screen, before a
    consultation exists, to fill in what the meeting is about. Nothing is
    stored -- the audio lives in memory for one request and the text goes
    straight back to the box the person is typing in.

    It is behind the owner gate like everything else here (rule 70), so it is
    not an open transcription service; it is Sheraj's own microphone.
    """
    data = await _read_bounded(file, audio.DICTATE_MAX_BYTES, "recording")
    try:
        # `transcribe_plain` makes a BLOCKING HTTP request. Awaited directly from
        # an async endpoint it holds the event loop for the whole call, so one
        # slow dictation stalled the consultation poll, the save of a turn, and
        # every other request in flight (rule 110). It belongs on a worker
        # thread, like every other blocking route in this router.
        result = await run_in_threadpool(
            audio.transcribe_plain, data, file.filename or "note.webm")
    except audio.AudioError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.post("/sessions/{session_id}/diarize")
def diarize(session_id: str):
    """
    Work out who said what, from the recording, after the meeting.

    Never automatic and never during: the live API cannot diarise at all, and
    this costs money and takes minutes. The result is stored ALONGSIDE the live
    transcript, never over it.
    """
    session = _session_or_404(session_id)
    _refuse_if_deleted(session, "a speaker-separated transcript")
    generation = store.deletion_generation(session_id)
    path = audio.recording_path(session_id)
    if not path:
        raise HTTPException(status_code=400,
                            detail="There is no recording saved for this meeting.")
    store.update_session(session_id, audio_status="transcribing", audio_note="")
    try:
        result = audio.transcribe_diarized(path)
    except audio.AudioError as e:
        store.update_session(session_id, audio_status="failed", audio_note=str(e))
        raise HTTPException(status_code=503, detail=str(e))
    # Minutes may have passed inside that call. If the words were deleted while
    # it ran, the result is made of text somebody asked to be gone (rule 100).
    if store.deletion_generation(session_id) != generation:
        store.update_session(session_id, audio_status="none", audio_note="")
        raise HTTPException(status_code=409, detail=(
            "The transcript of this consultation was deleted while the speakers were "
            "being worked out, so the result was discarded rather than written back."))
    written = store.replace_diarized_turns(session_id, result["segments"])
    applied = store.apply_speaker_names(session_id)
    store.update_session(session_id, audio_status="done", audio_note="")
    return {
        "turns_written": written, "turns_labelled": applied,
        "speakers": result["speakers"], "duration": result["duration"],
        "cost": result["cost"],
        "final_turns": store.list_turns(session_id, source="best"),
        "participants": store.list_participants(session_id),
        "session": store.get_session(session_id),
    }


# ── The report (rule 90) ────────────────────────────────────────────────────

def _build_record(session: dict, reuse_narrative: bool) -> dict:
    """Assemble the record from the CANONICAL rows, every time (rule 102).

    `reuse_narrative=True` re-uses prose already written and paid for, so the
    deterministic half -- decisions, commitments, owners, acceptance, passages
    -- refreshes from a corrected record with no model call. That is what makes
    "the owner was wrong, fix it and re-export" work without a paid button.
    """
    sid = session["id"]
    narrative = None
    if reuse_narrative:
        try:
            narrative = json.loads(session.get("report_narrative_json") or "{}")
        except (ValueError, TypeError):
            narrative = None
    return report.build_report(
        session=session,
        state=store.get_state(sid),
        decisions=store.list_decisions(sid),
        actions=store.list_action_items(sid),
        writings=store.list_writings(sid),
        turns=store.list_turns(sid, source="best"),
        participants=store.list_participants(sid),
        narrative=narrative or None,
    )


def _record_status(session: dict) -> dict:
    """Is what a person would export the thing a person actually approved?

    Three states, and the UI shows which: never approved (a draft), approved and
    current, approved but the record has CHANGED since -- that last one is the
    honest answer to "I corrected an owner, is my export right?", and the old
    code could not tell it apart from the second.
    """
    approved_at = session.get("approved_at")
    revision = int(session.get("record_revision") or 0)
    approved_revision = int(session.get("approved_revision") or 0)
    if not approved_at:
        return {"approved": False, "stale": False, "revision": revision,
                "approved_revision": 0, "approved_at": None,
                "note": "This is a draft. Nobody has approved it yet."}
    stale = revision != approved_revision
    return {
        "approved": True, "stale": stale, "revision": revision,
        "approved_revision": approved_revision, "approved_at": approved_at,
        "note": ("The record has been edited since it was approved, so the approved "
                 "copy is out of date. Review and approve it again."
                 if stale else "Approved, and current."),
    }


@router.post("/sessions/{session_id}/report")
def make_report(session_id: str, reuse_narrative: bool = False):
    """
    Build the one readable page the meeting produced -- as a DRAFT.

    The decision, the actions and any passages are copied out of the record; the
    summarising prose is written by the reasoning model. A model failure costs
    the prose and says so -- it never costs the report.

    `reuse_narrative=true` rebuilds the factual half from the record without
    paying for the prose again. Nothing here approves anything: approval is a
    person reading the preview and pressing the button (rule 102).
    """
    session = _session_or_404(session_id)
    sid = session["id"]
    built = _build_record(session, reuse_narrative)
    store.update_session(sid, report_md=built["markdown"],
                         report_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                         report_narrative_json=json.dumps(built.get("narrative") or {},
                                                          ensure_ascii=False))
    current = store.get_session(sid)
    return {"report": built["markdown"], "note": built["note"],
            "narrated": built["narrated"], "session": current,
            "record": _record_status(current)}


@router.get("/sessions/{session_id}/report/preview")
def preview_report(session_id: str):
    """Exactly what approving would approve, and the revision it belongs to.

    Free: it re-uses the prose already written and rebuilds everything else from
    the record. A person must be able to READ the thing before putting their
    name to it, and reading it must not cost anything or change anything.
    """
    session = _session_or_404(session_id)
    built = _build_record(session, reuse_narrative=True)
    status = _record_status(session)
    return {"preview": built["markdown"], "record": status,
            "approved": session.get("approved_md") or "",
            "differs_from_approved":
                (built["markdown"] or "").strip() != (session.get("approved_md") or "").strip()}


@router.post("/sessions/{session_id}/report/approve")
def approve_report(session_id: str, req: ApproveIn):
    """
    A person putting their name to the record (rule 102).

    Approval is bound to the REVISION that was on screen. If anything changed
    between the preview and this call, it is refused with the new text rather
    than approving words nobody read -- which is the whole difference between an
    approved record and a cached one.

    This is also the only writer of `approved_md`. `report_md` beside it stays
    what it always was: a draft, written automatically when the meeting ended.
    """
    session = _session_or_404(session_id)
    status = _record_status(session)
    if req.revision is not None and int(req.revision) != status["revision"]:
        built = _build_record(session, reuse_narrative=True)
        raise HTTPException(status_code=409, detail=(
            "The record changed while you were reading it, so it was not approved. "
            "Read the updated version and approve that."), headers={"X-Record-Revision":
                                                                    str(status["revision"])})
    built = _build_record(session, reuse_narrative=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    store.update_session(
        session_id, approved_md=built["markdown"], approved_at=now,
        approved_revision=status["revision"],
        report_md=built["markdown"], report_at=now,
        report_narrative_json=json.dumps(built.get("narrative") or {}, ensure_ascii=False),
        approved_graph_json=_snapshot_approved_graph(session_id, session))
    current = store.get_session(session_id)
    return {"approved": built["markdown"], "session": current,
            "record": _record_status(current)}


# ── Human authority over the map (rules 95-97) ──────────────────────────────
#
# Everything in this block exists because, until 2026-09-03, there was no way
# for a person to correct anything the assistant had understood. The map was
# whatever the model last said, and a mishearing stayed on screen for the rest
# of the meeting. The map is Abigail's current understanding; the people in the
# room are the authority on it.
#
# Every write here sets `human_edited`, which is what stops the next analysis
# pass putting the model's wording back (enforced in `reasoner.merge`).

@router.patch("/sessions/{session_id}/map/{list_name}/{item_id}")
def edit_map_item(session_id: str, list_name: str, item_id: str, req: MapItemIn):
    _session_or_404(session_id)
    _map_list_or_400(list_name)
    state = store.get_state(session_id)
    item = _find_map_item(state, list_name, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="No such item in the consultation map.")
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="Nothing to change.")

    if "status" in fields and list_name == "facts":
        # Rule 96. A HUMAN may say the group established something; this is the
        # only path to it, and the application never claims to have checked an
        # external source itself -- `evidence_note` is what a person recorded.
        status = core.normalize_fact_status(fields.pop("status"))
        item["status"] = status
    elif "status" in fields:
        fields.pop("status")
    if "lifecycle" in fields:
        lifecycle = core.normalize_lifecycle(fields.pop("lifecycle"))
        item["lifecycle"] = lifecycle
        item["resolved_at"] = (datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                               if lifecycle != core.DEFAULT_LIFECYCLE else "")
    for key, value in fields.items():
        item[key] = value
    item["human_edited"] = True
    item["human_reviewed"] = True
    saved = store.save_state(session_id, state)
    _touch_record(session_id)
    return {"item": item, "state": saved}


@router.delete("/sessions/{session_id}/map/{list_name}/{item_id}")
def delete_map_item(session_id: str, list_name: str, item_id: str):
    """A human removing something the assistant should never have written down.

    Note what this is NOT: the reasoner still cannot delete anything (rule 97).
    A person deciding an item was never real is a different act from a model
    deciding a concern has been dealt with."""
    _session_or_404(session_id)
    _map_list_or_400(list_name)
    state = store.get_state(session_id)
    before = len(state.get(list_name) or [])
    state[list_name] = [i for i in (state.get(list_name) or [])
                        if not (isinstance(i, dict) and i.get("id") == item_id)]
    if len(state.get(list_name) or []) == before:
        raise HTTPException(status_code=404, detail="No such item in the consultation map.")
    # Remembered by id, so an analysis pass that was already running cannot
    # hand it back (rule 104). The text is not kept -- see the table comment.
    store.record_removed_map_item(session_id, list_name, item_id)
    saved = store.save_state(session_id, state)
    _touch_record(session_id)
    return {"deleted": True, "state": saved}


@router.post("/sessions/{session_id}/map/{list_name}/{item_id}/review")
def review_map_item(session_id: str, list_name: str, item_id: str, req: ReviewIn):
    """Mark an item as read and agreed by a human, without changing its words."""
    _session_or_404(session_id)
    _map_list_or_400(list_name)
    state = store.get_state(session_id)
    item = _find_map_item(state, list_name, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="No such item in the consultation map.")
    item["human_reviewed"] = bool(req.reviewed)
    saved = store.save_state(session_id, state)
    _touch_record(session_id)
    return {"item": item, "state": saved}


@router.post("/sessions/{session_id}/turns/{turn_id}/text")
def correct_turn(session_id: str, turn_id: int, req: TurnTextIn):
    """
    Correct what the transcription misheard.

    This corrects the TRANSCRIPT and says so. It deliberately does not trigger
    a re-analysis: an incremental pass cannot reliably undo the consequences of
    a bad line it already read, and pretending otherwise would be worse than
    the mishearing. The map is corrected by hand, by the people who were there.
    """
    _session_or_404(session_id)
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400,
                            detail="A corrected line cannot be empty. Delete the meeting "
                                   "instead if that is what you meant.")
    turn = store.correct_turn(turn_id, text, session_id=session_id)
    if not turn or turn.get("session_id") != session_id:
        raise HTTPException(status_code=404, detail="No such turn in this session.")
    _touch_record(session_id)
    return {"turn": turn,
            "note": "The transcript is corrected. The consultation map is not rebuilt "
                    "automatically -- correct anything it got wrong directly."}


# ── Commitments (rule 95) ───────────────────────────────────────────────────

@router.post("/sessions/{session_id}/actions")
def create_action(session_id: str, req: ActionIn):
    _session_or_404(session_id)
    item = store.create_action_item(session_id, req.action, req.owner, req.due)
    if not item:
        raise HTTPException(status_code=400, detail="An action needs some words.")
    _touch_record(session_id)
    return {"action_item": item}


@router.patch("/sessions/{session_id}/actions/{action_id}")
def edit_action(session_id: str, action_id: str, req: ActionPatchIn):
    _session_or_404(session_id)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="Nothing to change.")
    item = store.update_action_item(action_id, session_id=session_id, **fields)
    if not item or item.get("session_id") != session_id:
        raise HTTPException(status_code=404, detail="No such action item in this session.")
    _touch_record(session_id)
    return {"action_item": item}


@router.delete("/sessions/{session_id}/actions/{action_id}")
def remove_action(session_id: str, action_id: str):
    """
    Delete a commitment. The canonical row is the whole record of it, so this
    also strips the working-map item behind it (if any) and tombstones its id
    — without that, a deleted action's map item stayed on the map with no
    canonical row behind it, and the graph rendered it back as a fresh,
    still-"proposed" phantom, because nothing else here told it the action was
    gone (rule 104's reasoning, applied to a deletion the map itself is not
    the entry point for).
    """
    _session_or_404(session_id)
    existing = next((a for a in store.list_action_items(session_id) if a["id"] == action_id), None)
    if not existing or not store.delete_action_item(action_id, session_id=session_id):
        raise HTTPException(status_code=404, detail="No such action item in this session.")
    map_id = existing.get("map_id")
    if map_id:
        state = store.get_state(session_id)
        items = state.get("action_items") or []
        kept = [i for i in items if not (isinstance(i, dict) and i.get("id") == map_id)]
        if len(kept) != len(items):
            state["action_items"] = kept
            store.save_state(session_id, state)
            store.record_removed_map_item(session_id, "action_items", map_id)
    _touch_record(session_id)
    return {"deleted": True}


def _status_for_acceptance(session_id: str, action_id: str,
                           accepted: Optional[bool]) -> Optional[str]:
    """Keep `status` and `owner_accepted` telling the same story (rule 101).

    Accepting sets the status to `accepted`. REVOKING has to move it back off
    `accepted`, or the record says both "Accepted" and "did not accept" at once
    -- which is what it did: the endpoint passed `status=None` on a revoke, and
    `update_action_item` skips None fields, so the old `accepted` simply stayed.

    Work that has visibly moved on is left alone. Someone who withdraws from
    something already `in_progress`, `blocked`, `completed` or `dropped` has not
    undone it, and silently resetting that to `proposed` would erase a real
    state nobody asked to change.
    """
    if accepted:
        return "accepted"
    current = next((a for a in store.list_action_items(session_id)
                    if a["id"] == action_id), None)
    if not current:
        return None
    return core.DEFAULT_ACTION_STATUS if current.get("status") == "accepted" else None


@router.post("/sessions/{session_id}/actions/{action_id}/accept")
def accept_action(session_id: str, action_id: str, req: AcceptIn):
    """
    Record that the owner accepted this commitment — or explicitly did not.

    A person is not committed because somebody else said their name in a
    meeting. `accepted=None` means nobody has recorded an answer, which is a
    different and more honest thing than False, and the report distinguishes
    them. Only a human reaches this endpoint; no model has a path to it.
    """
    _session_or_404(session_id)
    item = store.update_action_item(
        action_id, session_id=session_id, owner_accepted=req.accepted,
        accepted_by=(req.accepted_by or "").strip(),
        status=_status_for_acceptance(session_id, action_id, req.accepted))
    if not item or item.get("session_id") != session_id:
        raise HTTPException(status_code=404, detail="No such action item in this session.")
    _touch_record(session_id)
    return {"action_item": item}


@router.patch("/sessions/{session_id}/decisions/{decision_id}")
def edit_decision(session_id: str, decision_id: str, req: DecisionPatchIn):
    _session_or_404(session_id)
    _decision_or_404(session_id, decision_id)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="Nothing to change.")
    decision = store.update_decision(decision_id, session_id=session_id, **fields)
    _touch_record(session_id)
    return {"decision": decision}


# ── Closeout and retention (rules 94, 98) ───────────────────────────────────

@router.post("/sessions/{session_id}/closeout")
def closeout(session_id: str, req: CloseoutIn):
    """
    The group's own account of how the meeting ended.

    "No decision was reached" is a first-class outcome, not a failure, and the
    host is never blocked from choosing it. Making the choice EXPLICIT is the
    whole point: a meeting that genuinely decided nothing and a meeting whose
    decision was never recorded look identical otherwise.

    Nothing here confirms a decision — that is still `decisions/{id}/confirm`
    and still one human press per decision (rule 81).
    """
    session = _session_or_404(session_id)
    if req.outcome not in core.CLOSEOUT_OUTCOMES:
        raise HTTPException(status_code=400, detail=f"Unknown outcome: {req.outcome}")
    if req.retention_policy and req.retention_policy not in core.RETENTION_POLICIES:
        raise HTTPException(status_code=400,
                            detail=f"Unknown retention choice: {req.retention_policy}")
    fields = {
        "closeout_outcome": req.outcome,
        "closeout_note": (req.note or "").strip(),
        "closeout_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if req.reflection_at:
        fields["reflection_at"] = req.reflection_at
    if req.retention_policy:
        fields["retention_policy"] = req.retention_policy

    # Bound to the revision the host was reading, exactly like `/report/approve`
    # (rule 102). Checked BEFORE the closeout's own write, since that write is
    # itself a change to the record.
    before = int(session.get("record_revision") or 0)
    if req.approve_record and req.approve_revision is not None             and int(req.approve_revision) != before:
        raise HTTPException(status_code=409, detail=(
            "The record changed while you were reviewing it, so the closeout was not "
            "saved. Read the updated version and approve that."))

    updated = store.update_session(session_id, **fields)
    _touch_record(session_id)
    updated = store.get_session(session_id)

    note = ""
    if req.approve_record:
        # The outcome and the note ARE part of the record, so the approved copy
        # is built after they are written -- otherwise a host approves a report
        # that does not mention how the meeting ended. Free: the prose is
        # re-used, only the factual half is rebuilt.
        try:
            built = _build_record(updated, reuse_narrative=True)
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            store.update_session(
                session_id, approved_md=built["markdown"], approved_at=now,
                approved_revision=int(updated.get("record_revision") or 0),
                report_md=built["markdown"], report_at=now,
                report_narrative_json=json.dumps(built.get("narrative") or {},
                                                 ensure_ascii=False),
                approved_graph_json=_snapshot_approved_graph(session_id, updated))
            updated = store.get_session(session_id)
        except Exception as e:
            note = (f"The closeout is saved, but the record could not be rebuilt "
                    f"({type(e).__name__}), so nothing was approved. Try Approve again "
                    f"from the record.")

    # `until_closeout` means exactly this moment. Done here rather than on a
    # timer so the deletion happens while the person who chose it is watching --
    # and AFTER the approval above, or the record it approves would be built
    # from a transcript that has just been deleted.
    if (updated or {}).get("retention_policy") == "until_closeout":
        removed = store.delete_transcript(session_id)
        note = (note + f" The transcript has been deleted ({removed['turns']} lines). "
                       "The approved record is kept.").strip()
        if removed.get("cleanup_failed"):
            note += (" Some recording files could not be removed -- they are listed "
                     "on the session so this can be retried.")
        updated = store.get_session(session_id)
    detail = _detail(updated)
    detail["note"] = note
    return detail


@router.delete("/sessions/{session_id}/transcript")
def delete_transcript(session_id: str):
    """
    Delete the words, keep the approved record (rule 94).

    Irreversible, and the dashboard says so before it calls this. The report,
    the decisions, the commitments and the verified passages all survive — they
    are what the meeting was for.
    """
    _session_or_404(session_id)
    removed = store.delete_transcript(session_id)
    detail = _detail(store.get_session(session_id))
    # The whole summary, not a sentence about it. What was kept and what was
    # removed are both named, because the screen makes a promise here and the
    # only honest way to keep it is to say what the promise actually covered
    # (rule 103).
    detail["deleted"] = removed
    parts = [f"{removed['turns']} transcript line(s) deleted"]
    if removed["audio_files"]:
        parts.append(f"{removed['audio_files']} recording file(s) removed")
    if removed["observations"]:
        parts.append(f"{removed['observations']} private observation(s) removed")
    if removed["map_items_removed"]:
        parts.append(f"{removed['map_items_removed']} unreviewed map item(s) removed")
    note = ", ".join(parts) + "."
    if removed["map_items_kept"]:
        note += (f" {removed['map_items_kept']} item(s) a person reviewed by hand were "
                 "kept, along with the decisions, commitments and passages.")
    if removed["cleanup_failed"]:
        # Never reported as a clean success. A database flag cannot prove a file
        # left the disk, and this one did not.
        note += (f" {len(removed['cleanup_failed'])} recording file(s) could NOT be "
                 "removed from disk and are still there -- retry from the session.")
    detail["note"] = note
    return detail


# ── The concept map ──────────────────────────────────────────────────────────
#
# The graph itself is DERIVED, never stored — same discipline as the
# finished-video shelf (rule 58) and a gathering's commitments (rule 117).
# `graph.build_graph` reads the existing state, decisions and action_items
# rows plus this feature's own `graph_edges`/`graph_node_view` tables, and
# returns a fresh graph on every call — live or archived alike, and always
# without a network call, so "opening an archive never regenerates through AI"
# is true by construction rather than by a special case for ended sessions.

def _graph_snapshot(session_id: str, session: Optional[dict] = None,
                    persist_positions: bool = True) -> dict:
    session = session or _session_or_404(session_id)
    state = store.get_state(session_id)
    decisions = store.list_decisions(session_id)
    actions = store.list_action_items(session_id)
    edges_rows = store.list_graph_edges(session_id)
    views = store.list_node_views(session_id)
    built = graph.build_graph(session, state, decisions, actions, edges_rows, views)
    new_positions = built.pop("new_positions", {})
    if persist_positions:
        by_id = {n["id"]: n for n in built.get("nodes") or []}
        for node_id, (x, y, default_collapsed) in new_positions.items():
            kwargs = {"x": x, "y": y, "pinned": False, "layout_version": graph.LAYOUT_VERSION}
            # Only for a node with no stored view row at all (see the graph
            # payload's own comment) -- a stale position being reflowed under
            # the new layout must never silently re-collapse a branch a
            # person deliberately expanded.
            if default_collapsed is not None:
                kwargs["collapsed"] = default_collapsed
            parent_id = (by_id.get(node_id) or {}).get("parent_id")
            if parent_id:
                kwargs["parent_id"] = parent_id
            store.set_node_view(session_id, node_id, **kwargs)
    return built


def _snapshot_approved_graph(session_id: str, session: dict) -> str:
    """The map exactly as it stands at the moment a record is approved,
    frozen into `approved_graph_json` — an IMMUTABLE archive, never another
    editable source of truth (section 2). Bound to the same revision as
    `approved_md`/`approved_revision`, written in the same call, so the two
    can never describe different moments.

    Positions are not persisted from here: a snapshot is read, never dragged,
    and the live graph's own positions are unaffected either way."""
    return json.dumps(_graph_snapshot(session_id, session, persist_positions=False),
                      ensure_ascii=False)


@router.get("/sessions/{session_id}/graph")
def get_graph(session_id: str):
    return {"graph": _graph_snapshot(session_id)}


@router.get("/sessions/{session_id}/graph/approved")
def get_approved_graph(session_id: str):
    """The map as it stood when the record was last approved — an ARCHIVE,
    distinguishable from whatever the live map has become since (section 2).
    404 when nothing has ever been approved, the truthful answer rather than
    quietly substituting the current graph for it."""
    session = _session_or_404(session_id)
    raw = session.get("approved_graph_json")
    if not raw:
        raise HTTPException(status_code=404, detail=(
            "Nobody has approved a record for this consultation yet, so there is no "
            "approved map to show."))
    try:
        snapshot = json.loads(raw)
    except (ValueError, TypeError):
        raise HTTPException(status_code=500, detail="The approved map could not be read.")
    return {"graph": snapshot, "approved_at": session.get("approved_at"),
            "approved_revision": int(session.get("approved_revision") or 0)}


@router.patch("/sessions/{session_id}/graph/nodes/{node_id}/view")
def set_graph_node_view(session_id: str, node_id: str, req: NodeViewIn):
    """
    Where a node sits, whether it is pinned, whether its branch is collapsed.
    Pure layout: never touches `state_revision` (the speech governor's
    freshness check, rule 77) or `record_revision` (report approval, rule 102)
    — a drag or a collapsed branch cannot change what the assistant may say or
    stale an approved record.
    """
    _session_or_404(session_id)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="Nothing to change.")
    # A human placing a node by hand is, by definition, a CURRENT position —
    # stamped the same as an auto-computed one, so it is never later treated
    # as a stale leftover from an old layout scheme (it would not matter for
    # a pinned drag either way, since a pin is honoured regardless of
    # version, but stamping it keeps the field meaningful if it is ever
    # unpinned).
    if "x" in fields or "y" in fields:
        fields["layout_version"] = graph.LAYOUT_VERSION
    collapsing = "collapsed" in fields
    view = store.set_node_view(session_id, node_id, **fields)
    # Collapse changes how much space a branch occupies; reflow unpinned
    # cards so the overview does not leave a hole (or overlap) for hidden
    # children. Pins stay put.
    if collapsing:
        store.stale_unpinned_positions(session_id)
    return {"view": view, "session": store.get_session(session_id)}


@router.post("/sessions/{session_id}/graph/arrange")
def arrange_graph(session_id: str):
    """Recompute the layout for everything nobody has explicitly pinned. A
    deliberate reset (section 5: "an explicit Arrange map control") — a
    position someone dragged and pinned is left exactly where they put it.
    Purely geometric: no AI call, and it changes nothing about which theme
    (if any) an item sits under. For THAT, see "Organize ideas" below."""
    _session_or_404(session_id)
    store.clear_unpinned_node_views(session_id)
    return {"graph": _graph_snapshot(session_id)}


# ── "Organize ideas" (rule 132) ──────────────────────────────────────────────
#
# Deliberately separate from Arrange map above: Arrange is geometric and free;
# this is semantic and paid, so it is never run silently and never triggered
# by opening an archive. Preview proposes, validates, and HOLDS a patch
# (one paid call); apply commits that same validated patch against whatever
# the map says at that moment (rule 104's rebase); discard throws it away.

def _unplaced_from_graph(built: dict) -> list[dict]:
    """Every real item currently sitting in a PROVISIONAL bucket."""
    bucket_ids = {n["id"] for n in built["nodes"] if n["kind"] == "bucket"}
    if not bucket_ids:
        return []
    parented = {e["to_id"] for e in built["edges"]
               if e["relation"] == graph.HIERARCHY_RELATION and e["from_id"] in bucket_ids}
    by_id = {n["id"]: n for n in built["nodes"]}
    return [{"id": nid, "kind": by_id[nid]["kind"], "text": by_id[nid]["detail"],
            "source_turn_ids": by_id[nid].get("source_turn_ids") or []}
            for nid in parented if nid in by_id]


def _truncate(text: str, limit: int = 60) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _label_index(state: dict, extra: Optional[list[dict]] = None) -> dict[str, str]:
    label_by_id: dict[str, str] = {}
    for name in core.ITEM_LISTS:
        for item in (state.get(name) or []):
            if isinstance(item, dict) and item.get("id"):
                text = item.get("action") if name == "action_items" else item.get("text")
                if text:
                    label_by_id[item["id"]] = text
    for u in extra or []:
        if u.get("id") and u.get("text"):
            label_by_id.setdefault(u["id"], u["text"])
    return label_by_id


def _organize_summary(patch: dict, state: dict, accepted: list[dict],
                      unplaced: Optional[list[dict]] = None) -> list[str]:
    """Plain-language description of the VALIDATED proposal, not raw model counts."""
    label_by_id = _label_index(state, unplaced)
    new_theme_label = {t.get("tmp_id"): t.get("text", "") for t in patch.get("add", {}).get("themes", [])
                      if isinstance(t, dict) and t.get("tmp_id")}
    for t in patch.get("add", {}).get("themes", []):
        if isinstance(t, dict) and t.get("id") and t.get("text"):
            new_theme_label[t["id"]] = t["text"]

    def label(node_id: str) -> str:
        return _truncate(new_theme_label.get(node_id) or label_by_id.get(node_id) or node_id)

    lines: list[str] = []
    for t in patch.get("add", {}).get("themes", []):
        if isinstance(t, dict) and (t.get("text") or "").strip():
            lines.append(f"New topic: “{_truncate(t['text'])}”")
    for e in accepted:
        frm, to = e.get("from_id") or "", e.get("to_id") or ""
        if not frm or not to:
            continue
        relation = e.get("relation", "related_to")
        if relation == graph.HIERARCHY_RELATION:
            verb = "Move" if e.get("replaces_from") else "Place"
            lines.append(f"{verb} “{label(to)}” under “{label(frm)}”")
        else:
            rel_label = graph.RELATION_META.get(relation, {}).get("label", relation).lower()
            extra = f" — {e['label']}" if e.get("label") else ""
            lines.append(f"“{label(frm)}” {rel_label} “{label(to)}”{extra}")
    for tid in patch.get("retire_themes") or []:
        lines.append(f"Remove redundant topic “{label(str(tid))}” after moving its children")
    return lines[:80]


def _real_item_count(state: dict) -> int:
    n = 0
    for name in core.ITEM_LISTS:
        if name == "themes":
            continue
        n += sum(1 for i in (state.get(name) or []) if isinstance(i, dict) and i.get("id"))
    return n


def _dry_run_organize(session: dict, state: dict, patch: dict, edges_rows: list[dict],
                      decisions: list[dict], actions: list[dict],
                      rejected: set) -> dict:
    """Validate a proposal against a copy of the map. Never writes."""
    merged, merge_notes, resolved_edges = reasoner.merge(state, patch)
    validated, problem = reasoner.validate_state(merged)
    if problem:
        return {"ok": False, "note": problem, "merge_notes": merge_notes}
    node_ids, node_kind = graph.node_universe(validated, decisions, actions)
    # New themes from this merge must be in the universe (they are — merge
    # added them to state). Temporary ids in resolved_edges are already real.
    existing_parents, human_parented = graph.existing_parent_index(edges_rows)
    accepted, val_notes = graph.validate_edges(
        resolved_edges, {}, node_ids, node_kind, rejected,
        existing_parents=existing_parents, human_parented=human_parented,
        limit=graph.MAX_ORGANIZE_EDGES)
    proposed_rows = [r for r in edges_rows
                     if not (r.get("relation") == graph.HIERARCHY_RELATION
                             and any(e.get("to_id") == r.get("to_id") and e.get("replaces_from")
                                     for e in accepted)
                             and not r.get("human_edited"))]
    for e in accepted:
        proposed_rows.append({
            "id": f"preview:{e['from_id']}:{e['relation']}:{e['to_id']}",
            "from_id": e["from_id"], "to_id": e["to_id"], "relation": e["relation"],
            "label": e.get("label", ""), "inferred": e.get("inferred", True),
            "human_edited": False, "source_turn_ids": e.get("source_turn_ids") or [],
        })
    proposed = graph.build_graph(session, validated, decisions, actions, proposed_rows, {})
    proposed.pop("new_positions", None)
    placed = {e["to_id"] for e in accepted if e.get("relation") == graph.HIERARCHY_RELATION}
    unplaced_now = _unplaced_from_graph(proposed)
    coverage = {
        "items_considered": _real_item_count(state),
        "edges_proposed": len(resolved_edges),
        "edges_accepted": len(accepted),
        "edges_dropped": max(0, len(resolved_edges) - len(accepted)),
        "items_placed": len(placed),
        "items_unplaced": len(unplaced_now),
        "truncated": any("were not read" in n for n in val_notes),
        "new_themes": len(patch.get("add", {}).get("themes") or []),
    }
    return {
        "ok": True,
        "merged": validated,
        "accepted": accepted,
        "merge_notes": merge_notes,
        "val_notes": val_notes,
        "proposed_graph": proposed,
        "proposed_tree": graph.outline_tree(proposed),
        "coverage": coverage,
        "unplaced": unplaced_now,
    }


def _pending_public(pending: dict) -> dict:
    """What the dashboard needs from a held proposal — never the raw patch."""
    if not pending:
        return {}
    return {
        "summary": pending.get("summary") or [],
        "proposed_theme_count": pending.get("proposed_theme_count") or 0,
        "proposed_edge_count": pending.get("proposed_edge_count") or 0,
        "accepted_edge_count": pending.get("accepted_edge_count") or 0,
        "coverage": pending.get("coverage") or {},
        "omissions": pending.get("omissions") or [],
        "conflicts": pending.get("conflicts") or [],
        "proposed_tree": pending.get("proposed_tree") or [],
        "created_at": pending.get("created_at"),
    }


@router.post("/sessions/{session_id}/graph/organize/preview")
def preview_organize(session_id: str):
    session = _session_or_404(session_id)
    if not realtime.available():
        raise HTTPException(status_code=409, detail=(
            "No OpenAI API key is configured, so ideas cannot be organised."))
    lock = _lock_for(session_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail=(
            "Organisation is already running for this consultation."))
    try:
        existing = store.get_pending_organize(session_id)
        state = store.get_state(session_id)
        if existing and int(existing.get("base_revision") or 0) == int(state.get("state_revision") or 0) \
                and int(existing.get("graph_revision") or 0) == int(session.get("graph_revision") or 0):
            return _pending_public(existing)
        if _real_item_count(state) == 0 and not (state.get("themes") or []):
            raise HTTPException(status_code=409, detail=(
                "Nothing to organise — the map is empty."))
        built = _graph_snapshot(session_id, session, persist_positions=False)
        unplaced = _unplaced_from_graph(built)
        edges_rows = store.list_graph_edges(session_id)
        decisions = store.list_decisions(session_id)
        actions = store.list_action_items(session_id)
        rejected = store.rejected_edge_keys(session_id)
        recent_turns = store.list_turns(session_id, final_only=True, limit=12)
        # Beyond the recent narrative window, fetch exactly the turns a
        # currently-live item cites as its own source (section 4B: "the
        # source spans needed to interpret the current exchange") -- bounded
        # by however many distinct citations exist, never the whole
        # transcript, and never in place of the recency window above.
        cited_ids = _cited_turn_ids(state, unplaced) - {str(t["id"]) for t in recent_turns}
        cited_turns = list(store.get_turns_by_ids(session_id, cited_ids).values()) if cited_ids else []
        context = {"edges": edges_rows, "rejected": rejected,
                  "turns": recent_turns + cited_turns, "recent_turns": recent_turns}
        result = reasoner.organize(session, state, unplaced, context=context)
        if not result.ok:
            raise HTTPException(status_code=502, detail=result.note)
        dry = _dry_run_organize(session, state, result.patch, edges_rows, decisions,
                                actions, rejected)
        if not dry.get("ok"):
            raise HTTPException(status_code=409, detail=(
                "The proposal could not be applied to the current map. "
                + (dry.get("note") or "Run Organize ideas again.")))
        summary = _organize_summary(result.patch, dry["merged"], dry["accepted"], unplaced)
        conflicts = [n for n in dry["val_notes"] if "by hand" in n]
        omissions = list(dry["val_notes"])
        if result.note:
            omissions.append(result.note)
        extra = {
            "proposed_theme_count": len(result.patch.get("add", {}).get("themes") or []),
            "proposed_edge_count": len(result.patch.get("edges") or []),
            "accepted_edge_count": len(dry["accepted"]),
            "coverage": dry["coverage"],
            "omissions": omissions,
            "conflicts": conflicts,
            "proposed_tree": dry["proposed_tree"],
            "validated_edges": dry["accepted"],
            "graph_revision": int(session.get("graph_revision") or 0),
            "record_revision": int(session.get("record_revision") or 0),
        }
        store.set_pending_organize(session_id, patch=result.patch,
                                   base_revision=int(state.get("state_revision") or 0),
                                   summary=summary, extra=extra)
        held = store.get_pending_organize(session_id) or {}
        return _pending_public(held)
    finally:
        lock.release()


@router.post("/sessions/{session_id}/graph/organize/apply")
def apply_organize(session_id: str):
    session = _session_or_404(session_id)
    pending = store.get_pending_organize(session_id)
    if not pending:
        raise HTTPException(status_code=404, detail=(
            "There is no proposed organisation waiting -- run Organize ideas again."))
    lock = _lock_for(session_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail=(
            "Organisation is already running for this consultation."))
    try:
        fresh = store.get_state(session_id)
        patch = dict(pending.get("patch") or {})
        patch.pop("_preview", None)
        current_graph_rev = int(session.get("graph_revision") or 0)
        stored_graph_rev = pending.get("graph_revision")
        if stored_graph_rev is not None and int(stored_graph_rev) != current_graph_rev:
            raise HTTPException(status_code=409, detail=(
                "The map changed since this was proposed. Discard it and run "
                "Organize ideas again so you are applying what you just previewed."))
        merged, merge_notes, resolved_edges = reasoner.merge(fresh, patch)
        validated, problem = reasoner.validate_state(merged)
        if problem:
            store.clear_pending_organize(session_id)
            raise HTTPException(status_code=409, detail=(
                "The record changed since this was proposed and it no longer applies "
                "cleanly -- run Organize ideas again. " + problem))
        if int(fresh.get("state_revision") or 0) != int(pending.get("base_revision") or 0):
            merge_notes = list(merge_notes) + [
                "the record changed since this was proposed; applied onto the current version"]
        saved = store.save_state(session_id, validated)
        # Prefer the already-validated edges from preview so a 50-edge
        # proposal cannot silently apply only 40. Re-check them against the
        # live map so a concurrent human edit still wins.
        preview_edges = pending.get("validated_edges") or []
        to_apply = preview_edges or resolved_edges
        graph_notes, _edges_changed = _apply_graph_patch(
            session_id, to_apply, saved, limit=graph.MAX_ORGANIZE_EDGES)
        retire_notes = _retire_themes(session_id, saved, patch.get("retire_themes") or [])
        store.stale_unpinned_positions(session_id)
        store.clear_pending_organize(session_id)
        _touch_record(session_id)
        return {"graph": _graph_snapshot(session_id),
                "notes": list(merge_notes) + graph_notes + retire_notes,
                "coverage": pending.get("coverage") or {}}
    finally:
        lock.release()


def _retire_themes(session_id: str, state: dict, retire_ids: list) -> list[str]:
    """Drop empty, AI-generated topics after their children were moved.

    Human-edited themes are left alone. A theme that still has children
    after the patch is left alone rather than orphaning them.
    """
    if not retire_ids:
        return []
    notes: list[str] = []
    edges = store.list_graph_edges(session_id)
    still_parent = {e["from_id"] for e in edges if e.get("relation") == graph.HIERARCHY_RELATION}
    themes = [t for t in (state.get("themes") or []) if isinstance(t, dict)]
    by_id = {t["id"]: t for t in themes if t.get("id")}
    kept = []
    removed = 0
    for t in themes:
        tid = t.get("id")
        if tid not in retire_ids:
            kept.append(t)
            continue
        if t.get("human_edited") or t.get("human_reviewed"):
            notes.append(f"kept “{(t.get('text') or tid)}” because someone edited it by hand")
            kept.append(t)
            continue
        if tid in still_parent:
            notes.append(f"kept “{(t.get('text') or tid)}” because it still has children")
            kept.append(t)
            continue
        store.record_removed_map_item(session_id, "themes", tid)
        store.drop_inferred_contains(session_id, tid)
        removed += 1
    if removed:
        state["themes"] = kept
        store.save_state(session_id, state)
        notes.append(f"removed {removed} redundant topic(s)")
        store.bump_graph_revision(session_id)
    return notes


@router.post("/sessions/{session_id}/graph/organize/discard")
def discard_organize(session_id: str):
    _session_or_404(session_id)
    store.clear_pending_organize(session_id)
    return {"discarded": True}


@router.get("/sessions/{session_id}/graph/organize/pending")
def get_pending_organize(session_id: str):
    _session_or_404(session_id)
    pending = store.get_pending_organize(session_id)
    if not pending:
        return {"pending": None}
    return {"pending": _pending_public(pending)}


def _edge_or_404(session_id: str, edge_id: str) -> dict:
    for e in store.list_graph_edges(session_id):
        if e["id"] == edge_id:
            return e
    raise HTTPException(status_code=404, detail="No such connection in this session.")


def _assert_contains_ok(from_id: str, to_id: str, relation: str, node_kind: dict[str, str],
                        existing_parents: Optional[dict] = None) -> None:
    if relation != graph.HIERARCHY_RELATION:
        return
    if not graph.contains_source_ok(from_id, node_kind):
        raise HTTPException(status_code=400,
                            detail="Only a topic can contain something on the map.")
    if not graph.contains_target_ok(to_id, node_kind):
        raise HTTPException(status_code=400,
                            detail="A topic cannot contain the question itself.")
    if graph.contains_would_cycle(from_id, to_id, existing_parents or {}):
        raise HTTPException(status_code=400,
                            detail="That grouping would loop back on itself.")


def _cited_turn_ids(state: dict, unplaced: list[dict]) -> set:
    """Every turn id a currently-live map item names as its own source -- the
    bounded set `store.get_turns_by_ids` needs to build a short excerpt for
    each one (rule 134) without ever fetching the whole transcript."""
    ids: set = set()
    for name in core.ITEM_LISTS:
        for item in (state.get(name) or []):
            if isinstance(item, dict):
                ids.update(str(t) for t in (item.get("source_turn_ids") or []))
    for u in unplaced:
        ids.update(str(t) for t in (u.get("source_turn_ids") or []))
    return ids


def _assert_endpoint_ok(from_id: str, to_id: str, relation: str, node_kind: dict[str, str]) -> None:
    """The human side of `graph.endpoint_role_ok` (rule 133) -- a person's own
    connection is held to the same grammar a model's proposal is, so "answers"
    (say) cannot be drawn onto something that is not a question either way."""
    if not graph.endpoint_role_ok(relation, from_id, to_id, node_kind):
        raise HTTPException(status_code=400, detail=(
            f'"{graph.RELATION_META.get(relation, {}).get("label", relation)}" does not fit '
            "those two kinds of idea — see the relation's own meaning."))


@router.post("/sessions/{session_id}/graph/edges")
def add_graph_edge(session_id: str, req: EdgeIn):
    """
    A human drawing a connection by hand. Bypasses the AI rejection tombstone —
    a person adding back what they (or someone else) once rejected is a new,
    later decision, not the model quietly undoing the old one.
    """
    session = _session_or_404(session_id)
    relation = (req.relation or "").strip().lower()
    if relation not in graph.EDGE_RELATIONS:
        raise HTTPException(status_code=400, detail=f"Unknown relation: {relation}")
    state = store.get_state(session_id)
    node_ids, node_kind = graph.node_universe(
        state, store.list_decisions(session_id), store.list_action_items(session_id))
    if req.from_id not in node_ids or req.to_id not in node_ids:
        raise HTTPException(status_code=400, detail="That connection names a node that does not exist.")
    if req.from_id == req.to_id:
        raise HTTPException(status_code=400, detail="A connection cannot point a node at itself.")
    existing_parents, _human = graph.existing_parent_index(store.list_graph_edges(session_id))
    _assert_contains_ok(req.from_id, req.to_id, relation, node_kind, existing_parents)
    _assert_endpoint_ok(req.from_id, req.to_id, relation, node_kind)
    if relation == graph.HIERARCHY_RELATION:
        store.drop_inferred_contains(session_id, req.to_id, keep_from_id=req.from_id)
    existing_edges = store.list_graph_edges(session_id)
    is_new = not any(e["from_id"] == req.from_id and e["to_id"] == req.to_id
                     and e["relation"] == relation for e in existing_edges)
    if is_new and len(existing_edges) >= graph.MAX_STORED_EDGES:
        raise HTTPException(status_code=409, detail=(
            "This map already holds as many connections as it can — remove one before "
            "adding another."))
    edge = store.upsert_graph_edge(session_id, req.from_id, req.to_id, relation,
                                   label=req.label, human_edited=True)
    if not edge:
        raise HTTPException(status_code=409, detail="That connection could not be added.")
    store.bump_graph_revision(session_id)
    _touch_record(session_id)
    return {"edge": edge, "graph": _graph_snapshot(session_id, session)}


@router.patch("/sessions/{session_id}/graph/edges/{edge_id}")
def edit_graph_edge(session_id: str, edge_id: str, req: EdgePatchIn):
    _session_or_404(session_id)
    existing = _edge_or_404(session_id, edge_id)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if "relation" in fields:
        relation = str(fields["relation"]).strip().lower()
        if relation not in graph.EDGE_RELATIONS:
            raise HTTPException(status_code=400, detail=f"Unknown relation: {relation}")
        state = store.get_state(session_id)
        _, node_kind = graph.node_universe(
            state, store.list_decisions(session_id), store.list_action_items(session_id))
        existing_parents, _human = graph.existing_parent_index(store.list_graph_edges(session_id))
        _assert_contains_ok(existing["from_id"], existing["to_id"], relation, node_kind,
                            existing_parents)
        _assert_endpoint_ok(existing["from_id"], existing["to_id"], relation, node_kind)
        fields["relation"] = relation
    if not fields:
        raise HTTPException(status_code=400, detail="Nothing to change.")
    edge = store.update_graph_edge(edge_id, session_id=session_id, **fields)
    if not edge:
        raise HTTPException(status_code=404, detail="No such connection in this session.")
    store.bump_graph_revision(session_id)
    _touch_record(session_id)
    return {"edge": edge, "graph": _graph_snapshot(session_id)}


@router.delete("/sessions/{session_id}/graph/edges/{edge_id}")
def reject_graph_edge(session_id: str, edge_id: str):
    """A human taking a connection apart. Tombstoned so a later analysis pass
    cannot quietly recreate it (section 3)."""
    _session_or_404(session_id)
    _edge_or_404(session_id, edge_id)
    store.reject_graph_edge(edge_id, session_id=session_id)
    _touch_record(session_id)
    store.bump_graph_revision(session_id)
    return {"deleted": True, "graph": _graph_snapshot(session_id)}


@router.post("/sessions/{session_id}/graph/merge")
def merge_graph_nodes(session_id: str, req: MergeNodesIn):
    """
    Combine two nodes of the SAME kind into one — "node merging" (section 5).
    The survivor keeps `keep_id`, the union of both items' transcript
    provenance, and is marked human-edited; the other item is removed and
    tombstoned (rule 104) so it cannot come back as a near-duplicate, and any
    connection that named it is redirected to the survivor.
    """
    _session_or_404(session_id)
    if req.list_name not in core.ITEM_LISTS:
        raise HTTPException(status_code=400,
                            detail=f"There is no '{req.list_name}' in the consultation map.")
    try:
        saved = store.merge_map_items(session_id, req.list_name, req.keep_id, req.remove_id,
                                      text=req.text)
    except store.MergeRefused as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not saved:
        raise HTTPException(status_code=404,
                            detail="Both nodes must exist, in the same list, to be merged.")
    _touch_record(session_id)
    return {"state": saved, "graph": _graph_snapshot(session_id)}


@router.get("/sessions/{session_id}/graph/export.svg")
def export_graph_svg(session_id: str):
    session = _session_or_404(session_id)
    built = _graph_snapshot(session_id, session, persist_positions=False)
    svg = graph.render_svg(built, title=session.get("title") or "Consultation")
    return Response(content=svg, media_type="image/svg+xml")


@router.get("/sessions/{session_id}/graph/export.png")
def export_graph_png(session_id: str):
    session = _session_or_404(session_id)
    built = _graph_snapshot(session_id, session, persist_positions=False)
    png = graph.render_png_bytes(built, title=session.get("title") or "Consultation")
    return Response(content=png, media_type="image/png")


@router.get("/sessions/{session_id}/graph/export.html")
def export_graph_html(session_id: str):
    """A self-contained page — the map, the narrative and the authoritative
    outcomes — that opens with no app and no external service (section 6).
    Never includes a transcript excerpt: only what is already on the map."""
    session = _session_or_404(session_id)
    built = _graph_snapshot(session_id, session, persist_positions=False)
    try:
        narrative = json.loads(session.get("report_narrative_json") or "{}")
        if not isinstance(narrative, dict):
            narrative = {}
    except (ValueError, TypeError):
        narrative = {}
    html = graph.render_html_export(
        session, built, narrative, store.list_decisions(session_id),
        store.list_action_items(session_id), store.list_writings(session_id))
    return Response(content=html, media_type="text/html")


# ── Export ──────────────────────────────────────────────────────────────────

def _markdown(session: dict) -> str:
    sid = session["id"]
    state = store.get_state(sid)
    decisions = store.list_decisions(sid)
    confirmed = next((d for d in decisions if d["status"] == "confirmed"), None)
    lines = [f"# {session['title']}", ""]
    if session.get("question"):
        lines += [f"**Question before the group:** {session['question']}", ""]
    when = session.get("started_at") or session.get("created_at") or ""
    lines += [f"*{when} · {core.FRAMEWORKS.get(session.get('framework'), '')} · "
              f"{core.MODES.get(session.get('mode'), {}).get('label', '')}*", ""]
    if (state.get("summary") or "").strip():
        lines += ["## Summary", state["summary"].strip(), ""]

    def block(title: str, key: str, field: str = "text"):
        items = [i for i in (state.get(key) or []) if isinstance(i, dict) and i.get(field)]
        if not items:
            return
        lines.append(f"## {title}")
        for item in items:
            suffix = ""
            if key == "facts":
                status = item.get("status", core.DEFAULT_FACT_STATE)
                suffix = f" *({core.FACT_STATE_LABELS.get(status, status).lower()})*"
            else:
                # Nothing is deleted any more (rule 97), so an item that has
                # been dealt with is SHOWN with what happened to it rather than
                # quietly missing from the export.
                lifecycle = item.get("lifecycle", "open")
                if lifecycle != "open":
                    suffix = f" *({core.LIFECYCLE_LABELS.get(lifecycle, lifecycle).lower()}"
                    note = (item.get("resolution_note") or "").strip()
                    suffix += f": {note})*" if note else ")*"
            if item.get("human_edited"):
                suffix += " *[corrected by hand]*"
            lines.append(f"- {item[field]}{suffix}")
        lines.append("")

    block("Areas of agreement", "agreements")
    block("Ideas considered", "ideas")
    block("Unresolved", "tensions")
    block("Unresolved questions", "unresolved_questions")
    block("Key facts", "facts")
    block("Principles considered", "principles")
    block("Concerns", "needs_and_concerns")
    block("Questions to investigate", "questions_to_investigate")
    block("Possible syntheses", "possible_syntheses")

    lines.append("## Decision")
    confirmed_all = [d for d in decisions if d["status"] == "confirmed"]
    if confirmed_all:
        for d in confirmed_all:
            lines += [f"**Confirmed:** {d['text']}"]
            retained = [c for c in (d.get("retained_concerns") or []) if str(c).strip()]
            for c in retained:
                lines.append(f"  - *Concern carried forward:* {c}")
            lines.append("")
    else:
        outcome = session.get("closeout_outcome")
        lines += [core.CLOSEOUT_OUTCOMES.get(outcome or "", "")
                  if outcome else "No final decision was confirmed.", ""]
        if (session.get("closeout_note") or "").strip():
            lines += [session["closeout_note"].strip(), ""]
        for cand in decisions:
            if cand["status"] == "candidate":
                lines.append(f"- Possible decision (not confirmed): {cand['text']}")
        if decisions:
            lines.append("")

    actions = store.list_action_items(sid)
    lines.append("## Action items")
    if actions:
        for a in actions:
            owner = a.get("owner") or "Owner not assigned"
            # By value, not identity -- SQLite returns 1/0 (see the report).
            accepted = a.get("owner_accepted")
            if a.get("owner"):
                owner += (" — not yet accepted" if accepted is None else
                          " — accepted" if accepted else " — did not accept")
            due = f" — due {a['due']}" if a.get("due") else ""
            status = a.get("status") or "proposed"
            # Only show the status when it says something the acceptance state
            # has not already said -- "Tara - accepted - accepted" is noise.
            mark = ("" if status in ("proposed", "accepted")
                    else f" — {core.ACTION_STATUS_LABELS.get(status, status).lower()}")
            lines.append(f"- {a['action']} *({owner}{due}{mark})*")
    else:
        lines.append("None recorded.")
    lines.append("")

    passages = store.list_writings(sid)
    if passages:
        lines.append("## Verified writings")
        for p in passages:
            source = " — ".join(x for x in (p.get("source"), p.get("section")) if x)
            lines += [f"> {p['text']}", "", f"*{source}*", ""]

    lines.append("## Transcript")
    if session.get("transcript_deleted_at"):
        # Rule 94. The record survives the words; saying so plainly is better
        # than an empty heading that reads like the meeting was silent.
        lines += [f"The transcript was deleted on {session['transcript_deleted_at']}, "
                  "as the group chose. Everything above is the record that was kept.", ""]
    else:
        for turn in store.list_turns(sid, final_only=True, source="best"):
            who = turn.get("speaker_label") or ("Assistant" if turn["role"] == "assistant"
                                                else "Participant")
            corrected = " *(corrected)*" if turn.get("corrected_at") else ""
            lines.append(f"**{who}:** {turn['text']}{corrected}")
        lines.append("")
    return "\n".join(lines)


@router.post("/sessions/{session_id}/cleanup/retry")
def retry_cleanup(session_id: str):
    """Try again to remove recording files a deletion could not (rule 103).

    Deletion used to swallow an unlink failure and still report success, so a
    recording could be left on disk while the screen said it was gone. What
    remains is kept -- the file name and the error, never the content -- and
    this is the way to clear it.
    """
    _session_or_404(session_id)
    result = store.retry_cleanup(session_id)
    return {**result, "session": store.get_session(session_id)}


@router.get("/sessions/{session_id}/export", response_class=PlainTextResponse)
def export_session(session_id: str, scope: str = "full"):
    """
    The record as Markdown, for someone who wants it outside this application.
    It leaves the private store only because a person asked for it, by hand,
    one session at a time.

    Three scopes, and the difference between the first two is the whole point
    (rule 102):

    * `outcomes` -- the APPROVED record: the text a person read and put their
      name to. It used to return `report_md`, which is the DRAFT written
      automatically when the meeting ended, and call it approved. That draft was
      available before any closeout had happened and did not change when an
      owner was corrected, so an "approved record" could be sent to somebody
      containing a fact the group had already fixed.
    * `draft` -- the current unapproved text, explicitly labelled as one. A
      draft may be exported; it may not be exported as an approved record.
    * `full` -- the working record: map, transcript and all. Refused once the
      transcript has been deleted, rather than quietly returning a "full"
      export with the largest part of it missing (rule 94).
    """
    session = _session_or_404(session_id)
    if scope not in ("full", "outcomes", "draft"):
        raise HTTPException(status_code=400, detail=f"Unknown export scope: {scope}")

    if scope == "outcomes":
        text = (session.get("approved_md") or "").strip()
        if not text:
            raise HTTPException(status_code=409, detail=(
                "Nobody has approved a record for this consultation yet, so there is no "
                "approved record to export. Review it and approve it first, or export "
                "scope=draft to send the current draft as a draft."))
        status = _record_status(session)
        header = [f"<!-- Approved {status['approved_at']} -->"]
        if status["stale"]:
            # Not silently corrected and not silently sent either: the person
            # exporting is told, in the file, that the record moved on.
            header.append(
                "> **Note:** this is the approved record. The consultation record has "
                "been edited since it was approved, so this copy is not the latest.")
            header.append("")
        return PlainTextResponse("\n".join(header) + "\n" + text + "\n",
                                 media_type="text/markdown; charset=utf-8")

    if scope == "draft":
        built = _build_record(session, reuse_narrative=True)
        text = (built["markdown"] or "").strip()
        if not text:
            raise HTTPException(status_code=404,
                                detail="There is nothing recorded for this consultation yet.")
        banner = ("> **DRAFT — not approved.** Nobody has reviewed and approved this "
                  "record.\n\n") if not session.get("approved_at") else (
                 "> **DRAFT — newer than the approved record.**\n\n")
        return PlainTextResponse(banner + text + "\n",
                                 media_type="text/markdown; charset=utf-8")

    if session.get("transcript_deleted_at"):
        raise HTTPException(
            status_code=409,
            detail=("The transcript of this consultation was deleted, so there is no "
                    "full export any more. The approved record is still available "
                    "(scope=outcomes)."))
    return PlainTextResponse(_markdown(session), media_type="text/markdown; charset=utf-8")
