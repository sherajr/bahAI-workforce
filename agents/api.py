"""
bahAI Workforce — FastAPI backend for the React dashboard.
Runs on port 8765. The dashboard (dashboard/) calls these endpoints; the heavy
lifting lives in the sibling agent modules (librarian, artist, consultation,
scribe, reviewer, compositor, canva, etsy).

Start with: python agents/api.py  (from project root)

Map of this file:
  1. Background job store        — long pipelines run in worker threads,
                                   the dashboard polls /pipeline/status/{job_id}
  2. Revision helpers            — _diff_summary, _apply_review_feedback
  3. _pipeline_write_approve_sync — consultation → Scribe writes → Reviewer
                                   scores → mechanical-edit revision loop
  4. _run_full_pipeline          — the WHOLE bookmark pipeline (dashboard's
                                   "Run pipeline" button): task → Librarian →
                                   Artist → write/approve → save product →
                                   Compositor → Canva
  5. Products endpoints          — list/get/improve/manual-edit/revenue
  6. Canva + Etsy OAuth & publish
  7. Steward (P&L) + Trust report + health
"""

import json
import os
import re
import threading
import uuid
import uvicorn
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import FileResponse, PlainTextResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv

# Load .env before any submodule imports so all os.getenv() calls see the values
load_dotenv(dotenv_path=str(Path(__file__).parent.parent / ".env"), override=True)

from agents.librarian import retrieve, retrieve_ruhi_book1
from agents.state import (
    init_db, create_task, update_task_status, log_run, get_all_agent_statuses,
    create_product, update_product, get_all_products,
)

app = FastAPI(title="bahAI Workforce API", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Serve generated bookmark images to the dashboard at http://localhost:8765/outputs/<filename>
OUTPUTS_DIR = Path(__file__).parent.parent / "outputs"
OUTPUTS_DIR.mkdir(exist_ok=True)
app.mount("/outputs", StaticFiles(directory=str(OUTPUTS_DIR)), name="outputs")


def _web_image_path(local_path: str) -> str:
    """Convert a local outputs/ file path (Windows or POSIX) to a dashboard-servable URL path."""
    if not local_path:
        return ""
    name = str(local_path).replace("\\", "/").split("/")[-1]
    return f"/outputs/{name}"

# --- Startup ---

@app.on_event("startup")
def on_startup():
    init_db()
    # Secretary's reminder scheduler — all state in private/secretary.db, so a
    # restart resumes exactly where it left off.
    from agents import scheduler
    scheduler.start()
    cid = os.getenv("CANVA_CLIENT_ID", "")
    print(f"bahAI Workforce API ready. SQLite DB initialised.")
    print(f"CANVA_CLIENT_ID loaded: {bool(cid)} ({cid[:8] if cid else 'EMPTY'})")
    print(f"CANVA_TEMPLATE_ID: {os.getenv('CANVA_TEMPLATE_ID', 'EMPTY')}")


# --- Background job store (async pipeline for the dashboard) ---
#
# LLM pipelines take 3–5 minutes. The dashboard cannot block that long, so long
# endpoints run in a worker thread and report progress through this in-memory store.

JOBS: dict[str, dict] = {}          # job_id → {status, progress, steps, result, error, created_at, updated_at}
_JOBS_LOCK = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=2)
_MAX_JOBS = 50                       # keep memory bounded; oldest finished jobs are dropped

# Human-in-the-loop rendezvous: a job that pauses for Sheraj's input (after
# consultation round 2 — see consultation.run_consultation's request_human_input)
# registers a threading.Event here; POST /pipeline/status/{job_id}/respond sets
# it and wakes the paused worker thread. One entry per job that's currently
# waiting; entries are removed the moment the input is received or times out.
_PENDING_INPUT: dict[str, dict] = {}
_PENDING_LOCK = threading.Lock()
_HUMAN_INPUT_TIMEOUT = 1800  # 30 min — long enough not to nag, short enough a job can't hang forever


def _job_update(job_id: str, **fields):
    with _JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        turn = fields.pop("consultation_turn", None)
        job.update(fields)
        job["updated_at"] = datetime.utcnow().isoformat()
        if "progress" in fields:
            job.setdefault("steps", []).append(
                {"ts": job["updated_at"], "message": fields["progress"]}
            )
        if turn is not None:
            job.setdefault("consultation_live", []).append(turn)


def _start_job(kind: str, runner) -> str:
    """
    Register a job and run `runner(progress, on_turn, request_human_input)` in
    a worker thread. The three callbacks let a long pipeline (a) narrate short
    status text, (b) stream consultation turns live for the dashboard chat
    view, and (c) block the worker thread until Sheraj responds mid-run.
    """
    job_id = str(uuid.uuid4())[:8]
    now = datetime.utcnow().isoformat()
    with _JOBS_LOCK:
        # Evict oldest finished jobs beyond the cap
        finished = [k for k, v in JOBS.items() if v["status"] in ("done", "error")]
        for k in sorted(finished, key=lambda k: JOBS[k]["created_at"])[: max(0, len(JOBS) - _MAX_JOBS)]:
            JOBS.pop(k, None)
        JOBS[job_id] = {
            "job_id": job_id, "kind": kind, "status": "running",
            "progress": "Starting...", "steps": [], "result": None, "error": None,
            "consultation_live": [], "pending_prompt": None,
            "created_at": now, "updated_at": now,
        }

    def _progress(message: str):
        _job_update(job_id, progress=message)

    def _on_turn(turn: dict):
        _job_update(job_id, consultation_turn=turn)

    def _request_human_input(prompt: str) -> str:
        ev = threading.Event()
        with _PENDING_LOCK:
            _PENDING_INPUT[job_id] = {"event": ev, "response": ""}
        _job_update(job_id, status="waiting_for_input", pending_prompt=prompt)
        ev.wait(_HUMAN_INPUT_TIMEOUT)
        with _PENDING_LOCK:
            entry = _PENDING_INPUT.pop(job_id, {"response": ""})
        _job_update(job_id, status="running", pending_prompt=None)
        return entry.get("response", "")

    def _run():
        try:
            result = runner(_progress, _on_turn, _request_human_input)
            _job_update(job_id, status="done", progress="Complete", result=result)
        except Exception as e:
            _job_update(job_id, status="error", progress=f"Failed: {e}", error=str(e))
            with _PENDING_LOCK:
                _PENDING_INPUT.pop(job_id, None)

    _executor.submit(_run)
    return job_id


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


@app.get("/pipeline/jobs")
def pipeline_jobs():
    """Recent background jobs, newest first (lets the dashboard reattach after a refresh)."""
    with _JOBS_LOCK:
        jobs = sorted(JOBS.values(), key=lambda j: j["created_at"], reverse=True)
        return [
            {k: v for k, v in j.items() if k != "result"} | {"has_result": j["result"] is not None}
            for j in jobs[:20]
        ]


# --- Agent status (dashboard Trust tab) ---

@app.get("/agents")
def list_agents():
    return get_all_agent_statuses()


# --- Pipeline: write + approval cycle ---

class WriteApproveRequest(BaseModel):
    theme: str
    image_prompt: str
    citations: Optional[list] = None
    image_url: Optional[str] = None
    task_id: Optional[str] = None
    target_score: float = 9.0
    max_attempts: int = 3

def _diff_summary(find: str, replace: str, context: int = 24) -> str:
    """
    Summarize a find->replace edit by trimming the common prefix/suffix so the
    log shows only what actually differs. A raw head-truncation of both
    strings (the previous approach) made real edits look like no-ops whenever
    find/replace shared a long opening quote — which is the common case,
    since 'find' must quote existing listing text verbatim. Showing only the
    delta is what makes a genuine change visibly distinguishable from "nothing
    happened", which was the direct cause of a real user complaint.
    """
    i = 0
    while i < len(find) and i < len(replace) and find[i] == replace[i]:
        i += 1
    j = 0
    while (j < len(find) - i and j < len(replace) - i and
           find[len(find) - 1 - j] == replace[len(replace) - 1 - j]):
        j += 1
    prefix, suffix = find[:i], (find[len(find) - j:] if j else "")
    find_mid, replace_mid = find[i:len(find) - j], replace[i:len(replace) - j]

    lead = ("…" + prefix[-context:]) if len(prefix) > context else prefix
    tail = (suffix[:context] + "…") if len(suffix) > context else suffix

    if not find_mid and not replace_mid:
        return f'no visible change ("{find[:60]}")'
    if not find_mid:
        return f'{lead}[+ "{replace_mid[:200]}"]{tail}'
    if not replace_mid:
        return f'{lead}[- "{find_mid[:200]}"]{tail}'
    return f'{lead}["{find_mid[:150]}" -> "{replace_mid[:150]}"]{tail}'


# Matches a quoted span of 15+ chars inside a Fix note, tolerant of straight
# and curly quotes — used to catch a Fix note re-describing an edit that was
# already applied mechanically (see _apply_review_feedback).
_QUOTED_SPAN_RE = re.compile(r'[\'"“‘]([^\'"”’]{15,})[\'"”’]')


def _apply_review_feedback(listing: dict, review: dict, verified_quote: str,
                           extra_instructions: list[str] = None) -> tuple[dict, str, list]:
    """
    Turn a review into a revised listing.
    1. The Reviewer's surgical find-and-replace edits are applied MECHANICALLY —
       compliance no longer depends on the small local model obeying prose
       (observed failing three attempts in a row on 'remove every reference to 9').
    2. Every principle scored below 7 gets its 'Fix:' note surfaced as an
       instruction EVERY round, not just when the Reviewer supplied zero edits —
       previously a round with 2 edits covering one principle silently dropped
       Fix: notes for every other weak principle, so the Scribe only ever saw
       a fraction of the feedback it should have (the user's core complaint).
    3. Edits targeting bookmark_quote are rejected outright (that field is
       Librarian-locked and the Scribe cannot touch it either) and reported
       back to the Reviewer as blocked, so it stops re-requesting something
       structurally impossible and instead reframes the DESCRIPTION.
    Returns (revised_listing, note for the editing log, changes list for the
    next Reviewer call — so it knows exactly what was executed and never
    re-requests a change that already happened).
    """
    from agents.scribe import apply_edits, revise_listing_light

    edits = [e for e in (review.get("edits") or []) if isinstance(e, dict)]
    revised, unapplied, rejected_locked = apply_edits(listing, edits)
    applied = [e for e in edits if e not in unapplied and e not in rejected_locked]

    changes = []
    for e in applied:
        find, repl = str(e.get("find") or ""), str(e.get("replace") or "")
        field = e.get("field", "description")
        changes.append(f'{field}: {_diff_summary(find, repl)}')
    for e in rejected_locked:
        changes.append(
            'REJECTED: bookmark_quote is Librarian-locked and can never be edited — '
            "address any quote/theme mismatch by reframing the description instead"
        )

    instructions = list(extra_instructions or [])
    for e in unapplied:
        find, repl = str(e.get("find") or ""), str(e.get("replace") or "")
        field = e.get("field", "description")
        if repl:
            instructions.append(f'In the {field}, replace "{find}" with "{repl}".')
        else:
            instructions.append(f'Delete "{find}" from the {field}.')

    if not edits:
        # No surgical edits at all — the recommendation is the only lever we
        # have, since it isn't already implemented by anything mechanical.
        rec = (review.get("recommendation") or "").strip()
        if rec and not rec.lower().startswith("ship"):
            instructions.append(rec)

    # Surface every weak principle's Fix: note EVERY round, regardless of how
    # many edits were supplied — an edit array covers what it covers, but a
    # 9-principle review often has more weak spots than the edits address
    # (observed: 2 edits for one principle while two OTHER weak principles'
    # Fix notes were silently dropped because edits existed at all).
    edit_text = " ".join(f"{e.get('find','')} {e.get('replace','')}" for e in edits).lower()
    for v in (review.get("scores") or {}).values():
        if isinstance(v, dict) and v.get("score", 10) < 7 and "Fix:" in (v.get("note") or ""):
            fix = v["note"].split("Fix:", 1)[1].strip()
            if not fix or fix in instructions:
                continue
            # A Fix note is usually the Reviewer re-describing one of its own
            # `edits` entries in prose ("Replace 'A' with 'B'", "Add the
            # sentence 'C' after..."), so its wording never matches edit_text
            # verbatim even though the underlying change is identical — the
            # old whole-string check below always missed this. Concretely: a
            # mechanical edit already inserted sentence C, then this loop
            # ALSO turned the Fix note into a Scribe instruction to insert C
            # again, producing back-to-back near-duplicate sentences that
            # tanked Moderation/Craft scores every single revision attempt.
            # Comparing just the quoted span(s) inside the Fix note catches
            # this even though the surrounding instructional phrasing differs.
            quoted = _QUOTED_SPAN_RE.findall(fix)
            if quoted and all(q.lower() in edit_text for q in quoted):
                continue
            if fix.lower() in edit_text:
                continue
            instructions.append(fix)

    note_parts = []
    if applied:
        note_parts.append(f"{len(applied)} surgical edit{'s' if len(applied) != 1 else ''} applied mechanically")
    if rejected_locked:
        note_parts.append(f"{len(rejected_locked)} edit{'s' if len(rejected_locked) != 1 else ''} rejected (locked quote)")
    if instructions:
        revised = revise_listing_light(revised, instructions, verified_quote)
        note_parts.append(f"{len(instructions)} instruction{'s' if len(instructions) != 1 else ''} via Scribe")
        changes.extend(f"Scribe was instructed: {ins[:120]}" for ins in instructions)
    if verified_quote:
        revised["bookmark_quote"] = verified_quote

    # Unconditional claim scrub — runs regardless of which path produced this
    # revision. revise_listing_light already scrubs its own output, but a
    # round whose edits were fully covered by mechanical apply_edits (no
    # Scribe instructions needed) would otherwise skip sanitization entirely,
    # letting a Reviewer-authored false claim (e.g. an invented exact motif
    # count) ship untouched. This closes that gap for every path uniformly.
    from agents.scribe import _sanitize_claims
    revised = _sanitize_claims(revised)

    return revised, ("; ".join(note_parts) or "no actionable feedback"), changes


# Common function words excluded from the grounding overlap check so that a
# quote can't pass just by sharing "the/and/unto" with a passage.
_GROUNDING_STOPWORDS = frozenset(
    "the and that this with from unto thee thou thy thine hath have has for are not all our "
    "your his her its will shall which what when they them been were may can doth does did "
    "you but was is it in of to on at by an as be or so no".split()
)


