// Post to X (@peaceAntz) — a giveaway outreach tweet, never sold. The
// Coordinator briefs the team, the Librarian finds a verified quote (locked
// before anyone discusses it), the Artist paints, the team consults (same
// 3-round dialogue + round-2 pause as the bookmark/card pipelines), the
// Scribe drafts honoring the consultation's brief, and the Reviewer scores
// against the constitution (looping back to the Scribe up to twice). Nothing
// posts until Sheraj approves it here. Discarded drafts are gone for good —
// only what actually got posted is kept, in the Posted history below.

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bookmark, CheckCircle2, ExternalLink, ImagePlus, Loader2, Pencil, Send, Sparkles, Trash2, Undo2 } from "lucide-react";
import { api, imageUrl } from "../lib/api";
import { getActiveXPostJobId, setActiveXPostJobId } from "../lib/settings";
import type { Job, PendingXPost, XPostApproveResult, XPostJobResult } from "../lib/types";
import { badgeClasses, badgeFor } from "../lib/utils";
import { ConsultationPause } from "./ConsultationPause";
import { ConsultationTranscript } from "./ConsultationTranscript";
import { BadgePill, Button, Card, CardContent, CardHeader, CardTitle, ErrorNote } from "./ui";

const TWEET_HARD_MAX = 280;

