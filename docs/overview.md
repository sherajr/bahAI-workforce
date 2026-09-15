# System overview — the three pipelines, and what sits around them

Moved out of the root `AGENTS.md` to keep that file small. This is descriptive
background, not rule text — the numbered hard rules referenced below live in
`docs/rules/`. Loaded on demand from the root `AGENTS.md` routing table; do not
copy this into CLAUDE.md or the root AGENTS.md.

## The system in one page

Three product pipelines, all entered from `agents/api.py`, all running long work
through the same background job store (`_start_job` + `/pipeline/status/{job_id}`,
polled by the dashboard):

- **Bookmarks** (`_run_full_pipeline`) — sold on Etsy. Create task → Librarian
  retrieves citations → Artist builds the prompt and generates the image (xAI) →
  `_pipeline_write_approve_sync` (consultation → Scribe writes → Reviewer scores
  → mechanical-edit revision loop) → save product → Compositor renders
  front/back PNGs. A Canva autofill step exists but is **off by default and off
  in practice** (`CANVA_AUTOFILL_ENABLED`, unset): 0 of 10 attempts ever
  succeeded, so it was parked behind a switch rather than left to fail in every
  run. Don't describe it as part of the working pipeline.
- **Quote cards** (`_run_card_pipeline`) — given away, never sold. Librarian →
  Artist (card brief) → consultation with `product="quote_card"` → optional
  translation (`translator.py`, Grok path) → `card_compositor.render_quote_card`
  (3.5×2in, multi-script) → `reviewer.score_quote_card` (which sees the RENDERED
  front face) → requote/repaint loop driven by the review's machine-readable
  `action` field.
- **Video** (`video_pipeline.py`) — a scene or story becomes many simple 3–4s
  shots that assemble into one video. See rules 30–34, 58.

Three things in the dashboard are not product pipelines at all. **Gatherings**
(rules 117–120) is the thread that ties a piece of community service together
— purpose, consultation, decisions, who accepted what, the printed kit, and
what was learned — and **Home** is the front page built from it. Neither owns a
record of its own: a gathering's commitments ARE the consultation's action
items, read through the project rather than copied into it.

The third is **Live Consultation** (rules 73–86): a real meeting between people, heard through
the browser on the OpenAI Realtime API — transcribed, structured into a
consultation map, and very occasionally spoken to. It shares nothing with
`agents/consultation.py`, the product pipelines' team consultation, but the
word.

Products carry `product_type`; bookmark-only endpoints reject cards via
`_require_bookmark`. Around the pipelines sit the **Colony** (the workforce as
an organisation — rules 35–41b), the **Secretary** (Sheraj's personal
assistant, rules 15–28), the **wallet** (rules 42–49), and Abigail's bridge into
the teams (rules 50–54).

State: `workforce.db` holds everything about the work (products, task_runs,
spend, video, colony). `private/` holds everything personal and is git-ignored
(rule 15). Generated files live in `outputs/`; databases store paths, never
binaries.

