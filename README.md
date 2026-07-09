# bahAI Workforce

A local-first, multi-agent AI workshop that designs **Bahá'í-inspired bookmarks
and giveaway quote cards** — artwork, verified scripture quotes, and print-ready
files — run from a personal dashboard by one non-technical owner (Sheraj).

**What you actually get out of it, today:** print-ready 300 DPI PNG faces and a
cut-tolerant, two-page US Letter **print-sheet PDF** that you download and take
to any print shop. That's the product. Nothing is sold or auto-published
anywhere — an Etsy publishing path exists in the code but has never been
connected (0 of 84 products ever published; see [Honest status](#honest-status-whats-real-whats-not)).

> **AI coding agent? Start here.** Read [AGENTS.md](AGENTS.md) (canonical dev
> orientation for every tool — commands, pipelines, 28 hard rules, gotchas) and
> [STATUS.md](STATUS.md) (what's in progress, what recent sessions did) before
> changing anything. Multiple AI coding tools work on this repo — check
> `git status` and STATUS.md for in-flight work first.

## Why this exists

Sheraj is building a values-grounded creative business from zero, and the
project doubles as an experiment: **can Bahá'í principles — honesty,
consultation, trustworthiness, moderation — be enforced by code rather than
requested in prompts?** Every interesting piece of this system is an answer to
that question:

- LLMs were repeatedly caught inventing product claims ("handcrafted", "nine
  hand-painted motifs"), so honesty is **deterministic**: `scribe._sanitize_claims`
  strips false claims from every text path in code, and AI-artwork disclosures
  are appended by code, never written by a model.
- LLMs will confidently misquote scripture, so **every printed quote is
  verified against a local library of the actual writings** (ChromaDB vector
  index), re-checked deterministically before it's locked, and locked quotes
  cannot be rewritten by any agent afterward.
- Agents don't just generate — they **consult** (three structured rounds, in
  the spirit of Bahá'í consultation, with one pause where the owner is asked
  for direction mid-run), and the team's recorded decision binds the Reviewer.
- Agents **earn trust** from judged outcomes (review verdicts, deterministic
  checks — never "the API call succeeded"), and trust has consequences: low
  Reviewer trust means publishing requires explicit human confirmation.
- Cloud spend is **metered at the call site** and reported against a monthly
  ceiling; most work runs free on a local model (Qwen via Ollama), with paid
  models (xAI Grok, Claude) reserved for the tasks that need them.

The 9-principle constitution every product is scored against is in
[bahai-workforce-constitution.md](bahai-workforce-constitution.md). The 28 hard
rules in [AGENTS.md](AGENTS.md) each exist because of a real production bug.

## The roster

Seven AI personas, each a real pipeline role (display names live in the
dashboard; the backend keys on the lowercase ids):

| Name | Role | What they do |
|---|---|---|
| Ruth | Librarian | Retrieves and verifies quotes from the indexed Bahá'í writings |
| Theo | Artist | Builds image prompts, generates artwork (xAI) |
| Clara | Scribe | Writes and revises listing/marketing copy |
| Amos | Reviewer | Scores products 0–10 against the constitution (Grok + vision — he judges the actual rendered image) |
| Sofia | Translator | Quote-card translations (Spanish/Mandarin/Arabic), always labeled AI-assisted |
| Nora | Steward | Reports spend, revenue, and the monthly ceiling |
| Abigail | Secretary | Sheraj's personal assistant — dashboard chat + WhatsApp, Google Workspace, all actions via real tool calls behind approval gates |

## How a product gets made

1. **Pick a theme** on the dashboard's Pipeline tab ("detachment", "unity"…)
   and choose Bookmark or Quote card.
2. **The team works while you watch**: Ruth retrieves real citations, Theo
   paints, then all four production agents consult in three rounds — a live
   transcript streams to the dashboard.
3. **The run pauses for you** between consultation rounds: you're shown the
   direction (and artwork) and asked for guidance. Reply, or send nothing to
   let the team proceed.
4. **Write → score → revise**: Clara writes the copy, Amos scores it against
   the constitution, and a mechanical revision loop applies his edits until the
   target score, a stall, or the attempt cap. Every revision path ends in the
   honesty scrub. Products that fall short still save, wearing a visible
   "best effort" badge.
5. **Style it visually** (optional): the native layout editor in the product
   drawer adjusts font, text size/position, color, shading — with a live
   preview. It structurally cannot touch the printed words (the layout request
   carries no text; words are re-read from the verified stored data at render
   time).
6. **Download and print**: per-face PNGs, or the print-sheet PDF (page 1 =
   fronts grid, page 2 = backs grid, aligned for cutting) — take it to a print
   shop.

Two product lines share this shape:

- **Bookmarks** (2″×6″) — the would-be Etsy line; quotes drawn from a 7-text
  library of the writings.
- **Quote cards** (3.5″×2″) — giveaway outreach cards, never sold. Stricter by
  design: quotes may come **only** from Ruhi Book 1 (a dedicated index; an
  empty result fails the run rather than falling back), optionally translated
  with a code-appended "AI-assisted/unofficial" disclaimer printed on the card,
  and Amos scores the actual rendered card face.

There's also a human-approved **X/Twitter giveaway channel** (@peaceAntz):
drafts go through the same consultation + scoring, and nothing posts without
Sheraj clicking approve. Two real posts are live as of 2026-07-09.

## Honest status: what's real, what's not

*(as of 2026-07-09 — the working system's own honesty rules apply to its README)*

| Piece | Reality |
|---|---|
| Bookmark + quote-card pipelines | **Real and working.** 84 products created (66 bookmarks, 18 cards) |
| Print output (PNGs + print-sheet PDF) | **Real and working** — this is the actual product today |
| Visual layout editor | **Real and working**, exercised in production |
| Secretary (dashboard + WhatsApp + Google Workspace) | **Real and live** — runs unattended at logon (Phases 1–3; Phase 4 "recovery rhythms" not started) |
| X giveaway posting | **Real, human-approved** — 2 live posts |
| Etsy publishing | **Built, never connected.** No OAuth ever completed; `POST /etsy/publish` skips gracefully; 0 listings ever created. The listing copy the Scribe writes is currently review-and-display only |
| Canva autofill | **Built, broken in practice** — connected once, but all 10 autofill attempts failed (API 400s, last tried 2026-07-05); superseded by the native layout editor |
| Revenue | **$0 so far.** Total AI spend to date ≈ $14 (mostly metered per-call; ceiling $15/month) |

## Running it

On Sheraj's machine the backend **already runs as a Windows Scheduled Task**
("bahAI Secretary API", auto-starts at logon, port 8765) — don't start a second
copy; see AGENTS.md's Commands section for the restart procedure.

Fresh setup elsewhere:

```bash
pip install -r requirements.txt
python -m uvicorn agents.api:app --host 127.0.0.1 --port 8765   # backend
cd dashboard && npm install && npm run dev                       # UI on :5173
```

Requires: Ollama running locally (`qwen3-16k`, `nomic-embed-text`), an xAI API
key in `.env` (images + review), and `scripts/download_texts.py` +
`scripts/ingest_texts.py` once to build the local library. Anthropic key only
for the Secretary; Canva/Etsy/WhatsApp/Google/X keys all optional.

## What lives where

| Path | What it is |
|---|---|
| `AGENTS.md` | **Canonical dev orientation for any AI coding tool** — commands, pipelines, 28 hard rules, gotchas |
| `CLAUDE.md` | Thin `@AGENTS.md` import for Claude Code — don't edit directly |
| `STATUS.md` | Living snapshot + running log of recent sessions across all tools |
| `bahai-workforce-constitution.md` | The 9 principles every product is scored against |
| `agents/api.py` | FastAPI backend — all endpoints + both pipeline orchestrations |
| `agents/consultation.py` | The 3-round, scripture-grounded team consultation (one human pause) |
| `agents/librarian.py` | Vector search over the writings (ChromaDB); citation verification |
| `agents/artist.py` | Image prompt building + xAI image generation |
| `agents/scribe.py` | Listing copy writing/revision; `_sanitize_claims` honesty scrub |
| `agents/reviewer.py` | Constitution scoring (Grok + vision); card rubric |
| `agents/compositor.py` / `agents/card_compositor.py` | Render the print faces (bookmark 600×1800 px; card 1050×600 px, multi-script + RTL shaping) |
| `agents/layout.py` | Layout editor's knobs + the `sanitize()` boundary (never carries text) |
| `agents/print_sheet.py` | Multi-up, cut-tolerant Letter PDF generator |
| `agents/translator.py` | Card translations — disclaimers are fixed strings, never LLM-written |
| `agents/router.py` | LLM routing (local Ollama default; Grok for review/vision; Claude for the Secretary) + spend metering |
| `agents/state.py` | SQLite persistence (`workforce.db`): tasks, runs, trust, products, spend |
| `agents/secretary*.py`, `agents/gcal.py` + `g*.py`, `agents/whatsapp.py`, `agents/scheduler.py`, `agents/badi_dates.py` | The Secretary subsystem (private data stays in `private/`, git-ignored) |
| `agents/x_post.py` | The human-approved X giveaway pipeline |
| `agents/etsy.py` / `agents/canva.py` | Publishing integrations — built, currently dormant (see status table) |
| `dashboard/` | React + TypeScript + Tailwind UI (Pipeline, Products, Post to X, Abigail, Trust, Settings + activity log strip) |
| `docs/ARCHITECTURE.md` | Diagrams + deeper conventions |
| `scripts/` | One-time setup (text download/ingest) and diagnostics |

Generated at runtime (gitignored): `workforce.db`, `outputs/` (artwork, faces,
print sheets), `vector_store/`, `texts/`, `private/`, token files, `logs/`.

## Where it could go

Grounded in what the code already supports (no fantasy features):

- **Actually connect Etsy** — the entire path (OAuth, draft listing, price
  policy, trust gate, AI disclosure) is built and waiting; it's a
  credentials-and-approval task, not an engineering one.
- **Secretary Phase 4 (recovery rhythms)** — check-in/streak tables and the
  scheduler already exist; the spec is `docs/fable5-briefing-secretary.md`.
- **More card languages** — `translator.LANGUAGES` is config-shaped and the
  compositor handles arbitrary scripts; each addition needs a human-verified
  sample render (hard rule 9).
- **Close the loop on recipient feedback** — quote cards already store
  "how did it land?" notes; nothing reads them yet.
- **More product families** — `product_type`, per-type compositors, layout
  options, and print-sheet utilities generalize; a new line should copy the
  quote-card discipline (restricted sourcing, rendered-face review, code-owned
  disclosures).
- **Extend trust consequences** — trust levels exist and gate Etsy publishing;
  the same pattern could gate other unattended actions.

## Key conventions (the short version)

- **The local model is context-poor** — keep Qwen prompts lean; Grok handles
  scoring and vision; Claude belongs to the Secretary alone.
- **The printed quote is locked** after consultation; no agent path may rewrite
  it. (A manual owner edit is allowed but visibly flags the quote unverified
  and re-renders the face.)
- **Honesty-critical text is never trusted to LLM compliance** — scrubs and
  disclosures are code.
- **Verify live** — no formal test suite; the discipline is offline logic tests
  first, then real calls against the running system and real DB.

The full, binding versions of these (and 24 more) are in [AGENTS.md](AGENTS.md).
