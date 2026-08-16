"""
The Colony — the dashboard's view of the workforce as a living organisation.

This module owns everything the Colony tab needs that isn't already in
state.py: the TEAM groupings, per-agent settings, team goals, per-agent chat
history, and the confirm-before-acting queue. It also DERIVES the handoff
graph from data that already exists (`task_runs`), so the lines drawn between
agents on screen are real recorded work, never decoration.

Storage follows video_store.py's precedent: same workforce.db, its own
`CREATE TABLE IF NOT EXISTS` + try/except ALTER migrations, called from
state.init_db() OUTSIDE that function's own `with _connect()` block (SQLite
refuses a second writer while the outer transaction is open — see the comment
there; nesting it silently skips the tables on a fresh DB).

Nothing personal lives here (rule 15). The Secretary appears in the colony as
a NODE with her trust stats, but she is never chatted with through this
module — she has her own tab, her own Claude routing (rule 16) and her own
private store.
"""

import json
import sqlite3
from datetime import datetime, timedelta

from agents.state import DB_PATH, TRUST_LEVELS, get_all_agent_statuses

# ── Teams ─────────────────────────────────────────────────────────────────────
# Grouped BY PIPELINE (owner decision, 2026-08-13) so the handoff lines between
# clusters correspond to how work actually flows.
#
# Membership is PRIMARY membership — each agent belongs to exactly one team, so
# the graph has one unambiguous home position per node. Cross-team work (the
# Artist paints video frames as well as card art) shows up on its own, as a real
# derived handoff edge between clusters, rather than by duplicating a node.
#
# `instruments` are the non-persona rows in state.AGENT_NAMES: real entries in
# task_runs (so they carry the handoff graph) but not people — no avatar, no
# chat, no settings. Listing them keeps the graph honest: work genuinely passes
# through them.

TEAMS: dict[str, dict] = {
    "print_studio": {
        "name": "Print Studio",
        "blurb": "Bookmarks and quote cards — from a verified quote to a printed face.",
        "members": ["librarian", "artist", "scribe", "reviewer", "translator"],
        "instruments": ["consultation", "compositor"],
        "accent": "amber",
        "goal_kinds": ["quote_card", "bookmark"],
    },
    "film_crew": {
        "name": "Film Crew",
        "blurb": "A story broken into many simple shots, chained into one video.",
        "members": ["director", "videographer"],
        "instruments": [],
        "accent": "sky",
        "goal_kinds": ["video"],
    },
    "office": {
        "name": "Office",
        "blurb": "Sheraj's own desk — calendar, mail, reminders, WhatsApp.",
        "members": ["secretary"],
        "instruments": [],
        "accent": "violet",
        "goal_kinds": [],
    },
    "ledger": {
        "name": "Ledger",
        "blurb": "What things cost, what they earned, and who has earned trust.",
        "members": ["steward"],
        "instruments": [],
        "accent": "emerald",
        "goal_kinds": [],
    },
}

# agent id → team id (primary membership), built once from TEAMS.
AGENT_TEAM: dict[str, str] = {}
for _tid, _t in TEAMS.items():
    for _m in _t["members"] + _t["instruments"]:
        AGENT_TEAM[_m] = _tid

# Agents that are instruments, not people — no chat, no settings, no avatar.
INSTRUMENTS: set[str] = {m for t in TEAMS.values() for m in t["instruments"]}

# Display names, mirroring dashboard/src/lib/utils.ts's ROSTER. The backend
# still keys EVERYTHING on the lowercase ids (state.AGENT_NAMES, log_run,
# task_runs) — this map exists so prose Sheraj reads outside the dashboard
# (Abigail's reports on the teams) uses the names he sees on screen, and so he
# can say "ask Theo" rather than "ask artist". resolve() below is what keeps a
# name he uses from silently resolving to nothing.
DISPLAY_NAMES: dict[str, str] = {
    "librarian": "Ruth", "artist": "Theo", "scribe": "Clara", "reviewer": "Amos",
    "steward": "Nora", "translator": "Sofia", "secretary": "Abigail",
    "director": "Silas", "videographer": "Marta",
}

# The Secretary is a colony NODE but never a colony CHAT. Routing her through
# call_llm would put her on Qwen/Grok instead of Claude (rule 16) and would
# write her turns into workforce.db instead of her private store (rule 15).
# The dashboard sends you to her own tab instead.
NO_COLONY_CHAT: set[str] = {"secretary"} | INSTRUMENTS

