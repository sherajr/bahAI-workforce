"""
Shared job store and cross-pipeline helpers, extracted from api.py.

Long-running pipelines run in worker threads; the dashboard polls
GET /pipeline/status/{job_id} (still in api.py) against the JOBS dict here.

This module has NO dependency on agents.api or any *_api.py router, on
purpose: api.py and every pipeline router (pipeline_api, card_api, x_api,
video_api, colony_api, secretary_api) import FROM here, so there is nowhere
for a circular import to start (see AGENTS.md / docs/rules/dispatch.md's
warning about routers that import back from api.py). `agents.api`
re-exports the names below (JOBS, _start_job, etc.) for external callers
that reach into its namespace directly (agents/colony.py,
agents/secretary_colony.py, several scripts/test_*.py) rather than through
HTTP, so `api.JOBS` and `jobs.JOBS` are the same object.
"""

import html
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

from agents.state import create_task as _state_create_task, update_task_status
from agents.state import _connect as _state_connect

def _esc(value) -> str:
    """
    Escape anything an OAuth callback echoes back into its HTML page (rule 71).

    Those three callbacks are the only endpoints outside the API key gate
    (they are redirect targets, so the provider cannot carry a header), which
    makes them the one place a crafted link reaches HTML this server writes.
    `error`/`error_description` come straight off the query string, so
    unescaped they were a script-injection primitive on the API's own origin.
    """
    return html.escape(str(value), quote=True)


def _web_image_path(local_path: str) -> str:
    """Convert a local outputs/ file path (Windows or POSIX) to a dashboard-servable URL path."""
    if not local_path:
        return ""
    name = str(local_path).replace("\\", "/").split("/")[-1]
    return f"/outputs/{name}"

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


def create_task(directive: str, task_type: str, assigned_to: str = None) -> str:
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

