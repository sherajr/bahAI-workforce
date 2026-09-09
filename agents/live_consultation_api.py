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

import threading
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from agents import live_consultation as core
from agents import live_consultation_audio as audio
from agents import live_consultation_governor as governor
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


def _detail(session: dict) -> dict:
    sid = session["id"]
    state = store.get_state(sid)
    decisions = store.list_decisions(sid)
    return {
        "session": session,
        "state": state,
        "turns": store.list_turns(sid),
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
        "final_turns": store.list_turns(sid, source="best"),
        "has_diarized": store.has_diarized(sid),
        "report": session.get("report_md") or "",
        "mode_info": core.MODES.get(session.get("mode") or core.DEFAULT_MODE, {}),
    }


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    return _detail(_session_or_404(session_id))


@router.patch("/sessions/{session_id}")
def patch_session(session_id: str, req: SessionPatch):
    _session_or_404(session_id)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if "mode" in fields and fields["mode"] not in core.MODES:
        raise HTTPException(status_code=400, detail=f"Unknown participation mode: {fields['mode']}")
    if "presence" in fields and fields["presence"] not in core.PRESENCE_LEVELS:
        raise HTTPException(status_code=400, detail=f"Unknown presence: {fields['presence']}")
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
    if session["status"] == "ended":
        raise HTTPException(status_code=400, detail="That consultation has already ended.")
    if not session.get("participants_informed_at"):
        raise HTTPException(
            status_code=400,
            detail=("Before this starts, everyone in the room has to be told that "
                    f"{core.ASSISTANT_NAME} is listening and transcribing"
                    + (", and that the meeting is being recorded"
                       if session.get("record_audio") else "")
                    + ". Confirm that on the setup screen."))
    return _detail(store.start_session(session_id))


