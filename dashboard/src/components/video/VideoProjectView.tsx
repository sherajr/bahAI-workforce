import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, ArrowLeft, CheckCircle2, Download, FileJson, Film, Link2 as LinkIcon,
  Loader2, Play, RefreshCw, Scissors, ShieldCheck, Sparkles, Wand2, XCircle,
} from "lucide-react";
import { api, BASE } from "../../lib/api";
import { getVideoUi, patchVideoUi } from "../../lib/settings";
import type { VideoDirection, VideoMotionRepair, VideoProjectDetail } from "../../lib/types";
import {
  BadgePill, Button, Card, CardContent, CardHeader, CardTitle, ErrorNote, ProgressBar,
} from "../ui";
import { useVideoJob } from "../VideoPanel";
import { Storyboard } from "./Storyboard";
import { ContinuityEditor } from "./ContinuityEditor";

const STAGES = [
  { id: "source", label: "Source" },
  { id: "direction", label: "Direction" },
  { id: "analysis", label: "Analysis" },
  { id: "continuity", label: "Continuity" },
  { id: "shots", label: "Shots" },
  { id: "frames", label: "Frames" },
  { id: "clips", label: "Clips" },
  { id: "review", label: "Review" },
  { id: "export", label: "Export" },
];

type ProjectTab = "direction" | "story" | "storyboard" | "review";
const PROJECT_TABS: ProjectTab[] = ["direction", "story", "storyboard", "review"];

// Used only if /video/defaults is from an older server that doesn't send them.
const FALLBACK_PACING = [
  { id: "standard", label: "Fill the target length",
    description: "Plans enough shots to reach the duration you asked for." },
  { id: "cinematic", label: "Fewer, longer moments",
    description: "One shot per distinct moment, longest shots allowed, cuts only "
                 + "where the story changes — calmer, and possibly shorter." },
];

