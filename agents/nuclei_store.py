"""
The Material World store — nuclei, friends, gatherings.

Rule 15 / 59: the ONLY module that touches community data at rest.
Writes to private/nuclei.db (git-ignored). Nothing personal may enter
workforce.db, log_run summaries, job progress strings, stdout, or any
committed file.

Rule 60: this store holds only what a depicted friend could also see.
No phone, email, address, or intimate-note column exists.

Rule 61: never score a person's spiritual condition.

Tests MUST pass db_path= pointing at a temp file, and must call
assert_test_db first. Production callers omit db_path.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

PRIVATE_DIR = Path(__file__).parent.parent / "private"
DB_PATH = PRIVATE_DIR / "nuclei.db"

# Participation slugs (exclusive axis). Service slugs are not exclusive.
PARTICIPATION_SLUGS = (
    "everyone_else",
    "connected",
    "regularly_participating",
)
CORE_SERVICE_SLUGS = (
    "tutoring",
    "animating",
    "hosting",
    "childrens_classes",
)
SERVICE_SLUGS = CORE_SERVICE_SLUGS + (
    "accompanying",
    "being_accompanied",
)
POSTURE_SLUGS = ("protagonist",)

ACTOR_KINDS = ("person", "household", "collective")

# The Bahá'í Workforce is a real place on this map, not a decoration: a
# singleton grouping of its own kind, so adding a person to it is an ordinary
# membership row and every existing gate, drawer and query works unchanged
# (rule 65). Its slug is stable so the layout can pin it to its fixed chair.
WORKFORCE_KIND = "workforce"
WORKFORCE_SLUG = "bahai_workforce"
WORKFORCE_NAME = "Bahá'í Workforce"

# Channels a grouping can be reached on. WhatsApp GROUPS only — a channel row
# never holds a person's number (rule 60); one-to-one numbers live in
# Abigail's own contacts table and only there (rule 28).
CHANNEL_KINDS = ("whatsapp_group",)

# Worldwide bodies we briefly seeded; they do not belong on this local map.
RETIRED_INSTITUTION_SLUGS = (
    "universal_house_of_justice",
    "national_spiritual_assembly",
    "counsellors",
)


def assert_test_db(path: Path | str) -> Path:
    """Refuse to run tests against the owner's real private database."""
    path = Path(path).resolve()
    real = DB_PATH.resolve()
    if path == real or real in path.parents or path in (PRIVATE_DIR.resolve(),):
        raise RuntimeError(
            "nuclei tests must not open private/nuclei.db — pass a temp path"
        )
    private = PRIVATE_DIR.resolve()
    try:
        path.relative_to(private)
    except ValueError:
        return path
    raise RuntimeError(
        "nuclei tests must not write inside private/ — pass a temp path"
    )


def _db(db_path: Path | str | None = None) -> Path:
    return Path(db_path) if db_path is not None else DB_PATH


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = _db(db_path)
    path.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def init_db(db_path: Path | str | None = None):
    """Create schema and seed kind rows (labels only — never people)."""
    with _connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS grouping_kinds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,
                label TEXT NOT NULL,
                description TEXT,
                is_nucleus INTEGER NOT NULL DEFAULT 0,
                accent TEXT NOT NULL DEFAULT 'amber',
                sort_order INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS axes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,
                label TEXT NOT NULL,
                description TEXT,
                exclusive INTEGER NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS facet_kinds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,
                axis_id INTEGER NOT NULL REFERENCES axes(id),
                label TEXT NOT NULL,
                description TEXT,
                is_core INTEGER NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS tie_kinds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,
                label TEXT NOT NULL,
                description TEXT,
                directed INTEGER NOT NULL DEFAULT 1,
                draw_style TEXT NOT NULL DEFAULT 'flow',
                sort_order INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS activity_kinds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,
                label TEXT NOT NULL,
                is_core INTEGER NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS institute_units (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,
                label TEXT NOT NULL,
                sequence_order INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS actors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                display_name TEXT NOT NULL,
                how_we_met TEXT,
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                archived_at TEXT,
                CHECK (kind IN ('person', 'household', 'collective'))
            );
            CREATE TABLE IF NOT EXISTS groupings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind_id INTEGER NOT NULL REFERENCES grouping_kinds(id),
                name TEXT NOT NULL,
                born_from_id INTEGER REFERENCES groupings(id),
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                archived_at TEXT,
                pos_x REAL,
                pos_y REAL,
                slug TEXT
            );
            CREATE TABLE IF NOT EXISTS memberships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id INTEGER NOT NULL REFERENCES actors(id),
                grouping_id INTEGER NOT NULL REFERENCES groupings(id),
                orbit_index INTEGER NOT NULL,
                introduced_as TEXT,
                introduced_by_actor_id INTEGER REFERENCES actors(id),
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                ended_at TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_membership_orbit
                ON memberships(grouping_id, orbit_index);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_membership_live
                ON memberships(actor_id, grouping_id) WHERE ended_at IS NULL;
            CREATE TABLE IF NOT EXISTS membership_facets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                membership_id INTEGER NOT NULL REFERENCES memberships(id),
                facet_kind_id INTEGER NOT NULL REFERENCES facet_kinds(id),
                started_at TEXT DEFAULT (datetime('now', 'localtime')),
                ended_at TEXT
            );
            CREATE TABLE IF NOT EXISTS ties (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind_id INTEGER NOT NULL REFERENCES tie_kinds(id),
                from_actor_id INTEGER NOT NULL REFERENCES actors(id),
                to_actor_id INTEGER NOT NULL REFERENCES actors(id),
                grouping_id INTEGER REFERENCES groupings(id),
                started_at TEXT DEFAULT (datetime('now', 'localtime')),
                ended_at TEXT
            );
            CREATE TABLE IF NOT EXISTS cycles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                started_on TEXT NOT NULL,
                ended_on TEXT,
                reflection TEXT
            );
            CREATE TABLE IF NOT EXISTS activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind_id INTEGER NOT NULL REFERENCES activity_kinds(id),
                grouping_id INTEGER REFERENCES groupings(id),
                cycle_id INTEGER REFERENCES cycles(id),
                title TEXT,
                happened_at TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            );
            CREATE TABLE IF NOT EXISTS activity_participants (
                activity_id INTEGER NOT NULL REFERENCES activities(id),
                actor_id INTEGER NOT NULL REFERENCES actors(id),
                role_slug TEXT NOT NULL DEFAULT 'present',
                PRIMARY KEY (activity_id, actor_id)
            );
            CREATE TABLE IF NOT EXISTS household_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                household_id INTEGER NOT NULL REFERENCES actors(id),
                person_id INTEGER NOT NULL REFERENCES actors(id),
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                ended_at TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_household_member_live
                ON household_members(household_id, person_id) WHERE ended_at IS NULL;
            CREATE TABLE IF NOT EXISTS study_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id INTEGER NOT NULL REFERENCES actors(id),
                unit_id INTEGER NOT NULL REFERENCES institute_units(id),
                grouping_id INTEGER REFERENCES groupings(id),
                status TEXT NOT NULL,
                started_on TEXT,
                completed_on TEXT
            );
            CREATE TABLE IF NOT EXISTS gifts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                grouping_id INTEGER NOT NULL REFERENCES groupings(id),
                activity_id INTEGER REFERENCES activities(id),
                product_id TEXT,
                kind TEXT NOT NULL,
                theme TEXT,
                requested_at TEXT DEFAULT (datetime('now', 'localtime')),
                received_at TEXT,
                pending_action_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS grouping_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                grouping_id INTEGER NOT NULL REFERENCES groupings(id),
                kind TEXT NOT NULL DEFAULT 'whatsapp_group',
                label TEXT,
                link TEXT,
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                ended_at TEXT
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        _seed_kinds(conn)
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('quiet_after_days', '35')"
        )
        _migrate_groupings(conn)
        _retire_worldwide_institutions(conn)
        _restack_institutions(conn)
        _ensure_workforce_row(conn)
        conn.commit()


