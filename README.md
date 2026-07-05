# bahAI Workforce

A local-first multi-agent AI system that designs, writes, reviews, and publishes
Bahá'í-inspired bookmarks to Etsy (shop: PeaceAntz). Four AI agents — Librarian,
Artist, Scribe, Reviewer — consult together in the spirit of Bahá'í consultation,
then produce a finished product: artwork, a print-ready front/back bookmark pair,
and an Etsy listing scored against a 9-principle constitution.

A second product line, **Quote Cards** (3.5″×2″ giveaway outreach cards — never
sold, optionally carrying an AI-labeled translation in Spanish, Mandarin, or
Arabic), runs through a parallel pipeline: same agents, card-specific framing,
no Etsy. See `docs/fable5-briefing-quote-cards.md` for the design brief.

**How the app works, visually: see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).**

## Running it

```bash
# 1. Backend API (port 8765)
python agents/api.py

# 2. Dashboard (port 5173, proxies /api → 8765)
cd dashboard && npm run dev
```

Requires: Ollama running locally with `qwen3-16k` and `nomic-embed-text`,
plus API keys in `.env` (xAI for Grok + image generation; Canva and Etsy optional).

## What lives where

| Path | What it is |
|---|---|
| `agents/api.py` | FastAPI backend — all endpoints + the pipeline orchestration |
| `agents/consultation.py` | The 4-agent, 2-round consultation (scripture-grounded prompts) |
| `agents/librarian.py` | Vector search over Bahá'í writings (ChromaDB); citation verification |
| `agents/artist.py` | Image prompt building + xAI image generation |
| `agents/scribe.py` | Writes/revises the Etsy listing; mechanical edit application; honesty scrubbing |
| `agents/reviewer.py` | Scores listings 0–10 against the constitution (Grok + vision); card rubric (`score_quote_card`) |
| `agents/compositor.py` | Renders the 2×6-inch front (quote overlay) and back bookmark PNGs |
| `agents/translator.py` | Quote-card translations (Spanish/Mandarin/Arabic via Grok) — always labeled AI-assisted |
| `agents/card_compositor.py` | Renders the 3.5×2-inch quote-card faces (multi-script text, RTL shaping) |
| `agents/router.py` | LLM routing: local Ollama by default, paid Grok for review/copy tasks |
| `agents/state.py` | SQLite persistence (`workforce.db`): tasks, runs, agents, products |
| `agents/canva.py` / `agents/etsy.py` | OAuth + publishing integrations |
| `agents/system_prompt_builder.py` | Builds each agent's system prompt from the constitution |
| `dashboard/` | React + TypeScript + Tailwind UI (Pipeline, Products, Trust, Settings tabs) |
| `bahai-workforce-constitution.md` | The 9 principles every product is scored against |
| `scripts/` | One-time setup (`download_texts`, `ingest_texts`) and diagnostics |
| `docs/ARCHITECTURE.md` | Diagrams + deeper conventions for anyone (human or AI) changing the code |

Generated at runtime (gitignored): `workforce.db`, `outputs/` (images),
`vector_store/` (ChromaDB), `texts/` (source writings), `canva_token.json`.

## Key conventions

- **Local model is context-poor.** Qwen (via Ollama) handles the Scribe and
  Librarian. Keep its prompts short; never dump full transcripts at it.
  Grok (paid) handles Reviewer scoring and all vision calls.
- **The `bookmark_quote` field is Librarian-locked.** No agent or code path may
  rewrite it after consultation commits to it.
- **Revisions are mechanical first.** The Reviewer emits `edits`
  (find/replace); `scribe.apply_edits` applies them in code so compliance never
  depends on a small model obeying prose.
- **No fabricated claims.** `scribe._sanitize_claims` deterministically strips
  "handcrafted", exact motif counts ("nine-pointed"), etc. from every revision path.
- **Verify changes live.** The established pattern: offline logic test first,
  then a real call against the local Ollama / Grok APIs with real DB data.
