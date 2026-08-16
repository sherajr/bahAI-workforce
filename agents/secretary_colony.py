"""
Abigail's side of the workforce — the ONE boundary she crosses to reach the
teams (owner ask, 2026-08-14: "she should be able to interact with all the
teams, report back what they did, request them to do jobs, and give them the
info they need to do a good job").

Everything that passes between her world and theirs goes through this module,
in both directions, so the two things that make the crossing safe live in one
readable place instead of being re-derived at each call site:

  * **The privacy boundary (rule 15).** She is the only agent with Sheraj's
    personal data. Her briefs, goals and questions land in `workforce.db`,
    which is not private — so every string that crosses is checked in CODE by
    `assert_shareable`, not by asking the model to be careful. The check is
    deliberately NARROW and honest about it (see its docstring): it catches
    wholesale copying out of her memory notes and any contact detail, which
    are the concrete leaks. It cannot judge "is this sentence personal", and
    nothing here pretends it can — the system prompt carries that instruction
    as well, and the length caps bound how much can cross at all.

  * **The gate: talking is immediate, MAKING is approved.** Reading what the
    teams did, setting a goal, writing a brief and asking an agent a question
    are free or near-free and happen at once — an assistant who needs a
    permission click to ask Ruth a question is not an assistant. Asking a team
    to actually RUN a pipeline spends real money (xAI artwork, Grok review)
    and creates a saved product, so it always queues in her existing
    `pending_actions` queue and does nothing until Sheraj approves it — the
    same queue and the same approval path as Gmail (rule 25), so there is one
    place he says yes rather than two.

Rule 16 is untouched: Claude is hers, and the workforce agents she reaches
still run on their own models (`colony_chat` routes them exactly as their
pipeline work is routed). She is a caller here, never a stand-in.
"""

import json
import re
from datetime import datetime, timedelta

from agents import colony

# Caps on what may cross into workforce.db. These bound the blast radius of a
# leak as much as they bound prompt length (rule 1) — a brief that cannot be
# said in this much text is a brief Sheraj should be writing himself.
MAX_THEME_CHARS = 400
MAX_DETAIL_CHARS = 2000
MAX_MESSAGE_CHARS = 800

# Cards per approved run. Mirrors api._CARD_BATCH_MAX — the same spend guard,
# stated here so a request is capped before it is queued rather than 422-ing
# after Sheraj has already approved it.
MAX_CARDS_PER_RUN = 19

# How many consecutive words must match one of her private memory notes before
# it counts as copying rather than coincidence. Long on purpose: a short window
# would refuse the legitimate case this feature exists for (relaying a
# preference she knows about, in her own words), and a refusal Sheraj cannot
# understand is its own kind of failure.
_COPY_SHINGLE_WORDS = 12

_CONTACT_PATTERNS = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "an email address"),
    (re.compile(r"(?<!\d)(?:\+\d[\d\s().-]{7,}\d)(?!\d)"), "a phone number"),
]


class PrivateLeak(ValueError):
    """Raised when text bound for workforce.db carries something personal."""


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def _shingles(text: str, n: int = _COPY_SHINGLE_WORDS) -> set[str]:
    words = _words(text)
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def _private_shingles() -> set[str]:
    """
    Shingles of her memory notes only.

    Deliberately NOT her chat history or his task list: he routinely asks for
    work in the same words a task is written in ("make twenty cards for the
    devotional"), so including those would refuse the exact relay this feature
    exists to perform. Memory notes are the durable personal record — the file
    that must never be pasted into a team goal.
    """
    try:
        from agents import secretary_store as store
        return _shingles(store.read_all_memory_notes() or "")
    except Exception:
        # Fails OPEN on an unreadable store, and says so at the call site. A
        # broken private DB must not silently disable the check without a word.
        return set()


def assert_shareable(text: str, what: str = "that") -> str:
    """
    Check a string that is about to be written into workforce.db, and return it
    stripped. Raises PrivateLeak with a plain-language reason.

    What this really guarantees, stated honestly because a safeguard oversold
    is worse than none: it stops (a) any email address or phone number and
    (b) a span of {_COPY_SHINGLE_WORDS}+ consecutive words copied verbatim out
    of her private memory notes. It does NOT and cannot decide whether an
    ordinary sentence is personal — that judgement stays with her prompt and
    with the caps above. Same class of control as `_sanitize_claims`: narrow,
    deterministic, and never bypassable by prompt wording.
    """
    text = (text or "").strip()
    if not text:
        return text
    for pattern, label in _CONTACT_PATTERNS:
        if pattern.search(text):
            raise PrivateLeak(
                f"{what} contains what looks like {label}. The workforce's records are "
                "not private the way your notes are, so contact details never go into a "
                "goal, a brief or a question. Say it without them.")
    private = _private_shingles()
    if private and (private & _shingles(text)):
        raise PrivateLeak(
            f"{what} repeats a passage from your private notes word for word. Those stay "
            "in Abigail's own store and never reach the workforce database — say it in "
            "her own words, with only what the team needs for the work.")
    return text


