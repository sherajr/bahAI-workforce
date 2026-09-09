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
                analyzed INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'live'
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
            -- Who is in the room. Typed by a human at setup and only there: the
            -- live API cannot tell voices apart (see `speaker_key`), so a name
            -- here is a claim a person made, never one this system inferred.
            CREATE TABLE IF NOT EXISTS participants (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                -- The diarised speaker this person turned out to be ("A", "B"),
                -- filled in AFTER the meeting by a human matching a name to a
                -- voice. Null until then, and null is an honest answer.
                speaker_key TEXT,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_participant_session
                ON participants(session_id);
        """)
        # Migrations. CREATE TABLE IF NOT EXISTS is a no-op on a database that
        # already exists on disk, so a column added later needs its own ALTER
        # (the same gotcha AGENTS.md records for state.py and secretary_store).
        # `presence` arrived on 2026-08-21, after real sessions had been held.
        for column, ddl in (
            ("presence", "ALTER TABLE sessions ADD COLUMN presence TEXT NOT NULL "
                         "DEFAULT 'attentive'"),
            # 2026-08-25: the timed consultation, the recording, and the report.
            ("duration_minutes", "ALTER TABLE sessions ADD COLUMN duration_minutes "
                                 "INTEGER NOT NULL DEFAULT 0"),
            ("warn_minutes", "ALTER TABLE sessions ADD COLUMN warn_minutes "
                             "INTEGER NOT NULL DEFAULT 10"),
            ("report_md", "ALTER TABLE sessions ADD COLUMN report_md TEXT"),
            ("report_at", "ALTER TABLE sessions ADD COLUMN report_at TEXT"),
            ("audio_status", "ALTER TABLE sessions ADD COLUMN audio_status TEXT "
                             "NOT NULL DEFAULT 'none'"),
            ("audio_note", "ALTER TABLE sessions ADD COLUMN audio_note TEXT "
                           "NOT NULL DEFAULT ''"),
            # 'live' (heard during the meeting) or 'diarized' (worked out
            # afterwards from the recording). Both are kept: the diarised pass
            # is a BETTER record, not a licence to destroy the one the room
            # actually watched being written (rule 80).
            ("source", "ALTER TABLE turns ADD COLUMN source TEXT NOT NULL "
                       "DEFAULT 'live'"),
            # 2026-09-03 (rules 94-98). Eight real meetings, 438 turns and 169
            # action items existed when these were added, so every one is an
            # ALTER with an honest default -- nothing here may imply consent,
            # human review, acceptance or verification that never happened.
            ("retention_policy", "ALTER TABLE sessions ADD COLUMN retention_policy "
                                 "TEXT NOT NULL DEFAULT 'keep'"),
            # Null, not 0: an old session was never asked, and "the host did
            # not attest" is the truth about it. A default of 0 would say the
            # same thing, but null says it was never a question.
            ("participants_informed_at", "ALTER TABLE sessions ADD COLUMN "
                                         "participants_informed_at TEXT"),
            ("closeout_outcome", "ALTER TABLE sessions ADD COLUMN closeout_outcome TEXT"),
            ("closeout_note", "ALTER TABLE sessions ADD COLUMN closeout_note TEXT "
                              "NOT NULL DEFAULT ''"),
            ("closeout_at", "ALTER TABLE sessions ADD COLUMN closeout_at TEXT"),
            ("reflection_at", "ALTER TABLE sessions ADD COLUMN reflection_at TEXT"),
            ("transcript_deleted_at", "ALTER TABLE sessions ADD COLUMN "
                                      "transcript_deleted_at TEXT"),
            # The turn a human corrected. The ORIGINAL text is deliberately not
            # kept: someone corrects a line because the machine misheard
            # something, and holding the mishearing for ever defeats the point.
            # The flag is what an audit needs -- that this line was edited.
            ("corrected_at", "ALTER TABLE turns ADD COLUMN corrected_at TEXT"),
            # The map id (`action_3`, `decision_1`) this row came from. THE fix
            # for the write-once defect: identity used to be normalised text,
            # so an action that later learned an owner matched the old row and
            # was returned unchanged, and a REWORDED one became a second row.
            # 169 action items across six meetings carried 2 owners and 0 due
            # dates because of it.
            ("map_id", "ALTER TABLE action_items ADD COLUMN map_id TEXT"),
            ("owner_accepted", "ALTER TABLE action_items ADD COLUMN owner_accepted INTEGER"),
            ("accepted_by", "ALTER TABLE action_items ADD COLUMN accepted_by TEXT "
                            "NOT NULL DEFAULT ''"),
            ("accepted_at", "ALTER TABLE action_items ADD COLUMN accepted_at TEXT"),
            ("success_criteria", "ALTER TABLE action_items ADD COLUMN success_criteria "
                                 "TEXT NOT NULL DEFAULT ''"),
            ("support_needed", "ALTER TABLE action_items ADD COLUMN support_needed TEXT "
                               "NOT NULL DEFAULT ''"),
            ("blocker", "ALTER TABLE action_items ADD COLUMN blocker TEXT NOT NULL DEFAULT ''"),
            ("progress_note", "ALTER TABLE action_items ADD COLUMN progress_note TEXT "
                              "NOT NULL DEFAULT ''"),
            ("source_decision_id", "ALTER TABLE action_items ADD COLUMN source_decision_id "
                                   "TEXT NOT NULL DEFAULT ''"),
            ("human_edited", "ALTER TABLE action_items ADD COLUMN human_edited INTEGER "
                             "NOT NULL DEFAULT 0"),
            ("decision_map_id", "ALTER TABLE decisions ADD COLUMN map_id TEXT"),
            ("decision_retained", "ALTER TABLE decisions ADD COLUMN retained_concerns_json "
                                  "TEXT NOT NULL DEFAULT '[]'"),
            ("decision_human_edited", "ALTER TABLE decisions ADD COLUMN human_edited "
                                      "INTEGER NOT NULL DEFAULT 0"),
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass    # already there
        # Created here rather than in the script above for the reason AGENTS.md
        # records: a constraint added to a CREATE TABLE IF NOT EXISTS does
        # nothing to a table that already exists on disk.
        for index in (
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_action_map "
            "ON action_items(session_id, map_id) WHERE map_id IS NOT NULL",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_decision_map "
            "ON decisions(session_id, map_id) WHERE map_id IS NOT NULL",
        ):
            try:
                conn.execute(index)
            except sqlite3.OperationalError:
                pass
        conn.commit()


# ── Sessions ────────────────────────────────────────────────────────────────

def create_session(title: str, question: str = "", context: str = "",
                   framework: str = "bahai", mode: str = "facilitator",
                   decision_method: str = "unspecified", presence: str = "attentive",
                   record_audio: bool = False, duration_minutes: int = 0,
                   warn_minutes: int = 10, retention_policy: str = "keep",
                   participants_informed: bool = False,
                   realtime_model: str = "", reasoning_model: str = "",
                   transcribe_model: str = "", voice: str = "",
                   db_path: Path | str | None = None) -> dict:
    title = (title or "").strip() or "Consultation"
    sid = new_id("cons")
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO sessions (id, title, question, context, framework, mode,
                                     decision_method, presence, record_audio,
                                     duration_minutes, warn_minutes, audio_status,
                                     retention_policy, participants_informed_at,
                                     realtime_model, reasoning_model, transcribe_model,
                                     voice)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sid, title, (question or "").strip(), (context or "").strip(), framework, mode,
             decision_method, presence, 1 if record_audio else 0,
             max(0, int(duration_minutes or 0)), max(0, int(warn_minutes or 0)),
             "pending" if record_audio else "none",
             retention_policy, (_now() if participants_informed else None),
             realtime_model, reasoning_model, transcribe_model, voice),
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
               "transcribe_model", "voice", "started_at", "ended_at",
               "duration_minutes", "warn_minutes", "report_md", "report_at",
               "audio_status", "audio_note",
               # 2026-09-03: retention, the host's attestation, and the closeout
               # (rules 94/98). New columns must be added HERE as well as to the
               # migrations above, or they silently never save -- the same
               # allowlist gotcha AGENTS.md records for state.update_product.
               "retention_policy", "participants_informed_at", "closeout_outcome",
               "closeout_note", "closeout_at", "reflection_at",
               "transcript_deleted_at"}
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


def replace_diarized_turns(session_id: str, segments: list[dict],
                           db_path: Path | str | None = None) -> int:
    """Store the speaker-separated transcript, replacing any earlier diarised pass.

    The LIVE turns are untouched — running this twice cannot corrupt the record
    the room saw, and a diarisation that comes back worse can simply be dropped.
    The speaker label written here is the model's own letter; a person's name
    only appears once someone has mapped it (`apply_speaker_names`)."""
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM turns WHERE session_id = ? AND source = 'diarized'",
                     (session_id,))
        seq = 0
        for seg in segments:
            text = str(seg.get("text") or "").strip()
            if not text:
                continue
            seq += 1
            conn.execute(
                """INSERT INTO turns (session_id, realtime_item_id, sequence, role,
                                      speaker_label, text, is_final, source)
                   VALUES (?, NULL, ?, 'human', ?, ?, 1, 'diarized')""",
                (session_id, seq, (str(seg.get("speaker") or "").strip() or None), text))
        conn.commit()
    return seq


def has_diarized(session_id: str, db_path: Path | str | None = None) -> bool:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM turns WHERE session_id = ? AND source = 'diarized' LIMIT 1",
            (session_id,)).fetchone()
    return row is not None


def list_turns(session_id: str, final_only: bool = False, limit: int | None = None,
               source: str = "live", db_path: Path | str | None = None) -> list[dict]:
    """`source` is 'live', 'diarized', or 'best' — the diarised transcript when one
    exists, otherwise the live one. It defaults to 'live' so every existing caller
    (the reasoner above all) keeps reading exactly what it always read."""
    if source == "best":
        source = "diarized" if has_diarized(session_id, db_path=db_path) else "live"
    sql = "SELECT * FROM turns WHERE session_id = ?"
    params: list = [session_id]
    if source in ("live", "diarized"):
        sql += " AND source = ?"
        params.append(source)
    if final_only:
        sql += " AND is_final = 1"
    sql += " ORDER BY sequence ASC"
    with _connect(db_path) as conn:
        rows = _rows(conn.execute(sql, params))
    return rows[-limit:] if limit else rows


def unanalyzed_turns(session_id: str, db_path: Path | str | None = None) -> list[dict]:
    """Finalised turns the brain has not read yet — what makes analysis
    incremental rather than a re-reading of the whole meeting (rule 79)."""
    with _connect(db_path) as conn:
        return _rows(conn.execute(
            """SELECT * FROM turns
                WHERE session_id = ? AND is_final = 1 AND analyzed = 0
                  AND TRIM(text) != '' AND source = 'live'
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


def correct_turn(turn_id: int, text: str, db_path: Path | str | None = None) -> Optional[dict]:
    """A human fixing what the transcription misheard.

    `corrected_at` is stamped so the record shows the line was edited. The
    ORIGINAL is deliberately NOT kept: a person corrects a line precisely
    because the machine wrote down something that was not said, and storing
    the mishearing for ever would defeat the correction — especially when what
    it misheard was a name.

    This corrects the TRANSCRIPT and nothing else. It does not retroactively
    rebuild the consultation map, and nothing here should ever pretend it did:
    the map items are corrected by hand, by the people who know what was meant.
    """
    text = (text or "").strip()
    if not text:
        return None
    with _connect(db_path) as conn:
        conn.execute("UPDATE turns SET text = ?, corrected_at = ? WHERE id = ?",
                     (text, _now(), turn_id))
        conn.commit()
        row = conn.execute("SELECT * FROM turns WHERE id = ?", (turn_id,)).fetchone()
    return dict(row) if row else None


def delete_transcript(session_id: str, db_path: Path | str | None = None) -> dict:
    """Delete the words, keep the approved record (rule 94).

    What goes: every turn, and any audio recording. What stays: the report, the
    decisions, the action items, the verified passages, the consultation map —
    the things the meeting was FOR, and the things a person approved.

    Irreversible, and the caller must have said so on screen before arriving
    here. There is no soft delete, for the same reason `delete_session` has
    none: "delete the transcript" has to mean it."""
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
        turns = conn.execute("SELECT COUNT(*) FROM turns WHERE session_id = ?",
                             (session_id,)).fetchone()[0]
        conn.execute("DELETE FROM turns WHERE session_id = ?", (session_id,))
        conn.execute("""UPDATE sessions SET transcript_deleted_at = ?,
                                            audio_status = 'none', audio_note = ''
                         WHERE id = ?""", (_now(), session_id))
        conn.commit()
    return {"deleted": True, "turns": turns, "audio_files": removed_audio}


def transcript_exists(session_id: str, db_path: Path | str | None = None) -> bool:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT 1 FROM turns WHERE session_id = ? LIMIT 1",
                           (session_id,)).fetchone()
    return row is not None


