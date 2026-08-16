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

import hashlib
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
from agents.ruhi_book1_source import RUHI_BOOK1_QUOTES
from agents import layout as layout_opts
from agents.state import (
    init_db, create_task, update_task_status, log_run, get_all_agent_statuses,
    create_product, update_product, get_all_products,
    add_distribution, get_deeds_summary, DISTRIBUTION_KINDS,
)
from agents.state import _connect as _state_connect

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


class JobCancelled(BaseException):
    """
    Raised inside a worker thread when Sheraj cancels its job.

    Cancellation is COOPERATIVE and has to be: a Python thread cannot be killed
    from outside without leaving half-written files, open connections and locks
    behind, which is exactly the mess "cancel and start over" is supposed to
    avoid. Every pipeline in this file already narrates itself through
    `progress(...)` between stages, so that callback is the checkpoint — the
    run stops at the next step boundary rather than mid-call. A paid API call
    already in flight is allowed to finish; nothing else begins.

    It derives from BaseException, not Exception, and that is load-bearing.
    This codebase is full of deliberate `except Exception` blocks that turn a
    failed stage into a recorded, survivable error — a batch logs the card as
    failed and moves to the next one (rule 33b's spirit, applied everywhere).
    Every one of those would swallow a cancellation and carry on to the next
    paid stage. Making it a control-flow signal rather than an error is the
    same reason asyncio.CancelledError is a BaseException; `finally` blocks
    still run, so nothing leaks.
    """


# Tasks created by the job running on THIS thread. A pipeline calls
# create_task() deep inside itself and never sees a job id, so the mapping is
# recorded here instead of being threaded through every signature — one
# thread-local per worker, set by _start_job's runner below. It exists so a
# cancelled run can leave its task row honestly marked rather than sitting in
# the database as "in progress" forever.
_CURRENT_JOB = threading.local()

_state_create_task = create_task


def create_task(directive: str, task_type: str, assigned_to: str = None) -> str:  # noqa: F811
    """state.create_task, plus a note of which job the task belongs to."""
    task_id = _state_create_task(directive, task_type, assigned_to=assigned_to)
    job_id = getattr(_CURRENT_JOB, "job_id", None)
    if job_id:
        with _JOBS_LOCK:
            job = JOBS.get(job_id)
            if job is not None:
                job.setdefault("task_ids", []).append(task_id)
    return task_id


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


def _start_job(kind: str, runner, started_by: str = "sheraj") -> str:
    """
    Register a job and run `runner(progress, on_turn, request_human_input)` in
    a worker thread. The three callbacks let a long pipeline (a) narrate short
    status text, (b) stream consultation turns live for the dashboard chat
    view, and (c) block the worker thread until Sheraj responds mid-run.

    `started_by` is who set it going — "sheraj" from a dashboard button,
    "abigail" from an approved request of hers, "colony" from a team goal. It
    exists because a job the dashboard didn't start was invisible: the Pipeline
    tab only ever polled the job id it had just created itself, so a run
    Abigail launched on approval genuinely ran while the screen showed nothing,
    and the same card got made twice (2026-08-14, real). The panel now adopts
    any running job and uses this to say honestly whose it is.
    """
    job_id = str(uuid.uuid4())[:8]
    now = datetime.utcnow().isoformat()
    with _JOBS_LOCK:
        # Evict oldest finished jobs beyond the cap
        # "cancelled" belongs here with the other terminal states, or cancelled
        # jobs would never be evicted and the store would grow without bound.
        finished = [k for k, v in JOBS.items()
                    if v["status"] in ("done", "error", "cancelled")]
        for k in sorted(finished, key=lambda k: JOBS[k]["created_at"])[: max(0, len(JOBS) - _MAX_JOBS)]:
            JOBS.pop(k, None)
        JOBS[job_id] = {
            "job_id": job_id, "kind": kind, "status": "running",
            "progress": "Starting...", "steps": [], "result": None, "error": None,
            "consultation_live": [], "pending_prompt": None,
            "started_by": started_by, "cancel_requested": False, "task_ids": [],
            "created_at": now, "updated_at": now,
        }

    def _progress(message: str):
        _raise_if_cancelled(job_id)
        _job_update(job_id, progress=message)

    def _on_turn(turn: dict):
        _raise_if_cancelled(job_id)
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
        # cancel_job wakes this same event, so a run paused for input stops
        # here instead of carrying on into another paid stage.
        _raise_if_cancelled(job_id)
        return entry.get("response", "")

    def _run():
        _CURRENT_JOB.job_id = job_id
        try:
            result = runner(_progress, _on_turn, _request_human_input)
            _job_update(job_id, status="done", progress="Complete", result=result)
        except JobCancelled:
            # Not an error: a run Sheraj stopped on purpose. It must never be
            # reported as a failure, and it must never move any agent's trust.
            _finish_cancelled(job_id)
        except Exception as e:
            _job_update(job_id, status="error", progress=f"Failed: {e}", error=str(e))
        finally:
            _CURRENT_JOB.job_id = None
            with _PENDING_LOCK:
                _PENDING_INPUT.pop(job_id, None)

    _executor.submit(_run)
    return job_id


def _raise_if_cancelled(job_id: str):
    with _JOBS_LOCK:
        job = JOBS.get(job_id)
        cancelled = bool(job and job.get("cancel_requested"))
    if cancelled:
        raise JobCancelled()


def _finish_cancelled(job_id: str):
    """
    Settle a cancelled run: mark it cancelled and close out what it left open.

    What is NOT touched, deliberately: `task_runs` rows (they are the record of
    work that really happened, and the Colony's handoff graph is derived from
    them — deleting them would falsify history), products already saved (a card
    that finished before the cancel is finished, paid for and good), and files
    already written to outputs/ (artwork that cost money, and a thread may
    still be closing the handle). Cancelling stops the work; it does not
    rewrite what the work already did.
    """
    with _JOBS_LOCK:
        job = JOBS.get(job_id)
        task_ids = list(job.get("task_ids") or []) if job else []
    for task_id in task_ids:
        try:
            with _state_connect() as conn:
                row = conn.execute("SELECT status FROM tasks WHERE id = ?",
                                   (task_id,)).fetchone()
            # A task the run had already finished stays completed — only the
            # one it was in the middle of becomes cancelled.
            if row and row["status"] != "completed":
                update_task_status(task_id, "cancelled")
        except Exception:
            pass  # an unmarkable task must never turn a clean cancel into an error
    _job_update(job_id, status="cancelled", progress="Cancelled — nothing further will run",
                pending_prompt=None)


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
            # Rule 14: only Reviewer verdicts move trust here. Scribe field
            # presence is mechanical completeness, not a quality judgment.
            passed = output.get("passed") if agent == "reviewer" else None
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
                # passed_review=None — holding a consultation is process, not judged.
                log_run(req.task_id, "consultation", "consult", req.theme[:200],
                        f"{len(consultation['transcript'])} turns completed"
                        + (f"; quote len={len(vq)}" if vq else ""))
        except Exception as e:
            consultation["transcript"] = [{"agent": "System", "role": "error",
                                            "message": f"Consultation skipped: {e}"}]
            if req.task_id:
                log_run(req.task_id, "consultation", "consult", req.theme[:200],
                        f"failed: {e}"[:400])

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
                        f"failed: {e}"[:400])

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
    # Retrieval count is mechanical — trust moves on grounding/reviewer only.
    log_run(task_id, "librarian", "retrieve", theme[:200],
            f"{len(citations)} passages retrieved")

    progress("Artist is composing the image brief (local Qwen3)...")
    image_prompt = build_image_prompt(theme, citations)
    log_run(task_id, "artist", "brief", theme[:200], image_prompt[:200])

    progress("Artist is generating the artwork (xAI)...")
    gen = generate_image(image_prompt, aspect_ratio)
    image_path = gen.get("image_url", "")
    log_run(task_id, "artist", "generate", image_prompt[:200], image_path[:200])

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
        log_run(task_id, "compositor", "render", image_path[:200], front_path[:200])
    except Exception as e:
        compositor_error = str(e)
        log_run(task_id, "compositor", "render", image_path[:200],
                f"failed: {e}"[:400])

    # Canva autofill is dead commerce weight (0/10 successful autofills ever) —
    # parked behind an off-by-default switch so it never blocks a clean run.
    canva_enabled = os.getenv("CANVA_AUTOFILL_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )
    canva = {"skipped": True, "reason": "Canva not configured", "design_url": None}
    if not canva_enabled:
        progress("Canva autofill is turned off — skipped")
        canva = {"skipped": True, "reason": "Canva autofill is turned off — skipped",
                 "design_url": None}
    else:
        progress("Sending front image to Canva (skips gracefully if not connected)...")
        try:
            from agents.canva import autofill_bookmark, CANVA_CLIENT_ID, CANVA_TEMPLATE_ID
            if CANVA_CLIENT_ID and CANVA_TEMPLATE_ID and front_path:
                canva = autofill_bookmark(front_path)
                # Steward owns publishing/packaging (same as etsy_publish).
                log_run(task_id, "steward", "canva_autofill",
                        front_path[:200], (canva.get("design_url") or "")[:200])
        except Exception as e:
            canva = {"skipped": True, "reason": str(e), "design_url": None}
            log_run(task_id, "steward", "canva_autofill",
                    front_path[:200], f"failed: {e}"[:400])

    return {"front_path": front_path, "back_path": back_path,
            "compositor_error": compositor_error, "canva": canva}


def _run_full_pipeline(req: PipelineRunRequest, progress, on_turn=None, request_human_input=None) -> dict:
    """
    The whole bookmark pipeline in one background job:
    task → Librarian → Artist brief → Artist generate → consultation/write/score
    → save product → Compositor → Canva autofill.
    """
    progress("Creating task...")
    task_id = create_task(req.theme, "design", assigned_to="pipeline")

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
    # Owner-supplied exact quote. Verified against the selected sources
    # before any image generation or paid call; locks the printed quote for
    # the whole run (revision loop cannot swap it).
    pinned_quote: str = ""
    # Owner-selected quote sources (rule 11 update, 2026-08-04). None/empty =
    # Ruhi Book 1 only — the unchanged default. Ids: "ruhi_book1",
    # "lib:<slug>" (verified library text), "web:<http(s) url>" (RISKY:
    # prints machine-unverified wording, flagged on the product).
    sources: Optional[list[str]] = None
    # Citation label used ONLY when the pinned quote resolves to the risky
    # web tier — verified local tiers always print corpus metadata and can
    # never be overridden by this field.
    pinned_citation: str = ""


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


# Elision suffix forms accepted on shortened card quotes. The book's own
# style (" . . .") is what we emit; "..." and "…" are tolerated on input.
_ELISION_SUFFIXES = (" . . .", "...", "\u2026")


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

    When (and only when) the return is shorter than the full stripped text,
    appends the book's own elision marks " . . ." so a shortened quote is
    visibly honest about the cut.
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
    trimmed = text[:cut].strip()
    if trimmed == text:
        return text
    return trimmed + " . . ."


# Cache for agents/ruhi_book1_manifest.json — the SHA256 manifest frozen by
# scripts/verify_ruhi_book1.py after every corpus entry was machine-verified
# character-exact against the official Ruhi Book 1 PDF (edition 4.1.2.PE).
_RUHI_MANIFEST_CACHE: Optional[dict] = None


def _load_ruhi_manifest() -> dict:
    global _RUHI_MANIFEST_CACHE
    if _RUHI_MANIFEST_CACHE is not None:
        return _RUHI_MANIFEST_CACHE
    path = Path(__file__).parent / "ruhi_book1_manifest.json"
    if not path.exists():
        raise RuntimeError(
            "The Ruhi Book 1 verification manifest is missing. Run "
            "python scripts/verify_ruhi_book1.py --pdf <official pdf> --write-manifest "
            "before rendering quote cards."
        )
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"The Ruhi Book 1 manifest could not be read: {exc}") from exc
    quotes = manifest.get("quotes")
    if not isinstance(quotes, list) or len(quotes) != len(RUHI_BOOK1_QUOTES):
        raise RuntimeError(
            "The Ruhi Book 1 manifest does not match the current corpus size. Re-run "
            "python scripts/verify_ruhi_book1.py --pdf <official pdf> --write-manifest."
        )
    _RUHI_MANIFEST_CACHE = manifest
    return manifest