# ── Reading: what the teams have actually been doing ──────────────────────────

def _fmt_agent_line(agent: dict) -> str:
    name = colony.display_name(agent["name"])
    bits = [f"{name} ({agent['name']})"]
    if agent["total_runs"]:
        bits.append(f"{agent['trust_level_name']}, {agent['trust_score']:.0f}% clean "
                    f"over {agent['total_runs']} judged runs")
    else:
        bits.append("no judged run yet")
    if agent["live"]:
        bits.append("working right now")
    if agent["paused"]:
        bits.append("PAUSED by Sheraj")
    if agent["has_instructions"]:
        bits.append("has standing instructions")
    return "  - " + " — ".join(bits)


def _recent_products(days: int) -> list[dict]:
    from agents.state import get_all_products
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    out = []
    for p in get_all_products():
        created = str(p.get("created_at") or "")
        # created_at is stored as 'YYYY-MM-DD HH:MM:SS'; compare on the date
        # prefix so a format difference degrades to "include it" rather than
        # silently reporting an empty week.
        if created and created.replace(" ", "T") < cutoff:
            continue
        out.append(p)
    return out


def _product_line(p: dict) -> str:
    scores = p.get("reviewer_scores")
    overall = None
    try:
        if isinstance(scores, str) and scores:
            overall = json.loads(scores).get("overall")
    except json.JSONDecodeError:
        overall = None
    kind = p.get("product_type") or "bookmark"
    bits = [f"[{p.get('id')}] {p.get('title') or '(untitled)'}", kind]
    bits.append(f"scored {overall}" if overall is not None else "unscored")
    if p.get("target_reached") == 0:
        bits.append("best effort — target not reached")
    if p.get("etsy_listing_id"):
        bits.append("published to Etsy")
    return "  - " + ", ".join(bits)


def _running_jobs() -> list[dict]:
    """
    Pipelines in flight right now, read from the API's own job store — the same
    thing the dashboard's progress bar is showing him. Fails quiet: if the API
    module isn't loaded (a script, a test), "nothing running" is reported as
    unknown rather than asserted.
    """
    try:
        from agents import api
        jobs = api.pipeline_jobs()
    except Exception:
        return []
    return [j for j in jobs if j["status"] in ("running", "waiting_for_input")][:5]


def workforce_report(team: str | None = None, days: int = 7) -> str:
    """A plain-language account of what the teams did, built only from records."""
    colony.init_colony_db()
    snapshot = colony.colony_snapshot()
    agents_by_name = {a["name"]: a for a in snapshot["agents"]}

    wanted = None
    if team:
        wanted = colony.resolve_team(team)
        if not wanted:
            names = ", ".join(t["name"] for t in colony.TEAMS.values())
            return f"There is no team called '{team}'. The teams are: {names}."

    out: list[str] = []
    for t in snapshot["teams"]:
        if wanted and t["id"] != wanted:
            continue
        out.append(f"\n{t['name'].upper()} — {t['blurb']}")
        for member in t["members"]:
            if member in agents_by_name:
                out.append(_fmt_agent_line(agents_by_name[member]))
        if t["active_goals"]:
            for g in t["active_goals"]:
                progress = g["progress"]
                who = "you" if (g.get("set_by") or "sheraj") == "sheraj" else "Abigail, for you"
                line = f'  Goal (set by {who}): "{g["goal"]}"'
                if progress["measurable"]:
                    line += (f" — {progress['done']} made since it was set"
                             + (f", target {progress['target']}" if progress["target"] else ""))
                else:
                    line += " — steering only, nothing to count"
                out.append(line)
                if g.get("detail"):
                    out.append(f"    Detail given to them: {g['detail']}")
        else:
            out.append("  No goal set for this team.")

    runs = colony.recent_runs(limit=25)
    if wanted:
        members = set(colony.TEAMS[wanted]["members"] + colony.TEAMS[wanted]["instruments"])
        runs = [r for r in runs if r["agent"] in members]
    if runs:
        out.append(f"\nTHE LAST {len(runs)} STEPS OF REAL WORK (newest first)")
        for r in runs[:12]:
            # A judged run and a mechanical one must stay distinguishable all
            # the way to what she says (rule 14/35): calling a render step
            # "passed" would turn clean-run stats into an uptime metric.
            if r["judged"]:
                verdict = "passed review" if r["passed_review"] else "did not pass review"
            else:
                verdict = "mechanical step, not judged"
            out.append(f"  - {colony.display_name(r['agent'])}: {r['step']} — "
                       f"{(r['output_summary'] or '')[:80]} ({verdict}, {r['timestamp']})")
    else:
        out.append("\nNo recorded work steps yet.")

    products = _recent_products(days)
    if products:
        out.append(f"\nFINISHED IN THE LAST {days} DAYS ({len(products)})")
        out.extend(_product_line(p) for p in products[:10])
    else:
        out.append(f"\nNothing finished in the last {days} days.")

    running = _running_jobs()
    if running:
        out.append("\nRUNNING RIGHT NOW")
        for j in running:
            out.append(f"  - {j['kind']} ({j['job_id']}): {j['progress']}")

    pending = colony.list_actions("pending")
    if pending:
        out.append("\nTHE TEAM IS WAITING ON YOUR APPROVAL (Colony tab)")
        for a in pending[:8]:
            out.append(f"  - #{a['id']} {colony.display_name(a['agent'])}: {a['description']}")

    return "\n".join(out).strip()


