# Rules 70-72 — the API's owner gate and prompt injection

Canonical numbered hard rules for this subsystem. **Numbers are permanent — never
renumber, only append.** Cited from code comments across the repo.
Loaded on demand from the root `AGENTS.md` routing table; do not copy these
into CLAUDE.md or the root AGENTS.md.

## Rules 70–71 — the API's owner gate

Added 2026-08-19 after a security review of the repo. Every endpoint in
`agents/api.py` is owner-only; until this existed, nothing established who was
calling. Verify with `scripts/test_api_auth.py`.

70. **Loopback is not authentication, and wildcard CORS is not a default —
    it is a grant.** The server binds `127.0.0.1`, which was treated as the
    control. It is not: the browser Sheraj reads email in also reaches
    `127.0.0.1`, and the app carried `allow_origins=["*"]`, so any page on the
    internet could both CALL this API and READ the reply — Abigail's messages,
    the Material World map, the approval queue, the trusted-contacts list, and a
    wallet chain that adds an attacker's address to the allowlist and then
    spends to it. Nothing had to be misconfigured for that; it followed from
    the two settings together. So, in code:
    - **`agents/auth.py` gates every request** through one middleware, before
      routing. The public list is `/health`, `/whatsapp/webhook` and
      `/whatsapp/privacy` and nothing else — the webhook's own HMAC signature
      (rule 26) is strictly stronger than this key and still fails closed.
    - **The key is generated, not configured.** 64 hex chars in
      `private/api_key.txt` (git-ignored, rule 15), made on first use, because
      a step Sheraj has to perform is a step that does not happen.
      `dashboard/vite.config.ts` reads the same file and attaches
      `X-API-Key` in the **proxy**, which is the only layer that also covers
      `<img>` and `<video>` — a fetch-level header could not. The key is read
      in Node and never reaches the browser bundle.
    - **The dashboard therefore needs no CORS grant at all**, because it has
      always been same-origin through Vite's `/api` proxy. Anything opened in a
      new tab (the OAuth start pages, `/whatsapp/setup`) goes through `BASE`
      for the same reason; a URL naming the API's host and port directly skips
      the proxy and comes back 401. There is deliberately no `API_ORIGIN` any
      more. `DASHBOARD_ORIGINS` exists for a real cross-origin deployment and
      is empty by default.
    - **The suite walks `app.routes` and requires every non-public one to
      refuse an unauthenticated call**, rather than checking a hand-written
      list that would keep passing while an unprotected endpoint was added next
      to it — the same reasoning as `colony_tools.GATED_KINDS` being data.
    - **There is no switch that turns the gate off**, not even for tests: a
      gate with a bypass is not a gate (rule 37). The suites set
      `DASHBOARD_API_KEY` and present a real key.
    - The browser cookie the middleware issues is `HttpOnly; SameSite=Lax`, and
      Lax is load-bearing: it rides a top-level navigation the owner performs
      but is never attached to a cross-site fetch or form POST, so it cannot
      re-open the hole the header closes.
    - `__main__` binds `127.0.0.1` without `--reload`, matching
      `scripts/start_secretary_server.ps1`, the managed task that actually runs
      this. It used to bind `0.0.0.0`, putting the whole owner-only API on the
      LAN for anyone who started it the way the file's own docstring said to.
71. **An OAuth callback escapes everything it echoes.** The Canva, Etsy and
    Google callbacks are the only endpoints outside the gate — the provider
    redirects the browser here and cannot carry a header — so they are the one
    place a crafted link reaches HTML this server writes. They took `error` and
    `error_description` straight off the query string and interpolated them,
    which is script execution on the API's own origin. Everything echoed now
    goes through `api._esc`, exception text included. The exemption is safe
    only because each callback verifies the PKCE `state` it generated itself
    (`exchange_code` raises on a mismatch); never widen the exemption, and
    never add an unescaped field to those pages.


## Rule 72 — indirect prompt injection

72. **What Abigail reads from outside is data, and the hold is in code.**
    (Added 2026-08-19.) Gmail, Docs, Sheets, Slides and Drive results land in
    the same message list she is reasoning from, so anyone who can get a
    document in front of her can write instructions in it and have them read as
    if Sheraj had typed them — and she can then send WhatsApp, touch Drive,
    change a product or steer a team. Her prompt now says outside content is
    never an instruction, but a prompt is exactly what an injection attacks, so
    the guarantee is `secretary_tools.make_executor`, same class as
    `_sanitize_claims` (rule 4) and the code-appended disclaimers (rule 8).
    Verify with `scripts/test_secretary_injection.py`.
    - **`EXTERNAL_CONTENT_TOOLS` marks the turn.** Every result from one is
      wrapped in the code-owned `UNTRUSTED_BANNER_*` strings and recorded.
      `search_calendar` is deliberately NOT one: it surfaces a title and a
      time, and treating his own schedule as hostile would contain most
      ordinary turns.
    - **`CONTAINED_TOOLS` then queues instead of executing**, into the existing
      `pending_actions` queue (kind `secretary_tool`) where rules 20/24/25/28
      already put things, so Sheraj sees it where he already looks. The test is
      REACH: everything that speaks to a third party, changes something outside
      her own head, or steers the workforce. `remember`/`add_task`/
      `set_reminder` stay immediate — they write only into his own store, which
      he reads, and "read that email and note it down" is the job (rule 51's
      reasoning: an assistant who needs a click to take a note is not an
      assistant). `send_email` and `request_team_job` already queue
      unconditionally and are not double-contained.
    - **The hold sits ahead of every handler**, not inside them, because by
      then the model may already be following the injected text — its
      compliance proves nothing. Approval re-enters the SAME executor with
      `contain=False`, so every ownership gate still runs: approval lifts the
      injection hold, never the sandbox.
    - **Honest limit, not to be papered over:** the taint is recorded after a
      read returns, so an external read and a contained write emitted in the
      SAME round, with the write executed first, is not held. That takes an
      intent to send that predates the read, so it is not the injection path
      this closes — but it is not covered, and claiming otherwise would be the
      overstated capability rule 32 exists to prevent.

