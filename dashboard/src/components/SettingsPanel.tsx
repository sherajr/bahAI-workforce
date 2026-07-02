import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { API_ORIGIN, api } from "../lib/api";
import { getSettings, saveSettings } from "../lib/settings";
import { Button, Card, CardContent, CardHeader, CardTitle } from "./ui";

const GROK_TASKS = ["copy", "copywriting", "review", "creative_writing", "complex_analysis", "scribe", "reviewer", "librarian"];

export function SettingsPanel() {
  const [settings, setSettings] = useState(getSettings());
  const canva = useQuery({ queryKey: ["canva-status"], queryFn: api.getCanvaStatus });
  const etsy = useQuery({ queryKey: ["etsy-status"], queryFn: api.getEtsyStatus });

  const update = (patch: Partial<typeof settings>) => {
    const next = { ...settings, ...patch };
    setSettings(next);
    saveSettings(next);
  };

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <Card>
        <CardHeader>
          <CardTitle>Quality gate</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          <p className="text-sm text-slate-400">
            Applied to every pipeline run. Saved on this device.
          </p>
          <div>
            <div className="mb-1.5 flex justify-between text-sm">
              <span className="text-slate-300">Target score</span>
              <span className="font-mono text-slate-100">{settings.targetScore.toFixed(1)}/10</span>
            </div>
            <input
              type="range"
              min={6}
              max={10}
              step={0.5}
              value={settings.targetScore}
              onChange={(e) => update({ targetScore: parseFloat(e.target.value) })}
              className="w-full"
            />
          </div>
          <div>
            <div className="mb-1.5 flex justify-between text-sm">
              <span className="text-slate-300">Max revision attempts</span>
              <span className="font-mono text-slate-100">{settings.maxAttempts}</span>
            </div>
            <input
              type="range"
              min={1}
              max={5}
              step={1}
              value={settings.maxAttempts}
              onChange={(e) => update({ maxAttempts: parseInt(e.target.value) })}
              className="w-full"
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Canva</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {canva.isLoading && <p className="text-sm text-slate-500">Checking...</p>}
          {canva.data && (
            <p className="text-sm">
              {canva.data.authorised ? (
                <span className="text-emerald-300">Connected</span>
              ) : (
                <span className="text-orange-300">Not connected</span>
              )}
              {canva.data.template_id && (
                <span className="text-slate-500"> · template {canva.data.template_id}</span>
              )}
            </p>
          )}
          {canva.isError && (
            <p className="text-sm text-rose-300">
              Could not reach the API: {(canva.error as Error).message}
            </p>
          )}
          {!canva.data?.authorised && (
            <Button variant="secondary" onClick={() => window.open(`${API_ORIGIN}/canva/oauth/start`, "_blank")}>
              Connect Canva <ExternalLink className="h-3.5 w-3.5" />
            </Button>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Etsy</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {etsy.data && (
            <p className="text-sm">
              {!etsy.data.configured ? (
                <span className="text-orange-300">
                  Not configured — add ETSY_CLIENT_ID, ETSY_CLIENT_SECRET, and ETSY_SHOP_ID to .env
                </span>
              ) : etsy.data.authorised ? (
                <span className="text-emerald-300">Connected · shop {etsy.data.shop_id}</span>
              ) : (
                <span className="text-orange-300">Configured but not authorised</span>
              )}
            </p>
          )}
          {etsy.data?.configured && !etsy.data.authorised && (
            <Button variant="secondary" onClick={() => window.open(`${API_ORIGIN}/etsy/oauth/start`, "_blank")}>
              Connect Etsy <ExternalLink className="h-3.5 w-3.5" />
            </Button>
          )}
          <p className="text-xs text-slate-500">
            Publishing only ever creates drafts. Activating a listing (spending real money) stays
            in your hands inside Etsy.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Model routing</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <p className="text-slate-400">
            Fixed in <span className="font-mono text-xs">agents/router.py</span> — shown here for
            reference.
          </p>
          <div>
            <div className="text-xs uppercase tracking-widest text-slate-500">
              xAI Grok (creative, review, verification)
            </div>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {GROK_TASKS.map((t) => (
                <span key={t} className="rounded-full bg-slate-800 px-2.5 py-0.5 font-mono text-xs text-slate-300">
                  {t}
                </span>
              ))}
            </div>
          </div>
          <p className="text-slate-400">
            Everything else runs locally on{" "}
            <span className="font-mono text-xs text-slate-300">Ollama / qwen3-16k</span> — free,
            private, on your RTX GPU. Image vision uses Claude Haiku; image generation uses
            xAI Grok-imagine.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
