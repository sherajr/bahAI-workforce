import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { GitBranch, Orbit, ShieldCheck, TrendingUp, Wallet } from "lucide-react";
import { api } from "../../lib/api";
import { getColonyUi, patchColonyUi } from "../../lib/settings";
import type { Tab } from "../Nav";
import { agentLabel, badgeClasses, cn, formatDate, rosterFor } from "../../lib/utils";
import { BadgePill, Card, CardContent, CardHeader, CardTitle, ErrorNote, ProgressBar,
  RosterAvatar } from "../ui";
import { ActionQueue } from "./ActionQueue";
import { ActorDrawer } from "./ActorDrawer";
import { AgentDrawer } from "./AgentDrawer";
import { ColonyGraph } from "./ColonyGraph";
import { GroupingDrawer } from "./GroupingDrawer";
import { WORKFORCE_ANCHOR, WORLD_MORPH_MS } from "./layout";
import { RealWorldGraph, workforceScreenAnchor } from "./RealWorldGraph";
import { TeamDrawer } from "./TeamDrawer";
import { TreasuryView } from "./TreasuryView";
import { WorkforceDrawer } from "./WorkforceDrawer";

type View = "map" | "performance" | "handoffs" | "approvals" | "treasury";

const VIEWS: { id: View; label: string; icon: typeof Orbit }[] = [
  { id: "map", label: "Map", icon: Orbit },
  { id: "performance", label: "Performance", icon: TrendingUp },
  { id: "handoffs", label: "Handoffs", icon: GitBranch },
  { id: "approvals", label: "Approvals", icon: ShieldCheck },
  { id: "treasury", label: "Treasury", icon: Wallet },
];

