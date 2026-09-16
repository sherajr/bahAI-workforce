# AGENTS.md — orientation for AI coding agents

The canonical, tool-agnostic instructions for anything changing this codebase —
Claude Code, Codex, Antigravity, Grok, or a human. `README.md` has the project
map, `docs/ARCHITECTURE.md` the diagrams, `docs/overview.md` the three product
pipelines in one page. **`STATUS.md` is what is happening right now: read it
before you start, update it before you stop.**

**The numbered rules are the non-negotiable part of this codebase.** Each one
exists because of a real production bug; violating one reintroduces that bug.
Over 170 code comments cite them by number, so **the numbers are permanent —
never renumber, only append.** The rule bodies live in `docs/rules/*.md`,
split by subsystem, so no single coding tool has to load all of them just to
touch one part of the app. **Open the file(s) for the subsystem you're
touching — you don't need the rest.**

| Rules | Subsystem | File |
| --- | --- | --- |
| 1–14, 29, 111 | The product pipelines, the quote-card format, exact quotation verification | [docs/rules/pipelines.md](docs/rules/pipelines.md) |
| 15–28, 50–54 | The Secretary (Abigail) + her bridge into the teams | [docs/rules/secretary.md](docs/rules/secretary.md) |
| 30–34, 58 | The Video pipeline | [docs/rules/video.md](docs/rules/video.md) |
| 35–41b, 59–69 | The Colony, the Material World (nuclei), junior youth groups | [docs/rules/colony.md](docs/rules/colony.md) |
| 42–49 | The project wallet | [docs/rules/wallet.md](docs/rules/wallet.md) |
| 55–57 | Cancelling a run | [docs/rules/jobs.md](docs/rules/jobs.md) |
| 70–72 | The API's owner gate + indirect prompt injection | [docs/rules/api.md](docs/rules/api.md) |
| 73–110, 121–137 | Live Consultation (the Consultation tab) | [docs/rules/live-consultation.md](docs/rules/live-consultation.md) |
| 112–116 | What the dashboard costs to open, and to leave | [docs/rules/dashboard.md](docs/rules/dashboard.md) |
| 117–120 | A gathering, and a place to start | [docs/rules/gatherings.md](docs/rules/gatherings.md) |