def _assert_ruhi_verbatim(quote: str) -> None:
    """
    Fail loudly unless the about-to-print card quote is either:
      (a) an exact character match of a manifest-verified Ruhi Book 1 corpus
          entry's text.strip() (covers full entries, including those that
          natively end with ". . ."), or
      (b) a sentence-boundary prefix of such an entry, carrying honest
          elision marks (" . . .", also tolerating "..." or "…" as the
          suffix) — i.e. after stripping the suffix, the remainder equals
          text[:e].strip() for some sentence-end e from _SENTENCE_END_RE.
    Mid-sentence cuts and unmarked character prefixes are rejected. This
    catches stale ChromaDB text and unverified corpus edits BEFORE a card
    can render wrong words — same discipline as _sanitize_claims.
    """
    quote = (quote or "").strip()
    if not quote:
        raise RuntimeError("Empty card quote reached the verbatim gate — refusing to render.")
    manifest_quotes = _load_ruhi_manifest()["quotes"]

    # Detect a trailing elision suffix (book style first, then common variants).
    elided_body = None
    for suffix in _ELISION_SUFFIXES:
        if quote.endswith(suffix):
            elided_body = quote[: -len(suffix)].strip()
            break

    for idx, entry in enumerate(RUHI_BOOK1_QUOTES):
        text = str(entry["text"])
        text_stripped = text.strip()
        if manifest_quotes[idx].get("sha256") != hashlib.sha256(text.encode("utf-8")).hexdigest():
            continue
        # (a) exact full-entry match — must run before (b) so a corpus entry
        # that natively ends with ". . ." is accepted as-is, not re-parsed
        # as an elision of a shorter body.
        if quote == text_stripped:
            return
        # (b) sentence-boundary prefix + honest elision marks
        if elided_body:
            ends = [m.end() for m in _SENTENCE_END_RE.finditer(text_stripped)]
            for e in ends:
                if text_stripped[:e].strip() == elided_body:
                    return
    raise RuntimeError(
        "Card quote is not verbatim from the verified Ruhi Book 1 corpus, so the render "
        f"was stopped. Offending quote starts: {quote[:80]!r}. Shortened quotes must end "
        "at a sentence boundary and carry \" . . .\" elision marks. Likely causes: a mid-"
        "sentence cut, a missing elision mark, a stale ChromaDB index (re-run "
        "scripts/ingest_ruhi_book1.py), or an unverified corpus edit (re-run "
        "python scripts/verify_ruhi_book1.py --pdf <official pdf> --write-manifest)."
    )


def _quote_lenient_key(s: str) -> str:
    """
    Lenient key for matching an owner-supplied pinned quote against the
    corpus: lowercase; curly quotes/apostrophes → straight; "…" → ". . .";
    strip leading/trailing quotation marks; collapse whitespace runs.
    """
    s = (s or "")
    s = (s.replace("\u2018", "'").replace("\u2019", "'")
          .replace("\u201c", '"').replace("\u201d", '"')
          .replace("\u2026", ". . ."))
    s = s.strip().lower().strip("\"'")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _resolve_pinned_quote(supplied: str) -> dict:
    """
    Resolve an owner-supplied exact quote against the Ruhi Book 1 corpus.

    Returns {"quote": <exact text to print>, "source": <entry source>,
    "sha256": <hash of the full entry text>}. The resolved quote is always
    the corpus entry's exact characters (or an exact sentence-boundary
    prefix plus " . . ."). Raises RuntimeError before any paid work if the
    supply cannot be verified.
    """
    supplied = (supplied or "").strip()
    err = (
        "The supplied quote could not be verified against the Ruhi Book 1 corpus "
        "(67 verified passages). Card quotes must match an authorized passage exactly "
        "(ellipses allowed only to shorten at a sentence boundary). Nothing was generated."
    )
    if not supplied:
        raise RuntimeError(err)

    full_key = _quote_lenient_key(supplied)

    # Elision may be present on a shortened supply — strip from the body used
    # for prefix matching, but keep full_key (above) for whole-entry match so
    # corpus entries that natively end with ". . ." still resolve via (i).
    body = supplied
    for suffix in _ELISION_SUFFIXES:
        if body.endswith(suffix):
            body = body[: -len(suffix)].strip()
            break
    body_key = _quote_lenient_key(body)

    manifest_quotes = _load_ruhi_manifest()["quotes"]
    for idx, entry in enumerate(RUHI_BOOK1_QUOTES):
        text = str(entry["text"])
        text_stripped = text.strip()
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if manifest_quotes[idx].get("sha256") != sha:
            continue
        source = str(entry.get("source") or "").strip()
        entry_key = _quote_lenient_key(text_stripped)

        # (i) full entry (lenient) → print the EXACT corpus text
        if full_key == entry_key or body_key == entry_key:
            return {"quote": text_stripped, "source": source, "sha256": sha}

        # (ii) sentence-boundary prefix → EXACT prefix + " . . ."
        ends = [m.end() for m in _SENTENCE_END_RE.finditer(text_stripped)]
        for e in ends:
            prefix = text_stripped[:e].strip()
            if not prefix or prefix == text_stripped:
                continue
            prefix_key = _quote_lenient_key(prefix)
            if body_key == prefix_key or full_key == prefix_key:
                return {"quote": prefix + " . . .", "source": source, "sha256": sha}

    raise RuntimeError(err)


# ── Card quote sources (owner expansion of rule 11, 2026-08-04) ──────────────
# Quote cards DEFAULT to Ruhi Book 1 only — exactly the pre-expansion
# behavior — but Sheraj can now explicitly widen a single run's quote pool:
#   "ruhi_book1" — the manifest-verified Ruhi corpus (character-exact tier)
#   "lib:<slug>" — one ingested library text (verbatim-chunk tier: the printed
#                  text must be a boundary-honest verbatim span of an indexed
#                  chunk — the same index the bookmark product already prints
#                  from, so this adds no new trust in any LLM)
#   "web:<url>"  — RISKY and explicitly opt-in: the quote prints as supplied/
#                  fetched and is flagged quote_verified=false everywhere; it
#                  is never grounded and never silently trusted.
# Selection is per-run and explicit. Nothing ever falls back to a wider pool
# than what was selected — an empty result within the selection is still a
# hard stop.

RUHI_SOURCE_ID = "ruhi_book1"


def _parse_card_sources(sources: Optional[list]) -> tuple[bool, list[str], list[str]]:
    """
    Normalize a request's quote-source selection into
    (use_ruhi, lib_slugs, web_urls). None/empty → Ruhi only (the default and
    the pre-expansion behavior). Raises ValueError on unknown ids so
    endpoints can 422 before any job starts.
    """
    if not sources:
        return True, [], []
    from agents.librarian import list_library_sources
    known_slugs = {s["slug"] for s in list_library_sources()}
    use_ruhi, lib_slugs, web_urls = False, [], []
    for raw in sources:
        sid = str(raw or "").strip()
        if not sid:
            continue
        if sid == RUHI_SOURCE_ID:
            use_ruhi = True
        elif sid.startswith("lib:"):
            slug = sid[4:].strip()
            if slug not in known_slugs:
                raise ValueError(
                    f"Unknown library text '{slug}' — available: {sorted(known_slugs)}")
            if slug not in lib_slugs:
                lib_slugs.append(slug)
        elif sid.startswith("web:"):
            url = sid[4:].strip()
            if not url.lower().startswith(("http://", "https://")):
                raise ValueError(f"A web source must be an http(s) URL, got: {url[:80]!r}")
            if url not in web_urls:
                web_urls.append(url)
        else:
            raise ValueError(f"Unknown quote source id: {sid[:80]!r}")
    if not (use_ruhi or lib_slugs or web_urls):
        return True, [], []
    return use_ruhi, lib_slugs, web_urls


def _origin_label(origin: str) -> str:
    """Human name for a quote origin id (transcript/dashboard text only)."""
    if origin == RUHI_SOURCE_ID:
        return "Ruhi Book 1 (Reflections on the Life of the Spirit)"
    if origin.startswith("lib:"):
        from agents.librarian import list_library_sources
        slug = origin[4:]
        for s in list_library_sources():
            if s["slug"] == slug:
                return s["name"]
        return slug
    if origin.startswith("web:"):
        return re.sub(r"^https?://(www\.)?", "", origin[4:]).split("/")[0]
    return origin


def _card_retrieve(query: str, use_ruhi: bool, lib_slugs: list[str],
                   n_results: int = 3) -> list[dict]:
    """
    Citations for a card run, drawn ONLY from the selected verified local
    sources (web never contributes citations — it has no verified index).
    Each passage is tagged with `origin` ("ruhi_book1" or "lib:<slug>") so
    every downstream verbatim gate knows which discipline applies. Callers
    must treat an empty result exactly like the old empty-Ruhi result: a
    hard stop, never a silent fallback to a wider pool (rule 11).
    """
    out = []
    if use_ruhi:
        for p in (retrieve_ruhi_book1(query, n_results=n_results) or []):
            p = dict(p)
            p["origin"] = RUHI_SOURCE_ID
            out.append(p)
    if lib_slugs:
        for p in (retrieve(query, n_results=n_results, slugs=lib_slugs) or []):
            p = dict(p)
            p["origin"] = f"lib:{p.get('slug') or ''}"
            out.append(p)
    out.sort(key=lambda p: p.get("score") or 0.0, reverse=True)
    return out[:n_results]