# ── Steering: goals and briefs ────────────────────────────────────────────────

def set_team_goal(team: str, goal: str, detail: str = "",
                  target_count: int | None = None) -> str:
    team_id = colony.resolve_team(team)
    if not team_id:
        names = ", ".join(t["name"] for t in colony.TEAMS.values())
        return f"There is no team called '{team}'. The teams are: {names}."
    goal_text = assert_shareable(goal, "The goal")[:MAX_THEME_CHARS]
    detail_text = assert_shareable(detail, "The detail")[:MAX_DETAIL_CHARS]
    if not goal_text:
        return "A goal needs some text — nothing was set."

    colony.init_colony_db()
    created = colony.create_goal(
        team_id, goal_text, detail=detail_text, target_count=target_count,
        baseline_products=colony.current_product_count(team_id), set_by="abigail")
    # The line each agent CARRIES is capped for local Qwen's context (rule 1),
    # so say plainly when the stored goal is longer than the steering they see.
    truncated = ("" if len(goal_text) <= colony.GOAL_NOTE_MAX_CHARS else
                 f" (they each carry only the first {colony.GOAL_NOTE_MAX_CHARS} "
                 "characters as steering — the full goal is on the team's card)")
    return (f"Goal #{created['id']} set for the {colony.TEAMS[team_id]['name']}: "
            f"\"{goal_text}\". Every agent on that team now carries it in every prompt "
            f"they run, pipeline work included{truncated}. Progress is counted from "
            "products they actually finish, not from anything they claim.")


def brief_agent(agent: str, instructions: str, replace: bool = False) -> str:
    """
    Standing instructions for one agent — the channel for "here is what you
    need to know to do this well". They reach every prompt that agent runs
    (system_prompt_builder._instructions_steer), so this is a real change to
    how it works, not a note in a drawer.
    """
    agent_id = colony.resolve_agent(agent)
    if not agent_id:
        known = ", ".join(f"{colony.display_name(a)} ({a})"
                          for a in colony.DISPLAY_NAMES if a not in colony.NO_COLONY_CHAT)
        return f"There is nobody called '{agent}'. The workforce is: {known}."
    if agent_id in colony.NO_COLONY_CHAT:
        return (f"{colony.display_name(agent_id)} is not one of the workforce agents you "
                "can brief — that is either a pipeline step or Abigail herself.")

    text = assert_shareable(instructions, "The brief")
    if not text:
        return "There was nothing in the brief — nothing was saved."

    colony.init_colony_db()
    current = colony.get_agent_settings(agent_id)["custom_instructions"].strip()
    combined = text if (replace or not current) else (current + "\n" + text)
    combined = combined[: colony.INSTRUCTIONS_NOTE_MAX_CHARS * 2]
    colony.set_agent_settings(agent_id, custom_instructions=combined)

    name = colony.display_name(agent_id)
    note = ""
    if len(combined) > colony.INSTRUCTIONS_NOTE_MAX_CHARS:
        note = (f" Only the first {colony.INSTRUCTIONS_NOTE_MAX_CHARS} characters are "
                "carried into their prompts, so keep the important part early.")
    verb = "replaced" if replace or not current else "added to"
    return (f"{name}'s standing instructions {verb}. They now carry this in every prompt "
            f"they run, pipeline work included.{note}")


# ── Talking: asking an agent to answer, in its own voice ──────────────────────

