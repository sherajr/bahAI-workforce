# Rules 1-14, 29, 111 — the product pipelines

Canonical numbered hard rules for this subsystem. **Numbers are permanent — never
renumber, only append.** Cited from code comments across the repo.
Loaded on demand from the root `AGENTS.md` routing table; do not copy these
into CLAUDE.md or the root AGENTS.md.

## Rules 1–14 — the product pipelines

1. **Qwen has tight context.** Anything routed to Ollama (`router.py`:
   everything NOT in `GROK_TASK_TYPES`) must get lean prompts. Long prompts made
   Qwen burn its whole token budget thinking and return `{`.
2. **`bookmark_quote` is locked.** `apply_edits` rejects edits to it and must
   report them as `rejected_locked`, never silently drop them.
3. **Revision is forward-chaining.** The loop revises `cur_listing` with
   `cur_review` (the latest), tracks `best_*` separately, adopts ties (newer
   listing wins), and only counts strict regressions toward the 2-strike stall.
4. **Every revision path must end in `_sanitize_claims`.** It deterministically
   strips false claims (handcrafted, exact motif counts). LLM compliance is
   never trusted for honesty-critical text.
5. **Reviewer JSON can truncate at the token ceiling.** `_parse_review` tracks
   whether `_repair_truncated_json` fired and drops the last `edits` element if
   so. Keep `edits` early in the schema field order.
6. **Consultation scripture stays hand-curated.** `CONSULTATION_SCRIPTURE` in
   `consultation.py` maps each consultation moment to one short cited excerpt
   (≤40 words). No vector DB for this, per explicit owner decision.
7. **The consultation's round-2 decision is binding.** `reviewer.score()`
   receives `consultation_decision`; overrides must be named
   "REOPENING team decision: ..." — never silently contradicted.
8. **Translation disclaimers AND AI-artwork disclosures are code-appended,
   never LLM-written.** A card translation is always labelled
   AI-assisted/unofficial: fixed strings in `translator.LANGUAGES`, printed by
   the compositor and stored in metadata. Artwork provenance is disclosed the
   same way — `etsy.AI_ART_DISCLOSURE` appended in code to every published
   listing, `api.CARD_ART_DISCLOSURE` stored in every card's metadata, and the
   Scribe's `_HONESTY_RULES` forbid implying hand illustration. Same class as
   `_sanitize_claims`.
9. **A new card language ships only after a human-viewed render.** PIL draws
   missing glyphs as tofu without erroring, and unshaped Arabic renders as
   disjointed LTR letters — `card_compositor` shapes RTL per line with
   arabic-reshaper + python-bidi, and every font in `LANGUAGES.font_paths` was
   verified by eye. Adding a language = config entry + a viewed sample PNG.
