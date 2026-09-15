# Rules 55-57 — cancelling a run

Canonical numbered hard rules for this subsystem. **Numbers are permanent — never
renumber, only append.** Cited from code comments across the repo.
Loaded on demand from the root `AGENTS.md` routing table; do not copy these
into CLAUDE.md or the root AGENTS.md.

## Rules 55–57 — cancelling a run (`POST /pipeline/status/{job_id}/cancel`)

Added 2026-08-14 (owner ask: "a way to cancel the run if I want to start over"
that "clears out incomplete stuff and doesn't create issues with job numbers").
Verify with `scripts/test_job_cancel.py` (real worker threads, fake runners).

55. **Cancellation is COOPERATIVE, and `JobCancelled` derives from
    `BaseException`.** A Python thread cannot be killed from outside without
    leaving half-written files and held locks — precisely the mess "start over"
    is meant to avoid. Every pipeline already narrates itself through
    `progress(...)` between stages, so that callback (and `on_turn`, and the
    wake-up from a human-input pause) is the checkpoint. The BaseException
    inheritance is load-bearing, not style: this codebase is full of deliberate
    `except Exception` blocks that turn a failed stage into a survivable
    recorded error — `_run_card_batch` logs a failed card and moves to the next
    — and every one of them would swallow a cancellation and keep spending. The
    suite pins this with a runner that wraps its work in `except Exception`.
56. **A cancel stops the work; it never rewrites what the work already did.**
    `task_runs` rows stay (the handoff graph is derived from them, rule 35 —
    deleting them would falsify history), products already saved stay (a card
    finished before the cancel is finished and paid for), and files in
    `outputs/` stay (paid artwork, and a thread may still hold the handle). What
    IS closed out: the task row the run was in the middle of becomes `cancelled`
    (a task it had already completed stays completed), the pending-input
    rendezvous is cleared, and no result is recorded — so nothing half-made can
    be read off the job. A cancelled run is never reported as an error and never
    moves any agent's trust (rule 14).
57. **"cancelled" is a terminal status everywhere it matters.** It is in
    `_start_job`'s eviction set (otherwise cancelled jobs accumulate for ever),
    it drops out of `colony.team_activity` so the map stops showing the team as
    working, and the dashboard keeps polling until the job REALLY reports
    cancelled before unlocking the form — the button says "Stopping…", never
    "stopped", because the step in flight has to finish first. Job ids are
    random 8-char uuids, never a sequence, so a cancelled run can never collide
    with or renumber a later one.

