Context: bahAI Workforce's Secretary agent recently got a Google Workspace
expansion (Gmail/Drive/Docs/Sheets/Slides-read, native Python, no MCP —
CLAUDE.md rules 22-25; agents/google_auth.py, gcal.py, gmail.py, gdrive.py,
gdocs.py, gsheets.py, gslides.py, secretary_tools.py, router.py's
call_claude_agentic, secretary.py). Sheraj reconnected Google with the wider
scope list. Latest live testing result:

- Gmail read: WORKING (real inbox summarized correctly).
- Google Docs create: WORKING (created a real Doc, confirmed by ID).
- Google Sheets read/create: WORKING, but append is rough — no "insert at a
  specific row" capability (append-only, lands at the end), no "copy an
  existing sheet" capability, and on a ~173-row bulk-fill task the model
  twice DESCRIBED appending rows in plain prose instead of emitting the real
  `<sheet_append>` tags (self-caught both times, but only after Sheraj
  pointed out nothing showed up).
- Google Calendar event creation: BROKEN even on a trivial case ("water the
  garden tomorrow 7am" — a single unambiguous time, no OAuth scope gap since
  Calendar was already working before this expansion). The chat reply showed
  a stray `Water the garden</event>` — an orphaned closing tag with no
  matching opening tag — meaning the model's `<event ...>` tag never
  matched agents/secretary.py's `_EVENT_RE` regex at all (if it had matched,
  `.sub("", ...)` would have removed the entire tag INCLUDING "Water the
  garden", not left it behind). No error surfaced either, because until this
  fix nothing detected a malformed/partial tag as distinct from "no tag
  emitted at all" -- total silent failure.

I (a previous Claude session) already found and fixed the root architectural
gap tonight, don't redo this investigation:
1. `agents/secretary.py`'s `_STRAY_CLOSE_RE` only mopped up 3 self-closing
   tags' stray remainders (`</event_update>`, `</event_delete>`,
   `</drive_organize>`) — never `</event>`, `</sheet_append>`, etc. Replaced
   it with a generalized `_STRAY_TAG_RE` covering every known tag name, open
   or close form. Anything still matching it after all the real per-tag
   regexes have already consumed well-formed pairs is, by definition, a
   malformed tag the model half-emitted.