def sessions_due_for_transcript_deletion(db_path: Path | str | None = None) -> list[dict]:
    """Sessions whose retention window has passed and still have words in them.

    The clock is checked when the application looks, not by a timer: a machine
    that was switched off through the seventh day deletes on the next start,
    not on the day. That is a real limitation and the UI says so rather than
    implying a guarantee this cannot make."""
    from agents.live_consultation import RETENTION_POLICIES
    out = []
    with _connect(db_path) as conn:
        rows = _rows(conn.execute(
            """SELECT id, retention_policy, ended_at, closeout_at, transcript_deleted_at
                 FROM sessions
                WHERE transcript_deleted_at IS NULL AND status = 'ended'"""))
    for row in rows:
        policy = RETENTION_POLICIES.get(row.get("retention_policy") or "keep")
        if not policy or policy["days"] is None:
            continue
        if policy["days"] == 0:
            if row.get("closeout_at"):
                out.append(row)
            continue
        stamp = row.get("ended_at")
        if not stamp:
            continue
        try:
            then = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            continue
        if (datetime.now() - then).days >= policy["days"]:
            out.append(row)
    return out


# ── Participants (rule 80, and its 2026-08-25 amendment) ────────────────────
#
# A name here is always something a human typed. Nothing in this file infers a
# speaker, and the LIVE path still cannot: OpenAI's diarisation model is not
# available to a realtime session, so during the meeting every turn is
# "Participant" exactly as before. `speaker_key` is filled in afterwards, from a
# recording, by a person saying "voice B is Tara" -- a mapping, not a
# recognition, and no voice sample is ever stored to identify anyone by.

