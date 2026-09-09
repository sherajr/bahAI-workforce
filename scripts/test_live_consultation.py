"""
Offline regression suite for Live Consultation (rules 73-86).

    python scripts/test_live_consultation.py

Free and fast: no LLM calls, no realtime session, no microphone, no network and
no keys. Every model call and every HTTP call to OpenAI is a stub, because a
test that spends money to prove the code is shaped right is a test that will
eventually not be run.

What it pins, in order:
  1. the private store, and that a test can never open the owner's database
  2. the Speech Governor -- above all, that SILENCE NEVER PERMITS SPEECH
  3. the floor state machine, including barge-in from every state
  4. the reasoner: debounce, JSON repair, merge, and what a bad reply costs
  5. the realtime session config (create_response false) and cost estimation
  6. verified writings: never invented, a near miss is a failure
  7. the endpoints end to end through FastAPI's TestClient
  8. the isolation rules: nothing personal in workforce.db, the product
     consultation pipeline untouched

Console output is ASCII only (Windows cp1252 -- see AGENTS.md gotchas).
"""

import json
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── The network tripwire (rule 99) ──────────────────────────────────────────
#
# This suite calls itself offline. It was not. Until 2026-09-03 a normal run
# made three real, AUTHENTICATED calls to OpenAI on the owner's own key -- a
# model lookup, an actual mint of a live realtime credential, and a REAL
# billable chat completion (the report narrative, reached through
# `end_session`). It cost money on every run and nobody could see it: the suite
# redirects `state.DB_PATH` to a temp file, so `record_spend` wrote the charge
# into a database that is thrown away at exit.
#
# The hole was not a missing stub. The suite DOES set a fake key below -- but
# `agents/api.py` calls `load_dotenv(..., override=True)` at import, which puts
# the owner's real key straight back into `os.environ`. An assignment to
# `os.environ` is therefore NOT a defence and must never be treated as one
# again. That is why the guarantee is at the socket, ahead of every import: it
# cannot be undone by any later import, monkeypatch or env reload.
#
# Loopback stays open -- FastAPI's TestClient needs it. Anything else raises,
# loudly, naming the call site.
import socket as _socket

_OUTBOUND: list[str] = []
_real_connect = _socket.socket.connect


class _NetworkAttempted(BaseException):
    """Deliberately NOT an Exception -- exactly rule 55's reasoning.

    This codebase is full of `except Exception` blocks that turn a failed call
    into a survivable recorded error, and every one of them would swallow this
    and let the run report success. `realtime.check_model` is the sharpest
    case: it catches Exception and returns "the model is fine", so a blocked
    call was indistinguishable from a working one. That is exactly how three
    real paid calls hid inside a suite that printed "408 passed".
    """


def _is_loopback(address) -> bool:
    host = address[0] if isinstance(address, tuple) else address
    if not isinstance(host, str):
        return False
    return host.startswith("127.") or host in ("::1", "localhost", "0.0.0.0", "")


def _tripwire_connect(self, address, *args, **kwargs):
    if _is_loopback(address):
        return _real_connect(self, address, *args, **kwargs)
    import traceback
    site = "unknown"
    for frame in reversed(traceback.extract_stack()[:-1]):
        if "test_live_consultation" not in frame.filename:
            site = f"{os.path.basename(frame.filename)}:{frame.lineno} {frame.name}"
            break
    _OUTBOUND.append(site)
    raise _NetworkAttempted(
        "NETWORK TRIPWIRE: this suite is offline and something tried to reach "
        f"{address} from {site}. Stub the call rather than relaxing this -- a "
        "real request here spends the owner's money and is invisible, because "
        "spend is metered into a temp database that is discarded."
    )


_socket.socket.connect = _tripwire_connect

_TMP = Path(tempfile.mkdtemp(prefix="livecons_test_"))
os.environ["ANTHROPIC_API_KEY"] = os.environ.get("ANTHROPIC_API_KEY", "test-key")
os.environ.pop("DASHBOARD_ORIGINS", None)
# The suite presents a real key to the owner gate (rule 70: there is no bypass,
# not even for tests), but never the owner's own file.
os.environ["DASHBOARD_API_KEY"] = "test-" + ("c" * 59)

import agents.state as state  # noqa: E402

state.DB_PATH = _TMP / "workforce.db"

import agents.colony as colony  # noqa: E402

colony.DB_PATH = state.DB_PATH
state.init_db()

import agents.auth as auth  # noqa: E402

auth.PRIVATE_DIR = _TMP
auth.KEY_PATH = _TMP / "api_key.txt"

import agents.live_consultation as core  # noqa: E402
import agents.live_consultation_governor as gov  # noqa: E402
import agents.live_consultation_realtime as rt  # noqa: E402
import agents.live_consultation_reasoner as brain  # noqa: E402
import agents.live_consultation_store as store  # noqa: E402
import agents.live_consultation_writings as writ  # noqa: E402

# Point the store at a temp database BEFORE the API module ever touches it.
TEST_DB = _TMP / "consultation.db"
store.assert_test_db(TEST_DB)
store.DB_PATH = TEST_DB
store.AUDIO_DIR = _TMP / "consultation_audio"
store.init_db()

# ── The three real calls this suite used to make (rule 99) ──────────────────
#
# The tripwire proves nothing reaches the network; these stubs are what stop
# anything TRYING. Each wraps a function that already takes an injectable
# sender, so the tests exercising the real logic with their own fake sender
# keep doing so -- only the DEFAULT path, the one an endpoint takes, is made
# offline.
#
#   1. realtime.check_model          <- GET /capabilities      (free, but real)
#   2. realtime.create_client_secret <- POST /realtime/client-secret
#                                       (this actually MINTED a live credential)
#   3. router.call_openai            <- report.build_report <- POST .../end
#                                       (a real, billable chat completion)

_real_check_model = rt.check_model
_real_create_secret = rt.create_client_secret


def _offline_check_model(model_id, get=None):
    # A test injecting its own sender is testing the parsing, not the network.
    if get is not None:
        return _real_check_model(model_id, get=get)
    return True, ""


class _StubSecretResponse:
    status_code = 200

    @staticmethod
    def json():
        return {"value": "ek_test_not_a_real_client_secret",
                "expires_at": 9999999999, "session": {}}


def _offline_create_client_secret(session, instructions=None, post=None):
    return _real_create_secret(session, instructions=instructions,
                               post=post or (lambda *a, **k: _StubSecretResponse()))


rt.check_model = _offline_check_model
rt.create_client_secret = _offline_create_client_secret

# The report's narrative half. `router.call_openai` itself is deliberately NOT
# stubbed: the suite tests its real retry logic further down with its own fake
# transport, which is already offline. What must be made offline is the DEFAULT
# path `end_session` takes.
import agents.live_consultation_audio as audio  # noqa: E402
import agents.live_consultation_report as report  # noqa: E402
import agents.live_consultation_api as lcapi  # noqa: E402

_real_narrative = report._narrative


def _offline_narrative(session, state, call=None):
    if call is not None:
        return _real_narrative(session, state, call=call)
    return {}, "The summary was not written (offline test run)."


report._narrative = _offline_narrative

PASS = FAIL = 0
FAILURES: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(label + ((" -- " + detail) if detail else ""))


def section(name: str) -> None:
    print("\n-- " + name + " --")


# --- 1. The private store ----------------------------------------------------

section("the private store")

check("store writes to private/consultation.db by default",
      "private" in str(store.PRIVATE_DIR).replace("\\", "/"))
check("this suite is NOT pointed at the real database", store.DB_PATH == TEST_DB)

try:
    store.assert_test_db(Path(__file__).parent.parent / "private" / "consultation.db")
    check("assert_test_db refuses the real database", False)
except RuntimeError:
    check("assert_test_db refuses the real database", True)
try:
    store.assert_test_db(Path(__file__).parent.parent / "private" / "anything.db")
    check("assert_test_db refuses anything inside private/", False)
except RuntimeError:
    check("assert_test_db refuses anything inside private/", True)

sess = store.create_session("Gathering timing", question="When should we gather?",
                            context="First time in this neighbourhood.",
                            mode="facilitator", decision_method="consensus")
SID = sess["id"]
check("session created", bool(SID) and sess["status"] == "draft", str(sess.get("status")))
check("session carries its question", sess["question"] == "When should we gather?")

t1 = store.upsert_turn(SID, "I think Saturday morning works", realtime_item_id="item_1",
                       is_final=True)
t2 = store.upsert_turn(SID, "Saturday morning works for most of us",
                       realtime_item_id="item_1", is_final=True)
check("a repeated item id updates one turn, never doubles it", t1["id"] == t2["id"])
check("the later text wins", t2["text"].endswith("most of us"))
check("only one turn row exists", len(store.list_turns(SID)) == 1)

store.upsert_turn(SID, "", realtime_item_id="item_1", is_final=False)
check("a late empty partial cannot erase a finalised turn",
      store.list_turns(SID)[0]["text"].endswith("most of us"))
check("a finalised turn is never demoted to partial",
      store.list_turns(SID)[0]["is_final"] == 1)

# Ordering is by FIRST APPEARANCE, not by completion order (rule 80).
store.upsert_turn(SID, "long thought, finishes late", realtime_item_id="item_2")
store.upsert_turn(SID, "short reply", realtime_item_id="item_3")
store.upsert_turn(SID, "short reply", realtime_item_id="item_3", is_final=True)
store.upsert_turn(SID, "long thought, finishes late", realtime_item_id="item_2", is_final=True)
order = [t["realtime_item_id"] for t in store.list_turns(SID)]
check("turn order follows first appearance, not completion order",
      order == ["item_1", "item_2", "item_3"], str(order))

pending = store.unanalyzed_turns(SID)
check("unanalysed turns are the finalised ones", len(pending) == 3, str(len(pending)))
store.mark_turns_analyzed(SID, [t["id"] for t in pending])
check("marked turns do not come back", store.unanalyzed_turns(SID) == [])

labelled = store.label_turn(pending[0]["id"], "Tara")
check("a human can label a speaker by hand", labelled["speaker_label"] == "Tara")
check("no speaker label is invented anywhere else",
      all(t["speaker_label"] is None for t in store.list_turns(SID)[1:]))

saved = store.save_state(SID, {"summary": "Timing under discussion", "ideas": []})
check("saving state bumps the revision", saved["state_revision"] == 1)
saved2 = store.save_state(SID, {"summary": "Still timing"})
check("the revision only ever increases", saved2["state_revision"] == 2)
check("session row tracks the revision", store.get_session(SID)["state_revision"] == 2)

o1 = store.add_observation(SID, "possible_synthesis", "Optional RSVP may satisfy both",
                           importance=0.9, should_request_floor=True,
                           permission_request="Would it help to hear a possible synthesis?",
                           speech_brief="Optional RSVP keeps walk-ins and helps planning.",
                           state_revision=2)
o_dup = store.add_observation(SID, "possible_synthesis", "Optional RSVP may satisfy both",
                              importance=0.9, state_revision=2)
check("an observation already made is not made again", o1 is not None and o_dup is None)

d1 = store.upsert_decision_candidate(SID, "Hold the gathering on Saturday")
d_dup = store.upsert_decision_candidate(SID, "Hold the gathering on Saturday")
check("decision candidates deduplicate", d1["id"] == d_dup["id"])
check("a candidate is never born confirmed", d1["status"] == "candidate")
check("no confirmed decision before a human confirms one",
      store.confirmed_decision(SID) is None)
store.set_decision_status(d1["id"], "confirmed")
check("confirming works when a human asks for it",
      (store.confirmed_decision(SID) or {}).get("id") == d1["id"])

a1 = store.upsert_action_item(SID, "Book the hall", owner=None, due=None)
check("an action item with no owner stays unowned", a1["owner"] is None)

section("a database made before presence existed")

_old_db = _TMP / "before_presence.db"
_conn = __import__("sqlite3").connect(_old_db)
_conn.execute("""CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT NOT NULL,
    question TEXT NOT NULL DEFAULT '', context TEXT NOT NULL DEFAULT '',
    framework TEXT NOT NULL DEFAULT 'bahai', mode TEXT NOT NULL DEFAULT 'facilitator',
    decision_method TEXT NOT NULL DEFAULT 'unspecified', status TEXT NOT NULL DEFAULT 'draft',
    record_audio INTEGER NOT NULL DEFAULT 0, realtime_model TEXT, reasoning_model TEXT,
    transcribe_model TEXT, voice TEXT, state_revision INTEGER NOT NULL DEFAULT 0,
    created_at TEXT, started_at TEXT, ended_at TEXT)""")
_conn.execute("INSERT INTO sessions (id, title) VALUES ('old_1','A meeting from before')")
_conn.commit()
_conn.close()
store.init_db(_old_db)
_old_row = store.get_session("old_1", db_path=_old_db)
check("an existing meeting survives the migration",
      _old_row is not None and _old_row["title"] == "A meeting from before")
check("and is given the default presence rather than a NULL",
      _old_row["presence"] == core.DEFAULT_PRESENCE, str(_old_row.get("presence")))
check("init_db is safe to run twice", store.init_db(_old_db) is None)

# --- 2. The Speech Governor --------------------------------------------------

section("the speech governor: silence is not permission")

