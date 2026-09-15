"""
bahAI Workforce — FastAPI backend for the React dashboard.
Runs on port 8765. The dashboard (dashboard/) calls these endpoints; the heavy
lifting lives in the sibling agent modules (librarian, artist, consultation,
scribe, reviewer, compositor, canva, etsy).

Start with: python agents/api.py  (from project root)

This file is the APP FACTORY, and little else (split 2026-09-14 -- see the
"Parallel work" table in AGENTS.md): FastAPI app + owner-gate middleware +
CORS + startup, the background job store's routes (the store itself lives in
agents/jobs.py), the product-type-agnostic product routes (list/get/edit/
layout/print-sheet -- generic across bookmarks and cards), Steward/deeds/
trust reporting, and the `include_router(...)` calls that bring in every
subsystem: agents/{pipeline,card,video,colony,secretary,wallet,x}_api.py plus
the pre-existing products_api.py, live_consultation_api.py, gathering_api.py
and home_api.py.

Map of this file:
  1. Background job store routes  — long pipelines run in worker threads
                                    (agents/jobs.py); dashboard polls
                                    /pipeline/status/{job_id}
  2. Products endpoints           — list/get/manual-edit/layout/print-sheet
                                    (product-type-agnostic; bookmark- and
                                    card-only routes live in their own *_api.py)
  3. Steward (P&L) + Trust report + health
  4. Router includes for every subsystem, near the bottom of the file
"""

import json
import os
import re
import uvicorn
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv

# Load .env before any submodule imports so all os.getenv() calls see the values
load_dotenv(dotenv_path=str(Path(__file__).parent.parent / ".env"), override=True)

from agents import layout as layout_opts
from agents.state import (
    init_db, update_task_status, log_run, get_all_agent_statuses,
    create_product, update_product, get_all_products,
    add_distribution, get_deeds_summary, DISTRIBUTION_KINDS,
)
from agents.jobs import (
    JOBS, _JOBS_LOCK, _PENDING_INPUT, _PENDING_LOCK, create_task, _start_job,
    _web_image_path, _badge, _load_product_or_404, _require_bookmark,
    _print_pairs_for,
)

app = FastAPI(title="bahAI Workforce API", version="1.0")

# Owner-only, enforced on every request (rule 70). The dashboard's Vite proxy
# adds the key, so nothing in dashboard/src changes.
from agents.auth import api_key_middleware  # noqa: E402
app.middleware("http")(api_key_middleware)

# CORS is deliberately NOT wildcarded (rule 70). The dashboard reaches this API
# through Vite's same-origin /api proxy -- including images and video -- so it
# never makes a cross-origin request and needs no CORS grant at all. The
# wildcard that used to sit here served only a page trying to READ the reply.
_CORS_ORIGINS = [o.strip() for o in os.getenv("DASHBOARD_ORIGINS", "").split(",") if o.strip()]
if _CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware, allow_origins=_CORS_ORIGINS,
        allow_methods=["*"], allow_headers=["*"], allow_credentials=True)

# Serve generated bookmark images to the dashboard at http://localhost:8765/outputs/<filename>
OUTPUTS_DIR = Path(__file__).parent.parent / "outputs"
OUTPUTS_DIR.mkdir(exist_ok=True)
app.mount("/outputs", StaticFiles(directory=str(OUTPUTS_DIR)), name="outputs")



# --- Startup ---

@app.on_event("startup")
def on_startup():
    init_db()
    # Material World store — its own private file, never workforce.db (rule 59).
    from agents import nuclei_store
    nuclei_store.init_db()
    # Live Consultation store — its own private file too (rule 73). Meeting
    # transcripts never touch workforce.db.
    from agents import live_consultation_store
    live_consultation_store.init_db()
    # Retention is defined against a clock that keeps running while this
    # application is closed, so the catch-up belongs at startup (rule 106). A
    # machine switched off through the seventh day deletes on the next start --
    # which is exactly what the setting promises, and what the UI says.
    try:
        from agents.live_consultation_api import retention_sweep
        swept = retention_sweep(force=True)
        if swept.get("deleted"):
            print(f"Consultation retention: {len(swept['deleted'])} transcript(s) "
                  f"deleted on the retention schedule.")
        if swept.get("failed"):
            print(f"Consultation retention: {len(swept['failed'])} could NOT be "
                  f"deleted -- see the Consultation tab.")
    except Exception as e:
        # Never let a retention sweep stop the API from coming up.
        print(f"Consultation retention sweep skipped ({type(e).__name__}).")
    # Secretary's reminder scheduler — all state in private/secretary.db, so a
    # restart resumes exactly where it left off.
    from agents import scheduler
    scheduler.start()
    cid = os.getenv("CANVA_CLIENT_ID", "")
    print(f"bahAI Workforce API ready. SQLite DB initialised.")
    print(f"CANVA_CLIENT_ID loaded: {bool(cid)} ({cid[:8] if cid else 'EMPTY'})")
    print(f"CANVA_TEMPLATE_ID: {os.getenv('CANVA_TEMPLATE_ID', 'EMPTY')}")