def add_participant(session_id: str, name: str,
                    db_path: Path | str | None = None) -> Optional[dict]:
    name = (name or "").strip()[:80]
    if not name:
        return None
    pid = new_id("prt")
    with _connect(db_path) as conn:
        conn.execute("INSERT INTO participants (id, session_id, name) VALUES (?,?,?)",
                     (pid, session_id, name))
        conn.commit()
        row = conn.execute("SELECT * FROM participants WHERE id = ?", (pid,)).fetchone()
    return dict(row) if row else None


def list_participants(session_id: str, db_path: Path | str | None = None) -> list[dict]:
    with _connect(db_path) as conn:
        return _rows(conn.execute(
            "SELECT * FROM participants WHERE session_id = ? ORDER BY id", (session_id,)))


def remove_participant(participant_id: str, db_path: Path | str | None = None) -> bool:
    with _connect(db_path) as conn:
        cur = conn.execute("DELETE FROM participants WHERE id = ?", (participant_id,))
        conn.commit()
    return cur.rowcount > 0


def set_participant_speaker(participant_id: str, speaker_key: str | None,
                            db_path: Path | str | None = None) -> Optional[dict]:
    """Map a named person onto a diarised voice, or clear the mapping.

    One voice belongs to one person: assigning a key that another participant in
    the same session already holds takes it off them, rather than quietly
    labelling two people as the same voice."""
    key = (speaker_key or "").strip()[:16] or None
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM participants WHERE id = ?",
                           (participant_id,)).fetchone()
        if not row:
            return None
        if key:
            conn.execute("""UPDATE participants SET speaker_key = NULL
                             WHERE session_id = ? AND speaker_key = ? AND id != ?""",
                         (row["session_id"], key, participant_id))
        conn.execute("UPDATE participants SET speaker_key = ? WHERE id = ?",
                     (key, participant_id))
        conn.commit()
        row = conn.execute("SELECT * FROM participants WHERE id = ?",
                           (participant_id,)).fetchone()
    return dict(row) if row else None