# A goal note injected into a live pipeline prompt has to stay TINY — every
# non-Grok agent runs on local Qwen, whose context is the reason hard rule 1
# exists. This is the hard ceiling, enforced in code at both ends (on save and
# again on read), never a guideline.
GOAL_NOTE_MAX_CHARS = 240

# Standing instructions are the longer channel — a real brief ("quote only from
# Book 1, keep titles under six words") rather than one steering line — so the
# ceiling is higher. It is still a HARD cap for the same rule-1 reason: this
# text is appended to every prompt that agent runs, pipeline work included.
INSTRUCTIONS_NOTE_MAX_CHARS = 600

# How recently an agent must have logged a run to render as "live".
LIVE_WINDOW_MINUTES = 10


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def display_name(agent: str) -> str:
    return DISPLAY_NAMES.get(agent, agent.replace("_", " ").title())


def resolve_agent(name_or_id: str) -> str | None:
    """
    The backend id for whatever Sheraj (or Abigail relaying him) called someone:
    'artist', 'Artist', 'Theo', 'theo'. None when nobody matches — a caller must
    say so rather than guessing at a near-miss, because acting on the wrong
    agent is worse than asking which one he meant.
    """
    wanted = (name_or_id or "").strip().lower()
    if not wanted:
        return None
    if wanted in AGENT_TEAM:
        return wanted
    for agent, name in DISPLAY_NAMES.items():
        if name.lower() == wanted:
            return agent
    return None


def resolve_team(name_or_id: str) -> str | None:
    """Team id from 'print_studio', 'Print Studio', 'print studio' — or None."""
    wanted = " ".join((name_or_id or "").strip().lower().replace("_", " ").split())
    if not wanted:
        return None
    for team_id, team in TEAMS.items():
        if wanted in (team_id.replace("_", " "), team["name"].lower()):
            return team_id
    return None


def init_colony_db():
    """Create the colony's tables. Safe to call repeatedly."""
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS agent_settings (
                agent TEXT PRIMARY KEY,
                custom_instructions TEXT DEFAULT '',
                paused INTEGER DEFAULT 0,
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS team_goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team TEXT NOT NULL,
                goal TEXT NOT NULL,
                detail TEXT DEFAULT '',
                target_count INTEGER,
                status TEXT DEFAULT 'active',
                created_at TEXT DEFAULT (datetime('now')),
                completed_at TEXT,
                launched_job_id TEXT,
                baseline_products INTEGER DEFAULT 0,
                set_by TEXT DEFAULT 'sheraj'
            );

            CREATE TABLE IF NOT EXISTS agent_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                ts TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS colony_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent TEXT NOT NULL,
                kind TEXT NOT NULL,
                description TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                result TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                resolved_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_agent_messages_agent
                ON agent_messages (agent, id);
            CREATE INDEX IF NOT EXISTS idx_colony_actions_status
                ON colony_actions (status, id);
        """)
        # Migrations — state.py's pattern. CREATE TABLE IF NOT EXISTS is a
        # no-op on a table that already exists on disk, so a new column has to
        # be added explicitly or it silently never appears for existing users.
        for col in ("model TEXT DEFAULT ''",):
            try:
                conn.execute(f"ALTER TABLE agent_settings ADD COLUMN {col}")
                conn.commit()
            except Exception:
                pass  # column already exists
        # Who set a goal — Sheraj himself, or Abigail acting on his behalf. He
        # must be able to see which is which on the team card; a goal that
        # steers every agent on a team should never be of unclear origin.
        for col in ("set_by TEXT DEFAULT 'sheraj'",):
            try:
                conn.execute(f"ALTER TABLE team_goals ADD COLUMN {col}")
                conn.commit()
            except Exception:
                pass
        conn.commit()


# ── Per-agent settings ────────────────────────────────────────────────────────

def get_agent_settings(agent: str) -> dict:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM agent_settings WHERE agent = ?", (agent,)).fetchone()
    if not row:
        return {"agent": agent, "custom_instructions": "", "paused": False,
                "model": "", "updated_at": None}
    d = dict(row)
    d["paused"] = bool(d["paused"])
    # "" means "no override" — the router keeps its task-type default.
    d["model"] = d.get("model") or ""
    return d


def set_agent_settings(agent: str, custom_instructions: str | None = None,
                       paused: bool | None = None, model: str | None = None) -> dict:
    """
    Partial update — a field left as None keeps its current value. That matters
    for `model`: saving instructions must not silently clear a chosen model.
    Pass model="" to deliberately clear it back to the default routing.
    """
    current = get_agent_settings(agent)
    instructions = current["custom_instructions"] if custom_instructions is None else custom_instructions
    is_paused = current["paused"] if paused is None else bool(paused)
    chosen_model = current["model"] if model is None else model.strip()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO agent_settings (agent, custom_instructions, paused, model, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'))
               ON CONFLICT(agent) DO UPDATE SET
                 custom_instructions = excluded.custom_instructions,
                 paused = excluded.paused,
                 model = excluded.model,
                 updated_at = excluded.updated_at""",
            (agent, instructions, int(is_paused), chosen_model))
        conn.commit()
    return get_agent_settings(agent)