def _check_quote_grounding(quote: str, citations: list[dict]) -> tuple[bool, str]:
    """
    Deterministic backstop for the consultation Librarian's GROUNDED verdict
    (principles 3 and 9): never ship "Librarian-verified" on the model's
    self-report alone — the same discipline as _best_matching_citation in the
    card pipeline and scribe._sanitize_claims.

    With retrieved citations (the normal case — the Librarian was told to
    adapt from exactly these passages): at least 60% of the quote's distinct
    content words must appear in a single passage. "Condense" keeps source
    words and passes easily; a quote the Librarian actually invented shares
    only scattered vocabulary and fails.

    With no citations (retrieval was down; the Librarian drew from memory):
    librarian.verify() checks the full text index by embedding similarity.
    Any failure — including the index being unavailable — returns False:
    unverifiable is not the same as verified.

    Returns (traceable, human-readable reason for the log).
    """
    words = {w for w in re.sub(r"[^a-z0-9 ]", " ", quote.lower()).split()
             if len(w) >= 3 and w not in _GROUNDING_STOPWORDS}
    if not words:
        return False, "quote has no checkable content words"

    if citations:
        best_frac, best_src = 0.0, ""
        for c in citations:
            passage_words = set(re.sub(r"[^a-z0-9 ]", " ", str(c.get("text") or "").lower()).split())
            frac = len(words & passage_words) / len(words)
            if frac > best_frac:
                best_frac, best_src = frac, str(c.get("source") or "")
        if best_frac >= 0.6:
            return True, f"{best_frac:.0%} of content words traceable to: {best_src}"
        return False, (f"only {best_frac:.0%} of the quote's content words appear in any "
                       "retrieved passage")

    try:
        from agents.librarian import verify
        verdict = verify(quote)
        if verdict.get("verified"):
            return True, "verified against the full text index (embedding similarity)"
        return False, "; ".join(verdict.get("issues") or ["no close match in the text index"])
    except Exception as e:
        return False, f"could not verify against the text index ({e})"


def _pipeline_write_approve_sync(req: WriteApproveRequest, progress=None, on_turn=None,
                                 request_human_input=None) -> dict:
    """
    Core write-approve logic, callable from the sync endpoint, the async job
    wrapper, and the full /pipeline/run pipeline.
    1. Agents consult about the image (Artist describes, Scribe proposes, Reviewer guides).
       on_turn streams each turn live; request_human_input (if given) pauses after round 2
       so Sheraj can steer the team before the Scribe writes.
    2. If consultation agreed the artwork must change, the Artist regenerates it ONCE
       with the agreed adjustment — so the shipped image honours the consultation.
    3. Scribe writes a listing informed by the consultation.
    4. Reviewer scores it; Scribe revises if below target_score.
    Loops up to max_attempts times; stops early if revisions stall.
    Returns: {listing, review, attempts, target_reached, consultation,
              image_path, image_prompt} — image fields reflect any regeneration.
    """
    from agents.consultation import run_consultation
    from agents.scribe import write_listing
    from agents.reviewer import score as reviewer_score

    def _progress(msg: str):
        if progress:
            progress(msg)

    def _weakest(review: dict, n: int = 2) -> str:
        """Human-readable list of the n lowest-scoring principles."""
        scores = review.get("scores") or {}
        items = sorted(
            ((k.split("_", 1)[-1].replace("_", " "), v.get("score", 0))
             for k, v in scores.items() if isinstance(v, dict)),
            key=lambda kv: kv[1],
        )
        return ", ".join(f"{name} ({s}/10)" for name, s in items[:n]) or "n/a"

    def _review_summary(review: dict) -> str:
        overall = review.get("overall", 0)
        verdict = "meets the pass threshold" if review.get("passed") else "below the pass threshold"
        return (
            f"Overall {overall}/10 — {verdict}.\n"
            f"Weakest principles: {_weakest(review)}.\n"
            f"Recommendation: {review.get('recommendation', '')}"
        )

    def _log(agent, step, output):
        if req.task_id:
            if agent == "reviewer":
                passed = output.get("passed")
            elif agent == "scribe":
                # A scribe output passes when every essential listing field is present
                passed = all(
                    str(output.get(k) or "").strip()
                    for k in ("title", "description", "bookmark_quote")
                )
            else:
                passed = None
            log_run(req.task_id, agent, step, req.theme[:200], json.dumps(output)[:400],
                    passed_review=passed)

    # ── Step 1: Consultation ─────────────────────────────────────────────────
    def _preview_front(quote: str, transcript: list) -> str:
        """LLM-free front-face render for the pause — Sheraj steers from the
        actual printed look, not a text description of it."""
        from agents.compositor import render_bookmark_pair
        return _web_image_path(render_bookmark_pair(req.image_url, quote)["front_path"])

    consultation = {"transcript": [], "context": ""}
    if req.image_url:
        try:
            consultation = run_consultation(
                req.image_url, req.theme, req.image_prompt, req.citations or [],
                progress=progress, on_turn=on_turn, request_human_input=request_human_input,
                render_preview=_preview_front,
                preview_note=("The image above is the bookmark's front face as it would "
                              "print right now, with the team's verified quote."),
            )
            if req.task_id:
                vq = (consultation.get("verified_quote") or "").strip()
                log_run(req.task_id, "consultation", "consult", req.theme[:200],
                        f"{len(consultation['transcript'])} turns completed",
                        passed_review=bool(vq and len(vq) > 10))
        except Exception as e:
            consultation["transcript"] = [{"agent": "System", "role": "error",
                                            "message": f"Consultation skipped: {e}"}]
            if req.task_id:
                log_run(req.task_id, "consultation", "consult", req.theme[:200],
                        f"failed: {e}"[:400], passed_review=False)

    # ── Step 2: Honour the consultation's image decision ─────────────────────
    # If the team agreed the artwork itself must change, regenerate it once with
    # the agreed adjustment. Without this, the Reviewer scores an image that
    # ignores the consultation and (rightly) marks the whole product down.
    image_path = req.image_url
    image_prompt = req.image_prompt
    image_revision_log = []
    brief = consultation.get("brief") or {}
    adjustment = (brief.get("image_adjustment") or "").strip()
    if adjustment and image_path:
        _progress(f"Artist is repainting per the consultation: {adjustment[:120]}...")
        try:
            from agents.artist import generate_image
            revised_prompt = (
                f"{req.image_prompt}\n\n"
                f"IMPORTANT adjustment agreed in team consultation: {adjustment}"
            )
            gen = generate_image(revised_prompt, "2:3")
            new_path = gen.get("image_url", "")
            if new_path and Path(new_path).exists():
                image_path = new_path
                image_prompt = revised_prompt
                image_revision_log.append(
                    {"agent": "Artist", "role": "image revision (consultation)",
                     "message": f"Repainted the artwork per the team's agreed adjustment:\n{adjustment}"})
                _log("artist", "regenerate", {"adjustment": adjustment, "image": new_path})
        except Exception as e:
            image_revision_log.append(
                {"agent": "Artist", "role": "image revision (consultation)",
                 "message": f"Regeneration failed ({e}) — continuing with the original artwork."})
            if req.task_id:
                log_run(req.task_id, "artist", "regenerate", adjustment[:200],
                        f"failed: {e}"[:400], passed_review=False)

    # ── Step 3: Write → Score → Revise loop ──────────────────────────────────
    verified_quote = consultation.get("verified_quote", "")
    quote_grounded = consultation.get("quote_grounded", False)

    # Deterministic grounding backstop: the Librarian's GROUNDED verdict is a
    # self-report, and this quote gets locked for the rest of the run — check
    # it against the actual retrieved passages before letting "verified" stick.
    if verified_quote and quote_grounded:
        traceable, why = _check_quote_grounding(verified_quote, req.citations or [])
        if not traceable:
            quote_grounded = False
            consultation["transcript"].append({
                "agent": "System", "role": "grounding check",
                "message": ("The Librarian called this quote GROUNDED, but the deterministic "
                            f"check could not trace it to a source ({why}). The listing will "
                            "present it as the team's phrase, not a verified quotation."),
            })
            consultation["context"] += (
                "\n\nCORRECTION (deterministic grounding check): the quote above could NOT be "
                "traced to a verified source — do not describe it as a verified scriptural "
                "quotation; call it the team's guiding phrase instead."
            )
        if req.task_id:
            log_run(req.task_id, "librarian", "grounding_check", verified_quote[:200],
                    why[:400], passed_review=traceable)

    _progress(f"Scribe is writing the listing (attempt 1/{req.max_attempts})...")
    listing = write_listing(
        req.theme, image_prompt, req.citations or [], image_path,
        consultation_context=consultation["context"],
        verified_quote=verified_quote,
        quote_grounded=quote_grounded,
    )
    # Force-inject verified_quote — don't rely on LLM to follow the instruction
    if verified_quote:
        listing["bookmark_quote"] = verified_quote
    _progress("Reviewer is scoring against the 9 principles (seeing the artwork)...")
    consult_transcript = consultation.get("transcript", [])
    consult_decision = consultation.get("brief") or {}
    review  = reviewer_score(req.theme, image_prompt, listing,
                              consultation_transcript=consult_transcript,
                              image_path=image_path,
                              consultation_decision=consult_decision,
                              quote_grounded=quote_grounded if verified_quote else None)
    _log("scribe",   "write",   listing)
    _log("reviewer", "score_1", review)

    # Editing log — shown in the dashboard transcript viewer so the revision
    # work is visible. Kept separate from consult_transcript so the Reviewer's
    # Principle-4 evidence stays pure consultation.
    editing_log = image_revision_log + [
        {"agent": "Scribe", "role": "listing draft — attempt 1 (editing)",
         # Full description, not a head-truncated preview — edits in later
         # rounds land in paragraph 2+, and a fixed [:500] cap always showed
         # the same unchanged opening paragraph, making real revisions look
         # like no-ops even when the score was visibly moving.
         "message": f"Title: {listing.get('title', '')}\n\n"
                    f"{str(listing.get('description', ''))}"},
        {"agent": "Reviewer", "role": "score — attempt 1 (editing)",
         "message": _review_summary(review)},
    ]

    best_listing, best_review = listing, review
    # The revision chain always builds on the LATEST listing and review — never
    # on stale 'best' state. Revising best-with-best after a worse score just
    # reproduces the identical text and re-rolls the scoring dice (observed:
    # attempts 3 and 4 byte-identical, scored 6.2 then 6.8). Forward chaining
    # also guarantees the Reviewer's 'find' strings match the text they edit.
    cur_listing, cur_review = listing, review
    attempt = 1
    stalled = 0  # consecutive revisions that failed to beat the best score

    while best_review.get("overall", 0) < req.target_score and attempt < req.max_attempts:
        attempt += 1
        _progress(
            f"Score {cur_review.get('overall', 0)}/10 — weakest: {_weakest(cur_review)}. "
            f"Scribe is revising (attempt {attempt}/{req.max_attempts})..."
        )
        revised, revise_note, changes = _apply_review_feedback(cur_listing, cur_review, verified_quote)
        if revised == cur_listing:
            editing_log.append(
                {"agent": "System", "role": "editing stopped",
                 "message": "The review produced no applicable text changes — "
                            f"keeping the best version ({best_review.get('overall', 0)}/10)."})
            break
        changes_preview = "\n".join(f"  - {c[:220]}" for c in changes[:8])
        editing_log.append(
            {"agent": "Scribe", "role": f"revision — attempt {attempt} (editing)",
             "message": f"Addressing: {cur_review.get('recommendation', '')[:300]}\n"
                        f"How: {revise_note}\n"
                        + (f"Changes:\n{changes_preview}\n" if changes_preview else "")
                        # Full description (see attempt-1 comment above) — the
                        # point of this log is to let a human confirm the text
                        # actually changed, which a fixed head-truncation defeats.
                        + f"\nNew title: {revised.get('title', '')}\n\n"
                        f"{str(revised.get('description', ''))}"})
        _progress(f"Reviewer is re-scoring revision {attempt}/{req.max_attempts}...")
        review = reviewer_score(req.theme, image_prompt, revised,
                                 consultation_transcript=consult_transcript,
                                 image_path=image_path,
                                 previous_review=cur_review,
                                 changes_applied=changes,
                                 consultation_decision=consult_decision,
                                 quote_grounded=quote_grounded if verified_quote else None)
        prev_overall = cur_review.get("overall", 0)
        new_overall = review.get("overall", 0)
        trend = "improved" if new_overall > prev_overall else "did not improve"
        editing_log.append(
            {"agent": "Reviewer", "role": f"score — attempt {attempt} (editing)",
             "message": f"Overall {new_overall}/10 (was {prev_overall}/10 — {trend}).\n"
                        f"{_review_summary(review)}"})
        _log("scribe",   f"revise_{attempt}", revised)
        _log("reviewer", f"score_{attempt}",  review)

        cur_listing, cur_review = revised, review
        best_overall = best_review.get("overall", 0)
        if new_overall > best_overall:
            best_listing, best_review = revised, review
            stalled = 0
        elif new_overall == best_overall:
            # Tie goes to the newer listing — it has incorporated more feedback
            # (a tie previously discarded the revision that finally fixed the
            # redundancy the Reviewer had flagged for three rounds). A tie is
            # NOT counted toward the stall budget: real (if score-invisible)
            # progress was made, and it previously got stopped one attempt
            # short of a fix (e.g. 'remove mismatched tags') that was queued
            # up but never tried because a tie was treated as a failure.
            best_listing, best_review = revised, review
        else:
            # Only a genuine regression counts against the stall budget.
            stalled += 1
        if stalled >= 2:
            editing_log.append(
                {"agent": "System", "role": "editing stopped",
                 "message": f"Two revisions in a row scored worse than the best — "
                            f"keeping the best version ({best_review.get('overall', 0)}/10)."})
            break

    return {
        "listing":        best_listing,
        "review":         best_review,
        "attempts":       attempt,
        "target_reached": best_review.get("overall", 0) >= req.target_score,
        "consultation":   consultation["transcript"] + editing_log,
        "image_path":     image_path,
        "image_prompt":   image_prompt,
    }


# --- Pipeline: full theme → bookmark run (dashboard entry point) ---

class PipelineRunRequest(BaseModel):
    theme: str
    target_score: float = 9.0
    max_attempts: int = 3
    aspect_ratio: str = "2:3"


