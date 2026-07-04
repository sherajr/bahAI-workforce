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
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv

# Load .env before any submodule imports so all os.getenv() calls see the values
load_dotenv(dotenv_path=str(Path(__file__).parent.parent / ".env"), override=True)

from agents.librarian import retrieve
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
    consultation = {"transcript": [], "context": ""}
    if req.image_url:
        try:
            consultation = run_consultation(
                req.image_url, req.theme, req.image_prompt, req.citations or [],
                progress=progress, on_turn=on_turn, request_human_input=request_human_input,
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
                              consultation_decision=consult_decision)
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
                                 consultation_decision=consult_decision)
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
    update_product(product_id, reviewer_scores=json.dumps(review),
                   consultation=json.dumps(gen["consultation"]))

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
    _load_product_or_404(product_id)  # fail fast with 404 before starting the job
    job_id = _start_job(
        "redo-product",
        lambda progress, on_turn, ask: _redo_product(product_id, req, progress, on_turn, ask),
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
    product_id = body.get("product_id", "")
    if not product_id:
        raise HTTPException(status_code=422, detail="product_id is required")

    from agents.state import _connect
    with _connect() as conn:
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Product not found")
    product = dict(row)

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

# Rough per-product API cost: ~$0.05 image generation + ~$0.01 Grok tokens
ESTIMATED_COST_PER_PRODUCT = 0.06

@app.get("/steward/report")
def steward_report():
    """Simple profit-and-loss view across all products."""
    products = get_all_products()
    total_revenue = sum(float(p.get("revenue") or 0) for p in products)
    total_cost = round(len(products) * ESTIMATED_COST_PER_PRODUCT, 2)
    return {
        "total_products":  len(products),
        "total_revenue":   round(total_revenue, 2),
        "estimated_costs": total_cost,
        "estimated_profit": round(total_revenue - total_cost, 2),
        "cost_per_product": ESTIMATED_COST_PER_PRODUCT,
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