# ── Team goals ────────────────────────────────────────────────────────────────

def create_goal(team: str, goal: str, detail: str = "", target_count: int | None = None,
                baseline_products: int = 0, set_by: str = "sheraj") -> dict:
    if team not in TEAMS:
        raise ValueError(f"Unknown team '{team}' — known: {sorted(TEAMS)}")
    if not goal.strip():
        raise ValueError("A goal needs some text")
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO team_goals (team, goal, detail, target_count, baseline_products,
                                       set_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (team, goal.strip(), detail.strip(), target_count, baseline_products,
             (set_by or "sheraj").strip()))
        conn.commit()
        goal_id = cur.lastrowid
    return get_goal(goal_id)


def get_goal(goal_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM team_goals WHERE id = ?", (goal_id,)).fetchone()
    return dict(row) if row else None


def list_goals(team: str | None = None, status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM team_goals"
    clauses, params = [], []
    if team:
        clauses.append("team = ?")
        params.append(team)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id DESC"
    with _connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def update_goal(goal_id: int, **fields) -> dict | None:
    allowed = {"goal", "detail", "target_count", "status", "launched_job_id",
               "baseline_products"}
    sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not sets:
        return get_goal(goal_id)
    if sets.get("status") == "done":
        sets["completed_at"] = datetime.utcnow().isoformat()
    assignments = ", ".join(f"{k} = ?" for k in sets)
    with _connect() as conn:
        conn.execute(f"UPDATE team_goals SET {assignments} WHERE id = ?",
                     [*sets.values(), goal_id])
        conn.commit()
    return get_goal(goal_id)


def delete_goal(goal_id: int):
    with _connect() as conn:
        conn.execute("DELETE FROM team_goals WHERE id = ?", (goal_id,))
        conn.commit()


def active_goals_for_team(team: str) -> list[dict]:
    return list_goals(team=team, status="active")


def goal_note_for_agent(agent: str) -> str:
    """
    The one short line of goal steering an agent carries into its prompts.

    This is what makes a team goal actually STEER the work rather than just
    decorate a card (owner decision, 2026-08-13). It is deliberately capped at
    GOAL_NOTE_MAX_CHARS and joined to a single line: it is appended to prompts
    that run on local Qwen, and hard rule 1 exists because long prompts made
    Qwen burn its whole token budget and return "{". Truncation here is a
    feature, not a shortfall — a goal too long to state briefly is too long to
    steer with.

    Returns "" when the agent has no team, no active goal, or is an instrument.
    """
    team = AGENT_TEAM.get(agent)
    if not team or agent in INSTRUMENTS:
        return ""
    goals = active_goals_for_team(team)
    if not goals:
        return ""
    # Newest active goal wins — an owner who sets a new one means the new one.
    text = " ".join(goals[0]["goal"].split())
    if len(text) > GOAL_NOTE_MAX_CHARS:
        text = text[: GOAL_NOTE_MAX_CHARS - 1].rstrip() + "…"
    return text


def instructions_note_for_agent(agent: str) -> str:
    """
    The standing instructions this agent carries into EVERY prompt it runs.

    Same single-injection discipline as goal_note_for_agent (rule 39): it is
    appended by system_prompt_builder, the one place every agent prompt in the
    codebase is assembled, so a brief reaches pipeline runs and chat alike
    instead of applying in one path and being forgotten in the other. Before
    2026-08-14 these instructions only ever reached Colony chat, which made
    "standing instructions for you" quietly untrue of the agent's real work.

    Hard-capped for the same reason the goal note is (rule 1: local Qwen's
    context). Empty for instruments and for the Secretary — her own
    instructions live in her private store and are injected there (rule 15).
    """
    if agent in INSTRUMENTS or agent == "secretary":
        return ""
    text = " ".join(get_agent_settings(agent)["custom_instructions"].split())
    if len(text) > INSTRUCTIONS_NOTE_MAX_CHARS:
        text = text[: INSTRUCTIONS_NOTE_MAX_CHARS - 1].rstrip() + "…"
    return text


def goal_progress(goal: dict) -> dict:
    """
    Real progress against a goal: products finished by that team since the goal
    was set, counted from the products table — never a self-report from an
    agent, and never a percentage the model made up (principle 2, judge by
    fruit). `baseline_products` is the count at the moment the goal was created.
    """
    from agents.state import get_all_products

    kinds = TEAMS.get(goal["team"], {}).get("goal_kinds", [])
    if not kinds:
        return {"done": 0, "target": goal.get("target_count"), "measurable": False}
    products = get_all_products()
    matching = [p for p in products if (p.get("product_type") or "bookmark") in kinds]
    done = max(0, len(matching) - (goal.get("baseline_products") or 0))
    return {"done": done, "target": goal.get("target_count"), "measurable": True}


def current_product_count(team: str) -> int:
    """Baseline snapshot for a new goal — how many of that team's products exist now."""
    from agents.state import get_all_products

    kinds = TEAMS.get(team, {}).get("goal_kinds", [])
    if not kinds:
        return 0
    return sum(1 for p in get_all_products()
               if (p.get("product_type") or "bookmark") in kinds)


# ── Per-agent chat history ────────────────────────────────────────────────────

def add_agent_message(agent: str, role: str, content: str):
    with _connect() as conn:
        conn.execute("INSERT INTO agent_messages (agent, role, content) VALUES (?, ?, ?)",
                     (agent, role, content))
        conn.commit()


def get_agent_messages(agent: str, limit: int = 40) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_messages WHERE agent = ? ORDER BY id DESC LIMIT ?",
            (agent, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


def clear_agent_messages(agent: str):
    with _connect() as conn:
        conn.execute("DELETE FROM agent_messages WHERE agent = ?", (agent,))
        conn.commit()


# ── The confirm-before-acting queue ───────────────────────────────────────────
# Same shape and spirit as the Secretary's pending_actions (rules 20/24/25):
# the gate lives in the tool handler, and nothing paid or destructive happens
# until Sheraj says so from the dashboard.

def queue_action(agent: str, kind: str, description: str, payload: dict) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO colony_actions (agent, kind, description, payload)
               VALUES (?, ?, ?, ?)""",
            (agent, kind, description, json.dumps(payload)))
        conn.commit()
        return cur.lastrowid


def get_action(action_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM colony_actions WHERE id = ?", (action_id,)).fetchone()
    return dict(row) if row else None


def list_actions(status: str = "pending", limit: int = 50) -> list[dict]:
    sql = "SELECT * FROM colony_actions"
    params: list = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def resolve_action(action_id: int, status: str, result: str = "") -> dict | None:
    with _connect() as conn:
        conn.execute(
            """UPDATE colony_actions SET status = ?, result = ?, resolved_at = datetime('now')
               WHERE id = ?""",
            (status, result, action_id))
        conn.commit()
    return get_action(action_id)


# ── The handoff graph (derived, never stored) ─────────────────────────────────

def handoff_edges(days: int = 30, limit_tasks: int = 500) -> list[dict]:
    """
    Who hands work to whom, derived from task_runs.

    Within one task_id, consecutive rows in id order are consecutive steps of
    one piece of work, so agent(n) -> agent(n+1) is a real handoff. This is why
    the graph needs no new logging: every pipeline already writes task_runs, so
    the edges are a record of what happened rather than a diagram of what was
    intended. Self-loops (an agent stepping twice in a row, e.g. a revision
    round) are counted separately as `repeats` on the node, not as edges.
    """
    since = (datetime.utcnow() - timedelta(days=days)).isoformat(sep=" ", timespec="seconds")
    with _connect() as conn:
        rows = conn.execute(
            """SELECT task_id, agent, timestamp FROM task_runs
               WHERE task_id IN (
                   SELECT DISTINCT task_id FROM task_runs
                   WHERE timestamp >= ? ORDER BY task_id DESC LIMIT ?
               )
               ORDER BY task_id, id""",
            (since, limit_tasks)).fetchall()

    edges: dict[tuple[str, str], dict] = {}
    prev_task, prev_agent = None, None
    for row in rows:
        task_id, agent, ts = row["task_id"], row["agent"], row["timestamp"]
        if task_id == prev_task and prev_agent and prev_agent != agent:
            key = (prev_agent, agent)
            edge = edges.setdefault(key, {"source": prev_agent, "target": agent,
                                          "count": 0, "last_at": ts})
            edge["count"] += 1
            if ts and (edge["last_at"] is None or ts > edge["last_at"]):
                edge["last_at"] = ts
        prev_task, prev_agent = task_id, agent
    return sorted(edges.values(), key=lambda e: -e["count"])


def recent_runs(agent: str | None = None, limit: int = 25) -> list[dict]:
    sql = ("SELECT id, task_id, agent, step, input_summary, output_summary, "
           "passed_review, timestamp FROM task_runs")
    params: list = []
    if agent:
        sql += " WHERE agent = ?"
        params.append(agent)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    for r in rows:
        # passed_review is NULL for mechanical steps (rule 14) — keep that
        # distinction visible rather than collapsing it to False.
        r["judged"] = r["passed_review"] is not None
        r["passed_review"] = None if r["passed_review"] is None else bool(r["passed_review"])
    return rows


# Which team a background job belongs to. The job store keys on the pipeline
# that runs, not on a team, so this is the one place the two vocabularies meet.
# An unknown kind maps to nothing and is simply not shown — inventing a home
# for it would put a job on a team that isn't running it.
JOB_KIND_TEAM: dict[str, str] = {
    "full-pipeline": "print_studio",
    "card-pipeline": "print_studio",
    "card-batch": "print_studio",
    "write-approve": "print_studio",
    "improve": "print_studio",
    "x-post": "print_studio",
    "video_plan": "film_crew",
    "video_frames": "film_crew",
    "video_clips": "film_crew",
    "video_chain": "film_crew",
}

JOB_KIND_LABELS: dict[str, str] = {
    "full-pipeline": "making a bookmark",
    "card-pipeline": "making a quote card",
    "card-batch": "making a batch of quote cards",
    "write-approve": "writing and reviewing a listing",
    "improve": "improving a product",
    "x-post": "writing a giveaway post",
    "video_plan": "planning the shots",
    "video_frames": "generating frames",
    "video_clips": "rendering clips",
    "video_chain": "rendering a chained video",
    "team-consult": "consulting",
}

# Who set a job going, in words Sheraj reads on screen.
STARTER_LABELS: dict[str, str] = {
    "sheraj": "you", "abigail": "Abigail", "colony": "a team goal",
}


def team_activity() -> dict[str, list[dict]]:
    """
    What each team is DOING right now, keyed by team id.

    Read from the API's live job store rather than from a new table: a job is
    already the unit of "work in flight", and a second record of it could
    disagree with the first. Fails quiet — if the job store cannot be reached
    (a script, a test), teams simply report no activity rather than the page
    breaking over a decoration.
    """
    try:
        from agents import api
        jobs = api.pipeline_jobs()
    except Exception:
        return {}
    out: dict[str, list[dict]] = {}
    for job in jobs:
        if job.get("status") not in ("running", "waiting_for_input"):
            continue
        team = JOB_KIND_TEAM.get(job.get("kind") or "")
        if not team:
            continue
        started = job.get("started_by") or "sheraj"
        out.setdefault(team, []).append({
            "job_id": job.get("job_id"),
            "kind": job.get("kind"),
            "label": JOB_KIND_LABELS.get(job.get("kind") or "", job.get("kind")),
            "progress": job.get("progress") or "",
            "status": job.get("status"),
            "started_by": started,
            "started_by_label": STARTER_LABELS.get(started, started),
        })
    return out


def _live_agents() -> set[str]:
    since = (datetime.utcnow() - timedelta(minutes=LIVE_WINDOW_MINUTES)).isoformat(
        sep=" ", timespec="seconds")
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT agent FROM task_runs WHERE timestamp >= ?", (since,)).fetchall()
    return {r["agent"] for r in rows}


# ── The snapshot the graph renders ────────────────────────────────────────────

def _promotion_gap(status: dict) -> str:
    """Plain-language statement of what stands between this agent and the next level."""
    if status["trust_level"] >= 3:
        return "At the highest trust level."
    needs = []
    if status["total_runs"] < 5:
        remaining = 5 - status["total_runs"]
        needs.append(f"{remaining} more judged run{'s' if remaining > 1 else ''}")
    if status["trust_score"] < 80:
        needs.append(f"a clean rate of 80% (currently {status['trust_score']:.0f}%)")
    if not needs:
        return "Meets the promotion condition — advances on its next clean run."
    return "Needs " + " and ".join(needs) + " to advance."


def colony_snapshot() -> dict:
    """
    Everything the Colony graph draws in one call: agents with their trust and
    live state, teams with their goals, and the derived handoff edges.
    """
    init_colony_db()
    statuses = {s["name"]: s for s in get_all_agent_statuses()}
    live = _live_agents()

    agents = []
    for name, status in statuses.items():
        team = AGENT_TEAM.get(name)
        settings = get_agent_settings(name)
        agents.append({
            "name": name,
            "team": team,
            "is_instrument": name in INSTRUMENTS,
            "chattable": name not in NO_COLONY_CHAT,
            "trust_level": status["trust_level"],
            "trust_level_name": TRUST_LEVELS.get(status["trust_level"], "Unknown"),
            "trust_score": status["trust_score"],
            "total_runs": status["total_runs"],
            "clean_runs": status["clean_runs"],
            "consecutive_failures": status["consecutive_failures"],
            "promotion_note": _promotion_gap(status),
            "live": name in live,
            "paused": settings["paused"],
            "has_instructions": bool(settings["custom_instructions"].strip()),
        })

    activity = team_activity()
    teams = []
    for team_id, team in TEAMS.items():
        goals = active_goals_for_team(team_id)
        jobs = activity.get(team_id, [])
        teams.append({
            "id": team_id,
            "name": team["name"],
            "blurb": team["blurb"],
            "accent": team["accent"],
            "members": team["members"],
            "instruments": team["instruments"],
            "goal_kinds": team["goal_kinds"],
            "consultable": len(team["members"]) > 1,
            "active_goals": [g | {"progress": goal_progress(g)} for g in goals],
            # Work in flight right now — real jobs, so a team reads as busy
            # only when something is actually running for it. Deliberately NOT
            # widened to "an agent logged a step recently": a run that has just
            # finished or been CANCELLED leaves fresh task_runs behind, and a
            # team still showing as working after a cancel is exactly the kind
            # of stale signal this whole area exists to remove. Recent activity
            # is already carried, per agent, by the `live` flag.
            "jobs": jobs,
            "working": bool(jobs),
        })

    return {
        "agents": agents,
        "teams": teams,
        "edges": handoff_edges(),
        "pending_actions": len(list_actions("pending")),
        "generated_at": datetime.utcnow().isoformat(),
    }


def agent_detail(agent: str) -> dict:
    """One agent's full profile: trust, settings, recent work, and its team."""
    init_colony_db()
    statuses = {s["name"]: s for s in get_all_agent_statuses()}
    if agent not in statuses:
        raise ValueError(f"Unknown agent '{agent}'")
    status = statuses[agent]
    team_id = AGENT_TEAM.get(agent)
    edges = handoff_edges()
    return {
        "name": agent,
        "team": team_id,
        "team_name": TEAMS.get(team_id, {}).get("name") if team_id else None,
        "is_instrument": agent in INSTRUMENTS,
        "chattable": agent not in NO_COLONY_CHAT,
        "trust_level": status["trust_level"],
        "trust_level_name": TRUST_LEVELS.get(status["trust_level"], "Unknown"),
        "trust_score": status["trust_score"],
        "total_runs": status["total_runs"],
        "clean_runs": status["clean_runs"],
        "consecutive_failures": status["consecutive_failures"],
        "promotion_note": _promotion_gap(status),
        "live": agent in _live_agents(),
        "settings": get_agent_settings(agent),
        "goal_note": goal_note_for_agent(agent),
        "recent_runs": recent_runs(agent, limit=15),
        "hands_to": [e for e in edges if e["source"] == agent],
        "receives_from": [e for e in edges if e["target"] == agent],
    }
