"""
The Products shelf, as a bounded list (rule 116).

`GET /products` returns EVERY column of EVERY product: the full Etsy listing
JSON, the reviewer's whole scorecard, the layout, and -- the big one -- the
entire consultation transcript, which the shelf does not display at all. On a
laptop that is also running Ollama and ComfyUI, opening the Products tab meant
downloading and parsing every word every agent had ever said about every
product, to draw a grid of thumbnails.

This module adds a summary beside it. It does NOT replace it: `GET /products`
keeps its shape and its callers, because a list endpoint that quietly starts
returning different rows is how a working feature breaks somewhere nobody was
looking (the same reasoning as `confirmed_decision` staying singular in rule 98).

Two things this is careful about:

* **Filtering is global, paging is local.** Search, kind and badge filters and
  the sort are applied to the WHOLE shelf, then a page is cut from the result.
  A filter that only saw the loaded page would silently answer a different
  question from the one the bar appears to ask.
* **A video is still derived, never a row** (rule 58). Finished videos are
  merged in on every read from the video tables, exactly as the dashboard did
  in the browser, with the same guarantees: only a file that exists, a MEASURED
  length, a mock draft labelled as one, and videos dropping out under a
  review-result filter because they are reviewed shot by shot rather than
  scored out of ten.

No badge or score arithmetic is duplicated here. The parsed `review_overall` and
`target_reached` are handed back and the dashboard's existing `badgeForProduct`
decides, so the two can never drift apart.
"""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/products", tags=["products"])

# Columns the shelf actually draws. Everything else -- listing_copy in full,
# reviewer_scores in full, consultation, layout_json -- is fetched when a
# product's drawer opens, from the detail endpoint that already exists.
_SUMMARY_COLUMNS = (
    "id", "task_id", "title", "theme", "product_type", "status", "created_at",
    "image_url", "front_image", "back_image", "etsy_listing_id", "revenue",
    "recipient_feedback", "target_reached", "attempts",
)

SORTS = ("newest", "oldest", "score", "score_low", "title")


def _loads(raw) -> dict:
    try:
        value = json.loads(raw) if isinstance(raw, str) else (raw or {})
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def _summarize(row: dict) -> dict:
    """One shelf entry: what is drawn, plus what the search box searches."""
    listing = _loads(row.get("listing_copy"))
    review = _loads(row.get("reviewer_scores"))
    is_card = (row.get("product_type") or "bookmark") == "quote_card"
    card = listing if is_card else {}

    out = {k: row.get(k) for k in _SUMMARY_COLUMNS}
    out["review_overall"] = float(review.get("overall") or 0)
    # Small display fields only -- the printed quote and its citation, which the
    # card in the grid shows. Not the description, not the tags, not the
    # transcript.
    out["quote"] = (card.get("quote") if is_card else listing.get("bookmark_quote")) or ""
    out["citation"] = card.get("citation", "") if is_card else ""
    out["language_name"] = card.get("language_name", "") if is_card else ""
    # Which check verified this product's quotation, so an old overlap pass is
    # distinguishable from an exact one (rule 111). Absent means the retired
    # check, and the drawer says so rather than implying today's standard.
    out["quote_verification_method"] = _loads(row.get("quote_verification")).get("method", "")
    # The words somebody would actually type to find this again, joined once
    # here rather than rebuilt in the browser on every keystroke.
    out["search"] = " ".join(str(x) for x in [
        row.get("title"), row.get("theme"), row.get("id"),
        out["quote"], out["citation"], out["language_name"],
        listing.get("description", "") if not is_card else "",
        " ".join(listing.get("tags") or []) if not is_card else "",
        f"etsy {row['etsy_listing_id']}" if row.get("etsy_listing_id") else "",
    ] if x).lower()
    return out


