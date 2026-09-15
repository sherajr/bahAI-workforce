# Rules 117-120 — a gathering, and a place to start

Canonical numbered hard rules for this subsystem. **Numbers are permanent — never
renumber, only append.** Cited from code comments across the repo.
Loaded on demand from the root `AGENTS.md` routing table; do not copy these
into CLAUDE.md or the root AGENTS.md.

## Rules 117–120 — a gathering, and a place to start

Added 2026-09-09. The deeds-first direction (`STATUS.md`) named the
devotional-gathering kit as the flagship next thing; this is it, generalised,
plus the Home screen that makes any of it findable. `agents/gathering.py` owns
the domain, `agents/gathering_api.py` the endpoints, `agents/program_sheet.py`
the printed programme, `agents/home_api.py` the front page. Verify with
`scripts/test_live_consultation.py`.

117. **A project is the thread, and it OWNS nothing that already has an owner.**
     A gathering project ties a purpose, a consultation, what was decided, who
     took something on, the materials and what was learned into one thing a
     person can open. Three decisions make it safe rather than a second system:
     - **It lives in `private/consultation.db`, through
       `live_consultation_store.py`.** A gathering's title, its date and its
       purpose are the same class of private material a transcript is, so rule
       73 applies unchanged and this module stays the only one that touches it.
     - **Commitments are DERIVED, never copied.** A project's commitments are
       the action items of the consultations it links — the same rows, with the
       same tri-state acceptance (rule 101), so accepting one in the gathering
       view and accepting it in the Consultation tab are the same act on the
       same record. Same discipline as the finished-video shelf (rule 58). A
       project that has held no consultation has NO commitments, and that is the
       truthful answer rather than a parallel list that can disagree.
     - **Nothing is scored.** The stages are a description of how the work goes,
       not a funnel: a project can move back a stage, and `closed` is a resting
       place rather than a success condition. Commitments are COUNTED
       ("3 of 5 still open"); there is no percentage, no streak and no ranking
       of anybody, and the suite greps the API's own output for those words
       (rule 61).
     - **Deleting a project never deletes a consultation.** A meeting is a thing
       that happened; a project is a way of looking at it. Tidying away a plan
       must not destroy the record of the conversation that produced it.
     - A programme with no timings has **no length** rather than a guessed one.
       Assuming five minutes an item would print a number on the page nobody
       chose and then measure the group against it.
118. **Only an APPROVED, SELECTED outcome crosses into a project, and a refusal
     says why.** The offer list is built from confirmed decisions and open items
     of sessions whose record a human approved (rule 102) — never from the
     transcript, and never from the assistant's private observations, so a raw
     meeting never becomes standing context for the next one. Two halves:
     - **The gate is in the endpoint, not in what the UI chooses to show.**
       `POST .../outcomes` re-checks `approved_at` and returns 409; a hidden
       button is not a gate.
     - **A session that is not eligible yet is LISTED, with the reason.**
       "There is nothing here" and "you have not approved it yet" are completely
       different things to be told, and a section that just looks empty tells
       you the wrong one.
119. **A kit produces two real documents, and they are never one document.**
     - **The programme and the card sheet are separate PDFs.** The card sheet is
       a duplex grid whose page 2 has to line up with page 1 through the
       printer's long-edge flip (`print_sheet.build_print_sheet`); a programme
       page inside that file shifts every back face by a page and every card
       prints on the wrong side. Two downloads, and the screen says why.
     - **A reading points at a VERIFIED passage by id.** The programme stores no
       scripture text of its own, so there is nothing that could be edited into
       something the library does not contain, and nothing a model wrote can
       print as a quotation (rule 84). The endpoint refuses a `writing_id` that
       is not one of the gathering's own verified passages.
     - **A reading is never truncated to fit.** It flows onto another page.
       Quietly shortening a passage to make a layout work is the same failure as
       paraphrasing one. A passage that has gone missing prints as a stated
       placeholder and is reported in a header, rather than being dropped.
     - **Programme files are written under `private/`, not `outputs/`.**
       `outputs/` is a mounted static directory because quote-card images are
       public artwork; a programme names a real gathering and its date. Served
       only through the owner-gated endpoint, with the path resolved and checked
       against its own directory.
     - **Nothing in the kit builder spends anything.** Existing cards are added
       for free; making new ones is a separate, explicit act through the
       pipeline entry points that already have the batch caps, the review and
       the metering (rule 40). Font sizes in `program_sheet.py` are POINTS via
       `pt()` — the first version used pixel numbers at 300 dpi, which is 6.7pt
       on paper: unreadable, and invisible on screen because a preview is
       scaled.
120. **Home is a read, and it is the front door.** The dashboard opened on the
     Pipeline form — a theme box and a target score — which answers "make me a
     bookmark" and not "what was I doing?". Home is four sections in ordinary
     language: what to continue, what needs a decision, what happens next, and
     what to create. Sheraj is non-technical and dashboard-visible behaviour is
     the deliverable (AGENTS.md), so there is no pipeline, provider, agent graph
     or trust score on it.
     - **One request, and nothing is started.** No job, no microphone, no model,
       no provider query. Opening the application must not begin anything, and
       the poll is a minute rather than seconds — a fast timer would make simply
       having the app open more expensive.
     - **Every section degrades on its own.** A subsystem that raises is
       reported as unavailable in that section and the rest of the page still
       renders. A front page that 500s because one store is unhappy is worse
       than one that says which part is missing.
     - **Every number points at something real.** Approvals come from the actual
       queues (rules 20/24/25/28/37/51/102); a commitment comes from a
       consultation's own action items and shows its tri-state acceptance as
       three states, so "nobody has answered yet" never reads as agreement.
     - **A private title never leaves the response.** Home combines
       `private/consultation.db` and `workforce.db` in memory for one authorised
       reader and writes the combination nowhere — not to a log, not to a cache,
       not into a URL. Abigail's queue is shown by KIND, never by content: this
       is a screen somebody may walk past.

