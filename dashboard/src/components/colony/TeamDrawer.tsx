import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2, MessagesSquare, Play, Target, Trash2, X } from "lucide-react";
import { api } from "../../lib/api";
import type { ColonyTeam, Job, TeamConsultResult, TeamGoal } from "../../lib/types";
import { accentFor, agentLabel, cn, rosterFor } from "../../lib/utils";
import { Button, ErrorNote, ProgressBar, RosterAvatar } from "../ui";
import { useVideoJob } from "../VideoPanel";

const KIND_LABELS: Record<string, string> = {
  quote_card: "quote cards",
  bookmark: "bookmarks",
  video: "a video",
};

export function TeamDrawer({
  team, onClose, consultJobId, setConsultJobId,
}: {
  team: ColonyTeam;
  onClose: () => void;
  consultJobId: string | null;
  setConsultJobId: (id: string | null) => void;
}) {
  const accent = accentFor(team.accent);
  const goals = useQuery({
    queryKey: ["colony-goals", team.id],
    queryFn: () => api.getGoals(team.id),
    refetchInterval: 30_000,
  });

  return (
    <aside className="flex h-full min-h-0 w-[26rem] shrink-0 flex-col rounded-xl border
                      border-slate-800 bg-slate-950/70">
      <header className="flex items-start gap-3 border-b border-slate-800 px-4 py-3.5">
        <span className={cn("mt-1.5 h-3 w-3 shrink-0 rounded-full", accent.dot)} />
        <div className="min-w-0 flex-1">
          <h2 className="font-display text-lg text-slate-100">{team.name}</h2>
          <p className="text-xs leading-relaxed text-slate-500">{team.blurb}</p>
        </div>
        <button onClick={onClose} className="text-slate-500 hover:text-slate-300">
          <X className="h-4 w-4" />
        </button>
      </header>

      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4">
        {/* Work in flight, in words. Whoever started it — you, Abigail, or a
            goal — it is the same run, shown the same way. */}
        {(team.jobs ?? []).map((j) => (
          <div key={j.job_id}
               className="rounded-lg border border-emerald-400/25 bg-emerald-400/5 p-3">
            <div className="flex items-center gap-2 text-xs font-semibold text-emerald-300">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Working — {j.label}
            </div>
            <p className="mt-1.5 text-xs text-slate-300">{j.progress}</p>
            <p className="mt-1 text-[11px] text-slate-500">
              Started by {j.started_by_label} · job {j.job_id} · the Pipeline tab is
              showing it
            </p>
          </div>
        ))}

        <section>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
            Who's on it
          </h3>
          <div className="flex flex-wrap gap-2">
            {team.members.map((m) => (
              <span key={m} className="flex items-center gap-1.5 rounded-full border
                                       border-slate-700 bg-slate-900/60 py-1 pl-1 pr-2.5
                                       text-xs text-slate-300">
                <RosterAvatar src={rosterFor(m)?.avatar} name={agentLabel(m)}
                              className="h-5 w-5" />
                {agentLabel(m)}
              </span>
            ))}
          </div>
          {team.instruments.length > 0 && (
            <p className="mt-2 text-[11px] text-slate-600">
              Work also passes through {team.instruments.map(agentLabel).join(" and ")} —
              pipeline steps, not people.
            </p>
          )}
        </section>

        <GoalsSection team={team} goals={goals.data?.goals ?? []}
                      onChanged={() => goals.refetch()} />

        <ConsultSection team={team} jobId={consultJobId} setJobId={setConsultJobId} />
      </div>
    </aside>
  );
}