@app.get("/pipeline/status/{job_id}")
def pipeline_status(job_id: str):
    """Poll a background job. status: running | waiting_for_input | done | error."""
    with _JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return dict(job)


class JobRespondRequest(BaseModel):
    text: str = ""

@app.post("/pipeline/status/{job_id}/respond")
def pipeline_respond(job_id: str, req: JobRespondRequest):
    """
    Submit Sheraj's input for a job currently paused at status
    'waiting_for_input' (after consultation round 2, before the Scribe
    writes). Wakes the paused worker thread; empty text means 'no guidance,
    continue as-is.'
    """
    with _PENDING_LOCK:
        entry = _PENDING_INPUT.get(job_id)
        if not entry:
            raise HTTPException(status_code=409, detail="This job isn't waiting for input right now.")
        entry["response"] = req.text.strip()
        entry["event"].set()
    return {"status": "received"}


@app.post("/pipeline/status/{job_id}/cancel")
def pipeline_cancel(job_id: str):
    """
    Stop a running job so Sheraj can start over.

    Cooperative by necessity (see JobCancelled): this flags the job and wakes it
    if it is paused for input; the worker stops at its next step boundary,
    normally within seconds, and at worst when the API call already in flight
    returns. The response says which of those is happening rather than claiming
    the run is already dead — the panel keeps polling until the status really
    is 'cancelled', so the form never unlocks while work is still in the air.
    """
    with _JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job["status"] in ("done", "error", "cancelled"):
            return {"status": job["status"], "already_finished": True,
                    "message": f"That run already {job['status']} — nothing to cancel."}
        job["cancel_requested"] = True
        job["progress"] = "Stopping after the current step..."
        job["updated_at"] = datetime.utcnow().isoformat()
        job.setdefault("steps", []).append(
            {"ts": job["updated_at"], "message": "Cancel requested by Sheraj."})

    # A run paused at the consultation check-in is asleep on this event and
    # would otherwise sit there until the 30-minute timeout.
    with _PENDING_LOCK:
        entry = _PENDING_INPUT.get(job_id)
        if entry:
            entry["response"] = ""
            entry["event"].set()

    return {"status": "cancelling", "job_id": job_id,
            "message": "Stopping. The step already running finishes first — anything "
                       "finished before now is kept, nothing new starts."}


@app.get("/pipeline/jobs")
def pipeline_jobs():
    """Recent background jobs, newest first (lets the dashboard reattach after a refresh)."""
    with _JOBS_LOCK:
        jobs = sorted(JOBS.values(), key=lambda j: j["created_at"], reverse=True)
        return [
            {k: v for k, v in j.items() if k != "result"} | {"has_result": j["result"] is not None}
            for j in jobs[:20]
        ]


# --- Agent status (raw trust rows; the dashboard's Colony tab uses /colony) ---

@app.get("/agents")
def list_agents():
    return get_all_agent_statuses()


# --- The bookmark pipeline (rules 1-14, 29) ----------------------------------
# Moved to agents/pipeline_api.py: write/approve, /pipeline/run, targeted
# regeneration, and Canva + Etsy -- included near the bottom of this file.

# --- The quote-card pipeline (rules 1-14 by extension, 11, 29) --------------
# Moved to agents/card_api.py: write/consult/render/score/revise,
# /pipeline/run-card(-batch), quote sources, the Librarian's quote finder,
# and targeted regeneration.


