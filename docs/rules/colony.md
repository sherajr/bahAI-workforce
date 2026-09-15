# Rules 35-41b, 59-69 — the Colony and the Material World

Canonical numbered hard rules for this subsystem. **Numbers are permanent — never
renumber, only append.** Cited from code comments across the repo.
Loaded on demand from the root `AGENTS.md` routing table; do not copy these
into CLAUDE.md or the root AGENTS.md.

## Rules 35–41b — the Colony

The dashboard tab that treats the workforce as an organisation: performance,
handoffs, per-agent chat and settings, team goals, team consultation and the
approval queue. Replaced the Trust tab 2026-08-13. `agents/colony.py` owns the
teams, per-agent settings, goals, chat history and the queue (its own tables in
`workforce.db`); `colony_tools.py` holds the toolsets and the gate,
`colony_chat.py` the chat and team consultation, and
`dashboard/src/components/colony/` the UI. Verify with `scripts/test_colony.py`.

35. **The handoff graph is DERIVED from `task_runs`, never stored.** Within one
    `task_id`, consecutive rows in id order are consecutive steps, so
    agent(n) → agent(n+1) is a real handoff. Every pipeline already writes
    `task_runs`, so the lines on screen are a record of what happened rather
    than a drawing of what was intended — do not add a parallel "edges" table to
    make the picture tidier. A judged run and a mechanical one stay
    distinguishable all the way to the UI (`judged` flag, rule 14): showing a
    render step as "passed" would turn clean-run stats into an uptime metric.
36. **The workforce agents get their OWN tool-calling loop, never Claude's.**
    `router.call_llm_agentic` routes by `task_type` exactly like `call_llm`
    (Grok for the Artist/Reviewer, local Ollama for the rest) and exists
    precisely because rule 16 reserves Claude for the Secretary. It mirrors
    `call_claude_agentic`'s contract — same executor signature, hard round cap,
    forced final round, every round metered — with two provider-forced
    differences: OpenAI-style tool schemas, and `max_rounds` defaulting to 4
    rather than 6 (each round appends another tool result, and local Qwen's
    context is why rule 1 exists). `_normalize_tool_calls` is load-bearing: Grok
    returns tool arguments as a JSON STRING and Ollama as a dict, so dropping it
    silently breaks exactly one provider.
37. **Free reads run; anything paid or product-changing QUEUES.** The gate lives
    in `colony_tools.make_executor`, not in any prompt — an agent told "generate
    it now" queues anyway (verified live 2026-08-13: no file in `outputs/`,
    `image_gen` spend unmoved). `GATED_KINDS` is data so the suite can assert
    the whole paid surface is covered, and no gated kind may also have an entry
    in `IMMEDIATE_HANDLERS` — a gate with a bypass is not a gate. Approval is
    the only execution path (`colony_tools.run_approved_action`, called from the
    endpoint only, same shape as `secretary.execute_pending_action`). A queued
    action is appended to the reply in CODE, because the model may claim success
    without mentioning that nothing ran.
38. **The Secretary is a node in the Colony but never a chat in it.** (She
    reaches the teams a different way — rules 50–52.) `colony.NO_COLONY_CHAT`
    covers her and the instruments. Routing her here would drop her from Claude
    to Qwen/Grok (rule 16) and write her turns into `workforce.db` instead of
    her private store (rule 15); her node opens her own tab instead. The
    instruments (`compositor`, `consultation`) are real rows in `task_runs` —
    they carry the handoff graph — but are not people: no avatar, no chat, no
    settings.
39. **A team goal is injected in exactly ONE place, capped, and fails open.**
    `system_prompt_builder._goal_steer` appends it to every agent prompt in the
    codebase, so a goal reaches pipeline runs and chat alike and can never apply
    in one path but be forgotten in another (which is also why
    `colony_chat._agent_context` must NOT repeat it). It is one line, hard-capped
    at `colony.GOAL_NOTE_MAX_CHARS`, and a missing table or unreadable DB
    degrades to "no goal" rather than breaking a run — steering is an
    enhancement, not a dependency. Goal PROGRESS is counted from real finished
    products against a baseline taken when the goal was set, never self-reported
    (principle 2).