def apply_speaker_names(session_id: str, db_path: Path | str | None = None) -> int:
    """Write the mapped names onto every turn carrying a diarised speaker key.

    Turns keep their raw key in `speaker_label` until a mapping exists, so an
    unmapped meeting reads "A"/"B" rather than pretending to know anyone."""
    people = [p for p in list_participants(session_id, db_path=db_path) if p["speaker_key"]]
    if not people:
        return 0
    changed = 0
    with _connect(db_path) as conn:
        for person in people:
            cur = conn.execute(
                """UPDATE turns SET speaker_label = ?
                    WHERE session_id = ? AND speaker_label = ?""",
                (person["name"], session_id, person["speaker_key"]))
            changed += cur.rowcount
        conn.commit()
    return changed


# ── Consultation state ──────────────────────────────────────────────────────

def get_state(session_id: str, db_path: Path | str | None = None) -> dict:
    """The consultation map, always in the CURRENT vocabulary.

    Normalising on read rather than migrating the file is deliberate: the rows
    here are the only record of real conversations, and rewriting them in place
    to change a status word is a bigger risk than translating on the way out.
    A half-upgraded database therefore always reads correctly, and the modern
    shape is written back the next time anything saves (rules 96/97)."""
    from agents.live_consultation import normalize_state
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM session_state WHERE session_id = ?",
                           (session_id,)).fetchone()
    if not row:
        return {"state_revision": 0}
    try:
        state = json.loads(row["state_json"])
    except Exception:
        state = {}
    state = normalize_state(state)
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

