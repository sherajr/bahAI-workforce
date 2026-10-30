"""
The Secretary's private store — the ONLY module that touches Sheraj's personal
data at rest (CLAUDE.md hard rule: everything personal lives in private/ and
only there).

Two surfaces:
  private/secretary.db   — SQLite: conversations, check-ins, streaks, tasks,
                           reminders, contacts (WhatsApp allowlist), pending
                           actions (the one approval queue for every gated
                           write — calendar, Drive, Gmail, WhatsApp), settings.
  private/memory/*.md    — her long-term notes about Sheraj, plain markdown so
                           he can open and read every one of them himself.

Nothing in here may be imported for the purpose of writing personal content
into workforce.db, log_run summaries, or job progress strings.
"""

import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

PRIVATE_DIR = Path(__file__).parent.parent / "private"
MEMORY_DIR = PRIVATE_DIR / "memory"
DB_PATH = PRIVATE_DIR / "secretary.db"


def _connect() -> sqlite3.Connection:
    PRIVATE_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create the full Phase 1–4 schema. Later phases fill the empty tables."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,               -- 'user' | 'assistant'
                content TEXT NOT NULL,
                channel TEXT DEFAULT 'dashboard', -- 'dashboard' | 'whatsapp'
                sender TEXT,                      -- NULL = owner thread; guest name for guest threads
                ts TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS checkins (       -- Phase 4
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,               -- 'morning' | 'evening'
                content TEXT,
                ts TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS streaks (        -- Phase 4
                name TEXT PRIMARY KEY,            -- e.g. 'vape_free'
                started_on TEXT,
                best_days INTEGER DEFAULT 0,
                last_checkin TEXT
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                description TEXT NOT NULL,
                due TEXT,
                done INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS reminders (      -- Phase 2 scheduler
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message TEXT NOT NULL,
                fire_at TEXT NOT NULL,
                recurrence TEXT,                  -- NULL = one-off
                wake_me INTEGER DEFAULT 0,        -- overrides quiet hours
                fired INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS contacts (       -- Phase 3 WhatsApp allowlist
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT UNIQUE,
                allowlisted INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS notifications (   -- Phase 2: delivered to dashboard
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,               -- 'reminder' | 'scheduler_error' | ...
                title TEXT NOT NULL,              -- event names only, never check-in content
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS event_reminders (  -- dedupe: one fire per event+offset
                key TEXT PRIMARY KEY,             -- '<event_id>:<offset-label>'
                fired_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS event_overrides (  -- per-event lead-time overrides
                event_id TEXT PRIMARY KEY,
                offsets TEXT NOT NULL             -- CSV minutes, e.g. '120,30'; '' = none
            );

            CREATE TABLE IF NOT EXISTS pending_actions (  -- approval gate (other calendars)
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,               -- 'event_update' | 'event_delete' | ...
                description TEXT NOT NULL,        -- human-readable, shown for approval
                payload TEXT NOT NULL,            -- JSON args for execution on approval
                status TEXT DEFAULT 'pending',    -- 'pending' | 'approved' | 'rejected' | 'done' | 'failed'
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS wa_seen (   -- WhatsApp webhook dedupe (ids only, never content)
                message_id TEXT PRIMARY KEY,
                seen_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS outside_notes (  -- rule 138: remembered from outside content
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                note TEXT NOT NULL,               -- the memory note it would join
                fact TEXT NOT NULL,
                source TEXT NOT NULL,             -- which reads were in the turn
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS wa_rate (   -- rule 139: inbound WhatsApp budget (never content)
                phone TEXT NOT NULL,
                kind TEXT NOT NULL,               -- 'guest' | 'stranger'
                ts TEXT NOT NULL
            );
        """)
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('quiet_hours', '22:30-07:30')"
        )
        # Migration: added after `contacts` first shipped without it. Wrapped
        # in try/except (same pattern as agents/state.py) since ALTER TABLE
        # has no IF NOT EXISTS and this runs on every startup.
        try:
            conn.execute("ALTER TABLE contacts ADD COLUMN last_inbound_at TEXT")
        except sqlite3.OperationalError:
            pass
        # Migration: guest WhatsApp threads (owner decision 2026-07-12).
        # NULL sender = Sheraj's owner thread (all pre-existing rows); a
        # non-NULL value is the allowlisted contact's name for that guest
        # thread (both their user turns and Abigail's assistant replies).
        try:
            conn.execute("ALTER TABLE messages ADD COLUMN sender TEXT")
        except sqlite3.OperationalError:
            pass
        # Migration: `contacts.phone` shipped with an inline UNIQUE constraint
        # in the CREATE TABLE above, but CREATE TABLE IF NOT EXISTS is a no-op
        # on a table created before that constraint existed — so a DB created
        # under the old schema has no unique index at all, and
        # record_inbound_contact()'s `ON CONFLICT(phone)` upsert then fails
        # with "no such constraint" (same class of gotcha as state.py's
        # column migrations, but for constraints instead of columns). A
        # unique index is what SQLite actually checks for an upsert conflict
        # target, so add one explicitly rather than relying on the inline
        # constraint reapplying itself.
        try:
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_phone ON contacts(phone)")
        except sqlite3.OperationalError:
            pass
        # Migration: rule 138. NULL = Sheraj's own words; a value names the
        # outside reads (e.g. "read_gmail_message") that were in the turn when
        # she saved it, and the row stays marked until he keeps it.
        for table in ("tasks", "reminders"):
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN from_outside TEXT")
            except sqlite3.OperationalError:
                pass
        # Migration: wa_seen table for Meta webhook retry dedupe (ids only).
        # CREATE TABLE IF NOT EXISTS in the script above covers fresh DBs;
        # re-run here so existing private/secretary.db files get it too.
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS wa_seen ("
                "message_id TEXT PRIMARY KEY, "
                "seen_at TEXT DEFAULT (datetime('now', 'localtime')))"
            )
        except sqlite3.OperationalError:
            pass
        conn.commit()


# --- Conversation ---

def add_message(role: str, content: str, channel: str = "dashboard",
                sender: str = None):
    """Persist a turn. sender=None is the owner thread; a contact name is a
    guest WhatsApp thread (both their user rows and Abigail's replies)."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO messages (role, content, channel, sender) VALUES (?, ?, ?, ?)",
            (role, content, channel, sender))
        conn.commit()


def get_recent_messages(limit: int = 20, thread: str = "all") -> list[dict]:
    """Most recent conversation turns, oldest first (ready for an LLM prompt).

    thread="all"   — every row (history endpoint; includes sender field).
    thread="owner" — only owner-thread rows (sender IS NULL).
    thread=<name>  — only that guest's rows (sender = name). A guest TURN uses
                     get_guest_messages instead, so a contact saved under the
                     name "owner" or "all" can never be served Sheraj's thread
                     (rule 139).
    """
    with _connect() as conn:
        if thread == "all":
            rows = conn.execute(
                "SELECT role, content, channel, sender, ts FROM messages "
                "ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
        elif thread == "owner":
            rows = conn.execute(
                "SELECT role, content, channel, sender, ts FROM messages "
                "WHERE sender IS NULL ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT role, content, channel, sender, ts FROM messages "
                "WHERE sender = ? ORDER BY id DESC LIMIT ?",
                (thread, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


def get_guest_messages(name: str, limit: int = 20) -> list[dict]:
    """One guest's own thread and nothing else (rules 27/139). Separate from
    get_recent_messages on purpose: there "owner" and "all" are keywords."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT role, content, channel, sender, ts FROM messages "
            "WHERE sender = ? ORDER BY id DESC LIMIT ?",
            (name, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


# --- Long-term memory (markdown notes) ---

def _safe_note_name(name: str) -> str:
    """Confine note names to simple filenames inside private/memory/."""
    stem = re.sub(r"[^a-zA-Z0-9_-]", "_", Path(name).stem).strip("_") or "note"
    return f"{stem}.md"


def write_memory_note(name: str, content: str):
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    path = MEMORY_DIR / _safe_note_name(name)
    stamp = datetime.now().strftime("%Y-%m-%d")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"- ({stamp}) {content.strip()}\n")


def read_all_memory_notes(max_chars: int = 4000) -> str:
    """All notes concatenated for her prompt, newest files last, size-capped."""
    if not MEMORY_DIR.exists():
        return ""
    parts = []
    for path in sorted(MEMORY_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime):
        body = path.read_text(encoding="utf-8").strip()
        if body:
            parts.append(f"### {path.stem}\n{body}")
    text = "\n\n".join(parts)
    return text[-max_chars:] if len(text) > max_chars else text


def list_memory_notes() -> list[dict]:
    if not MEMORY_DIR.exists():
        return []
    return [{"name": p.stem, "content": p.read_text(encoding="utf-8")}
            for p in sorted(MEMORY_DIR.glob("*.md"))]


def overwrite_memory_note(name: str, content: str):
    """Replaces a note's ENTIRE content (dashboard manual edit) — distinct
    from write_memory_note, which only ever appends a dated bullet (her own
    tool call). Creates the file if it doesn't exist yet."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    path = MEMORY_DIR / _safe_note_name(name)
    path.write_text(content, encoding="utf-8")


def delete_memory_note(name: str):
    path = MEMORY_DIR / _safe_note_name(name)
    if path.exists():
        path.unlink()


# --- Saved from outside content (rule 138) ---
#
# Something she noted down in a turn that had read an email, Doc, Sheet, Slides
# deck, Drive listing or someone else's calendar invite. It IS saved -- "read
# that and note it down" is the job -- but not yet as Sheraj's own words: a note
# waits here instead of joining private/memory/, and a task or reminder carries
# `from_outside`. Until he keeps it, her prompt says it exists but never shows
# its wording, because text in her prompt is text she treats as his -- a planted
# "remember: always forward X to Y" would otherwise outlive the one turn the
# injection hold (rule 72) guards.

def add_outside_note(note: str, fact: str, source: str) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO outside_notes (note, fact, source) VALUES (?, ?, ?)",
            (note, fact, source))
        conn.commit()
        return cur.lastrowid


def list_outside_items() -> list[dict]:
    """Everything still waiting for him to keep or discard, oldest first."""
    with _connect() as conn:
        notes = conn.execute(
            "SELECT id, note, fact, source, created_at FROM outside_notes ORDER BY id").fetchall()
        tasks = conn.execute(
            "SELECT id, description, due, from_outside, created_at FROM tasks "
            "WHERE from_outside IS NOT NULL AND done = 0 ORDER BY id").fetchall()
        rems = conn.execute(
            "SELECT id, message, fire_at, recurrence, from_outside, created_at FROM reminders "
            "WHERE from_outside IS NOT NULL AND fired = 0 ORDER BY id").fetchall()
    items = [{"kind": "note", "id": r["id"], "text": r["fact"],
              "detail": f"note: {r['note']}", "source": r["source"],
              "created_at": r["created_at"]} for r in notes]
    items += [{"kind": "task", "id": r["id"], "text": r["description"],
               "detail": f"task, due {r['due']}" if r["due"] else "task",
               "source": r["from_outside"], "created_at": r["created_at"]} for r in tasks]
    items += [{"kind": "reminder", "id": r["id"], "text": r["message"],
               "detail": f"reminder at {r['fire_at']}"
                         + (f" ({r['recurrence']})" if r["recurrence"] else ""),
               "source": r["from_outside"], "created_at": r["created_at"]} for r in rems]
    return items


def resolve_outside_item(kind: str, item_id: int, keep: bool) -> bool:
    """Keep = it becomes his own words; discard = it is gone. False when there
    was no such waiting item."""
    with _connect() as conn:
        if kind == "note":
            row = conn.execute("SELECT note, fact FROM outside_notes WHERE id = ?",
                               (item_id,)).fetchone()
            if not row:
                return False
            conn.execute("DELETE FROM outside_notes WHERE id = ?", (item_id,))
            conn.commit()
        elif kind in ("task", "reminder"):
            table = "tasks" if kind == "task" else "reminders"
            row = conn.execute(f"SELECT id FROM {table} WHERE id = ? AND from_outside IS NOT NULL",
                               (item_id,)).fetchone()
            if not row:
                return False
            if keep:
                conn.execute(f"UPDATE {table} SET from_outside = NULL WHERE id = ?", (item_id,))
            else:
                conn.execute(f"DELETE FROM {table} WHERE id = ?", (item_id,))
            conn.commit()
            return True
        else:
            return False
    if keep:
        write_memory_note(row["note"], row["fact"])
    return True


# --- Tasks ---

def add_task(description: str, due: str = None, from_outside: str = None) -> int:
    with _connect() as conn:
        cur = conn.execute("INSERT INTO tasks (description, due, from_outside) VALUES (?, ?, ?)",
                           (description, due, from_outside))
        conn.commit()
        return cur.lastrowid


def get_open_tasks() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, description, due, from_outside, created_at FROM tasks "
            "WHERE done = 0 ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def complete_task(task_id: int):
    with _connect() as conn:
        conn.execute("UPDATE tasks SET done = 1 WHERE id = ?", (task_id,))
        conn.commit()


def get_all_tasks() -> list[dict]:
    """Open and done, for the dashboard's manual task-management view (her
    own prompt context still only ever sees get_open_tasks())."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, description, due, done, from_outside, created_at FROM tasks "
            "ORDER BY done, id").fetchall()
    return [dict(r) for r in rows]


def update_task(task_id: int, **fields):
    """Caller (the API endpoint, via Pydantic's exclude_unset) decides which
    columns to touch — an explicitly-passed None (e.g. due=None to clear a
    due date) is a real SQL NULL, not "leave untouched"; a key simply
    absent from **fields is what's left alone."""
    allowed = {"description", "due", "done"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    if "done" in fields and fields["done"] is not None:
        fields["done"] = int(fields["done"])
    if "description" in fields:
        # Only the dashboard edits a task by hand, and wording he typed himself
        # is his own words -- the outside-content mark goes (rule 138).
        fields["from_outside"] = None
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with _connect() as conn:
        conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", (*fields.values(), task_id))
        conn.commit()


def delete_task(task_id: int):
    with _connect() as conn:
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()


# --- Reminders (scheduler reads/writes; all state here so restarts lose nothing) ---

def add_reminder(message: str, fire_at: str, recurrence: str = None,
                 wake_me: bool = False, from_outside: str = None) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO reminders (message, fire_at, recurrence, wake_me, from_outside) "
            "VALUES (?, ?, ?, ?, ?)",
            (message, fire_at, recurrence, int(wake_me), from_outside))
        conn.commit()
        return cur.lastrowid


def get_due_reminders(now_iso: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE fired = 0 AND fire_at <= ? ORDER BY fire_at",
            (now_iso,)).fetchall()
    return [dict(r) for r in rows]


def get_pending_reminders() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, message, fire_at, recurrence, wake_me, from_outside FROM reminders "
            "WHERE fired = 0 ORDER BY fire_at").fetchall()
    return [dict(r) for r in rows]


def mark_reminder_fired(reminder_id: int):
    with _connect() as conn:
        conn.execute("UPDATE reminders SET fired = 1 WHERE id = ?", (reminder_id,))
        conn.commit()


def reschedule_reminder(reminder_id: int, next_fire_at: str):
    """Recurring reminder: roll fire_at forward instead of marking fired."""
    with _connect() as conn:
        conn.execute("UPDATE reminders SET fire_at = ? WHERE id = ?",
                     (next_fire_at, reminder_id))
        conn.commit()


def delete_reminder(reminder_id: int):
    with _connect() as conn:
        conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        conn.commit()


def get_all_reminders() -> list[dict]:
    """Fired and unfired, for the dashboard's manual reminder-management view
    (the scheduler and her own prompt context still only ever see
    get_pending_reminders())."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, message, fire_at, recurrence, wake_me, fired, from_outside, created_at "
            "FROM reminders ORDER BY fired, fire_at").fetchall()
    return [dict(r) for r in rows]


def update_reminder(reminder_id: int, **fields):
    """Same exclude_unset convention as update_task — an explicit
    recurrence=None clears a recurring reminder to one-off; an absent key
    leaves the column untouched."""
    allowed = {"message", "fire_at", "recurrence", "wake_me"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    if "wake_me" in fields and fields["wake_me"] is not None:
        fields["wake_me"] = int(fields["wake_me"])
    if "message" in fields:
        fields["from_outside"] = None  # his own wording now (rule 138)
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with _connect() as conn:
        conn.execute(f"UPDATE reminders SET {set_clause} WHERE id = ?", (*fields.values(), reminder_id))
        conn.commit()


# --- Notifications (dashboard-delivered; titles only, never check-in content) ---

def add_notification(kind: str, title: str) -> int:
    with _connect() as conn:
        cur = conn.execute("INSERT INTO notifications (kind, title) VALUES (?, ?)",
                           (kind, title))
        conn.commit()
        return cur.lastrowid


def get_notifications(after_id: int = 0, limit: int = 50) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM notifications WHERE id > ? ORDER BY id DESC LIMIT ?",
            (after_id, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


# --- Calendar-event reminder dedupe + per-event overrides ---

def event_reminder_already_fired(key: str) -> bool:
    with _connect() as conn:
        return conn.execute("SELECT 1 FROM event_reminders WHERE key = ?", (key,)).fetchone() is not None


def mark_event_reminder_fired(key: str):
    with _connect() as conn:
        conn.execute("INSERT OR IGNORE INTO event_reminders (key) VALUES (?)", (key,))
        conn.commit()


def set_event_override(event_id: str, offsets_csv: str):
    with _connect() as conn:
        conn.execute("INSERT INTO event_overrides (event_id, offsets) VALUES (?, ?) "
                     "ON CONFLICT(event_id) DO UPDATE SET offsets = excluded.offsets",
                     (event_id, offsets_csv))
        conn.commit()


def get_event_override(event_id: str) -> str | None:
    with _connect() as conn:
        row = conn.execute("SELECT offsets FROM event_overrides WHERE event_id = ?",
                           (event_id,)).fetchone()
    return row["offsets"] if row else None


# --- Pending actions (per-event confirmation gate for non-Secretary calendars) ---

def add_pending_action(kind: str, description: str, payload_json: str) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO pending_actions (kind, description, payload) VALUES (?, ?, ?)",
            (kind, description, payload_json))
        conn.commit()
        return cur.lastrowid


def get_pending_actions() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, kind, description, created_at FROM pending_actions "
            "WHERE status = 'pending' ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def get_pending_action(action_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM pending_actions WHERE id = ?", (action_id,)).fetchone()
    return dict(row) if row else None


def resolve_pending_action(action_id: int, status: str):
    with _connect() as conn:
        conn.execute("UPDATE pending_actions SET status = ? WHERE id = ?", (status, action_id))
        conn.commit()


# --- WhatsApp webhook dedupe (message ids only — never content; rule 15) ---

def seen_wa_message(message_id: str) -> bool:
    """Record a Meta message id and report whether it was already present.

    INSERT OR IGNORE: first call returns False (new), subsequent calls return
    True (already seen). Empty/missing ids are the caller's problem — this
    only stores non-empty ids. Ids live only in private/secretary.db.
    """
    if not message_id:
        return False
    with _connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO wa_seen (message_id) VALUES (?)",
            (message_id,))
        conn.commit()
        # rowcount 0 => ignored because the PRIMARY KEY already existed
        return cur.rowcount == 0


# --- Settings ---

def get_setting(key: str, default: str = None) -> str | None:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    with _connect() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
        conn.commit()


# --- Contacts (Phase 3 WhatsApp allowlist) ---
# The allowlist itself is owner-controlled only (dashboard/API), never
# LLM-writable — see agents/secretary_tools.py's docstring for why. Outbound
# approval for non-allowlisted numbers reuses `pending_actions` (kind
# "whatsapp_send") rather than a second queue table, same as every other
# gated write in this codebase.

def normalize_phone(phone: str) -> str:
    """
    Digits only, no '+'. Meta's webhook `from` field never includes a '+'
    (e.g. "15551234567"), but a human typing a number into the dashboard
    (or WHATSAPP_OWNER_NUMBER in .env) naturally writes "+1 555 123 4444" —
    every phone comparison in this module must go through this first, or an
    owner/allowlist check silently fails on a formatting mismatch, not a
    real identity mismatch.
    """
    return re.sub(r"\D", "", phone or "")


def add_contact(name: str, phone: str, allowlisted: bool = False) -> int:
    """Raises ValueError on a name another contact already has: a guest's
    thread is keyed by name, so two "Mom"s would read each other's messages
    (rule 139)."""
    with _connect() as conn:
        clash = conn.execute(
            "SELECT 1 FROM contacts WHERE lower(trim(name)) = lower(trim(?)) AND phone != ?",
            (name, normalize_phone(phone))).fetchone()
        if clash:
            raise ValueError(f"There is already a contact called '{name}'. Give this one "
                             "a different name so their conversations stay separate.")
        cur = conn.execute(
            "INSERT INTO contacts (name, phone, allowlisted) VALUES (?, ?, ?)",
            (name, normalize_phone(phone), int(allowlisted)))
        conn.commit()
        return cur.lastrowid


def list_contacts() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM contacts ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def get_contact_by_phone(phone: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM contacts WHERE phone = ?",
                           (normalize_phone(phone),)).fetchone()
    return dict(row) if row else None


def is_allowlisted(phone: str) -> bool:
    contact = get_contact_by_phone(phone)
    return bool(contact and contact["allowlisted"])


def set_contact_allowlisted(contact_id: int, allowlisted: bool):
    with _connect() as conn:
        conn.execute("UPDATE contacts SET allowlisted = ? WHERE id = ?",
                     (int(allowlisted), contact_id))
        conn.commit()


def remove_contact(contact_id: int):
    with _connect() as conn:
        conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
        conn.commit()


def count_recent_inbound(phone: str, kind: str, hours: int) -> int:
    """Inbound WhatsApp messages of this kind from this number in the last
    `hours` -- a count only, never content (rule 15)."""
    cutoff = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM wa_rate WHERE phone = ? AND kind = ? AND ts >= ?",
            (normalize_phone(phone), kind, cutoff)).fetchone()[0]


def record_inbound_rate(phone: str, kind: str):
    now = datetime.now()
    with _connect() as conn:
        conn.execute("INSERT INTO wa_rate (phone, kind, ts) VALUES (?, ?, ?)",
                     (normalize_phone(phone), kind, now.strftime("%Y-%m-%d %H:%M:%S")))
        conn.execute("DELETE FROM wa_rate WHERE ts < ?",
                     ((now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S"),))
        conn.commit()


def record_inbound_contact(phone: str, name: str = None) -> dict:
    """
    Upserts a contact on inbound WhatsApp message and stamps last_inbound_at
    — the 24-hour free-form messaging window (agents/whatsapp.py) is
    measured from this, and a never-messaged-before number always starts
    un-allowlisted (never auto-trusted just for having texted in).
    """
    phone = normalize_phone(phone)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        conn.execute(
            "INSERT INTO contacts (name, phone, last_inbound_at) VALUES (?, ?, ?) "
            "ON CONFLICT(phone) DO UPDATE SET last_inbound_at = excluded.last_inbound_at",
            (name or phone, phone, now))
        conn.commit()
        row = conn.execute("SELECT * FROM contacts WHERE phone = ?", (phone,)).fetchone()
    return dict(row)
