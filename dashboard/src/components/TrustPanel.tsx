import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { AgentStatus } from "../lib/types";
import { badgeClasses, cn, formatDate, rosterFor } from "../lib/utils";
import { BadgePill, Card, CardContent, CardHeader, CardTitle, ErrorNote, ProgressBar, RosterAvatar } from "./ui";

const LEVEL_STYLES: Record<number, string> = {
  0: "bg-slate-700/40 text-slate-300 border-slate-600",
  1: "bg-sky-400/10 text-sky-300 border-sky-400/40",
  2: "bg-violet-400/10 text-violet-300 border-violet-400/40",
  3: "bg-amber-400/15 text-amber-300 border-amber-400/40",
};

function promotionNote(a: AgentStatus): string {
  if (a.trust_level >= 3) return "At the highest trust level.";
  const parts: string[] = [];
  if (a.total_runs < 5) parts.push(`${5 - a.total_runs} more run${5 - a.total_runs > 1 ? "s" : ""} (minimum 5)`);
  if (a.trust_score < 80) parts.push(`a clean rate of 80% (currently ${a.trust_score.toFixed(0)}%)`);
  if (parts.length === 0) return "Meets the promotion condition — advances on its next clean run.";
  return `Needs ${parts.join(" and ")} to advance.`;
}

export function TrustPanel() {
  const agents = useQuery({ queryKey: ["agents"], queryFn: api.getAgents, refetchInterval: 30_000 });
  const trust = useQuery({ queryKey: ["trust"], queryFn: api.getTrustReport, refetchInterval: 60_000 });

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <Card>
        <CardHeader>
          <CardTitle>Agent trust</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-slate-400">
            Trust is earned through clean, reviewed work: agents advance at an 80% clean rate over
            5+ runs, and step back after 2 consecutive failures. Trust has a real consequence:
            until the Reviewer reaches Human-on-the-loop (level 2), publishing an Etsy draft asks
            for your confirmation first.
          </p>
          {agents.isError && (
            <ErrorNote>Could not load agents: {(agents.error as Error).message}</ErrorNote>
          )}
          {/* Only agents that have actually done reviewed work — listing
              never-run roles here would be a promise without a deed. */}
          {agents.data && agents.data.every((a) => a.total_runs === 0) && (
            <p className="text-sm text-slate-500">
              No agent has completed a reviewed run yet — run the pipeline once and trust
              scores will appear here.
            </p>
          )}
          {agents.data?.filter((a) => a.total_runs > 0).map((a) => {
            const r = rosterFor(a.name);
            return (
            <div key={a.name} className="rounded-lg border border-slate-800 bg-slate-950/50 p-4">
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2.5">
                  <RosterAvatar src={r?.avatar} name={r?.name ?? a.name} className="h-8 w-8" />
                  <div className="leading-tight">
                    <span className="block text-sm font-semibold text-slate-100">
                      {r?.name ?? a.name.charAt(0).toUpperCase() + a.name.slice(1)}
                    </span>
                    <span className="block text-xs text-slate-500">{r?.role ?? "agent"}</span>
                  </div>
                </div>
                <BadgePill className={cn(LEVEL_STYLES[a.trust_level] ?? LEVEL_STYLES[0])}>
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
                    <span className="text-rose-400"> · {a.consecutive_failures} consecutive failure{a.consecutive_failures > 1 ? "s" : ""}</span>
                  )}
                </span>
                <span>{promotionNote(a)}</span>
              </div>
            </div>
            );
          })}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Product quality history</CardTitle>
        </CardHeader>
        <CardContent>
          {trust.data && (
            <p className="mb-3 text-sm text-slate-400">
              {trust.data.total} products · {trust.data.passed} passed · average score{" "}
              {trust.data.average_score}/10
            </p>
          )}
          <div className="space-y-2">
            {trust.data?.products.map((p) => (
              <div
                key={p.product_id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-800 bg-slate-950/50 px-4 py-2.5"
              >
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
