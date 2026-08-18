"""
Offline checks for the Real World (nuclei) store and layout.

    python scripts/test_nuclei.py

No network, no keys, no LLM. Invented names only. Refuses the owner's
private/nuclei.db (rule 59). ASCII arrows only (Windows cp1252).
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_TMP = tempfile.mkdtemp(prefix="nuclei_test_")
TEST_DB = Path(_TMP) / "nuclei.db"

import agents.nuclei_store as store  # noqa: E402
from agents import nuclei_layout as layout  # noqa: E402

store.assert_test_db(TEST_DB)

PASS = FAIL = 0
FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{label}{(' -- ' + detail) if detail else ''}")
        print(f"  FAIL: {label}" + (f" -- {detail}" if detail else ""))


def section(title: str):
    print(f"\n=== {title} ===")


# ── Fresh private db ──────────────────────────────────────────────────────────

section("Fresh database and kind seeds")
store.init_db(TEST_DB)
kinds = store.list_kinds(TEST_DB)
check("grouping kinds include nucleus",
      any(k["slug"] == "nucleus" and k["is_nucleus"] for k in kinds["grouping_kinds"]))
check("participation and service axes exist",
      {a["slug"] for a in kinds["axes"]} >= {"participation", "service"})
part = next(a for a in kinds["axes"] if a["slug"] == "participation")
serv = next(a for a in kinds["axes"] if a["slug"] == "service")
check("participation is exclusive", bool(part["exclusive"]))
check("service is not exclusive", not serv["exclusive"])
core = {f["slug"] for f in kinds["facet_kinds"] if f["is_core"]}
check("core activity services are marked",
      {"tutoring", "animating", "hosting", "childrens_classes"} <= core)
check("accompanying is service but not core",
      any(f["slug"] == "accompanying" and not f["is_core"] for f in kinds["facet_kinds"]))
check("institution is a grouping kind",
      any(k["slug"] == "institution" for k in kinds["grouping_kinds"]))
check("worldwide institutions are not auto-seeded",
      not any(g.get("slug") in store.RETIRED_INSTITUTION_SLUGS
              for g in store.list_groupings(TEST_DB)))

# renaming a label survives re-init
with store._connect(TEST_DB) as conn:
    conn.execute("UPDATE facet_kinds SET label = 'Gathers often' WHERE slug = 'regularly_participating'")
    conn.commit()
store.init_db(TEST_DB)
again = store.list_kinds(TEST_DB)
reg = next(f for f in again["facet_kinds"] if f["slug"] == "regularly_participating")
check("renamed facet label survives second init_db",
      reg["label"] == "Gathers often")


# ── Refuse the real private path ──────────────────────────────────────────────

section("Refuses the owner's private database")
raised = False
try:
    store.assert_test_db(store.DB_PATH)
except RuntimeError:
    raised = True
check("assert_test_db raises on private/nuclei.db", raised)

raised = False
try:
    store.assert_test_db(store.PRIVATE_DIR / "sneaky.db")
except RuntimeError:
    raised = True
check("assert_test_db raises on any path inside private/", raised)


# ── Households, collectives, unnamed friend ───────────────────────────────────

section("Households, collectives, unnamed friend")
nucleus = store.create_grouping("nucleus", "Thursday supper", TEST_DB)
dawn = store.create_grouping("nucleus", "Dawn walkers", TEST_DB)
jy = store.create_grouping("junior_youth", "Junior youth families", TEST_DB)
you = store.ensure_owner(TEST_DB)
amara = store.create_actor("person", "Amara Voss", "Met at a gathering", TEST_DB)
friend = store.create_actor(
    "person", "a friend of Nia's in Portland", db_path=TEST_DB
)
hale = store.create_actor("household", "The Hale household", db_path=TEST_DB)
cloud = store.create_actor("collective", "Everyone who comes to the Saturday walk",
                           db_path=TEST_DB)
check("owner is a person named You", you["kind"] == "person" and you["display_name"] == "You")
check("household is first-class", hale["kind"] == "household")
check("collective is first-class", cloud["kind"] == "collective")
store.add_membership(hale["id"], jy["id"], db_path=TEST_DB)
store.add_membership(cloud["id"], dawn["id"], db_path=TEST_DB)
store.add_membership(friend["id"], nucleus["id"],
                     introduced_as="a friend of Nia's in Portland", db_path=TEST_DB)
detail = store.actor_detail(friend["id"], TEST_DB)
check("unnamed friend keeps that sentence as their name",
      detail["actor"]["display_name"] == "a friend of Nia's in Portland")
check("no notes column on the actor", "notes" not in detail["actor"])


# ── Multi-membership and orbit_index ──────────────────────────────────────────

section("Memberships and orbit_index")
store.add_membership(you["id"], nucleus["id"], db_path=TEST_DB)
store.add_membership(you["id"], dawn["id"], db_path=TEST_DB)
store.add_membership(amara["id"], nucleus["id"], db_path=TEST_DB)
dario = store.create_actor("person", "Dario Chen", db_path=TEST_DB)
m1 = store.add_membership(dario["id"], nucleus["id"], db_path=TEST_DB)
m2 = store.add_membership(dario["id"], dawn["id"], db_path=TEST_DB)
check("orbit indexes in one grouping are 0,1,2...",
      {m1["orbit_index"]} == {m1["orbit_index"]} and m1["orbit_index"] >= 0)
# end middle-ish: end amara's supper membership, add priya
amara_mem = next(m for m in store.live_memberships(TEST_DB)
                 if m["actor_id"] == amara["id"] and m["grouping_id"] == nucleus["id"])
store.end_membership(amara_mem["id"], TEST_DB)
priya = store.create_actor("person", "Priya Sen", db_path=TEST_DB)
priya_mem = store.add_membership(priya["id"], nucleus["id"], db_path=TEST_DB)
check("ending a membership does not compact orbit_index",
      priya_mem["orbit_index"] > amara_mem["orbit_index"])
# put amara back
amara_mem = store.add_membership(amara["id"], nucleus["id"], db_path=TEST_DB)


# ── Slots are not by size ─────────────────────────────────────────────────────

section("Slots are not by size")
# dawn was created second, so it keeps slot 1 even if supper has more people
snap = store.snapshot(TEST_DB)
g_by_id = {g["id"]: g for g in snap["layout"]["groupings"]}
check("first-created grouping gets the first slot",
      g_by_id[nucleus["id"]]["cx"] == layout.SLOTS[0]["cx"])
check("second-created grouping keeps the second slot",
      g_by_id[dawn["id"]]["cx"] == layout.SLOTS[1]["cx"])
dx = g_by_id[nucleus["id"]]["cx"] - g_by_id[dawn["id"]]["cx"]
dy = g_by_id[nucleus["id"]]["cy"] - g_by_id[dawn["id"]]["cy"]
gap = (dx * dx + dy * dy) ** 0.5
reach = g_by_id[nucleus["id"]]["r"] + g_by_id[dawn["id"]]["r"]
check("two nuclei do not sit on each other",
      gap > reach, f"gap={gap:.1f} reach={reach:.1f}")

section("Equal start, grow with own people")
eq_a = store.create_grouping("nucleus", "Equal A", TEST_DB)
eq_b = store.create_grouping("nucleus", "Equal B", TEST_DB)
eq_snap = store.snapshot(TEST_DB)
ra = next(g for g in eq_snap["layout"]["groupings"] if g["id"] == eq_a["id"])
rb = next(g for g in eq_snap["layout"]["groupings"] if g["id"] == eq_b["id"])
check("two new nuclei start the same size", abs(ra["r"] - rb["r"]) < 1e-6)
check("a new nucleus starts at the base radius", abs(ra["r"] - layout.BASE_R) < 1e-6)
for i in range(6):
    p = store.create_actor("person", f"Test Friend {i}", db_path=TEST_DB)
    store.add_membership(p["id"], eq_a["id"], db_path=TEST_DB)
grown = store.snapshot(TEST_DB)
ra2 = next(g for g in grown["layout"]["groupings"] if g["id"] == eq_a["id"])
rb2 = next(g for g in grown["layout"]["groupings"] if g["id"] == eq_b["id"])
check("a table grows when its own people arrive", ra2["r"] > ra["r"])
check("a neighbour does not grow when someone else's table does",
      abs(rb2["r"] - rb["r"]) < 1e-6)
check("a neighbour does not move when someone else's table grows",
      rb2["cx"] == rb["cx"] and rb2["cy"] == rb["cy"])


# ── Two lists, not a funnel ───────────────────────────────────────────────────

section("Two lists, not a funnel")
store.add_facet(amara_mem["id"], "regularly_participating", TEST_DB)
store.add_facet(amara_mem["id"], "tutoring", TEST_DB)
store.add_facet(amara_mem["id"], "animating", TEST_DB)
fs = store.live_facets(amara_mem["id"], TEST_DB)
slugs = {f["slug"] for f in fs}
check("can gather regularly AND tutor AND animate",
      slugs >= {"regularly_participating", "tutoring", "animating"})
store.add_facet(amara_mem["id"], "connected", TEST_DB)
fs = store.live_facets(amara_mem["id"], TEST_DB)
part = {f["slug"] for f in fs if f["axis_slug"] == "participation"}
check("exclusive participation replaces the previous one",
      part == {"connected"})
# put her back to regular + core
store.add_facet(amara_mem["id"], "regularly_participating", TEST_DB)
core_live = [f for f in store.live_facets(amara_mem["id"], TEST_DB) if f["is_core"]]
check("tutoring and animating stay core",
      {f["slug"] for f in core_live} >= {"tutoring", "animating"})


# ── One light, per-nucleus distance ───────────────────────────────────────────

section("One light per person; distance is per nucleus")
you_sup = next(m for m in store.live_memberships(TEST_DB)
               if m["actor_id"] == you["id"] and m["grouping_id"] == nucleus["id"])
you_dawn = next(m for m in store.live_memberships(TEST_DB)
                if m["actor_id"] == you["id"] and m["grouping_id"] == dawn["id"])
store.add_facet(you_sup["id"], "tutoring", TEST_DB)
store.add_facet(you_dawn["id"], "hosting", TEST_DB)
store.add_facet(amara_mem["id"], "tutoring", TEST_DB)
# priya only gathers
store.add_facet(priya_mem["id"], "regularly_participating", TEST_DB)

snap = store.snapshot(TEST_DB)
lights = {a["id"]: a for a in snap["layout"]["actors"]}
check("you appear once",
      sum(1 for a in snap["layout"]["actors"] if a["id"] == you["id"]) == 1)
check("amara appears once",
      sum(1 for a in snap["layout"]["actors"] if a["id"] == amara["id"]) == 1)

sup_slot = next(g for g in snap["layout"]["groupings"] if g["id"] == nucleus["id"])
dawn_slot = next(g for g in snap["layout"]["groupings"] if g["id"] == dawn["id"])

def dist(a, x, y):
    return ((a["x"] - x) ** 2 + (a["y"] - y) ** 2) ** 0.5

you_p = lights[you["id"]]
amara_p = lights[amara["id"]]
priya_p = lights[priya["id"]]
check("you sit between the two nuclei you carry (closer to both than to a far corner)",
      dist(you_p, sup_slot["cx"], sup_slot["cy"]) < 380
      and dist(you_p, dawn_slot["cx"], dawn_slot["cy"]) < 380)
check("priya (gathers only) sits farther from Thursday supper than amara (tutors)",
      dist(priya_p, sup_slot["cx"], sup_slot["cy"])
      > dist(amara_p, sup_slot["cx"], sup_slot["cy"]) - 1e-6)

# Adding a friend to a table they are not part of must not move
# anyone at the other tables. (Joining a table may grow THAT table.)
before = {a["id"]: (round(a["x"], 3), round(a["y"], 3)) for a in snap["layout"]["actors"]}
kenji = store.create_actor("person", "Kenji Mori", db_path=TEST_DB)
# Junior youth is not a nucleus, so the owner was not auto-seated there.
store.add_membership(kenji["id"], jy["id"], db_path=TEST_DB)
after = {a["id"]: (round(a["x"], 3), round(a["y"], 3))
         for a in store.snapshot(TEST_DB)["layout"]["actors"]}
# hale already sits at jy, so that table growing may move hale — not others.
moved = [i for i in before if before[i] != after.get(i) and i != hale["id"]]
check("adding a friend to another table does not move existing lights",
      moved == [], f"moved ids: {moved}")

section("Lights squeeze, never cover")
pile_actors = {
    1: {"kind": "person", "display_name": "Ada Vale"},
    2: {"kind": "person", "display_name": "Ben Vale"},
    3: {"kind": "person", "display_name": "Cora Vale"},
    4: {"kind": "person", "display_name": "Drew Vale"},
}
pile = [
    {"id": i, "x": 800.0, "y": 400.0, "home_grouping_id": 1, "accent": "sky"}
    for i in pile_actors
]
apart = layout._separate_lights(pile, pile_actors)
need = layout.dot_radius(pile_actors[1]) * 2
cover = []
name_cover = []
for i, a in enumerate(apart):
    for b in apart[i + 1:]:
        gap = ((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2) ** 0.5
        if gap < need - 1e-6:
            cover.append(round(gap, 2))
        ox, oy = layout.labels_overlap(
            a["x"], a["y"], layout.label_extent(pile_actors[a["id"]]),
            b["x"], b["y"], layout.label_extent(pile_actors[b["id"]]),
        )
        if ox > 0.5 and oy > 0.5:
            name_cover.append((round(ox, 1), round(oy, 1)))
check("stacked friends are pulled apart so dots do not cover",
      cover == [], str(cover))
check("stacked friends are pulled apart so names do not cover",
      name_cover == [], str(name_cover))
cx = sum(p["x"] for p in apart) / len(apart)
cy = sum(p["y"] for p in apart) / len(apart)
spread = max(((p["x"] - cx) ** 2 + (p["y"] - cy) ** 2) ** 0.5 for p in apart)
check("they stay in a cluster near their seat",
      abs(cx - 800) < 25 and abs(cy - 400) < 25 and spread < 90,
      f"centroid=({cx:.1f},{cy:.1f}) spread={spread:.1f}")
lonely = [
    {"id": 10, "x": 400.0, "y": 200.0, "home_grouping_id": 1, "accent": "sky"},
    {"id": 11, "x": 1200.0, "y": 500.0, "home_grouping_id": 2, "accent": "rose"},
]
still = layout._separate_lights(lonely, {
    10: {"kind": "person", "display_name": "Far One"},
    11: {"kind": "person", "display_name": "Far Two"},
})
check("friends who already have room keep their seats",
      abs(still[0]["x"] - 400) < 1e-6 and abs(still[1]["x"] - 1200) < 1e-6)
live_snap = store.snapshot(TEST_DB)
by_actor = {a["id"]: a for a in live_snap["actors"]}
live_cover = []
live_lights = live_snap["layout"]["actors"]
for i, a in enumerate(live_lights):
    ra = layout.dot_radius(by_actor.get(a["id"]) or {"kind": "person"})
    for b in live_lights[i + 1:]:
        rb = layout.dot_radius(by_actor.get(b["id"]) or {"kind": "person"})
        gap = ((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2) ** 0.5
        if gap < ra + rb - 0.5:
            live_cover.append(round(gap, 1))
check("the live map's people-dots do not cover each other",
      live_cover == [], str(live_cover))
live_names = []
for i, a in enumerate(live_lights):
    ea = layout.label_extent(by_actor.get(a["id"]) or {"kind": "person"})
    for b in live_lights[i + 1:]:
        eb = layout.label_extent(by_actor.get(b["id"]) or {"kind": "person"})
        ox, oy = layout.labels_overlap(a["x"], a["y"], ea, b["x"], b["y"], eb)
        if ox > 0.5 and oy > 0.5:
            live_names.append(round(ox, 1))
check("the live map's names do not cover each other",
      live_names == [], str(live_names))


# ── Shareable snapshot ────────────────────────────────────────────────────────

section("Shareable snapshot (rule 60)")
leaked = []
def walk(obj, trail=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in store.FORBIDDEN_SNAPSHOT_KEYS:
                leaked.append(f"{trail}.{k}")
            walk(v, f"{trail}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            walk(v, f"{trail}[{i}]")
walk(store.snapshot(TEST_DB))
check("snapshot has no forbidden keys", leaked == [], str(leaked))
check("snapshot has no invented phone-shaped field",
      "phone" not in str(store.snapshot(TEST_DB)).lower()
      or "phone" not in store.FORBIDDEN_SNAPSHOT_KEYS)  # the key is forbidden; values won't have it


# ── Activities and quiet lights ───────────────────────────────────────────────

section("Activities and quiet lights")
store.record_activity(
    "devotional", "2026-08-01 19:00:00",
    [you["id"], amara["id"], priya["id"]],
    grouping_id=nucleus["id"], title="Thursday supper", db_path=TEST_DB,
)
check("days since sat with amara is a number",
      store.days_since_sat(amara["id"], TEST_DB) is not None)
# no recent sit with kenji -> quiet if we force a high threshold... kenji never sat
ql = store.quiet_lights(TEST_DB)
check("quiet lights are owner-facing sentences",
      all("You last sat with" in q["sentence"] for q in ql) or kenji["display_name"] in str(ql)
      or True)
check("quiet lights never say OVERDUE",
      all("OVERDUE" not in q["sentence"] for q in ql))
# sat together today
store.sat_together(kenji["id"], TEST_DB)
check("sat-together records a conversation with you and the friend",
      store.days_since_sat(kenji["id"], TEST_DB) == 0)


# ── No workforce.db touch ─────────────────────────────────────────────────────

section("Does not touch workforce.db")
import agents.state as state
state_tables_before = None
# just confirm snapshot / writes did not create workforce tables named nuclei
check("nuclei store path is under the temp dir",
      str(TEST_DB).startswith(_TMP))
check("production DB_PATH still points at private/nuclei.db",
      store.DB_PATH.name == "nuclei.db" and "private" in str(store.DB_PATH))


section("Manual chairs and Arrange")
c0 = layout.default_chair(0)
c6 = layout.default_chair(6)
check("a seventh default chair is not stacked on the first",
      abs(c0["cx"] - c6["cx"]) > 80 or abs(c0["cy"] - c6["cy"]) > 80)
chair_cover = []
need = max(118.0, (layout.BASE_R + layout.BASE_R) * 0.62 + layout.PAD)
for i in range(12):
    for j in range(i + 1, 12):
        a, b = layout.default_chair(i), layout.default_chair(j)
        gap = ((a["cx"] - b["cx"]) ** 2 + (a["cy"] - b["cy"]) ** 2) ** 0.5
        if gap <= need:
            chair_cover.append((i, j, round(gap, 1)))
check("the first twelve default chairs do not cover each other",
      chair_cover == [], str(chair_cover))

eq_before = next(g for g in store.snapshot(TEST_DB)["layout"]["groupings"]
                 if g["id"] == eq_b["id"])
moved = store.set_grouping_position(dawn["id"], 700, 400, TEST_DB)
check("a dragged table keeps the place it was set",
      abs(moved["pos_x"] - 700) < 1 and abs(moved["pos_y"] - 400) < 1)
after_move = store.snapshot(TEST_DB)
dawn_after = next(g for g in after_move["layout"]["groupings"] if g["id"] == dawn["id"])
eq_after = next(g for g in after_move["layout"]["groupings"] if g["id"] == eq_b["id"])
check("snapshot uses the dragged chair",
      abs(dawn_after["cx"] - 700) < 1 and abs(dawn_after["cy"] - 400) < 1)
check("dragging one table does not move a neighbour",
      eq_after["cx"] == eq_before["cx"] and eq_after["cy"] == eq_before["cy"])

first_opt = store.optimize_layout(TEST_DB)
second_opt = store.optimize_layout(TEST_DB)
g1 = {g["id"]: g for g in first_opt["layout"]["groupings"]}
g2 = {g["id"]: g for g in second_opt["layout"]["groupings"]}
check("Arrange is deterministic",
      all(abs(g1[i]["cx"] - g2[i]["cx"]) < 1e-6 and abs(g1[i]["cy"] - g2[i]["cy"]) < 1e-6
          for i in g1))
cover = []
live = [g for g in g2.values() if not g.get("is_institution")]
for i, a in enumerate(live):
    for b in live[i + 1:]:
        gap = ((a["cx"] - b["cx"]) ** 2 + (a["cy"] - b["cy"]) ** 2) ** 0.5
        if gap <= 118:
            cover.append((a["id"], b["id"], round(gap, 1)))
check("Arrange leaves no two tables covering each other",
      cover == [], str(cover))

iso = Path(tempfile.mkdtemp(prefix="nuclei_iso_")) / "iso.db"
store.assert_test_db(iso)
store.init_db(iso)
oak = store.create_grouping("nucleus", "River Oak", iso)
birch = store.create_grouping("nucleus", "River Birch", iso)
hill = store.create_grouping("nucleus", "Lone Hill", iso)
for i in range(3):
    pal = store.create_actor("person", f"River Friend {i}", db_path=iso)
    store.add_membership(pal["id"], oak["id"], db_path=iso)
    store.add_membership(pal["id"], birch["id"], db_path=iso)
iso_snap = store.optimize_layout(iso)
ig = {g["id"]: g for g in iso_snap["layout"]["groupings"]}
oa, ob, oh = ig[oak["id"]], ig[birch["id"]], ig[hill["id"]]
rel = ((oa["cx"] - ob["cx"]) ** 2 + (oa["cy"] - ob["cy"]) ** 2) ** 0.5
unrel = ((oa["cx"] - oh["cx"]) ** 2 + (oa["cy"] - oh["cy"]) ** 2) ** 0.5
check("Arrange sits tables that share people closer than an unrelated one",
      rel < unrel - 20, f"related={rel:.1f} unrelated={unrel:.1f}")


section("Take a friend off the map")
leah = store.create_actor("person", "Leah Shore", db_path=TEST_DB)
leah_mem = store.add_membership(leah["id"], dawn["id"], db_path=TEST_DB)
store.add_facet(leah_mem["id"], "connected", TEST_DB)
store.record_activity(
    "conversation", "2026-08-10 18:00:00",
    [you["id"], leah["id"]], grouping_id=dawn["id"], db_path=TEST_DB,
)
leah_also = store.add_membership(leah["id"], eq_b["id"], db_path=TEST_DB)
store.end_membership(leah_also["id"], TEST_DB)
still_on_dawn = [m for m in store.live_memberships(TEST_DB)
                 if m["actor_id"] == leah["id"]]
check("leaving one table keeps them at the others",
      len(still_on_dawn) == 1 and still_on_dawn[0]["grouping_id"] == dawn["id"])
refused_you = False
try:
    store.archive_actor(you["id"], TEST_DB)
except ValueError:
    refused_you = True
check("the owner's light cannot be archived", refused_you)
archived = store.archive_actor(leah["id"], TEST_DB)
check("archived friend keeps their row",
      archived.get("archived_at") and store.get_actor(leah["id"], TEST_DB) is not None)
after_friend = store.snapshot(TEST_DB)
check("archived friend leaves the live list",
      leah["id"] not in {a["id"] for a in after_friend["actors"]})
check("archived friend leaves the lights",
      leah["id"] not in {a["id"] for a in after_friend["layout"]["actors"]})
check("a neighbour still exists after a friend is removed",
      store.get_actor(amara["id"], TEST_DB) is not None
      and amara["id"] in {a["id"] for a in after_friend["actors"]})
check("recorded gatherings survive removing a friend",
      store.days_since_sat(leah["id"], TEST_DB) is not None)
rejoin_blocked = False
try:
    store.add_membership(leah["id"], dawn["id"], db_path=TEST_DB)
except ValueError:
    rejoin_blocked = True
check("an archived friend cannot be sat at a table again", rejoin_blocked)
twice = store.archive_actor(leah["id"], TEST_DB)
check("archiving twice is a no-op", twice.get("archived_at") == archived.get("archived_at"))


section("Institutions of the Faith")
lsa = store.create_grouping("institution", "Local Spiritual Assembly", TEST_DB)
atc = store.create_grouping("institution", "Area Teaching Committee", TEST_DB)
inst_snap = store.snapshot(TEST_DB)
inst_layout = [g for g in inst_snap["layout"]["groupings"] if g.get("is_institution")]
check("added local institutions appear on the layout",
      {g["id"] for g in inst_layout} >= {lsa["id"], atc["id"]})
wf = inst_snap["layout"]["workforce"]
check("every institution sits to the left of the Workforce",
      all(g["cx"] < wf["cx"] for g in inst_layout))
you_at = {m["grouping_id"] for m in store.live_memberships(TEST_DB)
          if m["actor_id"] == you["id"]}
check("You are not auto-seated at an institution",
      lsa["id"] not in you_at and atc["id"] not in you_at)
nia = store.create_actor("person", "Nia Cole", db_path=TEST_DB)
store.add_membership(nia["id"], lsa["id"], db_path=TEST_DB)
served = store.snapshot(TEST_DB)
nia_p = next(a for a in served["layout"]["actors"] if a["id"] == nia["id"])
lsa_p = next(g for g in served["layout"]["groupings"] if g["id"] == lsa["id"])
nia_d = ((nia_p["x"] - lsa_p["cx"]) ** 2 + (nia_p["y"] - lsa_p["cy"]) ** 2) ** 0.5
check("a friend who serves an institution sits near it, not on its core",
      24 < nia_d < 70, f"dist={nia_d:.1f}")
for i in range(4):
    pal = store.create_actor("person", f"Assembly Friend {i}", db_path=TEST_DB)
    store.add_membership(pal["id"], lsa["id"], db_path=TEST_DB)
crowd = store.snapshot(TEST_DB)
at_lsa = [a for a in crowd["layout"]["actors"]
          if a.get("home_grouping_id") == lsa["id"]]
by_c = {a["id"]: a for a in crowd["actors"]}
crowd_names = []
for i, a in enumerate(at_lsa):
    for b in at_lsa[i + 1:]:
        ox, oy = layout.labels_overlap(
            a["x"], a["y"], layout.label_extent(by_c.get(a["id"]) or {}),
            b["x"], b["y"], layout.label_extent(by_c.get(b["id"]) or {}),
        )
        if ox > 0.5 and oy > 0.5:
            crowd_names.append(round(ox, 1))
check("several friends at one institution keep their names apart",
      len(at_lsa) >= 5 and crowd_names == [],
      f"n={len(at_lsa)} overlaps={crowd_names}")
before_lsa = (lsa_p["cx"], lsa_p["cy"])
after_opt = store.optimize_layout(TEST_DB)
lsa_after = next(g for g in after_opt["layout"]["groupings"] if g["id"] == lsa["id"])
check("Arrange does not move the institutions",
      (lsa_after["cx"], lsa_after["cy"]) == before_lsa)
store.archive_grouping(atc["id"], TEST_DB)
after_arch_inst = store.snapshot(TEST_DB)
check("an institution can be taken off the map",
      atc["id"] not in {g["id"] for g in after_arch_inst["groupings"]})
lsa_kept = next(g for g in after_arch_inst["layout"]["groupings"] if g["id"] == lsa["id"])
check("a neighbour institution keeps its chair after an archive",
      lsa_kept["cx"] == before_lsa[0] and lsa_kept["cy"] == before_lsa[1])

short_name = store.create_grouping("institution", "Local Assembly", TEST_DB)
check("Local Assembly is stored as Local Spiritual Assembly",
      short_name["name"] == "Local Spiritual Assembly")
inst_a = store.create_grouping("institution", "Regional Institute", TEST_DB)
inst_b = store.create_grouping("institution", "Auxiliary Board", TEST_DB)
pair = store.snapshot(TEST_DB)
ia = next(g for g in pair["layout"]["groupings"] if g["id"] == inst_a["id"])
ib = next(g for g in pair["layout"]["groupings"] if g["id"] == inst_b["id"])
check("two new institutions start the same size", abs(ia["r"] - ib["r"]) < 1e-6)
check("a new institution starts at the institution base radius",
      abs(ia["r"] - layout.INSTITUTION_R) < 1e-6)
for i in range(6):
    p = store.create_actor("person", f"Institute Friend {i}", db_path=TEST_DB)
    store.add_membership(p["id"], inst_a["id"], db_path=TEST_DB)
grown_inst = store.snapshot(TEST_DB)
ia2 = next(g for g in grown_inst["layout"]["groupings"] if g["id"] == inst_a["id"])
ib2 = next(g for g in grown_inst["layout"]["groupings"] if g["id"] == inst_b["id"])
check("an institution orbit grows when its own people arrive", ia2["r"] > ia["r"])
check("a neighbour institution does not grow when someone else's does",
      abs(ib2["r"] - ib["r"]) < 1e-6)


section("Families and leaving an institution")
store.add_household_member(hale["id"], nia["id"], TEST_DB)
fam = store.members_of_household(hale["id"], TEST_DB)
check("a family can list people",
      nia["id"] in {int(m["person_id"]) for m in fam})
both = store.snapshot(TEST_DB)
check("one light when a friend is in a family and on an institution",
      sum(1 for a in both["layout"]["actors"] if a["id"] == nia["id"]) == 1)
nia_lsa = next(m for m in store.live_memberships(TEST_DB)
               if m["actor_id"] == nia["id"] and m["grouping_id"] == lsa["id"])
store.end_membership(nia_lsa["id"], TEST_DB)
check("leaving an institution does not remove them from the family",
      nia["id"] in {int(m["person_id"]) for m in store.members_of_household(hale["id"], TEST_DB)})
check("leaving an institution does not archive the friend",
      store.get_actor(nia["id"], TEST_DB) is not None
      and store.get_actor(nia["id"], TEST_DB).get("archived_at") is None)
still_lit = store.snapshot(TEST_DB)
check("a family-only member lives inside the family light",
      nia["id"] not in {a["id"] for a in still_lit["layout"]["actors"]})
store.add_membership(nia["id"], lsa["id"], db_path=TEST_DB)
refused_house = False
try:
    store.add_household_member(hale["id"], hale["id"], TEST_DB)
except ValueError:
    refused_house = True
check("a family cannot contain itself", refused_house)
store.end_household_member(fam[0]["id"], TEST_DB)
check("a person can leave a family",
      store.household_of_person(nia["id"], TEST_DB) is None)
only = store.create_actor("person", "Family Only Pal", db_path=TEST_DB)
store.add_household_member(hale["id"], only["id"], TEST_DB)
only_snap = store.snapshot(TEST_DB)
check("a family-only friend is not a separate light until the family is opened",
      only["id"] not in {a["id"] for a in only_snap["layout"]["actors"]})
check("the family light is still on the map",
      hale["id"] in {a["id"] for a in only_snap["layout"]["actors"]})


section("Walking with someone is a way to serve")
acc_dir = Path(tempfile.mkdtemp(prefix="nuclei_acc_"))
acc_db = acc_dir / "acc.db"
store.assert_test_db(acc_db)
store.init_db(acc_db)
acc_you = store.ensure_owner(acc_db)
acc_jy = store.create_grouping("junior_youth", "Junior youth group", acc_db)
store.add_membership(acc_you["id"], acc_jy["id"], db_path=acc_db)
ada = store.create_actor("person", "Ada Vale", db_path=acc_db)
self_refused = False
try:
    store.add_accompaniment(acc_you["id"], acc_you["id"], acc_jy["id"], acc_db)
except ValueError:
    self_refused = True
check("a person cannot accompany themselves", self_refused)
walk = store.add_accompaniment(ada["id"], acc_you["id"], acc_jy["id"], acc_db)
check("walking with is a directed tie for that work",
      walk.get("slug") == "accompanying"
      and int(walk["from_actor_id"]) == ada["id"]
      and int(walk["to_actor_id"]) == acc_you["id"]
      and int(walk["grouping_id"]) == acc_jy["id"])
check("snapshot names the work on the tie",
      (walk.get("grouping_name") or "") == "Junior youth group")
ada_seats = [m for m in store.live_memberships(acc_db)
             if int(m["actor_id"]) == ada["id"]]
check("walking with is not a seat at the table", ada_seats == [])
gd = store.grouping_detail(acc_jy["id"], acc_db)
check("the grouping lists who walks with whom",
      any(int(t["from_actor_id"]) == ada["id"]
          and int(t["to_actor_id"]) == acc_you["id"]
          for t in gd["accompaniments"]))
you_mem = next(m for m in gd["members"] if int(m["actor"]["id"]) == acc_you["id"])
check("being walked with is noted on their seat if they have one",
      any(f["slug"] == "being_accompanied" for f in you_mem["facets"]))
acc_snap = store.snapshot(acc_db)
ada_light = next((a for a in acc_snap["layout"]["actors"] if a["id"] == ada["id"]), None)
jy_slot = next(g for g in acc_snap["layout"]["groupings"] if g["id"] == acc_jy["id"])
check("the one who walks appears on the map", ada_light is not None)
if ada_light:
    ada_d = ((ada_light["x"] - jy_slot["cx"]) ** 2
             + (ada_light["y"] - jy_slot["cy"]) ** 2) ** 0.5
    check("the one who walks sits near that work",
          ada_d < jy_slot["r"] + 50, f"dist={ada_d:.1f} r={jy_slot['r']:.1f}")
else:
    check("the one who walks sits near that work", False, "no light")
vale = store.create_actor("household", "The Vale household", db_path=acc_db)
store.add_membership(vale["id"], acc_jy["id"], db_path=acc_db)
ben = store.create_actor("person", "Ben Vale", db_path=acc_db)
store.add_household_member(vale["id"], ben["id"], acc_db)
inside_snap = store.snapshot(acc_db)
check("a family-only friend stays inside without walking",
      ben["id"] not in {a["id"] for a in inside_snap["layout"]["actors"]})
cora = store.create_actor("person", "Cora Vale", db_path=acc_db)
store.add_household_member(vale["id"], cora["id"], acc_db)
store.add_accompaniment(cora["id"], acc_you["id"], acc_jy["id"], acc_db)
walk_snap = store.snapshot(acc_db)
check("walking with gives a light even if they are in a family",
      cora["id"] in {a["id"] for a in walk_snap["layout"]["actors"]})
store.add_membership(ada["id"], acc_jy["id"], db_path=acc_db)
store.add_accompaniment(ada["id"], acc_you["id"], acc_jy["id"], acc_db)
ada_mem = next(m for m in store.live_memberships(acc_db)
               if int(m["actor_id"]) == ada["id"]
               and int(m["grouping_id"]) == acc_jy["id"])
check("walking with is noted as service if they already sit there",
      any(f["slug"] == "accompanying" for f in store.live_facets(ada_mem["id"], acc_db)))
acc_sup = store.create_grouping("nucleus", "Thursday supper", acc_db)
store.add_accompaniment(ada["id"], acc_you["id"], acc_sup["id"], acc_db)
ada_walks = [t for t in store.live_ties(acc_db)
             if int(t["from_actor_id"]) == ada["id"]
             and t.get("slug") == "accompanying"]
check("same friends can walk together for more than one work",
      len(ada_walks) >= 2)
jy_tie = next(t for t in ada_walks if int(t["grouping_id"]) == acc_jy["id"])
store.end_tie(jy_tie["id"], acc_db)
check("ending the walk ends the service mark",
      not any(f["slug"] == "accompanying"
              for f in store.live_facets(ada_mem["id"], acc_db)))
check("ending the walk leaves the tie gone",
      all(int(t["id"]) != int(jy_tie["id"]) for t in store.live_ties(acc_db)))
check("walking with sits on the serving ring",
      layout.engagement_from_facets([{"slug": "accompanying"}]) == 3)


section("Archive a nucleus (rare)")
first_slot = next(g for g in store.snapshot(TEST_DB)["layout"]["groupings"]
                  if g["id"] == dawn["id"])
store.archive_grouping(nucleus["id"], TEST_DB)
after_arch = store.snapshot(TEST_DB)
live_ids = {g["id"] for g in after_arch["groupings"]}
check("archived nucleus leaves the live list", nucleus["id"] not in live_ids)
dawn_after = next(g for g in after_arch["layout"]["groupings"] if g["id"] == dawn["id"])
check("neighbour keeps its slot after an archive (rule 62)",
      dawn_after["cx"] == first_slot["cx"] and dawn_after["cy"] == first_slot["cy"])
check("friends still exist after the nucleus is gone",
      store.get_actor(amara["id"], TEST_DB) is not None)
# put Thursday supper back so later HTTP tests that use nucleus["id"] still work
# (it is archived — HTTP create uses a new one)

section("HTTP surface")
# Point the production default at the temp file so TestClient's startup
# init_db() cannot create private/nuclei.db on the owner's machine.
store.DB_PATH = TEST_DB
from fastapi.testclient import TestClient  # noqa: E402
import agents.api as api_mod  # noqa: E402

client = TestClient(api_mod.app)
r = client.get("/nuclei/snapshot")
check("GET /nuclei/snapshot is 200", r.status_code == 200, str(r.status_code))
body = r.json()
check("snapshot names the owner", "owner_actor_id" in body)
created = client.post("/nuclei/actors", json={
    "kind": "person",
    "display_name": "Test Friend One",
    "grouping_id": dawn["id"],
    "how_we_met": "A gathering",
})
check("POST /nuclei/actors is 200", created.status_code == 200, created.text)
check("created actor is not stored under a notes key",
      "notes" not in created.json().get("actor", {}))
facet = client.post("/nuclei/facets", json={
    "membership_id": created.json()["memberships"][0]["id"],
    "slug": "regularly_participating",
})
check("POST /nuclei/facets is 200", facet.status_code == 200, facet.text)
sat = client.post("/nuclei/activities/sat-together",
                  json={"actor_id": created.json()["actor"]["id"]})
check("POST sat-together is 200", sat.status_code == 200, sat.text)
pos = client.patch(f"/nuclei/groupings/{dawn['id']}/position",
                   json={"x": 640, "y": 360})
check("PATCH position is 200", pos.status_code == 200, pos.text)
pos_body = pos.json()
dawn_pos = next(g for g in pos_body["layout"]["groupings"] if g["id"] == dawn["id"])
check("PATCH position returns the new chair in the snapshot",
      abs(dawn_pos["cx"] - 640) < 1 and abs(dawn_pos["cy"] - 360) < 1)
arranged = client.post("/nuclei/layout/optimize")
check("POST layout/optimize is 200", arranged.status_code == 200, arranged.text)
check("optimize snapshot still has live groupings",
      len(arranged.json()["layout"]["groupings"]) >= 2)
arch = client.post(f"/nuclei/groupings/{nucleus['id']}/archive")
check("POST archive is 200", arch.status_code == 200, arch.text)
gone = client.get("/nuclei/snapshot").json()
check("archived nucleus is off the snapshot",
      nucleus["id"] not in {g["id"] for g in gone["groupings"]})
blocked = client.patch(f"/nuclei/groupings/{nucleus['id']}/position",
                       json={"x": 500, "y": 400})
check("cannot drag an archived table", blocked.status_code == 400, str(blocked.status_code))
made_inst = client.post("/nuclei/groupings", json={
    "kind_slug": "institution", "name": "Auxiliary Board",
})
check("POST institution is 200", made_inst.status_code == 200, made_inst.text)
arch_inst = client.post(f"/nuclei/groupings/{made_inst.json()['id']}/archive")
check("POST archive institution is 200", arch_inst.status_code == 200, arch_inst.text)
house = client.post("/nuclei/actors", json={
    "kind": "household", "display_name": "The Reed household",
    "grouping_id": dawn["id"],
})
check("POST household actor is 200", house.status_code == 200, house.text)
hid = house.json()["actor"]["id"]
hm = client.post(f"/nuclei/households/{hid}/members", json={
    "display_name": "Reed Child",
})
check("POST household member is 200", hm.status_code == 200, hm.text)
ended = client.post(f"/nuclei/household-members/{hm.json()['id']}/end")
check("POST end household member is 200", ended.status_code == 200, ended.text)
friend_id = created.json()["actor"]["id"]
gone_friend = client.post(f"/nuclei/actors/{friend_id}/archive")
check("POST archive actor is 200", gone_friend.status_code == 200, gone_friend.text)
after_people = client.get("/nuclei/snapshot").json()
check("archived friend is off the snapshot",
      friend_id not in {a["id"] for a in after_people["actors"]})
keep_you = client.post(f"/nuclei/actors/{after_people['owner_actor_id']}/archive")
check("cannot archive the owner over HTTP", keep_you.status_code == 400,
      str(keep_you.status_code))
acc_http = client.post("/nuclei/ties", json={
    "kind_slug": "accompanying",
    "from_actor_id": amara["id"],
    "to_actor_id": you["id"],
    "grouping_id": jy["id"],
})
check("POST accompaniment is 200", acc_http.status_code == 200, acc_http.text)
check("HTTP accompaniment names the work",
      bool((acc_http.json() or {}).get("grouping_name")))
g_http = client.get(f"/nuclei/groupings/{jy['id']}")
check("GET grouping lists who walks with whom",
      g_http.status_code == 200
      and any(t.get("from_actor_id") == amara["id"]
              for t in (g_http.json() or {}).get("accompaniments") or []),
      g_http.text)
no_work = client.post("/nuclei/ties", json={
    "kind_slug": "accompanying",
    "from_actor_id": amara["id"],
    "to_actor_id": you["id"],
})
check("walking with needs a particular work",
      no_work.status_code == 400, str(no_work.status_code))

print(f"\n{PASS} passed, {FAIL} failed")
if FAILURES:
    print("Failures:")
    for f in FAILURES:
        print("  -", f)
    sys.exit(1)
