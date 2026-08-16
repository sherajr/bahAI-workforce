import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2, Clapperboard, Download, ExternalLink, FileText, Loader2, Printer, Search, X,
} from "lucide-react";
import { api, BASE, frontImageUrl, imageUrl } from "../lib/api";
import type { Tab } from "./Nav";
import type {
  EditProductPayload, EditProductResult, EtsyPublishResult, FinishedVideo, ImproveResult, Job,
  ProductRow, RegenerateImageResult, RegenerateQuoteResult,
} from "../lib/types";
import { getProductsUi, patchProductsUi, patchVideoUi } from "../lib/settings";
import {
  badgeClasses, badgeForProduct, formatDate, isQuoteCard, parseCardCopy, parseListing,
  parseReview, usd,
} from "../lib/utils";
import { ConsultationPause } from "./ConsultationPause";
import { ConsultationTranscript } from "./ConsultationTranscript";
import { LayoutEditor } from "./LayoutEditor";
import { ListingDetail } from "./ListingDetail";
import { CardRedirectCard, QuoteCardDetail } from "./QuoteCardPreview";
import { ScoreCard } from "./ScoreCard";
import { BadgePill, Button, Card, CardContent, ErrorNote } from "./ui";

// Friendly labels for per-language card pairs in the drawer (falls back to
// the uppercase code for any language not listed here).
const VARIANT_LANG_NAMES: Record<string, string> = {
  es: "Spanish",
  zh: "Chinese",
  ar: "Arabic",
};

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