40. **Launching a goal reuses the real pipeline entry points**, so a goal-started
    run is indistinguishable from a hand-started one and every gate applies
    unchanged. The Film Crew is deliberately the exception: it CREATES a video
    project and stops, because video planning is reviewed before any clip is
    rendered (rules 31/33) and a one-line goal must not skip that look.
41. **An action's outcome is read from the provider's REAL return shape.**
    Caught live: `run_approved_action` read `result["translation"]` when
    `translator.translate_quote` returns `text`, so an approved translation
    reported success with nothing in it. The suite now stubs these with the
    actual documented shapes (`text`/`disclaimer_en` for the translator,
    `image_url` for the artist) so a wrong key fails there, not in front of
    Sheraj. A translation surfaced in chat still carries its code-owned
    AI-assisted label (rule 8).
41a. **Per-agent model choice (`agents/models.py`, owner ask 2026-08-13) — the
    provider boundary is enforced in CODE, not by the dropdown.**
    `validate_choice` runs in the settings endpoint BEFORE storage: a workforce
    agent can never be saved onto Claude and Abigail can never be saved off it
    (rule 16 made structural rather than conventional). Also:
    - **No stored choice = today's routing, byte-for-byte.** `resolve()` with no
      override returns what task-type routing would have picked, so the feature
      is inert until used. Verified by test, not by inspection.
    - **`agent=` is passed EXPLICITLY at every call site**, never reverse-derived
      from the task type, because task types are shared: `creative_writing` is
      both the Artist and the Translator, `scribe` is both the Scribe and the
      X-post writer. The one exception is consultation's `"plan"` synthesis
      call, which belongs to the whole team.
    - **An unreachable provider is NOT evidence a model is gone.**
      `_is_known_missing` only reports absence when the provider list was
      fetched SUCCESSFULLY and the id was not in it; otherwise the stored id is
      trusted. Without that, a briefly-stopped Ollama would silently move every
      local agent onto a different model. A genuine fallback is always REPORTED.
    - **Model lists are discovered, never hardcoded** — and filtered:
      `nomic-embed-text` backs `get_embedding` and the citation index, and
      `grok-imagine-*` are image/video endpoints that would 404 a chat call.
    - **Paid means "not local".** Testing `provider == XAI` alone labelled
      Abigail's Claude default as FREE — a lie about money in the one place used
      to check it. Vision (`call_grok_vision`) and image generation are separate
      paid paths that a local model choice does NOT make free; the UI says so
      for the Reviewer and Artist.
41b. **A body's FINISH on the Colony map is judged work only, and "not scored
    yet" must never look like "scored badly".** (Owner ask 2026-08-16: a
    well-performing agent's sphere should be shiny and glimmering, a poor one
    covered in dross and scratches.) `dashboard/src/components/colony/finish.ts`
    computes it and `Sphere.tsx` draws it, under rules 14/35: only JUDGED runs
    are evidence, so instruments (`compositor`, `consultation`) get NO finish at
    all — they stay flat — and an agent with `total_runs === 0` renders PLAIN,
    never dull. Polishing a body with mechanical success would turn the map into
    the uptime metric rule 14 exists to prevent.
    - **`trust_score` is not usable raw here**: it defaults to 50.0 with zero
      runs, so reading it directly would paint every new agent half-tarnished.
      The finish is computed from `clean_runs`/`total_runs` with `total_runs`
      gating it, plus a `consecutive_failures` penalty so a current slump shows
      before the lifetime average catches up.
    - **A thin record is pulled toward plain** (`CONFIDENCE_RUNS`), because one
      reviewed run is an anecdote and a first failure should not read as a
      verdict. It softens the PICTURE only — the exact counts are on the hover
      card and in the drawer, and every state is stated in WORDS there too,
      since the texture is ~15px on screen and cannot be the only carrier.
    - **Marks are deterministic per body and revealed in order** from a fixed
      set, so worsening work ADDS dross where dross already is. Grime that
      reshuffled every render would be the same bug the deterministic layout in
      `layout.ts` exists to avoid.
    - A team core is finished from its members' judged work ADDED UP (derived,
      never stored — rule 35), excluding its instruments, so a core can never
      read brighter than the people in it.


## Rules 59–64 — the Material World (nuclei)

