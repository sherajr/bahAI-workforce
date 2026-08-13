"""
Persistence for the Video Generation pipeline.

Same database and same conventions as `state.py` (workforce.db, sqlite3 with
row_factory, `CREATE TABLE IF NOT EXISTS` + try/except ALTER TABLE
migrations, short uuid4 ids) — this is a new set of tables in the existing
store, not a parallel persistence framework.

Asset rule (matches the rest of the repo): generated files live on disk in
`outputs/` and only their PATHS are stored here. Nothing binary goes in a row.

Everything a run produces is written as it happens, so closing the app and
coming back never loses source text, direction, analysis, the continuity
bible, shot order, locked fields, prompts, frames, clips, seeds, approval
state, or error/retry state.
"""

import json
import sqlite3
import uuid
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "workforce.db"

# Pipeline stages, in order. `stage` on a project is the furthest stage it has
# reached; the UI uses it to decide which step to open on.
STAGES = ("source", "direction", "analysis", "continuity", "shots",
          "frames", "clips", "review", "export")

SHOT_STATUSES = ("planned", "frames_ready", "clip_ready", "approved", "error")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _nid() -> str:
    return str(uuid.uuid4())[:8]


def init_video_db():
    """
    Create the video tables. Called from `state.init_db()` so it runs on every
    API startup, exactly like the rest of the schema.
    """
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS video_projects (
                id TEXT PRIMARY KEY,
                task_id TEXT,
                title TEXT,
                status TEXT DEFAULT 'draft',
                stage TEXT DEFAULT 'source',
                source_kind TEXT DEFAULT 'scene_story',
                source_text TEXT,
                source_brief TEXT,
                source_instructions TEXT,
                source_product_id TEXT,
                direction_json TEXT,
                analysis_json TEXT,
                continuity_json TEXT,
                safety_json TEXT,
                export_json TEXT,
                error TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS video_shots (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                shot_number INTEGER NOT NULL,
                beat_id TEXT,
                data_json TEXT,
                locked_json TEXT,
                status TEXT DEFAULT 'planned',
                approved INTEGER DEFAULT 0,
                continuity_mode TEXT DEFAULT 'editorial_cut',
                first_frame_path TEXT,
                last_frame_path TEXT,
                clip_path TEXT,
                error TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS video_assets (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                shot_id TEXT,
                kind TEXT NOT NULL,
                ref_id TEXT,
                path TEXT,
                prompt TEXT,
                seed INTEGER,
                model TEXT,
                meta_json TEXT,
                version INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS video_jobs (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                cursor TEXT,
                total INTEGER DEFAULT 0,
                done INTEGER DEFAULT 0,
                attempts INTEGER DEFAULT 0,
                error TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_video_shots_project
                ON video_shots(project_id, shot_number);
            CREATE INDEX IF NOT EXISTS idx_video_assets_shot
                ON video_assets(shot_id, kind);
            CREATE INDEX IF NOT EXISTS idx_video_jobs_project
                ON video_jobs(project_id, kind);
        """)
        conn.commit()

        # Column migrations for DBs created by an earlier version of this file
        # (state.py's pattern — CREATE TABLE IF NOT EXISTS is a no-op on an
        # existing table, so new columns need their own ALTER).
        for table, col in (
            ("video_projects", "export_json TEXT"),
            ("video_projects", "safety_json TEXT"),
            ("video_projects", "error TEXT"),
            ("video_shots", "continuity_mode TEXT DEFAULT 'editorial_cut'"),
            ("video_jobs", "total INTEGER DEFAULT 0"),
            ("video_jobs", "done INTEGER DEFAULT 0"),
        ):
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
                conn.commit()
            except Exception:
                pass  # already present


def _touch(conn, table: str, row_id: str):
    conn.execute(f"UPDATE {table} SET updated_at = datetime('now') WHERE id = ?", (row_id,))


def _loads(value, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


# --- Projects ---

def create_project(
    title: str,
    source_kind: str = "scene_story",
    source_text: str = "",
    source_brief: str = "",
    source_instructions: str = "",
    source_product_id: str | None = None,
    task_id: str | None = None,
    direction: dict | None = None,
) -> str:
    project_id = _nid()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO video_projects
               (id, task_id, title, source_kind, source_text, source_brief,
                source_instructions, source_product_id, direction_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, task_id, title, source_kind, source_text, source_brief,
             source_instructions, source_product_id,
             json.dumps(direction) if direction else None),
        )
        conn.commit()
    return project_id


PROJECT_JSON_FIELDS = {
    "direction": "direction_json",
    "analysis": "analysis_json",
    "continuity": "continuity_json",
    "safety": "safety_json",
    "export": "export_json",
}

_PROJECT_SCALARS = {"title", "status", "stage", "source_kind", "source_text",
                    "source_brief", "source_instructions", "source_product_id", "error"}


def update_project(project_id: str, **kwargs):
    """
    Update scalar columns directly and dict fields (direction/analysis/
    continuity/safety/export) as JSON. Unknown keys are ignored, matching
    `state.update_product`'s allowlist behaviour.
    """
    fields: dict[str, object] = {}
    for key, value in kwargs.items():
        if key in _PROJECT_SCALARS:
            fields[key] = value
        elif key in PROJECT_JSON_FIELDS:
            fields[PROJECT_JSON_FIELDS[key]] = json.dumps(value) if value is not None else None
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with _connect() as conn:
        conn.execute(
            f"UPDATE video_projects SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
            (*fields.values(), project_id),
        )
        conn.commit()


def _project_row_to_dict(row) -> dict:
    d = dict(row)
    for name, col in PROJECT_JSON_FIELDS.items():
        d[name] = _loads(d.pop(col, None), None)
    return d


def get_project(project_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM video_projects WHERE id = ?", (project_id,)).fetchone()
    return _project_row_to_dict(row) if row else None


def list_projects() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM video_projects ORDER BY created_at DESC"
        ).fetchall()
        counts = {
            r["project_id"]: r["n"]
            for r in conn.execute(
                "SELECT project_id, COUNT(*) AS n FROM video_shots GROUP BY project_id"
            ).fetchall()
        }
    out = []
    for row in rows:
        d = _project_row_to_dict(row)
        d["shot_count"] = counts.get(d["id"], 0)
        out.append(d)
    return out


def delete_project(project_id: str):
    """Remove a project and everything belonging to it (rows only — generated
    files in outputs/ are left on disk, same as products)."""
    with _connect() as conn:
        for table in ("video_assets", "video_jobs", "video_shots", "video_projects"):
            col = "id" if table == "video_projects" else "project_id"
            conn.execute(f"DELETE FROM {table} WHERE {col} = ?", (project_id,))
        conn.commit()


# --- Shots ---

def replace_shots(project_id: str, shots: list[dict]) -> list[str]:
    """
    Write a freshly-planned shot list, discarding any previous plan for the
    project. Used by the planner; per-shot edits use `update_shot` so they
    never disturb generated assets.
    """
    ids: list[str] = []
    with _connect() as conn:
        conn.execute("DELETE FROM video_shots WHERE project_id = ?", (project_id,))
        for i, shot in enumerate(shots, start=1):
            shot_id = _nid()
            ids.append(shot_id)
            conn.execute(
                """INSERT INTO video_shots
                   (id, project_id, shot_number, beat_id, data_json, locked_json,
                    status, continuity_mode)
                   VALUES (?, ?, ?, ?, ?, ?, 'planned', ?)""",
                (shot_id, project_id, i, shot.get("beat_id", ""), json.dumps(shot),
                 json.dumps(shot.get("locked_fields", [])),
                 shot.get("continuity_mode", "editorial_cut")),
            )
        conn.commit()
    return ids


def add_shot(project_id: str, shot: dict, after_number: int | None = None) -> str:
    """Insert one shot, renumbering everything after it."""
    shot_id = _nid()
    with _connect() as conn:
        if after_number is None:
            row = conn.execute(
                "SELECT COALESCE(MAX(shot_number), 0) AS m FROM video_shots WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            number = row["m"] + 1
        else:
            number = after_number + 1
            conn.execute(
                """UPDATE video_shots SET shot_number = shot_number + 1
                   WHERE project_id = ? AND shot_number >= ?""",
                (project_id, number),
            )
        conn.execute(
            """INSERT INTO video_shots
               (id, project_id, shot_number, beat_id, data_json, locked_json, status, continuity_mode)
               VALUES (?, ?, ?, ?, ?, ?, 'planned', ?)""",
            (shot_id, project_id, number, shot.get("beat_id", ""), json.dumps(shot),
             json.dumps(shot.get("locked_fields", [])),
             shot.get("continuity_mode", "editorial_cut")),
        )
        conn.commit()
    return shot_id


def delete_shot(shot_id: str):
    with _connect() as conn:
        row = conn.execute(
            "SELECT project_id, shot_number FROM video_shots WHERE id = ?", (shot_id,)
        ).fetchone()
        if not row:
            return
        conn.execute("DELETE FROM video_assets WHERE shot_id = ?", (shot_id,))
        conn.execute("DELETE FROM video_shots WHERE id = ?", (shot_id,))
        conn.execute(
            """UPDATE video_shots SET shot_number = shot_number - 1
               WHERE project_id = ? AND shot_number > ?""",
            (row["project_id"], row["shot_number"]),
        )
        conn.commit()


def reorder_shots(project_id: str, ordered_ids: list[str]):
    """
    Set shot_number from the given order. Written in two passes through a
    negative temporary range because shot_number has a uniqueness expectation
    per project — a direct rewrite can collide mid-update.
    """
    with _connect() as conn:
        for i, shot_id in enumerate(ordered_ids, start=1):
            conn.execute(
                "UPDATE video_shots SET shot_number = ? WHERE id = ? AND project_id = ?",
                (-i, shot_id, project_id),
            )
        conn.execute(
            """UPDATE video_shots SET shot_number = -shot_number
               WHERE project_id = ? AND shot_number < 0""",
            (project_id,),
        )
        conn.commit()


_SHOT_SCALARS = {"status", "approved", "first_frame_path", "last_frame_path",
                 "clip_path", "error", "beat_id", "continuity_mode", "shot_number"}


def update_shot(shot_id: str, data: dict | None = None, locked: list[str] | None = None, **kwargs):
    """
    Update a shot. `data` merges into the stored shot record (never replaces
    it wholesale, so an edit to one field can't drop the rest), respecting
    locked fields — a locked field is only changed when it is explicitly
    passed in `data` by a human edit (`force_locked=True`).
    """
    force_locked = bool(kwargs.pop("force_locked", False))
    fields = {k: v for k, v in kwargs.items() if k in _SHOT_SCALARS}
    if isinstance(fields.get("approved"), bool):
        fields["approved"] = int(fields["approved"])

    with _connect() as conn:
        row = conn.execute("SELECT * FROM video_shots WHERE id = ?", (shot_id,)).fetchone()
        if not row:
            return
        if data is not None:
            current = _loads(row["data_json"], {})
            locked_now = set(_loads(row["locked_json"], []))
            merged = dict(current)
            for key, value in data.items():
                if key in locked_now and not force_locked:
                    continue  # locked: regeneration must not overwrite it
                merged[key] = value
            fields["data_json"] = json.dumps(merged)
        if locked is not None:
            fields["locked_json"] = json.dumps(sorted(set(locked)))
        if not fields:
            return
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE video_shots SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
            (*fields.values(), shot_id),
        )
        conn.commit()


def _shot_row_to_dict(row) -> dict:
    d = dict(row)
    data = _loads(d.pop("data_json", None), {})
    d["locked_fields"] = _loads(d.pop("locked_json", None), [])
    d["approved"] = bool(d.get("approved"))
    d["data"] = data
    return d


def get_shot(shot_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM video_shots WHERE id = ?", (shot_id,)).fetchone()
    return _shot_row_to_dict(row) if row else None


def list_shots(project_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM video_shots WHERE project_id = ? ORDER BY shot_number",
            (project_id,),
        ).fetchall()
    return [_shot_row_to_dict(r) for r in rows]


# --- Assets (paths only; files live in outputs/) ---

def add_asset(project_id: str, kind: str, path: str, shot_id: str | None = None,
              prompt: str = "", seed: int | None = None, model: str = "",
              meta: dict | None = None, ref_id: str | None = None) -> str:
    """
    Record a generated file. Version numbers increment per (shot, kind) so the
    history of regenerations is preserved rather than overwritten.
    """
    asset_id = _nid()
    with _connect() as conn:
        row = conn.execute(
            """SELECT COALESCE(MAX(version), 0) AS v FROM video_assets
               WHERE project_id = ? AND IFNULL(shot_id,'') = ? AND kind = ?""",
            (project_id, shot_id or "", kind),
        ).fetchone()
        conn.execute(
            """INSERT INTO video_assets
               (id, project_id, shot_id, kind, ref_id, path, prompt, seed, model, meta_json, version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (asset_id, project_id, shot_id, kind, ref_id, path, prompt, seed, model,
             json.dumps(meta) if meta else None, row["v"] + 1),
        )
        conn.commit()
    return asset_id


def list_assets(project_id: str, shot_id: str | None = None, kind: str | None = None) -> list[dict]:
    sql = "SELECT * FROM video_assets WHERE project_id = ?"
    params: list = [project_id]
    if shot_id is not None:
        sql += " AND shot_id = ?"
        params.append(shot_id)
    if kind is not None:
        sql += " AND kind = ?"
        params.append(kind)
    sql += " ORDER BY created_at DESC, version DESC"
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    out = []
    for row in rows:
        d = dict(row)
        d["meta"] = _loads(d.pop("meta_json", None), {})
        out.append(d)
    return out


# --- Resumable generation jobs ---

def start_job(project_id: str, kind: str, total: int = 0) -> str:
    """
    Open (or reopen) a resumable generation job. One live job per
    (project, kind): reopening returns the existing row so an interrupted run
    resumes from its cursor instead of starting over.
    """
    with _connect() as conn:
        row = conn.execute(
            """SELECT * FROM video_jobs WHERE project_id = ? AND kind = ?
               AND status IN ('pending', 'running', 'interrupted')
               ORDER BY created_at DESC LIMIT 1""",
            (project_id, kind),
        ).fetchone()
        if row:
            conn.execute(
                """UPDATE video_jobs SET status = 'running', attempts = attempts + 1,
                   total = ?, error = NULL, updated_at = datetime('now') WHERE id = ?""",
                (total or row["total"], row["id"]),
            )
            conn.commit()
            return row["id"]
        job_id = _nid()
        conn.execute(
            """INSERT INTO video_jobs (id, project_id, kind, status, total)
               VALUES (?, ?, ?, 'running', ?)""",
            (job_id, project_id, kind, total),
        )
        conn.commit()
    return job_id


def update_job(job_id: str, **kwargs):
    allowed = {"status", "cursor", "total", "done", "error"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with _connect() as conn:
        conn.execute(
            f"UPDATE video_jobs SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
            (*fields.values(), job_id),
        )
        conn.commit()


def get_job(job_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM video_jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def latest_job(project_id: str, kind: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            """SELECT * FROM video_jobs WHERE project_id = ? AND kind = ?
               ORDER BY created_at DESC LIMIT 1""",
            (project_id, kind),
        ).fetchone()
    return dict(row) if row else None


def mark_interrupted_jobs():
    """
    Called at startup: any job still marked 'running' from a previous process
    is really interrupted (the API was killed mid-generation). Flipping it here
    is what makes 'Resume' honest rather than a guess.
    """
    with _connect() as conn:
        conn.execute(
            "UPDATE video_jobs SET status = 'interrupted', updated_at = datetime('now') "
            "WHERE status = 'running'"
        )
        conn.commit()