def _find_verbatim_span(supplied_body: str, chunk_text: str) -> Optional[str]:
    """
    Locate supplied_body inside chunk_text as a contiguous run of whole
    words, comparing per-word under the same lenient normalization as
    _quote_lenient_key, and return the CHUNK's exact characters for that
    span — never the supplied characters (same "print the corpus text"
    discipline as _resolve_pinned_quote). None if not present.
    """
    sup = [t for t in (_quote_lenient_key(w) for w in supplied_body.split()) if t]
    if not sup:
        return None
    toks = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"\S+", chunk_text)]
    ch = [_quote_lenient_key(t[0]) for t in toks]
    m = len(sup)
    for i in range(len(ch) - m + 1):
        if ch[i:i + m] == sup:
            return chunk_text[toks[i][1]:toks[i + m - 1][2]]
    return None


def _span_boundary_ok(chunk_text: str, span: str, elided: bool) -> bool:
    """
    Boundary honesty for a library-chunk span (mirrors the Ruhi tier's
    whole-entry / sentence-prefix rule): the span must START at a sentence
    start and END with sentence punctuation — elision marks excuse stopping
    before the chunk's end, never a mid-sentence cut. A span at the chunk's
    own start counts only if the chunk itself opens sentence-clean: the
    SentenceChunker's 100-char overlap can open a chunk mid-sentence, and a
    fragment ("immensity of the heavens, until...") must never print as if
    it were a complete passage (caught in offline testing, 2026-08-04).
    """
    start = chunk_text.find(span)
    if start < 0:
        return False
    prefix = chunk_text[:start].rstrip()
    if prefix:
        tail = prefix.rstrip("\"'”’)")
        if not tail or tail[-1] not in ".!?":
            return False
    else:
        # Lowercase first letter = almost certainly an overlap fragment. The
        # rare genuinely-lowercase opening loses a candidate (its complete
        # neighbor chunk usually resolves instead) — safe direction.
        first_alpha = next((ch for ch in span if ch.isalpha()), "")
        if first_alpha and first_alpha.islower():
            return False
    body = span.rstrip().rstrip("\"'”’)")
    if not body or body[-1] not in ".!?":
        # A chunk-final span may legitimately end with the source's own
        # elision (entries like "… appear great . . ."), which strips to
        # nothing above — accept only exact chunk-tail in that case.
        return elided is False and chunk_text.rstrip().endswith(span.rstrip()) and body != ""
    return True


def _lib_excerpt(chunk_text: str) -> Optional[str]:
    """
    Card-safe excerpt of a library chunk: drop a mid-sentence opening
    fragment (overlap chunking can open mid-sentence), then trim to card
    length at sentence boundaries (_trim_card_quote). None when the chunk
    holds no sentence-clean text to print.
    """
    text = (chunk_text or "").strip()
    first_alpha = next((ch for ch in text if ch.isalpha()), "")
    if first_alpha and first_alpha.islower():
        m = _SENTENCE_END_RE.search(text)
        if not m:
            return None
        text = text[m.end():].strip()
        first_alpha = next((ch for ch in text if ch.isalpha()), "")
        if not text or (first_alpha and first_alpha.islower()):
            return None
    # Drop a leading "N. " list marker (Paris Talks enumerates points) — the
    # card shouldn't print "1. To show compassion...". Verification still
    # holds: the remaining span starts right after the marker's ".", which
    # _span_boundary_ok reads as a sentence end.
    text = re.sub(r"^\d{1,3}\.\s+", "", text)
    return _trim_card_quote(text) if text else None


def _resolve_library_quote(supplied: str, lib_slugs: list[str]) -> Optional[dict]:
    """
    Tier-2 resolver: verify an owner-supplied quote as a boundary-honest
    verbatim span of a chunk in the SELECTED library texts (the same
    ChromaDB index the bookmark product already prints from). Returns
    {"quote", "source", "origin": "lib:<slug>", "link"} — the quote is the
    chunk's exact characters (+ " . . ." when the supply elided early) — or
    None when no selected text contains it. The verbatim check itself is
    pure string matching against stored chunk text; the embedding search
    only decides which chunks get checked.
    """
    supplied = (supplied or "").strip()
    if not supplied:
        return None
    body, elided = supplied, False
    for suffix in _ELISION_SUFFIXES:
        if body.endswith(suffix):
            body, elided = body[: -len(suffix)].strip(), True
            break
    try:
        candidates = retrieve(body[:400], n_results=8, slugs=lib_slugs) or []
    except Exception:
        return None
    for p in candidates:
        chunk = str(p.get("text") or "")
        span = _find_verbatim_span(body, chunk)
        if not span or not _span_boundary_ok(chunk, span, elided):
            continue
        return {"quote": span + (" . . ." if elided else ""),
                "source": str(p.get("source") or "").strip(),
                "origin": f"lib:{p.get('slug') or ''}",
                "link": p.get("link", "")}
    return None


def _resolve_pinned_quote_multi(supplied: str, use_ruhi: bool, lib_slugs: list[str],
                                web_urls: list[str], citation_hint: str = "") -> dict:
    """
    Tiered pinned-quote resolution across the selected sources. Verified
    local tiers always run first (Ruhi character-exact, then library
    verbatim-chunk); the web tier — only when explicitly selected — accepts
    the text as supplied, flagged verified=False (RISKY: the wording is the
    owner's responsibility, machine-unverified by definition). Raises
    RuntimeError naming the selected sources when nothing matches and web
    was not selected. Returns {"quote","source","origin","verified"}.
    """
    supplied = (supplied or "").strip()
    if use_ruhi:
        try:
            r = _resolve_pinned_quote(supplied)
            return {"quote": r["quote"], "source": r["source"],
                    "origin": RUHI_SOURCE_ID, "verified": True}
        except RuntimeError:
            if not (lib_slugs or web_urls):
                raise
    if lib_slugs:
        r = _resolve_library_quote(supplied, lib_slugs)
        if r:
            return {"quote": r["quote"], "source": r["source"],
                    "origin": r["origin"], "verified": True}
    if web_urls:
        if not supplied:
            raise RuntimeError("Empty card quote cannot be pinned.")
        return {"quote": supplied,
                "source": (citation_hint or "").strip() or _origin_label(f"web:{web_urls[0]}"),
                "origin": f"web:{web_urls[0]}", "verified": False}
    selected = ([RUHI_SOURCE_ID] if use_ruhi else []) + [f"lib:{s}" for s in lib_slugs]
    raise RuntimeError(
        "The quote could not be verified against the selected sources "
        f"({', '.join(selected) or 'none'}). Card quotes must match a passage in a "
        "selected verified source exactly (ellipses allowed only to shorten at a "
        "sentence boundary) — or enable the risky web source to print unverified "
        "wording deliberately. Nothing was generated."
    )


def _assert_excerpt_of(quote: str, chunk_text: str) -> None:
    """
    Render gate for a library-chunk pick (the counterpart of
    _assert_ruhi_verbatim): the about-to-print quote must be a verbatim,
    boundary-honest span of the retrieved chunk — sentence-clean start,
    sentence-punctuation end, elision marks covering any early stop —
    exactly what _lib_excerpt produces. Fails loudly, same contract as the
    Ruhi gate.
    """
    quote = (quote or "").strip()
    chunk = (chunk_text or "").strip()
    body, elided = quote, False
    for suffix in _ELISION_SUFFIXES:
        if body.endswith(suffix):
            body, elided = body[: -len(suffix)].strip(), True
            break
    if quote and body:
        span = _find_verbatim_span(body, chunk)
        if (span is not None and _span_boundary_ok(chunk, span, elided)
                and span + (" . . ." if elided else "") == quote):
            return
    raise RuntimeError(
        "Card quote is not a verbatim excerpt of the retrieved library passage, so the "
        f"render was stopped. Offending quote starts: {quote[:80]!r}."
    )


def _assert_card_quote_verbatim(quote: str, origin: str) -> None:
    """
    Origin-aware render/save gate. Ruhi → the manifest gate; library → the
    quote must independently re-verify as a verbatim span of its selected
    text; web → no verified source of truth exists (that is the point of the
    tier) — the quote_verified=false flag, not this gate, is the safeguard.
    """
    if origin.startswith("lib:"):
        r = _resolve_library_quote(quote, [origin[4:]])
        if not r or r["quote"] != (quote or "").strip():
            raise RuntimeError(
                "Card quote no longer re-verifies verbatim against the selected library "
                f"text ({_origin_label(origin)}) — render stopped. Starts: {quote[:80]!r}. "
                "Likely cause: a stale ChromaDB index (re-run scripts/ingest_texts.py)."
            )
    elif not origin.startswith("web:"):
        _assert_ruhi_verbatim(quote)


# ── Risky web tier: fetch a page, extract candidate passages ────────────────

_WEB_FETCH_MAX_BYTES = 2_000_000