The Colony tab's other sky: Sheraj's nuclei and friends. Design in
`private/nuclei/`. Data in `private/nuclei.db`. Verify with
`scripts/test_nuclei.py`.

**It was called "the Real World" until 2026-08-18** (owner decision: the
Digital World is real too — the contrast that was meant is material against
digital). Everything a person READS now says Material World. The IDENTIFIERS
were deliberately left alone — `RealWorldGraph.tsx`, `layout_real_world()`,
the `rw*` state and the `world: "real"` value in localStorage — because
nothing keys on the words and renaming them buys nothing while
`rwScale`/`rwPanX`/`rwPanY` are already written into Sheraj's browser and the
git history says "Real World" throughout. Do not "tidy" that up; a stored
camera and a saved view preference would be silently lost for a wording
change. If it ever is renamed, migrate the stored keys rather than dropping
them.

59. **Community data lives in `private/nuclei.db` via `nuclei_store.py` and only there.** Follows `secretary_store.py`: one git-ignored SQLite file. Nothing personal in `workforce.db`, `log_run` summaries, job progress strings, stdout, or any committed file — including seeds, fixtures, tests and screenshots. Tests build invented people in a temp directory and must refuse to open `private/nuclei.db`. `assert_test_db(path)` is the gate.

60. **Models may read a shareable snapshot; they may not read private detail, and they may not write.** The store holds only what a depicted friend could also see: chosen name, groupings, the two lists, gatherings (who / kind / when), study as sentences, ties, gifts by theme. No phone, email, address, or intimate-note column exists. An unprompted model write is a bug. `assert_shareable` still runs on anything that later crosses into `workforce.db`.

61. **Never score a person's spiritual condition.** Count actions, not interior states. The largest numbers point at the owner (his consistency), never at a ranking of friends. No grade, percentage, heat-map of receptiveness, or "spiritual growth" figure on any human being.

62. **One light per person. Each nucleus is its own point of light; distance to each is about that nucleus.** Default chairs are by `created_at` then `id` (a seventh table spirals out — never `i % 6` on top of the first). `pos_x`/`pos_y` is an owner override: a drag, or Arrange / Optimize locations, which is deterministic from size, shared people, recorded gatherings and ties — not a physics sim on every load. The owner is excluded from that affinity so his seat at every nucleus cannot collapse the map (rule 61). A person is placed once: a target on each grouping's own rings (core service close, connected far); the seat is the engagement-weighted average, boosted by recorded gatherings. They are not copied. A household lists its people (`household_members`); the same person may sit in a family and serve on an institution. A family-only person lives inside the household light until that family is opened (petals). Someone who already sits elsewhere stays there; opening the family draws a thread. Walking with someone is service for a particular grouping (a directed tie), not a seat at that table — the one who walks sits near the work and is not made a member. Leaving an institution ends that membership only. A neighbour arriving does not change anyone's seat; lights that would cover each other (dots or names) are then squeezed apart by the smallest step that keeps every label readable — never a physics shuffle on load, never a score. Friends who serve a local institution sit on an even ring outside its light, not in a pile on the core. Taking a friend off the map archives them (ends live memberships, keeps gatherings); the owner's light cannot be archived. Local institutions of the Faith (LSA, Regional Institute, Auxiliary Board, Area Teaching Committee, and any the owner names) sit in a column left of the Workforce. The owner adds and rarely archives them; worldwide bodies are not on this map. Nuclei are points of light (the Vision in that place), not a single central sphere and not Colony agent-bodies.

62b. **A name is a display label and nothing is keyed on it, so anyone and
    anything on this map can be renamed.** (Owner ask 2026-08-17.) People,
    nuclei and institutions all rename through the store's existing
    `update_actor` / `update_grouping` — including the owner's own light, which
    `ensure_owner` creates as "You" and has always described as renameable; it
    is only ARCHIVING him that is refused. A rename keeps the id, so every
    membership, facet, tie, gathering and chair survives it and no seat moves.
    An empty or whitespace-only name is refused at the store, and the endpoint
    returns 400 with the reason rather than saving a nameless light.
    `RenameField.tsx` is the shared control; the one thing it must keep doing is
    resetting its draft when the drawer is pointed at someone else, or you open
    a second person and find the first one's name in the box, one Enter away
    from renaming the wrong light. The workforce label on the map reads its name
    from the snapshot for the same reason — hardcoding it would let a rename
    leave the map and the drawer disagreeing.
