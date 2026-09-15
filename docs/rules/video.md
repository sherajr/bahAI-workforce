# Rules 30-34, 58 — the Video pipeline

Canonical numbered hard rules for this subsystem. **Numbers are permanent — never
renumber, only append.** Cited from code comments across the repo.
Loaded on demand from the root `AGENTS.md` routing table; do not copy these
into CLAUDE.md or the root AGENTS.md.

## Rules 30–34, 58 — the Video pipeline

Turns a **scene, story, historical account or passage** into many simple 3–4
second shots that assemble into a coherent video. A bookmark or quote card can
be the SOURCE, but this is a general story-to-video tool, not a card-video tool
— keep that emphasis in any UI change. Verify with
`scripts/test_video_pipeline.py`.

**The governing design principle:** break a complex story into many simple shots
rather than asking a small video model for one complicated one. An 8GB-class
local model cannot resolve two simultaneous actions, a moving camera over a
moving crowd, or a location change mid-clip. Everything below follows from that.

Modules: `video_store.py` (persistence in `workforce.db` —
`video_projects`/`video_shots`/`video_assets`/`video_jobs`, paths only),
`video_director.py` (the LLM stages: story analysis → continuity bible →
per-BEAT shot planning, beat-by-beat so each prompt stays inside Qwen's context,
rule 1), `video_safety.py` (rule 30), `video_provider.py` (provider adapters,
capability detection, fallback selection), `video_pipeline.py` (orchestration:
`build_plan`, `generate_frames`, `generate_clips`, `generate_chained`,
`resume_state`), `video_assembly.py` (validation, ffmpeg assembly, export,
the finished-video shelf) and `videographer.py` (the ComfyUI HTTP client).

ComfyUI is **not part of this repo** — a separate portable install
(`C:\Users\Sheraj\ComfyUI_windows_portable`, its own Desktop shortcut) reached
over HTTP (`COMFYUI_URL`, default `http://127.0.0.1:8188`): `/prompt` → poll
`/history/{id}` → `/view`, with source images uploaded via `/upload/image`
rather than assuming shared filesystem access. `is_server_up()` gates every call
and raises `VideoGenerationError` telling the user to launch the shortcut,
rather than hanging. Local generation has no cloud cost so nothing meters it;
frame generation goes through `artist.generate_image` (xAI), which meters itself
at its own chokepoint.

Two invariants outside the numbered rules, both learned from real incidents:

- On the LTX graph, `LTXVScheduler`'s `terminal` is hardcoded to `0.1` and
  deliberately NOT exposed as a parameter — dropping it to `0.0` silently
  renders every frame pure black with no error (A/B confirmed 2026-08-12).
