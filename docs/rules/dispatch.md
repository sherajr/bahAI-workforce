# Appendix — dispatching work to the Grok / Codex / Antigravity CLIs

How to hand a precisely-scoped task to another CLI tool and re-verify its result —
moved intact from the root `AGENTS.md`. Loaded on demand from the root
`AGENTS.md` routing table; do not copy this into CLAUDE.md or the root AGENTS.md.

## Appendix — dispatching work to the Grok / Codex / Antigravity CLIs

`grok`, `codex` and `agy` are installed and authenticated on this machine, so
Claude Code can act as an orchestration layer: scope a task precisely against
the real code yourself, dispatch it headlessly, then re-verify the result
independently. An imprecisely-scoped task is the most likely way a dispatched
agent does the wrong thing confidently, and a dispatched agent's own "verified"
claim is never the last word — re-run the check and read the whole `git diff`.

```bash
# Grok — run in the FOREGROUND for any dispatch carrying edit/write permission:
# acceptEdits disables its own approval prompts, so a human has to be waiting.
grok --prompt-file <task-prompt> --worktree <name> \
  --allow "Edit" --allow "Write" --allow "Bash(python -c*)" --allow "Bash(grep*)" \
  --deny "Bash(git push*)" --deny "Bash(git commit*)" --deny "Bash(rm*)" --deny "Bash(git reset*)" \
  --permission-mode acceptEdits --max-turns 20 --output-format plain

# Codex (cloud is the default; valid slugs: gpt-5.5, gpt-5.4, gpt-5.4-mini —
# the gpt-*-codex names are rejected on a ChatGPT account).
# Sandbox: read-only | workspace-write | danger-full-access.
codex exec -s read-only - < <prompt-file>

# Antigravity — --mode plan is read-only, --mode accept-edits is scoped
# auto-approval (no --allow/--deny, no --worktree: isolate with git worktree).
agy --mode plan --print-timeout 9m --add-dir <repo> -p "<prompt>"
```

Three hard-won invocation gotchas:

- **`--worktree` did NOT actually isolate** a headless `--prompt-file` run
  (grok 0.2.91): no worktree was created and Grok edited the main tree directly.
  Never assume isolation — check `git status`/`git diff` immediately after.
  (The pre-existing worktree at `.grok/worktrees/...` is unrelated; leave it.)
- **`agy -p/--print` takes the prompt as its own value**, so
  `agy --print --mode plan "<prompt>"` feeds the literal string `--mode` to the
  model. Put `-p "<prompt>"` LAST.
- **`agy` doesn't treat the shell's cwd as its workspace** — without
  `--add-dir <repo>` it runs in a scratch directory and cannot see the repo.
- **Codex has corrupted files on Windows**: one dispatch rewrote `api.py` and
  `requirements.txt` with a BOM and cp1252 mojibake (every em dash and arrow
  mangled) while completing the task correctly otherwise. `git diff` caught it
  and the whole output was reverted. Read the diff for encoding damage, not just
  for logic, after any Codex dispatch that writes files.

Codex's model routing lives in `~/.codex/config.toml` (`model = "gpt-5.5"`,
`model_provider = "openai"`); the desktop app also writes that file, so if a
dispatch suddenly fails, re-apply those lines or pass per-invocation overrides
(`-c model_provider=openai -m gpt-5.5`), which always win.