def _fetch_web_page_text(url: str) -> tuple[str, list[str]]:
    """
    (page title, text blocks) from a web page, stdlib-only parsing. This
    feeds the RISKY quote tier: extraction is best-effort (sites differ,
    JS-rendered pages come back empty) and wording is NEVER verified — the
    caller flags everything from here as unverified.
    """
    import requests as _rq
    from html.parser import HTMLParser

    resp = _rq.get(url, timeout=20, stream=True,
                   headers={"User-Agent": "Mozilla/5.0 (bahAI-workforce quote finder)"})
    resp.raise_for_status()
    raw = resp.raw.read(_WEB_FETCH_MAX_BYTES + 1, decode_content=True)
    if len(raw) > _WEB_FETCH_MAX_BYTES:
        raise RuntimeError("page is larger than 2 MB — link a specific passage page instead")
    html = raw.decode(resp.encoding or "utf-8", errors="replace")

    _BLOCK_TAGS = ("p", "div", "blockquote", "li", "section", "article",
                   "h1", "h2", "h3", "br", "td")
    _SKIP_TAGS = ("script", "style", "noscript", "nav", "header", "footer")

    class _Extract(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.blocks: list[str] = []
            self._buf: list[str] = []
            self._skip = 0
            self._in_title = False
            self.title = ""

        def handle_starttag(self, tag, attrs):
            if tag in _SKIP_TAGS:
                self._skip += 1
            elif tag == "title":
                self._in_title = True
            elif tag in _BLOCK_TAGS:
                self._flush()

        def handle_endtag(self, tag):
            if tag in _SKIP_TAGS:
                self._skip = max(0, self._skip - 1)
            elif tag == "title":
                self._in_title = False
            elif tag in _BLOCK_TAGS:
                self._flush()

        def handle_data(self, data):
            if self._in_title:
                self.title += data
            elif not self._skip:
                self._buf.append(data)

        def _flush(self):
            text = " ".join("".join(self._buf).split())
            self._buf = []
            if len(text) >= 60:
                self.blocks.append(text)

    parser = _Extract()
    parser.feed(html)
    parser._flush()
    return " ".join(parser.title.split()), parser.blocks[:200]


# Obvious site chrome/boilerplate — never quote material. Best-effort filter
# for the risky tier (the owner still reviews everything it returns).
_WEB_BOILERPLATE_RE = re.compile(
    r"copyright|all rights reserved|terms of use|privacy|cookie|"
    r"download|available in the following formats|javascript|sign in|newsletter|"
    r"new version of|old version|can be accessed at|contact us|site map",
    re.IGNORECASE)

# "¶1:" / "§2." / "“3:" paragraph markers common on reference-library pages
# (reference.bahai.org renders its pilcrows as curly quotes) — noise on a
# card. Web-tier text is unverified by definition, so this cosmetic strip
# needs no verbatim gate.
_WEB_PARA_MARKER_RE = re.compile(r"^[¶§“”\"']?\s*\d+\s*[:.]\s*")


def _rank_web_blocks(topic: str, blocks: list[str], top_n: int) -> list[tuple[float, str]]:
    """
    Rank page blocks against the topic — local Ollama embeddings (the same
    model the index uses; free) with a plain token-overlap fallback. This is
    the risky tier: ranking is best-effort, the owner reviews every result.
    """
    import math as _math
    blocks = blocks[:120]
    try:
        from agents.librarian import _embed
        tvec = _embed(topic)

        def _cos(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            na = _math.sqrt(sum(x * x for x in a))
            nb = _math.sqrt(sum(y * y for y in b))
            return dot / (na * nb) if na and nb else 0.0

        scored = [(round(_cos(tvec, _embed(b[:1000])), 4), b) for b in blocks]
    except Exception:
        twords = set(re.findall(r"[a-z']+", topic.lower()))
        scored = [
            (round(len(twords & set(re.findall(r"[a-z']+", b.lower()))) / max(1, len(twords)), 4), b)
            for b in blocks
        ]
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored[:top_n]


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


def _cap_at_word(text: str, max_len: int) -> str:
    """Hard-cap a string at max_len, preferring a word boundary so a mid-word
    cut never reaches the render."""
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    cut = text[:max_len].rstrip()
    sp = cut.rfind(" ")
    if sp > max_len // 3:
        cut = cut[:sp].rstrip()
    return cut


def _card_reflection(quote: str, task_id: str, language: str | None) -> dict:
    """
    Ask the local Scribe for a short reflection question + gentle action
    prompt inspired by the card's quote. Returns a dict suitable for
    card_compositor.render_quote_card's reflection= kwarg:
      {"question", "action", optional "native": {"question", "action"}}
    On ANY failure returns {} so the compositor's code defaults take over.
    Mechanical step — passed_review=None (rule 14). Never writes the share
    line or disclaimers (rule 8 — those stay code-owned in the compositor).
    """
    from agents.router import call_llm

    prompt = (
        "You write the back of a small giveaway reflection card.\n\n"
        f'Quote:\n"{(quote or "")[:400]}"\n\n'
        'Return ONLY this JSON:\n'
        '{"question": "...", "action": "..."}\n\n'
        "Rules:\n"
        "- question: short personal reflection (<=90 chars), first person, ends with ?\n"
        "- action: gently-phrased self-chosen action prompt (<=60 chars), ends with :\n"
        "- Inspired by the quote's spirit; calm, constructive, never commanding or salesy\n"
        "- No emojis, no scripture paraphrase, no quotation marks inside the strings\n"
    )
    raw = ""
    try:
        raw = call_llm(
            "scribe",
            [{"role": "user", "content": prompt}],
            agent="scribe",
            json_mode=True,
            max_tokens=200,
            temperature=0.6,
        ).strip()
    except Exception:
        log_run(task_id, "scribe", "card_reflection", (quote or "")[:200],
                "fallback: defaults", passed_review=None)
        return {}

    data = None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        m = re.search(r"\{[\s\S]*?\}", raw or "")
        if m:
            try:
                data = json.loads(m.group(0))
            except (json.JSONDecodeError, TypeError, ValueError):
                data = None

    if not isinstance(data, dict):
        log_run(task_id, "scribe", "card_reflection", (quote or "")[:200],
                "fallback: defaults", passed_review=None)
        return {}

    question = _cap_at_word(str(data.get("question") or ""), 110)
    action = _cap_at_word(str(data.get("action") or ""), 70)
    if not question and not action:
        log_run(task_id, "scribe", "card_reflection", (quote or "")[:200],
                "fallback: defaults", passed_review=None)
        return {}

    result: dict = {}
    if question:
        result["question"] = question
    if action:
        result["action"] = action

    # Optional native pair for translated runs — never fail the card over this.
    if language and result:
        try:
            from agents.translator import translate_quote
            native: dict = {}
            if question:
                nq = (translate_quote(question, language).get("text") or "").strip()
                if nq:
                    native["question"] = _cap_at_word(nq, 110)
            if action:
                na = (translate_quote(action, language).get("text") or "").strip()
                if na:
                    native["action"] = _cap_at_word(na, 70)
            if native:
                result["native"] = native
        except Exception:
            pass

    log_run(task_id, "scribe", "card_reflection", (quote or "")[:200],
            (result.get("question") or "ok")[:200], passed_review=None)
    return result


def _reflection_from_card_copy(card_copy: dict) -> Optional[dict]:
    """Rebuild the reflection= dict from fields stored on a saved card.
    Returns None when nothing was stored (compositor defaults apply)."""
    if not card_copy:
        return None
    q = (card_copy.get("reflection_question") or "").strip()
    a = (card_copy.get("reflection_action") or "").strip()
    native = card_copy.get("reflection_native")
    if not q and not a and not native:
        return None
    out: dict = {}
    if q:
        out["question"] = q
    if a:
        out["action"] = a
    if isinstance(native, dict):
        nq = (native.get("question") or "").strip()
        na = (native.get("action") or "").strip()
        if nq or na:
            out["native"] = {k: v for k, v in (("question", nq), ("action", na)) if v}
    return out or None


def _variant_faces_from_rendered(rendered: dict) -> dict:
    """Store-shape for per-language card pairs: {code: {front, back}}."""
    faces = {}
    for code, pair in (rendered.get("variants") or {}).items():
        if not isinstance(pair, dict):
            continue
        faces[code] = {
            "front": pair.get("front_path") or pair.get("front") or "",
            "back": pair.get("back_path") or pair.get("back") or "",
        }
    return faces


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
    from agents.consultation import (
        run_consultation, run_card_revision_consultation,
        CARD_PIN_LABEL_LIBRARY, CARD_PIN_LABEL_WEB,
        CARD_SOURCE_SCOPE_EXPANDED, CARD_QUOTE_SOURCING_NOTE_EXPANDED,
    )
    from agents.reviewer import score_quote_card
    from agents.translator import translate_quote, LANGUAGES

    lang_name = LANGUAGES[req.language]["name"] if req.language else None
    # Redo-everything guidance steers retrieval/artwork only — req.theme
    # itself (used for title, stored theme, and every requote step below)
    # stays clean so a redo never bakes "NEW DIRECTION from Sheraj" text
    # into permanent storage the way appending it to req.theme would.
    retrieval_query = f"{req.theme}\n\n{req.guidance}" if req.guidance.strip() else req.theme

    progress("Creating task...")
    task_id = create_task(req.theme, "card_design", assigned_to="pipeline")

    # Owner-selected quote sources (rule 11 update, 2026-08-04): default is
    # Ruhi Book 1 only — the pre-expansion behavior, unchanged. Endpoints
    # validate ids before the job starts; this re-parse just converts.
    try:
        use_ruhi, lib_slugs, web_urls = _parse_card_sources(req.sources)
    except ValueError as e:
        raise RuntimeError(str(e))
    expanded = bool(lib_slugs or web_urls)

    # Owner-pinned quote: verify against the SELECTED sources BEFORE any
    # retrieval, image generation, or paid call. Fold the verified text into
    # the retrieval query (so citations + artist brief track the quote) but
    # never into req.theme (stored title stays clean).
    pinned = None
    pinned_turn = None
    if (req.pinned_quote or "").strip():
        pinned = _resolve_pinned_quote_multi(req.pinned_quote, use_ruhi, lib_slugs,
                                             web_urls, citation_hint=req.pinned_citation)
        retrieval_query = f"{retrieval_query}\n\n{pinned['quote']}"
        pinned_turn = {
            "agent": "System",
            "role": "owner-pinned quote",
            "message": (
                ('Sheraj pinned this exact quote (verified verbatim against '
                 f'{_origin_label(pinned["origin"])}): "{pinned["quote"]}" — '
                 'the team may shape artwork and framing only.')
                if pinned["verified"] else
                ('Sheraj pinned this quote from a WEB source '
                 f'({_origin_label(pinned["origin"])}) — its wording is NOT machine-'
                 'verified and the card is flagged accordingly: '
                 f'"{pinned["quote"]}" — the team may shape artwork and framing only.')
            ),
        }
        if on_turn:
            on_turn(pinned_turn)

    # Retrieval is restricted to the SELECTED verified sources (default: the
    # Ruhi Book 1 index alone — never the full 7-text corpus unless Sheraj
    # explicitly selected texts from it). Unlike the bookmark path (which
    # tolerates empty retrieval by letting the consultation's Librarian draw
    # on "well-known writings" generally), an empty result here must fail the
    # job outright: falling through to open-ended sourcing would silently
    # break the restriction the moment retrieval hiccups. The one exception:
    # a web-pinned run with NO local sources selected has no index to search
    # — the quote is already fixed, so the consultation proceeds citation-free.
    progress("Librarian is searching the selected sources for passages...")
    citations = _card_retrieve(retrieval_query, use_ruhi, lib_slugs, n_results=3)
    log_run(task_id, "librarian", "retrieve", retrieval_query[:200],
            f"{len(citations)} passages retrieved")
    if not citations:
        if not (pinned and not use_ruhi and not lib_slugs):
            raise RuntimeError(
                "No passage found in the selected source index(es) for this theme, or "
                "the index isn't built yet — run scripts/ingest_ruhi_book1.py (Ruhi) or "
                "scripts/ingest_texts.py (library). Quote cards only ever draw from the "
                "sources selected for the run, so this can't fall back to a wider pool."
            )

    progress("Artist is composing the card image brief (local Qwen3)...")
    image_prompt = build_card_image_prompt(retrieval_query, citations)
    log_run(task_id, "artist", "card_brief", req.theme[:200], image_prompt[:200])

    progress("Artist is generating the artwork (xAI)...")
    gen = generate_image(image_prompt, "2:3")
    image_path = gen.get("image_url", "")
    log_run(task_id, "artist", "generate", image_prompt[:200], image_path[:200])

    # ── Consultation (card framing) ──────────────────────────────────────────
    def _preview_front(quote: str, transcript: list) -> dict:
        """LLM-free render of BOTH faces for the pause (owner ask 2026-07-16 —
        the back now carries the artwork, so Sheraj steers from front AND
        back). Translation doesn't exist yet at this point in the pipeline,
        so the preview is English-only, and the reflection question isn't
        generated yet — the back previews with the code-default wording.
        When a quote is pinned, the pause preview shows the pinned text
        (what will actually print), not the consultation's interim pick."""
        print_quote = pinned["quote"] if pinned else quote
        preview = render_quote_card(
            image_path, print_quote,
            _librarian_source_from(transcript, citations)
            if not pinned else pinned["source"],
            reflection=None,
        )
        return {"front": _web_image_path(preview["front_path"]),
                "back": _web_image_path(preview["back_path"])}

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
            fixed_quote=(pinned["quote"] if pinned else ""),
            # Code-owned provenance wording (rule 11 update, 2026-08-04):
            # empty strings preserve the original Ruhi wording exactly.
            fixed_quote_label=(
                "" if not pinned or pinned["origin"] == RUHI_SOURCE_ID else
                CARD_PIN_LABEL_WEB.format(name=_origin_label(pinned["origin"]))
                if pinned["origin"].startswith("web:") else
                CARD_PIN_LABEL_LIBRARY.format(name=_origin_label(pinned["origin"]))
            ),
            source_scope_override=(CARD_SOURCE_SCOPE_EXPANDED if expanded else ""),
        )
        vq = (consultation.get("verified_quote") or "").strip()
        # passed_review=None — consultation process, not a quality verdict.
        log_run(task_id, "consultation", "consult", req.theme[:200],
                f"{len(consultation['transcript'])} turns completed"
                + (f"; quote len={len(vq)}" if vq else ""))
    except Exception as e:
        consultation["transcript"] = [{"agent": "System", "role": "error",
                                       "message": f"Consultation skipped: {e}"}]
        log_run(task_id, "consultation", "consult", req.theme[:200],
                f"failed: {e}"[:400])

    # Surface the pin on the stored transcript (on_turn already emitted it
    # live; prepend so the dashboard shows it before the consultation turns).
    if pinned_turn:
        consultation["transcript"] = [pinned_turn] + list(consultation.get("transcript") or [])

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
                    f"failed: {e}"[:400])

    # ── The quote (and its honesty flags) ────────────────────────────────────
    # When Sheraj pinned an exact quote, use it as-is (already corpus-verified)
    # and skip the consultation-matching / trim path entirely. Otherwise the
    # consultation's own wording is NEVER printed as-is: whatever quote (or
    # fragment of one) it proposed is used only to pick WHICH of the retrieved
    # Ruhi Book 1 passages the team meant — the printed text and citation are
    # always that passage's own verbatim (trimmed) text and true source
    # metadata. This is deterministic by construction (every passage in
    # `citations` came from retrieve_ruhi_book1, so this can never surface a
    # quote from outside the book), and it closes a real failure mode caught
    # live: a Librarian round can blend two different retrieved passages into
    # one composite line and credit the whole thing to just one of them —
    # round 2 once reversed round 1's own correct "ORIGINAL COMPOSITION"
    # verdict to "GROUNDED" for exactly this kind of blend.
    if pinned:
        quote = pinned["quote"]
        citation_src = pinned["source"]
        quote_origin = pinned["origin"]
        _assert_card_quote_verbatim(quote, quote_origin)  # no-op only for the flagged web tier
        quote_grounded = pinned["verified"]  # web tier is NEVER grounded
    else:
        proposed = (consultation.get("verified_quote") or "").strip()
        if proposed and citations:
            matched = _best_matching_citation(proposed, citations)
        elif citations:
            matched = citations[0]
        else:
            raise RuntimeError(
                "No verified quote available: consultation produced none and no passages "
                "were retrieved. Build the selected source index(es) or retry."
            )
        # Take the matched passage's card-safe excerpt; a library chunk that
        # is only a mid-sentence overlap fragment yields none — fall through
        # to the next-best citation rather than print a fragment.
        ordered = [matched] + [c for c in citations if c is not matched]
        quote = None
        for cand in ordered:
            cand_origin = cand.get("origin") or RUHI_SOURCE_ID
            q = (_trim_card_quote(cand["text"]) if cand_origin == RUHI_SOURCE_ID
                 else _lib_excerpt(cand["text"]))
            if q:
                quote, matched = q, cand
                break
        if quote is None:
            raise RuntimeError(
                "Every retrieved passage was a mid-sentence fragment — retry, or "
                "rephrase the theme so retrieval surfaces complete passages."
            )
        quote_origin = matched.get("origin") or RUHI_SOURCE_ID
        # Character-exact against the passage's own verified index, or the
        # job fails loudly — the Ruhi manifest gate for Ruhi picks, the
        # excerpt-of-chunk gate for selected library picks.
        if quote_origin == RUHI_SOURCE_ID:
            _assert_ruhi_verbatim(quote)
        else:
            _assert_excerpt_of(quote, matched["text"])
        quote_grounded = True  # always true here — a verbatim excerpt of an indexed passage
        citation_src = str(matched.get("source") or "").strip()

    # Quote lock: a pinned supply OR any pause guidance from Sheraj means the
    # revision loop may not swap the quote (production bug: mid-run "use the
    # full quote please" was later overridden by a requote action).
    quote_locked = bool(pinned) or bool((consultation.get("human_note") or "").strip())

    # Reflection face copy (back of the card) — LLM-written like listing copy;
    # share line + disclaimers stay code-owned inside the compositor (rule 8).
    # Empty dict on failure → compositor defaults. Before translation so a
    # translated run still gets native reflection via translate_quote inside.
    progress("Scribe is drafting the reflection question for the back...")
    reflection = _card_reflection(quote, task_id, req.language)

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
        # Translator script check is a deterministic judged outcome (rule 14).
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
        # reflection is closed-over; new quote → new reflection before re-render.
        r = render_quote_card(image_path, q, citation_src, translation=tr,
                              reflection=reflection)
        log_run(task_id, "compositor", "render_card", image_path[:200],
                r["front_path"][:200])
        return r

    def _score(q: str, tr: Optional[dict], rendered: dict, prev=None, note=None) -> dict:
        progress("Reviewer is scoring the card (seeing the rendered front face)...")
        return score_quote_card(
            req.theme, q, citation_src, quote_grounded,
            front_image_path=rendered["front_path"], translation=tr,
            consultation_transcript=consultation.get("transcript"),
            consultation_decision=brief or None,
            previous_review=prev, revision_note=note,
            quote_pinned=bool(pinned),
            back_image_path=rendered["back_path"],
            sourcing_note=(CARD_QUOTE_SOURCING_NOTE_EXPANDED if expanded else None),
            quote_web_unverified=str(quote_origin).startswith("web:"),
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
            history=revision_history, quote_pinned=quote_locked,
            back_image_path=rendered["back_path"],
            sourcing_note=(CARD_QUOTE_SOURCING_NOTE_EXPANDED if expanded else ""),
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
            "origin": quote_origin,
            "translation": translation, "reflection": reflection,
            "rendered": rendered, "review": review,
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
        # Code-enforce the quote lock regardless of what the consult returned
        # (consultation also coerces requote→ship when quote_pinned, but this
        # is the hard gate that makes the production bug unrecoverable).
        if action == "requote" and quote_locked:
            editing_log.append({
                "agent": "System", "role": "editing stopped",
                "message": "The team proposed swapping the quote, but Sheraj's choice "
                           "locks it for this run — keeping the quote.",
            })
            break
        attempt += 1

        if action == "requote":
            progress(f"Re-picking the quote per the Reviewer: {guidance[:100] or req.theme}...")
            try:
                passages = _card_retrieve(guidance.strip() or req.theme,
                                          use_ruhi, lib_slugs, n_results=3)
            except Exception:
                passages = []
            if passages:
                latest_citations = passages  # keep the team's view of "other candidates" current
            # First passage whose card-safe excerpt is usable AND different —
            # library fragments (no sentence-clean text) are skipped, never printed.
            pick = new_q = None
            for p in passages:
                p_origin = p.get("origin") or RUHI_SOURCE_ID
                q = (_trim_card_quote(p["text"]) if p_origin == RUHI_SOURCE_ID
                     else _lib_excerpt(p["text"]))
                if q and q != quote:
                    pick, new_q = p, q
                    break
            if not pick:
                editing_log.append({"agent": "System", "role": "editing stopped",
                                    "message": "No different passage found for the Reviewer's "
                                               "steer — keeping the best card."})
                break
            quote = new_q
            quote_origin = pick.get("origin") or RUHI_SOURCE_ID
            # Same gates as the initial pick — requotes get no exemption.
            if quote_origin == RUHI_SOURCE_ID:
                _assert_ruhi_verbatim(quote)
            else:
                _assert_excerpt_of(quote, pick["text"])
            quote_grounded = True
            citation_src = str(pick.get("source") or "").strip() or citation_src
            # passed_review=None — finding a different passage is mechanical;
            # whether it HELPED is judged by the re-score that follows.
            log_run(task_id, "librarian", f"requote_{attempt}", guidance[:200], quote[:200])
            try:
                translation = _translate(quote)
            except Exception as e:
                # Script-check / translate failure is a judged outcome (rule 14).
                log_run(task_id, "translator", "translate", quote[:200],
                        f"failed: {e}"[:400], passed_review=False)
                editing_log.append({"agent": "System", "role": "editing stopped",
                                    "message": f"Translation of the re-picked quote failed ({e}) — "
                                               "keeping the best card."})
                break
            # New quote → new reflection question/action (same path as initial).
            reflection = _card_reflection(quote, task_id, req.language)
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
                        f"failed: {e}"[:400])
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
                    "origin": quote_origin,
                    "translation": translation, "reflection": reflection,
                    "rendered": rendered, "review": new_review,
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
    # Final gate: a pinned quote must still be byte-identical at save time.
    if pinned:
        best_hash = hashlib.sha256(best["quote"].encode("utf-8")).hexdigest()
        pinned_hash = hashlib.sha256(pinned["quote"].encode("utf-8")).hexdigest()
        if best_hash != pinned_hash:
            raise RuntimeError(
                "Pinned quote was altered before save — refusing to write the product. "
                f"Pinned starts: {pinned['quote'][:80]!r}; "
                f"best starts: {best['quote'][:80]!r}."
            )
    refl = best.get("reflection") or {}
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
        "quote_pinned": bool(pinned),
        # Provenance (rule 11 update, 2026-08-04). quote_verified is False
        # ONLY for the risky web tier — the dashboard surfaces that plainly.
        "quote_verified": not str(best.get("origin") or "").startswith("web:"),
        "quote_provenance": best.get("origin") or RUHI_SOURCE_ID,
        "quote_sources": req.sources or [RUHI_SOURCE_ID],
        # Reflection face copy actually used (empty string when compositor
        # defaults filled in). Share line stays code-owned, never stored.
        "reflection_question": refl.get("question") or "",
        "reflection_action": refl.get("action") or "",
        "reflection_native": refl.get("native") or None,
        # Per-language card pair paths from the compositor (empty when EN-only).
        "variant_faces": _variant_faces_from_rendered(best["rendered"]),
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
        "quote_verified": not str(best.get("origin") or "").startswith("web:"),
        "quote_provenance": best.get("origin") or RUHI_SOURCE_ID,
        "citation": best["citation"],
        "translation": tr,
        "image_prompt": best["image_prompt"],
        "image_path": best["image_path"],
        "image_web": _web_image_path(best["image_path"]),
        "front_image_path": best["rendered"]["front_path"],
        "front_image_web": _web_image_path(best["rendered"]["front_path"]),
        "back_image_path": best["rendered"]["back_path"],
        "back_image_web": _web_image_path(best["rendered"]["back_path"]),
        # Per-language card pairs, passed explicitly so the dashboard's
        # results panel never has to fish them out of a stale product cache
        # (real bug: a fresh run's Spanish pair rendered fine but the panel
        # couldn't find the just-created product — 2026-07-16).
        "variant_faces": _variant_faces_from_rendered(best["rendered"]),
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
    try:
        use_ruhi, lib_slugs, web_urls = _parse_card_sources(req.sources)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    # A web-only run has no verified index for the team to pick a quote from
    # — the quote must be fixed up front (the finder can fetch candidates).
    if web_urls and not (use_ruhi or lib_slugs) and not (req.pinned_quote or "").strip():
        raise HTTPException(
            status_code=422,
            detail="A web-only source run needs an exact quote up front — use "
                   '"Find quotes" to fetch candidates from the page, or add a '
                   "verified source to the selection.")
    job_id = _start_job(
        "card-pipeline",
        lambda progress, on_turn, ask: _run_card_pipeline(req, progress, on_turn, ask),
    )
    return {"job_id": job_id, "status": "running"}


