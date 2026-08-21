import { useState } from "react";
import { BookOpen, ChevronDown, ChevronRight, ExternalLink } from "lucide-react";
import type {
  ConsultationAction, ConsultationDecision, ConsultationStateMap, MapItem, VerifiedWriting,
} from "../../lib/consultationTypes";
import { BadgePill, Button, Card, CardContent, CardHeader, CardTitle } from "../ui";

/**
 * The consultation as it stands. Ideas are the GROUP's — nothing here is
 * attributed to whoever said it (rule 79), and nothing here is a decision until
 * a person confirms one (rule 81).
 */
const SECTIONS: { key: keyof ConsultationStateMap; label: string; hint?: string }[] = [
  { key: "agreements", label: "Where we seem agreed" },
  { key: "ideas", label: "Ideas on the table" },
  { key: "tensions", label: "Still unresolved" },
  { key: "needs_and_concerns", label: "Concerns raised" },
  { key: "possible_syntheses", label: "Possible syntheses",
    hint: "Offered for the group to consider, not as the answer." },
  { key: "facts", label: "Facts" },
  { key: "assumptions", label: "Assumptions not yet established" },
  { key: "principles", label: "Principles in play" },
  { key: "unresolved_questions", label: "Open questions" },
  { key: "questions_to_investigate", label: "Needs information, not argument" },
];

const FACT_TONE: Record<string, string> = {
  confirmed: "border-emerald-500/30 bg-emerald-500/10 text-emerald-200",
  uncertain: "border-slate-700 bg-slate-800/60 text-slate-300",
  disputed: "border-amber-500/30 bg-amber-500/10 text-amber-200",
};

