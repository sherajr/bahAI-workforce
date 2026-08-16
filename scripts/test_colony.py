"""
Offline regression suite for the Colony (dashboard tab: the workforce as an
organisation). Free and fast — no LLM calls, no paid API, no GPU.

    python scripts/test_colony.py

Follows scripts/test_video_pipeline.py's shape: a temporary database, stubbed
model calls, and the whole HTTP surface exercised through FastAPI's TestClient.
Console output is ASCII only (Windows cp1252 — see AGENTS.md gotchas).
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Point the whole stack at a throwaway database BEFORE anything imports state.
_TMP = tempfile.mkdtemp(prefix="colony_test_")
os.environ["ANTHROPIC_API_KEY"] = os.environ.get("ANTHROPIC_API_KEY", "test-key")

import agents.state as state  # noqa: E402

state.DB_PATH = Path(_TMP) / "workforce.db"

import agents.colony as colony  # noqa: E402
import agents.colony_chat as colony_chat  # noqa: E402
import agents.colony_tools as colony_tools  # noqa: E402
import agents.router as router  # noqa: E402
import agents.video_store as video_store  # noqa: E402

colony.DB_PATH = state.DB_PATH
video_store.DB_PATH = state.DB_PATH

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


# ── Setup ─────────────────────────────────────────────────────────────────────

section("Fresh database")
state.init_db()
check("init_db creates the colony tables on a FRESH database",
      colony.get_agent_settings("artist")["custom_instructions"] == "",
      "this is the video-tables bug all over again if it fails: init_colony_db "
      "must run OUTSIDE state.init_db's own connection block")
check("every agent in AGENT_NAMES has a team",
      all(n in colony.AGENT_TEAM for n in state.AGENT_NAMES),
      f"missing: {[n for n in state.AGENT_NAMES if n not in colony.AGENT_TEAM]}")
check("no agent is in two teams (one unambiguous graph position each)",
      len(colony.AGENT_TEAM) == sum(len(t["members"]) + len(t["instruments"])
                                    for t in colony.TEAMS.values()))


# ── Teams and chattability ────────────────────────────────────────────────────

section("Teams, instruments, and who may be chatted with")
check("the Secretary is a colony node", "secretary" in colony.AGENT_TEAM)
check("the Secretary is NOT chattable from the Colony (rules 15/16)",
      "secretary" in colony.NO_COLONY_CHAT)
check("instruments are not chattable",
      colony.INSTRUMENTS and colony.INSTRUMENTS <= colony.NO_COLONY_CHAT)
check("compositor and consultation are instruments, not people",
      {"compositor", "consultation"} <= colony.INSTRUMENTS)
check("the personas ARE chattable",
      all(a not in colony.NO_COLONY_CHAT
          for a in ("librarian", "artist", "scribe", "reviewer", "steward", "translator")))

try:
    colony_chat.chat("secretary", "hello")
    check("chatting with the Secretary raises", False, "it did not raise")
except ValueError:
    check("chatting with the Secretary raises", True)
except Exception as e:
    check("chatting with the Secretary raises ValueError", False, f"raised {type(e).__name__}")


# ── Settings ──────────────────────────────────────────────────────────────────

section("Per-agent settings")
colony.set_agent_settings("scribe", custom_instructions="Write shorter titles.")
check("instructions persist",
      colony.get_agent_settings("scribe")["custom_instructions"] == "Write shorter titles.")
colony.set_agent_settings("scribe", paused=True)
check("pausing does not wipe the instructions (partial update)",
      colony.get_agent_settings("scribe")["custom_instructions"] == "Write shorter titles."
      and colony.get_agent_settings("scribe")["paused"] is True)
try:
    colony_chat.chat("scribe", "hello")
    check("a paused agent refuses to chat", False, "it did not raise")
except ValueError:
    check("a paused agent refuses to chat", True)
colony.set_agent_settings("scribe", paused=False)


# ── Goals and the capped steering note ────────────────────────────────────────

section("Team goals and goal steering")
check("no goal note before any goal exists", colony.goal_note_for_agent("artist") == "")

goal = colony.create_goal("print_studio", "Give away 50 cards on unity by Naw-Ruz",
                          detail="Focus on newcomers.", target_count=50)
check("goal created", goal["id"] > 0 and goal["status"] == "active")
check("goal steers every member of its team",
      colony.goal_note_for_agent("artist").startswith("Give away 50 cards"))
check("a goal does NOT leak to another team", colony.goal_note_for_agent("steward") == "")

long_goal = colony.create_goal("film_crew", "x" * 900)
note = colony.goal_note_for_agent("director")
check("a long goal is truncated to the Qwen-safe cap (rule 1)",
      len(note) <= colony.GOAL_NOTE_MAX_CHARS,
      f"note was {len(note)} chars, cap is {colony.GOAL_NOTE_MAX_CHARS}")
colony.delete_goal(long_goal["id"])

from agents.system_prompt_builder import build_system_prompt  # noqa: E402

prompt = build_system_prompt("artist", "design")
check("the goal reaches a real pipeline prompt, not just chat",
      "Give away 50 cards" in prompt)
check("an agent with no goal gets no goal block",
      "current goal for your team" not in build_system_prompt("steward", "steward"))
check("the goal appears exactly once in a chat prompt (no double injection)",
      colony_chat.build_agent_system_prompt("artist").count("Give away 50 cards") == 1)
check("a goal records who set it (Sheraj by default)", goal["set_by"] == "sheraj")

# Standing instructions ride the SAME single injection point as the goal
# (rule 39), so a brief reaches pipeline work rather than chat alone.
colony.set_agent_settings("artist", custom_instructions="Warmer palettes, less gold.")
check("standing instructions reach a real pipeline prompt",
      "Warmer palettes" in build_system_prompt("artist", "design"))
check("they appear exactly once in a chat prompt (no double injection)",
      colony_chat.build_agent_system_prompt("artist").count("Warmer palettes") == 1)
colony.set_agent_settings("artist", custom_instructions="y" * 3000)
check("a long brief is capped for local Qwen (rule 1)",
      len(colony.instructions_note_for_agent("artist"))
      <= colony.INSTRUCTIONS_NOTE_MAX_CHARS)
colony.set_agent_settings("artist", custom_instructions="")
check("no instructions means no instructions block",
      "standing instructions for you" not in build_system_prompt("artist", "design"))

check("an agent resolves from its display name", colony.resolve_agent("Theo") == "artist")
check("an agent resolves from its id", colony.resolve_agent("artist") == "artist")
check("an unknown name resolves to nothing, never a near-miss",
      colony.resolve_agent("Gertrude") is None)
check("a team resolves from its display name",
      colony.resolve_team("Print Studio") == "print_studio")
check("every persona has a display name",
      all(a in colony.DISPLAY_NAMES for a in colony.AGENT_TEAM if a not in colony.INSTRUMENTS))

try:
    colony.create_goal("no_such_team", "nope")
    check("an unknown team is rejected", False, "it did not raise")
except ValueError:
    check("an unknown team is rejected", True)

progress = colony.goal_progress(colony.get_goal(goal["id"]))
check("progress is measured, and starts at zero",
      progress["measurable"] and progress["done"] == 0 and progress["target"] == 50)
check("a steering-only team reports progress as unmeasurable",
      colony.goal_progress(colony.create_goal("office", "Keep Feast dates current"))
      ["measurable"] is False)


# ── The handoff graph, derived from task_runs ─────────────────────────────────

section("Handoff graph derived from task_runs")
task_a = state.create_task("bookmark on unity", "bookmark")
task_b = state.create_task("card on service", "quote_card")
for agent, step in [("librarian", "retrieve"), ("artist", "prompt"),
                    ("scribe", "write"), ("reviewer", "score")]:
    state.log_run(task_a, agent, step, "in", "out")
for agent, step in [("librarian", "retrieve"), ("artist", "prompt"),
                    ("scribe", "write")]:
    state.log_run(task_b, agent, step, "in", "out")

edges = {(e["source"], e["target"]): e["count"] for e in colony.handoff_edges()}
check("librarian -> artist counted across both tasks",
      edges.get(("librarian", "artist")) == 2, str(edges))
check("scribe -> reviewer counted once (only task A reached review)",
      edges.get(("scribe", "reviewer")) == 1, str(edges))
check("no edge is invented across two different tasks",
      ("reviewer", "librarian") not in edges, str(edges))

state.log_run(task_a, "reviewer", "score_again", "in", "out")
edges2 = {(e["source"], e["target"]) for e in colony.handoff_edges()}
check("an agent stepping twice in a row is not a self-edge",
      ("reviewer", "reviewer") not in edges2)


# ── Trust and rule 14 ─────────────────────────────────────────────────────────

section("Trust and judged-vs-mechanical runs (rule 14)")
before = state.get_agent_status("compositor")["total_runs"]
state.log_run(task_a, "compositor", "render", "in", "out")  # mechanical, no verdict
check("a mechanical run does not move trust",
      state.get_agent_status("compositor")["total_runs"] == before)
state.log_run(task_a, "translator", "translate", "in", "out", passed_review=True)
check("a judged run does move trust",
      state.get_agent_status("translator")["total_runs"] == 1)

runs = colony.recent_runs("compositor")
check("recent_runs keeps judged and unjudged distinguishable",
      any(r["judged"] is False and r["passed_review"] is None for r in runs))


# ── The tool gate ─────────────────────────────────────────────────────────────

section("The tool gate: free reads run, paid work queues")
check("every gated kind belongs to some agent's toolset",
      colony_tools.GATED_KINDS <= {n for a in colony_tools.AGENT_TOOLS
                                   for n in colony_tools.tools_for_names(a)})
check("the paid surface is gated: image generation",
      "generate_image" in colony_tools.GATED_KINDS)
check("the paid surface is gated: product scoring",
      "score_product" in colony_tools.GATED_KINDS)
check("the paid surface is gated: translation",
      "translate_quote" in colony_tools.GATED_KINDS)
check("no gated kind has an immediate handler (a gate that can be bypassed "
      "is not a gate)",
      not (colony_tools.GATED_KINDS & set(colony_tools.IMMEDIATE_HANDLERS)))
# Rule 1: lean prompts for local Qwen. The Steward is a DELIBERATE exception at
# 5 — the wallet is her domain and adds two tools (2026-08-14). Carved out
# narrowly rather than raising the limit for everyone, so the discipline still
# bites for the next agent that wants "just one more tool".
check("toolsets stay small for local Qwen (rule 1)",
      all(len(t) <= (5 if a == "steward" else 3)
          for a, t in colony_tools.AGENT_TOOLS.items()),
      str({a: len(t) for a, t in colony_tools.AGENT_TOOLS.items()}))

effects: dict = {"queued": [], "used": []}
execute = colony_tools.make_executor("artist", effects)

pending_before = len(colony.list_actions("pending"))
out = execute("generate_image", {"prompt": "a nine-pointed star at dawn"})
check("a paid call queues instead of running", "Queued for Sheraj's approval" in out)
check("the queued action is a real pending row",
      len(colony.list_actions("pending")) == pending_before + 1)
check("the tool result tells the model it has NOT run", "has NOT run" in out)

out2 = execute("generate_image", {"prompt": "a nine-pointed star at dawn"})
check("an identical repeat in one turn does not queue twice",
      len(colony.list_actions("pending")) == pending_before + 1, out2)

out3 = execute("spend_report", {})
check("an agent cannot use another agent's tool",
      "not one of your tools" in out3, out3)

steward_exec = colony_tools.make_executor("steward", {"queued": [], "used": []})
check("a free read runs immediately", "All-time $" in steward_exec("spend_report", {}))
check("an unknown tool name is reported, not raised",
      "not one of your tools" in steward_exec("delete_everything", {}))


class _Boom:
    def __call__(self, *a, **k):
        raise RuntimeError("index unavailable")


_saved = colony_tools.IMMEDIATE_HANDLERS["spend_report"]
colony_tools.IMMEDIATE_HANDLERS["spend_report"] = _Boom()
crash_exec = colony_tools.make_executor("steward", {"queued": [], "used": []})
result = crash_exec("spend_report", {})
check("a handler that raises returns an error string, never kills the turn",
      "didn't work" in result and "index unavailable" in result, result)
colony_tools.IMMEDIATE_HANDLERS["spend_report"] = _saved


# ── The agentic loop (stubbed providers) ──────────────────────────────────────

section("call_llm_agentic: tool-calling on Grok and Ollama")

_grok_script: list[dict] = []
_ollama_script: list[dict] = []
_rounds = {"grok": 0, "ollama": 0}


def _fake_grok(messages, tools, temperature, max_tokens, force_text=False, _attempt=0,
               model=None):
    _rounds["grok"] += 1
    if force_text:
        return {"role": "assistant", "content": "Final answer.", "tool_calls": []}
    return _grok_script.pop(0) if _grok_script else \
        {"role": "assistant", "content": "Done.", "tool_calls": []}


def _fake_ollama(messages, tools, temperature, max_tokens, force_text=False, timeout=None,
                 model=None):
    _rounds["ollama"] += 1
    _rounds["ollama_had_tools"] = bool(tools) and not force_text
    return _ollama_script.pop(0) if _ollama_script else \
        {"role": "assistant", "content": "Done.", "tool_calls": []}


router._grok_tool_round = _fake_grok
router._ollama_tool_round = _fake_ollama

calls_seen: list[tuple[str, dict]] = []


def _spy(name, args):
    calls_seen.append((name, args))
    return "tool said hello"


# Grok returns arguments as a JSON STRING; Ollama returns a dict. Both must
# reach the executor as the same dict — this is the whole point of
# _normalize_tool_calls, and getting it wrong breaks one provider silently.
_grok_script.append({
    "role": "assistant", "content": "Looking that up.",
    "tool_calls": [{"id": "c1", "function": {"name": "search_library",
                                             "arguments": '{"query": "unity"}'}}],
})
reply = router.call_llm_agentic("reviewer", [{"role": "user", "content": "hi"}],
                                system="sys", tools=[], executor=_spy, max_rounds=3)
check("Grok's JSON-string arguments are decoded to a dict",
      calls_seen and calls_seen[0] == ("search_library", {"query": "unity"}), str(calls_seen))
# Round 1 narrates and calls a tool; round 2 answers with no tool call and
# ends the loop. BOTH texts must survive — returning only the final round is
# the regression call_claude_agentic already learned the hard way.
check("every round's text is kept, not just the last",
      "Looking that up." in reply and "Done." in reply, reply)

calls_seen.clear()
_ollama_script.append({
    "role": "assistant", "content": "",
    "tool_calls": [{"function": {"name": "spend_report", "arguments": {}}}],
})
router.call_llm_agentic("steward", [{"role": "user", "content": "hi"}],
                        system="sys", tools=[{"x": 1}], executor=_spy, max_rounds=2)
check("Ollama's dict arguments reach the executor unchanged",
      calls_seen and calls_seen[0] == ("spend_report", {}), str(calls_seen))
check("Ollama's tool-less id still gets one (no crash on a missing id)", True)

# A model that never stops calling tools must still terminate in text.
_rounds["grok"] = 0
_grok_script.clear()
for _ in range(10):
    _grok_script.append({
        "role": "assistant", "content": "again",
        "tool_calls": [{"id": "x", "function": {"name": "t", "arguments": "{}"}}],
    })
looping = router.call_llm_agentic("reviewer", [{"role": "user", "content": "hi"}],
                                  system="sys", tools=[], executor=_spy, max_rounds=3)
check("a tool-looping model is capped and forced to answer in text",
      _rounds["grok"] == 3 and "Final answer." in looping, f"rounds={_rounds['grok']}")


def _raiser(name, args):
    raise RuntimeError("executor exploded")


_grok_script.clear()
_grok_script.append({
    "role": "assistant", "content": "trying",
    "tool_calls": [{"id": "c", "function": {"name": "t", "arguments": "{}"}}],
})
safe = router.call_llm_agentic("reviewer", [{"role": "user", "content": "hi"}],
                               system="sys", tools=[], executor=_raiser, max_rounds=2)
check("an executor that raises does not kill the turn", "Final answer." in safe, safe)

_grok_script.clear()
_grok_script.append({
    "role": "assistant", "content": "hm",
    "tool_calls": [{"id": "c", "function": {"name": "t", "arguments": "not json at all"}}],
})
calls_seen.clear()
router.call_llm_agentic("reviewer", [{"role": "user", "content": "hi"}],
                        system="sys", tools=[], executor=_spy, max_rounds=2)
check("unparseable tool arguments degrade to a dict rather than crashing",
      calls_seen and isinstance(calls_seen[0][1], dict), str(calls_seen))


# ── Chat, with the model stubbed ──────────────────────────────────────────────

section("Agent chat")
_grok_script.clear()
_ollama_script.clear()

_ollama_script.append({
    "role": "assistant", "content": "",
    "tool_calls": [{"function": {"name": "search_library",
                                 "arguments": {"query": "unity"}}}],
})
result = colony_chat.chat("librarian", "Find me something on unity.")
check("chat returns a reply", bool(result["reply"]))
check("chat history is stored", len(colony.get_agent_messages("librarian")) == 2)

# The ground-truth footer: a queued action must be visible even if the model
# never mentions it.
_grok_script.clear()
_grok_script.append({
    "role": "assistant", "content": "All set!",   # the model claims success
    "tool_calls": [{"id": "g1", "function": {
        "name": "generate_image", "arguments": '{"prompt": "dawn over a garden"}'}}],
})
res = colony_chat.chat("artist", "Make me a picture of dawn.")
check("a queued action is appended to the reply in code, not left to the model",
      "Waiting for your approval" in res["reply"], res["reply"])
check("the queued action is returned to the dashboard too", len(res["queued"]) == 1)

colony.clear_agent_messages("librarian")
check("clearing history works", colony.get_agent_messages("librarian") == [])


# ── Approving and declining ───────────────────────────────────────────────────

section("The approval queue")
pending = colony.list_actions("pending")
check("actions are pending until resolved", len(pending) >= 1)
action_id = pending[0]["id"]
colony.resolve_action(action_id, "declined", "Declined by Sheraj")
check("a declined action leaves the pending queue",
      colony.get_action(action_id)["status"] == "declined")
check("declining does not delete the record (the history stays honest)",
      colony.get_action(action_id) is not None)

# Running an approved action means reading the provider's REAL return shape.
# These stubs return exactly what agents/translator.translate_quote and
# agents/artist.generate_image actually return — a live run once reported a
# confident, EMPTY "Translated:" because the code read result["translation"]
# when the key is "text". A wrong key must fail here, not in front of Sheraj.
import agents.translator as _translator  # noqa: E402
import agents.artist as _artist  # noqa: E402

_translator.translate_quote = lambda q, lang: {
    "code": lang, "name": "Spanish", "native_name": "Espanol", "rtl": False,
    "text": "La tierra es un solo pais.",
    "disclaimer_native": "Traduccion asistida por IA.",
    "disclaimer_en": "AI-assisted translation, unofficial.",
}
_artist.generate_image = lambda prompt, aspect_ratio="2:3": {
    "image_url": "outputs/test.png", "remote_url": None, "model": "test",
}

outcome = colony_tools.run_approved_action({
    "kind": "translate_quote",
    "payload": json.dumps({"quote": "The earth is but one country.", "language": "es"}),
})
check("an approved translation reports the actual translated text",
      "La tierra es un solo pais." in outcome, outcome)
check("a translation is never surfaced without its AI-assisted label (rule 8)",
      "AI-assisted translation" in outcome, outcome)

outcome = colony_tools.run_approved_action({
    "kind": "generate_image",
    "payload": json.dumps({"prompt": "a star", "aspect_ratio": "2:3"}),
})
check("an approved image generation reports the saved file path",
      "outputs/test.png" in outcome, outcome)

try:
    colony_tools.run_approved_action({"kind": "nonsense", "payload": "{}"})
    check("an unknown action kind raises rather than silently doing nothing", False)
except ValueError:
    check("an unknown action kind raises rather than silently doing nothing", True)


# ── Per-agent model selection ────────────────────────────────────────────────

section("Per-agent model selection")
import agents.models as models  # noqa: E402

# Stub both providers' discovery so the suite stays offline and deterministic.
_FAKE_OLLAMA = [
    {"id": "qwen3-16k:latest", "provider": "ollama", "label": "qwen3-16k:latest",
     "paid": False, "note": "5.2GB local"},
    {"id": "llama3.1:8b", "provider": "ollama", "label": "llama3.1:8b",
     "paid": False, "note": "4.9GB local"},
]
_FAKE_XAI = [{"id": "grok-4.6", "provider": "xai", "label": "grok-4.6",
              "paid": True, "note": "paid API"}]
_FAKE_CLAUDE = [{"id": "claude-opus-5", "provider": "anthropic", "label": "Claude Opus 5",
                 "paid": True, "note": "paid API"}]

_reachable = {"ollama": True, "xai": True, "anthropic": True}
models._LISTERS = {
    "ollama": lambda: (_FAKE_OLLAMA, _reachable["ollama"]),
    "xai": lambda: (_FAKE_XAI, _reachable["xai"]),
    "anthropic": lambda: (_FAKE_CLAUDE, _reachable["anthropic"]),
}

check("a workforce agent is offered local and Grok models, never Claude",
      {m["provider"] for m in models.list_models("scribe")["models"]} == {"ollama", "xai"})
check("Abigail is offered Claude models and nothing else",
      {m["provider"] for m in models.list_models("secretary")["models"]} == {"anthropic"})

# Rule 16, enforced in code rather than by what the dropdown happens to list.
try:
    models.validate_choice("scribe", "claude-opus-5")
    check("a workforce agent CANNOT be put on Claude (rule 16)", False, "it was allowed")
except ValueError as e:
    check("a workforce agent CANNOT be put on Claude (rule 16)", "Abigail's alone" in str(e), str(e))
try:
    models.validate_choice("secretary", "grok-4.6")
    check("Abigail CANNOT be moved off Claude (rule 16)", False, "it was allowed")
except ValueError:
    check("Abigail CANNOT be moved off Claude (rule 16)", True)
try:
    models.validate_choice("scribe", "no-such-model")
    check("an unknown model id is refused", False, "it was allowed")
except ValueError:
    check("an unknown model id is refused", True)
check("a valid choice returns its provider",
      models.validate_choice("scribe", "llama3.1:8b") == "ollama")

# The whole feature must be inert until it is used.
colony.set_agent_settings("scribe", model="")
check("no override = today's routing for a local agent",
      models.resolve("scribe", "scribe") == ("ollama", router.OLLAMA_MODEL, ""))
check("no override = today's routing for a Grok agent",
      models.resolve("reviewer", "reviewer") == ("xai", router.XAI_MODEL, ""))
check("no agent at all = today's routing",
      models.resolve("reviewer", None)[:2] == ("xai", router.XAI_MODEL))

colony.set_agent_settings("scribe", model="grok-4.6")
check("an override moves the agent to the chosen model AND provider",
      models.resolve("scribe", "scribe")[:2] == ("xai", "grok-4.6"))
check("the override does not leak to another agent",
      models.resolve("librarian", "librarian")[:2] == ("ollama", router.OLLAMA_MODEL))
check("saving instructions does not clear a chosen model",
      (colony.set_agent_settings("scribe", custom_instructions="be brief")
       ["model"]) == "grok-4.6")
check("saving a pause does not clear a chosen model",
      (colony.set_agent_settings("scribe", paused=False)["model"]) == "grok-4.6")

# A model that genuinely went away falls back, and SAYS SO.
colony.set_agent_settings("scribe", model="qwen3-16k:latest")
_FAKE_OLLAMA.pop(0)
provider, model, note = models.resolve("scribe", "scribe")
check("a model that no longer exists falls back to the default",
      (provider, model) == ("ollama", router.OLLAMA_MODEL))
check("the fallback is REPORTED, never silent", "no longer available" in note, note)

# ...but an unreachable provider is NOT evidence the model is gone. Treating a
# stopped Ollama as "that model vanished" would silently move every local agent
# onto a different model the moment the service blipped.
_reachable["ollama"] = False
_reachable["xai"] = False
_reachable["anthropic"] = False
provider, model, note = models.resolve("scribe", "scribe")
check("an unreachable provider does NOT trigger a fallback",
      model == "qwen3-16k:latest" and note == "", f"{model!r} {note!r}")
_reachable.update({"ollama": True, "xai": True, "anthropic": True})
_FAKE_OLLAMA.insert(0, {"id": "qwen3-16k:latest", "provider": "ollama",
                        "label": "qwen3-16k:latest", "paid": False, "note": "5.2GB local"})

check("Abigail's default is Claude, not the local model",
      models.default_for_agent("secretary", "copy")[0] == "anthropic")
check("a workforce agent's default still comes from its task type",
      models.default_for_agent("scribe", "reviewer") == ("xai", router.XAI_MODEL))

# The router must actually SEND the resolved model, not just compute it.
sent: dict = {}
_real_ollama, _real_grok = router._call_ollama, router._call_grok
router._call_ollama = lambda *a, **k: (sent.update(k | {"provider": "ollama"}), "ok")[1]
router._call_grok = lambda *a, **k: (sent.update(k | {"provider": "xai"}), "ok")[1]
colony.set_agent_settings("librarian", model="llama3.1:8b")
router.call_llm("librarian", [{"role": "user", "content": "x"}], agent="librarian")
check("the chosen local model reaches the Ollama call",
      sent.get("model") == "llama3.1:8b" and sent.get("provider") == "ollama", str(sent))
sent.clear()
colony.set_agent_settings("librarian", model="grok-4.6")
router.call_llm("librarian", [{"role": "user", "content": "x"}], agent="librarian")
check("choosing a Grok model switches PROVIDER, not just the model name",
      sent.get("model") == "grok-4.6" and sent.get("provider") == "xai", str(sent))
sent.clear()
router.call_llm("librarian", [{"role": "user", "content": "x"}])
check("a call with no agent is unaffected by anyone's override",
      sent.get("model") == router.OLLAMA_MODEL, str(sent))
router._call_ollama, router._call_grok = _real_ollama, _real_grok
colony.set_agent_settings("librarian", model="")
colony.set_agent_settings("scribe", model="")


# ── HTTP surface ──────────────────────────────────────────────────────────────

section("HTTP surface (TestClient)")
from fastapi.testclient import TestClient  # noqa: E402

import agents.api as api  # noqa: E402

client = TestClient(api.app)

r = client.get("/colony")
check("GET /colony returns 200", r.status_code == 200, r.text[:200])
snap = r.json()
check("the snapshot carries agents, teams and edges",
      snap["agents"] and snap["teams"] and isinstance(snap["edges"], list))
check("teams carry their active goals",
      any(t["active_goals"] for t in snap["teams"] if t["id"] == "print_studio"))
check("the snapshot marks who can be chatted with",
      next(a for a in snap["agents"] if a["name"] == "secretary")["chattable"] is False)

r = client.get("/colony/agents/artist")
check("GET /colony/agents/{agent} returns 200", r.status_code == 200, r.text[:200])
check("agent detail carries the handoff edges for that agent",
      "hands_to" in r.json() and "receives_from" in r.json())
check("GET an unknown agent is a 404",
      client.get("/colony/agents/nobody").status_code == 404)

r = client.post("/colony/agents/scribe/settings",
                json={"custom_instructions": "Keep it plain."})
check("POST settings returns 200", r.status_code == 200, r.text[:200])
check("POST settings persists", r.json()["custom_instructions"] == "Keep it plain.")
check("an instrument rejects settings (it has no prompt of its own)",
      client.post("/colony/agents/compositor/settings",
                  json={"custom_instructions": "x"}).status_code == 400)

r = client.post("/colony/goals", json={"team": "ledger", "goal": "Stay under $20/mo"})
check("POST /colony/goals returns 200", r.status_code == 200, r.text[:200])
new_goal_id = r.json()["id"]
check("POST an unknown team is a 422",
      client.post("/colony/goals", json={"team": "nope", "goal": "x"}).status_code == 422)
check("PATCH a goal to done works",
      client.patch(f"/colony/goals/{new_goal_id}", json={"status": "done"}).status_code == 200)
check("a completed goal stops steering", colony.goal_note_for_agent("steward") == "")
check("PATCH an unknown goal is a 404",
      client.patch("/colony/goals/999999", json={"status": "done"}).status_code == 404)
check("launching a steering-only team's goal is refused with a reason",
      client.post(f"/colony/goals/{new_goal_id}/launch", json={}).status_code == 400)
check("DELETE a goal returns 200",
      client.delete(f"/colony/goals/{new_goal_id}").status_code == 200)

r = client.get("/colony/handoffs?days=30")
check("GET /colony/handoffs returns 200", r.status_code == 200, r.text[:200])
check("handoffs carry the recent run log", "recent_runs" in r.json())

r = client.get("/colony/actions")
check("GET /colony/actions returns 200", r.status_code == 200, r.text[:200])
check("resolving an unknown action is a 404",
      client.post("/colony/actions/999999").status_code == 404)
check("resolving an already-resolved action is a 400",
      client.post(f"/colony/actions/{action_id}").status_code == 400)

r = client.get("/colony/models?agent=scribe")
check("GET /colony/models returns 200", r.status_code == 200, r.text[:200])
check("the model list reports provider reachability",
      "reachable" in r.json() and "models" in r.json())
check("a workforce agent's list never contains a Claude model",
      all(m["provider"] != "anthropic" for m in r.json()["models"]))
check("saving a Claude model on a workforce agent is a 422",
      client.post("/colony/agents/scribe/settings",
                  json={"model": "claude-opus-5"}).status_code == 422)
check("saving a Grok model on Abigail is a 422",
      client.post("/colony/agents/secretary/settings",
                  json={"model": "grok-4.6"}).status_code == 422)
check("Abigail's instructions cannot be written from the Colony (they live in "
      "her private store)",
      client.post("/colony/agents/secretary/settings",
                  json={"custom_instructions": "x"}).status_code == 400)
# Paid means "not on this computer". Testing only for xAI once labelled her
# Claude default as FREE — a lie about money in the place used to check it.
sec = client.get("/colony/models?agent=secretary").json()
check("a Claude default is reported as PAID, not free",
      sec["default_paid"] is True, str(sec.get("default_provider")))
check("a local default is reported as free",
      client.get("/colony/models?agent=librarian").json()["default_paid"] is False)
check("the Reviewer is flagged as using the separate paid vision path",
      client.get("/colony/models?agent=reviewer").json()["uses_vision"] is True)

check("consulting an unknown team is a 404",
      client.post("/colony/teams/nope/consult", json={"question": "x"}).status_code == 404)
check("an empty consultation question is a 422",
      client.post("/colony/teams/print_studio/consult",
                  json={"question": "  "}).status_code == 422)
check("chatting with the Secretary through the Colony is refused",
      client.post("/colony/agents/secretary/chat",
                  json={"message": "hi"}).status_code == 400)


# ── Launching a goal into a real pipeline ─────────────────────────────────────

section("Goal launch reuses the real pipelines")
launched: dict = {}
_real_start_job = api._start_job


def _fake_start_job(kind, runner, started_by="sheraj"):
    launched["kind"] = kind
    launched["started_by"] = started_by
    return "job123"


api._start_job = _fake_start_job
r = client.post("/colony/goals", json={"team": "print_studio", "goal": "Cards on service",
                                       "target_count": 5})
gid = r.json()["id"]
r = client.post(f"/colony/goals/{gid}/launch", json={"kind": "quote_card"})
check("launching a card goal starts the REAL card pipeline job",
      r.status_code == 200 and launched.get("kind") == "card-pipeline",
      f"{r.status_code} {r.text[:200]} {launched}")
check("the launched job id is recorded against the goal",
      colony.get_goal(gid)["launched_job_id"] == "job123")
check("a goal-launched run is attributed to the Colony, so the Pipeline tab can "
      "adopt it and say where it came from",
      launched.get("started_by") == "colony", str(launched))
r = client.post(f"/colony/goals/{gid}/launch", json={"kind": "bookmark"})
check("launching a bookmark goal starts the REAL bookmark pipeline job",
      r.status_code == 200 and launched.get("kind") == "full-pipeline", r.text[:200])
check("a kind the team cannot run is a 422",
      client.post(f"/colony/goals/{gid}/launch", json={"kind": "video"}).status_code == 422)

r = client.post("/colony/goals", json={"team": "film_crew", "goal": "The Dawn-Breakers",
                                       "detail": "A short scene."})
vid_goal = r.json()["id"]
r = client.post(f"/colony/goals/{vid_goal}/launch", json={})
check("a film goal CREATES a project instead of rendering (rules 31/33)",
      r.status_code == 200 and r.json()["result"] == "project_created", r.text[:300])
check("the created video project really exists",
      video_store.get_project(r.json()["video_project_id"]) is not None)
api._start_job = _real_start_job


# ── Summary ───────────────────────────────────────────────────────────────────

print(f"\n{'=' * 62}")
print(f"  {PASS} passed, {FAIL} failed  ({PASS + FAIL} checks)")
if FAILURES:
    print("\nFailures:")
    for f in FAILURES:
        print(f"  - {f}")
print(f"{'=' * 62}")
sys.exit(1 if FAIL else 0)