FOREVER = 10 * 60 * 1000
for silence in (5_000, 7_000, 12_000, 20_000, 30_000, 120_000, FOREVER):
    d = gov.evaluate(gov.SpeechRequest(
        kind="unsolicited", mode="facilitator",
        ms_since_human_speech_ended=silence, ms_since_session_start=FOREVER))
    check(f"silence of {silence}ms alone does not permit speech",
          not d.allowed, f"{d.code}: {d.reason}")

d = gov.evaluate(gov.SpeechRequest(
    kind="unsolicited", mode="active",
    ms_since_human_speech_ended=FOREVER, ms_since_session_start=FOREVER))
check("not even in active-participant mode", not d.allowed, d.code)

# The presence dial (rule 87) makes her quicker. It must not make silence into
# permission at ANY setting -- that is the line the dial cannot cross.
for level in core.PRESENCE_LEVELS:
    for mode in ("facilitator", "active"):
        d = gov.evaluate(gov.SpeechRequest(
            kind="unsolicited", mode=mode, presence=level,
            ms_since_human_speech_ended=FOREVER, ms_since_session_start=FOREVER))
        check(f"silence never permits speech at presence={level}, mode={mode}",
              not d.allowed, d.code)

# The one thing that CAN open the door: something material and fresh.
good = dict(kind="unsolicited", mode="facilitator", ms_since_human_speech_ended=8_000,
            ms_since_session_start=FOREVER, observation_should_request_floor=True,
            observation_importance=0.9, observation_revision=7, current_revision=7)
d = gov.evaluate(gov.SpeechRequest(**good))
check("a material, fresh observation may ASK for the floor",
      d.allowed and d.action == "request_permission", f"{d.code}")
check("and asking is all it may do -- never speak directly", d.action != "speak")

section("the speech governor: categorical refusals")

check("scribe mode never speaks, even when invited",
      gov.evaluate(gov.SpeechRequest(kind="invited", mode="scribe")).code == "scribe_mode")
check("scribe mode never speaks, even on granted permission",
      gov.evaluate(gov.SpeechRequest(kind="permission_granted",
                                     mode="scribe")).code == "scribe_mode")
check("muted never speaks",
      gov.evaluate(gov.SpeechRequest(kind="invited", muted=True)).code == "muted")
check("paused listening never speaks",
      gov.evaluate(gov.SpeechRequest(kind="invited",
                                     listening_paused=True)).code == "listening_paused")
check("disconnected never speaks",
      gov.evaluate(gov.SpeechRequest(kind="invited", connected=False)).code == "not_connected")
check("speak-when-asked mode never volunteers",
      gov.evaluate(gov.SpeechRequest(**{**good, "mode": "on_request"})).code
      == "mode_no_unsolicited")

section("the speech governor: a human holds the floor")

for kind in ("invited", "queued_ask", "unsolicited", "permission_granted"):
    d = gov.evaluate(gov.SpeechRequest(kind=kind, human_speaking=True,
                                       ms_since_session_start=FOREVER))
    check(f"a {kind} request waits while a human is speaking",
          not d.allowed and d.code == "human_speaking", d.code)

d = gov.evaluate(gov.SpeechRequest(kind="invited", ms_since_human_speech_ended=100))
check("an invitation still waits out the grace period",
      not d.allowed and d.code == "grace", d.code)
check("and says how long to wait", (d.retry_after_ms or 0) > 0)

d = gov.evaluate(gov.SpeechRequest(kind="invited",
                                   ms_since_human_speech_ended=gov.INVITED_GRACE_MS + 1))
check("after the grace an invitation is answered", d.allowed and d.action == "speak", d.code)

d = gov.evaluate(gov.SpeechRequest(kind="queued_ask",
                                   ms_since_human_speech_ended=gov.QUEUED_ASK_GRACE_MS - 1))
check("a queued Ask AI waits for the floor to be genuinely free",
      not d.allowed and d.code == "grace")

section("the speech governor: restraint")

check("nothing is offered in the opening minutes",
      gov.evaluate(gov.SpeechRequest(**{**good, "ms_since_session_start": 30_000})).code
      == "warmup")
check("a cooldown after speaking",
      gov.evaluate(gov.SpeechRequest(**{**good, "ms_since_last_intervention": 60_000})).code
      == "cooldown")
check("a longer cooldown after a no",
      gov.evaluate(gov.SpeechRequest(**{**good, "ms_since_last_denial": 60_000})).code
      == "denied_cooldown")
check("never two requests at once",
      gov.evaluate(gov.SpeechRequest(**{**good, "permission_pending": True})).code
      == "permission_pending")
check("an unimportant observation is not worth interrupting for",
      gov.evaluate(gov.SpeechRequest(**{**good, "observation_importance": 0.4})).code
      == "below_threshold")
check("an observation the model did not want to raise is not raised",
      gov.evaluate(gov.SpeechRequest(**{**good,
                                        "observation_should_request_floor": False})).code
      == "nothing_to_say")
check("a stale observation is discarded, not spoken",
      gov.evaluate(gov.SpeechRequest(**{**good, "observation_revision": 3,
                                        "current_revision": 9})).code == "stale")
check("an already-surfaced observation is not surfaced again",
      gov.evaluate(gov.SpeechRequest(**{**good, "observation_status": "surfaced"})).code
      == "observation_not_open")
check("a dismissed observation stays dismissed",
      gov.evaluate(gov.SpeechRequest(**{**good, "observation_status": "dismissed"})).code
      == "observation_not_open")
check("a reflective pause is respected even with a good observation",
      gov.evaluate(gov.SpeechRequest(**{**good, "ms_since_human_speech_ended": 1_000})).code
      == "reflective_pause")

# The reason a person sees is a sentence, not a code.
d = gov.evaluate(gov.SpeechRequest(**{**good, "ms_since_human_speech_ended": 1_000}))
check("refusals are explained in plain language",
      d.reason.endswith(".") and " " in d.reason, d.reason)

section("presence: how quick she is (rule 87)")

_reserved = gov.resolve_policy("reserved")
_attentive = gov.resolve_policy("attentive")
_present = gov.resolve_policy("present")
check("reserved waits longest", _reserved["floor_open_ms"] > _attentive["floor_open_ms"])
check("present waits least", _present["floor_open_ms"] < _attentive["floor_open_ms"])
check("reserved holds back longest between offers",
      _reserved["unsolicited_cooldown_ms"] > _present["unsolicited_cooldown_ms"])
check("present has the lowest bar for asking",
      _present["min_importance"]["facilitator"] < _reserved["min_importance"]["facilitator"])
check("the bar can never leave 0..1",
      all(0.0 <= v <= 1.0 for level in core.PRESENCE_LEVELS
          for v in gov.resolve_policy(level)["min_importance"].values()))
check("an unknown presence falls back to the default rather than breaking",
      gov.resolve_policy("nonsense")["presence"] == core.DEFAULT_PRESENCE)
check("every preset reports which one it is",
      all(gov.resolve_policy(k)["presence"] == k for k in core.PRESENCE_LEVELS))

# The dial is real: the same moment is refused at one setting and allowed at another.
_moment = dict(kind="unsolicited", mode="facilitator", ms_since_human_speech_ended=2_000,
               ms_since_session_start=FOREVER, observation_should_request_floor=True,
               observation_importance=0.55, observation_revision=4, current_revision=4)
check("a 2s pause is still a reflective pause when reserved",
      gov.evaluate(gov.SpeechRequest(**{**_moment, "presence": "reserved"})).code
      in ("reflective_pause", "below_threshold"))
check("and the same moment lets her ask when present",
      gov.evaluate(gov.SpeechRequest(**{**_moment, "presence": "present"})).allowed)

# Answering a direct question is where the delay was actually felt.
check("a direct question is answered after a short beat, not a long one",
      gov.evaluate(gov.SpeechRequest(kind="invited",
                                     ms_since_human_speech_ended=600)).allowed)
check("the reserved setting still makes her wait longer for it",
      not gov.evaluate(gov.SpeechRequest(kind="invited", presence="reserved",
                                         ms_since_human_speech_ended=600)).allowed)

section("the floor state machine")

check("human speech wins from every state",
      all(gov.advance(s, "human_speech_started") == gov.HUMAN_SPEAKING
          for s in gov.FLOOR_STATES
          if s not in (gov.DISCONNECTED, gov.LISTENING_PAUSED)))
check("including mid-AI-sentence (barge-in)",
      gov.advance(gov.AI_SPEAKING, "human_speech_started") == gov.HUMAN_SPEAKING)
check("stopping speaking is a reflective pause, not a free floor",
      gov.advance(gov.HUMAN_SPEAKING, "human_speech_stopped") == gov.HUMAN_REFLECTIVE_PAUSE)
check("the floor only opens as a LABEL after the timer",
      gov.advance(gov.HUMAN_REFLECTIVE_PAUSE, "floor_open_elapsed") == gov.FLOOR_OPEN)
check("an ignored request returns to listening",
      gov.advance(gov.AI_PERMISSION_PENDING, "permission_expired") == gov.LISTENING_IDLE)
check("a cancelled response returns to listening, never resumes",
      gov.advance(gov.AI_SPEAKING, "ai_cancelled") == gov.LISTENING_IDLE)
check("a finished response returns to listening",
      gov.advance(gov.AI_SPEAKING, "ai_speech_done") == gov.LISTENING_IDLE)
check("an Ask AI during human speech queues",
      gov.advance(gov.HUMAN_SPEAKING, "ask_queued") == gov.AI_REQUEST_QUEUED)
check("a paused session ignores conversational events",
      gov.advance(gov.LISTENING_PAUSED, "human_speech_started") == gov.LISTENING_PAUSED)
check("every state has a human-readable label",
      all(s in gov.STATE_LABELS for s in gov.FLOOR_STATES))
check("the reflective-pause label promises no interruption",
      "not interrupt" in gov.STATE_LABELS[gov.HUMAN_REFLECTIVE_PAUSE])

section("direct address")

for said in ("AI, summarize where we are",
             "Assistant, what are we missing?",
             "Consultation assistant, what disagreements remain?",
             "Hey AI, can you help synthesize these ideas?",
             "So that is my worry. AI, what have we not looked at?"):
    check(f"heard as an invitation: {said[:34]}", gov.is_direct_address(said))

for said in ("I think AI is going to transform education",
             "The AI tools we tried last year did not help",
             "My assistant at work handles that",
             "We should ask an AI researcher",
             "Nobody wants AI making this decision for us"):
    check(f"NOT an invitation: {said[:34]}", not gov.is_direct_address(said))

check("a clear yes is a yes", gov.permission_answer("Yes, go ahead") is True)
check("a clear no is a no", gov.permission_answer("No, not yet") is False)
check("anything else is not an answer",
      gov.permission_answer("I was thinking about the transport问题 instead") is None)
check("an ambiguous reply never counts as consent",
      gov.permission_answer("Well, maybe we should think about it") is None)

# --- 3. The reasoner ---------------------------------------------------------

section("she is Abigail, and in a room she knows nothing (rule 88)")

_instructions = rt.session_config({"question": "When?", "mode": "facilitator"})["instructions"]
check("she is named in her own instructions", "Abigail" in _instructions)
check("and told she knows nothing of his private life here",
      "private life" in _instructions and "notes" in _instructions)
check("and that she cannot act on anything from here",
      "no email" in _instructions.lower() or "cannot do anything" in _instructions.lower())
check("the roster avatar is the same one the Secretary tab uses",
      core.ASSISTANT_AVATAR == "/abigail.jpg")

# The structural half: nothing in this subsystem can reach her private store.
_src = Path(__file__).parent.parent / "agents"
for _name in sorted(p.name for p in _src.glob("live_consultation*.py")):
    _text = (_src / _name).read_text(encoding="utf-8")
    check(f"{_name} never imports the Secretary's private store",
          "import secretary_store" not in _text
          and "from agents.secretary_store" not in _text
          and "secretary_store." not in _text.replace("`secretary_store.py`", ""))
    check(f"{_name} never reads her memory notes or tasks",
          "read_all_memory_notes" not in _text and "get_open_tasks" not in _text)

check("she is called by name where people can hear her",
      "Abigail" in core.session_instructions("x"))
check("her name is a wake word the client listens for",
      "abigail" in (Path(__file__).parent.parent / "dashboard" / "src" / "lib" /
                    "consultationGovernor.ts").read_text(encoding="utf-8").lower())

section("the consultation brain: when it runs")

ok, why = brain.should_analyze([], None)
check("no new turns, no paid call", not ok, why)
ok, why = brain.should_analyze([{"text": "a b c"}], None)
check("one short turn is not worth a call", not ok, why)
ok, why = brain.should_analyze([{"text": "word " * 60}], None)
check("a substantial turn is", ok, why)
ok, why = brain.should_analyze([{"text": "word " * 60}], 5)
check("but not twice in a few seconds", not ok, why)
ok, why = brain.should_analyze([{"text": "hi"}], 1, force=True)
check("unless a person asked for it", ok, why)

section("the consultation brain: bad output loses the pass, not the meeting")

SESSION = {"id": SID, "question": "When should we gather?", "framework": "bahai",
           "decision_method": "consensus", "context": ""}
