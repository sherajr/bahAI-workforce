import type { Listing } from "../lib/types";
import { Card, CardContent, CardHeader, CardTitle } from "./ui";

export function ListingDetail({ listing }: { listing: Listing }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Etsy listing</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <div>
          <div className="text-xs uppercase tracking-widest text-slate-500">Title</div>
          <div className="mt-1 text-slate-100">{listing.title}</div>
        </div>
        {listing.bookmark_quote && (
          <div className="space-y-1.5">
            <blockquote className="border-l-2 border-amber-400/60 pl-4 font-display text-base italic leading-relaxed text-amber-100/90">
              {listing.bookmark_quote}
            </blockquote>
            {listing.quote_verified === false && (
              <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
                Quote no longer Librarian-verified — it was edited by hand, so it hasn't been
                checked against the source texts. The pipeline's grounding guarantee doesn't
                cover this wording.
              </div>
            )}
          </div>
        )}
        <div>
          <div className="text-xs uppercase tracking-widest text-slate-500">Description</div>
          <p className="mt-1 whitespace-pre-wrap leading-relaxed text-slate-300">
            {listing.description}
          </p>
        </div>
        {listing.tags?.length > 0 && (
          <div>
            <div className="text-xs uppercase tracking-widest text-slate-500">Tags</div>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {listing.tags.map((t) => (
                <span
                  key={t}
                  className="rounded-full bg-slate-800 px-2.5 py-0.5 text-xs text-slate-300"
                >
                  {t}
                </span>
              ))}
            </div>
          </div>
        )}
        <div className="flex flex-wrap gap-8">
          {listing.materials?.length > 0 && (
            <div>
              <div className="text-xs uppercase tracking-widest text-slate-500">Materials</div>
              <div className="mt-1 text-slate-300">{listing.materials.join(", ")}</div>
            </div>
          )}
          {listing.price_note && (
            <div>
              <div className="text-xs uppercase tracking-widest text-slate-500">
                Price note (Scribe's suggestion)
              </div>
              <div className="mt-1 text-slate-300">{listing.price_note}</div>
              <div className="mt-0.5 text-xs text-slate-600">
                Display only — the actual Etsy price comes from your pricing policy, never from
                this text.
              </div>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
