"""
The Live Consultation store — meetings, transcripts, consultation state.

Rule 15 / 73: the ONLY module that touches consultation data at rest. Writes to
`private/consultation.db` (git-ignored). A meeting transcript is the most
sensitive thing this repo has ever held — people say things in consultation
they would not write down — so nothing from here may enter `workforce.db`,
`log_run` summaries, job progress strings, stdout, or any committed file.

Same shape as `secretary_store.py` and `nuclei_store.py`: one private SQLite
file, `db_path=` for tests only, `assert_test_db` refusing to let a test open
the owner's real database.

Ordering (rule 80): a turn's `sequence` is assigned when the item is FIRST
seen, not when its transcription completes. Realtime transcription finishes
out of order — a long turn can complete after a short one that started later —
so completion order is not speaking order. First-appearance order is, because
the delta for an item arrives while the person is still talking.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

PRIVATE_DIR = Path(__file__).parent.parent / "private"
DB_PATH = PRIVATE_DIR / "consultation.db"
AUDIO_DIR = PRIVATE_DIR / "consultation_audio"


def assert_test_db(path: Path | str) -> Path:
    """Refuse to run tests against the owner's real private database."""
    path = Path(path).resolve()
    real = DB_PATH.resolve()
    if path == real:
        raise RuntimeError(
            "live consultation tests must not open private/consultation.db "
            "— pass a temp path"
        )
    private = PRIVATE_DIR.resolve()
    try:
        path.relative_to(private)
    except ValueError:
        return path
    raise RuntimeError(
        "live consultation tests must not write inside private/ — pass a temp path"
    )


