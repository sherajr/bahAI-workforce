import { useEffect, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Download, ExternalLink, Loader2, Printer, X } from "lucide-react";
import { api, frontImageUrl, imageUrl } from "../lib/api";
import type {
  EditProductPayload, EditProductResult, EtsyPublishResult, ImproveResult, Job, ProductRow,
  RegenerateImageResult, RegenerateQuoteResult,
} from "../lib/types";
import {
  badgeClasses, badgeForProduct, formatDate, isQuoteCard, parseCardCopy, parseListing,
  parseReview, usd,
} from "../lib/utils";
import { ConsultationPause } from "./ConsultationPause";
import { ConsultationTranscript } from "./ConsultationTranscript";
import { ListingDetail } from "./ListingDetail";
import { QuoteCardDetail } from "./QuoteCardPreview";
import { ScoreCard } from "./ScoreCard";
import { BadgePill, Button, Card, CardContent, ErrorNote } from "./ui";

// ── Card ──────────────────────────────────────────────────────────────────────

/** Small circular download button overlaid on an image. */
function DownloadCircle({ href, filename, label }: { href: string; filename: string; label: string }) {
  return (
    <a
      href={href}
      download={filename}
      title={`Download ${label}`}
      onClick={(e) => e.stopPropagation()}
      className="absolute bottom-1.5 right-1.5 flex h-7 w-7 items-center justify-center rounded-full border border-slate-700 bg-slate-950/80 text-slate-300 backdrop-blur transition-colors hover:border-amber-400 hover:text-amber-300"
    >
      <Download className="h-3.5 w-3.5" />
    </a>
  );
}

/** One pane of the card preview (front or back) with its download button. */
function PaneImage({
  src, fallback, alt, downloadName,
}: { src: string; fallback?: string; alt: string; downloadName: string }) {
  const [cur, setCur] = useState(src || fallback || "");
  if (!cur) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <span className="text-xs text-slate-600">No image</span>
      </div>
    );
  }
  return (
    <div className="relative flex-1 overflow-hidden">
      <img
        src={cur}
        alt={alt}
        onError={() => setCur(fallback && cur !== fallback ? fallback : "")}
        className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.03]"
      />
      <DownloadCircle href={cur} filename={downloadName} label={alt.toLowerCase()} />
    </div>
  );
}

function ProductCard({ product, onOpen }: { product: ProductRow; onOpen: () => void }) {
  const review = parseReview(product);
  const overall = review?.overall ?? 0;
  const quoteCard = isQuoteCard(product);
  const cardCopy = parseCardCopy(product);
  // Final product renders (stored by the pipeline / backfill); fall back to the
  // legacy filename guess, then to the raw artwork for very old products.
  const front = imageUrl(product.front_image) || frontImageUrl(product.image_url);
  const back = imageUrl(product.back_image);
  const artwork = imageUrl(product.image_url);

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(); } }}
      className="group flex cursor-pointer flex-col overflow-hidden rounded-xl border border-slate-800 bg-slate-900/70 text-left transition-colors hover:border-amber-400/40"
    >
      {/* Quote cards are landscape (3.5×2): stack front over back instead of
          the bookmarks' side-by-side portrait panes. */}
      <div className={`flex h-56 gap-px overflow-hidden bg-slate-950 ${quoteCard ? "flex-col" : ""}`}>
        <PaneImage
          key={`f-${front || artwork}`}
          src={front}
          fallback={artwork}
          alt="Front"
          downloadName={`${product.id}-front.png`}
        />
        {back && (
          <PaneImage
            key={`b-${back}`}
            src={back}
            alt="Back"
            downloadName={`${product.id}-back.png`}
          />
        )}
      </div>
      <div className="flex flex-1 flex-col gap-2 p-4">
        {quoteCard && (
          <div className="text-[10px] uppercase tracking-widest text-sky-300">
            Quote card{cardCopy?.language_name ? ` · English + ${cardCopy.language_name}` : " · English"}
          </div>
        )}
        <div className="line-clamp-2 text-sm font-medium text-slate-100">
          {product.title ?? product.theme ?? product.id}
        </div>
        <div className="mt-auto flex items-center justify-between">
          <BadgePill className={badgeClasses(badgeForProduct(product, overall))}>
            {badgeForProduct(product, overall)}
          </BadgePill>
          <span className="font-mono text-xs text-slate-400">{overall.toFixed(1)}/10</span>
        </div>
        <div className="text-xs text-slate-600">{formatDate(product.created_at)}</div>
      </div>
    </div>
  );
}

