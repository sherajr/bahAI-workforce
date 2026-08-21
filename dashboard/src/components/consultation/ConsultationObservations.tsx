import { Eye, X } from "lucide-react";
import type { ConsultationObservation } from "../../lib/consultationTypes";
import { BadgePill, Button, Card, CardContent, CardHeader, CardTitle } from "../ui";

/**
 * What the assistant has noticed, without saying a word.
 *
 * This panel is the point of the whole restraint: an observation can be useful
 * on screen while being nowhere near worth interrupting a person for. Nothing
 * here is a quotation and nothing here is a decision — it is the assistant's
 * own reading, labelled as such.
 */
const KIND_LABEL: Record<string, string> = {
  possible_synthesis: "Possible synthesis",
  unaddressed_assumption: "Unexamined assumption",
  unrepresented_concern: "Concern not yet addressed",
  convergence: "Convergence",
  term_used_differently: "One word, two meanings",
  means_before_ends: "Implementation before objective",
  open_question: "Open question",
  note: "Noticed",
};

export function ConsultationObservations({
  observations, onAsk, onDismiss, disabled, assistantName,
}: {
  observations: ConsultationObservation[];
  assistantName: string;
  onAsk: (observation: ConsultationObservation) => void;
  onDismiss: (id: string) => void;
  disabled?: boolean;
}) {
  const open = observations.filter((o) => o.status === "open" || o.status === "surfaced");

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Eye className="h-4 w-4 text-slate-400" />
          What {assistantName} notices
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {open.length === 0 && (
          <p className="text-sm text-slate-500">
            Nothing worth raising. Most of a consultation should look like this.
          </p>
        )}
        {open.map((o) => (
          <div key={o.id} className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
            <div className="flex items-start justify-between gap-2">
              <BadgePill className="border-slate-700 bg-slate-800/80 text-slate-300">
                {KIND_LABEL[o.kind] ?? o.kind.replace(/_/g, " ")}
              </BadgePill>
              <button
                onClick={() => onDismiss(o.id)}
                title="Dismiss"
                className="rounded p-1 text-slate-500 hover:bg-slate-800 hover:text-slate-300"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
            <p className="mt-2 text-sm text-slate-200">{o.summary}</p>
            {o.detail && <p className="mt-1 text-xs text-slate-400">{o.detail}</p>}
            <div className="mt-2 flex items-center gap-2">
              <Button variant="ghost" className="px-2 py-1 text-xs"
                      onClick={() => onAsk(o)} disabled={disabled}>
                Ask {assistantName} to explain
              </Button>
              {o.status === "surfaced" && (
                <span className="text-xs text-amber-300">She has asked to speak about this.</span>
              )}
            </div>
          </div>
        ))}
        <p className="pt-1 text-[11px] leading-relaxed text-slate-600">
          These are {assistantName}'s own observations — not verified facts, and not decisions.
        </p>
      </CardContent>
    </Card>
  );
}