export function VideoProjectView({ projectId, onBack }: { projectId: string; onBack: () => void }) {
  const queryClient = useQueryClient();
  // Both the running job and the open sub-tab are restored from localStorage,
  // so switching away and back (which unmounts this component) keeps your
  // place AND keeps following a generation that's still running on the server.
  const [jobId, setJobIdState] = useState<string | null>(() => getVideoUi().jobId);
  const [tab, setTabState] = useState<ProjectTab>(() => {
    const saved = getVideoUi().tab;
    return (PROJECT_TABS as string[]).includes(saved ?? "") ? (saved as ProjectTab) : "direction";
  });

  const setJobId = (id: string | null) => { setJobIdState(id); patchVideoUi({ jobId: id }); };
  const setTab = (t: ProjectTab) => { setTabState(t); patchVideoUi({ tab: t }); };

  const detail = useQuery({
    queryKey: ["video-project", projectId],
    queryFn: () => api.getVideoProject(projectId),
    refetchInterval: jobId ? 3000 : false,
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["video-project", projectId] });
    queryClient.invalidateQueries({ queryKey: ["video-projects"] });
  };
  const job = useVideoJob(jobId, () => { setJobId(null); refresh(); });

  const plan = useMutation({
    mutationFn: () => api.planVideo(projectId),
    onSuccess: (r) => { setJobId(r.job_id); setTab("storyboard"); },
  });
  const frames = useMutation({
    mutationFn: (v: { ids: string[] | null; force: boolean }) =>
      api.generateVideoFrames(projectId, v.ids, v.force),
    onSuccess: (r) => setJobId(r.job_id),
  });
  const clips = useMutation({
    mutationFn: (v: { ids: string[] | null; force: boolean }) =>
      api.generateVideoClips(projectId, v.ids, v.force),
    onSuccess: (r) => setJobId(r.job_id),
  });
  const chain = useMutation({
    mutationFn: (v: { adapt: boolean; force: boolean }) =>
      api.chainVideo(projectId, v.adapt, v.force),
    onSuccess: (r) => { setJobId(r.job_id); setTab("review"); },
  });
  const cancel = useMutation({ mutationFn: (id: string) => api.cancelVideoJob(id) });
  // Deterministic and free, so it runs synchronously rather than as a job.
  const repair = useMutation({
    mutationFn: (recut: boolean) => api.repairVideoMotion(projectId, recut),
    onSuccess: refresh,
  });

  if (detail.isLoading) {
    return (
      <div className="flex items-center gap-2 py-16 text-sm text-slate-500">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading project…
      </div>
    );
  }
  if (detail.isError) return <ErrorNote>{(detail.error as Error).message}</ErrorNote>;

  const data = detail.data as VideoProjectDetail;
  const { project, shots, resume } = data;
  const stageIndex = Math.max(0, STAGES.findIndex((s) => s.id === project.stage));
  const running = !!jobId && job?.status === "running";
  const sacred = project.analysis?.sacred_flags ?? project.safety?.source_scan;

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="ghost" onClick={onBack} className="px-2">
          <ArrowLeft className="h-4 w-4" /> Projects
        </Button>
        <div className="min-w-0 flex-1">
          <h2 className="truncate font-display text-lg text-slate-100">{project.title}</h2>
          <div className="text-xs text-slate-500">
            {shots.length} shots · target {project.direction?.target_seconds ?? 60}s ·{" "}
            {project.source_kind.replace("_", " ")}
          </div>
        </div>
        {resume.complete && (
          <BadgePill className="border-emerald-500/40 bg-emerald-500/10 text-emerald-300">
            <CheckCircle2 className="mr-1 h-3 w-3" /> All assets generated
          </BadgePill>
        )}
      </div>

      {/* Stage rail */}
      <div className="flex flex-wrap items-center gap-1.5">
        {STAGES.map((s, i) => (
          <div key={s.id} className="flex items-center gap-1.5">
            <span
              className={
                "rounded-full px-2.5 py-1 text-[11px] font-medium " +
                (i < stageIndex
                  ? "bg-emerald-500/10 text-emerald-300"
                  : i === stageIndex
                    ? "bg-amber-400/15 text-amber-200"
                    : "bg-slate-800/60 text-slate-500")
              }
            >
              {s.label}
            </span>
            {i < STAGES.length - 1 && <span className="text-slate-700">·</span>}
          </div>
        ))}
      </div>

      {/* Sacred-figure notice — shown before anything is generated. */}
      {sacred?.has_reference && (
        <div className="flex gap-3 rounded-lg border border-sky-500/30 bg-sky-500/5 px-4 py-3 text-sm text-sky-200">
          <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            <div className="font-medium">Reverent treatment applied</div>
            <p className="mt-0.5 text-sky-200/80">
              This story references {sacred.figures.join(", ")}. Manifestations of God are
              never portrayed visually — shots show them indirectly (reactions, the room, an
              object, an empty threshold), and narration may name them with reverence.
            </p>
          </div>
        </div>
      )}

      {/* Running job */}
      {running && job && (
        <Card>
          <CardContent className="space-y-3 py-4">
            <div className="flex items-center gap-2 text-sm text-slate-300">
              <Loader2 className="h-4 w-4 animate-spin text-amber-400" />
              <span className="flex-1">{job.progress}</span>
              <Button variant="secondary" onClick={() => cancel.mutate(jobId!)}
                      loading={cancel.isPending} className="px-3 py-1 text-xs">
                Cancel
              </Button>
            </div>
            <ProgressBar value={job.steps.length ? Math.min(95, job.steps.length * 6) : 8} />
            <div className="max-h-28 overflow-y-auto text-xs text-slate-500">
              {job.steps.slice(-6).map((s, i) => <div key={i}>{s.message}</div>)}
            </div>
          </CardContent>
        </Card>
      )}
      {job?.status === "error" && <ErrorNote>{job.error}</ErrorNote>}
      {/* Mutation failures (e.g. the chain preflight refusing because ComfyUI
          isn't running) come back as a plain rejected request, not a job — they
          need their own line or the click looks like it did nothing. */}
      {[plan, frames, clips, chain].map((m, i) =>
        m.isError ? <ErrorNote key={i}>{(m.error as Error).message}</ErrorNote> : null
      )}

      {/* Tabs */}
      <div className="flex gap-1 border-b border-slate-800">
        {([
          ["direction", "Direction"],
          ["story", "Story & continuity"],
          ["storyboard", `Storyboard${shots.length ? ` (${shots.length})` : ""}`],
          ["review", "Review & export"],
        ] as const).map(([id, label]) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={
              "px-3 py-2 text-sm transition-colors " +
              (tab === id
                ? "border-b-2 border-amber-400 text-amber-200"
                : "text-slate-500 hover:text-slate-300")
            }
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "direction" && (
        <DirectionTab project={project} onSaved={refresh}
                      onPlan={() => plan.mutate()} planning={plan.isPending || running}
                      hasShots={shots.length > 0} />
      )}
      {tab === "story" && (
        <div className="space-y-5">
          <StoryTab project={project} />
          <ContinuityEditor project={project} onSaved={refresh} />
        </div>
      )}
      {tab === "storyboard" && (
        <>
          <ChainPanel
            onChain={(adapt, force) => chain.mutate({ adapt, force })}
            busy={running || chain.isPending}
            hasClips={shots.some((s) => s.clip_path)}
            onRepair={(recut) => repair.mutate(recut)}
            repairing={repair.isPending}
            repairResult={repair.data ?? null}
          />
          <Storyboard
            projectId={projectId}
            shots={shots}
            continuity={project.continuity}
            onChanged={refresh}
            onGenerateFrames={(ids, force) => frames.mutate({ ids, force })}
            onGenerateClips={(ids, force) => clips.mutate({ ids, force })}
            busy={running}
          />
        </>
      )}
      {tab === "review" && (
        <ReviewTab projectId={projectId} detail={data} busy={running}
                   onGenerateFrames={(ids, force) => frames.mutate({ ids, force })}
                   onGenerateClips={(ids, force) => clips.mutate({ ids, force })} />
      )}
    </div>
  );
}