function ProductCard({
  product,
  onOpen,
  selected,
  onToggleSelect,
}: {
  product: ProductRow;
  onOpen: () => void;
  selected: boolean;
  onToggleSelect: () => void;
}) {
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
      <div className={`flex h-56 gap-px overflow-hidden bg-slate-950 ${quoteCard ? "flex-col" : ""} relative`}>
        <div
          className="absolute top-2.5 left-2.5 z-10 flex h-6 w-6 items-center justify-center rounded bg-slate-900/90 border border-slate-800"
          onClick={(e) => e.stopPropagation()}
        >
          <input
            type="checkbox"
            checked={selected}
            onChange={onToggleSelect}
            className="h-4 w-4 cursor-pointer accent-amber-400"
          />
        </div>
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

// ── Finished videos ───────────────────────────────────────────────────────────
//
// A finished video sits on this shelf beside the bookmarks and cards, but it is
// NOT a product row — the backend derives it from the video project every time
// it's read (GET /video/finished). So it can't drift out of step with the Video
// tab, and it never lands in the Steward's product/revenue counts, which are
// about things made to be sold or given away in print.

/** Seconds → "1:04", prefixed "~" when it's the plan's length rather than a
 * measurement of the finished file. */
function formatDuration(seconds: number, measured = true): string {
  const s = Math.max(0, Math.round(seconds));
  return `${measured ? "" : "~"}${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** The mp4/JSON/SRT live on the API server, not the Vite dev origin. */
function outputUrl(webPath: string): string {
  return webPath ? `${BASE}${webPath}` : "";
}

function VideoCard({ video, onOpen }: { video: FinishedVideo; onOpen: () => void }) {
  const poster = outputUrl(video.poster_url);
  const [posterFailed, setPosterFailed] = useState(false);

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(); } }}
      className="group flex cursor-pointer flex-col overflow-hidden rounded-xl border border-slate-800 bg-slate-900/70 text-left transition-colors hover:border-amber-400/40"
    >
      <div className="relative flex h-56 items-center justify-center overflow-hidden bg-slate-950">
        {poster && !posterFailed ? (
          <img
            src={poster}
            alt="First frame"
            onError={() => setPosterFailed(true)}
            className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.03]"
          />
        ) : (
          <Clapperboard className="h-10 w-10 text-slate-700" />
        )}
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="flex h-12 w-12 items-center justify-center rounded-full border border-slate-200/40 bg-slate-950/60 backdrop-blur transition-colors group-hover:border-amber-300/70">
            {/* CSS triangle: needs a block box with no size of its own. */}
            <span className="ml-1 block h-0 w-0 border-y-[9px] border-l-[14px] border-y-transparent border-l-slate-100" />
          </span>
        </div>
        <span className="absolute bottom-1.5 right-1.5 rounded bg-slate-950/85 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
          {formatDuration(video.duration_seconds, video.duration_measured)}
        </span>
      </div>
      <div className="flex flex-1 flex-col gap-2 p-4">
        <div className="text-[10px] uppercase tracking-widest text-violet-300">
          Video · {video.clip_count} shot{video.clip_count === 1 ? "" : "s"}
        </div>
        <div className="line-clamp-2 text-sm font-medium text-slate-100">{video.title}</div>
        <div className="mt-auto flex items-center justify-between">
          {video.is_mock ? (
            <BadgePill className="bg-rose-400/10 text-rose-300 border-rose-400/40">
              MOCK — NOT REAL FOOTAGE
            </BadgePill>
          ) : (
            <BadgePill className="bg-violet-400/10 text-violet-300 border-violet-400/40">
              FINISHED
            </BadgePill>
          )}
        </div>
        <div className="text-xs text-slate-600">{formatDate(video.created_at)}</div>
      </div>
    </div>
  );
}

function VideoDrawer({
  video, onClose, onNavigate,
}: { video: FinishedVideo; onClose: () => void; onNavigate?: (tab: Tab) => void }) {
  const src = outputUrl(video.video_url);
  const filename = `${video.title.replace(/[^\w\- ]+/g, "").trim().slice(0, 60) || video.id}.mp4`;

  // Send the Video tab to this project's Review & export step — the same
  // persisted state the tab restores itself from (rule 33c).
  const openInVideoTab = () => {
    patchVideoUi({ projectId: video.id, tab: "review", jobId: null });
    onNavigate?.("video");
  };

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-slate-950/70" onClick={onClose}>
      <div
        className="h-full w-full max-w-2xl overflow-y-auto border-l border-slate-800 bg-slate-950 p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg text-slate-100">{video.title}</h2>
            <div className="mt-1 font-mono text-xs text-slate-500">
              video project {video.id} · {formatDate(video.created_at)}
            </div>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-800">
            <X className="h-5 w-5" />
          </button>
        </div>

        {video.is_mock && (
          <div className="mb-4 rounded-lg border border-rose-500/40 bg-rose-950/20 p-3 text-sm text-rose-200">
            This draft was built with the <strong>mock</strong> provider — placeholder clips, not
            real generation. Re-render it with a real provider before showing it to anyone.
          </div>
        )}

        <video
          src={src}
          poster={outputUrl(video.poster_url) || undefined}
          controls
          className="w-full rounded-xl border border-slate-800 bg-black"
        />

        <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat label="Length" value={formatDuration(video.duration_seconds, video.duration_measured)} />
          <Stat label="Shots joined" value={String(video.clip_count)} />
          <Stat label="Shots planned" value={String(video.shot_count)} />
          <Stat
            label="Source"
            value={
              video.source_kind === "bookmark" ? "Bookmark"
                : video.source_kind === "quote_card" ? "Quote card"
                : "Scene or story"
            }
          />
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <a
            href={src}
            download={filename}
            className="inline-flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 transition-colors hover:border-amber-400/50 hover:text-amber-200"
          >
            <Download className="h-4 w-4" /> Download the video
          </a>
          {video.metadata_url && (
            <a
              href={outputUrl(video.metadata_url)}
              download={`${video.id}-production-record.json`}
              className="inline-flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-300 transition-colors hover:border-slate-600"
            >
              <FileText className="h-4 w-4" /> Production record
            </a>
          )}
          {video.subtitles_url && (
            <a
              href={outputUrl(video.subtitles_url)}
              download={`${video.id}-narration.srt`}
              className="inline-flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-300 transition-colors hover:border-slate-600"
            >
              <FileText className="h-4 w-4" /> Narration subtitles
            </a>
          )}
          {onNavigate && (
            <Button variant="secondary" onClick={openInVideoTab}>
              <Clapperboard className="h-4 w-4" /> Open in the Video tab
            </Button>
          )}
        </div>

        <p className="mt-4 text-xs text-slate-600">
          Editing, re-rendering shots and re-assembling all happen in the Video tab — this shelf
          shows what's finished. Print sheets, Etsy and the giving ledger are for the printed
          bookmarks and cards, so they don't apply here.
        </p>
      </div>
    </div>
  );
}

// ── Detail drawer ─────────────────────────────────────────────────────────────

function ProductDrawer({
  product,
  onClose,
  selectedIds,
  onToggleSelect,
}: {
  product: ProductRow;
  onClose: () => void;
  selectedIds: string[];
  onToggleSelect: () => void;
}) {
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
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={selectedIds.includes(product.id)}
                onChange={onToggleSelect}
                className="h-4 w-4 cursor-pointer accent-amber-400"
              />
              <h2 className="text-lg text-slate-100">{product.title ?? product.theme}</h2>
            </div>
            <div className="mt-1 font-mono text-xs text-slate-500 pl-6">
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
          {/* Per-language card pairs (translated quote-card runs) — read
              straight from this product's own listing_copy, never a cache. */}
          {quoteCard && cardCopy?.variant_faces &&
            Object.entries(cardCopy.variant_faces).map(([code, pair]) => (
              <span key={code} className="contents">
                <DrawerImage
                  label={`${VARIANT_LANG_NAMES[code] ?? code.toUpperCase()} front`}
                  src={imageUrl(pair.front)}
                  downloadName={`${product.id}-${code}-front.png`}
                />
                <DrawerImage
                  label={`${VARIANT_LANG_NAMES[code] ?? code.toUpperCase()} back`}
                  src={imageUrl(pair.back)}
                  downloadName={`${product.id}-${code}-back.png`}
                />
              </span>
            ))}
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
              quoteProvenance={cardCopy.quote_provenance}
              languageName={cardCopy.language_name}
              translationText={cardCopy.translation_text}
              disclaimerNative={cardCopy.translation_disclaimer_native}
              disclaimerEn={cardCopy.translation_disclaimer_en}
              artworkDisclosure={cardCopy.artwork_disclosure}
            />
          )}
          {quoteCard && <CardRedirectCard product={product} />}
          {quoteCard && <FeedbackCard product={product} />}
          {product.front_image && <RecordDeedCard product={product} />}
          {listing && <ListingDetail listing={listing} />}

          {/* Visual layout editor — both product types. Presentation only;
              never edits the printed text. */}
          <LayoutEditor product={product} />

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
                    <p className="text-xs text-amber-400/80">
                      Editing the quote by hand marks it "no longer verified" (it won't be checked
                      against the source texts) and re-renders the printed face to match.
                    </p>
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

function RecordDeedCard({ product }: { product: ProductRow }) {
  const queryClient = useQueryClient();
  const [count, setCount] = useState<number>(1);
  const [kind, setKind] = useState<"gift" | "gathering" | "digital">("gift");
  const [note, setNote] = useState("");

  const recordDeed = useMutation({
    mutationFn: () =>
      api.recordDeed({
        kind,
        count,
        product_id: product.id,
        note: note.trim(),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["products"] });
      queryClient.invalidateQueries({ queryKey: ["steward"] });
      setCount(1);
      setNote("");
    },
  });

  return (
    <Card>
      <CardContent className="space-y-3 pt-4">
        <h3 className="text-sm font-semibold text-slate-100">Record a gift or share</h3>
        <p className="text-sm text-slate-400">
          Log when you hand out this physical print or share it digitally.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as any)}
            className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
          >
            <option value="gift">Gift (handed out)</option>
            <option value="gathering">Served a Gathering</option>
            <option value="digital">Shared Digitally</option>
          </select>
          <input
            type="number"
            min="1"
            value={count}
            onChange={(e) => setCount(Math.max(1, parseInt(e.target.value) || 1))}
            className="w-20 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
          />
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Optional note..."
            className="flex-1 min-w-[150px] rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 placeholder-slate-600"
          />
          <Button
            variant="secondary"
            loading={recordDeed.isPending}
            onClick={() => recordDeed.mutate()}
          >
            Record
          </Button>
        </div>
        {recordDeed.isError && <ErrorNote>{(recordDeed.error as Error).message}</ErrorNote>}
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

// ── Filtering ─────────────────────────────────────────────────────────────────

/** One thing on the shelf: a saved product, or a finished video derived from
 * its video project. Everything the filter bar needs is flattened onto it once,
 * so filtering and sorting never re-parse the stored JSON per keystroke. */
type GalleryItem =
  | {
      kind: "product"; id: string; type: "bookmark" | "quote_card"; title: string;
      createdAt: string; score: number; badge: string; search: string; product: ProductRow;
    }
  | {
      kind: "video"; id: string; type: "video"; title: string;
      createdAt: string; score: null; badge: null; search: string; video: FinishedVideo;
    };

const KIND_FILTERS: { id: string; label: string }[] = [
  { id: "all", label: "Everything" },
  { id: "bookmark", label: "Bookmarks" },
  { id: "quote_card", label: "Quote cards" },
  { id: "video", label: "Videos" },
];

const BADGE_FILTERS = ["EXCEPTIONAL", "APPROVED", "BORDERLINE", "REJECTED", "BEST EFFORT"];

const SORTS: { id: string; label: string }[] = [
  { id: "newest", label: "Newest first" },
  { id: "oldest", label: "Oldest first" },
  { id: "score", label: "Highest score" },
  { id: "score_low", label: "Lowest score" },
  { id: "title", label: "Title A–Z" },
];

function productItem(product: ProductRow): GalleryItem {
  const review = parseReview(product);
  const overall = review?.overall ?? 0;
  const card = parseCardCopy(product);
  const listing = isQuoteCard(product) ? null : parseListing(product);
  return {
    kind: "product",
    id: product.id,
    type: isQuoteCard(product) ? "quote_card" : "bookmark",
    title: product.title ?? product.theme ?? product.id,
    createdAt: product.created_at ?? "",
    score: overall,
    badge: String(badgeForProduct(product, overall)),
    // Searched as one lower-cased blob: title, theme, id, the printed quote and
    // its citation, and the listing's tags — the words Sheraj would actually
    // type to find a piece again.
    search: [
      product.title, product.theme, product.id,
      card?.quote, card?.citation, card?.language_name,
      listing?.bookmark_quote, listing?.description, (listing?.tags ?? []).join(" "),
      product.etsy_listing_id ? `etsy ${product.etsy_listing_id}` : "",
    ].filter(Boolean).join(" ").toLowerCase(),
    product,
  };
}

function videoItem(video: FinishedVideo): GalleryItem {
  return {
    kind: "video",
    id: video.id,
    type: "video",
    title: video.title,
    createdAt: video.created_at ?? "",
    score: null,
    badge: null,
    search: [video.title, video.id, "video", video.source_kind]
      .filter(Boolean).join(" ").toLowerCase(),
    video,
  };
}

function GalleryToolbar({
  search, onSearch, kind, onKind, badge, onBadge, sort, onSort,
  counts, showing, total, onClear,
}: {
  search: string; onSearch: (v: string) => void;
  kind: string; onKind: (v: string) => void;
  badge: string; onBadge: (v: string) => void;
  sort: string; onSort: (v: string) => void;
  counts: Record<string, number>; showing: number; total: number; onClear: () => void;
}) {
  const filtered = search.trim() !== "" || kind !== "all" || badge !== "all";
  const select =
    "rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200";

  return (
    <div className="space-y-3 rounded-xl border border-slate-800 bg-slate-900/40 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-[200px] flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
          <input
            value={search}
            onChange={(e) => onSearch(e.target.value)}
            placeholder="Search titles, themes, quotes, tags..."
            className="w-full rounded-lg border border-slate-700 bg-slate-950 py-2 pl-9 pr-3 text-sm text-slate-100 placeholder-slate-600"
          />
        </div>
        <select value={badge} onChange={(e) => onBadge(e.target.value)} className={select}>
          <option value="all">Any review result</option>
          {BADGE_FILTERS.map((b) => (
            <option key={b} value={b}>{b.charAt(0) + b.slice(1).toLowerCase()}</option>
          ))}
        </select>
        <select value={sort} onChange={(e) => onSort(e.target.value)} className={select}>
          {SORTS.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
        </select>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {KIND_FILTERS.map(({ id, label }) => (
          <button
            key={id}
            onClick={() => onKind(id)}
            className={
              "rounded-lg border px-3 py-1.5 text-xs transition-colors " +
              (kind === id
                ? "border-amber-400/40 bg-amber-400/10 text-amber-200"
                : "border-slate-700 bg-slate-800/40 text-slate-400 hover:text-slate-200")
            }
          >
            {label} <span className="font-mono text-[10px] text-slate-500">{counts[id] ?? 0}</span>
          </button>
        ))}
        <div className="ml-auto flex items-center gap-3">
          <span className="text-xs text-slate-500">
            Showing {showing} of {total}
          </span>
          {filtered && (
            <button onClick={onClear} className="text-xs text-amber-300 hover:underline">
              Clear filters
            </button>
          )}
        </div>
      </div>

      {/* A video has no review score, so a score filter can never honestly
          include one — say so rather than letting them quietly disappear. */}
      {badge !== "all" && kind !== "video" && counts.video > 0 && (
        <p className="text-xs text-slate-500">
          Videos are hidden while you filter by review result — they're reviewed shot by shot in
          the Video tab, not scored out of 10.
        </p>
      )}
    </div>
  );
}

// ── Gallery ───────────────────────────────────────────────────────────────────

export function ProductsGallery({ onNavigate }: { onNavigate?: (tab: Tab) => void } = {}) {
  const [open, setOpen] = useState<{ kind: "product" | "video"; id: string } | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [duplex, setDuplex] = useState(true);

  // The chosen view persists across tab switches; the typed search does not
  // (see settings.ts — coming back to an empty-looking shelf is the failure).
  const [search, setSearch] = useState("");
  const [kindFilter, setKindFilter] = useState(() => getProductsUi().kind);
  const [badgeFilter, setBadgeFilter] = useState(() => getProductsUi().badge);
  const [sort, setSort] = useState(() => getProductsUi().sort);

  const setKind = (v: string) => { setKindFilter(v); patchProductsUi({ kind: v }); };
  const setBadge = (v: string) => { setBadgeFilter(v); patchProductsUi({ badge: v }); };
  const setSortBy = (v: string) => { setSort(v); patchProductsUi({ sort: v }); };
  const clearFilters = () => { setSearch(""); setKind("all"); setBadge("all"); };

  const products = useQuery({
    queryKey: ["products"],
    queryFn: api.getProducts,
    refetchInterval: 30_000,
  });

  // Finished videos come from the video project store, not the products table.
  const videos = useQuery({
    queryKey: ["finished-videos"],
    queryFn: api.getFinishedVideos,
    refetchInterval: 30_000,
  });

  const steward = useQuery({
    queryKey: ["steward"],
    queryFn: api.getStewardReport,
    refetchInterval: 60_000,
  });

  const printGathering = useMutation<void, Error, void>({
    mutationFn: () => api.downloadGatheringSheet(selectedIds, duplex),
  });

  const handleToggleSelect = (id: string) => {
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );
  };

  const items = useMemo<GalleryItem[]>(() => [
    ...(products.data ?? []).map(productItem),
    ...(videos.data?.videos ?? []).map(videoItem),
  ], [products.data, videos.data]);

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: items.length, bookmark: 0, quote_card: 0, video: 0 };
    for (const item of items) c[item.type] += 1;
    return c;
  }, [items]);

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const filtered = items.filter((item) => {
      if (kindFilter !== "all" && item.type !== kindFilter) return false;
      // A video carries no review score, so it can never match a score filter.
      if (badgeFilter !== "all" && item.badge !== badgeFilter) return false;
      if (needle && !item.search.includes(needle)) return false;
      return true;
    });
    const byDate = (a: GalleryItem, b: GalleryItem) => a.createdAt.localeCompare(b.createdAt);
    const sorted = [...filtered];
    if (sort === "oldest") sorted.sort(byDate);
    else if (sort === "title") sorted.sort((a, b) => a.title.localeCompare(b.title));
    else if (sort === "score" || sort === "score_low") {
      // Unscored videos sort to the end either way — they aren't "a zero".
      sorted.sort((a, b) => {
        if (a.score === null && b.score === null) return -byDate(a, b);
        if (a.score === null) return 1;
        if (b.score === null) return -1;
        return sort === "score" ? b.score - a.score : a.score - b.score;
      });
    } else sorted.sort((a, b) => -byDate(a, b));
    return sorted;
  }, [items, search, kindFilter, badgeFilter, sort]);

  const openProduct = open?.kind === "product"
    ? products.data?.find((p) => p.id === open.id) ?? null : null;
  const openVideo = open?.kind === "video"
    ? videos.data?.videos.find((v) => v.id === open.id) ?? null : null;

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      {steward.data && (
        <>
          {/* DEEDS HEADLINE */}
          <div className="space-y-3 rounded-xl border border-slate-800 bg-slate-900/30 p-4">
            <div className="text-[11px] uppercase tracking-widest text-slate-400 font-semibold">Deeds for the Betterment of the World</div>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <Stat label="Cards Gifted" value={String(steward.data.deeds?.cards_gifted ?? 0)} accent="text-emerald-400 font-bold" />
              <Stat label="Gatherings Served" value={String(steward.data.deeds?.gatherings_served ?? 0)} accent="text-amber-400 font-bold" />
              <Stat label="Digital Shares" value={String(steward.data.deeds?.digital_shares ?? 0)} accent="text-cyan-400 font-bold" />
              <Stat label="Feedback Received" value={String(steward.data.deeds?.feedback_count ?? 0)} accent="text-indigo-400 font-bold" />
            </div>
            
            {steward.data.deeds?.recent && steward.data.deeds.recent.length > 0 && (
              <div className="mt-3 border-t border-slate-800/80 pt-3 text-xs">
                <div className="mb-2 font-semibold text-slate-400">Recent Deeds</div>
                <div className="space-y-1.5 max-h-32 overflow-y-auto pr-2">
                  {steward.data.deeds.recent.map((d) => (
                    <div key={d.id} className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800/30 pb-1 last:border-0 last:pb-0">
                      <div className="flex items-center gap-2">
                        <span className={`rounded px-1.5 py-0.5 text-[9px] font-mono uppercase font-semibold ${
                          d.kind === "gift" ? "bg-emerald-500/10 text-emerald-300 border border-emerald-500/20" :
                          d.kind === "gathering" ? "bg-amber-500/10 text-amber-300 border border-amber-500/20" :
                          "bg-cyan-500/10 text-cyan-300 border border-cyan-500/20"
                        }`}>
                          {d.kind}
                        </span>
                        <span className="font-medium text-slate-300">
                          {d.count}x {d.product_title || (d.product_id ? `product ${d.product_id}` : "deed")}
                        </span>
                        {d.note && <span className="text-slate-500 italic">({d.note})</span>}
                      </div>
                      <span className="text-[10px] font-mono text-slate-500">
                        {new Date(d.created_at).toLocaleDateString()}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* MONEY NUMBERS OR ERROR */}
          <div className="space-y-3 rounded-xl border border-slate-800 bg-slate-900/10 p-4">
            <div className="text-[11px] uppercase tracking-widest text-slate-500 font-semibold">Financial Ledger</div>
            {steward.data.error ? (
              <ErrorNote>Spend ledger could not be read: {steward.data.error}</ErrorNote>
            ) : (
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
                  <p className="text-xs text-slate-500 mt-2">
                    Includes {usd(steward.data.legacy_estimated_costs)} estimated for{" "}
                    {steward.data.legacy_products} product
                    {steward.data.legacy_products === 1 ? "" : "s"} made before per-call metering;
                    every run from now on is metered exactly.
                  </p>
                )}
                {steward.data.over_ceiling && (
                  <p className="text-xs text-rose-300 mt-2">
                    This month's API spend ({usd(steward.data.month_spend)}) has passed the{" "}
                    {usd(steward.data.monthly_ceiling)} moderation ceiling — worth a look before the
                    next big run.
                  </p>
                )}
              </>
            )}
          </div>
        </>
      )}

      {/* Gathering sheet print bar */}
      {selectedIds.length >= 2 && (
        <Card className="border-amber-500/30 bg-amber-950/10 p-4">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-3">
              <span className="text-sm font-semibold text-amber-200">
                {selectedIds.length} products selected
              </span>
              <button
                onClick={() => setSelectedIds([])}
                className="text-xs text-slate-400 hover:text-slate-200"
              >
                Clear selection
              </button>
            </div>
            <div className="flex flex-wrap items-center gap-4">
              <label className="flex items-center gap-2 text-xs text-slate-300 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={duplex}
                  onChange={(e) => setDuplex(e.target.checked)}
                  className="h-4 w-4 cursor-pointer accent-amber-400"
                />
                Double-sided printer alignment
              </label>
              <Button
                loading={printGathering.isPending}
                onClick={() => printGathering.mutate()}
                variant="primary"
              >
                <Printer className="h-4 w-4" />
                {printGathering.isPending ? "Generating PDF..." : "Print gathering sheet"}
              </Button>
            </div>
          </div>
          {printGathering.isError && (
            <div className="mt-3">
              <ErrorNote>{printGathering.error.message}</ErrorNote>
            </div>
          )}
        </Card>
      )}

      {products.isLoading && <p className="text-sm text-slate-500">Loading products...</p>}
      {products.isError && (
        <ErrorNote>
          Could not load products: {(products.error as Error).message}. Is the API running on port
          8765?
        </ErrorNote>
      )}
      {/* The videos live behind their own endpoint — if it fails, say so instead
          of quietly showing a shelf that's missing them. */}
      {videos.isError && (
        <ErrorNote>
          Could not load finished videos: {(videos.error as Error).message}
        </ErrorNote>
      )}

      {items.length > 0 && (
        <GalleryToolbar
          search={search} onSearch={setSearch}
          kind={kindFilter} onKind={setKind}
          badge={badgeFilter} onBadge={setBadge}
          sort={sort} onSort={setSortBy}
          counts={counts} showing={visible.length} total={items.length}
          onClear={clearFilters}
        />
      )}

      {!products.isLoading && items.length === 0 && (
        <Card>
          <CardContent className="pt-5 text-sm text-slate-400">
            No products yet. Head to the Pipeline tab and give the team its first theme.
          </CardContent>
        </Card>
      )}
      {items.length > 0 && visible.length === 0 && (
        <Card>
          <CardContent className="flex flex-wrap items-center gap-3 pt-5 text-sm text-slate-400">
            Nothing matches these filters.
            <button onClick={clearFilters} className="text-amber-300 hover:underline">
              Clear them
            </button>
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-4">
        {visible.map((item) =>
          item.kind === "product" ? (
            <ProductCard
              key={item.id}
              product={item.product}
              onOpen={() => setOpen({ kind: "product", id: item.id })}
              selected={selectedIds.includes(item.id)}
              onToggleSelect={() => handleToggleSelect(item.id)}
            />
          ) : (
            <VideoCard
              key={`v-${item.id}`}
              video={item.video}
              onOpen={() => setOpen({ kind: "video", id: item.id })}
            />
          )
        )}
      </div>

      {openProduct && (
        <ProductDrawer
          product={openProduct}
          onClose={() => setOpen(null)}
          selectedIds={selectedIds}
          onToggleSelect={() => handleToggleSelect(openProduct.id)}
        />
      )}
      {openVideo && (
        <VideoDrawer video={openVideo} onClose={() => setOpen(null)} onNavigate={onNavigate} />
      )}
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
