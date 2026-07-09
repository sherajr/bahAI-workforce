# Improvement Plan — Visual Editing, Agent Roster & Consultation Audit

**Date:** 2026-07-08 · **Status: PROPOSAL — nothing here is built yet. Waiting for Sheraj's go-ahead.**

This document is written for Sheraj to read and approve. It covers three things:

1. **Part 1** — How to let you visually adjust bookmarks and quote cards (move text, change fonts, tweak layout) without breaking the honesty safeguards.
2. **Part 2** — A full audit of every agent on the team: what they actually do, whether their access matches their job, names and faces for each of them (like we did for Abigail), and a health check of the consultation logic.
3. **Open questions** — the handful of decisions only you can make, listed at the end.

---

## Part 1 — Visual editing for bookmarks and quote cards

### What exists today

Right now the layout of every bookmark and card is **fixed in code**: the Compositor always puts the quote in the same place, in the same font, with the same dark gradient and gold star. Your only editing power is text-only (the "manual edit" on the Products tab), and that has no visual control at all. The Canva connection we have only does one thing: push the finished front image into a brand template — it's a delivery step, not an editor.

### What Canva's API can and can't do (checked against their current docs, July 2026)

- ✅ **Autofill** (what we use today): create a new design from a brand template, filling in image *and text* slots. Generally available.
- ✅ **Editor round-trip ("return navigation")**: our app can open a specific design in Canva's real editor, you edit it there with all of Canva's tools, press **Return**, and land back in our dashboard. Generally available.
- ✅ **Export**: after you edit, we can pull the finished design back as a print-quality PNG/PDF automatically.
- ❌ **Server-side editing**: our code cannot reach into a Canva design and move/restyle elements itself from the backend. Canva's "Design Editing API" exists but only for apps that run *inside* Canva — not for our kind of integration. So a fully in-dashboard editor that secretly drives Canva isn't possible today.

### The three options

| Option | What it is | Verdict |
|---|---|---|
| **A. Build our own editor** | A drag-and-drop canvas inside our dashboard (a big custom build) | ❌ Months of work, and we'd have to rebuild things Canva already does perfectly (fonts, alignment, Arabic text). Too much for what we need. |
| **B. Canva round-trip** | "Edit in Canva" button → real Canva editor → Return → we save the result | ✅ Great for bookmarks. You already know Canva. But risky for quote cards (explained below). |
| **C. Layout controls** | Simple controls in our dashboard (font, text position, size, gradient strength, star on/off) with instant preview — our own Compositor re-renders | ✅ Safe for both products. Covers the most common adjustments. Not full freedom. |

### Recommendation: C first, then B for bookmarks only

**Phase 1 — Layout controls (both products).** A "Adjust layout" panel on each product: pick from a short list of pre-approved fonts, nudge the quote block up/down, change its size, lighten/darken the gradient, toggle the gold star. Every change instantly re-renders a preview using our existing Compositor (free, no AI involved). The quote text itself **is not an input field** — it always comes from the locked, verified quote in the database, so there is literally no way to retype it. This keeps every existing safeguard automatically:

- The locked bookmark quote stays locked (hard rule 2).
- Card quotes stay verbatim Ruhi Book 1 (rules 11–12) — the editor can't touch the words.
- Translation and AI-artwork disclaimers stay printed by code (rule 8).
- Fonts stay on the eye-verified list, so Arabic/Chinese never silently break (rule 9).

**Phase 2 — "Edit in Canva" for bookmarks only.** For when the layout controls aren't enough: we create a Canva design pre-filled with the artwork and the locked quote, you edit freely in Canva, press Return, and we pull back the print-ready file. One honesty step on return: the dashboard shows the returned design next to the locked quote and asks you to confirm the quote text wasn't changed before it saves. (Canva can't stop anyone editing text inside its editor, so this confirmation — plus keeping the database's locked quote as the source of truth for the Etsy listing — is how we keep rule 2 honest.)

**Why not Canva for quote cards:** the card's front face must carry the printed translation disclaimer (rule 8), correctly shaped Arabic (rule 9), and a verbatim Book 1 quote (rule 11). In Canva all of those become freely editable text with no guard — three hard rules exposed at once. The layout controls give cards the flexibility they need without that risk. We can revisit later if Phase 1 feels too limited.

### A gap we found while auditing (needs your decision)

The existing manual-edit feature (Products tab) **already** lets the quote be rewritten: `bookmark_quote` is one of its editable fields, and manual edits also skip the automatic honesty scrub (the one that strips "handcrafted" etc.). This predates this plan — the visual editor didn't create the hole, it just made us look. Proposed fix: manual edits keep working for title/description/tags, but (a) they run through the same honesty scrub as everything else, and (b) editing the quote either gets removed from that form or visibly marks the product's quote as "no longer verified" on the dashboard. Your call which — see Open Questions.

