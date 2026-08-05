import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ChevronDown, ChevronRight, ExternalLink, Loader2 } from "lucide-react";
import { api, imageUrl } from "../lib/api";
import { getActiveJobId, getSettings, setActiveJobId } from "../lib/settings";
import type { CardBatchResult, Job, PipelineResult } from "../lib/types";
import { badgeClasses, ROSTER } from "../lib/utils";
import { BookmarkPreview } from "./BookmarkPreview";
import { ConsultationPause } from "./ConsultationPause";
import { ConsultationTranscript } from "./ConsultationTranscript";
import { ListingDetail } from "./ListingDetail";
import { QuoteCardDetail, QuoteCardPreview } from "./QuoteCardPreview";
import { ScoreCard } from "./ScoreCard";
import { BadgePill, Button, Card, CardContent, CardHeader, CardTitle, ErrorNote } from "./ui";

type PipelineKind = "bookmark" | "quote_card";
// Card jobs return PipelineResult; multi-quote batch jobs return CardBatchResult.
type PanelResult = PipelineResult | CardBatchResult;

// UI mirror of the backend's _CARD_BATCH_MAX spend guard.
const BATCH_MAX = 19;

export function PipelinePanel() {
  const [theme, setTheme] = useState("");
  const [kind, setKind] = useState<PipelineKind>("bookmark");
  const [language, setLanguage] = useState(""); // "" = English only
  // One entry per pinned quote. A single (or empty) entry is the classic run
  // with the mid-run check-in; 2+ non-empty entries queue a hands-free batch.
  const [quotes, setQuotes] = useState<string[]>([""]);
  // "Find quotes" (Librarian suggestions): topic defaults to the theme field
  // when left blank; results append into the quote boxes below.
  const [findTopic, setFindTopic] = useState("");
  const [findCount, setFindCount] = useState(4);
  const [findNote, setFindNote] = useState<string | null>(null);
  // Quote sources (rule 11 update 2026-08-04): default Ruhi Book 1 only.
  // sourceIds holds verified source ids; the risky web row adds "web:<url>".
  const [sourceIds, setSourceIds] = useState<string[]>(["ruhi_book1"]);
  const [webOn, setWebOn] = useState(false);
  const [webUrl, setWebUrl] = useState("");
  // Per-quote-box provenance from the finder (label + verified flag); null =
  // hand-typed. Display + citation pass-through only — the backend re-verifies
  // every quote itself on submit.
  const [quoteMeta, setQuoteMeta] = useState<({ source: string; verified: boolean } | null)[]>([null]);
  // Exact quotes + sources live behind a collapsed "Advanced options" —
  // most runs just want a theme; this is for when the owner wants to steer
  // the quote or widen sourcing (owner ask, 2026-08-05).
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [jobId, setJobId] = useState<string | null>(getActiveJobId());
  const queryClient = useQueryClient();

  const quoteSources = useQuery({
    queryKey: ["quote-sources"],
    queryFn: api.getQuoteSources,
    staleTime: Infinity,
    enabled: kind === "quote_card",
  });

  const activeSources = (): string[] | null => {
    const web = webOn && webUrl.trim() ? [`web:${webUrl.trim()}`] : [];
    const all = [...sourceIds, ...web];
    if (all.length === 0) return null; // nothing picked → backend default (Ruhi Book 1)
    if (all.length === 1 && all[0] === "ruhi_book1") return null; // plain default, request unchanged
    return all;
  };

  const toggleSource = (id: string) =>
    setSourceIds((ids) => (ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]));

  // Aligned quote/citation pairs for submission: citation labels ride along
  // only for unverified (web) suggestions — verified tiers re-derive theirs.
  const pinnedEntries = quotes
    .map((q, i) => ({
      q: q.trim(),
      c: quoteMeta[i] && !quoteMeta[i]!.verified ? quoteMeta[i]!.source : "",
    }))
    .filter((e) => e.q);
  const pinnedQuotes = pinnedEntries.map((e) => e.q);
  const pinnedCitations = pinnedEntries.map((e) => e.c);

  // Shown on the collapsed "Advanced options" row so customization is never
  // hidden silently — collapsing the panel doesn't collapse the state.
  const nonDefaultSources = activeSources();
  const advancedSummary = [
    pinnedQuotes.length > 0 ? `${pinnedQuotes.length} quote${pinnedQuotes.length > 1 ? "s" : ""} pinned` : null,
    nonDefaultSources ? `${nonDefaultSources.length} source${nonDefaultSources.length > 1 ? "s" : ""} selected` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  const languages = useQuery({
    queryKey: ["card-languages"],
    queryFn: api.getCardLanguages,
    staleTime: Infinity,
  });

  const start = useMutation({
    mutationFn: (t: string) => {
      const s = getSettings();
      if (kind !== "quote_card") return api.runPipeline(t, s.targetScore, s.maxAttempts);
      return pinnedQuotes.length > 1
        ? api.runCardBatch(t, language || null, pinnedQuotes, s.targetScore, s.maxAttempts,
                           activeSources(), pinnedCitations)
        : api.runCardPipeline(t, language || null, s.targetScore, s.maxAttempts,
                              pinnedQuotes[0] ?? "", activeSources(), pinnedCitations[0] ?? "");
    },
    onSuccess: (data) => {
      setJobId(data.job_id);
      setActiveJobId(data.job_id);
    },
  });

  // Free local search — the Librarian's Ruhi Book 1 index, no paid calls.
  // Suggestions arrive pre-verified (verbatim corpus text), so whatever lands
  // in the boxes will pass the batch's verification untouched.
  const suggest = useMutation({
    mutationFn: () => api.suggestRuhiQuotes(findTopic.trim() || theme.trim(), findCount, activeSources()),
    onSuccess: (res) => {
      const kept: string[] = [];
      const keptMeta: ({ source: string; verified: boolean } | null)[] = [];
      quotes.forEach((q, i) => {
        if (q.trim()) {
          kept.push(q);
          keptMeta.push(quoteMeta[i] ?? null);
        }
      });
      const have = new Set(kept.map((q) => q.trim()));
      const freshItems = res.items.filter((i) => !have.has(i.quote));
      const merged = [...kept, ...freshItems.map((i) => i.quote)].slice(0, BATCH_MAX);
      const mergedMeta = [
        ...keptMeta,
        ...freshItems.map((i) => ({ source: i.source, verified: i.verified })),
      ].slice(0, BATCH_MAX);
      setQuotes(merged.length ? merged : [""]);
      setQuoteMeta(mergedMeta.length ? mergedMeta : [null]);
      const librarian = ROSTER.librarian.name;
      if (!freshItems.length) {
        setFindNote(
          `${librarian} found ${res.items.length} passage${res.items.length === 1 ? "" : "s"} about "${res.topic}", but they're already in your list.` +
          (res.web_note ? ` (${res.web_note})` : "")
        );
      } else {
        const dropped = kept.length + freshItems.length - merged.length;
        const shortened = freshItems.filter((i) => i.shortened).length;
        const unverified = freshItems.filter((i) => !i.verified).length;
        setFindNote(
          `${librarian} added ${freshItems.length} quote${freshItems.length === 1 ? "" : "s"} about "${res.topic}" below` +
          (unverified ? ` — ${unverified} from the web, wording NOT verified` : ", all verified") +
          (shortened ? ` (${shortened} shortened at a sentence end to fit the card)` : "") +
          (dropped > 0 ? ` — ${dropped} didn't fit under the ${BATCH_MAX}-card cap` : "") +
          ". Look them over and remove any you don't want before generating." +
          (res.web_note ? ` Note: ${res.web_note}` : "")
        );
      }
    },
  });

  const jobQuery = useQuery<Job<PanelResult>>({
    queryKey: ["job", jobId],
    queryFn: () => api.getPipelineStatus<PanelResult>(jobId as string),
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
              {running
                ? "Working..."
                : kind === "quote_card"
                  ? pinnedQuotes.length > 1
                    ? `Generate ${pinnedQuotes.length} quote cards`
                    : "Generate quote card"
                  : "Generate bookmark"}
            </Button>
          </div>
          {kind === "quote_card" && (
            <div className="mt-4">
              <button
                type="button"
                onClick={() => setAdvancedOpen((o) => !o)}
                className="flex w-full items-center justify-between gap-3 rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2 text-left hover:border-amber-400/40"
              >
                <span className="flex items-center gap-1.5 text-sm font-medium text-slate-300">
                  {advancedOpen ? (
                    <ChevronDown className="h-4 w-4 text-slate-500" />
                  ) : (
                    <ChevronRight className="h-4 w-4 text-slate-500" />
                  )}
                  Advanced options
                  <span className="font-normal text-slate-500">— exact quotes, sources</span>
                </span>
                {!advancedOpen && advancedSummary && (
                  <span className="shrink-0 text-xs text-amber-300/80">{advancedSummary}</span>
                )}
              </button>
            </div>
          )}
          {kind === "quote_card" && advancedOpen && (
            <div className="mt-2 space-y-1.5">
              <label className="block text-sm font-medium text-slate-300">
                Exact quote{quotes.length > 1 ? "s" : ""} (optional)
              </label>
              <div className="space-y-2 rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-xs font-medium uppercase tracking-widest text-slate-500">
                    Quote sources
                  </span>
                  <div className="flex gap-2">
                    <button
                      onClick={() => setSourceIds(["ruhi_book1"])}
                      disabled={running}
                      className="rounded border border-slate-700 px-2 py-0.5 text-[11px] text-slate-400 hover:border-amber-400/60 hover:text-amber-300"
                    >
                      Ruhi Book 1 only
                    </button>
                    <button
                      onClick={() =>
                        setSourceIds((quoteSources.data?.sources ?? []).map((s) => s.id))
                      }
                      disabled={running || !quoteSources.data}
                      className="rounded border border-slate-700 px-2 py-0.5 text-[11px] text-slate-400 hover:border-amber-400/60 hover:text-amber-300"
                    >
                      All library texts
                    </button>
                  </div>
                </div>
                <div className="flex flex-wrap gap-x-4 gap-y-1.5">
                  {(quoteSources.data?.sources ?? [
                    { id: "ruhi_book1", name: "Ruhi Book 1 — Reflections on the Life of the Spirit", kind: "verified" as const, default: true },
                  ]).map((s) => (
                    <label key={s.id} className="flex cursor-pointer items-center gap-1.5 text-xs text-slate-300">
                      <input
                        type="checkbox"
                        checked={sourceIds.includes(s.id)}
                        onChange={() => toggleSource(s.id)}
                        disabled={running}
                        className="accent-amber-400"
                      />
                      {s.name}
                    </label>
                  ))}
                </div>
                <div className="flex flex-wrap items-center gap-2 border-t border-slate-800 pt-2">
                  <label className="flex cursor-pointer items-center gap-1.5 text-xs text-amber-300/90">
                    <input
                      type="checkbox"
                      checked={webOn}
                      onChange={(e) => setWebOn(e.target.checked)}
                      disabled={running}
                      className="accent-amber-400"
                    />
                    Also search a web page (risky)
                  </label>
                  {webOn && (
                    <input
                      value={webUrl}
                      onChange={(e) => setWebUrl(e.target.value)}
                      placeholder="https://reference.bahai.org/... (a page that shows the text)"
                      disabled={running}
                      className="min-w-[240px] flex-1 rounded-lg border border-amber-400/30 bg-slate-950 px-3 py-1.5 text-xs text-slate-100 placeholder-slate-600 focus:border-amber-400/60"
                    />
                  )}
                </div>
                {webOn && (
                  <p className="text-xs text-amber-300/70">
                    Web quotes are fetched, not verified — the wording can be wrong and some pages
                    won't work (bahai.org's new library needs JavaScript; reference.bahai.org pages
                    work well). Cards made from a web quote are marked "not source-verified" in the
                    gallery, and translations aren't offered any extra checking either.
                  </p>
                )}
                {sourceIds.length === 0 && !(webOn && webUrl.trim()) && (
                  <p className="text-xs text-slate-500">
                    Nothing selected — the team will use Ruhi Book 1 (the default).
                  </p>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2.5">
                <span className="shrink-0 text-xs text-slate-400">
                  Ask {ROSTER.librarian.name} (the Librarian) to find
                </span>
                <input
                  type="number"
                  min={1}
                  max={BATCH_MAX}
                  value={findCount}
                  onChange={(e) =>
                    setFindCount(Math.min(BATCH_MAX, Math.max(1, parseInt(e.target.value) || 1)))
                  }
                  disabled={running || suggest.isPending}
                  className="w-16 rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm text-slate-100 focus:border-amber-400/60"
                />
                <span className="shrink-0 text-xs text-slate-400">quotes about</span>
                <input
                  value={findTopic}
                  onChange={(e) => setFindTopic(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && !suggest.isPending && suggest.mutate()}
                  placeholder={theme.trim() ? `the theme above ("${theme.trim().slice(0, 30)}")` : 'e.g. "prayer", "kindness", "the soul"'}
                  disabled={running || suggest.isPending}
                  className="min-w-[170px] flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm text-slate-100 placeholder-slate-600 focus:border-amber-400/60"
                />
                <button
                  onClick={() => {
                    setFindNote(null);
                    suggest.mutate();
                  }}
                  disabled={running || suggest.isPending || (!findTopic.trim() && !theme.trim())}
                  className="shrink-0 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:border-amber-400/60 hover:text-amber-300 disabled:opacity-50"
                >
                  {suggest.isPending ? "Searching selected sources..." : "Find quotes"}
                </button>
              </div>
              {findNote && !suggest.isError && (
                <p className="text-xs text-emerald-300/80">{findNote}</p>
              )}
              {suggest.isError && (
                <ErrorNote>
                  Could not fetch quotes: {(suggest.error as Error).message}
                </ErrorNote>
              )}
              {quotes.map((q, i) => (
                <div key={i}>
                  <div className="flex gap-2">
                    <textarea
                      value={q}
                      onChange={(e) => {
                        setQuotes((qs) => qs.map((v, j) => (j === i ? e.target.value : v)));
                        // Edited by hand → the finder's provenance label no longer applies.
                        setQuoteMeta((ms) => ms.map((m, j) => (j === i ? null : m)));
                      }}
                      disabled={running}
                      rows={2}
                      placeholder={i === 0 ? "Paste the quote text here..." : "Paste another quote..."}
                      className="w-full rounded-lg border border-slate-700 bg-slate-950 px-4 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:border-amber-400/60 focus:outline-none resize-none"
                    />
                    {quotes.length > 1 && (
                      <button
                        onClick={() => {
                          setQuotes((qs) => qs.filter((_, j) => j !== i));
                          setQuoteMeta((ms) => ms.filter((_, j) => j !== i));
                        }}
                        disabled={running}
                        title="Remove this quote"
                        className="self-start rounded-lg border border-slate-700 px-2.5 py-1.5 text-sm text-slate-400 hover:border-rose-400/50 hover:text-rose-300"
                      >
                        ×
                      </button>
                    )}
                  </div>
                  {quoteMeta[i] && (
                    <p className={`mt-0.5 text-[11px] ${quoteMeta[i]!.verified ? "text-emerald-300/70" : "text-amber-300/80"}`}>
                      {quoteMeta[i]!.source} · {quoteMeta[i]!.verified ? "verified" : "web — wording not verified"}
                    </p>
                  )}
                </div>
              ))}
              <div className="flex items-start justify-between gap-3">
                <p className="text-xs text-slate-500">
                  {quotes.length > 1 ? (
                    <>
                      Two or more quotes run as a <span className="text-slate-400">hands-free batch</span>:
                      each quote becomes its own card (same theme and language), made one at a time,
                      and the mid-run check-in is skipped — the team proceeds on its own. Every quote
                      is verified against your selected sources before anything is generated
                      (web-sourced quotes are the deliberate exception — they print as fetched,
                      flagged as unverified).
                    </>
                  ) : (
                    <>
                      Paste a quote from a selected source to print it exactly as written — the team
                      then only designs the artwork around it. Leave empty to let the team choose
                      from the selected sources. Shortening with … is allowed at sentence ends. Add
                      more quotes to queue a hands-free batch of cards.
                    </>
                  )}
                </p>
                <button
                  onClick={() => {
                    setQuotes((qs) => [...qs, ""]);
                    setQuoteMeta((ms) => [...ms, null]);
                  }}
                  disabled={running || quotes.length >= BATCH_MAX}
                  className="shrink-0 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:border-amber-400/60 hover:text-amber-300 disabled:opacity-50"
                >
                  + Add another quote
                </button>
              </div>
            </div>
          )}
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
              Job {job.job_id} ·{" "}
              {job.kind === "card-batch"
                ? "each card in the batch takes about 3–5 minutes, made one after another."
                : "a full run usually takes 3–5 minutes."}{" "}
              You can switch tabs — the run continues on the server.
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

      {result && "batch" in result && <CardBatchResults result={result} />}

      {result && !("batch" in result) && (
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
                variantFaces={result.variant_faces}
              />
              <QuoteCardDetail
                quote={result.quote ?? ""}
                citation={result.citation}
                quoteGrounded={result.quote_grounded}
                quoteProvenance={result.quote_provenance}
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

          {result.canva?.skipped && (
            <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 px-4 py-3 text-sm text-amber-300">
              {result.canva.reason || "Canva autofill is turned off — skipped"}
            </div>
          )}
        </>
      )}
    </div>
  );
}

const VARIANT_NAMES: Record<string, string> = { es: "Spanish", zh: "Chinese", ar: "Arabic" };

/** Result view for a hands-free multi-quote batch: a summary card, then one
 *  compact card per quote (faces + score, or the error if that card failed —
 *  a failed card is announced here, never silently missing). Full detail for
 *  each finished card lives in the Products tab. */
function CardBatchResults({ result }: { result: CardBatchResult }) {
  return (
    <>
      <Card>
        <CardContent className="pt-4">
          <div className="text-xs uppercase tracking-widest text-slate-500">Batch complete</div>
          <div className="mt-0.5 text-lg text-slate-100">
            {result.theme}
            {result.language_name ? ` · English + ${result.language_name}` : ""}
          </div>
          <div className="mt-0.5 text-sm text-slate-400">
            {result.completed} of {result.total} cards made
            {result.failed > 0 && (
              <span className="text-rose-300"> · {result.failed} failed (details below)</span>
            )}
            {" "}— each finished card is in the Products tab for printing and edits.
          </div>
        </CardContent>
      </Card>

      {result.items.map((item) => (
        <Card key={item.index}>
          <CardContent className="pt-4">
            <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
              <div className="max-w-xl text-sm text-slate-300">
                <span className="font-mono text-xs text-slate-500">
                  Card {item.index}/{result.total} ·{" "}
                </span>
                “{item.quote.length > 120 ? `${item.quote.slice(0, 120)}…` : item.quote}”
              </div>
              {item.status === "done" && (
                <div className="flex items-center gap-2">
                  <span className="font-mono text-lg text-slate-100">
                    {item.overall?.toFixed(1)}/10
                  </span>
                  {item.badge && <BadgePill className={badgeClasses(item.badge)}>{item.badge}</BadgePill>}
                </div>
              )}
            </div>
            {item.status === "error" ? (
              <ErrorNote>This card could not be made and was skipped: {item.error}</ErrorNote>
            ) : (
              <div className="flex flex-wrap gap-3">
                <figure>
                  <img
                    src={imageUrl(item.front_image_web)}
                    alt={`Card ${item.index} front`}
                    className="w-60 rounded-lg border border-slate-800"
                  />
                  <figcaption className="mt-1 text-center text-xs text-slate-500">Front</figcaption>
                </figure>
                <figure>
                  <img
                    src={imageUrl(item.back_image_web)}
                    alt={`Card ${item.index} back`}
                    className="w-60 rounded-lg border border-slate-800"
                  />
                  <figcaption className="mt-1 text-center text-xs text-slate-500">Back</figcaption>
                </figure>
                {Object.entries(item.variant_faces ?? {}).map(([code, pair]) => (
                  <figure key={code}>
                    <img
                      src={imageUrl(pair.front)}
                      alt={`Card ${item.index} ${VARIANT_NAMES[code] ?? code} front`}
                      className="w-60 rounded-lg border border-slate-800"
                    />
                    <figcaption className="mt-1 text-center text-xs text-slate-500">
                      {VARIANT_NAMES[code] ?? code} front
                    </figcaption>
                  </figure>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      ))}
    </>
  );
}
