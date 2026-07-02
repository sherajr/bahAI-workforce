"""
Persistent state for bahAI Workforce.
SQLite database: agents, tasks, task_runs, products.
This is the external memory that keeps agent context minimal —
agents read/write here instead of carrying full history in their prompts.
"""

import sqlite3
import json
import uuid
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "workforce.db"

TRUST_LEVELS = {
    0: "Shadow/Advisory",       # every step approved
    1: "Approval-gated",        # routine auto-runs, consequential pauses
    2: "Human-on-the-loop",     # runs autonomously, Sheraj can interrupt
    3: "Bounded autonomy",      # full autonomy within defined domain
}

AGENT_NAMES = ["operator", "librarian", "artist", "scribe", "reviewer", "producer", "steward"]


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables and seed agent rows if they don't exist."""
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS agents (
                name TEXT PRIMARY KEY,
                trust_level INTEGER DEFAULT 0,
                trust_score REAL DEFAULT 50.0,
                total_runs INTEGER DEFAULT 0,
                clean_runs INTEGER DEFAULT 0,
                consecutive_failures INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                directive TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                assigned_to TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                completed_at TEXT,
                card_json TEXT
            );

            CREATE TABLE IF NOT EXISTS task_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                agent TEXT NOT NULL,
                step TEXT NOT NULL,
                input_summary TEXT,
                output_summary TEXT,
                passed_review INTEGER,
                reviewer_scores TEXT,
                timestamp TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (task_id) REFERENCES tasks(id)
            );

            CREATE TABLE IF NOT EXISTS products (
                id TEXT PRIMARY KEY,
                task_id TEXT,
                title TEXT,
                status TEXT DEFAULT 'draft',
                etsy_listing_id TEXT,
                image_url TEXT,
                listing_copy TEXT,
                reviewer_scores TEXT,
                revenue REAL DEFAULT 0.0,
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)
        # Migrations — safe to run on existing databases
        for col in ("image_prompt TEXT", "theme TEXT"):
            try:
                conn.execute(f"ALTER TABLE products ADD COLUMN {col}")
                conn.commit()
            except Exception:
                pass  # column already exists

        for name in AGENT_NAMES:
            conn.execute(
                "INSERT OR IGNORE INTO agents (name) VALUES (?)", (name,)
            )
        conn.commit()


# --- Task management ---

def create_task(directive: str, task_type: str, assigned_to: str = None) -> str:
    task_id = str(uuid.uuid4())[:8]
    card = {"id": task_id, "directive": directive, "type": task_type, "steps": [], "outputs": {}}
    with _connect() as conn:
        conn.execute(
            "INSERT INTO tasks (id, type, directive, assigned_to, card_json) VALUES (?, ?, ?, ?, ?)",
            (task_id, task_type, directive, assigned_to, json.dumps(card))
        )
        conn.commit()
    _write_task_card(task_id, card)
    return task_id


def get_task(task_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return dict(row) if row else None


def update_task_status(task_id: str, status: str):
    completed_at = datetime.utcnow().isoformat() if status in ("completed", "failed") else None
    with _connect() as conn:
        conn.execute(
            "UPDATE tasks SET status = ?, completed_at = ? WHERE id = ?",
            (status, completed_at, task_id)
        )
        conn.commit()


def update_task_card(task_id: str, updates: dict):
    """Merge updates into the task card JSON and persist both to DB and file."""
    with _connect() as conn:
        row = conn.execute("SELECT card_json FROM tasks WHERE id = ?", (task_id,)).fetchone()
        card = json.loads(row["card_json"]) if row and row["card_json"] else {}
        card.update(updates)
        conn.execute("UPDATE tasks SET card_json = ? WHERE id = ?", (json.dumps(card), task_id))
        conn.commit()
    _write_task_card(task_id, card)


def _write_task_card(task_id: str, card: dict):
    tasks_dir = Path(__file__).parent.parent / "tasks"
    tasks_dir.mkdir(exist_ok=True)
    (tasks_dir / f"{task_id}.json").write_text(json.dumps(card, indent=2), encoding="utf-8")


def load_task_card(task_id: str) -> dict:
    card_path = Path(__file__).parent.parent / "tasks" / f"{task_id}.json"
    if card_path.exists():
        return json.loads(card_path.read_text(encoding="utf-8"))
    row = get_task(task_id)
    return json.loads(row["card_json"]) if row and row.get("card_json") else {}


# --- Task run logging ---

def log_run(task_id: str, agent: str, step: str, input_summary: str, output_summary: str,
            passed_review: bool | None = None, reviewer_scores: dict | None = None):
    with _connect() as conn:
        conn.execute(
            """INSERT INTO task_runs (task_id, agent, step, input_summary, output_summary,
               passed_review, reviewer_scores) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (task_id, agent, step, input_summary, output_summary,
             int(passed_review) if passed_review is not None else None,
             json.dumps(reviewer_scores) if reviewer_scores else None)
        )
        conn.commit()
    _update_agent_trust(agent, passed_review)


# --- Trust system ---

def _update_agent_trust(agent: str, passed: bool | None):
    if passed is None:
        return
    with _connect() as conn:
        row = conn.execute("SELECT * FROM agents WHERE name = ?", (agent,)).fetchone()
        if not row:
            return
        total = row["total_runs"] + 1
        clean = row["clean_runs"] + (1 if passed else 0)
        consec_fail = 0 if passed else row["consecutive_failures"] + 1
        score = min(100.0, max(0.0, (clean / total) * 100))

        # Trust level: advances at 80% clean rate over 5+ runs; regresses on 2 consecutive failures
        level = row["trust_level"]
        if consec_fail >= 2 and level > 0:
            level -= 1
        elif total >= 5 and score >= 80 and level < 3:
            level += 1

        conn.execute(
            """UPDATE agents SET trust_level=?, trust_score=?, total_runs=?,
               clean_runs=?, consecutive_failures=? WHERE name=?""",
            (level, score, total, clean, consec_fail, agent)
        )
        conn.commit()


def get_agent_status(agent: str) -> dict:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM agents WHERE name = ?", (agent,)).fetchone()
    if not row:
        return {}
    d = dict(row)
    d["trust_level_name"] = TRUST_LEVELS.get(d["trust_level"], "Unknown")
    return d


def get_all_agent_statuses() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM agents").fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["trust_level_name"] = TRUST_LEVELS.get(d["trust_level"], "Unknown")
        result.append(d)
    return result


# --- Products ---

def create_product(
    task_id: str,
    title: str,
    image_url: str = None,
    listing_copy: str = None,
    image_prompt: str = None,
    theme: str = None,
) -> str:
    product_id = str(uuid.uuid4())[:8]
    with _connect() as conn:
        conn.execute(
            "INSERT INTO products (id, task_id, title, image_url, listing_copy, image_prompt, theme)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (product_id, task_id, title, image_url, listing_copy, image_prompt, theme)
        )
        conn.commit()
    return product_id


def get_all_products() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM products ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def update_product(product_id: str, **kwargs):
    allowed = {"status", "etsy_listing_id", "image_url", "listing_copy", "reviewer_scores", "revenue", "title", "image_prompt", "theme"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with _connect() as conn:
        conn.execute(f"UPDATE products SET {set_clause} WHERE id = ?", (*fields.values(), product_id))
        conn.commit()


if __name__ == "__main__":
    init_db()
    tid = create_task("Design a Bahá'í-inspired bookmark for Etsy", "design", "operator")
    print(f"Created task: {tid}")
    print(get_all_agent_statuses())
