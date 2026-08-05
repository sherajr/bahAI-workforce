import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2 } from "lucide-react";
import { api, imageUrl } from "../lib/api";
import type {
  Job, ProductRow, RegenerateCardImageResult, RegenerateCardQuoteResult, CardLanguage, CardCopy,
} from "../lib/types";
import { Button, Card, CardContent, CardHeader, CardTitle, ErrorNote } from "./ui";
import { ConsultationPause } from "./ConsultationPause";
import { ConsultationTranscript } from "./ConsultationTranscript";

const LANGUAGE_NAMES: Record<string, string> = {
  en: "English",
  es: "Spanish",
  fa: "Persian",
  ar: "Arabic",
  fr: "French",
  de: "German",
  pt: "Portuguese",
  ru: "Russian",
  zh: "Chinese",
};

function useLanguageNameLookup() {
  const { data } = useQuery<CardLanguage[]>({
    queryKey: ["card-languages"],
    queryFn: api.getCardLanguages,
    staleTime: 24 * 60 * 60 * 1000,
  });

  return (code: string) => {
    const found = data?.find((l) => l.code === code);
    if (found) return found.name;
    return LANGUAGE_NAMES[code] || code.toUpperCase();
  };
}

// Landscape (3.5" × 2") card faces — the bookmark preview's portrait layout
// would letterbox these into stamps, so cards get their own treatment.

function Face({
  label,
  src,
  downloadName,
  onFail,
}: {
  label: string;
  src: string;
  downloadName: string;
  onFail?: () => void;
}) {
  const [failed, setFailed] = useState(false);
  return (
    <div className="flex w-full max-w-md flex-col items-center gap-2">
      <span className="text-xs uppercase tracking-widest text-slate-500">{label}</span>
      {src && !failed ? (
        <a href={src} download={downloadName} title={`Download ${label}`}>
          <img
            src={src}
            alt={`Card ${label}`}
            onError={() => {
              setFailed(true);
              if (onFail) onFail();
            }}
            className="w-full rounded-lg border border-slate-800 object-contain shadow-lg"
          />
        </a>
      ) : (
        <div className="flex aspect-[7/4] w-full items-center justify-center rounded-lg border border-dashed border-slate-700 text-xs text-slate-600">
          Not rendered
        </div>
      )}
    </div>
  );
}

function VariantRow({
  code,
  frontPath,
  backPath,
  langName,
}: {
  code: string;
  frontPath?: string | null;
  backPath?: string | null;
  langName: string;
}) {
  const [frontFailed, setFrontFailed] = useState(false);
  const [backFailed, setBackFailed] = useState(false);

  if ((frontFailed || !frontPath) && (backFailed || !backPath)) return null;

  return (
    <div className="border-t border-slate-800 pt-6">
      <h3 className="mb-4 text-center text-sm font-semibold text-slate-300">
        {langName} card ({code.toUpperCase()})
      </h3>
      <div className="flex flex-wrap items-start justify-center gap-6">
        {!frontFailed && frontPath && (
          <Face
            label={`${langName} Front`}
            src={imageUrl(frontPath)}
            downloadName={`card-front-${code}.png`}
            onFail={() => setFrontFailed(true)}
          />
        )}
        {!backFailed && backPath && (
          <Face
            label={`${langName} Back`}
            src={imageUrl(backPath)}
            downloadName={`card-back-${code}.png`}
            onFail={() => setBackFailed(true)}
          />
        )}
      </div>
    </div>
  );
}

export function QuoteCardPreview({
  frontPath,
  backPath,
  variantFaces: propVariantFaces,
}: {
  frontPath?: string | null;
  backPath?: string | null;
  variantFaces?: Record<string, { front: string; back: string }> | null;
}) {
  const queryClient = useQueryClient();
  const products = queryClient.getQueryData<ProductRow[]>(["products"]);
  const getLangName = useLanguageNameLookup();

  const getFilename = (p: string | null | undefined) => {
    if (!p) return "";
    return p.replace(/\\/g, "/").split("/").pop() ?? "";
  };

  const matchedProduct = products?.find((p) => {
    const f1 = getFilename(p.front_image);
    const f2 = getFilename(frontPath);
    const b1 = getFilename(p.back_image);
    const b2 = getFilename(backPath);
    return (f1 && f1 === f2) || (b1 && b1 === b2);
  });

  const cardCopy = matchedProduct?.listing_copy ? (JSON.parse(matchedProduct.listing_copy) as CardCopy) : null;
  const variantFaces = propVariantFaces || cardCopy?.variant_faces;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Quote card preview — 3.5″ × 2″ (click a face to download)</CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="flex flex-wrap items-start justify-center gap-6">
          <Face label="Front (quote)" src={imageUrl(frontPath)} downloadName="card-front.png" />
          <Face label="Back (art)" src={imageUrl(backPath)} downloadName="card-back.png" />
        </div>

        {variantFaces && Object.entries(variantFaces).map(([code, pair]) => {
          const langName = getLangName(code);
          return (
            <VariantRow
              key={code}
              code={code}
              frontPath={pair.front}
              backPath={pair.back}
              langName={langName}
            />
          );
        })}
      </CardContent>
    </Card>
  );
}

