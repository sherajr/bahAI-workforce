import { useMemo, useState } from "react";
import type { ColonySnapshot } from "../../lib/types";
import { accentFor, agentLabel, cn } from "../../lib/utils";
import { edgePath, layoutColony, TILT, VIEW_H, VIEW_W } from "./layout";

interface Props {
  snapshot: ColonySnapshot;
  selectedAgent: string | null;
  selectedTeam: string | null;
  onSelectAgent: (name: string | null) => void;
  onSelectTeam: (team: string | null) => void;
  /** "map" draws everything; "handoffs" emphasises the edges and dims the rest. */
  emphasis: "map" | "handoffs";
}

export function ColonyGraph({
  snapshot, selectedAgent, selectedTeam, onSelectAgent, onSelectTeam, emphasis,
}: Props) {
  const [hover, setHover] = useState<string | null>(null);
  const layout = useMemo(() => layoutColony(snapshot), [snapshot]);

  const maxEdge = Math.max(1, ...snapshot.edges.map((e) => e.count));
  // What the cursor or the selection is focused on — used to decide which
  // edges stay lit. Hover wins so you can trace a path without losing your
  // selected agent's panel.
  const focus = hover ?? selectedAgent;

  return (
    // A bounded box with the SVG scaling to fit inside it. Letting the graph
    // take its natural height pushed the two lower teams below the fold — the
    // map has to be readable in one glance or it isn't a map. In the Handoffs
    // view it gives up height to the tables underneath it, which are the point
    // of that view.
    <div className={cn(
      "relative w-full overflow-hidden rounded-xl border border-slate-800 bg-[#080a10]",
      emphasis === "handoffs" ? "h-[min(46vh,500px)]" : "h-[min(66vh,720px)]",
    )}>
      <svg
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        preserveAspectRatio="xMidYMid meet"
        className="block h-full w-full"
        role="img"
        aria-label="The colony: every agent, grouped into teams, with the handoffs between them."
      >
        <defs>
          <radialGradient id="colony-vignette" cx="50%" cy="45%" r="75%">
            <stop offset="0%" stopColor="#0d1018" />
            <stop offset="100%" stopColor="#05060a" />
          </radialGradient>
          <pattern id="colony-grid" width="48" height="48" patternUnits="userSpaceOnUse">
            <path d="M 48 0 L 0 0 0 48" fill="none" stroke="#1e293b" strokeWidth="0.5"
                  opacity="0.35" />
          </pattern>
          {/* One sphere gradient per accent: light from the upper left, so every
              body in the scene is lit from the same direction. */}
          {["amber", "sky", "violet", "emerald"].map((accent) => {
            const hex = accentFor(accent).hex;
            return (
              <radialGradient key={accent} id={`sphere-${accent}`} cx="35%" cy="30%" r="72%">
                <stop offset="0%" stopColor="#ffffff" stopOpacity="0.95" />
                <stop offset="38%" stopColor={hex} stopOpacity="0.9" />
                <stop offset="100%" stopColor={hex} stopOpacity="0.28" />
              </radialGradient>
            );
          })}
          <filter id="colony-glow" x="-70%" y="-70%" width="240%" height="240%">
            <feGaussianBlur stdDeviation="7" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        <rect width={VIEW_W} height={VIEW_H} fill="url(#colony-vignette)" />
        <rect width={VIEW_W} height={VIEW_H} fill="url(#colony-grid)" />

        {/* Ambient ripples under each system — the soft pooled light in the
            reference. Pure decoration, so no pointer events. */}
        <g pointerEvents="none">
          {layout.teams.map(({ team, cx, cy, r }) => {
            const hex = accentFor(team.accent).hex;
            return [1.02, 1.26, 1.5].map((scale, i) => (
              <ellipse
                key={`${team.id}-ripple-${i}`}
                cx={cx} cy={cy + r * 0.16}
                rx={r * scale} ry={r * scale * TILT}
                fill="none" stroke={hex}
                strokeWidth={1}
                opacity={0.09 - i * 0.025}
              />
            ));
          })}
        </g>

        {/* Orbit rings. The outer ring turns very slowly; the inner one is
            static so the two never read as a single rigid object. */}
        <g pointerEvents="none">
          {layout.teams.map(({ team, cx, cy, r }) => {
            const hex = accentFor(team.accent).hex;
            const dim = selectedTeam && selectedTeam !== team.id;
            return (
              <g key={`${team.id}-orbits`} opacity={dim ? 0.3 : 1}>
                <ellipse
                  cx={cx} cy={cy} rx={r * 0.74} ry={r * 0.74 * TILT}
                  fill="none" stroke={hex} strokeWidth="1" opacity="0.32"
                  className="colony-orbit-spin"
                  style={{ ["--spin-origin" as string]: `${cx}px ${cy}px` }}
                />
                <ellipse
                  cx={cx} cy={cy} rx={r * 0.38} ry={r * 0.38 * TILT}
                  fill="none" stroke={hex} strokeWidth="1" opacity="0.2"
                />
              </g>
            );
          })}
        </g>

        {/* Handoff edges — real recorded work (derived from task_runs), so the
            stroke weight is the actual number of handoffs, not a constant. */}
        <g fill="none">
          {snapshot.edges.map((edge) => {
            const from = layout.byName[edge.source];
            const to = layout.byName[edge.target];
            if (!from || !to) return null;
            const involved = !focus || focus === edge.source || focus === edge.target;
            const hex = accentFor(
              layout.teams.find((t) => t.team.id === from.team)?.team.accent,
            ).hex;
            const weight = 0.8 + (edge.count / maxEdge) * 2.6;
            const lit = involved && (emphasis === "handoffs" || !!focus);
            return (
              <g key={`${edge.source}-${edge.target}`}>
                <path
                  d={edgePath(from, to)}
                  stroke={hex}
                  strokeWidth={weight}
                  opacity={involved ? (emphasis === "handoffs" ? 0.5 : 0.28) : 0.06}
                  strokeLinecap="round"
                />
                {lit && (
                  <path
                    d={edgePath(from, to)}
                    stroke={hex}
                    strokeWidth={weight + 0.6}
                    opacity={0.9}
                    strokeLinecap="round"
                    className="colony-edge-flow"
                  />
                )}
              </g>
            );
          })}
        </g>

        {/* Team cores. Clicking one opens that team's goals and consultation. */}
        {layout.teams.map(({ team, cx, cy, r }) => {
          const accent = accentFor(team.accent);
          const active = selectedTeam === team.id;
          const dim = selectedTeam && !active;
          const job = team.jobs?.[0];
          return (
            <g
              key={team.id}
              opacity={dim ? 0.45 : 1}
              className="cursor-pointer"
              onClick={() => onSelectTeam(active ? null : team.id)}
            >
              {/* A team with a job in flight pulses, the same signal a live
                  agent uses — so "something is happening" is legible from the
                  map alone, without opening anything. */}
              {job && (
                <circle cx={cx} cy={cy} r={r * 0.3} fill="#34d399" opacity="0.22"
                        className="colony-live-pulse"
                        style={{ ["--spin-origin" as string]: "center" }} />
              )}
              <circle cx={cx} cy={cy} r={r * 0.155} fill={`url(#sphere-${team.accent})`}
                      filter="url(#colony-glow)" />
              {active && (
                <circle cx={cx} cy={cy} r={r * 0.22} fill="none"
                        stroke={accent.hex} strokeWidth="1.5" opacity="0.8" />
              )}
              {/* The name goes ABOVE the core, and that placement is load-bearing
                  rather than taste. Members sit at -90 + step*(k + 1/2): the top
                  centre needs k + 1/2 to be a whole multiple of the count, which
                  it never is, so nothing can ever occupy it. Bottom centre is
                  taken whenever the member count is ODD (2k+1 = n) — Print Studio
                  has five, so Clara landed squarely on the label there.
                  Also cleared of the glow filter, whose blur spreads several px
                  past the circle's own radius. */}
              <text
                x={cx} y={cy - r * 0.155 - 20}
                textAnchor="middle"
                className="fill-slate-200 text-[13px] font-medium"
                style={{ paintOrder: "stroke", stroke: "#05060a", strokeWidth: 3 }}
              >
                {team.name}
              </text>
              {team.active_goals.length > 0 && (
                <text
                  x={cx} y={cy - r * 0.155 - 36}
                  textAnchor="middle"
                  className="text-[11px]"
                  fill={accent.hex}
                  opacity="0.85"
                  style={{ paintOrder: "stroke", stroke: "#05060a", strokeWidth: 3 }}
                >
                  {team.active_goals.length === 1
                    ? "1 goal"
                    : `${team.active_goals.length} goals`}
                </text>
              )}
              {/* What the team is doing RIGHT NOW, under its name. Real running
                  jobs only, so a lit team always means work is genuinely in
                  flight — a run started by Abigail or a goal shows here exactly
                  like one Sheraj started himself. */}
              {job && (
                <text
                  x={cx} y={cy + r * 0.155 + 26}
                  textAnchor="middle"
                  className="text-[11px]"
                  fill="#34d399"
                  style={{ paintOrder: "stroke", stroke: "#05060a", strokeWidth: 3 }}
                >
                  {`Working — ${job.label}`}
                  {team.jobs && team.jobs.length > 1 ? ` (+${team.jobs.length - 1})` : ""}
                </text>
              )}
            </g>
          );
        })}

        {/* Spokes from each core to its members — the constellation lines. */}
        <g pointerEvents="none">
          {layout.agents.map((a) => {
            const home = layout.teams.find((t) => t.team.id === a.team);
            if (!home) return null;
            const dim = selectedTeam && selectedTeam !== a.team;
            return (
              <line
                key={`${a.name}-spoke`}
                x1={home.cx} y1={home.cy} x2={a.x} y2={a.y}
                stroke={accentFor(home.team.accent).hex}
                strokeWidth="0.75"
                opacity={dim ? 0.06 : 0.22}
              />
            );
          })}
        </g>

        {/* The agents themselves. */}
        {layout.agents.map((a) => {
          const home = layout.teams.find((t) => t.team.id === a.team);
          const accent = accentFor(home?.team.accent);
          const active = selectedAgent === a.name;
          const dim = (selectedTeam && selectedTeam !== a.team) || (!!focus && focus !== a.name
            && !snapshot.edges.some(
              (e) => (e.source === focus && e.target === a.name)
                || (e.target === focus && e.source === a.name)));
          return (
            <g
              key={a.name}
              className="cursor-pointer"
              opacity={dim ? 0.4 : 1}
              onMouseEnter={() => setHover(a.name)}
              onMouseLeave={() => setHover(null)}
              onClick={() => onSelectAgent(active ? null : a.name)}
            >
              {a.agent.live && (
                <circle cx={a.x} cy={a.y} r={a.radius * 2.1} fill={accent.hex}
                        opacity="0.3" className="colony-live-pulse"
                        style={{ ["--spin-origin" as string]: "center" }} />
              )}
              {active && (
                <circle cx={a.x} cy={a.y} r={a.radius + 8} fill="none"
                        stroke={accent.hex} strokeWidth="1.5" opacity="0.9" />
              )}
              <circle
                cx={a.x} cy={a.y} r={a.radius}
                fill={a.isInstrument ? "#334155" : `url(#sphere-${home?.team.accent ?? "amber"})`}
                stroke={a.isInstrument ? "#475569" : "transparent"}
                strokeWidth="1"
              />
              {/* A paused agent is struck through — it still holds its place in
                  the team, but it will not run or answer. */}
              {a.agent.paused && (
                <line
                  x1={a.x - a.radius} y1={a.y + a.radius}
                  x2={a.x + a.radius} y2={a.y - a.radius}
                  stroke="#f43f5e" strokeWidth="2" opacity="0.9"
                />
              )}
              {!a.isInstrument && (
                <text
                  x={a.x} y={a.y + a.radius + 15}
                  textAnchor="middle"
                  className="fill-slate-300 text-[11px]"
                  style={{ paintOrder: "stroke", stroke: "#05060a", strokeWidth: 3 }}
                >
                  {agentLabel(a.name)}
                </text>
              )}
            </g>
          );
        })}
      </svg>

      <Legend />
      {hover && <HoverCard snapshot={snapshot} name={hover} />}
    </div>
  );
}