def _migrate_groupings(conn: sqlite3.Connection):
    """Owner-dragged chairs and institution slugs. CREATE TABLE is a no-op on disk."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(groupings)")}
    if "pos_x" not in cols:
        conn.execute("ALTER TABLE groupings ADD COLUMN pos_x REAL")
    if "pos_y" not in cols:
        conn.execute("ALTER TABLE groupings ADD COLUMN pos_y REAL")
    if "slug" not in cols:
        conn.execute("ALTER TABLE groupings ADD COLUMN slug TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_groupings_slug "
        "ON groupings(slug) WHERE slug IS NOT NULL"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS household_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            household_id INTEGER NOT NULL REFERENCES actors(id),
            person_id INTEGER NOT NULL REFERENCES actors(id),
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            ended_at TEXT
        )
    """)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_household_member_live "
        "ON household_members(household_id, person_id) WHERE ended_at IS NULL"
    )
    conn.execute(
        "UPDATE groupings SET name = 'Local Spiritual Assembly' "
        "WHERE name = 'Local Assembly'"
    )


def is_institution(g: dict | None) -> bool:
    if not g:
        return False
    return g.get("kind_slug") == "institution"


def is_workforce(g: dict | None) -> bool:
    if not g:
        return False
    return g.get("kind_slug") == WORKFORCE_KIND


def _ensure_workforce_row(conn: sqlite3.Connection):
    """One workforce, always present, always the same row (rule 65).

    Created here rather than through create_grouping so it can never be
    given a table chair: nuclei_layout pins it at its own fixed light and
    assign_slots skips it, so a new nucleus never slides onto it.
    """
    row = conn.execute(
        "SELECT id FROM groupings WHERE slug = ?", (WORKFORCE_SLUG,)
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE groupings SET archived_at = NULL WHERE id = ?", (row["id"],)
        )
        return
    kind = conn.execute(
        "SELECT id FROM grouping_kinds WHERE slug = ?", (WORKFORCE_KIND,)
    ).fetchone()
    if not kind:
        return
    conn.execute(
        "INSERT INTO groupings (kind_id, name, slug) VALUES (?, ?, ?)",
        (kind["id"], WORKFORCE_NAME, WORKFORCE_SLUG),
    )


def _retire_worldwide_institutions(conn: sqlite3.Connection):
    """Take House of Justice / National Assembly / Counsellors off this map.

    They were seeded once. This local picture is for LSA, the institute,
    Auxiliary Board, teaching committees — bodies the owner adds.
    """
    now = _now()
    for slug in RETIRED_INSTITUTION_SLUGS:
        row = conn.execute(
            "SELECT id FROM groupings WHERE slug = ? AND archived_at IS NULL",
            (slug,),
        ).fetchone()
        if not row:
            continue
        gid = int(row["id"])
        conn.execute(
            "UPDATE groupings SET archived_at = ? WHERE id = ?", (now, gid)
        )
        conn.execute(
            "UPDATE memberships SET ended_at = ? "
            "WHERE grouping_id = ? AND ended_at IS NULL",
            (now, gid),
        )


def _restack_institutions(conn: sqlite3.Connection):
    """One-time: drop auto-saved chairs so institutions sit on the tighter column.

    A later drag still writes pos_x/pos_y and is kept.
    """
    row = conn.execute(
        "SELECT value FROM settings WHERE key = 'institutions_stack_v2'"
    ).fetchone()
    if row:
        return
    kind = conn.execute(
        "SELECT id FROM grouping_kinds WHERE slug = 'institution'"
    ).fetchone()
    if kind:
        conn.execute(
            "UPDATE groupings SET pos_x = NULL, pos_y = NULL WHERE kind_id = ?",
            (int(kind["id"]),),
        )
    conn.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('institutions_stack_v2', '1')"
    )


