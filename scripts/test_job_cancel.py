"""
Offline regression suite for cancelling a running pipeline job.

    python scripts/test_job_cancel.py

Free and fast — no LLM calls, no paid API. Real worker threads and the real
job store, with fake runners standing in for the pipelines, because the whole
point of the feature is what happens to a thread that is genuinely in flight.
Console output is ASCII only (Windows cp1252 — see AGENTS.md gotchas).
"""

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# The API is owner-only (rule 70). Setting the key in the environment keeps
# this suite off the real private/api_key.txt and lets its TestClient present
# a valid key -- there is deliberately no switch that turns the gate off.
os.environ["DASHBOARD_API_KEY"] = "dashboard-suite-test-key"
_AUTH = {"X-API-Key": os.environ["DASHBOARD_API_KEY"]}

_TMP = Path(tempfile.mkdtemp(prefix="cancel_test_"))
os.environ["ANTHROPIC_API_KEY"] = os.environ.get("ANTHROPIC_API_KEY", "test-key")

import agents.state as state  # noqa: E402

state.DB_PATH = _TMP / "workforce.db"

import agents.colony as colony  # noqa: E402

colony.DB_PATH = state.DB_PATH
state.init_db()

import agents.api as api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(api.app, headers=_AUTH)

PASS = FAIL = 0
FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{label}{(' -- ' + detail) if detail else ''}")
        print(f"  FAIL: {label}" + (f" -- {detail}" if detail else ""))


def section(title: str):
    print(f"\n=== {title} ===")


