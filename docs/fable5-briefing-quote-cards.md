# bahAI Workforce — Briefing: Quote Cards (new product line)

**To Fable 5:** this is a new pipeline, parallel to the existing bookmark
pipeline, not a modification of it. Read `README.md` and `docs/ARCHITECTURE.md`
first — this briefing assumes you already know the agent roster (Librarian,
Artist, Scribe, Reviewer, Consultation, Compositor) and the constitution's 9
principles. Follow this repo's established discipline: propose your approach
for anything genuinely ambiguous below before implementing it, and verify
every piece live (real agent calls, real rendered images) rather than trusting
that code "looks right" — that pattern is why this codebase's bug count is low.

## What this is

A second, independent product: small **Quote Cards**, 3.5" wide × 2" tall
(landscape, standard business-card size), meant to be **given away, not
sold** — a teaching/outreach tool for sharing a Bahá'í quote with someone
unfamiliar with the Faith. No Etsy listing, no pricing, no product copy.
The deliverable is just: a verified quote, beautiful representative artwork,
and (optionally) a translation — rendered as a print-ready front/back PNG
pair, downloadable from the dashboard.

Card faces:
- **Front:** a soft vignette treatment of the artwork with the quote
  centered — English always; if a translation was requested, the translated
  text appears alongside it (English quote, then the translation beneath it,
  each legible on its own). Small citation text (author/work) below.
- **Back:** clean, full representative artwork — no text, just beauty.
  (Same spirit as the bookmark's back face, adapted to the new dimensions.)

## Why this matters (carry this into every design decision)

The whole point is accessibility to someone who has never encountered the
Faith. That should shape the Artist's prompt (welcoming, not esoteric or
jargon-dependent — someone with zero background should still find the image
beautiful and inviting) and the Reviewer's judgment (does this actually work
as a first introduction, not just as devotional art for an existing believer).

## Required new pieces

### 1. Translation

New capability — translate the Librarian-verified English quote into a
requested language. Needed languages: **Spanish, Mandarin, Arabic**, and
design this so adding more later (French, Persian/Farsi, Portuguese, etc.) is
just a config addition, not new code. Suggest a `LANGUAGES` dict:
`{code: {name, native_name, rtl: bool, font_path}}`.

**Non-negotiable honesty requirement, tied directly to this project's
Trustworthiness and Independent Investigation of Truth principles (3 and 9):**
an LLM-generated translation of scripture is NOT an official rendering. It
must be visibly labeled as an AI-assisted translation — small print on the
card or at minimum in the stored metadata — and never presented as if it
were an authorized translation. Do not skip this; it's the same class of
honesty discipline that already governs quote-grounding and motif-count
claims elsewhere in this codebase (see `_sanitize_claims` in `scribe.py` and
the `quote_grounded` framing in `consultation.py` for the existing pattern to
follow).