class CardBatchRequest(BaseModel):
    theme: str
    language: Optional[str] = None   # shared by every card in the batch
    target_score: float = 9.0
    max_attempts: int = 3
    # One owner-supplied exact quote per card. Each is resolved against the
    # SELECTED sources in the ENDPOINT — a bad paste is a 422 before the job
    # even starts, so nothing paid ever runs on an unverifiable quote (web
    # tier excepted by design: it is explicitly unverified and flagged).
    quotes: list[str] = []
    # Owner-selected quote sources shared by the whole batch (rule 11 update,
    # 2026-08-04). None/empty = Ruhi Book 1 only, the unchanged default.
    sources: Optional[list[str]] = None
    # Optional per-quote citation labels, aligned by index with `quotes`.
    # Used ONLY for quotes that resolve to the risky web tier (verified
    # tiers always print corpus metadata). The dashboard fills these from
    # the finder's results; missing/empty entries fall back to the web host.
    quote_citations: Optional[list[str]] = None


# Spend guard: every card in a batch is a full paid pipeline run (xAI image
# + Grok consultation/review), so a single request can't queue unbounded work.
_CARD_BATCH_MAX = 19


def _run_card_batch(req: CardBatchRequest, progress, on_turn=None) -> dict:
    """
    Run several quote cards as ONE background job, strictly one card at a
    time (same sequential GPU/API discipline as everything else). Batch runs
    are hands-free by design (owner ask, 2026-07-17): request_human_input is
    never passed down, so run_consultation's round-2 pause simply doesn't
    happen and the job never enters 'waiting_for_input'.

    req.quotes arrive already resolved to exact corpus text by the endpoint;
    _run_card_pipeline re-verifies each via its own pinned-quote gate (the
    authoritative check stays where it always was). After the up-front
    verification, one card's mid-run failure (e.g. a transient xAI error) is
    recorded on its item and announced as a turn — it never kills the rest
    of the batch.
    """
    total = len(req.quotes)
    items = []
    completed = 0
    for i, quote in enumerate(req.quotes):
        label = f"Card {i + 1}/{total}"
        if on_turn:
            on_turn({"agent": "System", "role": "batch",
                     "message": f'{label} begins: "{quote[:90]}{"..." if len(quote) > 90 else ""}" '
                                "— hands-free run, the team proceeds without the mid-run check-in."})
        citations_list = req.quote_citations or []
        item_req = CardPipelineRequest(
            theme=req.theme, language=req.language,
            target_score=req.target_score, max_attempts=req.max_attempts,
            pinned_quote=quote, sources=req.sources,
            pinned_citation=(citations_list[i] if i < len(citations_list) else ""),
        )
        try:
            r = _run_card_pipeline(
                item_req,
                lambda m, label=label: progress(f"{label}: {m}"),
                on_turn=on_turn, request_human_input=None,
            )
            completed += 1
            items.append({
                "index": i + 1, "status": "done",
                "quote": r["quote"], "citation": r.get("citation"),
                "product_id": r["product_id"], "task_id": r["task_id"],
                "front_image_web": r["front_image_web"],
                "back_image_web": r["back_image_web"],
                "variant_faces": r.get("variant_faces") or {},
                "overall": r["review"].get("overall"), "badge": r["badge"],
                "attempts": r["attempts"], "target_reached": r["target_reached"],
            })
        except Exception as e:
            items.append({"index": i + 1, "status": "error",
                          "quote": quote, "error": str(e)})
            if on_turn:
                on_turn({"agent": "System", "role": "error",
                         "message": f"{label} failed and was skipped ({e}) — moving on to the next card."})
    from agents.translator import LANGUAGES
    return {
        "batch": True,
        "product_type": "quote_card_batch",
        "theme": req.theme,
        "language": req.language,
        "language_name": LANGUAGES[req.language]["name"] if req.language else None,
        "total": total,
        "completed": completed,
        "failed": total - completed,
        "items": items,
    }


