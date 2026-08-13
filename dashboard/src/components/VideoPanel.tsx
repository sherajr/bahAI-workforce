import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, ArrowLeft, Clapperboard, Film, Loader2, Plus, Trash2,
} from "lucide-react";
import { api } from "../lib/api";
import { getVideoUi, patchVideoUi } from "../lib/settings";
import type { Job, VideoProject, VideoDirection } from "../lib/types";
import { BadgePill, Button, Card, CardContent, CardHeader, CardTitle, ErrorNote } from "./ui";
import { VideoProjectView } from "./video/VideoProjectView";

/**
 * Video Generation — the entry point.
 *
 * The identity here is "a scene or story becomes a video". A bookmark or quote
 * card is offered as a convenient SOURCE, never as the headline, because this
 * is a general story-to-video pipeline (owner spec).
 *
 * Which project is open is persisted (localStorage, same pattern as the
 * Pipeline tab's active job): switching tabs unmounts this panel, and a
 * multi-stage pipeline that forgets where you were every time you look at
 * something else is unusable.
 */
export function VideoPanel() {
  const [openId, setOpenId] = useState<string | null>(() => getVideoUi().projectId);

  const open = (id: string | null) => {
    setOpenId(id);
    // Clear the remembered sub-tab when leaving a project so the next one
    // opens on its own natural starting stage.
    patchVideoUi(id ? { projectId: id } : { projectId: null, tab: null, jobId: null });
  };

  if (openId) {
    return <VideoProjectView projectId={openId} onBack={() => open(null)} />;
  }
  return <VideoHome onOpen={open} />;
}

type SourceKind = "scene_story" | "bookmark" | "quote_card";