// ── Detail drawer ─────────────────────────────────────────────────────────────

function ProductDrawer({ product, onClose }: { product: ProductRow; onClose: () => void }) {
  const queryClient = useQueryClient();
  const quoteCard = isQuoteCard(product);
  const cardCopy = parseCardCopy(product);
  // For quote cards listing_copy holds card JSON, not an Etsy listing.
  const listing = quoteCard ? null : parseListing(product);
  const review = parseReview(product);
  const [notes, setNotes] = useState("");
  const [revenue, setRevenue] = useState("");
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({
    title: listing?.title ?? "",
    description: listing?.description ?? "",
    bookmark_quote: listing?.bookmark_quote ?? "",
    tags: (listing?.tags ?? []).join(", "),
    materials: (listing?.materials ?? []).join(", "),
    price_note: listing?.price_note ?? "",
  });

  const etsyStatus = useQuery({ queryKey: ["etsy-status"], queryFn: api.getEtsyStatus });

  const edit = useMutation<EditProductResult, Error, EditProductPayload>({
    mutationFn: (payload) => api.editProduct(product.id, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["products"] });
      setEditing(false);
    },
  });

  function saveEdit() {
    edit.mutate({
      title: form.title.trim(),
      description: form.description.trim(),
      bookmark_quote: form.bookmark_quote.trim(),
      tags: form.tags.split(",").map((t) => t.trim()).filter(Boolean),
      materials: form.materials.split(",").map((t) => t.trim()).filter(Boolean),
      price_note: form.price_note.trim(),
    });
  }

  const improve = useMutation<ImproveResult, Error, void>({
    mutationFn: () => api.improveProduct(product.id, notes),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["products"] }),
  });

  const publish = useMutation<EtsyPublishResult, Error, void>({
    // Trust gate: while the Reviewer is below Human-on-the-loop (level 2),
    // the API pauses the publish and asks for explicit confirmation.
    mutationFn: async () => {
      const first = await api.publishToEtsy(product.id);
      if (!first.requires_confirmation) return first;
      const ok = window.confirm(`${first.reason}\n\nCreate the Etsy draft anyway?`);
      if (!ok) {
        return {
          skipped: true,
          reason: "Cancelled — the Reviewer hasn't earned unattended publishing yet.",
        };
      }
      return api.publishToEtsy(product.id, true);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["products"] }),
  });

  const record = useMutation({
    mutationFn: () => api.recordRevenue(product.id, parseFloat(revenue)),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["products"] });
      queryClient.invalidateQueries({ queryKey: ["steward"] });
      setRevenue("");
    },
  });

  const printSheet = useMutation<void, Error, void>({
    mutationFn: () => api.downloadPrintSheet(product.id, product.title),
  });

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-slate-950/70" onClick={onClose}>
      <div
        className="h-full w-full max-w-2xl overflow-y-auto border-l border-slate-800 bg-slate-950 p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg text-slate-100">{product.title ?? product.theme}</h2>
            <div className="mt-1 font-mono text-xs text-slate-500">
              product {product.id} · {formatDate(product.created_at)}
              {product.etsy_listing_id && ` · Etsy #${product.etsy_listing_id}`}
            </div>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-800">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="mb-5 flex flex-wrap items-start justify-center gap-6 rounded-xl border border-slate-800 bg-slate-900/50 p-4">
          <DrawerImage
            label="Front"
            src={imageUrl(product.front_image) || frontImageUrl(product.image_url)}
            downloadName={`${product.id}-front.png`}
          />
          {product.back_image ? (
            <DrawerImage
              label="Back"
              src={imageUrl(product.back_image)}
              downloadName={`${product.id}-back.png`}
            />
          ) : (
            <DrawerImage
              label="Artwork"
              src={imageUrl(product.image_url)}
              downloadName={`${product.id}-artwork.jpg`}
            />
          )}
        </div>

        {product.front_image && product.back_image && (
          <div className="mb-5 flex items-center justify-center gap-3">
            <Button loading={printSheet.isPending} onClick={() => printSheet.mutate()}>
              <Printer className="h-4 w-4" />
              {printSheet.isPending ? "Building sheet..." : "Download printable sheet"}
            </Button>
            <span className="text-xs text-slate-500">Letter page, ready to cut</span>
          </div>
        )}
        {printSheet.isError && <ErrorNote>{printSheet.error.message}</ErrorNote>}

        <div className="space-y-5">
          {review && <ScoreCard review={review} />}
          {quoteCard && cardCopy && (
            <QuoteCardDetail
              quote={cardCopy.quote}
              citation={cardCopy.citation}
              quoteGrounded={cardCopy.quote_grounded}
              languageName={cardCopy.language_name}
              translationText={cardCopy.translation_text}
              disclaimerNative={cardCopy.translation_disclaimer_native}
              disclaimerEn={cardCopy.translation_disclaimer_en}
              artworkDisclosure={cardCopy.artwork_disclosure}
            />
          )}
          {quoteCard && <FeedbackCard product={product} />}
          {listing && <ListingDetail listing={listing} />}

          {/* Everything below acts on the listing/Etsy machinery — quote
              cards have neither (they're given away, not sold), and the API
              rejects these actions for cards anyway. */}
          {!quoteCard && (<>
          <Card>
            <CardContent className="space-y-3 pt-4">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-slate-100">Edit listing manually</h3>
                {!editing && (
                  <Button variant="secondary" onClick={() => setEditing(true)}>
                    Edit
                  </Button>
                )}
              </div>
              {editing && (
                <div className="space-y-3">
                  <Field label="Title">
                    <input
                      value={form.title}
                      onChange={(e) => setForm({ ...form, title: e.target.value })}
                      className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
                    />
                  </Field>
                  <Field label="Bookmark quote">
                    <textarea
                      value={form.bookmark_quote}
                      onChange={(e) => setForm({ ...form, bookmark_quote: e.target.value })}
                      rows={2}
                      className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
                    />
                  </Field>
                  <Field label="Description">
                    <textarea
                      value={form.description}
                      onChange={(e) => setForm({ ...form, description: e.target.value })}
                      rows={8}
                      className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
                    />
                  </Field>
                  <Field label="Tags (comma-separated)">
                    <input
                      value={form.tags}
                      onChange={(e) => setForm({ ...form, tags: e.target.value })}
                      className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
                    />
                  </Field>
                  <Field label="Materials (comma-separated)">
                    <input
                      value={form.materials}
                      onChange={(e) => setForm({ ...form, materials: e.target.value })}
                      className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
                    />
                  </Field>
                  <Field label="Price note">
                    <input
                      value={form.price_note}
                      onChange={(e) => setForm({ ...form, price_note: e.target.value })}
                      className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
                    />
                  </Field>
                  <div className="flex gap-2">
                    <Button loading={edit.isPending} onClick={saveEdit}>
                      Save changes
                    </Button>
                    <Button
                      variant="secondary"
                      onClick={() => {
                        setEditing(false);
                        setForm({
                          title: listing?.title ?? "",
                          description: listing?.description ?? "",
                          bookmark_quote: listing?.bookmark_quote ?? "",
                          tags: (listing?.tags ?? []).join(", "),
                          materials: (listing?.materials ?? []).join(", "),
                          price_note: listing?.price_note ?? "",
                        });
                      }}
                    >
                      Cancel
                    </Button>
                  </div>
                  {edit.isError && <ErrorNote>{edit.error.message}</ErrorNote>}
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardContent className="space-y-3 pt-4">
              <h3 className="text-sm font-semibold text-slate-100">Ask the team to improve it</h3>
              <input
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder='Optional guidance, e.g. "make it more poetic"'
                className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 placeholder-slate-600"
              />
              <Button loading={improve.isPending} onClick={() => improve.mutate()} variant="secondary">
                {improve.isPending ? "Revising (1–2 min)..." : "Improve listing"}
              </Button>
              {improve.isSuccess && (
                <p className="text-sm text-slate-400">
                  {improve.data.improved
                    ? improve.data.new_score > improve.data.old_score
                      ? `Improved ${improve.data.old_score.toFixed(1)} → ${improve.data.new_score.toFixed(1)} in ${improve.data.attempts} attempt${improve.data.attempts > 1 ? "s" : ""}.`
                      : `Revision kept at ${improve.data.new_score.toFixed(1)} — same score, more reviewer feedback incorporated.`
                    : `No improvement found (still ${improve.data.old_score.toFixed(1)}). The previous version was kept.`}
                </p>
              )}
              {improve.isError && <ErrorNote>{improve.error.message}</ErrorNote>}
            </CardContent>
          </Card>

          <RedirectCard product={product} />

          <Card>
            <CardContent className="space-y-3 pt-4">
              <h3 className="text-sm font-semibold text-slate-100">Publish to Etsy (draft)</h3>
              <p className="text-sm text-slate-400">
                Creates a <em>draft</em> listing in your shop with title, description, tags, price,
                and the front image. You review and activate it inside Etsy — nothing goes live on
                its own.
              </p>
              {product.etsy_listing_id ? (
                <p className="text-sm text-emerald-300">
                  Already on Etsy as draft #{product.etsy_listing_id}.
                </p>
              ) : etsyStatus.data && !etsyStatus.data.configured ? (
                <p className="text-sm text-slate-500">
                  Etsy isn’t connected yet — add your keys in Settings.
                </p>
              ) : (
                <Button loading={publish.isPending} onClick={() => publish.mutate()}>
                  Create draft on Etsy
                </Button>
              )}
              {publish.isSuccess &&
                (publish.data.skipped ? (
                  <p className="text-sm text-orange-300">Skipped: {publish.data.reason}</p>
                ) : (
                  <div className="space-y-1 text-sm text-slate-300">
                    <p>
                      Draft #{publish.data.etsy_listing_id} created
                      {publish.data.image_uploaded ? " with the front image." : "."}
                    </p>
                    {publish.data.image_error && (
                      <p className="text-orange-300">Image upload issue: {publish.data.image_error}</p>
                    )}
                    {publish.data.url && (
                      <a
                        href={publish.data.url}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1.5 text-amber-300 hover:underline"
                      >
                        Open in Etsy shop manager <ExternalLink className="h-3.5 w-3.5" />
                      </a>
                    )}
                  </div>
                ))}
              {publish.isError && <ErrorNote>{publish.error.message}</ErrorNote>}
            </CardContent>
          </Card>

          <Card>
            <CardContent className="space-y-3 pt-4">
              <h3 className="text-sm font-semibold text-slate-100">Record a sale</h3>
              <p className="text-sm text-slate-400">
                Current recorded revenue: {usd(Number(product.revenue ?? 0))}
              </p>
              <div className="flex gap-2">
                <input
                  value={revenue}
                  onChange={(e) => setRevenue(e.target.value)}
                  placeholder="Total revenue, e.g. 11.98"
                  inputMode="decimal"
                  className="w-48 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 placeholder-slate-600"
                />
                <Button
                  variant="secondary"
                  loading={record.isPending}
                  disabled={!revenue || isNaN(parseFloat(revenue))}
                  onClick={() => record.mutate()}
                >
                  Save revenue
                </Button>
              </div>
              {record.isError && <ErrorNote>{(record.error as Error).message}</ErrorNote>}
            </CardContent>
          </Card>
          </>)}
        </div>
      </div>
    </div>
  );
}