function GoalsSection({
  team, goals, onChanged,
}: { team: ColonyTeam; goals: TeamGoal[]; onChanged: () => void }) {
  const [adding, setAdding] = useState(false);
  const [text, setText] = useState("");
  const [detail, setDetail] = useState("");
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [launched, setLaunched] = useState<string | null>(null);

  const active = goals.filter((g) => g.status === "active");
  const canLaunch = team.goal_kinds.length > 0;

  const create = async () => {
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.createGoal({
        team: team.id, goal: text.trim(), detail: detail.trim(),
        target_count: target.trim() ? Number(target) : null,
      });
      setText(""); setDetail(""); setTarget(""); setAdding(false);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save that goal.");
    } finally {
      setBusy(false);
    }
  };

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try { await fn(); onChanged(); }
    catch (e) { setError(e instanceof Error ? e.message : "That didn't work."); }
    finally { setBusy(false); }
  };

  const launch = async (goal: TeamGoal, kind: string) => {
    setBusy(true);
    setError(null);
    setLaunched(null);
    try {
      const res = await api.launchGoal(goal.id, { kind });
      setLaunched(res.result === "project_created"
        ? (res.message ?? "Video project created — open the Video tab to plan its shots.")
        : `Started a real ${KIND_LABELS[kind] ?? kind} run. Watch it on the Pipeline tab.`);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not launch that.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section>
      <div className="mb-2 flex items-center justify-between">
        <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase
                       tracking-wider text-slate-500">
          <Target className="h-3.5 w-3.5" /> Goals
        </h3>
        {!adding && (
          <button onClick={() => setAdding(true)}
                  className="text-xs text-amber-300 hover:text-amber-200">
            Set a goal
          </button>
        )}
      </div>

      <p className="mb-2.5 text-[11px] leading-relaxed text-slate-500">
        {canLaunch
          ? "A goal shapes what this team writes and chooses, and you can start a real run from it."
          : "This team has no pipeline of its own, so a goal here steers how they work rather than starting anything."}
      </p>

      {adding && (
        <div className="mb-3 space-y-2 rounded-lg border border-slate-800 bg-slate-950/60 p-3">
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="e.g. Cards on unity, for people new to the Faith"
            className="w-full rounded-lg border border-slate-700 bg-slate-900/80 px-3 py-2
                       text-sm text-slate-200 placeholder:text-slate-600
                       focus:border-amber-400/50 focus:outline-none"
          />
          <textarea
            value={detail}
            onChange={(e) => setDetail(e.target.value)}
            rows={2}
            placeholder="Any detail they should keep in mind (optional)"
            className="w-full resize-none rounded-lg border border-slate-700 bg-slate-900/80
                       px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600
                       focus:border-amber-400/50 focus:outline-none"
          />
          {canLaunch && (
            <input
              value={target}
              onChange={(e) => setTarget(e.target.value.replace(/\D/g, ""))}
              placeholder="How many to make (optional)"
              inputMode="numeric"
              className="w-full rounded-lg border border-slate-700 bg-slate-900/80 px-3 py-2
                         text-sm text-slate-200 placeholder:text-slate-600
                         focus:border-amber-400/50 focus:outline-none"
            />
          )}
          <div className="flex gap-2">
            <Button onClick={create} loading={busy} disabled={busy || !text.trim()}>
              Set goal
            </Button>
            <Button variant="ghost" onClick={() => { setAdding(false); setError(null); }}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {active.length === 0 && !adding && (
        <p className="text-sm text-slate-500">No goal set for this team.</p>
      )}

      <div className="space-y-2">
        {active.map((g) => (
          <div key={g.id} className="rounded-lg border border-slate-800 bg-slate-950/50 p-3">
            <div className="flex items-start justify-between gap-2">
              <p className="text-sm text-slate-200">{g.goal}</p>
              <button
                onClick={() => act(() => api.deleteGoal(g.id))}
                title="Remove this goal"
                className="shrink-0 text-slate-600 hover:text-rose-400"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
            {g.detail && <p className="mt-1 text-xs text-slate-500">{g.detail}</p>}
            {/* A goal Abigail set on his behalf steers this whole team, so it
                says so plainly rather than looking like something he typed. */}
            {g.set_by === "abigail" && (
              <p className="mt-1 text-[11px] text-violet-300/80">
                Set by Abigail on your behalf
              </p>
            )}

            {/* Progress is counted from real finished products, never
                self-reported — so a team with no product pipeline honestly
                shows nothing rather than a made-up percentage. */}
            {g.progress?.measurable && g.progress.target ? (
              <div className="mt-2.5">
                <div className="flex items-center gap-3">
                  <ProgressBar
                    value={(g.progress.done / g.progress.target) * 100}
                    colorClass="bg-emerald-400"
                    className="flex-1"
                  />
                  <span className="font-mono text-xs text-slate-400">
                    {g.progress.done}/{g.progress.target}
                  </span>
                </div>
                <p className="mt-1 text-[11px] text-slate-600">
                  Counted from products actually finished since you set this.
                </p>
              </div>
            ) : g.progress?.measurable ? (
              <p className="mt-2 text-[11px] text-slate-600">
                {g.progress.done} made since you set this.
              </p>
            ) : null}

            <div className="mt-2.5 flex flex-wrap items-center gap-2">
              {team.goal_kinds.map((kind) => (
                <Button key={kind} variant="secondary" onClick={() => launch(g, kind)}
                        disabled={busy} className="px-3 py-1.5 text-xs">
                  <Play className="h-3 w-3" /> Make {KIND_LABELS[kind] ?? kind}
                </Button>
              ))}
              <Button variant="ghost" className="px-3 py-1.5 text-xs"
                      onClick={() => act(() => api.updateGoal(g.id, { status: "done" }))}
                      disabled={busy}>
                Mark done
              </Button>
            </div>
          </div>
        ))}
      </div>

      {launched && (
        <p className="mt-2 rounded-lg border border-emerald-400/30 bg-emerald-400/5 px-3 py-2
                      text-xs text-emerald-200">{launched}</p>
      )}
      {error && <div className="mt-2"><ErrorNote>{error}</ErrorNote></div>}
    </section>
  );
}

function ConsultSection({
  team, jobId, setJobId,
}: { team: ColonyTeam; jobId: string | null; setJobId: (id: string | null) => void }) {
  const [question, setQuestion] = useState("");
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Same reattach-and-survive-a-restart behaviour as the video pipeline's jobs.
  const job = useVideoJob(jobId, () => setJobId(null)) as Job<TeamConsultResult> | null;

  if (!team.consultable) {
    return (
      <section>
        <h3 className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold uppercase
                       tracking-wider text-slate-500">
          <MessagesSquare className="h-3.5 w-3.5" /> Consultation
        </h3>
        <p className="text-sm text-slate-500">
          Consultation needs more than one voice — this team has a single member, so talk
          to them directly instead.
        </p>
      </section>
    );
  }

  const ask = async () => {
    if (!question.trim()) return;
    setStarting(true);
    setError(null);
    try {
      const res = await api.consultTeam(team.id, question.trim());
      setJobId(res.job_id);
      setQuestion("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start the consultation.");
    } finally {
      setStarting(false);
    }
  };

  const running = job?.status === "running";
  const liveTurns = job?.consultation_live ?? [];
  const result = job?.status === "done" ? job.result : null;

  return (
    <section>
      <h3 className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold uppercase
                     tracking-wider text-slate-500">
        <MessagesSquare className="h-3.5 w-3.5" /> Consult the team
      </h3>
      <p className="mb-2 text-[11px] leading-relaxed text-slate-500">
        Each of them answers in turn, seeing what the others have said, then the
        Steward says where they actually agreed and where they didn't.
      </p>

      <textarea
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        rows={2}
        placeholder="Ask the team something…"
        disabled={running}
        className="w-full resize-none rounded-lg border border-slate-700 bg-slate-900/80
                   px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600
                   focus:border-amber-400/50 focus:outline-none disabled:opacity-50"
      />
      <Button onClick={ask} loading={starting || running}
              disabled={starting || running || !question.trim()}
              className="mt-2">
        {running ? "Consulting…" : "Ask the team"}
      </Button>

      {error && <div className="mt-2"><ErrorNote>{error}</ErrorNote></div>}
      {job?.status === "error" && (
        <div className="mt-2"><ErrorNote>{job.error ?? "The consultation failed."}</ErrorNote></div>
      )}
      {running && job?.progress && (
        <p className="mt-2 text-xs text-slate-500">{job.progress}</p>
      )}

      {(liveTurns.length > 0 || result) && (
        <div className="mt-3 space-y-2.5">
          {(result?.turns ?? liveTurns.map((t) => ({
            agent: t.agent, text: t.message,
          }))).map((turn, i) => (
            <div key={`${turn.agent}-${i}`} className="flex gap-2.5">
              <RosterAvatar src={rosterFor(turn.agent)?.avatar} name={agentLabel(turn.agent)}
                            className="mt-0.5 h-7 w-7 shrink-0" />
              <div className="min-w-0">
                <div className="text-xs font-medium text-slate-300">
                  {agentLabel(turn.agent)}
                </div>
                <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-400">
                  {turn.text}
                </p>
              </div>
            </div>
          ))}
          {result?.summary && (
            <div className="rounded-lg border border-amber-400/25 bg-amber-400/5 px-3 py-2.5">
              <div className="mb-1 text-xs font-semibold uppercase tracking-wider
                              text-amber-300/80">Where it landed</div>
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-amber-100/90">
                {result.summary}
              </p>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
