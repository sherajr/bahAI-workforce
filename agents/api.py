"""
bahAI Workforce — Python bridge service for n8n.
Runs on port 8765. n8n calls this for anything that needs Python:
system prompt building, Librarian checks, task card management, trust scoring.

Start with: python agents/api.py  (from project root)
"""

import json
import os
import uvicorn
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv

# Load .env before any submodule imports so all os.getenv() calls see the values
load_dotenv(dotenv_path=str(Path(__file__).parent.parent / ".env"), override=True)

from agents.system_prompt_builder import build_system_prompt
from agents.librarian import retrieve, verify, format_citation
from agents.state import (
    init_db, create_task, get_task, update_task_card,
    load_task_card, log_run, get_agent_status, get_all_agent_statuses,
    create_product, update_product, get_all_products,
)

app = FastAPI(title="bahAI Workforce API", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# --- Startup ---

@app.on_event("startup")
def on_startup():
    init_db()
    cid = os.getenv("CANVA_CLIENT_ID", "")
    print(f"bahAI Workforce API ready. SQLite DB initialised.")
    print(f"CANVA_CLIENT_ID loaded: {bool(cid)} ({cid[:8] if cid else 'EMPTY'})")
    print(f"CANVA_TEMPLATE_ID: {os.getenv('CANVA_TEMPLATE_ID', 'EMPTY')}")


# --- Operator endpoints ---

class PrepareRequest(BaseModel):
    directive: str
    task_type: str = "plan"

class SaveRequest(BaseModel):
    task_id: str
    llm_output: str
    librarian_result: Optional[dict] = None

@app.post("/operator/prepare")
def operator_prepare(req: PrepareRequest):
    """
    Create a task in the DB and build the system prompt + user message
    for the Operator's LLM decomposition call.
    Returns: task_id, system_prompt, user_message
    """
    task_id = create_task(req.directive, req.task_type, assigned_to="operator")
    agent_status = get_agent_status("operator")
    trust_level = agent_status.get("trust_level_name", "Shadow/Advisory")

    system_prompt = build_system_prompt("operator", "plan")

    user_message = (
        f"Directive: {req.directive}\n\n"
        f"Trust level: {trust_level}. All sub-tasks require approval before proceeding.\n\n"
        "Decompose this into a structured task card as JSON. Rules:\n"
        "1. The Librarian MUST appear as an explicit sub-task to verify any quotes, "
        "spiritual claims, or values grounding — this is non-negotiable.\n"
        "2. The Reviewer MUST be the final sub-task before anything ships.\n"
        "3. List only agents that are genuinely needed for this directive.\n\n"
        "Required JSON format:\n"
        "{\n"
        '  "directive": "<the directive>",\n'
        '  "task_type": "<short type label>",\n'
        '  "constitution_principles_used": ["<number: name>", ...],\n'
        '  "librarian_note": "<specific claim or topic the Librarian should verify>",\n'
        '  "sub_tasks": [\n'
        '    {"step": 1, "agent": "librarian", "action": "<verify specific claim or find relevant quote>", '
        '"input": "<what to search for>", "requires_approval": true},\n'
        '    {"step": 2, "agent": "<next agent>", "action": "<what to do>", '
        '"input": "<what it receives>", "requires_approval": true},\n'
        '    ...\n'
        '    {"step": N, "agent": "reviewer", "action": "score all outputs against constitution", '
        '"input": "all prior outputs", "requires_approval": true}\n'
        "  ],\n"
        '  "approval_required": true,\n'
        '  "trust_level": "Shadow/Advisory"\n'
        "}\n\n"
        "Agents: operator, librarian, artist, scribe, reviewer, producer, steward.\n"
        "Output ONLY the JSON object, no other text."
    )

    return {
        "task_id": task_id,
        "system_prompt": system_prompt,
        "user_message": user_message,
        "trust_level": trust_level,
    }


@app.post("/operator/save")
def operator_save(req: SaveRequest):
    """
    Parse the LLM's JSON output, merge in Librarian results, and save the task card.
    Returns the final task card.
    """
    raw = req.llm_output.strip()
    # Strip markdown code fences if the model wrapped the JSON
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        task_card = json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract the JSON object if there's surrounding text
        import re
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            task_card = json.loads(match.group())
        else:
            raise HTTPException(status_code=422, detail=f"Could not parse LLM output as JSON: {raw[:200]}")

    task_card["task_id"] = req.task_id
    if req.librarian_result:
        task_card["librarian_result"] = req.librarian_result

    update_task_card(req.task_id, task_card)
    directive_summary = task_card.get("directive", req.task_id)[:200]
    log_run(req.task_id, "operator", "decompose", directive_summary, json.dumps(task_card)[:500])

    return {"task_id": req.task_id, "task_card": task_card, "status": "saved"}


# --- Librarian endpoints ---

class LibrarianCheckRequest(BaseModel):
    text: str
    task_type: str = "all"
    n_results: int = 2

class LibrarianVerifyRequest(BaseModel):
    text: str

@app.post("/librarian/retrieve")
def librarian_retrieve(req: LibrarianCheckRequest):
    """
    Retrieve relevant passages from the local vector index.
    Falls back gracefully if the index hasn't been built yet (Phase 2).
    """
    passages = retrieve(req.text, n_results=req.n_results)

    if not passages:
        # Index not built — return constitution principles instead (Phase 1 fallback)
        system_prompt = build_system_prompt("librarian", req.task_type)
        return {
            "source": "constitution_fallback",
            "passages": [],
            "constitution_excerpt": system_prompt[:800],
            "note": "Vector index not yet built. Using constitution file directly. Run scripts/ingest_texts.py to enable full retrieval.",
        }

    return {
        "source": "vector_index",
        "passages": [
            {
                "text": p["text"],
                "source": p["source"],
                "section": p["section"],
                "link": p["link"],
                "score": p["score"],
                "formatted": format_citation(p),
            }
            for p in passages
        ],
    }


@app.post("/librarian/verify")
def librarian_verify(req: LibrarianVerifyRequest):
    """
    Check whether a piece of text contains verifiable citations or spiritual claims.
    """
    result = verify(req.text)
    log_run("_verify", "librarian", "verify", req.text[:200],
            json.dumps({"verified": result["verified"], "issues": result["issues"]})[:500])
    return result


# --- Constitution endpoint ---

@app.get("/constitution")
def get_constitution():
    """Return the full constitution text."""
    from pathlib import Path
    path = Path(__file__).parent.parent / "bahai-workforce-constitution.md"
    return {"text": path.read_text(encoding="utf-8")}


@app.post("/constitution/principles")
def get_principles(body: dict):
    """Return principles for a specific task type."""
    task_type = body.get("task_type", "all")
    agent = body.get("agent", "operator")
    return {"system_prompt": build_system_prompt(agent, task_type)}


# --- Task endpoints ---

@app.get("/tasks/{task_id}")
def get_task_endpoint(task_id: str):
    card = load_task_card(task_id)
    if not card:
        raise HTTPException(status_code=404, detail="Task not found")
    return card


@app.post("/tasks/{task_id}/update")
def update_task_endpoint(task_id: str, body: dict):
    update_task_card(task_id, body)
    return {"status": "updated", "task_id": task_id}


@app.post("/tasks/{task_id}/log-run")
def log_run_endpoint(task_id: str, body: dict):
    log_run(
        task_id,
        body.get("agent", "unknown"),
        body.get("step", "unknown"),
        body.get("input_summary", ""),
        body.get("output_summary", ""),
        body.get("passed_review"),
        body.get("reviewer_scores"),
    )
    return {"status": "logged"}


# --- Agent status endpoints ---

@app.get("/agents")
def list_agents():
    return get_all_agent_statuses()


@app.get("/agents/{agent_name}")
def get_agent(agent_name: str):
    status = get_agent_status(agent_name)
    if not status:
        raise HTTPException(status_code=404, detail="Agent not found")
    return status


# --- Task creation (lightweight — no LLM decomposition) ---

@app.post("/tasks/create")
def tasks_create(body: dict):
    """Create a task record and return a task_id for pipeline tracking."""
    task_id = create_task(
        directive=body.get("directive", ""),
        task_type=body.get("task_type", "design"),
        assigned_to="operator",
    )
    return {"task_id": task_id}


# --- Artist endpoints ---

class ArtistBriefRequest(BaseModel):
    theme: str
    citations: Optional[list] = None
    task_id: Optional[str] = None

class ArtistGenerateRequest(BaseModel):
    prompt: str
    aspect_ratio: str = "2:3"
    task_id: Optional[str] = None

@app.post("/artist/brief")
def artist_brief(req: ArtistBriefRequest):
    """Build a FLUX.1 image prompt from theme + Librarian citations."""
    from agents.artist import build_image_prompt
    image_prompt = build_image_prompt(req.theme, req.citations or [])
    if req.task_id:
        log_run(req.task_id, "artist", "brief", req.theme[:200], image_prompt[:200])
    return {"image_prompt": image_prompt}

@app.post("/artist/generate")
def artist_generate(req: ArtistGenerateRequest):
    """Generate an image via xAI and save it locally to outputs/."""
    from agents.artist import generate_image
    try:
        result = generate_image(req.prompt, req.aspect_ratio)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Image generation error: {e}")
    if req.task_id:
        log_run(req.task_id, "artist", "generate", req.prompt[:200], result.get("image_url", "")[:200])
    return result


# --- Scribe endpoints ---

class ScribeWriteRequest(BaseModel):
    theme: str
    image_prompt: str
    citations: Optional[list] = None
    image_url: Optional[str] = None
    task_id: Optional[str] = None

@app.post("/scribe/write")
def scribe_write(req: ScribeWriteRequest):
    """Write a full Etsy listing for a bookmark."""
    from agents.scribe import write_listing
    listing = write_listing(req.theme, req.image_prompt, req.citations or [], req.image_url)
    if req.task_id:
        log_run(req.task_id, "scribe", "write", req.theme[:200], json.dumps(listing)[:400])
    return listing


class ScribeReviseRequest(BaseModel):
    theme: str
    image_prompt: str
    citations: Optional[list] = None
    image_url: Optional[str] = None
    reviewer_notes: str = ""
    reviewer_scores: Optional[dict] = None
    task_id: Optional[str] = None

@app.post("/scribe/revise")
def scribe_revise(req: ScribeReviseRequest):
    """Rewrite a listing using reviewer feedback. Call when initial score is 6–7 (borderline)."""
    from agents.scribe import revise_listing
    listing = revise_listing(
        req.theme, req.image_prompt, req.citations or [],
        req.image_url, req.reviewer_notes, req.reviewer_scores or {},
    )
    if req.task_id:
        log_run(req.task_id, "scribe", "revise", req.theme[:200], json.dumps(listing)[:400])
    return listing


# --- Pipeline: write + approval cycle ---

class WriteApproveRequest(BaseModel):
    theme: str
    image_prompt: str
    citations: Optional[list] = None
    image_url: Optional[str] = None
    task_id: Optional[str] = None
    target_score: float = 9.0
    max_attempts: int = 3

@app.post("/pipeline/write-approve")
def pipeline_write_approve(req: WriteApproveRequest):
    """
    1. Agents consult about the image (Artist describes, Scribe proposes, Reviewer guides).
    2. Scribe writes a listing informed by the consultation.
    3. Reviewer scores it; Scribe revises if below target_score.
    Loops up to max_attempts times. Never touches the image.
    Returns: {listing, review, attempts, target_reached, consultation}
    """
    from agents.consultation import run_consultation
    from agents.scribe import write_listing, revise_listing
    from agents.reviewer import score as reviewer_score

    def _log(agent, step, output):
        if req.task_id:
            log_run(req.task_id, agent, step, req.theme[:200], json.dumps(output)[:400],
                    passed_review=output.get("passed") if agent == "reviewer" else None)

    # ── Step 1: Consultation ─────────────────────────────────────────────────
    consultation = {"transcript": [], "context": ""}
    if req.image_url:
        try:
            consultation = run_consultation(
                req.image_url, req.theme, req.image_prompt, req.citations or []
            )
            if req.task_id:
                log_run(req.task_id, "consultation", "consult", req.theme[:200],
                        f"{len(consultation['transcript'])} turns completed")
        except Exception as e:
            consultation["transcript"] = [{"agent": "System", "role": "error",
                                            "message": f"Consultation skipped: {e}"}]

    # ── Step 2: Write → Score → Revise loop ──────────────────────────────────
    verified_quote = consultation.get("verified_quote", "")

    listing = write_listing(
        req.theme, req.image_prompt, req.citations or [], req.image_url,
        consultation_context=consultation["context"],
        verified_quote=verified_quote,
    )
    # Force-inject verified_quote — don't rely on LLM to follow the instruction
    if verified_quote:
        listing["bookmark_quote"] = verified_quote
    review  = reviewer_score(req.theme, req.image_prompt, listing)
    _log("scribe",   "write",   listing)
    _log("reviewer", "score_1", review)

    best_listing, best_review = listing, review
    attempt = 1

    while best_review.get("overall", 0) < req.target_score and attempt < req.max_attempts:
        attempt += 1
        listing = revise_listing(
            req.theme, req.image_prompt, req.citations or [], req.image_url,
            reviewer_notes=best_review.get("recommendation", ""),
            reviewer_scores=best_review.get("scores", {}),
            consultation_context=consultation["context"],
            verified_quote=verified_quote,
        )
        # Force-inject again on each revision round
        if verified_quote:
            listing["bookmark_quote"] = verified_quote
        review = reviewer_score(req.theme, req.image_prompt, listing)
        _log("scribe",   f"revise_{attempt}", listing)
        _log("reviewer", f"score_{attempt}",  review)
        if review.get("overall", 0) > best_review.get("overall", 0):
            best_listing, best_review = listing, review

    return {
        "listing":        best_listing,
        "review":         best_review,
        "attempts":       attempt,
        "target_reached": best_review.get("overall", 0) >= req.target_score,
        "consultation":   consultation["transcript"],
    }


# --- Reviewer endpoints ---

class ReviewerScoreRequest(BaseModel):
    theme: str
    image_prompt: str
    listing: dict
    librarian_issues: Optional[list] = None
    task_id: Optional[str] = None

@app.post("/reviewer/score")
def reviewer_score(req: ReviewerScoreRequest):
    """Score image prompt + listing against all 7 constitution principles."""
    from agents.reviewer import score
    result = score(req.theme, req.image_prompt, req.listing, req.librarian_issues or [])
    passed = result.get("passed", False)
    if req.task_id:
        log_run(
            req.task_id, "reviewer", "score",
            req.theme[:200],
            json.dumps({"overall": result.get("overall"), "passed": passed})[:400],
            passed_review=passed,
        )
    return result


# --- Products endpoints ---

class ProductSaveRequest(BaseModel):
    task_id: str
    theme: str
    image_url: str
    image_prompt: str
    listing: dict
    reviewer_scores: Optional[dict] = None

@app.post("/products/save")
def products_save(req: ProductSaveRequest):
    """Save a completed bookmark product to the database."""
    product_id = create_product(
        task_id=req.task_id,
        title=req.listing.get("title", req.theme),
        image_url=req.image_url,
        listing_copy=json.dumps(req.listing),
        image_prompt=req.image_prompt,
        theme=req.theme,
    )
    if req.reviewer_scores:
        update_product(product_id, reviewer_scores=json.dumps(req.reviewer_scores))
    return {"product_id": product_id, "status": "saved"}

@app.get("/products")
def list_products():
    """List all saved products, newest first."""
    return get_all_products()

@app.get("/products/{product_id}")
def get_product(product_id: str):
    from agents.state import _connect
    with _connect() as conn:
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Product not found")
    return dict(row)


class ImproveRequest(BaseModel):
    target_score: float = 9.0
    max_attempts: int = 2
    human_notes: str = ""   # optional guidance from Sheraj, e.g. "make it more poetic"

@app.post("/products/{product_id}/improve")
def improve_product(product_id: str, req: ImproveRequest):
    """
    Re-run the revise → score cycle on an already-saved product without regenerating the image.
    Useful for products saved as BEST EFFORT or to push a score closer to 9.
    Updates the product in the database if the score improves.
    """
    from agents.state import _connect
    from agents.scribe import revise_listing
    from agents.reviewer import score as reviewer_score

    with _connect() as conn:
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Product not found")

    product = dict(row)
    image_url    = product.get("image_url", "")
    image_prompt = product.get("image_prompt", "")
    theme        = product.get("theme", "")
    listing_copy = product.get("listing_copy", "{}")
    raw_scores   = product.get("reviewer_scores", "{}")

    listing        = json.loads(listing_copy) if listing_copy else {}
    current_review = json.loads(raw_scores) if raw_scores else {}
    current_score  = current_review.get("overall", 0.0)

    if not theme and listing.get("title"):
        theme = listing["title"]

    reviewer_notes = current_review.get("recommendation", "")
    if req.human_notes:
        reviewer_notes = f"{req.human_notes}\n\n{reviewer_notes}".strip()

    best_listing = listing
    best_review  = current_review
    attempt      = 0

    while best_review.get("overall", 0) < req.target_score and attempt < req.max_attempts:
        attempt += 1
        revised = revise_listing(
            theme, image_prompt, [], image_url,
            reviewer_notes=reviewer_notes,
            reviewer_scores=best_review.get("scores", {}),
        )
        new_review = reviewer_score(theme, image_prompt, revised)
        log_run(product_id, "scribe",    f"improve_{attempt}", theme[:200], json.dumps(revised)[:400])
        log_run(product_id, "reviewer",  f"improve_score_{attempt}", theme[:200],
                json.dumps({"overall": new_review.get("overall")})[:200],
                passed_review=new_review.get("passed", False))

        if new_review.get("overall", 0) > best_review.get("overall", 0):
            best_listing = revised
            best_review  = new_review
            reviewer_notes = new_review.get("recommendation", reviewer_notes)

    improved = best_review.get("overall", 0) > current_score
    if improved:
        update_product(
            product_id,
            title=best_listing.get("title", theme),
            listing_copy=json.dumps(best_listing),
            reviewer_scores=json.dumps(best_review),
        )

    return {
        "product_id":    product_id,
        "improved":      improved,
        "old_score":     current_score,
        "new_score":     best_review.get("overall", current_score),
        "target_reached": best_review.get("overall", 0) >= req.target_score,
        "attempts":      attempt,
        "listing":       best_listing,
        "review":        best_review,
    }


# --- Canva Connect API endpoints ---

@app.get("/canva/oauth/start")
def canva_oauth_start():
    """
    Step 1 of Canva OAuth. Open this URL in a browser — it redirects to Canva
    for one-time approval, then back to /canva/oauth/callback automatically.
    """
    from agents.canva import build_auth_url
    from fastapi.responses import RedirectResponse, HTMLResponse
    if not os.getenv("CANVA_CLIENT_ID"):
        return HTMLResponse("""
            <html><body style="font-family:sans-serif;padding:2em">
            <h2>⚠️ Canva Client ID missing</h2>
            <p>Add your Canva credentials to <strong>.env</strong> first:</p>
            <ol>
              <li>Go to <a href="https://www.canva.com/developers" target="_blank">www.canva.com/developers</a></li>
              <li>Create an integration named <em>bahAI Workforce</em></li>
              <li>Set redirect URL to: <code>http://localhost:8765/canva/oauth/callback</code></li>
              <li>Copy the Client ID and Client Secret into your <code>.env</code> file</li>
              <li>Restart the API, then revisit this page</li>
            </ol>
            </body></html>
        """, status_code=400)
    auth_url = build_auth_url()
    return RedirectResponse(url=auth_url)


@app.get("/canva/oauth/callback")
def canva_oauth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
):
    """Canva redirects here after the user approves access. Exchanges code for tokens."""
    from agents.canva import exchange_code
    from fastapi.responses import HTMLResponse

    if error:
        return HTMLResponse(f"""
            <html><body style="font-family:sans-serif;padding:2em">
            <h2>❌ Canva authorisation failed</h2>
            <p><strong>Error:</strong> {error}</p>
            <p><strong>Details:</strong> {error_description or 'No details provided'}</p>
            <hr>
            <p>If this says <em>invalid_scope</em>: go to your
            <a href="https://www.canva.com/developers" target="_blank">Canva developer portal</a>
            → bahAI Workforce → <strong>Scopes</strong> tab → enable all required scopes → save,
            then <a href="/canva/oauth/start">try again</a>.</p>
            </body></html>
        """, status_code=400)

    if not code or not state:
        return HTMLResponse("""
            <html><body style="font-family:sans-serif;padding:2em">
            <h2>❌ Missing authorisation code</h2>
            <p>Canva did not return an authorisation code.
            <a href="/canva/oauth/start">Try again</a>.</p>
            </body></html>
        """, status_code=400)

    try:
        exchange_code(code, state)
        return HTMLResponse("""
            <html><body style="font-family:sans-serif;padding:2em">
            <h2>✅ Canva connected!</h2>
            <p>Your bahAI Workforce can now upload images and autofill your bookmark template.</p>
            <p>You can close this tab. The pipeline will handle everything automatically from now on.</p>
            </body></html>
        """)
    except Exception as e:
        return HTMLResponse(f"""
            <html><body style="font-family:sans-serif;padding:2em">
            <h2>❌ Token exchange failed</h2>
            <p>{e}</p>
            <p><a href="/canva/oauth/start">Try again</a>.</p>
            </body></html>
        """, status_code=400)