def _find_row(conn, table: str, session_id: str, map_id: str | None, key: str):
    """Locate an existing row by the map's stable id first, normalised text second.

    Identity USED to be the normalised text alone, and that was the whole bug
    (rule 95): an action first heard without an owner, then heard again with
    one, matched the old row and was handed straight back unchanged, while a
    REWORDED action became a second row. The map already assigns stable ids
    (`action_3`), and they were being thrown away at this boundary.

    Text stays as a fallback so the six real meetings whose rows predate
    `map_id` still match instead of silently doubling on the next pass -- and
    a text match ADOPTS the map id, so each row is claimed once and identity
    is stable from then on.
    """
    if map_id:
        row = conn.execute(
            f"SELECT * FROM {table} WHERE session_id = ? AND map_id = ?",
            (session_id, map_id)).fetchone()
        if row:
            return row
    if not key:
        return None
    return conn.execute(
        f"SELECT * FROM {table} WHERE session_id = ? AND dedupe_key = ?",
        (session_id, key)).fetchone()


def upsert_decision_candidate(session_id: str, text: str, rationale: str = "",
                              support: str = "", concerns: list[str] | None = None,
                              map_id: str | None = None,
                              db_path: Path | str | None = None) -> Optional[dict]:
    """A possible decision, created or REFINED. Never confirmed here —
    `confirm_decision` is the only path to that, and only a person can call it
    (rule 81), so `status` is untouched by everything below.

    A row a human has edited is left alone entirely: the model may keep
    noticing the candidate, but it does not get to overwrite what a person
    wrote about it.
    """
    text = (text or "").strip()
    key = text.lower()[:200]
    if not key:
        return None
    with _connect(db_path) as conn:
        existing = _find_row(conn, "decisions", session_id, map_id, key)
        if existing:
            row = dict(existing)
            if row.get("human_edited"):
                return _decision_out(row)
            # Only ever fill in or improve. A later pass that has forgotten the
            # rationale must not wipe the one already recorded.
            conn.execute(
                """UPDATE decisions
                      SET text = ?, rationale = ?, support = ?, concerns_json = ?,
                          dedupe_key = ?, map_id = COALESCE(map_id, ?)
                    WHERE id = ?""",
                (text or row["text"],
                 (rationale or "").strip() or row["rationale"],
                 (support or "").strip() or row["support"],
                 json.dumps(concerns) if concerns else row["concerns_json"],
                 key, map_id, row["id"]))
            conn.commit()
            return _decision_out(dict(conn.execute(
                "SELECT * FROM decisions WHERE id = ?", (row["id"],)).fetchone()))
        did = new_id("dec")
        conn.execute(
            """INSERT INTO decisions (id, session_id, text, rationale, support, concerns_json,
                                      dedupe_key, map_id)
               VALUES (?,?,?,?,?,?,?,?)""",
            (did, session_id, text, rationale or "", support or "",
             json.dumps(concerns or []), key, map_id))
        conn.commit()
        return _decision_out(dict(conn.execute(
            "SELECT * FROM decisions WHERE id = ?", (did,)).fetchone()))


