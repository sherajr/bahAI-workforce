"""
The Secretary's private store — the ONLY module that touches Sheraj's personal
data at rest (CLAUDE.md hard rule: everything personal lives in private/ and
only there).

Two surfaces:
  private/secretary.db   — SQLite: conversations, check-ins, streaks, tasks,
                           reminders, contacts (allowlist), approval queue,
                           settings.
  private/memory/*.md    — her long-term notes about Sheraj, plain markdown so
                           he can open and read every one of them himself.

Nothing in here may be imported for the purpose of writing personal content
into workforce.db, log_run summaries, or job progress strings.
"""

import re
import sqlite3
from datetime import datetime
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

            CREATE TABLE IF NOT EXISTS contacts (       -- Phase 3 allowlist
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT,
                allowlisted INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS approval_queue ( -- Phase 3
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipient TEXT NOT NULL,
                draft TEXT NOT NULL,
                status TEXT DEFAULT 'pending',    -- 'pending' | 'approved' | 'rejected' | 'sent'
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
        """)
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('quiet_hours', '22:30-07:30')"
        )
        conn.commit()


# --- Conversation ---

def add_message(role: str, content: str, channel: str = "dashboard"):
    with _connect() as conn:
        conn.execute("INSERT INTO messages (role, content, channel) VALUES (?, ?, ?)",
                     (role, content, channel))
        conn.commit()


def get_recent_messages(limit: int = 20) -> list[dict]:
    """Most recent conversation turns, oldest first (ready for an LLM prompt)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT role, content, channel, ts FROM messages ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()
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


# --- Tasks ---

def add_task(description: str, due: str = None) -> int:
    with _connect() as conn:
        cur = conn.execute("INSERT INTO tasks (description, due) VALUES (?, ?)",
                           (description, due))
        conn.commit()
        return cur.lastrowid


def get_open_tasks() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, description, due, created_at FROM tasks WHERE done = 0 ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def complete_task(task_id: int):
    with _connect() as conn:
        conn.execute("UPDATE tasks SET done = 1 WHERE id = ?", (task_id,))
        conn.commit()


# --- Reminders (scheduler reads/writes; all state here so restarts lose nothing) ---

def add_reminder(message: str, fire_at: str, recurrence: str = None,
                 wake_me: bool = False) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO reminders (message, fire_at, recurrence, wake_me) VALUES (?, ?, ?, ?)",
            (message, fire_at, recurrence, int(wake_me)))
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
            "SELECT id, message, fire_at, recurrence, wake_me FROM reminders "
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