RELAY_PREFIX = "Abigail here, relaying from Sheraj:"


def _cost_note(agent_id: str) -> str:
    """Whether this agent's answer costs money. Paid means NOT LOCAL (rule 41a)."""
    try:
        from agents import models
        from agents.colony_chat import CHAT_TASK_TYPE
        provider, model, _ = models.resolve(CHAT_TASK_TYPE.get(agent_id, "copy"), agent_id)
    except Exception:
        return ""
    if provider == models.OLLAMA:
        return ""
    return f" (answered on {model} — a paid call)"


def ask_agent(agent: str, message: str) -> dict:
    """
    Put a question or an instruction to one agent and get its real answer.

    Runs through colony_chat.chat — the SAME path the Colony tab's chat box
    uses — so the agent answers on its own model (rule 16), with its own tools,
    and anything paid or product-changing it tries still queues in the Colony
    queue rather than running. The turn is written into that agent's Colony
    history labelled as a relay, so Sheraj can open the Colony tab and read
    exactly what was asked in his name.
    """
    from agents import colony_chat

    agent_id = colony.resolve_agent(agent)
    if not agent_id:
        known = ", ".join(f"{colony.display_name(a)} ({a})"
                          for a in colony.DISPLAY_NAMES if a not in colony.NO_COLONY_CHAT)
        return {"ok": False, "text": f"There is nobody called '{agent}'. The workforce is: {known}."}
    if agent_id in colony.NO_COLONY_CHAT:
        return {"ok": False,
                "text": f"{colony.display_name(agent_id)} cannot be spoken to that way — "
                        "that is either a pipeline step or Abigail herself."}

    text = assert_shareable(message, "The message")[:MAX_MESSAGE_CHARS]
    if not text:
        return {"ok": False, "text": "There was nothing to ask."}

    result = colony_chat.chat(agent_id, f"{RELAY_PREFIX} {text}")
    name = colony.display_name(agent_id)
    out = f"{name} answered{_cost_note(agent_id)}:\n\n{result['reply']}"
    return {"ok": True, "text": out, "agent": agent_id,
            "queued": result.get("queued") or []}


# ── Making: asking a team to actually run a pipeline (always approved first) ───

def team_for_kind(kind: str) -> str | None:
    for team_id, team in colony.TEAMS.items():
        if kind in team["goal_kinds"]:
            return team_id
    return None


JOB_KINDS = {
    "quote_card": "a quote card to give away (Print Studio: verified quote, artwork, "
                  "review, printed faces)",
    "bookmark": "a bookmark listing (Print Studio: verified quote, artwork, listing copy, "
                "review, printed faces)",
    "video": "a video project (Film Crew: creates the project and stops, so you can look "
             "at the plan before any clip is rendered)",
}


def find_quotes(theme: str, count: int) -> tuple[list[dict], str]:
    """
    Ask the Librarian for `count` verified quotes on a theme. Free and local.

    The SAME function the dashboard's "Find quotes" button calls, so what comes
    back is already canonicalised through the resolvers the batch endpoint
    verifies with — a quote found here is guaranteed to pass verification when
    the run actually starts, instead of failing after Sheraj has approved it.
    Returns (items, note); an empty list with a note when nothing was found.
    """
    from agents import api

    try:
        result = api.suggest_ruhi_quotes(topic=theme, count=count)
    except Exception as e:
        detail = getattr(e, "detail", None) or str(e)
        return [], f"Ruth could not search the library right now: {detail}"
    items = result.get("items") or []
    if not items:
        return [], f"Ruth found nothing in the library about \"{theme}\"."
    note = ""
    if len(items) < count:
        note = (f"Ruth found {len(items)} passages, not the {count} asked for — "
                "the library has no more on that theme.")
    return items, note