def _decision_out(row: dict) -> dict:
    out = dict(row)
    for field, target in (("concerns_json", "concerns"),
                          ("retained_concerns_json", "retained_concerns")):
        try:
            out[target] = json.loads(out.pop(field, None) or "[]")
        except Exception:
            out[target] = []
    return out


def update_decision(decision_id: str, db_path: Path | str | None = None,
                    **fields) -> Optional[dict]:
    """A human editing a decision candidate. Sets `human_edited`, which is what
    stops the next analysis pass from putting its own words back (rule 95).

    `status` is NOT in the allowlist: confirming or rejecting goes through
    `set_decision_status`, so there is exactly one path to a confirmed decision
    (rule 81) and it cannot be reached by a general-purpose edit."""
    allowed = {"text", "rationale", "support"}
    sets, values = [], []
    for key, value in fields.items():
        if key in allowed and value is not None:
            sets.append(f"{key} = ?")
            values.append(value)
    if "concerns" in fields and isinstance(fields["concerns"], list):
        sets.append("concerns_json = ?")
        values.append(json.dumps([str(c) for c in fields["concerns"]]))
    if "retained_concerns" in fields and isinstance(fields["retained_concerns"], list):
        sets.append("retained_concerns_json = ?")
        values.append(json.dumps([str(c) for c in fields["retained_concerns"]]))
    if not sets:
        return None
    sets.append("human_edited = 1")
    values.append(decision_id)
    with _connect(db_path) as conn:
        conn.execute(f"UPDATE decisions SET {', '.join(sets)} WHERE id = ?", values)
        conn.commit()
        row = conn.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()
    return _decision_out(dict(row)) if row else None


def list_decisions(session_id: str, db_path: Path | str | None = None) -> list[dict]:
    with _connect(db_path) as conn:
        rows = _rows(conn.execute(
            "SELECT * FROM decisions WHERE session_id = ? ORDER BY created_at ASC, rowid ASC",
            (session_id,)))
    return [_decision_out(r) for r in rows]