@app.post("/pipeline/run-card-batch")
def pipeline_run_card_batch(req: CardBatchRequest, started_by: str = "sheraj"):
    """
    Queue several quote cards in one hands-free job (the consultation's
    mid-run pause is skipped for every card — batch runs never wait on
    Sheraj). Every quote is verified against the SELECTED sources HERE,
    before the job starts: an unverifiable paste is a 422 and nothing is
    generated. (A quote that resolves to the explicitly-selected risky web
    tier is accepted by design — unverified and flagged, per rule 11's
    2026-08-04 owner update.) Poll the returned job_id like any other
    pipeline job; the result carries batch=true and a per-card items list.
    """
    from agents.translator import LANGUAGES
    if not req.theme.strip():
        raise HTTPException(status_code=422, detail="theme is required")
    if req.language and req.language not in LANGUAGES:
        raise HTTPException(status_code=422,
                            detail=f"Unknown language '{req.language}' — offered: {sorted(LANGUAGES)}")
    try:
        use_ruhi, lib_slugs, web_urls = _parse_card_sources(req.sources)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    # Keep citations aligned while dropping empty quote boxes.
    raw_citations = req.quote_citations or []
    pairs = [(q.strip(), (raw_citations[i] if i < len(raw_citations) else "").strip())
             for i, q in enumerate(req.quotes or []) if (q or "").strip()]
    if not pairs:
        raise HTTPException(status_code=422, detail="at least one quote is required")
    if len(pairs) > _CARD_BATCH_MAX:
        raise HTTPException(
            status_code=422,
            detail=f"A batch is capped at {_CARD_BATCH_MAX} cards per run (each card is a "
                   "full paid pipeline run) — split the rest into a second batch.")
    resolved, resolved_citations = [], []
    for i, (q, hint) in enumerate(pairs):
        try:
            r = _resolve_pinned_quote_multi(q, use_ruhi, lib_slugs, web_urls,
                                            citation_hint=hint)
        except RuntimeError as e:
            raise HTTPException(
                status_code=422,
                detail=f'Quote {i + 1} of {len(pairs)} (starts: "{q[:60]}") '
                       f"could not be verified: {e}")
        resolved.append(r["quote"])
        # Web-tier quotes carry their citation label through the job; verified
        # tiers re-derive their citation from corpus metadata in the pipeline.
        resolved_citations.append(r["source"] if not r["verified"] else "")
    req.quotes = resolved
    req.quote_citations = resolved_citations
    job_id = _start_job(
        "card-batch",
        lambda progress, on_turn, ask: _run_card_batch(req, progress, on_turn),
        started_by=started_by,
    )
    return {"job_id": job_id, "status": "running", "total": len(resolved)}


def _fit_or_shorten(quote: str, source: str) -> Optional[tuple[str, bool]]:
    """
    (fitting quote, was_shortened) for a finder suggestion: the full text if
    it renders at the card's readable minimum (rule 29), else the longest
    sentence-boundary prefix + " . . ." that does; None when nothing fits.
    """
    from agents.card_compositor import quote_fits_card
    if quote_fits_card(quote, source):
        return quote, False
    best_prefix = None
    for m in _SENTENCE_END_RE.finditer(quote):
        prefix = quote[: m.end()].strip()
        if prefix and prefix != quote:
            cand = prefix + " . . ."
            if quote_fits_card(cand, source):
                best_prefix = cand  # keep the longest fitting prefix
    return (best_prefix, True) if best_prefix else None


@app.get("/quote-sources")
def quote_card_sources():
    """
    The quote-source options the card form can offer (rule 11 update,
    2026-08-04): Ruhi Book 1 (always first, the default) plus every ingested
    library text. The risky web option is a client-side row (it needs a URL
    typed in), so it isn't listed here.
    """
    from agents.librarian import list_library_sources
    return {"sources": (
        [{"id": RUHI_SOURCE_ID,
          "name": "Ruhi Book 1 — Reflections on the Life of the Spirit",
          "kind": "verified", "default": True}]
        + [{"id": f"lib:{s['slug']}", "name": s["name"], "kind": "verified",
            "default": False} for s in list_library_sources()]
    )}