Also load on demand: [docs/rules/gotchas.md](docs/rules/gotchas.md) (real
operational incidents — read before debugging anything that looks like "it
worked yesterday"), [docs/rules/dispatch.md](docs/rules/dispatch.md)
(dispatching work to the Grok/Codex/Antigravity CLIs), and
[docs/overview.md](docs/overview.md) (narrative: the three pipelines,
Gatherings/Home, and Live Consultation, in one page).

## Working norms

Sheraj uses several AI coding tools on this repo. These norms apply to all of
them equally — don't assume your own tool's defaults.

- **This file is the single source of truth.** `CLAUDE.md` exists only because
  Claude Code looks for that filename; it imports this file (`@AGENTS.md`) and
  adds nothing. Edit **AGENTS.md** (or the relevant `docs/rules/*.md`), never
  CLAUDE.md, or the two drift.
- **Read `STATUS.md` and `git status` / `git log -5` before anything
  nontrivial.** Another tool may have left uncommitted work in the tree. If you
  find substantial changes you didn't make, read them before adding more —
  don't overwrite or "clean up" work in progress.
- **Update `STATUS.md` when you finish a nontrivial chunk**: correct the
  Snapshot, prepend one Activity Log entry (date, tool/model, what changed and
  why, what's left). Point at file paths; git already has the diffs.
- **Only commit when Sheraj explicitly asks.** He reviews changes himself.
- **Sheraj is non-technical.** Dashboard-visible behaviour is the deliverable,
  and errors must surface where he'll see them (Activity Log, chat reply,
  visible UI state) — never fail silently. Canva autofill once failed silently
  for weeks. Report back in plain language.
- **Verify, don't trust a self-report** — your own or a dispatched agent's.
  Re-run the check: import the module, grep for the string that should be gone,
  read the whole `git diff`.
- **If another tool's row in STATUS.md "In flight" overlaps your paths, stop
  and tell Sheraj.** Do not "just be careful" in the same files.

## Commands and verification

```bash
cd dashboard && npm run dev                # the whole app (see below); UI on :5173
cd dashboard && npm run dev:web            # UI only, no backend step (old `vite`)
cd dashboard && npx tsc --noEmit           # typecheck (no JS test suite exists)
python -c "import agents.api"              # fast backend sanity check
python scripts/test_colony.py              # Colony tab: 135 checks
python scripts/test_secretary_colony.py    # Abigail <-> the teams: 92 checks
python scripts/test_job_cancel.py          # Cancelling a run: 24 checks
python scripts/test_wallet.py              # Wallet: 90 checks, no network/keys
python scripts/test_video_pipeline.py      # Video pipeline: 288 checks
python scripts/test_nuclei.py              # Material World (nuclei): 281 checks
python scripts/test_api_auth.py            # The API's owner gate: 66 checks
python scripts/test_secretary_injection.py # Prompt-injection hold: 50 checks
python scripts/test_live_consultation.py   # Live Consultation, the shelf,
                                           # gatherings and Home: 988 checks
python scripts/test_quote_verify.py        # Exact quotation verification: 50 checks
node scripts/verify_consultation_focus.mjs # The Focus view's pure logic
                                           # (no frontend test framework exists
                                           # otherwise): 19 checks
```

All of the suites above are offline and free. Check counts live **here only** —
`docs/rules/*.md` name their suite without a number, so the two can't drift apart.

There is no formal test framework: `scripts/test_*.py` are runnable checks.
Test logic offline with mocks first, then verify live against the real SQLite DB
(`agents.state.get_all_products()`) and real LLM calls (Ollama local, xAI Grok).
FastAPI's `TestClient` exercises endpoints without starting a server.

**The backend already runs as a Scheduled Task ("bahAI Secretary API") that
auto-starts at Windows logon — never start it with `python agents/api.py` or
`python -m agents.api`.** That file's `__main__` block binds `0.0.0.0` with
`reload=True`, and a second instance alongside the managed one leaves two
processes on :8765 (Windows lets a wildcard bind and a loopback bind coexist).
That happened for real and looked like "WhatsApp stopped responding" while the
dashboard kept working. To pick up a code change: kill whatever holds :8765
(from Bash: `netstat -ano | grep 8765`; from PowerShell, which has no `grep`:
`Get-NetTCPConnection -LocalPort 8765 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }`
-- `--reload`'s WatchFiles also spawns a child PID that survives killing the
parent), then either
`Start-ScheduledTask -TaskName "bahAI Secretary API"` or
`python -m uvicorn agents.api:app --host 127.0.0.1 --port 8765` (no `--reload`).
Killing it mid-session is fine — the task only re-triggers at the next logon.

**`npm run dev` now brings the whole app up in order** and is the normal way to
start work: `dashboard/scripts/dev.mjs` makes the API answer, reports Ollama and
the tunnel, clears leftover dev servers, then starts Vite.
`scripts/ensure_backend.ps1` does the API half and is worth running alone when
the backend needs reviving. Two things it exists for, both real (2026-08-24):
- **Readiness is `/health`, never a PID or a port.** The API was found alive
  with its LISTENING SOCKET DEAD — uvicorn's accept loop had exited with
  WinError 64 while the process stayed up, so the port was empty, the task still
  said "Ready" (it only triggers at logon) and the dashboard just said the
  backend was not running. A liveness check by process or by port would have
  reported everything fine. It clears every match rather than just the listener,
  because the venv's `pythonw.exe` launches the real interpreter as a CHILD —
  one API instance is always two processes.
- **Leftover Vite servers are cleared** (`scripts/clear_stale_dashboards.ps1`,
  scoped to node processes naming this repo's own Vite binary, so nothing else
  on the machine can match). Four dev servers from eleven days earlier were
  holding :5173–:5176 and answering nothing, so the bookmarked
  `localhost:5173` spun for ever while a new server quietly moved to :5177.
Ctrl+C stops the dashboard only — the API keeps running on purpose, since
Abigail answers WhatsApp through it whether or not a browser is open.
The Cloudflare Tunnel auto-starts the same way ("bahAI Secretary Tunnel"). Both
run `scripts/start_secretary_server.ps1` / `start_secretary_tunnel.ps1` and log
to `logs/*.out.log` / `*.err.log` (gitignored), since after a real reboot there
is no console to read — check `logs/` before assuming a code fault.

Full operational gotchas (env reload quirks, WhatsApp token/template traps,
webhook subscription pitfalls, OpenAI temperature quirk, Windows cp1252, DB
migration pattern) are in [docs/rules/gotchas.md](docs/rules/gotchas.md) — read
it before assuming something is a code regression.

For dispatching a precisely-scoped task to the Grok / Codex / Antigravity CLIs
and re-verifying the result, see
[docs/rules/dispatch.md](docs/rules/dispatch.md).

## Parallel work — pick rows that do not share a file

Sheraj runs Claude Code, Codex, and Grok on this repo. Two tools may run at
once only if their rows below do not share a path. `agents/api.py` and
`STATUS.md` are single-writer: one tool at a time, or a merge conflict is
guaranteed. Default isolation is a git worktree plus this allowlist. After
any dispatched agent, `git status` and the whole `git diff` — Codex has
BOM-corrupted api.py while "succeeding"; Grok `--worktree` has failed to
isolate. Details: docs/rules/dispatch.md.

| Surface | Owns these paths | Do not touch |
|---|---|---|
| Product pipelines | `agents/{librarian,artist,scribe,reviewer,compositor,card_compositor,consultation,quote_verify,layout,print_sheet,translator,program_sheet}.py` | `agents/api.py` unless the task names a route; `live_consultation*`; `private/` |
| Live Consultation | `agents/live_consultation*.py`, `dashboard/src/components/consultation/`, `dashboard/src/hooks/useRealtimeConsultation.ts`, `dashboard/src/lib/consultationGovernor.ts` | `agents/consultation.py` (different subsystem); `private/` contents except via the store module |
| Colony / nuclei | `agents/{colony,colony_chat,colony_tools,nuclei_store,nuclei_layout,nuclei_bridge}.py`, `dashboard/src/components/colony/` | secretary private store; product pipelines |
| Secretary | `agents/{secretary,secretary_store,secretary_tools,secretary_colony,whatsapp,gcal,gdocs,gdrive,gmail,gsheets,gslides,google_auth,scheduler,badi_dates}.py` | `workforce.db` product rows; live consultation store |
| Video | `agents/video_*.py`, `agents/videographer.py`, `dashboard/src/components/video/`, `dashboard/src/components/VideoPanel.tsx` | product compositors |
| Wallet | `agents/wallet.py` | colony layout |
| Gatherings / Home | `agents/{gathering,gathering_api,home_api,program_sheet}.py`, `dashboard/src/components/{GatheringsPanel,HomePanel}.tsx` | live_consultation_store internals unless the task is the shared commitment join |
| Dashboard chrome | `dashboard/src/{App,main}.tsx`, `components/{Nav,Layout,ui}.tsx` | backend modules other than the one endpoint you are wiring |