/** The ground-truth loop for the giveaway line (constitution principle 7):
 * the Reviewer guesses newcomer accessibility; this records what actually
 * happened when Sheraj handed the card to a real person. */
function FeedbackCard({ product }: { product: ProductRow }) {
  const queryClient = useQueryClient();
  const [text, setText] = useState(product.recipient_feedback ?? "");

  const save = useMutation({
    mutationFn: () => api.recordFeedback(product.id, text.trim()),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["products"] }),
  });

  return (
    <Card>
      <CardContent className="space-y-3 pt-4">
        <h3 className="text-sm font-semibold text-slate-100">How did it land?</h3>
        <p className="text-sm text-slate-400">
          After you give this card to someone, note their reaction here — it's the only real
          test of "newcomer accessibility" the team's own scores can't provide.
        </p>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder='e.g. "Gave it to a coworker — she asked what the Faith was and kept the card."'
          rows={3}
          className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 placeholder-slate-600"
        />
        <Button
          variant="secondary"
          loading={save.isPending}
          disabled={(product.recipient_feedback ?? "") === text.trim()}
          onClick={() => save.mutate()}
        >
          Save feedback
        </Button>
        {save.isSuccess && <p className="text-sm text-emerald-300">Saved.</p>}
        {save.isError && <ErrorNote>{(save.error as Error).message}</ErrorNote>}
      </CardContent>
    </Card>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-xs uppercase tracking-widest text-slate-500">{label}</span>
      {children}
    </label>
  );
}

