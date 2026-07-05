import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ExternalLink, Loader2 } from "lucide-react";
import { api } from "../lib/api";
import { getActiveJobId, getSettings, setActiveJobId } from "../lib/settings";
import type { Job } from "../lib/types";
import { badgeClasses } from "../lib/utils";
import { BookmarkPreview } from "./BookmarkPreview";
import { ConsultationPause } from "./ConsultationPause";
import { ConsultationTranscript } from "./ConsultationTranscript";
import { ListingDetail } from "./ListingDetail";
import { QuoteCardDetail, QuoteCardPreview } from "./QuoteCardPreview";
import { ScoreCard } from "./ScoreCard";
import { BadgePill, Button, Card, CardContent, CardHeader, CardTitle, ErrorNote } from "./ui";

type PipelineKind = "bookmark" | "quote_card";

export function PipelinePanel() {
  const [theme, setTheme] = useState("");
  const [kind, setKind] = useState<PipelineKind>("bookmark");
  const [language, setLanguage] = useState(""); // "" = English only
  const [jobId, setJobId] = useState<string | null>(getActiveJobId());
  const queryClient = useQueryClient();

  const languages = useQuery({
    queryKey: ["card-languages"],
    queryFn: api.getCardLanguages,
    staleTime: Infinity,
  });

  const start = useMutation({
    mutationFn: (t: string) => {
      const s = getSettings();
      return kind === "quote_card"
        ? api.runCardPipeline(t, language || null, s.targetScore, s.maxAttempts)
        : api.runPipeline(t, s.targetScore, s.maxAttempts);
    },
    onSuccess: (data) => {
      setJobId(data.job_id);
      setActiveJobId(data.job_id);
    },
  });

  const jobQuery = useQuery<Job>({
    queryKey: ["job", jobId],
    queryFn: () => api.getPipelineStatus(jobId as string),
    enabled: !!jobId,
    refetchInterval: (query) =>
      ["running", "waiting_for_input"].includes(query.state.data?.status ?? "") ? 2500 : false,
  });

  const job = jobQuery.data;
  const running = start.isPending || job?.status === "running" || job?.status === "waiting_for_input";
  const result = job?.status === "done" ? job.result : null;

  // Once a run finishes, the products list is stale.
  const jobStatus = job?.status;
  useEffect(() => {
    if (jobStatus === "done") {
      queryClient.invalidateQueries({ queryKey: ["products"] });
      queryClient.invalidateQueries({ queryKey: ["steward"] });
      queryClient.invalidateQueries({ queryKey: ["trust"] });
    }
  }, [jobStatus, queryClient]);

  const submit = () => {
    if (!theme.trim() || running) return;
    start.mutate(theme.trim());
  };

  const settings = getSettings();

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>{kind === "quote_card" ? "Create a quote card" : "Create a bookmark"}</CardTitle>
          <div className="flex overflow-hidden rounded-lg border border-slate-700 text-xs">
            {(
              [
                ["bookmark", "Bookmark"],
                ["quote_card", "Quote card"],
              ] as [PipelineKind, string][]
            ).map(([k, label]) => (
              <button
                key={k}
                onClick={() => !running && setKind(k)}
                disabled={running}
                className={
                  kind === k
                    ? "bg-amber-400/20 px-3 py-1.5 font-medium text-amber-300"
                    : "bg-slate-950 px-3 py-1.5 text-slate-400 hover:text-slate-200"
                }
              >
                {label}
              </button>
            ))}
          </div>
        </CardHeader>
        <CardContent>
          <p className="mb-3 text-sm text-slate-400">
            {kind === "quote_card" ? (
              <>
                A giveaway card (3.5″ × 2″) for sharing one beautiful idea with someone new to the
                Faith — verified quote, welcoming artwork, no listing, nothing sold. Pick a second
                language and the card carries an AI-assisted translation, clearly labeled as
                unofficial.
              </>
            ) : (
              <>
                Give the team a theme. The Librarian gathers passages, the Artist paints, the four
                agents consult, the Scribe writes, and the Reviewer scores against the constitution
                (target {settings.targetScore.toFixed(1)}/10, up to {settings.maxAttempts} attempt
                {settings.maxAttempts > 1 ? "s" : ""}).
              </>
            )}
          </p>
          <div className="flex flex-wrap gap-3">
            <input
              value={theme}
              onChange={(e) => setTheme(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
              placeholder='e.g. "unity of humanity", "the nightingale of paradise"'
              disabled={running}
              className="min-w-[220px] flex-1 rounded-lg border border-slate-700 bg-slate-950 px-4 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:border-amber-400/60"
            />
            {kind === "quote_card" && (
              <select
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
                disabled={running}
                className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2.5 text-sm text-slate-100 focus:border-amber-400/60"
              >
                <option value="">English only</option>
                {(languages.data ?? []).map((l) => (
                  <option key={l.code} value={l.code}>
                    English + {l.name}
                  </option>
                ))}
              </select>
            )}
            <Button onClick={submit} loading={running} disabled={!theme.trim()}>
              {running ? "Working..." : kind === "quote_card" ? "Generate quote card" : "Generate bookmark"}
            </Button>
          </div>
          {start.isError && (
            <div className="mt-3">
              <ErrorNote>Could not start the pipeline: {(start.error as Error).message}</ErrorNote>
            </div>
          )}
          {jobQuery.isError && jobId && (
            <div className="mt-3">
              <ErrorNote>
                Lost track of job {jobId}: {(jobQuery.error as Error).message}{" "}
                <button
                  className="underline"
                  onClick={() => {
                    setJobId(null);
                    setActiveJobId(null);
                  }}
                >
                  Dismiss
                </button>
              </ErrorNote>
            </div>
          )}
        </CardContent>
      </Card>

      {job && (job.status === "running" || job.status === "waiting_for_input") && (
        <Card>
          <CardHeader className="flex flex-row items-center gap-2">
            <Loader2 className="h-4 w-4 animate-spin text-amber-400" />
            <CardTitle>The team is at work</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="mb-3 text-sm font-medium text-amber-200">{job.progress}</p>
            <ol className="space-y-1.5">
              {job.steps.map((s, i) => {
                const isLast = i === job.steps.length - 1;
                return (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    {isLast ? (
                      <Loader2 className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin text-amber-400" />
                    ) : (
                      <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-400" />
                    )}
                    <span className={isLast ? "text-slate-200" : "text-slate-500"}>
                      {s.message}
                    </span>
                  </li>
                );
              })}
            </ol>
            <p className="mt-3 text-xs text-slate-600">
              Job {job.job_id} · a full run usually takes 3–5 minutes. You can switch tabs — the
              run continues on the server.
            </p>
          </CardContent>
        </Card>
      )}

      {/* Live view only while running/paused — once done, the full result below
          (transcript + editing log) supersedes it. */}
      {(job?.status === "running" || job?.status === "waiting_for_input") &&
        job.consultation_live && job.consultation_live.length > 0 && (
          <ConsultationTranscript turns={job.consultation_live} />
        )}

      {job?.status === "waiting_for_input" && job.pending_prompt && (
        <ConsultationPause jobId={job.job_id} prompt={job.pending_prompt} />
      )}

      {job?.status === "error" && (
        <ErrorNote>
          The pipeline stopped with an error (job {job.job_id}): {job.error}
          <div className="mt-1 text-xs text-rose-300/70">
            Nothing was hidden or smoothed over — check the API console for details, then try
            again.
          </div>
        </ErrorNote>
      )}

      {result && (
        <>
          <Card>
            <CardContent className="flex flex-wrap items-center justify-between gap-3 pt-4">
              <div>
                <div className="text-xs uppercase tracking-widest text-slate-500">
                  {result.product_type === "quote_card" ? "Quote card complete" : "Pipeline complete"}
                </div>
                <div className="mt-0.5 text-lg text-slate-100">
                  {result.theme}
                  {result.language_name ? ` · English + ${result.language_name}` : ""}
                </div>
                <div className="mt-0.5 font-mono text-xs text-slate-500">
                  task {result.task_id} · product {result.product_id} · {result.attempts} attempt
                  {result.attempts > 1 ? "s" : ""}
                </div>
              </div>
              <div className="flex items-center gap-3">
                <span className="font-mono text-2xl text-slate-100">
                  {result.review.overall?.toFixed(1)}/10
                </span>
                <BadgePill className={badgeClasses(result.badge)}>{result.badge}</BadgePill>
              </div>
            </CardContent>
          </Card>

          {result.product_type === "quote_card" ? (
            <>
              <QuoteCardPreview
                frontPath={result.front_image_web || result.front_image_path}
                backPath={result.back_image_web || result.back_image_path}
              />
              <QuoteCardDetail
                quote={result.quote ?? ""}
                citation={result.citation}
                quoteGrounded={result.quote_grounded}
                languageName={result.translation?.name}
                translationText={result.translation?.text}
                disclaimerNative={result.translation?.disclaimer_native}
                disclaimerEn={result.translation?.disclaimer_en}
              />
            </>
          ) : (
            <BookmarkPreview
              frontPath={result.front_image_web || result.front_image_path}
              backPath={result.back_image_web || result.back_image_path}
              originalPath={result.image_web || result.image_path}
            />
          )}
          {result.compositor_error && (
            <ErrorNote>Compositor could not render the halves: {result.compositor_error}</ErrorNote>
          )}

          <ConsultationTranscript turns={result.consultation} />
          <ScoreCard review={result.review} badge={result.badge} />
          {result.listing && <ListingDetail listing={result.listing} />}

          {result.canva?.design_url && (
            <Card>
              <CardContent className="flex items-center justify-between pt-4">
                <span className="text-sm text-slate-300">Canva design created</span>
                <a
                  href={result.canva.design_url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1.5 text-sm text-amber-300 hover:underline"
                >
                  Open in Canva <ExternalLink className="h-3.5 w-3.5" />
                </a>
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