def _seed_kinds(conn: sqlite3.Connection):
    groupings = [
        ("nucleus", "Nucleus", 1, "sky", 0),
        ("neighborhood", "Neighbourhood", 0, "emerald", 1),
        ("network", "Network", 0, "rose", 2),
        ("junior_youth", "Junior youth families", 0, "rose", 3),
        ("other", "Other grouping", 0, "amber", 4),
        ("institution", "Institution of the Faith", 0, "gold", 5),
        ("workforce", "The Bahá'í Workforce", 0, "amber", 6),
    ]
    for slug, label, is_n, accent, order in groupings:
        conn.execute(
            "INSERT OR IGNORE INTO grouping_kinds "
            "(slug, label, is_nucleus, accent, sort_order) VALUES (?, ?, ?, ?, ?)",
            (slug, label, is_n, accent, order),
        )
    axes = [
        ("participation", "How they gather", 1, 0),
        ("service", "How they serve", 0, 1),
        ("posture", "Taking initiative", 0, 2),
    ]
    for slug, label, exclusive, order in axes:
        conn.execute(
            "INSERT OR IGNORE INTO axes (slug, label, exclusive, sort_order) "
            "VALUES (?, ?, ?, ?)",
            (slug, label, exclusive, order),
        )
    axis_id = {r["slug"]: r["id"] for r in _rows(conn.execute("SELECT id, slug FROM axes"))}
    facets = [
        ("everyone_else", "participation", "Everyone else", 0, 0),
        ("connected", "participation", "Connected", 0, 1),
        ("regularly_participating", "participation", "Gathers regularly", 0, 2),
        ("tutoring", "service", "Tutoring a study circle", 1, 0),
        ("animating", "service", "Animating a junior youth group", 1, 1),
        ("hosting", "service", "Hosting a devotional", 1, 2),
        ("childrens_classes", "service", "Teaching a children's class", 1, 3),
        ("accompanying", "service", "Walking with a friend", 0, 4),
        ("being_accompanied", "service", "Being walked with", 0, 5),
        ("protagonist", "posture", "Taking initiative", 0, 0),
    ]
    for slug, axis, label, is_core, order in facets:
        conn.execute(
            "INSERT OR IGNORE INTO facet_kinds "
            "(slug, axis_id, label, is_core, sort_order) VALUES (?, ?, ?, ?, ?)",
            (slug, axis_id[axis], label, is_core, order),
        )
    ties = [
        ("accompanying", "Walking with", 1, "flow", 0),
        ("household", "Household", 0, "household", 1),
        ("introduced", "Introduced", 1, "introduced", 2),
    ]
    for slug, label, directed, style, order in ties:
        conn.execute(
            "INSERT OR IGNORE INTO tie_kinds "
            "(slug, label, directed, draw_style, sort_order) VALUES (?, ?, ?, ?, ?)",
            (slug, label, directed, style, order),
        )
    activities = [
        ("devotional", "Devotional gathering", 1, 0),
        ("childrens_class", "Children's class", 1, 1),
        ("junior_youth", "Junior youth group", 1, 2),
        ("study_circle", "Study circle", 1, 3),
        ("conversation", "Conversation", 0, 4),
        ("home_visit", "Home visit", 0, 5),
        ("book_share", "Book share", 0, 6),
        ("gathering", "Gathering", 0, 7),
        ("sharing_faith", "Sharing the Faith", 0, 8),
        ("camp", "Camp or weekend school", 0, 9),
    ]
    for slug, label, is_core, order in activities:
        conn.execute(
            "INSERT OR IGNORE INTO activity_kinds "
            "(slug, label, is_core, sort_order) VALUES (?, ?, ?, ?)",
            (slug, label, is_core, order),
        )
    units = [
        ("ruhi_1", "Book 1 — Reflections on the Life of the Spirit", 1),
        ("ruhi_2", "Book 2 — Arising to Serve", 2),
        ("ruhi_3", "Book 3 — Teaching Children's Classes", 3),
        ("ruhi_5", "Book 5 — Releasing the Powers of Junior Youth", 5),
        ("ruhi_7", "Book 7 — Walking Together on a Path of Service", 7),
    ]
    for slug, label, order in units:
        conn.execute(
            "INSERT OR IGNORE INTO institute_units (slug, label, sequence_order) "
            "VALUES (?, ?, ?)",
            (slug, label, order),
        )


# --- settings ---

def get_setting(key: str, default: str | None = None,
                db_path: Path | str | None = None) -> str | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str, db_path: Path | str | None = None):
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()


# --- kinds ---

def list_kinds(db_path: Path | str | None = None) -> dict:
    with _connect(db_path) as conn:
        return {
            "grouping_kinds": _rows(conn.execute(
                "SELECT * FROM grouping_kinds ORDER BY sort_order, id")),
            "axes": _rows(conn.execute("SELECT * FROM axes ORDER BY sort_order, id")),
            "facet_kinds": _rows(conn.execute(
                "SELECT fk.*, a.slug AS axis_slug, a.exclusive AS axis_exclusive "
                "FROM facet_kinds fk JOIN axes a ON a.id = fk.axis_id "
                "ORDER BY a.sort_order, fk.sort_order, fk.id")),
            "tie_kinds": _rows(conn.execute(
                "SELECT * FROM tie_kinds ORDER BY sort_order, id")),
            "activity_kinds": _rows(conn.execute(
                "SELECT * FROM activity_kinds ORDER BY sort_order, id")),
            "institute_units": _rows(conn.execute(
                "SELECT * FROM institute_units ORDER BY sequence_order, id")),
        }


def _kind_id(table: str, slug: str, db_path: Path | str | None = None) -> int:
    with _connect(db_path) as conn:
        row = conn.execute(f"SELECT id FROM {table} WHERE slug = ?", (slug,)).fetchone()
    if not row:
        raise ValueError(f"unknown {table} slug: {slug}")
    return int(row["id"])


# --- groupings ---

def create_grouping(kind_slug: str, name: str,
                    db_path: Path | str | None = None) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("A grouping needs a name")
    if kind_slug == WORKFORCE_KIND:
        # There is exactly one workforce and init_db already made it.
        raise ValueError("There is only one Bahá'í Workforce")
    if kind_slug == "institution" and name == "Local Assembly":
        name = "Local Spiritual Assembly"
    kind_id = _kind_id("grouping_kinds", kind_slug, db_path)
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO groupings (kind_id, name) VALUES (?, ?)",
            (kind_id, name),
        )
        conn.commit()
        gid = cur.lastrowid
    if kind_slug == "nucleus":
        owner = ensure_owner(db_path)
        mem = add_membership(int(owner["id"]), int(gid), db_path=db_path)
        add_facet(mem["id"], "regularly_participating", db_path)
    _place_new_grouping(int(gid), db_path)
    return get_grouping(gid, db_path)


def _place_new_grouping(gid: int, db_path: Path | str | None = None):
    """Sit a new table or institution on its default chair."""
    from agents.nuclei_layout import assign_slots
    placed = assign_slots(
        list_groupings(db_path, include_archived=True),
        live_memberships(db_path),
    )
    me = next((g for g in placed if int(g["id"]) == int(gid)), None)
    if not me:
        return
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE groupings SET pos_x = ?, pos_y = ? WHERE id = ?",
            (me["slot"]["cx"], me["slot"]["cy"], gid),
        )
        conn.commit()


def get_grouping(grouping_id: int, db_path: Path | str | None = None) -> dict | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT g.*, k.slug AS kind_slug, k.label AS kind_label, "
            "k.is_nucleus, k.accent "
            "FROM groupings g JOIN grouping_kinds k ON k.id = g.kind_id "
            "WHERE g.id = ?",
            (grouping_id,),
        ).fetchone()
    return dict(row) if row else None


def list_groupings(db_path: Path | str | None = None,
                   include_archived: bool = False) -> list[dict]:
    sql = (
        "SELECT g.*, k.slug AS kind_slug, k.label AS kind_label, "
        "k.is_nucleus, k.accent "
        "FROM groupings g JOIN grouping_kinds k ON k.id = g.kind_id "
    )
    if not include_archived:
        sql += "WHERE g.archived_at IS NULL "
    sql += "ORDER BY g.created_at, g.id"
    with _connect(db_path) as conn:
        return _rows(conn.execute(sql))


def archive_grouping(grouping_id: int, db_path: Path | str | None = None) -> dict:
    """Take a nucleus off the map. Friends stay. Gatherings stay.

    Memberships of this grouping are ended. Actors are not deleted.
    The grouping keeps its slot so a neighbour does not slide over
    (rule 62).
    """
    g = get_grouping(grouping_id, db_path)
    if not g:
        raise ValueError("No such grouping")
    if g.get("archived_at"):
        return g
    now = _now()
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE groupings SET archived_at = ? WHERE id = ?",
            (now, grouping_id),
        )
        conn.execute(
            "UPDATE memberships SET ended_at = ? "
            "WHERE grouping_id = ? AND ended_at IS NULL",
            (now, grouping_id),
        )
        conn.commit()
    out = get_grouping(grouping_id, db_path)
    if not out:
        raise ValueError("No such grouping")
    return out


