import { useEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import { Maximize2, Minus, Plus } from "lucide-react";
import type { ColonySnapshot, NucleiSnapshot } from "../../lib/types";
import { getColonyUi, patchColonyUi } from "../../lib/settings";
import { accentFor, agentLabel } from "../../lib/utils";
import { morphStyle, TILT, VIEW_H, VIEW_W, WORKFORCE_ANCHOR } from "./layout";

const ZOOM_MIN = 0.55;
const ZOOM_MAX = 3.2;

function institutionLines(name: string): string[] {
  if (name === "Local Assembly" || name === "Local Spiritual Assembly") {
    return ["Local Spiritual", "Assembly"];
  }
  if (name === "Area Teaching Committee") return ["Area Teaching", "Committee"];
  if (name.length > 24) return [`${name.slice(0, 22)}…`];
  return [name];
}

interface Props {
  snapshot: NucleiSnapshot;
  /** The Digital World's own snapshot — the workforce light opens into it. */
  colony?: ColonySnapshot;
  selectedActor: number | null;
  selectedGrouping: number | null;
  workforceOpen: boolean;
  /** false while the world-swap is folding this sky into the workforce light. */
  expanded?: boolean;
  onSelectActor: (id: number | null) => void;
  onSelectGrouping: (id: number | null) => void;
  onSelectWorkforce: () => void;
  onMoveGrouping: (id: number, x: number, y: number) => void | Promise<unknown>;
}

/** One body on the ring that opens out of the workforce light. */
type WorkforcePetal = {
  key: string;
  label: string;
  /** An agent has no dot on this map; a real person may already have one. */
  human: boolean;
  actorId?: number;
  live?: boolean;
  paused?: boolean;
  accent: string;
};

type Drag = {
  id: number;
  pointerId: number;
  startX: number;
  startY: number;
  origCx: number;
  origCy: number;
  lastCx: number;
  lastCy: number;
  moved: boolean;
};

type Cam = { k: number; x: number; y: number };
type Pan = {
  pointerId: number;
  startX: number;
  startY: number;
  origX: number;
  origY: number;
};

function clamp(n: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, n));
}

function clampCam(k: number, x: number, y: number): Cam {
  k = clamp(k, ZOOM_MIN, ZOOM_MAX);
  if (k >= 1) {
    x = clamp(x, VIEW_W * (1 - k), 0);
    y = clamp(y, VIEW_H * (1 - k), 0);
  } else {
    const cx = VIEW_W * (1 - k) / 2;
    const cy = VIEW_H * (1 - k) / 2;
    x = clamp(x, cx - 80, cx + 80);
    y = clamp(y, cy - 80, cy + 80);
  }
  return { k, x, y };
}

function loadCam(): Cam {
  const s = getColonyUi();
  return clampCam(s.rwScale ?? 1, s.rwPanX ?? 0, s.rwPanY ?? 0);
}

/**
 * Where the workforce light sits in VIEWBOX units right now — its fixed world
 * position pushed through this sky's saved camera. The Digital World has no
 * camera of its own, so it is handed this number to fold onto; without it a
 * panned or zoomed Real World would hand back a dot in the wrong place.
 */
export function workforceScreenAnchor(
  wf: { cx: number; cy: number },
): { cx: number; cy: number } {
  const c = loadCam();
  return { cx: c.x + wf.cx * c.k, cy: c.y + wf.cy * c.k };
}