2. When that fires now: the raw (pre-cleanup) model reply gets appended to
   `private/secretary_tag_debug.log` (git-ignored, personal-data rule
   CLAUDE.md #15 respected) for forensic diagnosis next time this happens,
   the stray fragment is stripped from what Sheraj sees, and
   `effects["errors"]` gets a line so `_ground_truth_confirmation` shows a
   visible "Didn't go through" warning line instead of silence.
3. Added a system-prompt clarification for multi-day all-day events (the
   "SCP Into the Woods" tech-week request spanned dates with no times yet) —
   Google Calendar's all-day end date is EXCLUSIVE, so a range covering
   Aug 30 through Sep 4 needs `end="2026-09-05"`; the model is now told not
   to skip the tag or ask for exact times just because none were given.
4. Verified offline only (regex/parsing logic, no live API calls): a
   well-formed tag still parses/strips correctly (no regression), and the
   exact observed failure text now produces a visible error + debug log
   entry instead of vanishing silently.

What is NOT yet resolved (the actual open question): WHY does the model
sometimes emit a malformed/partial tag (or describe an action in prose
without a real tag at all) instead of the well-formed syntax, even when the
system prompt explicitly and repeatedly instructs it not to? This has now
happened for both `<event>` and `<sheet_append>`/`<sheet_create>`. The fix
above makes the NEXT occurrence diagnosable (real raw text will land in
`private/secretary_tag_debug.log`) and visible to Sheraj instead of silent,
but does not explain the underlying cause.

Your job, staying TOKEN-AWARE throughout — these files are already known,
do not re-explore the codebase broadly, do not make more than a small number
of live Claude/Google API calls, and do not retry-loop on anything:

1. Read `private/secretary_tag_debug.log` if it exists (it won't unless the
   bug has reproduced since this fix shipped) — if there are entries, that's
   your ground truth for what malformed text actually gets generated. Only
   if the file is empty/missing should you consider live-reproducing (see
   step 2), and even then, ONE attempt, not several.
2. Optional, only if step 1 gave nothing: send ONE test message through
   `agents/secretary.py`'s `chat()` asking for a simple, unambiguous new
   calendar event (a novel test event, not reusing "water the garden" so it
   doesn't collide with any real reminder Sheraj may have set). Check
   `private/secretary_tag_debug.log` afterward. Delete/cancel the test event
   from the calendar afterward if it did get created (use
   `agents.gcal.search_events` to confirm either way, then
   `agents.gcal.delete_event` if it exists) so no test artifact lingers on
   Sheraj's real calendar.
3. If a debug-log entry (from either step) shows the model wrote a
   plausible-looking tag that still failed to match, diagnose why against
   the actual regexes in secretary.py (`_EVENT_RE`, `_ATTR_RE`, etc.) — e.g.
   smart/curly quotes instead of straight quotes, a missing required
   attribute, wrapped in markdown code fences that somehow altered
   whitespace, truncation from the `max_tokens=1500` cap on `call_claude_agentic`.
   Fix the actual regex/parsing gap if one is found (e.g. widen `_ATTR_RE`
   to also accept smart quotes if that's the culprit).
4. If nothing structural turns up in the tag markup itself and it looks like
   the model is simply choosing not to include the tag some of the time
   (a genuine instruction-following miss, not a parsing bug), that likely
   means the tag surface has grown too large for reliable one-shot
   compliance now that Calendar + 6 Workspace tag types all live in one
   system prompt. Consider (but don't implement without flagging back to
   Sheraj first — this changes established behavior): trimming/tightening
   `_SECRETARY_INSTRUCTIONS`, or adding one clear compact example block
   showing ALL tag types together near the top of the instructions rather
   than scattered under separate headers.
5. Report back in plain, non-technical language for Sheraj: what you found
   (or didn't), whether it's fixed now or still needs a live reproduction to
   catch it in the act, and any concrete next step. Do not present
   speculation as a confirmed fix — say plainly if the true root cause is
   still unconfirmed and the safety net (visible error + debug log) is the
   best available mitigation for now.

Separately, lower priority, only mention in your report rather than acting
on it unless Sheraj asks: Sheets has no "insert at row N" or "copy sheet"
tool right now (by design scope, not a bug) — worth flagging as a possible
future enhancement, not something to build today.

---

## RESOLVED (2026-07-07, follow-up session)

Root causes found by reading the real chat history (`private/secretary.db`
messages #44-117) and correlating with the per-round `claude_chat` spend
records in `workforce.db` — no live reproduction needed. Three distinct
mechanical bugs, none of them "the model choosing to disobey":

1. **`router.call_claude_agentic` returned only the FINAL round's text.**
   Tags the model emitted before/between tool calls were silently discarded
   (the model, seeing its own tag in conversation history, believed it had
   acted). The bulk-fill turns provably ran 6/6 rounds (spend records
   06:58:23-47 and 07:13:18-07:14:16 UTC). This was the "described appending
   rows in prose" mystery. Fixed: all rounds' text is now concatenated;
   `_apply_intents` dedupes character-identical repeat tags.
2. **Tag grammar inconsistency**: `<event>` takes its title in the BODY, but
   `<event_update>` was attributes-only self-closing. The model carried the
   body shape over (`<event_update ref=..>new description</event_update>`,
   messages #51/#53) — the opening tag matched and the update ran WITHOUT
   the description (body ignored), leaving `...</event_update>` visible.
   Note: `<event>` creation itself was never broken (#49 succeeded); the
   original report's `</event>` example was a paraphrase of the
   `</event_update>` failures. Fixed: self-closing tags now also accept a
   container body; event_update maps the body to `description`.
3. **`max_tokens=1500` truncated bulk replies mid-generation** (message
   #109 ends mid-word). Raised to 4000, truncation now appends a visible
   notice (`router.TRUNCATION_NOTICE`), and a reply ending in an unclosed
   partial tag is detected (`_PARTIAL_TAG_RE`) and reported.

Plus: `<sheet_append>` now takes many rows per tag (one CSV line each,
quoted values may contain commas) via one batched `gsheets.append_rows`
call, and `<sheet_create>` accepts header + initial data rows — the 173-row
fill that triggered all this is now a handful of tags instead of 173.
Offline-verified with 32 mocked parser/loop tests; no regressions to
well-formed tags.

## FOLLOW-UP (same day, live test session): the real dominant failure

Sheraj re-tested live immediately after the fix above shipped. Result: the
parsing fixes held up fine, but a THIRD, much more common failure mode
showed up that neither of us had caught yet — a plain instruction-following
miss, exactly the "bucket 4" possibility flagged (and deliberately not
acted on) in the original investigation above. In a ~14-turn session, the
model wrote confident commitment sentences ("Adding that now", "Trashing it
now, for real, tag included", "Setting that reminder now") and then, in the
large majority of those turns, emitted NO tag markup at all — not even a
malformed fragment. Confirmed empirically: `private/secretary.db`'s
`reminders` table was completely empty even after "Setting that reminder
now for tomorrow" was the model's literal reply, and the spend log showed
these were ordinary single-round replies (no multi-round collapsing at
play). The debug log only had 2 entries because it can only catch markup
that's PRESENT but broken — it had no way to notice markup that's simply
absent.

Likely mechanism (not fully provable, but consistent with the transcript):
once the model produced one text-only "did it" reply early in the session,
subsequent replies within the same conversation history kept imitating
that shape rather than self-correcting, even across many separate retries
and even when Sheraj re-sent the identical original request verbatim.

Two changes shipped for this:
1. **A new heuristic safety net in `secretary._apply_intents`**
   (`_looks_like_untagged_commitment` / `_ACTION_VERB_RE` /
   `_ACTION_MARKER_RE`): if a reply reads like a commitment to act (an
   action verb near a word like "now"/"again"/"for real") but produced
   zero effects and no malformed-tag match either, it's flagged immediately
   as "that sounded like I was taking an action, but I can't find a real
   action tag" — visible the SAME turn, not several turns later when
   Sheraj happens to check manually. Deliberately a loose heuristic (verb +
   marker co-occurrence anywhere in the reply) since false positives just
   add a harmless extra line; false negatives are the pre-existing status
   quo.
2. **Prompt hardening**: an unmissable "THE #1 WAY YOU FAIL HIM" block
   added at the very top of the instructions, naming the exact failure
   pattern in plain terms (a sentence is not an action; only the literal
   tag characters act), plus a single consolidated one-tag-per-line
   cheat-sheet up front instead of the tag syntax being scattered across
   several headers further down.

Verified offline (39 mocked tests total) including reproducing all three
exact failure sentences from tonight's transcript and confirming the new
detector catches them, while a real successful action using the same
"now"/"again" wording is never falsely flagged. **Not yet re-verified
live** — the prompt change is a mitigation for a model behavior tendency,
not a guarantee it won't recur; the code-side detector is what makes the
next occurrence (if any) visible immediately instead of silent.

## ARCHITECTURE MIGRATION (same day, immediately after): tags retired entirely

Sheraj re-tested live again right after the prompt-hardening fix above and
the SAME failure recurred (`Adding it now — July 8, 3:00–4:00pm, "Secretary
Test A":` followed by nothing — correctly caught this time by the new
detector, but still nothing actually happened). Confirmed this was the same
class as the FOLLOW-UP section above, not a new bug. He asked directly
whether switching to real Claude tool-calling would help instead of
continuing to patch a design built on custom text markup, and agreed to
the larger migration over further patching.

**Root cause of the whole bug family, in retrospect:** asking the model to
embed hand-rolled `<event>`/`<sheet_append>`/etc. syntax inside its own
free-text reply, then parsing that text with regex, was never a reliable
channel — three separate bug classes (dropped multi-round text, tag-shape
mismatches, and the model narrating "adding it now" without ever writing
the markup at all) all trace back to that one design choice. Claude's
native tool-calling is a structurally different mechanism: the model emits
a schema-validated function call as part of its response, not markup
embedded in prose it also has to remember to include.

**What changed:**
- `agents/secretary_tools.py`: every action that used to be a text tag
  (`remember`, `add_task`, `event`/`event_update`/`event_delete`,
  `remind_event`, `remind`→`set_reminder`, `email`→`send_email`,
  `doc_create`/`doc_append`, `sheet_create`/`append_sheet_rows`,
  `drive_organize`→`organize_drive_file`) is now a real Claude tool in
  `WRITE_TOOLS`, with the exact same ownership/approval gating that used to
  live in `secretary._apply_intents` moved into each tool's handler inside
  `make_executor`. Rows for Sheets are now native JSON arrays instead of
  hand-parsed CSV-in-a-tag-body — eliminates that whole class of quoting
  bugs outright. A write tool called twice with byte-identical arguments in
  one turn executes only once (in-memory dedup keyed on the call's JSON).
- `agents/secretary.py`: `_apply_intents` and the entire regex tag-parsing
  apparatus (`_REMEMBER_RE` … `_DRIVE_ORGANIZE_RE`, `_ATTR_RE`,
  `_STRAY_TAG_RE`, `_PARTIAL_TAG_RE`, `_iter_unique`, `_csv_rows`) are gone
  — actions execute live during the tool-calling loop, not parsed out of
  the finished reply. `_finalize_reply` replaces it: mostly just whitespace
  cleanup now, plus the SAME heuristic backstop from the previous section
  (renamed `_looks_like_uncommitted_action`), now checking whether any real
  tool call populated `effects` rather than whether tag markup parsed —
  still there because the model narrating without calling anything is
  still structurally possible, just expected to be much rarer.
- The system prompt shrank substantially: tool schemas (names, required
  fields, descriptions) now carry the syntax Claude needs, so the prompt's
  job is just behavior/policy (call tools instead of narrating, relay the
  tool's real result honestly, ownership/approval expectations) rather than
  teaching a custom markup language.
- CLAUDE.md rules 18, 22, 25 rewritten to describe the tool-calling
  architecture; rule 22's read-only constraint is explicitly lifted (write
  tools are now allowed in the loop) with the safety rationale spelled out.

**Honest status:** offline-verified only — 53 mocked tests covering every
write tool's happy path, its ownership-gated path, bad/missing input, the
duplicate-call guard, and exception handling, plus the finalize-reply
heuristic and the router's multi-round text handling. This has NOT been
tested against the real Claude API or real Google services yet. The
expectation, not yet proven, is that native tool-calling will be
substantially more reliable than the text-tag design was — that needs a
real live session to confirm, ideally re-running the exact test messages
that failed tonight (event update, bulk sheet append, event creation
retried after a "not there yet" report).