BEFORE = {"summary": "Timing under discussion", "state_revision": 4,
          "ideas": [{"id": "idea_1", "text": "Saturday morning"}]}

r = brain.analyze(SESSION, BEFORE, [{"id": 1, "text": "hello"}], [],
                  call=lambda m: "I am afraid I cannot do that")
check("unreadable output is reported, not raised", not r.ok and bool(r.note))
check("and the map is left exactly as it was", r.state == BEFORE)

def _boom(messages):
    raise RuntimeError("connection reset")

r = brain.analyze(SESSION, BEFORE, [{"id": 1, "text": "hello"}], [], call=_boom)
check("a failed call is reported in plain language",
      not r.ok and "unchanged" in r.note.lower(), r.note)
check("and the map survives it", r.state == BEFORE)

truncated = ('{"summary": "Half a summary", "add": {"ideas": [{"text": "Optional RSVP"}, '
             '{"text": "Cut off mid')
r = brain.analyze(SESSION, BEFORE, [{"id": 1, "text": "x"}], [], call=lambda m: truncated)
check("a reply truncated at the token ceiling is repaired, not lost", r.ok, r.note)
check("the complete part of it survives",
      any(i["text"] == "Optional RSVP" for i in r.state["ideas"]))

r = brain.analyze(SESSION, BEFORE, [{"id": 1, "text": "x"}], [],
                  call=lambda m: '```json\n{"summary": "Fenced"}\n```')
check("a fenced code block is read", r.ok and r.state["summary"] == "Fenced")

section("the consultation brain: what the merge guarantees")

patch = {
    "summary": "Two concerns, one possible synthesis.",
    "add": {
        "facts": [{"text": "Twelve people came last time", "status": "confirmed"},
                  {"text": "Twelve people came last time", "status": "uncertain"}],
        "ideas": ["Optional RSVP"],
        "action_items": [{"action": "Book the hall", "owner": "", "due": ""},
                         {"action": "Ask about transport", "owner": "Tara", "due": "Friday"}],
        "decision_candidates": [{"text": "Saturday morning", "concerns": "not a list"}],
    },
    "observations": [{"kind": "possible_synthesis", "importance": 3.0,
                      "summary": "RSVP could hold both", "should_request_floor": True,
                      "permission_request": "Would a possible synthesis help?"},
                     {"kind": "note", "importance": 0.9, "summary": "wants to speak",
                      "should_request_floor": True, "permission_request": ""}],
}
merged, notes = brain.merge(BEFORE, patch)
check("duplicate facts are added once", len([f for f in merged["facts"]]) == 1)
check("a bare string is accepted where an object was asked for",
      merged["ideas"][-1]["text"] == "Optional RSVP")
check("items get stable readable ids", merged["facts"][0]["id"] == "fact_1")
check("an empty owner is NOT an assignment", merged["action_items"][0]["owner"] is None)
check("an empty due date is NOT a deadline", merged["action_items"][0]["due"] is None)
check("a stated owner is kept", merged["action_items"][1]["owner"] == "Tara")
check("a stated due date is kept", merged["action_items"][1]["due"] == "Friday")
check("a decision candidate is only ever a candidate",
      merged["decision_candidates"][0]["status"] == "candidate")
check("a malformed concerns field degrades to empty, not to a crash",
      merged["decision_candidates"][0]["concerns"] == [])

merged_conf, _ = brain.merge({"confirmed_decision": {"id": "dec_1", "text": "Saturday"}},
                             {"confirmed_decision": {"id": "x", "text": "Sunday"},
                              "add": {}})
check("a model can never write confirmed_decision (rule 81)",
      merged_conf["confirmed_decision"]["text"] == "Saturday")

merged_none, _ = brain.merge({}, {"confirmed_decision": {"text": "Sunday"}})
check("and cannot create one from nothing", merged_none["confirmed_decision"] is None)

# This block used to assert the OPPOSITE -- that a "resolve" from the model
# REMOVED the item. That was the bug (rule 97): a minority concern could vanish
# from the record because a model decided it had been dealt with, taking the
# route by which the group's understanding developed with it.
kept, notes = brain.merge({"tensions": [{"id": "tension_1", "text": "structure vs openness"}]},
                          {"resolve": ["tension_1"]})
check("a model asking to delete a tension is refused", len(kept["tensions"]) == 1)
check("and the refusal is reported rather than silent",
      any("never deleted" in n for n in notes), str(notes))

marked, notes_a = brain.merge(
    {"tensions": [{"id": "tension_1", "text": "structure vs openness", "lifecycle": "open"}]},
    {"addressed": [{"id": "tension_1", "note": "the group widened the invitation"}]})
check("the model may mark a tension as appearing addressed",
      marked["tensions"][0]["lifecycle"] == "addressed")
check("the item is still there, with the reason it was marked",
      marked["tensions"][0]["text"] == "structure vs openness"
      and "widened" in marked["tensions"][0]["resolution_note"])
check("and the merge says nothing was deleted",
      any("none deleted" in n for n in notes_a), str(notes_a))

for _final in ("resolved", "deferred", "accepted_risk", "superseded"):
    _out, _ = brain.merge(
        {"tensions": [{"id": "tension_1", "text": "t", "lifecycle": "open"}]},
        {"addressed": [{"id": "tension_1", "lifecycle": _final}]})
    check(f"a model cannot mark a concern {_final}",
          _out["tensions"][0]["lifecycle"] == "addressed")

_human = brain.merge(
    {"tensions": [{"id": "tension_1", "text": "t", "lifecycle": "resolved",
                   "human_reviewed": True}]},
    {"addressed": [{"id": "tension_1", "note": "model thinks otherwise"}]})[0]
check("a human resolution is not re-marked by a later model pass",
      _human["tensions"][0]["lifecycle"] == "resolved")

_edit_guard = brain.merge(
    {"ideas": [{"id": "idea_1", "text": "what Tara actually said", "human_edited": True}]},
    {"update": [{"id": "idea_1", "text": "the model's version"}]})[0]
check("a human's wording survives a model update (rule 95)",
      _edit_guard["ideas"][0]["text"] == "what Tara actually said")

_prov = brain.merge({}, {"add": {"facts": [
    {"text": "The hall is free", "status": "group_established", "source_turn_ids": ["7", "9"]}]}})[0]
check("a model cannot establish a fact, only report it (rule 96)",
      _prov["facts"][0]["status"] == "reported")
check("and the turns it relied on are carried",
      _prov["facts"][0]["source_turn_ids"] == ["7", "9"])

_acc = brain.merge({}, {"add": {"action_items": [
    {"action": "Book the hall", "owner": "Tara", "status": "accepted"}]}})[0]
check("a model cannot accept a commitment on somebody's behalf (rule 95)",
      _acc["action_items"][0]["status"] == "proposed"
      and _acc["action_items"][0]["owner_accepted"] is None)
check("but it may still record an owner somebody actually named",
      _acc["action_items"][0]["owner"] == "Tara")

obs = brain.parse_observations(patch, 4)
check("importance is clamped to 0..1", obs[0].importance == 1.0)
check("an observation that wants the floor with nothing to ask cannot ask",
      obs[1].should_request_floor is False)
check("observations carry the revision they were formed against",
      all(o.state_revision == 4 for o in obs))

validated, problem = brain.validate_state(merged)
check("the merged map validates against the domain models", problem is None, str(problem))

# A fact's status is now normalised rather than rejected, because the
# vocabulary changed under eight real meetings of stored data and a hard
# validation error would have made those sessions unreadable. The guarantee
# that matters is not "invalid input raises" but "a model can never claim the
# group established something" -- asserted directly, above and below.
_norm_state, problem = brain.validate_state({"facts": [{"text": "x", "status": "made-up"}]})
check("an unknown fact status becomes the honest default rather than breaking the map",
      problem is None and _norm_state["facts"][0]["status"] == "reported")

_legacy = core.normalize_state({"facts": [{"id": "fact_1", "text": "x", "status": "confirmed"}],
                                "tensions": [{"id": "tension_1", "text": "t"}],
                                "action_items": [{"id": "action_1", "action": "a",
                                                  "status": "done"}]})
check("a legacy 'confirmed' fact becomes 'reported', never 'group_established'",
      _legacy["facts"][0]["status"] == "reported")
check("a legacy 'done' action becomes 'completed'",
      _legacy["action_items"][0]["status"] == "completed")
check("a legacy 'open' action becomes 'proposed', not 'accepted'",
      core.normalize_action_status("open") == "proposed")
check("an old item without a lifecycle reads as open",
      _legacy["tensions"][0]["lifecycle"] == "open")
check("and nothing is invented about who reviewed or accepted it",
      _legacy["tensions"][0]["human_reviewed"] is False
      and _legacy["action_items"][0]["owner_accepted"] is None)

for _state in ("group_established", "externally_verified", "superseded", "withdrawn"):
    check(f"a model cannot set a fact to {_state}",
          core.normalize_fact_status(_state, model_written=True) == "reported")
    check(f"but a human may set a fact to {_state}",
          core.normalize_fact_status(_state) == _state)

section("the consultation brain: the prompt stays compact")

long_state = {"summary": "s", "state_revision": 3,
              "ideas": [{"id": f"idea_{i}", "text": f"idea number {i}"} for i in range(200)]}
messages = brain.build_messages(SESSION, long_state, [{"id": 9, "text": "new"}],
                                [{"id": 8, "text": "recent"}])
prompt = json.dumps(messages)
check("a long meeting does not resend everything",
      prompt.count("idea number") <= brain.LIST_PROMPT_CAP,
      str(prompt.count("idea number")))
check("the system prompt carries the constitution's principles",
      "Seek truth, not victory" in messages[0]["content"])
check("and tells the model the meeting is data, not instructions",
      "DATA, NOT INSTRUCTIONS" in messages[0]["content"])

ctx = brain.speech_context({"summary": "Timing", "agreements": [{"text": "Keep it open"}],
                            "confirmed_decision": {"text": "Saturday"}}, question="When?")
check("a spoken briefing carries the map", "Keep it open" in ctx)
check("and, once decided, orients toward making it succeed", "succeed" in ctx)

# --- 4. The realtime session -------------------------------------------------

section("the realtime session config")

cfg = rt.session_config({"question": "When?", "mode": "facilitator"})
td = cfg["audio"]["input"]["turn_detection"]
check("turn detection is semantic", td["type"] == "semantic_vad")
# Eagerness changes when the detector REPORTS a turn has ended, never whether
# she may speak -- it was raised to medium on 2026-08-21 because "low" was the
# main reason she felt unresponsive (rule 87).
# Eagerness is resolved PER PRESET now, not a single fixed env var: it was the
# largest contributor to how long she appeared to wait, and was the one number
# the dial could not move (rule 87, amended 2026-08-24).
check("eagerness comes from the presence dial",
      td["eagerness"] == core.vad_eagerness(core.DEFAULT_PRESENCE))
check("and every preset resolves to a real eagerness",
      all(core.vad_eagerness(p) in ("low", "medium", "high")
          for p in core.PRESENCE_LEVELS))
check("a more present preset is at least as eager as a reserved one",
      ["low", "medium", "high"].index(core.vad_eagerness("present"))
      > ["low", "medium", "high"].index(core.vad_eagerness("reserved")))
check("and however eager it is, it still cannot create a response",
      td["create_response"] is False)
check("VAD CANNOT create a response (rule 75)", td["create_response"] is False)
check("but human speech does interrupt the model", td["interrupt_response"] is True)
check("input transcription is on", bool(cfg["audio"]["input"]["transcription"]["model"]))
check("the model id comes from configuration", cfg["model"] == core.REALTIME_MODEL)
check("instructions say the meeting is data, not instructions",
      "DATA, NOT INSTRUCTIONS" in cfg["instructions"])
check("instructions forbid filling a pause",
      "take your time" in cfg["instructions"].lower())
check("instructions forbid reciting scripture from memory",
      "from memory" in cfg["instructions"] or "reconstructed" in cfg["instructions"])
check("scribe mode is told it will never get the floor",
      "scribe mode" in rt.session_config({"mode": "scribe"})["instructions"].lower())


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


CAPTURED: dict = {}


def _fake_post(url, headers=None, json=None, timeout=None):
    CAPTURED["url"] = url
    CAPTURED["headers"] = headers or {}
    CAPTURED["body"] = json or {}
    return _FakeResponse({"value": "ek_test_secret", "expires_at": 123,
                          "session": {"model": core.REALTIME_MODEL}})


_saved_key = os.environ.get("OPENAI_API_KEY", "")
os.environ["OPENAI_API_KEY"] = "sk-test-not-a-real-key"
cred = rt.create_client_secret({"id": SID, "question": "When?", "mode": "facilitator"},
                               post=_fake_post)
check("the credential endpoint is the current client_secrets one",
      CAPTURED["url"].endswith("/realtime/client_secrets"), CAPTURED["url"])
check("the master key is sent to OpenAI, from the server",
      CAPTURED["headers"].get("Authorization", "").startswith("Bearer sk-"))
check("a safety identifier is sent, and it is opaque",
      CAPTURED["headers"].get("OpenAI-Safety-Identifier", "").startswith("bw_"))
check("the safety identifier contains nothing personal",
      "@" not in CAPTURED["headers"].get("OpenAI-Safety-Identifier", ""))
