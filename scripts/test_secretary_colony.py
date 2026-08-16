"""
Offline regression suite for Abigail working with the workforce teams
(agents/secretary_colony.py + the four workforce tools in secretary_tools.py).

    python scripts/test_secretary_colony.py

Free and fast — no LLM calls, no paid API, no network. Both databases are
throwaway: a temp workforce.db AND a temp private/ directory, so a test run can
never read or write Sheraj's real personal data (rule 15 applies to the tests
too). Console output is ASCII only (Windows cp1252 — see AGENTS.md gotchas).
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_TMP = Path(tempfile.mkdtemp(prefix="sec_colony_test_"))
os.environ["ANTHROPIC_API_KEY"] = os.environ.get("ANTHROPIC_API_KEY", "test-key")

import agents.state as state  # noqa: E402
import agents.secretary_store as store  # noqa: E402

state.DB_PATH = _TMP / "workforce.db"
store.PRIVATE_DIR = _TMP / "private"
store.MEMORY_DIR = store.PRIVATE_DIR / "memory"
store.DB_PATH = store.PRIVATE_DIR / "secretary.db"

import agents.colony as colony  # noqa: E402
import agents.colony_chat as colony_chat  # noqa: E402
import agents.router as router  # noqa: E402
import agents.secretary as secretary  # noqa: E402
import agents.secretary_colony as sc  # noqa: E402
import agents.secretary_tools as secretary_tools  # noqa: E402
import agents.system_prompt_builder as spb  # noqa: E402

colony.DB_PATH = state.DB_PATH

PASS = FAIL = 0
FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{label}{(' -- ' + detail) if detail else ''}")
        print(f"  FAIL: {label}" + (f" -- {detail}" if detail else ""))


def section(title: str):
    print(f"\n=== {title} ===")


state.init_db()
store.init_db()


# ── The privacy boundary (rule 15) ────────────────────────────────────────────

section("The privacy boundary: what may cross into workforce.db")

store.write_memory_note(
    "personal",
    "Sheraj has been struggling with his sleep since the move to the new flat "
    "and does not want that discussed with anyone else at all.")

check("ordinary work text crosses freely",
      sc.assert_shareable("Cards on the theme of unity, for a devotional gathering")
      == "Cards on the theme of unity, for a devotional gathering")

for bad, what in [("write to jane.doe@example.com about it", "an email address"),
                  ("call him on +1 555 123 4567 first", "a phone number")]:
    try:
        sc.assert_shareable(bad)
        check(f"a brief carrying {what} is refused", False, "it was allowed")
    except sc.PrivateLeak:
        check(f"a brief carrying {what} is refused", True)

try:
    sc.assert_shareable(
        "Sheraj has been struggling with his sleep since the move to the new flat "
        "and does not want that discussed")
    check("a passage copied verbatim out of her private notes is refused", False,
          "it was allowed into workforce.db")
except sc.PrivateLeak:
    check("a passage copied verbatim out of her private notes is refused", True)

check("a paraphrase that shares no long span still crosses",
      bool(sc.assert_shareable("Keep the tone restful; he wants quiet, unhurried cards.")))
check("the leak check reads her notes, not his tasks (a task he wants DONE "
      "must still be relayable)",
      "sleep" in store.read_all_memory_notes()
      and bool(sc.assert_shareable("Make twenty cards for the devotional on Friday")))


# ── Steering: goals and briefs actually reach the work ────────────────────────

section("Goals and briefs reach every prompt, pipeline work included")

out = sc.set_team_goal("Print Studio", "Give away 50 cards on unity by Naw-Ruz",
                       detail="For newcomers at the weekly gathering.", target_count=50)
check("a goal set by Abigail is created", "Goal #" in out, out[:120])
goals = colony.list_goals(team="print_studio", status="active")
check("the goal records that ABIGAIL set it, not Sheraj",
      goals and goals[0]["set_by"] == "abigail",
      f"set_by={goals[0]['set_by'] if goals else 'no goal'}")
check("a goal set by hand still records Sheraj",
      colony.create_goal("ledger", "Keep spend under $20 a month")["set_by"] == "sheraj")
check("the team's agents now carry the goal",
      "unity" in colony.goal_note_for_agent("scribe"))
check("the goal reaches a real pipeline prompt (not just chat)",
      "Naw-Ruz" in spb.build_system_prompt("scribe", "copy"))

out = sc.brief_agent("Clara", "Titles under six words. Never say handcrafted.")
check("briefing by DISPLAY name resolves to the right agent",
      "Clara" in out and
      "six words" in colony.get_agent_settings("scribe")["custom_instructions"], out[:120])
check("standing instructions reach a real pipeline prompt (the whole point)",
      "Never say handcrafted" in spb.build_system_prompt("scribe", "copy"))
sc.brief_agent("scribe", "Prefer plain nouns.")
instructions = colony.get_agent_settings("scribe")["custom_instructions"]
check("a second brief ADDS to the first rather than silently replacing it",
      "six words" in instructions and "plain nouns" in instructions)
sc.brief_agent("scribe", "Start over: short, plain titles.", replace=True)
check("replace=true overwrites",
      "six words" not in colony.get_agent_settings("scribe")["custom_instructions"])

colony.set_agent_settings("scribe", custom_instructions="x" * 5000)
note = colony.instructions_note_for_agent("scribe")
check("the instruction note is hard-capped for local Qwen (rule 1)",
      len(note) <= colony.INSTRUCTIONS_NOTE_MAX_CHARS,
      f"{len(note)} chars")
check("the cap holds all the way into the built prompt",
      spb.build_system_prompt("scribe", "copy").count("x" * 700) == 0)

colony.set_agent_settings("scribe", custom_instructions="Titles under six words.")
chat_prompt = colony_chat.build_agent_system_prompt("scribe")
check("chat states the standing instructions exactly ONCE (no double injection)",
      chat_prompt.count("Titles under six words.") == 1,
      f"counted {chat_prompt.count('Titles under six words.')}")
check("the Secretary is never given colony standing instructions (rule 15/16)",
      colony.instructions_note_for_agent("secretary") == "")
check("instruments carry no instructions",
      colony.instructions_note_for_agent("compositor") == "")

try:
    sc.set_team_goal("print_studio", "Match his notes: struggling with his sleep since "
                                     "the move to the new flat and does not want that")
    check("a goal carrying private text is refused before storage", False, "it was stored")
except sc.PrivateLeak:
    check("a goal carrying private text is refused before storage", True)


# ── Talking to an agent (rule 16: never on Claude) ────────────────────────────

section("ask_agent talks to the real agent, on the agent's own model")

calls: dict = {"llm": [], "claude": 0}
_real_call_llm = router.call_llm
_real_call_llm_agentic = router.call_llm_agentic
_real_call_claude_agentic = router.call_claude_agentic


def _fake_call_llm(task_type, messages, agent=None, **kw):
    calls["llm"].append({"task_type": task_type, "agent": agent, "messages": messages})
    return "Ruth here — I checked the index and found two passages."


def _fake_call_llm_agentic(task_type, messages, agent=None, **kw):
    # Ruth has tools, so her chat goes through the workforce's OWN tool-calling
    # loop (rule 36) — never Claude's.
    calls["llm"].append({"task_type": task_type, "agent": agent, "messages": messages})
    return "Ruth here — I checked the index and found two passages."


def _fake_call_claude_agentic(*a, **kw):
    calls["claude"] += 1
    return "(claude)"


router.call_llm = _fake_call_llm
colony_chat.call_llm = _fake_call_llm
router.call_llm_agentic = _fake_call_llm_agentic
colony_chat.call_llm_agentic = _fake_call_llm_agentic
router.call_claude_agentic = _fake_call_claude_agentic

result = sc.ask_agent("Ruth", "Is the quotation about the oneness of mankind verbatim?")
check("asking by display name reaches the librarian",
      result["ok"] and result["agent"] == "librarian", str(result)[:150])
check("the agent's own answer comes back", "two passages" in result["text"])
check("the workforce agent ran on ITS OWN model, never Claude (rule 16)",
      calls["claude"] == 0 and calls["llm"] and calls["llm"][-1]["agent"] == "librarian")
history = colony.get_agent_messages("librarian")
check("the relay is written into the agent's Colony history, labelled as a relay",
      any(m["role"] == "user" and m["content"].startswith(sc.RELAY_PREFIX) for m in history),
      "Sheraj must be able to read what was asked in his name")

check("asking Abigail herself is refused (she is not a colony chat)",
      sc.ask_agent("Abigail", "hello")["ok"] is False)
check("asking a pipeline instrument is refused",
      sc.ask_agent("compositor", "hello")["ok"] is False)
check("an unknown name is reported, never silently redirected",
      sc.ask_agent("Gertrude", "hello")["ok"] is False)

try:
    sc.ask_agent("Ruth", "He has been struggling with his sleep since the move to the "
                         "new flat and does not want that discussed")
    check("a question carrying private text never reaches the agent", False, "it was sent")
except sc.PrivateLeak:
    check("a question carrying private text never reaches the agent", True)


# ── Making: a job always waits for Sheraj ─────────────────────────────────────

section("request_team_job queues and starts NOTHING")

import agents.api as api  # noqa: E402

started: list = []
starters: list = []
_real_start_job = api._start_job


def _fake_start_job(kind, runner, started_by="sheraj"):
    started.append(kind)
    starters.append(started_by)
    return "job-abc"


api._start_job = _fake_start_job

before = len(store.get_pending_actions())
job = sc.request_team_job("quote_card", "Cards on unity for the weekly gathering")
check("the job is queued", job["ok"] and job["action_id"] > 0, str(job)[:150])
check("NOTHING was started by queueing it", started == [], f"started {started}")
pending = store.get_pending_actions()
check("it lands in her ONE approvals queue, as a workforce_job",
      len(pending) == before + 1 and
      store.get_pending_action(job["action_id"])["kind"] == "workforce_job")
check("the queued description says plainly that it costs money",
      "costs money" in job["description"], job["description"])
check("the reply tells her it has NOT started",
      "has NOT started" in job["text"] or "NOT started" in job["text"])
check("an unknown kind is refused rather than guessed",
      sc.request_team_job("poster", "something")["ok"] is False)

outcome = secretary.execute_pending_action(job["action_id"])
check("approving it runs the REAL card pipeline (rule 40)",
      started == ["card-pipeline"], f"started {started}")
check("the run is attributed to Abigail, so the Pipeline tab can adopt and label it",
      starters == ["abigail"], f"starters {starters}")
check("she says where to watch it rather than telling him to start it himself",
      "Pipeline tab" in outcome and "Colony map" in outcome, outcome[:160])
check("the approved action is resolved as done",
      store.get_pending_action(job["action_id"])["status"] == "done")
check("the outcome names the team and the job", "Print Studio" in outcome, outcome[:120])

started.clear()
job2 = sc.request_team_job("bookmark", "A bookmark on steadfastness")
secretary.execute_pending_action(job2["action_id"])
check("a bookmark job runs the REAL bookmark pipeline",
      started == ["full-pipeline"], f"started {started}")

started.clear()
job3 = sc.request_team_job("video", "The story of Mullá Husayn's first meeting",
                           detail="A short scene at the gate of Shiraz.")
video_outcome = secretary.execute_pending_action(job3["action_id"])
check("a video job CREATES a project and renders nothing (rules 31/33)",
      started == [] and "video project" in video_outcome, f"{started} {video_outcome[:120]}")


# ── Many cards in one request, with Ruth finding the quotes ───────────────────

section("Several cards are ONE request, one approval, one hands-free run")

found_calls: list = []


def _fake_suggest(topic="", count=4, sources=""):
    found_calls.append({"topic": topic, "count": count})
    return {"topic": topic, "requested": count, "skipped_too_long": 0,
            "items": [{"quote": f"Verified passage {i + 1} about {topic}.",
                       "source": "Ruhi Book 1", "score": 0.5, "shortened": False,
                       "origin": "ruhi_book1", "verified": True}
                      for i in range(min(count, 6))]}


_real_suggest = api.suggest_ruhi_quotes
api.suggest_ruhi_quotes = _fake_suggest

batch = sc.request_team_job("quote_card", "Service to humanity", count=4)
check("asking for four cards queues ONE action, not four",
      batch["ok"] and batch["count"] == 4 and len(store.get_pending_actions()) == 1,
      str(batch)[:160])
check("Ruth finds the quotes BEFORE it queues, so the approval names them",
      found_calls and found_calls[-1]["count"] == 4 and len(batch["quotes"]) == 4)
check("the queued description says how many and that each one costs",
      "4 quote cards" in batch["description"] and "4 times" in batch["description"],
      batch["description"])
check("the quotes are shown to Sheraj before he approves",
      "Verified passage 1" in batch["text"])

batched: dict = {}
_real_run_batch = api.pipeline_run_card_batch


def _fake_run_batch(req, started_by="sheraj"):
    batched["quotes"] = list(req.quotes)
    batched["theme"] = req.theme
    batched["started_by"] = started_by
    return {"job_id": "batch-1", "status": "running", "total": len(req.quotes)}


api.pipeline_run_card_batch = _fake_run_batch
started.clear()
outcome = secretary.execute_pending_action(batch["action_id"])
check("approving runs the REAL batch endpoint with all four quotes",
      len(batched.get("quotes", [])) == 4 and started == [], str(batched)[:160])
check("the batch is attributed to Abigail so the Pipeline tab can label it",
      batched.get("started_by") == "abigail")
check("she is told where to watch it, not to go and start it",
      "Pipeline tab" in outcome and "4 cards" in outcome, outcome[:160])

big = sc.request_team_job("quote_card", "Unity", count=99)
check("a request over the batch cap is capped, not refused",
      big["ok"] and 1 < big["count"] <= sc.MAX_CARDS_PER_RUN, str(big)[:120])
check("the cap is explained rather than silently applied",
      "capped at" in big["text"])
check("she is told when Ruth found fewer than asked (never padded to the number)",
      "not the" in big["text"] and str(big["count"]) in big["text"], big["text"][:200])

one = sc.request_team_job("bookmark", "Steadfastness", count=5)
check("bookmarks stay one per run, and say so",
      one["ok"] and one["count"] == 1 and "Only one" in one["text"], one["text"][:120])

api.suggest_ruhi_quotes = lambda topic="", count=4, sources="": {
    "topic": topic, "requested": count, "items": [], "skipped_too_long": 0}
none = sc.request_team_job("quote_card", "A theme with nothing in the library", count=3)
check("nothing found means nothing queued, said plainly",
      none["ok"] is False and "Nothing was queued" in none["text"], none["text"][:140])

api.suggest_ruhi_quotes = _real_suggest
api.pipeline_run_card_batch = _real_run_batch
api._start_job = _real_start_job


# ── What the teams are doing, on the map ──────────────────────────────────────

section("Team activity is derived from the real job store")

state.log_run("task-live-1", "artist", "prompt", "in", "out")
check("no running jobs means no team is working — even with an agent that just "
      "logged a step (a finished or cancelled run must not leave a team lit)",
      all(not t["jobs"] and not t["working"] for t in colony.colony_snapshot()["teams"]))

api.JOBS["fake-1"] = {
    "job_id": "fake-1", "kind": "card-batch", "status": "running",
    "progress": "Card 2/4: the Reviewer is scoring", "steps": [], "result": None,
    "error": None, "started_by": "abigail", "created_at": "2026-08-14T20:00:00",
    "updated_at": "2026-08-14T20:00:00",
}
snapshot = colony.colony_snapshot()
studio = next(t for t in snapshot["teams"] if t["id"] == "print_studio")
crew = next(t for t in snapshot["teams"] if t["id"] == "film_crew")
check("the running job lands on the team that is actually running it",
      len(studio["jobs"]) == 1 and studio["working"] is True and not crew["jobs"])
check("the job carries what it is doing, in plain words",
      studio["jobs"][0]["label"] == "making a batch of quote cards"
      and "Reviewer" in studio["jobs"][0]["progress"])
check("it says who started it, in words Sheraj reads",
      studio["jobs"][0]["started_by_label"] == "Abigail")

api.JOBS["fake-1"]["status"] = "done"
check("a finished job stops showing as work in flight",
      not next(t for t in colony.colony_snapshot()["teams"]
               if t["id"] == "print_studio")["jobs"])
api.JOBS.pop("fake-1", None)
check("an unknown job kind is not given a team it isn't running on",
      colony.JOB_KIND_TEAM.get("secretary-thing") is None)


# ── The approval queue is always stated, so she can't remember it wrong ───────

section("The approval queue in her prompt is ground truth, never memory")

for a in store.get_pending_actions():
    store.resolve_pending_action(a["id"], "done")

# Keep the suite genuinely offline: building her prompt would otherwise ask the
# real Google Calendar whether it is connected.
import agents.gcal as gcal  # noqa: E402
gcal.is_authorised = lambda: False

prompt, _ = secretary._build_system_prompt()
check("an EMPTY queue is stated explicitly, not left out",
      "Nothing is waiting right now" in prompt,
      "an omitted section is what let her describe finished actions as pending")
queued = sc.request_team_job("quote_card", "A single card on kindness")
prompt, _ = secretary._build_system_prompt()
check("a non-empty queue lists exactly what is pending",
      f"#{queued['action_id']}" in prompt and "COMPLETE current queue" in prompt)

# The prompt alone was NOT enough: she repeated a stale queue from her own
# earlier reply. The record has to overrule her memory in code.
live_id = queued["action_id"]
correction = secretary._approval_ground_truth(
    f"Waiting for you: #{live_id - 1}, #{live_id - 2} and #{live_id}.")
check("naming a finished action as still waiting is corrected from the record",
      f"#{live_id - 1}" in correction and f"Waiting now: #{live_id}" in correction,
      correction)
check("a correct reply gets no correction footer",
      secretary._approval_ground_truth(f"Just #{live_id} is waiting for approval.") == "")
check("a reply that mentions no action number is left alone",
      secretary._approval_ground_truth("Your calendar is clear tomorrow.") == "")
store.resolve_pending_action(live_id, "done")
check("with the queue empty, a stale claim says so plainly",
      "Nothing is waiting now" in secretary._approval_ground_truth(
          f"#{live_id} is still waiting for your approval."))


# ── Reporting back ────────────────────────────────────────────────────────────

section("workforce_report is built from records, not from memory")

state.log_run("task-rep-1", "librarian", "retrieve", "unity", "3 passages",
              passed_review=None)
state.log_run("task-rep-1", "reviewer", "score", "listing", "overall 9.2",
              passed_review=True)

report = sc.workforce_report()
check("the report names the teams", "PRINT STUDIO" in report and "FILM CREW" in report)
check("it uses the names Sheraj sees on screen", "Ruth" in report and "Nora" in report)
check("it shows the goal and who set it", "set by Abigail, for you" in report, report[:400])
check("a judged run is reported as judged",
      "passed review" in report)
check("a mechanical step is NOT reported as passing (rule 14)",
      "mechanical step, not judged" in report)
check("a single team can be asked about",
      "FILM CREW" not in sc.workforce_report(team="Print Studio"))
check("an unknown team is reported, never silently widened",
      "no team called" in sc.workforce_report(team="Marketing"))


# ── The tool surface she actually calls ───────────────────────────────────────

section("The tool surface and its gate")

names = [t["name"] for t in secretary_tools.ALL_TOOLS]
for tool in ("workforce_report", "ask_agent", "set_team_goal", "brief_agent",
             "request_team_job"):
    check(f"'{tool}' is offered to her", tool in names)
check("workforce_report is a READ tool",
      "workforce_report" in [t["name"] for t in secretary_tools.READ_TOOLS])
check("the three acting tools are WRITE tools (deduped, error-tracked)",
      all(t in [w["name"] for w in secretary_tools.WRITE_TOOLS]
          for t in ("ask_agent", "set_team_goal", "brief_agent", "request_team_job")))
check("she has NO tool that approves her own queued work",
      not any("approve" in n or "resolve" in n for n in names),
      "approval must stay Sheraj's, in the dashboard or by replying 'approve'")
check("she has NO tool that touches the wallet or its allowlist (rules 42/44)",
      not any("wallet" in n or "allowlist" in n or "send_usdc" in n for n in names))

effects = {"remembered": [], "tasks_added": [], "events": [], "reminders": [],
           "workspace": [], "workforce": [], "queued_for_approval": [], "errors": []}
executor = secretary_tools.make_executor({}, effects)

text = executor("workforce_report", {"team": "Print Studio"})
check("the executor dispatches workforce_report", "PRINT STUDIO" in text, text[:120])

started.clear()
api._start_job = _fake_start_job
text = executor("request_team_job", {"kind": "quote_card", "theme": "Cards on service"})
check("the executor queues a job and starts nothing",
      started == [] and len(effects["queued_for_approval"]) == 1,
      f"{started} {effects['queued_for_approval']}")
check("the queued job shows in the code-authored confirmation line",
      "Needs your approval" in secretary._ground_truth_confirmation(effects))
api._start_job = _real_start_job

text = executor("brief_agent", {"agent": "Theo", "instructions": "Warmer palettes."})
check("a brief through the executor lands and is recorded as a real effect",
      "Theo" in text and effects["workforce"], text[:120])
check("the workforce effect shows in the confirmation line",
      "Workforce:" in secretary._ground_truth_confirmation(effects))

text = executor("set_team_goal", {"team": "Print Studio",
                                  "goal": "reach him on +1 555 987 6543"})
check("a privacy refusal comes back as a plain explanation, not a tool failure",
      "Nothing was written" in text and not effects["errors"], text[:160])

text = executor("ask_agent", {"agent": "nobody", "message": "hi"})
check("an unknown agent is explained rather than crashing", "nobody" in text)

# A read-only turn records no effect, so the uncommitted-action check has to
# test "no tool ran at all" — not "no effect" — or an ordinary lookup answer
# containing an action verb gets flagged as a silent failure (seen live).
read_effects = {"remembered": [], "tasks_added": [], "events": [], "reminders": [],
                "workspace": [], "workforce": [], "queued_for_approval": [],
                "errors": [], "tool_calls": []}
secretary_tools.make_executor({}, read_effects)("workforce_report", {})
check("a read-only tool call is recorded as a real tool call",
      read_effects["tool_calls"] == ["workforce_report"])
secretary._finalize_reply("No goal set for them right now. Want me to set one?",
                          read_effects)
check("a read-only turn is NOT flagged as an action that never happened",
      read_effects["errors"] == [], str(read_effects["errors"]))
empty = dict(read_effects, tool_calls=[], errors=[])
secretary._finalize_reply("Adding that to the calendar now.", empty)
check("a narrated action with NO tool call at all is still caught (rule 22)",
      len(empty["errors"]) == 1, str(empty["errors"]))

router.call_llm = _real_call_llm
colony_chat.call_llm = _real_call_llm
router.call_llm_agentic = _real_call_llm_agentic
colony_chat.call_llm_agentic = _real_call_llm_agentic
router.call_claude_agentic = _real_call_claude_agentic


# ── Summary ───────────────────────────────────────────────────────────────────

print(f"\n{'=' * 62}")
print(f"  {PASS} passed, {FAIL} failed  ({PASS + FAIL} checks)")
if FAILURES:
    print("\nFailures:")
    for f in FAILURES:
        print(f"  - {f}")
print(f"{'=' * 62}")
sys.exit(1 if FAIL else 0)