def wait_for(job_id: str, status: str, timeout: float = 8.0) -> bool:
    """Poll the real job store until the worker settles, or give up."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with api._JOBS_LOCK:
            job = api.JOBS.get(job_id)
            if job and job["status"] == status:
                return True
        time.sleep(0.05)
    return False


# ── A run in flight stops at its next step ────────────────────────────────────

section("Cancelling a run that is genuinely in flight")

steps_done: list = []


def _long_runner(progress, on_turn, ask):
    for i in range(200):
        progress(f"step {i}")       # the cancellation checkpoint
        steps_done.append(i)
        time.sleep(0.05)
    return {"finished": True}


job_id = api._start_job("full-pipeline", _long_runner)
time.sleep(0.3)
r = client.post(f"/pipeline/status/{job_id}/cancel")
check("the cancel endpoint answers immediately with 'cancelling'",
      r.status_code == 200 and r.json()["status"] == "cancelling", r.text[:160])
check("it says the step in flight finishes first, rather than claiming it stopped",
      "finishes first" in r.json()["message"], r.json()["message"])
check("the run really stops", wait_for(job_id, "cancelled"),
      f"status was {api.JOBS[job_id]['status']}")
stopped_at = len(steps_done)
time.sleep(0.3)
check("and stays stopped — no further steps run", len(steps_done) == stopped_at,
      f"{stopped_at} -> {len(steps_done)}")

job = api.JOBS[job_id]
check("a cancelled run is NOT recorded as an error",
      job["status"] == "cancelled" and job["error"] is None)
check("it carries no result, so nothing half-made can be read off it",
      job["result"] is None)
check("the cancel is written into the run's own steps, where Sheraj can see it",
      any("Cancel requested" in s["message"] for s in job["steps"]))


# ── The signal cannot be swallowed by ordinary error handling ─────────────────

section("Cancellation survives the codebase's except-Exception blocks")

survived: list = []


def _swallowing_runner(progress, on_turn, ask):
    """
    Stands in for _run_card_batch: each item is wrapped so one failure is
    recorded and the batch moves on. If JobCancelled were an ordinary
    Exception, this would log the cancel as a failed card and keep spending.
    """
    for i in range(200):
        try:
            progress(f"card {i}")
            survived.append(i)
            time.sleep(0.05)
        except Exception:            # noqa: BLE001 — deliberately broad, as in the real batch
            survived.append("swallowed")
    return {"finished": True}


job_id = api._start_job("card-batch", _swallowing_runner)
time.sleep(0.3)
client.post(f"/pipeline/status/{job_id}/cancel")
check("a broad `except Exception` cannot swallow the cancellation",
      wait_for(job_id, "cancelled") and "swallowed" not in survived,
      "JobCancelled must derive from BaseException")


# ── A run paused for Sheraj's input ──────────────────────────────────────────

section("Cancelling a run that is paused waiting for input")


def _pausing_runner(progress, on_turn, ask):
    progress("working")
    ask("What would you like changed?")   # blocks until answered or cancelled
    progress("carrying on")               # must never be reached after a cancel
    return {"finished": True}


job_id = api._start_job("card-pipeline", _pausing_runner)
check("the run reaches the pause", wait_for(job_id, "waiting_for_input"))
client.post(f"/pipeline/status/{job_id}/cancel")
check("cancelling wakes it instead of leaving it asleep for 30 minutes",
      wait_for(job_id, "cancelled", timeout=5))
check("the pending-input rendezvous is cleaned up",
      job_id not in api._PENDING_INPUT)
check("it did not carry on into the next stage",
      api.JOBS[job_id]["progress"].startswith("Cancelled"))


# ── What a cancelled run leaves behind ───────────────────────────────────────

section("A cancelled run leaves an honest record")

marks: dict = {}


def _task_runner(progress, on_turn, ask):
    progress("Creating task...")
    finished = api.create_task("a task this run completed", "design")
    state.update_task_status(finished, "completed")
    in_flight = api.create_task("a task this run was in the middle of", "design")
    marks["finished"], marks["in_flight"] = finished, in_flight
    state.log_run(in_flight, "librarian", "retrieve", "in", "out")
    for i in range(200):
        progress(f"step {i}")
        time.sleep(0.05)
    return {"finished": True}


job_id = api._start_job("full-pipeline", _task_runner)
time.sleep(0.4)
client.post(f"/pipeline/status/{job_id}/cancel")
check("the run stops", wait_for(job_id, "cancelled"))


def task_status(task_id: str) -> str:
    with state._connect() as conn:
        row = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return row["status"] if row else "missing"


check("the task it was in the middle of is marked cancelled, not left running",
      task_status(marks["in_flight"]) == "cancelled",
      task_status(marks["in_flight"]))
check("a task it had already completed stays completed",
      task_status(marks["finished"]) == "completed", task_status(marks["finished"]))
check("the work it really did is KEPT in task_runs (the handoff graph is derived "
      "from those rows — deleting them would falsify history)",
      any(r["task_id"] == marks["in_flight"] for r in colony.recent_runs(limit=50)))


# ── Job bookkeeping ──────────────────────────────────────────────────────────

section("Job bookkeeping after a cancel")

r = client.get(f"/pipeline/status/{job_id}")
check("a cancelled job still reports itself over HTTP (the panel needs to see it)",
      r.status_code == 200 and r.json()["status"] == "cancelled", r.text[:140])
check("the job list no longer shows it as work in flight",
      all(j["status"] != "running"
          for j in client.get("/pipeline/jobs").json() if j["job_id"] == job_id))
check("and the Colony stops showing the team as working",
      all(not t["jobs"] for t in colony.colony_snapshot()["teams"]))

before = set(api.JOBS)
new_id = api._start_job("card-pipeline", lambda p, t, a: {"ok": True})
check("a new run after a cancel gets its own fresh id (ids are random — never "
      "reused, never a sequence that could collide)",
      new_id not in before and len(new_id) == 8)
wait_for(new_id, "done", timeout=3)

r = client.post(f"/pipeline/status/{new_id}/cancel")
check("cancelling an already-finished run is refused politely, not an error",
      r.status_code == 200 and r.json().get("already_finished") is True, r.text[:140])
check("and it does not overwrite what the run actually did",
      api.JOBS[new_id]["status"] == "done")
check("cancelling an unknown job is a 404",
      client.post("/pipeline/status/nosuchjob/cancel").status_code == 404)

# Eviction LAST: it deliberately drops old jobs, so nothing after it may look
# one up. Cancelled jobs must be evictable or the store grows for ever.
_real_max = api._MAX_JOBS
api._MAX_JOBS = 3
for _ in range(6):
    dead = api._start_job("card-pipeline", _long_runner)
    time.sleep(0.15)
    client.post(f"/pipeline/status/{dead}/cancel")
    wait_for(dead, "cancelled", timeout=5)
check("cancelled jobs are evicted like any other finished job — the store stays "
      "bounded no matter how many runs are started and stopped",
      len(api.JOBS) <= api._MAX_JOBS + 1, f"{len(api.JOBS)} jobs held")
api._MAX_JOBS = _real_max


# ── Summary ──────────────────────────────────────────────────────────────────

print(f"\n{'=' * 62}")
print(f"  {PASS} passed, {FAIL} failed  ({PASS + FAIL} checks)")
if FAILURES:
    print("\nFailures:")
    for f in FAILURES:
        print(f"  - {f}")
print(f"{'=' * 62}")
sys.exit(1 if FAIL else 0)