check("the credential is short-lived",
      0 < CAPTURED["body"]["expires_after"]["seconds"] <= 7200)
check("the session config goes with it",
      CAPTURED["body"]["session"]["audio"]["input"]["turn_detection"]["create_response"] is False)
check("only the short-lived secret comes back", cred["client_secret"] == "ek_test_secret")
check("the master key is NEVER in the response",
      "sk-test-not-a-real-key" not in json.dumps(cred))
check("the SDP endpoint is served to the client",
      cred["calls_url"].endswith("/realtime/calls"))

os.environ["OPENAI_API_KEY"] = ""
try:
    rt.create_client_secret({"id": SID}, post=_fake_post)
    check("a missing key fails with something a person can act on", False)
except rt.RealtimeError as e:
    check("a missing key fails with something a person can act on",
          "OPENAI_API_KEY" in str(e), str(e))
os.environ["OPENAI_API_KEY"] = "sk-test-not-a-real-key"

section("the router's OpenAI path")

# Caught for real on 2026-08-21: the GPT-5.x family refuses any temperature but
# its default, so EVERY OpenAI call from this router came back 400 -- which
# would have made the consultation brain unusable, and had already made the
# Colony's OpenAI provider (rule 41a) unusable without anyone noticing.
import agents.router as router  # noqa: E402


class _OpenAIStub:
    """Refuses a temperature the way the real API does, then succeeds."""

    def __init__(self):
        self.payloads = []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.payloads.append(json or {})
        if "temperature" in (json or {}):
            return _StubResponse(400, {"error": {
                "message": "Unsupported value: 'temperature' does not support 0.2 with "
                           "this model. Only the default (1) value is supported.",
                "type": "invalid_request_error", "param": "temperature"}})
        return _StubResponse(200, {"choices": [{"message": {"content": '{"ok": true}'}}]})


class _StubResponse:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests as _rq
            raise _rq.HTTPError(f"{self.status_code} error", response=self)


_stub = _OpenAIStub()
_real_post = router.requests.post
router.requests.post = _stub
router.OPENAI_KEY = "sk-test-not-a-real-key"
reply = router.call_openai([{"role": "user", "content": "hi"}], model="gpt-5.6-sol",
                           temperature=0.2, json_mode=True)
router.requests.post = _real_post
check("a model that refuses a temperature is retried without one", reply == '{"ok": true}')
check("and the retry is what actually carried the request",
      len(_stub.payloads) == 2 and "temperature" not in _stub.payloads[1])
check("the model id asked for is the model id sent",
      _stub.payloads[0]["model"] == "gpt-5.6-sol")
check("json mode survives the retry", "response_format" in _stub.payloads[1])

section("a configured model that does not exist")


class _ModelResponse:
    def __init__(self, status):
        self.status_code = status


rt._MODEL_CACHE.clear()
ok, note = rt.check_model("gpt-imaginary", get=lambda *a, **k: _ModelResponse(404))
check("a model id the account does not have is reported as missing", ok is False)
check("and the message says what to change", "CONSULTATION_REASONING_MODEL" in note, note)
rt._MODEL_CACHE.clear()
ok, _ = rt.check_model("gpt-real", get=lambda *a, **k: _ModelResponse(200))
check("a model the account does have is fine", ok is True)
rt._MODEL_CACHE.clear()


def _offline(*a, **k):
    raise OSError("no network")


ok, note = rt.check_model("gpt-real", get=_offline)
check("an unreachable API is NOT evidence a model is gone (rule 41a's discipline)",
      ok is True and note == "")
rt._MODEL_CACHE.clear()
ok, _ = rt.check_model("gpt-real", get=lambda *a, **k: _ModelResponse(500))
check("nor is a server error", ok is True)
rt._MODEL_CACHE.clear()

section("realtime cost")

cost = rt.estimate_cost({"input_token_details": {"audio_tokens": 10_000, "text_tokens": 2_000},
                         "output_token_details": {"audio_tokens": 3_000, "text_tokens": 200}},
                        "gpt-realtime-2.1")
check("a usage block becomes an estimate", cost and cost > 0, str(cost))
check("audio dominates the estimate, as it does the bill",
      cost > rt.estimate_cost({"input_token_details": {"text_tokens": 10_000}}, "gpt-realtime-2.1"))
check("no usage detail means NO number is invented", rt.estimate_cost({}) is None)
check("an empty usage block is not billed as zero",
      rt.record_usage({}, "gpt-realtime-2.1")["recorded"] is False)

before = state.get_spend_summary().get("by_kind", {}).get(rt.SPEND_KIND, 0)
rt.record_usage({"input_token_details": {"audio_tokens": 1_000}}, "gpt-realtime-2.1")
after = state.get_spend_summary().get("by_kind", {}).get(rt.SPEND_KIND, 0)
check("realtime spend reaches the Steward's ledger", after > before, f"{before} -> {after}")

# --- 5. Verified writings ----------------------------------------------------

section("verified writings are never invented")

_real_retrieve = None
import agents.librarian as librarian  # noqa: E402

CORPUS = [{"text": "The heaven of divine wisdom is illumined with the two luminaries of "
                   "consultation and compassion.", "source": "Baha'u'llah",
           "section": "Tablets", "link": "https://example.invalid/x", "score": 0.9}]
librarian.retrieve = lambda query, n_results=3, **kw: list(CORPUS)

found = writ.search("consultation")
check("a verified passage comes back with its source",
      found["available"] and found["passages"][0]["source"] == "Baha'u'llah")
check("and is marked verified", found["passages"][0]["verified"] is True)

verdict = writ.verify_quotation(CORPUS[0]["text"])
check("the exact text verifies", verdict["verified"] is True)
verdict = writ.verify_quotation("The heaven of divine wisdom is illumined with the two "
                                "luminaries of consultation and kindness.")
check("a near miss FAILS -- paraphrase must never pass as scripture",
      verdict["verified"] is False, str(verdict))
verdict = writ.verify_quotation("Consultation bestoweth greater awareness and transmuteth "
                                "conjecture into certitude, said the Blessed Beauty.")
check("a plausible invention fails too", verdict["verified"] is False)

librarian.retrieve = lambda *a, **k: []
empty = writ.search("something not in the library")
check("nothing found is reported as nothing found",
      empty["available"] and empty["passages"] == [] and bool(empty["note"]))


def _explode(*a, **k):
    raise RuntimeError("chroma is not running")


librarian.retrieve = _explode
broken = writ.search("consultation")
check("an unreachable index is reported, never faked",
      broken["available"] is False and "No quotation" in broken["note"], broken["note"])

check("the assistant points at the verified text rather than reciting it",
      "on screen" in writ.spoken_line({"source": "Baha'u'llah"}))
check("and says plainly when there is nothing",
      "could not find" in writ.spoken_line(None))
librarian.retrieve = lambda query, n_results=3, **kw: list(CORPUS)

# --- 6. The endpoints --------------------------------------------------------

section("the endpoints")

import agents.api as api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(api.app, headers={"X-API-Key": auth.get_or_create_key()})

r = client.get("/live-consultation/capabilities")
caps = r.json()
check("capabilities answers", r.status_code == 200)
check("it names the realtime model", caps["realtime_model"] == core.REALTIME_MODEL)
check("it names the reasoning model", caps["reasoning_model"] == core.REASONING_MODEL)
# A recorder exists now (rule 91): the live API cannot tell voices apart, so
# named speakers have to be worked out from a finished file afterwards. It is
# reported from whether a key is actually configured -- a checkbox that reads
# as "you are being recorded" must be true or refused, never decorative.
check("recording is reported honestly, from whether a key exists",
      caps["recording_supported"] == bool(os.getenv("OPENAI_API_KEY", "").strip()))
check("and the diarising model is named separately from the live one",
      caps["diarize_model"] == core.DIARIZE_MODEL
      and core.DIARIZE_MODEL != core.TRANSCRIBE_MODEL)
check("it carries the floor policy for the client governor",
      caps["floor_policy"]["floor_open_ms"] == gov.resolve_policy()["floor_open_ms"])
check("and one resolved set of numbers per presence preset",
      set(caps["floor_policies"]) == set(core.PRESENCE_LEVELS))
check("the browser is never asked to do the scaling itself",
      caps["floor_policies"]["present"]["floor_open_ms"]
      < caps["floor_policies"]["reserved"]["floor_open_ms"])
check("capabilities names her", caps["assistant_name"] == core.ASSISTANT_NAME)
check("and gives the UI her face", caps["assistant_avatar"] == core.ASSISTANT_AVATAR)
check("it leaks no key", "sk-" not in json.dumps(caps))
check("it lists the participation modes", {m["id"] for m in caps["modes"]} == set(core.MODES))

r = client.post("/live-consultation/sessions",
                json={"title": "Neighbourhood gathering", "question": "When should we gather?",
                      "mode": "facilitator", "decision_method": "consensus"})
check("a session can be created", r.status_code == 200, r.text[:120])
NEW = r.json()["id"]

r = client.post("/live-consultation/sessions",
                json={"title": "x", "record_audio": True})
if audio.available():
    check("recording can be switched on when a key exists",
          r.status_code == 200 and r.json()["record_audio"] == 1, r.text[:160])
else:
    # The one thing that must never happen is accepting the flag and doing
    # nothing: a checkbox that reads on screen as "you are being recorded" has
    # to be true or refused, never decorative (rule 91).
    check("without a key, recording is refused rather than silently ignored",
          r.status_code == 400, r.text[:160])

r = client.post("/live-consultation/sessions", json={"title": "x", "mode": "bossy"})
check("an unknown mode is refused", r.status_code == 400)

r = client.post("/live-consultation/sessions", json={"title": "x", "presence": "instant"})
check("an unknown presence is refused", r.status_code == 400, r.text[:120])

r = client.patch(f"/live-consultation/sessions/{NEW}", json={"presence": "present"})
check("presence can be changed mid-meeting", r.json()["session"]["presence"] == "present",
      r.text[:120])
r = client.patch(f"/live-consultation/sessions/{NEW}", json={"presence": "nope"})
check("but not to something that does not exist", r.status_code == 400)
client.patch(f"/live-consultation/sessions/{NEW}", json={"presence": "attentive"})

# Rule 94. The gate is at the SERVER, not only on the setup screen's disabled
# button: a page can be reloaded, and what deserves guarding is the moment the
# microphone starts, not the moment a checkbox is drawn.
r = client.post(f"/live-consultation/sessions/{NEW}/start")
check("a session will not start until the room has been told",
      r.status_code == 400, r.text[:160])
check("and the refusal says what to do about it",
      "listening" in r.text.lower() and "told" in r.text.lower(), r.text[:200])
check("nothing was started", client.get(f"/live-consultation/sessions/{NEW}")
      .json()["session"]["status"] == "draft")

r = client.post(f"/live-consultation/sessions/{NEW}/inform")
check("the host can attest that the room was told", r.status_code == 200, r.text[:160])
check("and the attestation is stamped, so it is a record rather than a flag",
      bool(r.json()["session"]["participants_informed_at"]))

r = client.post(f"/live-consultation/sessions/{NEW}/start")
check("a session starts", r.json()["session"]["status"] == "live")

r = client.post(f"/live-consultation/sessions/{NEW}/turns",
                json={"text": "I would rather we did not need to register",
                      "realtime_item_id": "rt_1", "is_final": True})
check("a turn is recorded", r.status_code == 200 and r.json()["turn"]["sequence"] == 1)
r = client.post(f"/live-consultation/sessions/{NEW}/turns",
                json={"text": "I would rather we did not need to register",
                      "realtime_item_id": "rt_1", "is_final": True})
check("a retried turn does not duplicate",
      len(client.get(f"/live-consultation/sessions/{NEW}").json()["turns"]) == 1)

detail = client.get(f"/live-consultation/sessions/{NEW}").json()
check("the detail view carries every part of the record",
      all(k in detail for k in ("session", "state", "turns", "observations", "decisions",
                                "action_items", "writings", "speech_events")))

# Analysis, with the model stubbed. No paid call is ever made by this suite.
REPLY = json.dumps({
    "question": "When should we gather?",
    "summary": "The group wants the gathering open; planning needs numbers.",
    "add": {
        "facts": [{"text": "Twelve people came last time", "status": "confirmed"}],
        "assumptions": [{"text": "Attendance is the main problem"}],
        "needs_and_concerns": [{"text": "Nobody should be turned away"}],
        "ideas": [{"text": "Optional RSVP"}],
        "possible_syntheses": [{"text": "Optional RSVP with walk-ins welcome"}],
        "decision_candidates": [{"text": "Hold it Saturday morning"}],
        "action_items": [{"action": "Ask the hall about Saturdays"}],
    },
    "observations": [{"kind": "possible_synthesis", "importance": 0.9,
                      "summary": "Optional RSVP may satisfy both concerns",
                      "detail": "It keeps the door open and still gives numbers.",
                      "should_request_floor": True,
                      "permission_request": "I think I see a possible synthesis. "
                                            "Would it be useful to hear it?",
                      "speech_brief": "Optional RSVP preserves walk-ins and improves planning."}],
    "writings_theme": "consultation",
})

import agents.live_consultation_api as lc_api  # noqa: E402

_real_analyze = brain.analyze


