"""
The human-approved X (@peaceAntz) giveaway pipeline's HTTP routes, extracted
from api.py. Business logic lives in agents/x_post.py; the routes here store
and manage the draft in agents.state's pending_x_posts table before Sheraj
approves a post.

No prefix on this router -- see wallet_api.py's docstring for why.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agents.jobs import _start_job, _web_image_path

router = APIRouter()


# --- Pipeline: Post to X (@peaceAntz) — giveaway outreach, never sold, never
# auto-posted. A background job like /pipeline/run and /pipeline/run-card:
# the consultation's round-2 pause genuinely blocks the worker thread
# awaiting Sheraj's guidance, so this can no longer answer synchronously.

class XPostRequest(BaseModel):
    topic: str
    include_quote: bool = True  # False: original reflection, no locked/attributed quote


def _run_x_post_job(req: XPostRequest, progress, on_turn=None, request_human_input=None) -> dict:
    """
    Runs the full pipeline (Librarian -> locked quote -> Artist -> consultation
    with round-2 pause -> Scribe -> Reviewer QA loop) and saves the drafted
    tweet to pending_x_posts for approval. Returns the job's `result` payload.
    """
    from agents.state import create_pending_x_post
    from agents.x_post import run_x_post_pipeline

    result = run_x_post_pipeline(req.topic, include_quote=req.include_quote, progress=progress,
                                 on_turn=on_turn, request_human_input=request_human_input)
    review = result["review"]
    post_id = create_pending_x_post(
        topic=result["topic"],
        tweet_text=result["tweet_text"],
        quote_locked=result["quote_locked"],
        quote_author=result["quote_author"],
        constitution_score=review.get("overall", 0.0),
        image_path=result["image_path"],
        image_prompt=result.get("image_prompt"),
        include_quote=result["include_quote"],
        inspired_by=result.get("inspired_by", ""),
    )
    return {
        "id": post_id,
        "topic": result["topic"],
        "tweet_text": result["tweet_text"],
        "image_path": result["image_path"],
        "image_web": _web_image_path(result["image_path"]) if result["image_path"] else None,
        "include_quote": result["include_quote"],
        "quote_locked": result["quote_locked"],
        "quote_author": result["quote_author"],
        "citation": result["citation"],
        "inspired_by": result.get("inspired_by", ""),
        "attempts": result["attempts"],
        "review": review,
        "consultation": result["consultation"],
    }


@router.post("/x-post")
def x_post_create(req: XPostRequest):
    """
    Dashboard entry point: run the whole pipeline — including the team's
    consultation and its round-2 pause for Sheraj's guidance — as a
    background job. Returns {job_id} immediately; poll
    GET /pipeline/status/{job_id} and POST .../respond for the pause, same
    as the bookmark and card pipelines.
    """
    if not req.topic.strip():
        raise HTTPException(status_code=422, detail="topic is required")
    job_id = _start_job(
        "x-post",
        lambda progress, on_turn, ask: _run_x_post_job(req, progress, on_turn, ask),
    )
    return {"job_id": job_id, "status": "running"}


@router.get("/x-post/pending")
def x_post_pending():
    from agents.state import get_pending_x_posts
    rows = get_pending_x_posts("pending")
    for r in rows:
        r["image_web"] = _web_image_path(r.get("image_path")) if r.get("image_path") else None
    return rows


@router.get("/x-post/drafts")
def x_post_drafts():
    """
    Posts Sheraj liked but wanted to think over before approving — set aside
    via POST /x-post/{id}/save-draft, out of the Pending approval list until
    she comes back to them.
    """
    from agents.state import get_pending_x_posts
    rows = get_pending_x_posts("draft")
    for r in rows:
        r["image_web"] = _web_image_path(r.get("image_path")) if r.get("image_path") else None
    return rows


@router.get("/x-post/posted")
def x_post_posted():
    """
    Permanent record of what actually got posted — the history Sheraj asked
    for. Discarded drafts are deleted outright (see x_post_discard) and never
    appear here; this only ever grows with real (or dry-run) posts.
    """
    from agents.state import get_pending_x_posts
    from agents.x_post import X_HANDLE
    rows = get_pending_x_posts("approved")
    for r in rows:
        r["image_web"] = _web_image_path(r.get("image_path")) if r.get("image_path") else None
        tweet_id = r.get("posted_tweet_id")
        r["posted_url"] = f"https://x.com/{X_HANDLE}/status/{tweet_id}" if tweet_id else None
    return rows


class XPostEditRequest(BaseModel):
    tweet_text: str


@router.patch("/x-post/{post_id}")
def x_post_edit(post_id: str, req: XPostEditRequest):
    """
    Hand-edit a pending draft's tweet text directly — bypasses the Scribe/
    Reviewer pipeline entirely, same discipline as PATCH /products/{id} for
    bookmarks. Only pending drafts are editable; the locked quote/author and
    image aren't touched (this edits the tweet's wording only).
    """
    from agents.state import get_x_post, update_x_post
    from agents.x_post import TWEET_DRAFT_MAX, TWEET_HARD_MAX

    post = get_x_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.get("status") not in ("pending", "draft"):
        raise HTTPException(status_code=422, detail=f"Post is already {post.get('status')} — only pending/draft posts can be edited")

    text = req.tweet_text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="tweet_text cannot be empty")
    # Draft budget leaves room for the code-appended AI-art disclosure at post time
    if len(text) > TWEET_DRAFT_MAX:
        raise HTTPException(status_code=422,
                            detail=(f"tweet_text is {len(text)} characters — exceeds the "
                                    f"{TWEET_DRAFT_MAX} draft maximum (must leave room for "
                                    f"the AI-art disclosure; posted hard limit is {TWEET_HARD_MAX})"))

    update_x_post(post_id, tweet_text=text)
    return {"id": post_id, "tweet_text": text}


class XPostRegenerateImageRequest(BaseModel):
    guidance: str = ""   # optional — unlike the bookmark equivalent, works fine with none


@router.post("/x-post/{post_id}/regenerate-image")
def x_post_regenerate_image(post_id: str, req: XPostRegenerateImageRequest):
    """
    Swap out a pending draft's image. With guidance, repaints toward that
    steer (same "append an IMPORTANT direction" pattern as the bookmark
    pipeline's regenerate-image); with none, just re-rolls the same prompt —
    image generation is stochastic, so this alone produces a genuinely
    different image without changing the creative direction. Only pending
    drafts can be re-imaged; the tweet text and locked quote are untouched.
    """
    from agents.state import get_x_post, update_x_post
    from agents.artist import build_x_post_image_prompt, generate_image

    post = get_x_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.get("status") not in ("pending", "draft"):
        raise HTTPException(status_code=422, detail=f"Post is already {post.get('status')} — only pending/draft posts can be re-imaged")

    old_prompt = post.get("image_prompt") or ""
    if not old_prompt:
        # Defensive fallback for a row saved before image_prompt was tracked.
        old_prompt = build_x_post_image_prompt(post.get("topic") or "", "Serene and luminous")

    guidance = req.guidance.strip()
    new_prompt = f"{old_prompt}\n\nIMPORTANT new direction: {guidance}" if guidance else old_prompt

    try:
        gen = generate_image(new_prompt, "16:9")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Image generation error: {e}")
    new_image_path = gen.get("image_url", "")

    update_x_post(post_id, image_path=new_image_path, image_prompt=new_prompt)
    return {
        "id": post_id,
        "image_path": new_image_path,
        "image_web": _web_image_path(new_image_path) if new_image_path else None,
    }


@router.post("/x-post/{post_id}/save-draft")
def x_post_save_draft(post_id: str):
    """
    Sets a pending post aside as a draft — liked, but not ready to approve
    yet. Moves it out of Pending approval into GET /x-post/drafts; every
    other action (edit, new image, approve, discard) still works on it.
    """
    from agents.state import get_x_post, update_x_post

    post = get_x_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.get("status") != "pending":
        raise HTTPException(status_code=422, detail=f"Post is already {post.get('status')} — only pending posts can be saved as a draft")

    update_x_post(post_id, status="draft")
    return {"id": post_id, "status": "draft"}


@router.post("/x-post/{post_id}/restore")
def x_post_restore(post_id: str):
    """Moves a draft back into Pending approval."""
    from agents.state import get_x_post, update_x_post

    post = get_x_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.get("status") != "draft":
        raise HTTPException(status_code=422, detail=f"Post is {post.get('status')}, not a draft")

    update_x_post(post_id, status="pending")
    return {"id": post_id, "status": "pending"}


@router.post("/x-post/approve/{post_id}")
def x_post_approve(post_id: str):
    from agents.state import get_x_post, update_x_post
    from agents.x_post import post_tweet

    post = get_x_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.get("status") not in ("pending", "draft"):
        raise HTTPException(status_code=409, detail=f"Post is already {post.get('status')}")

    try:
        result = post_tweet(post["tweet_text"], post.get("image_path"))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"X post failed: {e}")

    update_x_post(post_id, status="approved", posted_tweet_id=result.get("tweet_id"))
    return {
        "id": post_id,
        "status": "approved",
        "dry_run": result.get("dry_run", False),
        "posted_tweet_id": result.get("tweet_id"),
        "url": result.get("url"),
        "text": result.get("text"),
    }


@router.post("/x-post/discard/{post_id}")
def x_post_discard(post_id: str):
    """Discards for good — no 'discarded' status kept around; only what
    actually got posted is worth remembering (see GET /x-post/posted)."""
    from agents.state import get_x_post, delete_x_post
    post = get_x_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    delete_x_post(post_id)
    return {"id": post_id, "status": "discarded"}
