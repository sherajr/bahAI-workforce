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
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

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

resolved, notes = brain.merge({"tensions": [{"id": "tension_1", "text": "structure vs openness"}]},
                              {"resolve": ["tension_1"]})
check("a resolved tension leaves the unresolved list", resolved["tensions"] == [])
check("and the merge says what it did", any("resolved" in n for n in notes), str(notes))

obs = brain.parse_observations(patch, 4)
check("importance is clamped to 0..1", obs[0].importance == 1.0)
check("an observation that wants the floor with nothing to ask cannot ask",
      obs[1].should_request_floor is False)
check("observations carry the revision they were formed against",
      all(o.state_revision == 4 for o in obs))

validated, problem = brain.validate_state(merged)
check("the merged map validates against the domain models", problem is None, str(problem))

bad_state, problem = brain.validate_state({"facts": [{"text": "x", "status": "made-up"}]})
check("an invalid enum is caught by validation", problem is not None)

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
check("eagerness is the documented default", td["eagerness"] == rt.VAD_EAGERNESS)
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
check("it reports recording as unsupported", caps["recording_supported"] is False)
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
check("recording is refused rather than silently ignored", r.status_code == 400, r.text[:120])

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
check("the subsystem is its own set of modules", len(live_files) == 7, str(live_files))
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
