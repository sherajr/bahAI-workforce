# CLAUDE.md — orientation for AI coding sessions

Read `README.md` for the project map and `docs/ARCHITECTURE.md` for diagrams.
This file covers what an AI changing this code must know.

## Commands

```bash
python agents/api.py                       # backend on :8765
cd dashboard && npm run dev                # UI on :5173
cd dashboard && npx tsc --noEmit           # typecheck (no test suite exists)
python -c "import agents.api"              # fast backend sanity check
```

Verification pattern used throughout this repo: no formal test suite — test
logic offline with mocks first, then verify live against the real SQLite DB
(`agents.state.get_all_products()`) and real LLM calls (Ollama local, xAI Grok).
FastAPI's `TestClient` works well for endpoint checks without starting a server.

## The one pipeline that matters

`_run_full_pipeline` in `agents/api.py` is the entry point for everything:
create task → Librarian retrieves citations → Artist builds prompt + generates
image (xAI) → `_pipeline_write_approve_sync` (consultation → Scribe writes →
Reviewer scores → mechanical-edit revision loop) → save product → Compositor
renders front/back PNGs → Canva autofill. The dashboard polls
`/pipeline/status/{job_id}`; jobs run in a thread pool (`_start_job`).

## Hard rules (violating these reintroduces fixed production bugs)

1. **Qwen has tight context.** Anything routed to Ollama (`router.py`:
   everything NOT in `GROK_TASK_TYPES`) must get lean prompts. Long prompts
   made Qwen burn its whole token budget thinking and return `{`.
2. **`bookmark_quote` is locked.** `apply_edits` rejects edits to it and must
   report them as `rejected_locked`, never silently drop them.
3. **Revision is forward-chaining.** The loop revises `cur_listing` with
   `cur_review` (the latest), tracks `best_*` separately, adopts ties (newer
   listing wins), and only counts strict regressions toward the 2-strike stall.
4. **Every revision path must end in `_sanitize_claims`.** It deterministically
   strips false claims (handcrafted, exact motif counts). LLM compliance is
   never trusted for honesty-critical text.
5. **Reviewer JSON can truncate at the token ceiling.** `_parse_review` tracks
   whether `_repair_truncated_json` fired and drops the last `edits` element
   if so. Keep `edits` early in the schema field order.
6. **Consultation scripture stays hand-curated.** `CONSULTATION_SCRIPTURE` in
   `consultation.py` maps each consultation moment to one short cited excerpt
   (≤40 words). No vector DB for this, per explicit owner decision.
7. **The consultation's round-2 decision is binding.** `reviewer.score()`
   receives `consultation_decision`; overrides must be named
   "REOPENING team decision: ..." — never silently contradicted.

## Gotchas

- Windows console is cp1252: use ASCII `->` not `→` in anything `print`ed by
  scripts; the API/dashboard themselves are UTF-8 safe.
- `state.py` migrations run on every startup (ALTER TABLE wrapped in
  try/except) — add new product columns there AND to the `update_product`
  allowlist.
- Ollama calls set `think: False` (Qwen3 hybrid-thinking would silently eat
  the output budget otherwise).
- The owner (Sheraj) is non-technical: dashboard-visible behavior is the
  deliverable, and errors must surface in the Activity Log, never vanish
  (Canva autofill once failed silently for weeks).