def _stub_analyze(session, state_, new_turns, recent, final_pass=False, model=None, call=None):
    return _real_analyze(session, state_, new_turns, recent, final_pass=final_pass,
                         call=lambda messages: REPLY)


brain.analyze = _stub_analyze
lc_api.reasoner.analyze = _stub_analyze

r = client.post(f"/live-consultation/sessions/{NEW}/analyze", json={"force": True})
result = r.json()
check("an analysis pass runs", result.get("ok") is True, r.text[:160])
check("the map is updated", result["state"]["summary"].startswith("The group wants"))
check("an observation is recorded", len(result["observations"]) == 1)
check("a decision CANDIDATE is recorded", len(client.get(
    f"/live-consultation/sessions/{NEW}").json()["decisions"]) == 1)
check("no decision is confirmed by an analysis pass",
      client.get(f"/live-consultation/sessions/{NEW}").json()["confirmed_decision"] is None)
check("verified writings found for the theme are attached",
      len(client.get(f"/live-consultation/sessions/{NEW}").json()["writings"]) == 1)

r = client.post(f"/live-consultation/sessions/{NEW}/analyze", json={"force": False})
check("a second pass with nothing new does not spend anything",
      r.json()["ran"] is False, r.text[:120])

OBS = result["observations"][0]["id"]

# The floor, over HTTP. The session started seconds ago, so the warmup alone
# refuses -- which is itself the point.
r = client.post(f"/live-consultation/sessions/{NEW}/speech-permission",
                json={"kind": "unsolicited", "observation_id": OBS,
                      "ms_since_human_speech_ended": 30_000})
check("a brand-new session refuses an unsolicited offer",
      r.json()["allowed"] is False and r.json()["code"] == "warmup", r.text[:120])

# Age the session past the warmup, honestly, by moving its start time.
store.update_session(NEW, started_at="2020-01-01 00:00:00")

r = client.post(f"/live-consultation/sessions/{NEW}/speech-permission",
                json={"kind": "unsolicited", "observation_id": OBS,
                      "ms_since_human_speech_ended": 500})
check("a reflective pause still refuses",
      r.json()["allowed"] is False and r.json()["code"] == "reflective_pause", r.text[:120])

# The session's OWN presence is what the server gates on -- not the client's word.
store.update_session(NEW, presence="reserved")
r = client.post(f"/live-consultation/sessions/{NEW}/speech-permission",
                json={"kind": "unsolicited", "observation_id": OBS,
                      "ms_since_human_speech_ended": 3_500})
check("a reserved session holds back where an attentive one would ask",
      r.json()["allowed"] is False and r.json()["code"] == "reflective_pause", r.text[:120])
store.update_session(NEW, presence="attentive")

r = client.post(f"/live-consultation/sessions/{NEW}/speech-permission",
                json={"kind": "unsolicited", "observation_id": OBS,
                      "ms_since_human_speech_ended": 30_000})
payload = r.json()
check("with the floor free it may ASK", payload["allowed"] and
      payload["action"] == "request_permission", r.text[:160])
check("it is given only the question to say",
      payload["say"].startswith("I think I see a possible synthesis"))
check("the substance is NOT smuggled into the question",
      "walk-ins" not in payload["say"] and "preserves" not in payload["say"])

r = client.post(f"/live-consultation/sessions/{NEW}/speech-permission",
                json={"kind": "unsolicited", "observation_id": OBS,
                      "ms_since_human_speech_ended": 30_000})
check("while a request stands, no second request is made",
      r.json()["allowed"] is False and
      r.json()["code"] in ("permission_pending", "observation_not_open"), r.text[:120])

r = client.post(f"/live-consultation/sessions/{NEW}/observations/{OBS}/answer",
                json={"granted": False, "ignored": True})
check("an ignored request expires", r.json()["granted"] is False)
check("and the observation is not left open to ask again",
      r.json()["observation"]["status"] == "expired")

obs2 = store.add_observation(NEW, "unaddressed_assumption", "Attendance may not be the problem",
                             importance=0.9, should_request_floor=True,
                             permission_request="May I name one assumption?",
                             speech_brief="Three proposals assume attendance is the problem.",
                             state_revision=store.get_state(NEW)["state_revision"])
r = client.post(f"/live-consultation/sessions/{NEW}/speech-permission",
                json={"kind": "unsolicited", "observation_id": obs2["id"],
                      "ms_since_human_speech_ended": 30_000})
check("after an ignored request the assistant waits a long while",
      r.json()["allowed"] is False and r.json()["code"] == "denied_cooldown", r.text[:120])

r = client.post(f"/live-consultation/sessions/{NEW}/observations/{obs2['id']}/answer",
                json={"granted": True})
check("a yes hands over the substance", r.json()["granted"] is True)
check("and only then", "assume attendance" in r.json()["instructions"])

r = client.post(f"/live-consultation/sessions/{NEW}/ask",
                json={"text": "What are we missing?", "human_speaking": True})
check("Ask AI while someone is speaking waits rather than interrupting",
      r.json()["allowed"] is False and r.json()["code"] == "human_speaking")

r = client.post(f"/live-consultation/sessions/{NEW}/ask",
                json={"text": "What are we missing?",
                      "ms_since_human_speech_ended": 30_000})
check("Ask AI on a free floor is answered", r.json()["allowed"] is True, r.text[:160])
check("the answer's instructions carry the map",
      "Optional RSVP" in r.json()["instructions"])
check("and are code-owned, telling it to be brief",
      "briefly" in r.json()["instructions"])

store.update_session(NEW, mode="scribe")
r = client.post(f"/live-consultation/sessions/{NEW}/ask",
                json={"text": "Summarise", "ms_since_human_speech_ended": 30_000})
check("in scribe mode even a direct question gets no voice",
      r.json()["allowed"] is False and r.json()["code"] == "scribe_mode")
store.update_session(NEW, mode="facilitator")

DEC = client.get(f"/live-consultation/sessions/{NEW}").json()["decisions"][0]["id"]
r = client.post(f"/live-consultation/sessions/{NEW}/decisions/{DEC}/confirm")
check("a human can confirm a decision", r.json()["decision"]["status"] == "confirmed")
check("and only then does the map hold one",
      r.json()["state"]["confirmed_decision"]["text"] == "Hold it Saturday morning")
r = client.post(f"/live-consultation/sessions/{NEW}/decisions/{DEC}/reject")
check("and can take it back", r.json()["decision"]["status"] == "rejected")
check("which clears the confirmed decision",
      client.get(f"/live-consultation/sessions/{NEW}").json()["state"]
      .get("confirmed_decision") is None)

r = client.get(f"/live-consultation/sessions/{NEW}/export")
md = r.text
check("the record exports as markdown", r.status_code == 200 and md.startswith("# "))
check("an unconfirmed meeting says so plainly",
      "No final decision was confirmed." in md, md[:80])
check("an unowned action says so rather than inventing an owner",
      "Owner not assigned" in md)
check("the transcript is in the export", "did not need to register" in md)

r = client.post("/live-consultation/realtime/client-secret", json={"session_id": NEW})
check("the client-secret endpoint answers or explains itself",
      r.status_code in (200, 402, 503), str(r.status_code))

r = client.post(f"/live-consultation/sessions/{NEW}/usage",
                json={"usage": {"input_token_details": {"audio_tokens": 500}},
                      "model": core.REALTIME_MODEL})
check("realtime usage is metered through the endpoint", r.json()["recorded"] is True)

r = client.post(f"/live-consultation/sessions/{NEW}/end", json={})
check("a session ends", r.json()["session"]["status"] == "ended")

# A meeting nobody spoke in must not buy a closing summary of nothing.
_silent = client.post("/live-consultation/sessions", json={"title": "Nobody spoke"}).json()
client.post(f"/live-consultation/sessions/{_silent['id']}/start")
r = client.post(f"/live-consultation/sessions/{_silent['id']}/end", json={})
check("an empty meeting is not analysed at all", r.json()["note"] == "" or
      "nothing to summarise" in r.json()["note"], r.json().get("note", ""))
client.delete(f"/live-consultation/sessions/{_silent['id']}")

r = client.get("/live-consultation/sessions")
check("the archive lists it", any(s["id"] == NEW for s in r.json()["sessions"]))
check("the archive says whether a decision was confirmed",
      "decision_confirmed" in r.json()["sessions"][0])

section("deleting a session really deletes it")

turns_before = len(store.list_turns(NEW))
r = client.delete(f"/live-consultation/sessions/{NEW}")
check("delete reports what it removed", r.json()["deleted"] is True and
      r.json()["turns"] == turns_before)
check("the session is gone", store.get_session(NEW) is None)
check("its turns are gone", store.list_turns(NEW) == [])
check("its observations are gone", store.list_observations(NEW) == [])
check("its decisions are gone", store.list_decisions(NEW) == [])
check("its action items are gone", store.list_action_items(NEW) == [])
check("its writings are gone", store.list_writings(NEW) == [])
check("its state is gone", store.get_state(NEW).get("summary") is None)
check("its floor record is gone", store.list_speech_events(NEW) == [])
r = client.get(f"/live-consultation/sessions/{NEW}")
check("and it 404s afterwards", r.status_code == 404)

# --- 7. Isolation ------------------------------------------------------------

section("isolation")

check("no non-public route escapes the owner gate",
      all(not auth.is_public(r.path) for r in api.app.routes
          if getattr(r, "path", "").startswith("/live-consultation")))

unauth = TestClient(api.app)
r = unauth.get("/live-consultation/capabilities")
check("an unauthenticated call is refused (rule 70)", r.status_code == 401, str(r.status_code))
r = unauth.post("/live-consultation/sessions", json={"title": "x"})
check("including creating a session", r.status_code == 401)

# The strong version of rule 73: read workforce.db as BYTES and require that
# nothing anyone said in a meeting is in it.
SECRET_SENTENCE = "the transport arrangements for Nasrin's mother"
s2 = store.create_session("Private matters", question=SECRET_SENTENCE)
store.upsert_turn(s2["id"], SECRET_SENTENCE, realtime_item_id="rt_secret", is_final=True)
store.save_state(s2["id"], {"summary": SECRET_SENTENCE})
store.add_observation(s2["id"], "note", SECRET_SENTENCE)
rt.record_usage({"input_token_details": {"audio_tokens": 100}}, core.REALTIME_MODEL)
blob = Path(state.DB_PATH).read_bytes()
check("nothing said in a meeting reaches workforce.db",
      SECRET_SENTENCE.encode("utf-8") not in blob)
check("nor does the session title", b"Private matters" not in blob)
private_blob = Path(store.DB_PATH).read_bytes()
check("it is in the private consultation database, where it belongs",
      SECRET_SENTENCE.encode("utf-8") in private_blob)

src = Path(__file__).parent.parent / "agents"
live_files = sorted(p.name for p in src.glob("live_consultation*.py"))
check("the subsystem is its own set of modules", len(live_files) == 9, str(live_files))
for name in live_files:
    text = (src / name).read_text(encoding="utf-8")
    check(f"{name} does not import the product consultation pipeline",
          "from agents.consultation import" not in text and
          "import agents.consultation\n" not in text)

product_consultation = (src / "consultation.py").read_text(encoding="utf-8")
check("the product consultation pipeline knows nothing about this feature",
      "live_consultation" not in product_consultation)

api_text = (src / "api.py").read_text(encoding="utf-8")
api_touch = [ln for ln in api_text.splitlines() if "live_consultation" in ln]
check("api.py's change is small: a store init and a router include",
      len(api_touch) <= 5, str(len(api_touch)))

# No tool surface at all: an injected instruction in a transcript has nothing
# to reach (rule 72's reasoning, applied to a subsystem that simply has no
# tools rather than gating them).
for name in live_files:
    text = (src / name).read_text(encoding="utf-8")
    check(f"{name} exposes no tools to any model",
          '"tools"' not in text and "make_executor" not in text)

# --- the opening, the clock, the recording and the report (rules 89-93) ------
#
# NOTE: the uncommitted tests that covered these were destroyed by a bad file
# write on 2026-09-03 and could not be recovered. This section was rebuilt from
# the source and is not the original.

section("the opening she reads (rule 92)")

_open_bahai = core.opening_instructions(framework="bahai", question="When shall we gather?")
check("the Bahai-framework opening carries the passage verbatim",
      core.CONSULTATION_PASSAGE in _open_bahai)
check("and forbids altering a word of it",
      "word for word" in _open_bahai and "nothing added" in _open_bahai)
check("and names the source", core.CONSULTATION_PASSAGE_SOURCE in _open_bahai)
check("the passage is code-owned, never produced by a model",
      "CONSULTATION_PASSAGE" in (Path(__file__).parent.parent / "agents"
                                 / "live_consultation.py").read_text(encoding="utf-8"))

_open_general = core.opening_instructions(framework="general")
check("a general opening quotes no scripture at all",
      core.CONSULTATION_PASSAGE not in _open_general)
check("and is told not to", "do not quote scripture" in _open_general.lower())
check("and not to name a religion, because the room may not share one",
      "do not mention any religion" in _open_general.lower())
check("but it still commends the same qualities",
      core.CONSULTATION_QUALITIES in _open_general)

