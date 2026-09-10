"""
Gatherings: the endpoints (rules 117–120).

One project carries a piece of service from preparing, through the consultation
and the materials, to what was learned afterwards. Its own router, because
`agents/api.py` is long enough and this is a new domain rather than a new corner
of an old one — the same pattern `live_consultation_api.py` established.

Three boundaries this file is responsible for:

* **Only APPROVED, SELECTED outcomes cross into a project** (rule 118). The
  offer list is built from sessions whose record a human approved, and from the
  confirmed decisions and open items in it — never from the transcript, and
  never from the assistant's private observations.
* **A programme's private files are not public** (rule 119). Quote-card images
  live under `outputs/`, which is a mounted static directory; a programme names
  a real gathering, so it is written under `private/` and served only through
  the owner-gated endpoint here, with the path resolved rather than joined.
* **Nothing here spends money or sends anything.** Building a kit from cards
  that already exist is free; making new ones goes through the existing pipeline
  entry points with their own gates (rule 40), and generating a kit has never
  invited anybody to anything.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from agents import gathering as core
from agents import live_consultation_store as store

router = APIRouter(prefix="/gatherings", tags=["gatherings"])

# Private, git-ignored, and NOT the mounted `outputs/` directory: a programme
# carries a gathering's real title, its date and the questions the group chose.
PRIVATE_DIR = Path(__file__).parent.parent / "private" / "gatherings"


class ProjectIn(BaseModel):
    title: str = ""
    purpose: str = ""
    gathering_at: Optional[str] = None
    timezone: str = ""


class ProjectPatch(BaseModel):
    title: Optional[str] = None
    purpose: Optional[str] = None
    stage: Optional[str] = None
    gathering_at: Optional[str] = None
    timezone: Optional[str] = None
    notes: Optional[str] = None
    reflection_at: Optional[str] = None
    reflection_skipped: Optional[bool] = None
    reflection_notes: Optional[str] = None


class SessionLinkIn(BaseModel):
    session_id: str


class OutcomeIn(BaseModel):
    kind: str
    text: str
    session_id: str = ""
    ref_id: str = ""
    note: str = ""


class ItemIn(BaseModel):
    product_id: str


class ProgramIn(BaseModel):
    kind: str = "note"
    title: str = ""
    body: str = ""
    minutes: Optional[int] = None
    writing_id: str = ""


class ProgramPatch(BaseModel):
    kind: Optional[str] = None
    title: Optional[str] = None
    body: Optional[str] = None
    minutes: Optional[int] = None
    writing_id: Optional[str] = None


class ReorderIn(BaseModel):
    item_ids: list[str] = []


class PrintIn(BaseModel):
    duplex: bool = True
    include_variants: bool = False


def _project_or_404(project_id: str) -> dict:
    project = store.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="No such gathering.")
    return project


def _product_rows(product_ids: list[str]) -> list[dict]:
    """The chosen materials, from `workforce.db`.

    Read only. Nothing about the gathering goes the other way -- no title, no
    date, no name (rules 64/68): what crosses is a product id.
    """
    from agents.state import _connect
    if not product_ids:
        return []
    marks = ",".join("?" for _ in product_ids)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM products WHERE id IN ({marks})", product_ids).fetchall()
    by_id = {r["id"]: dict(r) for r in rows}
    return [by_id[pid] for pid in product_ids if pid in by_id]


def _writings_for(session_ids: list[str]) -> dict:
    out = {}
    for sid in session_ids:
        for w in store.list_writings(sid):
            out[str(w["id"])] = w
    return out


def _detail(project: dict) -> dict:
    pid = project["id"]
    session_ids = store.project_session_ids(pid)
    items = _product_rows(store.project_item_ids(pid))
    program = store.list_program(pid)
    sessions = [store.get_session(s) for s in session_ids]
    sessions = [s for s in sessions if s]
    return {
        "project": project,
        "stage_info": core.STAGES.get(project.get("stage") or core.DEFAULT_STAGE, {}),
        # Enough of each linked meeting to show it and say whether its record
        # was approved -- never its transcript (rule 118).
        "sessions": [{
            "id": s["id"], "title": s["title"], "status": s["status"],
            "started_at": s.get("started_at"), "ended_at": s.get("ended_at"),
            "closeout_outcome": s.get("closeout_outcome"),
            "approved_at": s.get("approved_at"),
            "transcript_deleted": bool(s.get("transcript_deleted_at")),
        } for s in sessions],
        "outcomes": store.list_project_outcomes(pid),
        # Derived from the linked consultations, never copied (rule 117).
        "commitments": store.project_commitments(pid),
        "items": [{
            "id": p["id"], "title": p.get("title"), "theme": p.get("theme"),
            "product_type": p.get("product_type") or "bookmark",
            "front_image": p.get("front_image"), "back_image": p.get("back_image"),
            "image_url": p.get("image_url"),
        } for p in items],
        "program": program,
        "program_minutes": core.program_minutes(program),
        "kit": core.kit_readiness(items, program),
        "writings": list(_writings_for(session_ids).values()),
    }


@router.get("")
def list_gatherings(limit: int = 100):
    projects = store.list_projects(limit=limit)
    out = []
    for p in projects:
        pid = p["id"]
        commitments = store.project_commitments(pid)
        out.append({
            **p,
            "stage_info": core.STAGES.get(p.get("stage") or core.DEFAULT_STAGE, {}),
            "session_count": len(store.project_session_ids(pid)),
            "item_count": len(store.project_item_ids(pid)),
            "outcome_count": len(store.list_project_outcomes(pid)),
            # Counted, never scored. These are things to do, not a mark out of
            # ten and not a streak (rule 61).
            "commitments_total": len(commitments),
            "commitments_open": len([c for c in commitments
                                     if c.get("status") in core.LIVE_STATUSES]),
        })
    return {"projects": out, "stages": core.STAGES}


@router.post("")
def create_gathering(req: ProjectIn):
    project = store.create_project(req.title, req.purpose, req.gathering_at, req.timezone)
    if not project:
        raise HTTPException(status_code=400, detail="A gathering needs a name.")
    return _detail(project)


@router.get("/{project_id}")
def get_gathering(project_id: str):
    return _detail(_project_or_404(project_id))


@router.patch("/{project_id}")
def patch_gathering(project_id: str, req: ProjectPatch):
    _project_or_404(project_id)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if "stage" in fields and fields["stage"] not in core.STAGES:
        raise HTTPException(status_code=400, detail=f"Unknown stage: {fields['stage']}")
    return _detail(store.update_project(project_id, **fields) or _project_or_404(project_id))


@router.delete("/{project_id}")
def delete_gathering(project_id: str):
    """Delete the project. The consultations stay.

    A meeting is a thing that happened and a project is a way of looking at it;
    tidying away the second must never destroy the first.
    """
    _project_or_404(project_id)
    return {"deleted": store.delete_project(project_id),
            "note": "The gathering is deleted. The consultations it linked are untouched."}


# ── Consultations ───────────────────────────────────────────────────────────

@router.post("/{project_id}/sessions")
def link_session(project_id: str, req: SessionLinkIn):
    _project_or_404(project_id)
    if not store.link_project_session(project_id, req.session_id):
        raise HTTPException(status_code=404, detail="No such consultation.")
    return _detail(_project_or_404(project_id))


@router.delete("/{project_id}/sessions/{session_id}")
def unlink_session(project_id: str, session_id: str):
    _project_or_404(project_id)
    if not store.unlink_project_session(project_id, session_id):
        raise HTTPException(status_code=404,
                            detail="That consultation is not linked to this gathering.")
    return _detail(_project_or_404(project_id))


@router.get("/{project_id}/available-outcomes")
def available_outcomes(project_id: str):
    """What this gathering could carry forward, and what is not eligible yet.

    Only sessions whose record a HUMAN APPROVED are offered (rule 118). A
    meeting that has not been closed out is listed with the reason rather than
    hidden: "there is nothing here" and "you have not approved it yet" are very
    different things to be told.
    """
    _project_or_404(project_id)
    offered, waiting = [], []
    for sid in store.project_session_ids(project_id):
        session = store.get_session(sid)
        if not session:
            continue
        if not session.get("approved_at"):
            waiting.append({
                "session_id": sid, "title": session["title"],
                "reason": ("Nobody has approved this consultation's record yet, so its "
                           "outcomes cannot be carried forward. Review and approve it "
                           "in the Consultation tab."),
            })
            continue
        for decision in store.list_decisions(sid):
            if decision.get("status") != "confirmed":
                continue
            offered.append({"session_id": sid, "kind": "decision",
                            "ref_id": decision["id"], "text": decision.get("text", ""),
                            "retained_concerns": decision.get("retained_concerns") or []})
        state = store.get_state(sid)
        for name, kind in (("unresolved_questions", "question"),
                           ("tensions", "concern"),
                           ("needs_and_concerns", "concern")):
            for item in state.get(name) or []:
                if not isinstance(item, dict):
                    continue
                if item.get("lifecycle") not in (None, "", "open"):
                    continue
                offered.append({"session_id": sid, "kind": kind,
                                "ref_id": item.get("id", ""),
                                "text": item.get("text", "")})
    return {"offered": offered, "waiting": waiting, "kinds": core.OUTCOME_KINDS}


@router.post("/{project_id}/outcomes")
def add_outcome(project_id: str, req: OutcomeIn):
    _project_or_404(project_id)
    if not core.normalize_outcome_kind(req.kind):
        raise HTTPException(status_code=400, detail=f"Unknown outcome kind: {req.kind}")
    if req.session_id:
        session = store.get_session(req.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="No such consultation.")
        # The gate, in code rather than in the UI's choice of what to offer.
        if not session.get("approved_at"):
            raise HTTPException(status_code=409, detail=(
                "That consultation's record has not been approved, so its outcomes "
                "cannot be carried into a gathering yet (rule 118)."))
    outcome = store.add_project_outcome(
        project_id, req.kind, req.text, req.session_id, req.ref_id, req.note)
    if not outcome:
        raise HTTPException(status_code=400, detail="An outcome needs some words.")
    return _detail(_project_or_404(project_id))


@router.delete("/{project_id}/outcomes/{outcome_id}")
def remove_outcome(project_id: str, outcome_id: str):
    _project_or_404(project_id)
    if not store.remove_project_outcome(project_id, outcome_id):
        raise HTTPException(status_code=404, detail="No such outcome on this gathering.")
    return _detail(_project_or_404(project_id))


# ── Materials ───────────────────────────────────────────────────────────────

@router.post("/{project_id}/items")
def add_item(project_id: str, req: ItemIn):
    """Put an EXISTING product in the kit. Free: nothing is generated here.

    Making new cards is a separate, explicit act through the pipeline the
    dashboard already uses, with its own batch caps, its own review and its own
    metering (rule 40). A kit builder that quietly started paid work would be
    the worst possible surprise.
    """
    _project_or_404(project_id)
    if not _product_rows([req.product_id]):
        raise HTTPException(status_code=404, detail=f"No product with id '{req.product_id}'.")
    store.add_project_item(project_id, req.product_id)
    return _detail(_project_or_404(project_id))


@router.delete("/{project_id}/items/{product_id}")
def remove_item(project_id: str, product_id: str):
    _project_or_404(project_id)
    if not store.remove_project_item(project_id, product_id):
        raise HTTPException(status_code=404, detail="That item is not in this kit.")
    return _detail(_project_or_404(project_id))


# ── The programme ───────────────────────────────────────────────────────────

@router.post("/{project_id}/program")
def add_program(project_id: str, req: ProgramIn):
    _project_or_404(project_id)
    if req.kind not in core.PROGRAM_KINDS:
        raise HTTPException(status_code=400, detail=f"Unknown programme item: {req.kind}")
    if req.kind == "reading" and req.writing_id:
        # A reading points at a VERIFIED passage, by id. The programme never
        # stores scripture text of its own (rule 119), so this is the only way
        # a passage reaches the page -- and it has to be a real one.
        if req.writing_id not in _writings_for(store.project_session_ids(project_id)):
            raise HTTPException(status_code=400, detail=(
                "That passage is not one of the verified passages from this gathering's "
                "consultations. A reading can only point at a passage that came out of "
                "the library."))
    item = store.add_program_item(project_id, req.kind, req.title, req.body,
                                  req.minutes, req.writing_id)
    if not item:
        raise HTTPException(status_code=400, detail=(
            f"A programme holds at most {core.MAX_PROGRAM_ITEMS} items."))
    return _detail(_project_or_404(project_id))


@router.patch("/{project_id}/program/{item_id}")
def patch_program(project_id: str, item_id: str, req: ProgramPatch):
    _project_or_404(project_id)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="Nothing to change.")
    if not store.update_program_item(project_id, item_id, **fields):
        raise HTTPException(status_code=404, detail="No such item in this programme.")
    return _detail(_project_or_404(project_id))


@router.delete("/{project_id}/program/{item_id}")
def remove_program(project_id: str, item_id: str):
    _project_or_404(project_id)
    if not store.remove_program_item(project_id, item_id):
        raise HTTPException(status_code=404, detail="No such item in this programme.")
    return _detail(_project_or_404(project_id))


@router.post("/{project_id}/program/reorder")
def reorder_program(project_id: str, req: ReorderIn):
    _project_or_404(project_id)
    store.reorder_program(project_id, req.item_ids)
    return _detail(_project_or_404(project_id))


@router.get("/{project_id}/program.pdf")
def download_program(project_id: str):
    """The programme as a printable page.

    Written under `private/`, never into the mounted `outputs/` directory, and
    served from here so the owner gate applies (rule 119). The path is BUILT
    from the project id rather than taken from a request, so there is nothing to
    traverse; it is still resolved and checked against its own directory, which
    costs nothing and cannot be forgotten later.
    """
    project = _project_or_404(project_id)
    from agents.program_sheet import build_program_sheet

    items = store.list_program(project_id)
    writings = _writings_for(store.project_session_ids(project_id))
    folder = PRIVATE_DIR / project_id
    folder.mkdir(parents=True, exist_ok=True)
    out = (folder / "program.pdf").resolve()
    if PRIVATE_DIR.resolve() not in out.parents:
        raise HTTPException(status_code=400, detail="Bad programme path.")
    try:
        built = build_program_sheet(project, items, writings, out_pdf_path=str(out))
    except Exception as e:
        raise HTTPException(status_code=500,
                            detail=f"The programme could not be built ({type(e).__name__}).")
    safe = "".join(c for c in (project.get("title") or "gathering")
                   if c.isalnum() or c in " -_").strip()[:60] or "gathering"
    return FileResponse(
        path=built["path"], media_type="application/pdf",
        filename=f"{safe} - programme.pdf",
        # The programme prints as ONE document and the cards print as another.
        # Putting a programme page into the duplex card sheet shifts every back
        # face by a page (rule 119), so they are never combined.
        headers={"X-Program-Pages": str(built["pages"]),
                 "X-Program-Warnings": "; ".join(built["warnings"])[:400]})


@router.post("/{project_id}/cards.pdf")
def download_cards(project_id: str, req: PrintIn):
    """The kit's cards, through the print sheet the dashboard already uses.

    Deliberately the SAME builder, with the same duplex behaviour and the same
    refusals (a mixed-size set, an unrendered face). A second print path would
    be a second set of alignment bugs.
    """
    _project_or_404(project_id)
    product_ids = store.project_item_ids(project_id)
    if not product_ids:
        raise HTTPException(status_code=400,
                            detail="There is nothing in this kit to print yet.")
    from agents.api import post_mixed_print_sheet, MixedPrintSheetRequest
    return post_mixed_print_sheet(MixedPrintSheetRequest(
        product_ids=product_ids, duplex=req.duplex,
        include_variants=req.include_variants))