export function ConsultationMap({
  state, decisions, actions, writings, onConfirmDecision, onRejectDecision, onToggleAction, busy,
}: {
  state: ConsultationStateMap;
  decisions: ConsultationDecision[];
  actions: ConsultationAction[];
  writings: VerifiedWriting[];
  onConfirmDecision: (id: string) => void;
  onRejectDecision: (id: string) => void;
  onToggleAction: (id: string, status: "open" | "done") => void;
  busy?: boolean;
}) {
  const [open, setOpen] = useState<Record<string, boolean>>({
    agreements: true, ideas: true, tensions: true, possible_syntheses: true,
  });
  const confirmed = decisions.find((d) => d.status === "confirmed");
  const candidates = decisions.filter((d) => d.status === "candidate");

  return (
    <div className="space-y-3">
      {state.summary && (
        <Card>
          <CardHeader><CardTitle>Where the consultation stands</CardTitle></CardHeader>
          <CardContent className="text-sm leading-relaxed text-slate-300">
            {state.summary}
          </CardContent>
        </Card>
      )}

      {(confirmed || candidates.length > 0) && (
        <Card className={confirmed ? "border-emerald-500/40" : "border-amber-400/40"}>
          <CardHeader>
            <CardTitle>{confirmed ? "Confirmed decision" : "Possible decision detected"}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {confirmed ? (
              <>
                <p className="text-sm text-emerald-100">{confirmed.text}</p>
                <div className="flex items-center gap-2">
                  <Button variant="ghost" onClick={() => onRejectDecision(confirmed.id)}
                          disabled={busy}>
                    Reopen — not decided after all
                  </Button>
                </div>
              </>
            ) : (
              candidates.map((c) => (
                <div key={c.id} className="space-y-2 border-b border-slate-800 pb-3 last:border-0 last:pb-0">
                  <p className="text-sm text-slate-200">{c.text}</p>
                  {c.rationale && <p className="text-xs text-slate-400">{c.rationale}</p>}
                  {c.concerns?.length > 0 && (
                    <p className="text-xs text-amber-200/80">
                      Still held against it: {c.concerns.join("; ")}
                    </p>
                  )}
                  <p className="text-xs text-slate-500">
                    Nothing is decided until someone here says so.
                  </p>
                  <div className="flex flex-wrap gap-2">
                    <Button onClick={() => onConfirmDecision(c.id)} disabled={busy}>
                      Confirm decision
                    </Button>
                    <Button variant="secondary" onClick={() => onRejectDecision(c.id)}
                            disabled={busy}>
                      Not decided
                    </Button>
                  </div>
                </div>
              ))
            )}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader><CardTitle>Consultation map</CardTitle></CardHeader>
        <CardContent className="space-y-1">
          {SECTIONS.every((s) => ((state[s.key] as MapItem[] | undefined) ?? []).length === 0) && (
            <p className="py-2 text-sm text-slate-500">
              The map fills in as the consultation develops.
            </p>
          )}
          {SECTIONS.map((section) => {
            const items = (state[section.key] as MapItem[] | undefined) ?? [];
            if (items.length === 0) return null;
            const isOpen = open[section.key as string] ?? false;
            return (
              <div key={section.key as string} className="border-b border-slate-800/70 last:border-0">
                <button
                  onClick={() => setOpen((o) => ({ ...o, [section.key]: !isOpen }))}
                  className="flex w-full items-center gap-2 py-2 text-left text-sm text-slate-200"
                >
                  {isOpen ? <ChevronDown className="h-4 w-4 text-slate-500" />
                          : <ChevronRight className="h-4 w-4 text-slate-500" />}
                  <span className="font-medium">{section.label}</span>
                  <span className="ml-auto text-xs text-slate-500">{items.length}</span>
                </button>
                {isOpen && (
                  <ul className="space-y-1.5 pb-3 pl-6">
                    {section.hint && (
                      <li className="text-xs text-slate-500">{section.hint}</li>
                    )}
                    {items.map((item) => (
                      <li key={item.id} className="text-sm leading-relaxed text-slate-300">
                        {item.text}
                        {section.key === "facts" && item.status && (
                          <BadgePill className={`ml-2 ${FACT_TONE[item.status] ?? FACT_TONE.uncertain}`}>
                            {item.status}
                          </BadgePill>
                        )}
                        {item.note && (
                          <span className="block text-xs text-slate-500">{item.note}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </CardContent>
      </Card>

      {actions.length > 0 && (
        <Card>
          <CardHeader><CardTitle>Action items</CardTitle></CardHeader>
          <CardContent className="space-y-2">
            {actions.map((a) => (
              <label key={a.id} className="flex items-start gap-3 text-sm text-slate-300">
                <input
                  type="checkbox"
                  checked={a.status === "done"}
                  onChange={() => onToggleAction(a.id, a.status === "done" ? "open" : "done")}
                  className="mt-1 accent-amber-400"
                />
                <span>
                  {a.action}
                  <span className="block text-xs text-slate-500">
                    {a.owner ?? "Owner not assigned"}
                    {a.due ? ` · due ${a.due}` : ""}
                  </span>
                </span>
              </label>
            ))}
          </CardContent>
        </Card>
      )}

      {writings.length > 0 && (
        <Card className="border-amber-400/30">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <BookOpen className="h-4 w-4 text-amber-300" />
              Verified writings
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-xs text-amber-200/70">
              Exact text from the verified library. The assistant does not quote from memory.
            </p>
            {writings.map((w) => (
              <blockquote key={w.id} className="border-l-2 border-amber-400/40 pl-3">
                <p className="text-sm italic leading-relaxed text-slate-200">{w.text}</p>
                <footer className="mt-1 text-xs text-slate-500">
                  {[w.source, w.section].filter(Boolean).join(" — ")}
                  {w.link && (
                    <a href={w.link} target="_blank" rel="noreferrer"
                       className="ml-2 inline-flex items-center gap-1 text-amber-300 hover:text-amber-200">
                      source <ExternalLink className="h-3 w-3" />
                    </a>
                  )}
                </footer>
              </blockquote>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