# --- Products endpoints ---

@app.get("/products")
def list_products():
    """List all saved products, newest first.

    Every column of every row, including the whole consultation transcript. Kept
    exactly as it was for its existing callers; the shelf itself now opens
    through `/products/summary`, which is bounded (rule 116).
    """
    return get_all_products()


# Registered HERE, above `/products/{product_id}`: FastAPI matches routes in
# registration order, so a literal path declared after a dynamic sibling is
# swallowed by it and "summary" would be looked up as a product id.
from agents.products_api import router as products_router   # noqa: E402

app.include_router(products_router)


@app.get("/products/{product_id}")
def get_product(product_id: str):
    from agents.state import _connect
    with _connect() as conn:
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Product not found")
    return dict(row)


# --- Improve, and targeted regeneration -- see agents/pipeline_api.py.


@app.get("/products/{product_id}/print-sheet")
def get_print_sheet(product_id: str):
    """
    Render a cut-tolerant, multi-up print sheet for this product's saved
    front/back faces: a single 2-page Letter PDF (page 1 = fronts grid,
    page 2 = backs grid), regenerated fresh from the CURRENT front_image/
    back_image every call so it always reflects the latest artwork.
    A translated quote card's per-language pairs are cycled in too — a
    bilingual card prints half English, half Spanish. Card size and grid
    count are derived automatically from the face images themselves --
    see agents/print_sheet.py.
    """
    from agents.print_sheet import build_print_sheet

    product = _load_product_or_404(product_id)
    front_path = product.get("front_image")
    back_path = product.get("back_image")
    if not front_path or not back_path:
        raise HTTPException(
            status_code=422,
            detail="This product doesn't have both a front and back image saved yet.",
        )
    if not Path(front_path).exists() or not Path(back_path).exists():
        raise HTTPException(
            status_code=404,
            detail="The saved front/back image files are missing on disk.",
        )

    out_path = OUTPUTS_DIR / f"print-sheet-{product_id}.pdf"
    try:
        build_print_sheet(pairs=_print_pairs_for(product), out_pdf_path=str(out_path))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not build the print sheet: {e}")

    safe_title = re.sub(r"[^A-Za-z0-9]+", "-", product.get("title") or "card").strip("-") or "card"
    return FileResponse(
        path=str(out_path),
        media_type="application/pdf",
        filename=f"{safe_title}-print-sheet.pdf",
    )


class MixedPrintSheetRequest(BaseModel):
    product_ids: list[str]
    duplex: bool = False
    # Cycle translated cards' per-language pairs into the sheet (half
    # English / half Spanish on a bilingual card). On by default.
    include_variants: bool = True


@app.post("/print-sheet")
def post_mixed_print_sheet(body: MixedPrintSheetRequest):
    """
    Gathering print sheet: tile a SET of products (same product_type, each
    with both faces) onto one 2-page Letter PDF, cycling through them in
    order across the grid. duplex=True mirrors columns on page 2 for
    long-edge home duplex printers (see agents/print_sheet.build_print_sheet).
    """
    from agents.print_sheet import build_print_sheet

    product_ids = [str(pid).strip() for pid in (body.product_ids or []) if str(pid).strip()]
    if not product_ids:
        raise HTTPException(
            status_code=422,
            detail="Pick at least one product to put on the print sheet.",
        )

    products = []
    for pid in product_ids:
        try:
            products.append(_load_product_or_404(pid))
        except HTTPException as e:
            if e.status_code == 404:
                raise HTTPException(
                    status_code=422,
                    detail=f"No product found with id '{pid}'. Check the id and try again.",
                )
            raise

    types = {(p.get("product_type") or "bookmark") for p in products}
    if len(types) > 1:
        raise HTTPException(
            status_code=422,
            detail="All products on one sheet must be the same type "
                   "(don't mix bookmarks and quote cards).",
        )

    pairs = []
    for p in products:
        front_path = p.get("front_image")
        back_path = p.get("back_image")
        if not front_path or not back_path:
            title = p.get("title") or p["id"]
            raise HTTPException(
                status_code=422,
                detail=f"'{title}' is missing a front or back image — "
                       "render both faces before building a print sheet.",
            )
        if not Path(front_path).exists() or not Path(back_path).exists():
            title = p.get("title") or p["id"]
            raise HTTPException(
                status_code=422,
                detail=f"The front/back image files for '{title}' are missing on disk.",
            )
        pairs.extend(_print_pairs_for(p, include_variants=body.include_variants))

    stem = "print-sheet-mixed-" + "-".join(product_ids[:6])
    if body.duplex:
        stem += "-duplex"
    out_path = OUTPUTS_DIR / f"{stem}.pdf"
    try:
        build_print_sheet(pairs=pairs, out_pdf_path=str(out_path), duplex=bool(body.duplex))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not build the print sheet: {e}")

    return FileResponse(
        path=str(out_path),
        media_type="application/pdf",
        filename=f"{stem}.pdf",
        headers={"X-Product-Ids": ",".join(product_ids)},
    )