def _generate_bookmark(theme: str, task_id: str, target_score: float, max_attempts: int,
                       aspect_ratio: str, progress, on_turn=None, request_human_input=None) -> dict:
    """
    Shared core of the bookmark pipeline: Librarian retrieval → Artist brief +
    generate → consultation/write/score/revise. Used both for a fresh
    /pipeline/run and for a product's targeted or full regeneration — those
    differ only in what happens to the RESULT (create a new product row vs.
    overwrite an existing one), never in how the result is produced.
    on_turn/request_human_input pass through to the consultation for the live
    chat view and the post-round-2 pause for Sheraj's input.
    Returns: {image_prompt, image_path, listing, review, attempts,
              target_reached, consultation}
    """
    from agents.artist import build_image_prompt, generate_image

    progress("Librarian is gathering passages from the writings...")
    try:
        citations = retrieve(theme, n_results=3) or []
    except Exception as e:
        # Retrieval failure is reported honestly but doesn't kill the run —
        # consultation Turn 4 has a designed fallback for zero citations.
        citations = []
        progress(f"Librarian retrieval unavailable ({e}) — continuing; "
                 "the Librarian will verify against known texts in consultation.")
    log_run(task_id, "librarian", "retrieve", theme[:200],
            f"{len(citations)} passages retrieved",
            passed_review=len(citations) > 0)

    progress("Artist is composing the image brief (local Qwen3)...")
    image_prompt = build_image_prompt(theme, citations)
    log_run(task_id, "artist", "brief", theme[:200], image_prompt[:200],
            passed_review=bool(image_prompt.strip()))

    progress("Artist is generating the artwork (xAI)...")
    gen = generate_image(image_prompt, aspect_ratio)
    image_path = gen.get("image_url", "")
    log_run(task_id, "artist", "generate", image_prompt[:200], image_path[:200],
            passed_review=bool(image_path) and Path(image_path).exists())

    wa_req = WriteApproveRequest(
        theme=theme, image_prompt=image_prompt, citations=citations,
        image_url=image_path, task_id=task_id,
        target_score=target_score, max_attempts=max_attempts,
    )
    wa = _pipeline_write_approve_sync(wa_req, progress, on_turn=on_turn,
                                      request_human_input=request_human_input)
    # The consultation may have agreed on an image adjustment, in which case the
    # Artist regenerated the artwork — everything downstream uses the final image.
    image_path = wa.get("image_path") or image_path
    image_prompt = wa.get("image_prompt") or image_prompt

    return {
        "image_prompt":   image_prompt,
        "image_path":     image_path,
        "listing":        wa["listing"],
        "review":         wa["review"],
        "attempts":       wa["attempts"],
        "target_reached": wa["target_reached"],
        "consultation":   wa["consultation"],
    }


def _render_and_publish(product_id: str, task_id: str, image_path: str, listing: dict, progress) -> dict:
    """
    Shared finishing steps once a product row exists: Compositor front/back
    render + Canva autofill. Returns {front_path, back_path, compositor_error, canva}.
    """
    from agents.compositor import render_bookmark_pair

    progress("Compositor is rendering front and back halves...")
    front_path, back_path = "", ""
    compositor_error = None
    quote = (listing.get("bookmark_quote") or "").strip()
    try:
        if not quote:
            raise ValueError("Listing has no bookmark_quote to overlay")
        rendered = render_bookmark_pair(image_path, quote)
        front_path, back_path = rendered["front_path"], rendered["back_path"]
        update_product(product_id, front_image=front_path, back_image=back_path)
        log_run(task_id, "compositor", "render", image_path[:200], front_path[:200],
                passed_review=bool(front_path) and Path(front_path).exists())
    except Exception as e:
        compositor_error = str(e)
        log_run(task_id, "compositor", "render", image_path[:200],
                f"failed: {e}"[:400], passed_review=False)

    progress("Sending front image to Canva (skips gracefully if not connected)...")
    canva = {"skipped": True, "reason": "Canva not configured", "design_url": None}
    try:
        from agents.canva import autofill_bookmark, CANVA_CLIENT_ID, CANVA_TEMPLATE_ID
        if CANVA_CLIENT_ID and CANVA_TEMPLATE_ID and front_path:
            canva = autofill_bookmark(front_path)
            log_run(task_id, "artist", "canva_autofill",
                    front_path[:200], (canva.get("design_url") or "")[:200])
    except Exception as e:
        canva = {"skipped": True, "reason": str(e), "design_url": None}
        log_run(task_id, "artist", "canva_autofill",
                front_path[:200], f"failed: {e}"[:400], passed_review=False)

    return {"front_path": front_path, "back_path": back_path,
            "compositor_error": compositor_error, "canva": canva}


def _run_full_pipeline(req: PipelineRunRequest, progress, on_turn=None, request_human_input=None) -> dict:
    """
    The whole bookmark pipeline in one background job:
    task → Librarian → Artist brief → Artist generate → consultation/write/score
    → save product → Compositor → Canva autofill.
    """
    progress("Creating task...")
    task_id = create_task(req.theme, "design", assigned_to="operator")

    gen = _generate_bookmark(req.theme, task_id, req.target_score, req.max_attempts,
                             req.aspect_ratio, progress, on_turn=on_turn,
                             request_human_input=request_human_input)
    listing, review = gen["listing"], gen["review"]
    image_path, image_prompt = gen["image_path"], gen["image_prompt"]

    progress("Saving product...")
    product_id = create_product(
        task_id=task_id,
        title=listing.get("title", req.theme),
        image_url=image_path,
        listing_copy=json.dumps(listing),
        image_prompt=image_prompt,
        theme=req.theme,
    )
    # Persist the consultation transcript so later re-scoring (e.g. the Improve
    # button) can present the same Principle-4 evidence the original score saw.
    # target_reached/attempts persist too: a stalled best-effort ship must stay
    # distinguishable from a clean pass after the in-memory job record is gone.
    update_product(product_id, reviewer_scores=json.dumps(review),
                   consultation=json.dumps(gen["consultation"]),
                   target_reached=1 if gen["target_reached"] else 0,
                   attempts=gen["attempts"])

    finish = _render_and_publish(product_id, task_id, image_path, listing, progress)

    update_task_status(task_id, "completed")
    overall = review.get("overall", 0)

    return {
        "task_id":          task_id,
        "product_id":       product_id,
        "theme":            req.theme,
        "image_prompt":     image_prompt,
        "image_path":       image_path,
        "image_web":        _web_image_path(image_path),
        "front_image_path": finish["front_path"],
        "front_image_web":  _web_image_path(finish["front_path"]),
        "back_image_path":  finish["back_path"],
        "back_image_web":   _web_image_path(finish["back_path"]),
        "compositor_error": finish["compositor_error"],
        "listing":          listing,
        "review":           review,
        "attempts":         gen["attempts"],
        "target_reached":   gen["target_reached"],
        "badge":            _badge(overall),
        "consultation":     gen["consultation"],
        "canva":            finish["canva"],
    }


@app.post("/pipeline/run")
def pipeline_run(req: PipelineRunRequest):
    """
    Dashboard entry point: run the ENTIRE bookmark pipeline from a theme.
    Returns {job_id} immediately; poll GET /pipeline/status/{job_id}.
    """
    if not req.theme.strip():
        raise HTTPException(status_code=422, detail="theme is required")
    job_id = _start_job(
        "full-pipeline",
        lambda progress, on_turn, ask: _run_full_pipeline(req, progress, on_turn, ask),
    )
    return {"job_id": job_id, "status": "running"}


# --- Pipeline: Quote Cards (giveaway product line — parallel to bookmarks) ---
#
# A quote card is NOT sold: no listing, no Etsy, no pricing. The deliverable
# is a verified quote + welcoming artwork + optional AI-labeled translation,
# rendered as a 3.5"x2" front/back PNG pair. See docs/fable5-briefing-quote-cards.md.

class CardPipelineRequest(BaseModel):
    theme: str
    language: Optional[str] = None   # LANGUAGES code ("es"/"zh"/"ar") or None for English-only
    target_score: float = 9.0
    max_attempts: int = 3
    # Redo-everything steer (regenerate-card-all only; empty for a normal new
    # card run). Folded into the retrieval query and image brief ONLY — the
    # stored theme/title stay the clean original so repeated redos don't
    # accumulate "NEW DIRECTION" text into permanent storage.
    guidance: str = ""


# Honesty disclosure for the card's ARTWORK (principle 3) — a fixed string
# stored in the card's metadata and shown on the dashboard, same discipline as
# the translation disclaimers. Whether it also gets printed on the card back
# is a design decision for Sheraj (hard rule 9: any card-face change ships
# only after a human-viewed render), so it is metadata-only for now.
CARD_ART_DISCLOSURE = (
    "Artwork created with AI image-generation tools, art-directed and curated by Sheraj. "
    "The quote is a verbatim excerpt from Ruhi Institute Book 1."
)


@app.get("/card/languages")
def card_languages():
    """Translation languages the card pipeline offers (config in translator.py)."""
    from agents.translator import LANGUAGES
    return [
        {"code": code, "name": cfg["name"], "native_name": cfg["native_name"]}
        for code, cfg in LANGUAGES.items()
    ]


_SENTENCE_END_RE = re.compile(r'[.!?](?=\s|$)')


def _trim_card_quote(text: str, limit: int = 150) -> str:
    """
    Trim a passage to a card-appropriate excerpt at a SENTENCE boundary —
    always a complete sentence, never a mid-sentence hard cut. A card that
    trails off with no closing punctuation ("...and verities will come to")
    reads as a broken render, not a deliberate excerpt — worse than a longer
    but complete quote. Takes as many whole sentences as fit within `limit`;
    if even the first sentence alone exceeds it, takes that one sentence in
    full anyway and lets the Card Compositor's own auto-shrink (or its
    raise-if-it-still-doesn't-fit guard) handle the length, rather than
    truncating it into a broken fragment here.
    """
    text = text.strip()
    if len(text) <= limit:
        return text
    ends = [m.end() for m in _SENTENCE_END_RE.finditer(text)]
    if not ends:
        return text  # no sentence punctuation at all — return whole passage rather than guess a cut
    cut = next((e for e in ends if e > limit), None)
    if cut is None:
        cut = ends[-1]
    else:
        fits = [e for e in ends if e <= limit]
        if fits:
            cut = fits[-1]
    return text[:cut].strip()


def _best_matching_citation(quote: str, citations: list[dict]) -> dict:
    """
    Bag-of-words overlap: which of the (up to 3) retrieved Ruhi Book 1
    passages does the consultation's proposed quote most closely track?
    Used to replace the LLM's own wording with that passage's VERBATIM text —
    see the comment at its call site in _run_card_pipeline for why this
    exists: live testing caught the Librarian blending two different
    retrieved passages into one composite quote and crediting the whole
    thing to only one of their sources, with its own round-2 verdict
    overriding round-1's correct 'ORIGINAL COMPOSITION' self-assessment.
    Never trust that self-report for a claim this consequential — verify
    deterministically instead, the same discipline as _sanitize_claims.
    """
    def norm(s: str) -> set:
        return set(re.sub(r"[^a-z0-9 ]", " ", s.lower()).split())

    quote_words = norm(quote)
    best, best_score = citations[0], -1
    for c in citations:
        score = len(quote_words & norm(c.get("text", "")))
        if score > best_score:
            best, best_score = c, score
    return best


def _librarian_source_from(transcript: list, citations: list) -> str:
    """
    The citation line printed on the card: the Librarian's own SOURCE:
    attribution from the latest consultation turn, falling back to the top
    retrieved passage's source metadata.
    """
    for turn in reversed(transcript or []):
        if turn.get("agent") == "Librarian":
            m = re.search(r"SOURCE:\s*(.+)", turn.get("message", ""))
            if m:
                return m.group(1).strip()
    if citations:
        return str(citations[0].get("source") or "").strip()
    return ""