def _db(db_path: Path | str | None = None) -> Path:
    return Path(db_path) if db_path is not None else DB_PATH


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = _db(db_path)
    path.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def init_db(db_path: Path | str | None = None) -> None:
    with _connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                question TEXT NOT NULL DEFAULT '',
                context TEXT NOT NULL DEFAULT '',
                framework TEXT NOT NULL DEFAULT 'bahai',
                mode TEXT NOT NULL DEFAULT 'facilitator',
                decision_method TEXT NOT NULL DEFAULT 'unspecified',
                presence TEXT NOT NULL DEFAULT 'attentive',
                status TEXT NOT NULL DEFAULT 'draft',
                record_audio INTEGER NOT NULL DEFAULT 0,
                realtime_model TEXT,
                reasoning_model TEXT,
                transcribe_model TEXT,
                voice TEXT,
                state_revision INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                started_at TEXT,
                ended_at TEXT
            );
            CREATE TABLE IF NOT EXISTS turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                realtime_item_id TEXT,
                sequence INTEGER NOT NULL,
                role TEXT NOT NULL DEFAULT 'human',
                speaker_label TEXT,
                text TEXT NOT NULL DEFAULT '',
                is_final INTEGER NOT NULL DEFAULT 0,
                started_at TEXT,
                ended_at TEXT,
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                analyzed INTEGER NOT NULL DEFAULT 0
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_turn_item
                ON turns(session_id, realtime_item_id)
                WHERE realtime_item_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_turn_seq ON turns(session_id, sequence);
            CREATE TABLE IF NOT EXISTS session_state (
                session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
                state_json TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT DEFAULT (datetime('now', 'localtime'))
            );
            CREATE TABLE IF NOT EXISTS observations (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                kind TEXT NOT NULL DEFAULT 'note',
                importance REAL NOT NULL DEFAULT 0,
                summary TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT '',
                should_request_floor INTEGER NOT NULL DEFAULT 0,
                permission_request TEXT NOT NULL DEFAULT '',
                speech_brief TEXT NOT NULL DEFAULT '',
                state_revision INTEGER NOT NULL DEFAULT 0,
                dedupe_key TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_obs_session ON observations(session_id, status);
            CREATE TABLE IF NOT EXISTS decisions (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                text TEXT NOT NULL DEFAULT '',
                rationale TEXT NOT NULL DEFAULT '',
                support TEXT NOT NULL DEFAULT '',
                concerns_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'candidate',
                dedupe_key TEXT NOT NULL DEFAULT '',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                confirmed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS action_items (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                action TEXT NOT NULL DEFAULT '',
                owner TEXT,
                due TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                dedupe_key TEXT NOT NULL DEFAULT '',
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            );
            CREATE TABLE IF NOT EXISTS writings (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                text TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                section TEXT NOT NULL DEFAULT '',
                link TEXT NOT NULL DEFAULT '',
                theme TEXT NOT NULL DEFAULT '',
                score REAL NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            );
            CREATE TABLE IF NOT EXISTS speech_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                allowed INTEGER NOT NULL DEFAULT 0,
                reason TEXT NOT NULL DEFAULT '',
                observation_id TEXT,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_speech_session
                ON speech_events(session_id, created_at);
        """)
        # Migrations. CREATE TABLE IF NOT EXISTS is a no-op on a database that
        # already exists on disk, so a column added later needs its own ALTER
        # (the same gotcha AGENTS.md records for state.py and secretary_store).
        # `presence` arrived on 2026-08-21, after real sessions had been held.
        for column, ddl in (
            ("presence", "ALTER TABLE sessions ADD COLUMN presence TEXT NOT NULL "
                         "DEFAULT 'attentive'"),
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass    # already there
        conn.commit()


# ── Sessions ────────────────────────────────────────────────────────────────

def create_session(title: str, question: str = "", context: str = "",
                   framework: str = "bahai", mode: str = "facilitator",
                   decision_method: str = "unspecified", presence: str = "attentive",
                   record_audio: bool = False,
                   realtime_model: str = "", reasoning_model: str = "",
                   transcribe_model: str = "", voice: str = "",
                   db_path: Path | str | None = None) -> dict:
    title = (title or "").strip() or "Consultation"
    sid = new_id("cons")
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO sessions (id, title, question, context, framework, mode,
                                     decision_method, presence, record_audio,
                                     realtime_model, reasoning_model, transcribe_model,
                                     voice)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sid, title, (question or "").strip(), (context or "").strip(), framework, mode,
             decision_method, presence, 1 if record_audio else 0, realtime_model,
             reasoning_model, transcribe_model, voice),
        )
        conn.execute("INSERT INTO session_state (session_id, state_json, revision) VALUES (?,?,0)",
                     (sid, json.dumps({"question": (question or "").strip()})))
        conn.commit()
    return get_session(sid, db_path=db_path)


def get_session(session_id: str, db_path: Path | str | None = None) -> Optional[dict]:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return dict(row) if row else None


def list_sessions(limit: int = 100, db_path: Path | str | None = None) -> list[dict]:
    """The archive. Carries the counts the list view shows so it never has to
    open every session to draw one row."""
    with _connect(db_path) as conn:
        rows = _rows(conn.execute(
            """SELECT s.*,
                      (SELECT COUNT(*) FROM turns t
                        WHERE t.session_id = s.id AND t.is_final = 1) AS turn_count,
                      (SELECT COUNT(*) FROM decisions d
                        WHERE d.session_id = s.id AND d.status = 'confirmed')
                        AS confirmed_decisions
                 FROM sessions s
                ORDER BY COALESCE(s.started_at, s.created_at) DESC
                LIMIT ?""", (limit,)))
    for r in rows:
        r["decision_confirmed"] = bool(r.pop("confirmed_decisions", 0))
    return rows


def update_session(session_id: str, db_path: Path | str | None = None, **fields) -> Optional[dict]:
    allowed = {"title", "question", "context", "framework", "mode", "decision_method",
               "presence", "status", "record_audio", "realtime_model", "reasoning_model",
               "transcribe_model", "voice", "started_at", "ended_at"}
    sets, values = [], []
    for key, value in fields.items():
        if key not in allowed or value is None:
            continue
        if key == "record_audio":
            value = 1 if value else 0
        sets.append(f"{key} = ?")
        values.append(value)
    if not sets:
        return get_session(session_id, db_path=db_path)
    values.append(session_id)
    with _connect(db_path) as conn:
        conn.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE id = ?", values)
        conn.commit()
    return get_session(session_id, db_path=db_path)


def start_session(session_id: str, db_path: Path | str | None = None) -> Optional[dict]:
    return update_session(session_id, status="live", started_at=_now(), db_path=db_path)


def end_session(session_id: str, db_path: Path | str | None = None) -> Optional[dict]:
    return update_session(session_id, status="ended", ended_at=_now(), db_path=db_path)


def delete_session(session_id: str, db_path: Path | str | None = None) -> dict:
    """Delete a meeting and everything it contains — transcript, state,
    observations, decisions, actions, writings, and any recording. There is no
    soft delete here on purpose: "delete this meeting" has to mean it."""
    removed_audio = 0
    folder = AUDIO_DIR / session_id
    if folder.exists():
        for f in folder.iterdir():
            try:
                f.unlink()
                removed_audio += 1
            except OSError:
                pass
        try:
            folder.rmdir()
        except OSError:
            pass
    with _connect(db_path) as conn:
        counts = {
            "turns": conn.execute("SELECT COUNT(*) FROM turns WHERE session_id = ?",
                                  (session_id,)).fetchone()[0],
            "observations": conn.execute("SELECT COUNT(*) FROM observations WHERE session_id = ?",
                                         (session_id,)).fetchone()[0],
        }
        # Explicit child deletes: PRAGMA foreign_keys is per-connection, and a
        # database made before it was set would otherwise leave orphans behind.
        for table in ("turns", "session_state", "observations", "decisions",
                      "action_items", "writings", "speech_events"):
            conn.execute(f"DELETE FROM {table} WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
    counts["audio_files"] = removed_audio
    counts["deleted"] = True
    return counts


# ── Turns (rule 80) ─────────────────────────────────────────────────────────

def upsert_turn(session_id: str, text: str, realtime_item_id: str | None = None,
                role: str = "human", speaker_label: str | None = None,
                is_final: bool = False, started_at: str | None = None,
                ended_at: str | None = None, db_path: Path | str | None = None) -> dict:
    """
    Idempotent by (session_id, realtime_item_id): the same item arriving again —
    a delta, then a completion, then a retried completion — updates one row
    rather than adding another.

    A finalised turn is never demoted back to partial, and never overwritten
    with empty text: a late empty delta must not erase what was said.
    """
    with _connect(db_path) as conn:
        existing = None
        if realtime_item_id:
            existing = conn.execute(
                "SELECT * FROM turns WHERE session_id = ? AND realtime_item_id = ?",
                (session_id, realtime_item_id)).fetchone()
        if existing:
            row = dict(existing)
            new_text = text if (text or "").strip() else row["text"]
            if row["is_final"] and not is_final:
                # A partial that arrives after the final one is stale by
                # definition; keep the finalised text.
                new_text = row["text"]
            conn.execute(
                """UPDATE turns SET text = ?, is_final = ?, speaker_label = COALESCE(?, speaker_label),
                          started_at = COALESCE(started_at, ?), ended_at = COALESCE(?, ended_at)
                     WHERE id = ?""",
                (new_text, 1 if (is_final or row["is_final"]) else 0, speaker_label,
                 started_at, ended_at, row["id"]))
            conn.commit()
            return dict(conn.execute("SELECT * FROM turns WHERE id = ?", (row["id"],)).fetchone())

        seq = conn.execute("SELECT COALESCE(MAX(sequence), 0) + 1 FROM turns WHERE session_id = ?",
                           (session_id,)).fetchone()[0]
        cur = conn.execute(
            """INSERT INTO turns (session_id, realtime_item_id, sequence, role, speaker_label,
                                  text, is_final, started_at, ended_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (session_id, realtime_item_id, seq, role, speaker_label, text or "",
             1 if is_final else 0, started_at, ended_at))
        conn.commit()
        return dict(conn.execute("SELECT * FROM turns WHERE id = ?", (cur.lastrowid,)).fetchone())


def list_turns(session_id: str, final_only: bool = False, limit: int | None = None,
               db_path: Path | str | None = None) -> list[dict]:
    sql = "SELECT * FROM turns WHERE session_id = ?"
    if final_only:
        sql += " AND is_final = 1"
    sql += " ORDER BY sequence ASC"
    with _connect(db_path) as conn:
        rows = _rows(conn.execute(sql, (session_id,)))
    return rows[-limit:] if limit else rows


def unanalyzed_turns(session_id: str, db_path: Path | str | None = None) -> list[dict]:
    """Finalised turns the brain has not read yet — what makes analysis
    incremental rather than a re-reading of the whole meeting (rule 79)."""
    with _connect(db_path) as conn:
        return _rows(conn.execute(
            """SELECT * FROM turns
                WHERE session_id = ? AND is_final = 1 AND analyzed = 0 AND TRIM(text) != ''
                ORDER BY sequence ASC""", (session_id,)))


def mark_turns_analyzed(session_id: str, turn_ids: list[int],
                        db_path: Path | str | None = None) -> int:
    if not turn_ids:
        return 0
    marks = ",".join("?" for _ in turn_ids)
    with _connect(db_path) as conn:
        cur = conn.execute(
            f"UPDATE turns SET analyzed = 1 WHERE session_id = ? AND id IN ({marks})",
            [session_id, *turn_ids])
        conn.commit()
        return cur.rowcount


def label_turn(turn_id: int, speaker_label: str | None,
               db_path: Path | str | None = None) -> Optional[dict]:
    """A human typing in who was speaking. There is no automatic diarisation
    here and none is inferred (rule 80)."""
    label = (speaker_label or "").strip() or None
    with _connect(db_path) as conn:
        conn.execute("UPDATE turns SET speaker_label = ? WHERE id = ?", (label, turn_id))
        conn.commit()
        row = conn.execute("SELECT * FROM turns WHERE id = ?", (turn_id,)).fetchone()
    return dict(row) if row else None


# ── Consultation state ──────────────────────────────────────────────────────

def get_state(session_id: str, db_path: Path | str | None = None) -> dict:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM session_state WHERE session_id = ?",
                           (session_id,)).fetchone()
    if not row:
        return {"state_revision": 0}
    try:
        state = json.loads(row["state_json"])
    except Exception:
        state = {}
    state["state_revision"] = row["revision"]
    return state


def save_state(session_id: str, state: dict, db_path: Path | str | None = None) -> dict:
    """Store a new state and bump the revision. The revision is what makes a
    prepared answer checkable for staleness (rule 77) — it only ever increases,
    and only here."""
    with _connect(db_path) as conn:
        row = conn.execute("SELECT revision FROM session_state WHERE session_id = ?",
                           (session_id,)).fetchone()
        revision = (row["revision"] if row else 0) + 1
        payload = dict(state)
        payload["state_revision"] = revision
        blob = json.dumps(payload, ensure_ascii=False)
        if row:
            conn.execute(
                """UPDATE session_state SET state_json = ?, revision = ?, updated_at = ?
                    WHERE session_id = ?""", (blob, revision, _now(), session_id))
        else:
            conn.execute(
                "INSERT INTO session_state (session_id, state_json, revision) VALUES (?,?,?)",
                (session_id, blob, revision))
        conn.execute("UPDATE sessions SET state_revision = ? WHERE id = ?", (revision, session_id))
        conn.commit()
    return payload


# ── Observations ────────────────────────────────────────────────────────────

def add_observation(session_id: str, kind: str, summary: str, detail: str = "",
                    importance: float = 0.0, should_request_floor: bool = False,
                    permission_request: str = "", speech_brief: str = "",
                    state_revision: int = 0, dedupe_key: str = "",
                    db_path: Path | str | None = None) -> Optional[dict]:
    """
    Returns None when this observation has already been made — deduplication is
    at the STORE, not in the panel that draws them, so a re-analysis that
    notices the same thing again cannot re-ask for the floor about it (rule 75).
    A dismissed observation stays dismissed for the same reason.
    """
    key = (dedupe_key or summary or "").strip().lower()[:200]
    with _connect(db_path) as conn:
        if key:
            dup = conn.execute(
                "SELECT id FROM observations WHERE session_id = ? AND dedupe_key = ?",
                (session_id, key)).fetchone()
            if dup:
                return None
        oid = new_id("obs")
        conn.execute(
            """INSERT INTO observations (id, session_id, kind, importance, summary, detail,
                                         should_request_floor, permission_request, speech_brief,
                                         state_revision, dedupe_key)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (oid, session_id, kind, float(importance or 0), summary, detail,
             1 if should_request_floor else 0, permission_request, speech_brief,
             int(state_revision or 0), key))
        conn.commit()
        return dict(conn.execute("SELECT * FROM observations WHERE id = ?", (oid,)).fetchone())


def list_observations(session_id: str, status: str | None = None,
                      db_path: Path | str | None = None) -> list[dict]:
    sql = "SELECT * FROM observations WHERE session_id = ?"
    args: list = [session_id]
    if status:
        sql += " AND status = ?"
        args.append(status)
    sql += " ORDER BY created_at DESC, rowid DESC"
    with _connect(db_path) as conn:
        return _rows(conn.execute(sql, args))


def set_observation_status(observation_id: str, status: str,
                           db_path: Path | str | None = None) -> Optional[dict]:
    with _connect(db_path) as conn:
        conn.execute("UPDATE observations SET status = ? WHERE id = ?", (status, observation_id))
        conn.commit()
        row = conn.execute("SELECT * FROM observations WHERE id = ?", (observation_id,)).fetchone()
    return dict(row) if row else None


def get_observation(observation_id: str, db_path: Path | str | None = None) -> Optional[dict]:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM observations WHERE id = ?", (observation_id,)).fetchone()
    return dict(row) if row else None


# ── Decisions and actions ───────────────────────────────────────────────────

def upsert_decision_candidate(session_id: str, text: str, rationale: str = "",
                              support: str = "", concerns: list[str] | None = None,
                              db_path: Path | str | None = None) -> Optional[dict]:
    """A possible decision. Never confirmed here — `confirm_decision` is the
    only path to that, and only a person can call it (rule 81)."""
    key = (text or "").strip().lower()[:200]
    if not key:
        return None
    with _connect(db_path) as conn:
        dup = conn.execute("SELECT * FROM decisions WHERE session_id = ? AND dedupe_key = ?",
                           (session_id, key)).fetchone()
        if dup:
            return dict(dup)
        did = new_id("dec")
        conn.execute(
            """INSERT INTO decisions (id, session_id, text, rationale, support, concerns_json,
                                      dedupe_key)
               VALUES (?,?,?,?,?,?,?)""",
            (did, session_id, text.strip(), rationale or "", support or "",
             json.dumps(concerns or []), key))
        conn.commit()
        return dict(conn.execute("SELECT * FROM decisions WHERE id = ?", (did,)).fetchone())


def list_decisions(session_id: str, db_path: Path | str | None = None) -> list[dict]:
    with _connect(db_path) as conn:
        rows = _rows(conn.execute(
            "SELECT * FROM decisions WHERE session_id = ? ORDER BY created_at ASC, rowid ASC",
            (session_id,)))
    for r in rows:
        try:
            r["concerns"] = json.loads(r.pop("concerns_json") or "[]")
        except Exception:
            r["concerns"] = []
    return rows


def set_decision_status(decision_id: str, status: str,
                        db_path: Path | str | None = None) -> Optional[dict]:
    confirmed_at = _now() if status == "confirmed" else None
    with _connect(db_path) as conn:
        conn.execute("UPDATE decisions SET status = ?, confirmed_at = ? WHERE id = ?",
                     (status, confirmed_at, decision_id))
        conn.commit()
        row = conn.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()
    if not row:
        return None
    out = dict(row)
    try:
        out["concerns"] = json.loads(out.pop("concerns_json") or "[]")
    except Exception:
        out["concerns"] = []
    return out


def confirmed_decision(session_id: str, db_path: Path | str | None = None) -> Optional[dict]:
    for d in list_decisions(session_id, db_path=db_path):
        if d.get("status") == "confirmed":
            return d
    return None


def upsert_action_item(session_id: str, action: str, owner: str | None = None,
                       due: str | None = None,
                       db_path: Path | str | None = None) -> Optional[dict]:
    key = (action or "").strip().lower()[:200]
    if not key:
        return None
    with _connect(db_path) as conn:
        dup = conn.execute("SELECT * FROM action_items WHERE session_id = ? AND dedupe_key = ?",
                           (session_id, key)).fetchone()
        if dup:
            return dict(dup)
        aid = new_id("act")
        conn.execute(
            """INSERT INTO action_items (id, session_id, action, owner, due, dedupe_key)
               VALUES (?,?,?,?,?,?)""",
            (aid, session_id, action.strip(), (owner or None), (due or None), key))
        conn.commit()
        return dict(conn.execute("SELECT * FROM action_items WHERE id = ?", (aid,)).fetchone())


def list_action_items(session_id: str, db_path: Path | str | None = None) -> list[dict]:
    with _connect(db_path) as conn:
        return _rows(conn.execute(
            "SELECT * FROM action_items WHERE session_id = ? ORDER BY created_at ASC, rowid ASC",
            (session_id,)))


def set_action_status(action_id: str, status: str,
                      db_path: Path | str | None = None) -> Optional[dict]:
    with _connect(db_path) as conn:
        conn.execute("UPDATE action_items SET status = ? WHERE id = ?", (status, action_id))
        conn.commit()
        row = conn.execute("SELECT * FROM action_items WHERE id = ?", (action_id,)).fetchone()
    return dict(row) if row else None


# ── Verified writings ───────────────────────────────────────────────────────

def add_writing(session_id: str, text: str, source: str = "", section: str = "",
                link: str = "", theme: str = "", score: float = 0.0,
                db_path: Path | str | None = None) -> Optional[dict]:
    """A passage that came out of the verified corpus. Deduplicated on the exact
    text, so the same passage found twice is shown once."""
    if not (text or "").strip():
        return None
    with _connect(db_path) as conn:
        dup = conn.execute("SELECT * FROM writings WHERE session_id = ? AND text = ?",
                           (session_id, text)).fetchone()
        if dup:
            return dict(dup)
        wid = new_id("wri")
        conn.execute(
            """INSERT INTO writings (id, session_id, text, source, section, link, theme, score)
               VALUES (?,?,?,?,?,?,?,?)""",
            (wid, session_id, text, source, section, link, theme, float(score or 0)))
        conn.commit()
        return dict(conn.execute("SELECT * FROM writings WHERE id = ?", (wid,)).fetchone())


def list_writings(session_id: str, db_path: Path | str | None = None) -> list[dict]:
    with _connect(db_path) as conn:
        return _rows(conn.execute(
            "SELECT * FROM writings WHERE session_id = ? ORDER BY created_at ASC, rowid ASC",
            (session_id,)))


# ── Speech events (the governor's record) ───────────────────────────────────

def log_speech_event(session_id: str, kind: str, allowed: bool, reason: str = "",
                     observation_id: str | None = None,
                     db_path: Path | str | None = None) -> None:
    """Every floor decision the server made, kept so a refusal can be explained
    afterwards and so cooldowns survive a page reload."""
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO speech_events (session_id, kind, allowed, reason, observation_id)
               VALUES (?,?,?,?,?)""",
            (session_id, kind, 1 if allowed else 0, reason or "", observation_id))
        conn.commit()


def list_speech_events(session_id: str, limit: int = 50,
                       db_path: Path | str | None = None) -> list[dict]:
    with _connect(db_path) as conn:
        rows = _rows(conn.execute(
            """SELECT * FROM speech_events WHERE session_id = ?
                ORDER BY id DESC LIMIT ?""", (session_id, limit)))
    return rows


def last_allowed_speech(session_id: str, kinds: tuple[str, ...] = ("intervention",),
                        db_path: Path | str | None = None) -> Optional[dict]:
    marks = ",".join("?" for _ in kinds)
    with _connect(db_path) as conn:
        row = conn.execute(
            f"""SELECT * FROM speech_events
                 WHERE session_id = ? AND allowed = 1 AND kind IN ({marks})
                 ORDER BY id DESC LIMIT 1""", [session_id, *kinds]).fetchone()
    return dict(row) if row else None