// The card's text content: quote, citation, translation + its disclaimers.
export function QuoteCardDetail({
  quote,
  citation,
  quoteGrounded,
  quoteProvenance,
  languageName,
  translationText,
  disclaimerNative,
  disclaimerEn,
  artworkDisclosure,
}: {
  quote: string;
  citation?: string | null;
  quoteGrounded?: boolean;
  // "ruhi_book1" | "lib:<slug>" | "web:<url>" (rule 11 update 2026-08-04;
  // absent on older cards = Ruhi Book 1).
  quoteProvenance?: string | null;
  languageName?: string | null;
  translationText?: string | null;
  disclaimerNative?: string | null;
  disclaimerEn?: string | null;
  artworkDisclosure?: string | null;
}) {
  const queryClient = useQueryClient();
  const products = queryClient.getQueryData<ProductRow[]>(["products"]);
  const getLangName = useLanguageNameLookup();

  const matchedProduct = products?.find((p) => {
    try {
      if (p.product_type !== "quote_card" || !p.listing_copy) return false;
      const copy = JSON.parse(p.listing_copy) as CardCopy;
      return copy.quote === quote;
    } catch {
      return false;
    }
  });

  const cardCopy = matchedProduct?.listing_copy ? (JSON.parse(matchedProduct.listing_copy) as CardCopy) : null;
  const variantFaces = cardCopy?.variant_faces;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Card text</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div>
          <div className="mb-1 flex items-center gap-2">
            <span className="text-xs uppercase tracking-widest text-slate-500">Quote (English)</span>
            <span
              className={`rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wide ${
                quoteGrounded
                  ? "border-emerald-400/40 bg-emerald-400/10 text-emerald-300"
                  : "border-orange-400/40 bg-orange-400/10 text-orange-300"
              }`}
            >
              {quoteGrounded
                ? "Librarian-verified"
                : quoteProvenance?.startsWith("web:")
                  ? "Web quote — wording not verified"
                  : "Not source-verified"}
            </span>
          </div>
          {quoteProvenance?.startsWith("web:") && (
            <p className="mb-1 text-xs text-amber-300/80">
              Sheraj chose this quote from a web page ({quoteProvenance.slice(4).replace(/^https?:\/\/(www\.)?/, "").split("/")[0]}) —
              it prints exactly as fetched and was never checked against a verified text.
            </p>
          )}
          <p className="whitespace-pre-line text-sm italic leading-relaxed text-slate-100">{quote}</p>
          {citation && <p className="mt-1 text-xs text-slate-400">{citation}</p>}
        </div>

        {translationText && (
          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-1 flex items-center gap-2">
              <span className="text-xs uppercase tracking-widest text-slate-500">
                {languageName ?? "Translation"}
              </span>
              <span className="rounded-full border border-sky-400/40 bg-sky-400/10 px-2 py-0.5 text-[10px] uppercase tracking-wide text-sky-300">
                AI-assisted translation
              </span>
            </div>
            <p className="whitespace-pre-line text-sm leading-relaxed text-slate-100">{translationText}</p>
            {disclaimerNative && (
              <p className="mt-2 text-xs text-slate-500">Printed on the card: {disclaimerNative}</p>
            )}
            {disclaimerEn && <p className="mt-1 text-xs text-slate-500">{disclaimerEn}</p>}
          </div>
        )}

        {artworkDisclosure && (
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full border border-sky-400/40 bg-sky-400/10 px-2 py-0.5 text-[10px] uppercase tracking-wide text-sky-300">
              AI-generated artwork
            </span>
            <p className="text-xs text-slate-500">{artworkDisclosure}</p>
          </div>
        )}

        {variantFaces && Object.entries(variantFaces).length > 0 && (
          <div className="border-t border-slate-800 pt-4 space-y-4">
            <span className="text-xs uppercase tracking-widest text-slate-500 block font-semibold">
              Per-language card pairs
            </span>
            {Object.entries(variantFaces).map(([code, pair]) => {
              const langName = getLangName(code);
              return (
                <VariantRow
                  key={code}
                  code={code}
                  frontPath={pair.front}
                  backPath={pair.back}
                  langName={langName}
                />
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** Redirect what the team produces for a quote card, BEFORE the next review
 * pass: a different (Ruhi Book 1) quote, different artwork, or a full redo —
 * the card equivalent of ProductsGallery's RedirectCard for bookmarks. */
export function CardRedirectCard({ product }: { product: ProductRow }) {
  const queryClient = useQueryClient();
  const [guidance, setGuidance] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);

  const quote = useMutation<RegenerateCardQuoteResult, Error, void>({
    mutationFn: () => api.regenerateCardQuote(product.id, guidance),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["products"] }),
  });

  const image = useMutation<RegenerateCardImageResult, Error, void>({
    mutationFn: () => api.regenerateCardImage(product.id, guidance),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["products"] }),
  });

  const redoAll = useMutation<{ job_id: string }, Error, void>({
    mutationFn: () => api.regenerateCardAll(product.id, guidance),
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
          Change direction BEFORE the next review — a different quote (always a verbatim Ruhi Book 1
          passage), different artwork, or start the whole card over.
        </p>
        <textarea
          value={guidance}
          onChange={(e) => setGuidance(e.target.value)}
          placeholder='e.g. "something about detachment instead", "more vibrant colors, remove the lotus"'
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
            New quote ({quote.data.citation}): "{quote.data.new_quote.slice(0, 80)}
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