def _run_card_pipeline(req: CardPipelineRequest, progress, on_turn=None,
                       request_human_input=None, existing_product_id: str = None) -> dict:
    """
    The whole quote-card pipeline in one background job:
    task → Librarian → Artist (card brief + generate) → consultation (card
    framing, includes the post-round-2 pause for Sheraj) → optional
    translation → Card Compositor → Reviewer (card rubric, sees the rendered
    front) → simple revision loop (re-pick quote or repaint artwork — there is
    no listing text to edit) → save product.

    existing_product_id: set only by the "redo everything" redirect action —
    overwrites that product's row in place (same in-place-redo contract as
    bookmarks' _redo_product) instead of creating a new one.
    """
    from agents.artist import build_card_image_prompt, generate_image
    from agents.card_compositor import render_quote_card
    from agents.consultation import run_consultation, run_card_revision_consultation
    from agents.reviewer import score_quote_card
    from agents.translator import translate_quote, LANGUAGES

    lang_name = LANGUAGES[req.language]["name"] if req.language else None
    # Redo-everything guidance steers retrieval/artwork only — req.theme
    # itself (used for title, stored theme, and every requote step below)
    # stays clean so a redo never bakes "NEW DIRECTION from Sheraj" text
    # into permanent storage the way appending it to req.theme would.
    retrieval_query = f"{req.theme}\n\n{req.guidance}" if req.guidance.strip() else req.theme

    progress("Creating task...")
    task_id = create_task(req.theme, "card_design", assigned_to="operator")

    # Quote cards may ONLY ever quote Ruhi Institute Book 1, "Reflections on
    # the Life of the Spirit" (owner decision, 2026-07) — retrieve_ruhi_book1
    # searches that restricted index, never the full 7-text corpus the
    # bookmark pipeline uses. Unlike the bookmark path (which tolerates empty
    # retrieval by letting the consultation's Librarian draw on "well-known
    # writings" generally), an empty result here must fail the job outright:
    # falling through to open-ended sourcing would silently break the
    # restriction the moment retrieval hiccups.
    progress("Librarian is searching Ruhi Book 1 for passages...")
    citations = retrieve_ruhi_book1(retrieval_query, n_results=3) or []
    log_run(task_id, "librarian", "retrieve", retrieval_query[:200],
            f"{len(citations)} passages retrieved", passed_review=len(citations) > 0)
    if not citations:
        raise RuntimeError(
            "No passage found in the Ruhi Book 1 index for this theme, or the index "
            "isn't built yet — run scripts/ingest_ruhi_book1.py. Quote cards only ever "
            "draw from Reflections on the Life of the Spirit, so this can't fall back "
            "to the general library."
        )

    progress("Artist is composing the card image brief (local Qwen3)...")
    image_prompt = build_card_image_prompt(retrieval_query, citations)
    log_run(task_id, "artist", "card_brief", req.theme[:200], image_prompt[:200],
            passed_review=bool(image_prompt.strip()))

    progress("Artist is generating the artwork (xAI)...")
    gen = generate_image(image_prompt, "2:3")
    image_path = gen.get("image_url", "")
    log_run(task_id, "artist", "generate", image_prompt[:200], image_path[:200],
            passed_review=bool(image_path) and Path(image_path).exists())

    # ── Consultation (card framing) ──────────────────────────────────────────
    def _preview_front(quote: str, transcript: list) -> str:
        """LLM-free front-face render for the pause. Translation doesn't exist
        yet at this point in the pipeline, so the preview is English-only —
        the preview_note below says so rather than letting Sheraj assume the
        final card looks exactly like this."""
        preview = render_quote_card(image_path, quote,
                                    _librarian_source_from(transcript, citations))
        return _web_image_path(preview["front_path"])

    consultation = {"transcript": [], "context": "", "brief": {}}
    try:
        consultation = run_consultation(
            image_path, req.theme, image_prompt, citations,
            progress=progress, on_turn=on_turn,
            request_human_input=request_human_input, product="quote_card",
            render_preview=_preview_front,
            preview_note=(
                "The image above is the card's front face as it would print right now."
                + (f" The {lang_name} translation isn't added yet — it goes on right "
                   "after this step, with its AI-assisted label." if lang_name else "")
            ),
        )
        vq = (consultation.get("verified_quote") or "").strip()
        log_run(task_id, "consultation", "consult", req.theme[:200],
                f"{len(consultation['transcript'])} turns completed",
                passed_review=bool(vq and len(vq) > 10))
    except Exception as e:
        consultation["transcript"] = [{"agent": "System", "role": "error",
                                       "message": f"Consultation skipped: {e}"}]
        log_run(task_id, "consultation", "consult", req.theme[:200],
                f"failed: {e}"[:400], passed_review=False)

    editing_log = []

    # Honour the consultation's image decision (same contract as bookmarks:
    # regenerate ONCE so the shipped card reflects what the team agreed).
    brief = consultation.get("brief") or {}
    adjustment = (brief.get("image_adjustment") or "").strip()
    if adjustment and image_path:
        progress(f"Artist is repainting per the consultation: {adjustment[:120]}...")
        try:
            revised_prompt = (f"{image_prompt}\n\n"
                              f"IMPORTANT adjustment agreed in team consultation: {adjustment}")
            regen = generate_image(revised_prompt, "2:3")
            new_path = regen.get("image_url", "")
            if new_path and Path(new_path).exists():
                image_path, image_prompt = new_path, revised_prompt
                editing_log.append(
                    {"agent": "Artist", "role": "image revision (consultation)",
                     "message": f"Repainted the artwork per the team's agreed adjustment:\n{adjustment}"})
                # passed_review stays None: generating a file is mechanical
                # success, not a quality verdict — trust only moves on judged
                # outcomes (principle 8).
                log_run(task_id, "artist", "regenerate",
                        adjustment[:200], new_path[:200])
        except Exception as e:
            editing_log.append(
                {"agent": "Artist", "role": "image revision (consultation)",
                 "message": f"Regeneration failed ({e}) — continuing with the original artwork."})
            log_run(task_id, "artist", "regenerate", adjustment[:200],
                    f"failed: {e}"[:400], passed_review=False)

    # ── The quote (and its honesty flags) ────────────────────────────────────
    # The consultation's own wording is NEVER printed as-is: whatever quote
    # (or fragment of one) it proposed is used only to pick WHICH of the
    # retrieved Ruhi Book 1 passages the team meant — the printed text and
    # citation are always that passage's own verbatim (trimmed) text and true
    # source metadata. This is deterministic by construction (every passage
    # in `citations` came from retrieve_ruhi_book1, so this can never
    # surface a quote from outside the book), and it closes a real failure
    # mode caught live: a Librarian round can blend two different retrieved
    # passages into one composite line and credit the whole thing to just
    # one of them — round 2 once reversed round 1's own correct "ORIGINAL
    # COMPOSITION" verdict to "GROUNDED" for exactly this kind of blend.
    proposed = (consultation.get("verified_quote") or "").strip()
    if proposed and citations:
        matched = _best_matching_citation(proposed, citations)
    elif citations:
        matched = citations[0]
    else:
        raise RuntimeError(
            "No verified quote available: consultation produced none and no passages "
            "were retrieved. Build the index (scripts/ingest_ruhi_book1.py) or retry."
        )
    quote = _trim_card_quote(matched["text"])
    quote_grounded = True  # always true — it's a verbatim excerpt of an indexed Book 1 passage
    citation_src = str(matched.get("source") or "").strip()

    # ── Optional translation (Grok; labeled AI-assisted by code, not the LLM) ─
    def _translate(q: str) -> Optional[dict]:
        if not req.language:
            return None
        progress(f"Translating the quote into {lang_name} (xAI Grok)...")
        try:
            tr = translate_quote(q, req.language)
        except Exception as first_err:
            progress(f"Translation attempt failed ({first_err}) — retrying once...")
            tr = translate_quote(q, req.language)  # second failure raises → job errors honestly
        log_run(task_id, "translator", "translate", q[:200],
                tr["text"][:200], passed_review=True)
        return tr

    try:
        translation = _translate(quote)
    except Exception as e:
        log_run(task_id, "translator", "translate", quote[:200],
                f"failed: {e}"[:400], passed_review=False)
        raise RuntimeError(f"Translation into {lang_name} failed twice: {e}") from e

    # ── Render → Score → Revise loop ─────────────────────────────────────────
    # No listing text exists, so revision levers are re-picking the quote
    # ("requote") or regenerating the artwork ("repaint") — chosen by the
    # Reviewer's machine-readable `action`, never inferred from prose.
    def _render(q: str, tr: Optional[dict]) -> dict:
        progress("Card Compositor is rendering the front and back faces...")
        r = render_quote_card(image_path, q, citation_src, translation=tr)
        log_run(task_id, "compositor", "render_card", image_path[:200],
                r["front_path"][:200],
                passed_review=bool(r["front_path"]) and Path(r["front_path"]).exists())
        return r

    def _score(q: str, tr: Optional[dict], rendered: dict, prev=None, note=None) -> dict:
        progress("Reviewer is scoring the card (seeing the rendered front face)...")
        return score_quote_card(
            req.theme, q, citation_src, quote_grounded,
            front_image_path=rendered["front_path"], translation=tr,
            consultation_transcript=consultation.get("transcript"),
            consultation_decision=brief or None,
            previous_review=prev, revision_note=note,
        )

    latest_citations = citations  # updated after each requote so the team always sees current candidates
    revision_history = []  # [{attempt, action, guidance, overall, prev_overall}, ...] this run

    def _team_decide(rendered: dict, review: dict, attempt: int) -> dict:
        """
        The whole team weighs in on the Reviewer's scored concerns before the
        pipeline commits to a revision — previously the Reviewer's own
        action/action_guidance drove requote/repaint unilaterally ("the last
        part just has the reviewer saying stuff" — owner feedback, 2026-07).
        Skipped when the card already meets target: no need to convene the
        team just to say "ship".
        """
        if review.get("overall", 0) >= req.target_score:
            return {"action": "ship", "action_guidance": ""}
        decision = run_card_revision_consultation(
            req.theme, quote, citation_src, rendered["front_path"], latest_citations,
            review, progress=progress, on_turn=on_turn, attempt=attempt,
            history=revision_history,
        )
        editing_log.extend(decision["transcript"])
        # passed_review=None — holding a consultation is process, not a judged
        # outcome; trust only moves on quality verdicts (principle 8).
        log_run(task_id, "consultation", f"card_revision_consult_{attempt}",
                req.theme[:200], decision["action"])
        return decision

    rendered = _render(quote, translation)
    review = _score(quote, translation, rendered)
    log_run(task_id, "reviewer", "card_score_1", req.theme[:200],
            json.dumps({"overall": review.get("overall")})[:200],
            passed_review=review.get("passed"))
    editing_log.append(
        {"agent": "Reviewer", "role": "card score — attempt 1 (editing)",
         "message": f"Overall {review.get('overall', 0)}/10.\n"
                    f"Recommendation: {review.get('recommendation', '')}"})

    best = {"quote": quote, "grounded": quote_grounded, "citation": citation_src,
            "translation": translation, "rendered": rendered, "review": review,
            "image_path": image_path, "image_prompt": image_prompt}
    cur_review = review
    attempt = 1
    stalled = 0

    decision = (_team_decide(rendered, review, attempt=1)
                if attempt < req.max_attempts else {"action": "ship", "action_guidance": ""})
    cur_action, cur_guidance = decision["action"], decision["action_guidance"]

    while best["review"].get("overall", 0) < req.target_score and attempt < req.max_attempts:
        action = cur_action
        guidance = cur_guidance
        if action not in ("requote", "repaint"):
            editing_log.append({"agent": "System", "role": "editing stopped",
                                "message": "The team's consultation reached no further revision "
                                           f"action — keeping the best card ({best['review'].get('overall', 0)}/10)."})
            break
        attempt += 1

        if action == "requote":
            progress(f"Re-picking the quote per the Reviewer: {guidance[:100] or req.theme}...")
            try:
                passages = retrieve_ruhi_book1(guidance.strip() or req.theme, n_results=3) or []
            except Exception:
                passages = []
            if passages:
                latest_citations = passages  # keep the team's view of "other candidates" current
            pick = next((p for p in passages
                         if _trim_card_quote(p["text"]) != quote), None)
            if not pick:
                editing_log.append({"agent": "System", "role": "editing stopped",
                                    "message": "No different passage found for the Reviewer's "
                                               "steer — keeping the best card."})
                break
            quote = _trim_card_quote(pick["text"])
            quote_grounded = True
            citation_src = str(pick.get("source") or "").strip() or citation_src
            # passed_review=None — finding a different passage is mechanical;
            # whether it HELPED is judged by the re-score that follows.
            log_run(task_id, "librarian", f"requote_{attempt}", guidance[:200], quote[:200])
            try:
                translation = _translate(quote)
            except Exception as e:
                log_run(task_id, "translator", "translate", quote[:200],
                        f"failed: {e}"[:400], passed_review=False)
                editing_log.append({"agent": "System", "role": "editing stopped",
                                    "message": f"Translation of the re-picked quote failed ({e}) — "
                                               "keeping the best card."})
                break
            revision_note = f'Quote re-picked per your steer: now "{quote[:120]}" ({citation_src})'
        else:  # repaint
            progress(f"Artist is repainting per the Reviewer: {guidance[:100]}...")
            try:
                new_prompt = (f"{image_prompt}\n\nIMPORTANT change requested in review: "
                              f"{guidance or 'better express the theme'}")
                regen = generate_image(new_prompt, "2:3")
                new_path = regen.get("image_url", "")
                if not (new_path and Path(new_path).exists()):
                    raise RuntimeError("no image returned")
                image_path, image_prompt = new_path, new_prompt
                # passed_review=None — same reasoning as requote above.
                log_run(task_id, "artist", f"repaint_{attempt}", guidance[:200],
                        new_path[:200])
            except Exception as e:
                log_run(task_id, "artist", f"repaint_{attempt}", guidance[:200],
                        f"failed: {e}"[:400], passed_review=False)
                editing_log.append({"agent": "System", "role": "editing stopped",
                                    "message": f"Repaint failed ({e}) — keeping the best card."})
                break
            revision_note = f"Artwork regenerated per your steer: {guidance[:150]}"

        rendered = _render(quote, translation)
        new_review = _score(quote, translation, rendered, prev=cur_review, note=revision_note)
        log_run(task_id, "reviewer", f"card_score_{attempt}", req.theme[:200],
                json.dumps({"overall": new_review.get("overall")})[:200],
                passed_review=new_review.get("passed"))
        prev_overall = cur_review.get("overall", 0)
        new_overall = new_review.get("overall", 0)
        editing_log.append(
            {"agent": "Reviewer", "role": f"card score — attempt {attempt} (editing)",
             "message": f"Overall {new_overall}/10 (was {prev_overall}/10 — "
                        f"{'improved' if new_overall > prev_overall else 'did not improve'}).\n"
                        f"Applied: {revision_note}\n"
                        f"Recommendation: {new_review.get('recommendation', '')}"})

        revision_history.append({"attempt": attempt, "action": action, "guidance": guidance,
                                  "overall": new_overall, "prev_overall": prev_overall})
        cur_review = new_review
        decision = (_team_decide(rendered, new_review, attempt=attempt)
                    if attempt < req.max_attempts else {"action": "ship", "action_guidance": ""})
        cur_action, cur_guidance = decision["action"], decision["action_guidance"]
        best_overall = best["review"].get("overall", 0)
        if new_overall >= best_overall:
            # Ties adopt the newer card — it incorporated more feedback
            # (same invariant as the listing loop). Only strict regressions
            # count toward the 2-strike stall.
            best = {"quote": quote, "grounded": quote_grounded, "citation": citation_src,
                    "translation": translation, "rendered": rendered, "review": new_review,
                    "image_path": image_path, "image_prompt": image_prompt}
            if new_overall > best_overall:
                stalled = 0
        else:
            stalled += 1
        if stalled >= 2:
            editing_log.append({"agent": "System", "role": "editing stopped",
                                "message": f"Two revisions in a row scored worse than the best — "
                                           f"keeping the best card ({best['review'].get('overall', 0)}/10)."})
            break

    # ── Save ─────────────────────────────────────────────────────────────────
    progress("Saving the quote card...")
    tr = best["translation"]
    card_copy = {
        "product_kind": "quote_card",
        "quote": best["quote"],
        "quote_grounded": best["grounded"],
        "citation": best["citation"],
        "language": req.language,
        "language_name": (tr or {}).get("name"),
        "translation_text": (tr or {}).get("text"),
        "translation_disclaimer_native": (tr or {}).get("disclaimer_native"),
        "translation_disclaimer_en": (tr or {}).get("disclaimer_en"),
        # Fixed string, never LLM-written — same honesty class as the
        # translation disclaimers above.
        "artwork_disclosure": CARD_ART_DISCLOSURE,
    }
    title = f"Quote card — {req.theme[:70]}" + (f" ({lang_name})" if lang_name else "")
    if existing_product_id:
        product_id = existing_product_id
        update_product(
            product_id, title=title, image_url=best["image_path"],
            listing_copy=json.dumps(card_copy), image_prompt=best["image_prompt"],
            theme=req.theme, product_type="quote_card",
        )
    else:
        product_id = create_product(
            task_id=task_id, title=title, image_url=best["image_path"],
            listing_copy=json.dumps(card_copy), image_prompt=best["image_prompt"],
            theme=req.theme, product_type="quote_card",
        )
    full_transcript = consultation.get("transcript", []) + editing_log
    overall = best["review"].get("overall", 0)
    update_product(product_id,
                   reviewer_scores=json.dumps(best["review"]),
                   consultation=json.dumps(full_transcript),
                   front_image=best["rendered"]["front_path"],
                   back_image=best["rendered"]["back_path"],
                   target_reached=1 if overall >= req.target_score else 0,
                   attempts=attempt)
    update_task_status(task_id, "completed")
    return {
        "task_id": task_id,
        "product_id": product_id,
        "product_type": "quote_card",
        "theme": req.theme,
        "language": req.language,
        "language_name": lang_name,
        "quote": best["quote"],
        "quote_grounded": best["grounded"],
        "citation": best["citation"],
        "translation": tr,
        "image_prompt": best["image_prompt"],
        "image_path": best["image_path"],
        "image_web": _web_image_path(best["image_path"]),
        "front_image_path": best["rendered"]["front_path"],
        "front_image_web": _web_image_path(best["rendered"]["front_path"]),
        "back_image_path": best["rendered"]["back_path"],
        "back_image_web": _web_image_path(best["rendered"]["back_path"]),
        "compositor_error": None,
        "review": best["review"],
        "attempts": attempt,
        "target_reached": overall >= req.target_score,
        "badge": _badge(overall),
        "consultation": full_transcript,
    }


