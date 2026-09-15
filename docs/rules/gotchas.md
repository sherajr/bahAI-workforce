# Gotchas

Operational gotchas for this codebase, learned from real incidents — moved intact
from the root `AGENTS.md`. Loaded on demand from the root `AGENTS.md` routing
table; do not copy these into CLAUDE.md or the root AGENTS.md.

## Gotchas

- **Windows console is cp1252**: use ASCII `->` not `→` in anything a script
  `print`s. The API and dashboard are UTF-8 safe.
- **`state.py` migrations run on every startup** (ALTER TABLE wrapped in
  try/except) — add new product columns there AND to the `update_product`
  allowlist. `secretary_store.py` follows the same pattern, but a NEW CONSTRAINT
  on an existing column needs its own migration: `CREATE TABLE IF NOT EXISTS` is
  a no-op on a table that already exists on disk, so editing the inline
  constraint does nothing for any DB created earlier. Add a
  `CREATE UNIQUE INDEX IF NOT EXISTS` alongside it — this bit us for real, when
  `contacts.phone` shipped without its UNIQUE applying and every inbound
  WhatsApp message crashed `record_inbound_contact`'s `ON CONFLICT(phone)`
  upsert before it reached the Secretary.
- **Ollama calls set `think: False`** — Qwen3's hybrid thinking would silently
  eat the output budget otherwise.
- **Card faces are composed at `_SS`× and downscaled on save**
  (`card_compositor._SS = 2`, for true anti-aliasing on small translated text).
  Every absolute pixel constant in that file — font sizes, floors, rule widths,
  insets — must be multiplied by `_SS`, or it renders at half its intended
  printed size. Shadow offsets scale with glyph size (~size/26) for the same
  reason: a fixed 2px shadow was a blocky halo on ~18px glyphs.
- **Don't identify freshly-created records by matching against a react-query
  cache** — the cache never contains a product created seconds ago. A card run's
  Spanish pair rendered and stored correctly but showed nothing on screen,
  because the preview matched variant filenames against a stale products cache;
  it read as "the run only did English". Pass the data explicitly from the
  result that created it (cache-matching is a fallback at best).
- **Cloud spend is METERED**: every paid call records itself via
  `state.record_spend` (chokepoints: `router._call_grok` / `call_grok_vision`,
  and `artist.generate_image`). The Steward reports actuals plus a soft monthly
  ceiling (`MONTHLY_SPEND_CEILING_USD`). Products created before
  `api.METERING_EPOCH` carry a flat `LEGACY_COST_PER_PRODUCT` estimate (labelled
  `legacy_estimate` in `spend_by_kind`) so pre-metering work never reads as $0.
  New paid call paths must meter themselves the same way.
- **Products persist `target_reached`/`attempts`**; one saved below its target
  wears the BEST EFFORT badge on the dashboard. Any pipeline that saves or
  overwrites a product must set both.
- **Uvicorn's `--reload` can serve a STALE env var** after editing `.env`, even
  across what look like full restarts (new PIDs). If a `.env` change doesn't
  take effect, kill every process on the port (Windows may leave a phantom
  LISTENING socket) and start once without `--reload` to confirm.
- **A NEW endpoint 404s in the browser until the backend is restarted, and the
  404 can look like a routing bug.** The managed task runs uvicorn WITHOUT
  `--reload` (deliberately, rule 70), so a router added to `agents/api.py` is
  not in the running process however many times the page is refreshed. The
  confusing part is the message: a new literal path under an existing dynamic
  one is answered by the OLD process's dynamic route, so `/products/summary`
  came back **"404: Product not found"** -- which reads exactly like rule 116's
  registration-order mistake and is not it. Verified the same way every time:
  `python -c "import agents.api"` and check `app.routes` for the path (that is
  the CODE), then restart and curl it (that is the SERVER). Real, 2026-09-10:
  three new tabs were dead in the dashboard while every one of their tests
  passed.