10. **Product-specific consultation wording goes through `_PRODUCT_FRAMES`,
    never inline in the shared prompt bodies.** Two sub-invariants are
    machine-critical: the Librarian's round-turn
    VERDICT/VERIFIED QUOTE/SOURCE/REASONING block is parsed
    (`_parse_verdict_grounded` + the quote-extraction loop) and must stay exact,
    and the revision-consult decision JSON contract ({action, action_guidance,
    team_note} + the REOPENING literals) is executed. The dashboard renders the
    Librarian's block as a friendly verification card
    (`ConsultationTranscript.tsx`) — humanize the display there, never her
    output format. (The turns themselves were restyled to natural first-person
    sentences in 2026-07 at Sheraj's request: an owner decision, not drift.)
11. **Quote cards quote only the sources SELECTED for that run — default: Ruhi
    Book 1 alone.** (Widened from "Ruhi Book 1 only, ever" by owner decision
    2026-08-04; the discipline survives as tiered trust in
    `api._parse_card_sources` / `_card_retrieve` / `_resolve_pinned_quote_multi`.)
    - **Default unchanged:** no `sources` on the request = Ruhi Book 1 only, via
      `retrieve_ruhi_book1()` + the SHA-manifest gates, byte-for-byte the old
      behaviour.
    - **`lib:<slug>` (verified tier):** one of the ingested 7-text `bahai_texts`
      chunks. The printed text must be a boundary-honest verbatim span of an
      indexed chunk (`_find_verbatim_span` + `_span_boundary_ok` +
      `_assert_excerpt_of`): sentence-clean start (overlap chunking can open a
      chunk mid-sentence, and a fragment must never print as if complete),
      sentence-punctuation end, elision marks for any early stop.
      `quote_verified: true`.
    - **`web:<url>` (RISKY tier, explicitly opt-in):** prints as supplied, is
      NEVER grounded, and carries `quote_verified: false` +
      `quote_provenance: "web:<url>"` everywhere (the dashboard badges it; the
      code-owned per-tier strings in `consultation.py` never claim
      verification). Web-only runs require a pinned quote; requote never draws
      from the web.
    - **Never widen silently:** an empty result within the SELECTED sources
      raises — no fallback to unselected texts, the general index, or the
      Librarian's memory. She never free-associates a quote.
12. **The bookmark quote's GROUNDED verdict is deterministically re-checked.**
    `api._check_quote_grounding` (word-overlap against the retrieved citations,
    or `librarian.verify()` when retrieval was empty) gates `quote_grounded`
    before the quote is locked — never reintroduce trust in the consultation
    Librarian's self-report alone. Unverifiable demotes to ungrounded; the
    demotion is logged and appended to the transcript.
13. **The Etsy price is policy-set, never parsed from LLM prose.**
    `etsy.BOOKMARK_PRICE` (env `ETSY_BOOKMARK_PRICE`) is the only price source;
    the Scribe's `price_note` is a display-only suggestion.
14. **`log_run(passed_review=...)` moves agent trust — only pass it for JUDGED
    outcomes** (a review verdict, or a deterministic check like the translator's
    script check or the grounding check). Mechanical success ("the API call
    returned a file") stays `None`, or clean-run stats become an uptime metric.
    Trust has a real consequence: `/etsy/publish` requires Reviewer trust level
    ≥ 2 or an explicit `confirm=true`.

### The visual layout editor (both product types)

`agents/layout.py` is the single source of truth for every adjustable
presentation knob (font, text size/position, colour, gradient/vignette,
star/rule toggles) plus `sanitize()`, the boundary that clamps an untrusted
layout dict from the dashboard to safe ranges and drops unknown keys.
`compositor.render_bookmark_pair` / `card_compositor.render_quote_card` take an
optional `layout` (defaults reproduce the pre-editor render byte-for-byte) and
an optional `dest_stem` (the live preview reuses one file pair per product
instead of accumulating). Endpoints: `GET /products/{id}/layout`,
`POST .../layout/preview` (render, no save), `POST .../layout` (render +
persist to `front_image`/`back_image`/`layout_json`). UI: `LayoutEditor.tsx` in
the product drawer.

**Load-bearing invariant: the editor NEVER carries text.** The printed quote,
citation, translation and code-appended disclaimers are all read from the
product's stored data at render time inside `_render_product_faces` — a layout
request has no field that can reach them, so rules 2/8/9/11/12 hold by
construction, and a card's translation/disclaimer keep their script-verified
fonts (`_load_font`'s `override_paths` is only ever passed for the Latin English
quote + citation). Layout edits are mechanical (`log_run(..., passed_review=None)`,
rule 14) and never touch the review score.

**Manual `PATCH /products/{id}` (edit_product) is a deliberate owner override,
not a rule-2 violation.** The quote stays locked to every *agent/pipeline* path
(`apply_edits`); a human hand-edit is allowed but not free: `_sanitize_claims`
still runs on the edited marketing text, and changing `bookmark_quote` sets
`quote_verified=false` (the dashboard shows a "no longer verified" note) and
re-renders the printed face so the image can't silently disagree with the words.
Don't "restore" rule 2 by blocking it — the flag is the intended design.

**The roster's display names and avatars are dashboard-only.**
`dashboard/src/lib/utils.ts`'s `ROSTER` maps each stable backend id
(`librarian`, `artist`, `scribe`, `reviewer`, `steward`, `translator`,
`secretary`) to a friendly name (Ruth, Theo, Clara, Amos, Nora, Sofia, Abigail),
role label and avatar. Display layer only — the backend keys everything on the
lowercase ids (`state.AGENT_NAMES`, `log_run`, consultation turn labels), so
trust history and logs are unaffected. Avatars live in
`dashboard/public/roster/` (gitignored, private); `RosterAvatar` falls back to
an initial when absent.


## Rule 29 — the quote-card format

29. **The quote is ALWAYS on the front and NEVER on the back** (redesign
    2026-07-16, owner spec). The front is readability-first: Tahoma dark ink on
    a light wash of the heavily blurred artwork, thin gold border, and
    `card_compositor.MIN_QUOTE_PX` as a hard floor — a quote that can't fit at
    the readable minimum FAILS the render, never shrinks below it. The back is a
    reflection face (question + gentle call-to-action + ruled writing lines +
    share line); the share line and the per-language reflection DEFAULTS are
    code-owned fixed strings (same class as rule 8 — the quote-inspired
    question/action may be LLM-written via `api._card_reflection`, but never the
    share line or disclaimers). Translated runs render SEPARATE per-language
    card pairs, never English-front/translation-back: the variant card carries
    the translated quote + its code-appended disclaimer on ITS front, stored in
    `card_copy.variant_faces`. Tahoma is used only where it safely covers the
    script — languages with verified `font_paths` in `translator.LANGUAGES`
    (zh, ar) keep them, and rule 9 still applies to any new language.


## Rule 111 — exact quotation verification

111. **A "verified" quotation is the source's own words, in the source's own
     order, with an honest beginning and end, under the source's own
     attribution.** `agents/quote_verify.py` decides it and every
     machine-generated path that may apply the label goes through it. Verify with
     `scripts/test_quote_verify.py`.

     What it replaced was word overlap at 60% of distinct content words. That is
     a similarity score, and similarity is not quotation. Measured on the code
     before the change, with an INVENTED sentence (nothing in that suite is
     scripture, and the trusted corpus is never opened or edited):

         Source:    The group must not publish confidential meeting notes.
         Candidate: The group must publish confidential meeting notes.

     — returned **verified, "100% of content words traceable"**. So did the same
     words reordered. `not` is three letters and sat in the stop-word list, along
     with `no` and `all`; the lesson is not "fix the list" but that a bag of
     words cannot decide this, because the words carrying the meaning are often
     the shortest ones. The retired list is left in `api.py` as a comment rather
     than deleted, so the next person can see why.
     - **Contiguous, in order, whole words.** A deleted negation breaks the run;
       so does a reordering.
     - **Honest boundaries.** A contiguous substring is not enough: starting one
       word after a negation turns a prohibition into an instruction while every
       word remains genuine. An excerpt begins at a sentence start and ends at
       sentence punctuation, an early stop must carry an elision mark, and a
       passage that itself opens mid-sentence (overlap chunking does this) can
       never print as a complete quotation.
     - **Attribution is part of it.** Correct words under an invented author are
       not verified, and the failure names where the words actually came from.
     - **What is printed is the CORPUS's characters**, never the candidate's, so
       a diacritic or curly apostrophe retyped by a model cannot reach the card.
       Matching folds those per word, so a correct quotation is not rejected for
       a keyboard difference.
     - **Unverifiable is reported as unverifiable.** No citations means no
       verification; the old code fell back to `librarian.verify()`'s embedding
       score and let a close match print as verified, which is the same mistake
       one layer down.
     - **A failure offers a way forward** (`eligible_excerpt`): a real,
       exactly-verifiable passage from the same retrieval, offered for review and
       never substituted.
     - **The method is stored** on the product (`quote_verification`), so an old
       overlap pass can never be read as though it had met today's standard, and
       a later regeneration cannot lean on a verified flag whose method is
       unknown. Historical products are NOT rewritten and their renders are not
       touched.