function VideoHome({ onOpen }: { onOpen: (id: string) => void }) {
  const [sourceKind, setSourceKind] = useState<SourceKind>("scene_story");
  const [text, setText] = useState("");
  const [title, setTitle] = useState("");
  const [instructions, setInstructions] = useState("");
  const [productId, setProductId] = useState("");
  const [targetSeconds, setTargetSeconds] = useState(60);
  const queryClient = useQueryClient();

  const projects = useQuery({ queryKey: ["video-projects"], queryFn: api.getVideoProjects });
  const defaults = useQuery({
    queryKey: ["video-defaults"], queryFn: api.getVideoDefaults, staleTime: Infinity,
  });
  const providers = useQuery({ queryKey: ["video-providers"], queryFn: api.getVideoProviders });
  const products = useQuery({
    queryKey: ["products"], queryFn: api.getProducts,
    enabled: sourceKind !== "scene_story",
  });

  const create = useMutation({
    mutationFn: () =>
      api.createVideoProject({
        title: title.trim() || undefined,
        source_kind: sourceKind,
        source_text: text.trim(),
        source_instructions: instructions.trim(),
        source_product_id: sourceKind === "scene_story" ? null : productId || null,
        direction: { target_seconds: targetSeconds } as Partial<VideoDirection>,
      }),
    onSuccess: (project) => {
      queryClient.invalidateQueries({ queryKey: ["video-projects"] });
      setText(""); setTitle(""); setInstructions(""); setProductId("");
      onOpen(project.id);
    },
  });

  const shotEstimate = Math.max(1, Math.round(targetSeconds / 3.5));
  const eligible = sourceKind === "scene_story" ? text.trim().length > 0 : !!productId;
  const sourceProducts = (products.data ?? []).filter((p) =>
    sourceKind === "bookmark"
      ? (p.product_type ?? "bookmark") === "bookmark"
      : p.product_type === "quote_card"
  );

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Clapperboard className="h-4 w-4 text-amber-400" />
            Create a Video from a Scene or Story
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm leading-relaxed text-slate-400">
            Paste a scene, historical account, passage or original idea. The team breaks it
            into <span className="text-slate-200">many short 3–4 second shots</span> instead
            of asking the video model for one long, complicated scene — that is what keeps
            quality and continuity usable on modest hardware.
          </p>

          {/* Source picker — Scene or Story is the dominant default. */}
          <div className="flex flex-wrap gap-2">
            {([
              ["scene_story", "Scene or story"],
              ["bookmark", "Use a bookmark"],
              ["quote_card", "Use a quote card"],
            ] as [SourceKind, string][]).map(([id, label]) => (
              <button
                key={id}
                onClick={() => setSourceKind(id)}
                className={
                  "rounded-lg border px-3 py-1.5 text-sm transition-colors " +
                  (sourceKind === id
                    ? "border-amber-400/40 bg-amber-400/10 text-amber-200"
                    : "border-slate-700 bg-slate-800/40 text-slate-400 hover:text-slate-200")
                }
              >
                {id === "scene_story" ? label : <span className="text-xs">{label}</span>}
              </button>
            ))}
          </div>

          {sourceKind === "scene_story" ? (
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={9}
              placeholder={
                "Paste your scene, story, historical account or passage here.\n\n" +
                "Example: “In 1848, a traveller arrived at the camp outside Mashhad carrying " +
                "a message. The prince listened, weighed the purse in his hand, and sent his " +
                "courier into the rain…”"
              }
              className="w-full rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:border-amber-400/50 focus:outline-none"
            />
          ) : (
            <div className="space-y-2">
              {products.isLoading && (
                <div className="flex items-center gap-2 text-sm text-slate-500">
                  <Loader2 className="h-4 w-4 animate-spin" /> Loading products…
                </div>
              )}
              {!products.isLoading && sourceProducts.length === 0 && (
                <p className="text-sm text-slate-500">
                  No {sourceKind === "bookmark" ? "bookmarks" : "quote cards"} yet — create one
                  in the Pipeline tab, or paste a scene instead.
                </p>
              )}
              <div className="grid max-h-64 grid-cols-1 gap-2 overflow-y-auto sm:grid-cols-2">
                {sourceProducts.map((p) => (
                  <button
                    key={p.id}
                    onClick={() => setProductId(p.id)}
                    className={
                      "rounded-lg border px-3 py-2 text-left text-sm transition-colors " +
                      (productId === p.id
                        ? "border-amber-400/40 bg-amber-400/10 text-amber-100"
                        : "border-slate-700 bg-slate-900/40 text-slate-300 hover:bg-slate-800/60")
                    }
                  >
                    <div className="truncate font-medium">{p.title ?? p.id}</div>
                    <div className="truncate text-xs text-slate-500">{p.theme ?? ""}</div>
                  </button>
                ))}
              </div>
              <p className="text-xs text-slate-500">
                Its text and attribution are copied in as a starting point — you can edit them
                freely, and the original {sourceKind === "bookmark" ? "bookmark" : "card"} is
                never changed.
              </p>
            </div>
          )}

          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-xs text-slate-400">
              Title (optional)
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Auto-generated if blank"
                className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:border-amber-400/50 focus:outline-none"
              />
            </label>
            <label className="text-xs text-slate-400">
              Target length: <span className="text-slate-200">{targetSeconds}s</span>
              {" → about "}
              <span className="text-amber-300">{shotEstimate} shots</span>
              <input
                type="range" min={15} max={180} step={5}
                value={targetSeconds}
                onChange={(e) => setTargetSeconds(Number(e.target.value))}
                className="mt-2 w-full accent-amber-400"
              />
            </label>
          </div>

          <label className="block text-xs text-slate-400">
            Tone, audience or message (optional)
            <input
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              placeholder="e.g. reverent and quiet, for people new to the story"
              className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:border-amber-400/50 focus:outline-none"
            />
          </label>

          {create.isError && <ErrorNote>{(create.error as Error).message}</ErrorNote>}

          <div className="flex items-center gap-3">
            <Button onClick={() => create.mutate()} loading={create.isPending} disabled={!eligible}>
              <Film className="h-4 w-4" /> Start video project
            </Button>
            {!eligible && (
              <span className="text-xs text-slate-500">
                {sourceKind === "scene_story"
                  ? "Paste some text to begin."
                  : "Pick a product to begin."}
              </span>
            )}
          </div>

          <ProviderNote providers={providers.data} />
          {defaults.isError && (
            <ErrorNote>Could not load video defaults: {(defaults.error as Error).message}</ErrorNote>
          )}
        </CardContent>
      </Card>

      <ProjectList
        projects={projects.data?.projects ?? []}
        loading={projects.isLoading}
        error={projects.error as Error | null}
        onOpen={onOpen}
      />
    </div>
  );
}