export function RealWorldGraph({
  snapshot, colony, selectedActor, selectedGrouping, workforceOpen,
  expanded = true,
  onSelectActor, onSelectGrouping, onSelectWorkforce, onMoveGrouping,
}: Props) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const dragRef = useRef<Drag | null>(null);
  const panRef = useRef<Pan | null>(null);
  const camRef = useRef<Cam>(loadCam());
  const [hover, setHover] = useState<{ kind: "actor" | "grouping"; id: number } | null>(null);
  const [overrides, setOverrides] = useState<Record<number, { cx: number; cy: number }>>({});
  const [draggingId, setDraggingId] = useState<number | null>(null);
  const [panning, setPanning] = useState(false);
  const [cam, setCam] = useState<Cam>(() => camRef.current);
  const [bloomIds, setBloomIds] = useState<number[]>([]);
  const [bloomOpen, setBloomOpen] = useState(false);

  // The sky starts folded on its FIRST painted frame and opens on the next, so
  // the grow-out runs whether this mount came from a world swap or from opening
  // the tab. Setting the open state in the same tick as the mount would give the
  // browser only the end state, and the transition would have nothing to run
  // from — the same reason the family bloom waits a frame.
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    const frame = requestAnimationFrame(() => setMounted(true));
    return () => cancelAnimationFrame(frame);
  }, []);
  const open = expanded && mounted;


  const actorsById = useMemo(() => {
    const m: Record<number, NucleiSnapshot["actors"][number]> = {};
    for (const a of snapshot.actors) m[a.id] = a;
    return m;
  }, [snapshot.actors]);
  const groupingsById = useMemo(() => {
    const m: Record<number, NucleiSnapshot["groupings"][number]> = {};
    for (const g of snapshot.groupings) m[g.id] = g;
    return m;
  }, [snapshot.groupings]);

  const placed = snapshot.layout.groupings.map((g) => {
    const o = overrides[g.id];
    return o ? { ...g, cx: o.cx, cy: o.cy } : g;
  });
  const lights = snapshot.layout.actors.map((p) => {
    if (p.home_grouping_id == null) return p;
    const o = overrides[p.home_grouping_id];
    const home = snapshot.layout.groupings.find((g) => g.id === p.home_grouping_id);
    if (!o || !home) return p;
    return { ...p, x: p.x + (o.cx - home.cx), y: p.y + (o.cy - home.cy) };
  });

  const seatedIds = useMemo(() => {
    const s = new Set<number>();
    for (const m of snapshot.memberships) s.add(m.actor_id);
    return s;
  }, [snapshot.memberships]);
  const familyMembers = snapshot.household_members ?? [];
  const selectedKind = selectedActor != null ? actorsById[selectedActor]?.kind : undefined;
  const groupingFamilyIds = useMemo(() => {
    if (selectedGrouping == null) return [] as number[];
    return snapshot.memberships
      .filter((m) => m.grouping_id === selectedGrouping)
      .map((m) => m.actor_id)
      .filter((id) => actorsById[id]?.kind === "household");
  }, [selectedGrouping, snapshot.memberships, actorsById]);
  const selectedGroupingMeta = selectedGrouping != null
    ? groupingsById[selectedGrouping] : undefined;
  const expandFromMembers = !!selectedGroupingMeta && (
    selectedGroupingMeta.kind_slug === "nucleus"
    || selectedGroupingMeta.kind_slug === "institution"
    || !!selectedGroupingMeta.is_nucleus
  );
  const accBloomHouses = useMemo(() => {
    const consider: number[] = [];
    const peopleHere = new Set<number>();
    if (expandFromMembers && selectedGrouping != null) {
      for (const m of snapshot.memberships) {
        if (m.grouping_id !== selectedGrouping) continue;
        peopleHere.add(m.actor_id);
        if (actorsById[m.actor_id]?.kind !== "household") continue;
        for (const fm of familyMembers) {
          if (fm.household_id === m.actor_id) peopleHere.add(fm.person_id);
        }
      }
    }
    for (const t of snapshot.ties) {
      if (t.slug !== "accompanying") continue;
      if (selectedGrouping != null && t.grouping_id === selectedGrouping) {
        consider.push(t.from_actor_id, t.to_actor_id);
      }
      if (expandFromMembers && (
        peopleHere.has(t.from_actor_id) || peopleHere.has(t.to_actor_id)
      )) {
        const throughYou = t.from_actor_id === snapshot.owner_actor_id
          || t.to_actor_id === snapshot.owner_actor_id;
        const nucleus = selectedGroupingMeta?.kind_slug === "nucleus"
          || !!selectedGroupingMeta?.is_nucleus;
        if (throughYou && nucleus && t.grouping_id !== selectedGrouping) {
          /* You sit at every nucleus — do not fan every walk out from one table. */
        } else {
          consider.push(t.from_actor_id, t.to_actor_id);
        }
      }
      if (selectedActor != null
          && (t.from_actor_id === selectedActor || t.to_actor_id === selectedActor)) {
        consider.push(
          t.from_actor_id === selectedActor ? t.to_actor_id : t.from_actor_id,
        );
      }
    }
    const ids: number[] = [];
    for (const pid of consider) {
      const fam = familyMembers.find((m) => m.person_id === pid);
      if (fam && !seatedIds.has(pid)) ids.push(fam.household_id);
    }
    return [...new Set(ids)];
  }, [selectedGrouping, selectedActor, snapshot.ties, snapshot.memberships,
    familyMembers, seatedIds, expandFromMembers, actorsById,
    snapshot.owner_actor_id, selectedGroupingMeta]);
  const wantedBloom = selectedKind === "household" && selectedActor != null
    ? [selectedActor]
    : [...new Set([...groupingFamilyIds, ...accBloomHouses])];
  const bloomKey = wantedBloom.slice().sort((a, b) => a - b).join(",");
  useEffect(() => {
    if (wantedBloom.length) {
      setBloomIds(wantedBloom);
      setBloomOpen(false);
      const frame = requestAnimationFrame(() => setBloomOpen(true));
      return () => cancelAnimationFrame(frame);
    }
    setBloomOpen(false);
    const t = window.setTimeout(() => setBloomIds([]), 420);
    return () => window.clearTimeout(t);
    // bloomKey captures the household set that should open.
  }, [bloomKey]);

  const bloomFamilyIds = new Set(
    familyMembers.filter((m) => bloomIds.includes(m.household_id)).map((m) => m.person_id),
  );

  // --- The workforce light opens the same way a family does (rule 65) --------
  // Its bodies are drawn on a fan to the RIGHT, away from the institution
  // column, on a rounder ellipse than the tables use: this is a panel opening
  // out, not another orbit lying on the ground plane, and a dozen names
  // squeezed onto a tilted ring would sit on top of each other.
  const wfPeople = snapshot.workforce_members ?? [];
  const wfPetals: WorkforcePetal[] = useMemo(() => {
    const rows: WorkforcePetal[] = [];
    for (const a of colony?.agents ?? []) {
      if (a.is_instrument) continue;
      rows.push({
        key: `agent-${a.name}`,
        label: agentLabel(a.name),
        human: false,
        live: a.live,
        paused: a.paused,
        accent: colony?.teams.find((t) => t.id === a.team)?.accent ?? "amber",
      });
    }
    for (const p of wfPeople) {
      rows.push({
        key: `person-${p.membership_id}`,
        label: p.display_name.split(" ")[0] || p.display_name,
        human: true,
        actorId: p.actor_id,
        accent: "amber",
      });
    }
    return rows;
  }, [colony, wfPeople]);

  const [wfOpen, setWfOpen] = useState(false);
  useEffect(() => {
    if (workforceOpen) {
      const frame = requestAnimationFrame(() => setWfOpen(true));
      return () => cancelAnimationFrame(frame);
    }
    setWfOpen(false);
    return undefined;
  }, [workforceOpen]);

  // A person already lit somewhere on this map keeps that light and is reached
  // by a thread — never redrawn as a second dot (rule 62), exactly as a family
  // member who already sits at a table is.
  const wfSeated = new Set(
    wfPeople.filter((p) => seatedIds.has(p.actor_id)).map((p) => p.actor_id),
  );
  const wfRing = wfPetals.filter((p) => !(p.actorId != null && wfSeated.has(p.actorId)));

  const insideOf = (houseId: number) =>
    familyMembers.filter((m) => m.household_id === houseId && !seatedIds.has(m.person_id));

  const petalOf = (houseId: number, personId: number): { x: number; y: number } | null => {
    const house = lights.find((p) => p.id === houseId);
    if (!house) return null;
    const inside = insideOf(houseId);
    const i = inside.findIndex((m) => m.person_id === personId);
    if (i < 0) return null;
    const n = inside.length;
    const r = 34 + n * 4;
    const ang = -Math.PI / 2 + (i / n) * Math.PI * 2;
    return { x: house.x + r * Math.cos(ang), y: house.y + r * TILT * Math.sin(ang) };
  };

  const actorPoint = (id: number): { x: number; y: number } | null => {
    const lit = lights.find((p) => p.id === id);
    if (lit) return { x: lit.x, y: lit.y };
    const fam = familyMembers.find((m) => m.person_id === id);
    if (!fam) return null;
    const petal = petalOf(fam.household_id, id);
    if (petal) return petal;
    const house = lights.find((p) => p.id === fam.household_id);
    return house ? { x: house.x, y: house.y } : null;
  };

  type FocusLink = { key: string; x1: number; y1: number; x2: number; y2: number; kind: string };
  const focusLinks: FocusLink[] = [];
  const connectedIds = new Set<number>();
  const pushLink = (x1: number, y1: number, x2: number, y2: number, kind: string, key: string) => {
    if (Math.hypot(x2 - x1, y2 - y1) < 6) return;
    focusLinks.push({ key, x1, y1, x2, y2, kind });
  };

  if (selectedActor != null) {
    connectedIds.add(selectedActor);
    const origin = actorPoint(selectedActor);
    if (origin) {
      for (const m of snapshot.memberships) {
        if (m.actor_id !== selectedActor) continue;
        const g = placed.find((p) => p.id === m.grouping_id);
        if (!g) continue;
        pushLink(origin.x, origin.y, g.cx, g.cy, "place", `place-${m.id}`);
      }
      for (const fm of familyMembers) {
        if (fm.person_id === selectedActor) {
          const house = actorPoint(fm.household_id);
          if (house) {
            connectedIds.add(fm.household_id);
            pushLink(origin.x, origin.y, house.x, house.y, "family", `fam-${fm.id}`);
          }
        }
        if (fm.household_id === selectedActor) {
          const dest = actorPoint(fm.person_id);
          if (dest) {
            connectedIds.add(fm.person_id);
            pushLink(origin.x, origin.y, dest.x, dest.y, "family", `fam-${fm.id}`);
          }
        }
      }
      for (const t of snapshot.ties) {
        const other = t.from_actor_id === selectedActor ? t.to_actor_id
          : t.to_actor_id === selectedActor ? t.from_actor_id : null;
        if (other == null) continue;
        const dest = actorPoint(other);
        if (!dest) continue;
        connectedIds.add(other);
        pushLink(origin.x, origin.y, dest.x, dest.y, t.slug, `tie-${t.id}`);
        // A walk is person-to-person. The work joins whoever actually
        // sits there — never a shortcut from the walker to the table.
        if (t.slug === "accompanying" && t.grouping_id != null) {
          const gg = placed.find((p) => p.id === t.grouping_id);
          if (!gg) continue;
          const seated = snapshot.memberships.filter((m) =>
            m.grouping_id === t.grouping_id
            && (m.actor_id === selectedActor || m.actor_id === other));
          if (seated.some((m) => m.actor_id === selectedActor)) continue;
          if (seated.length) {
            for (const m of seated) {
              const seat = actorPoint(m.actor_id);
              if (!seat) continue;
              connectedIds.add(m.actor_id);
              pushLink(seat.x, seat.y, gg.cx, gg.cy, "place", `acc-via-${t.id}-${m.actor_id}`);
            }
          } else {
            const hub = actorPoint(t.to_actor_id);
            if (!hub) continue;
            if (t.to_actor_id !== selectedActor) connectedIds.add(t.to_actor_id);
            pushLink(hub.x, hub.y, gg.cx, gg.cy, "place", `acc-work-${t.id}`);
          }
        }
      }
    }
  }
  if (selectedGrouping != null) {
    const g = placed.find((p) => p.id === selectedGrouping);
    if (g) {
      for (const m of snapshot.memberships) {
        if (m.grouping_id !== selectedGrouping) continue;
        const dest = actorPoint(m.actor_id);
        if (!dest) continue;
        connectedIds.add(m.actor_id);
        pushLink(g.cx, g.cy, dest.x, dest.y, "place", `gmem-${m.id}`);
        if (actorsById[m.actor_id]?.kind !== "household") continue;
        const house = dest;
        for (const fm of familyMembers) {
          if (fm.household_id !== m.actor_id) continue;
          const person = petalOf(fm.household_id, fm.person_id) ?? actorPoint(fm.person_id);
          if (!person) continue;
          connectedIds.add(fm.person_id);
          pushLink(house.x, house.y, person.x, person.y, "family", `gfam-${fm.id}`);
          pushLink(g.cx, g.cy, person.x, person.y, "place", `gperson-${fm.id}`);
        }
      }
      const memberIdSet = new Set(
        snapshot.memberships
          .filter((m) => m.grouping_id === selectedGrouping)
          .map((m) => m.actor_id),
      );
      const peopleHere: number[] = [];
      for (const id of memberIdSet) {
        peopleHere.push(id);
        if (actorsById[id]?.kind !== "household") continue;
        for (const fm of familyMembers) {
          if (fm.household_id === id) peopleHere.push(fm.person_id);
        }
      }
      const peopleHereSet = new Set(peopleHere);
      if (expandFromMembers) {
        // Keep going from each person: who they walk with, and other
        // tables they sit at. The clicked table does not jump to those
        // friends — the path is table -> person -> walk / other table.
        const youId = snapshot.owner_actor_id;
        const selectedIsNucleus = selectedGroupingMeta?.kind_slug === "nucleus"
          || !!selectedGroupingMeta?.is_nucleus;
        for (const pid of peopleHereSet) {
          const origin = actorPoint(pid);
          if (!origin) continue;
          // You sit at every nucleus by design. A table that reaches
          // you must not keep going to all your other tables.
          const stayPut = pid === youId && selectedIsNucleus;
          if (!stayPut) {
            for (const m of snapshot.memberships) {
              if (m.actor_id !== pid || m.grouping_id === selectedGrouping) continue;
              const other = placed.find((p) => p.id === m.grouping_id);
              if (!other) continue;
              pushLink(origin.x, origin.y, other.cx, other.cy, "place",
                `gmore-${pid}-${m.id}`);
            }
          }
          // A family is not a table seat. Someone on the Assembly who
          // belongs to a JY family still reaches that family by name.
          for (const fm of familyMembers) {
            if (fm.person_id !== pid) continue;
            const house = actorPoint(fm.household_id);
            if (!house) continue;
            connectedIds.add(fm.household_id);
            pushLink(origin.x, origin.y, house.x, house.y, "family",
              `ghouse-${fm.id}`);
          }
          for (const t of snapshot.ties) {
            if (t.slug !== "accompanying") continue;
            if (stayPut && t.grouping_id !== selectedGrouping) continue;
            const otherId = t.from_actor_id === pid ? t.to_actor_id
              : t.to_actor_id === pid ? t.from_actor_id : null;
            if (otherId == null) continue;
            const dest = actorPoint(otherId);
            if (!dest) continue;
            connectedIds.add(otherId);
            pushLink(origin.x, origin.y, dest.x, dest.y, "accompanying",
              `gwalk-${t.id}-${pid}`);
          }
        }
      } else {
        for (const t of snapshot.ties) {
          // JY and other work-tables: only walks recorded for THIS work.
          if (t.slug !== "accompanying" || t.grouping_id !== selectedGrouping) continue;
          const from = actorPoint(t.from_actor_id);
          const toP = actorPoint(t.to_actor_id);
          if (from) connectedIds.add(t.from_actor_id);
          if (toP) connectedIds.add(t.to_actor_id);
          if (from && toP) {
            pushLink(from.x, from.y, toP.x, toP.y, "accompanying", `gacc-pair-${t.id}`);
          }
          const fromMember = memberIdSet.has(t.from_actor_id);
          const toMember = memberIdSet.has(t.to_actor_id);
          if (!fromMember && !toMember && toP) {
            pushLink(g.cx, g.cy, toP.x, toP.y, "place", `gacc-hub-${t.id}`);
          }
        }
      }
    }
  }

  const wf = snapshot.layout.workforce;
  // Read from the snapshot rather than hardcoded, so the label on the map can
  // never disagree with what the row is actually called.
  const wfName = snapshot.groupings.find(
    (g) => g.id === snapshot.workforce_grouping_id,
  )?.name ?? "Bahá'í Workforce";
  const tables = placed.filter((g) => !g.is_institution);
  const institutions = placed.filter((g) => g.is_institution);
  const empty = snapshot.groupings.every((g) => g.kind_slug === "institution");
  const layoutSig = snapshot.layout.groupings
    .map((g) => `${g.id}:${g.cx.toFixed(1)}:${g.cy.toFixed(1)}`)
    .join("|");
  useEffect(() => {
    if (dragRef.current) return;
    setOverrides({});
  }, [layoutSig]);

  const applyCam = (next: Cam) => {
    const clean = clampCam(next.k, next.x, next.y);
    camRef.current = clean;
    setCam(clean);
    patchColonyUi({ rwScale: clean.k, rwPanX: clean.x, rwPanY: clean.y });
  };

  const clientToView = (clientX: number, clientY: number) => {
    const svg = svgRef.current;
    if (!svg) return { x: clientX, y: clientY };
    const ctm = svg.getScreenCTM();
    if (!ctm) return { x: clientX, y: clientY };
    const pt = svg.createSVGPoint();
    pt.x = clientX;
    pt.y = clientY;
    const p = pt.matrixTransform(ctm.inverse());
    return { x: p.x, y: p.y };
  };

  const clientToWorld = (clientX: number, clientY: number) => {
    const v = clientToView(clientX, clientY);
    const c = camRef.current;
    return { x: (v.x - c.x) / c.k, y: (v.y - c.y) / c.k };
  };

  const zoomAt = (viewX: number, viewY: number, factor: number) => {
    const c = camRef.current;
    const wx = (viewX - c.x) / c.k;
    const wy = (viewY - c.y) / c.k;
    const k = clamp(c.k * factor, ZOOM_MIN, ZOOM_MAX);
    applyCam({ k, x: viewX - wx * k, y: viewY - wy * k });
  };

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const v = clientToView(e.clientX, e.clientY);
      zoomAt(v.x, v.y, e.deltaY > 0 ? 0.9 : 1.11);
    };
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, []);

  const onPanPointerDown = (e: PointerEvent) => {
    if (dragRef.current) return;
    e.preventDefault();
    const v = clientToView(e.clientX, e.clientY);
    panRef.current = {
      pointerId: e.pointerId,
      startX: v.x,
      startY: v.y,
      origX: camRef.current.x,
      origY: camRef.current.y,
    };
    setPanning(true);
    try { (e.currentTarget as Element).setPointerCapture(e.pointerId); } catch { /* synthetic */ }
  };

  const onPanPointerMove = (e: PointerEvent) => {
    const p = panRef.current;
    if (!p || p.pointerId !== e.pointerId) return;
    const v = clientToView(e.clientX, e.clientY);
    applyCam({
      k: camRef.current.k,
      x: p.origX + (v.x - p.startX),
      y: p.origY + (v.y - p.startY),
    });
  };

  const onPanPointerUp = (e: PointerEvent) => {
    const p = panRef.current;
    if (!p || p.pointerId !== e.pointerId) return;
    panRef.current = null;
    setPanning(false);
  };

  const onGroupPointerDown = (e: PointerEvent, g: { id: number; cx: number; cy: number }) => {
    e.preventDefault();
    e.stopPropagation();
    const p = clientToWorld(e.clientX, e.clientY);
    dragRef.current = {
      id: g.id,
      pointerId: e.pointerId,
      startX: p.x,
      startY: p.y,
      origCx: g.cx,
      origCy: g.cy,
      lastCx: g.cx,
      lastCy: g.cy,
      moved: false,
    };
    setDraggingId(g.id);
    try { (e.currentTarget as Element).setPointerCapture(e.pointerId); } catch { /* synthetic events */ }
  };

  const onGroupPointerMove = (e: PointerEvent) => {
    const d = dragRef.current;
    if (!d || d.pointerId !== e.pointerId) return;
    const p = clientToWorld(e.clientX, e.clientY);
    const dx = p.x - d.startX;
    const dy = p.y - d.startY;
    if (!d.moved && dx * dx + dy * dy > 16) d.moved = true;
    if (!d.moved) return;
    d.lastCx = d.origCx + dx;
    d.lastCy = d.origCy + dy;
    setOverrides((prev) => ({ ...prev, [d.id]: { cx: d.lastCx, cy: d.lastCy } }));
  };

  const onGroupPointerUp = (e: PointerEvent) => {
    const d = dragRef.current;
    if (!d || d.pointerId !== e.pointerId) return;
    dragRef.current = null;
    setDraggingId(null);
    if (d.moved) {
      void Promise.resolve(onMoveGrouping(d.id, d.lastCx, d.lastCy)).finally(() => {
        setOverrides((prev) => {
          const next = { ...prev };
          delete next[d.id];
          return next;
        });
      });
      return;
    }
    setOverrides((prev) => {
      const next = { ...prev };
      delete next[d.id];
      return next;
    });
    onSelectGrouping(selectedGrouping === d.id ? null : d.id);
  };

  return (
    <div className="relative h-[min(66vh,720px)] w-full overflow-hidden rounded-xl border border-slate-800 bg-[#080a10]">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        preserveAspectRatio="xMidYMid meet"
        className={`block h-full w-full ${panning ? "cursor-grabbing" : "cursor-grab"}`}
        style={{ touchAction: "none" }}
        role="img"
        aria-label="The Real World: nuclei as points of light, friends as smaller lights. Scroll to zoom. Drag the sky to move. Drag a table to place it."
      >
        <defs>
          <radialGradient id="rw-vignette" cx="50%" cy="45%" r="75%">
            <stop offset="0%" stopColor="#0d1018" />
            <stop offset="100%" stopColor="#05060a" />
          </radialGradient>
          <pattern id="rw-grid" width="48" height="48" patternUnits="userSpaceOnUse">
            <path d="M 48 0 L 0 0 0 48" fill="none" stroke="#1e293b" strokeWidth="0.5" opacity="0.35" />
          </pattern>
          <filter id="rw-glow" x="-80%" y="-80%" width="260%" height="260%">
            <feGaussianBlur stdDeviation="7" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>
        <rect width={VIEW_W} height={VIEW_H} fill="url(#rw-vignette)" />
        <rect
          width={VIEW_W} height={VIEW_H} fill="transparent"
          onPointerDown={onPanPointerDown}
          onPointerMove={onPanPointerMove}
          onPointerUp={onPanPointerUp}
          onPointerCancel={onPanPointerUp}
        />
        <g transform={`translate(${cam.x} ${cam.y}) scale(${cam.k})`}>
        <rect width={VIEW_W} height={VIEW_H} fill="url(#rw-grid)" pointerEvents="none" />

        {/* Folds into the workforce light on a world-swap. It sits INSIDE the
            camera, anchored on the light's world position, so the fold happens
            where the light really is on screen however the sky has been panned
            — and ColonyPanel hands the other world that same screen point. */}
        <g
          className="colony-world-morph"
          style={morphStyle(open, {
            cx: wf?.cx ?? WORKFORCE_ANCHOR.cx, cy: wf?.cy ?? WORKFORCE_ANCHOR.cy,
          })}
          pointerEvents={open ? undefined : "none"}
        >

        {institutions.length > 0 && (
          <text
            x={118} y={118}
            textAnchor="middle"
            className="fill-slate-500 text-[11px] tracking-wide"
            style={{ paintOrder: "stroke", stroke: "#05060a", strokeWidth: 3 }}
          >
            Institutions of the Faith
          </text>
        )}
        {institutions.map((g) => {
          const hex = accentFor(g.accent).hex;
          const lines = institutionLines(groupingsById[g.id]?.name ?? "Institution");
          const dragging = draggingId === g.id;
          return (
            <g key={`inst-${g.id}`}>
              {[0.42, 0.64, 0.86].map((fr, i) => (
                <ellipse
                  key={fr}
                  pointerEvents="none"
                  cx={g.cx} cy={g.cy}
                  rx={g.r * fr} ry={g.r * fr * TILT}
                  fill="none" stroke={hex} strokeWidth={1}
                  opacity={0.32 - i * 0.06}
                />
              ))}
              <g
                className={dragging ? "cursor-grabbing" : "cursor-grab"}
                style={{ touchAction: "none" }}
                onPointerDown={(e) => onGroupPointerDown(e, g)}
                onPointerMove={onGroupPointerMove}
                onPointerUp={onGroupPointerUp}
                onPointerCancel={onGroupPointerUp}
              >
                <circle cx={g.cx} cy={g.cy} r={22} fill={hex} opacity="0.2"
                        filter="url(#rw-glow)" pointerEvents="none" />
                <circle cx={g.cx} cy={g.cy} r={16} fill="transparent" />
                <circle cx={g.cx} cy={g.cy} r={5.2} fill="#fff7ed" pointerEvents="none" />
                <circle cx={g.cx} cy={g.cy} r={2.6} fill="#ffffff" pointerEvents="none" />
                {lines.map((line, i) => (
                  <text
                    key={line}
                    x={g.cx} y={g.cy + g.r * TILT + 16 + i * 12}
                    textAnchor="middle"
                    className="fill-amber-100/90 text-[11px]"
                    style={{ paintOrder: "stroke", stroke: "#05060a", strokeWidth: 3 }}
                  >
                    {line}
                  </text>
                ))}
              </g>
            </g>
          );
        })}

        {tables.map((g) => {
          const hex = accentFor(g.accent).hex;
          const nucleus = g.is_nucleus;
          const op = nucleus ? 0.28 : 0.14;
          const dragging = draggingId === g.id;
          return (
            <g key={`g-${g.id}`}>
              {[0.42, 0.64, 0.86].map((fr, i) => (
                <ellipse
                  key={fr}
                  pointerEvents="none"
                  className={nucleus && i === 2 && !dragging ? "colony-orbit-spin" : undefined}
                  style={nucleus && i === 2 ? { ["--spin-origin" as string]: `${g.cx}px ${g.cy}px` } : undefined}
                  cx={g.cx} cy={g.cy}
                  rx={g.r * fr} ry={g.r * fr * TILT}
                  fill="none" stroke={hex} strokeWidth={1}
                  opacity={op - i * 0.05}
                />
              ))}
              <g
                className={dragging ? "cursor-grabbing" : "cursor-grab"}
                style={{ touchAction: "none" }}
                onPointerDown={(e) => onGroupPointerDown(e, g)}
                onPointerMove={onGroupPointerMove}
                onPointerUp={onGroupPointerUp}
                onPointerCancel={onGroupPointerUp}
              >
                <circle cx={g.cx} cy={g.cy} r={nucleus ? 34 : 22} fill={hex}
                        opacity={nucleus ? 0.28 : 0.16} filter="url(#rw-glow)"
                        pointerEvents="none" />
                {nucleus && (
                  <circle cx={g.cx} cy={g.cy} r={16} fill={hex} opacity="0.22"
                          className="colony-live-pulse" pointerEvents="none" />
                )}
                <circle cx={g.cx} cy={g.cy} r={18} fill="transparent" />
                <circle cx={g.cx} cy={g.cy} r={nucleus ? 6.5 : 4.2} fill="#fff7ed" pointerEvents="none" />
                <circle cx={g.cx} cy={g.cy} r={nucleus ? 3.5 : 2.2} fill="#ffffff" pointerEvents="none" />
                <text
                  x={g.cx} y={g.cy - g.r * TILT - 14}
                  textAnchor="middle"
                  className="fill-slate-200 text-[13px] font-medium"
                  style={{ paintOrder: "stroke", stroke: "#05060a", strokeWidth: 3 }}
                >
                  {groupingsById[g.id]?.name ?? "Grouping"}
                </text>
              </g>
            </g>
          );
        })}

        {focusLinks.map((link) => {
          const d = edgePath(link.x1, link.y1, link.x2, link.y2, 0.12);
          const family = link.kind === "family";
          const place = link.kind === "place";
          const stroke = family ? "#e8d48b" : place ? "#94a3b8" : accentFor("sky").hex;
          return (
            <g key={link.key} className="rw-family-link">
              <path d={d} fill="none" stroke={stroke} strokeWidth={1.5} opacity={0.4} />
              <path d={d} fill="none" stroke={stroke} strokeWidth={1.6} opacity={0.85}
                    strokeDasharray={place ? "5 6" : undefined}
                    className="colony-edge-flow" />
            </g>
          );
        })}

        {lights.map((p) => {
          const actor = actorsById[p.id];
          if (!actor) return null;
          const hex = accentFor(p.accent).hex;
          const days = snapshot.embers[String(p.id)];
          const ember = emberOpacity(days);
          const collective = actor.kind === "collective";
          const household = actor.kind === "household";
          const insideHere = household
            ? familyMembers.some((m) => m.household_id === p.id && !seatedIds.has(m.person_id))
            : false;
          const r = collective ? 9 : household ? (insideHere ? 7.4 : 6.2) : actor.display_name === "You" ? 5 : 4.2;
          const focus = hover?.kind === "actor" && hover.id === p.id;
          const dim = !!((selectedActor || selectedGrouping)
            && selectedActor !== p.id && !bloomFamilyIds.has(p.id)
            && !bloomIds.includes(p.id) && !connectedIds.has(p.id));
          return (
            <g
              key={`a-${p.id}`}
              className="cursor-pointer"
              opacity={dim ? 0.55 : 1}
              onMouseEnter={() => setHover({ kind: "actor", id: p.id })}
              onMouseLeave={() => setHover(null)}
              onClick={() => onSelectActor(selectedActor === p.id ? null : p.id)}
            >
              {collective ? (
                <>
                  <ellipse cx={p.x} cy={p.y} rx={r * 1.7} ry={r} fill={hex} opacity={0.08} />
                  <ellipse cx={p.x} cy={p.y} rx={r * 1.7} ry={r} fill="none" stroke={hex}
                           strokeWidth={1.2} strokeDasharray="3 3" opacity={0.75} />
                </>
              ) : (
                <>
                  <circle cx={p.x} cy={p.y} r={r * 3.4} fill="#f59e0b"
                          opacity={0.08 + ember * 0.22} filter="url(#rw-glow)" />
                  <circle cx={p.x} cy={p.y} r={r * 1.8} fill={hex} opacity={0.18 + ember * 0.25} />
                  <circle cx={p.x} cy={p.y} r={r} fill="#fff7ed" />
                  <circle cx={p.x} cy={p.y} r={r * 0.45} fill="#ffffff" />
                  {household && (
                    <circle cx={p.x} cy={p.y} r={r + 3.4} fill="none" stroke={hex} strokeWidth={1} opacity={0.55} />
                  )}
                  {household && insideHere && (
                    <circle cx={p.x} cy={p.y} r={r + 6.5} fill="none" stroke="#e8d48b"
                            strokeWidth={1} opacity={bloomIds.includes(p.id) ? 0.85 : 0.4} />
                  )}
                </>
              )}
              {(focus || selectedActor === p.id) && (
                <circle cx={p.x} cy={p.y} r={r + 7} fill="none" stroke="#f8fafc" strokeWidth={1.3} opacity={0.85} />
              )}
              <text
                x={p.x} y={p.y + r + 13}
                textAnchor="middle"
                className="fill-slate-300 text-[11px]"
                style={{ paintOrder: "stroke", stroke: "#05060a", strokeWidth: 3 }}
              >
                {shortName(actor.display_name, actor.kind)}
              </text>
            </g>
          );
        })}

        {bloomIds.map((houseId) => {
          const house = lights.find((p) => p.id === houseId);
          if (!house) return null;
          const inside = insideOf(houseId);
          const rPetal = 34 + inside.length * 4;
          return inside.map((m, i) => {
            const actor = actorsById[m.person_id];
            if (!actor) return null;
            const n = inside.length;
            const ang = -Math.PI / 2 + (i / n) * Math.PI * 2;
            const px = house.x + rPetal * Math.cos(ang);
            const py = house.y + rPetal * TILT * Math.sin(ang);
            const hex = accentFor(house.accent).hex;
            const r = 4.2;
            return (
              <g key={`petal-${m.id}`}>
                <line
                  x1={house.x} y1={house.y} x2={px} y2={py}
                  stroke="#e8d48b" strokeWidth={1.1}
                  className="rw-family-link"
                  opacity={bloomOpen ? 0.45 : 0}
                />
                <g
                  className="rw-petal cursor-pointer"
                  transform={`translate(${px} ${py})`}
                  opacity={bloomOpen ? 1 : 0}
                  onMouseEnter={() => setHover({ kind: "actor", id: actor.id })}
                  onMouseLeave={() => setHover(null)}
                  onClick={(e) => { e.stopPropagation(); onSelectActor(actor.id); }}
                >
                  <g style={{
                    transform: bloomOpen ? "scale(1)" : "scale(0.15)",
                    transformOrigin: "0px 0px",
                    transition: "transform 0.45s cubic-bezier(0.22, 1, 0.36, 1)",
                  }}>
                    <circle r={r * 3.2} fill="#f59e0b" opacity={0.16} filter="url(#rw-glow)" />
                    <circle r={r * 1.8} fill={hex} opacity={0.28} />
                    <circle r={r} fill="#fff7ed" />
                    <circle r={r * 0.45} fill="#ffffff" />
                    <text
                      y={r + 13}
                      textAnchor="middle"
                      className="fill-slate-300 text-[11px]"
                      style={{ paintOrder: "stroke", stroke: "#05060a", strokeWidth: 3 }}
                    >
                      {shortName(actor.display_name, actor.kind)}
                    </text>
                  </g>
                </g>
              </g>
            );
          });
        })}

        {/* Threads to workforce people who already have a light of their own. */}
        {workforceOpen && wfPeople.map((p) => {
          if (!wfSeated.has(p.actor_id)) return null;
          const lit = lights.find((l) => l.id === p.actor_id);
          if (!lit) return null;
          const d = edgePath(wf.cx, wf.cy, lit.x, lit.y, 0.1);
          return (
            <g key={`wf-thread-${p.membership_id}`} className="rw-family-link">
              <path d={d} fill="none" stroke="#fbbf24" strokeWidth={1.4}
                    opacity={wfOpen ? 0.4 : 0} />
            </g>
          );
        })}

        {workforceOpen && wfRing.map((petal, i) => {
          const at = workforcePetal(i, wfRing.length);
          const px = wf.cx + at.x;
          const py = wf.cy + at.y;
          const hex = accentFor(petal.accent).hex;
          const r = petal.human ? 4.6 : 5.4;
          return (
            <g key={`wf-petal-${petal.key}`}>
              <line
                x1={wf.cx} y1={wf.cy} x2={px} y2={py}
                stroke={petal.human ? "#fbbf24" : hex} strokeWidth={1}
                className="rw-family-link"
                opacity={wfOpen ? 0.34 : 0}
              />
              <g
                className="rw-petal cursor-pointer"
                transform={`translate(${px} ${py})`}
                opacity={wfOpen ? 1 : 0}
                onClick={(e) => {
                  e.stopPropagation();
                  if (petal.actorId != null) onSelectActor(petal.actorId);
                  else onSelectWorkforce();
                }}
              >
                <g style={{
                  transform: wfOpen ? "scale(1)" : "scale(0.15)",
                  transformOrigin: "0px 0px",
                  transition: "transform 0.45s cubic-bezier(0.22, 1, 0.36, 1)",
                }}>
                  <circle r={r * 3.2} fill={hex} opacity={petal.live ? 0.3 : 0.14}
                          filter="url(#rw-glow)" />
                  <circle r={r * 1.8} fill={hex} opacity={0.26} />
                  <circle r={r} fill={petal.paused ? "#64748b" : "#fff7ed"} />
                  <circle r={r * 0.45} fill="#ffffff" />
                  {/* A person on the workforce wears a ring, so a real friend
                      is never mistaken for one of the agents. */}
                  {petal.human && (
                    <circle r={r + 3.6} fill="none" stroke="#fbbf24" strokeWidth={1}
                            opacity={0.75} />
                  )}
                  <text
                    y={r + 13}
                    textAnchor="middle"
                    className={petal.human ? "fill-amber-100 text-[11px]" : "fill-slate-300 text-[11px]"}
                    style={{ paintOrder: "stroke", stroke: "#05060a", strokeWidth: 3 }}
                  >
                    {petal.label}
                  </text>
                </g>
              </g>
            </g>
          );
        })}

        <g className="cursor-pointer" onClick={onSelectWorkforce}>
          <circle cx={wf.cx} cy={wf.cy} r={32} fill="#fbbf24" opacity="0.22" filter="url(#rw-glow)" />
          <circle cx={wf.cx} cy={wf.cy} r={16} fill="#fbbf24" opacity="0.2" className="colony-live-pulse" />
          {workforceOpen && (
            <circle cx={wf.cx} cy={wf.cy} r={40} fill="none" stroke="#fbbf24"
                    strokeWidth={1.2} opacity={wfOpen ? 0.5 : 0}
                    className="rw-family-link" />
          )}
          {/* A real hit area: the drawn core is 6.5px and was almost
              unclickable at the default zoom. */}
          <circle cx={wf.cx} cy={wf.cy} r={26} fill="transparent" />
          <circle cx={wf.cx} cy={wf.cy} r={6.5} fill="#fff7ed" pointerEvents="none" />
          <circle cx={wf.cx} cy={wf.cy} r={3.5} fill="#ffffff" pointerEvents="none" />
          <text x={wf.cx} y={wf.cy + 28} textAnchor="middle" fill="#fde68a" className="text-[12px]"
                pointerEvents="none"
                style={{ paintOrder: "stroke", stroke: "#05060a", strokeWidth: 3 }}>
            {wfName}
          </text>
          {!workforceOpen && (
            <text x={wf.cx} y={wf.cy + 42} textAnchor="middle" fill="#94a3b8"
                  className="text-[10px]" pointerEvents="none"
                  style={{ paintOrder: "stroke", stroke: "#05060a", strokeWidth: 3 }}>
              click to open
            </text>
          )}
        </g>
        </g>

        </g>

        {/* The hinge: the one body that belongs to both skies. It fades up as
            this world folds away, and the other world's copy takes over from it
            at the same pixel. Drawn OUTSIDE the camera, in screen units, so it
            is the same size in both worlds however this one is zoomed. */}
        <g
          className="colony-world-morph"
          pointerEvents="none"
          style={{ opacity: open ? 0 : 1 }}
        >
          <circle cx={cam.x + wf.cx * cam.k} cy={cam.y + wf.cy * cam.k} r={32}
                  fill="#fbbf24" opacity="0.22" filter="url(#rw-glow)" />
          <circle cx={cam.x + wf.cx * cam.k} cy={cam.y + wf.cy * cam.k} r={6.5}
                  fill="#fff7ed" />
          <circle cx={cam.x + wf.cx * cam.k} cy={cam.y + wf.cy * cam.k} r={3.5}
                  fill="#ffffff" />
        </g>
      </svg>

      {/* The overlays fade with the sky they belong to, so a world-swap moves
          the whole view rather than leaving a legend floating over a fold. */}
      <div className="colony-world-morph" style={{ opacity: open ? 1 : 0 }}>
      <div className="pointer-events-none absolute bottom-4 left-4 max-w-[260px] space-y-1.5 rounded-lg
                      border border-slate-800/80 bg-slate-950/70 px-3 py-2.5 text-[11px]
                      text-slate-400 backdrop-blur">
        {empty ? (
          <div>Add a nucleus to begin — each one is a point of light.</div>
        ) : (
          <>
            <div className="flex items-center gap-2">
              <span className="h-2.5 w-2.5 rounded-full bg-amber-100 shadow-[0_0_8px_2px_rgba(251,191,36,0.7)]" />
              A nucleus — the Vision in that place
            </div>
            <div className="flex items-center gap-2">
              <span className="h-2.5 w-2.5 rounded-full bg-amber-100/70" />
              An institution of the Faith
            </div>
            <div className="flex items-center gap-2">
              <span className="h-2 w-2 rounded-full bg-sky-300" />
              Closer — they carry that table
            </div>
            <div className="flex items-center gap-2">
              <span className="h-2 w-2 rounded-full bg-slate-500" />
              Farther — connected, newly arrived
            </div>
            <div className="flex items-center gap-2">
              <span className="h-px w-4 bg-slate-500" />
              Click to see who walks with whom
            </div>
            <div>Scroll to zoom. Drag the empty sky to move around.</div>
            <div>Drag a table to place it. It stays where you leave it.</div>
            <div>Click a family to see who is in it. Click anyone to see how they connect.</div>
          </>
        )}
      </div>

      <div className="absolute right-3 top-3 z-10 flex gap-1">
        <button
          type="button"
          title="Zoom in"
          onClick={() => zoomAt(VIEW_W / 2, VIEW_H / 2, 1.25)}
          className="rounded-md border border-slate-700 bg-slate-950/80 p-1.5 text-slate-300 hover:border-slate-500 hover:text-slate-100"
        >
          <Plus className="h-4 w-4" />
        </button>
        <button
          type="button"
          title="Zoom out"
          onClick={() => zoomAt(VIEW_W / 2, VIEW_H / 2, 0.8)}
          className="rounded-md border border-slate-700 bg-slate-950/80 p-1.5 text-slate-300 hover:border-slate-500 hover:text-slate-100"
        >
          <Minus className="h-4 w-4" />
        </button>
        <button
          type="button"
          title="Show all"
          onClick={() => applyCam({ k: 1, x: 0, y: 0 })}
          className="rounded-md border border-slate-700 bg-slate-950/80 p-1.5 text-slate-300 hover:border-slate-500 hover:text-slate-100"
        >
          <Maximize2 className="h-4 w-4" />
        </button>
      </div>

      {hover?.kind === "actor" && actorsById[hover.id] && (
        <div className="pointer-events-none absolute right-4 top-16 w-60 rounded-lg border
                        border-slate-700 bg-slate-950/90 p-3 text-xs backdrop-blur">
          <div className="text-sm font-semibold text-slate-100">{actorsById[hover.id].display_name}</div>
          <div className="mt-0.5 text-slate-400">
            {actorsById[hover.id].kind === "household" ? "A household"
              : actorsById[hover.id].kind === "collective" ? "A gathering of many — not yet named"
                : "A friend"}
          </div>
          {actorsById[hover.id].how_we_met && (
            <div className="mt-2 text-slate-500">{actorsById[hover.id].how_we_met}</div>
          )}
        </div>
      )}
      </div>
    </div>
  );
}