- **`state.init_db()` calls `init_video_db()` OUTSIDE its own `with _connect()`
  block.** Nested inside, SQLite refuses the second writer ("database is
  locked"), the video tables are silently skipped on a fresh database, and every
  video endpoint then fails with "no such table". The suite has a fresh-DB
  regression test for exactly this. `init_colony_db()` is called the same way,
  for the same reason.

30. **Manifestations of God are never depicted visually.** `video_safety.py`
    enforces this DETERMINISTICALLY in code — same class as `_sanitize_claims`
    (rule 4) and the code-appended disclaimers (rule 8) — because prompt
    compliance is never trusted for a reverence-critical guarantee. It is
    deliberately ASYMMETRIC: the VISUAL fields (`subject`, `primary_action`,
    `first/last_frame_prompt`, `motion_prompt`) are rewritten to an indirect
    treatment (reactions, environment, objects, an empty threshold), while
    `narration` is left ALONE — naming Them with reverence in narration is the
    intended outcome, not a violation. Every rewrite is reported, never silent.
    `'Abdu'l-Bahá` and Shoghi Effendi are NOT Manifestations and may be depicted
    normally. **Matching normalises text first** (`_normalize`: strip diacritics,
    remove apostrophe-likes) — the first version matched a raw ASCII `'` and so
    missed `Bahá’u’lláh` with the typographic apostrophe this repo and every
    real source actually use, while its own leak-check shared the broken matcher
    and reported all-clear. Never "simplify" that back to a plain regex over raw
    text.
30b. **Detail and complexity are SEPARATE axes — maximise one, minimise the
    other.** (Owner ask, 2026-08-12.) Prompts must be *extremely* detailed
    (materials, texture, light source/direction/colour temperature, atmosphere,
    depth, optics) while the shot stays visually simple: one subject, one
    action, one camera behaviour. LTX-Video's docs warn that short prompts
    "suffer greatly", and **neither encoder truncates** —
    `comfy/text_encoders/lt.py` and `wan.py` both set `max_length=99999999`
    (Wan pads to a 512-token minimum), so length is free quality. Do not undo:
    `build_frame_prompt`/`build_motion_prompt` emit flowing PROSE, not
    comma-separated tags (these models are trained on natural-language captions;
    tag soup reads as a short prompt however many tags it has), and
    `complexity_score` measures ONLY narrative/camera load, never verbosity —
    `_HANDS` matches hand-INTENSIVE work, not any mention of hands ("his
    weathered hands rest on the pommel" is texture); `_CROWD` is waived by
    `_CROWD_OK` for a distant/still/blurred crowd; and the action word-cap
    applies to `primary_action` alone, never to the intentionally verbose
    frame/motion prompts. Wire detail into `complexity_score` and every rich
    shot gets split for nothing.
31. **Shots are 3–4 seconds, enforced in code.** `clamp_duration` corrects
    whatever the model returns; `split_complex_shots` measures
    `complexity_score` (sequential actions, crowds, hand-work, complex camera,
    >2 characters) and mechanically splits anything over `COMPLEXITY_LIMIT`. A
    model asked to "keep it simple" regularly does not — the code is the
    guarantee, and it reports every split it makes.
32. **Never claim a provider capability it does not have.**
    `videographer.FLF_SUPPORT` is hardcoded from an EMPIRICAL probe, not from
    ComfyUI's node list, because both would lie: `WanFirstLastFrameToVideo`
    exists, submits, and completes in 21s against Wan 2.2 TI2V-5B — and returns
    corrupted garbage (probe 2026-08-12: mean abs difference from the
    conditioning image 97/255, vs ~23 for a working run; visually a smeared
    colour field). Node presence AND a clean exit both reported "supported";
    only inspecting pixels revealed the truth. `resolve_strategy` therefore
    falls back (native FLF → first-frame i2v → chain-extract → text-only) and
    returns the reason, which the UI SHOWS. Re-probe before flipping any of it.
    The mock provider labels every asset `is_mock` end to end and must never be
    presentable as real generation.
33. **Continuity comes from locked descriptions, not from hope.** The continuity
    bible assigns stable ids; `build_frame_prompt` assembles every frame prompt
    from the shot PLUS the locked descriptions of the ids it references, in
    code, so a regenerated frame cannot quietly lose them. A `continuous` shot
    reuses the previous shot's last frame as its own first frame (literally the
    same file — position, costume, lighting and screen direction cannot drift);
    an `editorial_cut` generates a new first frame. Locking a field stops
    REGENERATION from changing it, never the owner: a human edit passes
    `force_locked=True`, the same spirit as the manual `PATCH /products/{id}`
    override.
33a. **Chained generation is the DEFAULT way to render a video, because
    independent generation looks like a slideshow.** (Owner report, 2026-08-12:
    "kind of like a trippy slide show".) `generate_frames` + `generate_clips`
    render every shot from its own text prompt, so each invents its own version
    of the character and place. `generate_chained()` threads real output
    forward: shot 1's clip is rendered, its ACTUAL final frame extracted with
    `videographer.extract_last_frame`, and that file becomes shot 2's first
    frame — each clip starts on the pixels the previous one ended on. What holds
    it together:
    - A `continuous` shot REUSES the extracted frame directly. An
      `editorial_cut` must generate a new frame (the angle changes on purpose)
      but is anchored by `video_director.observe_frame()` — a vision read of
      what the previous clip *actually* showed, folded in via
      `build_continuation_prompt` — so identity carries across the cut.
      `adapt=False` skips that paid call; the chain still works, with weaker
      carry-over at cuts.
    - `ClipSpec.image_strength=1.0` for chained runs. LTX's template default of
      0.15 means "loosely inspired by this image", which is wrong when the image
      IS the previous clip's last frame. Non-chained runs keep 0.15.
    - **PyAV is a hard dependency of chaining**, not an optional extra: without
      it every link silently degrades to an independent shot — exactly the
      problem being fixed. `generate_chained` preflights the import and refuses
      to start rather than producing a slideshow and calling it a chain.
    - On a shot failure the carry frame is CLEARED, so the next shot restarts
      the chain honestly instead of appearing to continue from a clip that
      doesn't exist.
33b. **Planning must survive a failed beat, and its calls need long timeouts.**
    Shot planning makes one LLM call PER STORY BEAT, each asking for hundreds of
    words, so a 7-beat plan is 10–15 minutes on local Qwen — and the router's
    120s default produced a real mid-plan read timeout that discarded six
    completed beats (2026-08-12). Three consequences, none to be undone:
    `call_llm` takes a `timeout` override and the Director passes
    `ANALYSIS/BIBLE/SHOT_TIMEOUT_S`; `plan_beat_shots` retries once with a
    LEANER prompt (a repeat of the same oversized request just fails the same
    way); and `build_plan` catches a beat failure, inserts a placeholder shot
    flagged `needs_replanning`, and CONTINUES — the project ends
    `planned_with_gaps` with the reason in `notes`, never zero shots.
    `VIDEO_DIRECTOR_MODEL=grok` opts planning onto the paid API when speed
    matters more than cost; the default stays local and free.
33c. **The Video tab's UI state is persisted** (`settings.getVideoUi` /
    `patchVideoUi`, the same localStorage pattern as the Pipeline tab's active
    job). Switching tabs unmounts the panel, and a multi-stage pipeline that
    forgets the open project, the sub-tab and the running job every time the
    user looks elsewhere is unusable. `useVideoJob` reattaches to a persisted
    job id and treats a 404 as "the API restarted, the job is gone" rather than
    polling a dead id for ever.