@app.get("/ruhi-quotes")
def suggest_ruhi_quotes(topic: str = "", count: int = 4, sources: str = ""):
    """
    Librarian quote suggestions for the card form: semantic search of the
    SELECTED sources (default: the Ruhi Book 1 corpus alone — the rule 11
    default; `sources` is a comma-separated id list, same ids as the
    pipeline). Local sources are free (ChromaDB + a local Ollama embedding
    — no paid LLM call, so no spend metering); a "web:<url>" source
    additionally fetches that page (RISKY tier: wording NOT verified,
    items flagged verified=false).

    Every local-tier quote is canonicalized through the same resolvers the
    batch endpoint verifies with, so pasting a suggestion straight into a
    run verifies by construction; anything too long for the card's readable
    minimum (rule 29) is shortened at a sentence boundary or skipped.
    Verified local results always rank before risky web results.
    """
    topic = (topic or "").strip()
    if not topic:
        raise HTTPException(status_code=422, detail="Give the Librarian a topic to search for.")
    if not 1 <= count <= _CARD_BATCH_MAX:
        raise HTTPException(
            status_code=422,
            detail=f"count must be between 1 and {_CARD_BATCH_MAX} (the batch cap).")
    try:
        use_ruhi, lib_slugs, web_urls = _parse_card_sources(
            [s for s in (sources or "").split(",") if s.strip()])
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    items, skipped_too_long, seen = [], 0, set()
    web_note = None

    if use_ruhi or lib_slugs:
        # Retrieve a few extra so a skipped-as-unfittable passage can be
        # backfilled and the owner still gets the number asked for.
        try:
            passages = _card_retrieve(topic, use_ruhi, lib_slugs,
                                      n_results=min(count + 5, 24))
        except Exception as e:
            raise HTTPException(
                status_code=503,
                detail=f"The Librarian's local search isn't reachable right now ({e}) — "
                       "is Ollama running?")
        if not passages and not web_urls:
            raise HTTPException(
                status_code=503,
                detail="The selected source index(es) haven't been built on this machine — "
                       "run scripts/ingest_ruhi_book1.py (Ruhi) or scripts/ingest_texts.py "
                       "(library). Quote suggestions never fall back to unselected sources.")
        for p in passages:
            if len(items) >= count:
                break
            origin = p.get("origin") or RUHI_SOURCE_ID
            if origin == RUHI_SOURCE_ID:
                try:
                    resolved = _resolve_pinned_quote(p.get("text") or "")
                except RuntimeError:
                    # Index text no longer matches the corpus/manifest (stale
                    # index) — never suggest text the batch gate would reject.
                    continue
                quote, source = resolved["quote"], resolved["source"]
            else:
                chunk = (p.get("text") or "").strip()
                quote = _lib_excerpt(chunk)  # sentence-clean, card-length excerpt
                if not quote:
                    continue  # mid-sentence overlap fragment — never suggest it
                source = str(p.get("source") or "").strip()
                try:
                    _assert_excerpt_of(quote, chunk)  # same gate the pipeline applies
                except RuntimeError:
                    continue
            fit = _fit_or_shorten(quote, source)
            if fit is None:
                skipped_too_long += 1
                continue
            quote, shortened = fit
            if shortened:
                # Canonicalize/re-verify the shortened form through the same
                # gates the batch endpoint uses — what we hand back must be
                # guaranteed to verify verbatim on submit.
                if origin == RUHI_SOURCE_ID:
                    quote = _resolve_pinned_quote(quote)["quote"]
                else:
                    try:
                        _assert_excerpt_of(quote, chunk)
                    except RuntimeError:
                        continue
            key = _quote_lenient_key(quote)
            if key in seen:
                continue  # Ruhi and the library share passages — suggest each once
            seen.add(key)
            items.append({"quote": quote, "source": source, "score": p.get("score"),
                          "shortened": shortened, "origin": origin, "verified": True})

    # RISKY web tier — only when explicitly selected; always after verified
    # results, every item flagged verified=false.
    for url in web_urls:
        if len(items) >= count:
            break
        try:
            title, blocks = _fetch_web_page_text(url)
        except Exception as e:
            web_note = f"Could not fetch {url}: {e}"
            continue
        blocks = [b for b in blocks if not _WEB_BOILERPLATE_RE.search(b)]
        if not blocks:
            web_note = (f"No readable passages found at {url} — the page may need "
                        "JavaScript; try a page that shows the text directly.")
            continue
        # Citation label: the page title up to a separator, else the host.
        src_label = re.split(r"\s+[|–—-]\s+", title)[0].strip() if title else ""
        src_label = src_label or _origin_label(f"web:{url}")
        for score, block in _rank_web_blocks(topic, blocks, top_n=count * 2):
            if len(items) >= count:
                break
            block = _WEB_PARA_MARKER_RE.sub("", block).strip()
            if len(block) < 40:
                continue  # a bare address line ("O SON OF SPIRIT!") isn't a quote
            fit = _fit_or_shorten(_trim_card_quote(block), src_label)
            if fit is None:
                skipped_too_long += 1
                continue
            quote, shortened = fit
            if len(quote) < 40:
                continue  # trimmed down to just an address/heading — skip
            key = _quote_lenient_key(quote)
            if key in seen:
                continue
            seen.add(key)
            items.append({"quote": quote, "source": src_label, "score": score,
                          "shortened": shortened, "origin": f"web:{url}",
                          "verified": False})

    if not items:
        raise HTTPException(
            status_code=422,
            detail=f'The Librarian searched the selected sources for "{topic}" but found '
                   "nothing that fits on a card at readable size"
                   + (f". {web_note}" if web_note else " — try a different topic."))
    return {"topic": topic, "requested": count, "items": items,
            "skipped_too_long": skipped_too_long, "web_note": web_note}


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