# --- Card regeneration -- see agents/card_api.py.


class ProductEditRequest(BaseModel):
    """Manual, human edit to a saved listing — no LLM involved. Only fields
    the caller actually sets are changed; everything else is left as-is."""
    title: Optional[str] = None
    description: Optional[str] = None
    bookmark_quote: Optional[str] = None
    tags: Optional[list[str]] = None
    materials: Optional[list[str]] = None
    price_note: Optional[str] = None

@app.patch("/products/{product_id}")
def edit_product(product_id: str, req: ProductEditRequest):
    """Directly overwrite one or more listing fields with human-supplied text.
    Bypasses the Scribe/Reviewer pipeline — for when Sheraj wants to hand-fix a
    listing rather than re-run consultation. Two honesty guardrails still apply
    and are NOT bypassable by a hand edit:

      * the same deterministic scrub every pipeline write ends in
        (scribe._sanitize_claims) runs on the edited marketing text, so a typed
        "handcrafted" or motif count is normalised exactly as if an agent had
        written it (rules 4 & 8). It only touches title/description/price_note/
        tags — never the quote itself.
      * editing bookmark_quote is allowed (owner decision, 2026-07) but the
        result is no longer Librarian-verified: the product is flagged
        quote_verified=false (the dashboard shows a "quote no longer verified"
        note), and the printed face is re-rendered on the same artwork so the
        image never silently disagrees with the new words.
    """
    from agents.state import _connect
    from agents.scribe import _sanitize_claims

    with _connect() as conn:
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Product not found")

    product = dict(row)
    # Manual listing edits are bookmark-only: editing a card's stored text
    # would silently diverge from the already-rendered PNGs.
    _require_bookmark(product)
    listing_copy = product.get("listing_copy", "{}")
    listing = json.loads(listing_copy) if listing_copy else {}

    edits = req.model_dump(exclude_unset=True)
    if not edits:
        raise HTTPException(status_code=400, detail="No fields provided to edit")

    old_quote = (listing.get("bookmark_quote") or "").strip()
    for field, value in edits.items():
        listing[field] = value
    new_quote = (listing.get("bookmark_quote") or "").strip()
    quote_changed = "bookmark_quote" in edits and new_quote != old_quote

    # Non-bypassable honesty scrub (marketing text only; never the quote).
    listing = _sanitize_claims(listing)

    if quote_changed:
        # A hand-typed quote is not Librarian-verified — mark it so nothing
        # downstream or on the dashboard presents it as grounded scripture.
        listing["quote_verified"] = False

    update_kwargs = {"listing_copy": json.dumps(listing)}
    if "title" in edits:
        update_kwargs["title"] = listing.get("title")

    rerender_note = None
    if quote_changed:
        # Keep the printed face in sync with the new quote — same artwork, the
        # product's saved layout. A re-render hiccup must never block the text
        # edit, so it degrades to a note.
        try:
            from agents.compositor import render_bookmark_pair
            layout = layout_opts.sanitize("bookmark", json.loads(product.get("layout_json") or "null"))
            rendered = render_bookmark_pair(product.get("image_url") or "",
                                            listing.get("bookmark_quote") or "", layout=layout)
            update_kwargs["front_image"] = rendered["front_path"]
            update_kwargs["back_image"] = rendered["back_path"]
        except Exception as e:
            rerender_note = f"Text saved, but the printed face could not be re-rendered ({e})."

    update_product(product_id, **update_kwargs)

    return {"product_id": product_id, "listing": listing,
            "quote_verified": listing.get("quote_verified", True),
            "rerender_note": rerender_note}


