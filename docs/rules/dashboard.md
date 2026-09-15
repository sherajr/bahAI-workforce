# Rules 112-116 — what the dashboard costs to open, and to leave

Canonical numbered hard rules for this subsystem. **Numbers are permanent — never
renumber, only append.** Cited from code comments across the repo.
Loaded on demand from the root `AGENTS.md` routing table; do not copy these
into CLAUDE.md or the root AGENTS.md.

## Rules 112–116 — what the dashboard costs to open, and to leave

112. **A panel is downloaded when it is opened, not when the app starts.** All
     eight were static imports, so opening the dashboard parsed the Colony's
     graph, the video editor, the product editor, Abigail's panel and the whole
     realtime consultation client before anything was on screen: one 692 kB
     bundle (186 kB gzip). Lazy imports take the entry chunk to 242 kB (75 kB
     gzip) and the build's own size warning goes away rather than being
     configured away. Inactive panels are still UNMOUNTED, exactly as before —
     keeping them mounted to preserve state would trade the download for a
     permanent cost in timers and memory — and nothing is prefetched on a hunch.
     `PanelBoundary` covers the two failures this newly makes possible: a chunk
     that will not download, and a panel that throws before the shell is drawn.
113. **An opted-in recording is saved as it is made.** The browser held every
     MediaRecorder chunk in an array until the meeting ended, so a crashed tab
     cost the WHOLE recording — while the comment beside `rec.start(5000)` said a
     timeslice meant a crash cost the last few seconds — and the array grew for
     the length of the meeting. Chunks now go to
     `POST .../audio/chunk?recording_id=&seq=` as they arrive.
     - **Order is the container.** A WebM stream is a header followed by
       continuation clusters; the later chunks are not independently decodable
       files. An out-of-order sequence is refused rather than written into the
       wrong place.
     - **A repeat is acknowledged, not applied**, so a retry after a dropped
       connection cannot duplicate audio into the file.
     - **Finalising is explicit**, so an interrupted meeting cannot leave
       something that looks complete and is not, and what the SERVER holds
       (`/audio/progress`) is the only honest "saved" claim — the recording
       indicator shows that, not what the browser emitted.
     - `stop()` is AWAITED by everything that ends a meeting. It used to be fired
       and forgotten while the UI moved on to closeout.
114. **Nothing outlives the screen that started it, and leaving is a choice.**
     `start()` awaits a microphone permission, then a credential, then a
     connection; unmounting during any of them left a live microphone attached to
     nothing, with the browser's recording indicator still lit. Every step now
     checks a capture generation and releases what it produced if the generation
     moved. The unmount cleanup stops the tracks, the recorder and the pending
     start — its comment promised "no live microphone" while doing none of it —
     and finalises the recording outside the component's lifetime. The same race
     is fixed in `DictateButton`, where unmount also had to stop the recorder's
     own `onstop` from firing a transcription request nobody asked for.
     Navigating away from a listening consultation asks first (`lib/navGuard.ts`,
     consulted by App for the sidebar AND every in-app link). The choice is
     deliberately never "end the meeting": ending leads to the closeout and must
     not be reachable by a mis-click. Resuming is always a press, and
     `started_at` survives it (rule 107).
115. **A poll asks what CHANGED.** The panel refetched the whole session every
     four seconds — and two copies of the transcript inside it, since `turns` and
     `final_turns` are the same rows until a speaker pass exists. Measured on
     synthetic meetings: 81 KB at 60 turns, 805 KB at 600, so the cost of
     listening grew with how long the group had been talking. `GET
     .../updates` answers from cursors; an idle poll is ~220 bytes at any
     transcript length, and one new line is ~900.
     - **The cursor is a per-turn revision, not the greatest turn id.** The
       changes that matter most here happen to turns that already exist: a line
       that finalises late, and a line a person corrects. An id cursor would
       never send either again. The suite pins a correction to the FIRST turn of
       the meeting being caught.
     - `list_turns(limit=...)` takes its limit in SQL. It read every row of the
       meeting and sliced in Python, so the reasoner's recent window loaded the
       whole transcript to throw almost all of it away.
     - `GET /sessions/{id}` is unchanged for its callers.
116. **The Products shelf loads a page, and filters all of it.** `GET /products`
     returns every column of every row — the full listing JSON, the whole
     scorecard, and the entire consultation transcript, which the shelf does not
     display at all. On the owner's real history that is **1.58 MB** to draw a
     grid of thumbnails; `GET /products/summary` is **78 KB** for the first page.
     Search, kind, badge and sort are applied to the WHOLE shelf and a page is
     cut from the result, because a filter that only saw the loaded page would
     answer a different question from the one the bar appears to ask. Videos stay
     DERIVED (rule 58) and still drop out under a review-result filter, with the
     reason returned rather than left to be guessed. No badge arithmetic is
     duplicated: the parsed score and `target_reached` go back and the
     dashboard's existing `badgeForProduct` decides, so the two cannot drift.
     `/products/summary` is registered BEFORE `/products/{product_id}` — FastAPI
     matches in registration order, and a literal path declared after a dynamic
     sibling is swallowed by it.