/** Redirect what the team produces, BEFORE the review step: a new quote, new
 * artwork, or a full redo — as opposed to "Improve listing" above, which only
 * ever edits the existing listing text. */
function RedirectCard({ product }: { product: ProductRow }) {
  const queryClient = useQueryClient();
  const [guidance, setGuidance] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);

  const quote = useMutation<RegenerateQuoteResult, Error, void>({
    mutationFn: () => api.regenerateQuote(product.id, guidance),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["products"] }),
  });

  const image = useMutation<RegenerateImageResult, Error, void>({
    mutationFn: () => api.regenerateImage(product.id, guidance),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["products"] }),
  });

  const redoAll = useMutation<{ job_id: string }, Error, void>({
    mutationFn: () => api.regenerateAll(product.id, guidance),
    onSuccess: (data) => setJobId(data.job_id),
  });

  const jobQuery = useQuery<Job>({
    queryKey: ["job", jobId],
    queryFn: () => api.getPipelineStatus(jobId as string),
    enabled: !!jobId,
    refetchInterval: (q) =>
      ["running", "waiting_for_input"].includes(q.state.data?.status ?? "") ? 2500 : false,
  });
  const job = jobQuery.data;
  const redoing = redoAll.isPending || job?.status === "running" || job?.status === "waiting_for_input";

  useEffect(() => {
    if (job?.status === "done") {
      queryClient.invalidateQueries({ queryKey: ["products"] });
    }
  }, [job?.status, queryClient]);

  const anyPending = quote.isPending || image.isPending || redoing;

  return (
    <Card>
      <CardContent className="space-y-3 pt-4">
        <h3 className="text-sm font-semibold text-slate-100">Redirect the team</h3>
        <p className="text-sm text-slate-400">
          Change direction BEFORE the next review — a different quote, different artwork, or start
          the whole piece over. Unlike "Improve listing" above, these act on the quote and image
          themselves, not just the listing text.
        </p>
        <textarea
          value={guidance}
          onChange={(e) => setGuidance(e.target.value)}
          placeholder='e.g. "make the quote about detachment instead", "more vibrant colors, remove the lotus"'
          rows={2}
          disabled={anyPending}
          className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 placeholder-slate-600"
        />
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" loading={quote.isPending} disabled={anyPending} onClick={() => quote.mutate()}>
            {quote.isPending ? "Searching (30-60s)..." : "New quote"}
          </Button>
          <Button
            variant="secondary"
            loading={image.isPending}
            disabled={anyPending || !guidance.trim()}
            onClick={() => image.mutate()}
          >
            {image.isPending ? "Painting (1-2 min)..." : "New artwork"}
          </Button>
          <Button loading={redoing} disabled={anyPending} onClick={() => redoAll.mutate()}>
            {redoing ? "Redoing (2-4 min)..." : "Redo everything"}
          </Button>
        </div>
        {!guidance.trim() && (
          <p className="text-xs text-slate-600">
            "New artwork" needs guidance on what should change. "New quote" and "Redo everything"
            work with no guidance too — they'll follow the original theme.
          </p>
        )}
        <p className="text-xs text-slate-600">
          "Redo everything" is one fresh pass, not a hunt for a target score — whatever the team
          produces this time is what gets saved, better or worse.
        </p>

        {redoing && job?.steps && job.steps.length > 0 && (
          <ol className="space-y-1 rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            {job.steps.map((s, i) => {
              const isLast = i === job.steps.length - 1;
              return (
                <li key={i} className="flex items-start gap-2 text-xs">
                  {isLast ? (
                    <Loader2 className="mt-0.5 h-3 w-3 shrink-0 animate-spin text-amber-400" />
                  ) : (
                    <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0 text-emerald-400" />
                  )}
                  <span className={isLast ? "text-slate-300" : "text-slate-600"}>{s.message}</span>
                </li>
              );
            })}
          </ol>
        )}
        {(job?.status === "running" || job?.status === "waiting_for_input") &&
          job.consultation_live && job.consultation_live.length > 0 && (
            <ConsultationTranscript turns={job.consultation_live} />
          )}
        {job?.status === "waiting_for_input" && job.pending_prompt && (
          <ConsultationPause jobId={job.job_id} prompt={job.pending_prompt} />
        )}
        {job?.status === "done" && (
          <p className="text-sm text-slate-400">
            Redo complete and saved — {job.result?.review?.overall?.toFixed(1)}/10.
          </p>
        )}
        {job?.status === "error" && <ErrorNote>Redo failed: {job.error}</ErrorNote>}

        {quote.isSuccess && (
          <p className="text-sm text-slate-400">
            New quote ({quote.data.source}): "{quote.data.new_quote.slice(0, 80)}
            {quote.data.new_quote.length > 80 ? "..." : ""}" — score {quote.data.old_score.toFixed(1)} →{" "}
            {quote.data.new_score.toFixed(1)}.
          </p>
        )}
        {image.isSuccess && (
          <p className="text-sm text-slate-400">
            New artwork generated — score {image.data.old_score.toFixed(1)} → {image.data.new_score.toFixed(1)}.
          </p>
        )}
        {quote.isError && <ErrorNote>{quote.error.message}</ErrorNote>}
        {image.isError && <ErrorNote>{image.error.message}</ErrorNote>}
        {redoAll.isError && <ErrorNote>{redoAll.error.message}</ErrorNote>}
      </CardContent>
    </Card>
  );
}