function Legend() {
  return (
    <div className="pointer-events-none absolute bottom-4 left-4 space-y-1.5 rounded-lg
                    border border-slate-800/80 bg-slate-950/70 px-3 py-2.5 text-[11px]
                    text-slate-400 backdrop-blur">
      <div className="flex items-center gap-2">
        <span className="h-2.5 w-2.5 rounded-full bg-amber-400" />
        Bigger body = more judged runs behind it
      </div>
      <div className="flex items-center gap-2">
        <span className="h-px w-4 bg-slate-500" />
        A line is a real handoff, recorded in the run log
      </div>
      <div className="flex items-center gap-2">
        <span className="h-2.5 w-2.5 rounded-full bg-emerald-400/60" />
        A halo means it worked in the last few minutes
      </div>
    </div>
  );
}

function HoverCard({ snapshot, name }: { snapshot: ColonySnapshot; name: string }) {
  const agent = snapshot.agents.find((a) => a.name === name);
  if (!agent) return null;
  const handoffs = snapshot.edges.filter((e) => e.source === name || e.target === name);
  const total = handoffs.reduce((n, e) => n + e.count, 0);
  return (
    <div className="pointer-events-none absolute right-4 top-4 w-60 rounded-lg border
                    border-slate-700 bg-slate-950/90 p-3 text-xs backdrop-blur">
      <div className="text-sm font-semibold text-slate-100">{agentLabel(name)}</div>
      <div className={cn("mt-0.5", agent.is_instrument ? "text-slate-500" : "text-slate-400")}>
        {agent.is_instrument ? "A step in the pipeline, not a colleague" : agent.trust_level_name}
      </div>
      {!agent.is_instrument && (
        <div className="mt-2 text-slate-400">
          {agent.total_runs > 0
            ? `${agent.clean_runs}/${agent.total_runs} clean judged runs`
            : "No judged run yet"}
        </div>
      )}
      <div className="mt-1 text-slate-500">
        {total > 0 ? `${total} handoffs recorded` : "No handoffs recorded yet"}
      </div>
    </div>
  );
}
