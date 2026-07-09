import { useEffect, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RotateCcw } from "lucide-react";
import { api, imageUrl } from "../lib/api";
import type { LayoutOptions, ProductLayout, ProductRow } from "../lib/types";
import { isQuoteCard } from "../lib/utils";
import { Button, Card, CardContent, ErrorNote } from "./ui";

/**
 * Visual layout editor for a saved product's front/back faces. Adjusts only how
 * a face LOOKS — font, text size/position, colour, shading, the star/line
 * toggles — and re-renders live. It never sends any text: the printed quote,
 * citation, translation, and disclaimers all come from the product's stored
 * data on the server, so nothing here can rewrite them (the quote stays locked;
 * a card's translation keeps its verified script font). "Save layout" only
 * swaps the rendered images — the review score is untouched.
 */
export function LayoutEditor({ product }: { product: ProductRow }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const card = isQuoteCard(product);

  const opts = useQuery<LayoutOptions>({
    queryKey: ["layout", product.id],
    queryFn: () => api.getLayout(product.id),
    enabled: open,
  });

  const [lay, setLay] = useState<ProductLayout | null>(null);
  const [preview, setPreview] = useState<{ front: string; back: string } | null>(null);

  useEffect(() => {
    if (opts.data && !lay) setLay(opts.data.current);
  }, [opts.data, lay]);

  const previewMut = useMutation({
    mutationFn: (l: ProductLayout) => api.previewLayout(product.id, l),
    onSuccess: (res) => {
      // Preview reuses one file per product (server overwrites it), so bust the
      // browser cache to actually show the new render.
      const bust = `?t=${Date.now()}`;
      setPreview({ front: imageUrl(res.front_image_web) + bust, back: imageUrl(res.back_image_web) + bust });
    },
  });

  // Debounced live preview as the controls move.
  const layKey = lay ? JSON.stringify(lay) : "";
  useEffect(() => {
    if (!lay) return;
    const t = setTimeout(() => previewMut.mutate(lay), 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layKey]);

  const save = useMutation({
    mutationFn: () => api.saveLayout(product.id, lay as ProductLayout),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["products"] }),
  });

  function set<K extends keyof ProductLayout>(k: K, v: ProductLayout[K]) {
    setLay((cur) => (cur ? { ...cur, [k]: v } : cur));
  }
  function reset() {
    if (opts.data) setLay({ ...opts.data.defaults });
  }

  const ranges = opts.data?.ranges ?? {};
  // Fall back to the product's saved faces until the first live preview lands.
  const frontSrc = preview?.front || imageUrl(product.front_image);
  const backSrc = preview?.back || imageUrl(product.back_image);

  return (
    <Card>
      <CardContent className="space-y-3 pt-4">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-slate-100">Adjust layout &amp; fonts</h3>
            <p className="text-xs text-slate-500">
              Change how it looks — the words stay exactly as the team wrote them.
            </p>
          </div>
          {!open ? (
            <Button variant="secondary" onClick={() => setOpen(true)}>
              Adjust
            </Button>
          ) : (
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Close
            </Button>
          )}
        </div>

        {open && opts.isLoading && (
          <p className="flex items-center gap-2 text-sm text-slate-500">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading controls…
          </p>
        )}
        {open && opts.isError && <ErrorNote>{(opts.error as Error).message}</ErrorNote>}

        {open && lay && opts.data && (
          <div className="space-y-4">
            {/* Live preview */}
            <div
              className={`relative flex justify-center gap-3 rounded-lg border border-slate-800 bg-slate-950 p-3 ${
                card ? "flex-col items-center" : ""
              }`}
            >
              {previewMut.isPending && (
                <div className="absolute right-2 top-2 flex items-center gap-1 text-[11px] text-amber-300">
                  <Loader2 className="h-3 w-3 animate-spin" /> rendering…
                </div>
              )}
              {frontSrc && (
                <img
                  src={frontSrc}
                  alt="Front preview"
                  className={card ? "max-h-40 rounded object-contain" : "max-h-72 rounded object-contain"}
                />
              )}
              {backSrc && (
                <img
                  src={backSrc}
                  alt="Back preview"
                  className={card ? "max-h-40 rounded object-contain" : "max-h-72 rounded object-contain"}
                />
              )}
            </div>

            {/* Controls */}
            <div className="grid grid-cols-2 gap-3">
              <SelectField
                label="Font"
                value={lay.font}
                onChange={(v) => set("font", v)}
                options={opts.data.fonts}
              />
              <SelectField
                label="Text colour"
                value={lay.text_color}
                onChange={(v) => set("text_color", v)}
                options={opts.data.colors}
              />
            </div>

            <SliderField
              label="Text size"
              value={lay.text_scale}
              range={ranges.text_scale}
              onChange={(v) => set("text_scale", v)}
              format={(v) => `${Math.round(v * 100)}%`}
            />

            {!card && (
              <>
                <SliderField
                  label="Text position"
                  value={lay.text_offset ?? 0}
                  range={ranges.text_offset}
                  onChange={(v) => set("text_offset", v)}
                  format={(v) => (v === 0 ? "center" : v < 0 ? "higher" : "lower")}
                />
                <SliderField
                  label="Shading behind text"
                  value={lay.gradient ?? 1}
                  range={ranges.gradient}
                  onChange={(v) => set("gradient", v)}
                  format={(v) => `${Math.round(v * 100)}%`}
                />
                <div className="flex gap-5">
                  <Toggle label="Gold star" checked={lay.show_star ?? true} onChange={(v) => set("show_star", v)} />
                  <Toggle label="Gold line" checked={lay.show_rule ?? true} onChange={(v) => set("show_rule", v)} />
                </div>
              </>
            )}

            {card && (
              <SliderField
                label="Background shading"
                value={lay.vignette ?? 1}
                range={ranges.vignette}
                onChange={(v) => set("vignette", v)}
                format={(v) => `${Math.round(v * 100)}%`}
              />
            )}

            <div className="flex items-center gap-2 pt-1">
              <Button loading={save.isPending} onClick={() => save.mutate()}>
                Save layout
              </Button>
              <Button variant="ghost" onClick={reset}>
                <RotateCcw className="h-4 w-4" /> Reset to default
              </Button>
              {save.isSuccess && <span className="text-sm text-emerald-300">Saved.</span>}
            </div>
            {save.isError && <ErrorNote>{(save.error as Error).message}</ErrorNote>}
            {previewMut.isError && (
              <ErrorNote>Preview failed: {(previewMut.error as Error).message}</ErrorNote>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function LabelWrap({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-xs uppercase tracking-widest text-slate-500">{label}</span>
      {children}
    </label>
  );
}

function SelectField({
  label, value, onChange, options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { key: string; label: string }[];
}) {
  return (
    <LabelWrap label={label}>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
      >
        {options.map((o) => (
          <option key={o.key} value={o.key}>
            {o.label}
          </option>
        ))}
      </select>
    </LabelWrap>
  );
}

function SliderField({
  label, value, range, onChange, format,
}: {
  label: string;
  value: number;
  range?: { min: number; max: number; step: number };
  onChange: (v: number) => void;
  format: (v: number) => string;
}) {
  const r = range ?? { min: 0, max: 1, step: 0.05 };
  return (
    <LabelWrap label={label}>
      <div className="flex items-center gap-3">
        <input
          type="range"
          min={r.min}
          max={r.max}
          step={r.step}
          value={value}
          onChange={(e) => onChange(parseFloat(e.target.value))}
          className="h-1.5 flex-1 cursor-pointer appearance-none rounded-full bg-slate-700 accent-amber-400"
        />
        <span className="w-16 text-right font-mono text-xs text-slate-400">{format(value)}</span>
      </div>
    </LabelWrap>
  );
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex cursor-pointer items-center gap-2 text-sm text-slate-200">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 rounded border-slate-600 bg-slate-950 accent-amber-400"
      />
      {label}
    </label>
  );
}