def update_grouping(grouping_id: int, name: str | None = None,
                    db_path: Path | str | None = None) -> dict:
    g = get_grouping(grouping_id, db_path)
    if not g:
        raise ValueError("No such grouping")
    if name is not None:
        name = name.strip()
        if not name:
            raise ValueError("A grouping needs a name")
        with _connect(db_path) as conn:
            conn.execute("UPDATE groupings SET name = ? WHERE id = ?",
                         (name, grouping_id))
            conn.commit()
    return get_grouping(grouping_id, db_path)


def set_grouping_position(grouping_id: int, x: float, y: float,
                          db_path: Path | str | None = None) -> dict:
    """Owner dragged this table. A neighbour does not move."""
    import math
    from agents.nuclei_layout import clamp_institution, clamp_point
    g = get_grouping(grouping_id, db_path)
    if not g:
        raise ValueError("No such grouping")
    if g.get("archived_at"):
        raise ValueError("That table is off the map")
    if not math.isfinite(float(x)) or not math.isfinite(float(y)):
        raise ValueError("Need a place on the map")
    if is_institution(g):
        cx, cy = clamp_institution(float(x), float(y))
    else:
        cx, cy = clamp_point(float(x), float(y))
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE groupings SET pos_x = ?, pos_y = ? WHERE id = ?",
            (cx, cy, grouping_id),
        )
        conn.commit()
    out = get_grouping(grouping_id, db_path)
    if not out:
        raise ValueError("No such grouping")
    return out


def optimize_layout(db_path: Path | str | None = None) -> dict:
    """Rearrange every live table from size, gatherings and relations."""
    from agents.nuclei_layout import optimize_positions
    init_db(db_path)
    owner = ensure_owner(db_path)
    # The workforce keeps its own fixed light, so it is never arranged
    # with the tables and never given a pos_x/pos_y (rule 65).
    community = [g for g in list_groupings(db_path, include_archived=True)
                 if not is_institution(g) and not is_workforce(g)]
    positions = optimize_positions(
        community,
        live_memberships(db_path),
        live_ties(db_path),
        activity_counts_by_grouping(db_path),
        owner_id=int(owner["id"]),
    )
    with _connect(db_path) as conn:
        for gid, (x, y) in positions.items():
            conn.execute(
                "UPDATE groupings SET pos_x = ?, pos_y = ? "
                "WHERE id = ? AND archived_at IS NULL",
                (x, y, gid),
            )
        conn.commit()
    return snapshot(db_path)


# --- actors ---

def create_actor(kind: str, display_name: str, how_we_met: str | None = None,
                 db_path: Path | str | None = None) -> dict:
    if kind not in ACTOR_KINDS:
        raise ValueError("kind must be person, household or collective")
    display_name = (display_name or "").strip()
    if not display_name:
        raise ValueError("A friend needs a name")
    how = (how_we_met or "").strip() or None
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO actors (kind, display_name, how_we_met) VALUES (?, ?, ?)",
            (kind, display_name, how),
        )
        conn.commit()
        aid = cur.lastrowid
    return get_actor(aid, db_path)


def get_actor(actor_id: int, db_path: Path | str | None = None) -> dict | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM actors WHERE id = ?", (actor_id,)).fetchone()
    return dict(row) if row else None


def list_actors(db_path: Path | str | None = None) -> list[dict]:
    with _connect(db_path) as conn:
        return _rows(conn.execute(
            "SELECT * FROM actors WHERE archived_at IS NULL ORDER BY id"
        ))


def update_actor(actor_id: int, display_name: str | None = None,
                 how_we_met: str | None = None,
                 db_path: Path | str | None = None) -> dict:
    a = get_actor(actor_id, db_path)
    if not a:
        raise ValueError("No such friend")
    fields = {}
    if display_name is not None:
        name = display_name.strip()
        if not name:
            raise ValueError("A friend needs a name")
        fields["display_name"] = name
    if how_we_met is not None:
        fields["how_we_met"] = how_we_met.strip() or None
    if fields:
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        with _connect(db_path) as conn:
            conn.execute(
                f"UPDATE actors SET {set_clause} WHERE id = ?",
                (*fields.values(), actor_id),
            )
            conn.commit()
    return get_actor(actor_id, db_path)


def archive_actor(actor_id: int, db_path: Path | str | None = None) -> dict:
    """Take a friend off the map. Gatherings stay. The row is kept.

    Live memberships end so they leave every table. The owner's light
    cannot be archived.
    """
    a = get_actor(actor_id, db_path)
    if not a:
        raise ValueError("No such friend")
    owner = ensure_owner(db_path)
    if int(a["id"]) == int(owner["id"]):
        raise ValueError("This light is you — it stays on the map")
    if a.get("archived_at"):
        return a
    now = _now()
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE actors SET archived_at = ? WHERE id = ?",
            (now, actor_id),
        )
        conn.execute(
            "UPDATE memberships SET ended_at = ? "
            "WHERE actor_id = ? AND ended_at IS NULL",
            (now, actor_id),
        )
        conn.execute(
            "UPDATE household_members SET ended_at = ? "
            "WHERE ended_at IS NULL AND (person_id = ? OR household_id = ?)",
            (now, actor_id, actor_id),
        )
        conn.commit()
    out = get_actor(actor_id, db_path)
    if not out:
        raise ValueError("No such friend")
    return out


def add_household_member(household_id: int, person_id: int,
                         db_path: Path | str | None = None) -> dict:
    """A person belongs to this family. They may already sit at another table."""
    house = get_actor(household_id, db_path)
    person = get_actor(person_id, db_path)
    if not house or house.get("kind") != "household" or house.get("archived_at"):
        raise ValueError("That is not a family on the map")
    if not person or person.get("kind") != "person" or person.get("archived_at"):
        raise ValueError("That friend is not on the map")
    if int(household_id) == int(person_id):
        raise ValueError("A family cannot contain itself")
    with _connect(db_path) as conn:
        live = conn.execute(
            "SELECT * FROM household_members WHERE household_id = ? AND person_id = ? "
            "AND ended_at IS NULL",
            (household_id, person_id),
        ).fetchone()
        if live:
            return dict(live)
        elsewhere = conn.execute(
            "SELECT id FROM household_members WHERE person_id = ? AND ended_at IS NULL",
            (person_id,),
        ).fetchone()
        if elsewhere:
            conn.execute(
                "UPDATE household_members SET ended_at = ? WHERE id = ?",
                (_now(), int(elsewhere["id"])),
            )
        cur = conn.execute(
            "INSERT INTO household_members (household_id, person_id) VALUES (?, ?)",
            (household_id, person_id),
        )
        conn.commit()
        mid = cur.lastrowid
        row = conn.execute("SELECT * FROM household_members WHERE id = ?", (mid,)).fetchone()
    return dict(row)


