import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ExternalLink, X } from "lucide-react";
import { api } from "../../lib/api";
import type { ColonyAgentDetail } from "../../lib/types";
import { agentLabel, agentRole, cn, formatDate, rosterFor } from "../../lib/utils";
import { BadgePill, Button, ErrorNote, ProgressBar, RosterAvatar } from "../ui";
import { AgentChat } from "./AgentChat";
import { ModelPicker } from "./ModelPicker";

const LEVEL_STYLES: Record<number, string> = {
  0: "bg-slate-700/40 text-slate-300 border-slate-600",
  1: "bg-sky-400/10 text-sky-300 border-sky-400/40",
  2: "bg-violet-400/10 text-violet-300 border-violet-400/40",
  3: "bg-amber-400/15 text-amber-300 border-amber-400/40",
};

type Pane = "work" | "chat" | "settings";

export function AgentDrawer({
  agent, onClose, onActed, onOpenSecretary,
}: {
  agent: string;
  onClose: () => void;
  onActed: () => void;
  onOpenSecretary: () => void;
}) {
  const [pane, setPane] = useState<Pane>("work");
  const detail = useQuery({
    queryKey: ["colony-agent", agent],
    queryFn: () => api.getColonyAgent(agent),
    refetchInterval: 20_000,
  });

  // A different agent selected means a different profile — start at their work
  // rather than leaving the previous agent's open pane selected.
  useEffect(() => setPane("work"), [agent]);

  const d = detail.data;
  const roster = rosterFor(agent);

  return (
    <aside className="flex h-full min-h-0 w-[26rem] shrink-0 flex-col rounded-xl border
                      border-slate-800 bg-slate-950/70">
      <header className="flex items-start gap-3 border-b border-slate-800 px-4 py-3.5">
        <RosterAvatar src={roster?.avatar} name={agentLabel(agent)} className="h-11 w-11" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h2 className="truncate font-display text-lg text-slate-100">
              {agentLabel(agent)}
            </h2>
            {d?.live && (
              <span className="flex items-center gap-1 text-[11px] text-emerald-300">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />working
              </span>
            )}
          </div>
          <p className="truncate text-xs text-slate-500">
            {agentRole(agent)}
            {d?.team_name ? ` · ${d.team_name}` : ""}
          </p>
        </div>
        <button onClick={onClose} className="text-slate-500 hover:text-slate-300">
          <X className="h-4 w-4" />
        </button>
      </header>

      {detail.isError && (
        <div className="p-4"><ErrorNote>{(detail.error as Error).message}</ErrorNote></div>
      )}
      {!d && !detail.isError && (
        <p className="p-4 text-sm text-slate-500">Loading…</p>
      )}

      {d && (
        <>
          {d.is_instrument ? (
            <div className="space-y-3 p-4 text-sm text-slate-400">
              <p>
                {agentLabel(agent)} is a step in the pipeline rather than a colleague —
                it renders and routes, it doesn't judge. That's why it has no trust score,
                no instructions and nothing to say.
              </p>
              <RecentWork detail={d} />
            </div>
          ) : !d.chattable ? (
            <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4">
              <Standing detail={d} />
              <p className="text-sm text-slate-400">
                Abigail is your personal assistant, not part of the workforce pipelines.
                She keeps everything personal in her own private store, so she's talked
                to — and her personality edited — in her own tab rather than here.
              </p>
              <Button onClick={onOpenSecretary} className="w-full">
                Open Abigail's tab <ExternalLink className="ml-1.5 h-3.5 w-3.5" />
              </Button>
              {/* Her model IS settable here. The backend only ever offers her
                  Claude models and refuses anything else (rule 16). */}
              <ModelPicker agent={d.name} onSaved={() => detail.refetch()} />
              <RecentWork detail={d} />
            </div>
          ) : (
            <>
              <nav className="flex gap-1 border-b border-slate-800 px-3 py-2">
                {(["work", "chat", "settings"] as Pane[]).map((p) => (
                  <button
                    key={p}
                    onClick={() => setPane(p)}
                    className={cn(
                      "rounded-lg px-3 py-1.5 text-xs font-medium capitalize transition-colors",
                      pane === p
                        ? "bg-amber-400/10 text-amber-300"
                        : "text-slate-500 hover:bg-slate-800/60 hover:text-slate-300",
                    )}
                  >
                    {p === "work" ? "Performance" : p}
                  </button>
                ))}
              </nav>
              <div className="min-h-0 flex-1 overflow-y-auto p-4">
                {pane === "work" && (
                  <div className="space-y-4">
                    <Standing detail={d} />
                    <Handoffs detail={d} />
                    <RecentWork detail={d} />
                  </div>
                )}
                {pane === "chat" && (
                  <div className="h-full">
                    <AgentChat agent={agent} initialMessages={d.messages}
                               onActed={onActed} />
                  </div>
                )}
                {pane === "settings" && (
                  <AgentSettingsPane detail={d} onSaved={() => detail.refetch()} />
                )}
              </div>
            </>
          )}
        </>
      )}
    </aside>
  );
}