def _print_pairs_for(product: dict, include_variants: bool = True) -> list[tuple]:
    """
    The (front, back) face pairs a product contributes to a print sheet: its
    main (English) pair plus, for translated quote cards, each per-language
    variant pair (card_copy.variant_faces). The sheet builder cycles pairs
    across the grid, so [English, Spanish] fills a sheet half-and-half
    (owner ask, 2026-07-16). Variant pairs whose files are missing on disk
    are skipped silently — the main pair's existing 422/404 checks stay the
    hard gate.
    """
    pairs = [(product.get("front_image"), product.get("back_image"))]
    if include_variants and (product.get("product_type") == "quote_card"):
        try:
            copy = json.loads(product.get("listing_copy") or "{}")
        except (json.JSONDecodeError, TypeError):
            copy = {}
        for pair in (copy.get("variant_faces") or {}).values():
            vf, vb = (pair or {}).get("front"), (pair or {}).get("back")
            if vf and vb and Path(vf).exists() and Path(vb).exists():
                pairs.append((vf, vb))
    return pairs


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
    task_id = create_task(theme, "design", assigned_to="pipeline")

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

    # Honor the sources the card was CREATED with (rule 11 update 2026-08-04)
    # — never widen. Web sources are excluded here by construction: there is
    # no verified index to re-pick from, so a web-only card can't requote.
    try:
        use_ruhi, lib_slugs, _web_urls = _parse_card_sources(card_copy.get("quote_sources"))
    except ValueError:
        use_ruhi, lib_slugs = True, []  # stored ids no longer resolvable → safe default

    query = req.guidance.strip() or theme
    passages = _card_retrieve(query, use_ruhi, lib_slugs, n_results=3)
    if not passages:
        raise HTTPException(
            status_code=422,
            detail="No matching passage found in this card's source index(es) for that "
                   "guidance. Try different wording, or rebuild the index "
                   "(scripts/ingest_ruhi_book1.py / scripts/ingest_texts.py). A card whose "
                   "only source was a web page has no verified pool to re-pick from — "
                   "run a new card instead.",
        )
    # First passage with a usable card-safe excerpt, preferring a different
    # quote — library overlap fragments (no sentence-clean text) are skipped.
    candidates = []
    for p in passages:
        p_origin = p.get("origin") or RUHI_SOURCE_ID
        q = (_trim_card_quote(p["text"]) if p_origin == RUHI_SOURCE_ID
             else _lib_excerpt(p["text"]))
        if q:
            candidates.append((p, q))
    if not candidates:
        raise HTTPException(
            status_code=422,
            detail="Every matching passage was a mid-sentence fragment — try different wording.")
    pick, new_quote = next(((p, q) for p, q in candidates if q != old_quote), candidates[0])
    pick_origin = pick.get("origin") or RUHI_SOURCE_ID
    # Same gates as the pipeline — manual requotes get no exemption.
    if pick_origin == RUHI_SOURCE_ID:
        _assert_ruhi_verbatim(new_quote)
    else:
        _assert_excerpt_of(new_quote, pick["text"])
    citation_src = str(pick.get("source") or "").strip()

    translation = None
    if language:
        try:
            translation = translate_quote(new_quote, language)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Translation failed: {e}")

    # New quote → new reflection face (same helper as the pipeline).
    reflection = _card_reflection(new_quote, product_id, language)

    try:
        rendered = render_quote_card(
            image_path, new_quote, citation_src,
            translation=translation, reflection=reflection,
        )
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
        back_image_path=rendered["back_path"],
    )

    card_copy["quote"] = new_quote
    card_copy["quote_grounded"] = True
    card_copy["quote_verified"] = True          # re-picks always come from a verified index
    card_copy["quote_provenance"] = pick_origin
    card_copy["citation"] = citation_src
    if language:
        card_copy["language_name"] = translation.get("name")
        card_copy["translation_text"] = translation.get("text")
        card_copy["translation_disclaimer_native"] = translation.get("disclaimer_native")
        card_copy["translation_disclaimer_en"] = translation.get("disclaimer_en")
    card_copy["reflection_question"] = (reflection or {}).get("question") or ""
    card_copy["reflection_action"] = (reflection or {}).get("action") or ""
    card_copy["reflection_native"] = (reflection or {}).get("native") or None
    card_copy["variant_faces"] = _variant_faces_from_rendered(rendered)

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
    # Quote unchanged → reuse stored reflection fields as-is.
    reflection = _reflection_from_card_copy(card_copy)

    new_prompt = f"{old_image_prompt}\n\nIMPORTANT new direction from Sheraj: {req.guidance}"
    try:
        gen = generate_image(new_prompt, "2:3")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Image generation error: {e}")
    new_image_path = gen.get("image_url", "")

    try:
        rendered = render_quote_card(
            new_image_path, quote, citation_src,
            translation=translation, reflection=reflection,
        )
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
        back_image_path=rendered["back_path"],
    )

    card_copy["variant_faces"] = _variant_faces_from_rendered(rendered)
    update_product(
        product_id, image_url=new_image_path, image_prompt=new_prompt,
        listing_copy=json.dumps(card_copy),
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

    # If Sheraj had pinned the quote on this card, a "redo everything" keeps it
    # pinned — a redo changes the artwork/framing, never his chosen words. The
    # stored quote is the exact pinned text (quote_pinned was set at save time),
    # so re-supplying it re-verifies it verbatim and re-locks it for the redo.
    pinned_quote = card_copy.get("quote", "") if card_copy.get("quote_pinned") else ""

    progress("Redoing the whole card from scratch...")
    card_req = CardPipelineRequest(theme=theme, language=language, guidance=req.guidance,
                                   pinned_quote=pinned_quote)
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
        log_run(task_id, "steward", "canva_autofill",
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
    Create a DRAFT Etsy listing from a saved product (title, description, tags)
    and upload the front bookmark image. Price is policy-set from
    etsy.BOOKMARK_PRICE (env ETSY_BOOKMARK_PRICE) — never parsed from LLM
    prose (rule 13). Nothing goes live — drafts are reviewed and activated by
    Sheraj inside Etsy.
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
    log_run(product.get("task_id") or product_id, "steward", "etsy_publish",
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


@app.get("/colony")
def colony_overview():
    """The whole graph in one call: agents, teams, goals and derived handoffs."""
    from agents import colony
    return colony.colony_snapshot()


@app.get("/colony/agents/{agent}")
def colony_agent(agent: str):
    from agents import colony
    _colony_agent_or_404(agent)
    detail = colony.agent_detail(agent)
    detail["messages"] = colony.get_agent_messages(agent) if detail["chattable"] else []
    return detail


@app.post("/colony/agents/{agent}/chat")
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


@app.delete("/colony/agents/{agent}/chat")
def colony_clear_chat(agent: str):
    from agents import colony
    _colony_agent_or_404(agent)
    colony.clear_agent_messages(agent)
    return {"result": "ok"}


@app.get("/colony/agents/{agent}/settings")
def colony_get_agent_settings(agent: str):
    from agents import colony
    _colony_agent_or_404(agent)
    colony.init_colony_db()
    return colony.get_agent_settings(agent)


@app.post("/colony/agents/{agent}/settings")
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


@app.get("/colony/models")
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


@app.get("/colony/handoffs")
def colony_handoffs(days: int = 30):
    from agents import colony
    return {"days": days, "edges": colony.handoff_edges(days=days),
            "recent_runs": colony.recent_runs(limit=40)}


# --- Colony: the confirm-before-acting queue --------------------------------

@app.get("/colony/actions")
def colony_list_actions(status: str = "pending"):
    from agents import colony
    colony.init_colony_db()
    return {"actions": colony.list_actions(status)}


@app.post("/colony/actions/{action_id}")
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

@app.get("/colony/goals")
def colony_list_goals(team: Optional[str] = None, status: Optional[str] = None):
    from agents import colony
    colony.init_colony_db()
    goals = colony.list_goals(team=team, status=status)
    return {"goals": [g | {"progress": colony.goal_progress(g)} for g in goals]}


@app.post("/colony/goals")
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


@app.patch("/colony/goals/{goal_id}")
def colony_update_goal(goal_id: int, req: GoalEditRequest):
    from agents import colony
    colony.init_colony_db()
    if not colony.get_goal(goal_id):
        raise HTTPException(status_code=404, detail=f"No goal #{goal_id}")
    goal = colony.update_goal(goal_id, **req.model_dump(exclude_none=True))
    return goal | {"progress": colony.goal_progress(goal)}


@app.delete("/colony/goals/{goal_id}")
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
        run_req = PipelineRunRequest(theme=theme)
        job_id = _start_job(
            "full-pipeline",
            lambda progress, on_turn, ask: _run_full_pipeline(run_req, progress, on_turn, ask),
            started_by=started_by)
        return {"result": "running", "job_id": job_id, "kind": kind, "theme": theme}

    if kind == "quote_card":
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


@app.post("/colony/goals/{goal_id}/launch")
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
#
# Safety model lives in agents/wallet.py, not here: owner-only allowlist, hard
# per-transaction and daily caps, USDC-only for the agent, mainnet opt-in, and
# on-chain verification of the token contract before every transfer.

class AllowlistRequest(BaseModel):
    label: str
    address: str
    note: str = ""


class TreasuryRequest(BaseModel):
    label: str
    address: str


class WalletSendRequest(BaseModel):
    to: str
    amount: str
    chain: Optional[str] = None
    note: str = ""


@app.get("/wallet/status")
def wallet_status():
    from agents import wallet
    return wallet.status()


@app.get("/wallet/balances")
def wallet_balances(address: Optional[str] = None):
    """Live holdings. An unreachable chain reports as unreachable, never as zero."""
    from agents import wallet
    try:
        out = wallet.balances(address)
    except wallet.WalletError as e:
        raise HTTPException(status_code=502, detail=str(e))
    out["treasury"] = [
        t | {"balances": _safe_balances(t["address"])} for t in wallet.list_treasury()
    ]
    return out


def _safe_balances(address: str) -> dict | None:
    from agents import wallet
    try:
        return wallet.balances(address)
    except Exception:
        return None


@app.post("/wallet/create")
def wallet_create():
    """
    Create the hot wallet. Returns the private key ONCE so Sheraj can back it
    up offline; it is never returned again and never logged.
    """
    from agents import wallet
    try:
        return wallet.create_wallet()
    except wallet.WalletError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/wallet/history")
def wallet_history(limit: int = 50):
    from agents import wallet
    wallet.init_wallet_db()
    return {"transactions": wallet.history(limit)}


@app.post("/wallet/allowlist")
def wallet_add_allowlist(req: AllowlistRequest):
    """
    Approve an address to receive funds. OWNER-ONLY by construction: no tool in
    colony_tools writes this table, mirroring the WhatsApp contacts allowlist
    (rule 28). It is what stops a prompt-injected Nora paying an attacker.
    """
    from agents import wallet
    wallet.init_wallet_db()
    try:
        return wallet.add_allowlist(req.label, req.address, req.note)
    except wallet.WalletError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.delete("/wallet/allowlist/{entry_id}")
def wallet_remove_allowlist(entry_id: int):
    from agents import wallet
    wallet.remove_allowlist(entry_id)
    return {"result": "ok"}


@app.post("/wallet/treasury")
def wallet_add_treasury(req: TreasuryRequest):
    """A watch-only address held elsewhere. Nora can read it and never spend it."""
    from agents import wallet
    wallet.init_wallet_db()
    try:
        return wallet.add_treasury(req.label, req.address)
    except wallet.WalletError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.delete("/wallet/treasury/{entry_id}")
def wallet_remove_treasury(entry_id: int):
    from agents import wallet
    wallet.remove_treasury(entry_id)
    return {"result": "ok"}


@app.post("/wallet/send")
def wallet_send(req: WalletSendRequest):
    """
    Sheraj's OWN send, straight from the dashboard with no LLM anywhere in the
    path — the safest way to move money here.

    It still requires the destination to be on the allowlist, because that also
    catches a mistyped address, and a wrong address is unrecoverable. It does
    NOT apply Nora's caps: those exist to bound an agent, not its owner.
    """
    from agents import wallet
    wallet.init_wallet_db()
    try:
        return wallet.send_usdc(
            req.chain or wallet.DEFAULT_CHAIN, req.to, req.amount,
            initiated_by="sheraj", note=req.note, bypass_limits=True)
    except wallet.WalletError as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- Colony: team consultation ----------------------------------------------

@app.post("/colony/teams/{team_id}/consult")
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
    # Dedupe on Meta's message id (ids only in private DB — rule 15).
    # Retries of the same delivery must not re-run a full Secretary turn.
    message_id = (msg.get("message_id") or "").strip()
    if message_id and secretary_store.seen_wa_message(message_id):
        return
    phone = msg["from"]
    secretary_store.record_inbound_contact(phone)
    # Three tiers (rule 27 + owner decision 2026-07-12):
    #   owner        → full secretary.chat (tools + memory)
    #   allowlisted  → tool-less guest_chat (own thread, no personal context)
    #   everyone else → canned reply; never reach any chat loop
    # Strangers must never reach secretary.chat — that would hand whoever
    # texts this number full access to Sheraj's calendar/Gmail/Drive via
    # her tool-calling loop. Allowlisted contacts get guest_chat only
    # (structurally tool-less; owner decision 2026-07-12).
    if whatsapp.is_owner(phone):
        try:
            result = secretary.chat(msg["text"], channel="whatsapp")
            whatsapp.send_text(phone, result["reply"])
        except Exception as e:
            secretary_store.add_notification("scheduler_error", f"WhatsApp reply failed: {type(e).__name__}")
    else:
        contact = secretary_store.get_contact_by_phone(phone)
        if contact and contact.get("allowlisted"):
            try:
                result = secretary.guest_chat(contact, msg["text"])
                whatsapp.send_text(phone, result["reply"])
            except Exception as e:
                secretary_store.add_notification("scheduler_error",
                    f"WhatsApp guest reply to {contact['name']} failed: {type(e).__name__}")
        else:
            try:
                whatsapp.send_text(phone, "This is Abigail, Sheraj's personal assistant — "
                                          "I can only take instructions from him directly.")
            except Exception:
                pass
            secretary_store.add_notification(
                "whatsapp", f"Message from a non-owner number ({phone[-4:]}) — auto-replied, not processed")


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


@app.get("/video/providers")
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


@app.get("/video/defaults")
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


@app.get("/video/projects")
def video_list_projects():
    from agents import video_store
    return {"projects": video_store.list_projects()}


@app.post("/video/projects")
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


@app.get("/video/projects/{project_id}")
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


@app.patch("/video/projects/{project_id}")
def video_update_project(project_id: str, req: VideoProjectUpdate):
    """Edit source, creative direction, or the continuity bible (including locks)."""
    from agents import video_store
    _video_project_or_404(project_id)
    edits = req.model_dump(exclude_unset=True)
    if not edits:
        raise HTTPException(status_code=400, detail="No fields provided to edit")
    video_store.update_project(project_id, **edits)
    return video_store.get_project(project_id)


@app.delete("/video/projects/{project_id}")
def video_delete_project(project_id: str):
    from agents import video_store
    _video_project_or_404(project_id)
    video_store.delete_project(project_id)
    return {"result": "deleted", "project_id": project_id}


@app.post("/video/projects/{project_id}/plan")
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


@app.post("/video/projects/{project_id}/frames")
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


@app.post("/video/projects/{project_id}/repair-motion")
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


@app.post("/video/projects/{project_id}/chain")
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


@app.post("/video/projects/{project_id}/clips")
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


@app.post("/video/jobs/{job_id}/cancel")
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


@app.patch("/video/shots/{shot_id}")
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


@app.post("/video/projects/{project_id}/shots")
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


@app.post("/video/shots/{shot_id}/duplicate")
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


@app.delete("/video/shots/{shot_id}")
def video_delete_shot(shot_id: str):
    from agents import video_store
    if not video_store.get_shot(shot_id):
        raise HTTPException(status_code=404, detail=f"Shot {shot_id} not found")
    video_store.delete_shot(shot_id)
    return {"result": "deleted", "shot_id": shot_id}


class ReorderRequest(BaseModel):
    shot_ids: list[str]


@app.post("/video/projects/{project_id}/shots/reorder")
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


@app.post("/video/shots/{shot_id}/split")
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


@app.post("/video/shots/{shot_id}/simplify")
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


@app.post("/video/shots/{shot_id}/merge")
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


@app.post("/video/projects/{project_id}/approve")
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

@app.get("/video/projects/{project_id}/validate")
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


@app.post("/video/projects/{project_id}/assemble")
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


@app.get("/video/finished")
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


@app.get("/video/projects/{project_id}/export")
def video_export(project_id: str):
    """The full production record as JSON (shot plan, prompts, seeds, bible)."""
    from agents import video_assembly
    _video_project_or_404(project_id)
    return video_assembly.export_metadata(project_id)


@app.get("/video/projects/{project_id}/subtitles")
def video_subtitles(project_id: str):
    from agents import video_assembly
    _video_project_or_404(project_id)
    return PlainTextResponse(video_assembly.export_subtitles(project_id),
                             media_type="text/plain; charset=utf-8")


# --- Health check ---

@app.get("/health")
def health():
    return {"status": "ok", "service": "bahAI Workforce API"}


if __name__ == "__main__":
    uvicorn.run("agents.api:app", host="0.0.0.0", port=8765, reload=True)
