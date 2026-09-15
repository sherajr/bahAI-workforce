"""
The Video tab's HTTP routes (rules 30-34, 58), extracted from api.py.

Business logic lives in agents/video_*.py and agents/videographer.py; long
stages (planning, frames, clips) run through the SAME background job store as
every other pipeline (agents/jobs.py) -- _start_job + /pipeline/status/{job_id}
in api.py.

No prefix on this router -- see wallet_api.py's docstring for why.
"""

import json
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from agents.jobs import _start_job, _web_image_path, create_task
from agents.state import get_all_products

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# Video Generation pipeline
#
# Turns a scene, story, historical account or passage into MANY simple 3-4
# second shots that assemble into a coherent video. A bookmark or quote card
# can be used as the source instead of pasted text, but the pipeline is a
# general story-to-video tool, not a card-video tool.
#
# Long stages (planning, frames, clips) run through the SAME background job
# store as every other pipeline here — _start_job + /pipeline/status/{job_id}.
# ─────────────────────────────────────────────────────────────────────────────

def _video_modules():
    """Imported lazily so the video stack never slows an unrelated endpoint."""
    from agents import (video_assembly, video_director, video_pipeline,
                        video_provider, video_safety, video_store)
    return video_store, video_director, video_pipeline, video_provider, video_assembly, video_safety


def _video_project_or_404(project_id: str) -> dict:
    from agents import video_store
    project = video_store.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Video project {project_id} not found")
    return project


def _shot_web_paths(shot: dict) -> dict:
    """Add dashboard-servable URLs beside the stored local paths."""
    out = dict(shot)
    out["first_frame_url"] = _web_image_path(shot.get("first_frame_path") or "")
    out["last_frame_url"] = _web_image_path(shot.get("last_frame_path") or "")
    out["clip_url"] = _web_image_path(shot.get("clip_path") or "")
    return out


class VideoProjectRequest(BaseModel):
    title: str = ""
    source_kind: str = "scene_story"          # scene_story | bookmark | quote_card
    source_text: str = ""
    source_brief: str = ""
    source_instructions: str = ""
    source_product_id: Optional[str] = None
    direction: Optional[dict] = None


DEFAULT_DIRECTION = {
    "target_seconds": 60,
    "aspect_ratio": "16:9",
    "visual_style": "cinematic realism",
    "historical_period": "",
    "setting": "",
    "mood": "",
    "color_palette": "natural, filmic",
    "audience": "general",
    "narration": "voiceover",                  # voiceover | none | on_screen_text
    "on_screen_text": "minimal",
    "shot_seconds": 3.5,
    # standard = fill the target duration; cinematic = fewer, longer,
    # non-overlapping moments that cut only where the story changes.
    "pacing": "standard",
    "provider": "comfyui:wan22",
    "low_resource": True,
}


@router.get("/video/providers")
def video_providers():
    """
    Capability report per provider. `first_last_frame` is DETECTED, never
    assumed — the dashboard shows the resulting fallback strategy so the user
    always knows how their clips are actually being made.
    """
    from agents import video_provider, video_assembly
    return {
        "providers": video_provider.list_providers(),
        "default": video_provider.DEFAULT_PROVIDER,
        "strategies": video_provider.STRATEGY_LABELS,
        "ffmpeg": video_assembly.has_ffmpeg(),
    }


@router.get("/video/defaults")
def video_defaults():
    """Creative-direction defaults + the shot-count maths for the UI."""
    from agents import video_director
    return {
        "direction": DEFAULT_DIRECTION,
        "min_shot_seconds": video_director.MIN_SHOT_SECONDS,
        "max_shot_seconds": video_director.MAX_SHOT_SECONDS,
        "complexity_limit": video_director.COMPLEXITY_LIMIT,
        "aspect_ratios": ["16:9", "9:16", "1:1", "4:5"],
        "visual_styles": ["cinematic realism", "documentary", "painterly", "watercolour",
                          "storybook illustration", "archival film", "silhouette animation"],
        "narration_options": ["voiceover", "on_screen_text", "none"],
        "pacing_options": [
            {"id": "standard", "label": "Fill the target length",
             "description": "Plans enough shots to reach the duration you asked for. "
                            "Best when the source is dense with events."},
            {"id": "cinematic", "label": "Fewer, longer moments",
             "description": "Plans one shot per distinct moment, uses the longest shots "
                            "allowed, and cuts only where the story actually changes — so "
                            "the video is calmer and may come in shorter than the target."},
        ],
    }