_open_named = core.opening_instructions(participants=["Tara", "Nasrin"])
check("she may greet a room she was given names for", "Tara" in _open_named)
check("but never goes round them one by one",
      "one by one" in _open_named)


section("scheduled speech is invited, never reached by silence (rules 75, 92)")

for _kind in ("opening", "time_warning"):
    check(f"{_kind} is a scheduled kind", _kind in gov.SCHEDULED_KINDS)
    check(f"{_kind} is refused in scribe mode",
          not gov.evaluate(gov.SpeechRequest(kind=_kind, mode="scribe")).allowed)
    check(f"{_kind} is refused when muted",
          not gov.evaluate(gov.SpeechRequest(kind=_kind, muted=True)).allowed)
    check(f"{_kind} is refused when listening is paused",
          not gov.evaluate(gov.SpeechRequest(kind=_kind, listening_paused=True)).allowed)
    check(f"{_kind} waits while a human is speaking",
          not gov.evaluate(gov.SpeechRequest(kind=_kind, human_speaking=True)).allowed)
    # The rule the whole feature stands on: neither is ever REACHED by silence.
    for _preset in core.PRESENCE_LEVELS:
        _d = gov.evaluate(gov.SpeechRequest(
            kind="unsolicited", mode="facilitator", presence=_preset,
            ms_since_human_speech_ended=600_000, ms_since_session_start=600_000,
            observation_should_request_floor=False))
        check(f"ten minutes of silence permits nothing at {_preset}", not _d.allowed)

_allowed = gov.evaluate(gov.SpeechRequest(kind="opening", ms_since_human_speech_ended=99_999))
check("but the opening is allowed when the floor is genuinely free", _allowed.allowed)


section("the opening fires once, from the record (rule 92)")

_O = client.post("/live-consultation/sessions",
                 json={"title": "Opening", "participants_informed": True}).json()["id"]
client.post(f"/live-consultation/sessions/{_O}/start")
r1 = client.post(f"/live-consultation/sessions/{_O}/opening",
                 json={"ms_since_human_speech_ended": 99999})
check("she opens the meeting", r1.json().get("allowed") is True, r1.text[:160])
check("and is given the passage to put on screen",
      core.CONSULTATION_PASSAGE[:40] in (r1.json().get("passage") or ""))
r2 = client.post(f"/live-consultation/sessions/{_O}/opening",
                 json={"ms_since_human_speech_ended": 99999})
check("a page reload cannot make her open the meeting twice",
      r2.json().get("allowed") is False and r2.json().get("code") == "already_opened")


section("the clock, and what she puts to the group (rule 92)")

_W = client.post("/live-consultation/sessions",
                 json={"title": "Timed", "participants_informed": True,
                       "duration_minutes": 30, "warn_minutes": 5}).json()["id"]
client.post(f"/live-consultation/sessions/{_W}/start")
store.save_state(_W, {"tensions": [{"id": "tension_1", "text": "cost against reach",
                                    "lifecycle": "open"}],
                      "unresolved_questions": [{"id": "question_1", "text": "who will host",
                                                "lifecycle": "open"}]})
_threads = lcapi._open_threads(_W)
check("the wrap-up reads what is still open from the map",
      "cost against reach" in _threads["unresolved"])
check("and the open questions", "who will host" in _threads["questions"])

# Rule 97 gave items a lifecycle; a settled concern must drop out of the
# wrap-up, or she reads out something dealt with an hour ago.
store.save_state(_W, {"tensions": [{"id": "tension_1", "text": "cost against reach",
                                    "lifecycle": "resolved"}],
                      "unresolved_questions": [{"id": "question_1", "text": "who will host",
                                                "lifecycle": "open"}]})
check("a resolved concern is no longer outstanding",
      "cost against reach" not in lcapi._open_threads(_W)["unresolved"])
check("but an open question still is",
      "who will host" in lcapi._open_threads(_W)["questions"])

_instr = lcapi._time_warning_instructions(store.get_session(_W), minutes_left=5, final=False)
check("the time check says how long is left", "5 minutes" in _instr)
check("she may put AT MOST TWO things to the group", "AT MOST TWO" in _instr)
check("and asks rather than concludes",
      "as questions" in _instr and "do not answer them yourself" in _instr)

_empty = client.post("/live-consultation/sessions",
                     json={"title": "Nothing open", "participants_informed": True}).json()["id"]
_instr2 = lcapi._time_warning_instructions(store.get_session(_empty), minutes_left=2, final=False)
check("with nothing outstanding she says only the time",
      "Nothing in the record is outstanding" in _instr2)
check("and is told not to invent a loose end", "do not invent" in _instr2.lower())


section("participants, and who said what (rules 80, 91)")

_P = client.post("/live-consultation/sessions",
                 json={"title": "Voices", "participants_informed": True,
                       "participants": ["Tara", "Nasrin"]}).json()["id"]
_people = client.get(f"/live-consultation/sessions/{_P}/participants").json()["participants"]
check("the people in the room can be named beforehand", len(_people) == 2)
check("and nothing is inferred about which voice is whose",
      all(p["speaker_key"] is None for p in _people))

client.post(f"/live-consultation/sessions/{_P}/turns",
            json={"text": "something said aloud", "realtime_item_id": "v_1", "is_final": True})
_turn = client.get(f"/live-consultation/sessions/{_P}").json()["turns"][0]
check("a live turn is never attributed to anyone (rule 80)",
      _turn["speaker_label"] is None)

r = client.post(f"/live-consultation/sessions/{_P}/participants/{_people[0]['id']}/speaker",
                json={"speaker_key": "A"})
check("a human may map a name onto a diarised voice", r.status_code == 200, r.text[:160])
r = client.post(f"/live-consultation/sessions/{_P}/participants/{_people[1]['id']}/speaker",
                json={"speaker_key": "A"})
_after = client.get(f"/live-consultation/sessions/{_P}/participants").json()["participants"]
check("one voice belongs to one person: the key moves rather than doubling",
      sum(1 for p in _after if p["speaker_key"] == "A") == 1)

# Rule 91's hard line: no voice reference clips, ever. That is biometric
# enrolment of the owner's friends and the spec ruled it out.
_audio_src = (Path(__file__).parent.parent / "agents" / "live_consultation_audio.py"
              ).read_text(encoding="utf-8")
check("the audio module never sends a voice reference sample",
      "known_speaker" not in _audio_src and "speaker_reference" not in _audio_src
      and "voice_sample" not in _audio_src)
check("the diarised transcript is stored beside the live one, never over it",
      "source = 'diarized'" in (Path(__file__).parent.parent / "agents"
                                / "live_consultation_store.py").read_text(encoding="utf-8"))
check("and list_turns still defaults to the record the room watched being written",
      len(store.list_turns(_P)) == len(store.list_turns(_P, source="live")))


section("dictation keeps nothing (rule 91b)")

check("dictation has a model id of its own, because it is a third job",
      core.DICTATE_MODEL and core.DICTATE_MODEL != core.TRANSCRIBE_MODEL
      and core.DICTATE_MODEL != core.DIARIZE_MODEL)
check("it holds the bytes in memory and never writes a file",
      "def transcribe_plain" in _audio_src
      and "save_recording" not in _audio_src.split("def transcribe_plain")[1])
check("a recording, by contrast, does go to the private folder",
      "AUDIO_DIR" in _audio_src)
_dict_body = _audio_src.split("def transcribe_plain")[1]
check("an empty press is refused before anything is sent",
      "Hold the button" in _dict_body)


section("the report: model prose, verbatim record (rule 90)")

_RP = client.post("/live-consultation/sessions",
                  json={"title": "Reported", "question": "When?",
                        "participants_informed": True}).json()["id"]
store.save_state(_RP, {"summary": "the group weighed two dates",
                       "agreements": [{"id": "agreement_1", "text": "everyone wants it soon",
                                       "lifecycle": "open"}]})
_dec = store.upsert_decision_candidate(_RP, "Meet on Saturday", map_id="decision_1")
store.set_decision_status(_dec["id"], "confirmed")
store.upsert_action_item(_RP, "Book the hall", map_id="action_1")

# A model that returns a decision and an action list -- which they do -- must
# not have either printed. This is where rules 81 and 83 would be quietly undone.
def _greedy_call(_messages):
    return json.dumps({
        "in_short": "A short summary of the meeting.",
        "discussion": "How the group got there.",
        "still_open": "What is still open.",
        "decision": "THE GROUP DECIDED SUNDAY",
        "action_items": [{"action": "SOMETHING INVENTED", "owner": "SOMEBODY"}],
    })

_built = report.build_report(
    session=store.get_session(_RP), state=store.get_state(_RP),
    decisions=store.list_decisions(_RP), actions=store.list_action_items(_RP),
    writings=[], turns=store.list_turns(_RP), participants=[], call=_greedy_call)
_md = _built["markdown"]
check("the model's prose is used", "A short summary of the meeting." in _md)
check("but a decision it invented is never printed",
      "THE GROUP DECIDED SUNDAY" not in _md)
check("nor an action it invented", "SOMETHING INVENTED" not in _md)
check("nor an owner it invented", "SOMEBODY" not in _md)
check("the real confirmed decision is printed verbatim", "Meet on Saturday" in _md)
check("and the real action, with its owner honestly blank",
      "Book the hall" in _md and "Owner not assigned" in _md)
store.upsert_action_item(_RP, "Ring the caretaker", owner="Tara", map_id="action_2")
_owned = report.build_report(
    session=store.get_session(_RP), state=store.get_state(_RP),
    decisions=store.list_decisions(_RP), actions=store.list_action_items(_RP),
    writings=[], turns=store.list_turns(_RP), participants=[], call=_greedy_call)["markdown"]
check("a named owner who has not accepted is not implied to have agreed",
      "Tara" in _owned and "not yet accepted" in _owned)

def _dead_call(_messages):
    raise RuntimeError("the model is unreachable")

_fallback = report.build_report(
    session=store.get_session(_RP), state=store.get_state(_RP),
    decisions=store.list_decisions(_RP), actions=store.list_action_items(_RP),
    writings=[], turns=store.list_turns(_RP), participants=[], call=_dead_call)
check("a dead model costs the prose, never the record",
      "Meet on Saturday" in _fallback["markdown"] and "Book the hall" in _fallback["markdown"])
check("and the report says the summary could not be written",
      bool(_fallback["note"]) and _fallback["narrated"] is False)


section("the glance panel reads the same source as her voice (rule 93)")

_G = client.post("/live-consultation/sessions",
                 json={"title": "Glance", "participants_informed": True}).json()["id"]
store.save_state(_G, {
    "themes": [{"id": "theme_1", "text": "the date", "lifecycle": "open"}],
    "agreements": [{"id": "agreement_1", "text": "soon", "lifecycle": "open"}],
    "tensions": [{"id": "tension_1", "text": "cost", "lifecycle": "open"}],
    "unresolved_questions": [{"id": "question_1", "text": "who hosts", "lifecycle": "open"}]})
_detail = client.get(f"/live-consultation/sessions/{_G}").json()
check("the detail view carries what is still open, for the glance panel",
      "open_threads" in _detail)
check("and it is the SAME function the spoken time check reads",
      _detail["open_threads"] == lcapi._open_threads(_G))
check("themes are carried as short labels, not more sentences",
      [t["text"] for t in _detail["state"]["themes"]] == ["the date"])
# Rule 61's instinct applied to a meeting: counts, never a completeness score.
_glance_src = (Path(__file__).parent.parent / "dashboard" / "src" / "components"
               / "consultation" / "ConsultationGlance.tsx").read_text(encoding="utf-8")
check("the panel's only percentage is a CSS width for the proportional bar",
      _glance_src.count("%") == 1 and 'const pct =' in _glance_src)
check("no percentage is ever rendered as text a group could chase",
      ">{pct(" not in _glance_src and "{pct(" not in _glance_src.replace("width: pct(", ""))
# Checked against the CODE, not the comments -- the file's own docstring
# explains why there is no completeness score, and matching that would be
# testing the explanation rather than the thing.
_glance_code = re.sub(r"/\*.*?\*/", "", _glance_src, flags=re.S)
_glance_code = re.sub(r"^\s*//.*$", "", _glance_code, flags=re.M)
check("and it reports counts, not a score of how complete the consultation is",
      "complete" not in _glance_code.lower() and "progress" not in _glance_code.lower())


# --- human authority, commitments, closeout, retention (rules 94-98) ---------

section("the persistence defects that lost every owner and deadline (rule 95)")

_S = client.post("/live-consultation/sessions",
                 json={"title": "Persistence", "question": "When?",
                       "participants_informed": True}).json()["id"]

# THE defect, at the store. Measured on the owner's real database before this
# fix: 169 action items carried 2 owners and 0 due dates, because an action
# heard again WITH an owner matched the old row by text and was returned
# untouched. One meeting alone had 102 near-identical actions.
_a1 = store.upsert_action_item(_S, "Book the hall", map_id="action_1")
_a2 = store.upsert_action_item(_S, "Book the hall", owner="Tara", due="2026-09-12",
                               map_id="action_1")
check("an action that later learns an owner keeps its row", _a1["id"] == _a2["id"])
check("and actually records the owner", _a2["owner"] == "Tara")
check("and the deadline", _a2["due"] == "2026-09-12")
check("only one action exists, not two", len(store.list_action_items(_S)) == 1)

