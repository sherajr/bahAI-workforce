import { useState } from "react";
import { imageUrl } from "../lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "./ui";

// Landscape (3.5" × 2") card faces — the bookmark preview's portrait layout
// would letterbox these into stamps, so cards get their own treatment.

function Face({ label, src, downloadName }: { label: string; src: string; downloadName: string }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className="flex w-full max-w-md flex-col items-center gap-2">
      <span className="text-xs uppercase tracking-widest text-slate-500">{label}</span>
      {src && !failed ? (
        <a href={src} download={downloadName} title={`Download ${label}`}>
          <img
            src={src}
            alt={`Card ${label}`}
            onError={() => setFailed(true)}
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

export function QuoteCardPreview({
  frontPath,
  backPath,
}: {
  frontPath?: string | null;
  backPath?: string | null;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Quote card preview — 3.5″ × 2″ (click a face to download)</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex flex-wrap items-start justify-center gap-6">
          <Face label="Front (quote)" src={imageUrl(frontPath)} downloadName="card-front.png" />
          <Face label="Back (art)" src={imageUrl(backPath)} downloadName="card-back.png" />
        </div>
      </CardContent>
    </Card>
  );
}

// The card's text content: quote, citation, translation + its disclaimers.
export function QuoteCardDetail({
  quote,
  citation,
  quoteGrounded,
  languageName,
  translationText,
  disclaimerNative,
  disclaimerEn,
  artworkDisclosure,
}: {
  quote: string;
  citation?: string | null;
  quoteGrounded?: boolean;
  languageName?: string | null;
  translationText?: string | null;
  disclaimerNative?: string | null;
  disclaimerEn?: string | null;
  artworkDisclosure?: string | null;
}) {
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
              {quoteGrounded ? "Librarian-verified" : "Not source-verified"}
            </span>
          </div>
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
      </CardContent>
    </Card>
  );
}
