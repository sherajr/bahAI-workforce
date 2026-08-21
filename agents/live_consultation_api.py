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

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from agents import live_consultation as core
from agents import live_consultation_governor as governor
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


class SessionPatch(BaseModel):
    title: Optional[str] = None
    question: Optional[str] = None
    context: Optional[str] = None
    framework: Optional[str] = None
    mode: Optional[str] = None
    decision_method: Optional[str] = None
    presence: Optional[str] = None


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


class ActionStatusIn(BaseModel):
    status: str = "open"


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
        # Raw audio is never stored, and no recorder is implemented. Saying
        # false is the honest answer; a checkbox that quietly does nothing is
        # the Canva-autofill failure this repo has already had once.
        "recording_supported": False,
        "realtime_model": core.REALTIME_MODEL,
        "reasoning_model": core.REASONING_MODEL,
        "transcribe_model": core.TRANSCRIBE_MODEL,
        "voice": core.VOICE,
        "calls_url": realtime.CALLS_URL,
        "modes": [{"id": k, **v} for k, v in core.MODES.items()],
        "frameworks": [{"id": k, "label": v} for k, v in core.FRAMEWORKS.items()],
        "decision_methods": [{"id": k, "label": v} for k, v in core.DECISION_METHODS.items()],
        "presence_levels": [{"id": k, **v} for k, v in core.PRESENCE_LEVELS.items()],
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
    if req.record_audio:
        # There is no recorder. Refusing is better than accepting a flag that
        # would read on screen as "your meeting is being recorded".
        raise HTTPException(
            status_code=400,
            detail="Raw audio recording is not implemented; nothing would be saved.")
    return store.create_session(
        title=req.title, question=req.question, context=req.context,
        framework=req.framework, mode=req.mode, decision_method=req.decision_method,
        presence=req.presence, record_audio=False,
        realtime_model=core.REALTIME_MODEL, reasoning_model=core.REASONING_MODEL,
        transcribe_model=core.TRANSCRIBE_MODEL, voice=core.VOICE,
    )


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
        "confirmed_decision": next((d for d in decisions if d["status"] == "confirmed"), None),
        "action_items": store.list_action_items(sid),
        "writings": store.list_writings(sid),
        "speech_events": store.list_speech_events(sid, limit=25),
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
    session = _session_or_404(session_id)
    if session["status"] == "ended":
        raise HTTPException(status_code=400, detail="That consultation has already ended.")
    return _detail(store.start_session(session_id))


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

        saved = store.save_state(sid, result.state)
        store.mark_turns_analyzed(sid, [t["id"] for t in new_turns])

        # Decision candidates and action items get their own rows so a human can
        # act on them one at a time — and so `confirmed_decision` has exactly one
        # writable path (rule 81).
        for cand in saved.get("decision_candidates", []):
            store.upsert_decision_candidate(
                sid, cand.get("text", ""), cand.get("rationale", ""),
                cand.get("support", ""), cand.get("concerns") or [])
        for act in saved.get("action_items", []):
            store.upsert_action_item(sid, act.get("action", ""), act.get("owner"), act.get("due"))

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


def _lookup_writings(session_id: str, theme: str) -> dict:
    """Verified passages only, stored against the session (rule 84)."""
    result = writings.search(theme)
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
def confirm_decision(session_id: str, decision_id: str):
    """
    A human confirming a decision. This is the ONLY way `confirmed_decision`
    is ever populated — no analysis pass, no model, and no amount of apparent
    agreement can do it (rule 81).
    """
    _session_or_404(session_id)
    _decision_or_404(session_id, decision_id)
    decision = store.set_decision_status(decision_id, "confirmed")
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
        raise HTTPException(status_code=402, detail=(
            f"This month's metered API spend (${spend['month_total']}) is already over "
            f"the ${spend['monthly_ceiling']} ceiling. Start anyway from the setup "
            "screen if you want to go ahead."))
    try:
        credential = realtime.create_client_secret(session)
    except realtime.RealtimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    credential["session_id"] = session["id"]
    credential["spend"] = spend
    return credential


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
                suffix = f" *({item.get('status', 'uncertain')})*"
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
    if confirmed:
        lines += [f"**Confirmed:** {confirmed['text']}", ""]
    else:
        lines += ["No final decision was confirmed.", ""]
        for cand in decisions:
            lines.append(f"- Possible decision (not confirmed): {cand['text']}")
        if decisions:
            lines.append("")

    actions = store.list_action_items(sid)
    lines.append("## Action items")
    if actions:
        for a in actions:
            owner = a.get("owner") or "Owner not assigned"
            due = f" — due {a['due']}" if a.get("due") else ""
            lines.append(f"- {a['action']} *({owner}{due})*")
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
    for turn in store.list_turns(sid, final_only=True):
        who = turn.get("speaker_label") or ("Assistant" if turn["role"] == "assistant"
                                            else "Participant")
        lines.append(f"**{who}:** {turn['text']}")
    lines.append("")
    return "\n".join(lines)


@router.get("/sessions/{session_id}/export", response_class=PlainTextResponse)
def export_session(session_id: str):
    """The whole record as Markdown, for someone who wants it outside this
    application. It leaves the private store only because a person asked for
    it, by hand, one session at a time."""
    session = _session_or_404(session_id)
    return PlainTextResponse(_markdown(session), media_type="text/markdown; charset=utf-8")
