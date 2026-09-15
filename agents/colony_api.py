"""
The Colony (rules 35-41b) and the Material World / nuclei (rules 59-69) HTTP
routes, extracted from api.py.

Business logic lives in agents/colony.py, agents/colony_chat.py,
agents/colony_tools.py, agents/nuclei_store.py, agents/nuclei_layout.py and
agents/nuclei_bridge.py; every route here is a thin, lazily-imported wrapper.

`launch_team_pipeline` also lives here (it sits right next to the goal-launch
route that is its only in-process caller) and lazily imports the pipeline
entry points it starts from agents.pipeline_api / agents.card_api / 
agents.video_api -- never at module load time, so there is no import-order
dependency on those files. agents.api re-exports it for
agents/secretary_colony.py, which calls it directly rather than over HTTP.

No prefix on this router -- see wallet_api.py's docstring for why.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agents.jobs import _start_job

router = APIRouter()


# --- The Colony (dashboard tab: the workforce as an organisation) ------------
#
# Everything the Colony graph needs: the snapshot it draws, per-agent detail,
# chat, settings, team goals and team consultation. Storage and the derived
# handoff graph live in agents/colony.py; chat and consultation in
# agents/colony_chat.py; the tool gate in agents/colony_tools.py.

class AgentChatRequest(BaseModel):
    message: str


class AgentSettingsRequest(BaseModel):
    custom_instructions: Optional[str] = None
    paused: Optional[bool] = None
    # "" clears the override back to the router's task-type default; None
    # (absent) leaves whatever is already saved alone.
    model: Optional[str] = None


class GoalRequest(BaseModel):
    team: str
    goal: str
    detail: str = ""
    target_count: Optional[int] = None


class GoalEditRequest(BaseModel):
    goal: Optional[str] = None
    detail: Optional[str] = None
    target_count: Optional[int] = None
    status: Optional[str] = None


class GoalLaunchRequest(BaseModel):
    # Which of the team's goal_kinds to run. Defaults to the team's first.
    kind: Optional[str] = None
    # Overrides the goal text as the run's theme/story when given.
    theme: Optional[str] = None
    language: Optional[str] = None


class TeamConsultRequest(BaseModel):
    question: str


def _colony_agent_or_404(agent: str) -> str:
    from agents import colony
    if agent not in colony.AGENT_TEAM:
        raise HTTPException(status_code=404, detail=f"No agent called '{agent}'")
    return agent


@router.get("/colony")
def colony_overview():
    """The whole graph in one call: agents, teams, goals and derived handoffs.

    The real PEOPLE on the workforce are merged in here at read time from the
    private store — derived on every read, never a row in workforce.db, the
    same shape as the finished-video shelf (rules 58 / 68). That is what lets
    someone Sheraj adds in the Material World show up in the Digital World too
    without a name ever being written on the workforce side.
    """
    from agents import colony
    snap = colony.colony_snapshot()
    snap["humans"] = _workforce_humans()
    return snap


def _workforce_humans() -> list[dict]:
    """Never let a missing/locked private DB break the Colony map."""
    try:
        from agents import nuclei_store
        nuclei_store.init_db()
        return nuclei_store.workforce_members()
    except Exception:
        return []


@router.get("/colony/agents/{agent}")
def colony_agent(agent: str):
    from agents import colony
    _colony_agent_or_404(agent)
    detail = colony.agent_detail(agent)
    detail["messages"] = colony.get_agent_messages(agent) if detail["chattable"] else []
    return detail


@router.post("/colony/agents/{agent}/chat")
def colony_agent_chat(agent: str, req: AgentChatRequest):
    """
    One chat turn with one agent, on ITS OWN model (rule 16 — never Claude).
    Paid or product-changing tools queue for approval instead of running.
    """
    from agents import colony_chat
    _colony_agent_or_404(agent)
    try:
        return colony_chat.chat(agent, req.message)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Surface the real reason (a dead Ollama, a missing key) — a silent
        # failure in a chat box is exactly the class of bug that hid the Canva
        # breakage for weeks.
        raise HTTPException(status_code=502,
                            detail=f"{agent} could not answer: {type(e).__name__}: {e}")


@router.delete("/colony/agents/{agent}/chat")
def colony_clear_chat(agent: str):
    from agents import colony
    _colony_agent_or_404(agent)
    colony.clear_agent_messages(agent)
    return {"result": "ok"}


@router.get("/colony/agents/{agent}/settings")
def colony_get_agent_settings(agent: str):
    from agents import colony
    _colony_agent_or_404(agent)
    colony.init_colony_db()
    return colony.get_agent_settings(agent)


@router.post("/colony/agents/{agent}/settings")
def colony_set_agent_settings(agent: str, req: AgentSettingsRequest):
    from agents import colony
    _colony_agent_or_404(agent)
    colony.init_colony_db()
    if agent in colony.INSTRUMENTS:
        raise HTTPException(
            status_code=400,
            detail=f"'{agent}' is a tool in the pipeline, not an agent with instructions.")
    if agent == "secretary" and (req.custom_instructions is not None or req.paused is not None):
        # Her personality lives in her own private store (rule 15), edited from
        # her own tab. Writing it here would land in a table nothing reads and
        # look like it had been saved — refuse instead of failing silently.
        raise HTTPException(
            status_code=400,
            detail="Abigail's instructions are edited in her own tab; only her model "
                   "can be set from the Colony.")
    if req.model:
        # The provider boundary is enforced HERE, before storage — a workforce
        # agent can never be saved onto Claude, nor Abigail off it (rule 16).
        # The dropdown is a convenience; this is the guarantee.
        from agents import models as model_registry
        try:
            model_registry.validate_choice(agent, req.model)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    return colony.set_agent_settings(agent, custom_instructions=req.custom_instructions,
                                    paused=req.paused, model=req.model)


@router.get("/colony/models")
def colony_models(agent: Optional[str] = None):
    """
    The models on offer — discovered live from each provider, never a hardcoded
    list that can go stale and offer something that no longer exists.

    Pass ?agent= to get only the ones that agent is allowed to use, plus what
    it would run on today. `reachable` reports whether each provider actually
    answered: an empty local list because Ollama is DOWN is a different fact
    from one because nothing is installed, and the UI must not confuse them.
    """
    from agents import colony
    from agents import models as model_registry

    result = model_registry.list_models(agent)
    if agent:
        from agents.colony_chat import CHAT_TASK_TYPE
        colony.init_colony_db()
        chosen = colony.get_agent_settings(agent).get("model") or ""
        task_type = CHAT_TASK_TYPE.get(agent, "copy")
        default_provider, default_model = model_registry.default_for_agent(agent, task_type)
        result |= {
            "agent": agent,
            "chosen": chosen,
            "default_model": default_model,
            "default_provider": default_provider,
            # Paid is "not running on this computer" — testing for xAI alone
            # labelled Abigail's Claude default as free, which is a lie about
            # money in the one place the user looks to check.
            "default_paid": default_provider != model_registry.OLLAMA,
            # The Reviewer and Artist read images through call_grok_vision,
            # which is a separate path from call_llm — moving them to a local
            # model does NOT make their image work free, and saying otherwise
            # would be a quiet lie about cost.
            "uses_vision": agent in ("reviewer", "artist"),
        }
    return result


@router.get("/colony/handoffs")
def colony_handoffs(days: int = 30):
    from agents import colony
    return {"days": days, "edges": colony.handoff_edges(days=days),
            "recent_runs": colony.recent_runs(limit=40)}


# --- Colony: the confirm-before-acting queue --------------------------------

@router.get("/colony/actions")
def colony_list_actions(status: str = "pending"):
    from agents import colony
    colony.init_colony_db()
    return {"actions": colony.list_actions(status)}


@router.post("/colony/actions/{action_id}")
def colony_resolve_action(action_id: int, approve: bool = True):
    """
    Approve or decline a queued action. Approval is the ONLY path that runs
    anything paid or product-changing from a chat — the same shape as the
    Secretary's approval endpoint (rules 20/24/25).
    """
    from agents import colony, colony_tools
    colony.init_colony_db()
    action = colony.get_action(action_id)
    if not action:
        raise HTTPException(status_code=404, detail=f"No action #{action_id}")
    if action["status"] != "pending":
        raise HTTPException(status_code=400,
                            detail=f"Action #{action_id} is already {action['status']}")
    if not approve:
        colony.resolve_action(action_id, "declined", "Declined by Sheraj")
        return {"result": "declined", "action": colony.get_action(action_id)}
    try:
        outcome = colony_tools.run_approved_action(action)
    except Exception as e:
        # A failed action stays visible as failed rather than silently
        # disappearing from the queue.
        colony.resolve_action(action_id, "failed", f"{type(e).__name__}: {e}")
        raise HTTPException(status_code=502, detail=f"That action failed: {e}")
    colony.resolve_action(action_id, "done", outcome)
    return {"result": "done", "outcome": outcome, "action": colony.get_action(action_id)}


# --- Colony: team goals ------------------------------------------------------

@router.get("/colony/goals")
def colony_list_goals(team: Optional[str] = None, status: Optional[str] = None):
    from agents import colony
    colony.init_colony_db()
    goals = colony.list_goals(team=team, status=status)
    return {"goals": [g | {"progress": colony.goal_progress(g)} for g in goals]}


@router.post("/colony/goals")
def colony_create_goal(req: GoalRequest):
    from agents import colony
    colony.init_colony_db()
    try:
        # Baseline the team's product count NOW, so progress counts what was
        # made because of the goal rather than everything ever made.
        goal = colony.create_goal(
            req.team, req.goal, detail=req.detail, target_count=req.target_count,
            baseline_products=colony.current_product_count(req.team))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return goal | {"progress": colony.goal_progress(goal)}


@router.patch("/colony/goals/{goal_id}")
def colony_update_goal(goal_id: int, req: GoalEditRequest):
    from agents import colony
    colony.init_colony_db()
    if not colony.get_goal(goal_id):
        raise HTTPException(status_code=404, detail=f"No goal #{goal_id}")
    goal = colony.update_goal(goal_id, **req.model_dump(exclude_none=True))
    return goal | {"progress": colony.goal_progress(goal)}


@router.delete("/colony/goals/{goal_id}")
def colony_delete_goal(goal_id: int):
    from agents import colony
    colony.init_colony_db()
    colony.delete_goal(goal_id)
    return {"result": "ok"}


def launch_team_pipeline(kind: str, theme: str, detail: str = "",
                         language: Optional[str] = None,
                         quotes: Optional[list[str]] = None,
                         started_by: str = "colony") -> dict:
    """
    Start the real pipeline for one of the teams' `goal_kinds`.

    The SINGLE implementation of "a team is asked to make something", shared by
    the Colony's goal-launch endpoint and by Abigail's approved job request
    (agents/secretary_colony.py). It reuses the same pipeline entry points the
    Pipeline and Video tabs use, so a run started this way is indistinguishable
    from a hand-started one and every gate — verification, metering, review —
    applies unchanged (rule 40).

    Several `quotes` for a card run means the real BATCH job, through the same
    endpoint function the dashboard's own multi-quote form posts to — so every
    quote is verified against the selected sources BEFORE anything paid starts,
    and a batch is hands-free (no mid-run pause) exactly as it is there.

    Video is deliberately different: it CREATES the project and stops. Video
    planning is a multi-stage, GPU-bound process the owner reviews before any
    clip is rendered (rules 31/33), and a one-line request must not skip the
    look he is supposed to take.
    """
    theme = (theme or "").strip()
    if not theme:
        raise ValueError("A pipeline run needs a theme")
    quotes = [q.strip() for q in (quotes or []) if (q or "").strip()]

    if kind == "bookmark":
        from agents.pipeline_api import PipelineRunRequest, _run_full_pipeline
        run_req = PipelineRunRequest(theme=theme)
        job_id = _start_job(
            "full-pipeline",
            lambda progress, on_turn, ask: _run_full_pipeline(run_req, progress, on_turn, ask),
            started_by=started_by)
        return {"result": "running", "job_id": job_id, "kind": kind, "theme": theme}

    if kind == "quote_card":
        from agents.card_api import (
            CardBatchRequest, CardPipelineRequest, _run_card_pipeline,
            pipeline_run_card_batch,
        )
        if len(quotes) > 1:
            batch = pipeline_run_card_batch(
                CardBatchRequest(theme=theme, language=language, quotes=quotes),
                started_by=started_by)
            return {"result": "running", "job_id": batch["job_id"], "kind": kind,
                    "theme": theme, "count": batch["total"]}
        run_req = CardPipelineRequest(theme=theme, language=language,
                                      pinned_quote=quotes[0] if quotes else "")
        job_id = _start_job(
            "card-pipeline",
            lambda progress, on_turn, ask: _run_card_pipeline(run_req, progress, on_turn, ask),
            started_by=started_by)
        return {"result": "running", "job_id": job_id, "kind": kind, "theme": theme,
                "count": 1}

    if kind == "video":
        from agents import video_store
        from agents.jobs import create_task
        from agents.video_api import DEFAULT_DIRECTION
        title = theme[:60] + ("..." if len(theme) > 60 else "")
        task_id = create_task(title[:200], "video", assigned_to=started_by or "pipeline")
        project_id = video_store.create_project(
            title=title, source_kind="scene_story",
            source_text=(detail or "").strip(), source_brief=theme,
            source_instructions="", source_product_id=None, task_id=task_id,
            direction=dict(DEFAULT_DIRECTION))
        return {"result": "project_created", "video_project_id": project_id, "kind": kind,
                "theme": theme,
                "message": "Video project created — open the Video tab to plan its shots."}

    raise ValueError(f"Cannot launch '{kind}'")


@router.post("/colony/goals/{goal_id}/launch")
def colony_launch_goal(goal_id: int, req: GoalLaunchRequest):
    """
    Start the real pipeline that serves this goal.

    Reuses the SAME pipeline entry points the Pipeline and Video tabs use —
    there is exactly one implementation of each pipeline, so a goal-launched
    run is indistinguishable from a hand-started one and every gate
    (verification, metering, review) applies unchanged.

    The Film Crew is deliberately different: it CREATES the video project and
    hands it back rather than rendering. Video is a multi-stage, GPU-bound
    pipeline whose planning the owner reviews before any clips are made
    (rules 31/33) — kicking off a render from a one-line goal would skip the
    look he is supposed to take.
    """
    from agents import colony
    colony.init_colony_db()
    goal = colony.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail=f"No goal #{goal_id}")

    team = colony.TEAMS[goal["team"]]
    kinds = team["goal_kinds"]
    if not kinds:
        raise HTTPException(
            status_code=400,
            detail=f"{team['name']} has no pipeline to launch — its goals are steering only.")
    kind = req.kind or kinds[0]
    if kind not in kinds:
        raise HTTPException(status_code=422,
                            detail=f"{team['name']} can run: {', '.join(kinds)}")

    theme = (req.theme or goal["goal"]).strip()

    try:
        out = launch_team_pipeline(kind, theme, detail=goal["detail"] or "",
                                   language=req.language, started_by="colony")
    except ValueError as e:  # unreachable given the membership check above
        raise HTTPException(status_code=422, detail=str(e))

    if out["result"] == "project_created":
        colony.update_goal(goal_id, launched_job_id=f"video:{out['video_project_id']}")
    else:
        colony.update_goal(goal_id, launched_job_id=out["job_id"])
    return out


# --- The project wallet (Nora's domain) --------------------------------------
# Moved to agents/wallet_api.py (rules 42-49) -- included near the bottom of
# this file with the rest of the routers.


# --- Colony: team consultation ----------------------------------------------

@router.post("/colony/teams/{team_id}/consult")
def colony_team_consult(team_id: str, req: TeamConsultRequest):
    """
    Put a question to a whole team; each member answers in turn, seeing what
    the others said. Several LLM calls, so it runs as a background job and
    streams its turns exactly like a pipeline consultation does.
    """
    from agents import colony, colony_chat
    colony.init_colony_db()
    if team_id not in colony.TEAMS:
        raise HTTPException(status_code=404, detail=f"No team called '{team_id}'")
    if not req.question.strip():
        raise HTTPException(status_code=422, detail="A consultation needs a question")

    def _run(progress, on_turn, ask):
        # Streamed in the SAME shape as a pipeline consultation turn
        # (agent/role/message) so the dashboard renders it with the components
        # it already has, instead of a second near-identical turn format.
        return colony_chat.run_team_consultation(
            team_id, req.question, progress=progress,
            on_turn=lambda t: on_turn({"agent": t["agent"], "role": "member",
                                       "message": t["text"]}))

    job_id = _start_job("team-consult", _run)
    return {"job_id": job_id, "status": "running", "team": team_id}
# --- Material World (nuclei) -------------------------------------------------------
# Rules 15 / 59 / 60 / 61. Personal community data lives in private/nuclei.db
# via nuclei_store.py only. These endpoints never write a name into
# workforce.db. Nothing here is a score on a soul.


def _nuclei():
    from agents import nuclei_store
    nuclei_store.init_db()
    return nuclei_store


def _nuclei_err(exc: Exception):
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise exc


class NucleiGroupingIn(BaseModel):
    kind_slug: str = "nucleus"
    name: str


class NucleiGroupingPatch(BaseModel):
    name: Optional[str] = None


class NucleiPositionIn(BaseModel):
    x: float
    y: float


class NucleiActorIn(BaseModel):
    kind: str = "person"
    display_name: str
    how_we_met: Optional[str] = None
    grouping_id: Optional[int] = None
    introduced_as: Optional[str] = None
    role_slug: Optional[str] = None


class NucleiActorPatch(BaseModel):
    display_name: Optional[str] = None
    how_we_met: Optional[str] = None


class NucleiMembershipIn(BaseModel):
    actor_id: int
    grouping_id: int
    introduced_as: Optional[str] = None
    introduced_by_actor_id: Optional[int] = None
    role_slug: Optional[str] = None


class NucleiFacetIn(BaseModel):
    membership_id: int
    slug: str


class NucleiActivityIn(BaseModel):
    kind_slug: str = "gathering"
    happened_at: Optional[str] = None
    participant_ids: list[int] = []
    grouping_id: Optional[int] = None
    title: Optional[str] = None
    host_ids: Optional[list[int]] = None


class NucleiSatIn(BaseModel):
    actor_id: int


class NucleiTieIn(BaseModel):
    kind_slug: str = "accompanying"
    from_actor_id: int
    to_actor_id: int
    grouping_id: Optional[int] = None


class NucleiHouseholdMemberIn(BaseModel):
    person_id: Optional[int] = None
    display_name: Optional[str] = None


@router.get("/nuclei/snapshot")
def nuclei_snapshot():
    try:
        return _nuclei().snapshot()
    except Exception as e:
        _nuclei_err(e)


@router.get("/nuclei/kinds")
def nuclei_kinds():
    return _nuclei().list_kinds()


@router.post("/nuclei/groupings")
def nuclei_create_grouping(req: NucleiGroupingIn):
    try:
        return _nuclei().create_grouping(req.kind_slug, req.name)
    except Exception as e:
        _nuclei_err(e)


@router.patch("/nuclei/groupings/{grouping_id}")
def nuclei_patch_grouping(grouping_id: int, req: NucleiGroupingPatch):
    try:
        return _nuclei().update_grouping(grouping_id, name=req.name)
    except Exception as e:
        _nuclei_err(e)


@router.patch("/nuclei/groupings/{grouping_id}/position")
def nuclei_set_position(grouping_id: int, req: NucleiPositionIn):
    """Owner dragged this table. Neighbours stay put."""
    ns = _nuclei()
    try:
        ns.set_grouping_position(grouping_id, req.x, req.y)
        return ns.snapshot()
    except Exception as e:
        _nuclei_err(e)


@router.post("/nuclei/layout/optimize")
def nuclei_optimize_layout():
    """Rearrange every table from size, who gathers where, and who walks with whom."""
    try:
        return _nuclei().optimize_layout()
    except Exception as e:
        _nuclei_err(e)


@router.post("/nuclei/groupings/{grouping_id}/archive")
def nuclei_archive_grouping(grouping_id: int):
    """Take a nucleus off the map. Friends and gatherings stay (rule 62)."""
    try:
        return _nuclei().archive_grouping(grouping_id)
    except Exception as e:
        _nuclei_err(e)


@router.get("/nuclei/groupings/{grouping_id}")
def nuclei_get_grouping(grouping_id: int):
    try:
        return _nuclei().grouping_detail(grouping_id)
    except Exception as e:
        _nuclei_err(e)


@router.post("/nuclei/actors")
def nuclei_create_actor(req: NucleiActorIn):
    ns = _nuclei()
    try:
        actor = ns.create_actor(req.kind, req.display_name, req.how_we_met)
        if req.grouping_id:
            mem = ns.add_membership(
                actor["id"], req.grouping_id,
                introduced_as=req.introduced_as,
                role_slug=req.role_slug,
            )
            ns.add_facet(mem["id"], "connected")
        return ns.actor_detail(actor["id"])
    except Exception as e:
        _nuclei_err(e)


@router.patch("/nuclei/actors/{actor_id}")
def nuclei_patch_actor(actor_id: int, req: NucleiActorPatch):
    try:
        _nuclei().update_actor(actor_id, display_name=req.display_name,
                               how_we_met=req.how_we_met)
        return _nuclei().actor_detail(actor_id)
    except Exception as e:
        _nuclei_err(e)


@router.get("/nuclei/actors/{actor_id}")
def nuclei_get_actor(actor_id: int):
    try:
        return _nuclei().actor_detail(actor_id)
    except Exception as e:
        _nuclei_err(e)


@router.post("/nuclei/actors/{actor_id}/archive")
def nuclei_archive_actor(actor_id: int):
    """Take a friend off the map. Gatherings stay. You cannot remove yourself."""
    try:
        return _nuclei().archive_actor(actor_id)
    except Exception as e:
        _nuclei_err(e)


@router.post("/nuclei/memberships")
def nuclei_add_membership(req: NucleiMembershipIn):
    try:
        mem = _nuclei().add_membership(
            req.actor_id, req.grouping_id,
            introduced_as=req.introduced_as,
            introduced_by_actor_id=req.introduced_by_actor_id,
            role_slug=req.role_slug,
        )
        facets = _nuclei().live_facets(int(mem["id"]))
        return {**mem, "facets": facets}
    except Exception as e:
        _nuclei_err(e)


@router.post("/nuclei/memberships/{membership_id}/end")
def nuclei_end_membership(membership_id: int):
    try:
        _nuclei().end_membership(membership_id)
        return {"result": "ended"}
    except Exception as e:
        _nuclei_err(e)


@router.post("/nuclei/households/{household_id}/members")
def nuclei_add_household_member(household_id: int, req: NucleiHouseholdMemberIn):
    ns = _nuclei()
    try:
        pid = req.person_id
        if pid is None:
            name = (req.display_name or "").strip()
            if not name:
                raise ValueError("Name someone in this family")
            person = ns.create_actor("person", name)
            pid = person["id"]
        return ns.add_household_member(household_id, int(pid))
    except Exception as e:
        _nuclei_err(e)


@router.post("/nuclei/household-members/{member_id}/end")
def nuclei_end_household_member(member_id: int):
    try:
        _nuclei().end_household_member(member_id)
        return {"result": "ended"}
    except Exception as e:
        _nuclei_err(e)


@router.post("/nuclei/facets")
def nuclei_add_facet(req: NucleiFacetIn):
    try:
        return _nuclei().add_facet(req.membership_id, req.slug)
    except Exception as e:
        _nuclei_err(e)


@router.post("/nuclei/facets/{facet_id}/end")
def nuclei_end_facet(facet_id: int):
    try:
        _nuclei().end_facet(facet_id)
        return {"result": "ended"}
    except Exception as e:
        _nuclei_err(e)


@router.post("/nuclei/ties")
def nuclei_add_tie(req: NucleiTieIn):
    try:
        if req.kind_slug == "accompanying":
            if req.grouping_id is None:
                raise ValueError("Walking with someone is for a particular work")
            return _nuclei().add_accompaniment(
                req.from_actor_id, req.to_actor_id, req.grouping_id,
            )
        return _nuclei().add_tie(
            req.kind_slug, req.from_actor_id, req.to_actor_id, req.grouping_id,
        )
    except Exception as e:
        _nuclei_err(e)


@router.post("/nuclei/ties/{tie_id}/end")
def nuclei_end_tie(tie_id: int):
    try:
        _nuclei().end_tie(tie_id)
        return {"result": "ended"}
    except Exception as e:
        _nuclei_err(e)


@router.post("/nuclei/activities")
def nuclei_record_activity(req: NucleiActivityIn):
    from datetime import datetime as _dt
    when = (req.happened_at or "").strip() or _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        return _nuclei().record_activity(
            req.kind_slug, when, req.participant_ids,
            grouping_id=req.grouping_id, title=req.title,
            host_ids=req.host_ids,
        )
    except Exception as e:
        _nuclei_err(e)


@router.post("/nuclei/activities/sat-together")
def nuclei_sat_together(req: NucleiSatIn):
    try:
        return _nuclei().sat_together(req.actor_id)
    except Exception as e:
        _nuclei_err(e)


@router.get("/nuclei/quiet-lights")
def nuclei_quiet_lights():
    return {"items": _nuclei().quiet_lights()}


# --- The Bahá'í Workforce on the Material World map (rules 65-68) ------------------
# The workforce light opens like a family opens: agents and the real people who
# work alongside them. Everything personal still lives in private/nuclei.db;
# agents.nuclei_bridge is the ONE module where the two sides touch.

class NucleiWorkforcePersonIn(BaseModel):
    display_name: Optional[str] = None
    actor_id: Optional[int] = None
    role: Optional[str] = None


class NucleiChannelIn(BaseModel):
    kind: str = "whatsapp_group"
    label: Optional[str] = None
    link: Optional[str] = None


class NucleiDraftIn(BaseModel):
    about: str
    to_kind: str = "contact"          # "contact" | "group"
    contact_id: Optional[int] = None
    channel_id: Optional[int] = None
    include_recent_work: bool = False


class NucleiSendIn(BaseModel):
    contact_id: int
    message: str


def _bridge():
    from agents import nuclei_bridge
    return nuclei_bridge


def _bridge_err(exc: Exception):
    from agents.nuclei_bridge import BridgeError
    if isinstance(exc, (BridgeError, ValueError)):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise exc


@router.get("/nuclei/workforce")
def nuclei_workforce():
    """Who works here, what is running, what was finished lately."""
    try:
        return _bridge().workforce_picture()
    except Exception as e:
        _bridge_err(e)


@router.post("/nuclei/workforce/people")
def nuclei_add_workforce_person(req: NucleiWorkforcePersonIn):
    try:
        added = _nuclei().add_workforce_person(
            display_name=req.display_name, actor_id=req.actor_id, role=req.role,
        )
        return {**added, "snapshot": _nuclei().snapshot()}
    except Exception as e:
        _nuclei_err(e)


@router.post("/nuclei/workforce/people/{membership_id}/end")
def nuclei_remove_workforce_person(membership_id: int):
    try:
        _nuclei().remove_workforce_person(membership_id)
        return {"result": "ok", "snapshot": _nuclei().snapshot()}
    except Exception as e:
        _nuclei_err(e)


@router.post("/nuclei/groupings/{grouping_id}/channel")
def nuclei_set_channel(grouping_id: int, req: NucleiChannelIn):
    """Note the WhatsApp GROUP a nucleus already talks in. Never a number."""
    try:
        channel = _nuclei().set_grouping_channel(
            grouping_id, label=req.label, link=req.link, kind=req.kind,
        )
        return {"channel": channel, "snapshot": _nuclei().snapshot()}
    except Exception as e:
        _nuclei_err(e)


@router.delete("/nuclei/channels/{channel_id}")
def nuclei_remove_channel(channel_id: int):
    try:
        _nuclei().remove_grouping_channel(channel_id)
        return {"result": "ok", "snapshot": _nuclei().snapshot()}
    except Exception as e:
        _nuclei_err(e)


@router.post("/nuclei/workforce/message/draft")
def nuclei_draft_message(req: NucleiDraftIn):
    """Clara drafts one WhatsApp message. Nothing is sent from here."""
    ns = _nuclei()
    to_name = ""
    nucleus_name = ""
    try:
        if req.to_kind == "group":
            channel = ns.get_grouping_channel(req.channel_id) if req.channel_id else None
            if not channel:
                raise ValueError("Pick a WhatsApp group to write to")
            grouping = ns.get_grouping(int(channel["grouping_id"]))
            nucleus_name = (grouping or {}).get("name") or ""
            to_name = channel.get("label") or nucleus_name
        else:
            from agents import secretary_store
            secretary_store.init_db()
            contact = next(
                (c for c in secretary_store.list_contacts()
                 if int(c["id"]) == int(req.contact_id or 0)), None,
            )
            if not contact:
                raise ValueError("Pick someone to write to")
            to_name = contact["name"]
        return _bridge().draft_message(
            about=req.about, to_name=to_name, to_kind=req.to_kind,
            include_recent_work=req.include_recent_work,
            nucleus_name=nucleus_name,
        )
    except Exception as e:
        _bridge_err(e)


@router.post("/nuclei/workforce/message/send")
def nuclei_send_message(req: NucleiSendIn):
    """Send to one contact on Abigail's WhatsApp. Rule 28's tiers are unchanged."""
    try:
        return _bridge().send_to_contact(req.contact_id, req.message)
    except Exception as e:
        _bridge_err(e)
