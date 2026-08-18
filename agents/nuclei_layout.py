"""
Where every Real World light sits.

Rule 62: one light per person. Each nucleus is its own point of light.
Distance to each nucleus is about *that* nucleus (core service close,
connected far). The drawn seat is the engagement-weighted average of
those per-nucleus targets. A neighbour arriving does not change anyone's seat. After seats
are chosen, lights that would cover each other are squeezed apart
by the smallest step that keeps every dot distinct.

Default chairs are assigned by grouping created_at then id, never by
size. pos_x/pos_y is an owner override (a drag, or Arrange the tables).
Nothing here is a score on a soul (rule 61).
"""

from __future__ import annotations

import math

TILT = 0.42
VIEW_W = 1440
VIEW_H = 720

# Fixed chairs — same starting size, far enough apart that two fully
# grown nuclei cannot sit on each other. Assigned in creation order.
# A table grows with ITS people only; a neighbour does not move.
BASE_R = 110
GROWTH = 18
MAX_R = 185
SLOTS = [
    {"cx": 480, "cy": 230},
    {"cx": 880, "cy": 230},
    {"cx": 1260, "cy": 230},
    {"cx": 480, "cy": 525},
    {"cx": 880, "cy": 525},
    {"cx": 1260, "cy": 525},
]
# Holes in that grid — a 7th table used to land on the 1st (i % 6).
EXTRA_SLOTS = [
    {"cx": 680, "cy": 378},
    {"cx": 1080, "cy": 378},
    {"cx": 680, "cy": 155},
    {"cx": 1080, "cy": 155},
    {"cx": 680, "cy": 645},
    {"cx": 1080, "cy": 645},
]

# Keep air between tables so names and cores do not cover a neighbour.
PAD = 36.0
# Leave the institution cluster, the workforce light, and the legend alone.
MIN_X = 360.0
MAX_X = 1380.0
MIN_Y = 130.0
MAX_Y = 660.0
WF_KEEP = 110.0

# Workforce sits to the RIGHT of the institutions of the Faith.
WORKFORCE = {"cx": 278, "cy": 318}

# Local institutions sit in a column left of the Workforce. The owner
# adds LSA, Regional Institute, Auxiliary Board, teaching committee…
INSTITUTION_SLOTS = [
    {"cx": 118, "cy": 168},
    {"cx": 118, "cy": 268},
    {"cx": 118, "cy": 368},
    {"cx": 118, "cy": 468},
    {"cx": 72, "cy": 218},
    {"cx": 164, "cy": 218},
    {"cx": 72, "cy": 318},
    {"cx": 164, "cy": 318},
]
INSTITUTION_R = 48.0
INSTITUTION_GROWTH = 14.0
INSTITUTION_MAX_R = 92.0
INST_MIN_X = 50.0
INST_MAX_X = 220.0
INST_MIN_Y = 120.0
INST_MAX_Y = 520.0
NUCLEUS_ACCENTS = ("sky", "violet", "emerald", "rose", "amber")

# engagement 0..4 -> radius as a fraction of the grouping's slot r
RING_R = (0.86, 0.72, 0.58, 0.44, 0.32)
ENGAGE_W = (0.4, 1.1, 2.2, 3.6, 5.2)

# Visible halo of a person-dot (matches RealWorldGraph). Not the big glow.
DOT_PAD = 3.0
INSTITUTION_SEAT_TILT = 0.68
# First-name labels sit under the dot (~11px type).
LABEL_UP = 10.0
LABEL_DOWN = 24.0
LABEL_PAD = 4.0


def _hash_unit(seed: str) -> float:
    h = 2166136261
    for ch in seed:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return h / 4294967296.0


def engagement_from_facets(facets: list[dict]) -> int:
    """0 everyone else .. 4 core service. Per membership, not a score."""
    slugs = {f.get("slug") for f in (facets or [])}
    if slugs & {"tutoring", "animating", "hosting", "childrens_classes"}:
        return 4
    if "protagonist" in slugs or "accompanying" in slugs:
        return 3
    if "regularly_participating" in slugs:
        return 2
    if "connected" in slugs:
        return 1
    return 0