export function ColonyPanel({ onNavigate }: { onNavigate: (tab: Tab) => void }) {
  // Restored from localStorage: switching tabs unmounts this panel, and losing
  // the selected agent and a running consultation every time is the same
  // problem the Video tab already solved.
  const saved = getColonyUi();
  const [view, setView] = useState<View>((saved.view as View) ?? "map");
  const [agent, setAgent] = useState<string | null>(saved.agent);
  const [team, setTeam] = useState<string | null>(saved.team);
  const [consultJobId, setConsultJobId] = useState<string | null>(saved.consultJobId);
  const [world, setWorld] = useState<"digital" | "real">(
    saved.world === "real" ? "real" : "digital",
  );
  const [rwActor, setRwActor] = useState<number | null>(null);
  const [rwGrouping, setRwGrouping] = useState<number | null>(null);
  const [rwWorkforce, setRwWorkforce] = useState(false);
  const [newNucleus, setNewNucleus] = useState("");
  const [newInstitution, setNewInstitution] = useState("");
  // false while a world-swap is folding the sky on screen into the workforce
  // light. Both skies use the same 1440x720 space and both draw that light, so
  // it is the one body that survives the swap and the fold anchors on it.
  const [expanded, setExpanded] = useState(true);
  const swapTimer = useRef<number | null>(null);
  useEffect(() => () => {
    if (swapTimer.current !== null) window.clearTimeout(swapTimer.current);
  }, []);

  const switchWorld = (next: "digital" | "real") => {
    // Ignore a second click mid-swap: re-entering would swap the world under a
    // fold that is already running and leave the incoming sky stuck at 2%.
    if (next === world || swapTimer.current !== null) return;
    setExpanded(false);
    swapTimer.current = window.setTimeout(() => {
      setWorld(next);
      // The incoming sky mounts folded and opens itself a frame later, so this
      // can be set in the same tick without racing the mount.
      setExpanded(true);
      swapTimer.current = null;
    }, WORLD_MORPH_MS);
  };

  useEffect(() => { patchColonyUi({ view, agent, team, consultJobId, world }); },
    [view, agent, team, consultJobId, world]);

  const colony = useQuery({
    queryKey: ["colony"],
    queryFn: api.getColony,
    // Faster while something is actually running: a live progress line that
    // updates every 15 seconds reads as a stuck one.
    refetchInterval: (query) =>
      query.state.data?.teams.some((t) => t.jobs?.length) ? 4_000 : 15_000,
  });

  const selectAgent = (name: string | null) => { setAgent(name); if (name) setTeam(null); };
  const selectTeam = (id: string | null) => { setTeam(id); if (id) setAgent(null); };

  const qc = useQueryClient();
  const nuclei = useQuery({
    queryKey: ["nuclei"],
    queryFn: api.getNucleiSnapshot,
    enabled: world === "real" || view === "map",
  });
  const addNucleus = useMutation({
    mutationFn: (name: string) => api.createNucleiGrouping("nucleus", name),
    onSuccess: () => { setNewNucleus(""); qc.invalidateQueries({ queryKey: ["nuclei"] }); },
  });
  const addInstitution = useMutation({
    mutationFn: (name: string) => api.createNucleiGrouping("institution", name),
    onSuccess: () => { setNewInstitution(""); qc.invalidateQueries({ queryKey: ["nuclei"] }); },
  });
  const moveNucleus = useMutation({
    mutationFn: ({ id, x, y }: { id: number; x: number; y: number }) =>
      api.setNucleiPosition(id, x, y),
    onSuccess: (snap) => { qc.setQueryData(["nuclei"], snap); },
  });
  const arrangeTables = useMutation({
    mutationFn: () => api.optimizeNucleiLayout(),
    onSuccess: (snap) => { qc.setQueryData(["nuclei"], snap); },
  });

  const snapshot = colony.data;
  const openTeam = snapshot?.teams.find((t) => t.id === team) ?? null;
  const rw = nuclei.data;

  return (
    <div className="mx-auto flex h-full min-h-0 max-w-[104rem] flex-col gap-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl text-slate-100">The Colony</h1>
          <p className="text-sm text-slate-500">
            {world === "real"
              ? "Your nuclei and friends. Each point of light is the Vision in that place."
              : "Everyone who works on this, what they've earned, and how the work moves between them."}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {view === "map" && (
            <div className="flex gap-1 rounded-xl border border-slate-800 bg-slate-950/60 p-1">
              <button
                type="button"
                onClick={() => switchWorld("digital")}
                className={cn(
                  "rounded-lg px-3.5 py-2 text-sm font-medium",
                  world === "digital" ? "bg-amber-400/10 text-amber-300" : "text-slate-400 hover:text-slate-200",
                )}
              >
                Digital World
              </button>
              <button
                type="button"
                onClick={() => switchWorld("real")}
                className={cn(
                  "rounded-lg px-3.5 py-2 text-sm font-medium",
                  world === "real" ? "bg-amber-400/10 text-amber-300" : "text-slate-400 hover:text-slate-200",
                )}
              >
                Material World
              </button>
            </div>
          )}
        <nav className="flex gap-1 rounded-xl border border-slate-800 bg-slate-950/60 p-1">
          {VIEWS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setView(id)}
              className={cn(
                "flex items-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium transition-colors",
                view === id
                  ? "bg-amber-400/10 text-amber-300"
                  : "text-slate-400 hover:bg-slate-800/60 hover:text-slate-200",
              )}
            >
              <Icon className="h-4 w-4" />
              {label}
              {id === "approvals" && !!snapshot?.pending_actions && (
                <span className="rounded-full bg-amber-400 px-1.5 text-[10px] font-bold
                                 leading-4 text-slate-950">
                  {snapshot.pending_actions}
                </span>
              )}
            </button>
          ))}
        </nav>
        </div>
      </header>

      {colony.isError && (
        <ErrorNote>Could not load the colony: {(colony.error as Error).message}</ErrorNote>
      )}

      <div className="flex min-h-0 flex-1 gap-4">
        <div className="min-w-0 flex-1 overflow-y-auto">
          {!snapshot && !colony.isError && (
            <p className="text-sm text-slate-500">Loading the colony…</p>
          )}

          {snapshot && (view === "map" || view === "handoffs") && world === "digital" && (
            <div className="space-y-4">
              <ColonyGraph
                snapshot={snapshot}
                selectedAgent={agent}
                selectedTeam={team}
                onSelectAgent={selectAgent}
                onSelectTeam={selectTeam}
                emphasis={view === "handoffs" ? "handoffs" : "map"}
                expanded={view === "map" ? expanded : true}
                anchor={workforceScreenAnchor(rw?.layout.workforce ?? WORKFORCE_ANCHOR)}
              />
              {view === "handoffs" && <HandoffList />}
              {view === "map" && !agent && !team && (
                <p className="text-center text-sm text-slate-500">
                  Click anyone to see their work, talk to them, or change how they work.
                  Click a team's centre to set its goals or consult it.
                </p>
              )}
            </div>
          )}

          {view === "map" && world === "real" && (
            <div className="space-y-4">
              {/* The map comes FIRST here, as it does in the Digital World.
                  That is not a preference: switching worlds folds one sky into
                  the workforce light and grows the other out of the same pixel,
                  and a control row above the map would sit that light ~110px
                  lower in this world than in the other, so the dot would jump
                  at the swap. Controls sit under the map instead. */}
              {rw && (
                <RealWorldGraph
                  snapshot={rw}
                  colony={snapshot}
                  selectedActor={rwActor}
                  selectedGrouping={rwGrouping}
                  workforceOpen={rwWorkforce}
                  expanded={expanded}
                  onSelectActor={(id) => {
                    setRwActor(id);
                    if (id) { setRwGrouping(null); setRwWorkforce(false); }
                  }}
                  onSelectGrouping={(id) => {
                    setRwGrouping(id);
                    if (id) { setRwActor(null); setRwWorkforce(false); }
                  }}
                  onSelectWorkforce={() => {
                    setRwWorkforce((open) => !open);
                    setRwActor(null);
                    setRwGrouping(null);
                  }}
                  onMoveGrouping={(id, x, y) => moveNucleus.mutateAsync({ id, x, y })}
                />
              )}
              {nuclei.isError && (
                <ErrorNote>Could not load the Material World: {(nuclei.error as Error).message}</ErrorNote>
              )}
              <div className="flex flex-wrap items-end gap-2">
                <label className="min-w-[16rem] flex-1 text-xs text-slate-500">
                  A new nucleus
                  <input
                    value={newNucleus}
                    onChange={(e) => setNewNucleus(e.target.value)}
                    placeholder="e.g. Thursday supper"
                    className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
                  />
                </label>
                <button
                  type="button"
                  disabled={!newNucleus.trim() || addNucleus.isPending}
                  onClick={() => addNucleus.mutate(newNucleus.trim())}
                  className="rounded-lg border border-amber-400/40 bg-amber-400/10 px-3 py-2 text-sm font-medium text-amber-200 disabled:opacity-40"
                >
                  Add this nucleus
                </button>
                <button
                  type="button"
                  disabled={!rw || rw.groupings.filter(
                    (g) => g.kind_slug !== "institution" && g.kind_slug !== "workforce",
                  ).length < 2 || arrangeTables.isPending}
                  onClick={() => arrangeTables.mutate()}
                  title="Places every table by size, who gathers where, and who walks with whom."
                  className="rounded-lg border border-slate-600 bg-slate-900 px-3 py-2 text-sm font-medium text-slate-200 hover:border-slate-500 disabled:opacity-40"
                >
                  {arrangeTables.isPending ? "Arranging…" : "Optimize locations"}
                </button>
              </div>
              <div className="flex flex-wrap items-end gap-2">
                <label className="min-w-[16rem] flex-1 text-xs text-slate-500">
                  A local institution
                  <input
                    value={newInstitution}
                    onChange={(e) => setNewInstitution(e.target.value)}
                    placeholder="e.g. Local Spiritual Assembly"
                    list="rw-institution-suggestions"
                    className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
                  />
                  <datalist id="rw-institution-suggestions">
                    <option value="Local Spiritual Assembly" />
                    <option value="Regional Institute" />
                    <option value="Auxiliary Board" />
                    <option value="Area Teaching Committee" />
                  </datalist>
                </label>
                <button
                  type="button"
                  disabled={!newInstitution.trim() || addInstitution.isPending}
                  onClick={() => addInstitution.mutate(newInstitution.trim())}
                  className="rounded-lg border border-amber-200/30 bg-amber-100/10 px-3 py-2 text-sm font-medium text-amber-100 disabled:opacity-40"
                >
                  Add this institution
                </button>
              </div>
              {addInstitution.isError && (
                <ErrorNote>{(addInstitution.error as Error).message}</ErrorNote>
              )}
              {(moveNucleus.isError || arrangeTables.isError) && (
                <ErrorNote>
                  {((moveNucleus.error || arrangeTables.error) as Error).message}
                </ErrorNote>
              )}
              {!rwActor && !rwGrouping && !rwWorkforce && (
                <p className="text-center text-sm text-slate-500">
                  Scroll to zoom, or drag the empty sky to move around.
                  Drag a nucleus to place it. Optimize locations sits related tables together.
                  Click a nucleus to add someone who sat with you. Click a friend to say
                  they gather regularly, or that they have begun to serve — they will sit closer.
                  Click a person, family, or table to see every connection.
                  Open a friend to take them off a table or an institution, or off the map.
                  Open a junior youth family to list the people in it.
                  Add a local institution on the left — LSA, Regional Institute,
                  Auxiliary Board, teaching committee. Open one to note who serves,
                  or to take it off the map.
                  Click the Bahá'í Workforce to open it: the agents fan out, you can
                  put real people on it, and you can write a WhatsApp message to a
                  friend or to a nucleus's group.
                </p>
              )}
            </div>
          )}

          {view === "performance" && <PerformanceView />}
          {view === "treasury" && <TreasuryView />}
          {view === "approvals" && (
            <div className="max-w-2xl">
              <ActionQueue onResolved={() => colony.refetch()} />
            </div>
          )}
        </div>

        {agent && world === "digital" && (
          <AgentDrawer
            agent={agent}
            onClose={() => setAgent(null)}
            onActed={() => colony.refetch()}
            onOpenSecretary={() => onNavigate("secretary")}
          />
        )}
        {openTeam && world === "digital" && (
          <TeamDrawer
            team={openTeam}
            onClose={() => setTeam(null)}
            consultJobId={consultJobId}
            setConsultJobId={setConsultJobId}
          />
        )}
        {world === "real" && rw && rwActor && (
          <ActorDrawer
            actorId={rwActor}
            snapshot={rw}
            onClose={() => setRwActor(null)}
            onChanged={() => qc.invalidateQueries({ queryKey: ["nuclei"] })}
            onSelectActor={(id) => setRwActor(id)}
          />
        )}
        {world === "real" && rw && rwWorkforce && (
          <WorkforceDrawer
            snapshot={rw}
            onClose={() => setRwWorkforce(false)}
            onChanged={() => qc.invalidateQueries({ queryKey: ["nuclei"] })}
            onSelectActor={(id) => { setRwActor(id); setRwWorkforce(false); }}
            onOpenDigital={() => { setRwWorkforce(false); switchWorld("digital"); }}
          />
        )}
        {world === "real" && rw && rwGrouping && (
          <GroupingDrawer
            groupingId={rwGrouping}
            snapshot={rw}
            onClose={() => setRwGrouping(null)}
            onChanged={() => qc.invalidateQueries({ queryKey: ["nuclei"] })}
            onSelectActor={(id) => { setRwActor(id); setRwGrouping(null); }}
          />
        )}
      </div>
    </div>
  );
}

