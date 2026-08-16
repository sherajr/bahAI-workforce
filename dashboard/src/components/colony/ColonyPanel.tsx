import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { GitBranch, Orbit, ShieldCheck, TrendingUp, Wallet } from "lucide-react";
import { api } from "../../lib/api";
import { getColonyUi, patchColonyUi } from "../../lib/settings";
import type { Tab } from "../Nav";
import { agentLabel, badgeClasses, cn, formatDate, rosterFor } from "../../lib/utils";
import { BadgePill, Card, CardContent, CardHeader, CardTitle, ErrorNote, ProgressBar,
  RosterAvatar } from "../ui";
import { ActionQueue } from "./ActionQueue";
import { AgentDrawer } from "./AgentDrawer";
import { ColonyGraph } from "./ColonyGraph";
import { TeamDrawer } from "./TeamDrawer";
import { TreasuryView } from "./TreasuryView";

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

  useEffect(() => { patchColonyUi({ view, agent, team, consultJobId }); },
    [view, agent, team, consultJobId]);

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

  const snapshot = colony.data;
  const openTeam = snapshot?.teams.find((t) => t.id === team) ?? null;

  return (
    <div className="mx-auto flex h-full min-h-0 max-w-[104rem] flex-col gap-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl text-slate-100">The Colony</h1>
          <p className="text-sm text-slate-500">
            Everyone who works on this, what they've earned, and how the work moves
            between them.
          </p>
        </div>
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
      </header>

      {colony.isError && (
        <ErrorNote>Could not load the colony: {(colony.error as Error).message}</ErrorNote>
      )}

      <div className="flex min-h-0 flex-1 gap-4">
        <div className="min-w-0 flex-1 overflow-y-auto">
          {!snapshot && !colony.isError && (
            <p className="text-sm text-slate-500">Loading the colony…</p>
          )}

          {snapshot && (view === "map" || view === "handoffs") && (
            <div className="space-y-4">
              <ColonyGraph
                snapshot={snapshot}
                selectedAgent={agent}
                selectedTeam={team}
                onSelectAgent={selectAgent}
                onSelectTeam={selectTeam}
                emphasis={view === "handoffs" ? "handoffs" : "map"}
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

          {view === "performance" && <PerformanceView />}
          {view === "treasury" && <TreasuryView />}
          {view === "approvals" && (
            <div className="max-w-2xl">
              <ActionQueue onResolved={() => colony.refetch()} />
            </div>
          )}
        </div>

        {agent && (
          <AgentDrawer
            agent={agent}
            onClose={() => setAgent(null)}
            onActed={() => colony.refetch()}
            onOpenSecretary={() => onNavigate("secretary")}
          />
        )}
        {openTeam && (
          <TeamDrawer
            team={openTeam}
            onClose={() => setTeam(null)}
            consultJobId={consultJobId}
            setConsultJobId={setConsultJobId}
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