# --- Visual layout editor (both product types) -------------------------------
#
# Lets Sheraj adjust how a face LOOKS — font, text size/position, colour,
# gradient/vignette, the star/rule toggles — and re-render, without ever
# touching what it SAYS. The printed quote, citation, translation, and the
# code-written disclaimers are pulled from the product's stored data at render
# time (below); nothing about the text can be edited through this path, so the
# honesty rules that govern that text (locked bookmark quote, verbatim Ruhi
# Book 1 card quote, script-verified translation fonts, code-appended
# disclosures) are preserved by construction. agents.layout.sanitize clamps
# every incoming value to a safe range before it reaches a compositor.

def _render_product_faces(product: dict, layout: dict, dest_stem: str | None = None) -> dict:
    """
    Re-render a product's front/back faces with the given (already-sanitised)
    layout, reading ALL text from the product's stored data — never from the
    request. Dispatches on product_type. Returns {front_path, back_path}.
    Raises HTTPException(422) on a missing artwork or text that won't fit.
    """
    ptype = product.get("product_type") or "bookmark"
    image_path = product.get("image_url") or ""
    if not image_path or not Path(image_path).exists():
        raise HTTPException(
            status_code=422,
            detail="This product's original artwork file is missing, so it can't be re-rendered.",
        )
    try:
        if ptype == "quote_card":
            from agents.card_compositor import render_quote_card
            card_copy = json.loads(product.get("listing_copy") or "{}")
            reflection = _reflection_from_card_copy(card_copy)
            rendered = render_quote_card(
                image_path,
                card_copy.get("quote") or "",
                card_copy.get("citation") or "",
                translation=_card_translation_dict(card_copy),
                layout=layout, dest_stem=dest_stem,
                reflection=reflection,
            )
            # Durable layout saves (no dest_stem) refresh variant_faces so the
            # stored per-language pair paths stay in sync with the re-render.
            # Preview stems leave listing_copy alone.
            if dest_stem is None:
                card_copy["variant_faces"] = _variant_faces_from_rendered(rendered)
                pid = product.get("id")
                if pid:
                    update_product(pid, listing_copy=json.dumps(card_copy))
            return rendered
        from agents.compositor import render_bookmark_pair
        listing = json.loads(product.get("listing_copy") or "{}")
        return render_bookmark_pair(
            image_path, listing.get("bookmark_quote") or "",
            layout=layout, dest_stem=dest_stem,
        )
    except HTTPException:
        raise
    except ValueError as e:
        # Card compositor raises when text won't fit legibly (e.g. text size
        # cranked too high) — surface it as guidance, not a 500.
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not re-render the faces: {e}")


class LayoutRequest(BaseModel):
    layout: dict = {}


@app.get("/products/{product_id}/layout")
def get_product_layout(product_id: str):
    """Current layout (defaults filled in) plus the fonts/colours/ranges the
    dashboard needs to render the editor controls for this product type."""
    product = _load_product_or_404(product_id)
    ptype = product.get("product_type") or "bookmark"
    saved = None
    if product.get("layout_json"):
        try:
            saved = json.loads(product["layout_json"])
        except (json.JSONDecodeError, TypeError):
            saved = None
    return {
        "product_id": product_id,
        "current": layout_opts.sanitize(ptype, saved),
        "has_saved": bool(saved),
        **layout_opts.options(ptype),
    }


@app.post("/products/{product_id}/layout/preview")
def preview_product_layout(product_id: str, req: LayoutRequest):
    """Re-render with the requested layout to temporary per-product preview
    files WITHOUT saving — drives the live preview as Sheraj adjusts controls."""
    product = _load_product_or_404(product_id)
    ptype = product.get("product_type") or "bookmark"
    clean = layout_opts.sanitize(ptype, req.layout)
    rendered = _render_product_faces(product, clean, dest_stem=f"layout-preview-{product_id}")
    return {
        "front_image_web": _web_image_path(rendered["front_path"]),
        "back_image_web": _web_image_path(rendered["back_path"]),
        "layout": clean,
    }