def end_household_member(member_id: int, db_path: Path | str | None = None):
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE household_members SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
            (_now(), member_id),
        )
        conn.commit()


def live_household_members(db_path: Path | str | None = None) -> list[dict]:
    with _connect(db_path) as conn:
        return _rows(conn.execute(
            "SELECT hm.*, p.display_name AS person_name, p.kind AS person_kind, "
            "h.display_name AS household_name "
            "FROM household_members hm "
            "JOIN actors p ON p.id = hm.person_id "
            "JOIN actors h ON h.id = hm.household_id "
            "WHERE hm.ended_at IS NULL ORDER BY hm.id"
        ))


def members_of_household(household_id: int, db_path: Path | str | None = None) -> list[dict]:
    return [m for m in live_household_members(db_path)
            if int(m["household_id"]) == int(household_id)]


def household_of_person(person_id: int, db_path: Path | str | None = None) -> dict | None:
    for m in live_household_members(db_path):
        if int(m["person_id"]) == int(person_id):
            return m
    return None


def ensure_owner(db_path: Path | str | None = None) -> dict:
    """The light that is you. Created once, renameable."""
    raw = get_setting("owner_actor_id", None, db_path)
    if raw:
        actor = get_actor(int(raw), db_path)
        if actor:
            return actor
    actor = create_actor("person", "You", db_path=db_path)
    set_setting("owner_actor_id", str(actor["id"]), db_path)
    return actor


# --- memberships ---

def next_orbit_index(grouping_id: int, db_path: Path | str | None = None) -> int:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(orbit_index) AS m FROM memberships WHERE grouping_id = ?",
            (grouping_id,),
        ).fetchone()
    return (int(row["m"]) + 1) if row and row["m"] is not None else 0


def add_membership(actor_id: int, grouping_id: int,
                   introduced_as: str | None = None,
                   introduced_by_actor_id: int | None = None,
                   db_path: Path | str | None = None) -> dict:
    actor = get_actor(actor_id, db_path)
    if not actor:
        raise ValueError("No such friend")
    if actor.get("archived_at"):
        raise ValueError("That friend is off the map")
    grouping = get_grouping(grouping_id, db_path)
    if not grouping:
        raise ValueError("No such grouping")
    if grouping.get("archived_at"):
        raise ValueError("That table is off the map")
    with _connect(db_path) as conn:
        live = conn.execute(
            "SELECT id FROM memberships WHERE actor_id = ? AND grouping_id = ? "
            "AND ended_at IS NULL",
            (actor_id, grouping_id),
        ).fetchone()
    if live:
        return get_membership(int(live["id"]), db_path)
    idx = next_orbit_index(grouping_id, db_path)
    intro = (introduced_as or "").strip() or None
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO memberships "
            "(actor_id, grouping_id, orbit_index, introduced_as, introduced_by_actor_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (actor_id, grouping_id, idx, intro, introduced_by_actor_id),
        )
        conn.commit()
        mid = cur.lastrowid
    return get_membership(mid, db_path)


def get_membership(membership_id: int, db_path: Path | str | None = None) -> dict | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM memberships WHERE id = ?", (membership_id,)
        ).fetchone()
    return dict(row) if row else None


def end_membership(membership_id: int, db_path: Path | str | None = None):
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE memberships SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
            (_now(), membership_id),
        )
        conn.commit()


def live_memberships(db_path: Path | str | None = None) -> list[dict]:
    with _connect(db_path) as conn:
        return _rows(conn.execute(
            "SELECT * FROM memberships WHERE ended_at IS NULL ORDER BY id"
        ))


# --- facets ---

def _facet_kind(slug: str, db_path: Path | str | None = None) -> dict:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT fk.*, a.slug AS axis_slug, a.exclusive AS axis_exclusive "
            "FROM facet_kinds fk JOIN axes a ON a.id = fk.axis_id WHERE fk.slug = ?",
            (slug,),
        ).fetchone()
    if not row:
        raise ValueError(f"unknown facet: {slug}")
    return dict(row)


def live_facets(membership_id: int, db_path: Path | str | None = None) -> list[dict]:
    with _connect(db_path) as conn:
        return _rows(conn.execute(
            "SELECT mf.*, fk.slug, fk.label, fk.is_core, a.slug AS axis_slug "
            "FROM membership_facets mf "
            "JOIN facet_kinds fk ON fk.id = mf.facet_kind_id "
            "JOIN axes a ON a.id = fk.axis_id "
            "WHERE mf.membership_id = ? AND mf.ended_at IS NULL "
            "ORDER BY a.sort_order, fk.sort_order",
            (membership_id,),
        ))


def add_facet(membership_id: int, slug: str,
              db_path: Path | str | None = None) -> dict:
    mem = get_membership(membership_id, db_path)
    if not mem or mem.get("ended_at"):
        raise ValueError("No live membership")
    kind = _facet_kind(slug, db_path)
    existing = live_facets(membership_id, db_path)
    for f in existing:
        if f["slug"] == slug:
            return f
    if kind["axis_exclusive"]:
        for f in existing:
            if f["axis_slug"] == kind["axis_slug"]:
                end_facet(int(f["id"]), db_path)
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO membership_facets (membership_id, facet_kind_id) VALUES (?, ?)",
            (membership_id, kind["id"]),
        )
        conn.commit()
        fid = cur.lastrowid
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT mf.*, fk.slug, fk.label, fk.is_core, a.slug AS axis_slug "
            "FROM membership_facets mf "
            "JOIN facet_kinds fk ON fk.id = mf.facet_kind_id "
            "JOIN axes a ON a.id = fk.axis_id WHERE mf.id = ?",
            (fid,),
        ).fetchone()
    return dict(row)


def end_facet(facet_id: int, db_path: Path | str | None = None):
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE membership_facets SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
            (_now(), facet_id),
        )
        conn.commit()


# --- ties ---

def add_tie(kind_slug: str, from_actor_id: int, to_actor_id: int,
            grouping_id: int | None = None,
            db_path: Path | str | None = None) -> dict:
    kid = _kind_id("tie_kinds", kind_slug, db_path)
    with _connect(db_path) as conn:
        if grouping_id is None:
            live = conn.execute(
                "SELECT * FROM ties WHERE kind_id = ? AND from_actor_id = ? "
                "AND to_actor_id = ? AND grouping_id IS NULL AND ended_at IS NULL",
                (kid, from_actor_id, to_actor_id),
            ).fetchone()
        else:
            live = conn.execute(
                "SELECT * FROM ties WHERE kind_id = ? AND from_actor_id = ? "
                "AND to_actor_id = ? AND grouping_id = ? AND ended_at IS NULL",
                (kid, from_actor_id, to_actor_id, grouping_id),
            ).fetchone()
        if live:
            return dict(live)
        cur = conn.execute(
            "INSERT INTO ties (kind_id, from_actor_id, to_actor_id, grouping_id) "
            "VALUES (?, ?, ?, ?)",
            (kid, from_actor_id, to_actor_id, grouping_id),
        )
        conn.commit()
        tid = cur.lastrowid
        row = conn.execute("SELECT * FROM ties WHERE id = ?", (tid,)).fetchone()
    return dict(row)