33d. **A shot's movement description is repaired in CODE, never trusted from the
    planner.** (`video_director.repair_motion`, run by `build_plan` and exposed
    as `POST /video/projects/{id}/repair-motion`.) Measured on real finished
    projects 2026-08-13: of 17 shots, **8 told the video model that nothing
    moves**, 4 repeated the previous shot's motion text verbatim, and 6
    `continuous` shots declared a framing the frame they reuse cannot have. All
    three produce the symptom the owner called "trippy" — handed a real first
    frame at `image_strength=1.0` with no movement to render, the model holds
    the composition and dissolves the texture. So, in code:
    - Stillness clauses are stripped CLAUSE-BY-CLAUSE, never word-by-word
      (deleting "no" inverts the meaning instead of removing it), and background
      stillness is re-added by the code-owned tail so it can never contradict
      the subject's own action.
    - A motion prompt duplicated from the previous shot is rebuilt from THIS
      shot's action, keeping any trailing AMBIENT sentence (one that doesn't
      mention the subject) so texture isn't thrown away with the error.
    - A `continuous` shot inherits the previous shot's `framing` and
      `camera_angle`: it reuses that clip's final frame, so a different declared
      setup is a claim the pixels cannot honour.
    Every repair is REPORTED and locked fields are left alone. Story-level
    repetition (the same action planned four times) is only ever WARNED about
    (`repeated_action_warnings`) — deciding an action was meant to happen once
    is a story judgement, not a mechanical one. Honest limit: an A/B over
    2 shots × 2 seeds moved the real-motion / morphing ratio 0.21 → 0.24, inside
    the noise. These defects are fixed because they are objectively wrong, not
    because a metric proved a large win.