**Arabic (and any future RTL language) is a real rendering problem, not just
a translation problem.** PIL does not shape Arabic script or handle
right-to-left layout on its own — you will likely need `arabic-reshaper` and
`python-bidi` (or equivalent) to get correctly-joined, correctly-ordered
glyphs, plus a font that actually contains Arabic glyphs (the bookmark
compositor's current font list — Palatino, Georgia, Times — will render
Arabic as empty boxes ("tofu"). Same concern for Mandarin: none of the
existing fonts contain CJK glyphs; you'll need something like Microsoft
YaHei or a bundled Noto CJK font. **Verify every new language by actually
looking at the rendered PNG, not by trusting that the code ran without an
exception** — a silently-broken card (tofu boxes) is a shipped bug, not a
missing feature.

Where to route the translation call: this is a "creative_writing"-adjacent
task needing nuance, so it should go through the paid Grok path
(`agents/router.py`'s `GROK_TASK_TYPES`), not the local model.

### 2. Card Compositor

A new render function — either a new `render_quote_card()` in
`agents/compositor.py` or a new sibling module (your call) — producing a
3.5"×2" front/back pair at true 300dpi (1050×600px per face; see the recent
print-resolution fix in `compositor.py` for why real pixel dimensions matter,
not just DPI metadata).

- Front needs the same kind of legibility treatment the bookmark's
  `_composite_front` already does (gradient/vignette so text is readable
  over a busy image), but centered rather than bottom-anchored, and sized to
  fit both the English quote and an optional translation without crowding a
  card this small. Decide whether the source artwork should be one landscape
  image split front/back (like the bookmark's crop-based approach) or two
  separate Artist generations (front background + back art) — the bookmark's
  quarter-crop technique was designed for a tall 2:3 image and may not suit
  this aspect ratio well; use your judgment and verify visually either way.
- Back: full clean artwork, matching/complementing the front.

### 3. Pipeline flow

Mirror `_generate_bookmark`'s shape, not its content:
theme (+ optional target language) → Librarian retrieves + verifies a quote
(unchanged — still English-source verification via the existing vector
index) → optional translation step → Artist builds a card-appropriate image
prompt (see accessibility note above) and generates art → consultation (see
below) → Reviewer scores → Card Compositor renders → save.

**Consultation:** reuse `run_consultation()` if reasonably possible rather
than forking a new one — its structure (Artist describes, Scribe proposes
tone/quote, Reviewer challenges, Librarian verifies) is still valuable even
without a listing to write, since it still settles the artwork direction and
confirms the quote. The Scribe's "tone" turn just informs card styling
instead of prose. If reuse turns out to be awkward in practice, a lighter
card-specific consultation is fine — your call, but justify it if you fork
one rather than defaulting to a fork out of convenience.

**Reviewer:** don't force this into the 9-principle Etsy-listing schema —
there's no listing. Add a lighter, purpose-built scoring function (e.g.
`score_quote_card()` in `reviewer.py`) covering: quote/citation accuracy,
translation honesty and quality, artwork fit AND accessibility to someone
new to the Faith, and print legibility of the front face. Keep the same
1-10 calibrated scale and "Fix:" note discipline the existing Reviewer uses
— consistency matters more than novelty here.

**Revision loop:** much simpler than the bookmark's, since there's no prose
to mechanically edit. If the Reviewer flags a weak fit, the lever is
re-picking the citation or regenerating the art, not text edits. Don't
port over `apply_edits`/`revise_listing_light` machinery that has nothing to
act on.

### 4. Storage

Prefer adding a `product_type` column to the existing `products` table
(default `'bookmark'` for all current rows) over a parallel table — far less
duplication, and the dashboard's existing product-gallery infrastructure
(list/get/manual-edit/download patterns) can be reused with type-aware
rendering rather than rebuilt. New columns as needed: `language`,
`translation_text`, `translation_disclaimer` (or fold these into a JSON blob
column, your call — follow whatever pattern is more consistent with how
`listing_copy` already works).

### 5. Dashboard

- Pipeline entry: theme input, a language dropdown (English is always
  included; optional second language selection triggers the translation
  step), and a clear indicator that this is the "Quote Cards" pipeline, not
  the bookmark one — could be a toggle, a separate sub-tab, or a `kind`
  parameter on the existing Pipeline tab; your call.
- Product display: the existing `ProductCard`/`DrawerImage` layout assumes
  portrait 1:3 bookmarks — a 3.5"×2" landscape card needs its own layout
  treatment, not a stretched bookmark card.

## What NOT to do

- No Etsy integration, no pricing, no "materials/price_note" fields — this
  product doesn't get sold.
- No forcing an official-translation claim onto an AI translation, ever.
- No shipping a language without actually looking at a rendered sample of it
  first.
- Don't duplicate the bookmark's honesty/anti-fabrication machinery
  wholesale if it doesn't apply here (e.g. the motif-count scrubbing is
  listing-text-specific and this product has no listing text) — but DO
  carry over the underlying principle (never assert what wasn't verified)
  wherever it's actually relevant (translation honesty, quote grounding).

## Acceptance criteria

- A theme with no language selected produces an English-only card: verified
  quote, artwork front (vignette) and back, at true 3.5"×2" 300dpi.
- A theme with e.g. Spanish selected produces the same card with both
  English and Spanish text on the front, Spanish visibly labeled as an
  AI-assisted translation.
- The same works for Mandarin and Arabic, with correctly-shaped,
  correctly-directioned glyphs — verified by actually viewing the rendered
  PNG for each, not just confirming no exception was thrown.
- Reviewer output for a card includes a genuine accessibility/newcomer-fit
  judgment, not just a repurposed Etsy-listing rubric.
- Existing bookmark pipeline is completely unaffected — same endpoints,
  same behavior, same tests passing.