63. **Grouping kinds, axes, facet kinds, tie kinds, activity kinds and institute units are DATA.** Adding a relationship type a year from now is an `INSERT`. Participation and service are two lists: service sits closer; core-activity kinds carry `is_core`. Do not hardcode the spreadsheet's six columns as an enum or a linear funnel.

64. **A workforce gift stores a `product_id` and a theme, never a friend's name, and `workforce.db` never learns who a gathering is for.** Job progress strings stay mechanical.


## Rules 65–68 — the Bahá'í Workforce on the Material World map

The workforce light on the Material World sky opens like a family opens: the agents
fan out, real people can be put on it, and a WhatsApp message can be written
from it to a friend or to a nucleus's group. Added 2026-08-17 (owner ask).
`agents/nuclei_bridge.py` is the one module where the two worlds touch;
`dashboard/src/components/colony/WorkforceDrawer.tsx` is the UI. Verify with
`scripts/test_nuclei.py`.

65. **The workforce is a real grouping, and never a table on the sky.** It is
    one row in `private/nuclei.db` of its own kind (`WORKFORCE_KIND` /
    `WORKFORCE_SLUG`), created by `init_db` and only there — `create_grouping`
    refuses a second one. Making it a grouping is what lets a person join it
    through the ordinary `add_membership` path, so every existing drawer, query
    and gate works unchanged. What it must never become is a table:
    `nuclei_layout.is_workforce_row` makes `assign_slots` skip it *before any
    chair index is spent* (otherwise a nucleus would slide onto the next chair
    for a light that is drawn somewhere else entirely), `optimize_layout`
    excludes it so Arrange can never move it, and it keeps the fixed
    `WORKFORCE` position it has always had.
    - **Its people get no second light** (rule 62). A workforce-only person has
      no seat on the sky at all and lives inside the workforce light until it is
      opened — exactly the family-only case. Someone who already gathers
      somewhere keeps that light and is reached by a thread when the workforce
      opens, never redrawn.
    - **The Digital World shows the same people, DERIVED.** `GET /colony`
      merges `nuclei_store.workforce_members()` in as `humans` on every read;
      they are never rows in `workforce.db` (rule 68), the same shape as the
      finished-video shelf (rule 58). They draw without trust, without a finish
      and without a team ring — polishing a person would be a score on a human
      being (rules 61 / 41b).
66. **A nucleus's WhatsApp GROUP is data; posting into one is not possible.**
    `grouping_channels` notes the group a table already talks in (a name and an
    invite link) so a message can be written *for* it. Two halves:
    - **Rule 60 is not relaxed.** No phone, email or address column exists on a
      channel and none may be added; a group invite link is what every member of
      that group can already see. One-to-one numbers stay in Abigail's
      `contacts` table and only there (rule 28). Only `whatsapp_group` is an
      accepted kind, and only a real `chat.whatsapp.com` / `wa.me` link is
      accepted — a link that is not one is refused rather than stored.
    - **The Cloud API has no group endpoint at all.** Meta's WhatsApp Cloud API
      — the whole basis of the Secretary — can only message one person. So a
      group draft ends in Copy plus a link that opens the group, and the UI SAYS
      SO on screen. Never add a Send button for a group: a button that silently
      does nothing is the Canva-autofill failure, and this one would look like a
      message went out to a whole community.