_a3 = store.upsert_action_item(_S, "Book the hall for the youth gathering",
                               map_id="action_1")
check("a reworded action updates the same row rather than duplicating",
      len(store.list_action_items(_S)) == 1 and "youth" in _a3["action"])
check("and rewording never clears an owner already recorded", _a3["owner"] == "Tara")

_a4 = store.upsert_action_item(_S, "Book the hall for the youth gathering",
                               owner=None, due=None, map_id="action_1")
check("a later pass with no owner does not erase the one recorded",
      _a4["owner"] == "Tara" and _a4["due"] == "2026-09-12")

_d1 = store.upsert_decision_candidate(_S, "Meet on Saturday", map_id="decision_1")
_d2 = store.upsert_decision_candidate(_S, "Meet on Saturday",
                                      rationale="More families are free",
                                      support="Most of the group",
                                      concerns=["The hall costs more"],
                                      map_id="decision_1")
check("a decision candidate's rationale can arrive later",
      _d2["rationale"] == "More families are free")
check("along with its support and concerns",
      _d2["support"] == "Most of the group" and _d2["concerns"] == ["The hall costs more"])
check("without duplicating the candidate", len(store.list_decisions(_S)) == 1)

# Rows made before `map_id` existed -- six real meetings' worth -- still match
# by text, and ADOPT the map id so identity is stable from then on.
_legacy_row = store.upsert_action_item(_S, "Older action with no map id")
check("a row predating map_id is created", _legacy_row["map_id"] is None)
_adopted = store.upsert_action_item(_S, "Older action with no map id", owner="Sam",
                                    map_id="action_9")
check("it is matched by text and adopts the map id, rather than doubling",
      _adopted["id"] == _legacy_row["id"] and _adopted["map_id"] == "action_9")
check("and it finally learns its owner", _adopted["owner"] == "Sam")

# The compact state the model sees must carry the fields it needs to tell a
# refinement from a new action -- their absence is why it kept re-proposing.
_slim = brain._state_for_prompt({"action_items": [
    {"id": "action_1", "action": "a", "owner": "Tara", "due": "2026-09-12",
     "status": "accepted"}]})
check("the model can see the due date it already recorded",
      _slim["action_items"][0]["due"] == "2026-09-12")
check("and the status", _slim["action_items"][0]["status"] == "accepted")


section("a model may not overwrite what a person wrote (rule 95)")

_edited = store.update_action_item(_a2["id"], action="What Tara actually agreed to",
                                   owner="Tara")
check("a human edit marks the row", bool(_edited["human_edited"]))
_after = store.upsert_action_item(_S, "the model's own wording", owner="Someone else",
                                  map_id="action_1")
check("a later analysis pass leaves a human-edited action alone",
      _after["action"] == "What Tara actually agreed to" and _after["owner"] == "Tara")

store.update_decision(_d2["id"], text="What the group actually said")
_dafter = store.upsert_decision_candidate(_S, "the model's wording", rationale="new",
                                          map_id="decision_1")
check("and a human-edited decision candidate too",
      _dafter["text"] == "What the group actually said")


section("commitments: a name is not an agreement (rule 95)")

_act = [a for a in store.list_action_items(_S) if a["map_id"] == "action_1"][0]
check("an action starts as proposed", _act["status"] == "proposed")
check("and nobody has accepted it", _act["owner_accepted"] is None)

r = client.post(f"/live-consultation/sessions/{_S}/actions/{_act['id']}/accept",
                json={"accepted": True, "accepted_by": "the host"})
_accepted = r.json()["action_item"]
check("a human can record that the owner accepted", _accepted["owner_accepted"] == 1)
check("the status moves to accepted", _accepted["status"] == "accepted")
check("and who recorded it is kept, because the host often does it for someone",
      _accepted["accepted_by"] == "the host")

r = client.post(f"/live-consultation/sessions/{_S}/actions/{_act['id']}/accept",
                json={"accepted": False})
check("declining is recorded as declining, not as silence",
      r.json()["action_item"]["owner_accepted"] is False)

# The three states have to survive the trip through SQLite as real booleans.
# They did not: SQLite stores 1/0, `1 is True` is False in Python and
# `1 === true` is false in TypeScript, so the export printed the
# self-contradicting "Tara - not yet accepted - accepted". Caught by walking
# the whole flow end to end, not by any unit test -- hence this one.
_row = [a for a in store.list_action_items(_S) if a["id"] == _act["id"]][0]
check("a declined commitment reads as False, not 0", _row["owner_accepted"] is False)
client.post(f"/live-consultation/sessions/{_S}/actions/{_act['id']}/accept",
            json={"accepted": True})
_row = [a for a in store.list_action_items(_S) if a["id"] == _act["id"]][0]
check("an accepted one reads as True, not 1", _row["owner_accepted"] is True)
check("and an unanswered one stays None, never False",
      store.upsert_action_item(_S, "nobody has been asked about this",
                               map_id="action_never")["owner_accepted"] is None)
_md_acc = _markdown_probe = client.get(f"/live-consultation/sessions/{_S}/export").text
check("so the export never says both 'not yet accepted' and 'accepted'",
      "not yet accepted — accepted" not in _md_acc
      and "not yet accepted - accepted" not in _md_acc)

r = client.post(f"/live-consultation/sessions/{_S}/actions",
                json={"action": "Ring the caretaker"})
_manual = r.json()["action_item"]
check("a human can add an action she never noticed", r.status_code == 200)
check("and it is born protected from the next analysis pass", bool(_manual["human_edited"]))
r = client.patch(f"/live-consultation/sessions/{_S}/actions/{_manual['id']}",
                 json={"blocker": "The hall is double booked", "status": "blocked"})
check("an action can be marked blocked, with the reason",
      r.json()["action_item"]["status"] == "blocked"
      and "double booked" in r.json()["action_item"]["blocker"])
r = client.delete(f"/live-consultation/sessions/{_S}/actions/{_manual['id']}")
check("and removed", r.json()["deleted"] is True)


section("human authority over the map (rules 95-96)")

store.save_state(_S, {"facts": [{"id": "fact_1", "text": "The hall is free",
                                 "status": "reported", "source_turn_ids": []}],
                      "tensions": [{"id": "tension_1", "text": "Cost against reach",
                                    "lifecycle": "open"}]})

r = client.patch(f"/live-consultation/sessions/{_S}/map/facts/fact_1",
                 json={"status": "group_established"})
check("a HUMAN may record that the group established a fact",
      r.json()["item"]["status"] == "group_established")
check("and the edit is marked as theirs", r.json()["item"]["human_edited"] is True)

_merged, _ = brain.merge(store.get_state(_S),
                         {"update": [{"id": "fact_1", "text": "the model's version",
                                      "status": "disputed"}]})
_fact = [f for f in _merged["facts"] if f["id"] == "fact_1"][0]
check("and a later model pass cannot undo it",
      _fact["status"] == "group_established" and _fact["text"] == "The hall is free")

r = client.patch(f"/live-consultation/sessions/{_S}/map/facts/fact_1",
                 json={"status": "externally_verified",
                       "evidence_note": "The booking email, 2 September"})
check("external verification records what a PERSON found",
      r.json()["item"]["evidence_note"].startswith("The booking email"))

r = client.patch(f"/live-consultation/sessions/{_S}/map/tensions/tension_1",
                 json={"text": "Cost against how many we can reach"})
check("a map item can be corrected", "how many" in r.json()["item"]["text"])

r = client.patch(f"/live-consultation/sessions/{_S}/map/tensions/tension_1",
                 json={"lifecycle": "resolved", "resolution_note": "The budget was raised"})
check("a human may resolve a concern", r.json()["item"]["lifecycle"] == "resolved")
check("the item is still in the map, not deleted",
      len(store.get_state(_S)["tensions"]) == 1)
r = client.patch(f"/live-consultation/sessions/{_S}/map/tensions/tension_1",
                 json={"lifecycle": "open"})
check("and it can be reopened", r.json()["item"]["lifecycle"] == "open")

r = client.patch(f"/live-consultation/sessions/{_S}/map/nonsense/tension_1",
                 json={"text": "x"})
check("an unknown map list is refused", r.status_code == 400)
r = client.delete(f"/live-consultation/sessions/{_S}/map/tensions/tension_1")
check("a human may delete something she should not have written down",
      r.json()["deleted"] is True and not store.get_state(_S)["tensions"])


section("provenance points at real lines in this meeting (rule 95)")

_T1 = client.post(f"/live-consultation/sessions/{_S}/turns",
                  json={"text": "We could hold it on Saturday", "realtime_item_id": "p_1",
                        "is_final": True}).json()["turn"]["id"]
_other = client.post("/live-consultation/sessions",
                     json={"title": "Another", "participants_informed": True}).json()["id"]
_T2 = client.post(f"/live-consultation/sessions/{_other}/turns",
                  json={"text": "A line in a different meeting",
                        "realtime_item_id": "p_2", "is_final": True}).json()["turn"]["id"]

_state, _dropped = lcapi._validate_provenance(_S, {
    "facts": [{"id": "fact_1", "text": "x",
               "source_turn_ids": [str(_T1), str(_T2), "99999"]}]})
check("a source reference to this meeting's own line is kept",
      str(_T1) in _state["facts"][0]["source_turn_ids"])
check("one from another session is dropped",
      str(_T2) not in _state["facts"][0]["source_turn_ids"])
check("and one that is simply invented is dropped",
      "99999" not in _state["facts"][0]["source_turn_ids"])
check("the drops are counted, not silent", _dropped == 2)
check("the reasoner's schema actually ASKS for source_turn_ids",
      "source_turn_ids" in brain._SCHEMA,
      "provenance reached 28 of 2110 real items because it was not in the schema")


section("correcting what she misheard (rule 95)")

r = client.post(f"/live-consultation/sessions/{_S}/turns/{_T1}/text",
                json={"text": "We could hold it on Sunday"})
check("a misheard line can be corrected", r.json()["turn"]["text"].endswith("Sunday"))
check("and the record shows it was edited", bool(r.json()["turn"]["corrected_at"]))
check("without claiming the map rebuilt itself",
      "not rebuilt automatically" in r.json()["note"])
r = client.post(f"/live-consultation/sessions/{_S}/turns/{_T1}/text", json={"text": "   "})
check("a line cannot be blanked by 'correcting' it to nothing", r.status_code == 400)


section("a meeting can end honestly with no decision (rule 98)")

_N = client.post("/live-consultation/sessions",
                 json={"title": "Undecided", "participants_informed": True}).json()["id"]
client.post(f"/live-consultation/sessions/{_N}/start")
client.post(f"/live-consultation/sessions/{_N}/turns",
            json={"text": "We talked it through and did not settle it",
                  "realtime_item_id": "n_1", "is_final": True})
client.post(f"/live-consultation/sessions/{_N}/end")
r = client.post(f"/live-consultation/sessions/{_N}/closeout",
                json={"outcome": "no_decision",
                      "note": "We ran out of time and will come back to it."})
check("a session can close with no decision at all", r.status_code == 200, r.text[:160])
check("and the outcome is recorded rather than left blank",
      r.json()["session"]["closeout_outcome"] == "no_decision")
check("no decision was invented", r.json()["confirmed_decision"] is None)
_md = client.get(f"/live-consultation/sessions/{_N}/export").text
check("and the export says so in words a person would use",
      "No decision was reached" in _md, _md[:200])
r = client.post(f"/live-consultation/sessions/{_N}/closeout", json={"outcome": "invented"})
check("an unknown outcome is refused", r.status_code == 400)


section("a decision can be confirmed and still carry dissent (rule 98)")

_C = client.post("/live-consultation/sessions",
                 json={"title": "Decided", "participants_informed": True}).json()["id"]
_cd = store.upsert_decision_candidate(_C, "Hold it on Saturday", map_id="decision_1")
r = client.post(f"/live-consultation/sessions/{_C}/decisions/{_cd['id']}/confirm",
                json={"retained_concerns": ["Families with small children may not manage it"]})
check("a human confirms the decision", r.json()["decision"]["status"] == "confirmed")
check("and the concern is carried WITH it, not tidied away",
      "small children" in str(r.json()["decision"]["retained_concerns"]))
_md = client.get(f"/live-consultation/sessions/{_C}/export").text
check("the export prints the retained concern beside the decision",
      "small children" in _md and "Confirmed" in _md)
_sc, _ = brain.merge(store.get_state(_C), {"confirmed_decision": {"text": "something else"}})
check("no model pass can touch a confirmed decision (rule 81 still holds)",
      (_sc.get("confirmed_decision") or {}).get("text") == "Hold it on Saturday")

# More than one thing can be settled in one meeting.
_cd2 = store.upsert_decision_candidate(_C, "Ask Nasrin to lead it", map_id="decision_2")
client.post(f"/live-consultation/sessions/{_C}/decisions/{_cd2['id']}/confirm", json={})
check("a consultation may confirm more than one decision",
      len(store.confirmed_decisions(_C)) == 2)
check("and the singular field still answers, for every old session and caller",
      client.get(f"/live-consultation/sessions/{_C}").json()["confirmed_decision"] is not None)


section("keeping the words, or not (rule 94)")

