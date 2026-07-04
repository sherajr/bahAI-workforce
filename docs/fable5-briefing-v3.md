# bahAI Workforce — Fable 5 Developer Briefing (v3)

> This document supersedes `fable5-briefing-v2.md`. Read the CHANGELOG at the bottom before starting.

---

## 1. Role & Non-Negotiables

You are a coding agent continuing development of **bahAI Workforce**, a local-first multi-agent AI system. The codebase is at `c:\Users\Sheraj\Documents\bahAI-workforce`. You are working with **Sheraj**, a non-technical Bahá'í practitioner building this from zero. He delegates technical decisions to you; your job is to make concrete recommendations, get sign-off, and build — not to present menus of options.

### Hard rules you must never break

1. **Never fabricate or loosely paraphrase a Bahá'í scriptural citation.** All scripture is quoted exactly with source, routed through the Librarian/verification path. If exact wording cannot be verified, say so explicitly — never invent one or present a paraphrase in quotation marks.
2. **When a requirement is ambiguous or the code contradicts the briefing, STOP and ask Sheraj** rather than guess. Use `AskUserQuestion` (one focused question, not four).
3. **One phase at a time. Wait for explicit "approved, proceed" before starting the next.** Never bundle phases — the user validates after each.
4. **Never ask Sheraj to restart uvicorn.** The API runs with `reload=True` and hot-reloads on every file save. Telling him to restart wastes his time and implies you don't know the setup.
5. **Never echo `.env` values in any response.** The file at `c:\Users\Sheraj\Documents\bahAI-workforce\.env` contains live API keys (xAI, Anthropic, Canva). Acknowledge their presence; never print their values.
6. **No Docker.** All services run natively. Don't suggest it.
7. **GPU has 8 GB VRAM.** Ollama (local LLM) and xAI image generation cannot run simultaneously — the pipeline already sequences them correctly. Don't change that.

---

## 2. Current Session Objectives

Three phases of work are ready to be implemented, in this order (rationale below):

### Recommended order

**Phase 1 → Constitution (7 → 9 principles) + update reviewer**
This is the fastest phase and is foundational. The reviewer already scores against exactly 7 principles using hardcoded key names. Expanding to 9 updates the scoring schema that everything else depends on. Doing it first means Phases 2 and 3 operate on the correct foundation.

**Phase 2 → Fix trust scoring (broken) + calibrate reviewer (too lenient)**
This is the most impactful functional fix. The trust system is effectively static right now — agent levels never move meaningfully. Fixing it makes the system honest about what agents have actually earned. Reviewer calibration is bundled here because the inflated scores are what feed the broken trust data.

**Phase 3 → Etsy publishing activation**
The code is largely written (OAuth flow, `/etsy/publish` endpoint, `publish_draft_listing()` call all exist). This phase is mostly blocked on Sheraj completing a one-time credential setup at etsy.com/developers. The coding work that remains is small. Doing it last means there are real, well-scored products ready to publish when the channel opens.

Present Phase 1's plan first. Wait for sign-off before writing code.

---

## 3. Project Context

### The real vision

**PeaceAntz** is Sheraj's brand — 5 years old on Etsy, 3 sales, dormant. It started as a band, became a crypto DAO experiment, then a personal brand. The long-term vision: a **decentralized autonomous organization** — a force for peace that no single person controls but everyone benefits from. A conglomerate of many things. **bahAI Workforce is the AI backbone of that DAO.** It starts by making bookmarks. That's the seed.

Sheraj's dual goal is personal transformation + income and community benefit — these are not separate purposes, they are the same work.

Everything you build should work toward this vision: multi-product ready, honest about quality, earning trust incrementally. The trust system matters because this will scale.

### PeaceAntz Etsy shop

- **URL:** https://www.etsy.com/shop/PeaceAntz
- **Location:** California, United States
- **Age:** 5 years — Etsy's algorithm trusts established accounts more than new ones
- **Sales:** 3 total, no reviews — dormant but real
- **Current listings:** 3D printing services, apparel — unrelated to bookmarks, likely to be retired eventually
- **Brand vibe:** peace symbol + ant logo — playful, peace-themed, naturally compatible with Bahá'í values
- **Etsy env vars** (all currently empty in `.env`): `ETSY_CLIENT_ID`, `ETSY_CLIENT_SECRET`, `ETSY_SHOP_ID`