67. **A draft that names a friend runs on the LOCAL model, and cannot be routed
    off it.** `router.call_local` exists for exactly this: `call_llm` picks a
    provider from the task type *and* from the per-agent model saved in the
    Colony tab (rule 41a), so anything going through it can be moved onto a paid
    cloud API by a dropdown. That is right for product work and wrong for a
    prompt containing one of Sheraj's friends' names — the Material World lives in
    `private/` precisely so those names stay on this machine. `call_local` takes
    no `agent=` parameter, so there is nothing to override; the suite asserts
    the routed call is never reached. Claude is not an option here either
    (rule 16). Two things follow from the model being small and local:
    - **A draft is never sent as written.** It lands in an editable box and
      Sheraj sends it, so nothing here is autonomous.
    - **Invented specifics are FLAGGED in code**, because prompt compliance is
      not trusted for anything a friend will act on (rule 4's reasoning). Asked
      only to "invite them on Friday", Qwen wrote "around 7" on the very first
      real draft. `invented_specifics()` points at any time, day, date or
      amount the message asserts that was not supplied, and the drawer shows
      it. It does NOT edit — there is no safe mechanical rewrite of free
      prose — and the prompt asks for a `[time]` blank rather than a guess.
68. **Reads cross the bridge; writes do not.** No friend's name, no group name
    and no group link is ever written into `workforce.db`.
    `nuclei_bridge.assert_no_personal_leak` refuses a recipient, a number or a
    link riding along in anything handed to the workforce side — narrow on
    purpose and it says so, the same discipline as
    `secretary_colony.assert_shareable` (rule 50). The suite proves the
    invariant the strong way: it adds a person over HTTP, exercises `/colony`
    and `/nuclei/workforce`, then reads `workforce.db` as BYTES and requires
    the name to be absent. Two consequences:
    - **Sending reuses rule 28's tiers untouched.** `send_to_contact` sends
      directly only to the owner or an allowlisted contact; anyone else becomes
      a `pending_actions` row of kind `whatsapp_send`, the same unified queue.
      Sheraj clicking Send in the drawer is owner action, not a relaxation — no
      new path to an un-allowlisted number is opened.
    - **No model has a door to any of this.** Nothing in `colony_tools` or
      `secretary_tools` exposes the nuclei, the channels or the workforce
      roster, and the suite asserts no tool name reaches them — the same
      control that makes the wallet allowlist (rule 42) and the WhatsApp
      allowlist (rule 28) survive a prompt injection.
68b. **Switching worlds folds one sky into the workforce light and grows the
    other out of it** (owner ask 2026-08-17). Both skies use the same
    1440x720 space and both draw that light, so it is the only body that
    survives the swap and is therefore the anchor. Four things are
    load-bearing:
    - **The map is the FIRST child in both worlds.** The Material World's "add a
      nucleus" rows were moved BELOW its map for this: a control row above it
      sat the workforce light ~110px lower in one world than in the other, and
      the dot visibly jumped at the swap. Do not move them back on top.
    - **The Digital World is handed the Material World's SCREEN anchor**
      (`workforceScreenAnchor` — the light's fixed position pushed through that
      view's saved camera), because the Material World pans and zooms and the
      Digital World has no camera of its own. The hinge dot is drawn OUTSIDE
      the camera in both, so it is the same size however far the Material World is
      zoomed.
    - **The incoming sky opens itself one PAINTED frame after mount**
      (`mounted` + `requestAnimationFrame`), the same pattern as the family
      bloom. Setting the open state in the same tick as the mount gives the
      browser only the end state and the transition has nothing to run from.
    - **`WORLD_MORPH_MS` and the CSS duration are one timing**, and the fold is
      what sequences the swap — change `colony/layout.ts` and
      `.colony-world-morph` in `index.css` together, or the world flips before
      the fold has finished. The opacity fade is deliberately DELAYED behind
      the scale so the bodies are seen travelling rather than winking out, and
      the whole thing is stilled under `prefers-reduced-motion`.


## Rule 69 — junior youth groups

69. **A junior youth group's people are the youth and the animators, recorded as facets.** (Owner ask 2026-08-18.) The grouping used to list families; the group itself is the junior youth in it and who animates it. Those parts are exclusive `group_role` facet kinds (`jy_youth`, `primary_animator`, `sub_animator`) — data, like every other facet (rule 63), not a JY-only column. They may only be set on a live membership of a `junior_youth` grouping, and only on a person: a household is not a junior youth or an animator. Marking an animator also sets the existing `animating` service facet on that same membership (and taking them off the animator role ends it), so the two lists stay honest and they sit closer the same way any animator does. The drawer lists the kids and the animators from those facets; it never grades them (rule 61). A person already inside a family that sits at this group does not get a second light for being marked as in it — they stay in the family light (rule 62). Ticking them in the family box records the membership and the role; it does not copy them onto the sky.