@router.get("/video/projects")
def video_list_projects():
    from agents import video_store
    return {"projects": video_store.list_projects()}


@router.post("/video/projects")
def video_create_project(req: VideoProjectRequest):
    """
    Create a project from pasted text (the primary path) or from an existing
    bookmark / quote card (a convenience source). A product source is
    REFERENCED by id and its text copied in as the starting point — the
    original product row is never modified or duplicated.
    """
    from agents import video_store

    source_kind = req.source_kind if req.source_kind in ("scene_story", "bookmark", "quote_card") \
        else "scene_story"
    source_text = (req.source_text or "").strip()
    title = (req.title or "").strip()
    product_id = req.source_product_id

    if source_kind in ("bookmark", "quote_card"):
        if not product_id:
            raise HTTPException(status_code=400,
                                detail="Pick a bookmark or quote card to use as the source.")
        product = next((p for p in get_all_products() if p["id"] == product_id), None)
        if not product:
            raise HTTPException(status_code=404, detail=f"Product {product_id} not found")
        listing = {}
        try:
            listing = json.loads(product.get("listing_copy") or "{}")
        except (ValueError, TypeError):
            listing = {}
        quote = (listing.get("bookmark_quote")
                 or (listing.get("card_copy") or {}).get("quote")
                 or "")
        attribution = (listing.get("quote_source")
                       or (listing.get("card_copy") or {}).get("source") or "")
        # Seed the source with the product's own words; the user can edit it
        # freely before planning, and the product row stays untouched.
        if not source_text:
            source_text = "\n\n".join(x for x in [
                f'"{quote}"' if quote else "",
                f"— {attribution}" if attribution else "",
                product.get("theme") or "",
            ] if x).strip()
        if not title:
            title = f"Video from {product.get('title') or product_id}"

    if not title:
        title = (source_text[:60] + "...") if len(source_text) > 60 else (source_text or "Untitled video")
    if source_kind == "scene_story" and not source_text and not (req.source_brief or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Paste a scene, story or passage (or write a short brief) to start.")

    direction = {**DEFAULT_DIRECTION, **(req.direction or {})}
    task_id = create_task(title[:200], "video", assigned_to="pipeline")
    project_id = video_store.create_project(
        title=title, source_kind=source_kind, source_text=source_text,
        source_brief=req.source_brief or "", source_instructions=req.source_instructions or "",
        source_product_id=product_id, task_id=task_id, direction=direction,
    )
    return video_store.get_project(project_id)


@router.get("/video/projects/{project_id}")
def video_get_project(project_id: str):
    from agents import video_pipeline, video_store
    project = _video_project_or_404(project_id)
    shots = [_shot_web_paths(s) for s in video_store.list_shots(project_id)]
    return {"project": project, "shots": shots,
            "resume": video_pipeline.resume_state(project_id)}


class VideoProjectUpdate(BaseModel):
    title: Optional[str] = None
    source_text: Optional[str] = None
    source_brief: Optional[str] = None
    source_instructions: Optional[str] = None
    direction: Optional[dict] = None
    continuity: Optional[dict] = None


@router.patch("/video/projects/{project_id}")
def video_update_project(project_id: str, req: VideoProjectUpdate):
    """Edit source, creative direction, or the continuity bible (including locks)."""
    from agents import video_store
    _video_project_or_404(project_id)
    edits = req.model_dump(exclude_unset=True)
    if not edits:
        raise HTTPException(status_code=400, detail="No fields provided to edit")
    video_store.update_project(project_id, **edits)
    return video_store.get_project(project_id)


@router.delete("/video/projects/{project_id}")
def video_delete_project(project_id: str):
    from agents import video_store
    _video_project_or_404(project_id)
    video_store.delete_project(project_id)
    return {"result": "deleted", "project_id": project_id}


@router.post("/video/projects/{project_id}/plan")
def video_plan(project_id: str):
    """Story analysis → continuity bible → shot plan, as a background job."""
    from agents import video_pipeline
    _video_project_or_404(project_id)

    def runner(progress, on_turn, request_human_input):
        return video_pipeline.build_plan(project_id, progress=progress)

    return {"job_id": _start_job("video_plan", runner), "status": "started"}


class VideoGenerateRequest(BaseModel):
    shot_ids: Optional[list[str]] = None    # None = the whole project
    force: bool = False                     # regenerate even if assets exist
    provider: Optional[str] = None


@router.post("/video/projects/{project_id}/frames")
def video_generate_frames(project_id: str, req: VideoGenerateRequest):
    """
    Generate first/last frames. Resumable and idempotent: shots that already
    have frames are skipped unless force=true, and shot_ids limits the run to
    a subset so one shot can be redone without touching the rest.
    """
    from agents import video_pipeline
    _video_project_or_404(project_id)
    cancel_flag = {"stop": False}

    def runner(progress, on_turn, request_human_input):
        return video_pipeline.generate_frames(
            project_id, shot_ids=req.shot_ids, force=req.force,
            progress=progress, should_cancel=lambda: cancel_flag["stop"],
        )

    job_id = _start_job("video_frames", runner)
    _VIDEO_CANCEL[job_id] = cancel_flag
    return {"job_id": job_id, "status": "started"}


class VideoRepairRequest(BaseModel):
    # Also apply the cinematic cut policy (cut only at beat boundaries and real
    # changes of place or time). Non-destructive: no shot is ever deleted.
    recut: bool = False


@router.post("/video/projects/{project_id}/repair-motion")
def video_repair_motion(project_id: str, req: VideoRepairRequest | None = None):
    """
    Fix movement descriptions — and optionally the cut rhythm — on an
    ALREADY-planned project, in place.

    Deterministic and free (no LLM call), so it runs synchronously rather than
    as a background job. Returns exactly what it changed.
    """
    from agents import video_pipeline
    _video_project_or_404(project_id)
    try:
        return video_pipeline.repair_project_motion(
            project_id, recut=bool(req and req.recut))
    except video_pipeline.VideoPipelineError as e:
        raise HTTPException(status_code=400, detail=str(e))


class VideoChainRequest(BaseModel):
    provider: Optional[str] = None
    # Vision-read each clip's real final frame so a shot after a CUT still
    # matches it. Paid (metered like every other vision call); the chain works
    # without it, just with weaker carry-over across cuts.
    adapt: bool = True
    force: bool = False


@router.post("/video/projects/{project_id}/chain")
def video_generate_chained(project_id: str, req: VideoChainRequest):
    """
    Generate the whole video as ONE CONTINUOUS CHAIN: each clip is rendered
    from the previous clip's REAL final frame, rather than every shot being
    rendered independently from its own text prompt (which is what makes a
    finished video look like disconnected slides).

    Sequential and resumable — a shot that already has a clip is skipped and
    the chain resumes from its actual final frame.
    """
    from agents import video_pipeline
    _video_project_or_404(project_id)

    # Preflight SYNCHRONOUSLY so a stopped ComfyUI, a missing 'av' package or an
    # unplanned project comes back as a plain error on the button press. Raised
    # inside the job thread instead, the message reached the dashboard as a
    # job-status error that the panel then cleared on the same tick — the user
    # saw a click that appeared to do nothing (reported 2026-08-13).
    ok, reason = video_pipeline.chain_preflight(project_id, req.provider)
    if not ok:
        raise HTTPException(status_code=400, detail=reason)

    cancel_flag = {"stop": False}

    def runner(progress, on_turn, request_human_input):
        return video_pipeline.generate_chained(
            project_id, provider_id=req.provider, adapt=req.adapt, force=req.force,
            progress=progress, should_cancel=lambda: cancel_flag["stop"],
        )

    job_id = _start_job("video_chain", runner)
    _VIDEO_CANCEL[job_id] = cancel_flag
    return {"job_id": job_id, "status": "started"}


@router.post("/video/projects/{project_id}/clips")
def video_generate_clips(project_id: str, req: VideoGenerateRequest):
    """Generate clips through the configured provider. Same resume semantics."""
    from agents import video_pipeline
    _video_project_or_404(project_id)
    cancel_flag = {"stop": False}

    def runner(progress, on_turn, request_human_input):
        return video_pipeline.generate_clips(
            project_id, shot_ids=req.shot_ids, force=req.force, provider_id=req.provider,
            progress=progress, should_cancel=lambda: cancel_flag["stop"],
        )

    job_id = _start_job("video_clips", runner)
    _VIDEO_CANCEL[job_id] = cancel_flag
    return {"job_id": job_id, "status": "started"}


# Cancellation flags for running video jobs. The worker polls its flag between
# shots (and ComfyUI is sent /interrupt), so cancelling stops at the next shot
# boundary and everything already generated is kept.
_VIDEO_CANCEL: dict[str, dict] = {}


@router.post("/video/jobs/{job_id}/cancel")
def video_cancel_job(job_id: str):
    flag = _VIDEO_CANCEL.get(job_id)
    if not flag:
        raise HTTPException(status_code=404,
                            detail="That video job isn't running (or can't be cancelled).")
    flag["stop"] = True
    _job_update(job_id, progress="Cancelling after the current shot...")
    return {"result": "cancelling", "job_id": job_id}


# --- Shot editing (the storyboard) ---

class ShotEditRequest(BaseModel):
    data: Optional[dict] = None
    locked_fields: Optional[list[str]] = None
    approved: Optional[bool] = None
    continuity_mode: Optional[str] = None


@router.patch("/video/shots/{shot_id}")
def video_edit_shot(shot_id: str, req: ShotEditRequest):
    """
    Hand-edit one shot. A human edit may change a LOCKED field (that's what
    locking is for — it stops *regeneration* from overwriting the choice, not
    the owner), so this passes force_locked.
    """
    from agents import video_director, video_safety, video_store
    shot = video_store.get_shot(shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail=f"Shot {shot_id} not found")

    data = req.data
    safety_notes: list[str] = []
    if data is not None:
        if "duration" in data:
            data = {**data, "duration": video_director.clamp_duration(data["duration"])}
        # Rule 30 is not waivable by hand-editing — same discipline as
        # `_sanitize_claims` still running on a manual listing edit (rule 4).
        # Enforced here AND at the render boundary (`_safe_shot_data`), because
        # a reverence guarantee should not depend on one code path.
        data, safety_notes = video_safety.enforce_shot(data)
    kwargs: dict = {}
    if req.approved is not None:
        kwargs["approved"] = bool(req.approved)
        kwargs["status"] = "approved" if req.approved else (shot.get("status") or "planned")
    if req.continuity_mode in ("continuous", "editorial_cut"):
        kwargs["continuity_mode"] = req.continuity_mode

    video_store.update_shot(shot_id, data=data, locked=req.locked_fields,
                            force_locked=True, **kwargs)
    updated = video_store.get_shot(shot_id)
    if data:  # keep the stored complexity score honest after any edit
        merged = updated["data"]
        merged["complexity_score"] = video_director.complexity_score(merged)
        video_store.update_shot(shot_id, data={"complexity_score": merged["complexity_score"]},
                                force_locked=True)
        updated = video_store.get_shot(shot_id)
    out = _shot_web_paths(updated)
    # A safeguard rewrite is always shown, never silent (rule 30).
    if safety_notes:
        out["safety_notes"] = safety_notes
    return out


class ShotAddRequest(BaseModel):
    after_number: Optional[int] = None
    data: Optional[dict] = None


@router.post("/video/projects/{project_id}/shots")
def video_add_shot(project_id: str, req: ShotAddRequest):
    from agents import video_director, video_safety, video_store
    _video_project_or_404(project_id)
    data = {**(req.data or {})}
    data.setdefault("duration", video_director.DEFAULT_SHOT_SECONDS)
    data["duration"] = video_director.clamp_duration(data["duration"])
    data.setdefault("negative_prompt", video_director.DEFAULT_NEGATIVE)
    safe, _ = video_safety.enforce_shot(data)
    safe["complexity_score"] = video_director.complexity_score(safe)
    shot_id = video_store.add_shot(project_id, safe, after_number=req.after_number)
    return _shot_web_paths(video_store.get_shot(shot_id))


@router.post("/video/shots/{shot_id}/duplicate")
def video_duplicate_shot(shot_id: str):
    from agents import video_store
    shot = video_store.get_shot(shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail=f"Shot {shot_id} not found")
    # A duplicate copies the PLAN, never the generated assets — otherwise two
    # shots would share one clip and regenerating either would confuse both.
    new_id = video_store.add_shot(shot["project_id"], shot["data"],
                                  after_number=shot["shot_number"])
    return _shot_web_paths(video_store.get_shot(new_id))


@router.delete("/video/shots/{shot_id}")
def video_delete_shot(shot_id: str):
    from agents import video_store
    if not video_store.get_shot(shot_id):
        raise HTTPException(status_code=404, detail=f"Shot {shot_id} not found")
    video_store.delete_shot(shot_id)
    return {"result": "deleted", "shot_id": shot_id}


class ReorderRequest(BaseModel):
    shot_ids: list[str]


@router.post("/video/projects/{project_id}/shots/reorder")
def video_reorder_shots(project_id: str, req: ReorderRequest):
    from agents import video_store
    _video_project_or_404(project_id)
    existing = {s["id"] for s in video_store.list_shots(project_id)}
    if set(req.shot_ids) != existing:
        raise HTTPException(
            status_code=400,
            detail="The reorder list must contain exactly the project's current shots.")
    video_store.reorder_shots(project_id, req.shot_ids)
    return {"shots": [_shot_web_paths(s) for s in video_store.list_shots(project_id)]}


@router.post("/video/shots/{shot_id}/split")
def video_split_shot(shot_id: str):
    """Split one shot into two simpler halves (the same code the planner uses)."""
    from agents import video_director, video_store
    shot = video_store.get_shot(shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail=f"Shot {shot_id} not found")
    first, second = video_director._split_shot(shot["data"])
    video_store.update_shot(shot_id, data=first, force_locked=True,
                            status="planned", clip_path=None, last_frame_path=None)
    new_id = video_store.add_shot(shot["project_id"], second, after_number=shot["shot_number"])
    return {"shots": [_shot_web_paths(video_store.get_shot(shot_id)),
                      _shot_web_paths(video_store.get_shot(new_id))]}


@router.post("/video/shots/{shot_id}/simplify")
def video_simplify_shot(shot_id: str):
    """Mechanically reduce a shot's complexity — no LLM call, instant and free."""
    from agents import video_director, video_store
    shot = video_store.get_shot(shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail=f"Shot {shot_id} not found")
    simplified = video_director.simplify_shot(shot["data"])
    video_store.update_shot(shot_id, data=simplified, force_locked=True)
    return _shot_web_paths(video_store.get_shot(shot_id))


class MergeRequest(BaseModel):
    other_shot_id: str


@router.post("/video/shots/{shot_id}/merge")
def video_merge_shots(shot_id: str, req: MergeRequest):
    from agents import video_director, video_store
    first = video_store.get_shot(shot_id)
    second = video_store.get_shot(req.other_shot_id)
    if not first or not second:
        raise HTTPException(status_code=404, detail="Both shots must exist to merge.")
    ok, reason = video_director.can_merge(first["data"], second["data"])
    if not ok:
        raise HTTPException(status_code=400, detail=reason)
    merged = video_director.merge_shots(first["data"], second["data"])
    video_store.update_shot(shot_id, data=merged, force_locked=True,
                            status="planned", clip_path=None)
    video_store.delete_shot(req.other_shot_id)
    return _shot_web_paths(video_store.get_shot(shot_id))


class ApproveRequest(BaseModel):
    shot_ids: Optional[list[str]] = None     # None = every shot in the project
    approved: bool = True


@router.post("/video/projects/{project_id}/approve")
def video_approve_shots(project_id: str, req: ApproveRequest):
    from agents import video_store
    _video_project_or_404(project_id)
    shots = video_store.list_shots(project_id)
    targets = [s for s in shots if s["id"] in set(req.shot_ids)] if req.shot_ids else shots
    for shot in targets:
        video_store.update_shot(shot["id"], approved=bool(req.approved),
                                status="approved" if req.approved else (shot.get("status") or "planned"))
    return {"updated": len(targets), "approved": bool(req.approved)}


# --- Review, validation, assembly, export ---

@router.get("/video/projects/{project_id}/validate")
def video_validate(project_id: str, vision: bool = False):
    """
    Continuity validation. Structural checks always run and are free; `vision`
    adds a paid frame-comparison pass using the existing vision model.
    """
    from agents import video_assembly
    _video_project_or_404(project_id)
    return video_assembly.validate_project(project_id, use_vision=vision)


class AssembleRequest(BaseModel):
    only_approved: bool = False
    crossfade: bool = False


@router.post("/video/projects/{project_id}/assemble")
def video_assemble(project_id: str, req: AssembleRequest):
    """
    Join clips into a draft video and write the production metadata. Metadata
    and subtitles are always written; if ffmpeg is missing or no clips exist,
    `video_path` is null and `reason` says exactly why.
    """
    from agents import video_assembly
    _video_project_or_404(project_id)
    result = video_assembly.assemble_draft(
        project_id, only_approved=req.only_approved, crossfade=req.crossfade)
    result["video_url"] = _web_image_path(result.get("video_path") or "")
    result["metadata_url"] = _web_image_path(result.get("metadata_path") or "")
    result["subtitles_url"] = _web_image_path(result.get("subtitles_path") or "")
    return result


@router.get("/video/finished")
def video_finished():
    """
    Finished videos for the Products shelf — every project with a real
    assembled file on disk, newest first.

    DERIVED from the video tables on every read, never a second saved copy of
    the fact: re-assembling a project changes what this returns, and deleting
    it removes the entry, with nothing to keep in step by hand. Videos are
    deliberately NOT rows in `products` — that would double-count them in the
    Steward's ledger and hand every bookmark/card action (print sheet, layout
    editor, Etsy publish) a product type it cannot act on.
    """
    from agents import video_assembly
    videos = []
    for item in video_assembly.list_finished():
        entry = dict(item)
        entry["video_url"] = _web_image_path(item.get("video_path") or "")
        entry["poster_url"] = _web_image_path(item.get("poster_path") or "")
        entry["metadata_url"] = _web_image_path(item.get("metadata_path") or "")
        entry["subtitles_url"] = _web_image_path(item.get("subtitles_path") or "")
        videos.append(entry)
    return {"videos": videos}


@router.get("/video/projects/{project_id}/export")
def video_export(project_id: str):
    """The full production record as JSON (shot plan, prompts, seeds, bible)."""
    from agents import video_assembly
    _video_project_or_404(project_id)
    return video_assembly.export_metadata(project_id)


@router.get("/video/projects/{project_id}/subtitles")
def video_subtitles(project_id: str):
    from agents import video_assembly
    _video_project_or_404(project_id)
    return PlainTextResponse(video_assembly.export_subtitles(project_id),
                             media_type="text/plain; charset=utf-8")