def set_decision_status(decision_id: str, status: str,
                        retained_concerns: list[str] | None = None,
                        db_path: Path | str | None = None) -> Optional[dict]:
    """The ONE path to a confirmed decision, and only a person reaches it
    (rule 81). `retained_concerns` is how a decision can be confirmed while
    real dissent stays attached to it rather than being tidied away."""
    confirmed_at = _now() if status == "confirmed" else None
    with _connect(db_path) as conn:
        if retained_concerns is not None:
            conn.execute(
                "UPDATE decisions SET retained_concerns_json = ? WHERE id = ?",
                (json.dumps([str(c) for c in retained_concerns]), decision_id))
        conn.execute("UPDATE decisions SET status = ?, confirmed_at = ? WHERE id = ?",
                     (status, confirmed_at, decision_id))
        conn.commit()
        row = conn.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()
    return _decision_out(dict(row)) if row else None


def confirmed_decisions(session_id: str, db_path: Path | str | None = None) -> list[dict]:
    """Every confirmed decision. A consultation can settle more than one thing,
    and the old singular field could only ever show the first."""
    return [d for d in list_decisions(session_id, db_path=db_path)
            if d.get("status") == "confirmed"]


def confirmed_decision(session_id: str, db_path: Path | str | None = None) -> Optional[dict]:
    for d in list_decisions(session_id, db_path=db_path):
        if d.get("status") == "confirmed":
            return d
    return None


def upsert_action_item(session_id: str, action: str, owner: str | None = None,
                       due: str | None = None, map_id: str | None = None,
                       source_decision_id: str = "",
                       db_path: Path | str | None = None) -> Optional[dict]:
    """
    Create or REFINE a proposed action.

    This is the function the owner/deadline defect lived in. It found an
    existing row by normalised text and returned it untouched, so an action
    heard first as "book the hall" and later as "book the hall — Tara, by the
    12th" kept `owner=None, due=None` for ever. Measured on the real database
    before the fix: 169 action items, 2 owners, 0 due dates.

    Three things make it correct now, and each matters on its own:
      * identity is the map's stable id, falling back to text (`_find_row`);
      * an owner or a due date that arrives LATER is written;
      * a value already present is never cleared by a later pass that has
        forgotten it — refinement only ever adds.

    What it still cannot do is invent an owner: `owner` and `due` reach here
    only when someone actually said them (rule 83), and a human-edited row is
    left entirely alone (rule 95).
    """
    action = (action or "").strip()
    key = action.lower()[:200]
    if not key:
        return None
    with _connect(db_path) as conn:
        existing = _find_row(conn, "action_items", session_id, map_id, key)
        if existing:
            row = dict(existing)
            if row.get("human_edited"):
                return _action_out(row)
            conn.execute(
                """UPDATE action_items
                      SET action = ?, owner = COALESCE(?, owner), due = COALESCE(?, due),
                          dedupe_key = ?, map_id = COALESCE(map_id, ?),
                          source_decision_id = CASE WHEN ? != '' THEN ?
                                                    ELSE source_decision_id END
                    WHERE id = ?""",
                (action or row["action"], (owner or None), (due or None), key, map_id,
                 source_decision_id, source_decision_id, row["id"]))
            conn.commit()
            return _action_out(dict(conn.execute(
                "SELECT * FROM action_items WHERE id = ?", (row["id"],)).fetchone()))
        aid = new_id("act")
        conn.execute(
            """INSERT INTO action_items (id, session_id, action, owner, due, dedupe_key,
                                         map_id, status, source_decision_id)
               VALUES (?,?,?,?,?,?,?,'proposed',?)""",
            (aid, session_id, action, (owner or None), (due or None), key, map_id,
             source_decision_id))
        conn.commit()
        return _action_out(dict(conn.execute(
            "SELECT * FROM action_items WHERE id = ?", (aid,)).fetchone()))