function ProviderNote({ providers }: { providers?: { providers: { id: string; available: boolean; label?: string; unavailable_reason?: string; is_mock?: boolean }[]; ffmpeg: boolean } }) {
  if (!providers) return null;
  const real = providers.providers.filter((p) => !p.is_mock);
  const up = real.filter((p) => p.available);
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2 text-xs text-slate-400">
      {up.length > 0 ? (
        <>Video engine ready: {up.map((p) => p.label ?? p.id).join(", ")}.</>
      ) : (
        <span className="text-amber-300">
          <AlertTriangle className="mr-1 inline h-3 w-3" />
          No local video engine is running{real[0]?.unavailable_reason ? ` — ${real[0].unavailable_reason}` : ""}.
          You can still plan shots and generate frames; clips need the engine.
        </span>
      )}
      {!providers.ffmpeg && (
        <> ffmpeg was not found, so clips can be exported individually but not joined.</>
      )}
    </div>
  );
}

function ProjectList({
  projects, loading, error, onOpen,
}: {
  projects: VideoProject[];
  loading: boolean;
  error: Error | null;
  onOpen: (id: string) => void;
}) {
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteVideoProject(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["video-projects"] }),
  });

  if (loading) {
    return (
      <Card>
        <CardContent className="flex items-center gap-2 py-8 text-sm text-slate-500">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading your video projects…
        </CardContent>
      </Card>
    );
  }
  if (error) return <ErrorNote>{error.message}</ErrorNote>;
  if (projects.length === 0) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-slate-500">
          No video projects yet. Paste a scene above to make your first one.
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader><CardTitle>Your video projects</CardTitle></CardHeader>
      <CardContent className="space-y-2">
        {projects.map((p) => (
          <div
            key={p.id}
            className="flex items-center gap-3 rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2"
          >
            <button onClick={() => onOpen(p.id)} className="min-w-0 flex-1 text-left">
              <div className="truncate text-sm font-medium text-slate-200">{p.title}</div>
              <div className="text-xs text-slate-500">
                {p.shot_count ?? 0} shots · stage {p.stage} ·{" "}
                {new Date(p.created_at + "Z").toLocaleDateString()}
              </div>
            </button>
            <BadgePill
              className={
                p.status === "complete"
                  ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                  : "border-slate-700 bg-slate-800/60 text-slate-400"
              }
            >
              {p.status}
            </BadgePill>
            <button
              onClick={() => {
                if (confirm(`Delete "${p.title}"? Generated files stay on disk.`)) remove.mutate(p.id);
              }}
              className="rounded-lg p-1.5 text-slate-500 hover:bg-rose-500/10 hover:text-rose-300"
              title="Delete project"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

/**
 * Shared job poller used by the project view's long stages.
 *
 * Reattaches to a job that was started before the panel was unmounted (the
 * job id is persisted), so switching tabs mid-generation doesn't lose the
 * progress view. A 404 means the job is genuinely gone — the API restarted and
 * its in-memory job store was cleared — so it stops rather than polling a dead
 * id forever.
 */
export function useVideoJob(jobId: string | null, onDone: () => void) {
  const [job, setJob] = useState<Job<unknown> | null>(null);
  useEffect(() => {
    // A FINISHED job's result is kept on screen. `onDone` clears the stored
    // job id, which re-runs this effect with jobId === null — and the earlier
    // version blanked `job` at that point, so a failed generation flashed its
    // error for a single tick and then looked like a button that did nothing
    // (reported 2026-08-13). The error stays until the next job starts.
    if (!jobId) return;
    let alive = true;
    const tick = async () => {
      try {
        const j = await api.getPipelineStatus<unknown>(jobId);
        if (!alive) return;
        setJob(j as Job<unknown>);
        if (j.status === "done" || j.status === "error") onDone();
      } catch (err) {
        if (!alive) return;
        if (err instanceof Error && err.message.startsWith("404")) {
          // The API restarted and the in-memory job is gone — nothing to show.
          setJob(null);
          onDone();          // clears the stored id and refreshes from the DB
        }
        // anything else is a transient poll failure; the next tick retries
      }
    };
    tick();
    const handle = setInterval(tick, 2500);
    return () => { alive = false; clearInterval(handle); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);
  return job;
}

export { Plus, ArrowLeft };