def radius_for(n_members: int) -> float:
    """Every table starts the same size and grows with its own people."""
    extra = GROWTH * math.sqrt(max(0, int(n_members) - 1))
    return min(MAX_R, BASE_R + extra)


def institution_radius(n_members: int) -> float:
    """Same growth shape as a nucleus, on a smaller starting size."""
    extra = INSTITUTION_GROWTH * math.sqrt(max(0, int(n_members) - 1))
    return min(INSTITUTION_MAX_R, INSTITUTION_R + extra)


def _member_counts(memberships: list[dict]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for m in memberships:
        gid = int(m["grouping_id"])
        counts[gid] = counts.get(gid, 0) + 1
    return counts


def default_chair(index: int) -> dict:
    """Creation-order chair. First six are the grid; later ones sit in
    the holes so a seventh table never lands on a neighbour."""
    if index < len(SLOTS):
        return dict(SLOTS[index])
    k = index - len(SLOTS)
    if k < len(EXTRA_SLOTS):
        return dict(EXTRA_SLOTS[k])
    ring = 1 + (k - len(EXTRA_SLOTS)) // 5
    slot = (k - len(EXTRA_SLOTS)) % 5
    ang = (slot / 5.0) * math.pi * 2 - 0.35
    rad = 320.0 * ring
    cx, cy = clamp_point(860.0 + rad * math.cos(ang),
                         390.0 + rad * 0.62 * math.sin(ang))
    return {"cx": cx, "cy": cy}


def _has_pos(g: dict) -> bool:
    return g.get("pos_x") is not None and g.get("pos_y") is not None


def is_institution_row(g: dict) -> bool:
    return g.get("kind_slug") == "institution"


def is_workforce_row(g: dict) -> bool:
    """The Bahá'í Workforce keeps its own fixed light (rule 65).

    It is a real grouping so that joining it is an ordinary membership, but
    it is never a table on the sky: it takes no chair, it takes no slot
    index (so a new nucleus cannot slide onto it), and its members get no
    seat from it — they open out of it like a family opens (rule 62).
    """
    return g.get("kind_slug") == "workforce"


def institution_chair(index: int) -> dict:
    if index < len(INSTITUTION_SLOTS):
        return dict(INSTITUTION_SLOTS[index])
    wrap = index - len(INSTITUTION_SLOTS)
    cx, cy = clamp_institution(118.0 + (wrap % 2) * 48.0 - 24.0,
                               158.0 + (wrap // 2 + 4) * 72.0)
    return {"cx": cx, "cy": cy}


def clamp_institution(cx: float, cy: float) -> tuple[float, float]:
    """Keep a local institution in the column left of the Workforce."""
    if not math.isfinite(cx) or not math.isfinite(cy):
        return INSTITUTION_SLOTS[0]["cx"], INSTITUTION_SLOTS[0]["cy"]
    cx = min(INST_MAX_X, max(INST_MIN_X, cx))
    cy = min(INST_MAX_Y, max(INST_MIN_Y, cy))
    return cx, cy


def chair_for(g: dict, index: int) -> dict:
    """Owner override if both coordinates are set; otherwise the default."""
    if is_institution_row(g):
        if _has_pos(g):
            cx, cy = clamp_institution(float(g["pos_x"]), float(g["pos_y"]))
            return {"cx": cx, "cy": cy}
        return institution_chair(index)
    if _has_pos(g):
        cx, cy = clamp_point(float(g["pos_x"]), float(g["pos_y"]))
        return {"cx": cx, "cy": cy}
    return default_chair(index)


def clamp_point(cx: float, cy: float, r: float = BASE_R) -> tuple[float, float]:
    """Keep a table on the canvas and off the workforce light."""
    if not math.isfinite(cx) or not math.isfinite(cy):
        return SLOTS[0]["cx"], SLOTS[0]["cy"]
    pad = max(24.0, r * 0.25)
    cx = min(MAX_X - pad, max(MIN_X + pad, cx))
    cy = min(MAX_Y, max(MIN_Y, cy))
    dx = cx - WORKFORCE["cx"]
    dy = cy - WORKFORCE["cy"]
    dist = math.hypot(dx, dy)
    keep = WF_KEEP + r * 0.35
    if dist < keep:
        if dist < 1e-6:
            cx = WORKFORCE["cx"] + keep
            cy = WORKFORCE["cy"]
        else:
            s = keep / dist
            cx = WORKFORCE["cx"] + dx * s
            cy = WORKFORCE["cy"] + dy * s
        cx = min(MAX_X - pad, max(MIN_X + pad, cx))
        cy = min(MAX_Y, max(MIN_Y, cy))
    return cx, cy


def _overlap_push(ax, ay, ar, bx, by, br) -> tuple[float, float, float]:
    """How far B must move from A so rings and labels do not cover.

    Returns (ux, uy, overlap). overlap > 0 means they sit on each other.
    """
    dx = bx - ax
    dy = by - ay
    d = math.hypot(dx, dy)
    # Cores and titles need an isotropic gap; flat rings can sit a bit closer
    # vertically, but a name stacked on a neighbour is the failure we saw.
    need = max(118.0, (ar + br) * 0.62 + PAD)
    if d < 1e-6:
        return 1.0, 0.0, need
    return dx / d, dy / d, need - d


def suggest_free_chair(occupied: list[dict]) -> tuple[float, float]:
    """First default chair that does not sit on an existing live table."""
    candidates = [default_chair(i) for i in range(len(SLOTS) + len(EXTRA_SLOTS) + 8)]
    for c in candidates:
        cx, cy = clamp_point(c["cx"], c["cy"])
        ok = True
        for o in occupied:
            _, _, overlap = _overlap_push(
                float(o["cx"]), float(o["cy"]), float(o.get("r") or BASE_R),
                cx, cy, BASE_R,
            )
            if overlap > 0:
                ok = False
                break
        if ok:
            return cx, cy
    last = candidates[-1]
    return clamp_point(last["cx"], last["cy"])


def assign_slots(groupings: list[dict],
                 memberships: list[dict] | None = None) -> list[dict]:
    """Stable: created_at then id, including archived chairs.

    An archived nucleus keeps its index so a neighbour does not slide
    over (rule 62). Only live groupings are returned. Radius grows
    with that grouping's live members, never with someone else's.
    A stored pos_x/pos_y wins over the default chair.
    """
    counts = _member_counts(memberships or [])
    ordered = sorted(
        groupings,
        key=lambda g: (g.get("created_at") or "", int(g["id"])),
    )
    out = []
    nucleus_n = 0
    table_i = 0
    inst_i = 0
    for g in ordered:
        if is_workforce_row(g):
            continue
        institution = is_institution_row(g)
        chair = chair_for(g, inst_i if institution else table_i)
        if institution:
            inst_i += 1
        else:
            table_i += 1
        if institution:
            accent = "gold"
        elif g.get("is_nucleus"):
            accent = NUCLEUS_ACCENTS[nucleus_n % len(NUCLEUS_ACCENTS)]
            nucleus_n += 1
        else:
            accent = g.get("accent") or "amber"
        if g.get("archived_at"):
            continue
        row = dict(g)
        n = counts.get(int(g["id"]), 0)
        r = institution_radius(n) if institution else radius_for(n)
        row["slot"] = {"cx": chair["cx"], "cy": chair["cy"], "r": r}
        row["accent"] = accent
        out.append(row)
    return out


def _affinity(
    groupings: list[dict],
    memberships: list[dict],
    ties: list[dict],
    activity_counts: dict,
    owner_id: int | None,
) -> dict[tuple[int, int], float]:
    """How much two tables belong near each other. Not a score on a person.

    Shared members (except the owner, who sits at every nucleus by design),
    accompaniment / household / introduced ties, and recorded gatherings
    at both tables. Owner-only links are ignored so his seat everywhere
    cannot collapse the map (rule 61).
    """
    members: dict[int, set[int]] = {}
    for m in memberships:
        members.setdefault(int(m["grouping_id"]), set()).add(int(m["actor_id"]))
    ids = [int(g["id"]) for g in groupings]
    aff: dict[tuple[int, int], float] = {}

    def bump(a: int, b: int, w: float):
        if a == b:
            return
        key = (a, b) if a < b else (b, a)
        aff[key] = aff.get(key, 0.0) + w

    for i, ga in enumerate(ids):
        for gb in ids[i + 1:]:
            sa = members.get(ga, set())
            sb = members.get(gb, set())
            shared = sa & sb
            if owner_id is not None:
                shared = {p for p in shared if p != int(owner_id)}
            if shared:
                bump(ga, gb, 4.0 * len(shared))

    by_actor: dict[int, set[int]] = {}
    for m in memberships:
        by_actor.setdefault(int(m["actor_id"]), set()).add(int(m["grouping_id"]))

    for t in ties or []:
        a = int(t["from_actor_id"])
        b = int(t["to_actor_id"])
        if owner_id is not None and (a == int(owner_id) or b == int(owner_id)):
            continue
        slug = t.get("slug") or ""
        w = 3.0 if slug == "accompanying" else (2.0 if slug == "household" else 1.0)
        for ga in by_actor.get(a, ()):
            for gb in by_actor.get(b, ()):
                bump(ga, gb, w)

    acts_by_actor: dict[int, dict[int, int]] = {}
    for (aid, gid), n in (activity_counts or {}).items():
        if owner_id is not None and int(aid) == int(owner_id):
            continue
        acts_by_actor.setdefault(int(aid), {})[int(gid)] = int(n)
    for _aid, per in acts_by_actor.items():
        gids = list(per)
        for i, ga in enumerate(gids):
            for gb in gids[i + 1:]:
                bump(ga, gb, 0.6 * min(per[ga], per[gb]))
    return aff


def optimize_positions(
    groupings: list[dict],
    memberships: list[dict],
    ties: list[dict] | None = None,
    activity_counts: dict | None = None,
    owner_id: int | None = None,
) -> dict[int, tuple[float, float]]:
    """Deterministic rearrangement from size, shared people, gatherings, ties.

    Not a physics sim on load — the owner clicks Arrange. Same input,
    same chairs. Starts from the default grid (not the last drag) so a
    second click does not wander. Large tables move less than small ones.
    """
    seed = []
    for g in groupings:
        row = dict(g)
        row["pos_x"] = None
        row["pos_y"] = None
        seed.append(row)
    placed = assign_slots(seed, memberships)
    if not placed:
        return {}
    aff = _affinity(placed, memberships, ties or [], activity_counts or {}, owner_id)
    counts = _member_counts(memberships)
    items = []
    for g in placed:
        gid = int(g["id"])
        items.append({
            "id": gid,
            "x": float(g["slot"]["cx"]),
            "y": float(g["slot"]["cy"]),
            "r": float(g["slot"]["r"]),
            "mass": 1.0 + math.sqrt(max(1, counts.get(gid, 0))),
        })

    vx = {it["id"]: 0.0 for it in items}
    vy = {it["id"]: 0.0 for it in items}
    for _ in range(90):
        fx = {it["id"]: 0.0 for it in items}
        fy = {it["id"]: 0.0 for it in items}
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                ux, uy, overlap = _overlap_push(
                    a["x"], a["y"], a["r"], b["x"], b["y"], b["r"]
                )
                if overlap > 0:
                    push = overlap * 0.55
                    fx[a["id"]] -= ux * push
                    fy[a["id"]] -= uy * push
                    fx[b["id"]] += ux * push
                    fy[b["id"]] += uy * push
                    continue
                key = (a["id"], b["id"]) if a["id"] < b["id"] else (b["id"], a["id"])
                w = aff.get(key, 0.0)
                if w <= 0:
                    continue
                dx = b["x"] - a["x"]
                dy = b["y"] - a["y"]
                d = math.hypot(dx, dy) or 1.0
                need = max(118.0, (a["r"] + b["r"]) * 0.62 + PAD)
                desired = need + 90.0 / (1.0 + w)
                if d > desired:
                    pull = (d - desired) * 0.14 * min(w, 12.0) / 12.0
                    fx[a["id"]] += (dx / d) * pull
                    fy[a["id"]] += (dy / d) * pull
                    fx[b["id"]] -= (dx / d) * pull
                    fy[b["id"]] -= (dy / d) * pull
        for it in items:
            gid = it["id"]
            vx[gid] = (vx[gid] + fx[gid] / it["mass"]) * 0.72
            vy[gid] = (vy[gid] + fy[gid] / it["mass"]) * 0.72
            it["x"], it["y"] = clamp_point(
                it["x"] + vx[gid], it["y"] + vy[gid], it["r"]
            )

    # Guarantee no cover remains (same order every time).
    for _ in range(40):
        moved = False
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                ux, uy, overlap = _overlap_push(
                    a["x"], a["y"], a["r"], b["x"], b["y"], b["r"]
                )
                if overlap <= 0:
                    continue
                moved = True
                share_a = b["mass"] / (a["mass"] + b["mass"])
                share_b = a["mass"] / (a["mass"] + b["mass"])
                a["x"], a["y"] = clamp_point(
                    a["x"] - ux * overlap * share_a,
                    a["y"] - uy * overlap * share_a,
                    a["r"],
                )
                b["x"], b["y"] = clamp_point(
                    b["x"] + ux * overlap * share_b,
                    b["y"] + uy * overlap * share_b,
                    b["r"],
                )
        if not moved:
            break

    return {it["id"]: (it["x"], it["y"]) for it in items}


def layout_real_world(
    groupings: list[dict],
    actors: list[dict],
    memberships: list[dict],
    facets_by_mem: dict,
    activity_counts: dict,
    household_members: list[dict] | None = None,
    ties: list[dict] | None = None,
) -> dict:
    """
    activity_counts: {(actor_id, grouping_id): n}
    facets_by_mem: {membership_id: [facet dicts]}
    """
    placed_g = assign_slots(groupings, memberships)
    by_g = {int(g["id"]): g for g in placed_g}
    mems_by_actor: dict[int, list] = {}
    for m in memberships:
        mems_by_actor.setdefault(int(m["actor_id"]), []).append(m)

    peers_by_g: dict[int, list[int]] = {}
    for m in memberships:
        g = by_g.get(int(m["grouping_id"]))
        if g and is_institution_row(g):
            peers_by_g.setdefault(int(m["grouping_id"]), []).append(int(m["actor_id"]))
    for gid in peers_by_g:
        peers_by_g[gid] = sorted(set(peers_by_g[gid]))

    house_of: dict[int, int] = {}
    for h in household_members or []:
        house_of[int(h["person_id"])] = int(h["household_id"])
    kind_rank = {"household": 0, "collective": 1}
    ordered_actors = sorted(
        actors, key=lambda a: (kind_rank.get(a.get("kind"), 2), int(a["id"]))
    )
    lights = []
    for actor in ordered_actors:
        aid = int(actor["id"])
        placed = _place_actor(
            actor, mems_by_actor.get(aid, []), by_g, facets_by_mem,
            activity_counts, peers_by_g,
            house_of=house_of, ties=ties,
        )
        if placed:
            lights.append(placed)
    lights = _separate_lights(lights, {int(a["id"]): a for a in actors})

    return {
        "workforce": dict(WORKFORCE),
        "groupings": [
            {
                "id": g["id"],
                "cx": g["slot"]["cx"],
                "cy": g["slot"]["cy"],
                "r": g["slot"]["r"],
                "is_nucleus": bool(g.get("is_nucleus")),
                "is_institution": is_institution_row(g),
                "slug": g.get("slug"),
                "accent": g.get("accent") or "amber",
            }
            for g in placed_g
        ],
        "actors": lights,
    }


def institution_seat_r(orbital_r: float) -> float:
    """Sit on the drawn orbit, just outside the outermost ring (0.72)."""
    return float(orbital_r) * 0.82


def _place_actor(actor, mems, by_g, facets_by_mem, activity_counts,
                 peers_by_g: dict | None = None,
                 house_of: dict | None = None,
                 ties: list[dict] | None = None) -> dict | None:
    aid = int(actor["id"])
    if actor.get("kind") == "collective":
        if not mems:
            return None
        home = by_g.get(int(mems[0]["grouping_id"]))
        if not home:
            return None
        slot = home["slot"]
        theta = 0.55
        rr = slot["r"] * 0.88
        return {
            "id": aid,
            "x": slot["cx"] + rr * math.cos(theta),
            "y": slot["cy"] + rr * TILT * math.sin(theta),
            "home_grouping_id": home["id"],
            "accent": home.get("accent") or "amber",
        }

    tx = ty = total = 0.0
    best_w = -1.0
    home_id = None
    home_accent = "amber"
    for m in mems:
        gid = int(m["grouping_id"])
        g = by_g.get(gid)
        if not g:
            continue
        facets = facets_by_mem.get(m["id"]) or facets_by_mem.get(str(m["id"])) or []
        institution = is_institution_row(g)
        e = 4 if institution else engagement_from_facets(facets)
        n_act = activity_counts.get((aid, gid), 0)
        w = ENGAGE_W[e] * (1 + n_act * 0.4)
        if institution:
            peers = (peers_by_g or {}).get(gid) or [aid]
            n = max(1, len(peers))
            try:
                i = peers.index(aid)
            except ValueError:
                i = 0
            theta = (i / n) * math.pi * 2
            rr = institution_seat_r(g["slot"]["r"])
            y_tilt = INSTITUTION_SEAT_TILT
            w *= 1.15
        else:
            theta = _hash_unit(f"{aid}:{gid}") * math.pi * 2 - math.pi
            rr = g["slot"]["r"] * RING_R[e]
            y_tilt = TILT
            if not g.get("is_nucleus"):
                w *= 0.7
        tx += (g["slot"]["cx"] + rr * math.cos(theta)) * w
        ty += (g["slot"]["cy"] + rr * y_tilt * math.sin(theta)) * w
        total += w
        if w > best_w:
            best_w = w
            home_id = gid
            home_accent = g.get("accent") or "amber"
    for t in ties or []:
        if (t.get("slug") or "") != "accompanying":
            continue
        if int(t.get("from_actor_id") or 0) != aid:
            continue
        gid = t.get("grouping_id")
        if not gid:
            continue
        g = by_g.get(int(gid))
        if not g:
            continue
        theta = _hash_unit(f"acc:{aid}:{gid}") * math.pi * 2 - math.pi
        rr = g["slot"]["r"] * RING_R[3]
        w = ENGAGE_W[3]
        tx += (g["slot"]["cx"] + rr * math.cos(theta)) * w
        ty += (g["slot"]["cy"] + rr * TILT * math.sin(theta)) * w
        total += w
        if w > best_w:
            best_w = w
            home_id = int(gid)
            home_accent = g.get("accent") or "amber"
    # Family-only people live inside the household light until it is opened.
    if total <= 0 and (house_of or {}).get(aid):
        return None
    if total <= 0:
        return None
    return {
        "id": aid,
        "x": tx / total,
        "y": ty / total,
        "home_grouping_id": home_id,
        "accent": home_accent,
    }


def dot_radius(actor: dict) -> float:
    """How wide the drawn dot is. Matches RealWorldGraph, plus a little air."""
    kind = actor.get("kind")
    if kind == "collective":
        return 9.0 * 1.7 + DOT_PAD
    if kind == "household":
        return 6.2 + 3.4 + DOT_PAD
    if actor.get("display_name") == "You":
        return 5.0 * 1.8 + DOT_PAD
    return 4.2 * 1.8 + DOT_PAD


def label_text(actor: dict) -> str:
    """The word drawn under the light — same shortening as the dashboard."""
    name = (actor.get("display_name") or "").strip() or "?"
    kind = actor.get("kind")
    if kind == "household":
        return name.replace("The ", "").replace(" household", "").replace(" Household", "")
    if kind == "collective":
        return "the gathering" if len(name) > 22 else name
    return name.split()[0]


def label_extent(actor: dict) -> tuple[float, float, float]:
    """Half-width, space above the dot, space below (for the name)."""
    text = label_text(actor)
    hw = max(15.0, len(text) * 3.2 + 6.0)
    return hw, LABEL_UP, LABEL_DOWN


def labels_overlap(ax: float, ay: float, a_ext, bx: float, by: float, b_ext,
                   pad: float = LABEL_PAD) -> tuple[float, float]:
    """Positive ox, oy means the name-boxes cover each other."""
    a_hw, a_up, a_dn = a_ext
    b_hw, b_up, b_dn = b_ext
    ox = (a_hw + b_hw + pad) - abs(bx - ax)
    top_a, bot_a = ay - a_up, ay + a_dn
    top_b, bot_b = by - b_up, by + b_dn
    oy = min(bot_a, bot_b) - max(top_a, top_b) + pad
    return ox, oy


def _separate_lights(lights: list[dict], actors_by_id: dict) -> list[dict]:
    """Keep every person's seat; spread covering dots and names apart.

    Deterministic (id order). Not a score. A neighbour who does not sit
    on anyone leaves every other light where it was.
    """
    if len(lights) < 2:
        return lights
    items = []
    for p in lights:
        actor = actors_by_id.get(int(p["id"])) or {}
        kind = actor.get("kind")
        items.append({
            "id": int(p["id"]),
            "x": float(p["x"]),
            "y": float(p["y"]),
            "ox": float(p["x"]),
            "oy": float(p["y"]),
            "r": dot_radius(actor),
            "ext": label_extent(actor),
            "mass": (
                3.5 if actor.get("display_name") == "You"
                else 2.0 if kind in ("household", "collective")
                else 1.0
            ),
            "src": p,
        })
    items.sort(key=lambda it: it["id"])

    def _push_overlaps() -> bool:
        moved = False
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                dx = b["x"] - a["x"]
                dy = b["y"] - a["y"]
                d = math.hypot(dx, dy)
                need_dot = a["r"] + b["r"]
                ox, oy = labels_overlap(a["x"], a["y"], a["ext"], b["x"], b["y"], b["ext"])
                if d < 1e-6:
                    ang = _hash_unit(f"{a['id']}:{b['id']}") * math.pi * 2
                    dx, dy, d = math.cos(ang), math.sin(ang), 1.0
                ux = uy = overlap = 0.0
                if d < need_dot:
                    ux, uy, overlap = dx / d, dy / d, need_dot - d
                if ox > 0 and oy > 0:
                    if ox < oy:
                        push_x = (1.0 if dx >= 0 else -1.0) * ox
                        if abs(push_x) > abs(ux * overlap):
                            ux, uy, overlap = (1.0 if dx >= 0 else -1.0), 0.0, ox
                    else:
                        push_y = (1.0 if dy >= 0 else -1.0) * oy
                        if abs(push_y) > abs(uy * overlap):
                            ux, uy, overlap = 0.0, (1.0 if dy >= 0 else -1.0), oy
                if overlap <= 0:
                    continue
                moved = True
                share_a = b["mass"] / (a["mass"] + b["mass"])
                share_b = a["mass"] / (a["mass"] + b["mass"])
                a["x"] -= ux * overlap * share_a
                a["y"] -= uy * overlap * share_a
                b["x"] += ux * overlap * share_b
                b["y"] += uy * overlap * share_b
        return moved

    for _ in range(70):
        moved = _push_overlaps()
        for it in items:
            it["x"] += (it["ox"] - it["x"]) * 0.03
            it["y"] += (it["oy"] - it["y"]) * 0.03
        if not moved:
            break
    for _ in range(50):
        if not _push_overlaps():
            break

    out = []
    for it in items:
        row = dict(it["src"])
        row["x"] = it["x"]
        row["y"] = it["y"]
        out.append(row)
    return out
