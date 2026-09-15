# dashboard/ — React + TypeScript + Tailwind + Vite

UI on :5173. `npm run dev` starts the API then Vite; `npm run dev:web` is UI
only; `npx tsc --noEmit` is the typecheck. There is no JS test suite.

Never identify a freshly-created record by matching the react-query cache —
the cache does not contain a product created seconds ago. Pass the data from
the result that created it.

A new backend endpoint 404s in the browser until the API is restarted. The
managed task runs uvicorn WITHOUT --reload. A 404 that says "Product not found"
on a brand-new path is often the OLD process answering, not a routing bug.
Verify with `python -c "import agents.api"` (the CODE) then restart and curl
(the SERVER). See docs/rules/gotchas.md.

Dashboard open-cost / Products shelf / Home payload: docs/rules/dashboard.md
(rules 112–116).

Nested AGENTS.md in the folders that have their own rules:
- src/components/consultation/  (Live Consultation tab)
- src/components/colony/        (Colony + Material World)
- src/components/video/         (Video tab)