@app.post("/products/{product_id}/layout")
def save_product_layout(product_id: str, req: LayoutRequest):
    """Re-render with the requested layout and SAVE it as the product's
    front/back faces (and remember the layout for next time). Text is untouched;
    only presentation changes, so no review/score is affected."""
    product = _load_product_or_404(product_id)
    ptype = product.get("product_type") or "bookmark"
    clean = layout_opts.sanitize(ptype, req.layout)
    rendered = _render_product_faces(product, clean)  # fresh files for the durable render
    update_product(
        product_id,
        front_image=rendered["front_path"], back_image=rendered["back_path"],
        layout_json=json.dumps(clean),
    )
    # Mechanical human-driven edit — passed_review=None so it never moves the
    # compositor's trust score (CLAUDE.md rule 14).
    log_run(product_id, "compositor", "layout_edit", ptype, json.dumps(clean)[:200])
    return {
        "product_id": product_id,
        "front_image_web": _web_image_path(rendered["front_path"]),
        "back_image_web": _web_image_path(rendered["back_path"]),
        "layout": clean,
    }


# --- Bookmark regeneration + Canva/Etsy -- see agents/pipeline_api.py.


# --- Steward: revenue and cost accounting ---

# Soft monthly cloud-spend ceiling (Moderation, principle 5) — crossing it
# never blocks a run, it turns the Steward's dashboard tile red so the excess
# is visible instead of silent. Override with MONTHLY_SPEND_CEILING_USD.
MONTHLY_SPEND_CEILING = float(os.getenv("MONTHLY_SPEND_CEILING_USD", "15"))

# Per-call metering (state.record_spend) shipped on this date. Products
# created BEFORE it have no ledger entries, so pretending they cost $0 would
# be a false report (the Steward "reports what the numbers say"). They get a
# flat estimate instead, clearly labeled. Derivation of the flat rate, from
# the same per-call figures in router.EST_COST_USD: one image generation
# (~$0.05) + ~5 Grok vision calls across consultation and review (~$0.05)
# + ~2 Grok chat calls (~$0.01) ≈ $0.11 per product.
METERING_EPOCH = "2026-07-06"
LEGACY_COST_PER_PRODUCT = 0.11

@app.get("/steward/report")
def steward_report(include_wallet: bool = False):
    """
    Mission-first report: deeds (giving ledger) headline the response; money
    follows as a byproduct. Costs are a labeled hybrid: runs since
    METERING_EPOCH are METERED — every paid Grok/vision/image call records
    itself via state.record_spend (see router.record_api_spend), so a
    repaint-heavy run costs visibly more than a clean one — while products
    from before metering existed carry a flat LEGACY_COST_PER_PRODUCT
    estimate rather than a misleading $0.
    """
    from agents.state import get_spend_summary
    products = get_all_products()
    total_revenue = sum(float(p.get("revenue") or 0) for p in products)
    spend = get_spend_summary()
    deeds = get_deeds_summary()

    legacy = [p for p in products if (p.get("created_at") or "") < METERING_EPOCH]
    legacy_cost = round(len(legacy) * LEGACY_COST_PER_PRODUCT, 2)
    this_month = datetime.utcnow().strftime("%Y-%m")
    legacy_month_cost = round(
        LEGACY_COST_PER_PRODUCT
        * sum(1 for p in legacy if (p.get("created_at") or "").startswith(this_month)), 2)

    total_cost = round(spend["total"] + legacy_cost, 2)
    month_cost = round(spend["month"] + legacy_month_cost, 2)
    by_kind = dict(spend["by_kind"])
    if legacy_cost:
        by_kind["legacy_estimate"] = legacy_cost

    # deeds first — pure and goodly deeds are the mission; money is secondary.
    report = {
        "deeds": deeds,
        "total_products":  len(products),
        "total_revenue":   round(total_revenue, 2),
        "estimated_costs": total_cost,
        "estimated_profit": round(total_revenue - total_cost, 2),
        "cost_per_product": round(total_cost / len(products), 2) if products else 0.0,
        "month_spend":     month_cost,
        "monthly_ceiling": MONTHLY_SPEND_CEILING,
        "over_ceiling":    month_cost > MONTHLY_SPEND_CEILING,
        "spend_by_kind":   by_kind,
        "legacy_products": len(legacy),
        "legacy_estimated_costs": legacy_cost,
        "products": [
            {
                "id":      p["id"],
                "title":   p.get("title"),
                "status":  p.get("status"),
                "revenue": float(p.get("revenue") or 0),
                "etsy_listing_id": p.get("etsy_listing_id"),
                "created_at": p.get("created_at"),
            }
            for p in products
        ],
    }
    # Pass through ledger-read failures untouched so $0 is never silent.
    if spend.get("error"):
        report["error"] = spend["error"]

    # Wallet holdings are OPT-IN: reading them means a live RPC call per chain,
    # and this report is polled. The Treasury view asks for them; the routine
    # P&L poll doesn't pay for them.
    if include_wallet:
        from agents import wallet
        try:
            report["wallet"] = wallet.balances()
        except Exception as e:
            # Never let an unreachable chain take down the whole report — but
            # never report it as $0 either.
            report["wallet"] = {"error": str(e)[:200]}
    return report


