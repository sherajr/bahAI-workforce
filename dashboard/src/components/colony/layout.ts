// Where every node sits in the Colony constellation.
//
// Deliberately DETERMINISTIC rather than force-directed: with a dozen nodes a
// physics simulation buys nothing and costs a layout that moves under the
// cursor and settles differently on every visit. Sheraj should be able to
// learn where Ruth lives and find her there tomorrow.

import type { ColonyAgent, ColonySnapshot, ColonyTeam } from "../../lib/types";

// Roughly 2:1 — close to the real proportions of the panel. A narrower canvas
// letterboxed the whole constellation into the middle third of the container
// and wasted the sides.
export const VIEW_W = 1440;
export const VIEW_H = 720;

/** Orbits are drawn as tilted disks — this is the foreshortening of the y axis. */
export const TILT = 0.42;

/**
 * Fixed slots, largest first. Teams are assigned to them in descending size
 * order, so the busiest team always gets the roomiest position and the map
 * keeps the same shape as teams grow or shrink.
 */
// The lower-LEFT of the canvas is deliberately left empty — the legend sits
// there, and a system underneath it would be half-covered.
const SLOTS = [
  { cx: 430, cy: 330, r: 215 },
  { cx: 1010, cy: 235, r: 155 },
  { cx: 1130, cy: 565, r: 125 },
  { cx: 720, cy: 600, r: 118 },
  { cx: 880, cy: 95, r: 92 },
  { cx: 190, cy: 630, r: 88 },
];

export interface PlacedAgent {
  name: string;
  team: string;
  x: number;
  y: number;
  /** Instruments sit on a tighter inner orbit and draw smaller. */
  isInstrument: boolean;
  radius: number;
  agent: ColonyAgent;
}

export interface PlacedTeam {
  team: ColonyTeam;
  cx: number;
  cy: number;
  r: number;
}

export interface ColonyLayout {
  teams: PlacedTeam[];
  agents: PlacedAgent[];
  byName: Record<string, PlacedAgent>;
}

/** Node radius from standing: more judged work earns a slightly larger body. */
function radiusFor(agent: ColonyAgent): number {
  if (agent.is_instrument) return 7;
  const base = 13;
  const earned = Math.min(7, Math.sqrt(Math.max(0, agent.total_runs)) * 1.6);
  return base + earned;
}

export function layoutColony(snapshot: ColonySnapshot): ColonyLayout {
  const agentsByName: Record<string, ColonyAgent> = {};
  for (const a of snapshot.agents) agentsByName[a.name] = a;

  const ordered = [...snapshot.teams].sort(
    (a, b) => b.members.length + b.instruments.length - (a.members.length + a.instruments.length),
  );

  const teams: PlacedTeam[] = [];
  const agents: PlacedAgent[] = [];

  ordered.forEach((team, i) => {
    const slot = SLOTS[i % SLOTS.length];
    teams.push({ team, cx: slot.cx, cy: slot.cy, r: slot.r });

    const place = (names: string[], ringRadius: number, isInstrument: boolean) => {
      names.forEach((name, idx) => {
        const agent = agentsByName[name];
        if (!agent) return; // an id in TEAMS with no row in the DB — skip, never crash
        // Every member is offset by HALF a step from the top, which keeps the
        // vertical axis through the core clear. That matters: the team's name
        // is printed directly below the core, and a two-member team placed at
        // top-and-bottom put its second member straight through that label
        // (seen for real on Film Crew and Office). Offset, they sit left and
        // right instead. Instruments take a further quarter-step so they never
        // hide directly behind a member on the outer ring.
        //
        // A team of ONE is the special case the half-step gets wrong: it lands
        // the sole member directly below the core, on the team label. Put it
        // beside the core instead (Office and Ledger are both teams of one).
        const step = (Math.PI * 2) / Math.max(1, names.length);
        const theta = names.length === 1
          ? 0
          : -Math.PI / 2 + idx * step + step / 2 + (isInstrument ? step / 4 : 0);
        agents.push({
          name,
          team: team.id,
          x: slot.cx + ringRadius * Math.cos(theta),
          y: slot.cy + ringRadius * TILT * Math.sin(theta),
          isInstrument,
          radius: radiusFor(agent),
          agent,
        });
      });
    };

    place(team.members, slot.r * 0.74, false);
    place(team.instruments, slot.r * 0.38, true);
  });

  const byName: Record<string, PlacedAgent> = {};
  for (const a of agents) byName[a.name] = a;
  return { teams, agents, byName };
}

/**
 * A curved path between two nodes. The control point is pushed perpendicular
 * to the line so two agents who hand work BOTH ways get two distinct arcs
 * instead of one overdrawn straight line.
 */
export function edgePath(
  from: { x: number; y: number },
  to: { x: number; y: number },
  bend = 0.18,
): string {
  const mx = (from.x + to.x) / 2;
  const my = (from.y + to.y) / 2;
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const cx = mx - dy * bend;
  const cy = my + dx * bend;
  return `M ${from.x} ${from.y} Q ${cx} ${cy} ${to.x} ${to.y}`;
}
