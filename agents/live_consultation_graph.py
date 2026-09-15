"""
The consultation concept map — a real, connected graph, derived, never copied.

Adds a genuine graph on top of the existing consultation map (`live_consultation.py`'s
`ConsultationState`): a root (the question), theme branches, and every fact,
assumption, principle, concern, idea, agreement, tension, question, synthesis,
decision and action as its own node, joined by typed edges — containment under a
theme, and real cross-links (supports, challenges, depends on, addresses, leads to,
related to) that the reasoner may propose.

Same discipline as the finished-video shelf (rule 58) and a gathering's
commitments (rule 117): **the graph is DERIVED, on every read, from the existing
authoritative rows** — `session_state`, `decisions`, `action_items` and this
module's own `graph_edges` table. There is no second copy of a decision's text or
an action's owner sitting in a node: `record_ref` points back at the canonical
row, and a node's `label` is a short DISPLAY truncation computed fresh every time,
never stored — so a corrected fact, an accepted commitment or a confirmed decision
is reflected the instant it is reflected anywhere else, and an export can never
print stale wording. `build_graph` is a pure function: no network call, no
database write, safe to run on every GET. Nothing here calls a model — relationship
EXTRACTION happens in `live_consultation_reasoner.py`'s existing analysis pass
(rule 79); this module only validates, assembles and renders what that pass (or a
person) proposed.

Layout is likewise derived, but with one deliberate exception: a node's position
is computed ONCE (the first time it appears with no stored view row) and then
persisted so the map does not reshuffle itself on every poll -- "position new
material locally," never recenter the whole diagram. The endpoint layer owns that
persistence (`live_consultation_api.py`); this module only computes positions
and never writes to the store, so it can be exercised in the test suite exactly
like every other pure function here.
"""

from __future__ import annotations

from typing import Optional
from xml.sax.saxutils import escape as _xml_escape

from agents.live_consultation import ITEM_LISTS

GRAPH_SCHEMA_VERSION = 1

ROOT_ID = "root"

# ── Vocabulary (data, not a chain of if-statements — same reasoning as rule 63) ─

# The list a map item lives in, to the graph node "kind" it becomes. Themes are
# the topic branches (section 3: "Venue, Accessibility, Programme" rather than a
# fixed category list); everything else is a leaf that may CONTAIN under a theme
# and may carry real cross-links to any other leaf.
LIST_TO_KIND: dict[str, str] = {
    "themes": "theme",
    "facts": "fact",
    "assumptions": "assumption",
    "principles": "principle",
    "needs_and_concerns": "concern",
    "ideas": "idea",
    "agreements": "agreement",
    "tensions": "tension",
    "unresolved_questions": "question",
    "questions_to_investigate": "investigate",
    "possible_syntheses": "synthesis",
    "decision_candidates": "decision",
    "action_items": "action",
}
KIND_TO_LIST: dict[str, str] = {v: k for k, v in LIST_TO_KIND.items()}

# Colour and label metadata, SERVED to the dashboard (capabilities) rather than
# duplicated in TypeScript — the same reasoning as the floor policy (rule 87):
# two copies of a legend disagree eventually. Colours are restrained (section 5)
# and chosen to read in both a light and a dark theme.
NODE_KIND_META: dict[str, dict] = {
    "root":        {"label": "Question",       "plural": "Question",        "color": "#94a3b8"},
    "theme":       {"label": "Theme",           "plural": "Themes",          "color": "#38bdf8"},
    "fact":        {"label": "Fact",            "plural": "Facts",           "color": "#64748b"},
    "assumption":  {"label": "Assumption",      "plural": "Assumptions",     "color": "#a78bfa"},
    "principle":   {"label": "Principle",       "plural": "Principles",      "color": "#f0abfc"},
    "concern":     {"label": "Concern",         "plural": "Concerns",        "color": "#fb923c"},
    "idea":        {"label": "Idea",            "plural": "Ideas",           "color": "#34d399"},
    "agreement":   {"label": "Agreement",       "plural": "Agreements",      "color": "#4ade80"},
    "tension":     {"label": "Tension",         "plural": "Tensions",        "color": "#f87171"},
    "question":    {"label": "Open question",   "plural": "Open questions",  "color": "#fbbf24"},
    "investigate": {"label": "To investigate",  "plural": "To investigate",  "color": "#fcd34d"},
    "synthesis":   {"label": "Possible synthesis", "plural": "Possible syntheses", "color": "#22d3ee"},
    "decision":    {"label": "Decision",        "plural": "Decisions",       "color": "#facc15"},
    "action":      {"label": "Action",          "plural": "Actions",         "color": "#60a5fa"},
    "bucket":      {"label": "Category",        "plural": "Categories",      "color": "#71717a"},
}

# The hierarchy relation is singular and load-bearing: it is the ONLY relation
# that may form the tree, and only a theme (or the synthetic root) may be its
# source. That single constraint is what keeps the hierarchy acyclic without any
# general cycle check — root -> theme -> item is two layers, and an item can
# never itself be a "contains" source, so a cycle cannot be constructed.
HIERARCHY_RELATION = "contains"
CROSS_RELATIONS = ("supports", "challenges", "depends_on", "addresses", "leads_to", "related_to")
EDGE_RELATIONS = (HIERARCHY_RELATION, *CROSS_RELATIONS)

RELATION_META: dict[str, dict] = {
    "contains":    {"label": "Contains",     "style": "solid",  "hierarchy": True},
    "supports":    {"label": "Supports",     "style": "solid",  "hierarchy": False},
    "challenges":  {"label": "Challenges",   "style": "dashed", "hierarchy": False},
    "depends_on":  {"label": "Depends on",   "style": "dashed", "hierarchy": False},
    "addresses":   {"label": "Addresses",    "style": "solid",  "hierarchy": False},
    "leads_to":    {"label": "Leads to",     "style": "solid",  "hierarchy": False},
    "related_to":  {"label": "Related to",   "style": "dotted", "hierarchy": False},
}

# Same convention as `agents/layout.py`'s SERIF_STACK: explicit Windows font
# paths, tried in order, degrading to PIL's built-in default rather than
# rendering tofu (this repo is Windows-only, per AGENTS.md's own gotchas).
_PNG_FONT_STACK = ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf",
                   "C:/Windows/Fonts/tahoma.ttf")
