"""
Talking to the workforce: one-to-one chat with an agent, and consultation with
a whole team.

Routing (hard rule 16): these agents run on THEIR OWN models — Grok for the
Artist and Reviewer, local Ollama for everyone else, exactly as their pipeline
work does. Claude belongs to the Secretary alone and is never borrowed here.
That is also why the Secretary herself is not chattable through this module
(colony.NO_COLONY_CHAT): routing her here would downgrade her model AND write
her turns into workforce.db instead of her private store (rule 15).

Every action an agent takes in chat is a real tool call through
router.call_llm_agentic (rule 22's reasoning, applied to the workforce), and
every paid or product-changing tool is gated behind Sheraj's approval by
colony_tools, not by prompt wording.
"""

from agents import colony, colony_tools
from agents.router import call_llm, call_llm_agentic
from agents.state import TRUST_LEVELS, get_agent_status
from agents.system_prompt_builder import build_system_prompt

# Which task_type each agent's chat routes through. These are the SAME task
# types their pipeline work uses, so an agent's chat runs on the same model as
# its real work — Theo and Amos on paid Grok (metered), everyone else local and
# free. Changing one of these changes what that agent costs to talk to.
CHAT_TASK_TYPE = {
    "librarian":    "cite",
    "artist":       "creative_writing",   # → Grok (paid)
    "scribe":       "copy",
    "reviewer":     "reviewer",           # → Grok (paid)
    "steward":      "steward",
    "translator":   "copy",
    "director":     "video_direction",
    "videographer": "video_direction",
}

# The constitution-principle subset each agent carries into chat, matching
# system_prompt_builder.TASK_PRINCIPLES keys.
CHAT_PRINCIPLE_TYPE = {
    "librarian": "cite", "artist": "design", "scribe": "copy",
    "reviewer": "review", "steward": "steward", "translator": "copy",
    "director": "design", "videographer": "design",
}

HISTORY_WINDOW = 12

_CHAT_FRAME = """## You are talking with Sheraj directly

He owns this workshop and is talking to you in the dashboard's Colony tab, not
running a pipeline. Speak plainly and in the first person, as yourself. Keep it
to a few short paragraphs unless he asks for more.

Honesty rules that outrank being helpful:
- Never invent a quotation, a citation, a score, a number or a file. If you
  need a real one, use one of your tools; if a tool finds nothing, say so.
- Your tools are the ONLY way you can affect anything. Describing an action is
  not doing it. If you did not call the tool, say what you would do instead of
  implying it happened.
- Some of your tools cost money or change a saved product. Those are queued for
  his approval rather than run. When one is queued, tell him it is waiting —
  never report it as finished.
"""


def _agent_context(agent: str) -> str:
    """The short, factual block of what this agent knows about its own standing."""
    status = get_agent_status(agent) or {}
    lines = []
    if status.get("total_runs"):
        lines.append(
            f"Your trust level is {TRUST_LEVELS.get(status.get('trust_level', 0), '?')} "
            f"({status.get('trust_score', 0):.0f}% clean across "
            f"{status.get('total_runs')} judged runs).")
    else:
        lines.append("You have not completed a judged run yet.")

    team_id = colony.AGENT_TEAM.get(agent)
    if team_id:
        team = colony.TEAMS[team_id]
        mates = [m for m in team["members"] if m != agent]
        lines.append(f"You are on the {team['name']} team"
                     + (f", alongside {', '.join(mates)}." if mates else "."))

    # Neither the team goal NOR the standing instructions are added here —
    # build_system_prompt injects both for every agent prompt in the codebase
    # (see its _goal_steer / _instructions_steer). Repeating either would state
    # the same thing twice in one prompt.
    return "\n".join(lines)


def build_agent_system_prompt(agent: str) -> str:
    return build_system_prompt(
        agent,
        CHAT_PRINCIPLE_TYPE.get(agent, "all"),
        extra_context=_CHAT_FRAME + "\n\n## What you know\n\n" + _agent_context(agent),
    )


def chat(agent: str, message: str) -> dict:
    """
    One chat turn with one agent. Returns {reply, queued, used, agent}.

    `queued` lists anything the turn put in front of Sheraj for approval — the
    dashboard shows those as real pending rows, so a queued action is visible
    whether or not the agent's own words mention it.
    """
    colony.init_colony_db()
    if agent in colony.NO_COLONY_CHAT:
        raise ValueError(f"{agent} is not chattable from the Colony")
    if agent not in colony.AGENT_TEAM:
        raise ValueError(f"Unknown agent '{agent}'")
    if not message.strip():
        raise ValueError("Nothing to say")

    if colony.get_agent_settings(agent)["paused"]:
        raise ValueError(f"{agent} is paused — un-pause them in their settings first")

    colony.add_agent_message(agent, "user", message)
    history = colony.get_agent_messages(agent, HISTORY_WINDOW)
    messages = [{"role": m["role"], "content": m["content"]} for m in history]

    effects: dict = {"queued": [], "used": []}
    executor = colony_tools.make_executor(agent, effects)
    tools = colony_tools.tools_for(agent)
    task_type = CHAT_TASK_TYPE.get(agent, "copy")

    if tools:
        reply = call_llm_agentic(
            task_type, messages, system=build_agent_system_prompt(agent),
            tools=tools, executor=executor, agent=agent,
            max_tokens=1200, max_rounds=4)
    else:
        reply = call_llm(
            task_type,
            [{"role": "system", "content": build_agent_system_prompt(agent)}] + messages,
            agent=agent, max_tokens=1200)

    reply = (reply or "").strip() or "I don't have an answer for that one."

    # Ground truth wins over the model's narration: if the turn queued
    # something, say so in code. The model may or may not have mentioned it,
    # and a silently-queued approval row is exactly the kind of thing that
    # vanishes without the owner noticing.
    if effects["queued"]:
        lines = "\n".join(f"• #{q['id']} — {q['description']}" for q in effects["queued"])
        reply += ("\n\n---\nWaiting for your approval before anything actually runs:\n"
                  + lines)

    colony.add_agent_message(agent, "assistant", reply)
    return {"agent": agent, "reply": reply,
            "queued": effects["queued"], "used": effects["used"]}