# --- Giving ledger (deeds) — human record-keeping only, no log_run/LLM ---

class DeedRequest(BaseModel):
    kind: str
    count: int = 1
    product_id: str | None = None
    note: str = ""


@app.post("/deeds")
def post_deed(body: DeedRequest):
    """
    Record a distribution (gift / gathering / digital). Pure bookkeeping —
    no agent trust movement, no LLM.
    """
    kind = (body.kind or "").strip().lower()
    if kind not in DISTRIBUTION_KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"kind must be one of {sorted(DISTRIBUTION_KINDS)} "
                   f"(gift = handed out, gathering = served a gathering, "
                   f"digital = shared digitally).",
        )
    dist_id = add_distribution(
        kind=kind,
        count=body.count,
        product_id=body.product_id,
        note=body.note or "",
    )
    from agents.state import _connect
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM distributions WHERE id = ?", (dist_id,)
        ).fetchone()
    return dict(row) if row else {"id": dist_id, "kind": kind}


@app.get("/deeds")
def get_deeds():
    """Mission summary: gifted cards, gatherings served, digital shares, feedback, recent."""
    return get_deeds_summary()


@app.post("/products/{product_id}/revenue")
def record_revenue(product_id: str, body: dict):
    """Record actual sales revenue for a product (Sheraj enters this after a sale)."""
    try:
        amount = float(body.get("amount", 0))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="amount must be a number")
    if amount < 0:
        raise HTTPException(status_code=422, detail="amount cannot be negative")
    from agents.state import _connect
    with _connect() as conn:
        row = conn.execute("SELECT id FROM products WHERE id = ?", (product_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Product not found")
    update_product(product_id, revenue=amount)
    return {"product_id": product_id, "revenue": amount}


@app.post("/products/{product_id}/feedback")
def record_feedback(product_id: str, body: dict):
    """
    Record what actually happened when a product met a real person — the
    ground truth the Reviewer's newcomer_accessibility guess never had
    (principle 7). Sheraj notes a recipient's reaction after handing out a
    quote card (or a buyer's comment on a bookmark); empty text clears it.
    """
    text = str(body.get("text") or "").strip()
    from agents.state import _connect
    with _connect() as conn:
        row = conn.execute("SELECT id, task_id FROM products WHERE id = ?", (product_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Product not found")
    update_product(product_id, recipient_feedback=text)
    log_run(dict(row).get("task_id") or product_id, "steward", "recipient_feedback",
            product_id, text[:400] or "(cleared)")
    return {"product_id": product_id, "recipient_feedback": text}


# --- Trust report ---


@app.get("/trust/report")
def trust_report():
    """
    Quality history for all saved products — newest first.
    Returns product titles, overall scores, pass/fail badge, and reviewer recommendation.
    """
    products = get_all_products()
    rows = []
    for p in products:
        raw_scores = p.get("reviewer_scores")
        scores = json.loads(raw_scores) if isinstance(raw_scores, str) and raw_scores else {}
        overall = scores.get("overall", 0.0)
        # A product that shipped below its target score (stall or max-attempts
        # exhaustion) wears BEST EFFORT, never a badge that looks like a clean
        # pass — principle 2. NULL target_reached = predates tracking.
        target_reached = p.get("target_reached")
        badge = "BEST EFFORT" if target_reached == 0 else _badge(overall)
        rows.append({
            "product_id":     p.get("id"),
            "title":          p.get("title"),
            "status":         p.get("status"),
            "created_at":     p.get("created_at"),
            "overall":        overall,
            "passed":         scores.get("passed", False),
            "badge":          badge,
            "target_reached": target_reached,
            "attempts":       p.get("attempts"),
            "recommendation": scores.get("recommendation", ""),
            "principle_scores": scores.get("scores", {}),
        })
    passed  = sum(1 for r in rows if r["passed"])
    average = round(sum(r["overall"] for r in rows) / len(rows), 1) if rows else 0.0
    return {
        "total":           len(rows),
        "passed":          passed,
        "rejected":        len(rows) - passed,
        "average_score":   average,
        "products":        rows,
    }


# --- The Colony + the Material World (nuclei) (rules 35-41b, 59-69) ---------
# Moved to agents/colony_api.py -- included near the bottom of this file with
# the rest of the routers.

# --- The project wallet -- see agents/wallet_api.py.
# --- Google OAuth, WhatsApp, and the Secretary (rules 15-28) ----------------
# Moved to agents/secretary_api.py.
# --- Video Generation pipeline (rules 30-34, 58) -----------------------------
# Moved to agents/video_api.py.


# --- Live Consultation (rules 73-86) ---
#
# Its own router in its own module rather than another thousand lines here. The
# owner gate is on the APP, not on any router, so every one of these paths is
# covered by rule 70 the moment it is mounted — nothing to remember, and
# scripts/test_api_auth.py proves it by walking the route table.
from agents.live_consultation_api import router as live_consultation_router  # noqa: E402

app.include_router(live_consultation_router)

# Gatherings (rules 117-119) and Home (rule 120) get their own routers rather
# than more of this file -- the pattern the Consultation router established.
from agents.gathering_api import router as gathering_router   # noqa: E402
from agents.home_api import router as home_router             # noqa: E402

app.include_router(gathering_router)
app.include_router(home_router)

# Routers split out of this file's own bulk (2026-09-14) so two coding tools
# can work on different subsystems without both editing this file -- see the
# "Parallel work" table near the top of AGENTS.md. Behaviour is unchanged:
# every route kept its exact path, method and body.
from agents.wallet_api import router as wallet_router          # noqa: E402
from agents.x_api import router as x_router                    # noqa: E402
from agents.video_api import router as video_router            # noqa: E402
from agents.colony_api import router as colony_router          # noqa: E402
from agents.secretary_api import router as secretary_router    # noqa: E402
from agents.pipeline_api import router as pipeline_router      # noqa: E402
from agents.card_api import router as card_router              # noqa: E402

app.include_router(wallet_router)
app.include_router(x_router)
app.include_router(video_router)
app.include_router(colony_router)
app.include_router(secretary_router)
app.include_router(pipeline_router)
app.include_router(card_router)

# Re-exported for callers that reach into agents.api's namespace directly
# rather than through HTTP (scripts/test_*.py, agents/colony.py,
# agents/secretary_colony.py) -- safe because pipeline_api etc. never import
# anything back from api.py (see agents/jobs.py's docstring).
from agents.video_api import video_generate_chained            # noqa: E402,F401
from agents.colony_api import launch_team_pipeline              # noqa: E402,F401
from agents.pipeline_api import (                               # noqa: E402
    verify_bookmark_quote, eligible_excerpt, _check_quote_grounding,
)
from agents.card_api import (                                   # noqa: E402
    _assert_ruhi_verbatim, _best_matching_citation, _trim_card_quote,
    _CARD_BATCH_MAX, pipeline_run_card_batch, suggest_ruhi_quotes,
)
from agents.x_api import _run_x_post_job                        # noqa: E402,F401


# --- Health check ---

@app.get("/health")
def health():
    return {"status": "ok", "service": "bahAI Workforce API"}


if __name__ == "__main__":
    # Loopback, no reload -- matches scripts/start_secretary_server.ps1, the
    # managed task that actually runs this in production. Binding 0.0.0.0
    # here put the whole owner-only API on the LAN (rule 70); --reload also
    # serves stale .env values (see AGENTS.md gotchas).
    uvicorn.run("agents.api:app", host="127.0.0.1", port=8765)