function DrawerImage({ label, src, downloadName }: { label: string; src: string; downloadName: string }) {
  const [failed, setFailed] = useState(false);
  if (!src || failed) return null;
  return (
    <div className="flex flex-col items-center gap-1.5">
      <span className="text-[11px] uppercase tracking-widest text-slate-500">{label}</span>
      <div className="relative">
        <img
          src={src}
          alt={label}
          onError={() => setFailed(true)}
          className="max-h-72 rounded-lg border border-slate-800 object-contain"
        />
        <DownloadCircle href={src} filename={downloadName} label={label.toLowerCase()} />
      </div>
    </div>
  );
}

// ── Gallery ───────────────────────────────────────────────────────────────────

export function ProductsGallery() {
  const [openId, setOpenId] = useState<string | null>(null);

  const products = useQuery({
    queryKey: ["products"],
    queryFn: api.getProducts,
    refetchInterval: 30_000,
  });

  const steward = useQuery({
    queryKey: ["steward"],
    queryFn: api.getStewardReport,
    refetchInterval: 60_000,
  });

  const open = products.data?.find((p) => p.id === openId) ?? null;

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      {steward.data && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            <Stat label="Products" value={String(steward.data.total_products)} />
            <Stat label="Revenue" value={usd(steward.data.total_revenue)} />
            <Stat label="API costs (est.)" value={usd(steward.data.estimated_costs)} />
            <Stat
              label="Spend this month"
              value={usd(steward.data.month_spend)}
              accent={steward.data.over_ceiling ? "text-rose-300" : undefined}
            />
            <Stat
              label="Est. profit"
              value={usd(steward.data.estimated_profit)}
              accent={steward.data.estimated_profit >= 0 ? "text-emerald-300" : "text-rose-300"}
            />
          </div>
          {steward.data.legacy_estimated_costs > 0 && (
            <p className="text-xs text-slate-500">
              Includes {usd(steward.data.legacy_estimated_costs)} estimated for{" "}
              {steward.data.legacy_products} product
              {steward.data.legacy_products === 1 ? "" : "s"} made before per-call metering;
              every run from now on is metered exactly.
            </p>
          )}
          {steward.data.over_ceiling && (
            <p className="text-xs text-rose-300">
              This month's API spend ({usd(steward.data.month_spend)}) has passed the{" "}
              {usd(steward.data.monthly_ceiling)} moderation ceiling — worth a look before the
              next big run.
            </p>
          )}
        </>
      )}

      {products.isLoading && <p className="text-sm text-slate-500">Loading products...</p>}
      {products.isError && (
        <ErrorNote>
          Could not load products: {(products.error as Error).message}. Is the API running on port
          8765?
        </ErrorNote>
      )}
      {products.data?.length === 0 && (
        <Card>
          <CardContent className="pt-5 text-sm text-slate-400">
            No products yet. Head to the Pipeline tab and give the team its first theme.
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-4">
        {products.data?.map((p) => (
          <ProductCard key={p.id} product={p} onOpen={() => setOpenId(p.id)} />
        ))}
      </div>

      {open && <ProductDrawer product={open} onClose={() => setOpenId(null)} />}
    </div>
  );
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/70 px-4 py-3">
      <div className="text-[11px] uppercase tracking-widest text-slate-500">{label}</div>
      <div className={`mt-1 font-mono text-lg ${accent ?? "text-slate-100"}`}>{value}</div>
    </div>
  );
}