/** The trust table and product quality history that used to be the Trust tab. */
function PerformanceView() {
  const colony = useQuery({ queryKey: ["colony"], queryFn: api.getColony });
  const trust = useQuery({
    queryKey: ["trust"], queryFn: api.getTrustReport, refetchInterval: 60_000,
  });

  // Only agents that have actually done reviewed work — listing a never-run
  // role here would be a promise without a deed.
  const worked = (colony.data?.agents ?? [])
    .filter((a) => !a.is_instrument && a.total_runs > 0)
    .sort((a, b) => b.trust_score - a.trust_score);

  return (
    <div className="max-w-4xl space-y-5">
      <Card>
        <CardHeader><CardTitle>Trust</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-slate-400">
            Trust is earned through clean, reviewed work: agents advance at an 80% clean
            rate over 5+ runs, and step back after 2 consecutive failures. It has a real
            consequence — until Amos reaches Human-on-the-loop, publishing an Etsy draft
            asks for your confirmation first.
          </p>
          {worked.length === 0 && (
            <p className="text-sm text-slate-500">
              No agent has completed a reviewed run yet — run a pipeline once and trust
              scores appear here.
            </p>
          )}
          {worked.map((a) => (
            <div key={a.name} className="rounded-lg border border-slate-800 bg-slate-950/50 p-4">
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2.5">
                  <RosterAvatar src={rosterFor(a.name)?.avatar} name={agentLabel(a.name)}
                                className="h-8 w-8" />
                  <div className="leading-tight">
                    <span className="block text-sm font-semibold text-slate-100">
                      {agentLabel(a.name)}
                    </span>
                    <span className="block text-xs text-slate-500">
                      {rosterFor(a.name)?.role ?? "agent"}
                    </span>
                  </div>
                </div>
                <BadgePill className="border-slate-600 bg-slate-700/40 text-slate-300">
                  {a.trust_level_name}
                </BadgePill>
              </div>
              <div className="flex items-center gap-3">
                <ProgressBar
                  value={a.trust_score}
                  colorClass={a.trust_score >= 80 ? "bg-emerald-400" : "bg-amber-400"}
                  className="flex-1"
                />
                <span className="w-12 text-right font-mono text-xs text-slate-400">
                  {a.trust_score.toFixed(0)}%
                </span>
              </div>
              <div className="mt-2 flex flex-wrap justify-between gap-2 text-xs text-slate-500">
                <span>
                  {a.clean_runs}/{a.total_runs} clean runs
                  {a.consecutive_failures > 0 && (
                    <span className="text-rose-400">
                      {" "}· {a.consecutive_failures} consecutive failure
                      {a.consecutive_failures > 1 ? "s" : ""}
                    </span>
                  )}
                </span>
                <span>{a.promotion_note}</span>
              </div>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Product quality history</CardTitle></CardHeader>
        <CardContent>
          {trust.data && (
            <p className="mb-3 text-sm text-slate-400">
              {trust.data.total} products · {trust.data.passed} passed · average score{" "}
              {trust.data.average_score}/10
            </p>
          )}
          <div className="space-y-2">
            {trust.data?.products.map((p) => (
              <div key={p.product_id}
                   className="flex flex-wrap items-center justify-between gap-2 rounded-lg
                              border border-slate-800 bg-slate-950/50 px-4 py-2.5">
                <div className="min-w-0">
                  <div className="truncate text-sm text-slate-200">{p.title}</div>
                  <div className="text-xs text-slate-600">{formatDate(p.created_at)}</div>
                </div>
                <div className="flex items-center gap-3">
                  <span className="font-mono text-sm text-slate-300">{p.overall.toFixed(1)}</span>
                  <BadgePill className={badgeClasses(p.badge)}>{p.badge}</BadgePill>
                </div>
              </div>
            ))}
            {trust.data?.products.length === 0 && (
              <p className="text-sm text-slate-500">No scored products yet.</p>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function HandoffList() {
  const handoffs = useQuery({
    queryKey: ["colony-handoffs"],
    queryFn: () => api.getColonyHandoffs(30),
    refetchInterval: 30_000,
  });

  const edges = handoffs.data?.edges ?? [];
  const runs = handoffs.data?.recent_runs ?? [];

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader><CardTitle>Who hands work to whom</CardTitle></CardHeader>
        <CardContent>
          <p className="mb-3 text-sm text-slate-400">
            Counted from the run log over the last 30 days — this is what actually
            happened, not how the pipeline is drawn.
          </p>
          {edges.length === 0 && <p className="text-sm text-slate-500">No handoffs yet.</p>}
          <div className="space-y-1.5">
            {edges.map((e) => (
              <div key={`${e.source}-${e.target}`}
                   className="flex items-center justify-between gap-3 rounded-lg border
                              border-slate-800 bg-slate-950/50 px-3 py-2 text-sm">
                <span className="text-slate-300">
                  {agentLabel(e.source)} <span className="text-slate-600">→</span>{" "}
                  {agentLabel(e.target)}
                </span>
                <span className="font-mono text-xs text-slate-500">{e.count}×</span>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Latest steps</CardTitle></CardHeader>
        <CardContent>
          <div className="space-y-1.5">
            {runs.slice(0, 20).map((r) => (
              <div key={r.id} className="rounded-lg border border-slate-800 bg-slate-950/50
                                         px-3 py-2 text-xs">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-slate-300">
                    {agentLabel(r.agent)} · {r.step}
                  </span>
                  {r.judged ? (
                    <span className={r.passed_review ? "text-emerald-400" : "text-rose-400"}>
                      {r.passed_review ? "passed" : "failed"}
                    </span>
                  ) : (
                    <span className="text-slate-600">not judged</span>
                  )}
                </div>
                <div className="mt-0.5 text-slate-600">{formatDate(r.timestamp)}</div>
              </div>
            ))}
            {runs.length === 0 && <p className="text-sm text-slate-500">Nothing logged yet.</p>}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