@app.post("/pipeline/run-card")
def pipeline_run_card(req: CardPipelineRequest):
    """
    Dashboard entry point for the Quote Cards pipeline (parallel to
    /pipeline/run, which stays bookmark-only). Returns {job_id} immediately;
    poll GET /pipeline/status/{job_id}.
    """
    from agents.translator import LANGUAGES
    if not req.theme.strip():
        raise HTTPException(status_code=422, detail="theme is required")
    if req.language and req.language not in LANGUAGES:
        raise HTTPException(status_code=422,
                            detail=f"Unknown language '{req.language}' — offered: {sorted(LANGUAGES)}")
    job_id = _start_job(
        "card-pipeline",
        lambda progress, on_turn, ask: _run_card_pipeline(req, progress, on_turn, ask),
    )
    return {"job_id": job_id, "status": "running"}


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


@app.post("/x-post")
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


@app.get("/x-post/pending")
def x_post_pending():
    from agents.state import get_pending_x_posts
    rows = get_pending_x_posts("pending")
    for r in rows:
        r["image_web"] = _web_image_path(r.get("image_path")) if r.get("image_path") else None
    return rows


@app.get("/x-post/drafts")
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


@app.get("/x-post/posted")
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


@app.patch("/x-post/{post_id}")
def x_post_edit(post_id: str, req: XPostEditRequest):
    """
    Hand-edit a pending draft's tweet text directly — bypasses the Scribe/
    Reviewer pipeline entirely, same discipline as PATCH /products/{id} for
    bookmarks. Only pending drafts are editable; the locked quote/author and
    image aren't touched (this edits the tweet's wording only).
    """
    from agents.state import get_x_post, update_x_post
    from agents.x_post import TWEET_HARD_MAX

    post = get_x_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.get("status") not in ("pending", "draft"):
        raise HTTPException(status_code=422, detail=f"Post is already {post.get('status')} — only pending/draft posts can be edited")

    text = req.tweet_text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="tweet_text cannot be empty")
    if len(text) > TWEET_HARD_MAX:
        raise HTTPException(status_code=422,
                            detail=f"tweet_text is {len(text)} characters — exceeds the {TWEET_HARD_MAX} hard maximum")

    update_x_post(post_id, tweet_text=text)
    return {"id": post_id, "tweet_text": text}


class XPostRegenerateImageRequest(BaseModel):
    guidance: str = ""   # optional — unlike the bookmark equivalent, works fine with none


@app.post("/x-post/{post_id}/regenerate-image")
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


@app.post("/x-post/{post_id}/save-draft")
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


@app.post("/x-post/{post_id}/restore")
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


@app.post("/x-post/approve/{post_id}")
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


@app.post("/x-post/discard/{post_id}")
def x_post_discard(post_id: str):
    """Discards for good — no 'discarded' status kept around; only what
    actually got posted is worth remembering (see GET /x-post/posted)."""
    from agents.state import get_x_post, delete_x_post
    post = get_x_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    delete_x_post(post_id)
    return {"id": post_id, "status": "discarded"}