---

## Part 2 — The agent roster, audited

### Who is actually on the team

I confirmed the roster from the database's agent list, every logged pipeline step, and the consultation transcripts — not from the docs. There are **six real personas**, plus labels and tools that look like agents but aren't:

| Agent | What it really does | Brain | Access check |
|---|---|---|---|
| **Librarian** | Finds verified passages (general library for bookmarks, Ruhi Book 1 only for cards), verifies quotes, speaks 4th in consultation | Local Qwen + the text index | ✅ Matches role. Its "verified" claim is never trusted alone — code double-checks (rule 12 confirmed working). |
| **Artist** | Writes image briefs, generates artwork (xAI, paid), describes/critiques images with vision (Grok, paid), speaks 1st in consultation | Local Qwen for briefs; Grok/xAI for eyes and paint | ✅ Matches role. One oddity: the Canva-push step is logged under the Artist's name though it's really a publishing step. |
| **Scribe** | Writes and revises the Etsy listing; applies the Reviewer's edits mechanically; every path ends in the honesty scrub | Local Qwen | ✅ Matches role. Cannot touch the locked quote (rule 2 confirmed: blocked edits are reported, never silently dropped). |
| **Reviewer** | Scores everything (9 principles for bookmarks, 5-criteria card rubric), sees the real rendered product, drives revisions with machine-readable actions | Grok + vision (paid) | ✅ Matches role. Its trust level is what gates Etsy publishing — appropriate that it's the strictest judge. |
| **Translator** | Spanish/Mandarin/Arabic card translations, always machine-checked and always labeled AI-assisted by code | Grok (paid) | ✅ Matches role. |
| **Secretary (Abigail)** | Your personal assistant — calendar, Gmail, Drive, WhatsApp | Claude Sonnet (hers alone) | ✅ Already audited separately. Correctly never appears in the workforce trust/log system (her world stays private). |
| *"Steward"* | The money report (spend, revenue, ceiling). **Pure arithmetic — no AI at all.** | none | Fine as-is. It can wear a name and face on the dashboard, but there's no mind behind it to audit. |
| *"Operator"* | Just a label on task rows ("assigned to: operator"). Never thinks, never acts. | none | Vestigial — recommend retiring the label or leaving as plumbing. Not a persona. |
| *"Producer"* | Appears exactly once: the log entry when a listing is published to Etsy. | none | Vestigial — recommend folding that one log line under Steward (publishing is a business act) and retiring the name. |
| *"Compositor"* | The code that renders PNGs. A tool, not an agent — but it sits in the trust table like one. | none | Keep as a tool. Becomes the engine behind Part 1's layout controls. |

Two things the docs don't mention that the audit surfaced: there's a **fifth pipeline** (posts to X/Twitter for @peaceAntz, human-approved before posting — it reuses the same four consulting personas, so no new agent needed), and the trust table contains rows for "consultation" and "compositor" — a process and a tool accumulating trust points no gate ever reads. Harmless, but worth tidying so the Trust tab only shows real minds.

### Consultation health check

I read the consultation code end-to-end and verified the scripture it stands on:

- **All six curated excerpts are real and correctly attributed.** The clash-of-opinions, freedom-of-expression, and accept-the-better-opinion passages are 'Abdu'l-Bahá's; "transmuteth conjecture into certitude" is Bahá'u'lláh's; the abide-by-the-majority passage is Shoghi Effendi's; "a contribution to the consensus" is 'Abdu'l-Bahá in *The Promulgation of Universal Peace* (p. 72 — note: that book is transcribed talks rather than an authenticated Tablet; standard to cite, just worth knowing). The hand-curated, no-vector-DB approach (rule 6) is intact and remains the right call.
- **The card quote restriction (rule 11) is airtight.** Cards can only retrieve from the Ruhi Book 1 index; an empty result kills the job rather than falling back; and the printed quote is always the *verbatim text of a retrieved passage* — the team's discussion picks which passage, but code, not the model, supplies the words. The requote loop re-searches the same restricted pool only. No leak found.
- **The grounding re-check (rule 12) is airtight.** The bookmark quote's "verified" status is decided by a deterministic word-overlap check (or index verification when retrieval was empty), never the Librarian's self-report. Unverifiable correctly demotes to "not verified" and the Scribe then frames it honestly.
- **One dead entry:** the sixth scripture excerpt ("a contribution to the consensus") is defined but never actually used in any prompt — the Scribe's round-2 prompt paraphrases it instead. Either wire it in or delete it.
- **Stale "two rounds" wording:** the consultation now runs **three** rounds (your pause sits between 2 and 3), but the Reviewer is still told "the team consulted in two rounds," the settled-decision block is labeled "round 2" when it actually summarizes all three rounds plus your guidance, and the architecture doc says cards use a "2-round structure." Wording-only fixes; no logic changes.
- **One soft spot (no change proposed, just naming it):** the Reviewer's round-2 "green-light or hold" is advisory prose — a "hold" doesn't mechanically pause anything; the team's round 3 and the synthesis absorb it. That has worked fine in practice and adding machinery has real cost, so I recommend leaving it, consciously.
- Bookmark vs card paths are consistent where they should be and different only where the products genuinely differ (cards: verbatim-snap + requote/repaint; bookmarks: locked quote + mechanical text edits). The shared prompt frames (rule 10) are intact.

