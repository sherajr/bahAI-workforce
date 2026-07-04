# bahAI Workforce — Architecture

One-page visual + reference for how the app works. (Mermaid diagrams render on
GitHub and in VSCode with a Mermaid extension.)

## The big picture

```mermaid
flowchart LR
    U([Sheraj]) -->|"theme, e.g. 'Unity of Religion'"| D[React Dashboard\n:5173]
    D -->|"/api/*"| A[FastAPI backend\n:8765 · agents/api.py]
    A --> DB[(workforce.db\nSQLite)]
    A --> O[Ollama · local Qwen\nScribe + Librarian tasks]
    A --> G[xAI Grok · paid\nReviewer, vision, images]
    A --> V[(ChromaDB\nvector_store/)]
    A --> C[Canva API\nbrand template autofill]
    A --> E[Etsy API\ndraft listings]
```

## The bookmark pipeline (the "Run pipeline" button)

`_run_full_pipeline` in `agents/api.py` — runs as a background job, the
dashboard polls progress.

```mermaid
flowchart TD
    T[1 · Create task] --> L[2 · Librarian retrieves citations\nvector search over Bahá'í writings]
    L --> AB[3 · Artist builds image prompt]
    AB --> AG[4 · Artist generates artwork\nxAI image API → outputs/]
    AG --> CON[5 · Consultation — 2 rounds × 4 agents\nArtist → Scribe → Reviewer → Librarian\neach turn grounded in a cited scripture excerpt]
    CON -->|"decision brief + verified quote (locked)"| W[6 · Scribe writes the Etsy listing\nlocal Qwen, lean prompt]
    W --> S[7 · Reviewer scores 0–10\nagainst the 9-principle constitution\nGrok + vision on the real artwork]
    S -->|"below target & attempts left"| R[8 · Revise\nmechanical find/replace edits first,\nlight LLM pass only if needed,\nthen honesty scrub]
    R --> S
    S -->|"target reached or attempts exhausted"| P[9 · Save product → workforce.db]
    P --> CP[10 · Compositor renders\nfront PNG quote overlay + back PNG\n2×6 inch print-ready]
    CP --> CV[11 · Canva autofill\nfront image → brand template]
```

Key loop invariants (step 7–8): always revise the **latest** listing with the
**latest** review; keep the **best** separately; ties adopt the newer listing;
only strict score regressions count toward the 2-strike stall stop; the
consultation's round-2 decision is binding on the Reviewer (overrides must say
"REOPENING team decision").

## Who talks to which model

```mermaid
flowchart LR
    subgraph paid["xAI Grok (paid, has vision)"]
        REV[Reviewer scoring]
        VIS[Artist/Reviewer looking at artwork]
        IMG[Image generation]
    end
    subgraph free["Ollama Qwen (local, free, small context)"]
        SCR[Scribe writing/revising]
        LIB[Librarian verdicts]
        PLN[Consultation brief synthesis]
    end
    ROUTER[agents/router.py\nGROK_TASK_TYPES decides] --> paid
    ROUTER --> free
```

## Data model (SQLite, `agents/state.py`)

```mermaid
erDiagram
    tasks ||--o{ task_runs : "logs every agent step"
    tasks ||--o{ products : "produces"
    agents ||--o{ task_runs : "trust updated per run"
    products {
        string id PK
        string title
        string image_url "raw artwork"
        string front_image "compositor render"
        string back_image "compositor render"
        string listing_copy "listing JSON"
        string reviewer_scores "review JSON"
        string consultation "transcript JSON"
        float revenue
        string etsy_listing_id
    }
```

## Dashboard tabs → endpoints

| Tab | Component | Endpoints used |
|---|---|---|
| Pipeline | `PipelinePanel.tsx` | `POST /pipeline/run`, `GET /pipeline/status/{id}`, `GET /pipeline/jobs` |
| Products | `ProductsGallery.tsx` | `GET /products`, `POST /products/{id}/improve`, `PATCH /products/{id}` (manual edit), `POST /products/{id}/revenue`, `POST /etsy/publish` |
| Trust | `TrustPanel.tsx` | `GET /trust/report`, `GET /agents` |
| Settings | `SettingsPanel.tsx` | `GET /canva/status`, `GET /etsy/status` |

Images are served from `outputs/` at `GET /outputs/{filename}`.
`POST /canva/autofill` is kept as a manual utility (re-push an image to Canva).

## History note

The system originally ran on n8n workflows calling granular per-agent
endpoints. n8n was abandoned for the custom dashboard (owner decision, 2026-07);
the workflows and their ~16 endpoints were removed in the 2026-07-03 cleanup.
If you need a granular capability back, call the agent module functions
directly — they all still exist (`librarian.retrieve`, `artist.generate_image`,
`scribe.write_listing`, `reviewer.score`, `compositor.render_bookmark_pair`).