# ── Team consultation ─────────────────────────────────────────────────────────

_CONSULT_FRAME = """## A team consultation

Sheraj has put a question to your whole team and wants each of you to speak in
turn. This is consultation in the Bahá'í sense: offer your view frankly, as
your own, then let it belong to the group rather than defending it.

Speak as yourself, in the first person, in 2-4 short sentences. Say the thing
only you would notice from your role. If you disagree with someone above you,
say so plainly and say why — unexamined agreement is a disservice to the work.
Do not summarise the others, do not repeat what has already been said, and do
not invent facts, quotes or numbers.
"""


def run_team_consultation(team_id: str, question: str, progress=None,
                          on_turn=None) -> dict:
    """
    Each member of a team speaks once on Sheraj's question, in turn, seeing what
    the others have already said — then the Steward closes with what it means
    for the work.

    Deliberately a SEPARATE, lean implementation rather than a reuse of
    consultation.run_consultation. That function's Librarian VERDICT block and
    its round-2 decision JSON are machine-parsed and machine-executed by the
    bookmark pipeline (rule 10); bending them to answer a free-form question
    would put the pipeline's contracts at risk for no gain. This one has no
    parsed contract at all — it is people talking.
    """
    colony.init_colony_db()
    team = colony.TEAMS.get(team_id)
    if not team:
        raise ValueError(f"Unknown team '{team_id}' — known: {sorted(colony.TEAMS)}")
    if not question.strip():
        raise ValueError("A consultation needs a question")

    members = [m for m in team["members"] if m not in colony.NO_COLONY_CHAT]
    if not members:
        raise ValueError(f"{team['name']} has nobody who can be consulted")

    turns: list[dict] = []
    for i, member in enumerate(members):
        if colony.get_agent_settings(member)["paused"]:
            continue
        if progress:
            progress(f"{member.title()} is speaking ({i + 1} of {len(members)})...")

        said_so_far = "\n\n".join(f"{t['agent'].title()} said: {t['text']}" for t in turns)
        user_block = (
            f"Sheraj asks the {team['name']} team:\n\n{question.strip()}\n\n"
            + (f"Already said in this consultation:\n\n{said_so_far}\n\n" if turns else "")
            + "Now give your own view.")

        system = build_system_prompt(
            member, CHAT_PRINCIPLE_TYPE.get(member, "all"),
            extra_context=_CONSULT_FRAME + "\n\n## What you know\n\n" + _agent_context(member))
        try:
            text = call_llm(CHAT_TASK_TYPE.get(member, "copy"),
                            [{"role": "system", "content": system},
                             {"role": "user", "content": user_block}],
                            agent=member, max_tokens=400).strip()
        except Exception as e:
            # One member failing must not lose the turns already gathered —
            # same principle as the video planner surviving a failed beat
            # (rule 33b). The gap is reported, never hidden.
            text = f"(Could not reach {member}: {type(e).__name__}: {e})"
        turn = {"agent": member, "text": text}
        turns.append(turn)
        if on_turn:
            on_turn(turn)

    if progress:
        progress("Drawing the consultation together...")
    summary = _synthesize(team, question, turns)
    return {"team": team_id, "team_name": team["name"], "question": question.strip(),
            "turns": turns, "summary": summary}


def _synthesize(team: dict, question: str, turns: list[dict]) -> str:
    """
    Close the consultation with what was actually agreed and what was not.

    Routed through the Steward's task type because the Steward's whole job is
    reporting what the numbers and the record say rather than what is pleasant
    to hear — the right voice for "here is where you actually disagreed".
    """
    if not turns:
        return "Nobody was available to consult."
    transcript = "\n\n".join(f"{t['agent'].title()}: {t['text']}" for t in turns)
    prompt = (
        f"The {team['name']} team consulted on Sheraj's question:\n\n{question}\n\n"
        f"{transcript}\n\n"
        "In at most 120 words, state: what the team agreed on, where they genuinely "
        "differed, and the single clearest next step. Do not invent agreement that "
        "isn't in the transcript — an unresolved disagreement is a useful result, so "
        "say so if that is what happened.")
    try:
        return call_llm("steward", [{"role": "user", "content": prompt}],
                        agent="steward", max_tokens=350).strip()
    except Exception as e:
        return f"(The closing summary could not be generated: {type(e).__name__}: {e})"
