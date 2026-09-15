# agents/ — FastAPI backend

Python. App factory is `agents/api.py`. New subsystems get their own `*_api.py`
router included from there; do not grow `api.py` unless the task names a route
in it. Two tools must not edit `api.py` at the same time.

Work state: `workforce.db` via `state.py`. Personal data: `private/`
(gitignored) via `secretary_store.py`, `live_consultation_store.py`,
`nuclei_store.py`. Never write meeting or secretary content into `workforce.db`.

Migrations: ALTER TABLE wrapped in try/except on startup; new product columns
also go on the `update_product` allowlist. A NEW CONSTRAINT on an existing
column needs its own `CREATE UNIQUE INDEX IF NOT EXISTS` — editing the inline
CREATE TABLE is a no-op on a DB that already exists (see docs/rules/gotchas.md).

Metering: every paid call records via `state.record_spend` (chokepoints:
`router._call_grok` / `call_grok_vision`, `artist.generate_image`).

Owner gate + prompt injection: docs/rules/api.md (rules 70–72).
Default verify: `python -c "import agents.api"` plus the suite from the root
AGENTS.md routing table for the subsystem you touched.

Load on demand, only if you are touching that subsystem:
- pipelines / quotes / print: docs/rules/pipelines.md
- Abigail / WhatsApp / Google: docs/rules/secretary.md
- live consultation: docs/rules/live-consultation.md — NOT agents/consultation.py
- colony / nuclei: docs/rules/colony.md
- video: docs/rules/video.md
- wallet: docs/rules/wallet.md
- cancel a run: docs/rules/jobs.md