@router.post("/sessions/{session_id}/inform")
def confirm_informed(session_id: str):
    """The host's attestation, recorded with a timestamp. It says the host said
    so — never that anybody consented, which this application cannot know."""
    _session_or_404(session_id)
    return _detail(store.update_session(
        session_id,
        participants_informed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")))


@router.post("/sessions/{session_id}/end")
def end_session(session_id: str, final_pass: bool = True):
    """
    Close the meeting and, if there is anything unread and a key configured,
    make one last analysis pass so the record is complete.

    A failed final pass is reported, never fatal: the transcript and the map as
    they stand are already saved, and losing them to a model error would be
    the worst possible moment for it.
    """
    session = _session_or_404(session_id)
    ended = store.end_session(session_id)
    note = ""
    if final_pass and realtime.available():
        try:
            result = _run_analysis(ended or session, final_pass=True)
            note = result.get("note", "")
        except Exception as e:
            note = f"The closing summary could not be made ({type(e).__name__}). Everything said is saved."
    # The report is what the room actually wants when the meeting stops, so it
    # is written here rather than waiting for someone to ask (owner ask
    # 2026-08-25). It is built AFTER the final analysis pass, so it summarises
    # the finished map rather than the map as it stood a minute before the end.
    # A failure here is a note, never an error: the meeting is over and
    # everything said is already saved.
    try:
        current = store.get_session(session_id)
        built = report.build_report(
            session=current, state=store.get_state(session_id),
            decisions=store.list_decisions(session_id),
            actions=store.list_action_items(session_id),
            writings=store.list_writings(session_id),
            turns=store.list_turns(session_id, source="best"),
            participants=store.list_participants(session_id))
        store.update_session(session_id, report_md=built["markdown"],
                             report_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        if built["note"]:
            note = (note + " " + built["note"]).strip()
    except Exception as e:
        note = (note + f" The report could not be written ({type(e).__name__}); "
                       "it can be written by hand from the record.").strip()
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
    _session_or_404(session_id)
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
    turn = store.label_turn(turn_id, req.speaker_label)
    if not turn or turn.get("session_id") != session_id:
        raise HTTPException(status_code=404, detail="No such turn in this session.")
    return {"turn": turn}


# ── Analysis ────────────────────────────────────────────────────────────────

def _run_analysis(session: dict, force: bool = False, final_pass: bool = False) -> dict:
    sid = session["id"]
    lock = _lock_for(sid)
    if not lock.acquire(blocking=False):
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
        recent = store.list_turns(sid, final_only=True, limit=reasoner.RECENT_WINDOW)
        result = reasoner.analyze(session, state, new_turns, recent, final_pass=final_pass)
        _LAST_ANALYSIS[sid] = time.time()
        if not result.ok:
            return {"ran": True, "ok": False, "note": result.note}

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
        for cand in saved.get("decision_candidates", []):
            store.upsert_decision_candidate(
                sid, cand.get("text", ""), cand.get("rationale", ""),
                cand.get("support", ""), cand.get("concerns") or [],
                map_id=cand.get("id") or None)
        for act in saved.get("action_items", []):
            store.upsert_action_item(sid, act.get("action", ""), act.get("owner"),
                                     act.get("due"), map_id=act.get("id") or None)

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
            "writings": found, "merge_notes": result.notes,
        }
    finally:
        lock.release()


def _validate_provenance(session_id: str, state: dict) -> tuple[dict, int]:
    """Drop any `source_turn_ids` entry that is not a real turn in this session.

    A source reference is only ever evidence of what was SAID in this meeting —
    never evidence that the thing said is true — and the UI is careful to say
    so. What it must at least be is real: a citation that opens nothing is
    worse than none, because it looks like corroboration.
    """
    valid = {str(t["id"]) for t in store.list_turns(session_id)}
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
                                         retained_concerns=req.retained_concerns)
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
    return {"decision": decision, "state": saved}


@router.post("/sessions/{session_id}/decisions/{decision_id}/reject")
def reject_decision(session_id: str, decision_id: str):
    """"Not decided." The candidate stays in the record; it just is not a
    decision, which is a truthful thing for a meeting to end with."""
    _session_or_404(session_id)
    _decision_or_404(session_id, decision_id)
    decision = store.set_decision_status(decision_id, "rejected")
    state = store.get_state(session_id)
    if (state.get("confirmed_decision") or {}).get("id") == decision_id:
        state["confirmed_decision"] = None
    saved = store.save_state(session_id, state)
    return {"decision": decision, "state": saved}


@router.post("/sessions/{session_id}/actions/{action_id}/status")
def set_action_status(session_id: str, action_id: str, req: ActionStatusIn):
    _session_or_404(session_id)
    if req.status not in ("open", "done"):
        raise HTTPException(status_code=400, detail=f"Unknown status: {req.status}")
    item = store.set_action_status(action_id, req.status)
    if not item or item.get("session_id") != session_id:
        raise HTTPException(status_code=404, detail="No such action item in this session.")
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
    if not store.remove_participant(participant_id):
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
    person = store.set_participant_speaker(participant_id, req.speaker_key)
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
    if not session.get("record_audio"):
        raise HTTPException(status_code=400,
                            detail="This meeting was not set to be recorded.")
    data = await file.read()
    try:
        path = audio.save_recording(session_id, data, file.filename or "meeting.webm")
    except audio.AudioError as e:
        store.update_session(session_id, audio_status="failed", audio_note=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    store.update_session(session_id, audio_status="uploaded", audio_note="")
    return {"saved": True, "bytes": path.stat().st_size,
            "session": store.get_session(session_id)}


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
    data = await file.read()
    try:
        result = audio.transcribe_plain(data, file.filename or "note.webm")
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

@router.post("/sessions/{session_id}/report")
def make_report(session_id: str):
    """
    Build the one readable page the meeting produced.

    The decision, the actions and any passages are copied out of the record; the
    summarising prose is written by the reasoning model. A model failure costs
    the prose and says so -- it never costs the report.
    """
    session = _session_or_404(session_id)
    sid = session["id"]
    built = report.build_report(
        session=session,
        state=store.get_state(sid),
        decisions=store.list_decisions(sid),
        actions=store.list_action_items(sid),
        writings=store.list_writings(sid),
        turns=store.list_turns(sid, source="best"),
        participants=store.list_participants(sid),
    )
    store.update_session(sid, report_md=built["markdown"],
                         report_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return {"report": built["markdown"], "note": built["note"],
            "narrated": built["narrated"], "session": store.get_session(sid)}


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
    saved = store.save_state(session_id, state)
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
    turn = store.correct_turn(turn_id, text)
    if not turn or turn.get("session_id") != session_id:
        raise HTTPException(status_code=404, detail="No such turn in this session.")
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
    return {"action_item": item}


@router.patch("/sessions/{session_id}/actions/{action_id}")
def edit_action(session_id: str, action_id: str, req: ActionPatchIn):
    _session_or_404(session_id)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="Nothing to change.")
    item = store.update_action_item(action_id, **fields)
    if not item or item.get("session_id") != session_id:
        raise HTTPException(status_code=404, detail="No such action item in this session.")
    return {"action_item": item}


@router.delete("/sessions/{session_id}/actions/{action_id}")
def remove_action(session_id: str, action_id: str):
    _session_or_404(session_id)
    items = {a["id"] for a in store.list_action_items(session_id)}
    if action_id not in items:
        raise HTTPException(status_code=404, detail="No such action item in this session.")
    return {"deleted": store.delete_action_item(action_id)}


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
        action_id, owner_accepted=req.accepted,
        accepted_by=(req.accepted_by or "").strip(),
        status="accepted" if req.accepted else None)
    if not item or item.get("session_id") != session_id:
        raise HTTPException(status_code=404, detail="No such action item in this session.")
    return {"action_item": item}


@router.patch("/sessions/{session_id}/decisions/{decision_id}")
def edit_decision(session_id: str, decision_id: str, req: DecisionPatchIn):
    _session_or_404(session_id)
    _decision_or_404(session_id, decision_id)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="Nothing to change.")
    decision = store.update_decision(decision_id, **fields)
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
    updated = store.update_session(session_id, **fields)

    note = ""
    # `until_closeout` means exactly this moment. Done here rather than on a
    # timer so the deletion happens while the person who chose it is watching.
    if (updated or {}).get("retention_policy") == "until_closeout":
        removed = store.delete_transcript(session_id)
        note = (f"The transcript has been deleted ({removed['turns']} lines). "
                "The approved record is kept.")
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
    detail["note"] = (f"{removed['turns']} transcript line(s) deleted"
                      + (f" and {removed['audio_files']} recording file(s) removed"
                         if removed["audio_files"] else "")
                      + ". The approved record is kept; the words are gone for good.")
    return detail


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


