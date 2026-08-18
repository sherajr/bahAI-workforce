import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Cpu } from "lucide-react";
import { api } from "../../lib/api";
import type { ModelOption } from "../../lib/types";
import { agentLabel, cn } from "../../lib/utils";
import { Button, ErrorNote } from "../ui";

const PROVIDER_LABELS: Record<string, string> = {
  ollama: "On this computer — free",
  xai: "Grok (xAI) — paid",
  openai: "OpenAI — paid",
  anthropic: "Claude (Anthropic) — paid",
};

const PROVIDER_NAMES: Record<string, string> = {
  ollama: "Ollama on this computer",
  xai: "xAI",
  openai: "OpenAI",
  anthropic: "Anthropic",
};

/**
 * Which model an agent runs on.
 *
 * The list is DISCOVERED from each provider, so it shows what is actually
 * installed and reachable rather than a hardcoded set that quietly goes stale.
 * The provider boundary — workforce agents never on Claude, Abigail never off
 * it (hard rule 16) — is enforced by the backend before anything is stored;
 * this component only has to render it.
 */
export function ModelPicker({ agent, onSaved }: { agent: string; onSaved: () => void }) {
  const choices = useQuery({
    queryKey: ["colony-models", agent],
    queryFn: () => api.getAgentModels(agent),
  });
  const [selected, setSelected] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const data = choices.data;
  useEffect(() => {
    if (data) setSelected(data.chosen ?? "");
    setSaved(false);
  }, [data, agent]);

  const models = data?.models ?? [];
  const byProvider = models.reduce<Record<string, ModelOption[]>>((acc, m) => {
    (acc[m.provider] ??= []).push(m);
    return acc;
  }, {});

  const current = models.find((m) => m.id === selected);
  const defaultIsFree = data ? !data.default_paid : true;
  // The warning you asked for: moving a normally-free agent onto a paid model
  // affects every pipeline run, not just chat.
  const goingPaid = !!current?.paid && defaultIsFree;
  const dirty = selected !== (data?.chosen ?? "");

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await api.setAgentSettings(agent, { model: selected });
      setSaved(true);
      choices.refetch();
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save that.");
    } finally {
      setSaving(false);
    }
  };

  const unreachable = Object.entries(data?.reachable ?? {})
    .filter(([, ok]) => !ok)
    .map(([p]) => p);

  return (
    <section>
      <h3 className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold uppercase
                     tracking-wider text-slate-500">
        <Cpu className="h-3.5 w-3.5" /> Model
      </h3>
      <p className="mb-2 text-xs leading-relaxed text-slate-500">
        {agent === "secretary"
          ? "Which Claude model Abigail thinks with, on the dashboard and on WhatsApp."
          : `Which model ${agentLabel(agent)} thinks with — in the pipelines as well as in chat.`}
      </p>

      {choices.isError && (
        <ErrorNote>Could not load the model list: {(choices.error as Error).message}</ErrorNote>
      )}

      <select
        value={selected}
        onChange={(e) => { setSelected(e.target.value); setSaved(false); }}
        className="w-full rounded-lg border border-slate-700 bg-slate-900/80 px-3 py-2
                   text-sm text-slate-200 focus:border-amber-400/50 focus:outline-none"
      >
        <option value="">
          Default{data?.default_model ? ` — ${data.default_model}` : ""}
          {data ? (data.default_paid ? " (paid)" : " (free)") : ""}
        </option>
        {Object.entries(byProvider).map(([provider, list]) => (
          <optgroup key={provider} label={PROVIDER_LABELS[provider] ?? provider}>
            {list.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}{m.note ? ` — ${m.note}` : ""}
              </option>
            ))}
          </optgroup>
        ))}
      </select>

      {/* An empty list because a provider is DOWN is a different fact from
          nothing being installed — say which it is rather than showing an
          unexplained empty dropdown. */}
      {unreachable.length > 0 && (
        <p className="mt-1.5 text-[11px] text-amber-300/80">
          Couldn't reach {unreachable.map((p) => PROVIDER_NAMES[p] ?? p).join(" or ")},
          so the live model list may be incomplete. Anything already chosen keeps working.
        </p>
      )}

      {goingPaid && (
        <div className="mt-2 flex gap-2 rounded-lg border border-amber-400/30 bg-amber-400/5
                        px-3 py-2 text-xs text-amber-200/90">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            {agentLabel(agent)} is free today. On a paid model, <strong>every</strong> run
            that uses them costs money — pipelines included, not just chat.
          </span>
        </div>
      )}

      {data?.uses_vision && (
        <p className="mt-2 text-[11px] leading-relaxed text-slate-500">
          Note: {agentLabel(agent)} looks at images through Grok's vision model, which is a
          separate paid path. Choosing a local model here changes their writing and
          reasoning, but not that.
        </p>
      )}

      <div className="mt-2.5 flex items-center gap-3">
        <Button onClick={save} loading={saving} disabled={saving || !dirty}>
          Save
        </Button>
        {selected && dirty && (
          <button onClick={() => setSelected("")}
                  className="text-xs text-slate-500 hover:text-slate-300">
            Use the default
          </button>
        )}
        {saved && <span className="text-xs text-emerald-300">Saved.</span>}
      </div>

      {error && <div className="mt-2"><ErrorNote>{error}</ErrorNote></div>}

      <p className={cn("mt-2 text-[11px]", data?.chosen ? "text-slate-400" : "text-slate-600")}>
        {data?.chosen
          ? `Running on ${data.chosen}.`
          : `Running on the default, ${data?.default_model ?? "…"}.`}
      </p>
    </section>
  );
}