33e. **A chained run refuses SYNCHRONOUSLY** (`chain_preflight`, called by the
    endpoint before `_start_job`, returning HTTP 400). Raised inside the job
    thread instead, the message reached the dashboard as a job error that
    `useVideoJob`'s `onDone` cleared on the same tick — the owner saw a click
    that appeared to do nothing and worked around it by generating the first
    frame by hand (2026-08-13). Two halves, both load-bearing: the preflight
    stays ahead of the job, and a FINISHED job's error stays on screen until the
    next job starts. The chain has always been able to start cold, from no
    frames at all — `test_cold_start_chain` pins that so the two failure modes
    are never confused again.
33f. **Pacing is a planning MODE, and cut count — not shot count — is the pacing
    a viewer feels.** (`direction.pacing`: `standard` | `cinematic`; owner ask
    2026-08-13 for "fewer, longer, non-overlapping beats".) Chained `continuous`
    shots are ONE unbroken take, each clip starting on the previous clip's real
    final frame — four continuous 4s shots read as a single 16-second take, not
    four cuts. Measured before this existed: "Adam's New Day" cut every 4.7s
    across 122s. `cinematic` does three deterministic things:
    - `beat_shot_budget` treats the clock's share as a CEILING and caps it at
      the beat's `distinct_moments` (a new analysis field). The video comes in
      SHORTER than the target rather than padded with restatements, and says so
      in the notes. Do not "fix" that by refilling to the target.
    - `dedupe_shots` REMOVES a moment an earlier nearby shot already covers
      (window 4, same similarity measure as `repeated_action_warnings`); the
      survivor keeps the better text at the EARLIER position. Standard pacing
      only warns — deleting a shot the owner may have meant is a story decision,
      and cinematic is where that decision is explicit.
    - `enforce_cut_policy` cuts only at beat boundaries and at real changes of
      place or time. A location or time-of-day change ALWAYS cuts even inside a
      beat: a `continuous` shot reuses the previous frame, so claiming
      continuity across a real change is a lie the pixels cannot tell.
    `MAX_SHOT_SECONDS` is NOT raised for this — rule 31's ceiling is a hardware
    fact, not a preference; cinematic pins shots to the top of the existing
    window. `POST .../repair-motion {"recut": true}` applies the policy to an
    already-planned project (26 cuts → 10 on a real 35-shot project) and never
    deletes a shot, because a dropped shot may already have a rendered clip.
34. **Everything is resumable at shot granularity.** Assets are written the
    moment they exist and a re-run skips shots that already have them, so
    closing the app costs at most the shot in flight. `mark_interrupted_jobs()`
    runs at startup to flip jobs a killed process left as `running` — that is
    what makes "Resume" truthful rather than a guess. Clip generation is
    SEQUENTIAL on purpose: two clips in flight on an 8GB card is an
    out-of-memory error, not throughput.
58. **A finished video reaches the Products shelf DERIVED, never copied.**
    (Owner ask 2026-08-15.) `video_assembly.list_finished()` +
    `GET /video/finished` build each entry from the video tables on every read;
    the Products tab merges them into its grid. A video is deliberately NOT a
    `products` row: that would double-count it in the Steward's ledger, hand the
    print sheet / layout editor / Etsy publish a `product_type` they cannot act
    on, and drift the moment a project is re-assembled or deleted. Four
    guarantees, pinned by `test_finished_shelf`:
    - **Only a file that exists is shelved** — one whose mp4 was deleted drops
      off rather than showing a player that cannot play (`include_missing=True`
      reports the gap for callers that want it).
    - **The length is MEASURED, not planned** (122.5s of plan measured 110.4s on
      a real project). ffprobe costs seconds and this list is polled, so it runs
      ONCE per file and is remembered on the export record against the exact
      path it measured; a re-assembly writes a new filename, so a stored value
      can never describe a different video. Unmeasurable falls back to the plan
      flagged `duration_measured: false` (the UI prefixes "~") — a fallback is
      never stored as a measurement.
    - **A mock-built draft says so** (rule 32), from the clip assets' own
      `is_mock` labels.
    - **Videos drop out while a review-result filter is active**, and the bar
      says why: they are reviewed shot by shot, not scored out of 10 (rules
      14/35). The shelf otherwise filters by kind, search and sort; the chosen
      view persists, the typed SEARCH does not — returning to a nearly-empty
      shelf because of a forgotten query is the failure the bar prevents.