def _video_entries() -> list[dict]:
    """Finished videos, derived on every read (rule 58)."""
    try:
        from agents.video_assembly import list_finished
        entries = list_finished()
    except Exception:
        # A broken video shelf must not take the product shelf with it; the
        # dashboard already shows its own error for the video half.
        return []
    out = []
    for v in entries:
        out.append({
            **v,
            "kind": "video",
            "product_type": "video",
            "review_overall": None,
            "search": " ".join(str(x) for x in [
                v.get("title"), v.get("project_id"), v.get("id")] if x).lower(),
        })
    return out


@router.get("/summary")
def products_summary(
    limit: int = 60,
    offset: int = 0,
    kind: str = "all",
    search: str = "",
    sort: str = "newest",
    badge: Optional[str] = None,
    include_videos: bool = True,
):
    """
    A page of the shelf, with the filters applied to all of it.

    Registered BEFORE `/products/{product_id}` in `api.py`, or the dynamic route
    would swallow the literal path and "summary" would be looked up as a product
    id -- a real and easy mistake with FastAPI's first-match routing.
    """
    if sort not in SORTS:
        raise HTTPException(status_code=400, detail=f"Unknown sort: {sort}")
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400,
                            detail="A page is between 1 and 200 items.")

    from agents.state import get_all_products

    items = [{**_summarize(p), "kind": "product"} for p in get_all_products()]
    all_videos = _video_entries() if include_videos else []

    # The chips show how many of each kind there are, and they have to count the
    # WHOLE shelf, not the page -- a count that described only what happened to
    # be loaded would be worse than no count at all.
    counts = {"all": len(items) + len(all_videos), "video": len(all_videos)}
    for i in items:
        key = i.get("product_type") or "bookmark"
        counts[key] = counts.get(key, 0) + 1
    counts.setdefault("bookmark", 0)
    counts.setdefault("quote_card", 0)

    videos_dropped = 0
    if include_videos:
        videos = all_videos
        if badge:
            # Rule 58: a video has no score out of ten, so it cannot satisfy a
            # review-result filter. It DROPS OUT, and the bar says why rather
            # than leaving somebody to wonder where their videos went.
            videos_dropped = len(videos)
        else:
            items += videos

    if kind and kind != "all":
        items = [i for i in items if (i.get("product_type") or "bookmark") == kind]
    if badge:
        # `target_reached == 0` is BEST EFFORT whatever the score; every other
        # badge is a score band. Kept in step with the dashboard's own
        # `badgeForProduct` by using the same two inputs and nothing else.
        want = badge.upper()
        def _badge(i: dict) -> str:
            if i.get("target_reached") == 0:
                return "BEST EFFORT"
            score = i.get("review_overall") or 0
            if score >= 9.5:
                return "EXCEPTIONAL"
            if score >= 9:
                return "APPROVED"
            if score >= 7:
                return "BORDERLINE"
            return "REJECTED"
        items = [i for i in items if _badge(i) == want]
    if search.strip():
        needle = search.strip().lower()
        items = [i for i in items if needle in (i.get("search") or "")]

    reverse = sort in ("newest", "score")
    if sort in ("newest", "oldest"):
        items.sort(key=lambda i: str(i.get("created_at") or ""), reverse=reverse)
    elif sort in ("score", "score_low"):
        # A video has no score. It sorts to the end either way rather than
        # pretending to be a zero, which would rank it below a rejected product.
        items.sort(key=lambda i: (i.get("review_overall") is None,
                                  -(i.get("review_overall") or 0) if reverse
                                  else (i.get("review_overall") or 0)))
    else:
        items.sort(key=lambda i: str(i.get("title") or "").lower())

    total = len(items)
    page = items[offset:offset + limit]
    return {
        "items": page,
        "counts": counts,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(page) < total,
        # Said out loud, because a filter that silently removes a whole kind of
        # thing is indistinguishable from a bug.
        "videos_hidden": videos_dropped,
        "videos_hidden_note": (
            "Videos are not shown while a review result is selected: they are reviewed "
            "shot by shot, not scored out of ten." if videos_dropped else ""),
    }