### How to work with Sheraj

- **Non-technical.** He follows instructions, runs commands, fills in forms. Does not write code.
- **Directed style.** Make a concrete recommendation; don't present a list of equal options.
- **Build iteratively.** Propose plan → wait for sign-off → build → check in.
- **Honest.** If something won't work, say so clearly. He'd rather know now.
- **Never ask him to restart uvicorn.** `reload=True` is already set.

### Stack

| Component | Detail |
|---|---|
| API backend | Python 3.11, FastAPI + uvicorn, `localhost:8765` |
| Dashboard frontend | React + Vite + shadcn/ui, `localhost:5173` |
| Start API | `python agents/api.py` from project root |
| Start dashboard | `npm run dev` inside `dashboard/` |
| n8n | Installed but abandoned — do not use |
| Canva | `agents/canva.py` exists, credentials in `.env`, not actively used — skipped gracefully |
| Local LLM | Ollama (`qwen3-16k:latest`) at `http://localhost:11434` — used for image brief writing |
| Cloud LLM | xAI Grok (`XAI_MODEL` from `.env`, `XAI_BASE_URL` default `https://api.x.ai/v1`) — used for scribe, reviewer, librarian |
| Image generation | xAI API (`XAI_IMAGE_MODEL` from `.env`) — saves to `outputs/` |
| Vector DB | ChromaDB local + `nomic-embed-text` embeddings via Ollama |
| State DB | SQLite at `workforce.db` |
| Hot reload | uvicorn `reload=True` — file saves auto-reload the API |

**LLM routing (`agents/router.py`):**
- Task types in `GROK_TASK_TYPES = {"copy", "copywriting", "review", "creative_writing", "complex_analysis", "scribe", "reviewer", "librarian"}` → Grok API
- All other task types (e.g. `"design"`, `"plan"`) → Ollama local

### Key files

| File | Purpose |
|---|---|
| `agents/api.py` | All HTTP endpoints, `_run_full_pipeline()`, `_pipeline_write_approve_sync()`, `_log()` |
| `agents/artist.py` | `build_image_prompt()` (Qwen3), `generate_image()` (xAI API) |
| `agents/consultation.py` | `run_consultation()` — two-round 4-turn consultation |
| `agents/scribe.py` | `write_listing()`, `revise_listing()` |
| `agents/reviewer.py` | `score()` → `{scores: {7 principles}, overall, passed, recommendation}` |
| `agents/librarian.py` | `retrieve()`, `verify()`, `format_citation()` |
| `agents/compositor.py` | `render_bookmark_pair()` → 300dpi front + back PNGs |
| `agents/router.py` | `call_llm()` routing logic, `GROK_TASK_TYPES` |
| `agents/state.py` | SQLite helpers, trust system (`_update_agent_trust`, `get_agent_status`) |
| `agents/etsy.py` | OAuth flow + `publish_draft_listing()` — exists, awaiting credentials |
| `agents/canva.py` | Canva OAuth + autofill — exists, skipped gracefully |
| `bahai-workforce-constitution.md` | 7 Bahá'í principles, each with verified quote + source link |
| `dashboard/src/components/` | PipelinePanel, TrustPanel, ProductsGallery, SettingsPanel, etc. |
| `workforce.db` | SQLite state — agents, tasks, task_runs, products tables |

### Pipeline flow (working end-to-end)

```
theme (from dashboard)
  → Librarian: retrieve() from ChromaDB
  → Artist: build_image_prompt() via Qwen3 (local)
  → Artist: generate_image() via xAI API → saved to outputs/
  → _pipeline_write_approve_sync():
      → run_consultation(): Round 1 (4 turns, Claude Haiku vision for image)
                          + Round 2 (4 turns, text LLM)
                          → verified_quote + transcript
      → Scribe: write_listing() with consultation_context + verified_quote
      → force-inject verified_quote → listing["bookmark_quote"]
      → Reviewer: score() against 7 principles → {scores, overall, passed, recommendation}
      → while overall < 9.0 and attempt < max_attempts:
            Scribe: revise_listing() → Reviewer: score() again
  → save product to SQLite
  → Compositor: render_bookmark_pair() → front_path, back_path (300dpi)
  → Canva autofill (skipped gracefully)
  → update task status → return result
```

---

## 4. Bug Reports & Tasks