_PNG_BOLD_FONT_STACK = ("C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf",
                        "C:/Windows/Fonts/tahomabd.ttf")

MAX_LABEL_CHARS = 44
MAX_EDGE_LABEL_CHARS = 80
# Bounds on a single proposed patch and on total stored edges, the same
# reasoning as `reasoner.LIST_PROMPT_CAP`: an unbounded graph is an unbounded
# prompt AND an unbounded page to render.
MAX_EDGES_PER_PATCH = 40
MAX_STORED_EDGES = 600


def _short_label(text: str, limit: int = MAX_LABEL_CHARS) -> str:
    """A DISPLAY truncation only — never stored, computed fresh every time, so
    the full wording (in `detail`) is always the exactly-approved text (section
    3: "a shorter node label is only a display label")."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",.;: ")
    return (cut or text[:limit]).rstrip() + "…"


# ── Building a node from an existing map item ───────────────────────────────

def _fact_node(item: dict) -> tuple[str, dict]:
    from agents.live_consultation import FACT_STATE_LABELS, DEFAULT_FACT_STATE
    status = item.get("status") or DEFAULT_FACT_STATE
    return status, {"evidence_note": item.get("evidence_note") or ""}


def _lifecycle_node(item: dict) -> tuple[str, dict]:
    from agents.live_consultation import DEFAULT_LIFECYCLE
    status = item.get("lifecycle") or DEFAULT_LIFECYCLE
    return status, {"resolution_note": item.get("resolution_note") or ""}


def _decision_node(row: dict) -> tuple[str, dict]:
    return row.get("status") or "candidate", {
        "rationale": row.get("rationale") or "",
        "support": row.get("support") or "",
        "concerns": row.get("concerns") or [],
        "retained_concerns": row.get("retained_concerns") or [],
        "confirmed_at": row.get("confirmed_at"),
    }


def _action_node(row: dict) -> tuple[str, dict]:
    from agents.live_consultation import DEFAULT_ACTION_STATUS
    return row.get("status") or DEFAULT_ACTION_STATUS, {
        "owner": row.get("owner"), "due": row.get("due"),
        "owner_accepted": row.get("owner_accepted"),
        "accepted_by": row.get("accepted_by") or "",
        "blocker": row.get("blocker") or "",
        "support_needed": row.get("support_needed") or "",
        "success_criteria": row.get("success_criteria") or "",
    }


def _canonical_row_node(row: dict, list_name: str) -> Optional[dict]:
    """
    A node built DIRECTLY from a canonical `decisions`/`action_items` row that
    has no matching working-map item — a human-created action (`create_action_item`
    never gets a `map_id`), or a row whose map item was stripped by transcript
    deletion (rule 103: confirmed decisions and accepted commitments survive
    deletion; their map items may not). Never a copy that can drift: this reads
    the SAME row `_decision_node`/`_action_node` would overlay onto a map item,
    so a canonical-only row is exactly as authoritative as a matched one.

    The node id is the row's own `map_id` when it has one — so an edge stored
    against that id (from before the map item vanished) keeps resolving — and
    the row's own id otherwise, since a purely human-created row never had a
    map id to lose.
    """
    kind = "decision" if list_name == "decision_candidates" else "action"
    text = (row.get("text") if list_name == "decision_candidates" else row.get("action")) or ""
    text = text.strip()
    if not text:
        return None
    if list_name == "decision_candidates":
        status, extra = _decision_node(row)
    else:
        status, extra = _action_node(row)
    node_id = row.get("map_id") or row["id"]
    return {
        "id": node_id, "kind": kind, "label": _short_label(text), "detail": text,
        "status": status, "status_label": _status_label(kind, status),
        "human_edited": bool(row.get("human_edited")),
        "source_turn_ids": [],
        "record_ref": {"list": list_name, "id": row["id"]},
        "extra": extra,
        "origin": "human" if row.get("human_edited") else "model",
        "has_map_item": False,
    }


def _orphan_canonical_rows(state: dict, decisions: list[dict],
                          actions: list[dict]) -> tuple[list[dict], list[dict]]:
    """Canonical rows with no matching working-map item, by list.

    A decision/action row is "claimed" by whichever map item shares its
    `map_id` — normally every row created via the analysis pass (rule 95). A
    row is an ORPHAN when its `map_id` is empty (a human-created action, which
    never gets one) or points at an item that is no longer in the map (most
    commonly: the transcript was deleted and only reviewed/edited items
    survived, rule 103 — a confirmed decision or an accepted action must not
    vanish from the map just because its working note did).
    """
    claimed: set[str] = set()
    for item in (state.get("decision_candidates") or []):
        if isinstance(item, dict) and item.get("id"):
            claimed.add(item["id"])
    for item in (state.get("action_items") or []):
        if isinstance(item, dict) and item.get("id"):
            claimed.add(item["id"])
    orphan_decisions = [d for d in decisions if not (d.get("map_id") and d["map_id"] in claimed)]
    orphan_actions = [a for a in actions if not (a.get("map_id") and a["map_id"] in claimed)]
    return orphan_decisions, orphan_actions


def _status_label(kind: str, status: Optional[str]) -> Optional[str]:
    if not status:
        return None
    from agents.live_consultation import (
        FACT_STATE_LABELS, LIFECYCLE_LABELS, ACTION_STATUS_LABELS,
    )
    if kind == "fact":
        return FACT_STATE_LABELS.get(status, status)
    if kind == "action":
        return ACTION_STATUS_LABELS.get(status, status)
    if kind == "decision":
        return {"candidate": "Candidate", "confirmed": "Confirmed",
                "rejected": "Not decided"}.get(status, status)
    return LIFECYCLE_LABELS.get(status, status)


def _item_node(list_name: str, item: dict, decisions_by_map_id: dict,
               actions_by_map_id: dict) -> Optional[dict]:
    """
    Build a node from a working-map item — except for a decision or action,
    where the CANONICAL row (`decisions`/`action_items`, keyed by `map_id`) is
    authoritative and the map item is only how the node was first noticed.

    Two things follow from that, both load-bearing (section 1):
      * the node's wording is the row's `text`/`action`, not the map item's —
        `edit_action`/`edit_decision` write the row, and until this the graph
        went on showing what was first heard, however many times it was
        corrected.
      * a decision/action map item with NO matching row is treated as
        deleted, not as a still-proposed node with nothing overlaid: the only
        way a map item loses its row is a human deleting the commitment out
        from under it (`remove_action`), and a stale row-less node reappearing
        because the map item itself was left behind is exactly the phantom
        this refusal exists to prevent.
    """
    kind = LIST_TO_KIND[list_name]
    row = None
    if list_name == "decision_candidates":
        row = decisions_by_map_id.get(item.get("id"))
    elif list_name == "action_items":
        row = actions_by_map_id.get(item.get("id"))
    if list_name in ("decision_candidates", "action_items") and not row:
        return None
    item_text = item.get("action") if list_name == "action_items" else item.get("text")
    if row:
        row_text = row.get("text") if list_name == "decision_candidates" else row.get("action")
        # The row is authoritative, but an empty row field (never happens with
        # a real database row — both columns are `NOT NULL DEFAULT ''` written
        # at creation — falls back to the map item rather than treating a
        # blank as evidence the row itself is gone) is not the same signal as
        # no row at all.
        text = (row_text or "").strip() or (item_text or "").strip()
    else:
        text = item_text
    text = (text or "").strip()
    if not text or not item.get("id"):
        return None
    extra: dict = {}
    status: Optional[str] = None
    if list_name == "facts":
        status, extra = _fact_node(item)
    elif list_name == "decision_candidates":
        status, extra = _decision_node(row)
    elif list_name == "action_items":
        status, extra = _action_node(row)
    elif list_name != "themes":
        status, extra = _lifecycle_node(item)
    human_edited = bool(item.get("human_edited")) or bool(row and row.get("human_edited"))
    return {
        "id": item["id"],
        "kind": kind,
        "label": _short_label(text),
        "detail": text,
        "status": status,
        "status_label": _status_label(kind, status),
        "human_edited": human_edited,
        "source_turn_ids": [str(t) for t in (item.get("source_turn_ids") or [])],
        "record_ref": {"list": list_name, "id": (row["id"] if row else item["id"])},
        "extra": extra,
        "origin": "human" if human_edited else "model",
        # Whether this node has a working-map item behind it, as opposed to a
        # canonical-only row (`_canonical_row_node`) — merge and delete-map-item
        # both operate on map items, so the UI needs to know which is which.
        "has_map_item": True,
    }


def _root_node(session: dict, state: dict) -> dict:
    question = (session.get("question") or state.get("question") or "").strip()
    objective = (state.get("objective") or "").strip()
    label = _short_label(question) if question else "This consultation"
    detail = question or "No question has been recorded for this consultation yet."
    if objective:
        detail += "\n\nObjective: " + objective
    return {
        "id": ROOT_ID, "kind": "root", "label": label, "detail": detail,
        "status": None, "status_label": None, "human_edited": False,
        "source_turn_ids": [], "record_ref": None, "extra": {}, "origin": "root",
        "has_map_item": False,
    }


def _bucket_node(kind: str) -> dict:
    meta = NODE_KIND_META.get(kind, {})
    return {
        "id": f"bucket:{kind}", "kind": "bucket",
        "label": meta.get("plural", kind.title()), "detail": meta.get("plural", kind.title()),
        "status": None, "status_label": None, "human_edited": False,
        "source_turn_ids": [], "record_ref": None, "extra": {},
        "origin": "fallback_grouping", "has_map_item": False,
    }


def _edge(from_id: str, to_id: str, relation: str, label: str = "", inferred: bool = True,
          human_edited: bool = False, source_turn_ids: Optional[list] = None,
          synthetic: bool = False, edge_id: Optional[str] = None) -> dict:
    meta = RELATION_META.get(relation, {})
    return {
        "id": edge_id or f"{'~' if synthetic else ''}{from_id}::{relation}::{to_id}",
        "from_id": from_id, "to_id": to_id, "relation": relation,
        "label": (label or "")[:MAX_EDGE_LABEL_CHARS],
        "kind": "hierarchy" if meta.get("hierarchy") else "cross",
        "inferred": bool(inferred), "human_edited": bool(human_edited),
        "source_turn_ids": [str(t) for t in (source_turn_ids or [])],
        "synthetic": synthetic,
    }


# ── Assembly ─────────────────────────────────────────────────────────────────

def build_graph(session: dict, state: dict, decisions: list[dict], actions: list[dict],
                edges_rows: list[dict], views: dict[str, dict]) -> dict:
    """
    Assemble the concept graph from the CANONICAL rows. Pure: makes no database
    write and no network call, so it is safe to run on every read (live sessions
    and finished ones alike) — "archived maps load from saved data without
    regenerating through AI" is true here because there is never any AI call to
    regenerate from.

    `views` is `{node_id: {x, y, pinned, collapsed}}` from `graph_node_view` —
    positions a person dragged, or a node's own previously-computed default.
    Anything with NO row here gets a freshly computed position, returned in
    `new_positions` for the caller to persist (this function never writes).
    """
    decisions_by_map_id = {d["map_id"]: d for d in decisions if d.get("map_id")}
    actions_by_map_id = {a["map_id"]: a for a in actions if a.get("map_id")}

    nodes: dict[str, dict] = {ROOT_ID: _root_node(session, state)}
    for list_name in ITEM_LISTS:
        for item in (state.get(list_name) or []):
            if not isinstance(item, dict):
                continue
            node = _item_node(list_name, item, decisions_by_map_id, actions_by_map_id)
            if node:
                nodes[node["id"]] = node

    # Canonical decisions/actions with no working-map item behind them any
    # more — a human-created action (never had one) or a confirmed decision /
    # accepted action whose map item was stripped by transcript deletion
    # (rule 103). Never omitted: the approved record survives even when the
    # note that first raised it does not.
    orphan_decisions, orphan_actions = _orphan_canonical_rows(state, decisions, actions)
    for row in orphan_decisions:
        node = _canonical_row_node(row, "decision_candidates")
        if node:
            nodes.setdefault(node["id"], node)
    for row in orphan_actions:
        node = _canonical_row_node(row, "action_items")
        if node:
            nodes.setdefault(node["id"], node)

    themes_present = any(n["kind"] == "theme" for n in nodes.values())
    fallback = not themes_present and not edges_rows

    edges: list[dict] = []
    seen_keys: set[tuple] = set()

    def add_edge(e: dict) -> None:
        key = (e["from_id"], e["to_id"], e["relation"])
        if key in seen_keys:
            return
        seen_keys.add(key)
        edges.append(e)

    # Stored edges (human-added or reasoner-proposed), filtered to endpoints that
    # still exist — a node removed by a human merge or delete simply drops its
    # edges from view rather than leaving a dangling reference (section 3:
    # "removing a visual duplicate must not erase its sources", satisfied because
    # the UNDERLYING item and its provenance are untouched; only this derived
    # edge stops being drawn).
    for row in edges_rows:
        if row["from_id"] not in nodes or row["to_id"] not in nodes:
            continue
        add_edge(_edge(row["from_id"], row["to_id"], row["relation"], row.get("label", ""),
                      inferred=not row.get("human_edited") and bool(row.get("inferred", True)),
                      human_edited=bool(row.get("human_edited")),
                      source_turn_ids=row.get("source_turn_ids") or [],
                      edge_id=row["id"]))

    contained = {e["to_id"] for e in edges if e["relation"] == HIERARCHY_RELATION}

    if fallback:
        # No themes and no extracted relationships at all — an old session, or
        # one still in its first few turns. Group by CATEGORY so the map is
        # still a readable tree rather than a flat dump, and mark every one of
        # these edges `synthetic` so the screen can say plainly that this is
        # fallback grouping, not something the group discussed (section 3).
        for list_name, kind in LIST_TO_KIND.items():
            if list_name == "themes":
                continue
            members = [n for n in nodes.values() if n["kind"] == kind]
            if not members:
                continue
            bucket = _bucket_node(kind)
            nodes[bucket["id"]] = bucket
            add_edge(_edge(ROOT_ID, bucket["id"], HIERARCHY_RELATION, synthetic=True, inferred=False))
            for member in members:
                add_edge(_edge(bucket["id"], member["id"], HIERARCHY_RELATION,
                              synthetic=True, inferred=False))
    else:
        # Every theme is a top-level branch of the root, always — a theme does
        # not need to be told it belongs to the meeting.
        for node_id, node in nodes.items():
            if node["kind"] == "theme":
                add_edge(_edge(ROOT_ID, node_id, HIERARCHY_RELATION, synthetic=True, inferred=False))
        # Anything real with no theme parenting it (the model did not place it,
        # or it predates this feature) hangs directly off the root rather than
        # being dropped — an orphan is still shown, just not under a topic.
        for node_id, node in nodes.items():
            if node_id == ROOT_ID or node["kind"] in ("theme", "bucket"):
                continue
            if node_id not in contained:
                add_edge(_edge(ROOT_ID, node_id, HIERARCHY_RELATION, synthetic=True, inferred=False))

    positions, new_positions = _layout(nodes, edges, views)
    for node_id, node in nodes.items():
        pos = positions.get(node_id, (0.0, 0.0))
        view = views.get(node_id) or {}
        node["x"], node["y"] = pos
        node["pinned"] = bool(view.get("pinned"))
        node["collapsed"] = bool(view.get("collapsed"))

    return {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "session_id": session.get("id"),
        "content_revision": int(state.get("state_revision") or 0),
        "graph_revision": int(session.get("graph_revision") or 0),
        "view_revision": int(session.get("graph_view_revision") or 0),
        # A decision/action node's wording and status, and the presence of an
        # edge a human just drew or rejected, can all change WITHOUT bumping
        # `content_revision` or `graph_revision` (confirming a decision, an
        # owner accepting, an edited action all bump only this one — rule 102).
        # The client's resync key has to include it or a correction can sit on
        # screen unrefreshed until something else happens to move (section 1).
        "record_revision": int(session.get("record_revision") or 0),
        "fallback": fallback,
        "nodes": list(nodes.values()),
        "edges": edges,
        "new_positions": new_positions,
    }


# ── Layout (deterministic, stable) ──────────────────────────────────────────

_NODE_DX = 220.0
_NODE_DY = 150.0


def _layout(nodes: dict[str, dict], edges: list[dict],
           views: dict[str, dict]) -> tuple[dict[str, tuple], dict[str, tuple]]:
    """
    A position for every node. One already stored (dragged, or computed on an
    earlier read) is kept EXACTLY — this is what keeps the map from reshuffling
    itself on every poll (section 5: "do not recenter or reshuffle the whole
    diagram on every update"). Only a node with no stored row gets a fresh
    position, returned separately so the caller can persist it once.
    """
    children: dict[str, list[str]] = {}
    for e in edges:
        if e["relation"] == HIERARCHY_RELATION:
            children.setdefault(e["from_id"], []).append(e["to_id"])

    depth: dict[str, int] = {ROOT_ID: 0}
    order: list[str] = [ROOT_ID]
    frontier = [ROOT_ID]
    seen = {ROOT_ID}
    while frontier:
        nxt = []
        for parent in frontier:
            for child in children.get(parent, []):
                if child in seen:
                    continue
                seen.add(child)
                depth[child] = depth[parent] + 1
                order.append(child)
                nxt.append(child)
        frontier = nxt
    # Anything unreachable through the hierarchy (should not happen — every real
    # node gets a synthetic root edge above — but a defensive fallback keeps a
    # stray node from vanishing off the page rather than crashing the render).
    for node_id in nodes:
        if node_id not in depth:
            depth[node_id] = 1
            order.append(node_id)

    by_depth: dict[int, list[str]] = {}
    for node_id in order:
        by_depth.setdefault(depth[node_id], []).append(node_id)

    def _free_slot(taken: list[float]) -> float:
        """The nearest-to-centre x that does not sit within one node-width of
        anything already placed at this level.

        This is COLLISION-AWARE, unlike the index-based scheme it replaced: the
        old version divided the row's width evenly by position among ALL nodes
        at the level (existing and new together) and gave a brand-new node
        whatever slot its index landed on — with no regard for where an
        existing, already-positioned node (dragged, or placed on an earlier
        read) actually sat. A first leaf kept at x=0 and a second one added
        later landed at x=110 by that arithmetic: at this component's 200px
        node width, a 90px overlap. Searching outward from centre for the
        nearest slot that clears every already-taken position (by distance,
        not by exact match — a human-dragged position is rarely a clean
        multiple of the grid) fixes that while staying deterministic: the same
        inputs always search the same candidates in the same order.
        """
        def clear(x: float) -> bool:
            return all(abs(x - t) >= _NODE_DX for t in taken)
        if clear(0.0):
            return 0.0
        n = 1
        while n < 10000:          # a safety bound, never reached in practice
            for candidate in (n * _NODE_DX, -n * _NODE_DX):
                if clear(candidate):
                    return candidate
            n += 1
        return 0.0

    positions: dict[str, tuple] = {}
    new_positions: dict[str, tuple] = {}
    for level, ids in sorted(by_depth.items()):
        occupied_x: list[float] = []
        pending: list[str] = []
        for node_id in ids:
            view = views.get(node_id)
            if view and view.get("x") is not None and view.get("y") is not None:
                x = float(view["x"])
                positions[node_id] = (x, float(view["y"]))
                occupied_x.append(x)
            else:
                pending.append(node_id)
        y = level * _NODE_DY
        for node_id in pending:
            x = _free_slot(occupied_x)
            occupied_x.append(x)
            positions[node_id] = (x, y)
            new_positions[node_id] = (x, y)
    return positions, new_positions


# ── Patch validation (section 3: "validate before applying, atomically") ────

def node_universe(state: dict, decisions: Optional[list[dict]] = None,
                  actions: Optional[list[dict]] = None) -> tuple[set[str], dict[str, str]]:
    """Every id an edge may legally reference right now, and each one's kind —
    what `validate_edges` checks proposed connections against. Cheap: it never
    builds a full node (no label truncation, no decision/action overlay), only
    what validation needs.

    `decisions`/`actions` are optional and add the canonical-only rows a human
    might want to connect (`_orphan_canonical_rows`) — a model never needs them,
    because it only ever proposes edges against ids it was just shown, which are
    always working-map ids."""
    ids = {ROOT_ID}
    kinds = {ROOT_ID: "root"}
    for list_name, kind in LIST_TO_KIND.items():
        for item in (state.get(list_name) or []):
            if isinstance(item, dict) and item.get("id"):
                ids.add(item["id"])
                kinds[item["id"]] = kind
    if decisions is not None or actions is not None:
        orphan_decisions, orphan_actions = _orphan_canonical_rows(
            state, decisions or [], actions or [])
        for row in orphan_decisions:
            node_id = row.get("map_id") or row["id"]
            ids.add(node_id)
            kinds[node_id] = "decision"
        for row in orphan_actions:
            node_id = row.get("map_id") or row["id"]
            ids.add(node_id)
            kinds[node_id] = "action"
    return ids, kinds


def resolve_edge_ref(ref: str, tmp_map: dict[str, str]) -> str:
    ref = str(ref or "").strip()
    return tmp_map.get(ref, ref)


def contains_target_ok(to_id: str, node_kind: dict[str, str]) -> bool:
    """Whether `to_id` may legally be CONTAINED by a theme.

    Only a theme (or the synthetic root) may be the "from" of a `contains`
    edge (checked by the caller); this is the other half — the "to" may never
    itself be a theme or the root. Without this half, "theme A contains theme
    B" and "theme A contains root" both passed every existing check (root and
    a theme both exist as real node ids, so the existence check does not catch
    them), which is a REAL cycle: root -> A -> root. The hierarchy is a strict
    two layers, root -> theme -> leaf, and this is what keeps it that way —
    not a general cycle walk, because a leaf can never be a `contains` "from"
    at all, so nothing deeper than two layers can ever be proposed in the
    first place.
    """
    return to_id != ROOT_ID and node_kind.get(to_id) not in ("theme", "bucket")


def validate_edges(raw_edges: list, tmp_map: dict[str, str], node_ids: set[str],
                    node_kind: dict[str, str], rejected: set[tuple],
                    valid_turn_ids: Optional[set[str]] = None) -> tuple[list[dict], list[str]]:
    """
    Turn a model's raw `edges` proposals into validated, ready-to-store dicts.

    Every check that fails DROPS that one edge rather than the whole patch — the
    same "one bad element loses the pass, not the meeting" discipline as
    `reasoner.merge` (rule 79). Endpoints are resolved through `tmp_map` first,
    so an edge that names a node the SAME patch just created still resolves.

    `valid_turn_ids`, when given, is checked the same way `_validate_provenance`
    checks a map item's `source_turn_ids`: a citation naming a turn that is not
    real in THIS session is worse than none, because it reads as corroboration
    that never happened. `None` skips the check (the pure, DB-less callers — the
    test suite chief among them — do not always have a turn table to check
    against).
    """
    accepted: list[dict] = []
    notes: list[str] = []
    if not isinstance(raw_edges, list):
        return accepted, notes
    dropped_unknown = dropped_bad_contains = dropped_rejected = dropped_self = 0
    dropped_provenance = 0
    for raw in raw_edges[:MAX_EDGES_PER_PATCH]:
        if not isinstance(raw, dict):
            continue
        relation = str(raw.get("relation") or "").strip().lower()
        if relation not in EDGE_RELATIONS:
            continue
        from_id = resolve_edge_ref(raw.get("from") or raw.get("from_id") or "", tmp_map)
        to_id = resolve_edge_ref(raw.get("to") or raw.get("to_id") or "", tmp_map)
        if not from_id or not to_id:
            continue
        if from_id == to_id:
            dropped_self += 1
            continue
        if from_id not in node_ids or to_id not in node_ids:
            dropped_unknown += 1
            continue
        # Acyclic by construction (see module docstring): `contains` may only
        # originate from a theme, and may never TARGET a theme or the root —
        # both halves are needed, or "theme A contains theme B" (and B contains
        # A right back) passes every other check here.
        if relation == HIERARCHY_RELATION and (
                node_kind.get(from_id) != "theme" or not contains_target_ok(to_id, node_kind)):
            dropped_bad_contains += 1
            continue
        if (from_id, to_id, relation) in rejected:
            dropped_rejected += 1
            continue
        stated = bool(raw.get("stated"))
        source_turn_ids = [str(t) for t in (raw.get("source_turn_ids") or [])][:8]
        if valid_turn_ids is not None:
            kept = [t for t in source_turn_ids if t in valid_turn_ids]
            if len(kept) != len(source_turn_ids):
                dropped_provenance += len(source_turn_ids) - len(kept)
            source_turn_ids = kept
        accepted.append({
            "from_id": from_id, "to_id": to_id, "relation": relation,
            "label": str(raw.get("label") or "")[:MAX_EDGE_LABEL_CHARS],
            "inferred": not stated,
            "source_turn_ids": source_turn_ids,
        })
    if dropped_unknown:
        notes.append(f"{dropped_unknown} proposed connection(s) named a node that does not exist")
    if dropped_bad_contains:
        notes.append(f"{dropped_bad_contains} proposed connection(s) tried to contain "
                     "something other than a plain item under a theme, and were dropped")
    if dropped_rejected:
        notes.append(f"{dropped_rejected} proposed connection(s) had already been rejected by hand")
    if dropped_self:
        notes.append(f"{dropped_self} proposed connection(s) pointed a node at itself")
    if dropped_provenance:
        notes.append(f"{dropped_provenance} source reference(s) on a connection dropped as unrecognised")
    return accepted, notes


# ── Export rendering (section 6: derived from the stored graph, never a second
#    AI interpretation; text is escaped; no transcript content ever appears) ──

def _node_box_lines(node: dict, width_chars: int = 26) -> list[str]:
    import textwrap
    return textwrap.wrap(node["label"], width_chars) or [node["label"]]


def render_svg(graph: dict, title: str = "") -> str:
    """A standalone SVG of the map — readable text, a legend, meaningful bounds.
    Nothing here reads the transcript; only node labels/details and edge labels,
    which are exactly the wording a person can already see on the map."""
    nodes = graph["nodes"]
    edges = graph["edges"]
    if not nodes:
        nodes = [{"id": ROOT_ID, "x": 0, "y": 0, "kind": "root", "label": "Empty consultation",
                 "status_label": None}]
    xs = [n["x"] for n in nodes]
    ys = [n["y"] for n in nodes]
    box_w, box_h = 180, 64
    pad = 80
    min_x, max_x = min(xs) - box_w / 2, max(xs) + box_w / 2
    min_y, max_y = min(ys) - box_h / 2, max(ys) + box_h / 2
    width = (max_x - min_x) + pad * 2
    height = (max_y - min_y) + pad * 2 + 160  # room for title + legend
    off_x = -min_x + pad
    off_y = -min_y + pad + 80

    def X(x: float) -> float: return x + off_x
    def Y(y: float) -> float: return y + off_y

    by_id = {n["id"]: n for n in nodes}
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}" '
        f'width="{width:.0f}" height="{height:.0f}" font-family="Arial, Helvetica, sans-serif">',
        # A marker for the cross-links only — a hierarchy edge's direction is
        # already visible from the tree shape, but "supports"/"depends_on" and
        # the rest have no shape to read direction from otherwise (section 6:
        # "visible direction on directional relationships").
        '<defs><marker id="rel-arrow" viewBox="0 0 10 10" refX="8" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M0,0 L10,5 L0,10 z" fill="#eab308"/></marker></defs>',
        '<rect width="100%" height="100%" fill="#0f172a"/>',
    ]
    if title:
        parts.append(f'<text x="{width/2:.0f}" y="36" font-size="20" fill="#f1f5f9" '
                     f'text-anchor="middle">{_xml_escape(title)}</text>')

    for e in edges:
        a, b = by_id.get(e["from_id"]), by_id.get(e["to_id"])
        if not a or not b:
            continue
        meta = RELATION_META.get(e["relation"], {})
        dash = {'dashed': '6,4', 'dotted': '2,3'}.get(meta.get("style", "solid"), "")
        cross = e["kind"] != "hierarchy"
        colour = "#475569" if not cross else "#eab308"
        x1, y1, x2, y2 = X(a["x"]), Y(a["y"]), X(b["x"]), Y(b["y"])
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        marker_attr = ' marker-end="url(#rel-arrow)"' if cross else ""
        parts.append(f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" '
                     f'stroke="{colour}" stroke-width="1.5"{dash_attr}{marker_attr} opacity="0.8"/>')
        # A relation always reads SOME label — the custom one if a human wrote
        # one, otherwise the relation's own name — never nothing at all. This
        # used to omit the label entirely whenever no custom text had been set,
        # which is most edges: a model rarely bothers to caption a connection
        # whose relation already says what it is.
        if cross:
            label_text = e.get("label") or meta.get("label", "")
            if label_text:
                mx, my = (x1 + x2) / 2, (y1 + y2) / 2
                parts.append(f'<text x="{mx:.0f}" y="{my:.0f}" font-size="10" fill="#cbd5e1" '
                             f'text-anchor="middle">{_xml_escape(label_text)}</text>')

    for n in nodes:
        colour = NODE_KIND_META.get(n["kind"], {}).get("color", "#64748b")
        cx, cy = X(n["x"]), Y(n["y"])
        rx, ry = box_w / 2, box_h / 2
        parts.append(f'<rect x="{cx-rx:.0f}" y="{cy-ry:.0f}" width="{box_w}" height="{box_h}" '
                     f'rx="8" fill="#1e293b" stroke="{colour}" stroke-width="2"/>')
        lines = _node_box_lines(n)
        start_y = cy - (len(lines) - 1) * 7
        for i, line in enumerate(lines[:3]):
            parts.append(f'<text x="{cx:.0f}" y="{start_y + i*15:.0f}" font-size="12" '
                         f'fill="#f1f5f9" text-anchor="middle">{_xml_escape(line)}</text>')
        kind_label = NODE_KIND_META.get(n["kind"], {}).get("label", n["kind"])
        parts.append(f'<text x="{cx:.0f}" y="{cy+ry-6:.0f}" font-size="9" fill="{colour}" '
                     f'text-anchor="middle">{_xml_escape(kind_label.upper())}</text>')

    # Legend
    ly = height - 130
    parts.append(f'<text x="24" y="{ly:.0f}" font-size="12" fill="#94a3b8">Legend</text>')
    used_kinds = sorted({n["kind"] for n in nodes}, key=lambda k: list(NODE_KIND_META).index(k)
                        if k in NODE_KIND_META else 99)
    for i, kind in enumerate(used_kinds):
        meta = NODE_KIND_META.get(kind, {})
        lx = 24 + (i % 6) * 150
        row_y = ly + 20 + (i // 6) * 20
        parts.append(f'<rect x="{lx}" y="{row_y-10}" width="12" height="12" rx="2" '
                     f'fill="{meta.get("color","#64748b")}"/>')
        parts.append(f'<text x="{lx+18}" y="{row_y}" font-size="11" fill="#cbd5e1">'
                     f'{_xml_escape(meta.get("label", kind))}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def render_png_bytes(graph: dict, title: str = "") -> bytes:
    """The same layout, rasterised with Pillow (already a hard dependency of
    this repo's compositors) rather than adding an SVG-to-raster library just
    for this one button."""
    import io
    from PIL import Image, ImageDraw, ImageFont

    nodes = graph["nodes"] or [{"id": ROOT_ID, "x": 0, "y": 0, "kind": "root",
                                "label": "Empty consultation", "status_label": None}]
    edges = graph["edges"]
    box_w, box_h = 200, 70
    pad = 90
    xs = [n["x"] for n in nodes]
    ys = [n["y"] for n in nodes]
    min_x, max_x = min(xs) - box_w / 2, max(xs) + box_w / 2
    min_y, max_y = min(ys) - box_h / 2, max(ys) + box_h / 2
    width = int((max_x - min_x) + pad * 2)
    height = int((max_y - min_y) + pad * 2 + 100)
    off_x, off_y = -min_x + pad, -min_y + pad + 60

    def X(x: float) -> int: return int(x + off_x)
    def Y(y: float) -> int: return int(y + off_y)

    img = Image.new("RGB", (max(width, 400), max(height, 300)), "#0f172a")
    draw = ImageDraw.Draw(img)

    def _load(size: int, paths: tuple[str, ...] = _PNG_FONT_STACK):
        for path in paths:
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
        return ImageFont.load_default()

    font, small, title_font = _load(13), _load(10), _load(18, _PNG_BOLD_FONT_STACK)

    if title:
        draw.text((width / 2, 24), title, fill="#f1f5f9", font=title_font, anchor="mm")

    def _styled_line(p1: tuple, p2: tuple, colour: str, style: str) -> None:
        """PIL has no native dashed stroke; approximate one by drawing short
        segments, so a "challenges" edge still reads differently from a
        "supports" one on the raster export, not only on the SVG (section 6:
        "PNG currently loses relationship … styles")."""
        if style == "solid":
            draw.line([p1, p2], fill=colour, width=2)
            return
        import math
        x1, y1 = p1
        x2, y2 = p2
        length = math.hypot(x2 - x1, y2 - y1) or 1.0
        dash_len, gap_len = (6, 4) if style == "dashed" else (2, 3)
        step = dash_len + gap_len
        t = 0.0
        while t < length:
            t_end = min(length, t + dash_len)
            f0, f1 = t / length, t_end / length
            draw.line([(x1 + (x2 - x1) * f0, y1 + (y2 - y1) * f0),
                      (x1 + (x2 - x1) * f1, y1 + (y2 - y1) * f1)], fill=colour, width=2)
            t += step

    by_id = {n["id"]: n for n in nodes}
    for e in edges:
        a, b = by_id.get(e["from_id"]), by_id.get(e["to_id"])
        if not a or not b:
            continue
        meta = RELATION_META.get(e["relation"], {})
        cross = e["kind"] != "hierarchy"
        colour = "#475569" if not cross else "#eab308"
        p1, p2 = (X(a["x"]), Y(a["y"])), (X(b["x"]), Y(b["y"]))
        _styled_line(p1, p2, colour, meta.get("style", "solid") if cross else "solid")
        if cross:
            label_text = e.get("label") or meta.get("label", "")
            if label_text:
                mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
                draw.text((mx, my), label_text, fill="#cbd5e1", font=small, anchor="mm")

    for n in nodes:
        colour = NODE_KIND_META.get(n["kind"], {}).get("color", "#64748b")
        cx, cy = X(n["x"]), Y(n["y"])
        box = (cx - box_w / 2, cy - box_h / 2, cx + box_w / 2, cy + box_h / 2)
        draw.rounded_rectangle(box, radius=8, fill="#1e293b", outline=colour, width=2)
        lines = _node_box_lines(n, width_chars=28)[:3]
        ty = cy - (len(lines) - 1) * 8
        for i, line in enumerate(lines):
            draw.text((cx, ty + i * 16), line, fill="#f1f5f9", font=font, anchor="mm")
        kind_label = NODE_KIND_META.get(n["kind"], {}).get("label", n["kind"]).upper()
        draw.text((cx, cy + box_h / 2 - 10), kind_label, fill=colour, font=small, anchor="mm")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _outline_html(graph: dict) -> str:
    """A textual outline of the map — the accessibility alternative to the SVG
    picture (section 6), built by walking the hierarchy edges from the root."""
    by_id = {n["id"]: n for n in graph["nodes"]}
    children: dict[str, list[str]] = {}
    for e in graph["edges"]:
        if e["relation"] == HIERARCHY_RELATION:
            children.setdefault(e["from_id"], []).append(e["to_id"])

    def walk(node_id: str, seen: set) -> str:
        if node_id in seen or node_id not in by_id:
            return ""
        seen = seen | {node_id}
        node = by_id[node_id]
        label = _xml_escape(node["detail"] or node["label"])
        kind = NODE_KIND_META.get(node["kind"], {}).get("label", node["kind"])
        status = f" — {_xml_escape(node['status_label'])}" if node.get("status_label") else ""
        kids = "".join(walk(c, seen) for c in children.get(node_id, []))
        inner = f"<li><strong>{label}</strong> <em>({_xml_escape(kind)}{status})</em>"
        if kids:
            inner += f"<ul>{kids}</ul>"
        return inner + "</li>"

    return f"<ul>{walk(ROOT_ID, set())}</ul>"


def render_html_export(session: dict, graph: dict, narrative: dict,
                       decisions: list[dict], actions: list[dict],
                       writings: list[dict]) -> str:
    """
    A self-contained page: the map (inline SVG), the narrative, and the
    authoritative outcomes — suitable for printing to PDF, opens with no app and
    no external service. Escaped throughout; never includes a transcript excerpt
    or a private interactive payload (section 6's default-shareable scope).
    """
    title = session.get("title") or "Consultation"
    esc = _xml_escape
    svg = render_svg(graph, title="")
    parts: list[str] = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        f"<title>{esc(title)}</title>",
        "<style>",
        "body{font-family:Georgia,'Times New Roman',serif;max-width:920px;margin:2rem auto;"
        "padding:0 1.5rem;color:#1e293b;background:#fff;line-height:1.5}",
        "h1{font-size:1.6rem;margin-bottom:0}",
        ".meta{color:#64748b;font-size:.9rem;margin-top:.25rem}",
        "h2{font-size:1.15rem;border-bottom:1px solid #e2e8f0;padding-bottom:.25rem;"
        "margin-top:2rem}",
        ".map-wrap{overflow-x:auto;border:1px solid #e2e8f0;border-radius:8px;margin:1rem 0}",
        "blockquote{border-left:3px solid #cbd5e1;padding-left:1rem;font-style:italic;color:#334155}",
        "ul{padding-left:1.2rem}",
        "@media print{.map-wrap{border:none}}",
        "</style></head><body>",
        f"<h1>{esc(title)}</h1>",
    ]
    # An approval-state banner — this export has never had one, so a reader
    # could not tell a reviewed record from a live draft still changing under
    # someone's hands (section 6: "no approval-state banner"). The three
    # states mirror `_record_status`: never approved, approved and current,
    # approved but the record has moved on since.
    approved_at = session.get("approved_at")
    if not approved_at:
        parts.append("<p class='meta'><strong>Draft — not yet approved.</strong> Nobody has "
                     "reviewed and approved this record.</p>")
    else:
        stale = (int(session.get("record_revision") or 0)
                 != int(session.get("approved_revision") or 0))
        if stale:
            parts.append(f"<p class='meta'><strong>Approved {esc(approved_at)}, but the "
                         "record has changed since.</strong> This export reflects the "
                         "CURRENT map, not the one that was approved.</p>")
        else:
            parts.append(f"<p class='meta'><strong>Approved {esc(approved_at)}, and "
                         "current.</strong></p>")
    if session.get("question"):
        parts.append(f"<p class='meta'><strong>The question before the group:</strong> "
                     f"{esc(session['question'])}</p>")
    if narrative.get("in_short"):
        parts.append(f"<h2>In short</h2><p>{esc(narrative['in_short'])}</p>")

    parts.append("<h2>Concept map</h2>")
    parts.append(f"<div class='map-wrap'>{svg}</div>")
    parts.append("<details><summary>Text outline (for screen readers, and if the "
                 "picture above does not render)</summary>" + _outline_html(graph) + "</details>")

    confirmed = [d for d in decisions if d.get("status") == "confirmed"]
    parts.append("<h2>What was decided</h2>")
    if confirmed:
        for d in confirmed:
            parts.append(f"<p><strong>{esc(d.get('text',''))}</strong></p>")
            retained = [c for c in (d.get("retained_concerns") or []) if str(c).strip()]
            if retained:
                parts.append("<p><em>Concerns carried forward:</em></p><ul>" +
                             "".join(f"<li>{esc(str(c))}</li>" for c in retained) + "</ul>")
    else:
        parts.append("<p>No decision was confirmed in this meeting.</p>")

    listed = [a for a in actions if (a.get("action") or "").strip() and a.get("status") != "dropped"]
    parts.append("<h2>What happens next</h2>")
    if listed:
        parts.append("<ul>")
        for a in listed:
            owner = (a.get("owner") or "").strip() or "Owner not assigned"
            # A name is a PROPOSAL until somebody records that the person
            # accepted it (rule 95) — the same three-state qualifier the report
            # and the plain-text export already carry. This export used to
            # print the owner's name with no qualifier at all, so an EXPLICITLY
            # DECLINED action read exactly like an accepted one.
            accepted = a.get("owner_accepted")
            if (a.get("owner") or "").strip():
                if accepted is None:
                    owner += " — not yet accepted"
                elif accepted:
                    by = (a.get("accepted_by") or "").strip()
                    owner += " — accepted" + (f" (recorded by {by})" if by else "")
                else:
                    owner += " — did not accept"
            due = f", due {esc(a['due'])}" if a.get("due") else ""
            status = a.get("status") or "proposed"
            from agents.live_consultation import ACTION_STATUS_LABELS
            mark = (f" <em>({esc(ACTION_STATUS_LABELS.get(status, status).lower())})</em>"
                   if status not in ("proposed", "accepted") else "")
            parts.append(f"<li><strong>{esc(a['action'])}</strong> — {esc(owner)}{due}{mark}</li>")
        parts.append("</ul>")
    else:
        parts.append("<p>No action items were recorded.</p>")

    if narrative.get("discussion"):
        parts.append(f"<h2>How the group got there</h2><p>{esc(narrative['discussion'])}</p>")
    if narrative.get("still_open"):
        parts.append(f"<h2>Still open</h2><p>{esc(narrative['still_open'])}</p>")

    if writings:
        parts.append("<h2>Passages referred to</h2>")
        for w in writings:
            text = (w.get("text") or "").strip()
            if not text:
                continue
            source = " — ".join(x for x in (w.get("source"), w.get("section")) if x)
            parts.append(f"<blockquote>{esc(text)}<footer>{esc(source)}</footer></blockquote>")

    parts.append("</body></html>")
    return "\n".join(parts)
