import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, X } from "lucide-react";
import { api, frontImageUrl, imageUrl } from "../lib/api";
import type { EtsyPublishResult, ImproveResult, ProductRow } from "../lib/types";
import {
  badgeClasses, badgeFor, formatDate, parseListing, parseReview, usd,
} from "../lib/utils";
import { ListingDetail } from "./ListingDetail";
import { ScoreCard } from "./ScoreCard";
import { BadgePill, Button, Card, CardContent, ErrorNote } from "./ui";

// ── Card ──────────────────────────────────────────────────────────────────────

function ProductCard({ product, onOpen }: { product: ProductRow; onOpen: () => void }) {
  const review = parseReview(product);
  const overall = review?.overall ?? 0;
  const [src, setSrc] = useState(frontImageUrl(product.image_url));
  const fallback = imageUrl(product.image_url);

  return (
    <button
      onClick={onOpen}
      className="group flex flex-col overflow-hidden rounded-xl border border-slate-800 bg-slate-900/70 text-left transition-colors hover:border-amber-400/40"
    >
      <div className="flex h-56 items-center justify-center overflow-hidden bg-slate-950">
        {src ? (
          <img
            src={src}
            alt={product.title ?? "Bookmark"}
            onError={() => setSrc(src === fallback ? "" : fallback)}
            className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.03]"
          />
        ) : (
          <span className="text-xs text-slate-600">No image</span>
        )}
      </div>
      <div className="flex flex-1 flex-col gap-2 p-4">
        <div className="line-clamp-2 text-sm font-medium text-slate-100">
          {product.title ?? product.theme ?? product.id}
        </div>
        <div className="mt-auto flex items-center justify-between">
          <BadgePill className={badgeClasses(badgeFor(overall))}>{badgeFor(overall)}</BadgePill>
          <span className="font-mono text-xs text-slate-400">{overall.toFixed(1)}/10</span>
        </div>
        <div className="text-xs text-slate-600">{formatDate(product.created_at)}</div>
      </div>
    </button>
  );
}

// ── Detail drawer ─────────────────────────────────────────────────────────────

function ProductDrawer({ product, onClose }: { product: ProductRow; onClose: () => void }) {
  const queryClient = useQueryClient();
  const listing = parseListing(product);
  const review = parseReview(product);
  const [notes, setNotes] = useState("");
  const [revenue, setRevenue] = useState("");

  const etsyStatus = useQuery({ queryKey: ["etsy-status"], queryFn: api.getEtsyStatus });

  const improve = useMutation<ImproveResult, Error, void>({
    mutationFn: () => api.improveProduct(product.id, notes),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["products"] }),
  });

  const publish = useMutation<EtsyPublishResult, Error, void>({
    mutationFn: () => api.publishToEtsy(product.id),
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
          <DrawerImage label="Front" src={frontImageUrl(product.image_url)} />
          <DrawerImage label="Artwork" src={imageUrl(product.image_url)} />
        </div>

        <div className="space-y-5">
          {review && <ScoreCard review={review} />}
          {listing && <ListingDetail listing={listing} />}

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
                    ? `Improved ${improve.data.old_score.toFixed(1)} → ${improve.data.new_score.toFixed(1)} in ${improve.data.attempts} attempt${improve.data.attempts > 1 ? "s" : ""}.`
                    : `No improvement found (still ${improve.data.old_score.toFixed(1)}). The previous version was kept.`}
                </p>
              )}
              {improve.isError && <ErrorNote>{improve.error.message}</ErrorNote>}
            </CardContent>
          </Card>

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
        </div>
      </div>
    </div>
  );
}

function DrawerImage({ label, src }: { label: string; src: string }) {
  const [failed, setFailed] = useState(false);
  if (!src || failed) return null;
  return (
    <div className="flex flex-col items-center gap-1.5">
      <span className="text-[11px] uppercase tracking-widest text-slate-500">{label}</span>
      <img
        src={src}
        alt={label}
        onError={() => setFailed(true)}
        className="max-h-72 rounded-lg border border-slate-800 object-contain"
      />
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
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat label="Products" value={String(steward.data.total_products)} />
          <Stat label="Revenue" value={usd(steward.data.total_revenue)} />
          <Stat label="Est. API costs" value={usd(steward.data.estimated_costs)} />
          <Stat
            label="Est. profit"
            value={usd(steward.data.estimated_profit)}
            accent={steward.data.estimated_profit >= 0 ? "text-emerald-300" : "text-rose-300"}
          />
        </div>
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
            No bookmarks yet. Head to the Pipeline tab and give the team its first theme.
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
