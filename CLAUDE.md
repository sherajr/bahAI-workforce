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

## The two pipelines

`_run_full_pipeline` in `agents/api.py` is the bookmark entry point:
create task → Librarian retrieves citations → Artist builds prompt + generates
image (xAI) → `_pipeline_write_approve_sync` (consultation → Scribe writes →
Reviewer scores → mechanical-edit revision loop) → save product → Compositor
renders front/back PNGs → Canva autofill. The dashboard polls
`/pipeline/status/{job_id}`; jobs run in a thread pool (`_start_job`).

`_run_card_pipeline` (same file) is the QUOTE CARD entry point — a giveaway
product, never sold: Librarian → Artist (card brief) → consultation with
`product="quote_card"` → optional translation (`translator.py`, Grok path) →
`card_compositor.render_quote_card` (3.5×2in, multi-script) →
`reviewer.score_quote_card` (sees the RENDERED front face) → requote/repaint
revision loop driven by the review's machine-readable `action` field.
Products carry `product_type`; bookmark-only endpoints reject cards via
`_require_bookmark`.

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
8. **Translation disclaimers are code-appended, never LLM-written.** A quote
   card translation is always labeled AI-assisted/unofficial: fixed strings in
   `translator.LANGUAGES`, printed on the card by the compositor and stored in
   metadata. Same class of honesty rule as `_sanitize_claims`.
9. **A new card language ships only after a human-viewed render.** PIL draws
   missing glyphs as tofu without erroring, and unshaped Arabic renders as
   disjointed LTR letters — `card_compositor` shapes RTL per line with
   arabic-reshaper + python-bidi, and every font in `LANGUAGES.font_paths` was
   verified by eye. Adding a language = config entry + a viewed sample PNG.
10. **Bookmark consultation prompts are frozen via `_PRODUCT_FRAMES`.** The
    "bookmark" frame values in `consultation.py` reproduce the original prompt
    strings character-for-character; card wording changes go in the
    "quote_card" frame, never inline in the shared prompt bodies.
11. **Quote cards may only quote Ruhi Book 1.** `librarian.retrieve_ruhi_book1()`
    (backed by `agents/ruhi_book1_source.py` + `scripts/ingest_ruhi_book1.py`'s
    own ChromaDB collection) is the ONLY retrieval path `_run_card_pipeline`
    may call — never `retrieve()` (the general 7-text index the bookmark
    pipeline uses). An empty result must raise, never fall back to the
    general index or let the consultation's Librarian free-associate a quote
    from memory.

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