# --- Products endpoints ---

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
    from agents.reviewer import score as reviewer_score

    with _connect() as conn:
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Product not found")

    product = dict(row)
    _require_bookmark(product)
    image_url    = product.get("image_url", "")
    image_prompt = product.get("image_prompt", "")
    theme        = product.get("theme", "")
    listing_copy = product.get("listing_copy", "{}")
    raw_scores   = product.get("reviewer_scores", "{}")

    listing        = json.loads(listing_copy) if listing_copy else {}
    current_review = json.loads(raw_scores) if raw_scores else {}
    current_score  = current_review.get("overall", 0.0)

    # Re-score under the SAME conditions that produced the saved score: the
    # Reviewer must see the artwork and the consultation transcript. Without
    # them the re-score is structurally lower (no Principle-4 evidence, no
    # image), so 'improved' could never come true no matter how good the
    # revision — the original Improve-button bug.
    try:
        consult_transcript = json.loads(product.get("consultation") or "[]")
    except (json.JSONDecodeError, TypeError):
        consult_transcript = []
    if not consult_transcript:
        # Product saved before transcripts were persisted. The team DID consult
        # during the original run — tell the Reviewer the record is missing so
        # Principle 4 is judged neutrally instead of as an absence.
        consult_transcript = [{
            "agent": "System", "role": "note",
            "message": "The team consulted in two rounds during the original pipeline run, "
                       "but this product predates transcript storage. Score Principle 4 "
                       "neutrally on the process that is documented — do not penalise the "
                       "missing record itself.",
        }]

    # The bookmark quote is Librarian-verified — lock it through every revision
    verified_quote = (listing.get("bookmark_quote") or "").strip()

    if not theme and listing.get("title"):
        theme = listing["title"]

    extra_instructions = (
        [f"Guidance from Sheraj (top priority): {req.human_notes}"] if req.human_notes else []
    )

    best_listing = listing
    best_review  = current_review
    # Forward chain: always revise the latest listing with the latest review
    cur_listing, cur_review = listing, current_review
    attempt      = 0

    while best_review.get("overall", 0) < req.target_score and attempt < req.max_attempts:
        attempt += 1
        revised, revise_note, changes = _apply_review_feedback(
            cur_listing, cur_review, verified_quote,
            extra_instructions=extra_instructions,
        )
        extra_instructions = []  # human guidance is applied once, not re-applied every round
        if revised == cur_listing:
            break  # nothing actionable — don't burn a Reviewer call on an identical listing
        new_review = reviewer_score(theme, image_prompt, revised,
                                    consultation_transcript=consult_transcript,
                                    image_path=image_url,
                                    previous_review=cur_review or None,
                                    changes_applied=changes)
        log_run(product_id, "scribe",    f"improve_{attempt}", theme[:200],
                f"{revise_note}: " + json.dumps(revised)[:350])
        log_run(product_id, "reviewer",  f"improve_score_{attempt}", theme[:200],
                json.dumps({"overall": new_review.get("overall")})[:200],
                passed_review=new_review.get("passed", False))

        cur_listing, cur_review = revised, new_review
        if new_review.get("overall", 0) >= best_review.get("overall", 0):
            # Ties go to the newer listing — it has incorporated more feedback
            best_listing = revised
            best_review  = new_review

    # Persist when the score rose OR a same-score revision incorporated more
    # feedback (tie-adopt) — otherwise the returned listing and the stored one
    # would silently diverge.
    improved = best_review.get("overall", 0) > current_score or best_listing != listing
    if improved:
        update_product(
            product_id,
            title=best_listing.get("title", theme),
            listing_copy=json.dumps(best_listing),
            reviewer_scores=json.dumps(best_review),
            target_reached=1 if best_review.get("overall", 0) >= req.target_score else 0,
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


# --- Targeted regeneration: quote / image / everything ---
#
# "Improve listing" (above) only ever edits the LISTING TEXT — it can't touch
# the locked quote or the artwork. These three endpoints let Sheraj redirect
# any of those, steered by free-text guidance, before the next review pass.
# Quote and image regeneration are synchronous (a single generation + rescore,
# comparable in length to Improve); "redo everything" re-runs the full
# pipeline and can take minutes, so it runs as a background job like
# /pipeline/run.

def _load_product_or_404(product_id: str) -> dict:
    from agents.state import _connect
    with _connect() as conn:
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Product not found")
    return dict(row)


def _require_bookmark(product: dict):
    """
    Guard for bookmark-only actions (improve/regenerate/publish): running them
    on a quote card would push it through listing/bookmark machinery it doesn't
    have (e.g. re-rendering a 3.5x2 card as a 2x6 bookmark). The dashboard
    hides these actions for cards; this makes the API honest about it too.
    """
    if (product.get("product_type") or "bookmark") != "bookmark":
        raise HTTPException(
            status_code=422,
            detail="This action applies to bookmark products only — quote cards have no "
                   "listing to improve or publish; re-run the card pipeline instead.",
        )


@app.get("/products/{product_id}/print-sheet")
def get_print_sheet(product_id: str):
    """
    Render a cut-tolerant, multi-up print sheet for this product's saved
    front/back faces: a single 2-page Letter PDF (page 1 = fronts grid,
    page 2 = backs grid), regenerated fresh from the CURRENT front_image/
    back_image every call so it always reflects the latest artwork.
    Card size and grid count are derived automatically from the face
    images themselves -- see agents/print_sheet.py.
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
        build_print_sheet(front_path, back_path, str(out_path))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not build the print sheet: {e}")

    safe_title = re.sub(r"[^A-Za-z0-9]+", "-", product.get("title") or "card").strip("-") or "card"
    return FileResponse(
        path=str(out_path),
        media_type="application/pdf",
        filename=f"{safe_title}-print-sheet.pdf",
    )


class RegenerateQuoteRequest(BaseModel):
    guidance: str = ""   # e.g. "make it about detachment instead of unity"

@app.post("/products/{product_id}/regenerate-quote")
def regenerate_quote(product_id: str, req: RegenerateQuoteRequest):
    """
    Replace ONLY the printed quote. Re-searches the Librarian's index (steered
    by guidance if given), re-renders front/back with the new quote overlaid
    on the SAME artwork, lightly adjusts the description to introduce the new
    quote instead of the old one, and re-scores. Always saves — this is a
    deliberate creative decision, not a quality-gated auto-improve like
    /improve, so an unchanged or lower score is not a reason to discard it.
    """
    from agents.scribe import revise_listing_light, _sanitize_claims
    from agents.compositor import render_bookmark_pair
    from agents.reviewer import score as reviewer_score

    product = _load_product_or_404(product_id)
    _require_bookmark(product)
    listing = json.loads(product.get("listing_copy") or "{}")
    image_url    = product.get("image_url", "")
    image_prompt = product.get("image_prompt", "")
    theme        = product.get("theme") or listing.get("title", "")
    old_quote    = (listing.get("bookmark_quote") or "").strip()

    # Guidance alone, not theme+guidance — the whole point of asking for a new
    # quote is to steer AWAY from the current theme, but embedding similarity
    # is dominated by whichever text is longer/more specific, so appending
    # guidance to the theme buried it and just re-found the old quote's
    # passage (verified live: "detachment from the world" alone retrieves
    # Bahá'u'lláh's actual detachment passage; theme+guidance combined
    # retrieved the original UHJ passage again instead).
    query = req.guidance.strip() or theme
    passages = retrieve(query, n_results=3) or []
    if not passages:
        raise HTTPException(
            status_code=422,
            detail="No matching passage found in the indexed writings for that guidance. "
                   "Try different wording, or run scripts/ingest_texts.py if the index isn't built.",
        )

    candidate = passages[0]["text"].strip()
    # Trim to a bookmark-length excerpt at a sentence boundary — matches the
    # 120-250 char quote length the Scribe targets elsewhere in the pipeline.
    if len(candidate) > 260:
        cut = candidate.rfind(".", 0, 260)
        candidate = candidate[:cut + 1] if cut > 60 else candidate[:260]
    new_quote = candidate

    instruction = (
        f'The bookmark\'s printed quote has changed from "{old_quote}" to "{new_quote}" '
        f"(source: {passages[0].get('source', '')}). Rewrite the description so it introduces "
        "and reflects THIS quote instead of the old one"
        + (f", per Sheraj's guidance: {req.guidance}" if req.guidance.strip() else "") + "."
    )
    listing = revise_listing_light(listing, [instruction], new_quote)
    listing["bookmark_quote"] = new_quote  # force — light editor must never miss the new lock
    listing = _sanitize_claims(listing)

    try:
        rendered = render_bookmark_pair(image_url, new_quote)
        front_path, back_path = rendered["front_path"], rendered["back_path"]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not re-render the bookmark: {e}")

    try:
        consult_transcript = json.loads(product.get("consultation") or "[]")
    except (json.JSONDecodeError, TypeError):
        consult_transcript = []
    old_review = json.loads(product.get("reviewer_scores") or "{}")
    review = reviewer_score(theme, image_prompt, listing,
                            consultation_transcript=consult_transcript,
                            image_path=image_url, previous_review=old_review or None)

    update_product(
        product_id, title=listing.get("title", theme), listing_copy=json.dumps(listing),
        reviewer_scores=json.dumps(review), front_image=front_path, back_image=back_path,
    )
    log_run(product_id, "librarian", "regenerate_quote", query[:200], new_quote[:200])

    return {
        "product_id": product_id,
        "old_quote": old_quote, "new_quote": new_quote, "source": passages[0].get("source", ""),
        "old_score": old_review.get("overall", 0), "new_score": review.get("overall", 0),
        "listing": listing, "review": review,
        "front_image_web": _web_image_path(front_path), "back_image_web": _web_image_path(back_path),
    }


class RegenerateImageRequest(BaseModel):
    guidance: str   # required — e.g. "more vibrant colors, remove the lotus, add mountains"

@app.post("/products/{product_id}/regenerate-image")
def regenerate_image(product_id: str, req: RegenerateImageRequest):
    """
    Replace ONLY the artwork. Repaints from the original image prompt plus
    fresh guidance, keeps the existing (locked) quote, re-renders front/back
    on the new artwork, lightly adjusts the description for any visual
    details that no longer apply, and re-scores. Always saves.
    """
    from agents.artist import generate_image
    from agents.scribe import revise_listing_light, _sanitize_claims
    from agents.compositor import render_bookmark_pair
    from agents.reviewer import score as reviewer_score

    if not req.guidance.strip():
        raise HTTPException(status_code=422,
                            detail="guidance is required — describe what should change about the artwork")

    product = _load_product_or_404(product_id)
    _require_bookmark(product)
    listing = json.loads(product.get("listing_copy") or "{}")
    old_image_prompt = product.get("image_prompt", "")
    theme = product.get("theme") or listing.get("title", "")
    quote = (listing.get("bookmark_quote") or "").strip()
    if not quote:
        raise HTTPException(status_code=422, detail="Listing has no bookmark_quote to overlay")

    new_prompt = f"{old_image_prompt}\n\nIMPORTANT new direction from Sheraj: {req.guidance}"
    try:
        gen = generate_image(new_prompt, "2:3")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Image generation error: {e}")
    new_image_path = gen.get("image_url", "")

    try:
        rendered = render_bookmark_pair(new_image_path, quote)
        front_path, back_path = rendered["front_path"], rendered["back_path"]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not render the new artwork: {e}")

    instruction = (
        f"The artwork was repainted per this guidance: {req.guidance}. If the description names "
        "specific visual details (motifs, colors, elements) that may no longer match the new "
        "artwork, update them; otherwise leave the text as-is."
    )
    listing = revise_listing_light(listing, [instruction], quote)
    listing["bookmark_quote"] = quote  # force — locked field
    listing = _sanitize_claims(listing)

    try:
        consult_transcript = json.loads(product.get("consultation") or "[]")
    except (json.JSONDecodeError, TypeError):
        consult_transcript = []
    old_review = json.loads(product.get("reviewer_scores") or "{}")
    review = reviewer_score(theme, new_prompt, listing,
                            consultation_transcript=consult_transcript,
                            image_path=new_image_path, previous_review=old_review or None)

    update_product(
        product_id, title=listing.get("title", theme), image_url=new_image_path,
        image_prompt=new_prompt, listing_copy=json.dumps(listing),
        reviewer_scores=json.dumps(review), front_image=front_path, back_image=back_path,
    )
    log_run(product_id, "artist", "regenerate_image", req.guidance[:200], new_image_path[:200])

    return {
        "product_id": product_id,
        "old_score": old_review.get("overall", 0), "new_score": review.get("overall", 0),
        "listing": listing, "review": review,
        "image_web": _web_image_path(new_image_path),
        "front_image_web": _web_image_path(front_path), "back_image_web": _web_image_path(back_path),
    }


class RegenerateAllRequest(BaseModel):
    guidance: str = ""

def _redo_product(product_id: str, req: RegenerateAllRequest, progress,
                  on_turn=None, request_human_input=None) -> dict:
    """
    Full redo: re-run the ENTIRE pipeline (Librarian, Artist, consultation,
    Scribe, Reviewer) from the theme, optionally steered by fresh guidance,
    and overwrite the existing product's row in place — for when the whole
    piece, not just one field, needs to change.

    A "redo" is a single fresh pass, not a hunt for a target score — that's
    what Improve/New quote/New artwork are for. max_attempts=1 means the
    write→score→revise loop in _generate_bookmark never enters its revise
    branch (attempt < max_attempts is immediately false), so whatever the
    Reviewer scores this ONE new attempt is what gets saved, no matter what
    it is. target_score is irrelevant with max_attempts=1 but a real float is
    still required by _generate_bookmark's signature.
    """
    product = _load_product_or_404(product_id)
    base_theme = product.get("theme") or json.loads(product.get("listing_copy") or "{}").get("title", "")
    theme = f"{base_theme}\n\nNEW DIRECTION from Sheraj: {req.guidance}" if req.guidance.strip() else base_theme

    progress("Redoing the whole piece from scratch...")
    task_id = create_task(theme, "design", assigned_to="operator")

    gen = _generate_bookmark(theme, task_id, target_score=10.0, max_attempts=1,
                             aspect_ratio="2:3", progress=progress, on_turn=on_turn,
                             request_human_input=request_human_input)
    listing, review = gen["listing"], gen["review"]
    image_path, image_prompt = gen["image_path"], gen["image_prompt"]

    progress("Saving the redone product...")
    update_product(
        product_id, title=listing.get("title", base_theme), image_url=image_path,
        listing_copy=json.dumps(listing), image_prompt=image_prompt, theme=base_theme,
        reviewer_scores=json.dumps(review), consultation=json.dumps(gen["consultation"]),
        target_reached=1 if gen["target_reached"] else 0, attempts=gen["attempts"],
    )

    finish = _render_and_publish(product_id, task_id, image_path, listing, progress)
    update_task_status(task_id, "completed")

    return {
        "product_id": product_id, "task_id": task_id,
        "listing": listing, "review": review,
        "attempts": gen["attempts"], "target_reached": gen["target_reached"],
        "consultation": gen["consultation"],
        "image_web": _web_image_path(image_path),
        "front_image_web": _web_image_path(finish["front_path"]),
        "back_image_web": _web_image_path(finish["back_path"]),
        "canva": finish["canva"],
    }

@app.post("/products/{product_id}/regenerate-all")
def regenerate_all(product_id: str, req: RegenerateAllRequest):
    """
    Background job: redo the ENTIRE product (image, quote, listing, score)
    from its theme plus fresh guidance, overwriting this product in place.
    Returns {job_id} immediately; poll GET /pipeline/status/{job_id}.
    """
    _require_bookmark(_load_product_or_404(product_id))  # fail fast before starting the job
    job_id = _start_job(
        "redo-product",
        lambda progress, on_turn, ask: _redo_product(product_id, req, progress, on_turn, ask),
    )
    return {"job_id": job_id, "status": "running"}


# --- Quote card "redirect the team" — same three levers as bookmarks above,
# adapted for a product with no listing: requote (Ruhi Book 1 only, hard
# rule 11), repaint, or redo everything. ---

def _require_card(product: dict):
    """Inverse of _require_bookmark: these three actions assume Ruhi-Book1-
    only retrieval and the card rubric/compositor, which a bookmark has
    neither of."""
    if (product.get("product_type") or "bookmark") != "quote_card":
        raise HTTPException(
            status_code=422,
            detail="This action applies to quote cards only — bookmarks use "
                   "regenerate-quote/regenerate-image/regenerate-all instead.",
        )


def _card_translation_dict(card_copy: dict) -> Optional[dict]:
    """Reconstructs translate_quote()'s dict shape from what's stored on the
    product, for re-rendering with an UNCHANGED translation (regenerate-card-
    image). Must include "code" — render_quote_card's font/RTL shaping keys
    off it, not off language_name."""
    if not card_copy.get("language"):
        return None
    return {
        "code": card_copy.get("language"),
        "name": card_copy.get("language_name"),
        "text": card_copy.get("translation_text"),
        "disclaimer_native": card_copy.get("translation_disclaimer_native"),
        "disclaimer_en": card_copy.get("translation_disclaimer_en"),
    }


class RegenerateCardQuoteRequest(BaseModel):
    guidance: str = ""   # e.g. "something about detachment instead of unity"

@app.post("/products/{product_id}/regenerate-card-quote")
def regenerate_card_quote(product_id: str, req: RegenerateCardQuoteRequest):
    """
    Replace ONLY the printed quote — same "redirect" contract as bookmarks'
    regenerate-quote, but sourced exclusively from Ruhi Book 1 (hard rule 11:
    retrieve_ruhi_book1, never the general library) and always verbatim, so
    quote_grounded stays True by construction. Re-renders on the SAME
    artwork, re-translates if the card has a translation, and re-scores with
    the card rubric. Always saves — a deliberate creative decision, not a
    quality-gated auto-improve.
    """
    from agents.card_compositor import render_quote_card
    from agents.reviewer import score_quote_card
    from agents.translator import translate_quote

    product = _load_product_or_404(product_id)
    _require_card(product)
    card_copy = json.loads(product.get("listing_copy") or "{}")
    theme = product.get("theme") or ""
    image_path = product.get("image_url", "")
    old_quote = card_copy.get("quote", "")
    language = card_copy.get("language")

    query = req.guidance.strip() or theme
    passages = retrieve_ruhi_book1(query, n_results=3) or []
    if not passages:
        raise HTTPException(
            status_code=422,
            detail="No matching passage found in the Ruhi Book 1 index for that guidance. "
                   "Try different wording, or run scripts/ingest_ruhi_book1.py if the index isn't built.",
        )
    pick = next((p for p in passages if _trim_card_quote(p["text"]) != old_quote), passages[0])
    new_quote = _trim_card_quote(pick["text"])
    citation_src = str(pick.get("source") or "").strip()

    translation = None
    if language:
        try:
            translation = translate_quote(new_quote, language)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Translation failed: {e}")

    try:
        rendered = render_quote_card(image_path, new_quote, citation_src, translation=translation)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not re-render the card: {e}")

    old_review = json.loads(product.get("reviewer_scores") or "{}")
    try:
        consult_transcript = json.loads(product.get("consultation") or "[]")
    except (json.JSONDecodeError, TypeError):
        consult_transcript = []
    review = score_quote_card(
        theme, new_quote, citation_src, True,
        front_image_path=rendered["front_path"], translation=translation,
        consultation_transcript=consult_transcript, previous_review=old_review or None,
    )

    card_copy["quote"] = new_quote
    card_copy["quote_grounded"] = True
    card_copy["citation"] = citation_src
    if language:
        card_copy["language_name"] = translation.get("name")
        card_copy["translation_text"] = translation.get("text")
        card_copy["translation_disclaimer_native"] = translation.get("disclaimer_native")
        card_copy["translation_disclaimer_en"] = translation.get("disclaimer_en")

    update_product(
        product_id, listing_copy=json.dumps(card_copy),
        reviewer_scores=json.dumps(review),
        front_image=rendered["front_path"], back_image=rendered["back_path"],
    )
    log_run(product_id, "librarian", "regenerate_card_quote", query[:200], new_quote[:200])

    return {
        "product_id": product_id,
        "old_quote": old_quote, "new_quote": new_quote, "citation": citation_src,
        "old_score": old_review.get("overall", 0), "new_score": review.get("overall", 0),
        "review": review,
        "front_image_web": _web_image_path(rendered["front_path"]),
        "back_image_web": _web_image_path(rendered["back_path"]),
    }


class RegenerateCardImageRequest(BaseModel):
    guidance: str   # required — e.g. "more vibrant colors, remove the lotus, add mountains"

@app.post("/products/{product_id}/regenerate-card-image")
def regenerate_card_image(product_id: str, req: RegenerateCardImageRequest):
    """
    Replace ONLY the artwork. Repaints from the original image prompt plus
    fresh guidance, keeps the existing (locked) quote/citation/translation,
    re-renders, and re-scores with the card rubric. Always saves.
    """
    from agents.artist import generate_image
    from agents.card_compositor import render_quote_card
    from agents.reviewer import score_quote_card

    if not req.guidance.strip():
        raise HTTPException(status_code=422,
                            detail="guidance is required — describe what should change about the artwork")

    product = _load_product_or_404(product_id)
    _require_card(product)
    card_copy = json.loads(product.get("listing_copy") or "{}")
    theme = product.get("theme") or ""
    old_image_prompt = product.get("image_prompt", "")
    quote = card_copy.get("quote", "")
    citation_src = card_copy.get("citation", "")
    quote_grounded = card_copy.get("quote_grounded", True)
    translation = _card_translation_dict(card_copy)

    new_prompt = f"{old_image_prompt}\n\nIMPORTANT new direction from Sheraj: {req.guidance}"
    try:
        gen = generate_image(new_prompt, "2:3")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Image generation error: {e}")
    new_image_path = gen.get("image_url", "")

    try:
        rendered = render_quote_card(new_image_path, quote, citation_src, translation=translation)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not render the new artwork: {e}")

    old_review = json.loads(product.get("reviewer_scores") or "{}")
    try:
        consult_transcript = json.loads(product.get("consultation") or "[]")
    except (json.JSONDecodeError, TypeError):
        consult_transcript = []
    review = score_quote_card(
        theme, quote, citation_src, quote_grounded,
        front_image_path=rendered["front_path"], translation=translation,
        consultation_transcript=consult_transcript, previous_review=old_review or None,
    )

    update_product(
        product_id, image_url=new_image_path, image_prompt=new_prompt,
        reviewer_scores=json.dumps(review),
        front_image=rendered["front_path"], back_image=rendered["back_path"],
    )
    log_run(product_id, "artist", "regenerate_card_image", req.guidance[:200], new_image_path[:200])

    return {
        "product_id": product_id,
        "old_score": old_review.get("overall", 0), "new_score": review.get("overall", 0),
        "review": review,
        "image_web": _web_image_path(new_image_path),
        "front_image_web": _web_image_path(rendered["front_path"]),
        "back_image_web": _web_image_path(rendered["back_path"]),
    }


def _redo_card(product_id: str, req: RegenerateAllRequest, progress,
              on_turn=None, request_human_input=None) -> dict:
    """
    Full redo of a quote card: re-run the ENTIRE card pipeline (Librarian,
    Artist, consultation, translation, Card Compositor, Reviewer) from the
    same theme/language, optionally steered by fresh guidance, overwriting
    this product's row in place — mirrors bookmarks' _redo_product.
    """
    product = _load_product_or_404(product_id)
    card_copy = json.loads(product.get("listing_copy") or "{}")
    theme = product.get("theme") or ""
    language = card_copy.get("language")

    progress("Redoing the whole card from scratch...")
    card_req = CardPipelineRequest(theme=theme, language=language, guidance=req.guidance)
    return _run_card_pipeline(card_req, progress, on_turn, request_human_input,
                              existing_product_id=product_id)


@app.post("/products/{product_id}/regenerate-card-all")
def regenerate_card_all(product_id: str, req: RegenerateAllRequest):
    """
    Background job: redo the ENTIRE quote card (artwork, quote, translation,
    score) from its theme plus fresh guidance, overwriting this product in
    place. Returns {job_id} immediately; poll GET /pipeline/status/{job_id}.
    """
    _require_card(_load_product_or_404(product_id))
    job_id = _start_job(
        "redo-card",
        lambda progress, on_turn, ask: _redo_card(product_id, req, progress, on_turn, ask),
    )
    return {"job_id": job_id, "status": "running"}


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
    Bypasses the Scribe/Reviewer pipeline entirely — for when Sheraj wants to
    hand-fix a listing rather than re-run consultation."""
    from agents.state import _connect

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

    for field, value in edits.items():
        listing[field] = value

    update_kwargs = {"listing_copy": json.dumps(listing)}
    if "title" in edits:
        update_kwargs["title"] = edits["title"]
    update_product(product_id, **update_kwargs)

    return {"product_id": product_id, "listing": listing}


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


# --- Etsy Open API v3 endpoints ---

@app.get("/etsy/oauth/start")
def etsy_oauth_start():
    """Step 1 of Etsy OAuth. Open in a browser — redirects to Etsy for one-time approval."""
    from agents.etsy import build_auth_url
    from fastapi.responses import RedirectResponse, HTMLResponse
    if not os.getenv("ETSY_CLIENT_ID"):
        return HTMLResponse("""
            <html><body style="font-family:sans-serif;padding:2em">
            <h2>⚠️ Etsy keystring missing</h2>
            <p>Add your Etsy credentials to <strong>.env</strong> first:</p>
            <ol>
              <li>Go to <a href="https://www.etsy.com/developers/your-apps" target="_blank">etsy.com/developers/your-apps</a> and create an app</li>
              <li>Set the callback URL to: <code>http://localhost:8765/etsy/oauth/callback</code></li>
              <li>Copy the <em>Keystring</em> into <code>ETSY_CLIENT_ID</code> and the shared secret into <code>ETSY_CLIENT_SECRET</code></li>
              <li>Add your numeric <code>ETSY_SHOP_ID</code> (from your shop URL or dashboard)</li>
              <li>Restart the API, then revisit this page</li>
            </ol>
            </body></html>
        """, status_code=400)
    return RedirectResponse(url=build_auth_url())