function PendingCard({ post }: { post: PendingXPost }) {
  const queryClient = useQueryClient();
  const [posted, setPosted] = useState<XPostApproveResult | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(post.tweet_text ?? "");
  const [regeningImage, setRegeningImage] = useState(false);
  const [imageGuidance, setImageGuidance] = useState("");

  const approve = useMutation({
    mutationFn: () => api.approveXPost(post.id),
    onSuccess: (res) => {
      setPosted(res);
      queryClient.invalidateQueries({ queryKey: ["x-post-pending"] });
      queryClient.invalidateQueries({ queryKey: ["x-post-drafts"] });
      queryClient.invalidateQueries({ queryKey: ["x-post-posted"] });
    },
  });

  const discard = useMutation({
    mutationFn: () => api.discardXPost(post.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["x-post-pending"] });
      queryClient.invalidateQueries({ queryKey: ["x-post-drafts"] });
    },
  });

  const edit = useMutation({
    mutationFn: (text: string) => api.editXPost(post.id, text),
    onSuccess: () => {
      setEditing(false);
      queryClient.invalidateQueries({ queryKey: ["x-post-pending"] });
      queryClient.invalidateQueries({ queryKey: ["x-post-drafts"] });
    },
  });

  const regenImage = useMutation({
    mutationFn: (guidance: string) => api.regenerateXPostImage(post.id, guidance),
    onSuccess: () => {
      setRegeningImage(false);
      setImageGuidance("");
      queryClient.invalidateQueries({ queryKey: ["x-post-pending"] });
      queryClient.invalidateQueries({ queryKey: ["x-post-drafts"] });
    },
  });

  const saveDraft = useMutation({
    mutationFn: () => api.saveXPostAsDraft(post.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["x-post-pending"] });
      queryClient.invalidateQueries({ queryKey: ["x-post-drafts"] });
    },
  });

  const restore = useMutation({
    mutationFn: () => api.restoreXPost(post.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["x-post-pending"] });
      queryClient.invalidateQueries({ queryKey: ["x-post-drafts"] });
    },
  });

  const isDraft = post.status === "draft";
  const score = post.constitution_score ?? 0;
  const badge = badgeFor(score);
  const busy =
    approve.isPending || discard.isPending || edit.isPending || regenImage.isPending ||
    saveDraft.isPending || restore.isPending;
  const overLimit = draft.length > TWEET_HARD_MAX;

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="truncate pr-4">{post.topic ?? "Untitled topic"}</CardTitle>
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm text-slate-300">{score.toFixed(1)}/10</span>
          <BadgePill className={badgeClasses(badge)}>{badge}</BadgePill>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex gap-3">
          {post.image_web && (
            <div className="relative h-24 w-24 shrink-0">
              <img
                src={imageUrl(post.image_web)}
                alt=""
                className="h-24 w-24 rounded-lg border border-slate-800 object-cover"
              />
              {regenImage.isPending && (
                <div className="absolute inset-0 flex items-center justify-center rounded-lg bg-slate-950/70">
                  <Loader2 className="h-5 w-5 animate-spin text-amber-400" />
                </div>
              )}
            </div>
          )}
          <div className="min-w-0 flex-1 space-y-1">
            {editing ? (
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                rows={4}
                autoFocus
                className="w-full resize-none rounded-lg border border-slate-700 bg-slate-900/80 p-3 text-sm leading-relaxed text-slate-100 focus:border-amber-400/50 focus:outline-none"
              />
            ) : (
              <p className="whitespace-pre-wrap rounded-lg border border-slate-800 bg-slate-900/60 p-3 text-sm leading-relaxed text-slate-200">
                {post.tweet_text}
              </p>
            )}
            <p className={overLimit ? "text-xs text-rose-400" : "text-xs text-slate-500"}>
              {editing ? draft.length : (post.tweet_text ?? "").length}/{TWEET_HARD_MAX} characters
              {post.include_quote
                ? post.quote_author && <> · quote locked from {post.quote_author}</>
                : <> · original reflection{post.inspired_by ? ` — inspired by ${post.inspired_by}` : ""}</>}
            </p>
          </div>
        </div>

        {approve.isError && (
          <ErrorNote>{approve.error instanceof Error ? approve.error.message : "Could not post to X."}</ErrorNote>
        )}
        {discard.isError && (
          <ErrorNote>{discard.error instanceof Error ? discard.error.message : "Could not discard this draft."}</ErrorNote>
        )}
        {edit.isError && (
          <ErrorNote>{edit.error instanceof Error ? edit.error.message : "Could not save your edit."}</ErrorNote>
        )}
        {regenImage.isError && (
          <ErrorNote>
            {regenImage.error instanceof Error ? regenImage.error.message : "Could not regenerate the image."}
          </ErrorNote>
        )}
        {saveDraft.isError && (
          <ErrorNote>
            {saveDraft.error instanceof Error ? saveDraft.error.message : "Could not save this as a draft."}
          </ErrorNote>
        )}
        {restore.isError && (
          <ErrorNote>
            {restore.error instanceof Error ? restore.error.message : "Could not move this back to pending."}
          </ErrorNote>
        )}

        {regeningImage && (
          <div className="space-y-2 rounded-lg border border-slate-700 bg-slate-900/60 p-3">
            <input
              value={imageGuidance}
              onChange={(e) => setImageGuidance(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !regenImage.isPending && regenImage.mutate(imageGuidance)}
              placeholder="Optional guidance, e.g. 'more vibrant colors, add birds' — leave blank to just re-roll"
              disabled={regenImage.isPending}
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 placeholder-slate-600 focus:border-amber-400/50 focus:outline-none"
            />
            <div className="flex gap-2">
              <Button
                onClick={() => regenImage.mutate(imageGuidance)}
                loading={regenImage.isPending}
              >
                {regenImage.isPending ? "Painting (1-2 min)..." : "Regenerate"}
              </Button>
              <Button
                variant="ghost"
                disabled={regenImage.isPending}
                onClick={() => {
                  setImageGuidance("");
                  setRegeningImage(false);
                }}
              >
                Cancel
              </Button>
            </div>
          </div>
        )}

        {posted ? (
          <div className="flex items-center gap-2 rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-300">
            <CheckCircle2 className="h-4 w-4 shrink-0" />
            {posted.dry_run ? (
              <span>Dry run — logged, not actually posted (TWITTER_DRY_RUN=true).</span>
            ) : (
              <span>Posted ✓</span>
            )}
            {posted.url && (
              <a
                href={posted.url}
                target="_blank"
                rel="noreferrer"
                className="ml-auto inline-flex items-center gap-1 text-emerald-200 hover:underline"
              >
                View on X <ExternalLink className="h-3.5 w-3.5" />
              </a>
            )}
          </div>
        ) : editing ? (
          <div className="flex gap-2">
            <Button
              onClick={() => edit.mutate(draft)}
              loading={edit.isPending}
              disabled={!draft.trim() || overLimit}
            >
              Save changes
            </Button>
            <Button
              variant="ghost"
              disabled={edit.isPending}
              onClick={() => {
                setDraft(post.tweet_text ?? "");
                setEditing(false);
              }}
            >
              Cancel
            </Button>
          </div>
        ) : !regeningImage ? (
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => approve.mutate()} loading={approve.isPending} disabled={busy}>
              <Send className="h-4 w-4" /> Approve &amp; post
            </Button>
            <Button variant="secondary" onClick={() => setEditing(true)} disabled={busy}>
              <Pencil className="h-4 w-4" /> Edit
            </Button>
            <Button variant="secondary" onClick={() => setRegeningImage(true)} disabled={busy}>
              <ImagePlus className="h-4 w-4" /> New image
            </Button>
            {isDraft ? (
              <Button variant="secondary" onClick={() => restore.mutate()} loading={restore.isPending} disabled={busy}>
                <Undo2 className="h-4 w-4" /> Move to pending
              </Button>
            ) : (
              <Button variant="secondary" onClick={() => saveDraft.mutate()} loading={saveDraft.isPending} disabled={busy}>
                <Bookmark className="h-4 w-4" /> Save as draft
              </Button>
            )}
            <Button variant="ghost" onClick={() => discard.mutate()} loading={discard.isPending} disabled={busy}>
              <Trash2 className="h-4 w-4" /> Discard
            </Button>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function PostedCard({ post }: { post: PendingXPost }) {
  return (
    <div className="flex gap-3 rounded-lg border border-slate-800 bg-slate-900/40 p-3">
      {post.image_web && (
        <img
          src={imageUrl(post.image_web)}
          alt=""
          className="h-16 w-16 shrink-0 rounded-lg border border-slate-800 object-cover"
        />
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-sm font-medium text-slate-200">{post.topic ?? "Untitled topic"}</span>
          <span className="shrink-0 text-xs text-slate-500">{(post.created_at ?? "").slice(0, 16)}</span>
        </div>
        <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-slate-400">{post.tweet_text}</p>
        <p className="mt-0.5 text-xs text-slate-600">
          {post.include_quote
            ? post.quote_author && `Quote locked from ${post.quote_author}`
            : `Original reflection${post.inspired_by ? ` — inspired by ${post.inspired_by}` : ""}`}
        </p>
        {post.posted_url ? (
          <a
            href={post.posted_url}
            target="_blank"
            rel="noreferrer"
            className="mt-1 inline-flex items-center gap-1 text-xs text-amber-300 hover:underline"
          >
            View on X <ExternalLink className="h-3 w-3" />
          </a>
        ) : (
          <span className="mt-1 inline-block text-xs text-slate-600">Dry run — not actually posted</span>
        )}
      </div>
    </div>
  );
}

export function XPostsPanel() {
  const [topic, setTopic] = useState("");
  const [includeQuote, setIncludeQuote] = useState(true);
  const [jobId, setJobId] = useState<string | null>(getActiveXPostJobId());
  const queryClient = useQueryClient();

  const pending = useQuery({
    queryKey: ["x-post-pending"],
    queryFn: api.getPendingXPosts,
    refetchInterval: 15_000,
  });

  const drafts = useQuery({
    queryKey: ["x-post-drafts"],
    queryFn: api.getDraftXPosts,
    refetchInterval: 15_000,
  });

  const posted = useQuery({
    queryKey: ["x-post-posted"],
    queryFn: api.getPostedXPosts,
  });

  const start = useMutation({
    mutationFn: ({ t, quote }: { t: string; quote: boolean }) => api.runXPost(t, quote),
    onSuccess: (data) => {
      setJobId(data.job_id);
      setActiveXPostJobId(data.job_id);
    },
  });

  const jobQuery = useQuery({
    queryKey: ["x-post-job", jobId],
    queryFn: () => api.getPipelineStatus<XPostJobResult>(jobId as string),
    enabled: !!jobId,
    refetchInterval: (query) =>
      ["running", "waiting_for_input"].includes(query.state.data?.status ?? "") ? 2500 : false,
  });

  const job = jobQuery.data as Job<XPostJobResult> | undefined;
  const running = start.isPending || job?.status === "running" || job?.status === "waiting_for_input";
  const result = job?.status === "done" ? job.result : null;

  const jobStatus = job?.status;
  useEffect(() => {
    if (jobStatus === "done") {
      queryClient.invalidateQueries({ queryKey: ["x-post-pending"] });
    }
  }, [jobStatus, queryClient]);

  const submit = () => {
    if (!topic.trim() || running) return;
    setTopic("");
    start.mutate({ t: topic.trim(), quote: includeQuote });
  };

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <div>
        <h1 className="font-display text-xl text-slate-100">Post to X</h1>
        <p className="text-sm text-slate-400">
          A giveaway reflection for @peaceAntz — never sold, never posted without your approval.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Consult &amp; draft</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="mb-3 text-sm text-slate-400">
            Give the team a topic. The Librarian retrieves verified passages, the Artist paints, the
            team consults in two rounds (you'll be asked for guidance before the third), the
            Scribe drafts the tweet, and the Reviewer scores it against the constitution.
          </p>

          <div className="mb-3 flex flex-wrap items-center gap-3">
            <span className="text-xs font-medium uppercase tracking-wide text-slate-500">Quote:</span>
            <div className="inline-flex overflow-hidden rounded-lg border border-slate-700">
              <button
                type="button"
                onClick={() => setIncludeQuote(true)}
                disabled={running}
                className={`px-3 py-1.5 text-sm transition-colors ${
                  includeQuote ? "bg-amber-500 text-slate-950 font-medium" : "bg-slate-900/80 text-slate-400 hover:text-slate-200"
                }`}
              >
                With a quote
              </button>
              <button
                type="button"
                onClick={() => setIncludeQuote(false)}
                disabled={running}
                className={`px-3 py-1.5 text-sm transition-colors ${
                  !includeQuote ? "bg-amber-500 text-slate-950 font-medium" : "bg-slate-900/80 text-slate-400 hover:text-slate-200"
                }`}
              >
                Original reflection, no quote
              </button>
            </div>
            <span className="text-xs text-slate-500">
              {includeQuote
                ? "The tweet weaves in a completely unaltered verbatim excerpt (may be shortened with “...”)."
                : "The Librarian's passages become background inspiration only — nothing is quoted or attributed."}
            </span>
          </div>

          <div className="flex flex-wrap gap-3">
            <input
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
              placeholder="e.g. unity in diversity, detachment, the power of prayer..."
              disabled={running}
              className="min-w-[280px] flex-1 rounded-lg border border-slate-700 bg-slate-900/80 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:border-amber-400/50 focus:outline-none"
            />
            <Button onClick={submit} loading={running} disabled={!topic.trim()}>
              <Sparkles className="h-4 w-4" /> Consult &amp; Draft
            </Button>
          </div>
          {start.isError && (
            <div className="mt-3">
              <ErrorNote>
                {start.error instanceof Error ? start.error.message : "Could not start the draft."}
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
                    <span className={isLast ? "text-slate-200" : "text-slate-500"}>{s.message}</span>
                  </li>
                );
              })}
            </ol>
            <p className="mt-3 text-xs text-slate-600">
              Job {job.job_id} · you can switch tabs — the run continues on the server.
            </p>
          </CardContent>
        </Card>
      )}

      {(job?.status === "running" || job?.status === "waiting_for_input") &&
        job.consultation_live && job.consultation_live.length > 0 && (
          <ConsultationTranscript turns={job.consultation_live} />
        )}

      {job?.status === "waiting_for_input" && job.pending_prompt && (
        <ConsultationPause jobId={job.job_id} prompt={job.pending_prompt} />
      )}

      {job?.status === "error" && (
        <ErrorNote>
          The draft stopped with an error (job {job.job_id}): {job.error}
        </ErrorNote>
      )}

      {result && (
        <>
          <Card>
            <CardContent className="flex flex-wrap items-center justify-between gap-3 pt-4">
              <div>
                <div className="text-xs uppercase tracking-widest text-slate-500">Draft complete</div>
                <div className="mt-0.5 text-lg text-slate-100">{result.topic}</div>
                <div className="mt-0.5 font-mono text-xs text-slate-500">
                  {result.attempts} attempt{result.attempts > 1 ? "s" : ""} ·{" "}
                  {result.include_quote
                    ? `quote locked from ${result.quote_author}`
                    : `original reflection${result.inspired_by ? ` — inspired by ${result.inspired_by}` : ""}`}
                </div>
              </div>
              <div className="flex items-center gap-3">
                <span className="font-mono text-2xl text-slate-100">
                  {result.review.overall?.toFixed(1)}/10
                </span>
                <BadgePill className={badgeClasses(badgeFor(result.review.overall ?? 0))}>
                  {badgeFor(result.review.overall ?? 0)}
                </BadgePill>
              </div>
            </CardContent>
          </Card>
          <ConsultationTranscript turns={result.consultation} />
        </>
      )}

      <div className="space-y-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Pending approval {pending.data && pending.data.length > 0 ? `(${pending.data.length})` : ""}
        </h2>
        {pending.isLoading && <p className="text-sm text-slate-500">Loading…</p>}
        {pending.isError && (
          <ErrorNote>Could not load pending posts: {pending.error.message}</ErrorNote>
        )}
        {pending.data && pending.data.length === 0 && (
          <p className="text-sm text-slate-500">Nothing waiting — draft a new post above.</p>
        )}
        {pending.data?.map((post) => <PendingCard key={post.id} post={post} />)}
      </div>

      <div className="space-y-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Drafts {drafts.data && drafts.data.length > 0 ? `(${drafts.data.length})` : ""}
        </h2>
        <p className="text-xs text-slate-600">
          Posts you liked but wanted to think over — set aside with "Save as draft" above.
        </p>
        {drafts.isLoading && <p className="text-sm text-slate-500">Loading…</p>}
        {drafts.isError && (
          <ErrorNote>Could not load drafts: {drafts.error.message}</ErrorNote>
        )}
        {drafts.data && drafts.data.length === 0 && (
          <p className="text-sm text-slate-500">Nothing set aside right now.</p>
        )}
        {drafts.data?.map((post) => <PendingCard key={post.id} post={post} />)}
      </div>

      <div className="space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Posted {posted.data && posted.data.length > 0 ? `(${posted.data.length})` : ""}
        </h2>
        <p className="text-xs text-slate-600">
          A permanent record of what actually went out — discarded drafts aren't kept here.
        </p>
        {posted.isLoading && <p className="text-sm text-slate-500">Loading…</p>}
        {posted.isError && (
          <ErrorNote>Could not load posted history: {posted.error.message}</ErrorNote>
        )}
        {posted.data && posted.data.length === 0 && (
          <p className="text-sm text-slate-500">Nothing posted yet.</p>
        )}
        <div className="space-y-2">
          {posted.data?.map((post) => <PostedCard key={post.id} post={post} />)}
        </div>
      </div>
    </div>
  );
}