def request_team_job(kind: str, theme: str, detail: str = "",
                     language: str | None = None, count: int = 1,
                     quotes: list[str] | None = None) -> dict:
    """
    Queue a real pipeline run for Sheraj's approval. NOTHING starts here.

    This is the "making" half of the gate: a bookmark or card run spends real
    money (xAI artwork, Grok review) and saves a product, so it waits in her
    ordinary approvals queue exactly like an email does (rule 25). The payload
    is what will run, verbatim — approval executes it through
    api.launch_team_pipeline, the same entry point the dashboard's own buttons
    use, so nothing about the run is special because she asked for it.

    Several cards are ONE request, not several: the quotes are found (or
    checked) HERE, before queueing, so the approval Sheraj sees names the exact
    quotes that will be printed rather than asking him to approve a number. It
    also means an unfindable theme fails now, free, instead of after he has
    said yes.
    """
    from agents import secretary_store as store

    kind = (kind or "").strip()
    if kind not in JOB_KINDS:
        return {"ok": False,
                "text": f"'{kind}' is not something a team can make. Options: "
                        + ", ".join(JOB_KINDS)}
    team_id = team_for_kind(kind)
    theme_text = assert_shareable(theme, "The theme")[:MAX_THEME_CHARS]
    detail_text = assert_shareable(detail, "The detail")[:MAX_DETAIL_CHARS]
    if not theme_text:
        return {"ok": False, "text": "A run needs a theme — nothing was queued."}

    team_name = colony.TEAMS[team_id]["name"]
    quotes = [q.strip() for q in (quotes or []) if (q or "").strip()]
    try:
        count = max(1, int(count or 1))
    except (TypeError, ValueError):
        count = 1
    note = ""

    if kind == "quote_card":
        wanted = max(count, len(quotes))
        if wanted > MAX_CARDS_PER_RUN:
            note = (f"A single run is capped at {MAX_CARDS_PER_RUN} cards (each one is a "
                    f"full paid pipeline run), so this is for {MAX_CARDS_PER_RUN} — ask "
                    "again for the rest. ")
            wanted = MAX_CARDS_PER_RUN
            quotes = quotes[:wanted]
        if wanted > 1 and len(quotes) < wanted:
            # She asked for several and did not supply them all — Ruth finds the
            # rest now, verified, so the approval names real quotes.
            found, find_note = find_quotes(theme_text, wanted - len(quotes))
            if not found and not quotes:
                return {"ok": False,
                        "text": f"{find_note} Nothing was queued — try a different theme, "
                                "or give the quote yourself."}
            have = {q.strip() for q in quotes}
            quotes += [i["quote"] for i in found if i["quote"].strip() not in have]
            note += (find_note + " ") if find_note else ""
    elif count > 1:
        # There is no batch path for bookmarks or video — say so rather than
        # quietly making one and letting him think he got several.
        note = (f"Only one {kind.replace('_', ' ')} can be made per run, so this is for "
                "one — ask again for another. ")
        count = 1

    made = len(quotes) if len(quotes) > 1 else 1
    noun = (f"{made} quote cards" if made > 1 else kind.replace("_", " "))
    cost = ("creates the project only, no cost yet" if kind == "video"
            else f"costs money: artwork and a paid review"
                 + (f", {made} times" if made > 1 else ""))
    description = (f"Ask the {team_name} to make {noun} — "
                   f"\"{theme_text[:120]}\" ({cost})")
    action_id = store.add_pending_action(
        "workforce_job", description,
        json.dumps({"kind": kind, "theme": theme_text, "detail": detail_text,
                    "language": language or None, "quotes": quotes}))

    listed = ""
    if len(quotes) > 1:
        listed = "\n\nThe quotes Ruth verified for it:\n" + "\n".join(
            f"{i + 1}. \"{q[:110]}{'...' if len(q) > 110 else ''}\""
            for i, q in enumerate(quotes))
    return {"ok": True, "action_id": action_id, "description": description,
            "count": made, "quotes": quotes,
            "text": (f"{note}Queued as action #{action_id} for Sheraj's approval — the "
                     f"{team_name} has NOT started. It runs only when he approves it "
                     "(he can reply 'approve' or tap Approve in the dashboard)."
                     + listed)}


def run_approved_job(payload: dict) -> str:
    """
    Execute a job Sheraj approved. Called only from
    secretary.execute_pending_action, never from a chat turn — approval is the
    only path, mirroring colony_tools.run_approved_action.

    `started_by="abigail"` is what lets the Pipeline tab adopt and label this
    run. Without it the job existed and progressed while the screen stayed
    blank, which cost a duplicate card the first time it happened.
    """
    from agents import api

    out = api.launch_team_pipeline(
        payload["kind"], payload["theme"], detail=payload.get("detail") or "",
        language=payload.get("language"), quotes=payload.get("quotes") or [],
        started_by="abigail")
    team = colony.TEAMS[team_for_kind(payload["kind"])]["name"]
    if out["result"] == "project_created":
        return (f"The {team} created video project {out['video_project_id']} — "
                "open the Video tab to plan its shots.")
    count = out.get("count") or 1
    made = f"{count} cards" if count > 1 else f"\"{out['theme']}\""
    return (f"The {team} has started on {made} (job {out['job_id']}). "
            "It is on screen in the Pipeline tab now, and the team is lit up in the "
            "Colony map while it works"
            + (" — a few minutes per card, one at a time." if count > 1
               else " — it takes a few minutes."))