def end_tie(tie_id: int, db_path: Path | str | None = None):
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT t.*, k.slug FROM ties t JOIN tie_kinds k ON k.id = t.kind_id "
            "WHERE t.id = ?",
            (tie_id,),
        ).fetchone()
        if not row:
            return
        conn.execute(
            "UPDATE ties SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
            (_now(), tie_id),
        )
        conn.commit()
    info = dict(row)
    if info.get("slug") == "accompanying" and info.get("grouping_id") is not None:
        _clear_stale_accompaniment_facets(
            int(info["from_actor_id"]), int(info["to_actor_id"]),
            int(info["grouping_id"]), db_path,
        )


def _clear_stale_accompaniment_facets(from_id: int, to_id: int, grouping_id: int,
                                      db_path: Path | str | None = None):
    """Drop the service marks once nobody is walking with anyone for that work."""
    ties = live_ties(db_path)
    still_from = any(
        t.get("slug") == "accompanying"
        and int(t["from_actor_id"]) == int(from_id)
        and t.get("grouping_id") is not None
        and int(t["grouping_id"]) == int(grouping_id)
        for t in ties
    )
    still_to = any(
        t.get("slug") == "accompanying"
        and int(t["to_actor_id"]) == int(to_id)
        and t.get("grouping_id") is not None
        and int(t["grouping_id"]) == int(grouping_id)
        for t in ties
    )
    for m in live_memberships(db_path):
        if int(m["grouping_id"]) != int(grouping_id):
            continue
        if int(m["actor_id"]) == int(from_id) and not still_from:
            for f in live_facets(m["id"], db_path):
                if f.get("slug") == "accompanying":
                    end_facet(int(f["id"]), db_path)
        if int(m["actor_id"]) == int(to_id) and not still_to:
            for f in live_facets(m["id"], db_path):
                if f.get("slug") == "being_accompanied":
                    end_facet(int(f["id"]), db_path)


def live_ties(db_path: Path | str | None = None) -> list[dict]:
    with _connect(db_path) as conn:
        return _rows(conn.execute(
            "SELECT t.*, k.slug, k.label, k.directed, k.draw_style, "
            "g.name AS grouping_name "
            "FROM ties t JOIN tie_kinds k ON k.id = t.kind_id "
            "LEFT JOIN groupings g ON g.id = t.grouping_id "
            "WHERE t.ended_at IS NULL ORDER BY t.id"
        ))


def add_accompaniment(from_actor_id: int, to_actor_id: int, grouping_id: int,
                      db_path: Path | str | None = None) -> dict:
    """Someone walks with someone else for a particular work. That is service."""
    if int(from_actor_id) == int(to_actor_id):
        raise ValueError("A person cannot accompany themselves")
    if not get_actor(from_actor_id, db_path) or not get_actor(to_actor_id, db_path):
        raise ValueError("No such friend")
    if not get_grouping(grouping_id, db_path):
        raise ValueError("No such grouping")
    tie = add_tie("accompanying", from_actor_id, to_actor_id, grouping_id, db_path)
    for m in live_memberships(db_path):
        if int(m["actor_id"]) == int(from_actor_id) and int(m["grouping_id"]) == int(grouping_id):
            add_facet(m["id"], "accompanying", db_path)
        if int(m["actor_id"]) == int(to_actor_id) and int(m["grouping_id"]) == int(grouping_id):
            add_facet(m["id"], "being_accompanied", db_path)
    return next(t for t in live_ties(db_path) if int(t["id"]) == int(tie["id"]))


# --- activities ---

def record_activity(kind_slug: str, happened_at: str, participant_ids: list[int],
                    grouping_id: int | None = None, title: str | None = None,
                    host_ids: list[int] | None = None,
                    db_path: Path | str | None = None) -> dict:
    kid = _kind_id("activity_kinds", kind_slug, db_path)
    ids = list(dict.fromkeys(int(i) for i in (participant_ids or [])))
    if not ids:
        raise ValueError("An activity needs someone who was there")
    hosts = set(int(i) for i in (host_ids or []))
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO activities (kind_id, grouping_id, title, happened_at) "
            "VALUES (?, ?, ?, ?)",
            (kid, grouping_id, (title or "").strip() or None, happened_at),
        )
        aid = cur.lastrowid
        for pid in ids:
            role = "hosted" if pid in hosts else "present"
            conn.execute(
                "INSERT INTO activity_participants (activity_id, actor_id, role_slug) "
                "VALUES (?, ?, ?)",
                (aid, pid, role),
            )
        conn.commit()
    return get_activity(aid, db_path)


def get_activity(activity_id: int, db_path: Path | str | None = None) -> dict:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT a.*, k.slug AS kind_slug, k.label AS kind_label "
            "FROM activities a JOIN activity_kinds k ON k.id = a.kind_id "
            "WHERE a.id = ?",
            (activity_id,),
        ).fetchone()
        parts = _rows(conn.execute(
            "SELECT * FROM activity_participants WHERE activity_id = ?",
            (activity_id,),
        ))
    if not row:
        raise ValueError("No such activity")
    out = dict(row)
    out["participants"] = parts
    return out


def sat_together(actor_id: int, db_path: Path | str | None = None) -> dict:
    owner = ensure_owner(db_path)
    if int(actor_id) == int(owner["id"]):
        raise ValueError("Sit with a friend — this light is already you")
    return record_activity(
        "conversation",
        _now(),
        [int(owner["id"]), int(actor_id)],
        db_path=db_path,
    )


def activities_for_actor(actor_id: int, limit: int = 20,
                         db_path: Path | str | None = None) -> list[dict]:
    with _connect(db_path) as conn:
        rows = _rows(conn.execute(
            "SELECT a.*, k.slug AS kind_slug, k.label AS kind_label "
            "FROM activities a "
            "JOIN activity_kinds k ON k.id = a.kind_id "
            "JOIN activity_participants p ON p.activity_id = a.id "
            "WHERE p.actor_id = ? "
            "ORDER BY a.happened_at DESC LIMIT ?",
            (actor_id, limit),
        ))
    return rows


def activity_counts_by_grouping(db_path: Path | str | None = None) -> dict[tuple[int, int], int]:
    """(actor_id, grouping_id) -> how many recorded gatherings they share."""
    with _connect(db_path) as conn:
        rows = _rows(conn.execute(
            "SELECT p.actor_id, a.grouping_id, COUNT(*) AS n "
            "FROM activity_participants p "
            "JOIN activities a ON a.id = p.activity_id "
            "WHERE a.grouping_id IS NOT NULL "
            "GROUP BY p.actor_id, a.grouping_id"
        ))
    return {(int(r["actor_id"]), int(r["grouping_id"])): int(r["n"]) for r in rows}