@router.get("/sessions/{session_id}/export", response_class=PlainTextResponse)
def export_session(session_id: str, scope: str = "full"):
    """
    The record as Markdown, for someone who wants it outside this application.
    It leaves the private store only because a person asked for it, by hand,
    one session at a time.

    `scope=outcomes` is the APPROVED record alone — the report, which is what
    somebody actually wants to send to a person who was not there. `scope=full`
    adds the working map and the transcript, and is refused once the transcript
    has been deleted rather than quietly returning a "full" export with the
    largest part of it missing (rule 94).
    """
    session = _session_or_404(session_id)
    if scope not in ("full", "outcomes"):
        raise HTTPException(status_code=400, detail=f"Unknown export scope: {scope}")
    if scope == "outcomes":
        text = (session.get("report_md") or "").strip()
        if not text:
            raise HTTPException(
                status_code=404,
                detail="No approved record has been written for this consultation yet.")
        return PlainTextResponse(text + "\n", media_type="text/markdown; charset=utf-8")
    if session.get("transcript_deleted_at"):
        raise HTTPException(
            status_code=409,
            detail=("The transcript of this consultation was deleted, so there is no "
                    "full export any more. The approved record is still available."))
    return PlainTextResponse(_markdown(session), media_type="text/markdown; charset=utf-8")