### Phase 1 — Constitution: 7 → 9 Principles

#### Background

9 is the sacred number in the Bahá'í Faith — the numerical value of "Bahá'", the nine-pointed star. The constitution must have 9 principles. The current 7 are:

1. Work as Worship *(Kitáb-i-Aqdas ¶33)*
2. Judge by Fruit, Not Motion *(Paris Talks)*
3. Trustworthiness / Amanah *(Tablets of Bahá'u'lláh)*
4. Consultation *(Selections from the Writings of 'Abdu'l-Bahá no. 43)*
5. Moderation *(Gleanings sec. CX)*
6. Deeds Over Words *(Hidden Words, Persian no. 5)*
7. Craft in Service of Social Good *(The Secret of Divine Civilization)*

#### Required changes

**A. Propose two new principles** in `bahai-workforce-constitution.md` using the same structure as existing ones:
- `## N. Principle Name`
- **For agents:** framing (how this principle governs agent behavior specifically)
- One verified Bahá'í quotation with exact source and bahai.org/library link
- Route quote selection through the Librarian's verification discipline — never invent a citation

Candidates to propose to Sheraj (he decides): Independent Investigation of Truth; Justice ('Adl); Unity in Diversity; Harmony of Science and Religion. Propose two with brief rationale, get sign-off before writing the constitution entries.

**B. Update `agents/reviewer.py`** — currently the `user_message` has 7 hardcoded principle keys in the JSON scaffold. After the constitution expands, add the two new principle keys to:
- The JSON scaffold in `user_message` (lines 62–74 in current file)
- The example in the docstring
- Update any references to "7 constitution principles" → "9 constitution principles" in prompts

**C. Update any string references** to "7 principles" across the codebase:
```
agents/reviewer.py    # "Score this bookmark product against all 7 constitution principles"
agents/api.py         # "/reviewer/score" docstring: "7 constitution principles"
bahai-workforce-constitution.md  # footer "seven" references
```

#### Acceptance criteria

- `bahai-workforce-constitution.md` has exactly 9 numbered principles, each with a verified quote and bahai.org link
- `agents/reviewer.py` JSON scaffold has exactly 9 principle keys
- Running a full pipeline produces a review JSON with 9 keys under `"scores"`
- No principle score is fabricated — each cites a real Bahá'í text

#### Verification (Sheraj runs this)

1. Open `bahai-workforce-constitution.md` — count the `## N.` headings. Should be 9.
2. Run a pipeline from the dashboard with any theme.
3. When complete, click the score card in the Pipeline panel — it should show 9 rows of principle scores.
4. Or run in the terminal:
   ```
   python -c "import sqlite3, json; conn = sqlite3.connect('workforce.db'); row = conn.execute('SELECT reviewer_scores FROM products ORDER BY created_at DESC LIMIT 1').fetchone(); scores = json.loads(row[0]); print(list(scores['scores'].keys()))"
   ```
   Output should list 9 principle keys.

---

### Phase 2A — Fix the Agent Trust Scoring System

#### Root cause

`agents/state.py:171` — `_update_agent_trust(agent, passed)` returns immediately when `passed is None`. This is correct behavior. The bug is in who passes a non-None value.

In `agents/api.py:470–473`, the `_log()` helper used inside `_pipeline_write_approve_sync()`:

```python
def _log(agent, step, output):
    if req.task_id:
        log_run(req.task_id, agent, step, req.theme[:200], json.dumps(output)[:400],
                passed_review=output.get("passed") if agent == "reviewer" else None)
```

Only the reviewer ever gets `passed_review` set. Every other agent — scribe, librarian, artist, consultation, compositor — gets `passed_review=None`. Their `total_runs` and `clean_runs` in the `agents` table **never update** from a pipeline run. They're permanently frozen at their seeded values (`total_runs=0`, `clean_runs=0`, `trust_score=50.0`).

Secondary bug: `agents/state.py:23` lists:
```python
AGENT_NAMES = ["operator", "librarian", "artist", "scribe", "reviewer", "producer", "steward"]
```
The pipeline also calls `log_run()` with `agent="consultation"` and `agent="compositor"`, but neither name exists in `AGENT_NAMES`. There are no corresponding rows in the `agents` table. If `passed_review` were ever non-None for these, `_update_agent_trust` would silently do nothing (the `WHERE name=?` query returns no row). They need `agents` table rows, or the run logging needs to map them to an existing agent name.

#### Required changes

**A. Add meaningful pass/fail signals for each agent in `_run_full_pipeline()` in `agents/api.py`:**

| Agent | What "passed" means | How to compute |
|---|---|---|
| `librarian` | Retrieved at least 1 citation | `passed = len(citations) > 0` |
| `artist` (brief) | image_prompt is non-empty | `passed = bool(image_prompt.strip())` |
| `artist` (generate) | image_path is non-empty and file exists | `passed = bool(image_path) and Path(image_path).exists()` |
| `compositor` | front_path file was created | `passed = bool(front_path) and Path(front_path).exists()` |

Update the relevant `log_run()` calls in `_run_full_pipeline()` to pass these `passed_review` booleans.

**B. Add meaningful pass/fail for consultation and scribe in `_pipeline_write_approve_sync()` `_log()` calls:**

| Agent | What "passed" means |
|---|---|
| `consultation` | `verified_quote` is non-empty after consultation |
| `scribe` (write/revise) | listing has all required fields: `title`, `description`, `bookmark_quote` all non-empty |

Update `_log()` in `_pipeline_write_approve_sync()`:

```python
def _log(agent, step, output):
    if req.task_id:
        if agent == "reviewer":
            passed = output.get("passed")
        elif agent == "scribe":
            passed = all(output.get(k, "").strip() for k in ("title", "description", "bookmark_quote"))
        else:
            passed = None  # consultation logged separately with its own signal
        log_run(req.task_id, agent, step, req.theme[:200], json.dumps(output)[:400],
                passed_review=passed)
```

**C. Add `"consultation"` and `"compositor"` to `AGENT_NAMES` in `agents/state.py`:**

```python
AGENT_NAMES = ["operator", "librarian", "artist", "scribe", "reviewer",
               "producer", "steward", "consultation", "compositor"]
```

This ensures `init_db()` seeds rows for them so trust updates can land.

**D. Wire consultation pass/fail in `_run_full_pipeline()`:**

After `wa = _pipeline_write_approve_sync(wa_req, progress)`, the consultation transcript is available. Log a consultation run with `passed_review = bool(wa["consultation"] and any(t.get("agent") == "Librarian" for t in wa["consultation"]))`.

#### Acceptance criteria

- After one full pipeline run, `total_runs > 0` for at least 5 of the 7 pipeline agents (librarian, artist, scribe, reviewer, consultation, compositor, operator)
- `trust_score` differs from `50.0` for at least one agent other than reviewer
- Trust level advances for any agent that consistently passes (80% over 5+ runs)

#### Verification (Sheraj runs this — SQLite)

After running two or three full pipelines, open a terminal in the project root and run:

```
python -c "
import sqlite3
conn = sqlite3.connect('workforce.db')
rows = conn.execute('SELECT name, trust_level, trust_score, total_runs, clean_runs FROM agents').fetchall()
for r in rows:
    print(r)
"
```

Expected: every agent that participates in the pipeline should show `total_runs > 0` and `trust_score != 50.0` (unless all runs were 50/50 exactly, which is astronomically unlikely).

---

### Phase 2B — Calibrate Reviewer Scores (Too Lenient)

#### Root cause

The Reviewer prompt in `agents/reviewer.py:54–76` provides a JSON scaffold with example scores (8, 7, 9, 6, 8, 7, 9) and says `"Score each principle 1–10. A score below 6 means revision required before shipping."` This framing, combined with the Grok model's tendency toward positive outputs, results in scores that cluster around 7–9 and rarely fall below 7 even for mediocre outputs.

The `PASS_THRESHOLD = 6.0` (line 16) is too low — a product scoring 6.1 passes, which means the system is happy to ship something that's genuinely weak.

#### Required changes

**A. Rewrite the scoring instruction in `agents/reviewer.py` to enforce a realistic scale:**

Replace the current scoring instruction with language like:

```
Score each principle 1–10 with strict calibration:
  9–10: Exceptional — this principle is actively embodied, would serve as an example
  7–8:  Good — solid alignment, minor gaps only
  5–6:  Mediocre — principle is present but weakly executed; revision recommended
  3–4:  Poor — principle violated or ignored; revision required
  1–2:  Failure — actively contradicts the principle

A first-draft listing should typically score 6–7 on most principles.
Scores of 9–10 should be rare and earned. If you are scoring everything 8+, you are not being critical enough.
Scores below 5 should appear whenever the deliverable is genuinely weak on a principle.
```

**B. Raise `PASS_THRESHOLD` from `6.0` to `7.0`:**

```python
PASS_THRESHOLD = 7.0
```

This makes "pass" mean "actually good" rather than "barely acceptable."

**C. Add negative exemplars** to the reviewer prompt — one example of what a score of 4 looks like vs a score of 9, per principle. This is the most effective way to calibrate LLM scoring. Add a section to `user_message` after the scoring instruction.

**D. Update `TRUST_BADGES` in `agents/api.py:1164–1170`** to match the new threshold:

```python
TRUST_BADGES = {
    (9.0, 10.1): "EXCEPTIONAL",
    (7.0,  9.0): "APPROVED",
    (5.0,  7.0): "BORDERLINE",
    (0.0,  5.0): "REJECTED",
}
```

#### Acceptance criteria

- After the change, reviewer scores for a first-draft listing average 6.0–7.5 (not 8.5+)
- Revision loop triggers more often (because scores are lower before the threshold)
- A product that would have scored 8.7 before calibration now scores 7.0–7.5 on the same content

#### Verification (Sheraj runs this)

Run one full pipeline. When the score card appears in the dashboard:
- Check that the overall score is NOT automatically 8+
- The revision loop should have run at least once (dashboard shows "revising (attempt 2/3)")
- If the first run scores 9+ without revision, the calibration isn't working — flag it

---

### Phase 2C — Smarter Revision Loop (add image_fit + quote_quality)

This is an extension of Phase 2 that makes the revision loop smarter about *what* to fix, not just *that* something is wrong.

#### Root cause

The current revision loop only retries the listing text. If the image is a bad fit for the theme, or the quote is corrupt/inaccurate, revising the text won't fix it. The Reviewer has no structured way to signal "regenerate the image" or "get a new quote."

The force-inject in `agents/api.py:500` and `api.py:525`:
```python
if verified_quote:
    listing["bookmark_quote"] = verified_quote
```
...means the quote can never be replaced during revision, even if it's wrong. This needs to become conditional on `quote_quality >= 7`.

#### Required changes

**A. Add two new fields to the Reviewer JSON output in `agents/reviewer.py`:**

```json
{
  "scores": { ... 9 principles ... },
  "overall": 7.7,
  "image_fit": 6,
  "quote_quality": 8,
  "passed": true,
  "recommendation": "..."
}
```

`image_fit` (1–10): does the image match the theme and Bahá'í aesthetic?
`quote_quality` (1–10): is the quote authentic, correctly formatted, and error-free?

Add these fields to the JSON scaffold in the `user_message` of `reviewer.py`, with scoring guidance.

**B. Update `_pipeline_write_approve_sync()` in `agents/api.py`** to route remediation based on these signals:

```python
image_fit = review.get("image_fit", 10)
quote_quality = review.get("quote_quality", 10)

if image_fit < 5:
    # Re-run build_image_prompt() + generate_image() + run_consultation()
    # (needs progress callback and task_id plumbed through)
    pass  # implement this branch
elif quote_quality < 7:
    # Re-run consultation round 2 only to get a new verified_quote
    # Then re-run write_listing() with the new quote
    pass  # implement this branch
elif best_review.get("overall", 0) < req.target_score:
    # Existing: revise_listing() text only
    pass
```

**C. Remove the unconditional force-inject of `verified_quote`** in `_pipeline_write_approve_sync()`. Replace with:

```python
# Only lock the quote if it's high quality; otherwise let the revision loop improve it
if verified_quote and review.get("quote_quality", 10) >= 7:
    listing["bookmark_quote"] = verified_quote
```

Remove the same unconditional force-inject on the revision round (line 525).

**D. Remove the `"USE EXACTLY THIS TEXT"` / `"KEEP EXACTLY THIS TEXT"` language from `agents/scribe.py`** (lines 43–45 in `write_listing`, lines 122–126 in `revise_listing`). Replace with gentler guidance that still signals the quote's importance but doesn't prevent the LLM from reformatting it cleanly:

```python
f'  "bookmark_quote": "Print exactly this Librarian-verified quote on the bookmark face '
f'(do not paraphrase, but you may adjust punctuation for readability): {verified_quote}",\n'
```

#### Acceptance criteria

- Reviewer JSON now contains `image_fit` and `quote_quality` fields on every pipeline run
- A run where `image_fit < 5` triggers re-generation of the image before text revision
- A run where `quote_quality < 7` triggers a new consultation pass to get a better quote
- The bookmark quote in the final listing is not locked to a bad quote just because the Librarian found one

#### Verification (Sheraj runs this)

1. Run a pipeline. After it completes, check the score card in the dashboard — it should now show `image_fit` and `quote_quality` alongside the principle scores.
2. (To test the image re-run branch): This requires a genuinely bad image. Not worth forcing artificially — confirm the logic is wired by reading the code, and trust it when a real bad image comes up.

---

### Phase 3 — Etsy Publishing Activation

#### Current state (more complete than v2 described)

**Already built in `agents/api.py`:**
- `GET /etsy/oauth/start` — full OAuth initiation with setup instructions page
- `GET /etsy/oauth/callback` — token exchange, stores tokens
- `GET /etsy/status` — shows configured/authorised state
- `POST /etsy/publish` — calls `publish_draft_listing(product)` in `agents/etsy.py`, updates `products` table with `etsy_listing_id` and `status="draft_on_etsy"`

**What's missing:**
1. `ETSY_CLIENT_ID`, `ETSY_CLIENT_SECRET`, `ETSY_SHOP_ID` in `.env` — Sheraj must fill these
2. Sheraj must visit `localhost:8765/etsy/oauth/start` once in a browser to complete OAuth
3. A "Publish to Etsy" button in the dashboard's Products Gallery (currently no UI trigger for `/etsy/publish`)
4. `publish_draft_listing()` in `agents/etsy.py` needs to be verified/implemented — confirm it handles image upload and listing creation against Etsy Open API v3

#### Credential setup steps (Sheraj does this first)

1. Go to [etsy.com/developers/your-apps](https://www.etsy.com/developers/your-apps) and create an app named "bahAI Workforce"
2. Set callback URL to: `http://localhost:8765/etsy/oauth/callback`
3. Copy the **Keystring** into `.env` as `ETSY_CLIENT_ID`
4. Copy the **Shared Secret** into `.env` as `ETSY_CLIENT_SECRET`
5. Find your **Shop ID**: go to your Etsy Shop manager, look at the URL — it will contain a numeric ID, or check `etsy.com/shop/PeaceAntz` → About → it appears in the URL
6. Add your numeric shop ID as `ETSY_SHOP_ID` in `.env`
7. Visit `http://localhost:8765/etsy/oauth/start` in your browser — it redirects to Etsy for one-time approval, then back to the callback. You'll see "Etsy connected!" if it worked.
8. Confirm at `http://localhost:8765/etsy/status` — should show `{"configured": true, "authorised": true}`

#### Required coding changes

**A. Verify `agents/etsy.py` implements `publish_draft_listing(product: dict)`** — confirm it:
- Creates a draft listing via Etsy Open API v3 `POST /v3/application/shops/{shop_id}/listings`
- Uploads the front bookmark image if `front_image_path` is available
- Returns `{"listing_id": ..., "state": "draft", "url": ..., "image_uploaded": bool}`
- If not fully implemented, complete it using Etsy Open API v3 (no third-party SDK needed)

**B. Add "Publish to Etsy" button in `dashboard/src/components/ProductsGallery.tsx`** (or wherever products are displayed):
- Show only for products where `etsy_listing_id` is null and `status != "draft_on_etsy"`
- On click: `POST /etsy/publish` with `{product_id}`
- On success: show listing URL; update product card to show "Listed on Etsy" state
- On error: show the error message from the API

**C. Add Etsy status indicator to `dashboard/src/components/SettingsPanel.tsx`** (or equivalent):
- Fetches `GET /etsy/status`
- Shows "Etsy: connected / not connected" so Sheraj can see OAuth state at a glance

#### Acceptance criteria

- `GET /etsy/status` returns `{"configured": true, "authorised": true}` after OAuth
- Running a pipeline then clicking "Publish to Etsy" in the dashboard creates a draft listing in PeaceAntz
- The draft appears in Etsy Shop Manager as a draft (not live) — Sheraj activates it himself
- The product in the dashboard shows the Etsy listing ID and a link to the draft

#### Verification (Sheraj runs this)

1. Complete the credential setup steps above
2. Run one full pipeline
3. In the Products Gallery, click "Publish to Etsy" on the completed product
4. Log into your Etsy account → Shop Manager → Listings → Drafts — the listing should appear there
5. Confirm it has the correct title, description, and front bookmark image attached

---

## 5. How to Deliver Changes

1. **Propose before building.** For each phase: describe what you're going to change (files, specific lines or functions), what the acceptance criteria are, and what Sheraj should do to verify. Wait for "approved, proceed."
2. **Build in one session per phase.** Don't split a phase across two sessions unless the phase itself is explicitly broken into sub-phases here.
3. **Test mechanically after every file change.** After editing Python files, the API hot-reloads. Confirm there are no import errors by checking `GET http://localhost:8765/health` returns `{"status": "ok"}`.
4. **Don't touch the `.env` file.** Sheraj fills credentials manually.
5. **Don't commit `.env`** under any circumstances. Confirm it's in `.gitignore` (it should already be) before any `git add`.
6. **Don't introduce new dependencies** without asking. Any `pip install` or `npm install` must be proposed first.
7. **Leave working things alone.** The pipeline, consultation, compositor, and dashboard UI are all working. Phase changes should be surgical — only touch files named in the required changes.

---

## 6. Design Principles

### For this codebase
- No abstraction beyond what the current task needs. Three similar lines is better than a premature helper.
- Error handling only at actual failure boundaries — not for scenarios that can't happen.
- No comments explaining what code does. Only add a comment when the *why* is non-obvious.
- The trust system is the integrity backbone. Every improvement to it makes the system more honest about what it's earned. Don't skip trust signal wiring to save lines.

### For the DAO vision
- Every agent should earn its autonomy through demonstrated clean runs — not have it assumed.
- New product lines will be added to this system. Build nothing that assumes bookmarks are the only product.
- The Reviewer's job is to be the hardest critic in the room. A system where everything scores 8.5 is a system that's lying to itself.
- Bahá'í citations are not decoration — they are the values operating system. Treat them with the same rigor the Librarian does.

---

## 7. Reference Materials

### SQLite schema (`agents/state.py:35–81`)

```sql
-- Agent trust state
agents (
    name TEXT PRIMARY KEY,
    trust_level INTEGER DEFAULT 0,       -- 0..3 (Shadow → Bounded autonomy)
    trust_score REAL DEFAULT 50.0,       -- 0..100 = clean_runs/total_runs * 100
    total_runs INTEGER DEFAULT 0,
    clean_runs INTEGER DEFAULT 0,
    consecutive_failures INTEGER DEFAULT 0
)

-- Tasks (one per pipeline run)
tasks (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    directive TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    assigned_to TEXT,
    created_at TEXT,
    completed_at TEXT,
    card_json TEXT
)

-- Per-agent-step log (trust source of truth)
task_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    step TEXT NOT NULL,
    input_summary TEXT,
    output_summary TEXT,
    passed_review INTEGER,        -- NULL = not evaluated; 1 = pass; 0 = fail
    reviewer_scores TEXT,
    timestamp TEXT
)

-- Completed bookmark products
products (
    id TEXT PRIMARY KEY,
    task_id TEXT,
    title TEXT,
    status TEXT DEFAULT 'draft',
    etsy_listing_id TEXT,
    image_url TEXT,
    listing_copy TEXT,
    reviewer_scores TEXT,
    revenue REAL DEFAULT 0.0,
    created_at TEXT,
    image_prompt TEXT,
    theme TEXT
)
```

### Trust level thresholds (`agents/state.py:183–188`)

```python
# Advances: total_runs >= 5 AND trust_score >= 80 → level += 1
# Regresses: consecutive_failures >= 2 → level -= 1

TRUST_LEVELS = {
    0: "Shadow/Advisory",
    1: "Approval-gated",
    2: "Human-on-the-loop",
    3: "Bounded autonomy",
}
```

### Key thresholds across the codebase

| Threshold | Value | Location | Meaning |
|---|---|---|---|
| `PASS_THRESHOLD` | `6.0` (→ raise to `7.0` in Phase 2B) | `agents/reviewer.py:16` | Minimum average to pass review |
| `target_score` | `9.0` (default) | `WriteApproveRequest` in `agents/api.py:449` | Score to aim for before stopping revision |
| `max_attempts` | `3` (default) | same | Max revision rounds |
| `image_fit < 5` | 5 | Phase 2C design | Trigger image regeneration |
| `quote_quality < 7` | 7 | Phase 2C design | Trigger consultation re-run for new quote |
| Trust advance: clean rate | 80% | `agents/state.py:187` | Minimum clean/total to advance a level |
| Trust advance: min runs | 5 | `agents/state.py:187` | Minimum total runs before advancement |
| Trust regress: consec failures | 2 | `agents/state.py:185` | Consecutive failures to drop a level |

### Agent names (canonical — use these exactly in `log_run()` calls)

Current `AGENT_NAMES` (Phase 2C adds `"consultation"` and `"compositor"`):
```python
["operator", "librarian", "artist", "scribe", "reviewer", "producer", "steward"]
```

### Reviewer output schema (post-Phase-2C)

```json
{
  "scores": {
    "1_work_as_worship":   {"score": 7, "note": "..."},
    "2_fruit_not_words":   {"score": 6, "note": "..."},
    "3_trustworthiness":   {"score": 8, "note": "..."},
    "4_consultation":      {"score": 7, "note": "..."},
    "5_moderation":        {"score": 6, "note": "..."},
    "6_deeds_over_words":  {"score": 7, "note": "..."},
    "7_craft_in_service":  {"score": 8, "note": "..."},
    "8_<new_principle>":   {"score": 7, "note": "..."},
    "9_<new_principle>":   {"score": 6, "note": "..."}
  },
  "overall": 6.9,
  "image_fit": 7,
  "quote_quality": 8,
  "passed": false,
  "recommendation": "Revise description to..."
}
```

---

## CHANGELOG: v2 → v3

### Discrepancies corrected from v2

1. **Grok model string was wrong.** v2 stated `grok-4.3` as the model. `agents/router.py:24` shows the code default is `grok-2-1212`; actual value is `XAI_MODEL` read from `.env` at runtime. v3 refers to `XAI_MODEL` env var and does not state a hardcoded model name.

2. **Etsy integration significantly more built than v2 described.** v2 listed "implement `create_draft_listing()`" as a TODO. Actually `agents/api.py:1039–1084` already has `/etsy/publish` calling `publish_draft_listing()` from `agents/etsy.py`, plus full OAuth flow at `/etsy/oauth/start` and `/etsy/oauth/callback`. Phase 3 is now correctly described as "activation" (credential setup + UI button), not "initial implementation."

3. **Steward already built.** v2 listed "Steward agent" as future work. `agents/api.py:1088–1115` already has `/steward/report` with `ESTIMATED_COST_PER_PRODUCT = 0.06`. Removed from the "to build" list.

4. **`consultation` and `compositor` not in `AGENT_NAMES`.** v2 didn't flag this. Phase 2A now includes adding them to `AGENT_NAMES` in `agents/state.py` so their trust rows exist in the DB.

5. **`revise_listing()` docstring mismatch.** `agents/scribe.py:99` says "Called automatically on borderline scores (6–7 range)" but the actual trigger in `_pipeline_write_approve_sync` is `overall < 9.0`. The docstring is misleading. Noted; Fable 5 should fix the docstring in Phase 2B when touching the file.

6. **`producer` in AGENT_NAMES has no trust signal.** v2 didn't note this. No `log_run()` call in the main pipeline uses `agent="producer"`. Its trust data will remain frozen unless a producer step is wired. Noted as a low-priority known gap — not in scope for these phases.

### What changed structurally in v3

1. All three phases now have concrete acceptance criteria and numbered verification steps using copy-pasteable SQLite commands or named dashboard panels — v2 had only descriptions.
2. Each phase has a root cause analysis (code file + line number), not just a description of symptoms.
3. The Phase 2 "smarter revision loop" is now Phase 2C (a sub-phase of Phase 2), clearly separated from the trust fix (2A) and calibration (2B).
4. Phase 3 has a step-by-step credential setup procedure for Sheraj.
5. A full SQLite schema is included in Section 7 so Fable 5 never needs to re-read `state.py` to know table structure.
6. The recommended phase order is explicit with reasoning (v2 said "Fable 5 decides" with no guidance).