def days_since_sat(actor_id: int, db_path: Path | str | None = None) -> int | None:
    """Days since the owner and this friend were at the same recorded activity."""
    owner = ensure_owner(db_path)
    oid = int(owner["id"])
    if int(actor_id) == oid:
        return 0
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT a.happened_at FROM activities a "
            "JOIN activity_participants p1 ON p1.activity_id = a.id AND p1.actor_id = ? "
            "JOIN activity_participants p2 ON p2.activity_id = a.id AND p2.actor_id = ? "
            "ORDER BY a.happened_at DESC LIMIT 1",
            (oid, int(actor_id)),
        ).fetchone()
    if not row:
        return None
    try:
        then = datetime.strptime(row["happened_at"][:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            then = datetime.strptime(row["happened_at"][:10], "%Y-%m-%d")
        except ValueError:
            return None
    return max(0, (datetime.now() - then).days)


def quiet_lights(db_path: Path | str | None = None) -> list[dict]:
    """Owner-facing sentences. Never 'overdue'. Rule 61."""
    days_limit = int(get_setting("quiet_after_days", "35", db_path) or "35")
    owner = ensure_owner(db_path)
    out = []
    for actor in list_actors(db_path):
        if int(actor["id"]) == int(owner["id"]):
            continue
        if actor["kind"] != "person":
            continue
        days = days_since_sat(int(actor["id"]), db_path)
        if days is None or days < days_limit:
            continue
        when = "today" if days == 0 else (
            "yesterday" if days == 1 else f"{days} days ago"
        )
        out.append({
            "actor_id": actor["id"],
            "display_name": actor["display_name"],
            "days": days,
            "sentence": f"You last sat with {actor['display_name']} {when}.",
        })
    out.sort(key=lambda r: r["days"], reverse=True)
    return out


# --- snapshot (the map's one payload) ---

# --- The Bahá'í Workforce as a place (rules 65-67) -----------------------------

def workforce_grouping(db_path: Path | str | None = None) -> dict:
    """The one workforce row. Created by init_db, never by create_grouping."""
    with _connect(db_path) as conn:
        _ensure_workforce_row(conn)
        conn.commit()
    for g in list_groupings(db_path):
        if g.get("slug") == WORKFORCE_SLUG:
            return g
    raise RuntimeError("The workforce grouping is missing")


def workforce_members(db_path: Path | str | None = None) -> list[dict]:
    """The people who work alongside the agents. Names, and what they do here.

    Rule 60: nothing here a depicted person could not also see — a chosen
    name and a role sentence. `introduced_as` is the existing membership
    column; no new personal field is invented for this.
    """
    wf = workforce_grouping(db_path)
    out = []
    for m in live_memberships(db_path):
        if int(m["grouping_id"]) != int(wf["id"]):
            continue
        actor = get_actor(int(m["actor_id"]), db_path)
        if not actor or actor.get("archived_at"):
            continue
        out.append({
            "membership_id": int(m["id"]),
            "actor_id": int(actor["id"]),
            "display_name": actor["display_name"],
            "kind": actor["kind"],
            "role": m.get("introduced_as") or "",
            "since": m.get("created_at"),
        })
    return out


def add_workforce_person(display_name: str | None = None,
                         actor_id: int | None = None,
                         role: str | None = None,
                         db_path: Path | str | None = None) -> dict:
    """Put a real person on the workforce — a new one, or a friend already here.

    A person already lit somewhere on the map keeps that light (rule 62);
    joining the workforce adds a membership, never a second dot.
    """
    wf = workforce_grouping(db_path)
    if actor_id is None:
        name = (display_name or "").strip()
        if not name:
            raise ValueError("Give the person a name")
        actor = create_actor("person", name, db_path=db_path)
        actor_id = int(actor["id"])
    else:
        actor = get_actor(int(actor_id), db_path)
        if not actor:
            raise ValueError("No such friend")
        if actor.get("kind") != "person":
            raise ValueError("Only a person can join the workforce")
        actor_id = int(actor["id"])
    mem = add_membership(actor_id, int(wf["id"]), introduced_as=role, db_path=db_path)
    if role and not (mem.get("introduced_as") or "").strip():
        with _connect(db_path) as conn:
            conn.execute(
                "UPDATE memberships SET introduced_as = ? WHERE id = ?",
                (role.strip(), int(mem["id"])),
            )
            conn.commit()
    return {"actor_id": actor_id, "membership_id": int(mem["id"])}


def remove_workforce_person(membership_id: int,
                            db_path: Path | str | None = None) -> dict:
    """Take someone off the workforce. The person stays on the map."""
    wf = workforce_grouping(db_path)
    mem = get_membership(int(membership_id), db_path)
    if not mem or int(mem["grouping_id"]) != int(wf["id"]):
        raise ValueError("That is not a workforce membership")
    end_membership(int(membership_id), db_path)
    return {"result": "ok"}


# --- Channels: the WhatsApp group a nucleus already talks in (rule 66) ---------

def set_grouping_channel(grouping_id: int, label: str | None = None,
                         link: str | None = None, kind: str = "whatsapp_group",
                         db_path: Path | str | None = None) -> dict:
    """Note the WhatsApp GROUP a table already uses. Never a person's number.

    Rule 60 still holds: no phone column exists here and none is added. A
    group invite link is what every member of that group can already see.
    """
    if kind not in CHANNEL_KINDS:
        raise ValueError("Only a WhatsApp group can be noted here")
    g = get_grouping(int(grouping_id), db_path)
    if not g:
        raise ValueError("No such grouping")
    label = (label or "").strip() or None
    link = (link or "").strip() or None
    if not label and not link:
        raise ValueError("Give the group a name or a link")
    if link and not link.lower().startswith(("https://chat.whatsapp.com/", "https://wa.me/")):
        raise ValueError(
            "A WhatsApp group link looks like https://chat.whatsapp.com/…"
        )
    now = _now()
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE grouping_channels SET ended_at = ? "
            "WHERE grouping_id = ? AND kind = ? AND ended_at IS NULL",
            (now, int(grouping_id), kind),
        )
        cur = conn.execute(
            "INSERT INTO grouping_channels (grouping_id, kind, label, link) "
            "VALUES (?, ?, ?, ?)",
            (int(grouping_id), kind, label, link),
        )
        conn.commit()
        cid = cur.lastrowid
    return get_grouping_channel(int(cid), db_path)


def get_grouping_channel(channel_id: int,
                         db_path: Path | str | None = None) -> dict | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM grouping_channels WHERE id = ?", (int(channel_id),)
        ).fetchone()
    return dict(row) if row else None