@app.get("/etsy/oauth/callback")
def etsy_oauth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
):
    """Etsy redirects here after approval. Exchanges the code for tokens."""
    from agents.etsy import exchange_code
    from fastapi.responses import HTMLResponse

    if error:
        return HTMLResponse(f"""
            <html><body style="font-family:sans-serif;padding:2em">
            <h2>❌ Etsy authorisation failed</h2>
            <p><strong>Error:</strong> {error}</p>
            <p><strong>Details:</strong> {error_description or 'No details provided'}</p>
            <p><a href="/etsy/oauth/start">Try again</a>.</p>
            </body></html>
        """, status_code=400)

    if not code or not state:
        return HTMLResponse("""
            <html><body style="font-family:sans-serif;padding:2em">
            <h2>❌ Missing authorisation code</h2>
            <p>Etsy did not return an authorisation code. <a href="/etsy/oauth/start">Try again</a>.</p>
            </body></html>
        """, status_code=400)

    try:
        exchange_code(code, state)
        return HTMLResponse("""
            <html><body style="font-family:sans-serif;padding:2em">
            <h2>✅ Etsy connected!</h2>
            <p>Your bahAI Workforce can now create draft listings in your shop.</p>
            <p>You can close this tab.</p>
            </body></html>
        """)
    except Exception as e:
        return HTMLResponse(f"""
            <html><body style="font-family:sans-serif;padding:2em">
            <h2>❌ Token exchange failed</h2>
            <p>{e}</p>
            <p><a href="/etsy/oauth/start">Try again</a>.</p>
            </body></html>
        """, status_code=400)


@app.get("/etsy/status")
def etsy_status():
    """Check whether Etsy is configured and authorised."""
    from agents.etsy import is_authorised, ETSY_CLIENT_ID, ETSY_SHOP_ID
    return {
        "configured": bool(ETSY_CLIENT_ID and ETSY_SHOP_ID),
        "authorised": is_authorised(),
        "shop_id": ETSY_SHOP_ID or None,
    }