- **`WHATSAPP_TOKEN` must be a permanent System User token**, not the temporary
  one from Meta's API Setup page — that one expires in ~24h and silently breaks
  both messaging and any Graph API call, looking exactly like a code regression.
  Generate via Business Settings → System Users → a system user with the WABA
  asset assigned → Generate New Token → expiration "Never". Check with
  `GET /v21.0/debug_token?input_token=<token>&access_token=<token>`
  (`expires_at: 0` and `is_valid: true` confirm the permanent kind).
- **Abigail's number is still Meta's sandbox TEST number** (5-recipient limit),
  so an allowlisted guest (rule 27) also has to be in Meta's test-recipient list
  or her replies silently fail to deliver. Moving to a real number is a future
  owner decision.
- **The outside-24h-window template fallback has never been proven to work.**
  `send_best_effort` → `send_template` uses `WHATSAPP_UPDATE_TEMPLATE`
  (default `secretary_update`), and on 2026-07-11 that template did not exist in
  Meta's system — error 132001 in every language, while `hello_world` sent fine.
  It needs a UTILITY template of that exact name, body exactly `{{1}}`, English
  (US), approved in WhatsApp Manager; no code change. `GET /whatsapp/setup`
  walks through creating it. **Re-confirmed still broken 2026-09-11**: a message
  to an allowlisted friend whose last inbound was two months old fell through to
  the template and came back HTTP 404 / error 132001, "template name
  (secretary_update) does not exist". So it is not unproven any more — it is
  known not to work, and every scheduler reminder sent outside the window is
  being dropped. The token was checked live the same day and is fine (permanent
  System User, `expires_at: 0`), so a template failure must never be diagnosed
  as an expired token.
- **A provider's refusal must reach Sheraj in the provider's own words.**
  `whatsapp.py` used `resp.raise_for_status()`, which discards the response
  BODY — the only place Meta says what was wrong. The 132001 above surfaced in
  the dashboard as `send_whatsapp failed: HTTPError` and nothing else, which is
  the Canva-autofill silent failure with a status code on top: he cannot act on
  it, and it reads as "WhatsApp is broken" rather than "that template was never
  created". Every send now goes through one `whatsapp._post` chokepoint raising
  `WhatsAppError` with Meta's code translated into plain language (`_ERROR_HELP`,
  which covers the sandbox test number's 131030 and the closed-window 131047),
  `send_best_effort` names the closed window as the REASON a template was tried
  at all, and `whatsapp.why(exc)` is what notification strings print.
  `why()` deliberately does NOT widen to `str(exc)` for an arbitrary exception —
  that can carry a request body, and a notification is not a place message
  content may appear (rule 15).
- **A WABA sends webhook events to whichever Meta app is in its
  `subscribed_apps` list** — a separate, API-level link from the App Dashboard's
  Callback URL/Verify Token and from the per-field "Subscribe" toggle. All of
  those can look correct while the WABA is subscribed to a different app (ours
  pointed at Meta's own "WA DevX Webhook Events 1P App" after reconnecting), and
  Meta's "Check test webhooks" log will show real inbound messages that never
  reach our server. Check `GET /{waba_id}/subscribed_apps`, fix with
  `POST /{waba_id}/subscribed_apps` (bearer `WHATSAPP_TOKEN`), whenever real
  messages stop arriving after a reconnect or app change.
- **The GPT-5.x family refuses a non-default `temperature`** ("Only the default
  (1) value is supported"), and `_call_openai` always sent one — so every
  OpenAI call from the router 400'd, silently making the Colony's OpenAI
  provider (rule 41a) unusable. It now retries once without the field. Related:
  the bare `gpt-5.6` id is NOT resolvable on this account (`GET
  /v1/models/gpt-5.6` → 404) even though `models.py` offers it as a
  documented alias; the real ids are `gpt-5.6-sol` / `-terra` / `-luna`.
- **`requirements.txt` covers the backend's direct third-party imports** but
  there is no lockfile and no venv checked in — a `ModuleNotFoundError` after a
  fresh `pip install -r requirements.txt` is a real gap in the file, not a local
  environment issue. Add the missing package.