// ── Chained generation ───────────────────────────────────────────────────────

/**
 * The recommended way to render. Explains the trade-off in plain language,
 * because the difference between the two modes is the difference between "a
 * continuous scene" and "a slideshow" and isn't obvious from a button label.
 */
function ChainPanel({
  onChain, busy, hasClips, onRepair, repairing, repairResult,
}: {
  onChain: (adapt: boolean, force: boolean) => void;
  busy: boolean;
  hasClips: boolean;
  onRepair: (recut: boolean) => void;
  repairing: boolean;
  repairResult: VideoMotionRepair | null;
}) {
  const [adapt, setAdapt] = useState(true);
  const [force, setForce] = useState(false);

  return (
    <Card className="border-amber-400/25 bg-amber-400/[0.03]">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <LinkIcon className="h-4 w-4 text-amber-400" />
          Generate as one continuous scene (recommended)
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm leading-relaxed text-slate-400">
          Each clip is generated from the{" "}
          <span className="text-slate-200">actual last frame of the clip before it</span>, so
          the video flows as one scene. Generating shots separately (the buttons below) makes
          every shot invent its own version of the person and place — which is what makes a
          finished video look like a slideshow.
        </p>
        <div className="flex flex-wrap gap-4 text-sm text-slate-400">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={adapt} onChange={(e) => setAdapt(e.target.checked)}
                   className="accent-amber-400" />
            Match across camera cuts (looks at each clip, small cost per shot)
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)}
                   className="accent-amber-400" />
            Redo shots that already have clips
          </label>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button onClick={() => onChain(adapt, force)} loading={busy}>
            <LinkIcon className="h-4 w-4" />
            {hasClips && !force ? "Continue the chain" : "Generate the whole video"}
          </Button>
          <span className="text-xs text-slate-500">
            Runs one shot at a time and saves as it goes — you can stop and resume.
          </span>
        </div>

        {/* Repairs a plan made before the movement fixes existed, without
            re-planning (which would discard every hand edit). */}
        <div className="space-y-2 border-t border-slate-800 pt-3">
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="secondary" onClick={() => onRepair(false)} loading={repairing}>
              <Wand2 className="h-4 w-4" />
              Check the movement descriptions
            </Button>
            <Button variant="secondary" onClick={() => onRepair(true)} loading={repairing}>
              <Scissors className="h-4 w-4" />
              Check movement and recut
            </Button>
          </div>
          <p className="text-xs leading-relaxed text-slate-500">
            Both are free, instant, and never delete a shot. The first fixes shots that
            say nothing moves, repeat the previous shot's movement, or claim a camera
            angle they can't have. <span className="text-slate-400">Recut</span> also
            joins shots inside the same moment into one continuous take, so the video
            cuts only where the story actually changes.
          </p>
          {repairResult && (
            <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3 text-xs">
              <p className="text-slate-300">
                {repairResult.shots_changed === 0
                  ? `Checked ${repairResult.total_shots} shots — nothing needed fixing.`
                  : `Fixed ${repairResult.shots_changed} of ${repairResult.total_shots} shots.`}
                {repairResult.recut && repairResult.cut_count != null && (
                  <span className="text-amber-300/90">
                    {" "}Now {repairResult.cut_count} cuts — one every{" "}
                    {repairResult.seconds_per_cut}s.
                  </span>
                )}
              </p>
              {repairResult.notes.length > 0 && (
                <ul className="mt-2 space-y-1 text-slate-400">
                  {repairResult.notes.map((n, i) => <li key={i}>• {n}</li>)}
                </ul>
              )}
              {repairResult.warnings.length > 0 && (
                <ul className="mt-2 space-y-1 text-amber-300/80">
                  {repairResult.warnings.map((w, i) => <li key={i}>⚠ {w}</li>)}
                </ul>
              )}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// ── Direction ────────────────────────────────────────────────────────────────

function DirectionTab({
  project, onSaved, onPlan, planning, hasShots,
}: {
  project: VideoProjectDetail["project"];
  onSaved: () => void;
  onPlan: () => void;
  planning: boolean;
  hasShots: boolean;
}) {
  const defaults = useQuery({
    queryKey: ["video-defaults"], queryFn: api.getVideoDefaults, staleTime: Infinity,
  });
  const providers = useQuery({ queryKey: ["video-providers"], queryFn: api.getVideoProviders });
  const [d, setD] = useState<VideoDirection>(
    (project.direction ?? {}) as VideoDirection
  );
  const [sourceText, setSourceText] = useState(project.source_text ?? "");

  const save = useMutation({
    mutationFn: () => api.updateVideoProject(project.id, { direction: d, source_text: sourceText }),
    onSuccess: onSaved,
  });

  const set = <K extends keyof VideoDirection>(k: K, v: VideoDirection[K]) =>
    setD((prev) => ({ ...prev, [k]: v }));
  // Cinematic pacing pins shot length to the maximum, so the estimate must use
  // that rather than the (now overridden) slider value.
  const cinematic = (d.pacing ?? "standard") === "cinematic";
  const effectiveShotSeconds = cinematic
    ? (defaults.data?.max_shot_seconds ?? 4)
    : (d.shot_seconds ?? 3.5);
  const shotEstimate = Math.max(1, Math.round((d.target_seconds ?? 60) / effectiveShotSeconds));

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader><CardTitle>Source</CardTitle></CardHeader>
        <CardContent>
          <textarea
            value={sourceText}
            onChange={(e) => setSourceText(e.target.value)}
            rows={7}
            className="w-full rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-2 text-sm text-slate-200 focus:border-amber-400/50 focus:outline-none"
          />
          {project.source_product_id && (
            <p className="mt-2 text-xs text-slate-500">
              Seeded from product {project.source_product_id} — editing here never changes
              that product.
            </p>
          )}
        </CardContent>
      </Card>

      {/* Pacing gets its own card, above the sliders: it changes how the
          finished video FEELS more than any other setting, and its trade-off
          (a shorter video) has to be visible before planning, not after. */}
      <Card>
        <CardHeader><CardTitle>Pacing</CardTitle></CardHeader>
        <CardContent className="space-y-2">
          {(defaults.data?.pacing_options ?? FALLBACK_PACING).map((opt) => {
            const active = (d.pacing ?? "standard") === opt.id;
            return (
              <button
                key={opt.id}
                type="button"
                onClick={() => set("pacing", opt.id as VideoDirection["pacing"])}
                className={`w-full rounded-lg border p-3 text-left transition ${
                  active
                    ? "border-amber-400/50 bg-amber-400/[0.06]"
                    : "border-slate-800 bg-slate-900/40 hover:border-slate-700"
                }`}
              >
                <div className="flex items-center gap-2">
                  <span className={`h-3 w-3 shrink-0 rounded-full border ${
                    active ? "border-amber-400 bg-amber-400" : "border-slate-600"}`} />
                  <span className="text-sm font-medium text-slate-200">{opt.label}</span>
                </div>
                <p className="mt-1 pl-5 text-xs leading-relaxed text-slate-400">
                  {opt.description}
                </p>
              </button>
            );
          })}
          {(d.pacing ?? "standard") === "cinematic" && (
            <p className="text-xs leading-relaxed text-amber-300/70">
              Shot length is set to the maximum automatically, and the target length
              becomes a ceiling rather than a quota.
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Creative direction</CardTitle></CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <Field label={cinematic
            ? `Target length — up to about ${shotEstimate} shots`
            : `Target length — about ${shotEstimate} shots`}>
            <input type="range" min={15} max={180} step={5}
                   value={d.target_seconds ?? 60}
                   onChange={(e) => set("target_seconds", Number(e.target.value))}
                   className="w-full accent-amber-400" />
            <div className="text-xs text-slate-400">
              {d.target_seconds ?? 60} seconds
              {cinematic && " (a ceiling — the video ends when the story does)"}
            </div>
          </Field>
          <Field label={`Shot length (${defaults.data?.min_shot_seconds ?? 3}–${defaults.data?.max_shot_seconds ?? 4}s)`}>
            <input type="range" min={3} max={4} step={0.5}
                   value={effectiveShotSeconds}
                   disabled={cinematic}
                   onChange={(e) => set("shot_seconds", Number(e.target.value))}
                   className="w-full accent-amber-400 disabled:opacity-40" />
            <div className="text-xs text-slate-400">
              {effectiveShotSeconds}s per shot
              {cinematic && " — set by cinematic pacing"}
            </div>
          </Field>
          <Field label="Aspect ratio">
            <Select value={d.aspect_ratio ?? "16:9"} onChange={(v) => set("aspect_ratio", v)}
                    options={defaults.data?.aspect_ratios ?? ["16:9"]} />
          </Field>
          <Field label="Visual style">
            <Select value={d.visual_style ?? ""} onChange={(v) => set("visual_style", v)}
                    options={defaults.data?.visual_styles ?? []} allowCustom />
          </Field>
          <Field label="Historical period"><Text value={d.historical_period ?? ""} onChange={(v) => set("historical_period", v)} placeholder="e.g. Persia, 1848" /></Field>
          <Field label="Setting"><Text value={d.setting ?? ""} onChange={(v) => set("setting", v)} placeholder="e.g. a desert camp" /></Field>
          <Field label="Mood"><Text value={d.mood ?? ""} onChange={(v) => set("mood", v)} placeholder="e.g. quiet, reverent" /></Field>
          <Field label="Colour palette"><Text value={d.color_palette ?? ""} onChange={(v) => set("color_palette", v)} /></Field>
          <Field label="Audience"><Text value={d.audience ?? ""} onChange={(v) => set("audience", v)} /></Field>
          <Field label="Narration">
            <Select value={d.narration ?? "voiceover"} onChange={(v) => set("narration", v)}
                    options={defaults.data?.narration_options ?? ["voiceover", "none"]} />
          </Field>
          <Field label="Video engine">
            <Select
              value={d.provider ?? "comfyui:wan22"}
              onChange={(v) => set("provider", v)}
              options={(providers.data?.providers ?? []).map((p) => p.id)}
              labels={Object.fromEntries(
                (providers.data?.providers ?? []).map((p) => [
                  p.id,
                  `${p.label ?? p.id}${p.available ? "" : " (offline)"}`,
                ])
              )}
            />
          </Field>
          <Field label="Low-resource preset">
            <label className="flex items-center gap-2 text-sm text-slate-300">
              <input type="checkbox" checked={d.low_resource ?? true}
                     onChange={(e) => set("low_resource", e.target.checked)}
                     className="accent-amber-400" />
              Keep clips short and small (recommended for 8GB GPUs)
            </label>
          </Field>
        </CardContent>
      </Card>

      <CapabilityNote providers={providers.data} selected={d.provider ?? ""} />

      <div className="flex flex-wrap items-center gap-3">
        <Button variant="secondary" onClick={() => save.mutate()} loading={save.isPending}>
          Save direction
        </Button>
        <Button onClick={onPlan} loading={planning}>
          <Sparkles className="h-4 w-4" />
          {hasShots ? "Re-plan shots" : "Analyse story & plan shots"}
        </Button>
        {hasShots && (
          <span className="text-xs text-amber-300/80">
            Re-planning replaces the current shot list (generated frames and clips stay on disk).
          </span>
        )}
      </div>
    </div>
  );
}

function CapabilityNote({
  providers, selected,
}: {
  providers?: { providers: { id: string; first_last_frame?: boolean; first_last_frame_note?: string; available: boolean; label?: string; typical_seconds_per_clip?: number; is_mock?: boolean }[]; strategies: Record<string, string> };
  selected: string;
}) {
  if (!providers) return null;
  const p = providers.providers.find((x) => x.id === selected);
  if (!p) return null;
  return (
    <div className="space-y-1 rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-3 text-xs text-slate-400">
      <div className="font-medium text-slate-300">How your clips will be made</div>
      {p.is_mock && (
        <div className="text-amber-300">
          This is the development mock — it writes clearly-marked placeholders, not real video.
        </div>
      )}
      {!p.available && <div className="text-rose-300">Currently offline.</div>}
      <div>
        First-and-last-frame conditioning:{" "}
        {p.first_last_frame ? (
          <span className="text-emerald-300">supported natively</span>
        ) : (
          <span className="text-slate-300">not available — {p.first_last_frame_note}</span>
        )}
      </div>
      {!p.first_last_frame && (
        <div>
          Fallback in use: clips are generated from the <em>first</em> frame, and each clip's
          real final frame is carried into the next shot for continuity.
        </div>
      )}
      {!!p.typical_seconds_per_clip && (
        <div>Roughly {p.typical_seconds_per_clip}s per clip on this machine.</div>
      )}
    </div>
  );
}

// ── Story analysis (read-only summary) ───────────────────────────────────────

function StoryTab({ project }: { project: VideoProjectDetail["project"] }) {
  const a = project.analysis;
  if (!a) {
    return (
      <Card>
        <CardContent className="py-8 text-center text-sm text-slate-500">
          No analysis yet — run “Analyse story &amp; plan shots” from the Direction tab.
        </CardContent>
      </Card>
    );
  }
  return (
    <Card>
      <CardHeader><CardTitle>Story analysis</CardTitle></CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-slate-300">{a.summary}</p>
        {a.central_message && (
          <p className="text-slate-400"><span className="text-slate-500">Message: </span>{a.central_message}</p>
        )}
        <div className="grid gap-3 sm:grid-cols-3">
          <Mini title="Characters" items={a.characters?.map((c) => c.name || c.id) ?? []} />
          <Mini title="Locations" items={a.locations?.map((l) => l.name || l.id) ?? []} />
          <Mini title="Props" items={a.props?.map((p) => p.name || p.id) ?? []} />
        </div>
        {!!a.beats?.length && (
          <div>
            <div className="mb-1 text-xs uppercase tracking-wide text-slate-500">Beats</div>
            <ol className="space-y-1">
              {a.beats.map((b, i) => (
                <li key={b.id ?? i} className="text-slate-400">
                  <span className="text-slate-300">{i + 1}. {b.title}</span> — {b.summary}
                </li>
              ))}
            </ol>
          </div>
        )}
        {!!a.continuity_risks?.length && (
          <div className="rounded-lg border border-amber-500/25 bg-amber-500/5 px-3 py-2">
            <div className="text-xs font-medium text-amber-300">Continuity risks</div>
            <ul className="mt-1 list-inside list-disc text-xs text-amber-200/80">
              {a.continuity_risks.map((r, i) => <li key={i}>{r}</li>)}
            </ul>
          </div>
        )}
        {!!a.do_not_depict_literally?.length && (
          <div className="rounded-lg border border-sky-500/25 bg-sky-500/5 px-3 py-2">
            <div className="text-xs font-medium text-sky-300">Not depicted literally</div>
            <ul className="mt-1 list-inside list-disc text-xs text-sky-200/80">
              {a.do_not_depict_literally.map((r, i) => <li key={i}>{r}</li>)}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Mini({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <div className="mb-1 text-xs uppercase tracking-wide text-slate-500">{title}</div>
      {items.length === 0
        ? <div className="text-xs text-slate-600">none</div>
        : <div className="flex flex-wrap gap-1">
            {items.map((t, i) => (
              <span key={i} className="rounded bg-slate-800/70 px-1.5 py-0.5 text-xs text-slate-300">{t}</span>
            ))}
          </div>}
    </div>
  );
}

// ── Review & export ──────────────────────────────────────────────────────────

function ReviewTab({
  projectId, detail, busy, onGenerateFrames, onGenerateClips,
}: {
  projectId: string;
  detail: VideoProjectDetail;
  busy: boolean;
  onGenerateFrames: (ids: string[] | null, force: boolean) => void;
  onGenerateClips: (ids: string[] | null, force: boolean) => void;
}) {
  const [vision, setVision] = useState(false);
  const [crossfade, setCrossfade] = useState(false);
  const [onlyApproved, setOnlyApproved] = useState(false);
  const queryClient = useQueryClient();

  const validation = useQuery({
    queryKey: ["video-validate", projectId, vision],
    queryFn: () => api.validateVideo(projectId, vision),
  });
  const assemble = useMutation({
    mutationFn: () => api.assembleVideo(projectId, onlyApproved, crossfade),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["video-project", projectId] }),
  });

  const { shots, resume } = detail;
  const exported = assemble.data ?? detail.project.export;

  return (
    <div className="space-y-5">
      {/* Resume / gaps */}
      {(resume.needs_frames.length > 0 || resume.needs_clips.length > 0) && (
        <Card>
          <CardHeader><CardTitle>Unfinished work</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <p className="text-sm text-slate-400">
              Work is saved as it happens — you can close the app and pick up here.
            </p>
            <div className="flex flex-wrap gap-2">
              {resume.needs_frames.length > 0 && (
                <Button variant="secondary" disabled={busy}
                        onClick={() => onGenerateFrames(resume.needs_frames, false)}>
                  <RefreshCw className="h-4 w-4" />
                  Generate {resume.needs_frames.length} missing frame set(s)
                </Button>
              )}
              {resume.needs_clips.length > 0 && (
                <Button variant="secondary" disabled={busy}
                        onClick={() => onGenerateClips(resume.needs_clips, false)}>
                  <Play className="h-4 w-4" />
                  Generate {resume.needs_clips.length} missing clip(s)
                </Button>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Timeline */}
      <Card>
        <CardHeader><CardTitle>Timeline</CardTitle></CardHeader>
        <CardContent className="space-y-2">
          {shots.length === 0 && <p className="text-sm text-slate-500">No shots yet.</p>}
          {shots.map((s) => (
            <div key={s.id}
                 className="flex items-center gap-3 rounded-lg border border-slate-800 bg-slate-900/40 p-2">
              <span className="w-8 shrink-0 text-center text-xs text-slate-500">#{s.shot_number}</span>
              <Thumb url={s.first_frame_url} alt="first" />
              <Thumb url={s.last_frame_url} alt="last" />
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm text-slate-300">
                  {s.data.subject || s.data.narrative_purpose || "—"}
                </div>
                <div className="truncate text-xs text-slate-500">
                  {s.data.duration ?? 3.5}s · {s.data.narration || s.data.sound_notes || "no narration"}
                </div>
              </div>
              {s.clip_url ? (
                <a href={`${BASE}${s.clip_url}`} target="_blank" rel="noreferrer"
                   className="text-xs text-amber-300 hover:underline">clip</a>
              ) : (
                <span className="text-xs text-slate-600">no clip</span>
              )}
              {s.approved && <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" />}
              {s.error && <XCircle className="h-4 w-4 shrink-0 text-rose-400" />}
            </div>
          ))}
        </CardContent>
      </Card>

      {/* Continuity validation */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between gap-3">
            <span>Continuity check</span>
            <label className="flex items-center gap-2 text-xs font-normal text-slate-400">
              <input type="checkbox" checked={vision} onChange={(e) => setVision(e.target.checked)}
                     className="accent-amber-400" />
              also compare frames with the vision model (paid)
            </label>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {validation.isLoading && (
            <div className="flex items-center gap-2 text-sm text-slate-500">
              <Loader2 className="h-4 w-4 animate-spin" /> Checking…
            </div>
          )}
          {validation.data && validation.data.findings.length === 0 && (
            <p className="text-sm text-emerald-300">No continuity problems found.</p>
          )}
          {validation.data?.findings.map((f, i) => (
            <div key={i}
                 className={
                   "rounded-lg border px-3 py-2 text-sm " +
                   (f.severity === "error"
                     ? "border-rose-500/30 bg-rose-500/5 text-rose-200"
                     : f.severity === "warning"
                       ? "border-amber-500/30 bg-amber-500/5 text-amber-200"
                       : "border-slate-700 bg-slate-800/40 text-slate-400")
                 }>
              <span className="text-xs opacity-70">Shot {f.shot_number} · {f.kind}</span>
              <div>{f.message}</div>
            </div>
          ))}
        </CardContent>
      </Card>

      {/* Assemble + export */}
      <Card>
        <CardHeader><CardTitle>Assemble &amp; export</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-4 text-sm text-slate-400">
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={onlyApproved}
                     onChange={(e) => setOnlyApproved(e.target.checked)} className="accent-amber-400" />
              only approved shots
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={crossfade}
                     onChange={(e) => setCrossfade(e.target.checked)} className="accent-amber-400" />
              crossfade between shots
            </label>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => assemble.mutate()} loading={assemble.isPending}>
              <Film className="h-4 w-4" /> Assemble draft video
            </Button>
            <a href={`${BASE}/video/projects/${projectId}/export`} target="_blank" rel="noreferrer">
              <Button variant="secondary"><FileJson className="h-4 w-4" /> Shot plan JSON</Button>
            </a>
            <a href={`${BASE}/video/projects/${projectId}/subtitles`} target="_blank" rel="noreferrer">
              <Button variant="secondary"><Download className="h-4 w-4" /> Subtitles (SRT)</Button>
            </a>
          </div>
          {assemble.isError && <ErrorNote>{(assemble.error as Error).message}</ErrorNote>}
          {exported && (
            <div className="rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2 text-sm">
              {exported.video_url ? (
                <>
                  <div className="mb-2 text-emerald-300">{exported.reason}</div>
                  <video src={`${BASE}${exported.video_url}`} controls
                         className="max-h-80 w-full rounded-lg border border-slate-800" />
                </>
              ) : (
                <div className="flex gap-2 text-amber-200">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>{exported.reason}</span>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Thumb({ url, alt }: { url: string; alt: string }) {
  if (!url) {
    return <div className="h-10 w-16 shrink-0 rounded border border-slate-800 bg-slate-900/60" />;
  }
  return (
    <img src={`${BASE}${url}`} alt={alt}
         className="h-10 w-16 shrink-0 rounded border border-slate-800 object-cover" />
  );
}

// ── small inputs ─────────────────────────────────────────────────────────────

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-xs text-slate-400">{label}</span>
      {children}
    </label>
  );
}

function Text({ value, onChange, placeholder }: {
  value: string; onChange: (v: string) => void; placeholder?: string;
}) {
  return (
    <input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
           className="w-full rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:border-amber-400/50 focus:outline-none" />
  );
}

function Select({ value, onChange, options, labels, allowCustom }: {
  value: string; onChange: (v: string) => void; options: string[];
  labels?: Record<string, string>; allowCustom?: boolean;
}) {
  const known = options.includes(value);
  return (
    <div className="space-y-1">
      <select value={known ? value : ""} onChange={(e) => onChange(e.target.value)}
              className="w-full rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-2 text-sm text-slate-200 focus:border-amber-400/50 focus:outline-none">
        {allowCustom && <option value="">custom…</option>}
        {options.map((o) => <option key={o} value={o}>{labels?.[o] ?? o}</option>)}
      </select>
      {allowCustom && !known && (
        <Text value={value} onChange={onChange} placeholder="describe the style" />
      )}
    </div>
  );
}