@app.get("/canva/status")
def canva_status():
    """Check whether Canva is authorised and show the template's fields."""
    from agents.canva import is_authorised, get_template_fields, CANVA_TEMPLATE_ID
    authorised = is_authorised()
    result = {"authorised": authorised, "template_id": CANVA_TEMPLATE_ID}
    if authorised and CANVA_TEMPLATE_ID:
        try:
            result["template_fields"] = get_template_fields()
        except Exception as e:
            result["template_fields_error"] = str(e)
    return result


@app.post("/canva/autofill")
def canva_autofill(body: dict):
    """Upload image to Canva and autofill the bookmark brand template. Returns design URL."""
    from agents.canva import autofill_bookmark, CANVA_CLIENT_ID, CANVA_TEMPLATE_ID
    image_path = body.get("image_path", "")
    if not image_path:
        raise HTTPException(status_code=422, detail="image_path is required")

    # Fail gracefully when Canva isn't configured yet — pipeline continues
    if not CANVA_CLIENT_ID or not CANVA_TEMPLATE_ID:
        return {
            "skipped": True,
            "reason": "Canva not configured. Add CANVA_CLIENT_ID, CANVA_CLIENT_SECRET, and CANVA_TEMPLATE_ID to .env, then visit /canva/oauth/start.",
            "design_url": None,
        }

    try:
        result = autofill_bookmark(image_path)
    except Exception as e:
        return {"skipped": True, "reason": str(e), "design_url": None}

    task_id = body.get("task_id")
    if task_id:
        log_run(task_id, "artist", "canva_autofill",
                image_path[:200], result.get("design_url", "")[:200])
    return result