def create_action_item(session_id: str, action: str, owner: str | None = None,
                       due: str | None = None,
                       db_path: Path | str | None = None) -> Optional[dict]:
    """A human adding an action the assistant never noticed. Born
    `human_edited`, so no analysis pass can rewrite it."""
    action = (action or "").strip()
    if not action:
        return None
    aid = new_id("act")
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO action_items (id, session_id, action, owner, due, dedupe_key,
                                         status, human_edited)
               VALUES (?,?,?,?,?,?,'proposed',1)""",
            (aid, session_id, action, (owner or None), (due or None),
             action.lower()[:200]))
        conn.commit()
        return _action_out(dict(conn.execute(
            "SELECT * FROM action_items WHERE id = ?", (aid,)).fetchone()))


def update_action_item(action_id: str, db_path: Path | str | None = None,
                       **fields) -> Optional[dict]:
    """A human correcting or committing to an action.

    Every field here is one only a person may set. `owner_accepted` is the
    load-bearing one: a named owner is a PROPOSAL until somebody records that
    they accepted, and None ("nobody has said") stays distinct from False
    ("asked, and did not")."""
    allowed = {"action", "owner", "due", "status", "success_criteria",
               "support_needed", "blocker", "progress_note", "accepted_by"}
    sets, values = [], []
    for key, value in fields.items():
        if key not in allowed or value is None:
            continue
        if key == "status":
            from agents.live_consultation import normalize_action_status
            value = normalize_action_status(str(value))
        sets.append(f"{key} = ?")
        values.append(value)
    if "owner_accepted" in fields:
        accepted = fields["owner_accepted"]
        sets.append("owner_accepted = ?")
        values.append(None if accepted is None else (1 if accepted else 0))
        sets.append("accepted_at = ?")
        values.append(_now() if accepted else None)
    if not sets:
        return None
    sets.append("human_edited = 1")
    values.append(action_id)
    with _connect(db_path) as conn:
        conn.execute(f"UPDATE action_items SET {', '.join(sets)} WHERE id = ?", values)
        conn.commit()
        row = conn.execute("SELECT * FROM action_items WHERE id = ?", (action_id,)).fetchone()
    return _action_out(dict(row)) if row else None


def delete_action_item(action_id: str, db_path: Path | str | None = None) -> bool:
    with _connect(db_path) as conn:
        cur = conn.execute("DELETE FROM action_items WHERE id = ?", (action_id,))
        conn.commit()
    return cur.rowcount > 0


def _action_out(row: dict) -> dict:
    """One shape for an action item, wherever it is read from.

    `owner_accepted` is coerced to a real bool (or None) HERE, at the boundary.
    SQLite stores 1/0, and letting that leak out broke both readers the same
    way: `accepted is True` is False for 1 in Python, and `=== true` is false
    for 1 in TypeScript. The export printed the self-contradicting
    "Tara - not yet accepted - accepted" as a result, caught by walking the
    whole flow end to end rather than by any unit test.

    It cannot simply be `bool(...)`: the three states are three different facts
    (rule 95), and None -- "nobody has recorded an answer" -- must stay
    distinct from False -- "asked, and declined".
    """
    out = dict(row)
    accepted = out.get("owner_accepted")
    out["owner_accepted"] = None if accepted is None else bool(accepted)
    return out


def list_action_items(session_id: str, db_path: Path | str | None = None) -> list[dict]:
    with _connect(db_path) as conn:
        return [_action_out(r) for r in _rows(conn.execute(
            "SELECT * FROM action_items WHERE session_id = ? ORDER BY created_at ASC, rowid ASC",
            (session_id,)))]


def set_action_status(action_id: str, status: str,
                      db_path: Path | str | None = None) -> Optional[dict]:
    with _connect(db_path) as conn:
        conn.execute("UPDATE action_items SET status = ? WHERE id = ?", (status, action_id))
        conn.commit()
        row = conn.execute("SELECT * FROM action_items WHERE id = ?", (action_id,)).fetchone()
    return _action_out(dict(row)) if row else None


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