@app.post("/etsy/publish")
def etsy_publish(body: dict):
    """
    Create a DRAFT Etsy listing from a saved product (title, description, tags,
    price parsed from price_note) and upload the front bookmark image.
    Nothing goes live — drafts are reviewed and activated by Sheraj inside Etsy.
    """
    from agents.etsy import publish_draft_listing
    from agents.state import get_agent_status
    product_id = body.get("product_id", "")
    if not product_id:
        raise HTTPException(status_code=422, detail="product_id is required")

    from agents.state import _connect
    with _connect() as conn:
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Product not found")
    product = dict(row)
    _require_bookmark(product)

    # Trust gate (principle 8 — trust must have a real consequence, not just a
    # display number): publishing toward the outside world requires the
    # Reviewer to have earned at least Human-on-the-loop (level 2). Below
    # that, Sheraj must explicitly confirm — the dashboard turns this response
    # into a confirm step and retries with confirm=true.
    if not body.get("confirm"):
        reviewer = get_agent_status("reviewer") or {}
        level = int(reviewer.get("trust_level") or 0)
        if level < 2:
            level_name = reviewer.get("trust_level_name", "Shadow/Advisory")
            return {
                "requires_confirmation": True,
                "trust_level": level,
                "trust_level_name": level_name,
                "reason": (f"The Reviewer's trust level is {level} ({level_name}) — below "
                           "Human-on-the-loop (2). Its scores haven't yet earned unattended "
                           "publishing, so please confirm this draft yourself."),
            }

    if product.get("etsy_listing_id"):
        return {
            "skipped": True,
            "reason": f"Product already has Etsy listing {product['etsy_listing_id']}",
            "etsy_listing_id": product["etsy_listing_id"],
        }

    try:
        result = publish_draft_listing(product)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Etsy publish failed: {e}")

    if result.get("skipped"):
        return result

    listing_id = str(result["listing_id"])
    update_product(product_id, etsy_listing_id=listing_id, status="draft_on_etsy")
    log_run(product.get("task_id") or product_id, "producer", "etsy_publish",
            product.get("title", "")[:200], f"listing_id={listing_id}")
    return {
        "product_id": product_id,
        "etsy_listing_id": listing_id,
        "state": result.get("state", "draft"),
        "url": result.get("url"),
        "image_uploaded": result.get("image_uploaded", False),
        "image_error": result.get("image_error"),
    }


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
def steward_report():
    """
    Profit-and-loss view across all products. Costs are a labeled hybrid:
    runs since METERING_EPOCH are METERED — every paid Grok/vision/image call
    records itself via state.record_spend (see router.record_api_spend), so a
    repaint-heavy run costs visibly more than a clean one — while products
    from before metering existed carry a flat LEGACY_COST_PER_PRODUCT
    estimate rather than a misleading $0.
    """
    from agents.state import get_spend_summary
    products = get_all_products()
    total_revenue = sum(float(p.get("revenue") or 0) for p in products)
    spend = get_spend_summary()

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

    return {
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

TRUST_BADGES = {
    (9.0, 10.1): "EXCEPTIONAL",
    (7.0,  9.0): "APPROVED",
    (5.0,  7.0): "BORDERLINE",
    (0.0,  5.0): "REJECTED",
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


# --- Google Workspace OAuth (the Secretary's; mirrors the Etsy flow) ---
# One consent screen, one token, shared by Calendar/Gmail/Drive/Docs/Sheets/
# Slides (agents/google_auth.py). Renamed from /gcal/* now that it covers
# more than Calendar — single-user project, no back-compat shim needed.

@app.get("/google/oauth/start")
def google_oauth_start():
    """Step 1 of Google OAuth. Open in a browser — one-time approval."""
    from agents.google_auth import build_auth_url
    from fastapi.responses import RedirectResponse, HTMLResponse
    if not os.getenv("GOOGLE_CLIENT_ID"):
        return HTMLResponse("""
            <html><body style="font-family:sans-serif;padding:2em">
            <h2>⚠️ Google credentials missing</h2>
            <p>Add Google credentials to <strong>.env</strong> first (one-time, ~5 minutes):</p>
            <ol>
              <li>Go to <a href="https://console.cloud.google.com/projectcreate" target="_blank">console.cloud.google.com</a> and create a project (any name, e.g. "bahAI Secretary")</li>
              <li>In <em>APIs &amp; Services → Library</em>, enable: <strong>Google Calendar API</strong>,
              <strong>Gmail API</strong>, <strong>Google Drive API</strong>, <strong>Google Docs API</strong>,
              <strong>Google Sheets API</strong>, and <strong>Google Slides API</strong></li>
              <li>In <em>APIs &amp; Services → OAuth consent screen</em>: choose <strong>External</strong>, fill in the app name and your email, and add yourself (sherajr22@gmail.com) as a <strong>Test user</strong></li>
              <li>In <em>APIs &amp; Services → Credentials → Create credentials → OAuth client ID</em>: choose <strong>Web application</strong> and add this authorized redirect URI: <code>http://localhost:8765/google/oauth/callback</code></li>
              <li>Copy the Client ID into <code>GOOGLE_CLIENT_ID</code> and the secret into <code>GOOGLE_CLIENT_SECRET</code> in <code>.env</code></li>
              <li>Restart the API, then revisit this page</li>
            </ol>
            </body></html>
        """, status_code=400)
    return RedirectResponse(url=build_auth_url())


@app.get("/google/oauth/callback")
def google_oauth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    """Google redirects here after approval. Exchanges the code and creates
    her Calendar + Drive sandboxes (idempotent — safe on reconnect too)."""
    from agents.google_auth import exchange_code
    from agents.gcal import ensure_secretary_calendar, SECRETARY_CALENDAR_NAME
    from agents.gdrive import ensure_secretary_folder, SECRETARY_FOLDER_NAME
    from fastapi.responses import HTMLResponse

    if error or not code or not state:
        return HTMLResponse(f"""
            <html><body style="font-family:sans-serif;padding:2em">
            <h2>❌ Google authorisation failed</h2>
            <p>{error or 'No authorisation code returned.'}</p>
            <p><a href="/google/oauth/start">Try again</a>.</p>
            </body></html>
        """, status_code=400)
    try:
        exchange_code(code, state, on_connected=lambda: (
            ensure_secretary_calendar(), ensure_secretary_folder()))
        return HTMLResponse(f"""
            <html><body style="font-family:sans-serif;padding:2em">
            <h2>✅ Google Workspace connected!</h2>
            <p>Your Secretary created her own calendar, <strong>"{SECRETARY_CALENDAR_NAME}"</strong>,
            and her own Drive folder, <strong>"{SECRETARY_FOLDER_NAME}"</strong>, and can now see your
            schedule and search/read your Gmail, Drive, Docs, Sheets, and Slides. You can close this
            tab and go back to the dashboard.</p>
            </body></html>
        """)
    except Exception as e:
        return HTMLResponse(f"""
            <html><body style="font-family:sans-serif;padding:2em">
            <h2>❌ Token exchange failed</h2><p>{e}</p>
            <p><a href="/google/oauth/start">Try again</a>.</p>
            </body></html>
        """, status_code=400)


@app.get("/google/status")
def google_status():
    from agents.google_auth import is_authorised
    from agents.gcal import her_calendar_id
    return {
        "configured": bool(os.getenv("GOOGLE_CLIENT_ID")),
        "authorised": is_authorised(),
        "secretary_calendar": her_calendar_id(),
    }


# --- WhatsApp (Secretary Phase 3, Meta Cloud API) ---
#
# The webhook below is the one endpoint in this whole API meant to be
# reachable from the public internet (via a Cloudflare Tunnel restricted to
# this path only — see /whatsapp/setup). It has no session/cookie auth like
# a browser-facing endpoint would; agents.whatsapp.verify_signature() is the
# entire security boundary. Never relax or bypass that check.

@app.get("/whatsapp/setup")
def whatsapp_setup():
    """Guided setup page, same style as /google/oauth/start's inline
    instructions — Sheraj is non-technical and this involves several
    external steps (Meta Developer account, test number, Cloudflare
    Tunnel) with no simple one-click OAuth flow to walk him through."""
    from agents import whatsapp
    configured = whatsapp.is_configured()
    status_line = ("✅ All WhatsApp settings are filled in below." if configured else
                   "⚠️ Some settings below are still empty.")
    return HTMLResponse(f"""
        <html><body style="font-family:sans-serif;max-width:700px;margin:2em auto;line-height:1.5">
        <h2>Connect the Secretary to WhatsApp</h2>
        <p>{status_line}</p>
        <h3>1. Create a Meta Developer app</h3>
        <ol>
          <li>Go to <a href="https://developers.facebook.com/apps" target="_blank">developers.facebook.com/apps</a>
              and create an app of type <strong>"Business"</strong>.</li>
          <li>Add the <strong>WhatsApp</strong> product to the app.</li>
          <li>Meta gives you a <strong>free test phone number</strong> automatically — start with that
              before requesting a real one.</li>
        </ol>
        <h3>2. Collect three values from the WhatsApp → API Setup page</h3>
        <ul>
          <li><code>WHATSAPP_TOKEN</code> — the temporary access token shown there (or a permanent
              one from System Users, once you're ready to go beyond testing)</li>
          <li><code>WHATSAPP_PHONE_NUMBER_ID</code> — shown right above the token</li>
          <li><code>WHATSAPP_APP_SECRET</code> — App Settings → Basic → App Secret (click "Show")</li>
        </ul>
        <h3>3. Pick your own values for two more</h3>
        <ul>
          <li><code>WHATSAPP_VERIFY_TOKEN</code> — any password-like string you make up (used only to
              confirm to Meta that the webhook is really yours)</li>
          <li><code>WHATSAPP_OWNER_NUMBER</code> — YOUR WhatsApp number in international format,
              e.g. <code>+15551234567</code> (this is the only number that gets full Secretary access)</li>
        </ul>
        <p>Put all five into your <code>.env</code> file (already has empty placeholders) and restart the API.</p>
        <h3>4. Expose this server to the internet — ONE path only</h3>
        <p>Meta needs to reach <code>/whatsapp/webhook</code> on this machine. Don't tunnel your whole API —
           install <a href="https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
           target="_blank">cloudflared</a> and use a config that only proxies the webhook path, e.g.:</p>
        <pre style="background:#f4f4f4;padding:1em;border-radius:6px">tunnel: bahai-secretary
credentials-file: &lt;path cloudflared gives you after 'cloudflared tunnel login'&gt;

ingress:
  - hostname: your-chosen-subdomain.your-domain.com
    path: /whatsapp/webhook
    service: http://localhost:8765
  - service: http_status:404</pre>
        <p>Then run <code>cloudflared tunnel run bahai-secretary</code> and leave it running alongside the API.</p>
        <h3>5. Point Meta at the webhook</h3>
        <ol>
          <li>In WhatsApp → Configuration, set the Callback URL to
              <code>https://your-chosen-subdomain.your-domain.com/whatsapp/webhook</code> and the
              Verify Token to whatever you picked for <code>WHATSAPP_VERIFY_TOKEN</code>.</li>
          <li>Click <strong>Verify and Save</strong> — Meta will call the webhook once to confirm it.</li>
          <li>Subscribe to the <strong>messages</strong> field.</li>
          <li>You'll also need to <strong>publish the app</strong> (requires a privacy policy URL —
              use <code>/whatsapp/privacy</code> on this same tunnel) before Meta will deliver real
              messages, not just dashboard test events.</li>
          <li><strong>Easy to miss:</strong> none of the above actually tells your WhatsApp Business
              Account (WABA) to send its events to THIS app — that's a separate link. Check with
              <code>GET https://graph.facebook.com/v21.0/&lt;WABA_ID&gt;/subscribed_apps</code> (bearer
              token = <code>WHATSAPP_TOKEN</code>). If your app isn't in the list (e.g. after
              reconnecting the app in Meta's UI, which can silently repoint it at Meta's own
              "WA DevX Webhook Events 1P App"), fix it with
              <code>POST</code> to that same URL. Meta's "Check test webhooks" log will show real
              messages arriving even when this is broken — it doesn't confirm delivery to your
              callback, only that Meta generated the event.</li>
        </ol>
        <h3>6. Message the test number from your phone</h3>
        <p>Save the test number as a contact and send it a message — the Secretary should reply.</p>
        <h3>7. (Later) the 24-hour-window fallback template</h3>
        <p>WhatsApp only allows free-form replies within 24 hours of your last message. For a reminder
           sent after a quiet day, submit a simple template for Meta's review (Message Templates →
           Create): name it <code>{whatsapp.WHATSAPP_UPDATE_TEMPLATE}</code>, category "Utility", body
           text <code>Update from Sheraj's assistant: {{{{1}}}}</code>. Approval can take up to a day —
           reminders work over the dashboard regardless while you wait.</p>
        <p><a href="/secretary/status">Check current connection status</a></p>
        </body></html>
    """)


@app.get("/whatsapp/privacy")
def whatsapp_privacy():
    """Privacy policy for Meta's app-publish requirement. Meta requires a
    publicly reachable URL before an app can leave development mode — this
    is that page, describing the one real thing this app does: a private,
    single-user assistant for Sheraj, never a public product."""
    return HTMLResponse("""
        <html><body style="font-family:sans-serif;max-width:700px;margin:2em auto;line-height:1.6">
        <h2>Privacy Policy — bahAI Secretary</h2>
        <p><em>Last updated 2026-07-07</em></p>
        <p>This application is a private, single-user personal assistant built for and used by
           one person (its owner). It is not a public product, is not distributed to other users,
           and does not knowingly collect data from anyone other than its owner.</p>
        <h3>What data is handled</h3>
        <ul>
          <li>Messages sent to and from the owner's WhatsApp number, calendar events, tasks, and
              reminders the owner creates through the assistant.</li>
          <li>This data is used solely to operate the assistant for its owner — scheduling,
              reminders, and answering questions the owner asks it.</li>
        </ul>
        <h3>Where it's stored</h3>
        <p>All personal data is stored in a private local database on the owner's own machine.
           It is never sold, shared for advertising, or made available to any third party except
           the service providers strictly necessary to operate the assistant:</p>
        <ul>
          <li><strong>Meta WhatsApp Business Cloud API</strong> — transports messages to and from
              WhatsApp.</li>
          <li><strong>Anthropic (Claude)</strong> — processes message text to generate the
              assistant's replies.</li>
          <li><strong>Google Workspace APIs</strong> (Calendar/Gmail/Drive/Docs/Sheets), only when
              the owner has connected them — used solely to read/write the owner's own data at the
              owner's request.</li>
        </ul>
        <h3>Data retention and deletion</h3>
        <p>Data is retained until the owner deletes it. As the sole user, the owner can delete any
           stored data directly at any time.</p>
        <h3>Contact</h3>
        <p>Questions about this policy: <a href="mailto:sherajr22@gmail.com">sherajr22@gmail.com</a></p>
        </body></html>
    """)


@app.get("/whatsapp/status")
def whatsapp_status():
    from agents import whatsapp
    return {
        "configured": whatsapp.is_configured(),
        "owner_number_set": bool(whatsapp.WHATSAPP_OWNER_NUMBER),
    }


@app.get("/whatsapp/webhook")
def whatsapp_webhook_verify(request: Request):
    """Meta's one-time handshake when you click 'Verify and Save' in the
    WhatsApp Configuration page."""
    from agents import whatsapp
    q = request.query_params
    challenge = whatsapp.verify_webhook_challenge(
        q.get("hub.mode", ""), q.get("hub.verify_token", ""), q.get("hub.challenge", ""))
    if challenge is None:
        raise HTTPException(status_code=403, detail="Verification failed")
    return PlainTextResponse(challenge)


def _handle_whatsapp_message(msg: dict):
    """Runs in a background task so the webhook can ack Meta immediately —
    Meta may retry the whole webhook delivery if it doesn't get a fast 200,
    which would otherwise risk a duplicate reply to the same message."""
    from agents import whatsapp, secretary, secretary_store
    phone = msg["from"]
    secretary_store.record_inbound_contact(phone)
    if not whatsapp.is_owner(phone):
        # Never route a non-owner message into the Secretary's chat loop —
        # that would hand a stranger who texts this number full access to
        # Sheraj's calendar/Gmail/Drive via her tool-calling loop.
        try:
            whatsapp.send_text(phone, "This is Abigail, Sheraj's personal assistant — "
                                      "I can only take instructions from him directly.")
        except Exception:
            pass
        secretary_store.add_notification(
            "whatsapp", f"Message from a non-owner number ({phone[-4:]}) — auto-replied, not processed")
        return
    try:
        result = secretary.chat(msg["text"], channel="whatsapp")
        whatsapp.send_text(phone, result["reply"])
    except Exception as e:
        secretary_store.add_notification("scheduler_error", f"WhatsApp reply failed: {type(e).__name__}")


@app.post("/whatsapp/webhook")
async def whatsapp_webhook_receive(request: Request, background_tasks: BackgroundTasks):
    from agents import whatsapp
    raw = await request.body()
    if not whatsapp.verify_signature(raw, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=403, detail="Invalid signature")
    payload = json.loads(raw)
    for msg in whatsapp.parse_webhook_messages(payload):
        background_tasks.add_task(_handle_whatsapp_message, msg)
    return {"status": "ok"}


# --- Secretary (Phase 1: chat + private memory) ---
#
# Privacy hard rule: everything below returns personal content ONLY to the
# dashboard's Secretary tab. Never log message content to log_run, job
# progress, or stdout.

class SecretaryChatRequest(BaseModel):
    message: str


@app.post("/secretary/chat")
def secretary_chat(req: SecretaryChatRequest):
    from agents import secretary
    text = (req.message or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty message")
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise HTTPException(status_code=503,
                            detail="ANTHROPIC_API_KEY is not set — add it to .env to enable Abigail")
    try:
        return secretary.chat(text, channel="dashboard")
    except Exception as e:
        # Surface the failure class, not the conversation content
        raise HTTPException(status_code=502, detail=f"Abigail is unavailable: {type(e).__name__}")


@app.get("/secretary/history")
def secretary_history(limit: int = 50):
    from agents import secretary_store
    secretary_store.init_db()
    return {"messages": secretary_store.get_recent_messages(min(limit, 200))}


@app.get("/secretary/status")
def secretary_status():
    from agents import secretary_store, whatsapp
    from agents.google_auth import is_authorised as google_authorised
    from agents.router import ANTHROPIC_MODEL
    secretary_store.init_db()
    return {
        "enabled": bool(os.getenv("ANTHROPIC_API_KEY")),
        "model": ANTHROPIC_MODEL,
        "notes": len(secretary_store.list_memory_notes()),
        "open_tasks": len(secretary_store.get_open_tasks()),
        "google_configured": bool(os.getenv("GOOGLE_CLIENT_ID")),
        "google_authorised": google_authorised(),
        "whatsapp_configured": whatsapp.is_configured(),
        "pending_reminders": len(secretary_store.get_pending_reminders()),
        "pending_approvals": len(secretary_store.get_pending_actions()),
    }


@app.get("/secretary/upcoming")
def secretary_upcoming(days: int = 14):
    """Merged, tagged calendar view + verified Bahá'í dates + pending reminders."""
    from datetime import date, timedelta
    from agents import badi_dates, secretary_store
    from agents import gcal
    secretary_store.init_db()
    events = []
    if gcal.is_authorised():
        try:
            events = gcal.list_events(days_ahead=min(days, 60))
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Calendar unreachable: {type(e).__name__}")
    today = date.today()
    badi = [{"date": e["date"].isoformat(), "name": e["name"], "kind": e["kind"],
             "work_suspended": e["work_suspended"]}
            for e in badi_dates.events_between(today, today + timedelta(days=min(days, 60)))]
    return {
        "events": events,
        "badi_events": badi,
        "reminders": secretary_store.get_pending_reminders(),
        "badi_source": badi_dates.OFFICIAL_CALENDAR_URL,
    }


@app.get("/secretary/notifications")
def secretary_notifications(after_id: int = 0):
    """Scheduler fires/failures for the dashboard (titles only — hard rule 8)."""
    from agents import secretary_store
    secretary_store.init_db()
    return {"notifications": secretary_store.get_notifications(after_id=after_id)}


@app.get("/secretary/approvals")
def secretary_approvals():
    from agents import secretary_store
    secretary_store.init_db()
    return {"pending": secretary_store.get_pending_actions()}


class ApprovalRequest(BaseModel):
    approve: bool


@app.post("/secretary/approvals/{action_id}")
def secretary_resolve_approval(action_id: int, req: ApprovalRequest):
    """Sheraj's per-event confirmation for writes to calendars she doesn't own."""
    from agents import secretary, secretary_store
    secretary_store.init_db()
    if not req.approve:
        secretary_store.resolve_pending_action(action_id, "rejected")
        return {"result": "rejected"}
    return {"result": secretary.execute_pending_action(action_id)}


# --- WhatsApp contacts (the allowlist — owner-controlled only, never
# LLM-writable; see agents/secretary_tools.py's SEND_WHATSAPP_TOOL docstring) ---

class ContactRequest(BaseModel):
    name: str
    phone: str
    allowlisted: bool = False


@app.get("/secretary/contacts")
def secretary_list_contacts():
    from agents import secretary_store
    secretary_store.init_db()
    return {"contacts": secretary_store.list_contacts()}


@app.post("/secretary/contacts")
def secretary_add_contact(req: ContactRequest):
    from agents import secretary_store
    secretary_store.init_db()
    if not req.name.strip() or not req.phone.strip():
        raise HTTPException(status_code=400, detail="Name and phone are both required")
    cid = secretary_store.add_contact(req.name.strip(), req.phone.strip(), req.allowlisted)
    return {"id": cid}


class AllowlistRequest(BaseModel):
    allowlisted: bool


@app.post("/secretary/contacts/{contact_id}/allowlist")
def secretary_set_contact_allowlisted(contact_id: int, req: AllowlistRequest):
    from agents import secretary_store
    secretary_store.set_contact_allowlisted(contact_id, req.allowlisted)
    return {"result": "ok"}


@app.delete("/secretary/contacts/{contact_id}")
def secretary_remove_contact(contact_id: int):
    from agents import secretary_store
    secretary_store.remove_contact(contact_id)
    return {"result": "ok"}


# --- Secretary: personality / custom instructions ---

class PersonalityRequest(BaseModel):
    custom_instructions: str


@app.get("/secretary/personality")
def secretary_get_personality():
    from agents import secretary_store
    secretary_store.init_db()
    return {"custom_instructions": secretary_store.get_setting("custom_instructions", "") or ""}


@app.post("/secretary/personality")
def secretary_set_personality(req: PersonalityRequest):
    from agents import secretary_store
    secretary_store.init_db()
    secretary_store.set_setting("custom_instructions", req.custom_instructions)
    return {"result": "ok"}


# --- Secretary: notes (manual view/edit of private/memory/*.md) ---

class NoteRequest(BaseModel):
    name: str
    content: str


@app.get("/secretary/notes")
def secretary_list_notes():
    from agents import secretary_store
    secretary_store.init_db()
    return {"notes": secretary_store.list_memory_notes()}


@app.post("/secretary/notes")
def secretary_save_note(req: NoteRequest):
    from agents import secretary_store
    secretary_store.init_db()
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Note name is required")
    secretary_store.overwrite_memory_note(name, req.content)
    return {"result": "ok"}


@app.delete("/secretary/notes/{name}")
def secretary_delete_note(name: str):
    from agents import secretary_store
    secretary_store.delete_memory_note(name)
    return {"result": "ok"}


# --- Secretary: tasks (manual view/edit — she still only sees open ones) ---

class TaskRequest(BaseModel):
    description: str
    due: Optional[str] = None


class TaskEditRequest(BaseModel):
    description: Optional[str] = None
    due: Optional[str] = None
    done: Optional[bool] = None


@app.get("/secretary/tasks")
def secretary_list_tasks():
    from agents import secretary_store
    secretary_store.init_db()
    return {"tasks": secretary_store.get_all_tasks()}


@app.post("/secretary/tasks")
def secretary_add_task(req: TaskRequest):
    from agents import secretary_store
    secretary_store.init_db()
    desc = req.description.strip()
    if not desc:
        raise HTTPException(status_code=400, detail="Description is required")
    tid = secretary_store.add_task(desc, due=req.due)
    return {"id": tid}


@app.patch("/secretary/tasks/{task_id}")
def secretary_edit_task(task_id: int, req: TaskEditRequest):
    from agents import secretary_store
    edits = req.model_dump(exclude_unset=True)
    if not edits:
        raise HTTPException(status_code=400, detail="No fields provided to edit")
    secretary_store.update_task(task_id, **edits)
    return {"result": "ok"}


@app.delete("/secretary/tasks/{task_id}")
def secretary_delete_task(task_id: int):
    from agents import secretary_store
    secretary_store.delete_task(task_id)
    return {"result": "ok"}


# --- Secretary: reminders (manual view/edit) ---

class ReminderRequest(BaseModel):
    message: str
    fire_at: str
    recurrence: Optional[str] = None
    wake_me: bool = False


class ReminderEditRequest(BaseModel):
    message: Optional[str] = None
    fire_at: Optional[str] = None
    recurrence: Optional[str] = None
    wake_me: Optional[bool] = None


@app.get("/secretary/reminders")
def secretary_list_reminders():
    from agents import secretary_store
    secretary_store.init_db()
    return {"reminders": secretary_store.get_all_reminders()}


@app.post("/secretary/reminders")
def secretary_add_reminder(req: ReminderRequest):
    from agents import secretary_store
    secretary_store.init_db()
    msg = req.message.strip()
    if not msg or not req.fire_at.strip():
        raise HTTPException(status_code=400, detail="Message and fire_at are both required")
    rid = secretary_store.add_reminder(msg, req.fire_at, recurrence=req.recurrence, wake_me=req.wake_me)
    return {"id": rid}


@app.patch("/secretary/reminders/{reminder_id}")
def secretary_edit_reminder(reminder_id: int, req: ReminderEditRequest):
    from agents import secretary_store
    edits = req.model_dump(exclude_unset=True)
    if not edits:
        raise HTTPException(status_code=400, detail="No fields provided to edit")
    secretary_store.update_reminder(reminder_id, **edits)
    return {"result": "ok"}


@app.delete("/secretary/reminders/{reminder_id}")
def secretary_delete_reminder(reminder_id: int):
    from agents import secretary_store
    secretary_store.delete_reminder(reminder_id)
    return {"result": "ok"}


# --- Health check ---

@app.get("/health")
def health():
    return {"status": "ok", "service": "bahAI Workforce API"}


if __name__ == "__main__":
    uvicorn.run("agents.api:app", host="0.0.0.0", port=8765, reload=True)