# --- Compositor ---

@app.post("/compositor/render")
def compositor_render(body: dict):
    """Split 2:3 artwork into front (quote overlay) and back (clean art) 1:3 bookmark halves."""
    from agents.compositor import render_bookmark_pair
    image_path = body.get("image_path", "")
    quote = body.get("quote", "").strip()
    if not image_path:
        raise HTTPException(status_code=422, detail="image_path is required")
    if not quote:
        raise HTTPException(status_code=422, detail="quote is required")
    try:
        result = render_bookmark_pair(image_path, quote)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    task_id = body.get("task_id")
    if task_id:
        log_run(task_id, "compositor", "render", image_path[:200], result["front_path"][:200])
    return {
        "front_image_path": result["front_path"],
        "back_image_path": result["back_path"],
        "success": True,
    }


# --- Trust report ---

TRUST_BADGES = {
    (9.0, 10.1): "EXCEPTIONAL",
    (7.0,  9.0): "APPROVED",
    (6.0,  7.0): "BORDERLINE",
    (0.0,  6.0): "REJECTED",
}

def _badge(overall: float) -> str:
    for (lo, hi), label in TRUST_BADGES.items():
        if lo <= overall < hi:
            return label
    return "UNKNOWN"

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
        rows.append({
            "product_id":     p.get("id"),
            "title":          p.get("title"),
            "status":         p.get("status"),
            "created_at":     p.get("created_at"),
            "overall":        overall,
            "passed":         scores.get("passed", False),
            "badge":          _badge(overall),
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


# --- Health check ---

@app.get("/health")
def health():
    return {"status": "ok", "service": "bahAI Workforce API"}


if __name__ == "__main__":
    uvicorn.run("agents.api:app", host="0.0.0.0", port=8765, reload=True)