def list_grouping_channels(grouping_id: int | None = None,
                           db_path: Path | str | None = None) -> list[dict]:
    sql = "SELECT * FROM grouping_channels WHERE ended_at IS NULL"
    args: tuple = ()
    if grouping_id is not None:
        sql += " AND grouping_id = ?"
        args = (int(grouping_id),)
    sql += " ORDER BY id"
    with _connect(db_path) as conn:
        return _rows(conn.execute(sql, args))


def remove_grouping_channel(channel_id: int, db_path: Path | str | None = None) -> dict:
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE grouping_channels SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
            (_now(), int(channel_id)),
        )
        conn.commit()
    return {"result": "ok"}


FORBIDDEN_SNAPSHOT_KEYS = (
    "phone", "email", "address", "notes", "private_note", "score",
    "grade", "receptiveness", "spiritual", "rank",
)


def snapshot(db_path: Path | str | None = None) -> dict:
    """Shareable picture of the Material World. Rule 60."""
    init_db(db_path)
    owner = ensure_owner(db_path)
    kinds = list_kinds(db_path)
    groupings = list_groupings(db_path)
    all_groupings = list_groupings(db_path, include_archived=True)
    actors = list_actors(db_path)
    memberships = live_memberships(db_path)
    facets_by_mem: dict[int, list] = {}
    for m in memberships:
        facets_by_mem[m["id"]] = live_facets(m["id"], db_path)
    ties = live_ties(db_path)
    counts = activity_counts_by_grouping(db_path)
    embers = {}
    for a in actors:
        if a["kind"] == "person" and int(a["id"]) != int(owner["id"]):
            embers[str(a["id"])] = days_since_sat(int(a["id"]), db_path)
    houses = live_household_members(db_path)
    wf = workforce_grouping(db_path)
    from agents.nuclei_layout import layout_real_world
    layout = layout_real_world(
        all_groupings, actors, memberships, facets_by_mem, counts, houses, ties,
    )
    out = {
        "owner_actor_id": owner["id"],
        "kinds": kinds,
        "groupings": groupings,
        "actors": actors,
        "memberships": memberships,
        "facets": facets_by_mem,
        "ties": ties,
        "household_members": [
            {
                "id": h["id"],
                "household_id": h["household_id"],
                "person_id": h["person_id"],
                "person_name": h["person_name"],
                "household_name": h["household_name"],
            }
            for h in houses
        ],
        "activity_counts": [
            {"actor_id": a, "grouping_id": g, "n": n}
            for (a, g), n in counts.items()
        ],
        "embers": embers,
        "layout": layout,
        "quiet_after_days": int(get_setting("quiet_after_days", "35", db_path) or "35"),
        "workforce": dict(layout["workforce"]),
        # The workforce is a grouping like any other, so the people on it are
        # ordinary memberships — but it is never a table on the sky: it keeps
        # its own fixed light and opens like a family (rule 65).
        "workforce_grouping_id": int(wf["id"]),
        "workforce_members": workforce_members(db_path),
        "channels": [
            {
                "id": c["id"], "grouping_id": c["grouping_id"], "kind": c["kind"],
                "label": c["label"], "link": c["link"],
            }
            for c in list_grouping_channels(db_path=db_path)
        ],
    }
    _assert_shareable_obj(out)
    return out


def _assert_shareable_obj(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in FORBIDDEN_SNAPSHOT_KEYS:
                raise RuntimeError(f"shareable snapshot leaked key: {k}")
            _assert_shareable_obj(v)
    elif isinstance(obj, list):
        for item in obj:
            _assert_shareable_obj(item)


def actor_detail(actor_id: int, db_path: Path | str | None = None) -> dict:
    actor = get_actor(actor_id, db_path)
    if not actor:
        raise ValueError("No such friend")
    mems = [m for m in live_memberships(db_path) if int(m["actor_id"]) == int(actor_id)]
    for m in mems:
        m["facets"] = live_facets(m["id"], db_path)
        g = get_grouping(int(m["grouping_id"]), db_path)
        m["grouping_name"] = g["name"] if g else ""
        m["grouping_kind"] = g["kind_slug"] if g else ""
        m["is_nucleus"] = bool(g["is_nucleus"]) if g else False
    days = days_since_sat(int(actor_id), db_path)
    sentence = None
    if actor["kind"] == "person" and days is not None:
        when = "today" if days == 0 else (
            "yesterday" if days == 1 else f"{days} days ago"
        )
        sentence = f"You last sat with {actor['display_name']} {when}."
    family = None
    family_members = []
    if actor["kind"] == "household":
        family_members = members_of_household(int(actor_id), db_path)
    elif actor["kind"] == "person":
        belong = household_of_person(int(actor_id), db_path)
        if belong:
            family = {
                "id": belong["id"],
                "household_id": belong["household_id"],
                "household_name": belong["household_name"],
            }
    return {
        "actor": actor,
        "memberships": mems,
        "ties": [t for t in live_ties(db_path)
                 if int(t["from_actor_id"]) == int(actor_id)
                 or int(t["to_actor_id"]) == int(actor_id)],
        "recent_activities": activities_for_actor(actor_id, 12, db_path),
        "days_since_sat": days,
        "sat_sentence": sentence,
        "family": family,
        "family_members": family_members,
    }


def grouping_detail(grouping_id: int, db_path: Path | str | None = None) -> dict:
    g = get_grouping(grouping_id, db_path)
    if not g:
        raise ValueError("No such grouping")
    mems = [m for m in live_memberships(db_path) if int(m["grouping_id"]) == int(grouping_id)]
    members = []
    for m in mems:
        a = get_actor(int(m["actor_id"]), db_path)
        if not a:
            continue
        extra = {}
        if a.get("kind") == "household":
            extra["family_members"] = members_of_household(int(a["id"]), db_path)
        members.append({
            **m,
            "actor": a,
            "facets": live_facets(m["id"], db_path),
            **extra,
        })
    with _connect(db_path) as conn:
        recent = _rows(conn.execute(
            "SELECT a.*, k.slug AS kind_slug, k.label AS kind_label "
            "FROM activities a JOIN activity_kinds k ON k.id = a.kind_id "
            "WHERE a.grouping_id = ? ORDER BY a.happened_at DESC LIMIT 12",
            (grouping_id,),
        ))
    acc = []
    for t in live_ties(db_path):
        if t.get("slug") != "accompanying" or t.get("grouping_id") is None:
            continue
        if int(t["grouping_id"]) != int(grouping_id):
            continue
        fa = get_actor(int(t["from_actor_id"]), db_path)
        ta = get_actor(int(t["to_actor_id"]), db_path)
        acc.append({
            **t,
            "from_name": fa["display_name"] if fa else "",
            "to_name": ta["display_name"] if ta else "",
        })
    return {
        "grouping": g,
        "members": members,
        "recent_activities": recent,
        "accompaniments": acc,
    }