function Standing({ detail: d }: { detail: ColonyAgentDetail }) {
  return (
    <section>
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
          Standing
        </h3>
        <BadgePill className={cn(LEVEL_STYLES[d.trust_level] ?? LEVEL_STYLES[0])}>
          {d.trust_level_name}
        </BadgePill>
      </div>
      {d.total_runs === 0 ? (
        <p className="text-sm text-slate-500">
          No judged run yet — trust appears once they've done reviewed work.
        </p>
      ) : (
        <>
          <div className="flex items-center gap-3">
            <ProgressBar
              value={d.trust_score}
              colorClass={d.trust_score >= 80 ? "bg-emerald-400" : "bg-amber-400"}
              className="flex-1"
            />
            <span className="w-11 text-right font-mono text-xs text-slate-400">
              {d.trust_score.toFixed(0)}%
            </span>
          </div>
          <p className="mt-1.5 text-xs text-slate-500">
            {d.clean_runs}/{d.total_runs} clean judged runs
            {d.consecutive_failures > 0 && (
              <span className="text-rose-400">
                {" "}· {d.consecutive_failures} consecutive failure
                {d.consecutive_failures > 1 ? "s" : ""}
              </span>
            )}
          </p>
          <p className="mt-1 text-xs text-slate-500">{d.promotion_note}</p>
        </>
      )}
      {/* Only shown when it differs from the default — an agent on the house
          model needs no announcement. */}
      {d.settings.model && (
        <p className="mt-2 text-xs text-slate-500">
          Thinking with <span className="text-slate-300">{d.settings.model}</span>.
        </p>
      )}
      {d.goal_note && (
        <p className="mt-3 rounded-lg border border-amber-400/25 bg-amber-400/5 px-3 py-2
                      text-xs text-amber-200/90">
          Working toward: {d.goal_note}
        </p>
      )}
    </section>
  );
}

function Handoffs({ detail: d }: { detail: ColonyAgentDetail }) {
  if (d.hands_to.length === 0 && d.receives_from.length === 0) return null;
  return (
    <section>
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
        Who they work with
      </h3>
      <div className="space-y-1 text-xs text-slate-400">
        {d.receives_from.map((e) => (
          <div key={`in-${e.source}`}>
            Takes work from <span className="text-slate-200">{agentLabel(e.source)}</span>
            {" "}· {e.count}×
          </div>
        ))}
        {d.hands_to.map((e) => (
          <div key={`out-${e.target}`}>
            Hands work to <span className="text-slate-200">{agentLabel(e.target)}</span>
            {" "}· {e.count}×
          </div>
        ))}
      </div>
    </section>
  );
}

function RecentWork({ detail: d }: { detail: ColonyAgentDetail }) {
  return (
    <section>
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
        Recent work
      </h3>
      {d.recent_runs.length === 0 ? (
        <p className="text-sm text-slate-500">Nothing logged yet.</p>
      ) : (
        <div className="space-y-1.5">
          {d.recent_runs.map((r) => (
            <div key={r.id} className="rounded-lg border border-slate-800 bg-slate-950/50
                                       px-3 py-2 text-xs">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-slate-300">{r.step}</span>
                {/* A mechanical step is NOT a pass — rule 14. Showing it as one
                    would turn clean-run stats into an uptime metric. */}
                {r.judged ? (
                  <span className={r.passed_review ? "text-emerald-400" : "text-rose-400"}>
                    {r.passed_review ? "passed" : "failed"}
                  </span>
                ) : (
                  <span className="text-slate-600">not judged</span>
                )}
              </div>
              {r.output_summary && (
                <div className="mt-0.5 truncate text-slate-500">{r.output_summary}</div>
              )}
              <div className="mt-0.5 text-slate-600">{formatDate(r.timestamp)}</div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function AgentSettingsPane({
  detail: d, onSaved,
}: { detail: ColonyAgentDetail; onSaved: () => void }) {
  const [text, setText] = useState(d.settings.custom_instructions);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setText(d.settings.custom_instructions);
    setSaved(false);
  }, [d.name, d.settings.custom_instructions]);

  const save = async (patch: { custom_instructions?: string; paused?: boolean }) => {
    setSaving(true);
    setError(null);
    try {
      await api.setAgentSettings(d.name, patch);
      setSaved(true);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-5">
      <ModelPicker agent={d.name} onSaved={onSaved} />

      <section>
        <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-slate-500">
          Standing instructions
        </h3>
        <p className="mb-2 text-xs leading-relaxed text-slate-500">
          Added to {agentLabel(d.name)}'s instructions every time they work — in the
          pipelines as well as in chat. Keep it short: most of them run on the local
          model, which has a tight budget for context.
        </p>
        <textarea
          value={text}
          onChange={(e) => { setText(e.target.value); setSaved(false); }}
          rows={6}
          placeholder="e.g. Prefer quotes about service over quotes about knowledge."
          className="w-full resize-none rounded-lg border border-slate-700 bg-slate-900/80
                     px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600
                     focus:border-amber-400/50 focus:outline-none"
        />
        <div className="mt-2 flex items-center gap-3">
          <Button onClick={() => save({ custom_instructions: text })}
                  loading={saving} disabled={saving}>
            Save
          </Button>
          {saved && <span className="text-xs text-emerald-300">Saved.</span>}
        </div>
      </section>

      <section className="rounded-lg border border-slate-800 bg-slate-950/50 p-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-sm text-slate-200">
              {d.settings.paused ? "Paused" : "Active"}
            </div>
            <p className="mt-0.5 text-xs text-slate-500">
              A paused agent won't answer in chat or take part in a team consultation.
              It keeps its place on the team and its trust history.
            </p>
          </div>
          <Button
            variant={d.settings.paused ? "primary" : "ghost"}
            onClick={() => save({ paused: !d.settings.paused })}
            disabled={saving}
          >
            {d.settings.paused ? "Un-pause" : "Pause"}
          </Button>
        </div>
      </section>

      {error && <ErrorNote>{error}</ErrorNote>}
    </div>
  );
}
