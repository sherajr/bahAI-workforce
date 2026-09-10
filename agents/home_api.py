"""
Home: what is happening, and what needs you (rule 120).

Sheraj is non-technical, and the dashboard opens on a pipeline form. Home is the
answer to "what was I doing, and what is waiting for me?" in ordinary language:
no pipeline, no provider, no agent graph, no trust score.

Three things it is careful about:

* **It is a READ, and a cheap one.** Nothing here starts a job, opens a
  microphone, initialises a model or asks a provider anything. It is polled by a
  screen, so it is assembled from counts and a handful of rows, and it is
  bounded — a long history must not make opening the app slower.
* **It combines private and workforce data in MEMORY, and writes neither.** A
  gathering's title lives in `private/consultation.db` and a product's title in
  `workforce.db`; this endpoint reads both for one authorised reader and stores
  the combination nowhere (rules 15/73/64). A private title never becomes an
  Activity Log line or a URL parameter.
* **Every number points at something real.** An approval count comes from the
  approval queues; a commitment comes from a consultation's own action items.
  Nothing here is self-reported by a model, and nothing is a score of a person.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter

router = APIRouter(prefix="/home", tags=["home"])

# Bounded on purpose: this is a screen, not a report.
MAX_PER_LIST = 6


def _safe(fn, default):
    """Home must render even when one subsystem is unhappy.

    A dashboard whose front page 500s because the wallet's RPC is down is worse
    than one that shows everything else and says that part is unavailable.
    """
    try:
        return fn()
    except Exception as e:                                   # noqa: BLE001
        return {"error": f"{type(e).__name__}", "value": default}


def _continue_items() -> list[dict]:
    """Work that is already under way, with a link to the right place."""
    out: list[dict] = []

    # A consultation that is open but not listening -- the most likely thing
    # somebody walked away from mid-meeting (rule 114).
    from agents import live_consultation_store as lstore
    for session in lstore.list_sessions(limit=20):
        if session.get("status") == "live":
            out.append({
                "kind": "consultation", "id": session["id"],
                "title": session.get("title") or "A consultation",
                "detail": "Open and not ended. Resume listening, or end it and review "
                          "the record.",
                "tab": "consultation",
            })
    # Gatherings that are mid-flight.
    from agents import gathering as gcore
    for project in lstore.list_projects(limit=20):
        stage = project.get("stage") or gcore.DEFAULT_STAGE
        if stage in ("closed",):
            continue
        out.append({
            "kind": "gathering", "id": project["id"],
            "title": project.get("title") or "A gathering",
            "detail": gcore.STAGES.get(stage, {}).get("blurb", ""),
            "stage": stage,
            "when": project.get("gathering_at") or "",
            "tab": "gatherings",
        })
    return out[:MAX_PER_LIST]


def _running_jobs() -> list[dict]:
    """Anything actually running right now, from the one job store."""
    from agents.api import JOBS
    out = []
    for job_id, job in list(JOBS.items()):
        if job.get("status") != "running":
            continue
        out.append({
            "kind": "job", "id": job_id,
            "title": (job.get("kind") or "A run").replace("_", " "),
            # Progress strings are mechanical by design and carry nothing
            # personal (rule 64), so they are safe to show here.
            "detail": str(job.get("progress") or "")[:200],
            "started_by": job.get("started_by") or "",
            "tab": "pipeline",
        })
    return out[:MAX_PER_LIST]


def _decisions_needed() -> dict:
    """Real pending approvals, with their real type and consequence."""
    items: list[dict] = []

    # Abigail's queue -- calendar, Drive, Gmail, WhatsApp, workforce jobs
    # (rules 20/24/25/28/51/72).
    try:
        from agents.secretary_store import get_pending_actions
        for action in get_pending_actions()[:MAX_PER_LIST]:
            items.append({
                "kind": "secretary", "id": str(action.get("id")),
                # The KIND, never the content: this list is a screen anybody
                # walking past can see, and the detail lives in her tab.
                "title": f"Abigail wants to {str(action.get('kind', '')).replace('_', ' ')}",
                "detail": "Approve or decline it in Abigail's tab.",
                "tab": "secretary",
            })
    except Exception:
        pass

    # The Colony's queue -- anything paid or product-changing (rule 37).
    try:
        from agents.colony import list_actions
        for action in list_actions(status="pending")[:MAX_PER_LIST]:
            items.append({
                "kind": "colony", "id": str(action.get("id")),
                "title": f"{action.get('agent', 'An agent')} wants to "
                         f"{str(action.get('kind', '')).replace('_', ' ')}",
                "detail": "It has not run. Approve it in the Colony tab.",
                "tab": "colony",
            })
    except Exception:
        pass

    # Consultations whose record nobody has approved yet (rule 102).
    from agents import live_consultation_store as lstore
    for session in lstore.list_sessions(limit=20):
        if session.get("status") != "ended":
            continue
        if session.get("approved_at"):
            continue
        if session.get("closeout_at"):
            continue
        items.append({
            "kind": "record", "id": session["id"],
            "title": f"Review the record of “{session.get('title') or 'a consultation'}”",
            "detail": "The meeting has ended and nobody has approved what it says.",
            "tab": "consultation",
        })

    # X posts waiting to be approved -- never auto-posted.
    try:
        from agents.state import get_pending_x_posts
        pending = get_pending_x_posts()
        if pending:
            items.append({
                "kind": "x_post", "id": str(pending[0].get("id")),
                "title": f"{len(pending)} post(s) waiting to go out on X",
                "detail": "Nothing is posted without you.",
                "tab": "x-posts",
            })
    except Exception:
        pass

    return {"items": items[:MAX_PER_LIST], "total": len(items)}


def _next_actions() -> dict:
    """Accepted commitments, blockers and scheduled reflections -- recorded, not guessed."""
    from agents import gathering as gcore
    from agents import live_consultation_store as lstore

    accepted, blocked = [], []
    for session in lstore.list_sessions(limit=30):
        for item in lstore.list_action_items(session["id"]):
            entry = {
                "id": item["id"], "session_id": session["id"],
                "action": item.get("action", ""),
                "owner": item.get("owner") or "",
                # Tri-state, and shown as such: "nobody has answered" is a
                # different fact from "asked and declined" (rule 101).
                "owner_accepted": item.get("owner_accepted"),
                "due": item.get("due") or "",
                "status": item.get("status"),
                "blocker": item.get("blocker") or "",
                "tab": "consultation",
            }
            if item.get("status") == "blocked" or item.get("blocker"):
                blocked.append(entry)
            elif item.get("owner_accepted") is True and \
                    item.get("status") in gcore.LIVE_STATUSES:
                accepted.append(entry)

    reflections = []
    today = datetime.now().date()
    soon = today + timedelta(days=30)
    for project in lstore.list_projects(limit=50):
        when = (project.get("reflection_at") or "").strip()
        if not when:
            continue
        try:
            due = datetime.strptime(when[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if due <= soon:
            reflections.append({
                "id": project["id"], "title": project.get("title") or "A gathering",
                "when": when, "overdue": due < today, "tab": "gatherings",
            })

    return {
        "accepted": accepted[:MAX_PER_LIST],
        "accepted_total": len(accepted),
        "blocked": blocked[:MAX_PER_LIST],
        "blocked_total": len(blocked),
        "reflections": sorted(reflections, key=lambda r: r["when"])[:MAX_PER_LIST],
    }


def _made() -> dict:
    """What has been made and given. Deeds, not a score (rule 61)."""
    from agents.state import _connect
    with _connect() as conn:
        products = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        cards = conn.execute(
            "SELECT COUNT(*) FROM products WHERE product_type = 'quote_card'").fetchone()[0]
    return {"products": products, "quote_cards": cards,
            "bookmarks": products - cards}


@router.get("/summary")
def home_summary():
    """
    Everything Home draws, in one read.

    One request rather than eight, because this is the first thing that happens
    when the app opens and eight parallel requests on a laptop that is also
    running Ollama is a slow first paint. Each section degrades on its own: a
    subsystem that raises is reported as unavailable and the rest still renders.
    """
    return {
        "continue": _safe(_continue_items, []),
        "running": _safe(_running_jobs, []),
        "decisions": _safe(_decisions_needed, {"items": [], "total": 0}),
        "next_actions": _safe(_next_actions, {}),
        "made": _safe(_made, {}),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