_R = client.post("/live-consultation/sessions",
                 json={"title": "Retention", "participants_informed": True,
                       "retention_policy": "days_7"}).json()["id"]
check("a retention choice is stored",
      client.get(f"/live-consultation/sessions/{_R}").json()["session"]["retention_policy"]
      == "days_7")
r = client.post("/live-consultation/sessions",
                json={"title": "x", "retention_policy": "for ever and ever"})
check("an unknown retention choice is refused", r.status_code == 400)

client.post(f"/live-consultation/sessions/{_R}/start")
client.post(f"/live-consultation/sessions/{_R}/turns",
            json={"text": "Something private that was said out loud",
                  "realtime_item_id": "r_1", "is_final": True})
store.upsert_action_item(_R, "Something to do afterwards", map_id="action_1")
client.post(f"/live-consultation/sessions/{_R}/end")

check("the full export contains the transcript while it exists",
      "Something private" in client.get(f"/live-consultation/sessions/{_R}/export").text)

r = client.delete(f"/live-consultation/sessions/{_R}/transcript")
check("the transcript can be deleted on its own", r.status_code == 200)
_after_del = client.get(f"/live-consultation/sessions/{_R}").json()
check("the words are gone", not _after_del["turns"])
check("the approved record is kept", len(_after_del["action_items"]) == 1)
check("and the session says so", bool(_after_del["session"]["transcript_deleted_at"]))

_full_after = client.get(f"/live-consultation/sessions/{_R}/export")
check("a FULL export is refused once the transcript is gone, not quietly emptied",
      _full_after.status_code == 409, str(_full_after.status_code))
check("and it says the approved record is still there", "approved record" in _full_after.text)

_U = client.post("/live-consultation/sessions",
                 json={"title": "Closeout deletion", "participants_informed": True,
                       "retention_policy": "until_closeout"}).json()["id"]
client.post(f"/live-consultation/sessions/{_U}/start")
client.post(f"/live-consultation/sessions/{_U}/turns",
            json={"text": "words that will not be kept", "realtime_item_id": "u_1",
                  "is_final": True})
client.post(f"/live-consultation/sessions/{_U}/end")
check("the words are there until the record is approved",
      len(client.get(f"/live-consultation/sessions/{_U}").json()["turns"]) == 1)
r = client.post(f"/live-consultation/sessions/{_U}/closeout",
                json={"outcome": "consultation_only"})
check("approving the record deletes them, as chosen", not r.json()["turns"])
check("and the room is told it happened", "deleted" in r.json()["note"])


section("old sessions survive the upgrade (rule 94)")

# A database made BEFORE any of this existed, upgraded in place. The owner's
# real file holds eight meetings and 438 turns; this pins the shape of that
# upgrade without touching it.
_OLD = _TMP / "pre_upgrade.db"
_conn = sqlite3.connect(_OLD)
_conn.executescript("""
    CREATE TABLE sessions (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, question TEXT NOT NULL DEFAULT '',
        context TEXT NOT NULL DEFAULT '', framework TEXT NOT NULL DEFAULT 'bahai',
        mode TEXT NOT NULL DEFAULT 'facilitator',
        decision_method TEXT NOT NULL DEFAULT 'unspecified',
        status TEXT NOT NULL DEFAULT 'ended', record_audio INTEGER NOT NULL DEFAULT 0,
        realtime_model TEXT, reasoning_model TEXT, transcribe_model TEXT, voice TEXT,
        state_revision INTEGER NOT NULL DEFAULT 3,
        created_at TEXT DEFAULT '2026-08-21 10:00:00',
        started_at TEXT DEFAULT '2026-08-21 10:00:00',
        ended_at TEXT DEFAULT '2026-08-21 11:00:00');
    CREATE TABLE turns (
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
        realtime_item_id TEXT, sequence INTEGER NOT NULL, role TEXT NOT NULL DEFAULT 'human',
        speaker_label TEXT, text TEXT NOT NULL DEFAULT '',
        is_final INTEGER NOT NULL DEFAULT 1, started_at TEXT, ended_at TEXT,
        created_at TEXT, analyzed INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE session_state (
        session_id TEXT PRIMARY KEY, state_json TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 3, updated_at TEXT);
    CREATE TABLE observations (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'note',
        importance REAL NOT NULL DEFAULT 0, summary TEXT NOT NULL DEFAULT '',
        detail TEXT NOT NULL DEFAULT '', should_request_floor INTEGER NOT NULL DEFAULT 0,
        permission_request TEXT NOT NULL DEFAULT '', speech_brief TEXT NOT NULL DEFAULT '',
        state_revision INTEGER NOT NULL DEFAULT 0, dedupe_key TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'open', created_at TEXT);
    CREATE TABLE decisions (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL, text TEXT NOT NULL DEFAULT '',
        rationale TEXT NOT NULL DEFAULT '', support TEXT NOT NULL DEFAULT '',
        concerns_json TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'candidate',
        dedupe_key TEXT NOT NULL DEFAULT '', created_at TEXT, confirmed_at TEXT);
    CREATE TABLE action_items (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL, action TEXT NOT NULL DEFAULT '',
        owner TEXT, due TEXT, status TEXT NOT NULL DEFAULT 'open',
        dedupe_key TEXT NOT NULL DEFAULT '', created_at TEXT);
    CREATE TABLE writings (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL, text TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT '', section TEXT NOT NULL DEFAULT '',
        link TEXT NOT NULL DEFAULT '', theme TEXT NOT NULL DEFAULT '',
        score REAL NOT NULL DEFAULT 0, created_at TEXT);
    CREATE TABLE speech_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, kind TEXT NOT NULL,
        allowed INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '',
        observation_id TEXT, created_at TEXT);
    INSERT INTO sessions (id, title, question) VALUES ('cons_old', 'An old meeting', 'Then?');
    INSERT INTO turns (session_id, sequence, text)
        VALUES ('cons_old', 1, 'said before the upgrade');
    INSERT INTO decisions (id, session_id, text, status)
        VALUES ('dec_old', 'cons_old', 'Something confirmed long ago', 'confirmed');
    INSERT INTO action_items (id, session_id, action, status)
        VALUES ('act_old', 'cons_old', 'An old action', 'done');
""")
_conn.execute("INSERT INTO session_state (session_id, state_json) VALUES (?,?)",
              ("cons_old", json.dumps({
                  "summary": "an old summary",
                  "facts": [{"id": "fact_1", "text": "an old fact", "status": "confirmed"}],
                  "tensions": [{"id": "tension_1", "text": "an old tension"}],
                  "action_items": [{"id": "action_1", "action": "An old action",
                                    "status": "open"}]})))
_conn.commit()
_conn.close()

store.init_db(db_path=_OLD)
store.init_db(db_path=_OLD)   # re-runnable, or it breaks on the second startup
check("an old session survives the upgrade", len(store.list_sessions(db_path=_OLD)) == 1)
check("with its transcript", len(store.list_turns("cons_old", db_path=_OLD)) == 1)
check("and its confirmed decision, the least reconstructable thing here",
      len(store.confirmed_decisions("cons_old", db_path=_OLD)) == 1)

_old_state = store.get_state("cons_old", db_path=_OLD)
check("an old 'confirmed' fact reads as 'reported', never as group-established",
      _old_state["facts"][0]["status"] == "reported")
check("an old tension gains a lifecycle of open",
      _old_state["tensions"][0]["lifecycle"] == "open")
check("an old action reads as proposed, not accepted",
      _old_state["action_items"][0]["status"] == "proposed")
check("and nobody is recorded as having accepted it",
      _old_state["action_items"][0]["owner_accepted"] is None)

_old_row = store.get_session("cons_old", db_path=_OLD)
check("an old session is not claimed to have informed its room",
      _old_row["participants_informed_at"] is None)
check("nor to have had its record approved", _old_row["closeout_outcome"] is None)
check("nor to have had its transcript deleted", _old_row["transcript_deleted_at"] is None)
check("and it keeps its words by default", _old_row["retention_policy"] == "keep")


section("privacy language is literally true (rule 94)")

_ui = Path(__file__).parent.parent / "dashboard" / "src" / "components" / "consultation"
_all_ui = "\n".join(f.read_text(encoding="utf-8") for f in _ui.glob("*.tsx"))
_setup = (_ui / "ConsultationSetup.tsx").read_text(encoding="utf-8")

# These two sentences were on screen and were simply false: live audio goes to
# OpenAI to be transcribed.
check("nothing claims what is said stays on this machine",
      "stays on this machine" not in _all_ui)
check("nothing claims the meeting is never heard by anyone else",
      "never heard by anyone else" not in _all_ui)
# And the stale panel that contradicted the working recorder 80 lines below it.
check("the stale 'there is no recorder' panel is gone",
      "no recorder in this version" not in _all_ui)
check("the setup screen says audio goes to OpenAI", "OpenAI" in _setup)
check("and that local storage is not encryption",
      "not the same as" in _setup and "encrypted" in _setup)
check("and that the provider's own terms apply to the audio", "terms apply" in _setup)
check("the start button is gated on the host's attestation",
      "disabled={!informed}" in _setup)
check("and the attestation does not pretend to be consent",
      "not a record of anyone agreeing" in _setup)


section("the consultation compilation is a separate corpus (rules 11, 84)")

_ingest = (Path(__file__).parent.parent / "scripts" / "ingest_consultation.py"
           ).read_text(encoding="utf-8")
_wsrc = (Path(__file__).parent.parent / "agents" / "live_consultation_writings.py"
         ).read_text(encoding="utf-8")
check("the compilation is ingested into its own collection",
      'COLLECTION_NAME = "consultation_compilation"' in _ingest)
check("and never opens or writes bahai_texts, which quote cards print from (rule 11)",
      'get_collection("bahai_texts")' not in _ingest
      and 'create_collection("bahai_texts")' not in _ingest
      and 'delete_collection("bahai_texts")' not in _ingest)
check("it uses the same distance metric as the other collections, or scores "
      "from the two could not be compared",
      '"hnsw:space": "cosine"' in _ingest)
check("the writings module searches the compilation", "COMPILATION_COLLECTION" in _wsrc)
check("institution-specific passages are kept out of a general consultation",
      'institutional and framework != "bahai"' in _wsrc)
check("a passage still only ever comes from the index, never from a model",
      '"verified": True' in _wsrc and "librarian" in _wsrc)

# The filter is useless if the endpoint never tells it which framework it is
# in -- it would sit defaulting to "bahai" and never fire. Caught for real
# while reading the diff.
_apisrc = (Path(__file__).parent.parent / "agents" / "live_consultation_api.py"
           ).read_text(encoding="utf-8")
check("the API passes the session's framework into the writings search",
      "framework=session.get(\"framework\")" in _apisrc)

_calls = []
_real_search = writ.search
writ.search = lambda theme, n_results=3, framework="bahai": (
    _calls.append(framework) or {"available": True, "passages": [], "note": ""})
_GEN = client.post("/live-consultation/sessions",
                   json={"title": "General", "framework": "general",
                         "participants_informed": True}).json()["id"]
client.post(f"/live-consultation/sessions/{_GEN}/writings", json={"theme": "unity"})
check("and a general consultation really does search as 'general'",
      _calls == ["general"], str(_calls))
writ.search = _real_search


# --- the suite is actually offline (rule 99) ---------------------------------

section("the offline suite is offline")

# The whole point. Until 2026-09-03 this run made three real AUTHENTICATED
# calls on the owner's own key -- a model lookup, a genuine mint of a live
# realtime credential, and a billable chat completion for the report narrative
# -- and still printed "408 passed", because each sat behind an
# `except Exception` that turned a failure into a shrug.
check("no outbound network call was attempted during the whole run",
      not _OUTBOUND, "; ".join(sorted(set(_OUTBOUND))))

# Setting os.environ is NOT the defence, and the suite must never again act as
# though it were: `agents/api.py` calls load_dotenv(override=True) at import,
# which puts the owner's real key straight back. Assert the hole is still there
# so nobody "fixes" the tripwire away believing the fake key protects them.
check("the fake key really is overwritten by api.py's load_dotenv(override=True)",
      os.environ.get("OPENAI_API_KEY", "") != "sk-test-not-a-real-key")

_armed = False
try:
    _socket.socket().connect(("api.openai.com", 443))
except _NetworkAttempted:
    _armed = True
except BaseException:
    _armed = False
check("the tripwire is still armed at the end of the run", _armed)
_OUTBOUND.clear()   # the probe above is not a real attempt

check("the tripwire cannot be swallowed by an `except Exception`",
      not issubclass(_NetworkAttempted, Exception))
check("loopback is still allowed, or the TestClient could not run",
      _is_loopback(("127.0.0.1", 8765)) and not _is_loopback(("api.openai.com", 443)))

os.environ["OPENAI_API_KEY"] = _saved_key
brain.analyze = _real_analyze

# --- Summary -----------------------------------------------------------------

print("\n" + "=" * 66)
print(f"Live Consultation: {PASS} passed, {FAIL} failed  ({PASS + FAIL} checks)")
if FAILURES:
    print("\nFailures:")
    for f in FAILURES:
        print("  - " + f)
print("=" * 66)
print(f"temp files: {_TMP}")
sys.exit(1 if FAIL else 0)