### Small code-hygiene fixes bundled in (no behavior change)

1. A stale comment in the router still claims the Secretary's tool path is "read-only" — it hasn't been since the 2026-07-07 migration. Fix the comment (rule 22 stays the truth).
2. The router lists task types ("copy", "copywriting", "complex_analysis") that nothing uses and that contradict the "Scribe is local" directive if ever used by accident. Remove them.
3. Re-label the Canva-push log entry from "artist" to the publishing persona.
4. Unused role descriptions (operator/producer/steward prompts that are never injected anywhere) get removed or marked as dashboard-display-only.

### Names and faces

Same treatment Abigail got, extended to the workforce. Two naming schemes for you to choose between:

**Scheme A — names from Bahá'í history (recommended).** Each agent is named for an early believer whose actual life's work matches the agent's job. Out of reverence, no names of the Central Figures or the Guardian are used or proposed.

| Agent | Name | Who it honors |
|---|---|---|
| Librarian | **Nabíl** | Nabíl-i-A'ẓam, the Faith's great chronicler — the keeper of verified history |
| Artist | **Mishkín** | Mishkín-Qalam, the celebrated calligrapher among the Apostles of Bahá'u'lláh |
| Scribe | **Varqá** | the poet Varqá — "dove"; words offered in service |
| Reviewer | **Hakím** | Lutfu'lláh Hakím, first Universal House of Justice member; the name itself means "discerning" |
| Steward | **Amín** | Hájí Amín, the trusted Trustee of Huqúqu'lláh — literally "the trustworthy one," who kept the Faith's funds |
| Translator | **Marzieh** | Marzieh Gail, the renowned Bahá'í translator and author |

**Scheme B — everyday names, like Abigail:** e.g. Nora, Theo, Clara, Ruth, Amos, Sofia. Warmer/plainer, and keeps the roster consistent with Abigail's register.

**Avatars — recommend generating them with the Artist's own image pipeline**, not sourcing photos: one consistent illustrated style for all six (Persian-miniature-style portraits in the same jewel tones and gold as the bookmarks), about $0.05 each on the meter, each one shown to you for approval before adoption — same "human-viewed render" discipline as card fonts. Reasons over supplied photos: perfect visual consistency across the set, no licensing questions, and it's fitting that the Artist paints the team. These would be stylized illustrations of no real person. Abigail's photo stays exactly as it is (personal, gitignored) either way.

### How Part 1 fits the roster

The visual editor is deliberately **not a new agent** — no new AI, no new spend. The layout controls are new hands for the existing Compositor tool; the Canva round-trip is a publishing-side feature logged in the Activity Log like every other step (mechanical steps stay out of trust scoring, per rule 14). A layout change never alters review scores or the BEST EFFORT badge — those judge the words and artwork, which layout editing can't touch. If you later manually confirm a Canva-edited bookmark, that confirmation is logged with your name on it, same as pipeline approvals.

---

## Suggested build order (after your go-ahead)

1. **Quick fixes** (an afternoon): manual-edit honesty scrub + quote-field decision, stale wording/comments, log relabels, dead config removal.
2. **Naming & avatars**: rename on the dashboard (internal IDs stay the same so history/trust carry over), generate the six portraits, you approve each.
3. **Phase 1 layout controls**: fonts/position/size/gradient/star with live preview, both products.
4. **Phase 2 Canva round-trip**: bookmarks only, with the return-confirmation step.

Each step is independently shippable; you can stop after any of them.

## Open questions for Sheraj

1. **Manual quote edits** (existing gap): remove the quote field from manual editing entirely, or keep it but mark the product "quote no longer verified" when used?
2. **Naming scheme**: historical names (Scheme A), everyday names (Scheme B), or a mix?
3. **Avatars**: OK to generate them with the Artist's pipeline as proposed? And should they be **public** (committed to the repo — they're brand art, nothing personal) or **private** like Abigail's photo?
4. **Part 1 scope**: agree to layout-controls-first, Canva-for-bookmarks-second? Or would you rather start with the Canva round-trip?