function shortName(name: string, kind: string): string {
  if (kind === "household") return name.replace(/^The /, "").replace(/ household$/i, "");
  if (kind === "collective") return name.length > 22 ? "the gathering" : name;
  const parts = name.split(" ");
  return parts[0] || name;
}

/**
 * Where the i-th body sits when the workforce light opens.
 *
 * A fan to the RIGHT, in rings of six: the institution column sits to the
 * left of the workforce and a full circle would drop names on top of it. The
 * ellipse is rounder than the tables' TILT because a dozen labels squeezed
 * onto a flat orbit overlap at the top and bottom of the ring.
 */
const WF_FAN_TILT = 0.78;
const WF_RING_SIZE = 6;
function workforcePetal(i: number, total: number): { x: number; y: number } {
  const ring = Math.floor(i / WF_RING_SIZE);
  const inRing = i % WF_RING_SIZE;
  const remaining = total - ring * WF_RING_SIZE;
  const n = Math.min(WF_RING_SIZE, remaining);
  const r = 88 + ring * 46;
  const span = (160 * Math.PI) / 180;
  const ang = n === 1 ? 0 : -span / 2 + (inRing / (n - 1)) * span;
  return { x: r * Math.cos(ang), y: r * WF_FAN_TILT * Math.sin(ang) };
}

function emberOpacity(days: number | null | undefined): number {
  if (days == null) return 0.14;
  if (days <= 10) return 0.72;
  if (days <= 21) return 0.46;
  if (days <= 35) return 0.28;
  return 0.1;
}

function edgePath(x1: number, y1: number, x2: number, y2: number, bend = 0.16): string {
  const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
  const dx = x2 - x1, dy = y2 - y1;
  return `M ${x1} ${y1} Q ${mx - dy * bend} ${my + dx * bend} ${x2} ${y2}`;
}
